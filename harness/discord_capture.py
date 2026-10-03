"""Capture Discord message history through a real browser (docs/NOTES.md "Discord capture").

`serve` opens Microsoft Edge under Patchright, the undetected Playwright fork, with
a persistent profile. The profile holds the login: it's gitignored and never backed up.
The capture only records responses the Discord web app requested itself and never
calls the API on its own. These go into harness/data/discord.db:

  GET /api/v*/channels/<id>/messages              message history (scrolling, jumps)
  GET /api/v*/guilds/<id>/messages/search         search results
  GET /api/v*/channels/<id>/threads/...           thread/forum post lists
  gateway WebSocket (zstd/zlib stream)            READY/GUILD_CREATE channel lists,
                                                  live MESSAGE_CREATE/UPDATE

The same process listens for one JSON line per request on 127.0.0.1:25980. The other
subcommands are clients of that port, apart from `stats` and `search`, which read the
DB directly and run under the plain harness Python:

  status | shot | goto URL | click SEL | fill SEL TEXT | press KEY | eval JS
  guilds | channels GUILD | crawl GUILD --channels ID,ID --until YYYY-MM-DD
  crawl-stop | quit | stats | search QUERY

`crawl` scrolls each channel up with mouse-wheel bursts at a human pace until the
channel's start or the --until date. It resumes from the oldest message already
stored (a jump link) and pauses with a beep on a captcha or logout, as the game
runner does for its captchas.

Setup: `py -3.13 -m venv .venv-discord && .venv-discord/Scripts/python.exe -m pip
install patchright zstandard`. Run serve with .venv-discord/Scripts/python.exe.
"""

import argparse
import asyncio
import datetime
import json
import os
import random
import re
import socket
import sqlite3
import sys
import time
import zlib
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "harness", "data")
DEFAULT_DB = os.path.join(DATA, "discord.db")
DEFAULT_PROFILE = os.path.join(DATA, "discord_profile")
LOGDIR = os.path.join(ROOT, "logs", "discord")
PORT = 25980  # outside the proxy's 25940-25960 upstream range; 25970 is triage
DISCORD_EPOCH_MS = 1420070400000
SCHEMA_VERSION = 1

# crawl pacing (seconds unless noted)
PACE = (2.0, 6.0)            # dwell after each load
LONG_PAUSE_P = 0.06          # chance of a longer read after a load
LONG_PAUSE = (20.0, 90.0)
SESSION_MIN = (60.0, 90.0)   # minutes of crawling before a break
BREAK_MIN = (5.0, 15.0)
RATE_LIMIT_PAUSE = (600.0, 900.0)
REJUMP_EVERY = 150           # loads; a jump drops the app's in-memory message list

MSG_RE = re.compile(r"^/api/v\d+/channels/(\d+)/messages$")
SEARCH_RE = re.compile(r"^/api/v\d+/(?:guilds|channels)/(\d+)/messages/search$")
THREADS_RE = re.compile(r"^/api/v\d+/(?:channels|guilds)/(\d+)/threads(?:/|$)")
CHANNEL_URL_RE = re.compile(r"/channels/(\d+)/(\d+)")
DISCORD_HOSTS = ("discord.com", "ptb.discord.com", "canary.discord.com")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS guilds(id INTEGER PRIMARY KEY, name TEXT, updated_t REAL);
CREATE TABLE IF NOT EXISTS channels(
  id INTEGER PRIMARY KEY, guild_id INTEGER, parent_id INTEGER, name TEXT,
  type INTEGER, topic TEXT, position INTEGER, updated_t REAL);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL, guild_id INTEGER,
  author_id INTEGER, author TEXT, ts TEXT NOT NULL, edited_ts TEXT, type INTEGER,
  content TEXT NOT NULL, embed_text TEXT, reply_to INTEGER, attachments TEXT,
  embeds TEXT, captured_t REAL);
CREATE INDEX IF NOT EXISTS messages_channel ON messages(channel_id, id);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
  content, author, embed_text, content='messages', content_rowid='id',
  tokenize='porter unicode61');
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
  INSERT INTO messages_fts(rowid, content, author, embed_text)
  VALUES (new.id, new.content, new.author, new.embed_text);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, content, author, embed_text)
  VALUES ('delete', old.id, old.content, old.author, old.embed_text);
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, content, author, embed_text)
  VALUES ('delete', old.id, old.content, old.author, old.embed_text);
  INSERT INTO messages_fts(rowid, content, author, embed_text)
  VALUES (new.id, new.content, new.author, new.embed_text);
END;
CREATE TABLE IF NOT EXISTS crawl(
  channel_id INTEGER PRIMARY KEY, reached_start INTEGER NOT NULL DEFAULT 0,
  loads INTEGER NOT NULL DEFAULT 0, note TEXT, updated_t REAL);
CREATE TABLE IF NOT EXISTS captures(
  id INTEGER PRIMARY KEY, t REAL, kind TEXT, url TEXT, status INTEGER, n INTEGER);
"""


def snowflake_ms(i):
    return (int(i) >> 22) + DISCORD_EPOCH_MS


def snowflake_date(i):
    return datetime.datetime.fromtimestamp(snowflake_ms(i) / 1000).strftime("%Y-%m-%d %H:%M")


def _int(v):
    return int(v) if v not in (None, "") else None


def log(msg):
    line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    os.makedirs(LOGDIR, exist_ok=True)
    with open(os.path.join(LOGDIR, "capture.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _embed_text(m):
    """Searchable text that isn't in `content`: embeds (bot posts, link previews)
    and forwarded messages."""
    parts = []
    for e in m.get("embeds") or []:
        for k in ("title", "description"):
            if e.get(k):
                parts.append(e[k])
        for f in e.get("fields") or []:
            parts.append(f"{f.get('name', '')}: {f.get('value', '')}")
        if (e.get("footer") or {}).get("text"):
            parts.append(e["footer"]["text"])
    for s in m.get("message_snapshots") or []:
        if (s.get("message") or {}).get("content"):
            parts.append(s["message"]["content"])
    return "\n".join(parts) or None


def norm_message(m, guild_id=None):
    a = m.get("author") or {}
    ref = m.get("message_reference") or {}
    atts = [{"filename": x.get("filename"), "url": x.get("url"),
             "content_type": x.get("content_type"), "size": x.get("size")}
            for x in m.get("attachments") or []]
    embeds = [{k: e[k] for k in ("type", "title", "description", "url", "fields") if e.get(k)}
              for e in m.get("embeds") or []]
    return {
        "id": int(m["id"]), "channel_id": int(m["channel_id"]),
        "guild_id": _int(m.get("guild_id")) or guild_id,
        "author_id": _int(a.get("id")), "author": a.get("global_name") or a.get("username"),
        "ts": m["timestamp"], "edited_ts": m.get("edited_timestamp"), "type": m.get("type", 0),
        "content": m.get("content") or "", "embed_text": _embed_text(m),
        "reply_to": _int(ref.get("message_id")) if ref.get("type", 0) == 0 else None,
        "attachments": json.dumps(atts) if atts else None,
        "embeds": json.dumps(embeds) if embeds else None,
    }


class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript(SCHEMA)
        self.db.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version', ?)",
                        (str(SCHEMA_VERSION),))
        self.db.commit()
        self._guild_of = dict(self.db.execute(
            "SELECT id, guild_id FROM channels WHERE guild_id IS NOT NULL"))

    def guild_of(self, channel_id):
        return self._guild_of.get(int(channel_id))

    def upsert_guild(self, gid, name):
        self.db.execute(
            "INSERT INTO guilds VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
            "name=coalesce(excluded.name, guilds.name), updated_t=excluded.updated_t",
            (int(gid), name, time.time()))

    def upsert_channel(self, ch, guild_id=None):
        gid = _int(ch.get("guild_id")) or guild_id
        cid = int(ch["id"])
        self.db.execute(
            "INSERT INTO channels VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
            "guild_id=coalesce(excluded.guild_id, channels.guild_id), "
            "parent_id=coalesce(excluded.parent_id, channels.parent_id), "
            "name=coalesce(excluded.name, channels.name), type=excluded.type, "
            "topic=coalesce(excluded.topic, channels.topic), "
            "position=coalesce(excluded.position, channels.position), updated_t=excluded.updated_t",
            (cid, gid, _int(ch.get("parent_id")), ch.get("name"), ch.get("type"),
             ch.get("topic"), ch.get("position"), time.time()))
        if gid:
            self._guild_of[cid] = gid

    def note_channel_guild(self, channel_id, guild_id):
        """Channel id seen under a guild in the page URL before any channel list."""
        cid, gid = int(channel_id), int(guild_id)
        if self._guild_of.get(cid) != gid:
            self.db.execute("INSERT INTO channels(id, guild_id, updated_t) VALUES (?, ?, ?) "
                            "ON CONFLICT(id) DO UPDATE SET guild_id=excluded.guild_id",
                            (cid, gid, time.time()))
            self._guild_of[cid] = gid
            self.db.commit()

    def add_messages(self, msgs, guild_id=None):
        """Upsert raw message objects; returns how many were new. An edited message
        replaces its stored text (the FTS triggers follow)."""
        new = 0
        now = time.time()
        for m in msgs:
            if not m.get("id") or not m.get("channel_id") or not m.get("timestamp"):
                continue
            r = norm_message(m, guild_id or self.guild_of(m["channel_id"]))
            cur = self.db.execute(
                "INSERT INTO messages VALUES (:id, :channel_id, :guild_id, :author_id, :author, "
                ":ts, :edited_ts, :type, :content, :embed_text, :reply_to, :attachments, :embeds, "
                ":captured_t) ON CONFLICT(id) DO NOTHING", {**r, "captured_t": now})
            if cur.rowcount:
                new += 1
            else:
                self.db.execute(
                    "UPDATE messages SET content=:content, embed_text=:embed_text, "
                    "edited_ts=:edited_ts, attachments=:attachments, embeds=:embeds, "
                    "guild_id=coalesce(guild_id, :guild_id) WHERE id=:id AND "
                    "(edited_ts IS NOT :edited_ts OR embed_text IS NOT :embed_text "
                    "OR (guild_id IS NULL AND :guild_id IS NOT NULL))", r)
        self.db.commit()
        return new

    def log_capture(self, kind, url, status, n):
        self.db.execute("INSERT INTO captures(t, kind, url, status, n) VALUES (?, ?, ?, ?, ?)",
                        (time.time(), kind, url, status, n))
        self.db.commit()

    def channel_span(self, channel_id):
        return self.db.execute("SELECT count(*), min(id), max(id) FROM messages WHERE channel_id=?",
                               (int(channel_id),)).fetchone()

    def crawl_get(self, channel_id):
        row = self.db.execute("SELECT reached_start, loads, note FROM crawl WHERE channel_id=?",
                              (int(channel_id),)).fetchone()
        return row or (0, 0, None)

    def crawl_set(self, channel_id, reached_start=None, add_loads=0, note=None):
        self.db.execute(
            "INSERT INTO crawl VALUES (?, coalesce(?, 0), ?, ?, ?) ON CONFLICT(channel_id) DO UPDATE SET "
            "reached_start=coalesce(?, crawl.reached_start), loads=crawl.loads+?, "
            "note=coalesce(?, crawl.note), updated_t=?",
            (int(channel_id), reached_start, add_loads, note, time.time(),
             reached_start, add_loads, note, time.time()))
        self.db.commit()

    def channels(self, guild_id):
        rows = self.db.execute(
            "SELECT c.id, c.parent_id, c.name, c.type, c.position, "
            "(SELECT count(*) FROM messages m WHERE m.channel_id=c.id), "
            "(SELECT min(id) FROM messages m WHERE m.channel_id=c.id), "
            "coalesce(k.reached_start, 0), k.note "
            "FROM channels c LEFT JOIN crawl k ON k.channel_id=c.id WHERE c.guild_id=?",
            (int(guild_id),)).fetchall()
        return [{"id": str(r[0]), "parent_id": str(r[1]) if r[1] else None, "name": r[2],
                 "type": r[3], "position": r[4], "messages": r[5],
                 "oldest": snowflake_date(r[6]) if r[6] else None,
                 "reached_start": bool(r[7]), "note": r[8]} for r in rows]

    def stats(self):
        q = self.db.execute
        return {
            "messages": q("SELECT count(*) FROM messages").fetchone()[0],
            "channels_with_messages": q("SELECT count(DISTINCT channel_id) FROM messages").fetchone()[0],
            "guilds": q("SELECT count(*) FROM guilds").fetchone()[0],
            "oldest": (lambda v: snowflake_date(v) if v else None)(q("SELECT min(id) FROM messages").fetchone()[0]),
            "per_channel": [
                {"channel": n or str(c), "messages": k, "oldest": snowflake_date(o),
                 "reached_start": bool(rs)}
                for c, n, k, o, rs in q(
                    "SELECT m.channel_id, c.name, count(*), min(m.id), coalesce(k.reached_start, 0) "
                    "FROM messages m LEFT JOIN channels c ON c.id=m.channel_id "
                    "LEFT JOIN crawl k ON k.channel_id=m.channel_id "
                    "GROUP BY m.channel_id ORDER BY count(*) DESC")],
        }

    def search(self, query, channel=None, limit=20):
        # quoted words: FTS syntax in the query can't break the MATCH
        words = re.findall(r"\w+", query)
        if not words:
            return []
        match = " ".join(f'"{w}"' for w in words)
        sql = ("SELECT m.id, m.channel_id, m.guild_id, c.name, m.author, m.ts, m.content, "
               "m.embed_text FROM messages_fts f JOIN messages m ON m.id=f.rowid "
               "LEFT JOIN channels c ON c.id=m.channel_id WHERE messages_fts MATCH ?")
        args = [match]
        if channel:
            sql += " AND (m.channel_id=? OR c.name=?)"
            args += [_int(channel) if str(channel).isdigit() else -1, str(channel)]
        sql += " ORDER BY bm25(messages_fts, 1.0, 0.3, 0.7) LIMIT ?"
        args.append(int(limit))
        return [{"id": str(r[0]), "channel": r[3] or str(r[1]), "author": r[4], "ts": r[5][:16],
                 "text": (r[6] or r[7] or "")[:500],
                 "link": f"https://discord.com/channels/{r[2] or '@me'}/{r[1]}/{r[0]}"}
                for r in self.db.execute(sql, args)]


class GatewayDecoder:
    """Decodes one gateway WebSocket's frames into dispatch payloads. The compressed
    transports share one decompressor across the whole connection, so it must see
    every frame from the first one on."""

    MAX_TEXT = 64 << 20

    def __init__(self, url):
        comp = (parse_qs(urlparse(url).query).get("compress") or [None])[0]
        self.kind = comp or "none"
        self._buf = b""
        self._text = ""
        if comp == "zstd-stream":
            import zstandard
            self._d = zstandard.ZstdDecompressor().decompressobj()
        elif comp == "zlib-stream":
            self._d = zlib.decompressobj()
        else:
            self._d = None

    def feed(self, payload):
        if isinstance(payload, str):
            data = payload
        else:
            if self.kind == "zlib-stream":
                self._buf += payload
                if not self._buf.endswith(b"\x00\x00\xff\xff"):
                    return []
                raw, self._buf = self._d.decompress(self._buf), b""
            elif self._d is not None:
                raw = self._d.decompress(payload)
            else:
                raw = zlib.decompress(payload)  # compress=true: one zlib message per frame
            data = raw.decode("utf-8")
        self._text += data
        try:
            obj = json.loads(self._text)
        except ValueError:
            if len(self._text) > self.MAX_TEXT:
                self._text = ""
            return []
        self._text = ""
        return [obj]


def gateway_apply(store, msg):
    """Store what a gateway payload says about guilds, channels and messages."""
    t, d = msg.get("t"), msg.get("d")
    if not t or not isinstance(d, dict):
        return 0
    if t == "READY":
        for g in d.get("guilds") or []:
            _apply_guild(store, g)
    elif t == "GUILD_CREATE":
        _apply_guild(store, d)
    elif t in ("CHANNEL_CREATE", "CHANNEL_UPDATE", "THREAD_CREATE", "THREAD_UPDATE"):
        if d.get("guild_id"):
            store.upsert_channel(d)
    elif t == "THREAD_LIST_SYNC":
        for th in d.get("threads") or []:
            store.upsert_channel(th, _int(d.get("guild_id")))
    elif t in ("MESSAGE_CREATE", "MESSAGE_UPDATE") and d.get("timestamp") and d.get("guild_id"):
        return store.add_messages([d])
    else:
        return 0
    store.db.commit()
    return 0


def _apply_guild(store, g):
    if not g.get("id"):
        return
    gid = int(g["id"])
    name = g.get("name") or (g.get("properties") or {}).get("name")
    store.upsert_guild(gid, name)
    for ch in (g.get("channels") or []) + (g.get("threads") or []):
        store.upsert_channel(ch, gid)


class Batch:
    __slots__ = ("seq", "before", "after", "around", "limit", "n", "oldest")

    def __init__(self, seq, q, msgs):
        self.seq = seq
        self.before = (q.get("before") or [None])[0]
        self.after = (q.get("after") or [None])[0]
        self.around = (q.get("around") or [None])[0]
        self.limit = int((q.get("limit") or ["50"])[0])
        self.n = len(msgs)
        self.oldest = min((int(m["id"]) for m in msgs), default=None)

    @property
    def hit_start(self):
        """A history page shorter than its limit, loading older messages (or the
        channel's first page), means there's nothing older."""
        return self.n < self.limit and not self.after and not self.around


class Capture:
    def __init__(self, store, args):
        self.store = store
        self.args = args
        self.page = None
        self.ctx = None
        self.batches = {}     # channel id -> last Batch
        self.seq = 0
        self.last_capture = None
        self.rate_limited_until = 0.0
        self.unauthorized = False
        self.crawl_task = None
        self.crawl_stop = False
        self.crawl_status = {"state": "idle"}
        self.closed = asyncio.Event()
        self.ws_frames = 0

    # ---- browser ----
    async def start(self):
        from patchright.async_api import async_playwright
        self._pw = await async_playwright().start()
        # Patchright's recommended setup: a real browser, persistent profile, headed,
        # no viewport/UA/header overrides.
        self.ctx = await self._pw.chromium.launch_persistent_context(
            self.args.profile, channel=self.args.channel, headless=False, no_viewport=True)
        self.ctx.on("response", self._on_response)
        self.ctx.on("close", lambda *_: self.closed.set())
        self.page = self.ctx.pages[0] if self.ctx.pages else await self.ctx.new_page()
        self.page.on("websocket", self._on_ws)
        await self.page.goto(self.args.url)
        log(f"browser up ({self.args.channel}), profile {self.args.profile}")

    async def stop(self):
        try:
            await self.ctx.close()
        except Exception:
            pass
        await self._pw.stop()

    # ---- capture ----
    def _on_ws(self, ws):
        if "gateway" not in ws.url:
            return
        dec = GatewayDecoder(ws.url)
        log(f"gateway socket ({dec.kind})")

        def frame(payload):
            self.ws_frames += 1
            try:
                for msg in dec.feed(payload):
                    gateway_apply(self.store, msg)
            except Exception as e:
                log(f"gateway decode error: {e!r}")
        ws.on("framereceived", frame)

    def _guild_from_page(self, channel_id):
        m = CHANNEL_URL_RE.search(self.page.url or "") if self.page else None
        if m and m.group(2) == str(channel_id) and m.group(1) != "@me":
            return int(m.group(1))
        return None

    async def _on_response(self, resp):
        u = urlparse(resp.url)
        if u.hostname not in DISCORD_HOSTS or not u.path.startswith("/api/"):
            return
        m_msg, m_search, m_threads = MSG_RE.match(u.path), SEARCH_RE.match(u.path), THREADS_RE.match(u.path)
        if not (m_msg or m_search or m_threads):
            if resp.status == 429:
                self._rate_limited(resp.url)
            elif resp.status == 401:
                self.unauthorized = True
            return
        if resp.request.method != "GET":
            return
        kind = "messages" if m_msg else "search" if m_search else "threads"
        if resp.status != 200:
            self.store.log_capture(kind, resp.url, resp.status, 0)
            if resp.status == 429:
                self._rate_limited(resp.url)
            elif resp.status == 401:
                self.unauthorized = True
            elif m_msg and resp.status == 403:
                self.store.crawl_set(m_msg.group(1), note="403 no access")
            return
        try:
            body = await resp.json()
        except Exception as e:
            log(f"unreadable {kind} response {u.path}: {e!r}")
            return
        q = parse_qs(u.query)
        if m_msg and isinstance(body, list):
            ch = m_msg.group(1)
            gid = self.store.guild_of(ch) or self._guild_from_page(ch)
            if gid:
                self.store.note_channel_guild(ch, gid)
            new = self.store.add_messages(body, gid)
            self.seq += 1
            self.batches[ch] = Batch(self.seq, q, body)
            n = len(body)
        elif m_search and isinstance(body, dict):
            gid = int(m_search.group(1)) if "/guilds/" in u.path else None
            for th in body.get("threads") or []:
                self.store.upsert_channel(th, gid)
            msgs = [x for hit in body.get("messages") or []
                    for x in (hit if isinstance(hit, list) else [hit])]
            new = self.store.add_messages(msgs, gid)
            n = len(msgs)
        elif m_threads and isinstance(body, dict):
            for th in body.get("threads") or []:
                self.store.upsert_channel(th)
            self.store.db.commit()
            msgs = [x for x in body.get("first_messages") or [] if isinstance(x, dict)]
            new = self.store.add_messages(msgs)
            n = len(body.get("threads") or [])
        else:
            return
        self.store.log_capture(kind, resp.url, 200, n)
        self.last_capture = {"t": time.time(), "kind": kind, "path": u.path, "query": u.query,
                             "n": n, "new": new}

    def _rate_limited(self, url):
        pause = random.uniform(*RATE_LIMIT_PAUSE)
        self.rate_limited_until = max(self.rate_limited_until, time.time() + pause)
        log(f"429 rate limited on {urlparse(url).path}; crawl pauses {pause / 60:.0f} min")

    # ---- crawl ----
    async def _health(self):
        """Block while the page needs a human or Discord asked us to slow down.
        Returns False when the crawl should stop."""
        beeped = False
        while not self.crawl_stop:
            url = self.page.url or ""
            if "/login" in url or self.unauthorized:
                self.crawl_status["state"] = "stopped: logged out"
                log("logged out; crawl stops")
                _beep()
                return False
            captcha = await self.page.locator("iframe[src*='hcaptcha'], iframe[src*='captcha']").count()
            if captcha:
                self.crawl_status["state"] = "paused: captcha (solve it in the window)"
                if not beeped:
                    log("captcha on screen; waiting for a human")
                    _beep()
                    beeped = True
                await asyncio.sleep(5)
                continue
            wait = self.rate_limited_until - time.time()
            if wait > 0:
                self.crawl_status["state"] = f"paused: rate limited, {wait / 60:.0f} min left"
                await asyncio.sleep(min(wait, 30))
                continue
            return True
        return False

    async def _sleep(self, seconds, why):
        self.crawl_status["state"] = f"{why} ({seconds:.0f}s)"
        end = time.time() + seconds
        while not self.crawl_stop and time.time() < end:
            await asyncio.sleep(min(1.0, end - time.time()))

    async def _wait_batch(self, ch, after_seq, timeout):
        end = time.time() + timeout
        while time.time() < end:
            b = self.batches.get(ch)
            if b and b.seq > after_seq:
                return b
            await asyncio.sleep(0.1)
        return None

    async def navigate(self, url):
        """In-app navigation (history API + popstate, as the app's own links do) when
        the app is already open; a full load otherwise."""
        if (self.page.url or "").startswith("https://discord.com/channels/"):
            path = urlparse(url).path
            await self.page.evaluate(
                "p => { history.pushState(null, '', p); "
                "window.dispatchEvent(new PopStateEvent('popstate', {state: null})); }", path)
            await asyncio.sleep(1.0)
            if urlparse(self.page.url).path == path:
                return
        await self.page.goto(url)

    async def _open(self, guild, ch, at_id):
        seq0 = self.seq
        url = f"https://discord.com/channels/{guild}/{ch}" + (f"/{at_id}" if at_id else "")
        self.store.note_channel_guild(ch, guild)
        await self.navigate(url)
        b = await self._wait_batch(str(ch), seq0, 20)
        if not b and at_id is None:
            # the app may serve an already-loaded channel from memory: no request
            b = self.batches.get(str(ch))
        return b

    async def _scroll_up(self, ch):
        """Mouse-wheel bursts over the message list until the app loads an older page."""
        lst = self.page.locator("[data-list-id='chat-messages']").first
        try:
            box = await lst.bounding_box(timeout=5000)
        except Exception:
            box = None
        if not box:
            return None
        x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
        y = box["y"] + min(box["height"], 900) * random.uniform(0.25, 0.6)
        await self.page.mouse.move(x, y, steps=random.randint(3, 8))
        seq0 = self.batches[ch].seq if ch in self.batches else self.seq
        end = time.time() + 12
        while time.time() < end and not self.crawl_stop:
            await self.page.mouse.wheel(0, -random.randint(350, 900))
            b = await self._wait_batch(ch, seq0, random.uniform(0.4, 1.1))
            if b and b.before:
                return b
        return None

    async def crawl(self, guild, channels, until_ms, max_loads):
        session_end = time.time() + 60 * random.uniform(*SESSION_MIN)
        results = []
        self.crawl_status = {"state": "starting", "guild": str(guild), "results": results}
        try:
            for ch in channels:
                if self.crawl_stop:
                    break
                ch = str(ch)
                res = {"channel": ch, "name": self._name(ch)}
                results.append(res)
                self.crawl_status["channel"] = res
                session_end = await self._crawl_channel(guild, ch, until_ms, max_loads, res, session_end)
            self.crawl_status["state"] = "stopped" if self.crawl_stop else "done"
        except Exception as e:
            self.crawl_status["state"] = f"error: {e!r}"
            log(f"crawl error: {e!r}")
        log(f"crawl {self.crawl_status['state']}: {json.dumps(results)}")

    def _name(self, ch):
        r = self.store.db.execute("SELECT name FROM channels WHERE id=?", (int(ch),)).fetchone()
        return r[0] if r else None

    async def _crawl_channel(self, guild, ch, until_ms, max_loads, res, session_end):
        reached, _, note = self.store.crawl_get(ch)
        count, oldest, _ = self.store.channel_span(ch)
        if reached or (oldest and snowflake_ms(oldest) <= until_ms) or note == "403 no access":
            res["result"] = "already done"
            return session_end
        if not await self._health():
            res["result"] = "stopped"
            return session_end
        log(f"crawl #{res['name'] or ch}: {count} stored, from {snowflake_date(oldest) if oldest else 'newest'}")
        b = await self._open(guild, ch, oldest)
        if b and b.hit_start and not oldest:
            self.store.crawl_set(ch, reached_start=1)
            res["result"] = "reached start"
            return session_end
        loads = stuck = 0
        await self._sleep(random.uniform(*PACE), "reading")
        while loads < max_loads and not self.crawl_stop:
            if not await self._health():
                res["result"] = "stopped"
                break
            if time.time() > session_end:
                await self._sleep(60 * random.uniform(*BREAK_MIN), "break")
                session_end = time.time() + 60 * random.uniform(*SESSION_MIN)
                continue
            if self.store.crawl_get(ch)[2] == "403 no access":
                res["result"] = "no access"
                break
            b = await self._scroll_up(ch)
            if b is None:
                last = self.batches.get(ch)
                if last and last.hit_start:
                    self.store.crawl_set(ch, reached_start=1)
                    res["result"] = "reached start"
                    break
                stuck += 1
                if stuck >= 3:
                    self.store.crawl_set(ch, note="stuck: no older page after 3 jumps")
                    res["result"] = "stuck"
                    log(f"crawl #{res['name'] or ch}: stuck")
                    break
                await self._open(guild, ch, self.store.channel_span(ch)[1])
                await self._sleep(random.uniform(*PACE), "re-jump")
                continue
            stuck = 0
            loads += 1
            self.store.crawl_set(ch, add_loads=1)
            count, oldest, _ = self.store.channel_span(ch)
            res.update(loads=loads, messages=count, oldest=snowflake_date(oldest) if oldest else None)
            if b.hit_start:
                self.store.crawl_set(ch, reached_start=1)
                res["result"] = "reached start"
                break
            if oldest and snowflake_ms(oldest) <= until_ms:
                res["result"] = "reached --until"
                break
            if loads % REJUMP_EVERY == 0:
                await self._open(guild, ch, oldest)
            pause = random.uniform(*PACE)
            if random.random() < LONG_PAUSE_P:
                pause += random.uniform(*LONG_PAUSE)
            await self._sleep(pause, "reading")
        else:
            res.setdefault("result", "stopped" if self.crawl_stop else "max loads")
        log(f"crawl #{res['name'] or ch}: {res.get('result')} {res.get('messages', count)} msgs, "
            f"oldest {res.get('oldest')}")
        return session_end

    # ---- control port ----
    async def handle(self, req):
        op = req.get("op")
        p = self.page
        if op == "status":
            return {"url": p.url, "title": await p.title(), "last_capture": self.last_capture,
                    "gateway_frames": self.ws_frames, "crawl": self.crawl_status,
                    "rate_limited_s": max(0, round(self.rate_limited_until - time.time())),
                    **{k: v for k, v in self.store.stats().items() if k != "per_channel"}}
        if op == "shot":
            os.makedirs(LOGDIR, exist_ok=True)
            path = os.path.join(LOGDIR, f"shot-{datetime.datetime.now():%Y%m%d-%H%M%S}.png")
            await p.screenshot(path=path, full_page=bool(req.get("full")))
            return {"path": path}
        if op == "goto":
            await self.navigate(req["url"])
            return {"url": p.url}
        if op == "click":
            await p.locator(req["selector"]).first.click(timeout=req.get("timeout", 10000))
            return {"url": p.url}
        if op == "fill":
            await p.locator(req["selector"]).first.fill(req["text"], timeout=10000)
            return {}
        if op == "press":
            await p.keyboard.press(req["key"])
            return {}
        if op == "eval":
            return {"result": await p.evaluate(req["js"])}
        if op == "guilds":
            return {"guilds": [{"id": str(i), "name": n} for i, n in
                               self.store.db.execute("SELECT id, name FROM guilds ORDER BY name")]}
        if op == "channels":
            return {"channels": self.store.channels(req["guild"])}
        if op == "crawl":
            if self.crawl_task and not self.crawl_task.done():
                return {"error": "a crawl is running"}
            until = datetime.datetime.strptime(req.get("until") or "2015-05-13", "%Y-%m-%d")
            self.crawl_stop = False
            self.crawl_task = asyncio.create_task(self.crawl(
                req["guild"], req["channels"], until.timestamp() * 1000, int(req.get("max_loads", 100000))))
            return {"started": len(req["channels"])}
        if op == "crawl_stop":
            self.crawl_stop = True
            return {"stopping": bool(self.crawl_task and not self.crawl_task.done())}
        if op == "quit":
            self.crawl_stop = True
            self.closed.set()
            return {"quitting": True}
        return {"error": f"unknown op {op!r}"}

    async def serve_client(self, reader, writer):
        try:
            while line := await reader.readline():
                try:
                    out = await self.handle(json.loads(line))
                except Exception as e:
                    out = {"error": repr(e)}
                writer.write((json.dumps(out, default=str) + "\n").encode())
                await writer.drain()
        finally:
            writer.close()


def _beep():
    try:
        import winsound
        for _ in range(3):
            winsound.Beep(880, 300)
    except Exception:
        print("\a", end="", flush=True)


async def serve(args):
    store = Store(args.db)
    cap = Capture(store, args)
    await cap.start()
    server = await asyncio.start_server(cap.serve_client, "127.0.0.1", args.port)
    log(f"control port 127.0.0.1:{args.port}, db {args.db}")
    async with server:
        await cap.closed.wait()
    if cap.crawl_task and not cap.crawl_task.done():
        cap.crawl_task.cancel()
    await cap.stop()
    log("serve stopped")


def call(req, port=PORT, timeout=60):
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
        s.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(1 << 16)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf)


def main():
    ap = argparse.ArgumentParser(description="Discord history capture (docs/NOTES.md)")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--db", default=DEFAULT_DB)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--profile", default=DEFAULT_PROFILE)
    s.add_argument("--channel", default="msedge", help="browser channel (msedge, chrome)")
    s.add_argument("--url", default="https://discord.com/app")
    sub.add_parser("status")
    s = sub.add_parser("shot")
    s.add_argument("--full", action="store_true")
    sub.add_parser("goto").add_argument("url")
    sub.add_parser("click").add_argument("selector")
    s = sub.add_parser("fill")
    s.add_argument("selector")
    s.add_argument("text")
    sub.add_parser("press").add_argument("key")
    sub.add_parser("eval").add_argument("js")
    sub.add_parser("guilds")
    sub.add_parser("channels").add_argument("guild")
    s = sub.add_parser("crawl")
    s.add_argument("guild")
    s.add_argument("--channels", required=True, help="comma-separated channel ids, in order")
    s.add_argument("--until", help="stop at this date (YYYY-MM-DD); default: channel start")
    s.add_argument("--max-loads", type=int, default=100000, help="per channel")
    sub.add_parser("crawl-stop")
    sub.add_parser("quit")
    sub.add_parser("stats")
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--channel")
    s.add_argument("--limit", type=int, default=20)
    a = ap.parse_args()

    if a.cmd == "serve":
        asyncio.run(serve(a))
        return 0
    if a.cmd in ("stats", "search"):
        if not os.path.exists(a.db):
            print(f"no capture DB at {a.db}", file=sys.stderr)
            return 1
        st = Store(a.db)
        out = st.stats() if a.cmd == "stats" else st.search(a.query, a.channel, a.limit)
        print(json.dumps(out, indent=1, ensure_ascii=False))
        return 0
    req = {"op": a.cmd.replace("-", "_")}
    if a.cmd == "shot":
        req["full"] = a.full
    elif a.cmd == "goto":
        req["url"] = a.url
    elif a.cmd in ("click", "fill"):
        req["selector"] = a.selector
        if a.cmd == "fill":
            req["text"] = a.text
    elif a.cmd == "press":
        req["key"] = a.key
    elif a.cmd == "eval":
        req["js"] = a.js
    elif a.cmd == "channels":
        req["guild"] = a.guild
    elif a.cmd == "crawl":
        req.update(guild=a.guild, channels=[c.strip() for c in a.channels.split(",") if c.strip()],
                   until=a.until, max_loads=a.max_loads)
    out = call(req, a.port)
    if a.cmd == "channels" and "channels" in out:
        _print_channels(out["channels"])
    else:
        print(json.dumps(out, indent=1, ensure_ascii=False, default=str))
    return 1 if "error" in out else 0


def _print_channels(chs):
    """Category tree: categories (type 4) with their channels, threads under parents."""
    by_parent = {}
    for c in chs:
        by_parent.setdefault(c["parent_id"], []).append(c)
    for v in by_parent.values():
        v.sort(key=lambda c: (c["type"] == 4, c["position"] or 0, c["name"] or ""))

    def show(c, depth):
        kind = {0: "#", 2: "voice", 4: "", 5: "news#", 11: "thread", 12: "thread", 13: "stage",
                15: "forum", 16: "media"}.get(c["type"], f"t{c['type']}")
        extra = f"  [{c['messages']} msgs, oldest {c['oldest']}{', start' if c['reached_start'] else ''}]" \
            if c["messages"] else ""
        note = f"  ({c['note']})" if c["note"] else ""
        label = f"== {c['name']} ==" if c["type"] == 4 else f"{kind} {c['name']}"
        print(f"{'  ' * depth}{label}  {c['id']}{extra}{note}")
        for k in by_parent.get(c["id"], []):
            if k["type"] in (11, 12) and k["messages"] == 0 and depth >= 1:
                continue  # unexplored threads would flood the tree
            show(k, depth + 1)

    for c in by_parent.get(None, []):
        show(c, 0)


if __name__ == "__main__":
    sys.exit(main())

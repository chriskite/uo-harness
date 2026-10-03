"""Discord corpus -> vetted game facts (docs/NOTES.md "Discord knowledge base";
decision: docs/PLAN.md).

Turns the captured Outlands Discord history (harness/data/discord.db, filled by
discord_capture.py) into consolidated, likely-true facts:

  extract      one LLM call per window of chat (a UTC day of one channel, packed from
               discord_search.build_chunks conversation chunks up to WINDOW_CHARS) pulls
               grounded claims; every claim must cite message ids from its window and a
               verbatim quote, checked deterministically (failures are dropped).
  consolidate  claims are embedded (bge-small, via discord_search.model) and leader-
               clustered by cosine >= CLUSTER_SIM; an LLM adjudicates each cluster into
               one fact with a verdict, then deterministic rules recount the independent
               authors and cap the verdict (official needs an official claim, consensus
               needs >= 2 supporting authors outnumbering the contradicting ones).
  promote      official/consensus facts go into the overseer's knowledge table
               (`ctl know`, source community, or doc for patch notes/announcements),
               importance <= 6. Re-runs don't inflate confirmations; an entry the
               overseer retracted or superseded is never re-added; a fact whose verdict
               drops is retracted only if the pipeline added it and nobody confirmed it.
  digest       docs/research/DISCORD_KB.md for coding agents (committed).

Every stage is incremental and idempotent. Window ids hash (channel, day, part, first id,
last id, PROMPT_VERSION), so backfilled history only re-extracts the days it touches.
LLM calls run headless `omp` (MODEL, no tools, no repo context) CONCURRENCY at a time;
each run stops submitting once --max-cost USD is spent (exit 2; rerun to resume).
State: harness/data/discord_kb.db (gitignored, backed up by backup.py).

Needs the capture venv (numpy, fastembed):
  .venv-discord/Scripts/python.exe harness/discord_kb.py run [--channels a,b] [--max-cost 40]
  .venv-discord/Scripts/python.exe harness/discord_kb.py extract [--channels a,b] [--limit N]
  .venv-discord/Scripts/python.exe harness/discord_kb.py consolidate [--recluster]
  .venv-discord/Scripts/python.exe harness/discord_kb.py promote [--db harness/data/harness.db] [--dry-run]
  .venv-discord/Scripts/python.exe harness/discord_kb.py digest [--out docs/research/DISCORD_KB.md]
  .venv-discord/Scripts/python.exe harness/discord_kb.py search QUERY [--all] [-k 10]
  .venv-discord/Scripts/python.exe harness/discord_kb.py stats
"""

import argparse
import bisect
import collections
import concurrent.futures as cf
import datetime
import hashlib
import json
import os
import queue
import re
import sqlite3
import subprocess
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import discord_capture as dc  # noqa: E402
import discord_search as ds  # noqa: E402

KB_DB = os.path.join(dc.DATA, "discord_kb.db")
HARNESS_DB = os.path.join(dc.DATA, "harness.db")
DIGEST = os.path.join(dc.ROOT, "docs", "research", "DISCORD_KB.md")

MODEL = "sonnet"
EXTRACT_THINKING = "off"
ADJUDICATE_THINKING = "low"
CONCURRENCY = 4
MAX_COST = 40.0                 # USD per run
LLM_TIMEOUT = 600
RETRY_SLEEP = 30
PROMPT_VERSION = "x1"           # extraction prompt; part of every window id
ADJUDICATE_VERSION = "a1"
WINDOW_CHARS = 24000
CLUSTER_SIM = 0.86
ADJ_BATCH = 8                   # clusters per adjudication call
ADJ_RECENT = 25                 # non-official claims sent per cluster (newest)
EVIDENCE_MAX = 5
PROMOTE_IMPORTANCE_MAX = 6      # brief() standing items need >= 7; mined facts never get there

DEFAULT_CHANNELS = ("harvesting", "newplayer", "scripting", "template-builds", "patch-notes", "announcements")
OFFICIAL_CHANNELS = ("patch-notes", "announcements")
SECTIONS = ("skills & training", "harvesting & resources", "crafting", "combat & pvm", "new players",
            "scripting & razor", "economy", "housing & storage", "travel & locations",
            "systems & mechanics", "other")
VERDICTS = ("official", "consensus", "single_source", "disputed", "outdated", "wrong", "not_useful")
PROMOTE_VERDICTS = ("official", "consensus")
DIGEST_VERDICTS = ("official", "consensus", "single_source")
RETRACT_PREFIX = "discord-kb:"

SYSTEM_PROMPT = "You are a precise data-processing tool. Output exactly one JSON object and nothing else."

EXTRACT_PROMPT = """You extract factual knowledge about the MMO "Ultima Online: Outlands" from a Discord chat log
(channel #<CHANNEL>, <DAY>). Each line is "[message_id] time author: text".
Extract every claim a player could use: game mechanics, skills and training, items and their
properties, locations, NPCs, monsters, resources, crafting, procedures (how to do X), rules,
server systems. Rules:
- Only assertions. A question is not a claim; its answer is.
- Each claim is atomic and self-contained: resolve pronouns, name the item/skill/place.
- Skip jokes, trade ads, greetings, drama, personal anecdotes with nothing generalisable.
- kind: "fact" or "procedure".
- topic: a short lowercase noun phrase (e.g. "lumberjacking skill gain").
- stance: "asserts", or "corrects" when the message says another claim is wrong.
- uncertain: true when the author hedges ("I think", "not sure", "iirc").
- message_ids: the 1-3 message ids whose text states the claim (ids from this log only).
- quote: a verbatim excerpt (at most 120 characters) copied from one of those messages.
- entities: names of items, skills, places, NPCs or creatures in the claim.
Output: {"claims": [{"kind","topic","statement","stance","uncertain","message_ids","quote","entities"}]}
Output {"claims": []} when there is nothing.
"""

ADJUDICATE_PROMPT = """You consolidate community claims about "Ultima Online: Outlands" into facts. For each cluster
of similar claims below, decide what is most likely true NOW. Official claims (patch notes,
announcements) outrank players. A newer claim that a mechanic changed outranks older claims.
Count independent authors (A1, A2...), not messages.
For each cluster output:
 cluster_id, kind ("fact"/"procedure"), topic (short lowercase noun phrase),
 statement (one self-contained present-tense sentence or short procedure with its caveats),
 verdict: "official" | "consensus" | "single_source" | "disputed" | "outdated" | "wrong" | "not_useful",
 importance: 1-6 (how much a player loses by not knowing it),
 section: one of ["skills & training","harvesting & resources","crafting","combat & pvm",
   "new players","scripting & razor","economy","housing & storage","travel & locations",
   "systems & mechanics","other"],
 supporting_claim_ids, contradicting_claim_ids (the c<id> numbers as integers),
 tags: up to 5 lowercase words, rationale: at most 200 characters.
Output: {"facts": [ ... one object per cluster ... ]}
Each claim line is: c<claim_id> | date | authors | official=0/1 | stance | uncertain=0/1 | statement | "quote"
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS windows(
  id TEXT PRIMARY KEY, channel_id INTEGER, day TEXT, part TEXT, first_id INTEGER, last_id INTEGER,
  n_msgs INTEGER, prompt_version TEXT, model TEXT, status TEXT, error TEXT, claims_kept INTEGER,
  claims_dropped INTEGER, t REAL);
CREATE TABLE IF NOT EXISTS claims(
  id INTEGER PRIMARY KEY, window_id TEXT NOT NULL, channel_id INTEGER, kind TEXT, topic TEXT,
  statement TEXT, entities TEXT, stance TEXT, uncertain INTEGER, message_ids TEXT, author_ids TEXT,
  quote TEXT, first_ts TEXT, official INTEGER, cluster_id INTEGER, vec BLOB);
CREATE INDEX IF NOT EXISTS claims_window ON claims(window_id);
CREATE INDEX IF NOT EXISTS claims_cluster ON claims(cluster_id);
CREATE TABLE IF NOT EXISTS llm_calls(
  id INTEGER PRIMARY KEY, t REAL, stage TEXT, ref TEXT, model TEXT, input INTEGER, output INTEGER,
  cost REAL, seconds REAL, ok INTEGER, error TEXT);
-- AUTOINCREMENT: a dissolved cluster's id must never come back, its fact row still points at it
CREATE TABLE IF NOT EXISTS clusters(
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, leader_claim_id INTEGER, signature TEXT,
  adjudicated_signature TEXT);
CREATE TABLE IF NOT EXISTS facts(
  id INTEGER PRIMARY KEY, cluster_id INTEGER UNIQUE, kind TEXT, topic TEXT, statement TEXT, section TEXT,
  verdict TEXT, confidence REAL, importance INTEGER, support_authors INTEGER, contradict_authors INTEGER,
  first_seen TEXT, last_seen TEXT, entities TEXT, tags TEXT, evidence TEXT, rationale TEXT, model TEXT,
  prompt_version TEXT, updated_t REAL);
-- what promote put into which knowledge store (target = normcase'd absolute path)
CREATE TABLE IF NOT EXISTS promotions(
  target TEXT NOT NULL, fact_id INTEGER NOT NULL, knowledge_id INTEGER, knowledge_action TEXT,
  promoted_hash TEXT, knowledge_status TEXT, t REAL, PRIMARY KEY(target, fact_id));
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
  topic, statement, tags, content='facts', content_rowid='id', tokenize='porter unicode61');
CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
  INSERT INTO facts_fts(rowid, topic, statement, tags) VALUES (new.id, new.topic, new.statement, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
  INSERT INTO facts_fts(facts_fts, rowid, topic, statement, tags)
  VALUES ('delete', old.id, old.topic, old.statement, old.tags);
END;
CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
  INSERT INTO facts_fts(facts_fts, rowid, topic, statement, tags)
  VALUES ('delete', old.id, old.topic, old.statement, old.tags);
  INSERT INTO facts_fts(rowid, topic, statement, tags) VALUES (new.id, new.topic, new.statement, new.tags);
END;
"""


class LLMError(Exception):
    pass


class Truncated(Exception):
    """The reply hit the output limit; the caller splits its input."""


# ---------------------------------------------------------------------------- LLM calls

CALLS = queue.SimpleQueue()   # one record per attempt, drained into llm_calls on the main thread


def _omp(prompt_text, model, thinking):
    """One headless omp call. Returns (exit code, assistant message or None, stderr tail)."""
    tmpdir = tempfile.gettempdir()
    fd, path = tempfile.mkstemp(prefix="discord_kb_", suffix=".txt", dir=tmpdir)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(prompt_text)
    try:
        # cwd = temp dir so no repo context files load; stdin closed or omp waits on it
        p = subprocess.run(
            ["omp", "-p", "--no-tools", "--no-session", "--no-extensions", "--no-skills", "--no-rules",
             "--no-title", "--model", model, "--thinking", thinking, "--mode", "json",
             "--system-prompt", SYSTEM_PROMPT, f"@{path}", "Process the attached file exactly as it instructs."],
            cwd=tmpdir, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=LLM_TIMEOUT)
    finally:
        os.remove(path)
    msg = None
    for line in p.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        m = ev.get("message") or {}
        if ev.get("type") == "message_end" and m.get("role") == "assistant":
            msg = m
    return p.returncode, msg, (p.stderr or "")[-300:]


def parse_json(text):
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except ValueError:
        i, j = t.find("{"), t.rfind("}")
        if i < 0 or j <= i:
            raise ValueError("no JSON object in the reply")
        return json.loads(t[i:j + 1])


def llm_json(prompt_text, model, thinking, stage, ref=None, validate=None):
    """Run one prompt; returns (parsed JSON object, meta). validate(obj) raises ValueError
    for a reply the caller can't use, which gets one corrective retry."""
    text, infra_retried, fix_retried = prompt_text, False, False
    while True:
        t0 = time.time()
        try:
            rc, msg, err = _omp(text, model, thinking)
        except subprocess.TimeoutExpired:
            rc, msg, err = -1, None, "timeout"
        usage = (msg or {}).get("usage") or {}
        # usage.input excludes prompt-cache reads/writes; the sum is the real prompt size
        prompt_tokens = sum(usage.get(x) or 0 for x in ("input", "cacheRead", "cacheWrite")) if usage else None
        meta = {"model": (msg or {}).get("model"), "input": prompt_tokens, "output": usage.get("output"),
                "cost": (usage.get("cost") or {}).get("total") or 0.0, "stop": (msg or {}).get("stopReason")}
        rec = {"t": t0, "stage": stage, "ref": ref, "model": meta["model"], "input": meta["input"],
               "output": meta["output"], "cost": meta["cost"], "seconds": round(time.time() - t0, 1),
               "ok": 0, "error": None}
        if rc != 0 or msg is None or meta["stop"] in ("error", "aborted"):
            rec["error"] = f"exit {rc} stop {meta['stop']}: {err}"
            CALLS.put(rec)
            if infra_retried:
                raise LLMError(rec["error"])
            infra_retried = True
            time.sleep(RETRY_SLEEP)
            continue
        if meta["stop"] in ("length", "max_tokens"):
            rec["error"] = "truncated"
            CALLS.put(rec)
            raise Truncated(f"output truncated ({meta['output']} tokens)")
        try:
            out = parse_json("".join(c.get("text", "") for c in msg.get("content") or ()
                                     if c.get("type") == "text"))
            if not isinstance(out, dict):
                raise ValueError("the reply is not a JSON object")
            if validate:
                validate(out)
        except ValueError as e:
            rec["error"] = f"invalid: {e}"[:500]
            CALLS.put(rec)
            if fix_retried:
                raise LLMError(rec["error"])
            fix_retried = True
            text = (prompt_text + f"\n\nYour previous output was invalid: {e}. "
                    "Output only the corrected JSON object.")
            continue
        rec["ok"] = 1
        CALLS.put(rec)
        return out, meta


class Budget:
    """Spend of this run (USD), from the llm_calls records drained so far."""

    def __init__(self, max_cost=MAX_COST):
        self.max_cost, self.spent, self.hit = max_cost, 0.0, False

    def drain(self, kb):
        while True:
            try:
                r = CALLS.get_nowait()
            except queue.Empty:
                break
            kb.execute("INSERT INTO llm_calls(t, stage, ref, model, input, output, cost, seconds, ok, error) "
                       "VALUES(:t, :stage, :ref, :model, :input, :output, :cost, :seconds, :ok, :error)", r)
            self.spent += r["cost"] or 0.0
        kb.commit()

    def exhausted(self):
        return self.spent >= self.max_cost


def run_pool(kb, budget, jobs, work, done):
    """work(job) runs on CONCURRENCY threads; done(job, future, jobs) on this thread (it may
    queue more jobs). Stops submitting once the budget is spent; in-flight calls finish."""
    jobs, pending = collections.deque(jobs), {}
    with cf.ThreadPoolExecutor(CONCURRENCY) as ex:
        while jobs or pending:
            while jobs and len(pending) < CONCURRENCY and not budget.exhausted():
                job = jobs.popleft()
                pending[ex.submit(work, job)] = job
            if not pending:
                break
            finished, _ = cf.wait(pending, return_when=cf.FIRST_COMPLETED)
            for f in finished:
                job = pending.pop(f)
                budget.drain(kb)
                done(job, f, jobs)
    budget.drain(kb)
    if jobs:
        budget.hit = True


# ---------------------------------------------------------------------------- storage

def open_kb(path=KB_DB):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    kb = sqlite3.connect(path, timeout=30)
    kb.execute("PRAGMA journal_mode=WAL")
    kb.executescript(SCHEMA)
    return kb


def _utc(mid):
    return datetime.datetime.fromtimestamp(dc.snowflake_ms(mid) / 1000, datetime.timezone.utc)


def _sha1(s):
    return hashlib.sha1(s.encode()).hexdigest()


class Msgs:
    """Read-only view of discord.db: channel names and message locations for links."""

    def __init__(self, path=dc.DEFAULT_DB):
        self.con = ds._open_msgs(path)
        self.names = {i: n for i, n in self.con.execute("SELECT id, name FROM channels") if n}
        self.ids = {}
        for i, n in self.names.items():
            self.ids.setdefault(n, i)
        self._loc = {}

    def link(self, mid):
        if mid not in self._loc:
            r = self.con.execute("SELECT guild_id, channel_id FROM messages WHERE id=?", (mid,)).fetchone()
            self._loc[mid] = r
        r = self._loc[mid]
        return f"https://discord.com/channels/{r[0] or '@me'}/{r[1]}/{mid}" if r else None

    def range(self, channel_ids):
        if not channel_ids:
            return None, None
        q = ",".join("?" * len(channel_ids))
        return self.con.execute(f"SELECT min(ts), max(ts) FROM messages WHERE channel_id IN ({q})",
                                list(channel_ids)).fetchone()


# ---------------------------------------------------------------------------- extraction

def _body(content, embed_text):
    return " ".join(x for x in (content, embed_text) if x).strip()   # as discord_search.build_chunks


def render(r):
    """r: (id, channel_id, guild_id, author, content, embed_text, reply_to, author_id, ts)."""
    mid, author, reply_to = r[0], r[3], r[6]
    body = _body(r[4], r[5]).replace("\r\n", "\n").replace("\n", " / ")
    reply = f" (reply to {reply_to})" if reply_to else ""
    return f"[{mid}] {_utc(mid):%Y-%m-%d %H:%M} UTC {author or '?'}{reply}: {body}"


def _window(channel_id, name, day, part, units):
    """units: list of chunks, each a list of (line, row)."""
    rows = [r for u in units for _, r in u]
    first, last = rows[0][0], rows[-1][0]
    return {"id": _sha1(f"{channel_id}:{day}:{part}:{first}:{last}:{PROMPT_VERSION}"),
            "channel_id": channel_id, "name": name, "day": day, "part": str(part), "units": units,
            "first_id": first, "last_id": last, "n_msgs": len(rows), "half": False}


def halves(w):
    """The window split in two (whole chunks if it has several, else lines), or None."""
    units = w["units"] if len(w["units"]) >= 2 else [[x] for x in w["units"][0]]
    if len(units) < 2:
        return None
    k = len(units) // 2
    out = []
    for suffix, part in (("a", units[:k]), ("b", units[k:])):
        h = _window(w["channel_id"], w["name"], w["day"], w["part"] + suffix, part)
        h["half"] = True
        out.append(h)
    return out


def channel_windows(msgs, channel_id, name, today):
    """Stable windows of one channel for every UTC day strictly before `today`."""
    rows = msgs.con.execute("SELECT id, channel_id, guild_id, author, content, embed_text, reply_to, "
                            "author_id, ts FROM messages WHERE channel_id=? ORDER BY id", (channel_id,)).fetchall()
    by_day = collections.defaultdict(list)
    for r in rows:
        by_day[f"{_utc(r[0]):%Y-%m-%d}"].append(r)
    out = []
    for day in sorted(by_day):
        if day >= today:
            continue
        drows = by_day[day]
        ids = [r[0] for r in drows]
        units = []
        for c in ds.build_chunks([r[:6] for r in drows], msgs.names):
            lo, hi = bisect.bisect_left(ids, c["first_id"]), bisect.bisect_right(ids, c["last_id"])
            units.append([(render(r), r) for r in drows[lo:hi] if _body(r[4], r[5])])
        part, cur, size = 0, [], 0
        for u in units:
            n = sum(len(line) + 1 for line, _ in u)
            if cur and size + n > WINDOW_CHARS:
                out.append(_window(channel_id, name, day, part, cur))
                part, cur, size = part + 1, [], 0
            cur.append(u)
            size += n
        if cur:
            out.append(_window(channel_id, name, day, part, cur))
    return out


def extract_prompt(w):
    lines = "\n".join(line for u in w["units"] for line, _ in u)
    return EXTRACT_PROMPT.replace("<CHANNEL>", w["name"]).replace("<DAY>", w["day"]) + "\n" + lines + "\n"


def _shape_claims(out):
    if not isinstance(out.get("claims"), list):
        raise ValueError('expected {"claims": [...]}')


_NORM_DROP = str.maketrans("", "", "*_`~>")


def norm(s):
    return " ".join((s or "").lower().translate(_NORM_DROP).split())


def _int_id(x):
    try:
        return int(str(x).strip().strip("[]c"))
    except ValueError:
        return None


def check_claims(w, raw, official):
    """Deterministic grounding checks. Returns (kept claim rows, dropped count)."""
    msgs = {r[0]: r for u in w["units"] for _, r in u}
    kept, dropped = [], 0
    for c in raw:
        try:
            kind, stance = c.get("kind"), c.get("stance")
            topic, statement = str(c.get("topic") or "").strip(), str(c.get("statement") or "").strip()
            mids = [_int_id(x) for x in (c.get("message_ids") or [])]
            quote = str(c.get("quote") or "").strip().strip('"\u201c\u201d').strip(". \u2026")
        except (AttributeError, TypeError):
            dropped += 1
            continue
        nq = norm(quote)
        cited = [msgs[i] for i in mids if i in msgs]
        if (kind not in ("fact", "procedure") or stance not in ("asserts", "corrects")
                or not topic or not statement or len(topic) > 300 or len(statement) > 300
                or not mids or len(cited) != len(mids) or not nq
                or not any(nq in norm(r[4] + " " + (r[5] or "")) or nq in norm(_body(r[4], r[5]).replace("\n", " / "))
                           for r in cited)):
            dropped += 1
            continue
        ents = c.get("entities") if isinstance(c.get("entities"), list) else []
        kept.append({
            "window_id": w["id"], "channel_id": w["channel_id"], "kind": kind, "topic": topic,
            "statement": statement, "entities": json.dumps(sorted({str(e).strip() for e in ents if str(e).strip()})),
            "stance": stance, "uncertain": int(bool(c.get("uncertain"))),
            "message_ids": json.dumps(sorted(set(mids))),
            "author_ids": json.dumps(sorted({r[7] for r in cited if r[7] is not None})),
            "quote": quote, "first_ts": min(r[8] for r in cited), "official": int(official)})
    return kept, dropped


def _store_window(kb, w, status, model=None, error=None, kept=None, dropped=None):
    kb.execute("DELETE FROM claims WHERE window_id=?", (w["id"],))
    kb.execute("INSERT OR REPLACE INTO windows VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (w["id"], w["channel_id"], w["day"], w["part"], w["first_id"], w["last_id"], w["n_msgs"],
                PROMPT_VERSION, model, status, error, len(kept) if kept is not None else None, dropped,
                time.time()))
    for c in kept or ():
        kb.execute("INSERT INTO claims(window_id, channel_id, kind, topic, statement, entities, stance, uncertain, "
                   "message_ids, author_ids, quote, first_ts, official) VALUES (:window_id, :channel_id, :kind, "
                   ":topic, :statement, :entities, :stance, :uncertain, :message_ids, :author_ids, :quote, "
                   ":first_ts, :official)", c)
    kb.commit()


def resolve_channels(msgs, names):
    out = []
    for n in names:
        cid = msgs.ids.get(n) or (int(n) if str(n).isdigit() else None)
        if cid is None:
            print(f"channel {n}: unknown, skipped")
            continue
        if not msgs.con.execute("SELECT 1 FROM messages WHERE channel_id=? LIMIT 1", (cid,)).fetchone():
            print(f"channel {msgs.names.get(cid, n)}: no stored messages, skipped")
            continue
        out.append((cid, msgs.names.get(cid, str(cid))))
    return out


def extract(kb, msgs, budget, channels=DEFAULT_CHANNELS, limit=None, today=None, log=print):
    today = today or f"{datetime.datetime.now(datetime.timezone.utc):%Y-%m-%d}"
    chans = resolve_channels(msgs, channels)
    stored = {r[0]: r[1:] for r in kb.execute(
        f"SELECT id, status, channel_id FROM windows WHERE channel_id IN ({','.join('?' * len(chans))})",
        [c for c, _ in chans])} if chans else {}
    todo, keep = [], set()
    for cid, name in chans:
        for w in channel_windows(msgs, cid, name, today):
            keep.add(w["id"])
            st = stored.get(w["id"], (None,))[0]
            if st == "split":
                for h in halves(w) or ():
                    keep.add(h["id"])
                    if stored.get(h["id"], (None,))[0] in (None, "failed"):
                        todo.append(h)
            elif st in (None, "failed"):
                todo.append(w)
    stale = [i for i in stored if i not in keep]
    if stale:
        for i in stale:
            kb.execute("DELETE FROM claims WHERE window_id=?", (i,))
            kb.execute("DELETE FROM windows WHERE id=?", (i,))
        kb.commit()
        log(f"dropped {len(stale)} stale windows")
    if limit is not None:
        todo = todo[:limit]
    log(f"extract: {len(todo)} windows to process over {len(chans)} channels")
    official = {cid for cid, name in chans if name in OFFICIAL_CHANNELS}
    stats = collections.Counter()

    def work(w):
        return llm_json(extract_prompt(w), MODEL, EXTRACT_THINKING, "extract", ref=w["id"], validate=_shape_claims)

    def done(w, f, jobs):
        try:
            out, meta = f.result()
        except Truncated as e:
            hs = None if w["half"] else halves(w)
            if hs:
                _store_window(kb, w, "split", error=str(e))
                jobs.extend(hs)
                stats["split"] += 1
            else:
                _store_window(kb, w, "failed", error=str(e))
                stats["failed"] += 1
            return
        except LLMError as e:
            _store_window(kb, w, "failed", error=str(e))
            stats["failed"] += 1
            log(f"  #{w['name']} {w['day']}/{w['part']}: failed: {e}")
            return
        kept, dropped = check_claims(w, [c for c in out["claims"] if isinstance(c, dict)],
                                     w["channel_id"] in official)
        dropped += sum(1 for c in out["claims"] if not isinstance(c, dict))
        _store_window(kb, w, "ok", model=meta["model"], kept=kept, dropped=dropped)
        stats["ok"] += 1
        stats["kept"] += len(kept)
        stats["dropped"] += dropped
        log(f"  #{w['name']} {w['day']}/{w['part']}: {len(kept)} claims, {dropped} dropped "
            f"(${budget.spent:.2f} so far)")

    run_pool(kb, budget, todo, work, done)
    if budget.hit:
        log(f"cost guard: ${budget.spent:.2f} >= ${budget.max_cost:.2f}, stopped submitting")
    return dict(stats)


# ---------------------------------------------------------------------------- consolidation

def embed(texts):
    """L2-normalised float32 embeddings (n, d)."""
    v = np.asarray(list(ds.model().embed(texts, batch_size=64)), dtype=np.float32)
    return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)


def _dissolve(kb, cluster_id):
    kb.execute("UPDATE claims SET cluster_id=NULL WHERE cluster_id=?", (cluster_id,))
    kb.execute("DELETE FROM clusters WHERE id=?", (cluster_id,))
    kb.execute("UPDATE facts SET verdict='dissolved', confidence=0.0, updated_t=? WHERE cluster_id=?",
               (time.time(), cluster_id))


def cluster(kb, recluster=False, log=print):
    if recluster:
        kb.execute("UPDATE claims SET cluster_id=NULL")
        kb.execute("DELETE FROM clusters")
        kb.execute("UPDATE facts SET verdict='dissolved', confidence=0.0, updated_t=? WHERE verdict!='dissolved'",
                   (time.time(),))
    todo = kb.execute("SELECT id, topic, statement FROM claims WHERE vec IS NULL ORDER BY id").fetchall()
    for i in range(0, len(todo), 256):
        batch = todo[i:i + 256]
        vecs = embed([f"{t}: {s}" for _, t, s in batch])
        kb.executemany("UPDATE claims SET vec=? WHERE id=?", [(v.tobytes(), cid) for (cid, _, _), v in zip(batch, vecs)])
    if todo:
        log(f"embedded {len(todo)} claims")
    orphans = [r[0] for r in kb.execute("SELECT id FROM clusters WHERE leader_claim_id NOT IN (SELECT id FROM claims)")]
    for c in orphans:
        _dissolve(kb, c)
    # clusters whose members all went (a leader is a member, so this only catches inconsistency)
    for (c,) in kb.execute("SELECT id FROM clusters WHERE id NOT IN (SELECT cluster_id FROM claims "
                           "WHERE cluster_id IS NOT NULL)").fetchall():
        _dissolve(kb, c)
    if orphans:
        log(f"dissolved {len(orphans)} clusters whose leader claim was dropped")
    leaders = {}   # kind -> [ids list, matrix rows list]
    for cid, kind, vec in kb.execute("SELECT c.id, c.kind, l.vec FROM clusters c JOIN claims l ON l.id=c.leader_claim_id "
                                     "ORDER BY c.id"):
        leaders.setdefault(kind, ([], []))
        leaders[kind][0].append(cid)
        leaders[kind][1].append(np.frombuffer(vec, dtype=np.float32))
    mats = {k: (ids, np.vstack(rows) if rows else None) for k, (ids, rows) in leaders.items()}
    new = joined = 0
    for claim_id, kind, vec in kb.execute("SELECT id, kind, vec FROM claims WHERE cluster_id IS NULL ORDER BY id").fetchall():
        v = np.frombuffer(vec, dtype=np.float32)
        ids, m = mats.get(kind, ([], None))
        if m is not None and len(ids):
            sims = m[:len(ids)] @ v
            best = int(np.argmax(sims))
            if sims[best] >= CLUSTER_SIM:
                kb.execute("UPDATE claims SET cluster_id=? WHERE id=?", (ids[best], claim_id))
                joined += 1
                continue
        cur = kb.execute("INSERT INTO clusters(kind, leader_claim_id) VALUES (?, ?)", (kind, claim_id))
        kb.execute("UPDATE claims SET cluster_id=? WHERE id=?", (cur.lastrowid, claim_id))
        ids = ids + [cur.lastrowid]
        if m is None:
            m = np.empty((64, v.shape[0]), dtype=np.float32)
        elif len(ids) > m.shape[0]:
            m = np.vstack([m, np.empty_like(m)])
        m[len(ids) - 1] = v
        mats[kind] = (ids, m)
        new += 1
    members = collections.defaultdict(list)
    for cid, claim_id in kb.execute("SELECT cluster_id, id FROM claims WHERE cluster_id IS NOT NULL"):
        members[cid].append(claim_id)
    kb.executemany("UPDATE clusters SET signature=? WHERE id=?",
                   [(_sha1(json.dumps(sorted(ms))), cid) for cid, ms in members.items()])
    kb.commit()
    log(f"clusters: {new} new, {joined} claims joined existing ones")
    return {"new": new, "joined": joined, "dissolved": len(orphans)}


CLAIM_COLS = ("id", "kind", "topic", "statement", "entities", "stance", "uncertain", "message_ids",
              "author_ids", "quote", "first_ts", "official")


def _claims_of(kb, cluster_id):
    rows = kb.execute(f"SELECT {', '.join(CLAIM_COLS)} FROM claims WHERE cluster_id=? ORDER BY first_ts, id",
                      (cluster_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(zip(CLAIM_COLS, r))
        for k in ("entities", "message_ids", "author_ids"):
            d[k] = json.loads(d[k] or "[]")
        out.append(d)
    return out


def cluster_block(cluster_id, kind, claims):
    """The prompt text of one cluster: all official claims plus the ADJ_RECENT newest others."""
    others = [c for c in claims if not c["official"]]
    sent = sorted([c for c in claims if c["official"]] + others[-ADJ_RECENT:], key=lambda c: (c["first_ts"], c["id"]))
    labels = {}
    for c in sent:
        for a in c["author_ids"]:
            labels.setdefault(a, f"A{len(labels) + 1}")
    lines = [f"Cluster {cluster_id} ({kind}, {len(claims)} claims, {len(sent)} shown):"]
    for c in sent:
        q = c["quote"].replace('"', "'")
        lines.append(f"c{c['id']} | {c['first_ts'][:10]} | {','.join(labels[a] for a in c['author_ids']) or '?'} | "
                     f"official={c['official']} | {c['stance']} | uncertain={c['uncertain']} | "
                     f"{c['statement']} | \"{q}\"")
    return "\n".join(lines)


def apply_rules(verdict, support_authors, contradict_authors, official_support):
    """Deterministic caps on the model's verdict."""
    if verdict == "official" and not official_support:
        verdict = "consensus"
    if verdict == "consensus" and not (support_authors >= 2 and support_authors > contradict_authors):
        verdict = "single_source" if support_authors == 1 else "disputed"
    if verdict == "single_source" and contradict_authors >= 1:
        verdict = "disputed"
    return verdict


def confidence(verdict, support_authors):
    if verdict == "official":
        return 0.85
    if verdict == "consensus":
        return round(min(0.80, 0.65 + 0.05 * (support_authors - 2)), 2)
    return {"single_source": 0.50, "disputed": 0.35}.get(verdict, 0.0)


def _ids(xs):
    return [i for i in (_int_id(x) for x in (xs if isinstance(xs, list) else [])) if i is not None]


def make_fact(cluster_id, kind, claims, f, model):
    """A fact row from the model's object for this cluster plus the deterministic rules."""
    by_id = {c["id"]: c for c in claims}
    sup = [by_id[i] for i in dict.fromkeys(_ids(f.get("supporting_claim_ids"))) if i in by_id]
    con = [by_id[i] for i in dict.fromkeys(_ids(f.get("contradicting_claim_ids"))) if i in by_id]
    sa = len({a for c in sup for a in c["author_ids"]})
    ca = len({a for c in con for a in c["author_ids"]})
    verdict = apply_rules(f["verdict"], sa, ca, any(c["official"] for c in sup))
    basis = sup or claims
    ev = []
    ordered = sorted(basis, key=lambda c: c["first_ts"], reverse=True)
    ordered = [c for c in ordered if c["official"]] + [c for c in ordered if not c["official"]]
    for c in ordered:
        for m in c["message_ids"]:
            if m not in ev and len(ev) < EVIDENCE_MAX:
                ev.append(m)
    tags = [str(t).strip().lower() for t in (f.get("tags") if isinstance(f.get("tags"), list) else [])][:5]
    try:
        imp = int(f.get("importance") or 1)
    except (TypeError, ValueError):
        imp = 1
    return {
        "cluster_id": cluster_id, "kind": f.get("kind") if f.get("kind") in ("fact", "procedure") else kind,
        "topic": str(f["topic"]).strip(), "statement": str(f["statement"]).strip(), "section": f["section"],
        "verdict": verdict, "confidence": confidence(verdict, sa), "importance": max(1, min(6, imp)),
        "support_authors": sa, "contradict_authors": ca,
        "first_seen": min(c["first_ts"] for c in basis)[:10], "last_seen": max(c["first_ts"] for c in basis)[:10],
        "entities": json.dumps(sorted({e for c in basis for e in c["entities"]})),
        "tags": json.dumps([t for t in tags if t]), "evidence": json.dumps(ev),
        "rationale": str(f.get("rationale") or "")[:300], "model": model, "prompt_version": ADJUDICATE_VERSION,
        "updated_t": time.time()}


FACT_COLS = ("cluster_id", "kind", "topic", "statement", "section", "verdict", "confidence", "importance",
             "support_authors", "contradict_authors", "first_seen", "last_seen", "entities", "tags", "evidence",
             "rationale", "model", "prompt_version", "updated_t")


def upsert_fact(kb, fact):
    kb.execute(f"INSERT INTO facts({', '.join(FACT_COLS)}) VALUES ({', '.join(':' + c for c in FACT_COLS)}) "
               f"ON CONFLICT(cluster_id) DO UPDATE SET "
               + ", ".join(f"{c}=excluded.{c}" for c in FACT_COLS if c != "cluster_id"), fact)


def _validator(cluster_ids):
    want = set(cluster_ids)

    def check(out):
        facts = out.get("facts")
        if not isinstance(facts, list):
            raise ValueError('expected {"facts": [...]}')
        got = {}
        for f in facts:
            if not isinstance(f, dict):
                raise ValueError("every fact must be an object")
            cid = _int_id(f.get("cluster_id"))
            if cid not in want:
                continue
            if f.get("verdict") not in VERDICTS:
                raise ValueError(f"cluster {cid}: verdict {f.get('verdict')!r} is not one of {VERDICTS}")
            if f.get("section") not in SECTIONS:
                raise ValueError(f"cluster {cid}: section {f.get('section')!r} is not one of {SECTIONS}")
            if not str(f.get("topic") or "").strip() or not str(f.get("statement") or "").strip():
                raise ValueError(f"cluster {cid}: topic and statement are required")
            got[cid] = f
        missing = want - got.keys()
        if missing:
            raise ValueError(f"missing clusters {sorted(missing)}")
        out["_by_cluster"] = got
    return check


def adjudicate(kb, budget, log=print):
    todo = kb.execute("SELECT id, kind, signature FROM clusters WHERE signature IS NOT adjudicated_signature "
                      "ORDER BY id").fetchall()
    log(f"adjudicate: {len(todo)} clusters")
    batches = [todo[i:i + ADJ_BATCH] for i in range(0, len(todo), ADJ_BATCH)]
    stats = collections.Counter()

    def work(batch):
        ro = sqlite3.connect(kb_path)   # own connection per call: sqlite3 connections aren't thread-shared
        try:
            blocks = {cid: (kind, sig, _claims_of(ro, cid)) for cid, kind, sig in batch}
        finally:
            ro.close()
        prompt = ADJUDICATE_PROMPT + "\n" + "\n\n".join(cluster_block(cid, kind, cl)
                                                        for cid, (kind, _, cl) in blocks.items()) + "\n"
        out, meta = llm_json(prompt, MODEL, ADJUDICATE_THINKING, "adjudicate",
                             ref=",".join(str(c) for c, _, _ in batch), validate=_validator(blocks))
        return out, meta, blocks

    def done(batch, f, jobs):
        try:
            out, meta, blocks = f.result()
        except Truncated:
            if len(batch) > 1:
                k = len(batch) // 2
                jobs.extend([batch[:k], batch[k:]])
                stats["split"] += 1
            else:
                stats["failed"] += 1
                log(f"  cluster {batch[0][0]}: truncated")
            return
        except LLMError as e:
            stats["failed"] += len(batch)
            log(f"  clusters {[c for c, _, _ in batch]}: failed: {e}")
            return
        for cid, (kind, sig, claims) in blocks.items():
            fact = make_fact(cid, kind, claims, out["_by_cluster"][cid], meta["model"])
            upsert_fact(kb, fact)
            kb.execute("UPDATE clusters SET adjudicated_signature=? WHERE id=?", (sig, cid))
            stats[fact["verdict"]] += 1
        kb.commit()
        stats["done"] += len(blocks)
        if stats["done"] % (ADJ_BATCH * 10) < len(blocks):
            log(f"  {stats['done']}/{len(todo)} clusters (${budget.spent:.2f} so far)")

    kb.commit()   # workers read the clusters and claims written above
    kb_path = kb.execute("PRAGMA database_list").fetchone()[2]
    run_pool(kb, budget, batches, work, done)
    if budget.hit:
        log(f"cost guard: ${budget.spent:.2f} >= ${budget.max_cost:.2f}, stopped submitting")
    return dict(stats)


def consolidate(kb, budget, recluster=False, log=print):
    return {"cluster": cluster(kb, recluster, log), "adjudicate": adjudicate(kb, budget, log)}


# ---------------------------------------------------------------------------- promotion

def promote_hash(kind, topic, statement, conf, importance):
    return _sha1(f"{kind}|{topic}|{statement}|{conf:.2f}|{importance}")


def promote(kb, msgs, db=HARNESS_DB, dry_run=False, log=print):
    """Official/consensus facts into the knowledge store `db`. What was promoted where is
    kept per target DB (promotions), so a trial run into a copy never mixes up the real
    store's entry ids."""
    import knowledge
    import memory
    target = os.path.normcase(os.path.abspath(db))
    k = knowledge.Knowledge(memory.connect(db))
    counts = collections.Counter()
    cols = ("id", "kind", "topic", "statement", "verdict", "confidence", "importance", "tags", "entities", "evidence")
    pcols = ("knowledge_id", "knowledge_action", "promoted_hash", "knowledge_status")
    facts = [dict(zip(cols + pcols, r)) for r in kb.execute(
        f"SELECT {', '.join('f.' + c for c in cols)}, {', '.join('p.' + c for c in pcols)} FROM facts f "
        "LEFT JOIN promotions p ON p.fact_id = f.id AND p.target = ? ORDER BY f.id", (target,))]

    def setf(fid, **kw):
        if not dry_run:
            kb.execute("INSERT INTO promotions(target, fact_id) VALUES (?, ?) ON CONFLICT DO NOTHING", (target, fid))
            kb.execute(f"UPDATE promotions SET {', '.join(c + '=?' for c in kw)}, t=? WHERE target=? AND fact_id=?",
                       [*kw.values(), time.time(), target, fid])

    # retractions first, so a re-clustered fact's new wording doesn't confirm its predecessor
    for f in facts:
        if f["knowledge_id"] is None or f["verdict"] in PROMOTE_VERDICTS:
            continue
        e = k.get(f["knowledge_id"])
        if (f["knowledge_action"] in ("added", "superseded") and e and e["status"] == "active"
                and e["confirmations"] == 0):
            counts["retract"] += 1
            if not dry_run:
                k.retract(f["knowledge_id"], f"{RETRACT_PREFIX} verdict now {f['verdict']}")
            setf(f["id"], knowledge_status="retracted")

    def blocked(f):
        """An entry with this content that someone other than the pipeline retracted."""
        h = knowledge.content_hash(f["kind"], f["statement"])
        r = k.con.execute("SELECT retract_reason FROM knowledge WHERE content_hash=? AND status='retracted'",
                          (h,)).fetchall()
        return any(not (x or "").startswith(RETRACT_PREFIX) for (x,) in r)

    for f in facts:
        if f["verdict"] not in PROMOTE_VERDICTS:
            continue
        ev = json.loads(f["evidence"] or "[]")
        imp = max(1, min(PROMOTE_IMPORTANCE_MAX, f["importance"] or 1))
        conf = f["confidence"]
        h = promote_hash(f["kind"], f["topic"], f["statement"], conf, imp)
        args = {"tags": ["discord"] + json.loads(f["tags"] or "[]"), "entities": json.loads(f["entities"] or "[]"),
                "source": "doc" if f["verdict"] == "official" else "community",
                "ref": f"{RETRACT_PREFIX}{f['id']}" + (f" {msgs.link(ev[0])}" if ev and msgs.link(ev[0]) else ""),
                "confidence": conf, "importance": imp}
        kid = f["knowledge_id"]
        e = k.get(kid) if kid is not None else None
        if kid is not None and (e is None or e["status"] != "active"):
            ours = e is not None and e["status"] == "retracted" and (e["retract_reason"] or "").startswith(RETRACT_PREFIX)
            if not ours:
                if f["knowledge_status"] != (e["status"] if e else "missing"):
                    setf(f["id"], knowledge_status=e["status"] if e else "missing")
                counts["kept_out"] += 1
                continue
            kid = None   # the pipeline retracted it itself; the fact is back
        if kid is None:
            if blocked(f):
                setf(f["id"], knowledge_status="retracted")
                counts["kept_out"] += 1
                continue
            counts["add"] += 1
            if not dry_run:
                r = k.add(f["kind"], f["topic"], f["statement"], **args)
                setf(f["id"], knowledge_id=r["id"], knowledge_action=r["action"], promoted_hash=h,
                     knowledge_status="active")
                counts[r["action"]] += 1
            continue
        if f["promoted_hash"] == h:
            counts["unchanged"] += 1
            continue
        counts["update"] += 1
        if dry_run:
            continue
        if f["knowledge_action"] == "confirmed":
            # the entry isn't ours (we only confirmed it): leave it, file the new wording separately
            r = k.add(f["kind"], f["topic"], f["statement"], **args)
            setf(f["id"], knowledge_id=r["id"], knowledge_action=r["action"], promoted_hash=h,
                 knowledge_status="active")
        elif e["topic"] == f["topic"] and e["content"] == f["statement"]:
            k.update(kid, confidence=conf, importance=imp)
            setf(f["id"], promoted_hash=h, knowledge_status="active")
        else:
            r = k.update(kid, content=f["statement"], topic=f["topic"], confidence=conf, importance=imp,
                         source=args["source"], ref=args["ref"], tags=args["tags"], entities=args["entities"])
            setf(f["id"], knowledge_id=r["id"], knowledge_action="superseded", promoted_hash=h,
                 knowledge_status="active")
    if not dry_run:
        kb.commit()
    k.con.close()
    log(("promote (dry run): " if dry_run else "promote: ") + json.dumps(dict(counts)))
    return dict(counts)


# ---------------------------------------------------------------------------- consumers

def digest(kb, msgs, out=DIGEST, log=print):
    now = datetime.datetime.now(datetime.timezone.utc)
    chans = [r[0] for r in kb.execute("SELECT DISTINCT channel_id FROM windows WHERE status='ok' ORDER BY channel_id")]
    lo, hi = msgs.range(chans)
    verdicts = dict(kb.execute("SELECT verdict, count(*) FROM facts GROUP BY verdict ORDER BY 2 DESC"))
    models = [r[0] for r in kb.execute("SELECT DISTINCT model FROM llm_calls WHERE ok=1 AND model IS NOT NULL")]
    lines = [
        "# Outlands Discord knowledge base",
        "",
        f"Generated by `harness/discord_kb.py digest` on {now:%Y-%m-%d %H:%M} UTC. Do not edit by hand.",
        "",
        f"- Corpus: {(lo or '?')[:10]} to {(hi or '?')[:10]}, channels "
        + ", ".join(f"#{msgs.names.get(c, c)}" for c in chans) + ".",
        "- Facts by verdict: " + ", ".join(f"{v} {n}" for v, n in verdicts.items()) + ".",
        f"- Model: {', '.join(models) or MODEL}; extraction prompt {PROMPT_VERSION}, adjudication {ADJUDICATE_VERSION}.",
        "- Confidence: everything here is community-derived. `official` = stated in #patch-notes or "
        "#announcements; `consensus` = at least two independent players agree and outnumber the "
        "dissent; `single_source` = one player said it, unverified. Verify in game before relying on it.",
        "",
    ]
    order = ",".join("?" * len(DIGEST_VERDICTS))
    for sec in SECTIONS:
        rows = kb.execute(f"SELECT topic, statement, verdict, confidence, support_authors, first_seen, last_seen, "
                          f"evidence FROM facts WHERE section=? AND verdict IN ({order}) "
                          f"ORDER BY confidence DESC, topic", (sec, *DIGEST_VERDICTS)).fetchall()
        if not rows:
            continue
        lines += [f"## {sec[0].upper() + sec[1:]}", ""]
        for topic, st, verdict, conf, sa, fs, ls, ev in rows:
            ev = json.loads(ev or "[]")
            link = msgs.link(ev[0]) if ev else None
            lines.append(f"- **{topic}**: {st} _({verdict}, conf {conf:.2f}, {sa} author{'s' if sa != 1 else ''}, "
                         f"{fs}–{ls})_" + (f" [src]({link})" if link else ""))
        lines.append("")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    part = out + ".part"
    with open(part, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))
    os.replace(part, out)
    log(f"digest: {out}")
    return out


def search(kb, msgs, query, k=10, all_verdicts=False):
    q = ds._fts_query(query)
    if not q:
        return []
    sql = ("SELECT f.id, f.verdict, f.confidence, f.topic, f.statement, f.evidence FROM facts_fts "
           "JOIN facts f ON f.id = facts_fts.rowid WHERE facts_fts MATCH ?")
    args = [q]
    if not all_verdicts:
        sql += f" AND f.verdict IN ({','.join('?' * len(DIGEST_VERDICTS))})"
        args += DIGEST_VERDICTS
    sql += " ORDER BY bm25(facts_fts, 2.0, 1.0, 0.5) LIMIT ?"
    out = []
    for fid, verdict, conf, topic, st, ev in kb.execute(sql, args + [k]):
        ev = json.loads(ev or "[]")
        out.append({"id": fid, "verdict": verdict, "confidence": conf, "topic": topic, "statement": st,
                    "link": msgs.link(ev[0]) if ev else None})
    return out


def stats(kb):
    one = lambda sql: kb.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "windows": dict(kb.execute("SELECT status, count(*) FROM windows GROUP BY status")),
        "claims_kept": one("SELECT count(*) FROM claims"),
        "claims_dropped": one("SELECT coalesce(sum(claims_dropped), 0) FROM windows"),
        "clusters": one("SELECT count(*) FROM clusters"),
        "facts": dict(kb.execute("SELECT verdict, count(*) FROM facts GROUP BY verdict")),
        "promoted": dict(kb.execute("SELECT target, count(*) FROM promotions WHERE knowledge_status='active' "
                                    "GROUP BY target")),
        "llm_calls": one("SELECT count(*) FROM llm_calls"),
        "cost_usd": round(one("SELECT coalesce(sum(cost), 0) FROM llm_calls"), 2),
    }


def main():
    ap = argparse.ArgumentParser(description="Discord corpus -> vetted facts (docs/NOTES.md)")
    ap.add_argument("--kb", default=KB_DB, help="pipeline state DB")
    ap.add_argument("--msgs", default=dc.DEFAULT_DB, help="captured Discord DB (read-only)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def llm_flags(p):
        p.add_argument("--channels", help="comma-separated channel names (default: %s)" % ",".join(DEFAULT_CHANNELS))
        p.add_argument("--max-cost", type=float, default=MAX_COST, help="USD per run")

    p = sub.add_parser("run", help="extract, consolidate, promote, digest")
    llm_flags(p)
    p.add_argument("--db", default=HARNESS_DB, help="knowledge store to promote into")
    p.add_argument("--out", default=DIGEST)
    p = sub.add_parser("extract")
    llm_flags(p)
    p.add_argument("--limit", type=int)
    p = sub.add_parser("consolidate")
    p.add_argument("--max-cost", type=float, default=MAX_COST)
    p.add_argument("--recluster", action="store_true")
    p = sub.add_parser("promote")
    p.add_argument("--db", default=HARNESS_DB)
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("digest")
    p.add_argument("--out", default=DIGEST)
    p = sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("-k", type=int, default=10)
    p.add_argument("--all", action="store_true", help="include disputed/outdated/wrong facts")
    sub.add_parser("stats")
    a = ap.parse_args()

    kb, msgs = open_kb(a.kb), Msgs(a.msgs)
    budget = Budget(getattr(a, "max_cost", MAX_COST))
    channels = tuple(c.strip() for c in a.channels.split(",") if c.strip()) \
        if getattr(a, "channels", None) else DEFAULT_CHANNELS
    if a.cmd == "extract":
        extract(kb, msgs, budget, channels, limit=a.limit)
        return 2 if budget.hit else 0
    if a.cmd == "consolidate":
        consolidate(kb, budget, recluster=a.recluster)
        return 2 if budget.hit else 0
    if a.cmd == "promote":
        promote(kb, msgs, a.db, dry_run=a.dry_run)
    elif a.cmd == "digest":
        digest(kb, msgs, a.out)
    elif a.cmd == "search":
        for r in search(kb, msgs, a.query, a.k, a.all):
            print(f"[{r['verdict']} {r['confidence']:.2f}] {r['topic']}: {r['statement']}\n    {r['link'] or ''}")
    elif a.cmd == "stats":
        print(json.dumps(stats(kb), indent=1))
    elif a.cmd == "run":
        extract(kb, msgs, budget, channels)
        if budget.hit:
            print("cost guard hit during extraction: stopping before consolidate/promote; rerun to resume")
            return 2
        consolidate(kb, budget)
        promote(kb, msgs, a.db)
        digest(kb, msgs, a.out)
        print(json.dumps(stats(kb), indent=1))
        return 2 if budget.hit else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())

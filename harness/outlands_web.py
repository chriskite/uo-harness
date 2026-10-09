"""Outlands websites -> harness/data/outlands_web.db (docs/NOTES.md "Outlands wiki, news and
forums"; decision: docs/PLAN.md "Wiki, patch notes and forums in the knowledge base").

Captures three public sources for the knowledge base:

  wiki    wiki.uooutlands.com (MediaWiki): every article (namespace 0, no redirects), the
          rendered HTML from action=parse, so template-built pages (trait groups,
          creatures) come with their text. Refetched when the page's `touched` changes
          (an edit, or a template it uses).
  news    uooutlands.com/news (WordPress REST): every post (patch notes, events,
          announcements). Refetched when `modified` changes. VIDEO posts carry no text.
  forums  forums.uooutlands.com (XenForo 2, HTML pages): the game-info sections in
          FORUM_NODES. A thread is refetched when its last-post time or reply count
          changes. Quoted text is kept in the stored HTML; to_blocks(strip_quotes=True)
          drops it when rendering.

All requests are plain HTTPS GETs paced per host (PACE) with a browser User-Agent; 429/5xx
back off and retry. Nothing here talks to the game, the client or the proxy.

  python harness/outlands_web.py crawl wiki|news|forums|all [--limit N]
  python harness/outlands_web.py stats
  python harness/outlands_web.py text KEY          # wiki:<pageid> / news:<postid> / thread:<id>

harness/web_kb.py turns wiki and news into knowledge entries; harness/forum_kb.py turns the
forum posts into vetted facts (the Discord KB pipeline).
"""

import argparse
import html as htmllib
import json
import os
import random
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(HERE, "data")
DEFAULT_DB = os.path.join(DATA, "outlands_web.db")
LOG = os.path.join(ROOT, "logs", "outlands_web.log")

WIKI = "https://wiki.uooutlands.com"
WIKI_API = WIKI + "/api.php"
NEWS_API = "https://uooutlands.com/wp-json/wp/v2/posts"
FORUM = "https://forums.uooutlands.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0")
PACE = {"wiki.uooutlands.com": (1.0, 2.0), "uooutlands.com": (1.0, 2.0), "forums.uooutlands.com": (1.5, 3.5)}
RETRIES = 4
TIMEOUT = 60

# Crawled and run through forum_kb (user decisions 2026-10-08): node id -> (name, official).
# Game-info sections only; then, on the LLM cost (~10M characters for all ten game-info
# sections), also not Patches (the same staff patch notes as the news posts, already in the
# store as written), Suggestions & Ideas, Bug Reports, Client & Launcher Support (low yield).
# Never: roleplay, media, off-topic, trade / price check / real estate (prices deferred, as for
# Discord), guild discussion, events. Staff posts in an official section are official claims.
FORUM_NODES = {
    6: ("Announcements", True), 36: ("Active Development", True),
    3: ("General Discussion", False), 16: ("Player Guides & Macros", False),
    31: ("New Player Questions", False), 37: ("Corpse Creek", False),
}
POSTS_PER_PAGE = 20

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs(
  key TEXT PRIMARY KEY, source TEXT NOT NULL, kind TEXT, title TEXT, url TEXT, rev TEXT,
  changed TEXT, published TEXT, html TEXT, fetched_t REAL);
CREATE TABLE IF NOT EXISTS threads(
  id INTEGER PRIMARY KEY, node_id INTEGER, title TEXT, url TEXT, author TEXT, started_ts INTEGER,
  last_ts INTEGER, replies INTEGER, fetched_last_ts INTEGER, fetched_replies INTEGER, fetched_t REAL);
CREATE TABLE IF NOT EXISTS posts(
  id INTEGER PRIMARY KEY, thread_id INTEGER NOT NULL, node_id INTEGER, position INTEGER,
  author TEXT, author_id INTEGER, staff INTEGER, ts INTEGER, html TEXT);
CREATE INDEX IF NOT EXISTS posts_thread ON posts(thread_id, position);
CREATE TABLE IF NOT EXISTS fetches(t REAL, url TEXT, status INTEGER, bytes INTEGER, note TEXT);
"""


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def connect(path=DEFAULT_DB):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con


# ---------------------------------------------------------------------------- HTTP

class Fetcher:
    """GETs paced per host; retries 429/5xx/network errors with backoff."""

    def __init__(self, con, pace=PACE, sleep=time.sleep):
        self.con, self.pace, self.sleep = con, pace, sleep
        self.last = {}

    def get(self, url) -> str:
        host = urllib.parse.urlsplit(url).hostname
        lo, hi = self.pace.get(host, (1.0, 2.0))
        for attempt in range(RETRIES + 1):
            wait = self.last.get(host, 0) + random.uniform(lo, hi) - time.monotonic()
            if wait > 0:
                self.sleep(wait)
            self.last[host] = time.monotonic()
            status, body, note = None, b"", None
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
                with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                    status, body = r.status, r.read()
            except urllib.error.HTTPError as e:
                status, note = e.code, (e.headers.get("Retry-After") if e.headers else None)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                note = str(e)[:200]
            self.con.execute("INSERT INTO fetches VALUES (?,?,?,?,?)", (time.time(), url, status, len(body), note))
            self.con.commit()
            if status == 200:
                return body.decode("utf-8", "replace")
            if status is not None and 400 <= status < 500 and status != 429:
                raise FileNotFoundError(f"{status} {url}")
            if attempt == RETRIES:
                raise RuntimeError(f"giving up on {url}: {status or note}")
            back = 30 * (attempt + 1)
            if status == 429 and note and note.isdigit():
                back = max(back, int(note))
            log(f"  {status or note} on {url}; retry in {back} s")
            self.sleep(back)

    def json(self, url):
        return json.loads(self.get(url))


# ---------------------------------------------------------------------------- HTML -> blocks

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
_BLOCK = {"p", "div", "section", "article", "ul", "ol", "dl", "dt", "dd", "pre", "center", "figure",
          "figcaption", "caption", "blockquote", "table", "thead", "tbody", "tfoot"}
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "button", "form", "select", "textarea"}
_SKIP_CLASSES = ("mw-editsection", "toc", "navbox", "mw-references-wrap", "reference", "bbCodeBlock-expandLink",
                 "printfooter", "catlinks", "noprint", "mw-empty-elt")


class _Blocks(HTMLParser):
    """Rendered HTML -> [(kind, level, text)]: ("h", 1-6, title), ("p", 0, text),
    ("li", depth, text) and ("row", 0, text) for table rows ("Header: cell; ..." when the
    table has a header row, "a: b" for a th/td pair, else "a | b")."""

    def __init__(self, strip_quotes):
        super().__init__(convert_charrefs=True)
        self.strip_quotes = strip_quotes
        self.stack = []           # [tag, skipping]
        self.out, self.buf = [], []
        self.list_depth = 0
        self.heading = None
        self.tables = []          # per open table: {"header": [...]|None, "row": [...]|None, "cell": [...]|None, "th": bool}

    @property
    def skipping(self):
        return bool(self.stack) and self.stack[-1][1]

    def _flush(self, kind="p", level=0):
        text = " ".join("".join(self.buf).split())
        self.buf = []
        if text:
            self.out.append((kind, level, text))

    def handle_starttag(self, tag, attrs):
        if tag in _VOID:
            if not self.skipping:
                if tag == "br":
                    self._text("\n")
                elif tag == "img":
                    alt = dict(attrs).get("alt") or ""
                    if self.tables and self.tables[-1]["cell"] is not None and alt and not alt.lower().endswith(
                            (".png", ".jpg", ".gif", ".jpeg", ".webp")):
                        self._text(f" {alt} ")
            return
        a = dict(attrs)
        cls = a.get("class") or ""
        skip = self.skipping or tag in _SKIP_TAGS or a.get("id") == "toc" \
            or any(c in cls.split() for c in _SKIP_CLASSES) or "display:none" in (a.get("style") or "").replace(" ", "") \
            or (self.strip_quotes and tag == "blockquote" and "bbCodeBlock--quote" in cls)
        self.stack.append([tag, skip])
        if skip:
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._flush()
            self.heading = int(tag[1])
        elif tag == "li":
            self._flush("li", self.list_depth) if self.buf else None
        elif tag in ("ul", "ol"):
            self._flush()
            self.list_depth += 1
        elif tag == "table":
            self._flush()
            self.tables.append({"header": None, "row": None, "cell": None, "th": False, "row_th": []})
        elif tag == "tr" and self.tables:
            self.tables[-1]["row"], self.tables[-1]["row_th"] = [], []
        elif tag in ("td", "th") and self.tables:
            t = self.tables[-1]
            if t["row"] is None:
                t["row"], t["row_th"] = [], []
            t["cell"], t["th"] = [], tag == "th"
        elif tag in _BLOCK and not self.tables:
            self._flush("li", self.list_depth) if (self.list_depth and self.buf) else self._flush()

    def handle_endtag(self, tag):
        if tag in _VOID or not any(t == tag for t, _ in self.stack):
            return
        while self.stack:
            t, skip = self.stack.pop()
            if not skip:
                self._close(t)
            if t == tag:
                break

    def _close(self, tag):
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6") and self.heading:
            self._flush("h", self.heading)
            self.heading = None
        elif tag == "li":
            self._flush("li", self.list_depth)
        elif tag in ("ul", "ol"):
            self._flush("li", self.list_depth) if self.buf else None
            self.list_depth = max(0, self.list_depth - 1)
        elif tag in ("td", "th") and self.tables:
            t = self.tables[-1]
            if t["cell"] is not None and t["row"] is not None:
                t["row"].append(" ".join("".join(t["cell"]).split()))
                t["row_th"].append(t["th"])
            t["cell"] = None
        elif tag == "tr" and self.tables:
            self._row(self.tables[-1])
        elif tag == "table" and self.tables:
            t = self.tables.pop()
            if t["row"]:
                self._row(t)
            if self.tables and self.tables[-1]["cell"] is not None:   # nested table: its text joins the cell
                inner = [x[2] for x in self.out[self._mark(t):]]
                self.tables[-1]["cell"].append(" " + "; ".join(inner) + " ")
                del self.out[self._mark(t):]
        elif tag in _BLOCK and not self.tables:
            self._flush("li", self.list_depth) if (self.list_depth and self.buf) else self._flush()

    def _mark(self, t):
        return t.setdefault("mark", len(self.out))

    def _row(self, t):
        cells, ths = t["row"] or [], t["row_th"] or []
        t["row"], t["row_th"] = None, []
        self._mark(t)
        if not any(cells):
            return
        if all(ths) and len(cells) > 1 and t["header"] is None:
            t["header"] = cells
            return
        if t["header"] and len(t["header"]) == len(cells):
            text = "; ".join(f"{h}: {c}" if h else c for h, c in zip(t["header"], cells) if c)
        elif len(cells) == 2 and ths and ths[0] and not ths[1]:
            text = f"{cells[0]}: {cells[1]}"
        else:
            text = " | ".join(c for c in cells if c)
        self.out.append(("row", 0, text))

    def _text(self, s):
        if self.tables and self.tables[-1]["cell"] is not None:
            self.tables[-1]["cell"].append(s.replace("\n", " "))
        elif s == "\n":
            self._flush("li", self.list_depth) if self.list_depth else self._flush()
        else:
            self.buf.append(s)

    def handle_data(self, data):
        if not self.skipping:
            self._text(data)

    def close(self):
        super().close()
        self._flush()
        return self.out


def to_blocks(html, strip_quotes=False):
    p = _Blocks(strip_quotes)
    p.feed(html or "")
    return p.close()


def to_text(html, strip_quotes=False) -> str:
    """Plain text, one block per line; list items as "- ", headings as "# "."""
    lines = []
    for kind, level, text in to_blocks(html, strip_quotes):
        lines.append(("#" * level + " " if kind == "h" else "  " * max(0, level - 1) + "- " if kind == "li" else "")
                     + text)
    return "\n".join(lines)


# ---------------------------------------------------------------------------- wiki

def crawl_wiki(con, f, limit=None):
    pages, cont = {}, {}
    while True:
        q = {"action": "query", "generator": "allpages", "gapnamespace": 0, "gapfilterredir": "nonredirects",
             "gaplimit": "max", "prop": "info", "format": "json", "formatversion": 2, **cont}
        d = f.json(WIKI_API + "?" + urllib.parse.urlencode(q))
        for p in d.get("query", {}).get("pages", []):
            pages[p["pageid"]] = p
        if "continue" not in d:
            break
        cont = d["continue"]
    stored = dict(con.execute("SELECT key, changed FROM docs WHERE source='wiki'"))
    gone = [k for k in stored if int(k.split(":")[1]) not in pages]
    for k in gone:
        con.execute("DELETE FROM docs WHERE key=?", (k,))
    con.commit()
    todo = [p for pid, p in sorted(pages.items()) if stored.get(f"wiki:{pid}") != p["touched"]]
    log(f"wiki: {len(pages)} articles, {len(todo)} to fetch, {len(gone)} gone")
    for n, p in enumerate(todo[:limit] if limit else todo, 1):
        q = {"action": "parse", "pageid": p["pageid"], "prop": "text|revid|displaytitle", "format": "json",
             "formatversion": 2, "disableeditsection": 1, "disablelimitreport": 1, "disabletoc": 1}
        try:
            d = f.json(WIKI_API + "?" + urllib.parse.urlencode(q))["parse"]
        except (FileNotFoundError, KeyError) as e:
            log(f"  wiki {p['title']}: {e}")
            continue
        url = WIKI + "/" + urllib.parse.quote(p["title"].replace(" ", "_"))
        con.execute("INSERT OR REPLACE INTO docs VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (f"wiki:{p['pageid']}", "wiki", "wiki", p["title"], url, str(d.get("revid")), p["touched"],
                     None, d.get("text") or "", time.time()))
        con.commit()
        if n % 100 == 0:
            log(f"  wiki {n}/{len(todo)}")
    return {"articles": len(pages), "fetched": len(todo[:limit] if limit else todo), "gone": len(gone)}


# ---------------------------------------------------------------------------- news

def news_kind(title):
    t = title.strip().upper()
    if t.startswith("VIDEO"):
        return "video"
    if re.search(r"\bPATCH(ES)?\b", t) and not re.search(r"\bPATCHER\b|ISSUES", t):
        return "patch"
    if t.startswith("EVENT"):
        return "event"
    return "news"


def crawl_news(con, f, limit=None):
    stored = dict(con.execute("SELECT key, changed FROM docs WHERE source='news'"))
    seen, fetched, page = set(), 0, 1
    while True:
        q = {"per_page": 100, "page": page, "_fields": "id,date_gmt,modified_gmt,link,title,content"}
        try:
            posts = f.json(NEWS_API + "?" + urllib.parse.urlencode(q))
        except FileNotFoundError:   # WordPress answers 400/404 past the last page
            break
        for p in posts:
            key = f"news:{p['id']}"
            seen.add(key)
            if stored.get(key) == p["modified_gmt"]:
                continue
            title = htmllib.unescape(p["title"]["rendered"])
            con.execute("INSERT OR REPLACE INTO docs VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (key, "news", news_kind(title), title, p["link"], None, p["modified_gmt"],
                         p["date_gmt"], p["content"]["rendered"], time.time()))
            fetched += 1
        con.commit()
        if len(posts) < 100 or (limit and len(seen) >= limit):
            break
        page += 1
    gone = [] if limit else [k for k in stored if k not in seen]
    for k in gone:
        con.execute("DELETE FROM docs WHERE key=?", (k,))
    con.commit()
    log(f"news: {len(seen)} posts, {fetched} new or changed, {len(gone)} gone")
    return {"posts": len(seen), "fetched": fetched, "gone": len(gone)}


# ---------------------------------------------------------------------------- forums

_THREAD_ITEM = re.compile(r'<div class="structItem structItem--thread[^"]*"[^>]*data-author="([^"]*)"(.*?)'
                          r'(?=<div class="structItem structItem--thread|<div class="block-footer|$)', re.S)


def parse_thread_list(page_html):
    """Thread rows of a forum listing page."""
    out = []
    for author, body in _THREAD_ITEM.findall(page_html):
        m = re.search(r'<div class="structItem-title">.*?<a href="(/index\.php\?threads/[^"]*?\.(\d+)/)"[^>]*>(.*?)</a>',
                      body, re.S)
        if not m:
            continue
        times = re.findall(r'data-timestamp="(\d+)"', body)
        rep = re.search(r"<dt>Replies</dt>\s*<dd>([\d,KkMm.]+)</dd>", body)
        replies = rep.group(1).replace(",", "") if rep else "0"
        replies = int(float(replies[:-1]) * 1000) if replies[-1:] in "kK" else int(float(replies)) if replies else 0
        out.append({"id": int(m.group(2)), "url": FORUM + htmllib.unescape(m.group(1)),
                    "title": " ".join(htmllib.unescape(re.sub(r"<[^>]+>", " ", m.group(3))).split()),
                    "author": htmllib.unescape(author), "started_ts": int(times[0]) if times else None,
                    "last_ts": int(times[-1]) if times else None, "replies": replies})
    return out


def last_page(page_html, path_re):
    return max([1] + [int(x) for x in re.findall(path_re + r"page-(\d+)", page_html)])


def parse_posts(page_html):
    """Posts of a thread page: id, author, author_id, staff, ts, body html."""
    out = []
    for seg in page_html.split('<article class="message message--post')[1:]:
        m = re.search(r'data-content="post-(\d+)"', seg)
        if not m:
            continue
        author = re.search(r'data-author="([^"]*)"', seg)
        name = re.search(r'<h4 class="message-name">(.*?)</h4>', seg, re.S)
        uid = re.search(r'data-user-id="(\d+)"', name.group(1)) if name else None
        ts = re.search(r'message-attribution.*?data-timestamp="(\d+)"', seg, re.S)
        b0 = seg.find('<div class="bbWrapper">')
        b1 = seg.find('<div class="js-selectToQuoteEnd">', b0)
        body = seg[b0 + len('<div class="bbWrapper">'):b1] if b0 >= 0 and b1 > b0 else ""
        body = body.rstrip()
        body = body[:-len("</div>")] if body.endswith("</div>") else body    # bbWrapper's close
        body = re.sub(r"\s*</div>\s*$", "", body)                            # itemprop=text's close
        # staff: a staff/admin/moderator username style, or the "Staff Member" banner in the
        # user cell (developers such as Luthius carry only the latter and --moderator/--admin)
        user_cell = seg[:b0] if b0 >= 0 else seg
        staff = bool(name and re.search(r"username--(staff|admin|moderator)\b", name.group(1))) \
            or bool(re.search(r"message-userBanner[^>]*>.*?Staff Member", user_cell, re.S))
        out.append({"id": int(m.group(1)), "author": htmllib.unescape(author.group(1)) if author else None,
                    "author_id": int(uid.group(1)) if uid else None, "staff": int(staff),
                    "ts": int(ts.group(1)) if ts else None, "html": body})
    return out


def crawl_thread(con, f, t, node_id):
    """All pages of one thread; replaces its stored posts."""
    first = f.get(t["url"])
    n = last_page(first, r"threads/[^\"/]*?\." + str(t["id"]) + "/")
    posts = parse_posts(first)
    for page in range(2, n + 1):
        posts += parse_posts(f.get(f"{t['url']}page-{page}"))
    con.execute("DELETE FROM posts WHERE thread_id=?", (t["id"],))
    con.executemany("INSERT OR REPLACE INTO posts VALUES (?,?,?,?,?,?,?,?,?)",
                    [(p["id"], t["id"], node_id, i, p["author"], p["author_id"], p["staff"], p["ts"], p["html"])
                     for i, p in enumerate(posts)])
    con.execute("UPDATE threads SET fetched_last_ts=last_ts, fetched_replies=replies, fetched_t=? WHERE id=?",
                (time.time(), t["id"]))
    con.commit()
    return len(posts)


def crawl_forums(con, f, nodes=None, limit=None):
    nodes = nodes or list(FORUM_NODES)
    stats = {"threads": 0, "fetched": 0, "posts": 0, "failed": 0}
    for node in nodes:
        name = FORUM_NODES[node][0]
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower().replace("&", "")).strip("-")
        base = f"{FORUM}/index.php?forums/{slug}.{node}/"
        stored = {r[0]: r[1:] for r in con.execute(
            "SELECT id, fetched_last_ts, fetched_replies FROM threads WHERE node_id=?", (node,))}
        page, pages, listed = 1, None, []
        while True:
            h = f.get(base if page == 1 else f"{base}page-{page}")
            pages = pages or last_page(h, re.escape(f"forums/{slug}.{node}/"))
            rows = parse_thread_list(h)
            listed += rows
            for t in rows:
                con.execute("INSERT INTO threads(id, node_id, title, url, author, started_ts, last_ts, replies) "
                            "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET node_id=excluded.node_id, "
                            "title=excluded.title, url=excluded.url, last_ts=excluded.last_ts, "
                            "replies=excluded.replies",
                            (t["id"], node, t["title"], t["url"], t["author"], t["started_ts"], t["last_ts"],
                             t["replies"]))
            con.commit()
            # listings are newest activity first: a page with nothing new (stickies aside) ends the walk
            fresh = [t for t in rows if stored.get(t["id"]) != (t["last_ts"], t["replies"])]
            if page >= pages or (stored and rows and not fresh and page > 1):
                break
            page += 1
        # every thread of the node not fetched as listed: the ones just listed with new posts,
        # plus older ones an interrupted crawl listed but never fetched (the walk above stops early)
        todo = [dict(zip(("id", "url", "title"), r)) for r in con.execute(
            "SELECT id, url, title FROM threads WHERE node_id=? AND (fetched_t IS NULL OR "
            "fetched_last_ts IS NOT last_ts OR fetched_replies IS NOT replies) ORDER BY last_ts DESC", (node,))]
        log(f"forum {name}: {len(listed)} threads listed on {page}/{pages} pages, {len(todo)} to fetch")
        stats["threads"] += len(listed)
        for i, t in enumerate(todo, 1):
            if limit and stats["fetched"] >= limit:
                return stats
            try:
                stats["posts"] += crawl_thread(con, f, t, node)
                stats["fetched"] += 1
            except (FileNotFoundError, RuntimeError, sqlite3.OperationalError) as e:
                stats["failed"] += 1
                log(f"  thread {t['id']} {t['title'][:60]}: {e}")
            if i % 50 == 0:
                log(f"  {name}: {i}/{len(todo)} threads")
    return stats


# ---------------------------------------------------------------------------- CLI

def stats(con):
    one = lambda sql, *a: con.execute(sql, a).fetchone()[0]  # noqa: E731
    return {
        "docs": dict(con.execute("SELECT source || ':' || kind, count(*) FROM docs GROUP BY 1")),
        "threads": one("SELECT count(*) FROM threads"),
        "threads_fetched": one("SELECT count(*) FROM threads WHERE fetched_t IS NOT NULL"),
        "posts": one("SELECT count(*) FROM posts"),
        "posts_by_node": {FORUM_NODES.get(n, (str(n),))[0]: c for n, c in
                          con.execute("SELECT node_id, count(*) FROM posts GROUP BY 1")},
        "fetches": one("SELECT count(*) FROM fetches"),
        "fetch_errors": one("SELECT count(*) FROM fetches WHERE status IS NOT 200"),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Outlands wiki/news/forums capture (docs/NOTES.md)")
    ap.add_argument("--db", default=DEFAULT_DB)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("crawl")
    p.add_argument("what", choices=("wiki", "news", "forums", "all"))
    p.add_argument("--limit", type=int, help="at most N pages/posts/threads (a trial)")
    p.add_argument("--nodes", help="forum node ids, comma-separated (default: all of FORUM_NODES)")
    sub.add_parser("stats")
    p = sub.add_parser("text")
    p.add_argument("key")
    a = ap.parse_args(argv)
    con = connect(a.db)
    if a.cmd == "stats":
        print(json.dumps(stats(con), indent=1))
    elif a.cmd == "text":
        if a.key.startswith("thread:"):
            for author, staff, ts, h in con.execute("SELECT author, staff, ts, html FROM posts WHERE thread_id=? "
                                                    "ORDER BY position", (int(a.key[7:]),)):
                print(f"--- {author}{' [staff]' if staff else ''} {time.strftime('%Y-%m-%d', time.gmtime(ts or 0))}")
                print(to_text(h, strip_quotes=True))
        else:
            r = con.execute("SELECT title, url, html FROM docs WHERE key=?", (a.key,)).fetchone()
            if not r:
                print("no such doc")
                return 1
            print(f"{r[0]}\n{r[1]}\n")
            print(to_text(r[2]))
    else:
        f = Fetcher(con)
        nodes = [int(x) for x in a.nodes.split(",")] if a.nodes else None
        for what in (("wiki", "news", "forums") if a.what == "all" else (a.what,)):
            t0 = time.time()
            r = {"wiki": lambda: crawl_wiki(con, f, a.limit), "news": lambda: crawl_news(con, f, a.limit),
                 "forums": lambda: crawl_forums(con, f, nodes, a.limit)}[what]()
            log(f"crawl {what} done in {time.time() - t0:.0f} s: {json.dumps(r)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

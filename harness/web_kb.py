"""Outlands wiki and news (patch notes) -> knowledge entries (docs/NOTES.md "Outlands wiki,
news and forums"; decision: docs/PLAN.md "Wiki, patch notes and forums in the knowledge base").

The wiki and the official news posts are authoritative text, so they go into the overseer's
`knowledge` table as they are, one entry per section chunk, without an LLM in between:

  chunks   every wiki article and news post captured by outlands_web.py is cut at its
           headings (a short line right before a list or table counts as a heading: news
           posts and forum-style pages style their headings as plain paragraphs) into
           pieces of at most CHUNK_CHARS. Topic = "<page> > <heading path>" for the wiki,
           "<YYYY-MM-DD> <post title> > <heading path>" for news, so dated patch notes read
           as history next to the current wiki.
  sync     brings the knowledge store in line with the chunks. Each chunk has a stable key
           (doc, heading path, part); what was written for which key is kept per target DB in
           outlands_web.db `kb_entries`. A changed chunk supersedes its entry (history kept),
           a vanished one is retracted (only when the sync added it and nobody confirmed it),
           an entry the overseer retracted or superseded is never put back with the same text.
           Entries: kind fact, source wiki (0.7) or doc (0.8, news), tags wiki / news +
           patch|event|news, importance IMPORTANCE, ref "web-kb:<key> <url>".
           Afterwards the new entries are embedded (embedder.py, GPU) when this Python has
           fastembed, so the first `ctl know search` doesn't pay for thousands of vectors.

  python harness/web_kb.py sync [--db harness/data/harness.db] [--dry-run]
  python harness/web_kb.py chunks KEY                # wiki:<pageid> / news:<postid>
  python harness/web_kb.py stats [--db ...]
"""

import argparse
import collections
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import outlands_web as ow  # noqa: E402

HARNESS_DB = os.path.join(ow.DATA, "harness.db")
CHUNK_CHARS = 1200
HEADING_MAX = 80             # a pseudo-heading line is at most this long
IMPORTANCE = 5
REF_PREFIX = "web-kb:"
SKIP_TITLES = {"Main Page"}
SEP = " > "

STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS kb_entries(
  target TEXT NOT NULL, key TEXT NOT NULL, knowledge_id INTEGER, action TEXT, hash TEXT, status TEXT,
  t REAL, PRIMARY KEY(target, key));
"""


# ---------------------------------------------------------------------------- chunking

def _sentences(text, size):
    """A too-long line cut at sentence ends (or spaces) into pieces of at most `size`."""
    out, cur = [], ""
    for s in re.split(r"(?<=[.!?])\s+", text):
        while len(s) > size:
            cut = s.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            if cur:
                out.append(cur)
                cur = ""
            out.append(s[:cut].strip())
            s = s[cut:].strip()
        if cur and len(cur) + 1 + len(s) > size:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        out.append(cur)
    return out


def _is_pseudo_heading(blocks, i):
    kind, _, text = blocks[i]
    return (kind == "p" and len(text) <= HEADING_MAX and not re.search(r"[.!?,;:]$", text)
            and i + 1 < len(blocks) and blocks[i + 1][0] in ("li", "row"))


def sections(blocks):
    """[(heading path tuple, anchor heading or None, [lines])] in document order."""
    real = {}                 # level -> heading text
    pseudo = None
    out = []
    cur_path, cur_anchor, lines = (), None, []

    def path():
        return tuple(real[k] for k in sorted(real)) + ((pseudo,) if pseudo else ())

    for i, (kind, level, text) in enumerate(blocks):
        heading = kind == "h" or _is_pseudo_heading(blocks, i)
        if heading:
            if lines:
                out.append((cur_path, cur_anchor, lines))
            if kind == "h":
                for k in [k for k in real if k >= level]:
                    del real[k]
                real[level] = text
                pseudo = None
                cur_anchor = text
            else:
                pseudo = text
            cur_path, lines = path(), []
            continue
        lines.append(("  " * max(0, level - 1) + "- " + text) if kind == "li" else text)
    if lines:
        out.append((cur_path, cur_anchor, lines))
    return out


def pack(lines, size=CHUNK_CHARS):
    """Lines packed into chunks of at most `size` characters (long lines split)."""
    chunks, cur = [], []
    n = 0
    for line in lines:
        pieces = _sentences(line, size) if len(line) > size else [line]
        for p in pieces:
            if cur and n + 1 + len(p) > size:
                chunks.append("\n".join(cur))
                cur, n = [], 0
            cur.append(p)
            n += len(p) + (1 if n else 0)
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:60] or "_"


def doc_chunks(key, source, kind, title, url, published, html):
    """The knowledge entries of one doc: [{key, topic, content, tags, entities, source, ref}]."""
    if source == "news" and kind == "video":
        return []
    if source == "wiki" and title in SKIP_TITLES:
        return []
    head = title if source == "wiki" else f"{(published or '')[:10]} {title}".strip()
    tags = ["wiki"] if source == "wiki" else ["news", kind]
    out, seen = [], collections.Counter()
    for path, anchor, lines in sections(ow.to_blocks(html)):
        base = f"{key}/" + "/".join(_slug(p) for p in path) if path else f"{key}/_"
        seen[base] += 1
        repeat = seen[base]
        if repeat > 1:                        # the same heading path twice in one page
            base = f"{base}~{repeat}"
        link = url + ("#" + urllib.parse.quote(anchor.replace(" ", "_")) if source == "wiki" and anchor else "")
        parts = pack(lines)
        for n, content in enumerate(parts):
            if len(content) < 3:
                continue
            # topics are unique per chunk: knowledge.add takes the same topic with near-identical
            # wording for a duplicate (a patch post's "Decode Map" for treasure and for resource maps)
            topic = SEP.join((head,) + path) + (f" #{repeat}" if repeat > 1 else "") \
                + (f" ({n + 1}/{len(parts)})" if len(parts) > 1 else "")
            out.append({"key": f"{base}/{n}", "topic": topic, "content": content, "tags": tags,
                        "entities": [title] if source == "wiki" else [],
                        "source": "wiki" if source == "wiki" else "doc",
                        "ref": f"{REF_PREFIX}{base}/{n} {link}"})
    return out


def all_chunks(web):
    """Every doc's chunks. A chunk whose text (as knowledge.content_hash normalises it) another
    chunk already has is left out, so the store doesn't confirm itself: template boilerplate
    shared by wiki pages, patch sections a later post repeats, and patch text the wiki copied
    word for word. The wiki (the current reference) keeps it over news, and a newer post over
    an older one. Because the dedupe spans both sources, a sync always covers both: synced
    alone, the wiki's copy of a patch section would confirm the news entry (2026-10-08)."""
    import knowledge
    rows = web.execute("SELECT key, source, kind, title, url, published, html FROM docs "
                       "ORDER BY source='wiki' DESC, published DESC, key").fetchall()
    out, texts = [], set()
    for r in rows:
        for c in doc_chunks(*r):
            h = knowledge.content_hash("fact", c["content"])
            if h not in texts:
                texts.add(h)
                out.append(c)
    return out


def chunk_hash(c):
    return hashlib.sha1(json.dumps([c["topic"], c["content"], sorted(c["tags"]), sorted(c["entities"]),
                                    c["source"], c["ref"], IMPORTANCE]).encode()).hexdigest()


# ---------------------------------------------------------------------------- sync

def sync(web, db=HARNESS_DB, dry_run=False, embed=True, log=print):
    """Bring the knowledge store `db` in line with the chunks. Returns counts."""
    import knowledge
    import memory
    web.executescript(STATE_SCHEMA)
    target = os.path.normcase(os.path.abspath(db))
    k = knowledge.Knowledge(memory.connect(db))
    chunks = all_chunks(web)
    want = {c["key"]: c for c in chunks}
    state = {r[0]: dict(zip(("key", "knowledge_id", "action", "hash", "status"), r)) for r in web.execute(
        "SELECT key, knowledge_id, action, hash, status FROM kb_entries WHERE target=?", (target,))}
    # entries this sync wrote but has no state for (a lost or other computer's outlands_web.db)
    # are found by their ref and adopted, instead of being confirmed by a second add
    by_ref = {}
    for kid, ref in k.con.execute("SELECT id, source_ref FROM knowledge WHERE status='active' AND source_ref LIKE ?",
                                  (REF_PREFIX + "%",)):
        by_ref.setdefault(ref.split(" ")[0][len(REF_PREFIX):], kid)
    for key in want:
        if (key not in state or state[key]["knowledge_id"] is None and state[key]["status"] != "blocked") \
                and key in by_ref:
            state[key] = {"key": key, "knowledge_id": by_ref[key], "action": "added", "hash": None, "status": "active"}
    counts = collections.Counter()

    def record(key, **kw):
        """Kept right after each knowledge write and committed at once: the crawler writes
        the same DB, and a sync-long transaction locked it out (2026-10-08)."""
        if dry_run:
            return
        web.execute("INSERT INTO kb_entries(target, key) VALUES (?, ?) ON CONFLICT DO NOTHING", (target, key))
        web.execute(f"UPDATE kb_entries SET {', '.join(c + '=?' for c in kw)}, t=? WHERE target=? AND key=?",
                    [*kw.values(), time.time(), target, key])
        web.commit()

    def ours_retracted(e):
        return e is not None and e["status"] == "retracted" and (e["retract_reason"] or "").startswith(REF_PREFIX)

    def blocked(c):
        """Someone other than this sync retracted an entry with exactly this text."""
        h = knowledge.content_hash("fact", c["content"])
        return any(not (x or "").startswith(REF_PREFIX) for (x,) in k.con.execute(
            "SELECT retract_reason FROM knowledge WHERE content_hash=? AND status='retracted'", (h,)))

    def add(c):
        if blocked(c):
            record(c["key"], knowledge_id=None, action=None, hash=None, status="blocked")
            counts["kept_out"] += 1
            return
        counts["add"] += 1
        if dry_run:
            return
        r = k.add("fact", c["topic"], c["content"], tags=c["tags"], entities=c["entities"], source=c["source"],
                  ref=c["ref"], importance=IMPORTANCE)
        counts[r["action"]] += 1
        record(c["key"], knowledge_id=r["id"], action=r["action"], hash=chunk_hash(c), status="active")

    # gone from the source: retract what the sync added, unless someone confirmed it
    for key, s in state.items():
        if key in want or s["status"] != "active":
            continue
        e = k.get(s["knowledge_id"]) if s["knowledge_id"] is not None else None
        if s["action"] in ("added", "superseded") and e and e["status"] == "active" and e["confirmations"] == 0:
            counts["retract"] += 1
            if not dry_run:
                k.retract(e["id"], f"{REF_PREFIX} gone from the source")
            record(key, status="retracted")
        else:                                  # confirmed by someone, or not ours: no longer tracked
            record(key, knowledge_id=None, action=None, hash=None, status="released")
            counts["released"] += 1

    for key, c in want.items():
        s = state.get(key)
        h = chunk_hash(c)
        if s is None or s["knowledge_id"] is None:
            if s is not None and s["status"] == "blocked" and blocked(c):
                counts["kept_out"] += 1
                continue
            add(c)
            continue
        e = k.get(s["knowledge_id"])
        if e is None or e["status"] != "active":
            if ours_retracted(e):
                add(c)                         # back in the source after the sync retracted it
            else:                              # the overseer retracted or superseded it: theirs now
                if s["status"] != (e["status"] if e else "missing"):
                    record(key, status=e["status"] if e else "missing")
                counts["kept_out"] += 1
            continue
        if s["hash"] == h:
            counts["unchanged"] += 1
            continue
        counts["update"] += 1
        if dry_run:
            continue
        if s["action"] == "confirmed":         # an existing entry we only confirmed isn't ours to edit
            add(c)
        elif e["topic"] == c["topic"] and e["content"] == c["content"]:
            k.update(e["id"], tags=c["tags"], entities=c["entities"])
            record(key, knowledge_id=e["id"], action=s["action"], hash=h, status="active")
        else:
            r = k.update(e["id"], content=c["content"], topic=c["topic"], tags=c["tags"], entities=c["entities"],
                         source=c["source"], ref=c["ref"])
            record(key, knowledge_id=r["id"], action="superseded", hash=h, status="active")
    if not dry_run:
        web.commit()
    if embed and not dry_run and counts["add"] + counts["update"]:
        import embedder
        if embedder.available():
            t0 = time.time()
            knowledge.Knowledge(k.con, embed=embedder)._refresh_vectors()
            log(f"embedded new entries in {time.time() - t0:.1f} s on {embedder.device()}")
    k.con.close()
    counts["chunks"] = len(want)
    log(("sync (dry run): " if dry_run else "sync: ") + json.dumps(dict(counts)))
    return dict(counts)


def stats(web, db=HARNESS_DB):
    web.executescript(STATE_SCHEMA)
    target = os.path.normcase(os.path.abspath(db))
    by = collections.Counter()
    for key, status in web.execute("SELECT key, status FROM kb_entries WHERE target=?", (target,)):
        by[f"{key.split(':')[0]}:{status}"] += 1
    return {"target": target, "entries": dict(by),
            "chunks": dict(collections.Counter(c["key"].split(":")[0] for c in all_chunks(web)))}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Outlands wiki/news -> knowledge entries (docs/NOTES.md)")
    ap.add_argument("--web", default=ow.DEFAULT_DB, help="outlands_web.py capture DB")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sync")
    p.add_argument("--db", default=HARNESS_DB, help="knowledge store")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-embed", action="store_true")
    p = sub.add_parser("chunks")
    p.add_argument("key")
    p = sub.add_parser("stats")
    p.add_argument("--db", default=HARNESS_DB)
    a = ap.parse_args(argv)
    web = ow.connect(a.web)
    if a.cmd == "sync":
        sync(web, a.db, a.dry_run, embed=not a.no_embed)
    elif a.cmd == "chunks":
        r = web.execute("SELECT key, source, kind, title, url, published, html FROM docs WHERE key=?",
                        (a.key,)).fetchone()
        if not r:
            print("no such doc")
            return 1
        for c in doc_chunks(*r):
            print(f"--- {c['key']} | {c['topic']} | {len(c['content'])} chars\n{c['content']}")
    elif a.cmd == "stats":
        print(json.dumps(stats(web, a.db), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

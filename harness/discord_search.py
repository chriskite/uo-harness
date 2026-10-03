"""Semantic search over the captured Outlands Discord history
(docs/NOTES.md "Discord capture" → "Semantic search"; decision: docs/PLAN.md).

Hybrid retrieval over harness/data/discord.db (filled by discord_capture.py):
  - meaning: BAAI/bge-small-en-v1.5 embeddings (fastembed-gpu, local ONNX on the GPU with a
    CPU fallback, no network after the one-time model download) of conversation chunks,
    cosine similarity;
  - words: the FTS5 BM25 index of single messages (porter stemming), words OR-ed;
  merged by reciprocal rank fusion, so an exact item name and a paraphrase both surface.

A chunk is a run of consecutive messages in one channel, each less than GAP_S after the
previous one, cut at MAX_MSGS messages or MAX_CHARS characters. That's one exchange,
because single chat lines are too short to embed well.
The vector index is harness/data/discord_vec.db. It's gitignored and not backed up,
because it can be regenerated from discord.db. Vectors are keyed by the chunk text's
hash, so a refresh only embeds new or changed chunks. `search` refreshes first, so
results always cover everything captured so far.

Needs the capture venv (fastembed):
  .venv-discord/Scripts/python.exe harness/discord_search.py search "QUESTION"
      [-k 8] [--channel harvesting] [--since 2026-09-01] [--json] [--no-refresh]
  .venv-discord/Scripts/python.exe harness/discord_search.py index    # refresh only
"""

import argparse
import bisect
import datetime
import hashlib
import json
import os
import re
import sqlite3
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import discord_capture as dc  # noqa: E402
from embedder import MODEL, device, model  # noqa: E402

VEC_DB = os.path.join(dc.DATA, "discord_vec.db")
GAP_S = 600
MAX_MSGS = 12
MAX_CHARS = 1200
RRF_K = 60
CANDIDATES = 60
STOPWORDS = set("""a an and are as at be by can do does for from how i if in is it its me my of on
or so that the this to was what when where which who why will with you your""".split())

VEC_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS chunks(
  hash TEXT PRIMARY KEY, channel_id INTEGER NOT NULL, guild_id INTEGER, first_id INTEGER NOT NULL,
  last_id INTEGER NOT NULL, n_msgs INTEGER, text TEXT NOT NULL, vec BLOB NOT NULL);
CREATE INDEX IF NOT EXISTS chunks_channel ON chunks(channel_id, first_id);
"""


def _date(snowflake):
    return dc.snowflake_date(snowflake)[:10]


def build_chunks(rows, names):
    """rows: (id, channel_id, guild_id, author, content, embed_text) ordered by channel, id.
    Returns chunk dicts with deterministic text (so unchanged chunks keep their hash)."""
    chunks, cur = [], None

    def close():
        if cur and cur["lines"]:
            head = f"#{names.get(cur['channel_id'], cur['channel_id'])} {_date(cur['first_id'])}"
            text = head + "\n" + "\n".join(cur["lines"])
            # position + text: a reposted identical message is its own chunk with its own link
            key = f"{cur['channel_id']}:{cur['first_id']}:{text}"
            chunks.append({k: cur[k] for k in ("channel_id", "guild_id", "first_id", "last_id")}
                          | {"n_msgs": len(cur["lines"]), "text": text,
                             "hash": hashlib.sha1(key.encode()).hexdigest()})

    for mid, ch, gid, author, content, embed_text in rows:
        body = " ".join(x for x in (content, embed_text) if x).strip()
        if not body:
            continue
        line = f"{author or '?'}: {body}"[:MAX_CHARS]
        t = dc.snowflake_ms(mid) / 1000
        if (cur is None or ch != cur["channel_id"] or t - cur["last_t"] > GAP_S
                or len(cur["lines"]) >= MAX_MSGS or cur["chars"] + len(line) > MAX_CHARS):
            close()
            cur = {"channel_id": ch, "guild_id": gid, "first_id": mid, "lines": [], "chars": 0}
        cur["lines"].append(line)
        cur["chars"] += len(line) + 1
        cur["last_id"], cur["last_t"] = mid, t
    close()
    return chunks


def _open_msgs(db_path):
    return sqlite3.connect("file:" + db_path.replace("\\", "/") + "?mode=ro", uri=True)


def _open_vec(path):
    v = sqlite3.connect(path)
    v.execute("PRAGMA journal_mode=WAL")
    v.executescript(VEC_SCHEMA)
    row = v.execute("SELECT value FROM meta WHERE key='model'").fetchone()
    if row and row[0] != MODEL:  # another model's vectors aren't comparable
        v.execute("DELETE FROM chunks")
    v.execute("INSERT INTO meta VALUES ('model', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (MODEL,))
    v.commit()
    return v


def refresh(db_path=dc.DEFAULT_DB, vec_path=VEC_DB, log=print):
    """Bring the vector index in line with the messages. Returns counts."""
    t0 = time.time()
    m = _open_msgs(db_path)
    names = {i: n for i, n in m.execute("SELECT id, name FROM channels") if n}
    rows = m.execute("SELECT id, channel_id, guild_id, author, content, embed_text FROM messages "
                     "ORDER BY channel_id, id").fetchall()
    m.close()
    chunks = build_chunks(rows, names)
    v = _open_vec(vec_path)
    have = {h for (h,) in v.execute("SELECT hash FROM chunks")}
    want = {c["hash"]: c for c in chunks}
    stale = have - want.keys()
    new = [c for h, c in want.items() if h not in have]
    if stale:
        v.executemany("DELETE FROM chunks WHERE hash=?", [(h,) for h in stale])
    if new:
        log(f"embedding {len(new)} new chunks on the {device()} ...")
        for i in range(0, len(new), 256):
            batch = new[i:i + 256]
            vecs = list(model().embed([c["text"] for c in batch], batch_size=64))
            v.executemany(
                "INSERT OR REPLACE INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(c["hash"], c["channel_id"], c["guild_id"], c["first_id"], c["last_id"], c["n_msgs"],
                  c["text"], np.asarray(e, dtype=np.float32).tobytes()) for c, e in zip(batch, vecs)])
            v.commit()
    v.commit()
    v.close()
    return {"messages": len(rows), "chunks": len(chunks), "embedded": len(new), "removed": len(stale),
            "seconds": round(time.time() - t0, 1)}


def _fts_query(q):
    words = [w for w in re.findall(r"\w+", q.lower()) if w not in STOPWORDS and len(w) > 1]
    return " OR ".join(f'"{w}"' for w in words)


def search(query, k=8, channel=None, since=None, db_path=dc.DEFAULT_DB, vec_path=VEC_DB):
    """Top-k chunks for a natural-language query, best first."""
    v = _open_vec(vec_path)
    m = _open_msgs(db_path)
    names = {i: n for i, n in m.execute("SELECT id, name FROM channels") if n}
    ch_ids = None
    if channel:
        c = str(channel).lstrip("#")
        ch_ids = {int(c)} if c.isdigit() else {i for i, n in names.items() if n == c}
        if not ch_ids:
            raise SystemExit(f"unknown channel {channel!r}")
    since_id = None
    if since:
        ms = datetime.datetime.strptime(since, "%Y-%m-%d").timestamp() * 1000
        since_id = (int(ms) - dc.DISCORD_EPOCH_MS) << 22

    rows = v.execute("SELECT hash, channel_id, guild_id, first_id, last_id, text, vec FROM chunks").fetchall()
    rows = [r for r in rows if (ch_ids is None or r[1] in ch_ids) and (since_id is None or r[4] >= since_id)]
    if not rows:
        return []
    mat = np.frombuffer(b"".join(r[6] for r in rows), dtype=np.float32).reshape(len(rows), -1)
    qv = np.asarray(next(iter(model().query_embed([query]))), dtype=np.float32)
    sims = mat @ qv / (np.linalg.norm(mat, axis=1) * np.linalg.norm(qv) + 1e-9)
    vec_rank = {int(i): r for r, i in enumerate(np.argsort(-sims)[:CANDIDATES])}

    # words: FTS hits on single messages, mapped to the chunk that contains them
    by_ch = {}
    for idx, r in enumerate(rows):
        by_ch.setdefault(r[1], []).append((r[3], r[4], idx))
    for lst in by_ch.values():
        lst.sort()
    word_rank = {}
    fq = _fts_query(query)
    if fq:
        hits = m.execute("SELECT m.id, m.channel_id FROM messages_fts f JOIN messages m ON m.id=f.rowid "
                         "WHERE messages_fts MATCH ? ORDER BY bm25(messages_fts, 1.0, 0.3, 0.7) LIMIT 400",
                         (fq,)).fetchall()
        for mid, ch in hits:
            lst = by_ch.get(ch)
            if not lst:
                continue
            j = bisect.bisect_right(lst, (mid, float("inf"), 0)) - 1
            if j >= 0 and lst[j][0] <= mid <= lst[j][1]:
                idx = lst[j][2]
                if idx not in word_rank:
                    word_rank[idx] = len(word_rank)
                    if len(word_rank) >= CANDIDATES:
                        break
    m.close()
    v.close()

    scores = {}
    for ranks in (vec_rank, word_rank):
        for idx, r in ranks.items():
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (RRF_K + r + 1)
    out = []
    for idx in sorted(scores, key=scores.get, reverse=True)[:k]:
        h, ch, gid, first, last, text, _ = rows[idx]
        out.append({"channel": names.get(ch, str(ch)), "date": _date(first), "score": round(scores[idx], 4),
                    "similarity": round(float(sims[idx]), 3), "word_match": idx in word_rank,
                    "link": f"https://discord.com/channels/{gid or '@me'}/{ch}/{first}", "text": text})
    return out


def main():
    ap = argparse.ArgumentParser(description="semantic search over the captured Discord history")
    ap.add_argument("--db", default=dc.DEFAULT_DB)
    ap.add_argument("--vec", default=VEC_DB)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("index", help="refresh the vector index")
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("-k", type=int, default=8)
    s.add_argument("--channel", help="channel name (without #) or id")
    s.add_argument("--since", help="only chunks ending on/after YYYY-MM-DD")
    s.add_argument("--json", action="store_true")
    s.add_argument("--no-refresh", action="store_true", help="skip the incremental index refresh")
    a = ap.parse_args()
    if not os.path.exists(a.db):
        print(f"no capture DB at {a.db}", file=sys.stderr)
        return 1
    if a.cmd == "index":
        print(json.dumps(refresh(a.db, a.vec)))
        return 0
    if not a.no_refresh:
        refresh(a.db, a.vec, log=lambda s: print(s, file=sys.stderr))
    res = search(a.query, a.k, a.channel, a.since, a.db, a.vec)
    if a.json:
        print(json.dumps(res, indent=1, ensure_ascii=False))
        return 0
    for r in res:
        print(f"[{r['score']:.4f} sim {r['similarity']:.2f}{' +words' if r['word_match'] else ''}] "
              f"#{r['channel']} {r['date']}  {r['link']}")
        print("  " + r["text"].split("\n", 1)[1].replace("\n", "\n  ")[:1200])
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

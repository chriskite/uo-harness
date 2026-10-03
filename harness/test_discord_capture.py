"""Discord capture tests (harness/discord_capture.py, docs/NOTES.md "Discord capture").

  1. Store: a page captured twice adds no duplicates; an edit replaces the
     stored text and the FTS index follows (old word gone, new word found);
     embed and forwarded text is searchable.
  2. Crawl end detection: a short page counts as the channel start only when it
     loaded older messages (before=) or was the first page, never after= or
     around= (a jump).
  3. Gateway: zlib-stream and zstd-stream payloads split over several frames
     decode once complete, with the decompressor carried across messages;
     READY fills the guild and channel tables.

Runs with .venv-discord (zstandard); the zstd case is skipped without it.
"""

import json
import os
import sys
import tempfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import discord_capture as dc  # noqa: E402

FAILURES = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


def msg(i, ch=10, content="hello", edited=None, **kw):
    return {"id": str(i), "channel_id": str(ch), "timestamp": "2026-01-02T03:04:05+00:00",
            "edited_timestamp": edited, "content": content,
            "author": {"id": "7", "username": "bob", "global_name": "Bob"}, **kw}


def test_store():
    with tempfile.TemporaryDirectory() as td:
        st = dc.Store(os.path.join(td, "d.db"))
        page = [msg(1, content="vanquishing hatchet"), msg(2, content="lumberjacking at Prevalia")]
        check(st.add_messages(page, 99) == 2, "first capture stores both")
        check(st.add_messages(page, 99) == 0, "recapture adds nothing")
        check(st.channel_span(10)[0] == 2, "no duplicate rows")

        st.add_messages([msg(1, content="exceptional axe", edited="2026-01-03T00:00:00+00:00")])
        check([r["id"] for r in st.search("axe")] == ["1"], "edited text is searchable")
        check(st.search("hatchet") == [], "pre-edit text is gone from the index")
        check([r["id"] for r in st.search("lumberjack")] == ["2"], "porter stemming matches")

        st.add_messages([msg(3, content="", embeds=[{"title": "Patch notes",
                                                     "fields": [{"name": "Cotton", "value": "drop rate up"}]}]),
                         msg(4, content="", message_snapshots=[{"message": {"content": "forwarded cotton tip"}}])])
        check({r["id"] for r in st.search("cotton")} == {"3", "4"}, "embed and forwarded text indexed")
        check(st.search('" OR *') == [], "FTS syntax in a query is harmless")
        st.db.close()


def test_hit_start():
    def b(query, n):
        return dc.Batch(1, {k: [v] for k, v in query.items()}, [msg(i) for i in range(1, n + 1)])
    check(b({"before": "500", "limit": "50"}, 12).hit_start, "short before= page is the start")
    check(b({"limit": "50"}, 3).hit_start, "short first page is the start")
    check(not b({"before": "500", "limit": "50"}, 50).hit_start, "full page is not the start")
    check(not b({"around": "500", "limit": "50"}, 20).hit_start, "short around= (jump) is not the start")
    check(not b({"after": "500", "limit": "50"}, 20).hit_start, "short after= page is not the start")


def _frames(blob, cut):
    return [blob[i:i + cut] for i in range(0, len(blob), cut)]


def test_gateway():
    ready = {"op": 0, "t": "READY", "d": {"guilds": [{
        "id": "99", "properties": {"name": "Outlands"},
        "channels": [{"id": "5", "type": 4, "name": "Info"},
                     {"id": "10", "type": 0, "name": "general", "parent_id": "5"}]}]}}
    create = {"op": 0, "t": "MESSAGE_CREATE", "d": {**msg(20), "guild_id": "99"}}

    comp = zlib.compressobj()
    dec = dc.GatewayDecoder("wss://gateway.discord.gg/?encoding=json&v=9&compress=zlib-stream")
    out = []
    for payload in (ready, create):
        blob = comp.compress(json.dumps(payload).encode()) + comp.flush(zlib.Z_SYNC_FLUSH)
        for f in _frames(blob, 7):
            out += dec.feed(f)
    check([o["t"] for o in out] == ["READY", "MESSAGE_CREATE"], f"zlib-stream decodes across frames: {[o['t'] for o in out]}")

    try:
        import zstandard
    except ImportError:
        print("SKIP zstd-stream (zstandard not installed)")
    else:
        zc = zstandard.ZstdCompressor().compressobj()
        dec = dc.GatewayDecoder("wss://gateway.discord.gg/?encoding=json&v=9&compress=zstd-stream")
        out = []
        for payload in (ready, create):
            blob = zc.compress(json.dumps(payload).encode()) + zc.flush(zstandard.COMPRESSOBJ_FLUSH_BLOCK)
            for f in _frames(blob, 5):
                out += dec.feed(f)
        check([o["t"] for o in out] == ["READY", "MESSAGE_CREATE"], "zstd-stream decodes across frames")

    with tempfile.TemporaryDirectory() as td:
        st = dc.Store(os.path.join(td, "d.db"))
        dc.gateway_apply(st, ready)
        dc.gateway_apply(st, create)
        chs = {c["id"]: c for c in st.channels(99)}
        check(chs.get("10", {}).get("parent_id") == "5" and chs["10"]["name"] == "general",
              "READY stores channels with their category")
        check(st.db.execute("SELECT name FROM guilds").fetchall() == [("Outlands",)], "READY stores the guild name")
        check(st.db.execute("SELECT guild_id FROM messages WHERE id=20").fetchone() == (99,),
              "live MESSAGE_CREATE stored with its guild")
        st.db.close()


def main():
    test_store()
    test_hit_start()
    test_gateway()
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILURES: {FAILURES}"))
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())

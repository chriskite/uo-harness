"""Discord semantic search chunking tests (harness/discord_search.py).

  Chunks break on a channel change, a gap over GAP_S, MAX_MSGS and MAX_CHARS; empty
  messages are skipped; the same messages give the same chunk hashes (so a refresh
  re-embeds nothing), and a new message changes only the chunk it joins.

Runs with .venv-discord (numpy); the model isn't needed.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import discord_capture as dc  # noqa: E402
import discord_search as ds  # noqa: E402

FAILURES = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


def sid(seconds):
    """Snowflake for a time `seconds` after 2026-07-01 12:00 UTC (same date in any US/EU zone)."""
    return (1782907200000 + int(seconds * 1000) - dc.DISCORD_EPOCH_MS) << 22


def row(t, ch=1, content="hi", author="bob", embed=None):
    return (sid(t), ch, 9, author, content, embed)


def test_chunks():
    names = {1: "harvesting", 2: "newplayer"}
    rows = [row(0, content="where are the goldenwood trees"), row(60, content="north of Prevalia"),
            row(60 + 2 * ds.GAP_S, content="next morning question"),
            row(61 + 2 * ds.GAP_S, content=""),                  # empty: skipped
            row(62 + 2 * ds.GAP_S, ch=2, content="other channel")]
    ch = ds.build_chunks(rows, names)
    check([c["n_msgs"] for c in ch] == [2, 1, 1], f"split on gap and channel: {[c['n_msgs'] for c in ch]}")
    check(ch[0]["text"].startswith("#harvesting 2026-07-01\nbob: where are the goldenwood trees"),
          f"chunk text has channel, date and author lines: {ch[0]['text'][:40]!r}")

    burst = [row(i, content=f"line {i}") for i in range(ds.MAX_MSGS + 3)]
    check([c["n_msgs"] for c in ds.build_chunks(burst, names)] == [ds.MAX_MSGS, 3], "split at MAX_MSGS")
    long = [row(i, content="x" * 700) for i in range(3)]
    check(len(ds.build_chunks(long, names)) == 3, "split at MAX_CHARS")

    again = ds.build_chunks(rows, names)
    check([c["hash"] for c in again] == [c["hash"] for c in ch], "same messages, same hashes")
    grown = ds.build_chunks(rows[:2] + [row(90, content="thanks")] + rows[2:], names)
    check(grown[0]["hash"] != ch[0]["hash"] and [c["hash"] for c in grown[1:]] == [c["hash"] for c in ch[1:]],
          "a new message changes only the chunk it joins")


def main():
    test_chunks()
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILURES: {FAILURES}"))
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())

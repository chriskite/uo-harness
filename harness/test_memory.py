"""Tests for harness/memory.py: the durable harness memory (docs/MEMORY.md).

Covers what a consumer relies on:
  * walk evidence derivation from proxy `step` / `blocked` envelopes
  * the walk projection: deny vs confirm recency, facet isolation
  * harvest node availability transitions over the regrowth window
  * MemoryWriter (the proxy's sink) persists events and walk moves
  * capture ingest: matches the capture's known walk (session_20260929_163420:
    36 proxy `step` events, VISUALIZER.md M1) and is idempotent per tag

Run: python harness/test_memory.py   (offline, a few seconds)
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import memory  # noqa: E402
import nav  # noqa: E402
from memory import UNKNOWN_Z, Memory, MemoryWriter  # noqa: E402

TAG = "20260929_163420"
FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def tmpdb() -> str:
    return os.path.join(tempfile.mkdtemp(), "harness.db")


def proxy_env(data):
    return {"origin": "proxy", "data": data}


def test_walk_rows():
    print("walk_rows")
    check("step -> confirmed move from the origin tile, with z",
          memory.walk_rows(proxy_env({"ev": "step", "from": [10, 10], "to": [11, 9], "z": 5}), 0, 1.0)
          == [(0, 10, 10, 5, 1, 1, 1.0)])
    check("blocked -> deny with the facet given",
          memory.walk_rows(proxy_env({"ev": "blocked", "from": [39, 66], "dir": 4, "z": 1}), 3, 2.0)
          == [(3, 39, 66, 1, 4, 0, 2.0)])
    check("missing z -> UNKNOWN_Z; unknown facet -> 0",
          memory.walk_rows(proxy_env({"ev": "blocked", "from": [1, 1], "dir": 0}), None, 0.0)
          == [(0, 1, 1, UNKNOWN_Z, 0, 0, 0.0)])
    check("a teleport (non-adjacent step) is not a move",
          memory.walk_rows(proxy_env({"ev": "step", "from": [1935, 2580], "to": [39, 65]}), 0, 0.0) == [])
    check("world-origin events are not walk evidence",
          memory.walk_rows({"origin": "world", "data": {"ev": "step", "from": [1, 1], "to": [2, 1]}}, 0, 0.0) == [])


def test_projection():
    print("walk projection")
    path = tmpdb()
    con = memory.connect(path)
    cur = con.cursor()
    memory._upsert_walk(cur, [
        (0, 10, 10, 0, 2, 1, 1.0),      # confirmed E
        (0, 10, 10, 0, 2, 0, 5.0),      # later denied E (door shut) -> blocked
        (0, 20, 20, 0, 4, 0, 1.0),      # denied S
        (0, 20, 20, 0, 4, 1, 9.0),      # later confirmed S -> not blocked
        (0, 20, 20, 7, 4, 0, 3.0),      # deny at another z, still older than the confirm
        (3, 39, 66, 1, 4, 1, 1.0),      # room facet
    ])
    memory._upsert_walk(cur, [(0, 10, 10, 0, 2, 1, 2.0)])   # repeat confirm: counted, not duplicated
    con.commit()
    n = con.execute("SELECT n FROM walk_moves WHERE facet=0 AND x=10 AND y=10 AND ok=1").fetchone()[0]
    con.close()
    check("repeated move increments n", n == 2, str(n))
    m = Memory(path)
    w0, w3 = m.walk_memory(0), m.walk_memory(3)
    check("deny newer than the last confirm blocks", ((10, 10), 2) in w0.blocked, str(w0.blocked))
    check("confirm newer than every deny unblocks (any z)", ((20, 20), 4) not in w0.blocked, str(w0.blocked))
    check("confirmed moves are edges", nav.step((10, 10), 2) in w0.tiles and (20, 21) in w0.tiles)
    check("facets are isolated", (39, 66) not in w0.tiles and (39, 66) in w3.tiles and len(w3.tiles) == 2,
          str(sorted(w3.tiles)))
    m.close()


def test_harvest():
    print("harvest nodes")
    m = Memory(tmpdb())
    node = (0, 1935, 2605, 0, 0x0CE0)
    check("unknown tree is available", m.harvest_available(0, 1935, 2605, 0, 600, 1000.0))
    m.harvest_record(*node, "success", 10, t=1000.0)
    m.harvest_record(*node, "fail", 0, t=1010.0)
    m.harvest_record(*node, "depleted", t=1020.0)
    r = m.harvest_node(0, 1935, 2605, 0)
    check("attempts/successes/yield accumulate; depletion stamped",
          r["attempts"] == 2 and r["successes"] == 1 and r["yield"] == 10 and r["depleted_at"] == 1020.0, str(r))
    check("depleted tree unavailable inside the regrowth window",
          not m.harvest_available(0, 1935, 2605, 0, 600, 1619.0))
    check("available again at the window's end", m.harvest_available(0, 1935, 2605, 0, 600, 1620.0))
    m.harvest_record(0, 1940, 2600, 0, 0x0CE0, "unreachable", t=1000.0)
    check("unreachable tree skipped inside the window, retried after",
          not m.harvest_available(0, 1940, 2600, 0, 600, 1100.0)
          and m.harvest_available(0, 1940, 2600, 0, 600, 1700.0))
    m.harvest_record(0, 1941, 2601, 0, 0x0CE0, "not_tree", t=1000.0)
    check("not-a-tree is never available again", not m.harvest_available(0, 1941, 2601, 0, 0, 1e12))
    check("same tile on another facet is a different node", m.harvest_available(1, 1935, 2605, 0, 600, 1100.0))
    n = m.con.execute("SELECT COUNT(*) FROM harvest_attempts").fetchone()[0]
    check("every outcome logged as an attempt row", n == 5, str(n))
    m.episode("lumber", {"t_start": 1.0, "t_end": 2.0, "logs": 10})
    m.episode("errand", {"t_start": 3.0})
    check("episodes are per loop, in order", m.episodes("lumber") == [{"t_start": 1.0, "t_end": 2.0, "logs": 10}])
    m.close()


def test_writer():
    print("MemoryWriter (proxy sink)")
    path = tmpdb()
    w = MemoryWriter(path)
    w.open_session("t1")
    w.record("t1", {"seq": 0, "t": 1.0, "origin": "proxy",
                    "data": {"ev": "step", "from": [5, 5], "to": [5, 4], "z": 0}}, 0)
    w.record("t1", {"seq": 1, "t": 2.0, "origin": "proxy",
                    "data": {"ev": "blocked", "from": [5, 4], "dir": 0, "z": 0}}, 0)
    w.record("t1", {"seq": 2, "t": 3.0, "origin": "world", "data": {"ev": "speech_heard", "text": "hi"}}, 0)
    w.close_session("t1")
    w.flush()
    m = Memory(path)
    evs = m.con.execute("SELECT seq, origin, ev FROM events ORDER BY seq").fetchall()
    check("every envelope persisted in order", evs == [(0, "proxy", "step"), (1, "proxy", "blocked"),
                                                       (2, "world", "speech_heard")], str(evs))
    wm = m.walk_memory(0)
    check("walk evidence derived", (5, 4) in wm.tiles and ((5, 4), 0) in wm.blocked, str(wm.stats()))
    ended = m.con.execute("SELECT ended FROM sessions WHERE tag='t1'").fetchone()[0]
    check("session closed", ended is not None)
    check("no writer errors", w.errors == 0, str(w.errors))
    m.close()


def test_ingest():
    print(f"ingest session_{TAG}")
    path = tmpdb()
    out = memory.ingest(path, os.path.join(ROOT, "logs"), [TAG], verbose=False)
    m = Memory(path)
    steps = m.con.execute("SELECT COUNT(*) FROM events WHERE origin='proxy' AND ev='step'").fetchone()[0]
    check("the capture's 36 proxy steps", steps == 36, str(steps))
    check("walk rows derived from them", out["walk_rows"] >= 36, str(out))
    wm = m.walk_memory(0)
    check("the errand's login spot is walked ground", (1963, 2597) in wm.tiles, str(wm.stats()))
    again = memory.ingest(path, os.path.join(ROOT, "logs"), [TAG], verbose=False)
    check("re-ingest of a known tag is a no-op", again["sessions"] == 0 and again["skipped"] == 1, str(again))
    m.close()


def main():
    for t in (test_walk_rows, test_projection, test_harvest, test_writer, test_ingest):
        t()
    print(f"\nmemory: {'ALL PASS' if not FAILURES else f'{len(FAILURES)} FAILURES'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

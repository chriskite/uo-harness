"""Replay integration tests: feed captured sessions through the full pipeline.

Replays logs/session_20260928_141253 and logs/session_20260928_164548
(raw proxy captures) through harness/replay.py (prelude -> session key ->
Huffman/XOR -> framing both directions -> WorldRuntime) and asserts the
end-state documented in the Phase 2 contract.

Run directly (python harness/test_world_replay.py) or via harness/test_world.py.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import replay

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S1 = (f"{ROOT}/logs/session_20260928_141253.c2s.raw",
      f"{ROOT}/logs/session_20260928_141253.s2c.raw")
S2 = (f"{ROOT}/logs/session_20260928_164548.c2s.raw",
      f"{ROOT}/logs/session_20260928_164548.s2c.raw")
PLAYER_SERIAL = 0x00094375

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(name)


def test_session_141253():
    print("== replay session_20260928_141253 ==")
    result = replay.replay_session(*S1)
    state, events, rt = result.state, result.events, result.runtime

    check("self serial", state.self.serial == PLAYER_SERIAL,
          f"(got {state.self.serial!r})")
    check("self name", state.self.name == "TestWorth",
          f"(got {state.self.name!r})")
    keepalives = [e for e in events if e["ev"] == "keepalive"]
    check("keepalives >= 30", len(keepalives) >= 30, f"(got {len(keepalives)})")
    walks = [e for e in events if e["ev"] == "walk" and e["moved"]]
    check("walks with position change >= 20", len(walks) >= 20,
          f"(got {len(walks)})")
    check("census serials >= 5", len(state.census.serials) >= 5,
          f"(got {len(state.census.serials)})")
    total_s2c = sum(n for (d, _), n in rt.packet_counts.items() if d == "s2c")
    worst = max((n, pid) for (d, pid), n in rt.unhandled.items() if d == "s2c") \
        if any(d == "s2c" for d, _ in rt.unhandled) else (0, None)
    check("no unhandled S2C id over 40%", worst[0] <= 0.4 * total_s2c,
          f"(worst id {worst[1]!r} x{worst[0]} of {total_s2c})")
    # drain semantics: second drain is empty
    check("second drain empty", rt.drain_events() == [])


def test_session_164548():
    print("== replay session_20260928_164548 ==")
    result = replay.replay_session(*S2)
    state, events, rt = result.state, result.events, result.runtime

    spells = [e["spell_id"] for e in events if e["ev"] == "spell_cast"]
    check("spell casts >= 2", len(spells) >= 2, f"(got {spells})")
    check("spell ids {5, 15}", set(spells) >= {5, 15}, f"(got {set(spells)})")
    queries = [e for e in events if e["ev"] == "item_query"]
    check("item queries >= 10", len(queries) >= 10, f"(got {len(queries)})")
    check("self serial", state.self.serial == PLAYER_SERIAL,
          f"(got {state.self.serial!r})")
    responses = [e for e in events if e["ev"] == "gump_response"]
    check("gump responses >= 1", len(responses) >= 1,
          f"(got {len(responses)})")


def test_determinism():
    print("== determinism ==")
    for tag, paths in (("141253", S1), ("164548", S2)):
        r1 = replay.replay_session(*paths)
        r2 = replay.replay_session(*paths)
        s1 = json.dumps(r1.state.snapshot(), sort_keys=True)
        s2 = json.dumps(r2.state.snapshot(), sort_keys=True)
        check(f"snapshot deterministic {tag}", s1 == s2)
        e1 = json.dumps(r1.events)
        e2 = json.dumps(r2.events)
        check(f"events deterministic {tag}", e1 == e2)


TESTS = [test_session_141253, test_session_164548, test_determinism]


def main():
    for t in TESTS:
        t()
    print(f"\nreplay: {'ALL PASS' if not FAILURES else f'{len(FAILURES)} FAILURES'}")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())

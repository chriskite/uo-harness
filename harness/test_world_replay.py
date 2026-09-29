"""Replay integration tests: feed captured sessions through the full pipeline.

Replays raw proxy captures (logs/session_*.{c2s,s2c}.raw) through
harness/replay.py (13-byte prelude -> XOR keys -> per-packet S2C Huffman via
uo/s2c.py, C2S XOR + framing -> WorldRuntime) and asserts end-state facts
that are grounded in the real server packets:

- self serial 0x00094375 (0x1B LoginConfirm), name TestWorth (0x11/0x98/0xA9)
- login position (0x1B) and final position (last self 0x20/0x77)
- one 0x22 ConfirmWalk per C2S 0x02 walk on sessions without injected walks
- every captured S2C packet frames under the length table and parses

Run directly (python harness/test_world_replay.py) or via harness/test_world.py.
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import replay
from uo.s2c import PRELUDE_LEN
from world.runtime import WorldRuntime, S2C

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def paths(tag):
    return (f"{ROOT}/logs/session_{tag}.c2s.raw",
            f"{ROOT}/logs/session_{tag}.s2c.raw")


S1 = paths("20260928_141253")
S2 = paths("20260928_164548")
S3 = paths("20260929_144541")
PLAYER_SERIAL = 0x00094375
PLAYER_NAME = "TestWorth"

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(name)


def _count(events, ev, **match):
    return sum(1 for e in events if e["ev"] == ev
               and all(e.get(k) == v for k, v in match.items()))


def _walks_confirmed(events):
    walks = _count(events, "walk")
    confirms = _count(events, "walk_confirm")
    check("one ConfirmWalk per C2S walk", walks > 0 and walks == confirms,
          f"(walks {walks}, confirms {confirms})")


def test_session_141253():
    print("== replay session_20260928_141253 ==")
    result = replay.replay_session(*S1)
    state, events, rt = result.state, result.events, result.runtime

    check("self serial", state.self.serial == PLAYER_SERIAL,
          f"(got {state.self.serial!r})")
    check("self name", state.self.name == PLAYER_NAME,
          f"(got {state.self.name!r})")
    check("self vitals from 0x11", (state.self.hits_max, state.self.mana_max,
                                    state.self.stam_max) == (80, 65, 15),
          f"(got {state.self.hits_max}, {state.self.mana_max}, "
          f"{state.self.stam_max})")
    check("S2C keepalives >= 30",
          _count(events, "keepalive", direction=S2C) >= 30)
    _walks_confirmed(events)
    check("census serials >= 5", len(state.census.serials) >= 5,
          f"(got {len(state.census.serials)})")
    named = [m for m in state.mobiles.values()
             if m.name and m.x is not None]
    check("named, positioned mobiles >= 10", len(named) >= 10,
          f"(got {len(named)})")
    check("second drain empty", rt.drain_events() == [])


def test_session_164548():
    print("== replay session_20260928_164548 ==")
    result = replay.replay_session(*S2)
    state, events, rt = result.state, result.events, result.runtime

    spells = [e["spell_id"] for e in events if e["ev"] == "spell_cast"]
    check("spell casts >= 2", len(spells) >= 2, f"(got {spells})")
    check("spell ids {5, 15}", set(spells) >= {5, 15}, f"(got {set(spells)})")
    check("item queries >= 10", _count(events, "item_query") >= 10)
    check("self serial", state.self.serial == PLAYER_SERIAL,
          f"(got {state.self.serial!r})")
    check("gump responses >= 1", _count(events, "gump_response") >= 1)
    check("server buffs on self", bool(state.buffs.get(PLAYER_SERIAL)),
          f"(got {state.buffs})")
    _walks_confirmed(events)


def test_session_144541_s2c_only():
    print("== replay session_20260929_144541, server side only ==")
    s2c_raw = open(S3[1], "rb").read()
    _, _, pkts = replay.s2c_packets(s2c_raw)
    # login position straight from the 0x1B LoginConfirm
    rt = WorldRuntime()
    rt.feed_packet(S2C, s2c_raw[:PRELUDE_LEN])
    for pkt in pkts:
        rt.feed_packet(S2C, pkt)
        if pkt[0] == 0x1B:
            break
    s = rt.state.self
    check("0x1B self serial", s.serial == PLAYER_SERIAL, f"(got {s.serial!r})")
    check("0x1B login position", (s.x, s.y, s.z) == (0x7AB, 0xA25, 0),
          f"(got {(s.x, s.y, s.z)})")

    result = replay.replay_session(None, S3[1])
    s = result.state.self
    check("S2C-only self serial", s.serial == PLAYER_SERIAL,
          f"(got {s.serial!r})")
    check("S2C-only self name", s.name == PLAYER_NAME, f"(got {s.name!r})")
    check("S2C-only character list", result.state.characters == [PLAYER_NAME],
          f"(got {result.state.characters})")
    check("S2C-only final position (last self 0x20/0x77)",
          (s.x, s.y, s.z, s.position_absolute) == (0x7A6, 0xA25, 0, True),
          f"(got {(s.x, s.y, s.z, s.position_absolute)})")
    check("S2C-only welcome text heard",
          any(e["ev"] == "speech_heard" and e["text"] == "Welcome TestWorth!"
              for e in result.events))


def test_session_144541_full():
    print("== replay session_20260929_144541 ==")
    result = replay.replay_session(*S3)
    s = result.state.self
    check("server position wins over dead reckoning",
          (s.x, s.y) == (0x7A6, 0xA25), f"(got {(s.x, s.y)})")
    _walks_confirmed(result.events)


def test_all_captures():
    print("== every capture: framing, parsing, identity ==")
    tags = sorted(os.path.basename(p)[len("session_"):-len(".s2c.raw")]
                  for p in glob.glob(f"{ROOT}/logs/session_*.s2c.raw")
                  if os.path.getsize(p) > PRELUDE_LEN)
    check("captures found", len(tags) >= 3, f"(got {tags})")
    for tag in tags:
        result = replay.replay_session(*paths(tag))
        rt = result.runtime
        ok = (rt.replay_stats["s2c_length_mismatch"] == 0
              and rt.parse_failures == 0
              and result.state.self.serial == PLAYER_SERIAL
              and not rt.anomalies)
        check(f"{tag} clean", ok,
              f"(mismatch {rt.replay_stats['s2c_length_mismatch']}, "
              f"parse_failures {rt.parse_failures}, "
              f"serial {result.state.self.serial!r}, "
              f"anomalies {dict(rt.anomalies)})")


def test_determinism():
    print("== determinism ==")
    for tag, p in (("141253", S1), ("164548", S2)):
        r1 = replay.replay_session(*p)
        r2 = replay.replay_session(*p)
        s1 = json.dumps(r1.state.snapshot(), sort_keys=True)
        s2 = json.dumps(r2.state.snapshot(), sort_keys=True)
        check(f"snapshot deterministic {tag}", s1 == s2)
        check(f"events deterministic {tag}",
              json.dumps(r1.events) == json.dumps(r2.events))


TESTS = [test_session_141253, test_session_164548,
         test_session_144541_s2c_only, test_session_144541_full,
         test_all_captures, test_determinism]


def main():
    for t in TESTS:
        t()
    print(f"\nreplay: {'ALL PASS' if not FAILURES else f'{len(FAILURES)} FAILURES'}")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())

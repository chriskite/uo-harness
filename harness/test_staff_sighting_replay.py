"""Regression: the invulnerable-player staff hint (speech_guard.invulnerable_player,
notoriety 7 + player flag 0x20; docs/PLAN.md "Staff alarm on an invulnerable player
in view") never fires on any committed capture.

Every `git ls-files 'logs/session_*.s2c.raw'` capture is replayed through the world
model (replay.replay_session). Notoriety and flags only change in an 0x20 (Outlands
0x77/0x78 carry neither: docs/research/THREATS.md §1.1), so the mobile an 0x20 is
about is checked after every 0x20: the check is exact, not sampled. The runners'
own path (SpeechGuard.sightings over a world snapshot) runs every SNAPSHOT_EVERY
packets and at the end of each capture. The test also asserts the captures hold
what makes the zero meaningful: notoriety-7 mobiles (vendors, NPCs) and mobiles
with the player flag.

Run: python harness/test_staff_sighting_replay.py   (~2 s over the 10 committed
captures, 2026-10-04; it reads the working-tree files)
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import replay  # noqa: E402
import threats  # noqa: E402
from speech_guard import SpeechGuard, invulnerable_player  # noqa: E402

SNAPSHOT_EVERY = 500
FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {'' if cond else detail}")
    if not cond:
        FAILURES.append(name)


def captures():
    out = subprocess.run(["git", "ls-files", "logs/session_*.s2c.raw"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout.split()
    return [os.path.join(ROOT, p) for p in out]


def scan(s2c_path):
    """(hits, noto7 serials, player-flag serials, 0x20 checks, sightings-path hits) for one capture."""
    c2s_path = s2c_path.replace(".s2c.raw", ".c2s.raw")
    guard = SpeechGuard()
    hits, noto7, flagged, n = [], set(), set(), {"checks": 0, "pkts": 0, "guard": []}

    def on_s2c(rt, pkt):
        n["pkts"] += 1
        if pkt[0] == 0x20 and len(pkt) >= 5:
            serial = int.from_bytes(pkt[1:5], "big")
            m = rt.state.mobiles.get(serial)
            if m is not None:
                n["checks"] += 1
                d = m.to_dict()
                if d.get("notoriety") == 7:
                    noto7.add(serial)
                if (d.get("flags") or 0) & threats.FLAG_PLAYER_HINT:
                    flagged.add(serial)
                if invulnerable_player(d):
                    hits.append((n["pkts"], f"0x{serial:08X}", d))
        if n["pkts"] % SNAPSHOT_EVERY == 0:
            n["guard"] += guard.sightings(rt.state.snapshot())

    res = replay.replay_session(c2s_path if os.path.exists(c2s_path) else None, s2c_path, on_s2c=on_s2c)
    n["guard"] += guard.sightings(res.state.snapshot())
    return hits, noto7, flagged, n["checks"], n["guard"]


def main():
    print("== the invulnerable-player hint over every committed capture ==")
    t0 = time.monotonic()
    paths = captures()
    check("committed captures found", len(paths) > 0, str(paths))
    all_hits, all_guard, noto7, flagged, checks = [], [], 0, 0, 0
    for p in paths:
        hits, n7, fl, c, g = scan(p)
        tag = os.path.basename(p)[len("session_"):-len(".s2c.raw")]
        print(f"  {tag}: {c} mobile 0x20s, {len(n7)} notoriety-7 mobiles, {len(fl)} with the player flag, "
              f"{len(hits)} hint(s)")
        all_hits += [(tag, *h) for h in hits]
        all_guard += [(tag, x["serial"]) for x in g]
        noto7 += len(n7)
        flagged += len(fl)
        checks += c
    check("the captures hold notoriety-7 mobiles and player-flagged mobiles (the zero means something)",
          noto7 > 0 and flagged > 0, f"{noto7} notoriety 7, {flagged} flagged")
    check(f"zero hints after any of the {checks} mobile 0x20s (exact: notoriety/flags change only there)",
          all_hits == [], str(all_hits[:5]))
    check(f"zero sightings on the runners' path (SpeechGuard.sightings every {SNAPSHOT_EVERY} packets and at "
          f"the end)", all_guard == [], str(all_guard[:5]))
    print(f"  {len(paths)} captures in {time.monotonic() - t0:.1f} s")
    print("ALL PASS" if not FAILURES else f"FAILED: {len(FAILURES)}: {FAILURES}")
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()

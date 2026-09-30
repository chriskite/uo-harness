"""Tests for harness/pathfind.py on the real Outlands map (read-only install files).

Ground truth is server evidence:
- every server-confirmed move in walk memory (harness/data/walkmem.json,
  facet 0) must be walkable under the client rules from some standing z
- the live agent run of 2026-09-29 was put upstairs in the Shelter inn at
  (1938, 2584, z 20) and could not get out on 2D walk memory; a 3D route
  down to the innkeeper's floor must exist
- the demo's tree (1898, 2622, static 0x0CE0) is not walkable
- the demo's harvest stand (1898, 2621) lands at z 10, the z the server
  reported there (0x20/0x77 in session 20260929_204225)

Run: python harness/test_pathfind.py   (a few seconds)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nav  # noqa: E402
import pathfind  # noqa: E402
import uomap  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}{'' if cond else ' ' + detail}")
    if not cond:
        FAILURES.append(name)


def standing_zs(walk, x, y):
    return {o[3] for o in walk.objects(x, y) if o[2] & (pathfind.POF_SURFACE | pathfind.POF_BRIDGE)}


def main():
    walk = pathfind.Walk(uomap.UoMap(0))
    print("== walk memory agrees with the client rules ==")
    mem = nav.WalkMemory.load(os.path.join(ROOT, "harness", "data", "walkmem.json"))
    edges = [(a, b) for a, b in mem.edges if a[0] >= 1000]      # facet 0 (rooms are facet 3)
    bad = []
    for a, b in edges:
        d = nav.direction(a, b)
        if not any((walk.can_walk(a[0], a[1], z, d) or (None, None))[:2] == b
                   for z in standing_zs(walk, *a)):
            bad.append((a, b))
    check(f"all {len(edges)} server-confirmed moves are walkable", not bad and len(edges) > 1000,
          f"{len(bad)} disagree, e.g. {bad[:5]}")

    print("== live agent denies upstairs (session 20260929_204225) are walls in the model ==")
    import json
    rows = [json.loads(line) for line in open(os.path.join(ROOT, "logs", "session_20260929_204225.jsonl"),
                                              encoding="utf-8") if '"blocked"' in line]
    denies = [r for r in rows if r.get("ev") == "blocked" and r.get("src") == "agent"
              and 1930 <= r["from"][0] <= 1945 and 2578 <= r["from"][1] <= 2592]
    # The two door tiles: the runner logged "move 6/4 from (1937, 2583) blocked
    # by a door; opening it" there (closed doors are dynamic items).
    doors = {(1936, 2583), (1937, 2584)}
    walls = [r for r in denies if nav.step(tuple(r["from"]), r["dir"]) not in doors]
    wrong = [r for r in walls if walk.can_walk(r["from"][0], r["from"][1], 20, r["dir"]) is not None]
    check(f"all {len(walls)} non-door denies are walls in the static map", len(walls) >= 20 and not wrong,
          str([(r["from"], r["dir"]) for r in wrong]))

    print("== inn upstairs -> innkeeper floor (live attempt 1) ==")
    path = pathfind.plan(walk, (1938, 2584, 20), nav.within((1943, 2596), 0))
    check("route found", path is not None)
    if path:
        check("route descends to the ground floor", path[-1][2] <= 2 and path[0][2] == 20,
              f"{path[0]} -> {path[-1]}")
        check("route stays reasonable (< 40 steps)", len(path) - 1 < 40, str(len(path) - 1))

    print("== trees ==")
    check("the demo tree tile is not walkable", walk.can_walk(1898, 2621, 10, 4) is None)
    stand = walk.new_z(1898, 2621, 10, 4)
    check("the demo harvest stand is walkable at the server's z 10", stand == 10, str(stand))

    print(f"\npathfind: {'ALL PASS' if not FAILURES else f'{len(FAILURES)} FAILURES'}")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())

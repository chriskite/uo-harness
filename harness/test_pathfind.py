"""Tests for harness/pathfind.py on the real Outlands map (read-only install files).

Ground truth is server evidence:
- every server-confirmed move in walk memory built from the committed
  captures (logs/, facet 0) must be walkable under the client rules from some standing z
- the live agent run of 2026-09-29 was put upstairs in the Shelter inn at
  (1938, 2584, z 20) and could not get out on 2D walk memory; a 3D route
  down to the innkeeper's floor must exist
- the demo's tree (1898, 2622, static 0x0CE0) is not walkable
- the demo's harvest stand (1898, 2621) lands at z 10, the z the server
  reported there (0x20/0x77 in session 20260929_204225)
- live run 3 (session 20260929_224710) chopped the surface trees (1924, 2591)
  and (1924, 2588) (z 5) from a cave under them (z -20, "cave floor" statics)
  and asked the innkeeper for the room from there: height-aware goals
  (agent_link.reach_z / same_floor) must end on the target's level

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
    mem = nav.build_from_logs(os.path.join(ROOT, "logs"))
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

    print("== height-aware goals: not from the cave under the trees (live run 3) ==")
    import agent_link
    td = uomap.tiledata()
    exit_tile = (1930, 2589, 20)                  # where the room exit put the agent
    for tx, ty, g in ((1924, 2591, 0x0CE0), (1924, 2588, 0x0CD8)):
        flat = pathfind.plan(walk, exit_tile, nav.within((tx, ty), 1))
        check(f"tree {tx},{ty}: a height-blind goal ends in the cave (the live hazard)",
              flat is not None and flat[-1][2] == -20, str(flat and flat[-1]))
        path = pathfind.plan(walk, exit_tile, nav.within((tx, ty), 1, agent_link.reach_z(5, td.item(g).height)))
        check(f"tree {tx},{ty}: the height-aware goal ends beside it on the surface",
              path is not None and path[-1][2] == 5 and nav.chebyshev(path[-1][:2], (tx, ty)) == 1,
              str(path and path[-1]))
    inn = pathfind.plan(walk, (1925, 2592, -20), nav.within((1932, 2595), 4, agent_link.same_floor(21)))
    check("innkeeper from the cave: ends within one storey of her (ground floor or up)",
          inn is not None and inn[-1][2] >= 0, str(inn and inn[-1]))

    print("== proxy z per confirmed step matches the server (walk confirms carry no z) ==")
    import proxy
    import viz_feed
    seen = []                                     # (facet, predicted z, server z) at the predicted tile
    orig = proxy.MoveAuthority.on_self_position

    def spy(ma, x, y, z, d):
        if ma.pos is not None and (ma.pos[0], ma.pos[1]) == (x, y):
            seen.append((drv.tap.world.state.self.map, ma.pos[2], z))
        return orig(ma, x, y, z, d)
    proxy.MoveAuthority.on_self_position = spy
    try:
        drv = viz_feed.ReplayDriver("20260929_204225", os.path.join(ROOT, "logs"))
        drv.run_to_end()
    finally:
        proxy.MoveAuthority.on_self_position = orig
    mapped = [s for s in seen if s[0] in pathfind.MAP_FACETS]
    wrong = [s for s in mapped if s[1] != s[2]]
    check("demo capture: every server anchor on a mapped facet agrees with the map-computed z "
          "(the old anchor-only z missed 6 of 103 here; live 2026-09-30, 12 of 57)",
          len(mapped) >= 80 and not wrong, f"{len(mapped)} anchors, wrong {wrong[:5]}")

    print(f"\npathfind: {'ALL PASS' if not FAILURES else f'{len(FAILURES)} FAILURES'}")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())

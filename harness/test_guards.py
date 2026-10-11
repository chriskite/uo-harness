"""Tests for harness/guards.py (guard-zone crossings, flight goals, bank
markers, the way there on foot: plan_route on a synthetic grid and on the
real map when the install has it), nav.any_of, agent_link.stamina_ok and the
memory store's guard_points. No network.

Run: python harness/test_guards.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import guards  # noqa: E402
import nav  # noqa: E402
from agent_link import stamina_ok  # noqa: E402
from memory import Memory  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(name)


def test_entries():
    step = {"ev": "step", "from": [788, 1494], "to": [788, 1493]}
    enter = {"ev": "cliloc", "cliloc": guards.ENTER_CLILOC}
    r = guards.entries([step, enter])
    check("enter notice after a one-tile step: the tile we stand on", r == [(788, 1493)], r)
    r = guards.entries([step, {"ev": "cliloc", "cliloc": 500113}])
    check("leave notice: nothing (it doesn't say where the inside is)", r == [], r)
    r = guards.entries([{"ev": "step", "from": [1, 1], "to": [3, 1]}, enter])
    check("enter notice after a two-tile step (teleport): nothing", r == [], r)
    r = guards.entries([enter])
    check("enter notice with no step before it: nothing", r == [], r)


def test_any_of():
    g = nav.any_of([((10, 10), 0), ((50, 50), 2)])
    check("any_of accepts each goal's area", g((10, 10)) and g((49, 48)))
    check("any_of rejects outside every area", not g((12, 10)))
    check("any_of heuristic is the nearest goal's", g.heuristic((0, 0)) == 10, g.heuristic((0, 0)))


def test_flee_goals():
    pts = {(10, 0), (-10, 0)}
    r = guards.flee_goals(pts, [], 0, (0, 0), (5, 0))
    check("near attacker: places on its side dropped", r == [((-10, 0), 0)], r)
    r = guards.flee_goals(pts, [], 0, (0, 0), (100, 0))
    check("far attacker: all kept", sorted(r) == [((-10, 0), 0), ((10, 0), 0)], r)
    r = guards.flee_goals({(10, 0), (8, 1)}, [], 0, (0, 0), (5, 0))
    check("every place on the attacker's side: all kept", len(r) == 2, r)
    r = guards.flee_goals({(300, 0), (10, 0)}, [((20, 0), 0), ((0, 20), 1)], 0, (0, 0), None)
    check("any distance (no cap since 2026-10-11), nearest first; other facets dropped; banks get BANK_RADIUS",
          r == [((10, 0), 0), ((20, 0), guards.BANK_RADIUS), ((300, 0), 0)], r)


def test_bank_markers():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "m.xml")
        with open(p, "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?><Pack Name="t">\n'
                    '<Marker Name="Horseshoe Bay Bank" X="2009" Y="2222" Icon="BANK" Facet="0"/>\n'
                    '<Marker Name="Corpse Creek Bank" X="0864" Y="0702" Icon="BANK" Facet="0"/>\n'
                    '<Marker Name="Horseshoe Bay Healer" X="2000" Y="2200" Icon="HEALER" Facet="0"/>\n'
                    '</Pack>')
        r = guards.bank_markers(p)
        check("banks outside lawless towns only", r == [((2009, 2222), 0)], r)
        r = guards.bank_markers(os.path.join(d, "missing.xml"))
        check("missing marker file: none", r == [], r)


def test_stamina_ok():
    check("stamina 0 (hamstrung): walk", not stamina_ok({"world": {"self": {"stam": 0}}}))
    check("stamina 1: walk (stock client: Stamina <= 1)", not stamina_ok({"world": {"self": {"stam": 1}}}))
    check("stamina unknown: run", stamina_ok({"world": {"self": {"stam": None}}}))


def test_memory_points():
    with tempfile.TemporaryDirectory() as d:
        m = Memory(os.path.join(d, "t.db"))
        m.guard_point_record(0, 1, 2)
        m.guard_point_record(0, 1, 2)
        m.guard_point_record(1, 3, 4)
        check("guard_points per facet", m.guard_points(0) == {(1, 2)}, m.guard_points(0))
        n = m.con.execute("SELECT n FROM guard_points WHERE facet=0 AND x=1 AND y=2").fetchone()[0]
        check("a repeated crossing counts up", n == 2, n)
        m.con.close()


class Grid:
    """pathfind.Walk's face for plan_route / stand_tile: `land` tiles walkable at z 0, 8 directions (no corner
    rule)."""
    DX = (0, 1, 1, 1, 0, -1, -1, -1)
    DY = (-1, -1, 0, 1, 1, 1, 0, -1)

    def __init__(self, land):
        self.land = set(land)

    def can_walk(self, x, y, z, d):
        n = (x + self.DX[d], y + self.DY[d])
        return (*n, 0) if n in self.land else None

    def objects(self, x, y):
        return [(0, 0, 3, 0, ("flat", None))] if (x, y) in self.land else []


def test_plan_route():
    main = {(x, y) for x in range(0, 60) for y in range(0, 30)}
    isle = {(x, y) for x in range(100, 120) for y in range(0, 20)}          # 400 tiles, water all round
    walk = Grid(main | isle)
    town = [((55, 25), 2)]
    r = guards.plan_route(walk, (2, 2, 0), town)
    check("reachable: 'reached', the path from the start into the goal's radius",
          r.status == "reached" and r.path[0] == (2, 2) and max(abs(r.path[-1][0] - 55), abs(r.path[-1][1] - 25)) <= 2
          and r.goal == ((55, 25), 2), r)
    r = guards.plan_route(walk, (105, 5, 0), town)
    check("an island: every tile searched, none guarded", r.status == "island" and r.path == []
          and r.expanded == len(isle), r)
    r = guards.plan_route(walk, (2, 2, 0), [((110, 10), 0)], max_expand=500)
    check("the budget spent first: undecided", r.status == "undecided" and r.expanded == 501, r)
    pocket = Grid({(0, 0), (1, 0)} | isle)
    r = guards.plan_route(pocket, (0, 0, 0), town)
    check("a pocket smaller than FLEE_ISLAND_MIN (a roof, a rock): undecided, not an island",
          r.status == "undecided", r)
    r = guards.plan_route(walk, (2, 2, 0), [((55, 25), 2), ((110, 10), 0)])
    check("two goals, one across the water: the reachable one", r.status == "reached" and r.goal == ((55, 25), 2), r)
    tile = guards.stand_tile(walk, (62, 10))
    check("stand_tile: off the water to the nearest land ring", tile is not None and tile[0] == 59
          and abs(tile[1] - 10) <= 3, tile)
    path = [(i, 0) for i in range(100)]
    check("join: level with us beside the route, not its start", guards.join(path, (40, 3)) == 43, guards.join(path, (40, 3)))
    check("ahead: GUARD_LEG_STEPS-style tiles of route past the join", guards.ahead(path, 43, 40) == 83
          and guards.ahead(path, 90, 40) == 99)
    sparse = path[::4] + [path[-1]]
    j = guards.join(sparse, (40, 3))
    check("a cached (sparse) route: joined level with us too, its legs counted in tiles",
          sparse[j] == (44, 0) and sparse[guards.ahead(sparse, j, 40)] == (84, 0), (j, sparse[j]))
    check("join: at its start when we stand there", guards.join(path, (0, 0)) == 0)
    s = guards.straight_route((0, 0), [((300, 30), 0), ((-500, 0), 2)])
    check("no map: the straight line to the nearest place ('unmapped')", s.status == "unmapped"
          and s.path[0] == (0, 0) and s.path[-1] == (300, 30) and len(s.path) == 301, s.summary())
    r = guards.plan_route(walk, (2, 2, 0), town)
    back = guards.Route.from_json(r.to_json())
    check("the store's cached form: every ROUTE_KEEP_EVERY-th tile and the last, source 'spot'",
          back.status == "reached" and back.path[0] == r.path[0] and back.path[-1] == r.path[-1]
          and len(back.path) <= len(r.path) // guards.ROUTE_KEEP_EVERY + 2 and back.goal == r.goal
          and back.source == "spot", back)


# The client's facet-0 bank markers outside lawless towns (Banks_and_Healers.xml, read 2026-10-11)
BANKS_0 = [((1614, 1501), 2), ((1929, 1704), 2), ((1750, 3003), 2), ((2188, 628), 2), ((3050, 545), 2),
           ((726, 1508), 2), ((1955, 2583), 2), ((3732, 534), 2), ((2009, 2222), 2), ((3840, 2881), 2)]


def test_plan_route_map():
    """On the real map (skipped without the install's map files): the 16:44 death spot (lumber-20261011-163321-38d5,
    Dan at (4172,849) when Jimii Hendrix came) reaches Anchor's Rest's bank on foot within the escape's budget (~1 s);
    Nusero Island (witcher_280's centre) is an island within the trip's budget."""
    import uomap
    import pathfind
    if not os.path.exists(os.path.join(uomap.INSTALL, "map0.uoo")):
        print("  SKIP no map files")
        return
    walk = pathfind.Walk(uomap.UoMap(0))
    r = guards.plan_route(walk, (4172, 849, 0), BANKS_0)
    check("the 16:44 death spot: reached Anchor's Rest (3732,534) within FLEE_ROUTE_ESCAPE_EXPAND",
          r.status == "reached" and r.goal[0] == (3732, 534), r.summary())
    print(f"    {r.summary()}")
    start = guards.stand_tile(walk, (339, 2043))
    r = guards.plan_route(walk, start, BANKS_0, guards.FLEE_ROUTE_PREP_EXPAND)
    check("Nusero Island: an island within FLEE_ROUTE_PREP_EXPAND (recall is the only way out)", r.status == "island",
          r.summary())
    print(f"    {r.summary()}")


if __name__ == "__main__":
    for t in (test_entries, test_any_of, test_flee_goals, test_bank_markers,
              test_stamina_ok, test_memory_points, test_plan_route, test_plan_route_map):
        print(t.__name__)
        t()
    print("FAILED: " + ", ".join(FAILURES) if FAILURES else "ALL PASS")
    sys.exit(1 if FAILURES else 0)

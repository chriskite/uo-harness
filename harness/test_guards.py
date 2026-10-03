"""Tests for harness/guards.py (guard-zone crossings, flight goals, bank
markers), nav.any_of, agent_link.stamina_ok and the memory store's
guard_points. No network.

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
    check("beyond FLEE_MAX_DIST and other facets dropped; banks get BANK_RADIUS",
          sorted(r) == [((10, 0), 0), ((20, 0), guards.BANK_RADIUS)], r)


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


if __name__ == "__main__":
    for t in (test_entries, test_any_of, test_flee_goals, test_bank_markers,
              test_stamina_ok, test_memory_points):
        print(t.__name__)
        t()
    print("FAILED: " + ", ".join(FAILURES) if FAILURES else "ALL PASS")
    sys.exit(1 if FAILURES else 0)

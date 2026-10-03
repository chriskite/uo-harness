"""The dungeon crawl's geometry and efficiency model (harness/crawl.py), offline.

Two rooms side by side, a one-tile wall between them with a door at its far end: waypoint
coverage, areas and levels must follow routes, never the straight line through the wall.
The model must shrink a single bad fight of a well-known creature away but learn to avoid
an unknown one we fled from; levels open only once known and predicted safe, and close
after survival leaves; the store prior joins loot gold to fights and keeps other dungeons'
level stats out; the next waypoint skips closed levels, depleted and dangerous areas.

Run: python harness/test_crawl.py   (no network, < 1 s)
"""
import json
import os
import random
import sys
import tempfile
import time
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import crawl  # noqa: E402
import nav  # noqa: E402
from memory import Memory  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}{'' if cond else ' ' + str(detail)}")
    if not cond:
        FAILURES.append(name)


def grid_floor(tiles, exit_tile):
    """A Floor over `tiles` with 8-way steps, diagonals only past two open corners."""
    tiles = set(tiles)
    adj = {}
    for x, y in tiles:
        ns = []
        for dx, dy in nav.DIR_DELTA:
            n = (x + dx, y + dy)
            if n in tiles and (dx == 0 or dy == 0 or ((x + dx, y) in tiles and (x, y + dy) in tiles)):
                ns.append(n)
        adj[(x, y)] = tuple(ns)
    return crawl.Floor(adj, exit_tile)


# room A x 0-9, room B x 11-20 (y 0-9); the wall x = 10 has its door at (10, 9)
ROOMS = {(x, y) for x in range(0, 21) for y in range(0, 10) if x != 10} | {(10, 9)}
EXIT = (9, 0)           # in room A, against the wall: room B is 2 tiles away in a straight line
TELE = [(5, 0)]


def test_geometry():
    print("== waypoints, areas, levels ==")
    floor = grid_floor(ROOMS, EXIT)
    wps = crawl.waypoints(floor, 4, TELE)
    far = max(min(floor.bfs(w).get(t, 1 << 30) for w in wps) for t in ROOMS
              if all(crawl.cheb(t, p) > crawl.TELEPORT_GAP for p in TELE))
    check("every tile (away from the teleporter) within 4 route steps of a waypoint", far <= 4, far)
    check("waypoints in both rooms", any(w[0] < 10 for w in wps) and any(w[0] > 10 for w in wps), wps)
    check("no waypoint within 2 tiles of the teleporter",
          all(crawl.cheb(w, p) > crawl.TELEPORT_GAP for w in wps for p in TELE), wps)
    check("the exit's straight-line neighbour across the wall is 20 route steps out (round by the door)",
          floor.dist_exit[(11, 0)] == 20, floor.dist_exit.get((11, 0)))
    own = crawl.cells(floor, wps)
    check("a tile across the wall belongs to a waypoint in its own room",
          wps[own[(11, 0)]][0] > 10 and wps[own[(9, 1)]][0] < 10, (wps[own[(11, 0)]], wps[own[(9, 1)]]))
    band = 10
    lv = {w: floor.dist_exit[w] // band for w in wps}
    check("levels by route: room B's waypoints are deeper than room A's (straight line: the other way round)",
          min(v for w, v in lv.items() if w[0] > 10) >= 1 and min(v for w, v in lv.items() if w[0] < 10) == 0, lv)

    mem = nav.WalkMemory()
    for t in ROOMS:
        for n in floor.adj[t]:
            mem.add_step(t, n)
    mem.add_step((30, 30), (31, 30))                 # somewhere else, not connected
    f2 = crawl.floor_from_memory(mem, EXIT, EXIT, TELE)
    check("walk-memory floor: the connected tiles minus the teleporter",
          len(f2) == len(ROOMS) - 1 and (5, 0) not in f2 and (30, 30) not in f2, len(f2))


def test_model():
    print("== efficiency model ==")
    bats = [{"name": "a mongbat", "fight_s": 40.0, "hits": 20, "outcome": "kill", "gold": 20} for _ in range(100)]
    m = crawl.Model(bats)
    m.add_fight("a mongbat", 300.0, 80, "kill")
    check("one bad fight of a well-known type doesn't make it avoided",
          m.avoid("a mongbat", 94, 0.5, 180) is None, m.estimate("a mongbat"))
    check("a type never fought isn't avoided (no evidence)", m.avoid("an ogre", 94, 0.5, 180) is None)
    m.add_fight("an ogre", None, 45, "fled")
    check("one fight we fled from an unknown type: avoided", m.avoid("ogre", 94, 0.5, 180) is not None,
          m.estimate("ogre"))
    for _ in range(3):
        m.add_fight("a troll", 50.0, 70, "kill")
    check("hits per fight against our max hits: avoided at 94 max hits, not at 200",
          m.avoid("troll", 94, 0.5, 180) is not None and m.avoid("troll", 200, 0.5, 180) is None,
          m.estimate("troll"))
    m.add_fight("a slime", 400.0, 0, "kill")
    m.add_fight("a slime", 400.0, 0, "kill")
    check("too slow to kill: avoided", "s per kill" in (m.avoid("slime", 94, 0.5, 180) or ""), m.estimate("slime"))
    e = m.estimate("mongbat")
    check("gold per kill and gold/min while fighting shrunk toward the pool",
          abs(e["gold"] - 20) < 0.1 and abs(e["gold_min"] - e["gold"] * 60 / e["fight_s"]) < 1e-6, e)


class FakeHunt:
    def __init__(self, args):
        self.args = args
        self.spot = EXIT
        self.memory = types.SimpleNamespace(job_event=lambda *a, **k: None)
        self.human = types.SimpleNamespace(rng=random.Random(1))
        self.mover = types.SimpleNamespace(danger={}, replan_requested=False)
        self.patrolling = False
        self.dead = set()


def args(**kw):
    a = dict(pull_range=4, crawl_band=10, crawl_floors=0, crawl_dwell=6.0, crawl_depleted_s=90.0,
             crawl_learn_s=600.0, crawl_max_dmg=30.0, crawl_depth_risk=1.5, crawl_max_leaves=2,
             crawl_max_hits=0.5, crawl_max_fight_s=180.0, target_name="")
    a.update(kw)
    return types.SimpleNamespace(**a)


def make_crawl(model, **kw):
    c = crawl.Crawl(FakeHunt(args(**kw)), model)
    c.floor = grid_floor(ROOMS, EXIT)
    c.wps = crawl.waypoints(c.floor, 4, TELE)
    c.cell_of = crawl.cells(c.floor, c.wps)
    c.level = [c.floor.dist_exit[w] // 10 for w in c.wps]
    c.top = max(c.level)
    return c


def test_levels():
    print("== levels ==")
    c = make_crawl(crawl.Model())
    c.update_levels()
    check("no data: level 0 only", c.unlocked == 0)
    c.model.add_level(0, s=700.0, hits=50)
    c.update_levels()
    check("level 0 known (> learn_s) and safe: level 1 opens", c.unlocked == 1, c.model.rates(1))
    c = make_crawl(crawl.Model())
    c.model.add_level(0, s=700.0, hits=700)          # 60 hits/min observed
    c.update_levels()
    check("level 0 known but costly: the predicted level 1 risk keeps it closed", c.unlocked == 0,
          c.model.rates(1))
    c = make_crawl(crawl.Model(), crawl_learn_s=60.0)
    c.model.add_level(0, s=700.0, hits=50)
    c.update_levels()
    lv1 = next(w for w, k in zip(c.wps, c.level) if k == 1)
    c.on_leave(lv1, True)
    c.on_leave(lv1, True)
    c.update_levels()
    check("two survival leaves from level 1: back up to level 0, and it stays closed", c.unlocked == 0
          and 1 in c.closed, (c.unlocked, c.closed))
    c.update_levels()
    check("a closed level isn't reopened this run", c.unlocked == 0)


def test_choose():
    print("== next waypoint ==")
    c = make_crawl(crawl.Model())
    a_side = [i for i, w in enumerate(c.wps) if w[0] < 10]
    here = c.wps[a_side[0]]
    nxt = c.choose(here)
    check("only open levels: the next waypoint is in level 0", nxt is not None and c.level[nxt] == 0,
          (nxt, c.level))
    now = time.monotonic()
    for i in a_side:
        if c.level[i] == 0 and i != c.cell_of[here]:
            c.depleted[i] = now + 100
    c.unlocked = c.top
    nxt = c.choose(here)
    check("depleted areas skipped", nxt is not None and c.depleted.get(nxt, 0) <= now, nxt)
    deep = [i for i in range(len(c.wps)) if c.level[i] >= 1]
    c.danger[1] = (c.wps[deep[0]], now, "troll")
    picks = {c.choose(here) for _ in range(20)}
    check("an avoided creature's area is skipped", deep[0] not in picks, picks)
    c2 = make_crawl(crawl.Model())
    c2.unlocked = c2.top
    i0, i1 = [i for i in range(len(c2.wps)) if i != c2.cell_of[here]][:2]
    for i in range(len(c2.wps)):
        c2.visited[i] = now
    c2.visited.pop(i1)
    picks = [c2.choose(here) for _ in range(20)]
    check("the stalest area wins over freshly visited ones", picks.count(i1) == 20, picks)
    c2.crowd_t[i1] = now
    c2.visited[i0] = now - 300
    picks = [c2.choose(here) for _ in range(20)]
    check("players or pets seen there: a crowded area loses to a less stale empty one", i1 not in picks, picks)


def test_prior():
    print("== store prior ==")
    db = os.path.join(tempfile.mkdtemp(), "harness.db")
    mem = Memory(db)
    for i in range(4):
        s = f"0x00{i:06X}"
        mem.job_event("hunt", "fight", {"serial": s, "name": "a mongbat", "fight_s": 30.0, "hits_lost": 10,
                                        "outcome": "kill"})
        if i < 2:
            mem.job_event("hunt", "loot", {"mob": s, "name": "a mongbat", "gold": 40})
    mem.job_event("hunt", "fight", {"serial": "0x00FF0000", "name": "an ogre", "fight_s": 12.0,
                                    "hits_lost": 50, "outcome": "fled"})
    t = time.time() - 3600
    mem.episode("hunt", {"t_start": t, "t_end": t + 600, "gold": 100, "hits_lost": 200,
                         "crawl": {"spot": [9, 0], "levels": {"1": {"s": 600.0, "hits": 30, "gold": 50,
                                                                     "kills": 2, "leaves": 0}}}})
    mem.episode("hunt", {"t_start": t + 700, "t_end": t + 1300, "gold": 0, "hits_lost": 0,
                         "crawl": {"spot": [1, 1], "levels": {"1": {"s": 900.0, "hits": 900}}}})
    m = crawl.load_prior(mem.con, (9, 0))
    e = m.estimate("mongbat")
    check("gold joined by mob serial; kills without a loot count 0 gold: 20 a kill (shrunk toward the pool)",
          m.gold["mongbat"] == [40, 40, 0, 0] and abs(e["gold"] - 20.0) < 1e-6, (m.gold["mongbat"], e))
    check("the fled ogre is avoided from the start of the next run", m.avoid("ogre", 94, 0.5, 180) is not None)
    check("level stats from earlier crawls from the same spot only", m.levels[1]["s"] == 600.0
          and m.levels[1]["hits"] == 30, dict(m.levels[1]))
    check("level 0's base: the hunt episodes' gold and hits lost per minute (20 min: 5 and 10)",
          abs(m.base["gold_min"] - 5.0) < 1e-6 and abs(m.base["hits_min"] - 10.0) < 1e-6, m.base)
    mem.close()


if __name__ == "__main__":
    test_geometry()
    test_model()
    test_levels()
    test_choose()
    test_prior()
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILURES: {FAILURES}"))
    sys.exit(0 if not FAILURES else 1)

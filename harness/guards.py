"""Town-guard zones: where to run when a PK shows up, and the way there on foot.

Guard zone boundaries are not in the client data. Two sources:
- learned entries: the server's "You are now under the protection of the town
  guards." (cliloc 500112) right after a one-tile step marks the tile we stand on
  as inside a zone (guard_points table, memory.py). Mover.step records them
  live; `backfill` replays the store's event log.
- bank markers from the client's Banks_and_Healers.xml (read-only, install dir),
  minus lawless towns.

The server's guard notices lag and skip crossings (docs/NOTES.md "Guard zone
notices", live 2026-10-02 Prevalia: one 500113 3.3 s after the step out, then
none over 14 more crossings in 95 s). So a notice only says which side we are on
when it arrives: a "left" notice (500113) doesn't tell where the inside is, and
no tile beyond the one we stand on is inferred.

The way there (user 2026-10-11, after Outland Dan's death to a PK: "ideally we'd run towards the
nearest guard zone no matter how far away it is. Unless we're trapped on an island in which case our
only choice is to recall"): `plan_route`, a weighted A* over the map's walkability (pathfind.Walk)
to the nearest guarded place, no distance cap. Its answer is "reached" (the route), "island" (every
tile reachable on foot was searched: no guarded place among them), or "undecided" (the search budget
ran out first). Measured on the real map (2026-10-11, 40 random lumber spots on facet 0, cold tile
cache): weight 3 reaches 33 of them in 0.04-1.6 s (656-33 062 expansions, ~20 000/s) over routes of
123-1 063 steps; the unweighted search needed 300 000 expansions (3.1 s) for the 629-step route from
the 16:44 death spot (4172,849) to Anchor's Rest's bank, weight 3 9 196 (0.26 s) for 689 steps.
Nusero Island (witcher_280) is an island after 125 046 expansions (7.0 s), witcher_242 after 126 796.

CLI: python harness/guards.py backfill [--db PATH]
     python harness/guards.py route X Y [--facet N] [--expand N] [--db PATH]
"""
import argparse
import heapq
import itertools
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nav  # noqa: E402
import uomap  # noqa: E402

ENTER_CLILOC = 500112          # "You are now under the protection of the town guards."
MARKERS = os.path.join(uomap.INSTALL, "ClassicUO", "Data", "Client", "Banks_and_Healers.xml")
LAWLESS_TOWNS = ("Corpse Creek",)
ATTACKER_NEAR = 12             # an attacker this close steers the flight away from it
# plan_route: the heuristic's weight (3: the 16:44 death spot's route came 10 % longer than the unweighted
# search's, 689 vs 629 steps, at a thirtieth of its expansions)
FLEE_ROUTE_WEIGHT = 3.0
FLEE_ROUTE_ESCAPE_EXPAND = 20000    # at escape time: ~1 s cold (the 2026-10-11 measurements above)
FLEE_ROUTE_PREP_EXPAND = 150000     # before a trip, from the spot (the room; cached): Nusero's 125 046 fit
FLEE_ISLAND_MIN = 200          # an exhausted search smaller than this started in a pocket (a roof, a rock):
#                                undecided, not an island (witcher_188's centre: 8 states)
ROUTE_KEEP_EVERY = 4           # a cached route keeps every 4th tile (the Mover plans between them)
BANK_RADIUS = 2
ENTRY_WINDOW_S = 1.0           # backfill: the notice follows its step within this (no recall/teleport since)


def entries(events) -> list:
    """[(x, y)] tiles we stood on when an enter notice (500112) came, for each
    one in `events` (event dicts in order) that follows a one-tile `step`."""
    out = []
    last = None
    for e in events:
        ev = e.get("ev")
        if ev == "step":
            last = (tuple(e["from"][:2]), tuple(e["to"][:2]))
        elif ev == "cliloc" and e.get("cliloc") == ENTER_CLILOC and last is not None \
                and nav.chebyshev(last[0], last[1]) == 1:
            out.append(last[1])
    return out


def backfill(memory) -> int:
    """Record a guard point for every enter notice in the store's event log that
    came within ENTRY_WINDOW_S of a one-tile step: that step's destination. Facet
    0: step events carry no facet, and every session so far was on map 0.
    Returns the count."""
    n = 0
    session = None
    last = None                  # (from, to, t) of the session's last step
    rows = memory.con.execute("SELECT session, seq, t, ev, data FROM events "
                              "WHERE ev IN ('step', 'cliloc') ORDER BY session, seq").fetchall()
    for sess, _seq, t, ev, data in rows:
        if sess != session:
            session, last = sess, None
        e = json.loads(data)
        if ev == "step":
            last = (tuple(e["from"][:2]), tuple(e["to"][:2]), t)
        elif e.get("cliloc") == ENTER_CLILOC and last is not None \
                and nav.chebyshev(last[0], last[1]) == 1 and t is not None and last[2] is not None \
                and 0 <= t - last[2] <= ENTRY_WINDOW_S:
            memory.guard_point_record(0, last[1][0], last[1][1], t)
            n += 1
    return n


def bank_markers(path: str = MARKERS) -> list:
    """[((x, y), facet)] of the client's BANK markers outside lawless towns;
    [] if the file is missing or unreadable."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return []
    out = []
    for m in root.iter("Marker"):
        name = m.get("Name") or ""
        if m.get("Icon") != "BANK" or name.startswith(LAWLESS_TOWNS):
            continue
        try:
            out.append(((int(m.get("X")), int(m.get("Y"))), int(m.get("Facet") or 0)))
        except (TypeError, ValueError):
            continue
    return out


def flee_goals(points, banks, facet, me, attacker) -> list:
    """[((x, y), radius)] guarded places on `facet`, any distance from `me` (user 2026-10-11: "no matter how
    far away it is"; plan_route finds the nearest one on foot): learned points (radius 0) and bank markers
    (BANK_RADIUS), nearest first. With an attacker within ATTACKER_NEAR, places nearer to it than to us are
    dropped (unless that drops all of them)."""
    me = tuple(me[:2])
    cands = [((int(p[0]), int(p[1])), 0) for p in points]
    cands += [(tuple(c), BANK_RADIUS) for c, f in banks if (f or 0) == (facet or 0)]
    cands.sort(key=lambda g: nav.chebyshev(g[0], me))
    if attacker is not None and nav.chebyshev(me, tuple(attacker[:2])) <= ATTACKER_NEAR:
        a = tuple(attacker[:2])
        away = [(c, r) for c, r in cands if nav.chebyshev(c, a) >= nav.chebyshev(c, me)]
        if away:
            cands = away
    return cands


@dataclass
class Route:
    """plan_route's answer. `path`: [(x, y)] from the start to the goal (status 'reached', or 'unmapped': no map
    data, a straight line as walk memory plans over unknown tiles), else []. `goal`: ((x, y), radius)."""
    status: str                # "reached" | "island" | "undecided" | "unmapped"
    path: list = field(default_factory=list)
    goal: tuple | None = None
    expanded: int = 0
    elapsed_s: float = 0.0
    source: str = "escape"     # "escape" (planned from where we stand) or "spot" (before the trip, cached)

    @property
    def steps(self) -> int | None:
        """The route's length in tiles (a cached route's points lie up to ROUTE_KEEP_EVERY apart); None without one."""
        return sum(_cheb(a, b) for a, b in zip(self.path, self.path[1:])) if self.path else None

    def summary(self) -> dict:
        """The job events' view: status, steps, goal, expansions, seconds, source."""
        return {"status": self.status, "steps": self.steps,
                "goal": list(self.goal[0]) if self.goal else None, "expanded": self.expanded,
                "s": round(self.elapsed_s, 2), "source": self.source}

    def to_json(self) -> dict:
        """The store's cached form: every ROUTE_KEEP_EVERY-th tile and the last."""
        keep = [list(t) for t in self.path[::ROUTE_KEEP_EVERY]]
        if self.path and (len(self.path) - 1) % ROUTE_KEEP_EVERY:
            keep.append(list(self.path[-1]))
        return {"status": self.status, "path": keep, "goal": [list(self.goal[0]), self.goal[1]] if self.goal else None,
                "expanded": self.expanded, "s": round(self.elapsed_s, 2), "t": round(time.time(), 1)}

    @staticmethod
    def from_json(d: dict) -> "Route":
        goal = d.get("goal")
        return Route(d["status"], [tuple(t) for t in d.get("path") or ()],
                     ((int(goal[0][0]), int(goal[0][1])), int(goal[1])) if goal else None,
                     d.get("expanded") or 0, d.get("s") or 0.0, "spot")


def goals_key(goals) -> str:
    """A cache key part for a set of guarded places: their count and a checksum."""
    import zlib
    gs = sorted((int(c[0]), int(c[1]), int(r)) for c, r in goals)
    return f"{len(gs)}:{zlib.crc32(json.dumps(gs).encode()):08x}"


def plan_route(walk, start, goals, max_expand: int = FLEE_ROUTE_ESCAPE_EXPAND,
               weight: float = FLEE_ROUTE_WEIGHT) -> Route:
    """The way on foot from `start` (x, y, z) to the nearest of `goals` ([((x, y), radius)]) over
    `walk` (pathfind.Walk: its can_walk, locked doors included): A* with the Chebyshev heuristic to
    the goals times `weight` (not the shortest route, a near one fast). A search that runs out of
    tiles is an island (FLEE_ISLAND_MIN), one that runs out of `max_expand` undecided."""
    t0 = time.monotonic()
    goals = [((int(c[0]), int(c[1])), int(r)) for c, r in goals]
    if not goals:
        return Route("island", elapsed_s=0.0)

    def h(x, y):
        return min(max(0, max(abs(x - c[0]), abs(y - c[1])) - r) for c, r in goals)
    start = (int(start[0]), int(start[1]), int(start[2]))
    tie = itertools.count()
    g_best = {start: 0}
    parent = {}
    heap = [(weight * h(start[0], start[1]), next(tie), start)]
    closed = set()
    n = 0
    while heap:
        _, _, cur = heapq.heappop(heap)
        if cur in closed:
            continue
        if h(cur[0], cur[1]) == 0:
            path = [cur[:2]]
            while cur in parent:
                cur = parent[cur]
                path.append(cur[:2])
            path.reverse()
            end = path[-1]
            goal = min(goals, key=lambda g: max(abs(end[0] - g[0][0]), abs(end[1] - g[0][1])) - g[1])
            return Route("reached", path, goal, n, time.monotonic() - t0)
        closed.add(cur)
        n += 1
        if n > max_expand:
            return Route("undecided", expanded=n, elapsed_s=time.monotonic() - t0)
        x, y, z = cur
        ng = g_best[cur] + 1
        for d in range(8):
            nxt = walk.can_walk(x, y, z, d)
            if nxt is None or nxt in closed:
                continue
            if ng < g_best.get(nxt, 1 << 30):
                g_best[nxt] = ng
                parent[nxt] = cur
                heapq.heappush(heap, (ng + weight * h(nxt[0], nxt[1]), next(tie), nxt))
    return Route("island" if n >= FLEE_ISLAND_MIN else "undecided", expanded=n, elapsed_s=time.monotonic() - t0)


def straight_route(start, goals) -> Route:
    """No map data (the rental facet, --no-map): the straight line to the nearest goal, as walk memory's planner
    takes unknown tiles to be walkable (status 'unmapped')."""
    sx, sy = int(start[0]), int(start[1])
    goal = min(goals, key=lambda g: max(abs(sx - g[0][0]), abs(sy - g[0][1])) - g[1])
    (gx, gy), _ = goal
    n = max(abs(gx - sx), abs(gy - sy))
    path = [(sx + round((gx - sx) * i / n), sy + round((gy - sy) * i / n)) for i in range(n + 1)] if n else [(sx, sy)]
    return Route("unmapped", path, ((int(gx), int(gy)), int(goal[1])))


def _cheb(a, b) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def join(path, here) -> int:
    """The index of the route point to head for from `here`: the one with the least straight-line distance from
    us plus route left after it, in tiles (a cached route keeps every ROUTE_KEEP_EVERY-th tile), so a route we
    stand beside is joined level with us, not at its start; the earliest of equals (along a straight stretch every
    point ahead ties)."""
    rest = [0] * len(path)
    for i in range(len(path) - 2, -1, -1):
        rest[i] = rest[i + 1] + _cheb(path[i], path[i + 1])
    return min(range(len(path)), key=lambda k: (_cheb(path[k], here) + rest[k], k))


def ahead(path, k: int, steps: int) -> int:
    """The index of the first route point at least `steps` tiles of route past point `k`, else the last."""
    run = 0
    for i in range(k, len(path) - 1):
        run += _cheb(path[i], path[i + 1])
        if run >= steps:
            return i + 1
    return len(path) - 1


def stand_tile(walk, xy, reach: int = 6):
    """(x, y, z) of a tile near `xy` (nearest ring first) whose standing height lets a step leave it (a spot's
    centre may be a tree, water or a rock), else None."""
    cx, cy = int(xy[0]), int(xy[1])
    for r in range(reach + 1):
        ring = [(cx + dx, cy + dy) for dx in range(-r, r + 1) for dy in range(-r, r + 1)
                if max(abs(dx), abs(dy)) == r]
        for x, y in ring:
            for z in sorted({o[3] for o in walk.objects(x, y) if o[2] & 2}):    # surfaces' tops (POF_SURFACE)
                if any(walk.can_walk(x, y, z, d) is not None for d in range(8)):
                    return x, y, z
    return None


def main():
    import memory as memory_mod
    ap = argparse.ArgumentParser(description="town-guard zone points and the way there")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backfill", help="record guard points from the store's event log")
    b.add_argument("--db", default=memory_mod.DEFAULT_DB)
    r = sub.add_parser("route", help="plan_route from a tile to the nearest guarded place (store + bank markers)")
    r.add_argument("x", type=int)
    r.add_argument("y", type=int)
    r.add_argument("--facet", type=int, default=0)
    r.add_argument("--expand", type=int, default=FLEE_ROUTE_PREP_EXPAND)
    r.add_argument("--db", default=memory_mod.DEFAULT_DB)
    args = ap.parse_args()
    mem = memory_mod.Memory(args.db)
    if args.cmd == "route":
        import pathfind
        walk = pathfind.Walk(uomap.UoMap(args.facet))
        start = stand_tile(walk, (args.x, args.y))
        goals = flee_goals(mem.guard_points(args.facet), bank_markers(), args.facet, (args.x, args.y), None)
        route = plan_route(walk, start, goals, args.expand) if start else Route("undecided")
        print(json.dumps({"start": start, **route.summary()}))
        return
    n = backfill(mem)
    total = mem.con.execute("SELECT COUNT(*) FROM guard_points").fetchone()[0]
    print(json.dumps({"recorded": n, "points": total}))


if __name__ == "__main__":
    main()

"""Dungeon crawl for the hunt runner (`loop_hunt.py --crawl`; docs/HUNT_LOOP.md "Crawl").

Instead of standing on one fight spot, the runner patrols the dungeon floor: waypoints
covering the walkable area, visited in a staleness-by-distance order at the Mover's
human pace, fighting what it meets within --pull-range of where it stands.

- **The floor** (`Floor`): one dungeon level (floor), numbered from 1 as in the game: the
  tiles reachable from where we stand without crossing a known teleporter (the Mover's
  set: the store's `teleporters` table and the exit tile), from the map (pathfind.Walk,
  the client's own walk rules) or, without one, from walk memory. Route steps to the exit
  spot come from one BFS (`dist_exit`). The crawl stays on the floor it entered
  (`FLOOR`): the NPD is dungeon level 1 only (no teleporter to another part; docs), and
  floor transitions through a teleporter aren't built.
- **Zones** (internal depth bands, numbered from 1; not game levels): "deeper" on one
  floor is route distance from the exit: zone z = the tiles `(z - 1) x band` to
  `z x band - 1` steps out. A zone opens only once the one before is known (`learn_s`
  observed there, store history included) and the next one's predicted hits lost per
  minute (the zone before's x `depth_risk` until it has its own data) is at most
  `max_dmg`; a zone that turns out worse, or forces `max_leaves` survival leaves in a
  run, is closed again (come back out).
- **Waypoints** (`waypoints`): greedy coverage, roomiest tile first (clearance from the
  walls), each covering the floor tiles within `radius` route steps (never through a
  wall), none next to a teleporter. Each floor tile belongs to its nearest waypoint by
  route (`cells`): the waypoint's area.
- **The efficiency model** (`Model`): per creature type the fights (time, hits lost,
  gold, outcome) and per zone the minutes, hits lost and gold, persisted as `fight` job
  events and the episode rows' `crawl` block and loaded back from the store at the start
  (`load_prior`; kills before `fight` events existed are rebuilt from the event log).
  Estimates are shrunk toward the pool of all fights (`PRIOR_N` pseudo-fights) and toward
  the zone before (`PRIOR_MIN` pseudo-minutes). A type is avoided when its estimated hits
  lost per fight exceed `max_hits` x our max hits, its fights take longer than
  `max_fight_s`, or it made us flee in more than MAX_FLEE of its fights.
- **Avoided creatures** become Mover danger zones (AVOID_R, also from where they were last
  seen, for DANGER_S) and their areas are skipped; they are fought only when they attack.
- **Choosing the next waypoint** (`Crawl.choose`): staleness (time since our last visit,
  capped) x value (kills there this run) x crowd (players or pets seen there lately: they
  steal kills) x zone preference (its gold rate) / (1 + route steps / D0), with a little
  noise. Depleted areas (targets came, then none for `depleted_s`) rest for RESPAWN_S.
- **At a waypoint**: a glance of about `dwell` s when nothing showed up; while targets
  keep coming, stay until none for `depleted_s` (then the area is depleted).

Run: python harness/crawl.py floor [--spot X Y]   (the NPD floor graph from the map)
     python harness/crawl.py priors              (the per-creature and per-zone priors)
"""
import argparse
import bisect
import collections
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nav  # noqa: E402
import threats  # noqa: E402
from agent_link import Abort, log  # noqa: E402

# measured in the NPD (memory store 2026-10-03, docs/HUNT_LOOP.md "Crawl"): used when the
# store has no fights / no hunt episodes at all
DEFAULT_FIGHT = {"fight_s": 63.0, "hits": 34.6, "gold": 13.7}
DEFAULT_ZONE = {"gold_min": 7.8, "hits_min": 24.5}
FLOOR = 1               # the dungeon level crawled: the one entered (the NPD has only level 1)
PRIOR_N = 2.0           # pseudo-fights: a type's estimate starts at the pool of all fights
PRIOR_MIN = 5.0         # pseudo-minutes: a zone's rates start at the zone before's
FLEE_PRIOR = 0.05       # the pool's share of fights we fled from
MAX_FLEE = 0.3          # avoid a type we fled from in more than this share (estimate)
AVOID_R = 6             # tiles: danger zone around an avoided creature [INFERENCE: aggro range]
DANGER_S = 300.0        # an avoided creature's last sighting shapes routes this long
CROWD_S = 300.0         # players / pets seen at a waypoint lower its score this long
CROWD_FACTOR = 0.3
RESPAWN_S = 600.0       # a depleted area rests this long [INFERENCE: spawn timers unknown]
STALE_CAP_S = 600.0     # staleness beyond this counts the same
D0 = 20.0               # route steps that halve a waypoint's score
TELEPORT_GAP = 2        # no waypoint within this many tiles of a teleporter
MAX_ROUTE = 400         # floor BFS bound (route steps from the start)
MAX_TILES = 60000
ARTICLE = re.compile(r"^(a|an|the)\s+", re.I)


def type_key(name) -> str:
    """'a giant rat' -> 'giant rat' (a creature type: its name without the article)."""
    return ARTICLE.sub("", (name or "").strip().lower()) or "?"


def cheb(a, b) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


# --------------------------------------------------------------------------- the floor
class Floor:
    """Walkable tiles (2D) with their neighbours, and route steps to `exit_tile`."""

    def __init__(self, adj: dict, exit_tile):
        self.adj = adj
        self.exit = tuple(exit_tile)
        self.dist_exit = self.bfs(self.exit)

    def __contains__(self, t) -> bool:
        return tuple(t[:2]) in self.adj

    def __len__(self) -> int:
        return len(self.adj)

    def bfs(self, src, limit: int | None = None) -> dict:
        """{tile: route steps from src} over the floor (up to `limit` steps)."""
        src = tuple(src[:2])
        if src not in self.adj:
            return {}
        dist, q = {src: 0}, collections.deque([src])
        while q:
            c = q.popleft()
            dc = dist[c]
            if limit is not None and dc >= limit:
                continue
            for n in self.adj[c]:
                if n not in dist:
                    dist[n] = dc + 1
                    q.append(n)
        return dist

    def nearest(self, pos, reach: int = 3):
        """The floor tile nearest `pos` (pos itself when on the floor), or None."""
        pos = tuple(pos[:2])
        if pos in self.adj:
            return pos
        near = [(cheb(pos, (pos[0] + dx, pos[1] + dy)), (pos[0] + dx, pos[1] + dy))
                for dx in range(-reach, reach + 1) for dy in range(-reach, reach + 1)
                if (pos[0] + dx, pos[1] + dy) in self.adj]
        return min(near)[1] if near else None


def floor_from_map(walk, start, exit_tile, teleporters) -> Floor:
    """The floor from the map: BFS over (x, y, z) with walk.can_walk (pathfind.Walk)
    from `start` (x, y, z), never onto a tile in `teleporters` except `exit_tile`."""
    tele = set(teleporters) - {tuple(exit_tile)}
    s = (int(start[0]), int(start[1]), int(start[2]))
    seen, q, adj, depth = {s}, collections.deque([s]), {}, {s: 0}
    while q and len(adj) < MAX_TILES:
        cur = q.popleft()
        x, y, z = cur
        here = adj.setdefault((x, y), set())
        if (x, y) == tuple(exit_tile) and cur != s:
            continue                       # the exit tile ends a route: nothing beyond it
        for d in range(8):
            n = walk.can_walk(x, y, z, d)
            if n is None or n[:2] in tele:
                continue
            here.add(n[:2])
            adj.setdefault(n[:2], set()).add((x, y))
            if n not in seen and depth[cur] < MAX_ROUTE:
                seen.add(n)
                depth[n] = depth[cur] + 1
                q.append(n)
    return Floor({t: tuple(sorted(v)) for t, v in adj.items()}, exit_tile)


def floor_from_memory(mem: nav.WalkMemory, start, exit_tile, teleporters) -> Floor:
    """The floor from walk memory (no map): its confirmed moves, both ways, from the
    tiles reachable from `start`, never through a tile in `teleporters` but `exit_tile`."""
    tele = set(teleporters) - {tuple(exit_tile)}
    nb = collections.defaultdict(set)
    for a, b in mem.edges:
        if a in tele or b in tele:
            continue
        nb[a].add(b)
        nb[b].add(a)
    start = tuple(start[:2])
    adj, q = {}, collections.deque([start])
    seen = {start}
    while q:
        c = q.popleft()
        adj[c] = tuple(sorted(nb[c]))
        for n in nb[c]:
            if n not in seen:
                seen.add(n)
                q.append(n)
    return Floor(adj, exit_tile)


# --------------------------------------------------------------------------- waypoints
def clearance(floor: Floor) -> dict:
    """{tile: steps to the nearest tile with a missing neighbour (a wall or the edge)}."""
    edge = [t for t, ns in floor.adj.items() if len(ns) < 8]
    dist, q = {t: 0 for t in edge}, collections.deque(edge)
    while q:
        c = q.popleft()
        for n in floor.adj[c]:
            if n not in dist:
                dist[n] = dist[c] + 1
                q.append(n)
    return dist


def waypoints(floor: Floor, radius: int, teleporters=()) -> list:
    """Greedy coverage of the floor: the roomiest uncovered tile (clearance, then nearest
    the exit) becomes a waypoint covering every tile within `radius` route steps of it
    (route, not straight line: never through a wall). No waypoint within TELEPORT_GAP of a
    teleporter (a tile only those could cover stays uncovered). Ordered by route steps
    from the exit."""
    clear = clearance(floor)
    tele = list(teleporters)
    order = sorted(floor.adj, key=lambda t: (-clear.get(t, 0), floor.dist_exit.get(t, 1 << 30), t))
    covered, out = set(), []
    for t in order:
        if t in covered or t not in floor.dist_exit or any(cheb(t, p) <= TELEPORT_GAP for p in tele):
            continue
        out.append(t)
        covered.update(floor.bfs(t, radius))
    return sorted(out, key=lambda t: (floor.dist_exit[t], t))


def cells(floor: Floor, wps: list) -> dict:
    """{tile: index of its nearest waypoint by route} (multi-source BFS)."""
    own = {w: i for i, w in enumerate(wps)}
    q = collections.deque(wps)
    while q:
        c = q.popleft()
        for n in floor.adj[c]:
            if n not in own:
                own[n] = own[c]
                q.append(n)
    return own


# --------------------------------------------------------------------------- the model
class Model:
    """Per creature type: fights [(fight_s or None, hits lost, outcome)], gold per kill.
    Per zone: seconds, hits lost, gold, kills, survival leaves (store + this run)."""

    def __init__(self, fights=(), zones=None, depth_risk: float = 1.5, base: dict | None = None):
        self.fights = collections.defaultdict(list)     # type -> [(fight_s, hits, outcome)]
        self.gold = collections.defaultdict(list)       # type -> [gold per kill; 0: no corpse / taken]
        self._pool_cache = None                         # _pool() until the next fight or gold
        for f in fights:
            self.add_fight(f["name"], f.get("fight_s"), f.get("hits") or 0, f.get("outcome") or "kill")
            if f.get("gold") is not None:
                self.add_gold(f["name"], f["gold"])
        self.zones = collections.defaultdict(lambda: {"s": 0.0, "hits": 0, "gold": 0, "kills": 0, "leaves": 0})
        for k, v in (zones or {}).items():
            row = self.zones[int(k)]
            for f in row:
                row[f] += v.get(f) or 0
        self.depth_risk = depth_risk
        self.base = dict(base or DEFAULT_ZONE)

    # ---- creature types
    def add_fight(self, name, fight_s, hits, outcome):
        self.fights[type_key(name)].append((fight_s, hits, outcome))
        self._pool_cache = None

    def add_gold(self, name, gold):
        self.gold[type_key(name)].append(gold)
        self._pool_cache = None

    def _pool(self):
        """The prior means over every fight (DEFAULT_FIGHT when there are none)."""
        if self._pool_cache is None:
            all_f = [f for fs in self.fights.values() for f in fs]
            timed = [f[0] for f in all_f if f[0] is not None and f[2] == "kill"]
            golds = [g for gs in self.gold.values() for g in gs]
            self._pool_cache = {
                "fight_s": sum(timed) / len(timed) if timed else DEFAULT_FIGHT["fight_s"],
                "hits": sum(f[1] for f in all_f) / len(all_f) if all_f else DEFAULT_FIGHT["hits"],
                "gold": sum(golds) / len(golds) if golds else DEFAULT_FIGHT["gold"],
                "flee": FLEE_PRIOR}
        return self._pool_cache

    def estimate(self, name) -> dict:
        """Shrunk estimates for a type: fights `n`, `fight_s` (kills only), `hits` lost per
        fight, `gold` per kill (0 for a kill a pet or player looted), `flee` share, `gold_min` while fighting it."""
        fs, gs, pool = self.fights.get(type_key(name), []), self.gold.get(type_key(name), []), self._pool()

        def shrink(vals, prior):
            return (PRIOR_N * prior + sum(vals)) / (PRIOR_N + len(vals))
        timed = [f[0] for f in fs if f[0] is not None and f[2] == "kill"]
        est = {"n": len(fs), "kills": sum(1 for f in fs if f[2] == "kill"),
               "fight_s": shrink(timed, pool["fight_s"]),
               "hits": shrink([f[1] for f in fs], pool["hits"]),
               "gold": shrink(gs, pool["gold"]),
               "flee": shrink([1.0 if f[2] == "fled" else 0.0 for f in fs], pool["flee"])}
        est["gold_min"] = est["gold"] * 60.0 / max(1.0, est["fight_s"])
        return est

    def avoid(self, name, hits_max, max_hits: float, max_fight_s: float) -> str | None:
        """Why a type is too strong or too slow for us now, or None."""
        e = self.estimate(name)
        if not e["n"]:
            return None                       # never fought: the pool says nothing about it
        if e["flee"] > MAX_FLEE:
            return f"fled from it in {e['flee']:.0%} of fights (est.)"
        if hits_max and e["hits"] > max_hits * hits_max:
            return f"{e['hits']:.0f} hits lost per fight (est.) > {max_hits:.0%} of {hits_max}"
        if e["fight_s"] > max_fight_s:
            return f"{e['fight_s']:.0f} s per kill (est.) > {max_fight_s:.0f} s"
        return None

    # ---- zones
    def add_zone(self, k, **inc):
        row = self.zones[k]
        for f, v in inc.items():
            row[f] += v

    def rates(self, z) -> dict:
        """Shrunk gold and hits lost per minute in zone z: zone 1 toward `base` (the
        store's hunt episodes), zone z toward zone z-1's estimate (hits x depth_risk)."""
        if z <= 1:
            prior = dict(self.base)
        else:
            up = self.rates(z - 1)
            prior = {"gold_min": up["gold_min"], "hits_min": up["hits_min"] * self.depth_risk}
        row = self.zones[z]
        m = row["s"] / 60.0
        return {"minutes": m,
                "gold_min": (PRIOR_MIN * prior["gold_min"] + row["gold"]) / (PRIOR_MIN + m),
                "hits_min": (PRIOR_MIN * prior["hits_min"] + row["hits"]) / (PRIOR_MIN + m)}


# --------------------------------------------------------------------------- the store
def _hunt_windows(con):
    return [(a, b, json.loads(d)) for a, b, d in con.execute(
        "SELECT t_start, t_end, data FROM episodes WHERE loop='hunt' AND t_end IS NOT NULL ORDER BY t_start")]


def fights_from_events(con, windows, skip=frozenset()) -> list:
    """Fights rebuilt from the event log for `kill` job events without a `fight` event
    (before the crawl recorded them): the first attack/cast intent at the serial starts
    it, the kill ends it; hits lost = the '-N' overhead numbers on us in between (us: the
    attackers of the 0x2F swings, all our own on Outlands; threats.py); gold from the loot
    event (its `mob`, else the corpse named by the 0xDEAD event)."""
    if not windows:
        return []
    lo, hi = windows[0][0] - 60, windows[-1][1] + 60
    kills = [(t, json.loads(d)) for t, d in con.execute(
        "SELECT t, data FROM job_events WHERE job='hunt' AND kind='kill' ORDER BY t")]
    kills = [(t, d) for t, d in kills if d.get("serial") not in skip]
    if not kills:
        return []
    first = {}
    for t, d in con.execute("SELECT t, data FROM events WHERE ev='agent_intent' AND t BETWEEN ? AND ? "
                            "AND data LIKE '%target_serial%' ORDER BY t", (lo, hi)):
        it = json.loads(d).get("intent") or {}
        if it.get("loop") == "hunt" and it.get("kind") in ("attack", "cast") and it.get("target_serial"):
            first.setdefault(int(it["target_serial"], 16), t)
    selfs = {json.loads(d).get("attacker") for (d,) in con.execute(
        "SELECT data FROM events WHERE ev='swing' AND t BETWEEN ? AND ?", (lo, hi))}
    hurt = []
    for t, d in con.execute("SELECT t, data FROM events WHERE ev='speech_heard' AND t BETWEEN ? AND ? "
                            "AND data LIKE '%\"text\": \"-%'", (lo, hi)):
        e = json.loads(d)
        if e.get("serial") in selfs and (e.get("text") or "")[1:].isdigit():
            hurt.append((t, int(e["text"][1:])))
    hurt_t = [t for t, _ in hurt]
    corpse_mob = {}
    for (d,) in con.execute("SELECT data FROM events WHERE ev='mobile_death' AND t BETWEEN ? AND ?", (lo, hi)):
        e = json.loads(d)
        if e.get("corpse") is not None:
            corpse_mob[e["corpse"]] = e.get("serial")
    gold = {}
    for (d,) in con.execute("SELECT data FROM job_events WHERE job='hunt' AND kind='loot'"):
        e = json.loads(d)
        mob = int(e["mob"], 16) if e.get("mob") else corpse_mob.get(int(e.get("corpse") or "0", 16))
        if mob is not None:
            gold[mob] = None if e.get("refused") else (e.get("gold") or 0)   # refused: not our kill's gold
    out = []
    for t, d in kills:
        s = int(d["serial"], 16)
        t0 = first.get(s)
        hits = sum(v for _, v in hurt[bisect.bisect_left(hurt_t, t0):bisect.bisect_right(hurt_t, t)]) \
            if t0 is not None else None
        out.append({"name": d.get("name"), "fight_s": (t - t0) if t0 is not None else None,
                    "hits": hits or 0, "outcome": "kill", "gold": gold.get(s, 0)})
    return out


def load_prior(con, spot=None, depth_risk: float = 1.5) -> Model:
    """The model the crawl starts from: every `fight` job event (gold joined from the
    loot events by mob serial), the older kills rebuilt from the event log, the zone
    stats of earlier crawl visits from `spot` (rows from before the rename keep them as
    0-based `levels`: level k is zone k + 1), and the hunt episodes' gold and hits lost per
    minute as zone 1's base. `con`: the store's sqlite connection."""
    gold = {}
    for (d,) in con.execute("SELECT data FROM job_events WHERE job='hunt' AND kind='loot'"):
        e = json.loads(d)
        if e.get("mob"):
            gold[e["mob"]] = None if e.get("refused") else (e.get("gold") or 0)   # refused: no gold sample
    fights, seen = [], set()
    for (d,) in con.execute("SELECT data FROM job_events WHERE job='hunt' AND kind='fight' ORDER BY id"):
        e = json.loads(d)
        seen.add(e.get("serial"))
        fights.append({"name": e.get("name"), "fight_s": e.get("fight_s") if e.get("outcome") == "kill" else None,
                       "hits": e.get("hits_lost") or 0, "outcome": e.get("outcome"),
                       "gold": gold.get(e.get("serial"), 0) if e.get("outcome") == "kill" else None})
    windows = _hunt_windows(con)
    fights += fights_from_events(con, windows, frozenset(seen))
    zones, minutes, g, h = collections.defaultdict(dict), 0.0, 0, 0
    for a, b, row in windows:
        minutes += (b - a) / 60.0
        g += row.get("gold") or 0
        h += row.get("hits_lost") or 0
        c = row.get("crawl") or {}
        if spot is not None and c.get("spot") is not None and tuple(c["spot"]) != tuple(spot):
            continue
        bands = [(int(k), v) for k, v in (c.get("zones") or {}).items()] \
            + [(int(k) + 1, v) for k, v in (c.get("levels") or {}).items()]
        for z, v in bands:
            for f, x in v.items():
                zones[z][f] = zones[z].get(f, 0) + x
    base = {"gold_min": g / minutes, "hits_min": h / minutes} if minutes >= 1.0 else None
    return Model(fights, zones, depth_risk, base)


# --------------------------------------------------------------------------- the crawl
class Crawl:
    """Patrol state for one runner. `hunt` is the HuntLoop (its args, mover, human,
    pick_target, attackers, leave_reason); the floor is built on the first `setup`."""

    def __init__(self, hunt, model: Model):
        self.h = hunt
        self.a = hunt.args
        self.model = model
        self.floor = None
        self.wps, self.cell_of, self.zone = [], {}, []    # waypoints, tile -> waypoint, waypoint -> zone
        self.unlocked = 1            # the deepest zone open (zones count from 1)
        self.closed = set()          # zones closed this run (came back out)
        self.wp = None               # the waypoint index we are heading to / holding at
        self.arrived_t = None        # when we got to it
        self.leg_t = 0.0             # when we set out for it (targets met on the way count for it)
        self.glance_s = 0.0
        self.visited = {}            # waypoint -> monotonic time of our last stay
        self.depleted = {}           # waypoint -> monotonic time it may be chosen again
        self.target_t = {}           # waypoint -> monotonic time a target was last in reach there
        self.kills_at = collections.Counter()
        self.crowd_t = {}            # waypoint -> monotonic time a player or pet was seen there
        self.danger = {}             # serial -> (tile, monotonic time, type) of avoided creatures
        self.avoided = {}            # type -> why (logged once)
        self.tick_t = None
        self.visit_zones = collections.defaultdict(lambda: {"s": 0.0, "hits": 0, "gold": 0, "kills": 0, "leaves": 0})
        self.counts = collections.Counter()

    # ---- setup
    def setup(self, st):
        """Build the floor (map, else walk memory) from where we stand, the waypoints,
        their areas and zones. Called inside, once per run."""
        if self.floor is not None:
            return
        m = self.h.mover
        facet = st["world"]["self"].get("map")
        tele = m.teleporter_tiles(facet)
        pos = self.h.pos(st)
        walk = m.walk_map(st)
        t0 = time.monotonic()
        if walk is not None:
            self.floor = floor_from_map(walk, pos[:3], self.h.spot, tele)
        else:
            self.floor = floor_from_memory(m.mem_for(facet), self.h.spot, self.h.spot, tele)
        if self.h.spot not in self.floor.dist_exit or len(self.floor) < 2:
            raise ValueError(f"no floor around the exit spot {self.h.spot} ({len(self.floor)} tiles)")
        self.wps = waypoints(self.floor, self.a.pull_range, tele)
        self.cell_of = cells(self.floor, self.wps)
        self.zone = [self.floor.dist_exit[w] // self.a.crawl_band + 1 for w in self.wps]
        top = max(self.zone)
        if self.a.crawl_zones:
            top = min(top, self.a.crawl_zones)
        self.top = top
        log(f"crawl: dungeon level {FLOOR} floor: {len(self.floor)} tiles "
            f"({'map' if walk is not None else 'walk memory'}, {time.monotonic() - t0:.1f} s), "
            f"{len(self.wps)} waypoints, zones 1-{top} ({self.a.crawl_band} route steps each; farthest "
            f"{max(self.floor.dist_exit.values())} steps from the exit)")
        self.update_zones()

    def zone_at(self, pos):
        """The zone of the area we stand in; off the floor (the arrival tile is a
        teleporter, so not a floor tile) the nearest floor tile's."""
        t = self.floor.nearest(pos) if self.floor is not None else None
        i = self.cell_of.get(t)
        return self.zone[i] if i is not None else None

    def route_steps(self, pos) -> int | None:
        if self.floor is None:
            return None
        t = self.floor.nearest(pos)
        return self.floor.dist_exit.get(t) if t is not None else None

    # ---- zones
    def update_zones(self):
        """Open the next zone when this one is known and the next one's predicted risk
        is acceptable; close the deepest when it proved worse (come back out)."""
        a, m = self.a, self.model
        k = self.unlocked
        if k > 1:
            r = m.rates(k)
            leaves = self.counts[f"leaves{k}"]
            if (r["minutes"] * 60 >= a.crawl_learn_s and r["hits_min"] > a.crawl_max_dmg) \
                    or leaves >= a.crawl_max_leaves:
                self.closed.add(k)
                self.unlocked = k - 1
                why = (f"{leaves} survival leave(s)" if leaves >= a.crawl_max_leaves
                       else f"{r['hits_min']:.0f} hits lost/min > {a.crawl_max_dmg:g}")
                log(f"crawl: coming back out to zone {k - 1}: zone {k} {why}")
                self.h.memory.job_event("hunt", "crawl_zone", {"zone": k, "floor": FLOOR, "open": False,
                                                               "why": why, "rates": r})
                return
        if k >= self.top or (k + 1) in self.closed:
            return
        here, nxt = m.rates(k), m.rates(k + 1)
        if here["minutes"] * 60 >= a.crawl_learn_s and nxt["hits_min"] <= a.crawl_max_dmg:
            self.unlocked = k + 1
            log(f"crawl: going deeper to zone {k + 1}: zone {k} known ({here['minutes']:.1f} min, "
                f"{here['gold_min']:.1f} gold/min, {here['hits_min']:.1f} hits/min); zone {k + 1} "
                f"predicted {nxt['hits_min']:.1f} hits/min <= {a.crawl_max_dmg:g}")
            self.h.memory.job_event("hunt", "crawl_zone", {"zone": k + 1, "floor": FLOOR, "open": True,
                                                           "rates": nxt, "before": here})

    # ---- per state read
    def account(self, st, lost: int):
        """Time and hits lost go to the zone we stand in; creatures in view update the
        danger zones, the crowd marks and the areas' last target time."""
        if self.floor is None:
            return
        now = time.monotonic()
        pos = self.h.pos(st)
        k = self.zone_at(pos)
        if k is not None:
            dt = now - self.tick_t if self.tick_t is not None else 0.0
            if 0 < dt < 30:
                self.model.add_zone(k, s=dt)
                self.visit_zones[k]["s"] += dt
            if lost:
                self.model.add_zone(k, hits=lost)
                self.visit_zones[k]["hits"] += lost
        self.tick_t = now
        self.observe(st, pos, now)

    def observe(self, st, pos, now):
        world = st["world"]
        labels = world.get("labels") or {}
        me = st["movement"]["self_serial"]
        hits_max = world["self"].get("hits_max")
        danger_zones = {}
        for key, mob in world["mobiles"].items():
            s = int(key, 16) if isinstance(key, str) else key
            if s == me or mob.get("x") is None or s in self.h.dead:
                continue
            tile = (mob["x"], mob["y"])
            kind, player, _ = threats.identify(mob, labels.get(key))
            i = self.cell_of.get(self.floor.nearest(tile) or tile)
            if player or mob.get("pet"):
                if i is not None:
                    self.crowd_t[i] = now
                continue
            if kind != "monster" or not self.h.wanted(world, key, mob):
                continue
            name = labels.get(key) or mob.get("name")
            why = self.model.avoid(name, hits_max, self.a.crawl_max_hits, self.a.crawl_max_fight_s)
            if why:
                if type_key(name) not in self.avoided:
                    self.avoided[type_key(name)] = why
                    log(f"crawl: avoiding {type_key(name)}: {why}")
                self.danger[s] = (tile, now, type_key(name))
            elif i is not None and cheb(pos, tile) <= self.a.pull_range:
                self.target_t[i] = now
        for s, (tile, t, name) in list(self.danger.items()):
            if now - t > DANGER_S:
                del self.danger[s]
                continue
            danger_zones[f"crawl:{s:08X}"] = (tile, AVOID_R)
        mv = self.h.mover
        stale = [k for k in mv.danger if k.startswith("crawl:") and k not in danger_zones]
        new = [k for k, z in danger_zones.items() if mv.danger.get(k) is None or cheb(mv.danger[k][0], z[0]) >= 3]
        for k in stale:
            del mv.danger[k]
        mv.danger.update(danger_zones)
        if new and self.h.patrolling:
            mv.replan_requested = True

    def avoid(self, st, key, mob) -> bool:
        """Don't pull this one (its type is too strong or too slow for us)."""
        name = (st["world"].get("labels") or {}).get(key) or mob.get("name")
        return self.model.avoid(name, st["world"]["self"].get("hits_max"),
                                self.a.crawl_max_hits, self.a.crawl_max_fight_s) is not None

    # ---- outcomes
    def on_kill(self, pos):
        k, i = self.zone_at(pos), self.cell_of.get(tuple(pos[:2]))
        if k is not None:
            self.model.add_zone(k, kills=1)
            self.visit_zones[k]["kills"] += 1
        if i is not None:
            self.kills_at[i] += 1
            self.target_t[i] = time.monotonic()

    def on_gold(self, pos, gold):
        k = self.zone_at(pos)
        if k is not None and gold:
            self.model.add_zone(k, gold=gold)
            self.visit_zones[k]["gold"] += gold

    def on_leave(self, pos, survival: bool):
        k = self.zone_at(pos) if pos is not None else None
        if k is None:
            k = self.unlocked
        if survival:
            self.counts[f"leaves{k}"] += 1
            self.visit_zones[k]["leaves"] += 1
        self.wp, self.arrived_t = None, None

    # ---- patrol
    def interrupt(self, st) -> str | None:
        """Why the patrol walk should stop now (the hunt loop takes over), or None."""
        h = self.h
        if h.attackers(st):
            return "under attack"
        if h.leave_reason(st):
            return "a leave rule"
        if h.pending_speech:
            return "speech nearby"
        if h.pick_target(st) is not None:
            return "a target in reach"
        return None

    def dangerous(self, i) -> bool:
        w = self.wps[i]
        return any(cheb(w, tile) <= AVOID_R + 2 or self.cell_of.get(tile) == i
                   for tile, _, _ in self.danger.values())

    def choose(self, pos):
        """The next waypoint index (or None): open zones, not depleted, not near an
        avoided creature, reachable; staleness x value x crowd x zone / distance."""
        now = time.monotonic()
        start = self.floor.nearest(pos)
        dist = self.floor.bfs(start) if start is not None else {}
        rates = {k: self.model.rates(k)["gold_min"] for k in range(1, self.unlocked + 1)}
        best_rate = max(rates.values()) or 1.0
        here = self.cell_of.get(start)
        best, best_score = None, 0.0
        for i, w in enumerate(self.wps):
            if self.zone[i] > self.unlocked or i == here or w not in dist:
                continue
            if self.depleted.get(i, 0) > now or self.dangerous(i):
                continue
            stale = min(now - self.visited[i], STALE_CAP_S) if i in self.visited else STALE_CAP_S
            score = (max(stale, 1.0) * (1.0 + 0.5 * min(3, self.kills_at[i]))
                     * (CROWD_FACTOR if now - self.crowd_t.get(i, -1e9) < CROWD_S else 1.0)
                     * max(0.25, rates[self.zone[i]] / best_rate)
                     / (1.0 + dist[w] / D0) * self.h.human.rng.uniform(0.85, 1.15))
            if score > best_score:
                best, best_score = i, score
        return best

    def idle(self, st):
        """Nothing to fight or loot: hold at the waypoint (a glance, or while targets keep
        coming), else walk to the next one; the walk stops when something comes up."""
        h, now = self.h, time.monotonic()
        pos = h.pos(st)
        if self.wp is not None and cheb(pos, self.wps[self.wp]) <= 1:
            i = self.wp
            if self.arrived_t is None:
                self.arrived_t = now
                self.glance_s = self.a.crawl_dwell * h.human.rng.uniform(0.6, 1.4)
                self.counts["waypoints"] += 1
            productive = self.target_t.get(i, -1e9) >= self.leg_t
            until = (self.target_t[i] + self.a.crawl_depleted_s) if productive else self.arrived_t + self.glance_s
            self.visited[i] = now
            if now < until:
                what = self.a.target_name.strip() or "monsters"
                h.doing("wait", f"Hunting for {what} around {self.wps[i][0]},{self.wps[i][1]} "
                                f"(zone {self.zone[i]}, {h.totals['kills']} killed)", self.wps[i])
                time.sleep(0.4)
                return
            if productive:
                self.depleted[i] = now + RESPAWN_S
                self.counts["depleted"] += 1
                log(f"crawl: area {self.wps[i]} depleted ({self.kills_at[i]} kill(s), no target for "
                      f"{self.a.crawl_depleted_s:.0f} s); moving on")
            self.wp, self.arrived_t = None, None
        if self.wp is None:
            self.update_zones()
            self.wp = self.choose(pos)
            if self.wp is None:
                self.depleted.clear()                    # nothing left: rest periods end early
                self.wp = self.choose(pos)
            if self.wp is None:
                h.doing("wait", "Nothing to patrol to; waiting", tuple(pos[:2]))
                time.sleep(0.4)
                return
            self.leg_t = now
        w = self.wps[self.wp]
        h.doing("patrol", f"Patrolling to {w[0]},{w[1]} (zone {self.zone[self.wp]})", w)
        h.patrolling = True
        try:
            why = h.mover.walk_to(lambda: w, 1, f"patrol to {w}", stop=h.patrol_stop,
                                  max_moves=max(250, 2 * max(self.floor.dist_exit.values())))
            if why:
                log(f"patrol to {w}: stopped ({why})")
        except Abort as e:
            if "no route" not in str(e) and "cut by mobiles" not in str(e):
                raise
            log(f"patrol to {w}: {e}; skipping it for {RESPAWN_S:.0f} s")
            self.depleted[self.wp] = now + RESPAWN_S
            self.wp = None
        finally:
            h.patrolling = False

    def summary(self) -> dict:
        """The episode row's `crawl` block: the dungeon level (`floor`), this visit's zone
        stats (load_prior sums them across runs), the deepest open zone, the types avoided,
        waypoints held and depleted."""
        out = {"spot": list(self.h.spot), "floor": FLOOR, "band": self.a.crawl_band, "unlocked": self.unlocked,
               "closed": sorted(self.closed), "avoided": dict(self.avoided),
               "waypoints": self.counts["waypoints"], "depleted": self.counts["depleted"],
               "zones": {str(k): {f: round(v, 1) if f == "s" else v for f, v in row.items()}
                          for k, row in self.visit_zones.items()}}
        self.visit_zones.clear()
        self.counts["waypoints"] = self.counts["depleted"] = 0
        return out


# --------------------------------------------------------------------------- CLI
def _ro(path):
    """The store read-only (never created, never written)."""
    import sqlite3
    if not os.path.exists(path):
        raise SystemExit(f"no memory store at {path}")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _cli_floor(args):
    import pathfind
    import uomap
    con = _ro(args.memory) if args.memory else None
    tele = {(x, y) for x, y in con.execute("SELECT x, y FROM teleporters WHERE facet = 0")} if con else set()
    tele.add(nav.step(tuple(args.spot), 4))
    walk = pathfind.Walk(uomap.UoMap(0))
    start = (args.start[0], args.start[1], args.z)
    floor = floor_from_map(walk, start, tuple(args.spot), tele)
    wps = waypoints(floor, args.radius, tele)
    zs = [floor.dist_exit[w] // args.band + 1 for w in wps]
    print(f"dungeon level {FLOOR} floor from {start}: {len(floor)} tiles; farthest {max(floor.dist_exit.values())} "
          f"route steps from the exit spot {tuple(args.spot)}; teleporters avoided: {sorted(tele)}")
    xs = [t[0] for t in floor.adj]
    ys = [t[1] for t in floor.adj]
    print(f"bbox x {min(xs)}-{max(xs)}, y {min(ys)}-{max(ys)}; {len(wps)} waypoints (radius {args.radius})")
    per = collections.Counter(floor.dist_exit[t] // args.band + 1 for t in floor.adj)
    for z in sorted(per):
        ws = [w for w, wz in zip(wps, zs) if wz == z]
        print(f"zone {z}: route steps {(z - 1) * args.band}-{z * args.band - 1}, {per[z]} tiles, "
              f"{len(ws)} waypoints: {' '.join(f'{x},{y}' for x, y in ws)}")


def _cli_priors(args):
    m = load_prior(_ro(args.memory), None)
    print(f"zone 1 base: {m.base}")
    for name in sorted(m.fights, key=lambda n: -len(m.fights[n])):
        e = m.estimate(name)
        print(f"{name}: {e['n']} fights ({e['kills']} kills), {e['fight_s']:.0f} s/kill, {e['hits']:.1f} hits/fight, "
              f"{e['gold']:.1f} gold/kill, flee {e['flee']:.0%}; avoid at 94 hits: "
              f"{m.avoid(name, 94, 0.5, 180) or 'no'}")
    for k in sorted(m.zones):
        print(f"zone {k}: {m.zones[k]} -> {m.rates(k)}")


def main(argv=None):
    from memory import DEFAULT_DB
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("floor", help="the floor graph from the map (read-only)")
    f.add_argument("--spot", type=int, nargs=2, default=[5535, 529])
    f.add_argument("--start", type=int, nargs=2, default=[5536, 528])
    f.add_argument("--z", type=int, default=0)
    f.add_argument("--radius", type=int, default=8)
    f.add_argument("--band", type=int, default=40)
    f.add_argument("--memory", default=DEFAULT_DB, help="store for the teleporters (read-only; '' = none)")
    p = sub.add_parser("priors", help="per-creature and per-zone priors from the store (read-only)")
    p.add_argument("--memory", default=DEFAULT_DB)
    args = ap.parse_args(argv)
    {"floor": _cli_floor, "priors": _cli_priors}[args.cmd](args)


if __name__ == "__main__":
    main()

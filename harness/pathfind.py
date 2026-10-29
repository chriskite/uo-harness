"""3D walkability and route planning over the Outlands map (harness/uomap.py).

A port of the client's Pathfinder rules (upstream ClassicUO
Game/Pathfinder.cs CreateItemList / CalculateMinMaxZ / CalculateNewZ /
CanWalk; Outlands' versions are in decompiled/map_formats.c, same logic,
with z as int instead of sbyte). Given a tile and a z, it answers whether a
step in a direction is walkable and at which z the player lands. That is
the same decision the client makes before it sends a walk request.

Differences from the client, on purpose:
- Doors (dynamic items with the Door flag) don't block a step onto their own
  tile: the walker opens them ahead, like the client's auto-open
  (agent_link.Mover). They do block a diagonal past them (can_walk).
- Mobiles are handled by the caller as blocked tiles (they move).
- The A* is a plain heap-based search over (x, y, z) with per-plan
  cost noise (humanize.Human.cost_scale); diagonal and straight steps cost
  the same (both are one step on the server).

Read-only: everything comes from the install files through uomap.
"""
import heapq
import itertools

import uomap

TICK_EVERY = 1500        # plan(): expansions between tick() calls (~0.2 s of search) [INFERENCE: timing]

POF_IMPASSABLE_OR_SURFACE = 1
POF_SURFACE = 2
POF_BRIDGE = 4
DEFAULT_BLOCK_HEIGHT = 16
DX = (0, 1, 1, 1, 0, -1, -1, -1)
DY = (-1, -1, 0, 1, 1, 1, 0, -1)
MAP_FACETS = (0, 1, 4, 5)     # facets with geometry in mapN.uoo (2 and 3 are blank; docs/MAP.md)
CACHE_TILES = 100_000        # Walk: tiles a cache holds before it starts over (a long live session's memory)
_MISS = object()


class Walkers:
    """One Walk per facet with map geometry (lazily built), refreshed with the
    current ground items. Shared by the proxy (z per confirmed step), the
    Mover (planning) and ctl (local map)."""

    def __init__(self):
        self._walks = {}
        self._shut = {}              # (facet, rect, doors) -> {(x, y, z): enclosed?} (_enclosed)

    def get(self, facet, ground=(), inside=None) -> "Walk | None":
        """The Walk for `facet` (None: no geometry there), with `ground` =
        iterable of (x, y, graphic, z, multi) ground items as dynamic objects
        (ground_items builds it). A multi (a house) stands for its pieces from
        multi.mul, placed around its tile like the client does (Item.LoadMulti);
        the client's pathfinder never uses the multi's own graphic. `inside`: the
        walker's (x, y, z); then the doors of every house it isn't shut in are
        `locked` (another player's house doesn't open to us: live 2026-10-05 an
        escape route through such a door stopped at it and Outland Dan died). A
        house's doors are separate door items (multi.mul pieces have none) on or
        next to its footprint (house_rect). Shut in = no walk from `inside` leaves the
        house's pieces without stepping onto a door tile (_enclosed): standing on a
        house's porch, yard or outside stairs is outside it (live 2026-10-11 16:43:35,
        multi 0xE9 at 4235,802: its rect held the escape route's tile 4225,808 and its
        outside stairs, so its doors counted as ours and Dan lost 4 s at a locked door)."""
        facet = 0 if facet is None else facet
        if facet not in MAP_FACETS:
            return None
        w = self._walks.get(facet)
        if w is None:
            w = Walk(uomap.UoMap(facet))
            self._walks[facet] = w
        index, houses, singles = {}, [], []
        for x, y, g, z, multi in ground:
            if x is None or g is None:
                continue
            z = z or 0
            if multi:
                comps = uomap.multi_components(g, w.m.root)
                for dx, dy, dz, pg in comps:
                    index.setdefault((x + dx, y + dy), []).append((pg, z + dz))
                if comps:
                    houses.append(house_rect(x, y, comps))
            else:
                index.setdefault((x, y), []).append((g, z))
                singles.append((x, y, g))
        w.set_ground(index)
        w.locked = frozenset()
        if inside is None or not houses:
            return w
        td = uomap.tiledata()
        doors = [(x, y) for x, y, g in singles if (it := td.item(g)) is not None and it.flags & uomap.DOOR]
        start = (int(inside[0]), int(inside[1]), int(inside[2]))
        locked = set()
        for r in houses:
            mine = frozenset(t for t in doors if _in_rect(t, r))
            if mine and not self._enclosed(facet, w, r, mine, start):
                locked |= mine
        w.locked = frozenset(locked)
        return w

    def _enclosed(self, facet, walk: "Walk", rect, doors: frozenset, start) -> bool:
        """Whether `start` (x, y, z) is shut in by the house of `rect`: no walk from it reaches a
        tile off the house's pieces (rect less its one-tile rim) without stepping onto a door
        tile. Every node the search reaches gets the same answer (they connect), cached per
        house and door layout, so a walk through or past a house searches it once."""
        if not _in_rect(start, (rect[0] + 1, rect[1] + 1, rect[2] - 1, rect[3] - 1)):
            return False
        key = (facet, rect, doors)
        known = self._shut.get(key)
        if known is None:
            if len(self._shut) > 64:
                self._shut.clear()
            known = self._shut[key] = {}
        if start in known:
            return known[start]
        inner = (rect[0] + 1, rect[1] + 1, rect[2] - 1, rect[3] - 1)
        seen, todo, shut = {start}, [start], True
        while todo:
            x, y, z = todo.pop()
            if not _in_rect((x, y), inner) or known.get((x, y, z)) is False:
                shut = False
                break
            for d in range(8):
                n = walk.can_walk(x, y, z, d)
                if n is not None and n not in seen and n[:2] not in doors:
                    seen.add(n)
                    todo.append(n)
        for n in seen:
            known[n] = shut
        return shut

    def put(self, facet, walk: "Walk"):
        """Use `walk` for `facet` (tests with synthetic geometry)."""
        self._walks[facet] = walk


def house_rect(x: int, y: int, comps) -> tuple:
    """(x0, y0, x1, y1): a house's footprint from its pieces, one tile wider each way (its
    doors stand on the outline's edge or just outside the pieces' bounding box)."""
    xs, ys = [dx for dx, _, _, _ in comps], [dy for _, dy, _, _ in comps]
    return x + min(xs) - 1, y + min(ys) - 1, x + max(xs) + 1, y + max(ys) + 1


def _in_rect(t, r) -> bool:
    return r[0] <= t[0] <= r[2] and r[1] <= t[1] <= r[3]


def ground_items(items) -> list:
    """Walkers.get's `ground` from the world model's item dicts (state JSON
    `world.items` values): items on the ground, multis (data_type 2) flagged."""
    return [(it.get("x"), it.get("y"), it.get("graphic"), it.get("z"), it.get("data_type") == 2)
            for it in items if it.get("container") is None]


def _skip_land(graphic: int) -> bool:
    """Land graphics the client leaves out of the path item list."""
    return not ((graphic < 0x01AE and graphic != 2) or (graphic > 0x01B5 and graphic != 0x01DB))


class Walk:
    """Walkability on one facet. `dynamic(x, y)` returns ground items on the tile
    as (graphic, z) pairs (doors, placed items); the static map comes from uomap."""

    def __init__(self, umap: "uomap.UoMap", dynamic=None):
        self.m = umap
        self.td = umap.tiledata
        self.dynamic = dynamic or (lambda x, y: ())
        self._locked = frozenset()
        self._ground = None          # set_ground's last index
        self._objs = {}
        self._steps = {}             # (x, y) -> {(z, d, door_corners): can_walk's answer} for steps from that tile
        self._land = {}

    @property
    def locked(self) -> frozenset:
        """Door tiles of houses we're outside of (Walkers.get `inside`): no step onto them."""
        return self._locked

    @locked.setter
    def locked(self, tiles):
        tiles = frozenset(tiles)
        if tiles != self._locked:
            self._drop_steps(tiles ^ self._locked)
            self._locked = tiles

    def clear(self):
        """Drop every cached tile list and step (`dynamic` replaced by hand)."""
        self._objs.clear()
        self._steps.clear()
        self._ground = None

    def set_ground(self, index: dict):
        """The ground items: {(x, y): [(graphic, z), ...]} as `dynamic`. Only the tiles whose items changed since
        the last call lose their cached lists and the steps that read them (Walkers.get calls this on every state
        read; dropping everything each time cost the simulator a third of its route planning)."""
        old = self._ground
        if old is not None and old == index:
            return                   # `dynamic` stays the same object: los.Sight keeps its item cache too
        self.dynamic = lambda x, y: index.get((x, y), ())
        if old is None:
            self.clear()
        else:
            changed = [t for t in old.keys() | index.keys() if old.get(t) != index.get(t)]
            for t in changed:
                self._objs.pop(t, None)
            self._drop_steps(changed)
        self._ground = index

    def _drop_steps(self, tiles):
        """Forget the steps that read `tiles`: a step from (x, y) reads that tile, the one it lands on and a
        diagonal's two corners, all within 1 of (x, y)."""
        steps = self._steps
        if not steps:
            return
        for tx, ty in tiles:
            for x in (tx - 1, tx, tx + 1):
                for y in (ty - 1, ty, ty + 1):
                    steps.pop((x, y), None)

    # ---------------------------------------------------------------- tiles
    def _land_info(self, x, y):
        v = self._land.get((x, y))
        if v is None:
            v = self.m.land_stretch(x, y)
            self._land[(x, y)] = v
        return v

    def _corner_z(self, x, y, d, ls):
        """Land.CalculateCurrentAverageZ(direction) from the stretched corners."""
        def dz(k):
            if k == 1:
                return self._land_info(x + 1, y).z if self._land_info(x + 1, y) else ls.z
            if k == 2:
                c = self._land_info(x + 1, y + 1)
                return c.z if c else ls.z
            if k == 3:
                c = self._land_info(x, y + 1)
                return c.z if c else ls.z
            return ls.z
        r = dz(((d >> 1) + 1) & 3)
        if d & 1:
            return r
        return (r + dz(d >> 1)) >> 1

    def objects(self, x, y):
        """Sorted path objects on (x, y): tuples (z, height, flags, avg_z, kind)."""
        key = (x, y)
        objs = self._objs.get(key)
        if objs is not None:
            return objs
        objs = []
        cell = self.m.land(x, y)
        if cell is not None:
            tile_id, _ = cell
            if not _skip_land(tile_id):
                lt = self.td.land(tile_id)
                ls = self._land_info(x, y)
                flags = POF_IMPASSABLE_OR_SURFACE
                if not lt.flags & uomap.IMPASSABLE:
                    flags |= POF_SURFACE | POF_BRIDGE
                stretched = ls.textured and ls.min_z != ls.max_z
                objs.append((ls.min_z, ls.avg_z - ls.min_z, flags, ls.avg_z,
                             ("land", ls) if stretched else ("flat", None)))
        statics = self.m.statics(x, y) or ()
        for graphic, z, ground in ([(s.graphic, s.z, False) for s in statics]
                                   + [(g, gz, True) for g, gz in self.dynamic(x, y)]):
            it = self.td.item(graphic)
            if it is None:
                continue
            f = it.flags
            if f & uomap.DOOR and ground:
                # the walker opens a door item when denied; a static with the Door flag is art that never opens (the
                # secret-door stone walls 0xE8-0xF7 walling Tidal Tomb's pit at (6650-6662, 2048-2061): skipped,
                # they let the planner and the simulator step off the ledge at z 19 into it, simulator 2026-10-26)
                continue
            flags = 0
            if f & (uomap.IMPASSABLE | uomap.SURFACE):
                flags = POF_IMPASSABLE_OR_SURFACE
            if not f & uomap.IMPASSABLE:
                if f & uomap.SURFACE:
                    flags |= POF_SURFACE
                if f & uomap.BRIDGE:
                    flags |= POF_BRIDGE
            if not flags:
                continue
            height = it.height
            avg = z + (height // 2 if f & uomap.BRIDGE else height)
            objs.append((z, height, flags, avg, ("item", graphic)))
        objs.sort(key=lambda o: (o[0], o[1]))
        if len(self._objs) >= CACHE_TILES:
            self._objs.clear()
        self._objs[key] = objs
        return objs

    # ---------------------------------------------------------------- rules
    def _min_max_z(self, nx, ny, cur_z, d):
        """CalculateMinMaxZ: from the tile we step off (new tile minus the move)."""
        min_z, max_z = -128, cur_z
        d &= 7
        back = d ^ 4
        x, y = nx + DX[back], ny + DY[back]
        for z, height, flags, avg, kind in self.objects(x, y):
            if avg <= cur_z and kind[0] == "land":
                a = self._corner_z(x, y, d, kind[1])
                min_z = max(min_z, a)
                max_z = max(max_z, a)
            else:
                if flags & POF_IMPASSABLE_OR_SURFACE and avg <= cur_z and min_z < avg:
                    min_z = avg
                if flags & POF_BRIDGE and cur_z == avg:
                    max_z = max(max_z, z + height)
                    min_z = min(min_z, z)
        return min_z, max_z + 2

    def new_z(self, x, y, z, d):
        """CalculateNewZ: the z the player stands at on (x, y), or None.

        Upstream's sbyte z marks "no surface" with resultZ -128; Outlands' z is an int and a surface at
        exactly -128 holds: the client sent and the server confirmed both diagonals (6886,1980)<->(6887,1981)
        in Tidal Tomb, whose corner (6887,1980) is underwater sand at land z -128 and nothing else
        (captures 20261024_225113 and 20261025_210102). So "none" is None here, not a z value."""
        min_z, max_z = self._min_max_z(x, y, z, d)
        objs = self.objects(x, y)
        if not objs:
            return None
        objs = objs + [(128, 128, POF_IMPASSABLE_OR_SURFACE, 128, ("cap", None))]
        result, best_delta, current = None, 1000000, -128
        z = max(z, min_z)
        for i, (oz, oh, of, oavg, _k) in enumerate(objs):
            if not of & POF_IMPASSABLE_OR_SURFACE:
                continue
            if oz - min_z >= DEFAULT_BLOCK_HEIGHT:
                for j in range(i - 1, -1, -1):
                    tz, th, tf, tavg, _tk = objs[j]
                    if not tf & (POF_SURFACE | POF_BRIDGE):
                        continue
                    if tavg >= current and oz - tavg >= DEFAULT_BLOCK_HEIGHT and (
                            (tavg <= max_z and tf & POF_SURFACE) or (tf & POF_BRIDGE and tz <= max_z)):
                        delta = abs(z - tavg)
                        if delta < best_delta:
                            best_delta, result = delta, tavg
            min_z = max(min_z, oavg)
            current = max(current, oavg)
        return result

    def door_on(self, x, y, z) -> bool:
        """A door (static or ground item with the tiledata Door flag) stands on
        (x, y) within a body height of z."""
        for graphic, gz in [(s.graphic, s.z) for s in (self.m.statics(x, y) or ())] + list(self.dynamic(x, y)):
            it = self.td.item(graphic)
            if it is not None and it.flags & uomap.DOOR and abs((gz or 0) - z) < DEFAULT_BLOCK_HEIGHT:
                return True
        return False

    def can_walk(self, x, y, z, d, door_corners: bool = True):
        """CanWalk without the client's direction substitution: the landing
        (nx, ny, nz) of a step from (x, y, z) in direction d, or None.

        A door on the target tile doesn't block (the walker opens it ahead, like
        the client's auto-open), unless it is `locked` (another player's house). A
        door on either corner tile of a diagonal does
        (door_corners): door items are impassable for the client, open or closed,
        so it never sends that diagonal (live 20260930_182751: a diagonal past a
        closed double-door leaf was denied). The proxy's z tracking of
        server-confirmed steps passes door_corners=False. Answers are cached per
        start tile (set_ground and `locked` drop the ones a change reads)."""
        at = self._steps.get((x, y))
        if at is None:
            if len(self._steps) >= CACHE_TILES:
                self._steps.clear()
            at = self._steps[(x, y)] = {}
        key = (z, d, door_corners)
        r = at.get(key, _MISS)
        if r is _MISS:
            r = at[key] = self._can_walk(x, y, z, d, door_corners)
        return r

    def _can_walk(self, x, y, z, d, door_corners):
        nx, ny = x + DX[d], y + DY[d]
        if (nx, ny) in self.locked:
            return None
        nz = self.new_z(nx, ny, z, d)
        if nz is None:
            return None
        if d & 1:
            for side in ((d + 1) % 8, (d + 7) % 8):
                sx, sy = x + DX[side], y + DY[side]
                if self.new_z(sx, sy, z, side) is None or (door_corners and self.door_on(sx, sy, z)):
                    return None
        return nx, ny, nz


def plan(walk: Walk, start, goal_fn, blocked_moves=(), occupied=(), cost_scale=None,
         max_expand: int = 30000, max_steps: int | None = None, tick=None, tick_every: int = TICK_EVERY):
    """A* over (x, y, z): the cheapest route from start (x, y, z) to the first
    tile satisfying goal_fn((x, y)) (and goal_fn.z_ok(z) when the goal has one:
    a tile below or above the target, e.g. a cave under a tree, isn't the
    target's), as [(x, y, z), ...] including start, or None. blocked_moves:
    {(x, y, d)} learned server denies. occupied: {(x, y)}. max_steps: only routes of
    at most that many steps (a node that can't reach the goal within it, by the
    heuristic, isn't expanded): a refused detour fails fast. tick(): called every
    tick_every expansions, so a caller can look for threats during a long search (it
    may raise; live 2026-10-05 nine 7 s plans at a landing hid a norse bear rider's
    approach and Dan died standing there)."""
    start = (int(start[0]), int(start[1]), int(start[2]))
    h = getattr(goal_fn, "heuristic", None) or (lambda t: 0)
    z_ok = getattr(goal_fn, "z_ok", None)
    blocked = set(blocked_moves)
    occ = set(occupied)
    tie = itertools.count()
    g_best = {start: 0.0}
    steps = {start: 0}
    parent = {}
    heap = [(h(start[:2]), next(tie), start)]
    closed = set()
    expanded = 0
    while heap:
        _, _, cur = heapq.heappop(heap)
        if cur in closed:
            continue
        if goal_fn(cur[:2]) and (z_ok is None or z_ok(cur[2])):
            path = [cur]
            while cur in parent:
                cur = parent[cur]
                path.append(cur)
            return path[::-1]
        closed.add(cur)
        expanded += 1
        if expanded > max_expand:
            return None
        if tick is not None and expanded % tick_every == 0:
            tick()
        x, y, z = cur
        n = steps[cur] + 1
        for d in range(8):
            if (x, y, d) in blocked:
                continue
            nxt = walk.can_walk(x, y, z, d)
            if nxt is None or nxt in closed or nxt[:2] in occ:
                continue
            if max_steps is not None and n + h(nxt[:2]) > max_steps:
                continue
            step = cost_scale((x, y), nxt[:2]) if cost_scale else 1.0
            ng = g_best[cur] + step
            if ng < g_best.get(nxt, float("inf")):
                g_best[nxt] = ng
                steps[nxt] = n
                parent[nxt] = cur
                heapq.heappush(heap, (ng + h(nxt[:2]), next(tie), nxt))
    return None

"""3D walkability and route planning over the Outlands map (harness/uomap.py).

A port of the client's Pathfinder rules (upstream ClassicUO
Game/Pathfinder.cs CreateItemList / CalculateMinMaxZ / CalculateNewZ /
CanWalk; Outlands' versions are in decompiled/map_formats.c, same logic,
with z as int instead of sbyte). Given a tile and a z, it answers whether a
step in a direction is walkable and at which z the player lands. That is
the same decision the client makes before it sends a walk request.

Differences from the client, on purpose:
- Doors (dynamic items with the Door flag) don't block planning. The walker
  opens them when a step is actually denied (agent_link.Mover).
- Mobiles are handled by the caller as blocked tiles (they move).
- The A* is a plain heap-based search over (x, y, z) with per-plan
  cost noise (humanize.Human.cost_scale); diagonal and straight steps cost
  the same (both are one step on the server).

Read-only: everything comes from the install files through uomap.
"""
import heapq
import itertools

import uomap

POF_IMPASSABLE_OR_SURFACE = 1
POF_SURFACE = 2
POF_BRIDGE = 4
DEFAULT_BLOCK_HEIGHT = 16
DX = (0, 1, 1, 1, 0, -1, -1, -1)
DY = (-1, -1, 0, 1, 1, 1, 0, -1)


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
        self._objs = {}
        self._land = {}

    def clear(self):
        """Drop cached tile lists (dynamic items changed)."""
        self._objs.clear()

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
        for graphic, z in [(s.graphic, s.z) for s in statics] + list(self.dynamic(x, y)):
            it = self.td.item(graphic)
            if it is None:
                continue
            f = it.flags
            if f & uomap.DOOR:
                continue                      # the walker opens doors when denied
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
        """CalculateNewZ: the z the player stands at on (x, y), or None."""
        min_z, max_z = self._min_max_z(x, y, z, d)
        objs = self.objects(x, y)
        if not objs:
            return None
        objs = objs + [(128, 128, POF_IMPASSABLE_OR_SURFACE, 128, ("cap", None))]
        result, best_delta, current = -128, 1000000, -128
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
        return None if result == -128 else result

    def can_walk(self, x, y, z, d):
        """CanWalk without the client's direction substitution: the landing
        (nx, ny, nz) of a step from (x, y, z) in direction d, or None."""
        nx, ny = x + DX[d], y + DY[d]
        nz = self.new_z(nx, ny, z, d)
        if nz is None:
            return None
        if d & 1:
            for side in ((d + 1) % 8, (d + 7) % 8):
                if self.new_z(x + DX[side], y + DY[side], z, side) is None:
                    return None
        return nx, ny, nz


def plan(walk: Walk, start, goal_fn, blocked_moves=(), occupied=(), cost_scale=None,
         max_expand: int = 30000):
    """A* over (x, y, z): the cheapest route from start (x, y, z) to the first
    tile satisfying goal_fn((x, y)), as [(x, y, z), ...] including start, or
    None. blocked_moves: {(x, y, d)} learned server denies. occupied: {(x, y)}."""
    start = (int(start[0]), int(start[1]), int(start[2]))
    h = getattr(goal_fn, "heuristic", None) or (lambda t: 0)
    blocked = set(blocked_moves)
    occ = set(occupied)
    tie = itertools.count()
    g_best = {start: 0.0}
    parent = {}
    heap = [(h(start[:2]), next(tie), start)]
    closed = set()
    expanded = 0
    while heap:
        _, _, cur = heapq.heappop(heap)
        if cur in closed:
            continue
        if goal_fn(cur[:2]):
            path = [cur]
            while cur in parent:
                cur = parent[cur]
                path.append(cur)
            return path[::-1]
        closed.add(cur)
        expanded += 1
        if expanded > max_expand:
            return None
        x, y, z = cur
        for d in range(8):
            if (x, y, d) in blocked:
                continue
            nxt = walk.can_walk(x, y, z, d)
            if nxt is None or nxt in closed or nxt[:2] in occ:
                continue
            step = cost_scale((x, y), nxt[:2]) if cost_scale else 1.0
            ng = g_best[cur] + step
            if ng < g_best.get(nxt, float("inf")):
                g_best[nxt] = ng
                parent[nxt] = cur
                heapq.heappush(heap, (ng + h(nxt[:2]), next(tie), nxt))
    return None

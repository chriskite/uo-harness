"""Line of sight over the Outlands map, after RunUO/ServUO `Map.LineOfSight(Point3D, Point3D)`
(ServUO Server/Map.cs, region "Line Of Sight"; RunUO 2 Server/Map.cs is the same algorithm).

The server walks a 3-D line from the lower point to the higher one (sorted by x, then y, then z)
in unit steps, rounding each step to a tile point (C# `Math.Round`, banker's rounding, which is
Python's `round`), and refuses sight at the first point where
- the land's corner z range [lowest corner, highest corner] holds the point's z (ignored land
  graphics excepted: `pathfind._skip_land`, i.e. LandTile.Ignored), or
- the land is 0x244 (Map.InvalidLandTiles) with no statics and no items on the tile, or
- a static (multi pieces included: GetStaticTiles(x, y, true)) flagged Window or NoShoot spans the
  point's z: `t.z <= p.z <= t.z + CalcHeight` (CalcHeight = height / 2 for Bridge tiles), or
- a ground item flagged Window or NoShoot spans it, with one unit of slack on top
  (`loc.z <= p.z + 1 && loc.z + CalcHeight >= p.z`).
On the destination's own tile a land/static/item span that overlaps [dest.z, dest.z + 1] doesn't
block (the target stands in or on it). Wall/Impassable flags alone don't block sight: only
Window|NoShoot (walls carry NoShoot in tiledata).

Mobiles: `Map.LineOfSight(Mobile, Mobile)` and `GetPoint(mobile)` add 14 to both mobiles' z (the
eye; both ServUO and RunUO 2 have `p.Z += 14; //eye ? 15 : 10;`), so `can_see` adds EYE = 14
itself. Points farther apart than MAX_DISTANCE on x or y are not in sight.

[INFERENCE] Outlands runs its own server; that it keeps these rules is checked against capture
session_20261012_210013 (see harness/test_los.py: bard/All Kill target answers accepted vs answered
"Target cannot be seen." 500237).
"""
import math

import pathfind
import uomap

EYE = 14                 # Map.GetPoint(Mobile): Location.Z + 14
MAX_DISTANCE = 25        # ServUO Map.MaxLOSDistance = Core.GlobalMaxUpdateRange (24) + 1 [INFERENCE for Outlands]
INVALID_LAND = frozenset((0x244,))   # Map.InvalidLandTiles
_SIGHT_FLAGS = uomap.WINDOW | uomap.NOSHOOT
_EMPTY = ()


class Sight:
    """Line of sight on one facet, from a pathfind.Walk's map, tiledata and ground items
    (`walk.dynamic`, refreshed by Walkers.get: the item cache follows it)."""

    def __init__(self, walk: "pathfind.Walk"):
        self.walk = walk
        self.m = walk.m
        self.td = walk.td
        self._tiles = {}         # (x, y) -> (land_low, land_top, land_ignored, invalid_land, static spans)
        self._items = {}         # (x, y) -> ground item spans (cleared when walk.dynamic changes)
        self._items_src = None

    # ---------------------------------------------------------------- tiles
    def _span(self, graphic):
        """(height, blocks) for an item graphic: CalcHeight and whether Window|NoShoot is set
        (a graphic past artdata.uoo's end counts as neither)."""
        if not 0 <= graphic < self.td.item_count:
            return 0, False
        it = self.td.item(graphic)
        h = it.height // 2 if it.flags & uomap.BRIDGE else it.height
        return h, bool(it.flags & _SIGHT_FLAGS)

    def _tile(self, x, y):
        t = self._tiles.get((x, y))
        if t is not None:
            return t
        m = self.m
        cell = m.land(x, y)
        land_id = cell[0] if cell else 0
        zs = (m._tile_z(x, y), m._tile_z(x, y + 1), m._tile_z(x + 1, y), m._tile_z(x + 1, y + 1))
        statics = m.statics(x, y) or _EMPTY
        spans = []
        for s in statics:
            h, blocks = self._span(s.graphic)
            if blocks:
                spans.append((s.z, s.z + h))
        t = (min(zs), max(zs), pathfind._skip_land(land_id),
             land_id in INVALID_LAND and not statics, tuple(spans))
        self._tiles[(x, y)] = t
        return t

    def _item_spans(self, x, y):
        """(spans of Window|NoShoot ground items, any item at all) on (x, y)."""
        v = self._items.get((x, y))
        if v is None:
            items = list(self.walk.dynamic(x, y))
            spans = []
            for g, z in items:
                h, blocks = self._span(g)
                if blocks:
                    z = z or 0
                    spans.append((z, z + h))
            v = self._items[(x, y)] = (tuple(spans), bool(items))
        return v

    # ---------------------------------------------------------------- rules
    def point_blocked(self, org, dest):
        """Map.LineOfSight(org, dest) on raw points: the first path point (x, y, z) that blocks
        sight, or None when dest is in sight. A pair out of MAX_DISTANCE returns dest."""
        ox, oy, oz = org
        dx, dy, dz = dest
        if abs(ox - dx) > MAX_DISTANCE or abs(oy - dy) > MAX_DISTANCE:
            return (dx, dy, dz)
        ex, ey, ez = dx, dy, dz          # `end`: the original destination
        if ox > dx or (ox == dx and (oy > dy or (oy == dy and oz > dz))):
            ox, oy, oz, dx, dy, dz = dx, dy, dz, ox, oy, oz
        if ox == dx and oy == dy and oz == dz:
            return None
        xd, yd, zd = dx - ox, dy - oy, dz - oz
        flat = math.sqrt(xd * xd + yd * yd)
        sq3d = math.sqrt(flat * flat + zd * zd) if zd else flat
        rise, run, zslp = yd / sq3d, xd / sq3d, zd / sq3d
        # NumberBetween(v, b1, b2, 0.5): lo - 0.5 < v < hi + 0.5
        xlo, xhi = min(ox, dx) - 0.5, max(ox, dx) + 0.5
        ylo, yhi = min(oy, dy) - 0.5, max(oy, dy) + 0.5
        zlo, zhi = min(oz, dz) - 0.5, max(oz, dz) + 0.5
        x, y, z = float(ox), float(oy), float(oz)
        path = []
        last = None
        while xlo < x < xhi and ylo < y < yhi and zlo < z < zhi:
            p = (round(x), round(y), round(z))
            if p != last:
                path.append(p)
                last = p
            x += run
            y += rise
            z += zslp
        if not path:
            return None
        if last != (dx, dy, dz):
            path.append((dx, dy, dz))
        end_top = ez + 1
        tile = self._tiles.get
        dyn = self.walk.dynamic
        if dyn is not self._items_src:
            self._items.clear()
            self._items_src = dyn
        for p in path:
            px, py, pz = p
            t = tile((px, py)) or self._tile(px, py)
            at_end = px == ex and py == ey
            low, top, ignored, invalid, spans = t
            if low <= pz <= top and not ignored and (not at_end or low > end_top or top < ez):
                return p
            ispans, any_item = self._item_spans(px, py)
            if invalid and not any_item:
                return p
            for sz, stop in spans:
                if sz <= pz <= stop and (not at_end or sz > end_top or stop < ez):
                    return p
            for iz, itop in ispans:
                if iz <= pz + 1 and itop >= pz and (not at_end or iz > end_top or itop < ez):
                    return p
        return None

    def blocked_at(self, a, b):
        """The first point blocking sight from mobile at a (x, y, z) to mobile at b, eyes at
        z + EYE (as can_see), or None when b is in sight."""
        return self.point_blocked((a[0], a[1], a[2] + EYE), (b[0], b[1], b[2] + EYE))

    def can_see(self, a, b) -> bool:
        """Whether a mobile standing at a (x, y, z) sees one at b: Map.LineOfSight(Mobile, Mobile),
        both z raised by EYE (14)."""
        return self.point_blocked((a[0], a[1], a[2] + EYE), (b[0], b[1], b[2] + EYE)) is None


def surface_z(walk: "pathfind.Walk", x: int, y: int, near_z: int = 0) -> int:
    """The walkable surface z at (x, y) nearest `near_z` (the world model's own mobile z is often
    stale: no per-step z, docs/NOTES.md "Line of sight"); `near_z` where the tile has none."""
    best = None
    for z, h, flags, avg, kind in walk.objects(x, y):
        if flags & pathfind.POF_SURFACE and (best is None or abs(avg - near_z) < abs(best - near_z)):
            best = avg
    return near_z if best is None else best


def sight_for(walkers: "pathfind.Walkers", facet: int) -> "Sight | None":
    """A Sight on `facet` from `walkers`' Walk (None where Walkers.get has no geometry). An
    existing Walk is taken as is, so its current ground items (the last Walkers.get) stay; the
    Sight is cached on the Walk, so repeated calls share tile caches."""
    facet = 0 if facet is None else facet
    if facet not in pathfind.MAP_FACETS:
        return None
    walk = walkers._walks.get(facet) or walkers.get(facet)
    if walk is None:
        return None
    s = getattr(walk, "_sight", None)
    if s is None:
        s = walk._sight = Sight(walk)
    return s

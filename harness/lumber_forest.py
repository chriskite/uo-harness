"""Forest spots (docs/LUMBER_LOOP.md §6 "Forest spots", user decision 2026-10-07).

A discovered spot was a 29x29 window of the densest trees. It held 500-1 000 logs against a
hazard-optimal trip of ~5 000, so 118 of 123 active spots were grove-bound, and 30 of 35
stored trips ended because the window ran dry or one monster covered all its trees. A forest
spot is the window grown over the whole forest around it, the way a paint bucket fills:

- forest ground: a tile we can walk onto (pathfind.Walk.can_walk) within LINK tiles of a tree
  static (uomap.find_trees); the fill spreads over it in 4 directions from every walkable forest
  tile of the spot's window, so trees up to 2·LINK+1 tiles apart belong together and a river,
  cliff or clearing ends the forest;
- a tree within REACH tiles of a filled tile is the forest's;
- the fill keeps out of towns (TOWN_RADIUS from a township marker), learned guard points
  (GUARD_KEEP) and spots set aside by hand (disabled, not merged), except the window itself;
- every spot grows at once (one breadth-first fill), so two spots in one woodland split it;
  a spot stops growing at CAP trees (user decision 2026-10-07: about twice a trip's worth);
- territories that meet merge while together they hold at most CAP trees (smallest first):
  the merged spot keeps the id with the most trips, the others become `disabled` with
  `merged_into` (lumber_opt.merged_alias: their trips count for the forest);
- the area is stored as the CELL x CELL squares (lumber_opt.CELL) the fill covered (each to
  the territory with most tiles in it, none that touch a no-go zone outside the windows),
  with center/radius the square around them; the original window is kept in `forest.window`,
  so a rebuild starts from the same seeds.

Prototype numbers (2026-10-07, without the walk check): LINK 3 grew witcher_89 from 42 to
923 trees and witcher_265 from 51 to 1 220; LINK 4 joined whole woodlands (witcher_56: 280
-> 2 056), so 3 it is.
"""
import re
import time
from collections import deque

import lumber_opt

LINK = 3            # forest ground: walkable tiles within this many tiles (Chebyshev) of a tree
REACH = 2           # a tree belongs to the forest that fills a tile this close to it
CAP = 800           # trees per forest spot at most (~2 x a 5 000-log trip at ~12 logs per tree static)
GUARD_KEEP = 18     # tiles around a learned guard point the fill stays out of (as discover: window + 4)
CHUNK = 64          # trees and no-go zones are indexed per CHUNK x CHUNK tiles
DIRS4 = ((0, -1, 0), (1, 0, 2), (0, 1, 4), (-1, 0, 6))   # (dx, dy, UO direction): N, E, S, W
NAME_TAIL = re.compile(r"\s*\([^()]*\btrees\b[^()]*\)\s*$")


class Trees:
    """Tree statics from trees_fn(x0, y0, x1, y1) -> [(x, y, z, graphic)] (uomap.find_trees),
    loaded per CHUNK: `at` (a tree on the tile) and `near` (within `link` tiles of one)."""

    def __init__(self, trees_fn, link: int = LINK):
        self.fn, self.link = trees_fn, link
        self._at, self._near = {}, {}

    def _load(self, k):
        x0, y0, L = k[0] * CHUNK, k[1] * CHUNK, self.link
        at, near = set(), set()
        for x, y, _z, _g in self.fn(x0 - L, y0 - L, x0 + CHUNK - 1 + L, y0 + CHUNK - 1 + L):
            if x0 <= x < x0 + CHUNK and y0 <= y < y0 + CHUNK:
                at.add((x, y))
            for nx in range(max(x0, x - L), min(x0 + CHUNK, x + L + 1)):
                for ny in range(max(y0, y - L), min(y0 + CHUNK, y + L + 1)):
                    near.add((nx, ny))
        self._at[k], self._near[k] = at, near

    def at(self, x: int, y: int) -> bool:
        k = (x // CHUNK, y // CHUNK)
        if k not in self._at:
            self._load(k)
        return (x, y) in self._at[k]

    def near(self, x: int, y: int) -> bool:
        k = (x // CHUNK, y // CHUNK)
        if k not in self._near:
            self._load(k)
        return (x, y) in self._near[k]


class Zones:
    """No-go rectangles (x0, y0, x1, y1), indexed per CHUNK; calling it tests a tile."""

    def __init__(self, rects=()):
        self.rects, self._by = [], {}
        for r in rects:
            self.add(*r)

    def add(self, x0, y0, x1, y1):
        r = (int(x0), int(y0), int(x1), int(y1))
        self.rects.append(r)
        for kx in range(r[0] // CHUNK, r[2] // CHUNK + 1):
            for ky in range(r[1] // CHUNK, r[3] // CHUNK + 1):
                self._by.setdefault((kx, ky), []).append(r)

    def __call__(self, x: int, y: int) -> bool:
        return any(r[0] <= x <= r[2] and r[1] <= y <= r[3] for r in self._by.get((x // CHUNK, y // CHUNK), ()))

    def touches(self, x0, y0, x1, y1) -> bool:
        """Some zone overlaps the rectangle."""
        return any(r[0] <= x1 and x0 <= r[2] and r[1] <= y1 and y0 <= r[3]
                   for kx in range(x0 // CHUNK, x1 // CHUNK + 1) for ky in range(y0 // CHUNK, y1 // CHUNK + 1)
                   for r in self._by.get((kx, ky), ()))


def walk_fns(walk):
    """(stand_z, step) over a pathfind.Walk: stand_z(x, y) is the z we stand at on the tile
    when a step from a side lands there (None: nobody walks onto it); step(x, y, z, d) the
    z of the step's landing, or None."""
    def surface(x, y):
        objs = walk.objects(x, y)
        return next((o[0] + o[1] for o in objs if o[4][0] in ("flat", "item")), 0) if objs else 0

    def stand_z(x, y):
        for dx, dy, d in DIRS4:
            px, py = x - dx, y - dy
            r = walk.can_walk(px, py, surface(px, py), d)
            if r is not None and (r[0], r[1]) == (x, y):
                return r[2]
        return None

    def step(x, y, z, d):
        r = walk.can_walk(x, y, z, d)
        return None if r is None else r[2]
    return stand_z, step


def grow(sources, trees: Trees, stand_z, step, stop=None, *, reach: int = REACH, cap: int = CAP) -> list:
    """Grow every source's window into its forest (module doc). sources: [(id, window area
    {center, radius})], the most-tried first (a tile both windows hold goes to the first).
    stop: a Zones (or tile predicate) the fill doesn't enter outside the windows.
    -> [{"members": [ids], "cells": [(cx, cy)], "claimed": trees claimed, "trees": tree
    statics in the cells}], members in source order; a source whose window has no walkable
    forest ground comes back alone with no cells."""
    n = len(sources)
    owner, claimed = {}, set()
    count, done = [0] * n, [False] * n
    touch = set()
    q = deque()

    def take(x, y, z, lab):
        owner[(x, y)] = lab
        q.append((x, y, z, lab))
        for tx in range(x - reach, x + reach + 1):
            for ty in range(y - reach, y + reach + 1):
                if (tx, ty) not in claimed and trees.at(tx, ty):
                    claimed.add((tx, ty))
                    count[lab] += 1
        if count[lab] >= cap:
            done[lab] = True

    for lab, (_sid, w) in enumerate(sources):
        (cx, cy), r = w["center"], int(w["radius"])
        for x in range(cx - r, cx + r + 1):
            for y in range(cy - r, cy + r + 1):
                if (x, y) not in owner and trees.near(x, y):
                    z = stand_z(x, y)
                    if z is not None:
                        take(x, y, z, lab)
    while q:
        x, y, z, lab = q.popleft()
        if done[lab]:
            continue
        for dx, dy, d in DIRS4:
            nx, ny = x + dx, y + dy
            o = owner.get((nx, ny))
            if o is not None:
                if o != lab and step(x, y, z, d) is not None:
                    touch.add((min(o, lab), max(o, lab)))
                continue
            if not trees.near(nx, ny) or (stop is not None and stop(nx, ny)):
                continue
            nz = step(x, y, z, d)
            if nz is not None:
                take(nx, ny, nz, lab)
                if done[lab]:
                    break

    parent, size = list(range(n)), count[:]

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    while True:                                    # meeting territories merge, smallest pair first
        best = None
        for a, b in touch:
            ra, rb = find(a), find(b)
            if ra != rb and size[ra] + size[rb] <= cap:
                k = (size[ra] + size[rb], min(ra, rb), max(ra, rb))
                best = k if best is None or k < best else best
        if best is None:
            break
        _, ra, rb = best
        parent[rb] = ra
        size[ra] += size[rb]

    C = lumber_opt.CELL
    votes = {}
    for (x, y), lab in owner.items():
        v = votes.setdefault((x // C, y // C), {})
        root = find(lab)
        v[root] = v.get(root, 0) + 1
    groups = {}
    for lab in range(n):
        groups.setdefault(find(lab), []).append(lab)
    cells = {root: [] for root in groups}
    for cell, v in votes.items():
        root = min(v, key=lambda r: (-v[r], r))
        x0, y0 = cell[0] * C, cell[1] * C
        box = (x0, y0, x0 + C - 1, y0 + C - 1)
        if stop is not None and _touches(stop, box) and not any(
                _overlaps(box, sources[lab][1]) for lab in groups[root]):
            continue
        cells[root].append(cell)
    out = []
    for root in sorted(groups, key=lambda r: min(groups[r])):
        cs = sorted(cells[root])
        out.append({"members": [sources[lab][0] for lab in groups[root]], "cells": cs, "claimed": size[root],
                    "trees": sum(trees.at(x, y) for cx, cy in cs
                                 for x in range(cx * C, cx * C + C) for y in range(cy * C, cy * C + C))})
    return out


def _overlaps(box, w) -> bool:
    (cx, cy), r = w["center"], int(w["radius"])
    return box[0] <= cx + r and cx - r <= box[2] and box[1] <= cy + r and cy - r <= box[3]


def _touches(stop, box) -> bool:
    if isinstance(stop, Zones):
        return stop.touches(*box)
    return any(stop(x, y) for x in range(box[0], box[2] + 1) for y in range(box[1], box[3] + 1))


def window_of(spot: dict) -> dict:
    """The square a spot's forest grows from: the window it was found as."""
    w = (spot.get("forest") or {}).get("window") or spot["area"]
    return {"center": [int(w["center"][0]), int(w["center"][1])], "radius": int(w["radius"])}


def base_name(spot: dict) -> str:
    """The spot's name without its tree count ("Witcher 265: X (51 trees, 23 tiles off)")."""
    return (spot.get("forest") or {}).get("base_name") or NAME_TAIL.sub("", spot.get("name") or spot["id"])


def forest_area(cells, window: dict, trees: int) -> dict:
    """A forest spot's area: its cells and the square around them."""
    C = lumber_opt.CELL
    x0, y0 = min(c[0] for c in cells) * C, min(c[1] for c in cells) * C
    x1, y1 = max(c[0] for c in cells) * C + C - 1, max(c[1] for c in cells) * C + C - 1
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    r = max(cx - x0, x1 - cx, cy - y0, y1 - cy)
    return {"center": [cx, cy], "radius": r, "cells": [[int(a), int(b)] for a, b in cells],
            "note": f"forest of {trees} tree statics in {len(cells)} cells of {C}x{C} tiles, grown from the window "
                    f"{window['center'][0]},{window['center'][1]} r{window['radius']} (lumber_forest.py)"}


def no_go(spots: dict, facet: int, towns=(), guard_points=()) -> Zones:
    """The zones a fill keeps out of on `facet`: towns, guard points, spots set aside by hand."""
    z = Zones()
    for x, y in towns:
        z.add(x - lumber_opt.TOWN_RADIUS, y - lumber_opt.TOWN_RADIUS,
              x + lumber_opt.TOWN_RADIUS, y + lumber_opt.TOWN_RADIUS)
    for x, y in guard_points:
        z.add(x - GUARD_KEEP, y - GUARD_KEEP, x + GUARD_KEEP, y + GUARD_KEEP)
    for s in spots.values():
        if s.get("status") == "disabled" and not s.get("merged_into") and s.get("area") \
                and int(s.get("facet") or 0) == facet:
            (cx, cy), r = s["area"]["center"], int(s["area"]["radius"])
            z.add(cx - r, cy - r, cx + r, cy + r)
    return z


def trip_counts(memory) -> dict:
    """{spot id: lumber trip rows} (raw ids, before any merge)."""
    return dict(memory.con.execute(
        "SELECT json_extract(data, '$.spot'), COUNT(*) FROM episodes WHERE loop = 'lumber' "
        "AND json_extract(data, '$.spot') IS NOT NULL GROUP BY 1").fetchall())


def build(spots: dict, facet: int, trees: Trees, stand_z, step, trips: dict, *, towns=(), guard_points=(),
          cap: int = CAP, now: float | None = None) -> dict:
    """The forests of every active spot on `facet` (and of the spots an earlier build merged),
    as store writes: {"forests": [summary], "writes": [(id, status, data, reason)],
    "ungrown": [ids]}. Each spot's data is its current spot (lumber_opt.load_spots) with
    the forest's area, or its window back and `merged_into`."""
    now = time.time() if now is None else now
    src = [s for s in spots.values() if int(s.get("facet") or 0) == facet and s.get("area")
           and (s.get("status") == "active" or (s.get("status") == "disabled" and s.get("merged_into")))]
    src.sort(key=lambda s: (-trips.get(s["id"], 0), s["id"]))
    stop = no_go(spots, facet, towns, guard_points)
    got = grow([(s["id"], window_of(s)) for s in src], trees, stand_z, step, stop, cap=cap)
    forests, writes, ungrown = [], [], []
    for f in got:
        if not f["cells"]:
            ungrown += f["members"]
            continue
        keeper = min(f["members"], key=lambda sid: (-trips.get(sid, 0), sid))
        k = spots[keeper]
        window = window_of(k)
        name = base_name(k)
        merged = [sid for sid in f["members"] if sid != keeper]
        data = {key: v for key, v in k.items() if key not in ("id", "status", "source", "reason", "merged_into")}
        data.update(area=forest_area(f["cells"], window, f["trees"]), tree_count=f["trees"],
                    name=f"{name} forest ({f['trees']} trees" + (f", {len(merged) + 1} spots)" if merged else ")"),
                    forest={"window": window, "base_name": name, "trees": f["trees"], "claimed": f["claimed"],
                            "cells": len(f["cells"]), "link": trees.link, "cap": cap, "built_t": round(now, 1)})
        writes.append((keeper, "active", data, None))
        for sid in merged:
            s = spots[sid]
            d = {key: v for key, v in s.items() if key not in ("id", "status", "source", "reason", "forest")}
            d.update(area=window_of(s), merged_into=keeper)
            if s.get("forest"):
                d["name"] = base_name(s)
            writes.append((sid, "disabled", d, f"merged into forest {keeper}"))
        forests.append({"id": keeper, "trees": f["trees"], "claimed": f["claimed"], "cells": len(f["cells"]),
                        "window_trees": k.get("tree_count"), "merged": merged,
                        "extent": [data["area"]["center"], data["area"]["radius"]]})
    forests.sort(key=lambda f: -f["trees"])
    return {"forests": forests, "writes": writes, "ungrown": ungrown}


def apply(memory, writes, spots: dict):
    """Store a build's writes (a seed spot's row carries what differs from the seed too)."""
    sources = {r["id"]: r["source"] for r in memory.lumber_spot_rows()}
    for sid, status, data, reason in writes:
        memory.lumber_spot_put(sid, status, data, sources.get(sid) or spots[sid].get("source") or "forests", reason)

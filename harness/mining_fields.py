"""Ore-field spots: a mining spot grown over the ore ground around its forge (user request 2026-11-02,
docs/PLAN.md "Ore fields", `ctl mining fields`).

A mining spot was the square ore_nodes.FORGE_NEAR (40) around its forge (`ore_nodes.py spots`), so a
mountain that ran on past the square was cut off at its edge. An ore-field spot is that square grown
the way lumber_forest.py grows a grove window into its forest (one breadth-first fill, lumber_forest.grow):

- ore ground: a tile we can walk onto (pathfind.Walk.can_walk) within LINK tiles of a mineable tile
  (ore_nodes.Tiles: the rock, cave and flat-mountain land and cave-floor statics) outside the town and
  guard-point exclusions; the fill spreads over it in 4 directions from every such tile of the square,
  so ore patches up to 2·LINK+1 tiles apart belong together and open ground or water ends the field;
- an ore block (8x8, a node) is the field's when one of its mineable tiles lies within ore_nodes.REACH
  of a filled tile: what Smart Harvest digs from there. A spot stops growing at CAP blocks;
- the fill keeps out of towns, learned guard points and the squares of spots set aside by hand
  (lumber_forest.no_go), except the spot's own square;
- every spot grows alone, so fields may overlap as the squares did (a block's state is shared:
  mine_blocks, so a block one spot dug out reads depleted from the other). Fields don't merge, unlike
  forests: a spot is its forge, where the runner's weight smelt walks;
- the area is stored as the CELL x CELL cells the fill covered (lumber_opt.CELL, the ore blocks), with
  center/radius the square around them; the runner (loop_mining.candidate_stands) and the viz take the
  stands whose tile lies in a cell (ore_nodes.spot_area_stands). The square is kept in `field.window`,
  so a rebuild starts from the same seeds.
"""
import time

import lumber_forest
import lumber_opt
import ore_nodes

LINK = 2      # ore ground: walkable tiles within this many tiles (Chebyshev) of a mineable tile (a stand's reach)
CAP = 300     # ore blocks per spot at most: ~2 stints of 60 field min at ~1 700 ore/h and ~12 ore a block
CHUNK = lumber_forest.CHUNK


class Ground:
    """Mineable tiles of map m (ore_nodes.grids, outside the exclusion `rects`), loaded per CHUNK rows of
    the whole map width: `at` (a mineable tile), `near` (within `link` tiles of one), `unit` (its ore block)."""

    def __init__(self, m, tiles, rects, link: int = LINK):
        self.m, self.tiles, self.rects, self.link = m, tiles, rects, link
        self._rows = {}

    def _load(self, ky):
        y0, L, B = ky * CHUNK, self.link, ore_nodes.BLOCK
        by0, by1 = max((y0 - L) // B, 0), min((y0 + CHUNK - 1 + L) // B + 1, self.m.blocks_h)
        _walk, bits, _forges = ore_nodes.grids(self.m, self.tiles, by0, by1)
        oy = by0 * B
        mine = (bits != 0) & ~ore_nodes.nogo_grid(self.rects, 0, oy, bits.shape)
        near = ore_nodes.dilate(mine, L)
        r0 = y0 - oy
        self._rows[ky] = (mine[r0:r0 + CHUNK], near[r0:r0 + CHUNK])

    def _get(self, x, y, i):
        if not (0 <= x < self.m.width and 0 <= y < self.m.height):
            return False
        ky = y // CHUNK
        if ky not in self._rows:
            self._load(ky)
        return bool(self._rows[ky][i][y - ky * CHUNK, x])

    def at(self, x: int, y: int) -> bool:
        return self._get(x, y, 0)

    def near(self, x: int, y: int) -> bool:
        return self._get(x, y, 1)

    @staticmethod
    def unit(x: int, y: int) -> tuple:
        return (x // ore_nodes.BLOCK, y // ore_nodes.BLOCK)


def window_of(spot: dict) -> dict:
    """The square a spot's field grows from: its square before any fill."""
    w = (spot.get("field") or {}).get("window") or spot["area"]
    return {"center": [int(w["center"][0]), int(w["center"][1])], "radius": int(w["radius"])}


def field_area(cells, window: dict) -> dict:
    """An ore field's area: its cells and the square around them."""
    C = lumber_opt.CELL
    x0, y0 = min(c[0] for c in cells) * C, min(c[1] for c in cells) * C
    x1, y1 = max(c[0] for c in cells) * C + C - 1, max(c[1] for c in cells) * C + C - 1
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    r = max(cx - x0, x1 - cx, cy - y0, y1 - cy)
    return {"center": [cx, cy], "radius": r, "cells": [[int(a), int(b)] for a, b in cells],
            "note": f"ore field of {len(cells)} cells of {C}x{C} tiles, grown from the square "
                    f"{window['center'][0]},{window['center'][1]} r{window['radius']} (mining_fields.py)"}


def area_nodes(ground: Ground, area: dict) -> tuple[int, int]:
    """(ore blocks, stands) of an area as the runner finds them (ore_nodes.spot_area_stands)."""
    stands = ore_nodes.spot_area_stands(ground.m, ground.tiles, ground.rects, area)
    return len({tuple(b) for s in stands for b in s["blocks"]}), len(stands)


def build(spots: dict, facet: int, ground: Ground, stand_z, step, *, towns=(), guard_points=(), cap: int = CAP,
          now: float | None = None) -> dict:
    """The fields of every active spot on `facet` as store writes: {"fields": [summary], "writes": [(id,
    status, data, reason)], "ungrown": [ids]}. A write's data holds the keys the field sets: its area,
    `nodes` (the ore blocks its stands reach, area_nodes: the runner's count) and `field` (`window_nodes`:
    the square's count)."""
    now = time.time() if now is None else now
    src = sorted((s for s in spots.values() if int(s.get("facet") or 0) == facet and s.get("area")
                  and s.get("status") == "active"), key=lambda s: s["id"])
    stop = lumber_forest.no_go(spots, facet, towns, guard_points)
    fields, writes, ungrown = [], [], []
    for s in src:
        sid, window = s["id"], window_of(s)
        f = lumber_forest.grow([(sid, window)], ground, stand_z, step, stop, reach=ore_nodes.REACH, cap=cap,
                               unit=ground.unit)[0]
        if not f["cells"]:
            ungrown.append(sid)
            continue
        area = field_area(f["cells"], window)
        nodes, stands = area_nodes(ground, area)
        before, _ = area_nodes(ground, window)
        data = {"area": area, "nodes": nodes,
                "field": {"window": window, "window_nodes": before, "nodes": nodes, "claimed": f["claimed"],
                          "cells": len(f["cells"]), "stands": stands, "link": ground.link, "cap": cap,
                          "built_t": round(now, 1)}}
        writes.append((sid, "active", data, s.get("reason")))
        fields.append({"id": sid, "nodes": nodes, "window_nodes": before, "claimed": f["claimed"],
                       "cells": len(f["cells"]), "stands": stands, "extent": [area["center"], area["radius"]]})
    fields.sort(key=lambda f: -f["nodes"])
    return {"fields": fields, "writes": writes, "ungrown": ungrown}


def apply(memory, writes):
    """Store a build's writes over each spot's row (its other fields, source and reason kept; a seed
    spot's new row is 'fields')."""
    rows = {r["id"]: r for r in memory.mining_spot_rows()}
    for sid, status, data, reason in writes:
        row = rows.get(sid) or {}
        memory.mining_spot_put(sid, status, {**(row.get("data") or {}), **data}, row.get("source") or "fields",
                               row.get("reason", reason))

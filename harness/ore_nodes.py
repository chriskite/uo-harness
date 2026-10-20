"""Ore nodes from the map files: where a miner can dig, facet-wide (docs/NOTES.md "Mining: what
the repo and the store know").

    python harness/ore_nodes.py scan [--facet 0] [--out PATH] [--land NAMES] [--statics NAMES]
    python harness/ore_nodes.py near X Y [--radius 40] [--facet 0] [--land NAMES] [--statics NAMES]
    python harness/ore_nodes.py spots [--facet 0] [--out harness/data/mining_spots.json] [--min-nodes 20]

Model (community facts, single sources; RunUO's ore bank is 8x8 too [INFERENCE]): a mining node
is one 8x8 map block, the blocks the map file is stored in. Smart Harvest (a pickaxe double-click)
digs every node with ore left that has a mineable tile within REACH tiles (Chebyshev) of us, so a
stand at a block corner can reach four nodes ("within 2 tiles of a grid intersection you reach
all 4 surrounding nodes").

- mineable tile: land whose tiledata name is in --land, or a static whose name is in --statics.
  Outlands keeps its own list (patch 2019-02-25), so these are candidates until a dig confirms a
  family; `near` names stands whose reach holds a single family, for those test digs.
- walkable tile (approximation, z ignored): passable land that isn't water or void, or a SURFACE
  static on it, and no IMPASSABLE static on it. Over a cave the mountain's surface counts too, so the
  runner (loop_mining.candidate_stands) keeps only the stands its z-aware walk reaches.
- node: an 8x8 block with a mineable tile within REACH of a walkable tile.
- stand: a walkable tile with a node's mineable tile within REACH; n = the nodes it reaches.
- excluded (no node tile, no stand): lumber_opt.TOWN_RADIUS around the atlas towns and
  lumber_forest.GUARD_KEEP around the memory store's learned guard points, as lumber discovery.
- areas: 8-connected groups of node blocks; forges: forge statics (0x0FB1, 0x197A-0x19A9: RunUO
  IsForge [INFERENCE]) grouped by FORGE_LINK tiles. Player-house forges aren't in the map files.

Not in the client files: which families the server takes, region rules beyond the exclusions,
and node state (ore left, colour, respawn), which only digging shows.

`scan` prints a summary and writes --out (JSON, regenerable, gitignored): areas, forge sites, every
node block [bx, by, area, tiles, family bits] and the stands reaching 3+ nodes [x, y, n]. The map
is read in bands of BAND block rows, so memory stays near 0.5 GB. Reads the install's map and
tiledata files and the memory store (read-only); sends nothing anywhere.

`spots` writes the mining runner's seed spots (loop_mining.py, mining_opt.py): one per non-town
overworld forge site with --min-nodes node blocks within FORGE_NEAR tiles, outside lumber_opt's
no-go areas, at most one site per FORGE_NEAR tiles (the richest). Each spot's area is the square
FORGE_NEAR around the forge. Every spot is 'active' (user 2026-10-19: all of them, the planner learns
which are worth it); `ctl mining spot set <id> --status disabled` parks one.
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from collections import deque
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lumber_forest  # noqa: E402
import lumber_opt  # noqa: E402
import places  # noqa: E402
import uomap  # noqa: E402
from memory import DEFAULT_DB  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "ore_nodes.json")
REACH = 2                 # tiles (Chebyshev) Smart Harvest reaches: measured for trees, a community fact for ore
BLOCK = 8                 # a node is one BLOCK x BLOCK map block
BAND = 128                # block rows per pass (1 024 tiles)
PAD = 1                   # block rows of context around a band (needs >= 2 * REACH tiles)
LAND = ("rock", "cave", "flat mountain")
STATICS = ("cave floor",)
VOID = frozenset({"noname", "void", "nodraw", "water"})
FORGES = frozenset({0x0FB1, *range(0x197A, 0x19AA)})
FORGE_LINK = 6            # forge pieces this close (Chebyshev) are one site
FORGE_NEAR = 40           # tiles: the node blocks a forge site serves
OVERWORLD_X = {0: 4600}   # map0: ocean from x 4 500 to 4 700, the dungeon levels east of it
MIN_AREA = 20             # blocks: the summary's "large area"
SPOTS = os.path.join(HERE, "data", "mining_spots.json")


class Tiles:
    """tiledata lookups, filled for the ids a band holds: land -> (passable, family bits),
    statics -> (surface, impassable, family bits). Family i is bit i of `families`:
    ("land", name) first, then ("static", name)."""

    def __init__(self, td, land=LAND, statics=STATICS):
        self.td = td
        self.families = [("land", n) for n in land] + [("static", n) for n in statics]
        if len(self.families) > 32:
            raise ValueError("at most 32 tile families")
        self._bits = {f: 1 << i for i, f in enumerate(self.families)}
        self._land = self._new(3)
        self._item = self._new(4)

    @staticmethod
    def _new(fields):
        return {"known": np.zeros(0, bool), "cols": [np.zeros(0, np.uint32) for _ in range(fields)]}

    def bit(self, family) -> int:
        return self._bits[family]

    def names(self, bits: int) -> list:
        return [f"{k}:{n}" for (k, n), b in self._bits.items() if bits & b]

    def _lut(self, table, ids, info):
        """Grow table to cover ids, fill the entries of the ids present, return the columns."""
        top = int(ids.max()) + 1 if ids.size else 0
        if top > len(table["known"]):
            grow = top - len(table["known"])
            table["known"] = np.concatenate([table["known"], np.zeros(grow, bool)])
            table["cols"] = [np.concatenate([c, np.zeros(grow, np.uint32)]) for c in table["cols"]]
        present = np.nonzero(np.bincount(ids.ravel(), minlength=top)[:top])[0] if ids.size else ()
        for t in present:
            if not table["known"][t]:
                for c, v in zip(table["cols"], info(int(t))):
                    c[t] = v
                table["known"][t] = True
        return table["cols"]

    def _land_info(self, t):
        try:
            lt = self.td.land(t)
        except IndexError:
            return 0, 0, 0
        name = lt.name.lower()
        ok = not lt.flags & (uomap.IMPASSABLE | uomap.WET) and name not in VOID
        return int(ok), self._bits.get(("land", name), 0), 0

    def _item_info(self, g):
        try:
            it = self.td.item(g)
        except IndexError:
            return 0, 0, 0, 0
        name = uomap.display_name(it.name).lower()
        return (int(bool(it.flags & uomap.SURFACE)), int(bool(it.flags & uomap.IMPASSABLE)),
                self._bits.get(("static", name), 0), int(g in FORGES))

    def land(self, ids):
        """(passable bool, family bits u32) grids for a grid of land ids."""
        ok, bits, _ = self._lut(self._land, ids, self._land_info)
        return ok[ids].astype(bool), bits[ids]

    def statics(self, graphics):
        """(surface, impassable, family bits, forge) per static graphic."""
        surf, imp, bits, forge = self._item_lut(graphics)
        return surf[graphics].astype(bool), imp[graphics].astype(bool), bits[graphics], forge[graphics].astype(bool)

    def _item_lut(self, graphics):
        return self._lut(self._item, graphics, self._item_info)


# -------------------------------------------------------------------- grid analysis
def dilate(a, r):
    """Bool grid a grown by r tiles in every direction (Chebyshev), clipped at its edges."""
    rows = a.copy()
    for d in range(1, r + 1):
        rows[:, d:] |= a[:, :-d]
        rows[:, :-d] |= a[:, d:]
    out = rows.copy()
    for d in range(1, r + 1):
        out[d:] |= rows[:-d]
        out[:-d] |= rows[d:]
    return out


def reachable(bits, walk, nogo, reach=REACH):
    """Mineable tiles (bits != 0) outside nogo with a walkable tile within `reach`."""
    return (bits != 0) & ~nogo & dilate(walk, reach)


def stands(reach_mask, walk, nogo, ox=0, oy=0, reach=REACH):
    """Walkable tiles outside nogo with a reachable mineable tile within `reach`, and how many
    distinct BLOCK x BLOCK blocks of the global grid hold one there (element [0, 0] of the grids
    is tile (ox, oy)). -> (ys, xs, n) in grid coordinates, n in 1..4."""
    if 2 * reach + 1 > BLOCK:
        raise ValueError("a stand's reach must fit in two blocks per axis")
    h, w = walk.shape
    ys, xs = np.nonzero(walk & ~nogo & dilate(reach_mask, reach))
    sat = np.zeros((h + 1, w + 1), np.int32)
    np.cumsum(reach_mask, axis=0, dtype=np.int32, out=sat[1:, 1:])
    np.cumsum(sat[1:, 1:], axis=1, out=sat[1:, 1:])

    def any_in(x0, y0, x1, y1):   # a reachable tile in the inclusive rectangle, clipped to the grid
        x0, y0 = np.maximum(x0, 0), np.maximum(y0, 0)
        x1, y1 = np.minimum(x1, w - 1), np.minimum(y1, h - 1)
        c = sat[y1 + 1, x1 + 1] - sat[y0, x1 + 1] - sat[y1 + 1, x0] + sat[y0, x0]
        return (x1 >= x0) & (y1 >= y0) & (c > 0)
    x0, x1, y0, y1 = xs - reach, xs + reach, ys - reach, ys + reach
    cross_x = (ox + x0) // BLOCK != (ox + x1) // BLOCK
    cross_y = (oy + y0) // BLOCK != (oy + y1) // BLOCK
    sx = (ox + x1) // BLOCK * BLOCK - ox          # first column of the right-hand block
    sy = (oy + y1) // BLOCK * BLOCK - oy
    lx1 = np.where(cross_x, sx - 1, x1)
    ty1 = np.where(cross_y, sy - 1, y1)
    n = any_in(x0, y0, lx1, ty1).astype(np.int8)
    n += cross_x & any_in(sx, y0, x1, ty1)
    n += cross_y & any_in(x0, sy, lx1, y1)
    n += cross_x & cross_y & any_in(sx, sy, x1, y1)
    return ys, xs, n


def nogo_grid(rects, ox, oy, shape):
    """Bool grid (element [0, 0] = tile (ox, oy)) of the tiles inside any rectangle."""
    g = np.zeros(shape, bool)
    for x0, y0, x1, y1 in rects:
        r0, r1 = max(y0 - oy, 0), min(y1 - oy + 1, shape[0])
        c0, c1 = max(x0 - ox, 0), min(x1 - ox + 1, shape[1])
        if r0 < r1 and c0 < c1:
            g[r0:r1, c0:c1] = True
    return g


def area_stands(m, tiles, rects, center, radius, reach=REACH) -> list[dict]:
    """The stands within `radius` (Chebyshev) of `center` on map m: [{x, y, n, blocks}], `blocks` the
    sorted [bx, by] global blocks holding a reachable mineable tile within `reach` of the stand
    (len(blocks) == n, as stands() counts them). [] for an area without one."""
    cx, cy = center
    by0 = max((cy - radius - reach) // BLOCK - PAD, 0)
    by1 = min((cy + radius + reach) // BLOCK + 1 + PAD, m.blocks_h)
    if by0 >= by1:
        return []
    walk, bits, _fg = grids(m, tiles, by0, by1)
    oy = by0 * BLOCK
    nogo = nogo_grid(rects, 0, oy, walk.shape)
    mask = reachable(bits, walk, nogo, reach)
    ys, xs, n = stands(mask, walk, nogo, 0, oy, reach)
    gy = ys + oy
    sel = np.maximum(np.abs(xs - cx), np.abs(gy - cy)) <= radius
    ys, xs, gy, n = ys[sel], xs[sel], gy[sel], n[sel]
    if not len(xs):
        return []
    h, w = mask.shape
    bx0, by0s = (xs - reach) // BLOCK, (gy - reach) // BLOCK     # a stand's reach spans at most 2x2 blocks
    hit = np.zeros((len(xs), 2, 2), bool)
    rows = np.arange(len(xs))
    for dy in range(-reach, reach + 1):
        for dx in range(-reach, reach + 1):
            tx, ty = xs + dx, ys + dy
            ok = (tx >= 0) & (tx < w) & (ty >= 0) & (ty < h)
            ok &= mask[np.clip(ty, 0, h - 1), np.clip(tx, 0, w - 1)]
            hit[rows[ok], ((gy + dy) // BLOCK - by0s)[ok], ((xs + dx) // BLOCK - bx0)[ok]] = True
    out = []
    for i in range(len(xs)):
        iy, ix = np.nonzero(hit[i])
        blocks = sorted([int(bx0[i] + a), int(by0s[i] + b)] for a, b in zip(ix, iy))
        out.append({"x": int(xs[i]), "y": int(gy[i]), "n": len(blocks), "blocks": blocks})
    return out


def grids(m, tiles, by0, by1):
    """(walk, bits, forges) of block rows [by0, by1) of map m: the walkable approximation and the
    tile-family bits as (rows * 8, width) grids, the forge statics as [(x, y, z, graphic)]."""
    ids, _z = m.land_rows(by0, by1)
    walk, bits = tiles.land(ids)
    x, y, z, g, _hue = m.statics_rows(by0, by1)
    surf, imp, sbits, forge = tiles.statics(g)
    flat = (y - by0 * BLOCK) * m.width + x
    wf, bf = walk.reshape(-1), bits.reshape(-1)
    wf[flat[surf]] = True
    wf[flat[imp]] = False
    hit = sbits != 0
    np.bitwise_or.at(bf, flat[hit], sbits[hit])
    forges = list(zip(x[forge].tolist(), y[forge].tolist(), z[forge].tolist(), g[forge].tolist()))
    return walk, bits, forges


def scan(m, tiles, rects, band=BAND):
    """The whole map in bands of `band` block rows -> {"tiles": reachable mineable tiles per
    block, "families": their family bits per block, "stands": (xs, ys, n), "forges": [...]}."""
    bw, bh = m.blocks_w, m.blocks_h
    per_block = np.zeros((bh, bw), np.int32)
    fam = np.zeros((bh, bw), np.uint32)
    sxs, sys_, sns, forges = [], [], [], []
    for b0 in range(0, bh, band):
        b1 = min(b0 + band, bh)
        p0, p1 = max(b0 - PAD, 0), min(b1 + PAD, bh)
        walk, bits, fg = grids(m, tiles, p0, p1)
        oy = p0 * BLOCK
        nogo = nogo_grid(rects, 0, oy, walk.shape)
        reach = reachable(bits, walk, nogo)
        ys, xs, n = stands(reach, walk, nogo, 0, oy)
        c0, c1 = (b0 - p0) * BLOCK, (b1 - p0) * BLOCK
        core = (ys >= c0) & (ys < c1)
        sxs.append(xs[core].astype(np.int32))
        sys_.append((ys[core] + oy).astype(np.int32))
        sns.append(n[core])
        shape = (b1 - b0, BLOCK, bw, BLOCK)
        per_block[b0:b1] = reach[c0:c1].reshape(shape).sum(axis=(1, 3))
        fam[b0:b1] = np.bitwise_or.reduce(np.where(reach[c0:c1], bits[c0:c1], 0).reshape(shape), axis=(1, 3))
        forges += [f for f in fg if b0 * BLOCK <= f[1] < b1 * BLOCK]
    return {"tiles": per_block, "families": fam,
            "stands": (np.concatenate(sxs), np.concatenate(sys_), np.concatenate(sns)), "forges": forges}


def label_areas(node):
    """(labels, areas): 8-connected groups of True blocks, labels 1.. in row-major order of
    their first block (0 = none); areas[i] = the (by, bx) blocks of label i + 1."""
    h, w = node.shape
    labels = np.zeros((h, w), np.int32)
    areas = []
    for by, bx in zip(*np.nonzero(node)):
        if labels[by, bx]:
            continue
        lab = len(areas) + 1
        labels[by, bx] = lab
        q, cells = deque([(int(by), int(bx))]), []
        while q:
            y, x = q.popleft()
            cells.append((y, x))
            for ny in range(max(y - 1, 0), min(y + 2, h)):
                for nx in range(max(x - 1, 0), min(x + 2, w)):
                    if node[ny, nx] and not labels[ny, nx]:
                        labels[ny, nx] = lab
                        q.append((ny, nx))
        areas.append(cells)
    return labels, areas


def forge_sites(records, link=FORGE_LINK):
    """Forge statics [(x, y, z, graphic)] grouped into sites (a piece within `link` tiles,
    Chebyshev, of another joins it) -> [{x, y, z, pieces, graphics}], x/y/z the piece nearest
    the site's middle."""
    parent = list(range(len(records)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    cells = {}
    for i, (x, y, _z, _g) in enumerate(records):
        cells.setdefault((x // link, y // link), []).append(i)
    for i, (x, y, _z, _g) in enumerate(records):
        for cx in (x // link - 1, x // link, x // link + 1):
            for cy in (y // link - 1, y // link, y // link + 1):
                for j in cells.get((cx, cy), ()):
                    if j < i and max(abs(records[j][0] - x), abs(records[j][1] - y)) <= link:
                        parent[find(i)] = find(j)
    groups = {}
    for i in range(len(records)):
        groups.setdefault(find(i), []).append(records[i])
    out = []
    for pieces in groups.values():
        mx = sum(p[0] for p in pieces) / len(pieces)
        my = sum(p[1] for p in pieces) / len(pieces)
        x, y, z, _g = min(pieces, key=lambda p: (max(abs(p[0] - mx), abs(p[1] - my)), p[0], p[1]))
        out.append({"x": x, "y": y, "z": z, "pieces": len(pieces),
                    "graphics": sorted({f"{p[3]:#06x}" for p in pieces})})
    return sorted(out, key=lambda s: (s["x"], s["y"]))


# -------------------------------------------------------------------- places
def guard_points(db, facet) -> list:
    """The memory store's learned guard-zone tiles on facet, read-only ([] without a store)."""
    if not db or not os.path.exists(db):
        return []
    con = sqlite3.connect(Path(os.path.abspath(db)).as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        return con.execute("SELECT x, y FROM guard_points WHERE facet = ?", (facet,)).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def exclusions(facet, db):
    """(rects, towns, guard points): the rectangles no node tile or stand may lie in."""
    towns = [(t["x"], t["y"]) for t in places.atlas(("town",)) if t["facet"] == facet]
    gps = guard_points(db, facet)
    tr, gk = lumber_opt.TOWN_RADIUS, lumber_forest.GUARD_KEEP
    rects = [(x - tr, y - tr, x + tr, y + tr) for x, y in towns] + [(x - gk, y - gk, x + gk, y + gk) for x, y in gps]
    return rects, towns, gps


class Runes:
    """Nearest rune-library row (every library; their runes land on facet 0)."""

    def __init__(self, facet):
        rows = [r for r in places.runes() if r.get("x") is not None and r.get("y") is not None] if facet == 0 else []
        self.rows = rows
        self.x = np.array([r["x"] for r in rows], np.int64)
        self.y = np.array([r["y"] for r in rows], np.int64)

    def nearest(self, x, y):
        if not self.rows:
            return None
        d = np.maximum(np.abs(self.x - x), np.abs(self.y - y))
        i = int(np.argmin(d))
        r = self.rows[i]
        return {"name": r["name"], "library": r.get("library"), "x": r["x"], "y": r["y"], "tiles": int(d[i])}


def side(facet, x):
    edge = OVERWORLD_X.get(facet)
    return None if edge is None else ("overworld" if x < edge else "dungeon")


# -------------------------------------------------------------------- report
def build(res, facet, tiles, towns, gps, runes):
    """The JSON document of a scan (module doc)."""
    per_block, fam = res["tiles"], res["families"]
    node = per_block > 0
    labels, areas = label_areas(node)
    bh, bw = node.shape
    xs, ys, n = res["stands"]
    area_of = np.zeros(len(xs), np.int32)
    for dx, dy in ((-REACH, -REACH), (REACH, -REACH), (-REACH, REACH), (REACH, REACH)):
        lab = labels[np.clip((ys + dy) // BLOCK, 0, bh - 1), np.clip((xs + dx) // BLOCK, 0, bw - 1)]
        area_of = np.where(area_of == 0, lab, area_of)
    by_area = {k: np.bincount(area_of[n == k], minlength=len(areas) + 1) for k in (3, 4)}

    def town(x, y):
        return any(max(abs(tx - x), abs(ty - y)) <= lumber_opt.TOWN_RADIUS for tx, ty in towns)

    sites = forge_sites(res["forges"])
    near_forge = np.zeros_like(node)
    for i, s in enumerate(sites, 1):
        s["id"] = i
        s["town"] = town(s["x"], s["y"])
        s["side"] = side(facet, s["x"])
        win = (slice(max((s["y"] - FORGE_NEAR) // BLOCK, 0), (s["y"] + FORGE_NEAR) // BLOCK + 1),
               slice(max((s["x"] - FORGE_NEAR) // BLOCK, 0), (s["x"] + FORGE_NEAR) // BLOCK + 1))
        s["nodes_near"] = int(node[win].sum())
        s["areas"] = sorted(int(a) for a in np.unique(labels[win]) if a)
        if not s["town"]:
            near_forge[win] = True
        s["rune"] = runes.nearest(s["x"], s["y"])
    out_areas = []
    for i, cells in enumerate(areas, 1):
        by, bx = np.array(cells).T
        cx, cy = int(round(bx.mean() * BLOCK + BLOCK / 2)), int(round(by.mean() * BLOCK + BLOCK / 2))
        bits = int(np.bitwise_or.reduce(fam[by, bx]))
        out_areas.append({
            "id": i, "blocks": len(cells), "tiles": int(per_block[by, bx].sum()), "center": [cx, cy],
            "box": [int(bx.min()) * BLOCK, int(by.min()) * BLOCK, int(bx.max()) * BLOCK + BLOCK - 1,
                    int(by.max()) * BLOCK + BLOCK - 1],
            "stands4": int(by_area[4][i]), "stands3": int(by_area[3][i]), "side": side(facet, cx),
            "families": tiles.names(bits), "forges": [s["id"] for s in sites if i in s["areas"]],
            "rune": runes.nearest(cx, cy)})
    out_areas.sort(key=lambda a: (-a["blocks"], a["id"]))
    bys, bxs = np.nonzero(node)
    blocks = np.stack([bxs, bys, labels[bys, bxs], per_block[bys, bxs], fam[bys, bxs].astype(np.int64)], axis=1)
    keep = n >= 3
    summary = {}
    for name in (("overworld", "dungeon") if facet in OVERWORLD_X else ("all",)):
        if name == "all":
            bsel, ssel = np.ones_like(node), np.ones(len(xs), bool)
        else:
            edge = OVERWORLD_X[facet] // BLOCK
            bsel = np.zeros_like(node)
            bsel[:, :edge] = name == "overworld"
            bsel[:, edge:] = name == "dungeon"
            ssel = (xs < OVERWORLD_X[facet]) == (name == "overworld")
        summary[name] = {"node_blocks": int((node & bsel).sum()),
                         "near_forge": int((node & bsel & near_forge).sum()),
                         "stands": [int(((n == k) & ssel).sum()) for k in (1, 2, 3, 4)]}
    return {
        "facet": facet, "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "families": tiles.names((1 << len(tiles.families)) - 1),
        "rules": {"reach": REACH, "block": BLOCK, "town_radius": lumber_opt.TOWN_RADIUS,
                  "guard_keep": lumber_forest.GUARD_KEEP, "forge_link": FORGE_LINK, "forge_near": FORGE_NEAR,
                  "overworld_x": OVERWORLD_X.get(facet),
                  "walk": "approximate: passable land or a SURFACE static, no IMPASSABLE static, z ignored"},
        "excluded": {"towns": len(towns), "guard_points": len(gps)},
        "summary": summary, "forge_statics": len(res["forges"]),
        "areas": out_areas, "forges": sites,
        "blocks_fields": ["bx", "by", "area", "tiles", "family_bits"], "blocks": blocks.tolist(),
        "stands_fields": ["x", "y", "nodes"],
        "stands": np.stack([xs[keep], ys[keep], n[keep]], axis=1).astype(np.int64).tolist(),
    }


def print_scan(doc, seconds, out, top):
    print(f"map{doc['facet']}: {', '.join(doc['families'])} (reach {doc['rules']['reach']}, "
          f"nodes {BLOCK}x{BLOCK}), {seconds:.1f} s")
    print(f"excluded: {doc['excluded']['towns']} towns (±{doc['rules']['town_radius']}), "
          f"{doc['excluded']['guard_points']} guard points (±{doc['rules']['guard_keep']})")
    for name, s in doc["summary"].items():
        print(f"{name:>9}: {s['node_blocks']:,} node blocks, {s['near_forge']:,} within {FORGE_NEAR} tiles of a "
              f"non-town forge; stands reaching 1/2/3/4 nodes: {' / '.join(f'{v:,}' for v in s['stands'])}")
    sites = doc["forges"]
    print(f"forges: {doc['forge_statics']} statics at {len(sites)} sites, {sum(s['town'] for s in sites)} in towns")
    big = [a for a in doc["areas"] if a["blocks"] >= MIN_AREA]
    print(f"areas: {len(doc['areas'])} ({len(big)} of >= {MIN_AREA} blocks hold {sum(a['blocks'] for a in big):,})")
    print(f"  {'blocks':>6} {'4-node':>6} {'forges':>6}  {'centre':<12} {'side':<9} nearest rune")
    for a in doc["areas"][:top]:
        r = a["rune"]
        rune = f"{r['name']} ({r['tiles']})" if r else "-"
        centre = f"({a['center'][0]},{a['center'][1]})"
        print(f"  {a['blocks']:6d} {a['stands4']:6d} {len(a['forges']):6d}  {centre:<12} {a['side'] or '-':<9} {rune}")
    if out:
        print(f"wrote {os.path.relpath(out)} ({os.path.getsize(out) / 1e6:.1f} MB)")


def near(m, tiles, rects, x, y, radius, runes, facet):
    """Lines describing the nodes, stands, families and forges within `radius` of (x, y)."""
    by0 = max((y - radius) // BLOCK - PAD, 0)
    by1 = min((y + radius) // BLOCK + 1 + PAD, m.blocks_h)
    walk, bits, fg = grids(m, tiles, by0, by1)
    oy = by0 * BLOCK
    nogo = nogo_grid(rects, 0, oy, walk.shape)
    reach = reachable(bits, walk, nogo)
    ys, xs, n = stands(reach, walk, nogo, 0, oy)
    gy = ys + oy
    d = np.maximum(np.abs(xs - x), np.abs(gy - y))
    sel = d <= radius
    xs, gy, ys, n, d = xs[sel], gy[sel], ys[sel], n[sel], d[sel]
    fam_in = np.zeros(len(xs), np.int64)
    for f in tiles.families:
        b = tiles.bit(f)
        hit = dilate(reach & ((bits & np.uint32(b)) != 0), REACH)[ys, xs]
        fam_in |= np.where(hit, b, 0)
    lines = [f"map{facet} ({x}, {y}), radius {radius}" + (" (inside a town/guard exclusion)"
                                                          if 0 <= y - oy < nogo.shape[0] and nogo[y - oy, x] else "")]
    r0, r1 = max(y - radius - oy, 0), min(y + radius - oy + 1, walk.shape[0])
    c0, c1 = max(x - radius, 0), min(x + radius + 1, m.width)
    box_bits, box_reach = bits[r0:r1, c0:c1], reach[r0:r1, c0:c1]
    lines.append("tile families (mineable tiles / reachable from a walkable tile):")
    for f in tiles.families:
        b = np.uint32(tiles.bit(f))
        has = (box_bits & b) != 0
        lines.append(f"  {f[0] + ':' + f[1]:<24} {int(has.sum()):6d} / {int((has & box_reach).sum()):6d}")
    yy, xx = np.nonzero(box_reach)
    blocks = set(zip(((xx + c0) // BLOCK).tolist(), ((yy + r0 + oy) // BLOCK).tolist()))
    lines.append(f"node blocks touching the square: {len(blocks)} ({len(yy)} reachable tiles)")
    counts = [int((n == k).sum()) for k in (1, 2, 3, 4)]
    lines.append(f"stands reaching 1/2/3/4 nodes: {' / '.join(map(str, counts))}")
    order = np.lexsort((d, -n))[:10]
    for i in order:
        lines.append(f"  stand ({xs[i]},{gy[i]}) {n[i]} nodes, {d[i]} tiles away: {', '.join(tiles.names(int(fam_in[i])))}")
    lines.append("single-family stands for a test dig (nearest):")
    for f in tiles.families:
        b = tiles.bit(f)
        one = np.nonzero(fam_in == b)[0]
        if len(one):
            i = one[np.argmin(d[one])]
            lines.append(f"  {f[0] + ':' + f[1]:<24} ({xs[i]},{gy[i]}), {d[i]} tiles away, {n[i]} nodes")
    sites = [s for s in forge_sites(fg) if max(abs(s["x"] - x), abs(s["y"] - y)) <= radius]
    lines.append(f"forge sites within {radius}: {len(sites)}")
    for s in sites:
        lines.append(f"  ({s['x']},{s['y']},z{s['z']}) {s['pieces']} pieces {' '.join(s['graphics'])}")
    r = runes.nearest(x, y)
    if r:
        lines.append(f"nearest rune: {r['name']} ({r['library']}) at ({r['x']},{r['y']}), {r['tiles']} tiles")
    return lines


def _names(v):
    return tuple(s.strip().lower() for s in v.split(",") if s.strip())


def seed_spots(doc, facet, min_nodes) -> list[dict]:
    """The mining seed spots of a scan document (module doc, `spots`): richest forge sites first."""
    taken = []
    sites = [s for s in doc["forges"] if not s["town"] and s["side"] == "overworld" and s["nodes_near"] >= min_nodes
             and lumber_opt.no_go(facet, (s["x"], s["y"])) is None]
    for s in sorted(sites, key=lambda s: (-s["nodes_near"], s["x"], s["y"])):
        if any(max(abs(s["x"] - t["x"]), abs(s["y"] - t["y"])) <= FORGE_NEAR for t in taken):
            continue
        taken.append(s)
    out = []
    for s in taken:
        x, y = s["x"], s["y"]
        rune = (s.get("rune") or {}).get("name")
        out.append({"id": f"forge_{x}_{y}", "name": f"{rune or 'unnamed'} forge ({x},{y})", "facet": facet,
                    "area": {"center": [x, y], "radius": FORGE_NEAR, "note": "ore_nodes.py spots"},
                    "forge": {"x": x, "y": y, "z": s["z"]}, "nodes": s["nodes_near"],
                    "pvp": True, "hazard_prior": 0.5, "source": "ore_nodes",
                    "status": "active"})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="ore nodes from the map files (module doc)")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--facet", type=int, default=0)
    common.add_argument("--land", type=_names, default=LAND, help="mineable land tiledata names, comma-separated")
    common.add_argument("--statics", type=_names, default=STATICS, help="mineable static tiledata names")
    common.add_argument("--db", default=DEFAULT_DB, help="memory store for learned guard points (read-only)")
    common.add_argument("--root", default=uomap.INSTALL)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", parents=[common], help="the whole facet: summary + JSON")
    s.add_argument("--out", default=OUT, help="JSON output ('' = none)")
    s.add_argument("--top", type=int, default=15, help="areas listed")
    s.add_argument("--band", type=int, default=BAND, help="block rows per pass")
    nr = sub.add_parser("near", parents=[common], help="nodes, stands, families and forges around X Y")
    nr.add_argument("x", type=int)
    nr.add_argument("y", type=int)
    nr.add_argument("--radius", type=int, default=40)
    sp = sub.add_parser("spots", parents=[common], help="mining seed spots at the forge sites (module doc)")
    sp.add_argument("--out", default=SPOTS, help="seed spot JSON (mining_opt.SEEDS)")
    sp.add_argument("--min-nodes", type=int, default=20, help="node blocks within FORGE_NEAR of the forge")
    sp.add_argument("--band", type=int, default=BAND, help="block rows per pass")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    m = uomap.UoMap(a.facet, a.root)
    tiles = Tiles(m.tiledata, a.land, a.statics)
    rects, towns, gps = exclusions(a.facet, a.db)
    runes = Runes(a.facet)
    if a.cmd == "near":
        print("\n".join(near(m, tiles, rects, a.x, a.y, a.radius, runes, a.facet)))
        return 0
    t0 = time.time()
    doc = build(scan(m, tiles, rects, a.band), a.facet, tiles, towns, gps, runes)
    if a.cmd == "spots":
        spots = seed_spots(doc, a.facet, a.min_nodes)
        _write_json(a.out, {"_doc": "Mining seed spots: non-town overworld forge sites with ore nodes around them "
                                    "(python harness/ore_nodes.py spots; mining_opt.load_spots), all active (user "
                                    "2026-10-19); `ctl mining spot set <id> --status disabled` parks one.",
                            "generated": doc["generated"], "spots": spots}, indent=1)
        for s in spots:
            print(f"{s['id']:<18} {s['nodes']:4d} nodes  {s['status']:<9} {s['name']}")
        print(f"wrote {os.path.relpath(a.out)}: {len(spots)} spots, "
              f"{sum(s['status'] == 'active' for s in spots)} active ({time.time() - t0:.1f} s)")
        return 0
    if a.out:
        _write_json(a.out, doc)
    print_scan(doc, time.time() - t0, a.out, a.top)
    return 0


def _write_json(path, doc, indent=None):
    """`doc` to `path` atomically (.part, then replace)."""
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=indent, separators=(",", ":") if indent is None else None)
        if indent is not None:
            f.write("\n")
    os.replace(tmp, path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

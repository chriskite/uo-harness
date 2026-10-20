"""Behaviour of the ore node finder (harness/ore_nodes.py): how many 8x8 nodes a stand reaches
at and away from block corners, which mineable tiles count as reachable, what statics do to
walking and mining, exclusions, the band seams of a facet scan, forge sites and areas.
A synthetic map; no game files.

Run: python harness/test_ore_nodes.py
"""
import os
import random
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ore_nodes as on  # noqa: E402
import uomap  # noqa: E402
from uomap import IMPASSABLE, SURFACE, WET  # noqa: E402

FAILURES = []
GRASS, ROCK, WATER, VOID = 0, 1, 2, 3
CAVE_FLOOR, BOULDER, FORGE = 10, 11, 0x0FB1


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}{'' if cond else ' ' + str(detail)}")
    if not cond:
        FAILURES.append(name)


class TileData:
    LAND = {GRASS: (0, "grass"), ROCK: (IMPASSABLE, "rock"), WATER: (IMPASSABLE | WET, "water"), VOID: (0, "void")}
    ITEMS = {CAVE_FLOOR: (SURFACE, "cave floor"), BOULDER: (IMPASSABLE, "boulder"), FORGE: (IMPASSABLE, "forge")}
    land_count, item_count = 4, 0x2000

    def land(self, i):
        flags, name = self.LAND[i]
        return uomap.LandTile(i, flags, 0, name)

    def item(self, g):
        flags, name = self.ITEMS[g]
        return uomap.ItemTile(g, flags, 0, 0, 0, 0, 0, name, 0, 0)


class Map:
    """land: (H, W) tile ids; statics: [(x, y, z, graphic)]."""

    def __init__(self, land, statics=()):
        self.land = np.asarray(land, np.uint32)
        self.height, self.width = self.land.shape
        self.blocks_h, self.blocks_w = self.height // 8, self.width // 8
        self.st = list(statics)

    def land_rows(self, by0, by1):
        rows = self.land[by0 * 8:by1 * 8]
        return rows.copy(), np.zeros(rows.shape, np.int16)

    def statics_rows(self, by0, by1):
        s = [r for r in self.st if by0 * 8 <= r[1] < by1 * 8]
        col = list(zip(*s)) if s else [(), (), (), ()]
        return (np.array(col[0], np.int64), np.array(col[1], np.int64), np.array(col[2], np.int16),
                np.array(col[3], np.uint32), np.zeros(len(s), np.uint16))


def run(m, rects=(), band=on.BAND, statics=("cave floor",)):
    res = on.scan(m, on.Tiles(TileData(), ("rock",), statics), list(rects), band)
    xs, ys, n = res["stands"]
    return res, {(int(x), int(y)): int(k) for x, y, k in zip(xs, ys, n)}


def test_corner():
    print("a stand reaches every 8x8 block with a mineable tile within 2 tiles")
    land = np.full((32, 32), GRASS)
    for x, y in ((6, 6), (10, 6), (6, 10), (10, 10)):
        land[y, x] = ROCK
    res, st = run(Map(land))
    check("block corner (8,8): 4 nodes", st.get((8, 8)) == 4, st.get((8, 8)))
    check("(8,6): the two blocks above the corner", st.get((8, 6)) == 2, st.get((8, 6)))
    check("(4,4): only (6,6), one node", st.get((4, 4)) == 1, st.get((4, 4)))
    check("(3,3), 3 tiles from the nearest rock: no stand", (3, 3) not in st)
    check("the rock tile itself is no stand (impassable)", (6, 6) not in st)
    check("one reachable tile in each of the 4 blocks",
          res["tiles"][:2, :2].tolist() == [[1, 1], [1, 1]], res["tiles"][:2, :2].tolist())


def test_mountain_core():
    print("rock farther than 2 tiles from walkable ground is no node")
    land = np.full((40, 40), GRASS)
    land[:24, :24] = ROCK
    res, _ = run(Map(land))
    t = res["tiles"]
    check("block (1,1) deep in the mass: no node", t[1, 1] == 0, t[1, 1])
    check("block (0,0) at the map edge, under rock: no node", t[0, 0] == 0, t[0, 0])
    check("block (2,2) by the grass: a node", t[2, 2] > 0, t[2, 2])
    check("block (2,2) counts only the rock within 2 tiles of grass (tiles 22-23 of its edges)",
          t[2, 2] == 8 * 8 - 6 * 6, t[2, 2])


def test_statics():
    print("statics: a cave floor is walkable and mineable, a boulder blocks the tile")
    land = np.full((16, 16), VOID)
    floor = [(x, y, 0, CAVE_FLOOR) for x in range(2, 14) for y in range(2, 14)]
    res, st = run(Map(land, floor + [(7, 7, 0, BOULDER)]))
    check("void land under nothing: no stand", (0, 0) not in st)
    check("on the cave floor: a stand", (3, 3) in st)
    check("the boulder's tile: no stand", (7, 7) not in st)
    check("cave floor family on the nodes", int(res["families"][0, 0]) == 2, int(res["families"][0, 0]))
    _, st_off = run(Map(land, floor), statics=())
    check("without the cave-floor family: no stands at all", not st_off, len(st_off))


def test_exclusions():
    print("an excluded rectangle has no node tiles and no stands")
    land = np.full((32, 32), GRASS)
    land[4, 4] = land[4, 20] = ROCK
    res, st = run(Map(land), rects=[(0, 0, 12, 12)])
    check("excluded rock: no node", res["tiles"][0, 0] == 0)
    check("rock outside: a node", res["tiles"][0, 2] == 1)
    check("no stand inside the rectangle", not any(x <= 12 and y <= 12 for x, y in st))


def test_bands():
    print("a facet scan in bands gives what one pass gives")
    rnd = random.Random(7)
    land = np.array([[rnd.choice((GRASS, GRASS, ROCK, ROCK, WATER, VOID)) for _ in range(80)] for _ in range(72)])
    statics = [(rnd.randrange(80), rnd.randrange(72), 0, rnd.choice((CAVE_FLOOR, BOULDER, FORGE)))
               for _ in range(600)]
    m = Map(land, statics)
    one, st_one = run(m, rects=[(30, 30, 41, 44)], band=9)
    many, st_many = run(m, rects=[(30, 30, 41, 44)], band=1)
    check("node tiles per block", np.array_equal(one["tiles"], many["tiles"]))
    check("families per block", np.array_equal(one["families"], many["families"]))
    check("stands and their node counts", st_one == st_many, len(set(st_one.items()) ^ set(st_many.items())))
    check("every forge piece once", sorted(one["forges"]) == sorted(many["forges"])
          == sorted((x, y, z, g) for x, y, z, g in statics if g == FORGE))


def test_forge_sites():
    print("forge pieces within 6 tiles form one site")
    sites = on.forge_sites([(100, 100, 0, 0x197A), (105, 100, 0, 0x197B), (111, 100, 0, 0x197C),
                            (130, 100, 5, 0x0FB1)])
    check("a chain 6 tiles apart joins, a piece 19 tiles on doesn't", [s["pieces"] for s in sites] == [3, 1],
          sites)
    check("a site sits on its middle piece", (sites[0]["x"], sites[0]["y"]) == (105, 100), sites[0])


def test_areas():
    print("areas are 8-connected node blocks")
    node = np.zeros((6, 6), bool)
    node[0, 0] = node[1, 1] = node[2, 2] = True     # a diagonal chain
    node[5, 5] = True
    labels, areas = on.label_areas(node)
    check("the diagonal chain is one area", sorted(len(a) for a in areas) == [1, 3], areas)
    check("labels 1.. in row-major order", labels[0, 0] == 1 and labels[5, 5] == 2, labels.tolist())


def main():
    for t in (test_corner, test_mountain_core, test_statics, test_exclusions, test_bands, test_forge_sites,
              test_areas):
        t()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)}: {FAILURES}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

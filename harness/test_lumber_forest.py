"""Behaviour of the forest builder (harness/lumber_forest.py, docs/LUMBER_LOOP.md §6
"Forest spots"): how far a spot's window grows over the forest around it, where it stops
(gaps, water, towns, the tree cap), when spots merge, and what the store gets.
A synthetic map (trees, water); no game files.

Run: python harness/test_lumber_forest.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lumber_forest as lf  # noqa: E402
import lumber_opt as lo  # noqa: E402
from memory import Memory  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}{'' if cond else ' ' + str(detail)}")
    if not cond:
        FAILURES.append(name)


class World:
    """Trees (impassable) and water on an open plain."""

    def __init__(self, trees, water=()):
        self.trees, self.water = set(trees), set(water)

    def trees_fn(self, x0, y0, x1, y1):
        return [(x, y, 0, 0x0CE0) for x, y in self.trees if x0 <= x <= x1 and y0 <= y <= y1]

    def open(self, x, y):
        return (x, y) not in self.trees and (x, y) not in self.water

    def stand_z(self, x, y):
        return 0 if self.open(x, y) else None

    def step(self, x, y, z, d):
        dx, dy = next((a, b) for a, b, dd in lf.DIRS4 if dd == d)
        return 0 if self.open(x + dx, y + dy) else None

    def grow(self, sources, stop=None, cap=lf.CAP):
        return lf.grow(sources, lf.Trees(self.trees_fn), self.stand_z, self.step, stop, cap=cap)


def lattice(x0, x1, y0, y1, k=3):
    return {(x, y) for x in range(x0, x1 + 1, k) for y in range(y0, y1 + 1, k)}


def win(x, y, r=5):
    return {"center": [x, y], "radius": r}


def trees_in(world, f):
    C = lo.CELL
    cells = set(f["cells"])
    return {t for t in world.trees if (t[0] // C, t[1] // C) in cells}


def test_extent():
    print("== a window grows over the trees it's joined to; a wide gap or water ends the forest ==")
    strip = lattice(200, 260, 200, 210)                    # trees 3 apart running east of the window
    beyond = lattice(270, 300, 200, 210)                    # 10 tiles past the strip's last tree: too far
    north = lattice(200, 260, 180, 189)                     # its trees' ground meets the strip's, but
    river = {(x, y) for x in range(150, 320) for y in range(190, 193)}   # water between them
    w = World(strip | beyond | north, river)
    f = w.grow([("a", win(203, 205))])[0]
    got = trees_in(w, f)
    check("the strip is one forest", strip <= got, len(strip - got))
    check("trees 10 tiles past it aren't (more than 2·LINK+1 apart)", not got & {t for t in beyond if t[0] >= 272},
          sorted(got & beyond)[:5])
    check("the grove across the water isn't, though its trees stand within reach", not (got & north),
          sorted(got & north)[:5])
    town = lf.Zones([(240, 150, 400, 260)])
    g = w.grow([("a", win(203, 205))], stop=town)[0]
    check("a town zone stops the fill: no cell of the forest touches it",
          g["cells"] and max(cx for cx, _ in g["cells"]) * lo.CELL + lo.CELL - 1 < 240, g["cells"][-3:])


def test_cap_and_merge():
    print("== spots in one woodland split it up to the cap; territories that meet merge when they fit ==")
    wood = lattice(400, 640, 400, 460)                     # 81 x 21 = 1 701 trees
    w = World(wood)
    got = w.grow([("west", win(405, 430)), ("east", win(635, 430))], cap=300)
    check("two spots, each stopped at the cap: no merge (600 > 300)",
          [f["members"] for f in got] == [["west"], ["east"]] and all(f["claimed"] >= 300 for f in got),
          [(f["members"], f["claimed"]) for f in got])
    small = World(lattice(800, 830, 800, 812))              # 11 x 5 = 55 trees
    m = small.grow([("c", win(803, 806)), ("d", win(827, 806))], cap=300)
    check("two spots in a small grove meet and merge: one forest of all its trees",
          len(m) == 1 and sorted(m[0]["members"]) == ["c", "d"] and m[0]["trees"] == 55,
          [(f["members"], f["trees"]) for f in m])


def test_build_store():
    print("== build: the keeper has the most trips, the other is merged into it; a rebuild changes nothing ==")
    d = tempfile.mkdtemp()
    seeds = os.path.join(d, "seeds.json")
    with open(seeds, "w") as fh:
        json.dump({"spots": []}, fh)
    mem = Memory(os.path.join(d, "h.db"))
    for sid, (x, y) in {"c": (803, 806), "d": (827, 806)}.items():
        mem.lumber_spot_put(sid, "active", {"id": sid, "name": f"Witcher {sid}: Glade (30 trees, 5 tiles off)",
                                            "facet": 0, "area": win(x, y), "trees": [], "pvp": True,
                                            "hazard_prior": 0.5, "tree_count": 30}, "discover")
    mem.lumber_spot_put("x", "disabled", {"id": "x", "facet": 0, "area": win(860, 806, 12), "trees": []},
                        "discover", "surrounded by mobs")
    world = World(lattice(800, 900, 800, 812))
    spots = lo.load_spots(mem, seeds)
    out = lf.build(spots, 0, lf.Trees(world.trees_fn), world.stand_z, world.step, {"d": 5, "c": 1}, now=1.0)
    lf.apply(mem, out["writes"], spots)
    after = lo.load_spots(mem, seeds)
    d_, c_ = after["d"], after["c"]
    check("one forest kept by the most-tried spot; the other disabled, merged into it",
          [f["id"] for f in out["forests"]] == ["d"] and d_["status"] == "active" and c_["status"] == "disabled"
          and c_["merged_into"] == "d" and c_["reason"] == "merged into forest d" and lo.merged_alias(after) == {"c": "d"},
          (out["forests"], c_))
    check("the disabled spot is a no-go zone: the forest stops short of its square (the strip's far end too)",
          max(cx for cx, _ in d_["area"]["cells"]) * lo.CELL + lo.CELL - 1 < 848, d_["area"]["cells"][-2:])
    check("the forest's area: cells, a square around them, its window kept; its name without the old count",
          lo.check_spot(d_) is None and d_["forest"]["window"] == win(827, 806)
          and all(lo.in_area(d_["area"], *t) for t in [(803, 803), (827, 803)])
          and d_["name"] == f"Witcher d: Glade forest ({d_['tree_count']} trees, 2 spots)"
          and c_["area"] == win(803, 806), (d_["name"], d_["forest"]))
    again = lf.build(after, 0, lf.Trees(world.trees_fn), world.stand_z, world.step, {"d": 5, "c": 1}, now=1.0)
    check("a rebuild grows from the kept windows to the same forest", again["writes"] == out["writes"],
          [(a[0], b[0]) for a, b in zip(again["writes"], out["writes"])])
    mem.close()


if __name__ == "__main__":
    for fn in (test_extent, test_cap_and_merge, test_build_store):
        fn()
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILURES: {FAILURES}"))
    sys.exit(0 if not FAILURES else 1)

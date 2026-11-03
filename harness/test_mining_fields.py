"""Behaviour of the ore-field builder (harness/mining_fields.py, docs/PLAN.md "Ore fields"): how far a spot's
square grows along the rock, what ends it (open ground, water, a no-go zone, the cap), spots growing alone, and
the store round trip. A synthetic map (test_ore_nodes.Map); no game files.

Run: python harness/test_mining_fields.py
"""
import json
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lumber_forest as lf  # noqa: E402
import lumber_opt as lo  # noqa: E402
import mining_fields as mf  # noqa: E402
import mining_opt  # noqa: E402
import ore_nodes as on  # noqa: E402
from memory import Memory  # noqa: E402
from test_ore_nodes import GRASS, ROCK, WATER, Map, TileData  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}{'' if cond else ' ' + str(detail)}")
    if not cond:
        FAILURES.append(name)


def world():
    """A ridge of rock x 20-139, y 40-49 on grass; 6 open tiles east of its ground a second ridge (x 148-159);
    south of it a river (y 52-55) and a third ridge beyond (y 58-64)."""
    land = np.full((120, 176), GRASS)
    land[40:50, 20:140] = ROCK
    land[40:50, 148:160] = ROCK
    land[52:56, 0:176] = WATER
    land[58:65, 20:60] = ROCK
    m = Map(land)
    tiles = on.Tiles(TileData(), ("rock",), ("cave floor",))
    passable = land == GRASS

    def stand_z(x, y):
        return 0 if 0 <= x < m.width and 0 <= y < m.height and passable[y, x] else None

    def step(x, y, z, d):
        dx, dy = next((a, b) for a, b, dd in lf.DIRS4 if dd == d)
        return stand_z(x + dx, y + dy)
    return m, tiles, stand_z, step


def win(x, y, r=6):
    return {"center": [x, y], "radius": r}


def spot(sid, x, y, r=6, **kw):
    return {"id": sid, "name": sid, "facet": 0, "area": win(x, y, r), "forge": {"x": x, "y": y, "z": 0},
            "status": "active", **kw}


def test_extent():
    print("== a square grows along the rock it touches; open ground, water, the cap and a no-go zone end it ==")
    m, tiles, stand_z, step = world()
    g = mf.Ground(m, tiles, [])
    f = lf.grow([("a", win(30, 36))], g, stand_z, step, reach=on.REACH, cap=mf.CAP, unit=g.unit)[0]
    cells = set(f["cells"])
    check("the field runs the ridge's length, round its ends to the south side",
          max(cx for cx, _ in cells) == 141 // 8 and (8, 6) in cells and min(cx for cx, _ in cells) <= 2,
          sorted(cells)[-4:])
    check("the ridge 6 open tiles east isn't the field's (more than 2·LINK+1 apart)",
          not any(cx >= 148 // 8 for cx, _ in cells), sorted(cells)[-4:])
    check("the ridge across the river isn't, though its ground lies within LINK of the water",
          not any(cy >= 56 // 8 for _, cy in cells), sorted(c for c in cells if c[1] >= 7))
    blocks = {(bx, by) for bx in range(20 // 8, 139 // 8 + 1) for by in (40 // 8, 49 // 8)}
    check("every block of the ridge is claimed, as ore blocks (not tiles)", f["claimed"] == len(blocks),
          (f["claimed"], len(blocks)))
    c = lf.grow([("a", win(30, 36))], g, stand_z, step, reach=on.REACH, cap=10, unit=g.unit)[0]
    check("the cap stops the fill: ~10 blocks, short of the ridge's east end",
          10 <= c["claimed"] <= 12 and max(cx for cx, _ in c["cells"]) < 12, (c["claimed"], c["cells"][-2:]))
    town = lf.Zones([(80, 0, 90, 119)])
    t = lf.grow([("a", win(30, 36))], g, stand_z, step, town, reach=on.REACH, cap=mf.CAP, unit=g.unit)[0]
    check("a no-go zone stops it: no cell reaches the zone", max(cx * 8 + 7 for cx, _ in t["cells"]) < 80,
          t["cells"][-2:])


def test_build_store():
    print("== build: spots grow alone and overlap; the store row keeps its fields; a rebuild changes nothing ==")
    m, tiles, stand_z, step = world()
    g = mf.Ground(m, tiles, [])
    d = tempfile.mkdtemp()
    seeds = os.path.join(d, "seeds.json")
    with open(seeds, "w") as fh:
        json.dump({"spots": [spot("west", 30, 36, nodes=4), spot("east", 130, 36, nodes=4),
                             spot("park", 150, 70, 4)]}, fh)
    mem = Memory(os.path.join(d, "h.db"))
    mem.mining_spot_put("east", "active", {"faction_zone": "z"}, "runner")
    mem.mining_spot_put("park", "disabled", {}, "overseer", "monsters")
    spots = mining_opt.load_spots(mem, seeds)
    out = mf.build(spots, 0, g, stand_z, step, now=1.0)
    mf.apply(mem, out["writes"])
    after = mining_opt.load_spots(mem, seeds)
    w, e = after["west"], after["east"]
    check("both active spots grow; the disabled one doesn't",
          sorted(f["id"] for f in out["fields"]) == ["east", "west"] and after["park"]["status"] == "disabled"
          and "field" not in after["park"], out["fields"])
    check("each grows alone over the whole ridge: the two fields overlap",
          w["area"]["cells"] == e["area"]["cells"], (len(w["area"]["cells"]), len(e["area"]["cells"])))
    stands = on.spot_area_stands(m, tiles, [], w["area"])
    check("the field's stands lie in its cells, its count the blocks they reach; the square's count kept",
          stands and all(lo.in_area(w["area"], s["x"], s["y"]) for s in stands)
          and w["nodes"] == len({tuple(b) for s in stands for b in s["blocks"]}) > w["field"]["window_nodes"] > 0,
          (w["nodes"], w["field"]["window_nodes"]))
    check("a field is a spot the runner can work: area cells, forge, its square kept",
          mining_opt.check_spot(w) is None and w["field"]["window"] == win(30, 36) and w["forge"]["x"] == 30)
    check("the row keeps what it carried and its source; a seed's new row is 'fields'",
          e["faction_zone"] == "z" and {r["id"]: r["source"] for r in mem.mining_spot_rows()}
          == {"east": "runner", "park": "overseer", "west": "fields"}, mem.mining_spot_rows())
    again = mf.build(after, 0, g, stand_z, step, now=1.0)
    check("a rebuild grows from the kept squares to the same fields", again["writes"] == out["writes"],
          [(a[0], b[0]) for a, b in zip(again["writes"], out["writes"])])
    mem.close()


if __name__ == "__main__":
    for fn in (test_extent, test_build_store):
        fn()
    print(f"\n{len(FAILURES)} failure(s)" if FAILURES else "\nALL PASS")
    sys.exit(1 if FAILURES else 0)

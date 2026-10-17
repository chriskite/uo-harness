"""Tests for harness/los.py (line of sight after ServUO Map.LineOfSight).

Synthetic cases run on a fake map (no install files needed). Capture cases need the install's
map0.uoo/artdata.uoo (read-only) and are skipped without them.

Capture ground truth: session_20261012_210013 (Logan Wolf at the Mausoleum, facet 0), replayed
through world.runtime.WorldRuntime. Each client target_response on a mobile was followed either by
a bard result ("You play successfully...", "You fail to...", "Whom do you wish them to attack?") or
an unanswered "All Kill" (= seen), or by cliloc 500237 "Target cannot be seen." (= blocked). The
positions are the world model's at that moment; the replay's self z is stale (no per-step z), so z
is the standing surface on the tile nearest the model's z (pathfind Walk.objects).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import los  # noqa: E402
import pathfind  # noqa: E402
import uomap  # noqa: E402

FAILURES = []

# (time, our position, target position, seen) from session_20261012_210013.
CAPTURE = [
    ("21:21:23 Peacemaking nightstalker: 500237 (portcullis 0x6F5, Window, at 5244,662)",
     (5243, 658, 0), (5244, 663, 0), False),
    ("21:23:47 All Kill corpse purger: 500237 (stone wall 0xC8, NoShoot, at 5265,656)",
     (5266, 648, 0), (5265, 657, 0), False),
    ("21:16:51 Peacemaking spare parts at the entrance", (5603, 709, 0), (5596, 710, 0), True),
    ("21:18:19 Discordance shadow minion, up a level", (5210, 698, 0), (5210, 689, 15), True),
    ("21:21:26 Peacemaking the same nightstalker from the side", (5242, 661, 0), (5244, 664, 0), True),
    ("21:21:49 Discordance shadowguard", (5243, 660, 0), (5242, 652, 1), True),
    ("21:23:54 Discordance the same corpse purger", (5268, 661, 0), (5265, 657, 0), True),
    ("21:26:24 Discordance shadowguard on the z 25 floor", (5201, 682, 25), (5194, 682, 25), True),
    ("21:27:09 Discordance vampire countess", (5182, 678, 25), (5177, 681, 25), True),
    ("21:30:20 Discordance shadowguard", (5192, 686, 25), (5194, 681, 25), True),
]


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}{'' if cond else ' ' + detail}")
    if not cond:
        FAILURES.append(name)


# ---------------------------------------------------------------- synthetic map
GRASS = 3
WALL_NS = 1          # stone wall, Wall|Impassable|NoShoot, height 20
WALL_PLAIN = 2       # Wall|Impassable only, height 20
WINDOW = 3           # Window, height 20
LOW = 4              # NoShoot, height 5 (below the eye)
ITEM_COUNT = 16


class FakeTileData:
    item_count = ITEM_COUNT

    _ITEMS = {
        WALL_NS: (uomap.WALL | uomap.IMPASSABLE | uomap.NOSHOOT, 20),
        WALL_PLAIN: (uomap.WALL | uomap.IMPASSABLE, 20),
        WINDOW: (uomap.WINDOW, 20),
        LOW: (uomap.NOSHOOT, 5),
    }

    def item(self, g):
        flags, h = self._ITEMS.get(g, (0, 0))
        return uomap.ItemTile(g, flags, 0, 0, 0, 0, h, "fake", 0, 0)

    def land(self, tile_id):
        return uomap.LandTile(tile_id, 0, 0, "grass")


class FakeMap:
    """Flat grass at z 0 (`hills` overrides z per tile), statics from a dict."""

    def __init__(self, statics=None, hills=None):
        self.tiledata = FakeTileData()
        self._statics = statics or {}
        self._hills = hills or {}

    def land(self, x, y):
        return GRASS, self._hills.get((x, y), 0)

    def _tile_z(self, x, y):
        return self._hills.get((x, y), 0)

    def statics(self, x, y):
        return [uomap.Static(g, z, 0) for g, z in self._statics.get((x, y), ())]


def sight(statics=None, hills=None, dynamic=None):
    return los.Sight(pathfind.Walk(FakeMap(statics, hills), dynamic))


def synthetic():
    print("== synthetic ==")
    s = sight()
    check("open floor 10 tiles: visible", s.can_see((100, 100, 0), (110, 100, 0)))
    check("open floor diagonal: visible", s.can_see((100, 100, 0), (107, 106, 0)))
    check("same tile: visible", s.can_see((100, 100, 0), (100, 100, 0)))
    check("26 tiles apart: blocked (MaxLOSDistance 25)", not s.can_see((100, 100, 0), (126, 100, 0)))

    wall = {(105, y): [(WALL_NS, 0)] for y in range(90, 111)}
    s = sight(wall)
    check("through a NoShoot wall: blocked", not s.can_see((100, 100, 0), (110, 100, 0)))
    check("blocked_at is the wall tile at eye height",
          s.blocked_at((100, 100, 0), (110, 100, 0)) == (105, 100, los.EYE),
          str(s.blocked_at((100, 100, 0), (110, 100, 0))))
    check("blocked either way", not s.can_see((110, 100, 0), (100, 100, 0)))
    check("along the wall's near side: visible", s.can_see((104, 92, 0), (104, 108, 0)))
    check("over the wall from above it: visible", s.can_see((100, 100, 30), (110, 100, 30)))
    check("target standing in the wall tile: visible (end tile exemption)",
          s.can_see((100, 100, 0), (105, 100, 0)))

    s = sight({(105, 100): [(WALL_PLAIN, 0)]})
    check("Wall|Impassable without NoShoot/Window: visible", s.can_see((100, 100, 0), (110, 100, 0)))
    w = los.Sight(pathfind.Walk(FakeMap({(105, 100): [(WALL_PLAIN, 0)]}), None), walls=True)
    check("...blocked with walls (a hold's sight)", not w.can_see((100, 100, 0), (110, 100, 0)))
    check("...a low NoShoot still below the eye with walls", los.Sight(
        pathfind.Walk(FakeMap({(105, 100): [(LOW, 0)]}), None), walls=True).can_see((100, 100, 0), (110, 100, 0)))
    s = sight({(105, 100): [(WINDOW, 0)]})
    check("Window flag: blocked", not s.can_see((100, 100, 0), (110, 100, 0)))
    s = sight({(105, 100): [(LOW, 0)]})
    check("NoShoot below eye height: visible", s.can_see((100, 100, 0), (110, 100, 0)))

    s = sight(hills={(105, 100): 40, (105, 101): 40, (106, 100): 40, (106, 101): 40})
    check("a hill spanning eye height: blocked", not s.can_see((100, 100, 0), (110, 100, 0)))

    items = {}
    walk = pathfind.Walk(FakeMap(), lambda x, y: items.get((x, y), ()))
    s = los.Sight(walk)
    check("no ground item: visible", s.can_see((100, 100, 0), (110, 100, 0)))
    items[(105, 100)] = [(WALL_NS, 0)]
    walk.dynamic = lambda x, y: items.get((x, y), ())
    check("NoShoot ground item (new walk.dynamic): blocked", not s.can_see((100, 100, 0), (110, 100, 0)))

    walkers = pathfind.Walkers()
    check("sight_for: no geometry on facet 2", los.sight_for(walkers, 2) is None)
    walkers.put(0, walk)
    sf = los.sight_for(walkers, 0)
    check("sight_for: the facet's Walk, cached", sf is not None and sf.walk is walk and los.sight_for(walkers, 0) is sf)


def capture():
    print("== capture session_20261012_210013 (Mausoleum) ==")
    if not os.path.exists(os.path.join(uomap.INSTALL, "map0.uoo")):
        print("  SKIP install map files absent")
        return
    s = los.sight_for(pathfind.Walkers(), 0)
    for name, a, b, seen in CAPTURE:
        got = s.can_see(a, b)
        check(f"{name}: {'seen' if seen else 'blocked'}", got == seen, f"got {got}, blocked_at {s.blocked_at(a, b)}")


def main():
    synthetic()
    capture()
    print(f"\nlos: {'ALL PASS' if not FAILURES else f'{len(FAILURES)} FAILURES'}")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())

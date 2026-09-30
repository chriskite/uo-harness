"""Tests for harness/uomap.py: Outlands .uoo map + tiledata reader.

Ground truth is server-confirmed capture evidence (logs/session_*.jsonl) and
the committed walk memory (harness/data/walkmem.json):
  * C2S 0x6C target responses (tree at 1898,2622 z10 graphic 0x0CE0),
  * S2C self positions with z: 0x1B login, 0x20 draw-player, 0x77 self move,
    0x21 move-reject (15 B: seq u8, x u32, y u32, dir u8, z i32 -- z is i32be
    at offset 11, see docs/MAP.md), with the facet from 0xBF sub 0x08,
  * S2C 0xF3 world items (the Shelter inn doors).
The install dir is opened read-only (mmap ACCESS_READ). Spec: docs/MAP.md.

Run: python harness/test_uomap.py   (read-only, a few seconds)
"""
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uomap
from uomap import BRIDGE, DOOR, IMPASSABLE, SURFACE, UoMap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS = os.path.join(ROOT, "logs")
DEMO = "20260929_204225"          # tree demo + Shelter inn upstairs + rental
TREE_TXT = os.path.join(uomap.INSTALL, "ClassicUO", "Data", "Client", "tree.txt")
PERSON_HEIGHT = 16                # upstream Constants.DEFAULT_CHARACTER_HEIGHT
FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(name)


def eq(name, got, want):
    check(name, got == want, f"(got {got!r}, want {want!r})")


# -- capture evidence --------------------------------------------------------

def _be(fmt, b, o):
    return struct.unpack_from(">" + fmt, b, o)[0]


def scan_session(tag):
    """(positions, targets, items) from one session's jsonl.

    positions: (kind, facet, x, y, z) for self; kind '1B'/'20'/'77'/'21'.
    Positions logged before the session's first 0xBF/0x08 get that facet.
    targets: (x, y, z, graphic) from C2S 0x6C. items: (graphic, x, y, z)
    from S2C 0xF3."""
    positions, targets, items, pending = [], [], [], []
    me, facet = None, None
    with open(os.path.join(LOGS, f"session_{tag}.jsonl"), encoding="utf-8") as fh:
        for line in fh:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            h = ev.get("hex")
            if not h or ev.get("len") != len(h) // 2:
                continue            # jsonl truncates long packets
            b = bytes.fromhex(h)
            pid, d = b[0], ev.get("dir")
            pos = None
            if d == "s2c":
                if pid == 0x1B and len(b) >= 26:
                    me = _be("I", b, 1)
                    pos = ("1B", _be("I", b, 13), _be("I", b, 17), _be("i", b, 21))
                elif pid == 0x20 and len(b) >= 28 and _be("I", b, 1) == me:
                    pos = ("20", _be("I", b, 13), _be("I", b, 17), _be("i", b, 24))
                elif pid == 0x77 and len(b) == 18 and _be("I", b, 1) == me:
                    pos = ("77", _be("I", b, 5), _be("I", b, 9), _be("i", b, 13))
                elif pid == 0x21 and len(b) == 15:
                    pos = ("21", _be("I", b, 2), _be("I", b, 6), _be("i", b, 11))
                elif pid == 0xBF and len(b) >= 6 and b[3:5] == b"\x00\x08":
                    facet = b[5]
                    positions += [(k, facet, x, y, z) for k, x, y, z in pending]
                    pending = []
                elif pid == 0xF3 and len(b) >= 30:
                    items.append((_be("I", b, 8), _be("I", b, 17),
                                  _be("I", b, 21), _be("i", b, 25)))
            elif d == "c2s" and pid == 0x6C and len(b) == 27:
                targets.append((_be("I", b, 11), _be("I", b, 15),
                                _be("i", b, 19), _be("I", b, 23)))
            if pos:
                if facet is None:
                    pending.append(pos)
                else:
                    positions.append((pos[0], facet) + pos[1:])
    return positions, targets, items


def all_sessions():
    return sorted(f[len("session_"):-len(".jsonl")] for f in os.listdir(LOGS)
                  if f.startswith("session_") and f.endswith(".jsonl"))


def tree_graphics():
    out = set()
    with open(TREE_TXT, encoding="latin-1") as fh:
        for line in fh:
            key = line.split("=", 1)[0].strip()
            if key.isdigit():
                out.add(int(key))
    return out


# -- walkability helpers (simplified; the real rules are Pathfinder's) -------

def stand_heights(m, x, y):
    """Candidate standing z values on (x, y): walkable land at its stretched
    average z, plus every Surface/Bridge static top (z + calc_height)."""
    td = m.tiledata
    tile_id, _ = m.land(x, y)
    out = []
    if not td.land(tile_id).impassable:
        out.append(m.land_stretch(x, y).avg_z)
    for s in m.statics(x, y):
        it = td.item(s.graphic)
        if it.flags & (SURFACE | BRIDGE):
            out.append(s.z + it.calc_height)
    return out


def blocked_at(m, x, y, z):
    """An Impassable, non-surface static overlaps a body standing at z."""
    td = m.tiledata
    for s in m.statics(x, y):
        it = td.item(s.graphic)
        if it.flags & IMPASSABLE and not it.flags & (SURFACE | BRIDGE):
            if s.z < z + PERSON_HEIGHT and z < s.z + max(it.height, 1):
                return True
    return False


# ---------------------------------------------------------------------------

def test_headers(maps):
    print("headers")
    m0 = maps[0]
    eq("map0 is 1344x768 blocks", (m0.blocks_w, m0.blocks_h), (1344, 768))
    # facet00.mul (harness/facet.py) is the same facet at 1 px/tile
    eq("map0 tiles match facet00 picture", (m0.width, m0.height), (10752, 6144))
    eq("map1 is 7168x4096", (maps[1].width, maps[1].height), (7168, 4096))
    td = m0.tiledata
    eq("landdata has 0x4000 records", td.land_count, 0x4000)
    eq("artdata has 0x1167C records", td.item_count, 0x1167C)
    check("map0 has static blocks", m0.static_block_count() > 100000)
    eq("water land 0xA8 is Impassable|Wet", td.land(0xA8).flags & 0xFF, 0xC0)
    eq("water land 0xA8 name", td.land(0xA8).name, "water")
    check("out of bounds land is None", m0.land(m0.width, 0) is None
          and m0.land(-1, 5) is None)


def test_tree(m, targets):
    print("tree target (capture 0x6C, session %s)" % DEMO)
    trees = tree_graphics()
    hits = [t for t in targets if (t[0], t[1]) == (1898, 2622) and t[3] == 0x0CE0]
    check("capture has the 0x0CE0 target at 1898,2622", bool(hits))
    tx, ty, tz, tg = hits[0]
    eq("target z from capture", tz, 10)
    sts = [s for s in m.statics(tx, ty) if s.graphic == tg]
    check("map has static 0x0CE0 at 1898,2622", bool(sts), m.statics(tx, ty))
    if not sts:
        return
    eq("static z equals the target z", sts[0].z, tz)
    it = m.tiledata.item(tg)
    check("tiledata name is a tree", it.name.endswith("tree"), it.name)
    check("0x0CE0 listed in tree.txt", tg in trees)
    check("tree is Impassable", it.impassable)
    check("tree is not a Surface", not it.surface)
    eq("tree height", it.height, 20)
    eq("tree weight 255 (not movable)", it.weight, 255)
    # every other tree target in the demo: a tree.txt static at the target z
    others = {(x, y, z) for x, y, z, g in targets
              if (x, y) != (1898, 2622) and (g in trees or g == 0)
              and 1800 < x < 2100}
    for x, y, z in sorted(others):
        ok = any(s.graphic in trees and s.z == z for s in m.statics(x, y))
        check(f"tree.txt static at target ({x}, {y}, z{z})", ok, m.statics(x, y))


def test_login_spot(m, positions):
    print("login spot 0x7AB,0xA25")
    zs = {p[4] for p in positions if (p[1], p[2], p[3]) == (0, 0x7AB, 0xA25)}
    eq("captures put the player there at z 0", zs, {0})
    tile_id, z = m.land(0x7AB, 0xA25)
    check("land is walkable", not m.tiledata.land(tile_id).impassable)
    eq("land average z", m.land_stretch(0x7AB, 0xA25).avg_z, 0)
    check("no impassable static at z 0", not blocked_at(m, 0x7AB, 0xA25, 0))


def test_inn(m, positions, items):
    print("Shelter inn upstairs 1937,2583")
    zs = {p[4] for p in positions if (p[1], p[2], p[3]) == (0, 1937, 2583)}
    eq("capture z at 1937,2583 (0x21, i32 z)", zs, {20})
    td = m.tiledata
    floors = [s for s in m.statics(1937, 2583) if td.item(s.graphic).surface]
    tops = sorted(s.z + td.item(s.graphic).calc_height for s in floors)
    check("an upstairs surface tops out at z 20", 20 in tops, tops)
    check("a ground-floor surface tops out at z <= 1", any(t <= 1 for t in tops), tops)
    eq("land under the inn", m.land(1937, 2583)[1], 0)
    check("z 20 is standable and unblocked",
          20 in stand_heights(m, 1937, 2583) and not blocked_at(m, 1937, 2583, 20))
    check("ground floor z 1 is standable", 1 in stand_heights(m, 1937, 2583))
    # the inn doors are dynamic 0xF3 items, not statics
    for (x, y, z) in [(1933, 2589, 20), (1935, 2588, 0)]:
        caught = [g for g, ix, iy, iz in items if (ix, iy, iz) == (x, y, z)]
        check(f"capture door item at ({x}, {y}, z{z}) has the DOOR flag",
              bool(caught) and all(td.item(g).door for g in caught), caught)
        check(f"no door static at ({x}, {y})",
              not any(td.item(s.graphic).flags & DOOR for s in m.statics(x, y)))


def test_walkmem(m, positions):
    print("walk memory tiles")
    with open(os.path.join(ROOT, "harness", "data", "walkmem.json")) as fh:
        tiles = [tuple(t) for t in json.load(fh)["tiles"]]
    # server-authoritative absolute positions only (0x21 z anomalies, MAP.md)
    known = {}
    for kind, facet, x, y, z in positions:
        if facet == 0 and kind in ("1B", "20", "77"):
            known.setdefault((x, y), set()).add(z)
    facet0 = [t for t in tiles if t not in RENTAL_TILES]
    bad = [t for t in facet0
           if not any(not blocked_at(m, t[0], t[1], z) for z in stand_heights(m, *t))]
    check(f"all {len(facet0)} facet-0 walked tiles have an unblocked standing z",
          not bad, bad[:10])
    with_z = [(t, known[t]) for t in facet0 if t in known]
    mism = [(t, zs, stand_heights(m, *t)) for t, zs in with_z
            if not zs <= set(z for z in stand_heights(m, *t)
                             if not blocked_at(m, t[0], t[1], z))]
    check(f"{len(with_z)} walked tiles with capture z: z is a free standing height",
          len(with_z) >= 10 and not mism, mism[:5])


# Walk-memory tiles from the rental-room walk (session DEMO; facet 3 per
# 0xBF/0x08, 0x77 at 39,65 z1 and 0x21 at 39,68 / 40,67). walkmem.json has no
# facet, so these are excluded from the facet-0 checks.
RENTAL_TILES = {(39, 65), (39, 68), (40, 66), (40, 67)}


def test_self_positions(maps, positions):
    print("self positions with z (0x1B/0x20/0x77)")
    pts = {(f, x, y, z) for k, f, x, y, z in positions if k in ("1B", "20", "77")}
    facet0 = sorted(p for p in pts if p[0] == 0)
    m = maps[0]
    bad = [p for p in facet0 if p[3] not in stand_heights(m, p[1], p[2])]
    check(f"{len(facet0)} facet-0 positions sit on land avg z or a surface top",
          len(facet0) >= 15 and not bad, bad)
    check("an upstairs (z 20) position is among them",
          any(p[3] == 20 for p in facet0))
    check("a stretched-land position (1898,2621 z10, raw land z 11)",
          (0, 1898, 2621, 10) in pts and m.land(1898, 2621)[1] == 11
          and m.land_stretch(1898, 2621).avg_z == 10)
    # rental rooms: facet 3 per 0xBF/0x08; the file holds no statics and only
    # impassable NoName land there, so the room is not in the static files
    rental = sorted(p for p in pts if p[0] == 3)
    check("capture has rental-room positions on facet 3", bool(rental), pts)
    m3 = maps[3]
    eq("map3 has no static blocks", m3.static_block_count(), 0)
    check("rental tiles are impassable 0x244 land in map3",
          all(m3.land(x, y)[0] == 0x244 and m3.tiledata.land(0x244).impassable
              for _, x, y, _ in rental))


def main():
    maps = {i: UoMap(i) for i in (0, 1, 3)}
    positions, targets, items = [], [], []
    for tag in all_sessions():
        p, t, it = scan_session(tag)
        positions += p
        if tag == DEMO:
            targets, items = t, it
    test_headers(maps)
    test_tree(maps[0], targets)
    test_login_spot(maps[0], positions)
    test_inn(maps[0], positions, items)
    test_walkmem(maps[0], positions)
    test_self_positions(maps, positions)
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)}: {FAILURES}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

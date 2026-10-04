"""Tests for harness/uomap.py: Outlands .uoo map + tiledata reader.

Ground truth is server-confirmed capture evidence (logs/session_*.jsonl) and
per-facet walk memory built from the captures (walk_memories: the proxy's step
rows on the facet they were logged on, else the raw-pair reconstruction):
  * C2S 0x6C target responses (tree at 1898,2622 z10 graphic 0x0CE0),
  * S2C self positions with z: 0x1B login, 0x20 draw-player, 0x77 self move,
    0x21 move-reject (15 B: seq u8, x u32, y u32, dir u8, z i32 -- z is i32be
    at offset 11, see docs/MAP.md), with the facet from 0xBF sub 0x08,
  * S2C 0xF3 world items (the Shelter inn doors; ground items and house multis
    as extra standing surfaces).
The install dir is opened read-only (mmap ACCESS_READ). Spec: docs/MAP.md.

Run: python harness/test_uomap.py   (read-only, a few seconds)
"""
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nav
import uomap
from uo.s2c import PRELUDE_LEN, S2CStream, prelude_keys
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
    """(positions, targets, items, steps) from one session's jsonl.

    positions: (kind, facet, x, y, z, held) for self; kind '1B'/'20'/'77'/'21';
    held: reported on the session's 0x1B login tile before the player moved
    off it (no step row, no self position elsewhere since the 0x1B).
    targets: (x, y, z, graphic) from C2S 0x6C. items: (facet, data_type,
    graphic, x, y, z) from S2C 0xF3 (data_type 2 = a multi, a house).
    steps: (facet, row) for the proxy's `step` / `blocked` rows.
    Facet: the latest 0xBF/0x08 before the row; rows logged before the
    session's first 0xBF/0x08 get that facet."""
    positions, targets, items, steps = [], [], [], []
    pending = []                    # (list, row) waiting for the first facet
    me, facet, login = None, None, None

    def put(dest, row, at=0):       # the facet goes in at row index `at`
        if facet is None:
            pending.append((dest, row, at))
        else:
            dest.append(row[:at] + (facet,) + row[at:])

    with open(os.path.join(LOGS, f"session_{tag}.jsonl"), encoding="utf-8") as fh:
        for line in fh:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("ev") in ("step", "blocked"):
                if ev["ev"] == "step":
                    login = None
                put(steps, (ev,))
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
                    for dest, row, at in pending:
                        dest.append(row[:at] + (facet,) + row[at:])
                    pending = []
                elif pid == 0xF3 and len(b) >= 30:
                    put(items, (b[3], _be("I", b, 8), _be("I", b, 17),
                                _be("I", b, 21), _be("i", b, 25)))
            elif d == "c2s" and pid == 0x6C and len(b) == 27:
                targets.append((_be("I", b, 11), _be("I", b, 15),
                                _be("i", b, 19), _be("I", b, 23)))
            if pos:
                if pos[0] == "1B":
                    login = pos[1:3]
                elif pos[1:3] != login:
                    login = None
                put(positions, (pos[0], pos[1], pos[2], pos[3], pos[1:3] == login), at=1)
    return positions, targets, items, steps


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


def raw_facets(s2c_raw):
    """Facets (0xBF/0x08) in a raw S2C capture's decoded stream."""
    try:
        s2c_key, _ = prelude_keys(s2c_raw[:PRELUDE_LEN])
    except ValueError:
        return set()
    return {p[5] for _, p in S2CStream(s2c_key).feed(s2c_raw[PRELUDE_LEN:])
            if len(p) >= 6 and p[0] == 0xBF and p[3:5] == b"\x00\x08"}


def walk_memories(scans):
    """({facet: nav.WalkMemory}, skipped tags): the server-confirmed moves of
    the captures, per facet. A session with proxy `step` rows gives those, each
    on the facet it was logged on (scan_session). Older sessions (no step rows)
    give their raw-pair reconstruction (nav.reconstruct_session) on the one
    facet their S2C stream shows (0 if none, like memory.ingest); one showing
    two facets can't be split per move and is skipped."""
    mems, skipped = {}, []
    for tag, (_, _, _, steps) in sorted(scans.items()):
        if steps:
            for f, row in steps:
                nav.apply_log_row(mems.setdefault(f, nav.WalkMemory()), row)
            continue
        base = os.path.join(LOGS, f"session_{tag}")
        try:
            if os.path.getsize(base + ".c2s.raw") <= nav.CLIENT_PREAMBLE_LEN:
                continue            # lost C2S buffer: confirms can't be matched to walks
            with open(base + ".c2s.raw", "rb") as fh:
                c2s = fh.read()
            with open(base + ".s2c.raw", "rb") as fh:
                s2c = fh.read()
        except OSError:
            continue
        facets = raw_facets(s2c)
        if len(facets) > 1:
            skipped.append(tag)
            continue
        nav.reconstruct_session(c2s, s2c, mems.setdefault(facets.pop() if facets else 0,
                                                          nav.WalkMemory()))
    return mems, skipped


def dynamic_index(m, items, facet):
    """{(x, y): {(graphic, z)}}: the ground items and house pieces any capture
    showed on `facet`. A multi (0xF3 data_type 2) stands for its multi.mul
    pieces around its tile, as the client places them (uomap.multi_components)."""
    out = {}
    for f, data_type, g, x, y, z in items:
        if f != facet:
            continue
        if data_type == 2:
            for dx, dy, dz, piece in uomap.multi_components(g, m.root):
                out.setdefault((x + dx, y + dy), set()).add((piece, z + dz))
        elif data_type == 0:
            out.setdefault((x, y), set()).add((g, z))
    return out


# -- walkability helpers (simplified; the real rules are Pathfinder's) -------

def stand_heights(m, x, y, dyn=None):
    """Candidate standing z values on (x, y): walkable land at its stretched
    average z, plus every Surface/Bridge static top (z + calc_height), plus the
    same for dynamic objects on the tile (dyn: dynamic_index)."""
    td = m.tiledata
    tile_id, _ = m.land(x, y)
    out = []
    if not td.land(tile_id).impassable:
        out.append(m.land_stretch(x, y).avg_z)
    objs = [(s.graphic, s.z) for s in m.statics(x, y)] + sorted((dyn or {}).get((x, y), ()))
    for g, z in objs:
        it = td.item(g)
        if it is not None and it.flags & (SURFACE | BRIDGE):
            out.append(z + it.calc_height)
    return out


def blocked_at(m, x, y, z):
    """An Impassable, non-surface static overlaps a body standing at z. A
    zero-height one occupies no z range: the client's CalculateNewZ (pathfind
    Walk.new_z) lets a body stand on the surface it sits on, and the server
    confirmed such steps (fence 0xB2D7 at 1919,2608-2609 z0 in 20261001_191355;
    tiledata-height-0 lamp posts on raised walkways, e.g. 0xC401 at 2042,2212
    z20, in 20260930_123206 and later)."""
    td = m.tiledata
    for s in m.statics(x, y):
        it = td.item(s.graphic)
        if it.flags & IMPASSABLE and not it.flags & (SURFACE | BRIDGE):
            if s.z < z + PERSON_HEIGHT and z < s.z + it.height:
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
        caught = [g for _f, _dt, g, ix, iy, iz in items if (ix, iy, iz) == (x, y, z)]
        check(f"capture door item at ({x}, {y}, z{z}) has the DOOR flag",
              bool(caught) and all(td.item(g).door for g in caught), caught)
        check(f"no door static at ({x}, {y})",
              not any(td.item(s.graphic).flags & DOOR for s in m.statics(x, y)))


# Positions the walk rules don't model, each with its evidence:
# - The character-creation spawn (1955,2625) z 6: Hackworth's first capture
#   (0x20F127, 20260930_223009) and Shackleworth's creation (0x3D701F, C2S 0x00
#   create in 20261003_113952) both log in there, and Hackworth again in
#   20261001_191355 (his 223009 positions never left it). The server sets that
#   z: the tile's plank (0x07CD, z 6, height 1) tops out at 7, where the client
#   would stand (pathfind Walk.new_z). Only reports held on the login tile are
#   left out (scan_session `held`); a walk onto the tile is still checked.
CREATION_SPAWN = (0, 1955, 2625, 6)
# - (1615,1519), facet 0 (TestWorth on the Test Shard, 20260930_123206,
#   t 1790802889.636): the server confirmed walk seq 0x76 SW from (1616,1518) and
#   the N step after it to (1615,1518). The client's statics wall that tile off
#   (stone wall 0x00DE z 22 + iron fence 0x081F z 26 along y 1519, x 1613-1616) and
#   pathfind refuses the step, yet the position is the server's: the wooden pole at
#   (1597,1521) entered view range (18 tiles) on that confirm (x 1615); the carpet
#   rack at (1627,1536) came back into range at (1612,1518) four moves later, which
#   needs that step's y + 1; and the chain dead-reckoned from the deny at (1637,1458)
#   ends exactly on the next server deny (1603,1521), 125 moves later. So the
#   server's map has no wall there [INFERENCE: it differs from the client's statics].
SERVER_ONLY_FLOOR = {(0, 1615, 1519)}


def spawn_held(p):
    """A self position the creation spawn placed (CREATION_SPAWN)."""
    return p[5] and p[1:5] == CREATION_SPAWN


def test_walkmem(m, positions, mems, skipped, dyn):
    print("walk memory tiles (per facet: proxy step rows / raw-pair reconstruction)")
    if skipped:
        print(f"  note: sessions without step rows on 2+ facets left out: {skipped}")
    tiles = sorted(mems.get(0, nav.WalkMemory()).tiles)
    # server-authoritative absolute positions only (0x21 z anomalies, MAP.md)
    known = {}
    for p in positions:
        kind, facet, x, y, z, _held = p
        if facet == 0 and kind in ("1B", "20", "77") and not spawn_held(p):
            known.setdefault((x, y), set()).add(z)
    facet0 = [t for t in tiles if (0,) + t not in SERVER_ONLY_FLOOR]
    check("the server-only floor tiles are among the walked ones",
          all(t[1:] in mems.get(t[0], nav.WalkMemory()).tiles for t in SERVER_ONLY_FLOOR))

    def free(t):
        return [z for z in stand_heights(m, *t, dyn=dyn) if not blocked_at(m, t[0], t[1], z)]
    bad = [t for t in facet0 if not free(t)]
    check(f"all {len(facet0)} facet-0 walked tiles have an unblocked standing z",
          len(facet0) >= 1000 and not bad, bad[:10])
    with_z = [(t, known[t]) for t in facet0 if t in known]
    mism = [(t, zs, stand_heights(m, *t, dyn=dyn)) for t, zs in with_z if not zs <= set(free(t))]
    check(f"{len(with_z)} walked tiles with capture z: z is a free standing height",
          len(with_z) >= 10 and not mism, mism[:5])


def test_self_positions(maps, positions, dyn):
    print("self positions with z (0x1B/0x20/0x77)")
    pts = {p[1:5] for p in positions if p[0] in ("1B", "20", "77") and not spawn_held(p)}
    facet0 = sorted(p for p in pts if p[0] == 0)
    m = maps[0]
    bad = [p for p in facet0 if p[3] not in stand_heights(m, p[1], p[2], dyn=dyn)]
    check(f"{len(facet0)} facet-0 positions sit on land avg z or a surface top",
          len(facet0) >= 15 and not bad, bad)
    check("an upstairs (z 20) position is among them",
          any(p[3] == 20 for p in facet0))
    check("a stretched-land position (1898,2621 z10, raw land z 11)",
          (0, 1898, 2621, 10) in pts and m.land(1898, 2621)[1] == 11
          and m.land_stretch(1898, 2621).avg_z == 10)
    check("a position on a house piece only (Cambria library, 1706,3180 z2)",
          (0, 1706, 3180, 2) in pts and 2 not in stand_heights(m, 1706, 3180)
          and 2 in stand_heights(m, 1706, 3180, dyn=dyn))
    # rental rooms: facet 3 per 0xBF/0x08; the file holds no statics and only
    # impassable NoName land there, so the room is not in the static files
    rental = sorted(p for p in pts if p[0] == 3)
    check("capture has rental-room positions on facet 3", bool(rental), pts)
    m3 = maps[3]
    eq("map3 has no static blocks", m3.static_block_count(), 0)
    check("rental tiles are impassable 0x244 land in map3",
          all(m3.land(x, y)[0] == 0x244 and m3.tiledata.land(0x244).impassable
              for _, x, y, _ in rental))


def test_display_name():
    print("tiledata name plural markup (client StringHelper.GetPluralAdjustedString)")
    from uomap import display_name as dn
    eq("%s% plural", dn("amethyst%s%", True), "amethysts")
    eq("%s% singular", dn("amethyst%s%", False), "amethyst")
    eq("%ies/y% plural", dn("rub%ies/y%", True), "rubies")
    eq("%ies/y% singular", dn("rub%ies/y%", False), "ruby")
    eq("%ves/f% singular", dn("bread loa%ves/f%", False), "bread loaf")
    eq("text after the marker is kept", dn("slab%s% of bacon", True), "slabs of bacon")
    eq("stray % is dropped", dn("Executioner's Cap%", False), "Executioner's Cap")
    eq("no marker is untouched", dn("Garlic", True), "Garlic")
    td = uomap.tiledata()
    left = [g for g in range(td.item_count) if "%" in dn(td.item(g).name or "", True) or "%" in dn(td.item(g).name or "", False)]
    check("no real item name keeps a % after resolving", not left, str([td.item(g).name for g in left[:5]]))


def main():
    maps = {i: UoMap(i) for i in (0, 1, 3)}
    scans = {tag: scan_session(tag) for tag in all_sessions()}
    positions = [p for s in scans.values() for p in s[0]]
    targets, items = scans[DEMO][1], scans[DEMO][2]
    dyn = dynamic_index(maps[0], [it for s in scans.values() for it in s[2]], 0)
    mems, skipped = walk_memories(scans)
    test_display_name()
    test_headers(maps)
    test_tree(maps[0], targets)
    test_login_spot(maps[0], positions)
    test_inn(maps[0], positions, items)
    test_walkmem(maps[0], positions, mems, skipped, dyn)
    test_self_positions(maps, positions, dyn)
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)}: {FAILURES}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

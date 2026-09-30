"""Read-only reader for Outlands' on-disk map and tiledata (.uoo) files.

Format spec, evidence and open questions: docs/MAP.md.

    m = UoMap(0)                    # map0.uoo (Outlands' main facet)
    m.land(x, y)                    # -> (tile_id, z)            raw land cell
    m.land_stretch(x, y)            # -> LandStretch(z, avg_z, min_z, max_z, textured)
    m.statics(x, y)                 # -> [Static(graphic, z, hue), ...]
    m.iter_statics(x0, y0, x1, y1)  # -> (x, y, Static) over a rectangle
    td = m.tiledata                 # TileData (landdata.uoo + artdata.uoo)
    td.land(tile_id)                # -> LandTile(id, flags, tex_id, name)
    td.item(graphic)                # -> ItemTile(id, flags, weight, layer, count, anim_id, height, name,
                                    #             gump_male, gump_female)

Everything is opened read-only via mmap(ACCESS_READ); nothing is loaded
wholesale. land() is one struct.unpack_from on the mapping (no cache
needed); statics are decoded per 8x8 block and kept in a bounded LRU.

CLI:  python harness/uomap.py tile X Y [--map N]
"""
import argparse
import collections
import enum
import mmap
import os
import struct
import sys

INSTALL = "C:/Program Files (x86)/Ultima Online Outlands"

MAP_MAGIC = 0xE532363F   # UOO.TerrainFile.OpenRead @ 0x141062bd0 (-0x1acdc9c1)
DATA_MAGIC = 0x1E7FAB6D  # landdata/artdata/art/texmaps .uoo (UOOFile.ReadIndex)
MAP_HEADER = 0x12        # _terrainStartOffset: land cells start here
LAND_CELL = 6            # u32 tile id, i16 z
STATIC_REC = 9           # u8 cell delta, u32 graphic, u16 hue, i16 z
NO_STATICS = 0x7FFFFFFFFFFFFFFF
DATA_HEADER = 12         # u32 magic, u32 version, u32 count
LAND_REC = 48
ITEM_REC = 72

_LAND = struct.Struct("<Ih")
_I64 = struct.Struct("<q")
_U32 = struct.Struct("<I")
_STATIC = struct.Struct("<BIHh")
_LAND_TD = struct.Struct("<QI")
_ITEM_TD = struct.Struct("<QBBxxiiiiHHI")


class TileFlag(enum.IntFlag):
    """u64 tiledata flags. Bits 0-31 and MultiMovable equal upstream
    ClassicUO.Assets TileFlag; NoClip, IgnoreHeightNoDraw, NoDraw and
    HuedLight are Outlands-only bits (UOO.ArtTile getters, docs/MAP.md)."""
    BACKGROUND = 0x1
    WEAPON = 0x2
    TRANSPARENT = 0x4
    TRANSLUCENT = 0x8
    WALL = 0x10
    DAMAGING = 0x20
    IMPASSABLE = 0x40
    WET = 0x80
    UNKNOWN1 = 0x100
    SURFACE = 0x200
    BRIDGE = 0x400
    GENERIC = 0x800            # stackable
    WINDOW = 0x1000
    NOSHOOT = 0x2000
    ARTICLE_A = 0x4000
    ARTICLE_AN = 0x8000
    INTERNAL = 0x10000
    FOLIAGE = 0x20000
    PARTIAL_HUE = 0x40000
    NOHOUSE = 0x80000
    MAP = 0x100000
    CONTAINER = 0x200000
    WEARABLE = 0x400000
    LIGHT_SOURCE = 0x800000
    ANIMATION = 0x1000000
    NO_DIAGONAL = 0x2000000    # upstream "HoverOver"/NoDiagonal
    UNKNOWN2 = 0x4000000
    ARMOR = 0x8000000
    ROOF = 0x10000000
    DOOR = 0x20000000
    STAIR_BACK = 0x40000000
    STAIR_RIGHT = 0x80000000
    ALPHA_BLEND = 0x100000000
    USE_NEW_ART = 0x200000000
    ART_USED = 0x400000000
    NO_CLIP = 0x800000000              # Outlands (ArtTile.get_IsNoClip)
    NO_SHADOW = 0x1000000000
    PIXEL_BLEED = 0x2000000000
    PLAY_ANIM_ONCE = 0x4000000000
    MULTI_MOVABLE = 0x10000000000
    IGNORE_HEIGHT_NO_DRAW = 0x20000000000   # Outlands
    NO_DRAW = 0x2000000000000               # Outlands
    HUED_LIGHT = 0x4000000000000            # Outlands


# Plain-int constants for hot paths (IntFlag ops are slow).
BACKGROUND = int(TileFlag.BACKGROUND)
WALL = int(TileFlag.WALL)
IMPASSABLE = int(TileFlag.IMPASSABLE)
WET = int(TileFlag.WET)
SURFACE = int(TileFlag.SURFACE)
BRIDGE = int(TileFlag.BRIDGE)
WINDOW = int(TileFlag.WINDOW)
NOSHOOT = int(TileFlag.NOSHOOT)
INTERNAL = int(TileFlag.INTERNAL)
FOLIAGE = int(TileFlag.FOLIAGE)
NO_DIAGONAL = int(TileFlag.NO_DIAGONAL)
ROOF = int(TileFlag.ROOF)
DOOR = int(TileFlag.DOOR)


def flag_names(flags):
    """'IMPASSABLE|SURFACE' style text for a raw u64."""
    names = [f.name for f in TileFlag if flags & f.value]
    return "|".join(names) if names else "-"


class LandTile(collections.namedtuple("LandTile", "id flags tex_id name")):
    __slots__ = ()
    impassable = property(lambda s: bool(s.flags & IMPASSABLE))
    wet = property(lambda s: bool(s.flags & WET))
    no_diagonal = property(lambda s: bool(s.flags & NO_DIAGONAL))


class ItemTile(collections.namedtuple(
        "ItemTile", "id flags weight layer count anim_id height name gump_male gump_female")):
    __slots__ = ()
    impassable = property(lambda s: bool(s.flags & IMPASSABLE))
    surface = property(lambda s: bool(s.flags & SURFACE))
    bridge = property(lambda s: bool(s.flags & BRIDGE))
    wet = property(lambda s: bool(s.flags & WET))
    wall = property(lambda s: bool(s.flags & WALL))
    door = property(lambda s: bool(s.flags & DOOR))
    foliage = property(lambda s: bool(s.flags & FOLIAGE))
    roof = property(lambda s: bool(s.flags & ROOF))
    internal = property(lambda s: bool(s.flags & INTERNAL))
    no_diagonal = property(lambda s: bool(s.flags & NO_DIAGONAL))

    @property
    def calc_height(self):
        """Walk height: half for Bridge tiles (ArtTile.get_CalcHeight)."""
        return self.height // 2 if self.flags & BRIDGE else self.height


Static = collections.namedtuple("Static", "graphic z hue")
LandStretch = collections.namedtuple("LandStretch",
                                     "z avg_z min_z max_z textured")


def _open_ro(path):
    with open(path, "rb") as fh:
        return mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)


def _cstr(raw):
    return raw.split(b"\0", 1)[0].decode("latin-1")


class TileData:
    """landdata.uoo (48-byte land records) + artdata.uoo (72-byte item
    records) + the texmaps.uoo id set (needed for land stretching)."""

    def __init__(self, root=INSTALL):
        self.root = root
        self._land = _open_ro(os.path.join(root, "landdata.uoo"))
        self._art = _open_ro(os.path.join(root, "artdata.uoo"))
        self.land_count = self._check(self._land, LAND_REC, "landdata.uoo")
        self.item_count = self._check(self._art, ITEM_REC, "artdata.uoo")
        self._land_cache = {}
        self._item_cache = {}
        self._texmaps = None

    @staticmethod
    def _check(mm, rec, name):
        magic, _version, count = struct.unpack_from("<III", mm, 0)
        if magic != DATA_MAGIC or DATA_HEADER + count * rec != len(mm):
            raise ValueError(f"{name}: unexpected header/size")
        return count

    def land(self, tile_id):
        t = self._land_cache.get(tile_id)
        if t is None:
            if not 0 <= tile_id < self.land_count:
                raise IndexError(f"land id {tile_id:#x} out of range")
            o = DATA_HEADER + tile_id * LAND_REC
            flags, tex = _LAND_TD.unpack_from(self._land, o)
            t = LandTile(tile_id, flags, tex, _cstr(self._land[o + 12:o + 48]))
            self._land_cache[tile_id] = t
        return t

    def item(self, graphic):
        t = self._item_cache.get(graphic)
        if t is None:
            if not 0 <= graphic < self.item_count:
                raise IndexError(f"item graphic {graphic:#x} out of range")
            o = DATA_HEADER + graphic * ITEM_REC
            (flags, weight, layer, count, anim, gump_m, gump_f, _u1, _u2,
             height) = _ITEM_TD.unpack_from(self._art, o)
            t = ItemTile(graphic, flags, weight, layer, count, anim, height,
                         _cstr(self._art[o + 36:o + 72]), gump_m, gump_f)
            self._item_cache[graphic] = t
        return t

    def has_texmap(self, tex_id):
        """True if texmaps.uoo holds an entry for tex_id (Land.ApplyStretch
        only stretches land whose TexID has a texture)."""
        if self._texmaps is None:
            ids = set()
            mm = _open_ro(os.path.join(self.root, "texmaps.uoo"))
            try:
                pos = 8
                while pos + 8 <= len(mm):
                    eid, length = struct.unpack_from("<Ii", mm, pos)
                    if length == 0:
                        break
                    ids.add(eid)
                    pos += 8 + length
            finally:
                mm.close()
            self._texmaps = frozenset(ids)
        return tex_id in self._texmaps


_TILEDATA = {}


def tiledata(root=INSTALL):
    """Shared TileData per install root."""
    td = _TILEDATA.get(root)
    if td is None:
        td = _TILEDATA[root] = TileData(root)
    return td


_SKILL_NAMES = {}


def skill_names(root=INSTALL) -> list[str]:
    """Skill names by id, as the client loads them (ClassicUO SkillsLoader.cs:31-55):
    Skills.idx = 12-byte entries (offset i32, length i32, extra i32); each
    skills.mul record is a u8 has-action flag then the ASCII name (length - 1
    bytes, NUL-terminated). Entries with length <= 0 are skipped, and ids count
    the valid ones in order."""
    names = _SKILL_NAMES.get(root)
    if names is None:
        with open(os.path.join(root, "Skills.idx"), "rb") as f:
            idx = f.read()
        with open(os.path.join(root, "skills.mul"), "rb") as f:
            data = f.read()
        names = []
        for i in range(0, len(idx) - 11, 12):
            off, length, _extra = struct.unpack_from("<iii", idx, i)
            if length <= 0 or off < 0 or off + length > len(data):
                continue
            names.append(data[off + 1:off + length].split(b"\0", 1)[0].decode("ascii", "replace"))
        _SKILL_NAMES[root] = names
    return names


class UoMap:
    """One mapN.uoo facet. Coordinates outside the map return None."""

    STATIC_CACHE_BLOCKS = 4096

    def __init__(self, index=0, root=INSTALL):
        self.index = index
        self.root = root
        self.path = os.path.join(root, f"map{index}.uoo")
        self._mm = _open_ro(self.path)
        magic, version, cw, ch, bw, bh = struct.unpack_from("<IIBBII", self._mm, 0)
        if magic != MAP_MAGIC or version != 1:
            raise ValueError(f"{self.path}: bad header {magic:#x} v{version}")
        if (cw, ch) != (8, 8):
            raise ValueError(f"{self.path}: unsupported chunk size {cw}x{ch}")
        self.blocks_w, self.blocks_h = bw, bh
        self.width, self.height = bw * 8, bh * 8
        self._lut = MAP_HEADER + bw * bh * 64 * LAND_CELL   # _staticLUTOffset
        if self._lut + bw * bh * 8 > len(self._mm):
            raise ValueError(f"{self.path}: truncated")
        self._blocks = collections.OrderedDict()
        self._td = None

    @property
    def tiledata(self):
        if self._td is None:
            self._td = tiledata(self.root)
        return self._td

    def close(self):
        self._mm.close()

    def in_bounds(self, x, y):
        return 0 <= x < self.width and 0 <= y < self.height

    # -- land ---------------------------------------------------------------
    def land(self, x, y):
        """(tile_id, z) of the land cell, or None outside the map."""
        if not (0 <= x < self.width and 0 <= y < self.height):
            return None
        off = MAP_HEADER + (((y >> 3) * self.blocks_w + (x >> 3)) * 64
                            + ((y & 7) << 3) + (x & 7)) * LAND_CELL
        return _LAND.unpack_from(self._mm, off)

    def _tile_z(self, x, y):
        """Map.GetTileZ: -32768 for negative coords, 0 past the far edge."""
        if x < 0 or y < 0:
            return -32768
        cell = self.land(x, y)
        return cell[1] if cell else 0

    def land_stretch(self, x, y):
        """Land.Create + Land.ApplyStretch (Outlands @ 0x140318680/0x1403188a0):
        textured land takes its walk z from the 4 corner z values."""
        cell = self.land(x, y)
        if cell is None:
            return None
        tile_id, z = cell
        lt = self.tiledata.land(tile_id)
        flat = (lt.tex_id == 0 and lt.flags & WET) or not self.tiledata.has_texmap(lt.tex_id)
        if flat:
            return LandStretch(z, z, z, z, False)
        z_right = self._tile_z(x + 1, y)
        z_left = self._tile_z(x, y + 1)
        z_bottom = self._tile_z(x + 1, y + 1)
        if abs(z - z_bottom) <= abs(z_left - z_right):
            avg = (z + z_bottom) >> 1
        else:
            avg = (z_left + z_right) >> 1
        corners = (z, z_right, z_left, z_bottom)
        return LandStretch(z, avg, min(corners), max(corners), True)

    # -- statics ------------------------------------------------------------
    def _block_statics(self, bx, by):
        key = by * self.blocks_w + bx
        cells = self._blocks.get(key)
        if cells is not None:
            self._blocks.move_to_end(key)
            return cells
        cells = {}
        (rel,) = _I64.unpack_from(self._mm, self._lut + key * 8)
        if rel != NO_STATICS:
            pos = MAP_HEADER + rel
            (count,) = _U32.unpack_from(self._mm, pos)
            pos += 4
            cell = 0
            for _ in range(count):
                delta, graphic, hue, z = _STATIC.unpack_from(self._mm, pos)
                pos += STATIC_REC
                cell += delta
                if graphic == 0 or graphic == 0xFFFFFFFF or cell >= 64:
                    continue   # Chunk.Load skips these
                cells.setdefault(cell, []).append(Static(graphic, z, hue))
        self._blocks[key] = cells
        if len(self._blocks) > self.STATIC_CACHE_BLOCKS:
            self._blocks.popitem(last=False)
        return cells

    def statics(self, x, y):
        """Statics on (x, y) in file order, or None outside the map."""
        if not (0 <= x < self.width and 0 <= y < self.height):
            return None
        cells = self._block_statics(x >> 3, y >> 3)
        return list(cells.get(((y & 7) << 3) + (x & 7), ()))

    def iter_statics(self, x0, y0, x1, y1):
        """Yield (x, y, Static) for every static in the inclusive rectangle."""
        x0, y0 = max(x0, 0), max(y0, 0)
        x1, y1 = min(x1, self.width - 1), min(y1, self.height - 1)
        for by in range(y0 >> 3, (y1 >> 3) + 1):
            for bx in range(x0 >> 3, (x1 >> 3) + 1):
                for cell, sts in self._block_statics(bx, by).items():
                    x, y = bx * 8 + (cell & 7), by * 8 + (cell >> 3)
                    if x0 <= x <= x1 and y0 <= y <= y1:
                        for s in sts:
                            yield x, y, s

    def static_block_count(self):
        """Number of 8x8 blocks that carry a statics list."""
        n = 0
        for k in range(self.blocks_w * self.blocks_h):
            if _I64.unpack_from(self._mm, self._lut + k * 8)[0] != NO_STATICS:
                n += 1
        return n

    def find_trees(self, x0, y0, x1, y1):
        """Tree statics in the rectangle: impassable statics whose tiledata name
        says tree (not potted trees or stumps). Harvestability is learned by the
        harvester (a non-tree answers cliloc 500489). -> [(x, y, z, graphic)]"""
        td = self.tiledata
        out = []
        for x, y, s in self.iter_statics(x0, y0, x1, y1):
            it = td.item(s.graphic)
            if it is None or not it.flags & IMPASSABLE:
                continue
            name = it.name.lower()
            if "tree" in name and "potted" not in name and "stump" not in name:
                out.append((x, y, s.z, s.graphic))
        return out


# -- CLI -------------------------------------------------------------------
def describe_tile(m, x, y):
    cell = m.land(x, y)
    if cell is None:
        return [f"({x}, {y}) is outside map{m.index} ({m.width}x{m.height})"]
    td = m.tiledata
    lt = td.land(cell[0])
    st = m.land_stretch(x, y)
    lines = [f"map{m.index} ({x}, {y})",
             f"  land   {cell[0]:#06x} z={cell[1]:<4} avg_z={st.avg_z} min_z={st.min_z}"
             f" {'textured' if st.textured else 'flat'}  tex={lt.tex_id}"
             f"  '{lt.name}'  [{flag_names(lt.flags)}]"]
    sts = m.statics(x, y)
    if not sts:
        lines.append("  (no statics)")
    for s in sorted(sts, key=lambda s: s.z):
        it = td.item(s.graphic)
        top = s.z + it.calc_height
        lines.append(f"  static {s.graphic:#06x} z={s.z:<4} h={it.height:<3} top={top:<4}"
                     f" hue={s.hue:<5} '{it.name}'  [{flag_names(it.flags)}]")
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description="Outlands .uoo map reader")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tile", help="land + statics at X Y")
    t.add_argument("x", type=lambda v: int(v, 0))
    t.add_argument("y", type=lambda v: int(v, 0))
    t.add_argument("--map", type=int, default=0)
    t.add_argument("--root", default=INSTALL)
    args = ap.parse_args(argv)
    m = UoMap(args.map, args.root)
    print("\n".join(describe_tile(m, args.x, args.y)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

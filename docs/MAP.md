# Outlands map data (.uoo) — format spec

Reader: `harness/uomap.py`. Tests: `harness/test_uomap.py`. Decompiled evidence:
`decompiled/map_formats.c` (local only: `decompiled/` is gitignored; regenerate it with
§Reproduce). Status: verified 2026-09-29 against the install files, the repo copy of
`ClassicUO.exe`, and captures.

Outlands does not ship `map*.mul`, `statics*.mul`, `staidx*.mul` or `tiledata.mul`. The
client (Outlands' NativeAOT fork of ClassicUO) reads everything through its own `UOO.*`
classes:

| File | Class (mrt metadata) | Content |
|---|---|---|
| `map0.uoo` … `map5.uoo` | `UOO.TerrainFile` | land cells + statics for one facet |
| `landdata.uoo` | data file of `UOO.LandTile` | land tiledata, 0x4000 × 48 B |
| `artdata.uoo` | `UOO.ArtDataFile` / `UOO.ArtTile` | item (static) tiledata, 0x1167C × 72 B |
| `texmaps.uoo`, `art.uoo`, `landtiles.uoo`, `gumps.uoo` | `UOO.UOOFile` | id-keyed payload chains (art, not needed for walkability) |

All integers are little-endian. File names come from UTF-16 string literals that the loaders
reference: `landdata.uoo` @ 0x143eec510 and `artdata.uoo` @ 0x143ece420 (both used by the
TileDataLoader lambda @ 0x1401bc5e0), plus `"map"` + index + `".uoo"` built in
`MapLoader.<Load>b__33_0` @ 0x1401b8260 (literals @ 0x143eef4e8 / 0x143da1438).

## mapN.uoo (`UOO.TerrainFile`)

### Header (0x12 bytes)

| Off | Type | Value (map0) | Meaning |
|---|---|---|---|
| 0x00 | u32 | 0xE532363F | magic. `OpenRead` @ 0x141062bd0 checks `!= -0x1acdc9c1`, the writer `SetMapDimensions` @ 0x141062fc0 writes it |
| 0x04 | u32 | 1 | version. Anything other than 1 throws "Unsupported map terrain file version" |
| 0x08 | u8 | 8 | chunk (block) width in tiles (`+0x34`) |
| 0x09 | u8 | 8 | chunk height (`+0x35`) |
| 0x0A | u32 | 1344 | width in blocks (`+0x20`) |
| 0x0E | u32 | 768 | height in blocks (`+0x24`) |

`OpenRead` then sets `_terrainStartOffset = 0x12` (`+0x28`) and
`_staticLUTOffset = 0x12 + cw*ch*W*H*6` (`+0x2c`). The field names come from the metadata
string `_writer&_terrainStartOffset _staticLUTOffset`.

### Land section (starts at 0x12)

Blocks are **row-major by block y**: `block = by * W + bx` (`TerrainFile.GetBlockNum`
@ 0x141063580 returns `W * y + x`). Upstream MUL is column-major, `x * H + y`
(`ClassicUO.Assets/MapLoader.cs:623`). Each block is 64 cells with no block header (MUL has a
4-byte header). Cells within a block are row-major, `cell = (y & 7) * 8 + (x & 7)`
(`GetTerrainTile` @ 0x1410636c0: `(cw*ch*block + x%cw + (y%ch)*cw) * 6 + 0x12`).

Land cell, 6 bytes (writer `AddTerrainChunk` @ 0x141063340 writes a u32 and then a short):

| Off | Type | Meaning |
|---|---|---|
| 0 | u32 | land tile id (`Land.Create(uint)` @ 0x140318680). Upper 16 bits are 0 in every cell of all six maps; max id is 0x3FFB |
| 4 | i16 | z. **Signed 16-bit, not sbyte.** map0 land ranges from −270 to 200 |

Address: `0x12 + ((by*W + bx)*64 + (y&7)*8 + (x&7)) * 6`.
`Chunk.Load` @ 0x1402d76a0 walks the 64 cells as `x = i & 7`, `y = i >> 3`.

### Statics LUT (at `_staticLUTOffset`)

`W*H` entries of **i64**, indexed by the same `block = by*W + bx`. Each entry holds an
offset **relative to 0x12** (`GetStatics` @ 0x141063820 seeks to `lut + block*8`, reads an
Int64, and then seeks to `value + _terrainStartOffset`). `0x7FFFFFFFFFFFFFFF` (long.MaxValue)
means "no statics". The writer fills the LUT with that value.

### Statics block (at `0x12 + lut[block]`)

```
u32 count
count × 9-byte record:
  +0 u8   cell delta   running sum over the block gives the cell index
  +1 u32  graphic      artdata index
  +5 u16  hue
  +7 i16  z
```

Evidence comes from `AddTerrainStatics` @ 0x141063440, which writes byte, u32, u16, short,
and from `Chunk.Load`, which consumes records like this:

```
cell += rec[0];                         // cumulative
if (graphic != 0 && graphic != -1 && cell < 64)
    Static.Create(graphic, hue, cell); x = bx*cw + cell % cw; y = by*ch + cell / cw; z = (int)rec.z
```

Records are sorted by cell. The delta is 0 for extra statics on the same cell. A full block
with one static per cell is 4 + 64·9 = 580 B, the most common block size in map0. Upstream
MUL statics are 7 B with explicit x/y bytes and an sbyte z (`MapLoader.cs:630-637`). Here z
is **i16**. map0 statics z ranges from −296 to 393.

`AddTerrainStatics` appends the new block at the end of the file and then rewrites the LUT
entry. So a patched file leaves orphaned old blocks behind. map0 has 82,174,514 bytes of
unreferenced gaps between live blocks. maps 1, 4 and 5 have none.
[INFERENCE: the launcher patcher or the client's UltimaLive write path patched map0 in
place. map0.uoo's mtime is 2026-09-27 15:04, next to art.uoo/artdata.uoo at 15:09.]

### Facets in this install

| File | Tiles | Blocks with statics | Static records | Land z range | Notes |
|---|---|---|---|---|---|
| map0.uoo | 10752 × 6144 | 201,993 | 10,773,929 | −270 … 200 | main world. Same size as `facet00.mul` (harness/facet.py) |
| map1.uoo | 7168 × 4096 | 116,694 | 3,163,286 (50 with graphic 0/−1, skipped) | −128 … 125 | |
| map2.uoo | 2304 × 1600 | 0 | 0 | 0 … 0 | placeholder: flat, land ids ≤ 0x1FF |
| map3.uoo | 2560 × 2048 | 0 | 0 | 0 … 0 | every cell is land 0x244 `NoName` (Wall\|Impassable). **Rental rooms are on facet 3** (capture 0xBF/0x08 → 3 in session 20260929_204225) |
| map4.uoo | 1448 × 1448 | 13,250 | 506,758 | −60 … 113 | |
| map5.uoo | 1280 × 4096 | 13,328 | 329,580 | −128 … 127 | |

Every statics block in every map parses with its cumulative cell ≤ 63, graphic < 0x1167C, and
no overlapping blocks (full scan).

## landdata.uoo / artdata.uoo (tiledata)

Header: `u32 magic 0x1E7FAB6D, u32 version (1, not checked), u32 count`, followed by
`count` fixed-size records. The data-file Load functions @ 0x1412e84e0 (landdata) and
@ 0x1412e8870 (artdata) read magic, skip the version, store the count, and read records as
memory-mapped structs. Sizes check out exactly: `12 + 0x4000*48 = 786,444` and
`12 + 0x1167C*72 = 5,133,036`.

### Land record (48 B) — `UOO.LandTile`

| Off | Type | Field | Evidence |
|---|---|---|---|
| 0 | u64 | flags (TileFlag) | `LandTile.get_IsImpassable` `*p & 0x40`, `get_IsWet` `& 0x80`, `get_IsNoDiagonal` `& 0x2000000` (@ 0x141061820/810/830). Water 0xA8 = 0xC0 |
| 8 | u32 | TexID | `Land.Create` @ 0x140318680: `IsStretched = TileData[+8] == 0 && IsWet`. `ApplyStretch` looks `[+8]` up in the texmaps index. 3,800 ids have TexID == id, and 12,498 have 0 |
| 12 | char[36] | name, NUL-padded ASCII | bytes: `VOID!!!!!!`, `grass`, `water`, `forest` |

Record 0 contains garbage (`"UNUS\0\0ED"` smeared over the flags/TexID bytes). Its flags have
no low bits set, so it is harmless.

### Item record (72 B) — `UOO.ArtTile`

| Off | Type | Field | Evidence |
|---|---|---|---|
| 0 | u64 | flags (TileFlag) | all the `ArtTile.get_*` getters test `*p & bit` (below) |
| 8 | u8 | weight | `Pathfinder.CreateItemList` @ 0x140210140: `0x5a < *(byte*)(itemdata+8)` (upstream `Weight <= 0x5A`, `Pathfinder.cs:170`). Trees and doors = 255, logs = 2 |
| 9 | u8 | layer | [INFERENCE from values] backpack 0x15, cloak 0x14, broadsword 1 |
| 10 | 2 B | padding | always 0 |
| 12 | i32 | count / quantity | [INFERENCE] upstream `Count` |
| 16 | i32 | anim id | [INFERENCE] backpack 0x1A6, cloak 0x1D4 |
| 20 | i32 | male paperdoll gump | = 50000 + anim in 2006/2293 records; confirmed 2026-09-30 by rendering the gumps (harness/paperdoll.py) |
| 24 | i32 | female paperdoll gump | = 60000 + anim (1750) or 0 (541; use the male gump). Confirmed as above |
| 28 | u16 | hue? | [INFERENCE] non-zero in 38 records |
| 30 | u16 | ? | equals the layer byte in 71,034/71,292 records [INFERENCE: quality/light index] |
| 32 | u32 | height | `ArtTile.get_CalcHeight` @ 0x141061b70 `p[8]` (uint index 8 = byte 32), halved if Bridge. Pathfinder reads `*(int*)(itemdata+0x20)` |
| 36 | char[36] | name | `walnut tree`, `wooden boards` |

The record layout matches the struct offsets the client code uses (+8 weight, +0x20
height), so the file is read as the in-memory struct.

### TileFlag (u64)

Bits 0–31 are identical to upstream `ClassicUO.Assets/TileDataLoader.cs:404-552`. The
getter @ 0x141061xxx shows each one:

| Bit | Name | Outlands getter |
|---|---|---|
| 0x1 | Background | IsBackground |
| 0x2 | Weapon | IsWeapon |
| 0x4 | Transparent | IsTransparent |
| 0x8 | Translucent | IsTranslucent |
| 0x10 | Wall | IsWall |
| 0x20 | Damaging | – |
| 0x40 | Impassable | Impassable / LandTile.IsImpassable |
| 0x80 | Wet | LandTile.IsWet |
| 0x200 | Surface | IsSurface / Surface |
| 0x400 | Bridge | Bridge / IsSloped / CalcHeight |
| 0x800 | Generic (stackable) | IsStackable |
| 0x1000 | Window | IsWindow |
| 0x2000 | NoShoot | IsNoShoot |
| 0x4000 / 0x8000 | ArticleA / ArticleAn | – |
| 0x10000 | Internal | IsInternal |
| 0x20000 | Foliage | IsFoliage |
| 0x40000 | PartialHue | IsPartialHue |
| 0x200000 | Container | IsContainer |
| 0x400000 | Wearable | IsWearable |
| 0x800000 | LightSource | IsLight |
| 0x1000000 | Animation | IsAnimated |
| 0x2000000 | NoDiagonal | LandTile.IsNoDiagonal |
| 0x10000000 | Roof | IsRoof |
| 0x20000000 | Door | IsDoor |
| 0x10000000000 | MultiMovable | IsMultiMovable (same as upstream) |

**Outlands-only high bits** (not in upstream's enum):

| Bit | Getter |
|---|---|
| 0x800000000 | IsNoClip |
| 0x20000000000 | IsIgnoreHeightNoDraw |
| 0x2000000000000 | IsNoDraw |
| 0x4000000000000 | IsHuedLight |

The remaining upstream high bits (AlphaBlend…PlayAnimOnce) are kept in `uomap.TileFlag` by
their upstream values but are unverified for Outlands.

## texmaps.uoo and other `UOOFile` containers

`UOOFile.ReadIndex` @ 0x141063ec0: `u32 magic 0x1E7FAB6D, u32 version`, then a chain of
`u32 id, i32 length, payload[length]`, ending at `length == 0` (or EOF). texmaps.uoo holds
3,338 ids between 1 and 16379. Only the id set matters here. See land stretch below.

**gumps.uoo** (decoded 2026-09-30; reader `harness/uoart.py` `UooImages`): 7018 ids from 0 to 62727. Each
payload is:

| Off | Type | Field |
|---|---|---|
| 0 | u16 | width |
| 2 | u16 | height |
| 4 | 4×u16 | a rectangle (x, y, w, h); equal to (0, 0, width, height) on the entries checked, unused |
| 12 | u32 | n = payload length − 16 |
| 16 | n bytes | **raw deflate** (zlib wbits −15) of width×height u16 pixels, row-major, **RGB555, 0 = transparent** |

Every entry decompresses to exactly width×height×2 bytes. The paperdoll body is gump 12 (male)
or 13 (female), 260×237, and item overlays are the full canvas size.

**hues.mul** is stock: groups of u32 header + 8 × (32 u16 colours, u16 start, u16 end, 20-byte
name). A pixel is hued as `table[pixel red 5 bits]`, only grey pixels for PartialHue items
(ClassicUO HuesLoader GetColor16 / GetPartialHueColor).

**art.uoo** (decoded 2026-10-02, `harness/uoart.py`): 69,117 ids from 0 to 71291, same payload
as gumps.uoo. The ids are **item graphics as the server sends them**, with no 0x4000 offset
(gold coins 0x0EED–0x0EEF and scales 0x1851 render as themselves; [INFERENCE] land art lives in
landtiles.uoo). The rectangle at offset 4 is `(x0, y0, x1, y1)`, the **inclusive bounding box of
the opaque pixels**: this held on all 69,117 entries (full scan). The 61 fully transparent
entries store `(width, height, 0, 0)`. The item is drawn inside a larger canvas, e.g. the gold
pile 0x0EEF is 44×32 with box (7, 1, 38, 24).

## Client semantics that matter for walking

- **Land walk z (stretch).** Outlands `Land.Create`/`Land.ApplyStretch` @ 0x140318680/0x1403188a0
  behave like upstream `Land.cs:48,96-132`. Water-like land (TexID 0 and Wet), and land whose
  TexID has no texmaps entry, is flat: avg = min = z. Otherwise the corners are
  top = z(x,y), right = z(x+1,y), left = z(x,y+1), bottom = z(x+1,y+1), and
  avg = `|top-bottom| <= |left-right| ? (top+bottom)>>1 : (left+right)>>1`, min = min of the
  four corners. Outlands additionally stores max of the four (+0xa4). Corner z comes from
  `Map.GetTileZ` @ 0x1402d8730, which returns −32768 for negative coordinates and 0 past the
  far edge (the zeroed struct from `GetTerrainTile`). Implemented as `UoMap.land_stretch`.
  Capture proof: server z 10 at (1898,2621), where the raw land z is 11 and the corners are
  11/10/10/10.
- **Statics walk height.** `CalcHeight = Bridge ? height/2 : height`. Pathfinder item entries
  use `top = z + CalcHeight` (CreateItemList @ 0x140210140 reads `*(int*)(itemdata+0x20)` and halves it for Bridge).
- **Pathfinder land entry** (CreateItemList): land graphics 2, 0x1AE–0x1B5 and 0x1DB are
  skipped. Impassable land gives flags=1, otherwise 7. Entry z = MinZ, average = AverageZ.
  This is the same as upstream. The full decompile of CalculateMinMaxZ, CalculateNewZ, CanWalk
  and CreateItemList is in `decompiled/map_formats.c` for the pathfinder port.
- **z width.** Map z is i16 on disk and handled as int in the client (`GetTileZ` returns a
  short). Upstream is sbyte.

## The CSV route

`Ultima.TileDataExtensions.ReadTiledataInformationFromCSV` @ 0x141080c40 and
`ReadLanddataInformationFromCSV` @ 0x1410811b0 parse one CSV row (a string[]) into an
item/land record. They back `Ultima.TileData.Import{Item,Land}DataFromCsv`, which is
UltimaSDK editor tooling compiled into the client. The runtime TileDataLoader
(@ 0x1401bc5e0) loads `landdata.uoo`/`artdata.uoo` directly. The only `.csv` strings in the
binary are `*.csv`, `_SkillLog.csv` and `settings.csv`, and the install has no tiledata CSV.
[INFERENCE: the CSV code is dead at runtime. No call path was traced beyond the loader
decompile.]

## Protocol finding (from validation)

S2C **0x21 move-reject is 15 bytes with z = i32be at offset 11**: `seq u8, x u32, y u32,
dir u8, z i32`. For example `21 fb 00000778 000009fd 80 ffffffec` is (1912,2557) z −20,
matching land z −20. `harness/world/layouts.py` currently reads z as i8 @11 plus skip 3. That
gives 0 for any z in 0…255 and −1 for small negative z.

## Validation (harness/test_uomap.py, 42 checks, all pass, ~0.7 s)

- Tree target: C2S 0x6C in 20260929_204225 → (1898, 2622, z 10, graphic 0x0CE0). map0 has
  static 0x0CE0 at z 10, tiledata `walnut tree`, Background|Impassable|ArticleA, height 20,
  weight 255, listed in `ClassicUO/Data/Client/tree.txt` (3296). The graphic-0 target at
  (1984, 2602, z 0) has static 0x0CD3 `tree` (tree.txt) at z 0.
- Login spot (0x7AB, 0xA25): every capture z there is 0. Land 0x3FD `dirt` z 0, walkable,
  no statics.
- Shelter inn (1937, 2583): capture z 20 (0x21, i32). Statics `wooden boards` 0x4A9 at z 20
  (Surface, h 0) for upstairs, and `wooden plank` 0x7C9 at z 0 h 1 (top 1) for the ground
  floor, over land z 0. The inn doors at (1933,2589,z20) and (1935,2588,z0) come in as 0xF3
  items 0x6AD/0x6A5 (tiledata `wooden door`, Door flag). Neither tile has a door static:
  **doors are dynamic, not in the map file.**
- Walk memory (built from the committed captures): all 838 facet-0 tiles have a standing height
  (land avg z or surface top) with no Impassable static overlapping a 16-high body. The 15
  tiles with capture z from 0x1B/0x20/0x77 all sit on such a height.
- Self positions from 0x1B/0x20/0x77, 16 distinct on facet 0, including z 20 upstairs, z 21 on
  the inn stairs, z −20 in the sunken area at (1912,2556), and the stretched (1898,2621 z10): each
  equals the land avg z or a Surface/Bridge top.

Throwaway full-map scans (not in the test) confirmed the per-facet figures in the table above.

## Open questions / risks

- **Binary version.** The decompile is from the repo copy of `ClassicUO.exe` (70,434,992 B).
  The installed `ClassicUO/ClassicUO.exe` is now 70,458,544 B. The data files validate the
  format, but addresses may have shifted.
- **Stale areas / live edits.** UltimaLive (`0x3F`) can push block updates, and the client
  may persist them into `mapN.uoo` (see the append layout above). Captures show almost no
  0x3F traffic. The files can still differ from the server in places.
- **Session 20260929_161433 z anomaly.** A run of 0x21 rejects reports z 20 at
  (1955–1961, 2610) and (1961, 2605–2609), where the map has flat land z 0 and no statics.
  The next login (163420, 0x1B) puts the player at (1961,2605) z 0. Unexplained
  [INFERENCE: stale server z after the upstairs walk at (1955,2586) z 20]. Excluded from the
  z checks, which use only 0x1B/0x20/0x77.
- **Rental rooms (facet 3).** map3.uoo is a blank placeholder (impassable NoName land, no
  statics). Room geometry reaches the client some other way. Candidates: a multi (0xF3
  multi/`multi.mul`), live UltimaLive blocks, or dynamic items. The capture shows only 3
  facet-3 items (`0x6E5`, `0xE76`, and an 0xF3 with data_type 2 / graphic 0x190 at
  (39,65,1)). Walking there needs capture-derived geometry, not the files.
- **Seasons.** `Land.UpdateGraphicBySeason` swaps land graphics for display only. Walkability
  uses the raw id [INFERENCE, matches upstream].
- **Multis/houses** (`multi.idx/mul`) aren't in the map files. They come over the wire as 0xF3
  data_type 2, and `uomap.multi_components` + `pathfind.Walkers.get` place their pieces as
  ground objects (since 2026-10-02; format and live evidence in docs/NOTES.md "Mounted movement
  and player houses"). Custom houses (`0xD8`) aren't handled; none have been seen yet.
- Item fields at +9..+31 (layer, count, anim, gumps, hue) are value-inferred only. Flags,
  weight, height and name are decompile-confirmed.

## Reproduce

Decompile list: a `name<TAB>hexVA` TSV (VAs from `mrt_map.json`, e.g. `UOO.TerrainFile`,
`UOO.ArtTile`, `ClassicUO.Game.Pathfinder`), then:

```
"<ghidra>/support/analyzeHeadless.bat" "<repo>/ghidra" UOProject \
  -process ClassicUO.exe -noanalysis -readOnly -scriptPath "<repo>/ghidra_scripts" \
  -postScript DecompileList.java <targets.tsv> <out.c>
```

~15 s per run, since the project is already analysed. `-readOnly` leaves the project
untouched.

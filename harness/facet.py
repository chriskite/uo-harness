"""Facet picture reader: the client's pre-rendered top-down map, served as PNG chunks.

`facetNN.mul` (install dir, read-only) is a 1 px/tile colour picture of map NN.
Format as in upstream ClassicUO `MultiMapLoader.LoadFacet`: u16le width, u16le
height, then one record per row: i32le byte count, followed by that many bytes
of (u8 run length, u16le ARGB1555 colour) runs. Colours convert like
`HuesHelper.Color16To32`: 5-bit channel v -> v*255//31, red = bits 10-14.

Outlands' facet00.mul is 10752x6144. Its 1 px/tile scale was verified on 40 bank
markers (docs/NOTES.md). The file dates from 2024-12, so areas added since are
missing from it: they are pure black (the Hunting Grounds, x 7906-8474 y 72-330,
opened 2025-11). MapRadar fills the picture's black pixels from the live map
(mapN.uoo, which the client patches) in the client's radar colours (radarcol.mul),
as the client's own minimap does: per tile the higher of the land and its topmost
static. Void land is black in radarcol too, so real black stays black.

Only a row-offset index is kept in memory. A chunk is decoded from the RLE on
demand and its PNG is cached; the full RGB picture would be ~200 MB.
"""
import os
import struct
import threading
import zlib

CHUNK = 256
DEFAULT_PATH = r"C:/Program Files (x86)/Ultima Online Outlands/facet00.mul"

# 5-bit channel -> 8-bit, as the client's table
_C5 = bytes(v * 255 // 31 for v in range(32))
RADAR_ITEMS = 0x4000     # radarcol.mul: land colours [0, 0x4000), item colours from here (ClassicUO RadarColors)


def _png(width: int, height: int, rgb: bytes) -> bytes:
    stride = width * 3
    raw = b"".join(b"\x00" + rgb[y * stride:(y + 1) * stride] for y in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6))
            + chunk(b"IEND", b""))


class MapRadar:
    """Radar colours of map N's tiles from the install dir (read-only): mapN.uoo + radarcol.mul."""

    def __init__(self, index: int = 0, root: str = os.path.dirname(DEFAULT_PATH)):
        import numpy as np
        import uomap
        self.map = uomap.UoMap(index, root)
        self.source = f"map{index}.uoo + radarcol.mul"
        with open(os.path.join(root, "radarcol.mul"), "rb") as f:
            c = np.frombuffer(f.read(), "<u2")
        c5 = np.frombuffer(_C5, np.uint8)
        # ARGB1555 -> RGB rows, as the facet's runs
        self.colours = np.stack([c5[(c >> 10) & 31], c5[(c >> 5) & 31], c5[c & 31]], axis=1)
        self.lock = threading.Lock()     # UoMap's statics LRU isn't thread-safe

    def _colour(self, idx):
        import numpy as np
        ok = idx < len(self.colours)
        out = np.zeros(idx.shape + (3,), np.uint8)
        out[ok] = self.colours[idx[ok]]
        return out

    def rgb(self, x0: int, y0: int, x1: int, y1: int):
        """numpy uint8 [y1 - y0, x1 - x0, 3]: tiles [x0, x1) x [y0, y1) in radar colours."""
        import numpy as np
        m = self.map
        by0, by1 = y0 >> 3, (y1 + 7) >> 3
        with self.lock:
            ids, lz = m.land_rows(by0, by1)
            sx, sy, sz, sg, _ = m.statics_rows(by0, by1)
        oy = by0 * 8
        ids, lz = ids[y0 - oy:y1 - oy, x0:x1], lz[y0 - oy:y1 - oy, x0:x1].astype(np.int32)
        out = self._colour(np.where(ids < RADAR_ITEMS, ids, len(self.colours)).astype(np.int64))
        keep = (sx >= x0) & (sx < x1) & (sy >= y0) & (sy < y1)
        sx, sy, sz, sg = sx[keep] - x0, sy[keep] - y0, sz[keep].astype(np.int32), sg[keep].astype(np.int64)
        if len(sx):
            cell = sy * (x1 - x0) + sx
            order = np.lexsort((sz, cell))               # by cell, then z: the last of each cell is its top
            cell, sx, sy, sz, sg = cell[order], sx[order], sy[order], sz[order], sg[order]
            top = np.r_[cell[1:] != cell[:-1], True]
            sx, sy, sz, sg = sx[top], sy[top], sz[top], sg[top]
            col = self._colour(sg + RADAR_ITEMS)
            over = (sz >= lz[sy, sx]) & col.any(axis=1)  # above the land, and a colour (not 0) to show
            out[sy[over], sx[over]] = col[over]
        return out


def radar_for(facet_path: str) -> MapRadar | None:
    """The map fill for facetNN.mul: mapNN.uoo and radarcol.mul beside it, or None when they can't be read."""
    digits = "".join(ch for ch in os.path.basename(facet_path) if ch.isdigit())
    try:
        return MapRadar(int(digits or 0), os.path.dirname(facet_path))
    except (OSError, ValueError, ImportError):
        return None


class FacetPicture:
    def __init__(self, path: str, fill: MapRadar | None = None):
        self.path = path
        self.fill = fill             # paints the picture's black pixels from the live map (module docstring)
        with open(path, "rb") as f:
            self.data = f.read()
        self.width, self.height = struct.unpack_from("<HH", self.data, 0)
        if self.width < 1 or self.height < 1:
            raise ValueError(f"{path}: bad facet size {self.width}x{self.height}")
        self.rows = []  # (offset of first run, offset past the last run)
        off = 4
        for _ in range(self.height):
            (n,) = struct.unpack_from("<i", self.data, off)
            if n < 0 or n % 3 or off + 4 + n > len(self.data):
                raise ValueError(f"{path}: bad row record at byte {off}")
            self.rows.append((off + 4, off + 4 + n))
            off += 4 + n
        self.mtime = os.path.getmtime(path)
        self.lock = threading.Lock()
        self.cache: dict[tuple[int, int], bytes] = {}

    @property
    def chunks(self) -> tuple[int, int]:
        return (-(-self.width // CHUNK), -(-self.height // CHUNK))

    def meta(self) -> dict:
        cx, cy = self.chunks
        return {"available": True, "width": self.width, "height": self.height,
                "chunk": CHUNK, "chunks_x": cx, "chunks_y": cy,
                "source": os.path.basename(self.path), "mtime": self.mtime,
                "fill": self.fill.source if self.fill is not None else None}

    def _row_rgb(self, y: int, x0: int, x1: int) -> bytearray:
        """RGB bytes of row y for columns [x0, x1)."""
        out = bytearray()
        data = self.data
        x = 0
        off, end = self.rows[y]
        while off < end and x < x1:
            run = data[off]
            c = data[off + 1] | (data[off + 2] << 8)
            off += 3
            lo, hi = max(x, x0), min(x + run, x1)
            if lo < hi:
                px = bytes((_C5[(c >> 10) & 31], _C5[(c >> 5) & 31], _C5[c & 31]))
                out += px * (hi - lo)
            x += run
        if len(out) < (x1 - x0) * 3:  # a short row: pad with black
            out += bytes((x1 - x0) * 3 - len(out))
        return out

    def chunk_png(self, cx: int, cy: int) -> bytes | None:
        """PNG of chunk (cx, cy): tiles [cx*CHUNK, ...) x [cy*CHUNK, ...), clipped at the edge."""
        ncx, ncy = self.chunks
        if not (0 <= cx < ncx and 0 <= cy < ncy):
            return None
        key = (cx, cy)
        with self.lock:
            hit = self.cache.get(key)
        if hit is not None:
            return hit
        x0, y0 = cx * CHUNK, cy * CHUNK
        x1, y1 = min(x0 + CHUNK, self.width), min(y0 + CHUNK, self.height)
        rgb = b"".join(self._row_rgb(y, x0, x1) for y in range(y0, y1))
        if self.fill is not None:
            import numpy as np
            pic = np.frombuffer(rgb, np.uint8).reshape(y1 - y0, x1 - x0, 3)
            blank = ~pic.any(axis=2)
            if blank.any():
                pic = pic.copy()
                pic[blank] = self.fill.rgb(x0, y0, x1, y1)[blank]
                rgb = pic.tobytes()
        png = _png(x1 - x0, y1 - y0, rgb)
        with self.lock:
            self.cache[key] = png
        return png

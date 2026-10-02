"""The client's image containers, read-only from the install dir: gumps.uoo
(gump art) and art.uoo (item art), for the visualizer.

Both are UOOFile containers (docs/MAP.md: u32 magic 0x1E7FAB6D, u32 version,
then `u32 id, i32 length, payload` records). Each payload is
  u16 width, u16 height, u16 x0, u16 y0, u16 x1, u16 y1,
  u32 n, then n bytes of raw deflate -> width*height u16 pixels, row-major,
  RGB555 with 0 = transparent.
art.uoo ids are item graphics as sent by the server (no 0x4000 offset, checked
on the gold coins 0x0EED-0x0EEF and scales 0x1851; [INFERENCE] land art is in
landtiles.uoo), and (x0, y0, x1, y1) is the inclusive bounding
box of the opaque pixels: verified 2026-10-02 on all 69,117 entries. The 61
fully transparent ones store (width, height, 0, 0), i.e. x1 < x0.
"""
import collections
import mmap
import os
import struct
import zlib

import uomap
from pngenc import png_bytes

GUMPS_PATH = os.path.join(uomap.INSTALL, "gumps.uoo")
ART_PATH = os.path.join(uomap.INSTALL, "art.uoo")
DECODED_CACHE = 64
ITEM_PNG_CACHE = 64


class UooImages:
    """Read-only, lazily indexed access to one image container (gumps.uoo, art.uoo)."""

    def __init__(self, path: str):
        self._f = open(path, "rb")
        self._mm = mmap.mmap(self._f.fileno(), 0, access=mmap.ACCESS_READ)
        magic, _version = struct.unpack_from("<II", self._mm, 0)
        if magic != uomap.DATA_MAGIC:
            raise ValueError(f"{path}: bad magic {magic:#x}")
        self._index = {}
        pos = 8
        while pos + 8 <= len(self._mm):
            gid, length = struct.unpack_from("<Ii", self._mm, pos)
            if length == 0:
                break
            self._index[gid] = (pos + 8, length)
            pos += 8 + length
        self._cache = collections.OrderedDict()

    def __contains__(self, gid) -> bool:
        return gid in self._index

    def bbox(self, gid: int):
        """(x0, y0, x1, y1) inclusive box of the opaque pixels as stored, or None."""
        entry = self._index.get(gid)
        return None if entry is None else struct.unpack_from("<4H", self._mm, entry[0] + 4)

    def get(self, gid: int):
        """(width, height, pixels: tuple of RGB555 u16, 0 = transparent), or None."""
        hit = self._cache.get(gid)
        if hit is not None:
            self._cache.move_to_end(gid)
            return hit
        entry = self._index.get(gid)
        if entry is None:
            return None
        off, _length = entry
        w, h, _x0, _y0, _x1, _y1, n = struct.unpack_from("<HHHHHHI", self._mm, off)
        raw = zlib.decompress(self._mm[off + 16:off + 16 + n], -15)
        if len(raw) != w * h * 2:
            raise ValueError(f"image {gid}: {len(raw)} bytes for {w}x{h}")
        img = (w, h, struct.unpack(f"<{w * h}H", raw))
        self._cache[gid] = img
        if len(self._cache) > DECODED_CACHE:
            self._cache.popitem(last=False)
        return img


def rgb555(c: int) -> tuple[int, int, int]:
    return ((c >> 10) & 31) * 255 // 31, ((c >> 5) & 31) * 255 // 31, (c & 31) * 255 // 31


def crop_bgra(img, box) -> tuple[int, int, bytes]:
    """(w, h, BGRA) of `img` (w, h, RGB555 pixels) cut to the inclusive box (x0, y0, x1, y1)."""
    w, _h, px = img
    x0, y0, x1, y1 = box
    cw, ch = x1 - x0 + 1, y1 - y0 + 1
    out = bytearray(cw * ch * 4)
    for y in range(ch):
        row = (y0 + y) * w + x0
        for x in range(cw):
            c = px[row + x]
            if c:
                r, g, b = rgb555(c)
                o = (y * cw + x) * 4
                out[o:o + 4] = bytes((b, g, r, 255))
    return cw, ch, bytes(out)


class ItemArt:
    """Item art from art.uoo as PNGs cropped to the opaque pixels."""

    def __init__(self, images: UooImages | None = None):
        self.images = images or UooImages(ART_PATH)
        self._pngs = collections.OrderedDict()

    def png(self, graphic: int) -> bytes | None:
        """The item's art as a PNG, or None when art.uoo has no (visible) image for it."""
        hit = self._pngs.get(graphic)
        if hit is not None:
            self._pngs.move_to_end(graphic)
            return hit
        img = self.images.get(graphic)
        box = self.images.bbox(graphic)
        if img is None or box is None or box[2] < box[0] or box[3] < box[1]:
            return None
        data = png_bytes(*crop_bgra(img, box), alpha=True)
        self._pngs[graphic] = data
        if len(self._pngs) > ITEM_PNG_CACHE:
            self._pngs.popitem(last=False)
        return data

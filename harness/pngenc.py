"""Minimal PNG writer (stdlib only): 8-bit truecolour, with or without alpha,
from top-down BGRA bytes. Used by screen.py (screenshots), paperdoll.py and uoart.py."""
import struct
import zlib


def png_bytes(w: int, h: int, bgra: bytes, alpha: bool = False) -> bytes:
    """A PNG of a w x h top-down BGRA buffer: RGB (colour type 2), or RGBA
    (colour type 6) keeping the alpha channel."""
    stride, ch = w * 4, 4 if alpha else 3
    raw = bytearray()
    row_out = bytearray(w * ch)
    for y in range(h):
        row = bgra[y * stride:(y + 1) * stride]
        row_out[0::ch] = row[2::4]
        row_out[1::ch] = row[1::4]
        row_out[2::ch] = row[0::4]
        if alpha:
            row_out[3::ch] = row[3::4]
        raw += b"\x00" + row_out

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6 if alpha else 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))

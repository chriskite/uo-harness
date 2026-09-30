"""Tests for harness/pngenc.py (screenshots, paperdoll).

Offline and deterministic: channel order, alpha, row filters and chunk CRCs
against hand-built BGRA images.

Run: python harness/test_pngenc.py
"""
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pngenc  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def decode_png(data: bytes):
    """(w, h, depth, colour type, chunk kinds, CRCs ok, [(filter, [pixel tuples])])."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, chunks = 8, []
    while pos < len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        kind, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + n]
        crc = struct.unpack(">I", data[pos + 8 + n:pos + 12 + n])[0]
        chunks.append((kind, body, crc == zlib.crc32(kind + body) & 0xFFFFFFFF))
        pos += 12 + n
    w, h, depth, ctype = struct.unpack(">IIBB", chunks[0][1][:10])
    ch = 4 if ctype == 6 else 3
    raw = zlib.decompress(b"".join(c[1] for c in chunks if c[0] == b"IDAT"))
    rows = []
    for y in range(h):
        line = raw[y * (1 + ch * w):(y + 1) * (1 + ch * w)]
        rows.append((line[0], [tuple(line[1 + ch * x:1 + ch * (x + 1)]) for x in range(w)]))
    return w, h, depth, ctype, [c[0] for c in chunks], all(c[2] for c in chunks), rows


def main():
    print("== PNG encoder ==")
    # 3x2 BGRA: row 0 red, green, blue; row 1 white, black, (10, 20, 30); alpha varies
    px = [[(0, 0, 255, 255), (0, 255, 0, 0), (255, 0, 0, 128)],
          [(255, 255, 255, 255), (0, 0, 0, 255), (30, 20, 10, 7)]]
    bgra = b"".join(bytes(p) for row in px for p in row)
    w, h, depth, ctype, kinds, crcs_ok, rows = decode_png(pngenc.png_bytes(3, 2, bgra))
    check("RGB: header 3x2, 8-bit truecolour", (w, h, depth, ctype) == (3, 2, 8, 2))
    check("chunks IHDR, IDAT, IEND with valid CRCs", kinds == [b"IHDR", b"IDAT", b"IEND"] and crcs_ok)
    check("every row uses filter 0", [f for f, _ in rows] == [0, 0])
    check("BGRA converted to RGB, rows top-down",
          [r for _, r in rows] == [[(255, 0, 0), (0, 255, 0), (0, 0, 255)],
                                   [(255, 255, 255), (0, 0, 0), (10, 20, 30)]], str(rows))
    w, h, depth, ctype, kinds, crcs_ok, rows = decode_png(pngenc.png_bytes(3, 2, bgra, alpha=True))
    check("RGBA: colour type 6", (w, h, depth, ctype) == (3, 2, 8, 6) and crcs_ok)
    check("RGBA keeps the alpha channel",
          [r for _, r in rows] == [[(255, 0, 0, 255), (0, 255, 0, 0), (0, 0, 255, 128)],
                                   [(255, 255, 255, 255), (0, 0, 0, 255), (10, 20, 30, 7)]], str(rows))
    print(f"\npngenc: {'ALL PASS' if not FAILURES else f'{len(FAILURES)} FAILURES'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

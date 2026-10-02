"""Tests for harness/uoart.py.

Deterministic: a synthetic UOOFile container covers the index chain, the crop
to the stored opaque box (inclusive edges) and fully transparent / unknown
entries. Then, when the install dir exists, real art.uoo items (read-only).

Run: python harness/test_uoart.py
"""
import os
import struct
import sys
import tempfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uoart  # noqa: E402
import uomap  # noqa: E402

FAILURES = []
RED, BLUE = 31 << 10, 31


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def entry(gid, w, h, pixels, box):
    raw = zlib.compressobj(wbits=-15)
    data = raw.compress(struct.pack(f"<{w * h}H", *pixels)) + raw.flush()
    payload = struct.pack("<HHHHHHI", w, h, *box, len(data)) + data
    return struct.pack("<Ii", gid, len(payload)) + payload


def png_size(data):
    return struct.unpack(">II", data[16:24])


def main():
    print("== synthetic container ==")
    w, h = 5, 4
    px = [0] * (w * h)
    px[1 * w + 1] = RED      # top-left of the opaque box
    px[2 * w + 3] = BLUE     # bottom-right
    blob = (struct.pack("<II", uomap.DATA_MAGIC, 1)
            + entry(7, w, h, px, (1, 1, 3, 2))
            + entry(8, 3, 3, [0] * 9, (3, 3, 0, 0))   # fully transparent: (w, h, 0, 0)
            + struct.pack("<Ii", 0, 0))
    fd, path = tempfile.mkstemp(suffix=".uoo")
    os.write(fd, blob)
    os.close(fd)
    images = uoart.UooImages(path)
    try:
        art = uoart.ItemArt(images)
        cw, ch, bgra = uoart.crop_bgra(images.get(7), images.bbox(7))
        check("crop keeps both inclusive edges", (cw, ch) == (3, 2), str((cw, ch)))
        check("corner pixels land at the crop corners",
              bgra[0:4] == bytes((0, 0, 255, 255)) and bgra[-4:] == bytes((255, 0, 0, 255)), bgra.hex())
        check("transparent pixels stay transparent", bgra[4:8] == bytes(4))
        check("png is the cropped size", png_size(art.png(7)) == (3, 2))
        check("fully transparent entry -> None", art.png(8) is None)
        check("unknown graphic -> None", art.png(9) is None)
    finally:
        images._mm.close()
        images._f.close()
        os.remove(path)

    if os.path.exists(uoart.ART_PATH):
        print("== real client art (read-only) ==")
        real = uoart.ItemArt()
        check("gold coin pile 0x0EEF crops to 32x24", png_size(real.png(0x0EEF)) == (32, 24))
        check("scales 0x1851 crop to 18x29", png_size(real.png(0x1851)) == (18, 29))

    print(f"\n{'FAILED: ' + ', '.join(FAILURES) if FAILURES else 'all passed'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

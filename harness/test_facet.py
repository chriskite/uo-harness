"""Tests for harness/facet.py and viz_server's /api/facet routes.

Uses a synthetic facet file (300x260 tiles, so chunks are clipped at the right
and bottom edges and runs cross the 256-tile chunk border). The format follows
upstream ClassicUO MultiMapLoader.LoadFacet. The real facet00.mul was checked
once against 40 bank markers (docs/NOTES.md); these tests don't depend on the
install dir.

Run: python harness/test_facet.py   (no network besides a private localhost port, <2 s)
"""
import json
import os
import struct
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import facet  # noqa: E402
import viz_server  # noqa: E402

FAILURES = []

RED, GREEN, BLUE, GREY = 0x7C00, 0x03E0, 0x001F, 0x4210  # ARGB1555
W, H = 300, 260


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {'' if cond else detail}")
    if not cond:
        FAILURES.append(name)


def row_runs(y):
    """Row y: 250 red, 20 green (crosses x=256), then 30 blue; the last row is all grey."""
    if y == H - 1:
        return [(255, GREY), (45, GREY)]
    return [(250, RED), (20, GREEN), (30, BLUE)]


def build(path):
    out = bytearray(struct.pack("<HH", W, H))
    for y in range(H):
        body = b"".join(struct.pack("<BH", n, c) for n, c in row_runs(y))
        out += struct.pack("<i", len(body)) + body
    with open(path, "wb") as f:
        f.write(out)


def png_pixels(png):
    """(width, height, rows of RGB tuples) of an unfiltered 8-bit RGB PNG."""
    w, h = struct.unpack(">II", png[16:24])
    idat = b""
    off = 8
    while off < len(png):
        (n,) = struct.unpack(">I", png[off:off + 4])
        tag = png[off + 4:off + 8]
        if tag == b"IDAT":
            idat += png[off + 8:off + 8 + n]
        off += 12 + n
    raw = zlib.decompress(idat)
    stride = 1 + w * 3
    rows = []
    for y in range(h):
        line = raw[y * stride + 1:(y + 1) * stride]
        rows.append([tuple(line[i:i + 3]) for i in range(0, len(line), 3)])
    return w, h, rows


class StubFeed:
    subscribers = []


def main():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "facet00.mul")
        build(path)
        fp = facet.FacetPicture(path)

        print("decode")
        m = fp.meta()
        check("size and chunk grid", (m["width"], m["height"], m["chunks_x"], m["chunks_y"]) == (300, 260, 2, 2), m)
        w, h, rows = png_pixels(fp.chunk_png(0, 0))
        check("full chunk is 256x256", (w, h) == (256, 256), (w, h))
        check("ARGB1555 red -> (255,0,0)", rows[0][0] == (255, 0, 0), rows[0][0])
        check("run crossing the chunk border: green up to x=255", rows[0][250] == (0, 255, 0) and rows[0][255] == (0, 255, 0))
        w, h, rows = png_pixels(fp.chunk_png(1, 0))
        check("edge chunk clipped to 44 columns", (w, h) == (44, 256), (w, h))
        check("green continues into chunk 1 (x=256..269)", rows[5][0] == (0, 255, 0) and rows[5][13] == (0, 255, 0))
        check("then blue", rows[5][14] == (0, 0, 255) and rows[5][43] == (0, 0, 255))
        w, h, rows = png_pixels(fp.chunk_png(1, 1))
        check("corner chunk clipped to 44x4", (w, h) == (44, 4), (w, h))
        check("5-bit 16 -> 131 like the client's table", rows[3][0] == (131, 131, 131), rows[3][0])
        check("out-of-range chunk -> None", fp.chunk_png(2, 0) is None and fp.chunk_png(-1, 0) is None)

        bad = os.path.join(tmp, "bad.mul")
        with open(bad, "wb") as f:
            f.write(struct.pack("<HHi", 10, 2, 999))
        try:
            facet.FacetPicture(bad)
            check("truncated file rejected", False)
        except ValueError:
            check("truncated file rejected", True)

        print("routes")
        srv = viz_server.VizServer(("127.0.0.1", 0), StubFeed(), tmp, os.path.join(tmp, "none.json"), path)
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{srv.server_address[1]}"
            meta = json.loads(urllib.request.urlopen(f"{base}/api/facet").read())
            check("/api/facet meta", meta.get("available") and meta.get("chunk") == 256, meta)
            r = urllib.request.urlopen(f"{base}/api/facet/1/1.png")
            check("chunk served as image/png", r.headers["Content-Type"] == "image/png"
                  and png_pixels(r.read())[:2] == (44, 4))
            for bad_url, code in (("/api/facet/5/5.png", 404), ("/api/facet/x/1.png", 400)):
                try:
                    urllib.request.urlopen(base + bad_url)
                    check(f"{bad_url} -> {code}", False)
                except urllib.error.HTTPError as e:
                    check(f"{bad_url} -> {code}", e.code == code, e.code)
        finally:
            srv.shutdown()
            srv.server_close()
        off = viz_server.VizServer(("127.0.0.1", 0), StubFeed(), tmp, os.path.join(tmp, "none.json"), None)
        check("--no-facet reports unavailable", off.facet is None and "disabled" in off.facet_error)
        off.server_close()

    print("\nALL PASS" if not FAILURES else f"\nFAILED: {FAILURES}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

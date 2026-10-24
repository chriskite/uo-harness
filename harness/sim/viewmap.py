"""The map under the viz Sim page (harness/viz_server.py /api/sim/map and /api/sim/los): facet 0's walkable floor and
walls as PNG chunks, and line of sight between points, from the client's map files (read-only, as the simulator's own
World: pathfind.Walk and los.Sight without ground items)."""
from __future__ import annotations

import os
import sys
import threading

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HARNESS not in sys.path:
    sys.path.insert(0, HARNESS)

import los  # noqa: E402
import pathfind  # noqa: E402
import pngenc  # noqa: E402

CACHE_N = 256
_lock = threading.Lock()
_geo = None                   # (walk, sight) once built
_cache: dict = {}             # (cx, cy, size) -> PNG bytes (insertion order: the oldest goes first)


def _get():
    """(walk, sight) for facet 0; RuntimeError without map geometry. Call with _lock held."""
    global _geo
    if _geo is None:
        walk = pathfind.Walkers().get(0)
        if walk is None:
            raise RuntimeError("no map geometry for facet 0 (install files missing?)")
        _geo = (walk, los.Sight(walk))
    return _geo


def _tile(walk, x, y):
    """(b, g, r, a) of a tile: floor grey by height, wall brown, nothing transparent."""
    objs = walk.objects(x, y)
    walls = [o for o in objs if o[2] & pathfind.POF_IMPASSABLE_OR_SURFACE and not o[2] & pathfind.POF_SURFACE]
    best = None
    for z, _h, flags, avg, _k in objs:
        if flags & pathfind.POF_SURFACE and not any(w[0] < avg + 16 and w[0] + w[1] > avg for w in walls):
            best = avg if best is None else max(best, avg)
    if best is not None:
        v = int(90 + max(0, min(120, (best + 20) * 1.5)))
        return v, v, v, 255
    if walls:
        return 50, 60, 70, 255
    return 0, 0, 0, 0


def chunk_png(cx: int, cy: int, size: int = 64) -> bytes | None:
    """The size x size tiles from (cx*size, cy*size) as a PNG (one pixel a tile); None outside the map."""
    with _lock:
        key = (cx, cy, size)
        if key in _cache:
            return _cache[key]
        walk, _sight = _get()
        x0, y0 = cx * size, cy * size
        if cx < 0 or cy < 0 or x0 >= walk.m.width or y0 >= walk.m.height:
            return None
        buf = bytearray(size * size * 4)
        for j in range(size):
            for i in range(size):
                x, y = x0 + i, y0 + j
                if x < walk.m.width and y < walk.m.height:
                    o = (j * size + i) * 4
                    buf[o:o + 4] = bytes(_tile(walk, x, y))
        png = pngenc.png_bytes(size, size, bytes(buf), alpha=True)
        _cache[key] = png
        while len(_cache) > CACHE_N:
            _cache.pop(next(iter(_cache)))
        return png


def los_many(frm, tos) -> list[bool]:
    """Whether a mobile at `frm` (x, y, z) sees one at each of `tos` (los.Sight.can_see)."""
    with _lock:
        _walk, sight = _get()
        return [sight.can_see(tuple(frm), tuple(to)) for to in tos]

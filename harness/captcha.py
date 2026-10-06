"""Captcha solver: reads the harvest captcha's digits straight from the gump
layout (ANTICHEAT.md §8.8/§8.13). No OCR, no screen capture: the digits are
drawn as `tilepic` dot glyphs at layout coordinates, and the dot cluster of
each digit is a fixed shape with displaced dots, so a layout carries the
answer explicitly.

Method: tilepics are split into the three digit clusters at the pair of x gaps
that leaves three plausible digits, the widest such pair (until 2026-10-06 simply
the two largest x gaps; see digit_clusters), each cluster is normalized (centroid-centered, y-span scaled to 1.0)
and matched against the captured reference set (data/captcha_font.json) with
a translation-aligned trimmed Chamfer distance (the dots are displaced, so
per-dot lattice assignment is ambiguous; trimmed nearest-neighbor means are
robust to it). A digit is accepted only when the best reference beats the
best reference of every *other* digit by MIN_MARGIN; anything less confident
returns None and the caller falls back to the pause + alert path instead of
guessing (a wrong answer costs a strike; 3 strikes = 6 h harvest block).

Calibration (offline, 2026-09-30): leave-one-out over the 21 captured digit
samples misclassifies only the two singleton digits (3 and 9 — no second
sample exists to vote against, an eval artifact; they are in the shipping
reference set). Jitter bootstrap (sigma ~3.5 px, 15 % dot dropout, 0-2 noise
dots, 40 draws per reference): at MIN_MARGIN 9 %, 99.2 % of accepted digits
are right and 12 % are rejected to the fallback. Digit 0 is a synthetic oval
ring — no captured sample yet; the margin gate rejects it if the real shape
differs.
"""

import json
import math
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_PATH = os.path.join(HERE, "data", "captcha_font.json")

MIN_MARGIN = 0.09          # best digit must beat every other digit by this
SHIFT = 0.09               # alignment search radius (normalized units)
STEPS = 9                  # shift search grid per axis
TRIM = 0.15                # drop the worst 15 % of nearest-neighbor distances
MIN_DOTS, MAX_DOTS = 6, 25  # per digit cluster (captured: 10-17)
MIN_YS, MAX_YS = 40, 120   # digit y-span in px (captured: 63-82)
MAX_XS = 120               # digit x-span in px (captured: 35-80)

_FONT = None


def _font():
    global _FONT
    if _FONT is None:
        with open(FONT_PATH) as f:
            _FONT = [(r["digit"], [tuple(p) for p in r["pts"]])
                     for r in json.load(f)["references"]]
    return _FONT


def _plausible(cl) -> bool:
    xs = [p[0] for p in cl]
    ys = [p[1] for p in cl]
    return (MIN_DOTS <= len(cl) <= MAX_DOTS
            and MIN_YS <= max(ys) - min(ys) <= MAX_YS
            and max(xs) - min(xs) <= MAX_XS)


def digit_clusters(layout: str):
    """The captcha's three digit dot clusters, left to right, as raw (x, y)
    lists — or None when the layout doesn't look like a captcha digit field
    (decoys have no tilepics; a partial/garbled gump fails the sanity gates).
    The two cuts are the x gaps that leave three plausible digits, the widest
    such pair (by its narrower gap, then their sum): the two widest gaps alone
    can both fall at one digit's edges, a stray dot 29 px off the 3rd digit of
    175 (live 2026-10-06 14:01) tying the 29 px gap between digits 1 and 2."""
    pts = [(int(x), int(y))
           for x, y, _ in re.findall(r"\{ tilepic (\d+) (\d+) (-?\d+) \}", layout)]
    if len(pts) < 3 * MIN_DOTS:
        return None
    pts.sort()
    gaps = [(b[0] - a[0], i + 1) for i, (a, b) in enumerate(zip(pts, pts[1:])) if b[0] > a[0]]
    best = None
    for n, (g1, c1) in enumerate(gaps):
        if not _plausible(pts[:c1]):
            continue
        for g2, c2 in gaps[n + 1:]:
            if _plausible(pts[c1:c2]) and _plausible(pts[c2:]):
                key = (min(g1, g2), g1 + g2)
                if best is None or key > best[0]:
                    best = (key, c1, c2)
    if best is None:
        return None
    _, c1, c2 = best
    return [pts[:c1], pts[c1:c2], pts[c2:]]


def submit_button(layout: str, guide_button: int = 1):
    """The captcha's submit button: the one reply button besides Guide (the
    id is random per captcha — 594, 843, 101, 690, 949, 383, 865 captured).
    Never key on a fixed id (ANTICHEAT.md §8.13)."""
    buttons = [int(b) for b in
               re.findall(r"\{ button \d+ \d+ \d+ \d+ 1 0 (\d+) \}", layout)]
    rest = [b for b in buttons if b != guide_button]
    return rest[0] if len(rest) == 1 else None


def _normalize(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    h = (max(ys) - min(ys)) or 1
    cx = sum(xs) / len(xs)
    cy = sum(ys) / len(ys)
    return [((x - cx) / h, (y - cy) / h) for x, y in pts]


def _dist(a, b):
    """Translation-aligned trimmed Chamfer distance between normalized point
    sets (lower = more similar). One squared-distance matrix per shift serves
    both directions (row minima: a -> b, column minima: b -> a)."""
    diffs = [[(x - u, y - v) for u, v in b] for x, y in a]
    ka = max(1, int(len(a) * (1 - TRIM)))
    kb = max(1, int(len(b) * (1 - TRIM)))
    best = 1e9
    for si in range(STEPS):
        dx = -SHIFT + 2 * SHIFT * si / (STEPS - 1)
        for sj in range(STEPS):
            dy = -SHIFT + 2 * SHIFT * sj / (STEPS - 1)
            m = [[(ex - dx) ** 2 + (ey - dy) ** 2 for ex, ey in row] for row in diffs]
            ab = sorted(math.sqrt(min(row)) for row in m)
            ba = sorted(math.sqrt(min(col)) for col in zip(*m))
            c = (sum(ab[:ka]) / ka + sum(ba[:kb]) / kb) / 2
            if c < best:
                best = c
    return best


def read_digit(pts):
    """(digit, distance, margin) for one normalized dot cluster: the nearest
    reference, its distance, and how much closer it is than the nearest
    reference of any other digit (1.0 = unopposed)."""
    n = _normalize(pts)
    scores = sorted((_dist(n, ref), digit) for digit, ref in _font())
    best_d, best_digit = scores[0]
    second_d = next(c for c, d in scores if d != best_digit)
    return best_digit, best_d, (second_d - best_d) / second_d


def solve(layout: str):
    """The captcha's 3-digit answer, or None when the layout isn't a readable
    captcha or any digit is below the confidence margin (the caller's pause +
    alert fallback covers those; never guess — a wrong answer is a strike)."""
    clusters = digit_clusters(layout)
    if clusters is None:
        return None
    digits = []
    for cl in clusters:
        digit, _, margin = read_digit(cl)
        if margin < MIN_MARGIN:
            return None
        digits.append(digit)
    return "".join(digits)

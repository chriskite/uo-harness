"""Tiny dependency-free SVG chart helper for the uo-harness paper.

Every function returns an <svg> string styled by the paper's CSS classes
(.c-axis .c-grid .c-tick .c-label .c-title .c-s1..c-s8 .c-val .c-legend).
Colors come from CSS, so charts match the page theme. Usage:

    import sys; sys.path.insert(0, r"<TEMP>/uo-paper")
    from svgchart import bar, hbar, line, heatmap, scatter
    svg = bar(["a","b"], {"commits": [3, 5]}, ylabel="commits/day")

Series are dicts name -> list[float] (insertion order = color order s1, s2, ...).
"""
from __future__ import annotations

import html
import math

W, H = 720, 320
PAD_L, PAD_R, PAD_T, PAD_B = 64, 20, 28, 56


def _esc(s) -> str:
    return html.escape(str(s), quote=True)


def _nice_max(v: float) -> float:
    if v <= 0:
        return 1.0
    e = 10 ** math.floor(math.log10(v))
    for m in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if v <= m * e:
            return m * e
    return 10 * e


def _fmt(v: float) -> str:
    if abs(v) >= 1e6:
        return f"{v/1e6:.1f}M".replace(".0M", "M")
    if abs(v) >= 1e4:
        return f"{v/1e3:.0f}k"
    if abs(v) >= 1000:
        return f"{v/1e3:.1f}k".replace(".0k", "k")
    if v == int(v):
        return str(int(v))
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _open(w, h, label):
    return (f'<svg class="chart" viewBox="0 0 {w} {h}" role="img" aria-label="{_esc(label)}" '
            f'xmlns="http://www.w3.org/2000/svg">')


def _legend(names, x, y):
    out, cx = [], x
    for i, n in enumerate(names):
        out.append(f'<rect x="{cx}" y="{y-9}" width="10" height="10" rx="2" class="c-s{i%8+1}"/>'
                   f'<text x="{cx+14}" y="{y}" class="c-legend">{_esc(n)}</text>')
        cx += 24 + 7 * len(str(n))
    return "".join(out)


def _yaxis(ymax, x0, x1, y0, y1, ylabel, ticks=5, fmt=_fmt):
    out = []
    for i in range(ticks + 1):
        v = ymax * i / ticks
        y = y0 - (y0 - y1) * i / ticks
        out.append(f'<line x1="{x0}" x2="{x1}" y1="{y:.1f}" y2="{y:.1f}" class="c-grid"/>'
                   f'<text x="{x0-6}" y="{y+4:.1f}" text-anchor="end" class="c-tick">{fmt(v)}</text>')
    if ylabel:
        out.append(f'<text transform="translate(14 {(y0+y1)/2:.0f}) rotate(-90)" text-anchor="middle" '
                   f'class="c-label">{_esc(ylabel)}</text>')
    return "".join(out)


def bar(categories, series, *, ylabel="", xlabel="", title="", stacked=False, w=W, h=H,
        values=False, every=1, rotate=0, ymax=None):
    """Vertical bars. Multiple series -> grouped (or stacked=True)."""
    names = list(series)
    n = len(categories)
    x0, x1, y0, y1 = PAD_L, w - PAD_R, h - PAD_B, PAD_T + (14 if len(names) > 1 else 0)
    if stacked:
        top = max((sum(series[k][i] for k in names) for i in range(n)), default=1)
    else:
        top = max((max(v) for v in series.values() if v), default=1)
    ym = ymax or _nice_max(top)
    out = [_open(w, h, title or ylabel), _yaxis(ym, x0, x1, y0, y1, ylabel)]
    slot = (x1 - x0) / max(n, 1)
    gw = slot * 0.78
    bw = gw if stacked else gw / len(names)
    for i, c in enumerate(categories):
        sx = x0 + slot * i + (slot - gw) / 2
        base = y0
        for j, k in enumerate(names):
            v = series[k][i]
            bh = (y0 - y1) * v / ym
            if stacked:
                bx, by = sx, base - bh
                base -= bh
            else:
                bx, by = sx + bw * j, y0 - bh
            out.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{max(bw-1,1):.1f}" height="{max(bh,0):.1f}" '
                       f'class="c-s{j%8+1}"><title>{_esc(c)} · {_esc(k)}: {_fmt(v)}</title></rect>')
            if values and v and not stacked:
                out.append(f'<text x="{bx+bw/2:.1f}" y="{by-3:.1f}" text-anchor="middle" class="c-val">{_fmt(v)}</text>')
        if i % every == 0:
            cx = x0 + slot * i + slot / 2
            if rotate:
                out.append(f'<text transform="translate({cx:.1f} {y0+12}) rotate({rotate})" text-anchor="end" '
                           f'class="c-tick">{_esc(c)}</text>')
            else:
                out.append(f'<text x="{cx:.1f}" y="{y0+16}" text-anchor="middle" class="c-tick">{_esc(c)}</text>')
    out.append(f'<line x1="{x0}" x2="{x1}" y1="{y0}" y2="{y0}" class="c-axis"/>')
    if xlabel:
        out.append(f'<text x="{(x0+x1)/2:.0f}" y="{h-6}" text-anchor="middle" class="c-label">{_esc(xlabel)}</text>')
    if len(names) > 1:
        out.append(_legend(names, x0, PAD_T))
    out.append("</svg>")
    return "".join(out)


def hbar(labels, values, *, xlabel="", title="", w=W, bar_h=20, label_w=200, fmt=_fmt, series_class=1):
    """Horizontal ranking bars (one series)."""
    n = len(labels)
    h = PAD_T + n * (bar_h + 6) + 34
    x0, x1 = label_w, w - 60
    xm = _nice_max(max(values) if values else 1)
    out = [_open(w, h, title or xlabel)]
    for i in range(5):
        x = x0 + (x1 - x0) * i / 4
        out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{PAD_T-6}" y2="{h-30}" class="c-grid"/>'
                   f'<text x="{x:.1f}" y="{h-16}" text-anchor="middle" class="c-tick">{fmt(xm*i/4)}</text>')
    for i, (lab, v) in enumerate(zip(labels, values)):
        y = PAD_T + i * (bar_h + 6)
        bw = (x1 - x0) * v / xm
        out.append(f'<text x="{x0-8}" y="{y+bar_h*0.7:.1f}" text-anchor="end" class="c-tick">{_esc(lab)}</text>'
                   f'<rect x="{x0}" y="{y}" width="{max(bw,1):.1f}" height="{bar_h}" rx="2" class="c-s{series_class}">'
                   f'<title>{_esc(lab)}: {fmt(v)}</title></rect>'
                   f'<text x="{x0+bw+5:.1f}" y="{y+bar_h*0.7:.1f}" class="c-val">{fmt(v)}</text>')
    if xlabel:
        out.append(f'<text x="{(x0+x1)/2:.0f}" y="{h-2}" text-anchor="middle" class="c-label">{_esc(xlabel)}</text>')
    out.append("</svg>")
    return "".join(out)


def line(xs, series, *, ylabel="", xlabel="", title="", w=W, h=H, xticks=None, dots=True,
         ymax=None, area=False, step=False):
    """Line chart over numeric xs. xticks: list of (x, label) pairs."""
    names = list(series)
    x0, x1, y0, y1 = PAD_L, w - PAD_R, h - PAD_B, PAD_T + (14 if len(names) > 1 else 0)
    xmin, xmax = min(xs), max(xs)
    span = (xmax - xmin) or 1
    top = max((max(v for v in s if v is not None) for s in series.values()), default=1)
    ym = ymax or _nice_max(top)
    px = lambda x: x0 + (x1 - x0) * (x - xmin) / span
    py = lambda y: y0 - (y0 - y1) * y / ym
    out = [_open(w, h, title or ylabel), _yaxis(ym, x0, x1, y0, y1, ylabel)]
    for j, k in enumerate(names):
        pts = [(px(x), py(y)) for x, y in zip(xs, series[k]) if y is not None]
        if not pts:
            continue
        if step:
            d = f"M{pts[0][0]:.1f},{pts[0][1]:.1f}" + "".join(
                f"H{a:.1f}V{b:.1f}" for a, b in pts[1:])
        else:
            d = "M" + "L".join(f"{a:.1f},{b:.1f}" for a, b in pts)
        if area:
            out.append(f'<path d="{d}L{pts[-1][0]:.1f},{y0}L{pts[0][0]:.1f},{y0}Z" class="c-area c-s{j%8+1}"/>')
        out.append(f'<path d="{d}" class="c-line c-s{j%8+1}"/>')
        if dots and len(pts) <= 60:
            for (a, b), x, y in zip(pts, [x for x, y in zip(xs, series[k]) if y is not None],
                                    [y for y in series[k] if y is not None]):
                out.append(f'<circle cx="{a:.1f}" cy="{b:.1f}" r="2.6" class="c-dot c-s{j%8+1}">'
                           f'<title>{_esc(k)}: {_fmt(y)}</title></circle>')
    for x, lab in (xticks or [(xmin, _fmt(xmin)), (xmax, _fmt(xmax))]):
        out.append(f'<text x="{px(x):.1f}" y="{y0+16}" text-anchor="middle" class="c-tick">{_esc(lab)}</text>')
    out.append(f'<line x1="{x0}" x2="{x1}" y1="{y0}" y2="{y0}" class="c-axis"/>')
    if xlabel:
        out.append(f'<text x="{(x0+x1)/2:.0f}" y="{h-6}" text-anchor="middle" class="c-label">{_esc(xlabel)}</text>')
    if len(names) > 1:
        out.append(_legend(names, x0, PAD_T))
    out.append("</svg>")
    return "".join(out)


def scatter(points, *, xlabel="", ylabel="", title="", w=W, h=H, xmax=None, ymax=None, groups=None):
    """points: list of (x, y) or (x, y, label). groups: optional list of group index per point."""
    x0, x1, y0, y1 = PAD_L, w - PAD_R, h - PAD_B, PAD_T
    xm = xmax or _nice_max(max(p[0] for p in points))
    ym = ymax or _nice_max(max(p[1] for p in points))
    out = [_open(w, h, title or ylabel), _yaxis(ym, x0, x1, y0, y1, ylabel)]
    for i in range(6):
        x = x0 + (x1 - x0) * i / 5
        out.append(f'<text x="{x:.1f}" y="{y0+16}" text-anchor="middle" class="c-tick">{_fmt(xm*i/5)}</text>')
    for i, p in enumerate(points):
        g = (groups[i] if groups else 0) % 8 + 1
        cx, cy = x0 + (x1 - x0) * p[0] / xm, y0 - (y0 - y1) * p[1] / ym
        tip = p[2] if len(p) > 2 else f"{_fmt(p[0])}, {_fmt(p[1])}"
        out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="3.5" class="c-dot c-s{g}"><title>{_esc(tip)}</title></circle>')
    out.append(f'<line x1="{x0}" x2="{x1}" y1="{y0}" y2="{y0}" class="c-axis"/>')
    if xlabel:
        out.append(f'<text x="{(x0+x1)/2:.0f}" y="{h-6}" text-anchor="middle" class="c-label">{_esc(xlabel)}</text>')
    out.append("</svg>")
    return "".join(out)


def heatmap(rows, cols, matrix, *, title="", w=W, cell_h=22, label_w=90, col_every=1, fmt=_fmt):
    """rows/cols labels; matrix[r][c] numeric. Intensity via opacity of .c-heat."""
    nr, nc = len(rows), len(cols)
    h = PAD_T + nr * cell_h + 30
    cw = (w - label_w - PAD_R) / max(nc, 1)
    mx = max((max(r) for r in matrix), default=1) or 1
    out = [_open(w, h, title)]
    for i, r in enumerate(rows):
        y = PAD_T + i * cell_h
        out.append(f'<text x="{label_w-6}" y="{y+cell_h*0.68:.1f}" text-anchor="end" class="c-tick">{_esc(r)}</text>')
        for j, v in enumerate(matrix[i]):
            op = 0.06 + 0.94 * (v / mx) if v else 0.0
            out.append(f'<rect x="{label_w+j*cw:.1f}" y="{y}" width="{cw-1.5:.1f}" height="{cell_h-1.5}" rx="2" '
                       f'class="c-heat-bg"/>')
            if v:
                out.append(f'<rect x="{label_w+j*cw:.1f}" y="{y}" width="{cw-1.5:.1f}" height="{cell_h-1.5}" rx="2" '
                           f'class="c-heat" fill-opacity="{op:.2f}"><title>{_esc(r)} · {_esc(cols[j])}: {fmt(v)}</title></rect>')
    for j, c in enumerate(cols):
        if j % col_every == 0:
            out.append(f'<text x="{label_w+j*cw+cw/2:.1f}" y="{PAD_T+nr*cell_h+16}" text-anchor="middle" '
                       f'class="c-tick">{_esc(c)}</text>')
    out.append("</svg>")
    return "".join(out)

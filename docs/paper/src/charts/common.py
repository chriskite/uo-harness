"""Shared helpers for charts/fig_*.py: load a dataset, save an SVG figure, matplotlib house style.

    from common import load, save, mpl, mpl_svg, PALETTE
    d = load("lumber_daily")            # data/lumber_daily.json
    save("lumber_daily", svg_string)    # figures/lumber_daily.svg, included as <!--#include figures/lumber_daily.svg-->

Simple bar/line/heatmap charts: svgchart.py (themed by the page CSS). Anything statistical
(densities, CDFs, posteriors, annotated scatter): matplotlib via mpl() + mpl_svg(fig, name).

For the pseudonymized edition build.py sets PAPER_FIGURES_DIR (a temp dir, so the committed
figures stay as they are) and PAPER_PSEUDONYMS (the rules); PSEUDO then rewrites every chart text.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
sys.path.insert(0, SRC)
from pseudonyms import from_env  # noqa: E402

PSEUDO = from_env()
FIGURES = os.environ.get("PAPER_FIGURES_DIR") or os.path.join(SRC, "figures")
PALETTE = ["#1f5f8b", "#b4572b", "#3d7f4f", "#b8892a", "#6b4fa0", "#2a9d8f", "#8a8f98", "#c44e74"]
INK, INK2, INK3, GRID = "#1f2328", "#4a5059", "#737a85", "#ece8df"


def load(name: str):
    with open(os.path.join(SRC, "data", f"{name}.json"), encoding="utf-8") as f:
        return json.load(f)


def save(name: str, svg: str) -> None:
    os.makedirs(FIGURES, exist_ok=True)
    with open(os.path.join(FIGURES, f"{name}.svg"), "w", encoding="utf-8") as f:
        f.write(svg)


def mpl():
    """Import matplotlib with the paper's house style; returns pyplot."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from cycler import cycler

    plt.rcParams.update({
        "svg.fonttype": "none",
        "font.family": "sans-serif",
        "font.sans-serif": ["Segoe UI", "Helvetica Neue", "Arial", "DejaVu Sans"],
        "font.size": 10.5,
        "text.color": INK,
        "axes.labelcolor": INK2,
        "axes.labelsize": 10.5,
        "axes.labelweight": "semibold",
        "axes.titlesize": 11.5,
        "axes.titleweight": "bold",
        "axes.edgecolor": "#9aa0a8",
        "axes.linewidth": 0.9,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "axes.axisbelow": True,
        "axes.prop_cycle": cycler(color=PALETTE),
        "grid.color": GRID,
        "grid.linewidth": 0.9,
        "xtick.color": INK3,
        "ytick.color": INK3,
        "xtick.labelsize": 9.5,
        "ytick.labelsize": 9.5,
        "legend.frameon": False,
        "legend.fontsize": 9.5,
        "lines.linewidth": 2.0,
        "figure.facecolor": "none",
        "axes.facecolor": "none",
        "savefig.transparent": True,
    })
    return plt


def mpl_svg(fig, name: str) -> str:
    """Serialize a matplotlib figure to a responsive inline SVG (class 'chart mpl')."""
    import matplotlib.pyplot as plt
    from matplotlib.text import Text
    from matplotlib.ticker import FixedFormatter, FuncFormatter

    if PSEUDO:  # before layout, so bbox_inches="tight" measures the replaced text
        for t in fig.findobj(Text):
            if t.get_text():
                t.set_text(PSEUDO.apply(t.get_text()))
        # tick labels are re-created from the formatter at draw time (set_*ticklabels makes a
        # FuncFormatter or FixedFormatter), so the mapping goes into the formatter itself
        for ax in fig.axes:
            for axis in (ax.xaxis, ax.yaxis):
                for fmt in (axis.get_major_formatter(), axis.get_minor_formatter()):
                    if isinstance(fmt, FixedFormatter):
                        fmt.seq = [PSEUDO.apply(str(s)) for s in fmt.seq]
                    elif isinstance(fmt, FuncFormatter):
                        fmt.func = lambda x, pos=None, f=fmt.func: PSEUDO.apply(str(f(x, pos)))
    plt.rcParams["svg.hashsalt"] = name  # unique, deterministic clip-path ids per figure
    buf = io.StringIO()
    fig.savefig(buf, format="svg", bbox_inches="tight", pad_inches=0.06)
    plt.close(fig)
    s = buf.getvalue()
    s = re.sub(r"^.*?(<svg)", r"\1", s, flags=re.S)
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    s = re.sub(r"<metadata>.*?</metadata>", "", s, flags=re.S)
    s = re.sub(r"<style[^>]*>.*?</style>", "", s, flags=re.S)  # matplotlib's global '*{...}' would leak into the page
    s = re.sub(r'(<svg[^>]*?)\swidth="[^"]*"', r"\1", s, count=1)
    s = re.sub(r'(<svg[^>]*?)\sheight="[^"]*"', r"\1", s, count=1)
    s = s.replace("<svg ", f'<svg class="chart mpl" role="img" aria-label="{name}" ', 1)
    return s.strip() + "\n"

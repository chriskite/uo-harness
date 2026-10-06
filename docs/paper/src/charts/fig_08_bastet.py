"""Bastet's three kills on a common clock (seconds from first sight) -> figures/08_bastet.svg."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load, save, mpl, mpl_svg, PALETTE, INK, INK3  # noqa: E402

d = load("08_bastet")
plt = mpl()
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

BLUE, RUST, GREEN, GOLD, PURPLE = PALETTE[0], PALETTE[1], PALETTE[2], PALETTE[3], PALETTE[4]
RED = "#b23a3a"

fig, ax = plt.subplots(figsize=(10, 3.5))
enc = d["encounters"]
labels = []
for i, e in enumerate(enc):
    y = len(enc) - 1 - i
    start = e["tracking"] if e.get("tracking") is not None else 0.0
    ax.plot([start, e["death"]], [y, y], color="#c9c4b8", lw=1.2, zorder=1)
    for h in e.get("hypothetical_cast") and [e["hypothetical_cast"]] or []:
        ax.barh(y, h[1] - h[0], left=h[0], height=0.34, color="none", edgecolor=BLUE,
                hatch="////", lw=1.0, zorder=2)
        ax.text((h[0] + h[1]) / 2, y + 0.27, "recall if pressed at 0.29 s", ha="center",
                va="bottom", fontsize=8.5, color=BLUE)
    for c in e["casts"]:
        if c[1] > c[0]:
            ax.barh(y, c[1] - c[0], left=c[0], height=0.34, color=BLUE, alpha=0.85, zorder=2)
            ax.plot([c[1]], [y], marker="|", ms=16, mew=2.2, color=RUST, zorder=4)
        else:
            ax.plot([c[0]], [y], marker="x", ms=7, mew=1.8, color=RUST, zorder=4)
    if e.get("gave_up") is not None:
        ax.annotate("escape gives up (3 tries)", (e["gave_up"], y), xytext=(e["gave_up"] + 0.6, y + 0.3),
                    fontsize=8.5, color=INK3, arrowprops=dict(arrowstyle="-", color=INK3, lw=0.8))
    if e.get("hits_span"):
        a, b = e["hits_span"]
        ax.barh(y, b - a, left=a, height=0.34, color=RED, alpha=0.12, lw=0, zorder=1)
        ax.text((a + b) / 2, y - 0.28, "six hits", ha="center", va="top", fontsize=8.5, color=RED)
    for h in e["hits"]:
        ax.plot([h], [y], marker="v", ms=7, color=RED, zorder=5)
    if e.get("attacking") is not None:
        ax.plot([e["attacking"]], [y], marker="D", ms=6, color=GOLD, zorder=5)
    if e.get("tracking") is not None:
        ax.plot([e["tracking"]], [y], marker="^", ms=8, color=PURPLE, zorder=5)
        ax.text(e["tracking"], y + 0.24, f"Tracking: {e['tracking_tiles']} tiles", ha="left",
                va="bottom", fontsize=8.5, color=PURPLE)
    ax.plot([e["death"]], [y], marker="X", ms=10, color=INK, zorder=6)
    ax.text(e["death"] + 0.25, y, f"dead {e['death']:.2f} s", va="center", fontsize=9, color=INK)
    tiles = f", seen at {e['sight_tiles']} tiles" if e.get("sight_tiles") else ""
    labels.append(f"{e['name']}{tiles}")

ax.axvline(0, color=INK3, lw=0.9, ls=":", zorder=0)
ax.set_yticks(range(len(enc)))
ax.set_yticklabels(list(reversed(labels)))
ax.set_ylim(-0.6, len(enc) - 0.4)
ax.set_xlim(-5.2, 16.6)
ax.set_xlabel("seconds from first sight of Bastet")
ax.grid(axis="y", visible=False)
handles = [
    Patch(facecolor=BLUE, alpha=0.85, label="recall cast (2.03 s to land)"),
    Line2D([], [], marker="|", ls="", ms=12, mew=2.2, color=RUST, label="cast broken / refused"),
    Line2D([], [], marker="D", ls="", ms=6, color=GOLD, label='"Bastet is attacking you!"'),
    Line2D([], [], marker="v", ls="", ms=7, color=RED, label="hit on us"),
    Line2D([], [], marker="^", ls="", ms=8, color=PURPLE, label="Tracking hit"),
    Line2D([], [], marker="X", ls="", ms=9, color=INK, label="death"),
]
ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=6, fontsize=8.8,
          handletextpad=0.4, columnspacing=1.2)
save("08_bastet", mpl_svg(fig, "08_bastet"))

"""Walked facet-0 tiles (binned) with rune-library landing tiles -> figures/05_reach.svg."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import PALETTE, load, mpl, mpl_svg, save  # noqa: E402

d = load("05_walk_reach")
B = d["bin"]
X0, X1, Y0, Y1 = 0, 5760, 0, 4096  # facet-0 overworld window (tiles); y grows south

grid = np.zeros(((Y1 - Y0) // B, (X1 - X0) // B))
for bx, by, n in d["cells"]:
    gx, gy = bx - X0 // B, by - Y0 // B
    if 0 <= gx < grid.shape[1] and 0 <= gy < grid.shape[0]:
        grid[gy, gx] = n
img = np.ma.masked_where(grid == 0, np.log1p(grid))

plt = mpl()
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

cmap = LinearSegmentedColormap.from_list("walk", ["#5b93bb", "#1f5f8b", "#0d2c44"])
fig, ax = plt.subplots(figsize=(10, 7.4))
ax.imshow(img, cmap=cmap, extent=(X0, X1, Y1, Y0), interpolation="nearest", aspect="equal", zorder=1)
w = np.array(d["witcher_table"])
p = np.array(d["dtf_places"])
ax.scatter(w[:, 0], w[:, 1], s=9, marker="o", facecolors="none", edgecolors=PALETTE[1], linewidths=0.8,
           label=f"Witcher rune landing tiles ({len(w)}, Cambria + DTF)", zorder=2)
ax.scatter(p[:, 0], p[:, 1], s=9, marker="^", color=PALETTE[4], alpha=0.8, linewidths=0,
           label=f"DTF named-place runes ({len(p)})", zorder=3)
offs = {"Cambria Rune Library": (40, 260), "Shelter Island inn": (110, -150),
        "DTF guild house (rune library, home landing)": (-1350, -260)}
for name, (x, y) in d["places"].items():
    ax.scatter([x], [y], s=70, marker="*", color=PALETTE[3], edgecolors="#1f2328", linewidths=0.6, zorder=4)
    dx, dy = offs.get(name, (60, -60))
    ax.annotate(name, (x, y), (x + dx, y + dy), fontsize=9, color="#1f2328",
                bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none", alpha=0.85),
                arrowprops=dict(arrowstyle="-", color="#737a85", lw=0.7), zorder=5)
ax.scatter([], [], s=40, marker="s", color="#1f5f8b", label=f"walked tiles ({d['distinct_tiles']:,} distinct, {B}x{B} cells)")
ax.set_xlim(X0, X1)
ax.set_ylim(Y1, Y0)
ax.set_xlabel("x (tiles, east →)")
ax.set_ylabel("y (tiles, south ↓)")
ax.legend(loc="lower left", fontsize=9, markerscale=1.6)
ax.grid(alpha=0.5)
save("05_reach", mpl_svg(fig, "05_reach"))

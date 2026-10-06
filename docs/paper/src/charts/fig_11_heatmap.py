"""Hour-of-day x day heatmaps: human prompts (top) and assistant turns (bottom), CDT.

Writes figures/11_heatmap.svg from data/11_heatmap.json.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load, save, mpl, mpl_svg, INK2  # noqa: E402

h = load("11_heatmap")
plt = mpl()
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

days = [d[5:] for d in h["days"]]
panels = [
    ("human prompts", h["user_prompts"], LinearSegmentedColormap.from_list("g", ["#f6f4ee", "#3d7f4f"]), True),
    ("assistant turns", h["assistant_turns"], LinearSegmentedColormap.from_list("b", ["#f6f4ee", "#1f5f8b"]), False),
]
fig, axes = plt.subplots(2, 1, figsize=(10, 5.6), sharex=True)
for ax, (name, m, cmap, annotate) in zip(axes, panels):
    im = ax.imshow(m, aspect="auto", cmap=cmap, interpolation="nearest")
    ax.set_yticks(range(len(days)), days)
    ax.set_ylabel(name)
    ax.grid(False)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_xticks([i - 0.5 for i in range(25)], minor=True)
    ax.set_yticks([i - 0.5 for i in range(len(days) + 1)], minor=True)
    ax.grid(which="minor", color="white", linewidth=1.2)
    ax.tick_params(which="minor", length=0)
    vmax = max(max(r) for r in m)
    if annotate:
        for i, row in enumerate(m):
            for j, v in enumerate(row):
                if v:
                    ax.text(j, i, str(v), ha="center", va="center", fontsize=7.5,
                            color="white" if v > vmax * 0.55 else INK2)
    cb = fig.colorbar(im, ax=ax, fraction=0.025, pad=0.01)
    cb.outline.set_visible(False)
    cb.ax.tick_params(labelsize=8.5)
axes[0].axvspan(-0.5, 8.5, color="#ece8df", alpha=0.35, lw=0)
axes[0].text(4, 4, "no prompts\n00:00–08:59", ha="center", va="center", fontsize=10, color=INK2, style="italic")
axes[1].set_xticks(range(24), [f"{i:02d}" for i in range(24)])
axes[1].set_xlabel("hour of day (CDT)")
fig.tight_layout()
save("11_heatmap", mpl_svg(fig, "11_heatmap"))

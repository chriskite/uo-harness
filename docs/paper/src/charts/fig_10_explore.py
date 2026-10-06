"""Exploration curve: cumulative distinct lumber spots vs trip index, new-spot trips marked by character.

Writes figures/10_explore.svg from data/10_explore.json.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import INK2, INK3, PALETTE, load, mpl, mpl_svg, save  # noqa: E402

d = load("10_explore")
trips = d["trips"]  # [id, start, character, spot, new_spot, cumulative_distinct]
colors = {"TestWorth": PALETTE[6], "Hackworth": PALETTE[0], "Outland Dan": PALETTE[1]}
plt = mpl()
fig, ax = plt.subplots(figsize=(7.2, 3.6))
x = list(range(1, len(trips) + 1))
ax.step(x, [t[5] for t in trips], where="post", color=INK3, lw=1.4, zorder=2)
ax.plot(x, x, color="#d9d4c7", lw=1, ls=":", zorder=1)
ax.text(28.5, 36.5, "every trip a new spot", fontsize=8.5, color=INK3, ha="center", va="center",
        rotation=45, rotation_mode="anchor", transform_rotates_text=True)
for char, color in colors.items():
    pts = [(i + 1, t[5]) for i, t in enumerate(trips) if t[2] == char and t[4]]
    xs, ys = zip(*pts)
    ax.scatter(xs, ys, s=26, color=color, zorder=3, label=f"{char}: new spot")
first = next(i for i, t in enumerate(trips) if t[1] > d["planner_live"]) + 1
ax.axvline(first - 0.5, color=INK3, ls="--", lw=0.9)
ax.text(first + 1.0, 24, "Thompson-sampling planner\nshipped 10-02 23:26", fontsize=8.8, color=INK2)
greedy = next(i for i, t in enumerate(trips) if t[0] == d["greedy_from_trip"]) + 1
ax.axvline(greedy - 0.5, color=INK3, ls="--", lw=0.9)
ax.text(greedy - 1, 22, "greedy picks\nfrom 10-05 22:49", fontsize=8.8, color=INK2, ha="right")
ax.set_xlim(0, len(trips) + 1)
ax.set_ylim(0, 44)
ax.set_xlabel("lumber trip number (in start order)")
ax.set_ylabel("distinct spots visited so far")
ax.legend(loc="lower right", fontsize=8.8)
fig.tight_layout()
save("10_explore", mpl_svg(fig, "10_explore"))

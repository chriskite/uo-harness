"""Harvest-attempt outcomes, per-tree targeting vs Smart Harvest -> figures/06_harvest_outcomes.svg."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import PALETTE, load, mpl, mpl_svg, save  # noqa: E402

d = load("06_harvest")["outcomes"]
eras = [("smart_harvest", "Smart Harvest\n(target yourself)"), ("per_tree", "per-tree\ntargeting")]
kinds = [
    ("success", "success", PALETTE[2]),
    ("fail", "fail (500495)", PALETTE[6]),
    ("depleted", "tree depleted", PALETTE[3]),
    ("nothing_near", "nothing nearby", PALETTE[1]),
    ("unreachable", "unreachable", PALETTE[4]),
    ("not_tree", "not a tree", PALETTE[7]),
]

plt = mpl()
fig, ax = plt.subplots(figsize=(10, 2.5))
for yi, (era, label) in enumerate(eras):
    counts = d[era]
    tot = sum(counts.values())
    left = 0.0
    for k, name, color in kinds:
        n = counts.get(k, 0)
        if not n:
            continue
        share = 100 * n / tot
        ax.barh(yi, share, left=left, color=color, height=0.62, label=name if yi == 1 or k == "nothing_near" else None)
        if share >= 4:
            ax.text(left + share / 2, yi, f"{n:,}\n{share:.0f}%", ha="center", va="center", fontsize=8.5,
                    color="white" if k == "success" else "#1f2328")
        left += share
    ax.text(101, yi, f"{tot:,}", va="center", fontsize=9)
ax.set_yticks(range(len(eras)), [e[1] for e in eras])
ax.set_xlim(0, 108)
ax.set_xticks([0, 20, 40, 60, 80, 100])
ax.set_xlabel("share of harvest attempts (%)  (right: attempts)")
ax.grid(axis="y", visible=False)
h, lab = ax.get_legend_handles_labels()
order = [lab.index(n) for _, n, _ in kinds if n in lab]
ax.legend([h[i] for i in order], [lab[i] for i in order], ncol=6, loc="upper center",
          bbox_to_anchor=(0.5, -0.32), fontsize=9)
save("06_harvest_outcomes", mpl_svg(fig, "06_harvest_outcomes"))

"""Isotonic fit of P(regrown | gap) for depleted trees retried later (figures/07_regrowth.svg)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load, save, mpl, mpl_svg, PALETTE, INK2, INK3

d = load("07_regrowth")
cur = d["store_2026_10_05"]["curve"]
plt = mpl()
fig, ax = plt.subplots(figsize=(7.2, 3.7))

X_END = 6000.0
starts = [b["from_min"] for b in cur]
ends = starts[1:] + [X_END]
for b, x1 in zip(cur, ends):
    x0 = b["from_min"]
    ax.hlines(b["p"], x0, x1, color=PALETTE[0], lw=2.4, zorder=4)
    if x1 / x0 > 1.15:
        ax.text((x0 * x1) ** 0.5, b["p"] + 0.035, f"n={b['n']}", ha="center", fontsize=8.2, color=PALETTE[0])
small = [b["n"] for b, x1 in zip(cur, ends) if x1 / b["from_min"] <= 1.15]
ax.text(36, 0.36, "n=" + " · ".join(map(str, small)), ha="center", fontsize=8.2, color=PALETTE[0])
for (x1, p0), p1 in zip(zip(ends[:-1], [b["p"] for b in cur[:-1]]), [b["p"] for b in cur[1:]]):
    ax.vlines(x1, p0, p1, color=PALETTE[0], lw=1.0, alpha=0.6, zorder=3)

# 2026-10-02 four-bin table (docs/NOTES.md): >60 drawn out to 4 h
old = d["doc_2026_10_02"]["bins"]
spans = {"15-30": (15, 30), "30-45": (30, 45), "45-60": (45, 60), ">60": (60, 240)}
for i, b in enumerate(old):
    x0, x1 = spans[b["gap"]]
    p = b["regrown"] / b["n"]
    ax.hlines(p, x0, x1, color=PALETTE[1], lw=5, alpha=0.35, zorder=2,
              label="2026-10-02 bins (137 pairs)" if i == 0 else None)

ax.axhline(0.6, color=INK3, lw=1, ls=(0, (4, 3)), zorder=1)
ax.text(16, 0.615, "REGROW_P = 0.6", fontsize=8.6, color=INK3)
ax.axvline(65, color=PALETTE[2], lw=1.4, zorder=1)
ax.text(68, 0.05, "65 min window", fontsize=9, color=PALETTE[2])
ax.axvline(20, color=PALETTE[7], lw=1.2, ls=":", zorder=1)
ax.text(19, 0.30, "old 20 min", fontsize=8.6, color=PALETTE[7], rotation=90, ha="right", va="center")

ax.plot([], [], color=PALETTE[0], lw=2.4, label="isotonic fit, 2026-10-05 (277 pairs)")
ax.legend(loc="lower right", fontsize=8.8)
ax.set_xscale("log")
ax.set_xlim(14, X_END)
ax.set_xticks([15, 30, 60, 120, 240, 480, 1440, 4320])
ax.set_xticklabels(["15", "30", "60", "120", "240", "480", "1 day", "3 days"])
ax.set_ylim(-0.03, 1.1)
ax.set_xlabel("gap between 'depleted' and the next try on the same tree (minutes, log scale)")
ax.set_ylabel("P(tree has regrown)")

save("07_regrowth", mpl_svg(fig, "07_regrowth"))

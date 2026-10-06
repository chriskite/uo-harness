"""Chop-cycle gaps before and after script pace (2026-10-04 22:34) -> figures/06_chop_pace.svg."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import INK2, INK3, PALETTE, load, mpl, mpl_svg, save  # noqa: E402

d = load("06_harvest")
g, cap = d["gaps"], d["capture"]
bins = g["bins"]
mids = [(a + b) / 2 for a, b in zip(bins, bins[1:])]
width = bins[1] - bins[0]

plt = mpl()
fig, ax = plt.subplots(figsize=(10, 3.3))
for key, color, label in (("human", PALETTE[0], "human texture (to 2026-10-04 22:34)"),
                          ("script", PALETTE[1], "script pace (from 2026-10-04 22:34)")):
    s = g[key]
    tot = s["n"]
    ax.bar(mids, [100 * c / tot for c in s["hist"]], width=width * 0.92, color=color, alpha=0.78,
           label=f"{label}: n = {tot:,}, median {s['median']:.2f} s")
    ax.axvline(s["median"], color=color, lw=1.4, ls="--")

ax.axvline(cap["server_reply_s"], color=INK3, lw=1.2, ls=":")
ax.text(cap["server_reply_s"] - 0.15, ax.get_ylim()[1] * 0.97, f"server's chop\n{cap['server_reply_s']} s",
        ha="right", va="top", fontsize=9, color=INK2)
ax.set_xlim(3, 15)
ax.set_xlabel("seconds between consecutive chop results at one stand or tree")
ax.set_ylabel("share of gaps (%)")
ax.legend(loc="upper right", bbox_to_anchor=(1.0, 0.72), facecolor="white", framealpha=0.9, frameon=True, edgecolor="none")
save("06_chop_pace", mpl_svg(fig, "06_chop_pace"))

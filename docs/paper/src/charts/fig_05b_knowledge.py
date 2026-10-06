"""The knowledge table at the snapshot: importance of active entries by origin (share of each
group) and the share of active entries any search or brief ever returned -> figures/05b_knowledge.svg."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import INK2, PALETTE, load, mpl, mpl_svg, save  # noqa: E402

d = load("05b_knowledge")
imp = d["importance_by_source"]
groups = {"Discord pipeline (community, doc)": ("community", "doc"),
          "Seer and owner (other sources)": ("observed", "user", "wiki", "inferred")}
gcol = [PALETTE[6], PALETTE[4]]

plt = mpl()
fig, (ax, bx) = plt.subplots(1, 2, figsize=(10.4, 3.9), gridspec_kw={"width_ratios": [1.25, 1]})

levels = list(range(1, 11))
w = 0.4
for gi, (name, srcs) in enumerate(groups.items()):
    counts = [sum(imp.get(s, {}).get(str(i), 0) for s in srcs) for i in levels]
    n = sum(counts)
    ax.bar([i + (gi - 0.5) * w for i in levels], [c / n * 100 for c in counts], width=w, color=gcol[gi],
           label=f"{name}, n = {n:,}")
ax.axvline(6.5, color=INK2, lw=0.9, ls="--")
ax.text(6.62, 44, "brief's standing list:\nimportance ≥ 7", fontsize=8.6, color=INK2, va="top")
ax.set_xticks(levels)
ax.set_xlabel("importance (1–10, set by the writer)")
ax.set_ylabel("% of the group's active entries")
ax.set_ylim(0, 75)
ax.set_title("Importance by origin", loc="left")
ax.legend(loc="upper left", fontsize=8.4, framealpha=0.9)
ax.grid(axis="x", visible=False)

order = ["community", "doc", "wiki", "observed", "user"]
ret = d["returned_by_source"]
shares = [ret[s]["returned"] / ret[s]["active"] * 100 for s in order]
cols = [PALETTE[6], PALETTE[6], PALETTE[4], PALETTE[4], PALETTE[4]]
ys = list(range(len(order)))[::-1]
bx.barh(ys, shares, color=cols, height=0.62)
for y, s, sh in zip(ys, order, shares):
    bx.text(sh + 1.5, y, f'{ret[s]["returned"]:,} of {ret[s]["active"]:,}', va="center", fontsize=9, color=INK2)
bx.set_yticks(ys)
bx.set_yticklabels(order, family="monospace", fontsize=9.5)
bx.set_xlim(0, 118)
bx.set_xticks([0, 25, 50, 75, 100])
bx.set_xlabel("% of active entries ever returned")
bx.set_title("Ever recalled, by source_type", loc="left")
bx.grid(axis="y", visible=False)
fig.tight_layout(w_pad=2.2)
save("05b_knowledge", mpl_svg(fig, "05b_knowledge"))

"""Planner snapshot: field-rate posteriors of the measured spots and P(best) over all eligible spots
(figures/07_spots.svg)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load, save, mpl, mpl_svg, PALETTE, INK2, INK3, GRID

d = load("07_spot_posteriors")
spots = d["spots"]
pick = d["pick"][0] if isinstance(d["pick"], list) else d["pick"]
plt = mpl()
fig, (ax, bx) = plt.subplots(1, 2, figsize=(10, 5.0), gridspec_kw={"width_ratios": [1.05, 1]})

# left: posterior mean field rate with 80 % interval, spots with measured field time
meas = sorted([s for s in spots if s["visited"] and s["field_h"] > 0], key=lambda s: s["rate_logs_h"])
prior = 2316
ax.axvspan(1246, 3540, color=GRID, alpha=0.9, zorder=0)
ax.axvline(prior, color=INK3, lw=1, ls=(0, (4, 3)), zorder=1)
ax.text(prior, len(meas) - 0.2, "prior of an unvisited spot\n2,316 [1,246–3,540]", ha="center", va="bottom",
        fontsize=8.4, color=INK3)
for i, s in enumerate(meas):
    col = INK3 if s["status"] != "active" else PALETTE[0]
    lo, hi = s["rate_80"]
    ax.hlines(i, lo, hi, color=col, lw=1.6, zorder=2)
    ax.plot([s["rate_logs_h"]], [i], "o", color=col, ms=4 + 2.2 * s["trips"] ** 0.5 * 2, zorder=3,
            mec="white", mew=0.8)
ax.set_yticks(range(len(meas)))
ax.set_yticklabels([f"{s['id']}  ({s['trips']} tr, {s['field_h']:.2f} h)" for s in meas], fontsize=8.6)
ax.set_ylim(-0.7, len(meas) + 0.9)
ax.set_xlim(900, 4400)
ax.set_xlabel("field rate λ, logs per field hour (mean, 80 % interval)")
ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))
ax.grid(axis="y", visible=False)

# right: P(best) across all eligible spots, sorted
el = sorted([s for s in spots if s["eligible"]], key=lambda s: -s["p_best"])
cols = [PALETTE[0] if s["visited"] else PALETTE[3] for s in el]
xs = range(len(el))
bx.bar(xs, [s["p_best"] for s in el], width=0.85, color=cols, zorder=2)
uni = 1 / len(el)
bx.axhline(uni, color=INK3, lw=1, ls=(0, (4, 3)))
bx.text(len(el) - 1, uni + 0.0006, f"uniform 1/{len(el)}", ha="right", fontsize=8.4, color=INK3)
for sid, txt, (dx, ty) in ((el[0]["id"], "max", (14, 0.029)), (pick["greedy"], "greedy best", (4, 0.020)),
                           (pick["spot"], "the pick", (8, 0.0145))):
    i = next(j for j, s in enumerate(el) if s["id"] == sid)
    p = el[i]["p_best"]
    bx.annotate(f"{sid}\n{txt}, {p:.3f}", (i, p), xytext=(i + dx, ty),
                fontsize=8.4, color=INK2, arrowprops=dict(arrowstyle="-", color=INK3, lw=0.8))
from matplotlib.patches import Patch
bx.legend(handles=[Patch(color=PALETTE[0], label="visited (≥ 1 trip row)"),
                   Patch(color=PALETTE[3], label="never visited")], loc="upper right", fontsize=8.8)
bx.set_xlim(-1, len(el))
bx.set_ylim(0, 0.034)
bx.set_xlabel(f"eligible spots, sorted by P(best) (n = {len(el)})")
bx.set_ylabel("P(best) over 2,000 Thompson draws")
bx.grid(axis="x", visible=False)

fig.tight_layout(w_pad=2.5)
save("07_spots", mpl_svg(fig, "07_spots"))

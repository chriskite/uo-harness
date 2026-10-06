"""Bayesian appendix: field-rate prior vs posteriors, and Thompson value draws vs the max over all spots
(figures/22b_bayes.svg)."""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load, save, mpl, mpl_svg, PALETTE, INK2, INK3

d = load("22b_bayes")
plt = mpl()
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 3.9), gridspec_kw={"wspace": 0.28})


def gamma_pdf(x, a, b):
    """Gamma(shape a, rate b) density at x > 0."""
    return math.exp(a * math.log(b) - math.lgamma(a) + (a - 1) * math.log(x) - b * x)


xs = [10 * i for i in range(1, 701)]
curves = [
    ("prior (every spot)", d["prior"]["alpha0"], d["prior"]["beta0_h"], INK3, (0, (4, 3))),
    ("witcher_23 (%d trips)" % d["witcher_23"]["trips"], d["witcher_23"]["alpha"], d["witcher_23"]["beta_h"], PALETTE[0], "-"),
    ("horseshoe_bay (%d trips)" % d["horseshoe_bay"]["trips"], d["horseshoe_bay"]["alpha"], d["horseshoe_bay"]["beta_h"],
     PALETTE[1], "-"),
]
for label, a, b, col, ls in curves:
    ax1.plot(xs, [1000 * gamma_pdf(x, a, b) for x in xs], color=col, ls=ls, lw=2.0, label=label)
ax1.axvline(d["prior"]["mu"], color=INK3, lw=0.9, ls=":")
ax1.text(d["prior"]["mu"] + 70, 2.35, "prior mean\n%s" % f"{d['prior']['mu']:,.0f}", fontsize=8.4, color=INK3, va="top",
         ha="left")
ax1.set_xlim(0, 7000)
ax1.set_ylim(0, 3.3)
ax1.set_xlabel("field rate λ (logs per field hour)")
ax1.set_ylabel("density (per 1,000 logs/h)")
ax1.set_title("Field rate: prior and posteriors", fontsize=10.5, loc="left")
ax1.legend(loc="upper right", fontsize=8.4)
ax1.set_xticks([0, 1000, 2000, 3000, 4000, 5000, 6000, 7000])
ax1.set_xticklabels([f"{v:,}" for v in [0, 1000, 2000, 3000, 4000, 5000, 6000, 7000]])


def density(h):
    n = sum(h["counts"])
    centres = [h["lo"] + h["width"] * (i + 0.5) for i in range(len(h["counts"]))]
    return centres, [c / n / h["width"] * 1000 for c in h["counts"]]


series = [
    ("witcher_23 (greedy best, P(best) %.3f)" % d["witcher_23"]["p_best"], d["draws"]["witcher_23"], PALETTE[0], "-"),
    ("horseshoe_bay", d["draws"]["horseshoe_bay"], PALETTE[1], "-"),
    ("witcher_279 (never visited)", d["draws"]["witcher_279"], PALETTE[3], "-"),
    ("best of all %d eligible spots" % d["eligible"], d["max"], PALETTE[4], (0, (4, 2))),
]
for label, h, col, ls in series:
    x, y = density(h)
    ax2.plot(x, y, color=col, ls=ls, lw=1.8, label=label)
ax2.set_xlim(0, 7000)
ax2.set_ylim(0, 3.4)
ax2.set_xlabel("one Thompson draw of net stored logs/trip-h")
ax2.set_ylabel("density (per 1,000 logs/trip-h)")
ax2.set_title("Why the best-measured spot rarely wins a draw", fontsize=10.5, loc="left")
ax2.legend(loc="upper right", fontsize=8.4)
ax2.set_xticks([0, 1000, 2000, 3000, 4000, 5000, 6000, 7000])
ax2.set_xticklabels([f"{v:,}" for v in [0, 1000, 2000, 3000, 4000, 5000, 6000, 7000]])
for ax in (ax1, ax2):
    ax.tick_params(axis="y", colors=INK3)
    ax.yaxis.label.set_color(INK2)

save("22b_bayes", mpl_svg(fig, "22b_bayes"))

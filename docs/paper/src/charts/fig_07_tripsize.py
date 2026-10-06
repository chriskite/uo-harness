"""Net stored logs/hour vs trip size Q under the renewal-reward model, per spot (figures/07_tripsize.svg)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load, save, mpl, mpl_svg, PALETTE, INK2, INK3

d = load("07_tripsize_curves")["curves"]
plt = mpl()
fig, ax = plt.subplots(figsize=(10, 4.4))

spots = [("witcher_23", PALETTE[0]), ("horseshoe_bay", PALETTE[1]),
         ("terran_wilds", PALETTE[2]), ("witcher_291", PALETTE[4])]

ref = d["reference_h0"]
xs, ys = zip(*ref["series"])
ax.plot(xs, ys, color=INK3, lw=1.4, ls=(0, (4, 3)), zorder=1)
ax.text(10000 * 1.03, ys[-1], "no hazards\n(λ 2,000, T 150 s)", color=INK3, fontsize=9, va="center")


def at(series, q):
    """Linear interpolation of the series at q."""
    for (q0, v0), (q1, v1) in zip(series, series[1:]):
        if q0 <= q <= q1:
            return v0 + (v1 - v0) * (q - q0) / (q1 - q0)
    return series[-1][1]


LABEL_DY = {"terran_wilds": 55, "witcher_291": -70}
for sid, col in spots:
    c = d[sid]
    s = c["series"]
    xs, ys = zip(*s)
    ax.plot(xs, ys, color=col, zorder=3)
    qs = c["q_star_uncapped"]
    ax.plot([qs], [c["rate_at_q_star"]], "o", color=col, ms=7, zorder=5)
    sq = c["sqrt_rule_q"]
    ax.plot([sq], [at(s, sq)], marker="^", mfc="white", mec=col, mew=1.6, ms=7, ls="none", zorder=5)
    g = c["grove_logs"]
    if g < qs:
        ax.plot([g], [at(s, g)], marker="s", color=col, ms=6, zorder=5)
        ax.vlines(g, at(s, g) - 90, at(s, g) + 90, color=col, lw=1.2, ls=":", zorder=2)
    label = f"{sid}  (λ {c['lam']:,}, T {c['T_s']} s,\nh_D {c['hz'][0]:.2f}, h_S {c['hz'][1]:.2f} /field h)"
    ax.text(10000 * 1.03, ys[-1] + LABEL_DY.get(sid, 0), label, color=col, fontsize=8.6, va="center")

ax.set_xscale("log")
ax.set_xlim(100, 10000)
ax.set_xticks([100, 200, 500, 1000, 2000, 5000, 10000])
ax.set_xticklabels(["100", "200", "500", "1,000", "2,000", "5,000", "10,000"])
ax.set_ylim(800, 2900)
ax.set_xlabel("logs per trip Q (log scale)")
ax.set_ylabel("net stored logs per agent-hour")
ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))

# marker legend
h1, = ax.plot([], [], "o", color=INK2, ms=7, label="Q* (renewal-reward optimum)")
h2, = ax.plot([], [], "^", mfc="white", mec=INK2, mew=1.6, ms=7, label="first-order rule λ√(2T/h_D)")
h3, = ax.plot([], [], "s", color=INK2, ms=6, label="grove capacity cap (where it binds)")
ax.legend(handles=[h1, h2, h3], loc="lower right", fontsize=9)

save("07_tripsize", mpl_svg(fig, "07_tripsize"))

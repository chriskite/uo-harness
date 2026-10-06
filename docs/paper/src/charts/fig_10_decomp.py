"""Throughput decomposition: per-day chop cadence, success rate, logs per success (small multiples) and a
multiplicative waterfall from 09-29 to 10-05 in implied logs per chop-hour.

Writes figures/10_decomp.svg from data/10_decomp.json.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import INK, INK2, INK3, PALETTE, load, mpl, mpl_svg, save  # noqa: E402

days = load("10_decomp")["days"]
colors = {"TestWorth": PALETTE[6], "Hackworth": PALETTE[0], "Outland Dan": PALETTE[1]}
plt = mpl()
fig = plt.figure(figsize=(10, 3.7))
gs = fig.add_gridspec(1, 4, width_ratios=[1, 1, 1, 1.75], wspace=0.55)
x = list(range(len(days)))
lab = [d["date"][5:] for d in days]
col = [colors[d["character"]] for d in days]
panels = [
    ("cadence_s", "median chop cycle (s)", (0, 11), "{:.1f}"),
    ("success_rate", "success / (success + fail)", (0, 1.1), "{:.2f}"),
    ("logs_per_success", "logs per successful chop", (0, 9.5), "{:.1f}"),
]
for k, (key, ylabel, ylim, fmt) in enumerate(panels):
    ax = fig.add_subplot(gs[0, k])
    v = [d[key] for d in days]
    ax.bar(x, v, 0.7, color=col)
    for i in (0, len(days) - 1):
        ax.text(i, v[i] + ylim[1] * 0.02, fmt.format(v[i]), ha="center", fontsize=8.3, color=INK)
    ax.set_xticks(x, lab, rotation=90, fontsize=8.3)
    ax.set_ylim(*ylim)
    ax.set_ylabel(ylabel, fontsize=9.5)

# waterfall (log scale): 09-29 -> 10-05
a = days[0]
z = next(d for d in days if d["date"] == "2026-10-05")  # the best completed trip's day
steps = [
    ("09-29\nimplied", a["implied_logs_per_chop_h"], None),
    ("cycle\n×%.2f" % (a["cadence_s"] / z["cadence_s"]), a["cadence_s"] / z["cadence_s"], PALETTE[5]),
    ("success\n×%.2f" % (z["success_rate"] / a["success_rate"]), z["success_rate"] / a["success_rate"], PALETTE[2]),
    ("yield\n×%.2f" % (z["logs_per_success"] / a["logs_per_success"]), z["logs_per_success"] / a["logs_per_success"], PALETTE[3]),
]
w = fig.add_subplot(gs[0, 3])
level = None
for i, (name, val, color) in enumerate(steps):
    if level is None:
        w.bar(i, val, 0.62, color=PALETTE[6])
        level = val
    else:
        new = level * val
        w.bar(i, new - level, 0.62, bottom=level, color=color)
        level = new
w.bar(len(steps), level, 0.62, color=PALETTE[1])
w.text(len(steps), level * 1.08, f"{z['implied_logs_per_chop_h']:,}", ha="center", fontsize=8.8, color=INK)
w.text(0, steps[0][1] * 1.08, f"{steps[0][1]:,.0f}", ha="center", fontsize=8.8, color=INK)
w.axhline(z["logs_per_trip_h"], color=INK3, ls="--", lw=1)
w.text(-0.4, z["logs_per_trip_h"] * 1.07, f"10-05 measured\n{z['logs_per_trip_h']:,} logs/trip-h", fontsize=8.3, color=INK2)
w.set_yscale("log")
w.set_ylim(300, 7500)
w.set_yticks([300, 500, 1000, 2000, 4000], ["300", "500", "1,000", "2,000", "4,000"])
w.minorticks_off()
w.set_xticks(range(len(steps) + 1), [s[0] for s in steps] + ["10-05\nimplied"], fontsize=8.3)
w.set_ylabel("logs per chop-hour (log scale)", fontsize=9.5)
fig.subplots_adjust(left=0.06, right=0.99, bottom=0.2, top=0.95)
save("10_decomp", mpl_svg(fig, "10_decomp"))

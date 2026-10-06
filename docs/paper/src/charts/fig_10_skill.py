"""Lumberjacking skill by character over time (left) and Outland Dan's skill vs cumulative successful chops (right).

Writes figures/10_skill.svg from data/10_skill.json.
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import INK2, INK3, PALETTE, load, mpl, mpl_svg, save  # noqa: E402

d = load("10_skill")
P = lambda s: dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")  # noqa: E731
plt = mpl()
import matplotlib.dates as mdates  # noqa: E402

fig, (a, b) = plt.subplots(1, 2, figsize=(10, 3.9), gridspec_kw={"width_ratios": [1.55, 1]})
colors = {"TestWorth": PALETTE[6], "Hackworth": PALETTE[0], "Outland Dan": PALETTE[1]}
for char, color in colors.items():
    pts = [(P(t), v) for t, c, v in d["updates"] if c == char]
    xs, ys = zip(*pts)
    a.step(xs, ys, where="post", color=color, label=char)
    if char == "Hackworth":  # no further 0x3A gain; trip rows keep reporting 69.1
        a.plot([xs[-1], P(d["hackworth_last_trip"])], [ys[-1], ys[-1]], color=color, ls="--", lw=1.6)
        a.text(P(d["hackworth_last_trip"]), ys[-1] + 2.2, "flat at 69.1", ha="right", fontsize=8.8, color=color)
    if char == "TestWorth":
        a.annotate("set to 100 in the\nTest Shard editor", (xs[-1], 100), xytext=(30, -14), textcoords="offset points",
                   fontsize=8.8, color=INK2, va="center", arrowprops=dict(arrowstyle="-", color=INK3, lw=0.8))
    if char == "Outland Dan":
        a.text(xs[-1], ys[-1] + 2.5, f"{ys[-1]:.1f}", ha="center", fontsize=9, color=color, fontweight="semibold")
a.xaxis.set_major_locator(mdates.DayLocator())
a.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
a.set_xlim(P("2026-09-29 12:00:00"), P("2026-10-06 14:00:00"))
a.set_ylim(45, 105)
a.set_xlabel("date (2026, CDT)")
a.set_ylabel("Lumberjacking (skill points)")
a.legend(loc="center left", bbox_to_anchor=(0.0, 0.62))

xs, ys = zip(*d["dan_vs_successes"])
b.plot(xs, ys, color=PALETTE[1], lw=1.8)
b.set_xlabel("Outland Dan's cumulative successful chops")
b.set_ylabel("Lumberjacking")
b.set_ylim(45, 100)
b.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))
b.text(xs[-1], 48, f"{len(ys)} skill updates\nover {xs[-1]:,} successes", ha="right", fontsize=8.8, color=INK2)
fig.tight_layout(w_pad=2.5)
save("10_skill", mpl_svg(fig, "10_skill"))

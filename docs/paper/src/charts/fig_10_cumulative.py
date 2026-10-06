"""Cumulative logs chopped (harvest_attempts) and boards stored (trip rows) over the project.

Writes figures/10_cumulative.svg from data/10_cumulative.json.
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import INK2, INK3, PALETTE, load, mpl, mpl_svg, save  # noqa: E402

d = load("10_cumulative")
P = lambda s: dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")  # noqa: E731
plt = mpl()
import matplotlib.dates as mdates  # noqa: E402

fig, ax = plt.subplots(figsize=(10, 3.9))
cx = [P(t) for t, _ in d["chopped"]]
cy = [v for _, v in d["chopped"]]
sx = [P(t) for t, _ in d["stored"]]
sy = [v for _, v in d["stored"]]
ax.step(cx, cy, where="post", color=PALETTE[2], label="logs chopped (harvest_attempts successes)")
ax.step(sx, sy, where="post", color=PALETTE[1], label="boards stored (trip rows)")
ax.text(cx[-1], cy[-1] + 500, f"{cy[-1]:,}", ha="right", fontsize=9.5, color=PALETTE[2], fontweight="semibold")
ax.text(sx[-1] + dt.timedelta(hours=1.5), sy[-1], f"{sy[-1]:,}", ha="left", va="center", fontsize=9.5, color=PALETTE[1], fontweight="semibold")
for when, label in (("2026-10-01 19:20", "Hackworth\nstarts"), ("2026-10-04 22:05", "Outland Dan\nstarts")):
    ax.axvline(P(when + ":00"), color=INK3, lw=0.9, ls=":")
    ax.text(P(when + ":00"), 15500, " " + label, fontsize=8.8, color=INK2, va="top")
ax.xaxis.set_major_locator(mdates.DayLocator())
ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
ax.set_xlim(P("2026-09-29 12:00:00"), P("2026-10-06 00:00:00"))
ax.set_ylim(0, 20500)
ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))
ax.set_xlabel("date (2026, CDT)")
ax.set_ylabel("cumulative count")
ax.legend(loc="upper left")
fig.tight_layout()
save("10_cumulative", mpl_svg(fig, "10_cumulative"))

"""Logs per trip-hour for each of the 63 lumber trips, in trip order, by character and outcome.

Writes figures/10_trips.svg from data/10_trips.json.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import INK2, INK3, PALETTE, load, mpl, mpl_svg, save  # noqa: E402

trips = load("10_trips")["trips"]
colors = {"TestWorth": PALETTE[6], "Hackworth": PALETTE[0], "Outland Dan": PALETTE[1]}
plt = mpl()
fig, ax = plt.subplots(figsize=(10, 4.2))

# day bands
days = []
for i, t in enumerate(trips):
    if not days or days[-1][0] != t["date"]:
        days.append([t["date"], i, i])
    days[-1][2] = i
for k, (day, a, b) in enumerate(days):
    if k % 2:
        ax.axvspan(a - 0.5, b + 0.5, color="#f1ede4", zorder=0, lw=0)
    ax.text((a + b) / 2, 3950, day[5:], ha="center", va="top", fontsize=8.8, color=INK3)

for char, color in colors.items():
    for done in (True, False):
        pts = [(i, t["logs_per_trip_hour"] or 0) for i, t in enumerate(trips)
               if t["character"] == char and t["category"].startswith("completed") == done]
        if not pts:
            continue
        xs, ys = zip(*pts)
        ax.scatter(xs, ys, s=[34] * len(xs), color=color if done else "none", edgecolors=color, linewidths=1.4,
                   label=f"{char}, {'completed' if done else 'aborted'}", zorder=3)

ax.axhline(355, color=INK3, lw=1.1, ls="--", zorder=1)
ax.text(41.5, 430, "09-29 Shelter baseline ≈ 355 boards per agent-active hour", fontsize=8.8, color=INK2)
best = max(range(len(trips)), key=lambda i: trips[i]["logs_per_trip_hour"] or 0)
ax.annotate(f'trip {trips[best]["id"]}: {trips[best]["logs"]:,} logs in {trips[best]["duration_min"]:.1f} min',
            (best, trips[best]["logs_per_trip_hour"]), xytext=(-60, 28), textcoords="offset points", fontsize=8.8,
            color=INK2, va="center", arrowprops=dict(arrowstyle="-", color=INK3, lw=0.8))
ax.set_xlim(-0.8, len(trips) - 0.2)
ax.set_ylim(-120, 4000)
ax.set_xlabel("lumber trip, in start order (shaded bands = days, 2026)")
ax.set_ylabel("logs per trip-hour")
ax.legend(loc="upper left", bbox_to_anchor=(0.0, 0.93), ncol=2, fontsize=8.8)
fig.tight_layout()
save("10_trips", mpl_svg(fig, "10_trips"))

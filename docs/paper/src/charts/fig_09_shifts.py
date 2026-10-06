"""Overseer shifts per day (gap heuristic) with character deaths, as day lanes over the clock."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import INK2, PALETTE, load, mpl, mpl_svg, save  # noqa: E402

d = load("09_shifts")
plt = mpl()

SHORT = [  # one short label per heuristic shift, same order as d["shifts"]
    "TestWorth: Young demos", "TestWorth: HSB lumber", "TestWorth: trip, wipe",
    "Hackworth: Shelter lumber", "NPD", "", "Hackworth: hunt, horse, lumber, PK test",
    "runebook research", "Shackleworth quest \u2192 Hackworth in Seer voice", "Urukton; LumberSeer",
    "Outland Dan: DTF", "room trip", "Outland Dan: room loop", "Outland Dan: Seer4\u20136",
]
CHAR = {"TestWorth": PALETTE[6], "Hackworth": PALETTE[0], "Shackleworth": PALETTE[4], "Outland Dan": PALETTE[2]}


def hours(hhmm):
    h, m = hhmm.split(":")
    return int(h) + int(m) / 60


days = sorted({s["start"][:10] for s in d["shifts"]})
lane = {day: len(days) - 1 - i for i, day in enumerate(days)}
fig, ax = plt.subplots(figsize=(10, 3.9))
for s, label in zip(d["shifts"], SHORT):
    day, y = s["start"][:10], lane[s["start"][:10]]
    x0, x1 = hours(s["start"][11:]), hours(s["end"][11:])
    if s["hours"] == 0:
        continue  # a single memory lookup, no shift
    who = min((c for c in CHAR if c in s["label"]), key=s["label"].find)
    ax.barh(y, x1 - x0, left=x0, height=0.44, color=CHAR[who], alpha=0.85, edgecolor="none")
    if label:
        ax.text(x0, y + 0.3, label, fontsize=8.6, color=INK2, va="bottom", ha="left")
for k in d["deaths"]:
    ax.plot(hours(k["time"]), lane[k["day"]], marker="X", ms=9, color="#b23a3a", mec="white", mew=0.8, zorder=5)
ax.set_yticks(range(len(days)))
ax.set_yticklabels([day[5:] for day in reversed(days)])
ax.set_ylim(-0.6, len(days) - 0.2)
ax.set_xlim(8.5, 23.5)
ax.set_xticks(range(9, 24, 2))
ax.set_xticklabels([f"{h:02d}:00" for h in range(9, 24, 2)])
ax.set_xlabel("local time (CDT)")
ax.grid(axis="y", visible=False)
handles = [plt.Rectangle((0, 0), 1, 1, color=c, alpha=0.85) for c in CHAR.values()]
handles.append(plt.Line2D([], [], marker="X", ls="none", ms=8, color="#b23a3a", mec="white"))
ax.legend(handles, list(CHAR) + ["character death"], ncol=5, loc="upper center",
          bbox_to_anchor=(0.5, -0.2), fontsize=9)
save("09_shifts", mpl_svg(fig, "09_shifts"))

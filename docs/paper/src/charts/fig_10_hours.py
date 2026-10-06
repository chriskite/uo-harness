"""Logged-in hours per day by character (stacked; Test Shard hatched) with agent-active and human-input hours.

Writes figures/10_hours.svg from data/10_hours.json.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import INK, INK2, INK3, PALETTE, load, mpl, mpl_svg, save  # noqa: E402

d = load("10_hours")["days"]
chars = [
    ("TestWorth", "TestWorth (Test Shard)", PALETTE[6], "///"),
    ("Hackworth", "Hackworth", PALETTE[0], None),
    ("Shackleworth", "Shackleworth", PALETTE[4], None),
    ("Outland Dan", "Outland Dan", PALETTE[1], None),
    ("Logan Wolf", "Logan Wolf", PALETTE[3], None),
    ("(no character select)", "no character select", "#d9d4c7", None),
]
plt = mpl()
fig, ax = plt.subplots(figsize=(10, 4.2))
x = list(range(len(d)))
bottom = [0.0] * len(d)
for key, label, color, hatch in chars:
    v = [r["by_character"].get(key, 0.0) for r in d]
    ax.bar(x, v, 0.62, bottom=bottom, color=color if not hatch else "#e6e3dc", edgecolor=color if hatch else "white",
           hatch=hatch, linewidth=0.8 if hatch else 0.4, label=label)
    bottom = [b + q for b, q in zip(bottom, v)]
for i, r in enumerate(d):
    ax.text(i, r["logged_in_h"] + 0.35, f'{r["logged_in_h"]:.1f} h', ha="center", fontsize=8.8, color=INK)
ax.plot(x, [r["agent_active_h"] for r in d], "D-", color=INK, lw=1.4, ms=5.5, label="agent-active hours")
ax.plot(x, [r["human_input_h"] for r in d], "o--", color=PALETTE[2], lw=1.4, ms=5, label="human-input hours")
ax.annotate("12.49 h overnight Test Shard\nsession, mostly idle", (4.2, 10.5), xytext=(5.6, 17.5), fontsize=8.8,
            color=INK2, ha="center", arrowprops=dict(arrowstyle="-", color=INK3, lw=0.8))
ax.set_xticks(x, [r["date"][5:] for r in d])
ax.set_xlabel("day (2026, CDT; a session counts on the day it started)")
ax.set_ylabel("hours")
ax.set_ylim(0, 22)
ax.legend(loc="upper left", ncol=2, fontsize=9)
fig.tight_layout()
save("10_hours", mpl_svg(fig, "10_hours"))

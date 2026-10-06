"""Daily assistant turns by model (stacked) with human prompts per day (right axis).

Writes figures/11_daily.svg from data/11_daily.json.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load, mpl, mpl_svg, PALETTE, INK2, INK3  # noqa: E402

d = load("11_daily")["rows"]
plt = mpl()
labels = [r["date"][5:] for r in d]
x = list(range(len(d)))
k3 = [r["turns_kimi_k3"] for r in d]
opus = [r["turns_opus_5_5"] for r in d]
sonnet = [r["turns_sonnet_5_5"] for r in d]
prompts = [r["prompts_laptop"] + r["prompts_desktop"] for r in d]

fig, ax = plt.subplots(figsize=(10, 4.1))
ax.bar(x, k3, 0.66, color=PALETTE[1], label="Kimi K3")
ax.bar(x, opus, 0.66, bottom=k3, color=PALETTE[0], label="Claude Opus 5.5")
ax.bar(x, sonnet, 0.66, bottom=[a + b for a, b in zip(k3, opus)], color=PALETTE[3], label="Claude Sonnet 5.5")
ax.set_ylabel("assistant turns per day")
ax.set_xticks(x, labels)
ax.set_xlabel("day (2026, CDT)")
ax.set_ylim(0, 6000)

ax2 = ax.twinx()
ax2.plot(x, prompts, color=PALETTE[2], marker="o", lw=2, label="human prompts (right axis)")
for xi, p in zip(x, prompts):
    ax2.annotate(str(p), (xi, p), textcoords="offset points", xytext=(0, 9), ha="center",
                 fontsize=9, color=PALETTE[2], fontweight="semibold",
                 bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.9))
ax2.set_ylim(0, 200)
ax2.set_ylabel("human prompts per day", color=PALETTE[2])
ax2.grid(False)
ax2.spines["right"].set_visible(True)
ax2.tick_params(axis="y", colors=PALETTE[2])

notes = [
    (2, 2484, "09-29 14:06\nHANDOFF.md relay,\nKimi K3 → Opus", 58),
    (5, 1591, "main session\nplays the game;\n27 subagent turns", 78),
    (6, 4837, "peak: 4,837 turns,\n29 subagents, 76 commits", 18),
]
for xi, y, t, dy in notes:
    ax.annotate(t, (xi, y), xytext=(0, dy), textcoords="offset points", ha="center", fontsize=8.8,
                color=INK2, arrowprops=dict(arrowstyle="-", color=INK3, lw=0.8),
                bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none", alpha=0.85))
h1, l1 = ax.get_legend_handles_labels()
h2, l2 = ax2.get_legend_handles_labels()
ax.legend(h1 + h2, l1 + l2, loc="upper left", ncol=2)
fig.tight_layout()

from common import save  # noqa: E402

save("11_daily", mpl_svg(fig, "11_daily"))

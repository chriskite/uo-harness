"""Codebase growth per commit: stacked Python/viz LOC areas plus prose-doc words (right axis).

Writes figures/11_loc.svg from data/11_loc_growth.json.
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load, save, mpl, mpl_svg, PALETTE, INK2, INK3  # noqa: E402

rows = sorted(load("11_loc_growth")["rows"], key=lambda r: r["date"])
plt = mpl()
import matplotlib.dates as mdates  # noqa: E402

t = [datetime.fromisoformat(r["date"]).replace(tzinfo=None) for r in rows]
core = [r["harness_core_loc"] for r in rows]
tests = [r["test_loc"] for r in rows]
other = [r["other_py_loc"] for r in rows]
viz = [r["viz_loc"] for r in rows]
words = [r["md_words_excl_kb"] / 1000 for r in rows]

fig, ax = plt.subplots(figsize=(10, 4.0))
ax.stackplot(t, core, tests, other, viz, step="post",
             colors=[PALETTE[0], PALETTE[2], PALETTE[6], PALETTE[3]], alpha=0.85,
             labels=["harness core (Python)", "tests (Python)", "RE/tool scripts (Python)", "viz (TS/TSX/CSS)"])
ax.set_ylabel("lines of code")
ax.set_ylim(0, 70000)
ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v/1000:.0f}k"))
ax.xaxis.set_major_locator(mdates.DayLocator())
ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
ax.set_xlabel("commit time (2026, CDT)")

ax2 = ax.twinx()
ax2.step(t, words, where="post", color=PALETTE[1], lw=2, label="prose docs, k words (right axis)")
ax2.set_ylim(0, 240)
ax2.set_ylabel("Markdown words (thousands)", color=PALETTE[1])
ax2.tick_params(axis="y", colors=PALETTE[1])
ax2.grid(False)
ax2.spines["right"].set_visible(True)

py_end = rows[-1]["py_loc"]
ax.annotate(f"577 Python lines\n(09-27 23:01)", (t[4], rows[4]["py_loc"]), xytext=(18, 40),
            textcoords="offset points", fontsize=8.8, color=INK2,
            arrowprops=dict(arrowstyle="-", color=INK3, lw=0.8))
ax.annotate(f"{py_end:,} Python + {viz[-1]:,} viz\n(10-06 11:49)", (t[-1], py_end + viz[-1]), xytext=(-150, 6),
            textcoords="offset points", fontsize=8.8, color=INK2,
            arrowprops=dict(arrowstyle="-", color=INK3, lw=0.8))
h1, l1 = ax.get_legend_handles_labels()
h2, l2 = ax2.get_legend_handles_labels()
ax.legend(h1 + h2, l1 + l2, loc="upper left", ncol=1, fontsize=9)
fig.tight_layout()
save("11_loc", mpl_svg(fig, "11_loc"))

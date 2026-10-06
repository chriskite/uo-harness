"""Rows per table of the memory store (log scale), coloured by main writer -> figures/05_db_tables.svg."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import PALETTE, load, mpl, mpl_svg, save  # noqa: E402

d = load("05_db_tables")
rows = d["tables"][::-1]  # smallest at the bottom of the list -> largest on top
writers = ["proxy", "runners", "overseer/ctl", "Mover", "shared bus"]
colour = {w: PALETTE[i] for i, w in enumerate(writers)}

plt = mpl()
fig, ax = plt.subplots(figsize=(7.2, 4.6))
ys = range(len(rows))
ax.barh(list(ys), [r["rows"] for r in rows], color=[colour[r["writer"]] for r in rows], height=0.68)
ax.set_xscale("log")
ax.set_xlim(1, 2e7)
ax.set_yticks(list(ys))
ax.set_yticklabels([r["table"] for r in rows], family="monospace", fontsize=9.5)
for y, r in zip(ys, rows):
    ax.text(r["rows"] * 1.18, y, f'{r["rows"]:,}', va="center", fontsize=9, color="#4a5059")
ax.set_xlabel("rows (log scale)")
ax.grid(axis="y", visible=False)
handles = [plt.Rectangle((0, 0), 1, 1, color=colour[w]) for w in writers]
ax.legend(handles, writers, title="main writer", loc="lower right", fontsize=9, title_fontsize=9)
save("05_db_tables", mpl_svg(fig, "05_db_tables"))

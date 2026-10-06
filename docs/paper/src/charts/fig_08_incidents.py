"""Deaths, near-deaths, theft and false alarms as a character swimlane, 09-30 -> 10-06 -> figures/08_incidents.svg.

Days are columns sized by their number of incidents; inside a day the incidents are in clock order
(spacing not to scale)."""
import os
import sys
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load, save, mpl, mpl_svg, PALETTE, INK, INK2, INK3  # noqa: E402

d = load("08_incidents")
plt = mpl()
from matplotlib.lines import Line2D  # noqa: E402

LANES = ["TestWorth", "Hackworth", "Shackleworth", "Outland Dan"]
CAUSE = OrderedDict([
    ("Bastet (PK)", PALETTE[7]),
    ("monster, runner trip", PALETTE[1]),
    ("overseer by hand", PALETTE[4]),
    ("blind goto", PALETTE[3]),
    ("planned demo", PALETTE[6]),
    ("thief", PALETTE[2]),
    ("false alarm", PALETTE[0]),
])
KIND = {"death": ("X", 12), "near-death": ("^", 9), "theft": ("D", 7.5), "false_alarm": ("o", 8.5)}
SHORT_FIX = {"same-day demo fixes": "demo fixes", "(none recorded)": "none", "BENIGN_EFFECTS": "benign effects",
             "no corpse runs": "no corpse run", "travel_guard": "guarded goto",
             "0add311, c1b4eed": "0add311\nc1b4eed", "cb2e981, 5266884": "cb2e981\n5266884",
             "ade127e, 5589433": "ade127e\n5589433", "5e70fad, ee59524": "5e70fad\nee59524"}
ORDER_KEY = {"night": "23:00", "≈16:45": "16:45"}
SLOT = 1.25

rows = sorted(d["incidents"], key=lambda r: (r["date"], ORDER_KEY.get(r["time"], r["time"])))
days = OrderedDict()
for r in rows:
    days.setdefault(r["date"], []).append(r)

fig, ax = plt.subplots(figsize=(11.5, 4.7))
x = 0.0
day_spans = []
for day, evs in days.items():
    x0 = x
    x += 0.75 * SLOT
    for i, r in enumerate(evs):
        r["_x"] = x
        r["_alt"] = rows.index(r) % 2
        x += SLOT
    x -= 0.25 * SLOT
    day_spans.append((day, x0, x))
for _, a, b in day_spans[1:]:
    ax.axvline(a, color="#d8d2c4", lw=0.9, zorder=0)
for day, a, b in day_spans:
    ax.text((a + b) / 2, len(LANES) - 0.3, day[5:], ha="center", va="bottom", fontsize=9.5, color=INK2,
            fontweight="semibold")

for r in rows:
    y = len(LANES) - 1 - LANES.index(r["character"])
    m, ms = KIND[r["kind"]]
    col = CAUSE[r["cause"]]
    hollow = r["kind"] == "false_alarm"
    ax.plot([r["_x"]], [y], marker=m, ms=ms, color=col, mfc="white" if hollow else col, mew=1.8, ls="", zorder=3)
    ax.text(r["_x"], y + 0.2, f"{r['id']}\n{r['time']}", ha="center", va="bottom", fontsize=8, color=INK,
            linespacing=1.05)
    fx = SHORT_FIX.get(r["fix"], r["fix"])
    # the lower row sits below a two-line upper label, so neighbours never collide
    ax.text(r["_x"], y - 0.2 - 0.36 * r["_alt"], fx, ha="center", va="top", fontsize=7.3, color=INK3,
            family="monospace")

ax.set_yticks(range(len(LANES)))
ax.set_yticklabels(list(reversed(LANES)))
ax.set_ylim(-0.8, len(LANES) - 0.05)
ax.set_xlim(0, x)
ax.set_xticks([])
ax.grid(False)
ax.spines["bottom"].set_visible(False)
kind_h = [Line2D([], [], marker=KIND[k][0], ls="", ms=KIND[k][1] * 0.8, color=INK2,
                 mfc="white" if k == "false_alarm" else INK2, mew=1.5, label=k.replace("_", " "))
          for k in ("death", "near-death", "theft", "false_alarm")]
cause_h = [Line2D([], [], marker="s", ls="", ms=8, color=c, label=n) for n, c in CAUSE.items()]
leg1 = ax.legend(handles=kind_h, loc="upper left", bbox_to_anchor=(0.0, -0.01), ncol=4, fontsize=8.6,
                 handletextpad=0.3, columnspacing=1.0)
ax.add_artist(leg1)
ax.legend(handles=cause_h, loc="upper left", bbox_to_anchor=(0.0, -0.09), ncol=7, fontsize=8.6,
          handletextpad=0.3, columnspacing=0.9)
save("08_incidents", mpl_svg(fig, "08_incidents"))

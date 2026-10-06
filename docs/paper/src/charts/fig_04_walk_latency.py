"""Agent walk timing: step-gap histograms (left) and confirm-latency percentiles (right)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load, mpl, mpl_svg, PALETTE, INK3  # noqa: E402

d = load("04_walk_latency")["sessions"]
plt = mpl()
fig, (a, b) = plt.subplots(1, 2, figsize=(10, 3.7), gridspec_kw={"width_ratios": [1.7, 1]})

colors = [PALETTE[0], PALETTE[3], PALETTE[1]]
for (tag, s), c in zip(d.items(), colors):
    h = {float(k): v for k, v in s["gap_hist_10ms_under_1s"].items()}
    n = s["agent_step_gap_s"]["n"]
    xs = [i / 100 for i in range(5, 61)]
    ys = [100 * h.get(round(x, 2), 0) / n for x in xs]
    a.step(xs, ys, where="mid", color=c, label=f'{s["label"]} (n={n:,})')
for x, lab in ((0.1, "mounted\nrun floor"), (0.2, "on-foot\nrun floor"), (0.4, "walk\nfloor")):
    a.axvline(x, color=INK3, lw=1, ls=(0, (4, 3)))
    a.text(x, 1.02, lab, transform=a.get_xaxis_transform(), ha="center", va="bottom", fontsize=8.5, color=INK3)
a.set_xlim(0.05, 0.6)
a.set_xlabel("gap between consecutive agent steps (s, 10 ms bins)")
a.set_ylabel("share of steps (%)")
a.legend(loc="center right")

labels = [s["label"] for s in d.values()]
for i, ((tag, s), c) in enumerate(zip(d.items(), colors)):
    q = s["confirm_latency_s"]
    y = len(d) - 1 - i
    b.plot([q["p10"] * 1000, q["p90"] * 1000], [y, y], color=c, lw=6, solid_capstyle="butt", alpha=0.55)
    b.plot([q["p90"] * 1000, q["p99"] * 1000], [y, y], color=c, lw=1.6)
    b.plot([q["median"] * 1000], [y], "o", color=c, ms=8, mec="white", mew=1.2)
    b.plot([q["p99"] * 1000], [y], "|", color=c, ms=12, mew=2)
    b.text(q["p99"] * 1000 + 6, y, f'med {q["median"] * 1000:.0f} ms\np99 {q["p99"] * 1000:.0f} · max {q["max"]:.1f} s',
           va="center", fontsize=8.5, color=INK3)
b.set_yticks(range(len(d)))
b.set_yticklabels(labels[::-1])
b.set_xlim(0, 290)
b.set_ylim(-0.6, len(d) - 0.4)
b.set_xlabel("walk → server confirm latency (ms)")
b.grid(axis="y", visible=False)
fig.tight_layout(w_pad=2.5)

from common import save  # noqa: E402

save("04_walk_latency", mpl_svg(fig, "04_walk_latency"))

"""CAPTCHA answer latency by solver + digit frequency -> figures/08_captcha.svg."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load, save, mpl, mpl_svg, PALETTE, INK, INK3  # noqa: E402

d = load("08_captchas")
plt = mpl()
BLUE, RUST = PALETTE[0], PALETTE[1]

fig, (ax, bx) = plt.subplots(1, 2, figsize=(9.6, 2.9), gridspec_kw={"width_ratios": [1.75, 1], "wspace": 0.28})
rng = np.random.default_rng(8)
groups = [("agent", "auto-solver", BLUE, 1), ("human", "human in client", RUST, 0)]
ax.axvspan(7.7, 17.5, color="#ece8df", alpha=0.7, lw=0, zorder=0)
ax.text(12.6, 1.62, "human solves measured before the solver: 7.7–17.5 s", ha="center", va="center",
        fontsize=8.3, color=INK3)
for key, name, col, y in groups:
    xs = np.array([r["answer_s"] for r in d["rows"] if r["by"] == key])
    ys = y + rng.uniform(-0.22, 0.22, len(xs))
    ax.scatter(xs, ys, s=22, color=col, alpha=0.75, edgecolor="white", lw=0.5, zorder=3)
    med = float(np.median(xs))
    ax.plot([med, med], [y - 0.33, y + 0.33], color=INK, lw=2, zorder=4)
    ax.text(med, y + 0.36, f"median {med:.2f} s", ha="center", va="bottom", fontsize=8.5, color=INK)
ax.set_yticks([0, 1])
ax.set_yticklabels([f"human (n={sum(r['by'] == 'human' for r in d['rows'])})",
                    f"auto-solver (n={sum(r['by'] == 'agent' for r in d['rows'])})"])
ax.set_ylim(-0.5, 1.8)
ax.set_xlim(5, 25)
ax.set_xlabel("gump open → answer sent (s)")
ax.grid(axis="y", visible=False)

digits = [str(k) for k in range(10)]
counts = [d["digit_counts"][k] for k in digits]
bars = bx.bar(digits, counts, color=[RUST if c == 0 else BLUE for c in counts], width=0.7)
bx.text(0, 1.2, "0", ha="center", va="bottom", fontsize=9, color=RUST, fontweight="bold")
bx.set_xlabel("digit")
bx.set_ylabel("times seen (159 digits)")
bx.grid(axis="x", visible=False)
save("08_captcha", mpl_svg(fig, "08_captcha"))

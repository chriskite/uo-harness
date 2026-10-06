"""CAPTCHA solver: what it sees and how sure it was -> figures/08_captcha_read.svg, 08_captcha_margins.svg."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load, save, mpl, mpl_svg, PALETTE, INK, INK2, INK3  # noqa: E402

d = load("08_captcha_solver")
plt = mpl()
CL = [PALETTE[0], PALETTE[2], PALETTE[4]]
RED = "#b23a3a"

# ---- figure 1: one real CAPTCHA as the layout draws it, and each digit against its nearest references
ex = d["example"]
fig = plt.figure(figsize=(11, 3.4))
gs = fig.add_gridspec(1, 4, width_ratios=[2.3, 1, 1, 1], wspace=0.3)
ax = fig.add_subplot(gs[0, 0])
xs_all = sorted(p[0] for cl in ex["raw"] for p in cl)
for k, cl in enumerate(ex["raw"]):
    ax.scatter([p[0] for p in cl], [p[1] for p in cl], s=26, color=CL[k], zorder=3)
cuts = [(max(p[0] for p in a) + min(p[0] for p in b)) / 2 for a, b in zip(ex["raw"], ex["raw"][1:])]
for x in cuts:
    ax.axvline(x, color=INK3, lw=0.9, ls="--")
ax.set_ylim(162, 60)
ax.set_xlim(xs_all[0] - 12, xs_all[-1] + 12)
ax.set_aspect("equal")
ax.set_title(f"the layout's dots (answer {ex['answer']})", loc="left", fontsize=10)
ax.set_xlabel("x (gump px)")
ax.set_ylabel("y (gump px)")
ax.grid(False)
for k, dig in enumerate(ex["digits"]):
    bx = fig.add_subplot(gs[0, k + 1])
    q = np.array(dig["query"]); b = np.array(dig["best"]["pts"]); r = np.array(dig["runner_up"]["pts"])
    bx.scatter(r[:, 0], r[:, 1], s=30, marker="x", color=RED, lw=1.1, label=f"nearest {dig['runner_up']['digit']}", zorder=2)
    bx.scatter(b[:, 0], b[:, 1], s=46, facecolor="none", edgecolor=INK2, lw=1.0, label=f"nearest {dig['best']['digit']}", zorder=3)
    bx.scatter(q[:, 0], q[:, 1], s=20, color=CL[k], label="this digit", zorder=4)
    bx.set_xlim(-0.62, 0.62); bx.set_ylim(0.66, -0.66)
    bx.set_aspect("equal")
    bx.set_xticks([]); bx.set_yticks([])
    bx.grid(False)
    for s in ("left", "bottom"):
        bx.spines[s].set_visible(False)
    bx.set_title(f"{dig['best']['digit']}: D {dig['best']['D']:.3f} vs {dig['runner_up']['digit']}: {dig['runner_up']['D']:.3f}",
                 fontsize=8.8, loc="center")
    bx.text(0, 0.78, f"margin {dig['margin']:.2f}", ha="center", va="top", fontsize=8.6, color=INK)
    bx.legend(loc="upper center", bbox_to_anchor=(0.5, -0.2), fontsize=7.6, ncol=1, handletextpad=0.2,
              borderaxespad=0, labelspacing=0.25)
save("08_captcha_read", mpl_svg(fig, "08_captcha_read"))

# ---- figure 2: margins of every digit read out of sample
plt = mpl()
fig, ax = plt.subplots(figsize=(9.6, 2.7))
rng = np.random.default_rng(5)
rows = [("held out by session\n(26 dataset captchas)", [m for x in d["loso"] for m in x["margins"]], 1, PALETTE[0]),
        ("live, after the last\nfont update (27 captchas)", [m for x in d["live"] for m in x["margins"]], 0, PALETTE[2])]
gate = d["gate"]["MIN_MARGIN"]
ax.axvspan(0, gate, color="#f3dcda", lw=0, zorder=0)
ax.axvline(gate, color=RED, lw=1.2)
ax.text(gate + 0.008, 1.62, f"gate {gate:.2f}: below it, the human answers", fontsize=8.4, color=RED, va="center")
for name, ms, y, col in rows:
    ms = np.array(ms)
    ax.scatter(ms, y + rng.uniform(-0.2, 0.2, len(ms)), s=18, color=col, alpha=0.75, edgecolor="white", lw=0.4, zorder=3)
    ax.plot([np.median(ms)] * 2, [y - 0.3, y + 0.3], color=INK, lw=1.8, zorder=4)
    lo = ms.min()
    dy = 0.36 if y == 1 else -0.42
    ax.annotate(f"lowest {lo:.3f}", (lo, y), (lo + 0.03, y + dy), fontsize=8.2, color=INK2, va="center",
                arrowprops=dict(arrowstyle="-", color=INK3, lw=0.7))
mis = d["misread_2026_10_01"]
ax.scatter([mis["margin"]], [0.5], marker="X", s=70, color=RED, zorder=5)
ax.annotate(f"10-01: {mis['answer']} read as {mis['read']}\n(the only real 9 held out)", (mis["margin"], 0.5),
            (0.2, 0.42), fontsize=8.2, color=RED, va="center",
            arrowprops=dict(arrowstyle="-", color=RED, lw=0.7))
ax.set_yticks([1, 0])
ax.set_yticklabels([r[0] for r in rows], fontsize=8.8)
ax.set_ylim(-0.7, 1.8)
ax.set_xlim(0, 1)
ax.set_xlabel("margin of each digit: (D₂ − D₁) / D₂, nearest other digit against nearest same digit")
ax.grid(axis="y", visible=False)
save("08_captcha_margins", mpl_svg(fig, "08_captcha_margins"))

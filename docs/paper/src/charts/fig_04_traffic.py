"""Traffic composition per day: server-visible C2S by sender (left), client-only fabrications (right)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load, save, mpl, mpl_svg, PALETTE, INK  # noqa: E402

d = load("04_traffic_daily")["days"]
days = [r["day"] for r in d]
x = list(range(len(days)))
plt = mpl()
fig, (a, b) = plt.subplots(1, 2, figsize=(10, 3.9))

cl = [r["c2s_client"] / 1000 for r in d]
ag = [r["c2s_agent"] / 1000 for r in d]
a.bar(x, cl, color=PALETTE[0], label="relayed from the client")
a.bar(x, ag, bottom=cl, color=PALETTE[1], label="injected by the agent")
for i, r in enumerate(d):
    a.text(i, (r["c2s_client"] + r["c2s_agent"]) / 1000 + 1.5, f'{r["hours"]:.0f} h', ha="center", fontsize=8.5, color=INK)
a.set_xticks(x)
a.set_xticklabels(days)
a.set_ylabel("C2S packets the server saw (thousands)")
a.legend(loc="upper left")
a.set_ylim(0, 125)

re_ = [r["reanchor"] / 1000 for r in d]
tc = [r["target_cancel"] / 1000 for r in d]
gc = [r["gump_close"] / 1000 for r in d]
b.bar(x, re_, color=PALETTE[2], label="0x21 re-anchor")
b.bar(x, tc, bottom=re_, color=PALETTE[3], label="0x6C target cancel")
b.bar(x, gc, bottom=[p + q for p, q in zip(re_, tc)], color=PALETTE[4], label="0xBF/4 gump close")
b.plot(x, [r["confirm_hidden"] / 1000 for r in d], "o--", color=INK, lw=1.2, ms=5, label="agent confirms hidden")
b.set_xticks(x)
b.set_xticklabels(days)
b.set_ylabel("client-only S2C packets (thousands)")
b.legend(loc="upper left")
fig.tight_layout(w_pad=3)
save("04_traffic", mpl_svg(fig, "04_traffic"))

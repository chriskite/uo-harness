"""How the memory store grew: new rows per local day by table group (log scale) and the store's
size in the hourly NAS backups -> figures/05b_growth.svg."""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import INK3, PALETTE, load, mpl, mpl_svg, save  # noqa: E402

d = load("05b_growth")
days = d["days"]
labels = [day[5:] for day in days]  # MM-DD
order = ["events_world", "events_proxy", "walk_moves", "harvest_attempts", "bus", "knowledge"]
colour = dict(zip(order, [PALETTE[0], "#7fa7c4", PALETTE[2], PALETTE[3], PALETTE[1], PALETTE[4]]))
marker = dict(zip(order, ["o", "o", "s", "D", "^", "v"]))

plt = mpl()
fig, (ax, bx) = plt.subplots(1, 2, figsize=(10.4, 4.5), gridspec_kw={"width_ratios": [1.35, 1]})

xs = range(len(days))
for k in order:
    ys = [v if v > 0 else None for v in d["series"][k]]
    ax.plot(list(xs), ys, marker=marker[k], ms=5, lw=1.6, color=colour[k], label=d["labels"][k])
ax.set_yscale("log")
ax.set_ylim(3, 1e6)
ax.set_xticks(list(xs))
ax.set_xticklabels(labels)
ax.set_xlabel("local day (2026)")
ax.set_ylabel("new rows per day (log scale)")
ax.set_title("Rows added per day", loc="left")
ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.17), fontsize=8.6, ncol=3, handlelength=1.6, columnspacing=1.0)
ax.annotate("Discord facts promoted", xy=(6, d["series"]["knowledge"][6]), xytext=(6.2, 5),
            fontsize=8.6, color=INK3, ha="center", arrowprops={"arrowstyle": "-", "color": INK3, "lw": 0.8})

ts = [dt.datetime.fromisoformat(b["snapshot_local"]) for b in d["backups"]]
mb = [b["db_bytes"] / 1e6 for b in d["backups"]]
gz = [b["gz_bytes"] / 1e6 for b in d["backups"]]
bx.plot(ts, mb, marker="o", ms=3.5, lw=1.8, color=PALETTE[0], label="store (uncompressed)")
bx.plot(ts, gz, marker="o", ms=3.0, lw=1.4, color=PALETTE[3], label="gzipped backup")
bx.set_ylim(0, 320)
bx.set_ylabel("MB")
bx.set_title("Store size in the hourly backups", loc="left")
import matplotlib.dates as mdates  # noqa: E402

bx.xaxis.set_major_locator(mdates.DayLocator())
bx.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
bx.set_xlabel("local time (2026)")
bx.legend(loc="upper left", fontsize=8.6)
jump = next(i for i, b in enumerate(d["backups"]) if b["snapshot_local"] == "2026-10-04T12:45")
bx.annotate("+10 MB in 45 min:\n3,102 knowledge rows", xy=(ts[jump], mb[jump]), xytext=(ts[jump] - dt.timedelta(hours=30), 245),
            fontsize=8.6, color=INK3, ha="center", arrowprops={"arrowstyle": "-", "color": INK3, "lw": 0.8})
fig.tight_layout(w_pad=2.2)
save("05b_growth", mpl_svg(fig, "05b_growth"))

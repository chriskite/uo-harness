"""The 18:53 death on the walk in (hunt-20261022-185159-c0c5, capture session_20261022_163222, 2026-10-22) in the
simulator. Live: the red Botan came into view at (5610, 677) at 18:52:33 on the Mausoleum's first level, 18 tiles
from Logan at about (5592, 688), ran south and out of view, came back at (5593, 711) at 18:53:04, followed Logan
through the first teleporter (5554, 700) 3 s after him and said "Botan is attacking you!" at 18:53:17 on the
second level; the walk in had no hostile-player check, so the pets guarded, Logan vetted and played Area Peace on
him and died at (5535, 713) at 18:53:48 (docs/NOTES.md "Dead on the walk in to a red").

Both cases, from home with both pets and no monsters, the live command's runner arguments, a short --timeout so the
run ends at home after --pk-wait:
  level1  Botan appears where he did when Logan first stands at x <= 5592 on the first level, and walks his live
          tiles (0x20/0x77, their times) before he comes at us (sim.world.add_red).
  level2  Botan appears at the first teleporter's arrival (5547, 696) 3 s after Logan does (live 18:53:11 / 18:53:14)
          and walks his live tiles on the second level.
Per seed: dead or not, where the run ended, the leaves and how ("gold moongate", "through the teleporters").

    python harness/sim/fight1853.py <seeds> [level1|level2] [--log] [--harness DIR]

--harness runs another checkout's runner (a snapshot, with this simulator copied in) for a before/after comparison.
"""
import contextlib
import io
import os
import sys
import tempfile

ARGS = sys.argv[1:]
HARNESS = os.path.abspath(ARGS[ARGS.index("--harness") + 1]) if "--harness" in ARGS \
    else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS)
SEEDS = [int(s) for s in ARGS[0].split(",")] if ARGS and not ARGS[0].startswith("--") else [1]
CASE = next((a for a in ARGS if a in ("level1", "level2")), "level1")
SHOW_LOG = "--log" in ARGS

import loop_hunt  # noqa: E402
import memory  # noqa: E402
from agent_link import Abort  # noqa: E402
from sim import clock as simclock  # noqa: E402
from sim import run as simrun  # noqa: E402
from sim.link import SimLink  # noqa: E402
from sim.scenario import load  # noqa: E402
from sim.world import World, cheb  # noqa: E402

EXTRA = ["--heal-in-place", "--vet-range", "5", "--crawl-learn-s", "180"]
MINUTES = 6
LEVEL1 = [(5609, y, 0.2) for y in range(678, 689)] + [(5608, 689, 0.3), (5607, 690, 0.2)] \
    + [(5606, y, 0.2) for y in range(691, 700)] + [(5606, 700, 2.7), (5606, 701, 0.2)] \
    + [(5606 - i, 701 + i, 1.8) for i in range(1, 11)] + [(x, 711, 1.8) for x in range(5595, 5592, -1)] \
    + [(5593 - i, 711 - i, 0.2) for i in range(1, 5)] + [(x, 707, 0.2) for x in range(5588, 5554, -1)] \
    + [(5554, y, 0.2) for y in range(706, 700, -1)]
LEVEL1_AT, LEVEL1_WHEN = (5610, 677), 5592          # he showed when Logan stood at x 5592
LEVEL2 = [(5547, y, 0.2) for y in range(697, 707)] + [(5546, 707, 0.3), (5545, 708, 0.2), (5544, 708, 0.3),
                                                      (5543, 708, 0.2), (5542, 708, 0.8)]
LEVEL2_AT, ARRIVAL, LEVEL2_DELAY = (5547, 696), (5547, 696), 3.0


def one(seed):
    sc = load()
    sc["spawns"] = []
    sc["player"]["start"] = sc["recall"]["rows"][0]["at"]         # home, as run.run_once without at_hunt
    sc["vet"]["range"] = 5
    clock = simclock.SimClock(epoch=simrun.EPOCH)
    w = World(sc, seed=seed)
    home = w.player.pos
    link = SimLink(w, clock)
    db = os.path.join(tempfile.mkdtemp(prefix="fight_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(sc, EXTRA, MINUTES)
                                             + ["--memory", db, "--seed", str(seed)])
    patched = simclock.install(clock)
    store = memory.Memory(db)
    world_tick, red, seen = clock.on_advance, [], {"arrived": None, "nearest": 99}

    def watch(now):
        world_tick(now)
        p = w.player.pos
        if not red:
            if CASE == "level1" and 5554 < p[0] <= LEVEL1_WHEN and 670 <= p[1] <= 730:
                red.append(w.add_red(*LEVEL1_AT, name="Botan", route=LEVEL1))
            if CASE == "level2":
                if seen["arrived"] is None and cheb(p, ARRIVAL) <= 2:
                    seen["arrived"] = w.t
                if seen["arrived"] is not None and w.t - seen["arrived"] >= LEVEL2_DELAY:
                    red.append(w.add_red(*LEVEL2_AT, name="Botan", route=LEVEL2))
        elif not w.player.ghost:
            seen["nearest"] = min(seen["nearest"], cheb(p, red[0].pos))
    clock.on_advance = watch
    out = io.StringIO()
    outcome = "done"
    try:
        with contextlib.redirect_stdout(out):
            loop = loop_hunt.HuntLoop(link, store, args)
            try:
                loop.run()
            except Abort as e:
                outcome = f"aborted: {str(e)[:70]}"
    finally:
        clock.on_advance = world_tick
        store.close()
        simclock.uninstall(patched)
    logl = out.getvalue().splitlines()
    return {"seed": seed, "red": bool(red), "dead": w.metrics["deaths"], "home": cheb(w.player.pos, home) <= 5,
            "pos": w.player.pos[:2], "nearest_red": seen["nearest"] if red else None,
            "gold_gate_leave": any("LEAVING the walk in by the gold moongate" in ln for ln in logl),
            "rush": any("on through the teleporters at flight pace" in ln for ln in logl),
            "gate_leave": any("LEAVING by the moongate" in ln for ln in logl),
            "sim_s": round(clock.elapsed), "outcome": outcome, "log": logl}


if __name__ == "__main__":
    rows = []
    for s in SEEDS:
        r = one(s)
        log = r.pop("log")
        rows.append(r)
        print(r)
        if SHOW_LOG:
            print("\n".join("    " + line[:170] for line in log))
    if len(rows) > 1:
        n = len(rows)
        print(f"{CASE} all {n}: red placed {sum(r['red'] for r in rows)}, deaths {sum(r['dead'] for r in rows)}, "
              f"home {sum(r['home'] for r in rows)}, gold-gate leaves {sum(r['gold_gate_leave'] for r in rows)}, "
              f"rushes {sum(r['rush'] for r in rows)}, gate leaves {sum(r['gate_leave'] for r in rows)}")

"""The 22:27 ping-pong at a ledge (hunt-20261021-221736-1743, capture session_20261021_164924, 2026-10-21) in the
simulator, from 22:27:28: Logan at (5179, 683, 25) on the Maus 3 ledge, both pets beside him, two shade wolves idle
below it at (5178, 691, -3) and (5178, 694, -5), the way down the ramp west of them ((5177, 689) at z 2); no respawns;
the live command's runner arguments. Live for 74 s (40 rounds) the approach walked the beeline to within 6 tiles
of the wolf on the ledge ((5180, 684) -> (5181, 685): "toward a target: route 1 steps from (5180, 684)"), where the
wolf was off our floor (on_our_floor: 12 steps round by the ramp against a budget of 9), so not a target; the
patrol's next step back to (5180, 684) made it a target again (11 steps against 10). The user: "Logan is stuck
bumping into a wall trying to approach a shade wolf" (docs/NOTES.md "Ping-pong at the ledge"). Per seed: the
approaches, the targets picked, the approaches that ended off the wolf's floor, kills, deaths; then the totals.

    python harness/sim/fight2227.py <seeds> [--log] [--harness DIR]

--harness runs another checkout's runner (a snapshot, with this simulator copied in) for a before/after comparison.
"""
import contextlib
import io
import os
import re
import sys
import tempfile

ARGS = sys.argv[1:]
HARNESS = os.path.abspath(ARGS[ARGS.index("--harness") + 1]) if "--harness" in ARGS \
    else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS)
SEEDS = [int(s) for s in ARGS[0].split(",")] if ARGS and not ARGS[0].startswith("--") else [1]
SHOW_LOG = "--log" in ARGS

import loop_hunt  # noqa: E402
import memory  # noqa: E402
import tamer  # noqa: E402,F401
import los  # noqa: E402
import crawl  # noqa: E402,F401
import escape  # noqa: E402,F401
from agent_link import Abort  # noqa: E402
from sim import clock as simclock  # noqa: E402
from sim import run as simrun  # noqa: E402
from sim.link import SimLink  # noqa: E402
from sim.scenario import load  # noqa: E402
from sim.world import World  # noqa: E402

# the capture's positions at 22:27:28 (0x20/0x77 of each serial; Logan's from the task log)
SPAWNS = [("shade wolf", (5178, 691, -3)), ("shade wolf", (5178, 694, -5))]
START = (5179, 683, 25)
EXTRA = ["--heal-in-place", "--vet-range", "5", "--crawl-learn-s", "180"]
MINUTES = 2
APPROACH = re.compile(r"toward a target: arrived at \((\d+), (\d+)\)")


def one(seed):
    sc = load()
    sc["spawns"] = [{"key": k, "at": list(at), "respawn_s": [99999, 99999]} for k, at in SPAWNS]
    sc["defaults"]["spawn_spread"] = sc["defaults"]["wander_per_s"] = 0   # each where the capture had it
    sc["player"]["start"] = list(START)
    sc["vet"]["range"] = 5
    clock = simclock.SimClock(epoch=simrun.EPOCH)
    w = World(sc, seed=seed)
    link = SimLink(w, clock)
    db = os.path.join(tempfile.mkdtemp(prefix="fight_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(sc, EXTRA, MINUTES, at_hunt=True)
                                             + ["--memory", db, "--seed", str(seed)])
    patched = simclock.install(clock)
    store = memory.Memory(db)
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
        store.close()
        simclock.uninstall(patched)
    logl = out.getvalue().splitlines()
    walk = w.walk
    ledge = 0                           # approaches ending on the ledge, over 10 z above the wolves' floor
    for line in logl:
        if (m := APPROACH.search(line)) is not None and los.surface_z(w.walk, int(m.group(1)), int(m.group(2)), 25) > 10:
            ledge += 1
    return {"seed": seed, "approaches": sum("toward a target: arrived" in line for line in logl),
            "ledge_approaches": ledge, "targets": sum(" target a shade wolf" in line for line in logl),
            "kills": w.metrics["kills"], "dead": w.metrics["deaths"], "sim_s": round(clock.elapsed),
            "outcome": outcome, "log": logl}


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
        print(f"all {n}: approaches {sum(r['approaches'] for r in rows)}, on the ledge "
              f"{sum(r['ledge_approaches'] for r in rows)}, targets picked {sum(r['targets'] for r in rows)}, "
              f"kills {sum(r['kills'] for r in rows)}, deaths {sum(r['dead'] for r in rows)}")

"""The 22:03 death at Maus 3's east wing mouth (hunt-20261022-211657-79a9, capture session_20261022_163222,
2026-10-22) in the simulator. Live: Logan at (5239, 654) with both pets; the red Flat Lander (a caster: nothing in his
hands, "Corp Por" x3 and "Por Ort Grav", a cast every ~2.2 s, 30-34 a bolt) came into view at (5222, 659) at 22:02:59.5,
west on the way to the crimson gate, ran east to (5230, 651), back to (5226, 651), stood and cast; the flight went
"past them at once" (the wing is a dead end) with no rope, walked into his bolts and died at 22:03:09.85
(docs/NOTES.md "Dead at the east wing's mouth to a red").

Per seed, from the leave (HuntLoop.leave_by_gate "urgent", the runner's own flight): dead or through the gate, the
ropes and where they landed. His tiles and step times are the capture's (0x77/0x20), then he comes at us
(sim.world.add_red: a caster, as he was).

    python harness/sim/fight2203.py <seeds> [--log] [--trace DIR] [--harness DIR]

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
SHOW_LOG = "--log" in ARGS

import loop_hunt  # noqa: E402
import memory  # noqa: E402
from agent_link import Abort  # noqa: E402
from sim import clock as simclock  # noqa: E402
from sim import run as simrun  # noqa: E402
from sim import trace  # noqa: E402
from sim.link import SimLink  # noqa: E402
from sim.scenario import load  # noqa: E402
from sim.world import World, cheb  # noqa: E402
TRACE = ARGS[ARGS.index("--trace") + 1] if "--trace" in ARGS else None   # record each run's trace (sim/trace.py) here
STAMP = trace.stamp()

START = (5239, 654)
PETS = ((5238, 652), (5240, 652))
EXTRA = ["--heal-in-place", "--vet-range", "5", "--crawl-learn-s", "180"]
# (x, y, seconds after the one before), from the capture, t 0 = 22:02:59.75 at (5222, 659)
FLAT_LANDER = [(5222 + i, 659 - i, 0.2) for i in range(1, 9)] \
    + [(5229, 651, 0.3), (5228, 651, 0.2), (5227, 651, 0.4), (5226, 651, 0.25),
       (5225, 652, 2.0), (5224, 653, 0.2), (5224, 654, 2.0), (5224, 655, 0.2)]


def one(seed):
    sc = load()
    sc["spawns"] = []
    sc["player"]["start"] = list(START) + [0]
    sc["vet"]["range"] = 5
    clock = simclock.SimClock(epoch=simrun.EPOCH)
    w = World(sc, seed=seed)
    for p, xy in zip(w.pets(), PETS):
        p.x, p.y = xy
    link = SimLink(w, clock)
    tracer = trace.Tracer(w, trace.path_for(TRACE, STAMP, "fight2203", seed), label="fight2203", seed=seed,
                          epoch=simrun.EPOCH) if TRACE else None
    db = os.path.join(tempfile.mkdtemp(prefix="fight_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(sc, EXTRA, 10, at_hunt=True)
                                             + ["--memory", db, "--seed", str(seed)])
    patched = simclock.install(clock)
    store = memory.Memory(db)
    world_tick, reds = clock.on_advance, []

    def watch(now):
        world_tick(now)
        if not reds:
            # his kit live: empty hands, Energy Bolts and a Lightning, two water elementals ("River", "Flow") on Logan
            reds.append(w.add_red(5222, 659, name="Flat Lander", route=list(FLAT_LANDER),
                                  spec={"hands": [[0, 0, 1]], "summon_share": 1.0}))
    clock.on_advance = watch
    out = io.StringIO()
    outcome = "?"
    try:
        with contextlib.redirect_stdout(tracer.tee(out) if tracer else out):
            loop = loop_hunt.HuntLoop(link, store, args)
            loop.team.setup(loop.state())
            loop.state()
            try:
                res = loop.leave_by_gate("urgent")
                outcome = "through the gate" if res.get("ok") else f"not out: {res.get('error') or res}"[:70]
            except Abort as e:
                outcome = f"aborted: {str(e)[:60]}"
    finally:
        clock.on_advance = world_tick
        store.close()
        simclock.uninstall(patched)
    logl = out.getvalue().splitlines()
    ropes = [ln[9:] for ln in logl if "rope to" in ln]
    result = {"seed": seed, "dead": w.metrics["deaths"], "outcome": outcome, "ropes": len(ropes),
            "hidden_ropes": sum("out of their sight" in r for r in ropes), "hits_left": w.player.hits,
            "sim_s": round(clock.elapsed, 1), "log": logl}
    if tracer is not None:
        tracer.close({k: v for k, v in result.items() if k not in ("log", "hits")})
    return result


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
        print(f"all {n}: deaths {sum(r['dead'] for r in rows)}, through the gate "
              f"{sum(r['outcome'] == 'through the gate' for r in rows)}, ropes {sum(r['ropes'] for r in rows)} "
              f"({sum(r['hidden_ropes'] for r in rows)} out of sight)")

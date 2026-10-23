"""The 20:01 death in Maus 3's east wing (hunt-20261022-191304-536b, capture session_20261022_163222, 2026-10-22) in
the simulator. Live: Logan at (5244, 685) with both pets near him; the red Druidx came into view at (5243, 667) at
20:00:40.1, down the wing's only corridor (its way out to the crimson gate runs north up x 5243 to y 654, then west),
Viny 1.1 s behind him on his tile, OIOOOIIO into view at (5243, 719) at 20:00:55.7. "Druidx is attacking you!" at
20:00:41.8. The flight ran "away from them first" south ahead of them into the wing's south-west end (5224, 737), 156
steps from the gate, turned back and died at 20:01:02 (docs/NOTES.md "Dead in the east wing's dead end").

Per seed, from the leave (HuntLoop.leave_by_gate "urgent", the runner's own flight): dead or through the gate, the
flight's first decision ("away from them first" or "a dead end: past them at once"), the nearest any red came in the
first 6 s. Their tiles and step times are the capture's (0x77/0x20), then they come at us (sim.world.add_red).

    python harness/sim/fight2001.py <seeds> [--log] [--harness DIR]

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
from sim.link import SimLink  # noqa: E402
from sim.scenario import load  # noqa: E402
from sim.world import World, cheb  # noqa: E402

START = (5244, 685)
PETS = ((5243, 689), (5246, 687))           # GraveRobber "at 4" when the leave fired; AlaFlambe near
EXTRA = ["--heal-in-place", "--vet-range", "5", "--crawl-learn-s", "180"]
# (x, y, seconds after the one before), from the capture: Druidx from (5243, 667) at 20:00:40.14 (t 0), a stand while
# he cast at (5243, 678) (42.42 -> 44.88, out of view between 678 and 682), then on south
DRUIDX = [(5243, y, 0.2) for y in range(668, 679)] + [(5243, y, 0.6) for y in range(679, 683)] \
    + [(5243, y, 0.2) for y in range(683, 689)] + [(5244, y, 0.2) for y in range(689, 734)]
VINY_T, VINY = 1.1, [(5243, y, 0.2) for y in range(668, 678)] \
    + [(5243 + (y >= 689), y, 1.2 if y == 678 else 0.45) for y in range(678, 730)]
OIO_T, OIO_AT = 15.5, (5243, 719)


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
    db = os.path.join(tempfile.mkdtemp(prefix="fight_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(sc, EXTRA, 10, at_hunt=True)
                                             + ["--memory", db, "--seed", str(seed)])
    patched = simclock.install(clock)
    store = memory.Memory(db)
    world_tick, reds, near = clock.on_advance, [], [99]
    t0 = [None]

    def watch(now):
        world_tick(now)
        if t0[0] is None:
            t0[0] = w.t
            # Druidx live: a recurve bow and a crossbow, an Energy Bolt now and then (the archer kit, a bow)
            reds.append(w.add_red(5243, 667, name="Druidx", route=DRUIDX, kit="archer",
                                  spec={"hands": [[5042, 2, 1, 10]]}))
        el = w.t - t0[0]
        if len(reds) == 1 and el >= VINY_T:
            reds.append(w.add_red(5243, 667, name="Viny", route=VINY))
        if len(reds) == 2 and el >= OIO_T:
            reds.append(w.add_red(*OIO_AT, name="OIOOOIIO"))
        if el <= 6.0 and not w.player.ghost:
            near[0] = min(near[0], *(cheb(w.player.pos, r.pos) for r in reds))
    clock.on_advance = watch
    out = io.StringIO()
    outcome = "?"
    try:
        with contextlib.redirect_stdout(out):
            loop = loop_hunt.HuntLoop(link, store, args)
            loop.team.setup(loop.state())
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
    first = next((ln for ln in logl if "to the moongate: the way passes" in ln), "")
    return {"seed": seed, "dead": w.metrics["deaths"], "outcome": outcome,
            "first": "dead end: past" if "a dead end" in first else ("away first" if "away from them" in first
                                                                      else first[9:60]),
            "nearest_6s": near[0], "sim_s": round(clock.elapsed, 1), "log": logl}


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
              f"{sum(r['outcome'] == 'through the gate' for r in rows)}, first decisions "
              f"{ {k: sum(r['first'] == k for r in rows) for k in {r['first'] for r in rows}} }")

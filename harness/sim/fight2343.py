"""The 23:43 death in Maus 3's east wing (hunt-20261030-230618-058b, capture session_20261030_175703, 2026-10-30) in
the simulator. Live: Logan at (5273, 652) with both pets beside him; the red Vowxin Retp came into view at (5255, 653)
at 23:43:26.36 and walked east up the corridor to (5261, 653); S p a c e B a r and Silent Assassin came into view at
(5254, 653) and (5251, 653) at 29.17 and came east to (5257, 653) and (5256, 653). All three held a spellbook and a
shield and cast (Explosion, Energy Bolt, Magic Arrow, Fireball; two Walls of Stone). The flight went "past them at
once" (the wing is a dead end), roped to (5264, 655) and walked west to (5258, 654); at 31.19 Silent Assassin's Wall of
Stone stood across the corridor's only way west at (5254, 652-654), the gate's way had no route, and the flight stood
there "looking again" 6 times until dead at 23:43:36.23 (docs/NOTES.md "Walled in the east wing"). The shadowguard at
(5248, 652) is placed as live.

Per seed, from the leave (HuntLoop.leave_by_gate "urgent", the runner's own flight): dead or through the gate, the
"no route" lines, the seconds Logan stood on one tile in a red's sight within 12 while the wall stood, the longest such
stretch, the cover legs taken, ropes. The reds' tiles and step times are the capture's (0x77/0x20), then they come at
us (sim.world.add_red, the mage kit, a spellbook in hand); the wall's tiles and time are the capture's (World.add_wall).

    python harness/sim/fight2343.py <seeds: 1,2,3 or 1-24> [--log] [--trace DIR] [--harness DIR]

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


def seeds(arg):
    if "-" in arg:
        a, b = arg.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(s) for s in arg.split(",")]


SEEDS = seeds(ARGS[0]) if ARGS and not ARGS[0].startswith("--") else [1]
SHOW_LOG = "--log" in ARGS

import los  # noqa: E402
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

START = (5273, 652, 0)
PETS = ((5273, 653), (5273, 653))            # Bussin and AlaFlambe on one tile beside him when the leave fired
SPAWNS = [("shadowguard", (5248, 652, 0))]   # awake (war mode) live, west of where the wall went up
EXTRA = ["--heal-in-place", "--vet-range", "5", "--crawl-learn-s", "180"]
SPELLBOOK = {"hands": [[0x0EFA, 1, 1]]}      # live each held a spellbook (0x0EFA, layer 1) and a shield
# (x, y, seconds after the one before), from the capture; t 0 = 23:43:26.36, Vowxin Retp in view, the leave
VOWXIN = [(5256, 653, 0.20), (5257, 653, 0.20), (5258, 653, 0.23), (5259, 653, 1.87), (5260, 653, 0.17),
          (5261, 653, 0.27)]
LATE_T = 2.81                                # 29.17: the other two into view
SPACEBAR_AT, SPACEBAR = (5254, 653), [(5255, 653, 0.13), (5256, 653, 0.11), (5257, 653, 0.24)]
SILENT_AT, SILENT = (5251, 653), [(5252, 653, 0.13), (5253, 653, 0.14), (5254, 653, 0.24), (5255, 653, 0.17),
                                  (5256, 653, 0.19)]
WALL_T, WALL = 4.83, [(5254, 652), (5254, 653), (5254, 654)]   # 31.19: Silent Assassin's In Sanct Ylem of 30.69


STILL_S = 1.0                                # a stretch on one tile this long counts as standing (a step takes 0.2-0.4)


def one(seed):
    sc = load()
    sc["spawns"] = [{"key": k, "at": list(at), "respawn_s": [99999, 99999]} for k, at in SPAWNS]
    sc["defaults"]["spawn_spread"] = sc["defaults"]["wander_per_s"] = 0
    sc["player"]["start"] = list(START)
    clock = simclock.SimClock(epoch=simrun.EPOCH)
    w = World(sc, seed=seed)
    for p, xy in zip(w.pets(), PETS):
        p.x, p.y = xy
    link = SimLink(w, clock)
    tracer = trace.Tracer(w, trace.path_for(TRACE, STAMP, "fight2343", seed), label="fight2343", seed=seed,
                          epoch=simrun.EPOCH) if TRACE else None
    db = os.path.join(tempfile.mkdtemp(prefix="fight_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(sc, EXTRA, 10, at_hunt=True)
                                             + ["--memory", db, "--seed", str(seed)])
    patched = simclock.install(clock)
    store = memory.Memory(db)
    world_tick, reds = clock.on_advance, []
    t0, wall = [None], [None]
    # stretches on one tile in a red's sight while the wall stands: all of them, and those after the first "no route"
    seen = {"still_s": 0.0, "run_s": 0.0, "max_s": 0.0, "last": None}
    stall = {"still_s": 0.0, "run_s": 0.0, "max_s": 0.0}
    out = io.StringIO()

    def tally(acc, standing, dt):
        if standing:
            acc["run_s"] += dt
            acc["max_s"] = max(acc["max_s"], acc["run_s"])
        else:
            if acc["run_s"] >= STILL_S:
                acc["still_s"] += acc["run_s"]
            acc["run_s"] = 0.0

    def in_sight(pl) -> bool:
        eye = (pl.x, pl.y, pl.z + los.EYE)
        return any(r.alive and cheb(r.pos, pl.pos) <= 12
                   and w.sight.point_blocked((r.x, r.y, r.z + los.EYE), eye) is None for r in reds)

    def watch(now):
        world_tick(now)
        if t0[0] is None:
            t0[0] = w.t
            reds.append(w.add_red(5255, 653, name="Vowxin Retp", route=list(VOWXIN), spec=SPELLBOOK))
        el = w.t - t0[0]
        if len(reds) == 1 and el >= LATE_T:
            reds.append(w.add_red(*SPACEBAR_AT, name="S p a c e B a r", route=list(SPACEBAR), spec=SPELLBOOK))
            reds.append(w.add_red(*SILENT_AT, name="Silent Assassin", route=list(SILENT), spec=SPELLBOOK))
        if wall[0] is None and el >= WALL_T:
            wall[0] = w.t
            w.add_wall(WALL)
        pl = w.player
        up = wall[0] is not None and any(it.get("kind") == "wall" for it in w.items.values())
        last, seen["last"] = seen["last"], (w.t, pl.pos)
        standing = up and last is not None and not pl.ghost and last[1] == pl.pos and in_sight(pl)
        dt = w.t - last[0] if last is not None else 0.0
        tally(seen, standing, dt)
        tally(stall, standing and "no route from" in out.getvalue(), dt)
    clock.on_advance = watch

    outcome = "?"
    try:
        with contextlib.redirect_stdout(tracer.tee(out) if tracer else out):
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
    result = {"seed": seed, "dead": w.metrics["deaths"], "outcome": outcome,
              "no_route": sum("no route from" in ln for ln in logl),
              "still_seen_s": round(seen["still_s"] + (seen["run_s"] if seen["run_s"] >= STILL_S else 0.0), 1),
              "longest_s": round(seen["max_s"], 1),
              "stall_s": round(stall["still_s"] + (stall["run_s"] if stall["run_s"] >= STILL_S else 0.0), 1),
              "stall_longest_s": round(stall["max_s"], 1),
              "cover": sum("meanwhile, toward" in ln for ln in logl),
              "ropes": sum("rope to" in ln for ln in logl), "walled": wall[0] is not None,
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
              f"{sum(r['outcome'] == 'through the gate' for r in rows)}, 'no route' lines "
              f"{sum(r['no_route'] for r in rows)} (in {sum(r['no_route'] > 0 for r in rows)} runs), standing 1+ s in "
              f"a red's sight while walled {sum(r['still_seen_s'] for r in rows):.1f} s (longest "
              f"{max(r['longest_s'] for r in rows):.1f} s), of it after a 'no route' (the stall) "
              f"{sum(r['stall_s'] for r in rows):.1f} s (longest {max(r['stall_longest_s'] for r in rows):.1f} s), "
              f"cover legs {sum(r['cover'] for r in rows)}, ropes {sum(r['ropes'] for r in rows)}")

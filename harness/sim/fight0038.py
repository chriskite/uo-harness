"""The 00:38 live death (20261024_225113, 2026-10-25) in the simulator, from 00:38:19: Logan at (5244, 729) with the pets
beside a master reanimator at (5239, 721) that wakes on them, an idle shadowguard at (5231, 722) and an idle corpse purger
at (5229, 728) to the west, as the capture has them; no respawns; the live command's runner arguments. The two casters as
the capture shows them (docs/NOTES.md "Dead vetting a pet in two casters' reach"): the reanimator casting every ~2.4 s, its
spells landing on every one of us within 4 of its victim (the lightnings at 00:38:41.3 and 44.7 on Logan and all three
pets) [INFERENCE: the 4]; the shadowguard standing at 11-13 and casting every ~6.7 s at all of us in its reach of 13 (its
fireballs at 37.7 and 44.4). Live the reposition took Logan out of the reanimator's reach, the walk to a hurt pet (the
tank at 92 %) took him back to 4 of it, a vet finish just before a potion's click made the runner count a potion that
was never drunk, and he died 6 s later with no potion tried. Per seed: death, Logan's lowest hits, his damage by source,
potions, vet channels, walks to a hurt pet and the ones not taken, potions not drunk at full health; then the totals.

    python harness/sim/fight0038.py <seeds|a-b> [--log] [--trace DIR] [--harness DIR]

--harness runs another checkout's runner (a snapshot, with this simulator copied in) for a before/after comparison.
"""
import contextlib
import copy
import io
import os
import sys
import tempfile

ARGS = sys.argv[1:]
HARNESS = os.path.abspath(ARGS[ARGS.index("--harness") + 1]) if "--harness" in ARGS \
    else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS)


def _seeds(a):
    if "-" in a:
        lo, hi = a.split("-")
        return list(range(int(lo), int(hi) + 1))
    return [int(s) for s in a.split(",")]


SEEDS = _seeds(ARGS[0]) if ARGS and not ARGS[0].startswith("--") else [1]
SHOW_LOG = "--log" in ARGS

import loop_hunt  # noqa: E402
import memory  # noqa: E402
import tamer  # noqa: E402,F401
import crawl  # noqa: E402,F401
import escape  # noqa: E402,F401
from agent_link import Abort  # noqa: E402
from sim import clock as simclock  # noqa: E402
from sim import run as simrun  # noqa: E402
from sim import trace  # noqa: E402
from sim.link import SimLink  # noqa: E402
from sim.scenario import load  # noqa: E402
from sim.world import World, cheb  # noqa: E402
TRACE = ARGS[ARGS.index("--trace") + 1] if "--trace" in ARGS else None   # record each run's trace (sim/trace.py) here
STAMP = trace.stamp()

# the capture's positions at 00:38:25 (z 0 there): Logan back from a look at a void slime, 12 from the reanimator, which
# is awake at 78 % on the tank; the pets start beside him (the runner's roster is the pets within 3 at the start) and go
# back to it
SPAWNS = [("master reanimator", (5239, 721, 0)), ("shadowguard", (5231, 722, 0)), ("corpse purger", (5229, 727, 0))]
START = (5245, 733, 0)
REANIMATOR_HITS = 0.78
GUARD_WAKES_S = 10.0          # live its first animation in war mode at 00:38:35.0, 10 s in, the pets 8-11 from it
EXTRA = ["--heal-in-place", "--vet-range", "5", "--crawl-learn-s", "180"]
MINUTES = 2
# the casters as live at 00:38:20-45: the reanimator's ten spells on our side in 23.5 s; the shadowguard's fireballs
# 6.7 s apart from 11-13 tiles, on Logan at 13; each on all of us in reach (area_r)
CASTERS = {"master reanimator": {"every_s": 2.4, "area_r": 4},
           "shadowguard": {"every_s": 6.7, "range": 13, "keep": 13, "area_r": 6}}


def one(seed):
    sc = load()
    sc["spawns"] = [{"key": k, "at": list(at), "respawn_s": [99999, 99999]} for k, at in SPAWNS]
    sc["defaults"]["spawn_spread"] = sc["defaults"]["wander_per_s"] = 0   # each where the capture had it
    sc["player"]["start"] = list(START)
    for k, over in CASTERS.items():
        c = sc["creatures"][k] = copy.deepcopy(sc["creatures"][k])
        c["ranged"].update(over)
    clock = simclock.SimClock(epoch=simrun.EPOCH)
    w = World(sc, seed=seed)
    tank = w.pets()[0]
    for m in w.mobs.values():
        if m.key == "master reanimator":
            m.hits, m.warmode, m.target, m.seen_target_t = REANIMATOR_HITS * m.hits_max, True, tank.serial, w.t
            m.threat[tank.serial] = 1
            for p in w.pets():
                p.mode, p.target, p.warmode = "guard", m.serial, True
    link = SimLink(w, clock)
    tracer = trace.Tracer(w, trace.path_for(TRACE, STAMP, "fight0038", seed), label="fight0038", seed=seed,
                          epoch=simrun.EPOCH) if TRACE else None
    db = os.path.join(tempfile.mkdtemp(prefix="fight_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(sc, EXTRA, MINUTES, at_hunt=True)
                                             + ["--memory", db, "--seed", str(seed)])
    hits = []
    orig_hurt = w.hurt

    def hurt(victim, amount, by, kind="melee", song_pets=True, **kw):
        before = victim.hits
        orig_hurt(victim, amount, by, kind, song_pets, **kw)
        if victim is w.player and before != victim.hits:
            hits.append((round(w.t, 1), by.key if by is not None else kind, round(before - victim.hits)))
    w.hurt = hurt
    low = [w.player.hits]
    orig_tick = w._tick
    guard = next(m for m in w.mobs.values() if m.key == "shadowguard")

    def tick():
        orig_tick()
        low[0] = min(low[0], w.player.hits)
        if guard.alive and not guard.warmode and w.t >= GUARD_WAKES_S and tank.alive:
            guard.warmode, guard.target, guard.seen_target_t = True, tank.serial, w.t
            guard.threat[tank.serial] = 1
    w._tick = tick
    patched = simclock.install(clock)
    store = memory.Memory(db)
    out = io.StringIO()
    outcome = "done"
    try:
        with contextlib.redirect_stdout(tracer.tee(out) if tracer else out):
            loop = loop_hunt.HuntLoop(link, store, args)
            try:
                loop.run()
            except Abort as e:
                outcome = f"aborted: {str(e)[:70]}"
    finally:
        store.close()
        simclock.uninstall(patched)
    logl = out.getvalue().splitlines()
    by = {}
    for _, src, n in hits:
        by[src] = by.get(src, 0) + n
    res = {"seed": seed, "dead": w.metrics["deaths"], "low": round(low[0]), "taken": sum(n for _, _, n in hits),
           "by": by, "kills": w.metrics["kills"],
           "potions": sum(line.split(" ", 1)[-1].startswith("heal potion:") for line in logl),
           "vet": sum("vet supplies (" in line for line in logl),
           "to_pet": sum("to a hurt pet: route" in line for line in logl),
           "to_pet_held": sum("to a hurt pet:" in line and "staying out of it" in line for line in logl),
           "not_drunk": sum("heal potion not drunk" in line for line in logl),
           "leaves": sum("LEAVING" in line for line in logl), "sim_s": round(clock.elapsed), "outcome": outcome,
           "log": logl, "hits": hits}
    if tracer is not None:
        tracer.close({k: v for k, v in res.items() if k not in ("log", "hits")})
    return res


if __name__ == "__main__":
    rows = []
    for s in SEEDS:
        r = one(s)
        log = r.pop("log")
        hl = r.pop("hits")
        rows.append(r)
        print(r, flush=True)
        if SHOW_LOG:
            print("    HITS", hl)
            print("\n".join("    " + line[:170] for line in log))
    if len(rows) > 1:
        n = len(rows)
        tot = {k: sum(r[k] for r in rows) for k in ("dead", "potions", "vet", "to_pet", "to_pet_held", "not_drunk",
                                                     "leaves", "kills")}
        print(f"all {n}: deaths {tot['dead']}, lowest hits median {sorted(r['low'] for r in rows)[n // 2]}, "
              f"damage on Logan mean {sum(r['taken'] for r in rows) / n:.0f}, potions {tot['potions']}, "
              f"vet {tot['vet']}, walks to a hurt pet {tot['to_pet']}, held {tot['to_pet_held']}, "
              f"potions not drunk {tot['not_drunk']}, leaves {tot['leaves']}, kills {tot['kills']}")

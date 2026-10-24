"""The 18:21 live death (hunt-20261021-175514-caa5, capture session_20261021_164924, 2026-10-21) in the simulator,
from 18:20:44: Logan at (5244, 672) on 100 hits after a potion, both pets beside him, a blightmare idle at (5242, 685)
that the crawl was about to patrol toward ("crawl: blightmare 0x003C5C2A in view at 13 tiles: patrolling toward it"),
three tormented souls around ((5248, 674), (5251, 681), (5239, 688)); no respawns; the live command's runner
arguments. Live a patrol step woke the blightmare on Logan at 10 tiles, it cast at him from 12 for 16 s while the pets
killed a soul (the reposition never stepped out of its reach), the patrol went on toward it after the loot, and he
died on the walk out (docs/NOTES.md "Dead in a blightmare's reach again"). Per seed: death, Logan's lowest hits, his
damage by source, the blightmare's casts at him, kills, leaves; then the totals.

    python harness/sim/fight1820.py <seeds> [--log] [--trace DIR] [--harness DIR]

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

# the capture's positions at 18:20:44 (the 0x77/0x78 of each serial; the blightmare stood at (5242, 685) all fight)
SPAWNS = [("blightmare", (5242, 685, 0)), ("tormented soul", (5248, 674, 0)), ("tormented soul", (5251, 681, 0)),
          ("tormented soul", (5239, 688, 0))]
START = (5244, 672, 0)
# --awake: from 18:20:49.5, the blightmare in war mode on Logan (since 48.5) at (5242, 673) on 87 hits, the soul that came
# on at (5245, 674) on GraveRobber, the pets beside it (live both at (5246, 675); a tile nearer here, within the 3 tiles
# the runner's start claims its pets by)
AWAKE_SPAWNS = [("blightmare", (5242, 685, 0)), ("tormented soul", (5245, 674, 0)), ("tormented soul", (5251, 681, 0)),
                ("tormented soul", (5239, 688, 0))]
AWAKE_START, AWAKE_HITS, AWAKE_PETS = (5242, 673, 0), 87, ((5245, 675), (5245, 676))
AWAKE = "--awake" in ARGS
EXTRA = ["--heal-in-place", "--vet-range", "5", "--crawl-learn-s", "180"]
MINUTES = 3


def one(seed):
    sc = load()
    sc["spawns"] = [{"key": k, "at": list(at), "respawn_s": [99999, 99999]} for k, at in (AWAKE_SPAWNS if AWAKE else SPAWNS)]
    sc["defaults"]["spawn_spread"] = sc["defaults"]["wander_per_s"] = 0   # each where the capture had it
    sc["player"]["start"] = list(AWAKE_START if AWAKE else START)
    sc["vet"]["range"] = 5
    clock = simclock.SimClock(epoch=simrun.EPOCH)
    w = World(sc, seed=seed)
    if AWAKE:
        bm, soul = (next(m for m in w.mobs.values() if m.kind == "monster" and m.pos[:2] == at[:2])
                    for at in (AWAKE_SPAWNS[0][1], AWAKE_SPAWNS[1][1]))
        w.player.hits = AWAKE_HITS
        for p, xy in zip(w.pets(), AWAKE_PETS):
            p.x, p.y = xy
        gr = next(p for p in w.pets() if p.name == "GraveRobber")
        bm.target, bm.threat = w.player.serial, {w.player.serial: 50}
        soul.target, soul.threat = gr.serial, {gr.serial: 50}
        w._warm(bm, True)
        w._warm(soul, True)
    link = SimLink(w, clock)
    tracer = trace.Tracer(w, trace.path_for(TRACE, STAMP, "fight1820", seed), label="fight1820", seed=seed,
                          epoch=simrun.EPOCH) if TRACE else None
    db = os.path.join(tempfile.mkdtemp(prefix="fight_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(sc, EXTRA, MINUTES, at_hunt=True)
                                             + ["--memory", db, "--seed", str(seed)])
    hits = []
    orig_hurt = w.hurt

    def hurt(victim, amount, by, kind="melee", song_pets=True):
        before = victim.hits
        orig_hurt(victim, amount, by, kind, song_pets)
        if victim is w.player and before != victim.hits:
            hits.append((round(w.t, 1), by.key if by is not None else kind, round(before - victim.hits),
                         cheb(by.pos, victim.pos) if by is not None else None))
    w.hurt = hurt
    low = [w.player.hits]
    orig_tick = w._tick

    def tick():
        orig_tick()
        low[0] = min(low[0], w.player.hits)
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
    for _, src, n, _ in hits:
        by[src] = by.get(src, 0) + n
    res = {"seed": seed, "dead": w.metrics["deaths"], "low": round(low[0]), "taken": sum(h[2] for h in hits),
            "by": by, "blightmare_hits": sum(1 for h in hits if h[1] == "blightmare"), "kills": w.metrics["kills"],
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
        print(r)
        if SHOW_LOG:
            print("    HITS", hl)
            print("\n".join("    " + line[:170] for line in log))
    if len(rows) > 1:
        n = len(rows)
        print(f"all {n}: deaths {sum(r['dead'] for r in rows)}, runs a blightmare hit {sum(1 for r in rows if r['blightmare_hits'])}, "
              f"its hits {sum(r['blightmare_hits'] for r in rows)}, lowest hits median {sorted(r['low'] for r in rows)[n // 2]}, "
              f"damage on Logan mean {sum(r['taken'] for r in rows) / n:.0f}, kills {sum(r['kills'] for r in rows)}, "
              f"leaves {sum(r['leaves'] for r in rows)}")

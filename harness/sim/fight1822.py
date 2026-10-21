"""The 18:22 live death (20261020_180909, 2026-10-20) in the simulator, from 18:22:00: Logan at (5210, 680) with both
pets beside him, a shadowguard idle at (5192, 682) that the crawl was patrolling toward, the countess room beyond it
(a vampire countess, a corpsebride and a vampire bat idle at (5177, 682), (5176, 678), (5175, 682)), an eerie corpse
of the Field of Souls at (5179, 680) (it comes into view at 18 tiles, as live at 18:22:07), empty soul jars in the
pack; the room's neighbours as live (shade wolves and a reanimated knight in the room below, a vampire, a corpsebride
and a shade wolf to the north); no respawns; the live command's runner arguments. Live the runner walked alone to the
corpse while the pets took the shadowguard, the room woke on Logan, and he died on the walk out (docs/NOTES.md "Dead
after a soul-jar walk"). Per seed: death, Logan's lowest hits, his damage by source, the jar filled or not, the seconds a
monster was on him with no pet within 5 tiles, leaves; then the totals.

    python harness/sim/fight1822.py <seeds> [--log] [--harness DIR]

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
import soul_jar  # noqa: E402
from agent_link import Abort  # noqa: E402
from sim import clock as simclock  # noqa: E402
from sim import run as simrun  # noqa: E402
from sim.link import SimLink  # noqa: E402
from sim.scenario import load  # noqa: E402
from sim.world import World, cheb  # noqa: E402

# the capture's positions at 18:22:00-09 (z from the scenario's spawns there)
SPAWNS = [("shadowguard", (5192, 682, 25)), ("vampire countess", (5177, 682, 25)), ("corpsebride", (5176, 678, 25)),
          ("vampire bat", (5175, 682, 25)), ("shade wolf", (5180, 695, -5)), ("shade wolf", (5174, 698, 0)),
          ("reanimated knight", (5179, 698, -3)), ("vampire", (5189, 665, 24)), ("corpsebride", (5189, 664, 24)),
          ("shade wolf", (5191, 664, 23))]
START = (5210, 680, 25)
EERIE = (5179, 680, 25)
EXTRA = ["--heal-in-place", "--vet-range", "5", "--crawl-learn-s", "180"]
MINUTES = 4


def one(seed):
    sc = load()
    sc["spawns"] = [{"key": k, "at": list(at), "respawn_s": [99999, 99999]} for k, at in SPAWNS]
    sc["defaults"]["spawn_spread"] = sc["defaults"]["wander_per_s"] = 0   # each where the capture had it
    sc["player"]["start"] = list(START)
    sc["vet"]["range"] = 5
    sc["loadout"]["items"].append({"serial": 0x6FFF0001, "graphic": soul_jar.EMPTY_JAR_GRAPHIC, "amount": 5,
                                   "kind": "soul_jar"})
    clock = simclock.SimClock(epoch=simrun.EPOCH)
    w = World(sc, seed=seed)
    w.eerie_corpse(*EERIE[:2], spirit="Gilles Styles", z=EERIE[2])
    link = SimLink(w, clock)
    db = os.path.join(tempfile.mkdtemp(prefix="fight_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(sc, EXTRA, MINUTES, at_hunt=True)
                                             + ["--memory", db, "--seed", str(seed)])
    hits = []
    orig_hurt = w.hurt

    def hurt(victim, amount, by, kind="melee", song_pets=True):
        before = victim.hits
        orig_hurt(victim, amount, by, kind, song_pets)
        if victim is w.player and before != victim.hits:
            hits.append((round(w.t, 1), by.key if by is not None else kind, round(before - victim.hits)))
    w.hurt = hurt
    low, alone = [w.player.hits], [0.0, 0]
    orig_tick = w._tick

    def tick():
        orig_tick()
        pl = w.player
        low[0] = min(low[0], pl.hits)
        if pl.alive and not pl.ghost and any(m.kind == "monster" and m.alive and m.target == pl.serial
                                             for m in w.mobs.values()):
            far = min((cheb(p.pos, pl.pos) for p in w.pets() if p.alive and not p.ghost), default=99)
            alone[1] = max(alone[1], far)
            if far > 5:
                alone[0] += 0.1
    w._tick = tick
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
    by = {}
    for _, src, n in hits:
        by[src] = by.get(src, 0) + n
    return {"seed": seed, "dead": w.metrics["deaths"], "low": round(low[0]), "taken": sum(n for _, _, n in hits),
            "by": by, "jar": w.metrics.get("soul_jars", 0), "kills": w.metrics["kills"],
            "on_us_pets_off_s": round(alone[0], 1), "on_us_pets_max_off": alone[1],
            "leaves": sum("LEAVING" in line for line in logl), "sim_s": round(clock.elapsed), "outcome": outcome,
            "log": logl, "hits": hits}


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
        print(f"all {n}: deaths {sum(r['dead'] for r in rows)}, jars {sum(r['jar'] for r in rows)}, "
              f"lowest hits median {sorted(r['low'] for r in rows)[n // 2]}, "
              f"damage on Logan mean {sum(r['taken'] for r in rows) / n:.0f}, "
              f"seconds with a monster on him and no pet within 5, mean {sum(r['on_us_pets_off_s'] for r in rows) / n:.1f}, "
              f"leaves {sum(r['leaves'] for r in rows)}")

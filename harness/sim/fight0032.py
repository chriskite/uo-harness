"""The 00:32:45 live death (hunt-20261025-234615-4525, 2026-10-26) in the simulator, from 00:32:08: Logan at (5247, 687) on
100 hits after a potion, GraveRobber on an awake reanimated daemon at (5241, 693) (86 %), AlaFlambe at (5244, 683), an
idle blightmare at (5242, 685) and two idle tormented souls at (5238, 686) and (5247, 681), as the capture has them; no
respawns; the live command's runner arguments. Live: "to a hurt pet" walked him 8 steps to (5239, 687) for GraveRobber at
65 % (TO_PET_CASTER_AT 0.8 since fafcbc6; 0.5 held it), the soul woke on him, "to heal the tank" stepped 2 deep into the
daemon's bone-burst reach at (5235, 686), the blightmare woke there 6 off, the stand-off out of the daemon's reach found
no route within 6 steps three times, and its spells on all of us at once (-27, -30) with the blightmare's disease and a
last -28 took him 100 -> 0 in 7.6 s, the potion clock running (docs/NOTES.md "Dead in a daemon's and a blightmare's
reach"). Per seed: death, Logan's lowest hits, his damage by source, potions, vet channels, walks to a hurt pet and the
ones not taken; then the totals.

    python harness/sim/fight0032.py <seeds|a-b> [--log] [--set MODULE.NAME=VALUE ...] [--harness DIR]
"""
import ast
import contextlib
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
from agent_link import Abort  # noqa: E402
from sim import clock as simclock  # noqa: E402
from sim import run as simrun  # noqa: E402
from sim.link import SimLink  # noqa: E402
from sim.scenario import load  # noqa: E402
from sim.world import World  # noqa: E402

for i, a in enumerate(ARGS):
    if a == "--set":
        name, value = ARGS[i + 1].split("=", 1)
        mod, attr = name.rsplit(".", 1)
        setattr(sys.modules[mod], attr, ast.literal_eval(value))

SPAWNS = [("reanimated daemon", (5241, 693, 0)), ("blightmare", (5242, 685, 0)), ("tormented soul", (5238, 686, 0)),
          ("tormented soul", (5247, 681, 0))]
START = (5247, 687, 0)
DAEMON_HITS = 0.86

EXTRA = ["--heal-in-place", "--vet-range", "5", "--crawl-learn-s", "180"]
MINUTES = 2


def one(seed):
    sc = load()
    sc["spawns"] = [{"key": k, "at": list(at), "respawn_s": [99999, 99999]} for k, at in SPAWNS]
    sc["defaults"]["spawn_spread"] = sc["defaults"]["wander_per_s"] = 0   # each where the capture had it
    sc["player"]["start"] = list(START)
    clock = simclock.SimClock(epoch=simrun.EPOCH)
    w = World(sc, seed=seed)
    gr = next((p for p in w.pets() if p.name == "GraveRobber"), w.pets()[0])
    for p in w.pets():                    # beside Logan for the runner's roster (pets within 3 at the start); GraveRobber
        p.x, p.y = START[0] - 1, START[1]  # then goes back to the daemon as live (it stood on it at (5241, 692))
    for m in w.mobs.values():
        if m.key == "reanimated daemon":
            m.hits, m.warmode, m.target, m.seen_target_t = DAEMON_HITS * m.hits_max, True, gr.serial, w.t
            m.threat[gr.serial] = 1
            gr.mode, gr.target, gr.warmode = "guard", m.serial, True
    gr.hits = 0.81 * gr.hits_max
    link = SimLink(w, clock)
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

    def tick():
        orig_tick()
        low[0] = min(low[0], w.player.hits)
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
            "by": by, "kills": w.metrics["kills"],
            "potions": sum(line.split(" ", 1)[-1].startswith("heal potion:") for line in logl),
            "vet": sum("vet supplies (" in line for line in logl),
            "to_pet": sum("to a hurt pet: route" in line for line in logl),
            "to_pet_held": sum("to a hurt pet:" in line and "staying out of it" in line for line in logl),
            "tank": sum("to heal the tank: route" in line for line in logl),
            "no_route": sum("stand-off cut short" in line for line in logl),
            "leaves": sum("LEAVING" in line for line in logl), "outcome": outcome, "log": logl}


if __name__ == "__main__":
    rows = []
    for s in SEEDS:
        r = one(s)
        log = r.pop("log")
        rows.append(r)
        print(r, flush=True)
        if SHOW_LOG:
            print("\n".join("    " + line[:170] for line in log))
    if len(rows) > 1:
        n = len(rows)
        tot = {k: sum(r[k] for r in rows) for k in ("dead", "potions", "vet", "to_pet", "to_pet_held", "tank",
                                                     "no_route", "leaves", "kills")}
        print(f"all {n}: deaths {tot['dead']}, lowest hits median {sorted(r['low'] for r in rows)[n // 2]}, "
              f"damage on Logan mean {sum(r['taken'] for r in rows) / n:.0f}, potions {tot['potions']}, "
              f"vet {tot['vet']}, walks to a hurt pet {tot['to_pet']}, held {tot['to_pet_held']}, "
              f"to heal the tank {tot['tank']}, stand-offs with no route {tot['no_route']}, leaves {tot['leaves']}, "
              f"kills {tot['kills']}")

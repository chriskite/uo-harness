"""The 19:49 live fight (20261015_171928) in the simulator: Logan at the arrival (5207, 737) with both pets,
apparition A idle at (5218, 722), apparition B idle at (5195, 724), banshees at (5197, 706) and (5217, 704), no
respawns; the live command's runner arguments. Per seed: Logan's hits taken by source, the lowest hits, death,
leaves (and called-off ones), seconds in a war banshee's spell reach and sight with no pet within 5, B's first
target, and the first 30 runner log lines on request.

    python harness/sim/fight1949.py <seeds> [--log] [--harness DIR]

--harness runs another checkout's runner (a snapshot of the deployed code, with this simulator copied in)
for a before/after comparison (docs/SIM.md "The 19:49 live fight").
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
from sim.link import SimLink  # noqa: E402
from sim.scenario import load  # noqa: E402
from sim.world import World, cheb  # noqa: E402

SPAWNS = [("apparition", (5218, 722)), ("apparition", (5195, 724)), ("banshee", (5197, 706)), ("banshee", (5217, 704))]
EXTRA = ["--heal-in-place", "--vet-range", "5", "--crawl-learn-s", "180"]


def one(seed):
    sc = load()
    sc["spawns"] = [{"key": k, "at": [x, y, 0], "respawn_s": [99999, 99999]} for k, (x, y) in SPAWNS]
    sc["player"]["start"] = [5207, 737, 0]
    sc["vet"]["range"] = 5
    clock = simclock.SimClock(epoch=simrun.EPOCH)
    w = World(sc, seed=seed)
    link = SimLink(w, clock)
    db = os.path.join(tempfile.mkdtemp(prefix="fight_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(sc, EXTRA, 4, at_hunt=True)
                                             + ["--memory", db, "--seed", str(seed)])
    apps = sorted((m for m in w.mobs.values() if m.key == "apparition"), key=lambda m: m.x)
    b_serial = apps[0].serial
    hits, first_b = [], []
    orig_hurt = w.hurt

    def hurt(victim, amount, by, kind="melee"):
        before = victim.hits
        orig_hurt(victim, amount, by, kind)
        if victim is w.player and before != victim.hits:
            hits.append((round(w.t, 1), (by.key if by is not None else kind) + ("" if by is None else f":{kind}:{by.serial & 0xff}"),
                         round(before - victim.hits)))
    w.hurt = hurt
    exposed = [0.0]
    low = [w.player.hits]
    orig_tick = w._tick

    def tick():
        orig_tick()
        pl = w.player
        low[0] = min(low[0], pl.hits)
        b = w.mobs.get(b_serial)
        if b is not None and b.target is not None and not first_b:
            first_b.append("Logan" if b.target == pl.serial else (w.mobs[b.target].name if b.target in w.mobs else "?"))
        if pl.alive and not pl.ghost:
            for m in w.mobs.values():
                if m.key == "banshee" and m.alive and m.target == pl.serial and cheb(m.pos, pl.pos) <= 10 \
                        and w.can_see(m, pl) and not any(cheb(p.pos, pl.pos) <= 5 for p in w.pets() if p.alive):
                    exposed[0] += 0.1
                    break
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
    return {"seed": seed, "dead": w.metrics["deaths"], "low": round(low[0]), "taken": sum(n for _, _, n in hits), "by": by,
            "kills": w.metrics["kills"], "leaves": sum("LEAVING" in l for l in logl),
            "called_off": sum("called off" in l for l in logl), "banshee_exposed_s": round(exposed[0], 1),
            "B_first": first_b[0] if first_b else None, "sim_s": round(clock.elapsed), "outcome": outcome,
            "log": logl, "hits": hits}


if __name__ == "__main__":
    for s in SEEDS:
        r = one(s)
        log = r.pop("log")
        hl = r.pop("hits")
        print(r)
        if SHOW_LOG:
            print("    HITS", hl)
            print("\n".join("    " + l[:170] for l in log))

"""The 23:30 live death (hunt-20261023-220741-f118, capture session_20261023_201849, 2026-10-23) in the simulator,
from 23:30:22.5: Logan at (5241, 692) on 100 hits, both pets a tile behind him at (5241, 693), a blightmare just woken
on him at (5244, 685), 7 tiles off (war mode 22.51, its first spell on him 25.15), an idle tormented soul at
(5235, 686), and south a reanimated daemon at (5240, 700) in war mode on 8 % of its hits, fought by other players
at (5241, 700) (dead 7.5 s later); no respawns; the live command's runner arguments. The runner's state as live: the
pets last told 'All Kill' at the daemon (23:29:32; it was dropped at 23:29:34 and passed over till 23:30:34), so
following, not guarding, while the runner took them for guarding (no 'All Guard Me' since 23:28:52). Live, cover
walked him 8 steps past the blightmare to (5247, 687), 3 tiles from it; it walked after him and cast from 1-2 tiles
with the pets beside it out of war mode until 2.4 s after the 'All Kill' at 23:30:30.6, and he died at 23:30:37
(docs/NOTES.md "Dead with the pets off guard"). Per seed: death, Logan's lowest hits, his damage by source, the
blightmare's hits on him, his nearest tile to it, the cover walks' tiles, kills, leaves; then the totals.

    python harness/sim/fight2330.py <seeds> [--log] [--trace DIR] [--harness DIR]

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

# the capture's positions at 23:30:22.5 (0x20/0x77 of each serial; Logan's and the pets' from their 0x77s)
BLIGHTMARE, SOUL, DAEMON = (5244, 685, 0), (5235, 686, 0), (5240, 700, 0)
SPAWNS = [("blightmare", BLIGHTMARE), ("tormented soul", SOUL), ("reanimated daemon", DAEMON)]
START, PETS = (5241, 692, 0), ((5241, 693), (5241, 693))
DAEMON_HITS = 0.08            # its bar at 23:30:23 (8/100); killed 23:30:30 ("killed a reanimated daemon", kill 96)
OTHERS = ((5241, 700), (5241, 701))                 # LiquidElsa, FireBug and the rest of that party on it
OTHERS_DPS = 90.0             # the two together take its 1,280 hits in ~7 s, as live
EXTRA = ["--heal-in-place", "--vet-range", "5", "--crawl-learn-s", "180"]
MINUTES = 2
COVER = re.compile(r"cover: the .* shoots us here; to \((\d+), (\d+)\)")


def one(seed):
    sc = load()
    sc["spawns"] = [{"key": k, "at": list(at), "respawn_s": [99999, 99999]} for k, at in SPAWNS]
    sc["defaults"]["spawn_spread"] = sc["defaults"]["wander_per_s"] = 0   # each where the capture had it
    sc["player"]["start"] = list(START)
    sc["others"] = {**sc["others"], "enabled": True, "arrive_per_h": 0.0, "unseen": {}}   # only the placed party
    clock = simclock.SimClock(epoch=simrun.EPOCH)
    w = World(sc, seed=seed)
    bm, soul, dm = (next(m for m in w.mobs.values() if m.kind == "monster" and m.pos[:2] == at[:2])
                    for at in (BLIGHTMARE, SOUL, DAEMON))
    for p, xy in zip(w.pets(), PETS):
        p.x, p.y = xy
    party = [w.add_other(x, y, 0, stay=600, dps=OTHERS_DPS / len(OTHERS)) for x, y in OTHERS]
    dm.hits = dm.hits_max * DAEMON_HITS
    dm.target, dm.threat = party[0].serial, {o.serial: 50 for o in party}
    for o in party:
        o.target = dm.serial
    bm.target, bm.threat = w.player.serial, {w.player.serial: 50}
    w._warm(bm, True)
    w._warm(dm, True)
    link = SimLink(w, clock)
    tracer = trace.Tracer(w, trace.path_for(TRACE, STAMP, "fight2330", seed), label="fight2330", seed=seed,
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
    low, near = [w.player.hits], [cheb(w.player.pos, bm.pos)]
    orig_tick = w._tick

    def tick():
        orig_tick()
        low[0] = min(low[0], w.player.hits)
        if bm.alive and w.player.alive and not w.player.ghost:
            near[0] = min(near[0], cheb(w.player.pos, bm.pos))
    w._tick = tick
    patched = simclock.install(clock)
    store = memory.Memory(db)
    out = io.StringIO()
    outcome = "done"
    try:
        with contextlib.redirect_stdout(tracer.tee(out) if tracer else out):
            loop = loop_hunt.HuntLoop(link, store, args)
            setup = loop.team.setup

            def live_team(st):
                # live: "All Guard Me" last at 23:28:52, then 'All Kill' at the daemon (23:29:07, 19, 32; dropped
                # 23:29:34, refused 23:29:47 and 59): the runner took the pets for guarding, but they followed him
                # out of war mode beside the blightmare casting at him until 2.4 s after the 23:30:30.6 'All Kill'
                setup(st)
                loop.team.guard_needed, loop.team.guard_t = False, loop_hunt.time.monotonic() - 90.0
                loop.team.kill_order = dm.serial
                loop.skip[dm.serial] = loop_hunt.time.monotonic() + 11.5    # dropped 23:29:34 for 60 s
            loop.team.setup = live_team
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
    covers = [(int(m.group(1)), int(m.group(2))) for line in logl if (m := COVER.search(line))]
    res = {"seed": seed, "dead": w.metrics["deaths"], "low": round(low[0]), "taken": sum(h[2] for h in hits),
           "by": by, "blightmare_hits": sum(1 for h in hits if h[1] == "blightmare"), "nearest": near[0],
           "covers": covers, "kills": w.metrics["kills"], "leaves": sum("LEAVING" in line for line in logl),
           "sim_s": round(clock.elapsed), "outcome": outcome, "log": logl, "hits": hits}
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
              f"nearest to it median {sorted(r['nearest'] for r in rows)[n // 2]}, "
              f"damage on Logan mean {sum(r['taken'] for r in rows) / n:.0f}, "
              f"cover walks {sum(len(r['covers']) for r in rows)}, kills {sum(r['kills'] for r in rows)}, "
              f"leaves {sum(r['leaves'] for r in rows)}")

"""The 23:33 death on the walk out of Maus 3 (hunt-20261103-232205-58c6, capture session_20261103_101743, 2026-11-03)
in the simulator. Live at 23:32:37 the return rule fired at (5242, 709) with both pets beside him ("LEAVING by the
moongate: taking the loot home"); the walk to the gate at (5203, 703) (117 route steps, north up x 5243 and round)
took Logan, the pets 1-2 behind on follow, to 3-4 tiles of an idle shadowguard at (5244, 652); it woke on him
(23:32:51.84), ensnared and diseased him at 8 tiles (53.78) and again at 13 (00:66) as it followed, and he died at
23:33:05.80 (docs/NOTES.md "Ensnared on the walk out"). The monsters in view as live: the shadowguard, an idle elder
vampire countess at (5215, 639, 15) and an elder vampire at (5212, 641, 22) on the ledge west, the idle master
reanimator at (5241, 724) behind him; no respawns; the live command's runner arguments. Left out: the tormented soul
that WazabiGreen fought at (5237, 675) (another player's fight; it never turned on us).

The run starts on the hunt level with the return rule firing on its first look (HuntLoop.return_reason patched) and
ends at home (no --enter-recall). Per seed: death and whether it came on the leave (between "LEAVING" and the gate),
Logan's lowest hits, his damage by source, the ensnares on him, kills on the way, through the gate, the leave's
seconds; then the totals.

    python harness/sim/fight2333.py <seeds: 1,2,3 or 1-24> [--log] [--trace DIR] [--harness DIR]

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


def seeds(arg):
    if "-" in arg:
        a, b = arg.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(s) for s in arg.split(",")]


SEEDS = seeds(ARGS[0]) if ARGS and not ARGS[0].startswith("--") else [1]
SHOW_LOG = "--log" in ARGS

import loop_hunt  # noqa: E402
import memory  # noqa: E402
from agent_link import Abort  # noqa: E402
from sim import clock as simclock  # noqa: E402
from sim import run as simrun  # noqa: E402
from sim import trace  # noqa: E402
from sim import pets as simpets  # noqa: E402
from sim.link import SimLink  # noqa: E402
from sim.scenario import load  # noqa: E402
from sim.world import World  # noqa: E402
TRACE = ARGS[ARGS.index("--trace") + 1] if "--trace" in ARGS else None   # record each run's trace (sim/trace.py) here
STAMP = trace.stamp()

START = (5242, 709, 0)
PETS = ((5242, 710), (5242, 710))            # Bussin and AlaFlambe on one tile beside him when the leave fired
# the capture's tiles (0x77/0x20) when each came into view, all idle then
SPAWNS = [("shadowguard", (5244, 652, 0)), ("elder vampire countess", (5215, 639, 15)),
          ("elder vampire", (5212, 641, 22)), ("master reanimator", (5241, 724, 0))]
# the shadowguard as live at 23:32:51-33:05: its spells on our side 4.15, 2.73 and 4.10 s apart (the ensnares at 53.78
# and 00.66, the lightning at 57.93, the flamestrike at 04.76 [INFERENCE: its, 1.2 s after its action 6]) from 8-13
# tiles as it followed him west (5244 -> 5235); the lightning on Logan 3 tiles from its target Bussin (area_r); all four
# on Logan, the pets' hits on it from 01.15 on (at_player_p: a spell at a pet goes to him in its reach and sight)
CASTER = {"every_s": 3.7, "range": 13, "keep": 13, "area_r": 3, "at_player_p": 1.0}
EXTRA = ["--heal-in-place", "--vet-range", "5", "--crawl-learn-s", "180"]
WHY = "taking the loot home: 30806 gp carried (the 23:32:37 leave)"
MINUTES = 10
ENSNARED = "You have been ensnared and diseased!"


def one(seed):
    sc = load()
    simpets.select(sc, "phoenix_incubus", None, None)   # the live team: AlaFlambe (the tank) and Bussin
    sc["spawns"] = [{"key": k, "at": list(at), "respawn_s": [99999, 99999]} for k, at in SPAWNS]
    sc["defaults"]["spawn_spread"] = sc["defaults"]["wander_per_s"] = 0   # each where the capture had it
    sc["player"]["start"] = list(START)
    sg = sc["creatures"]["shadowguard"] = copy.deepcopy(sc["creatures"]["shadowguard"])
    sg["ranged"].update(CASTER)
    clock = simclock.SimClock(epoch=simrun.EPOCH)
    w = World(sc, seed=seed)
    for p, xy in zip(w.pets(), PETS):
        p.x, p.y = xy
    link = SimLink(w, clock)
    tracer = trace.Tracer(w, trace.path_for(TRACE, STAMP, "fight2333", seed), label="fight2333", seed=seed,
                          epoch=simrun.EPOCH) if TRACE else None
    db = os.path.join(tempfile.mkdtemp(prefix="fight_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(sc, EXTRA, MINUTES, at_hunt=True)
                                             + ["--memory", db, "--seed", str(seed)])
    hits, texts = [], []
    orig_hurt, orig_sys = w.hurt, w._sys

    def hurt(victim, amount, by, kind="melee", *a, **kw):
        before = victim.hits
        r = orig_hurt(victim, amount, by, kind, *a, **kw)
        if victim is w.player and before != victim.hits:
            hits.append((round(w.t, 1), by.key if by is not None else kind, round(before - victim.hits)))
        return r

    def sys_text(text, *a, **kw):
        texts.append((round(w.t, 1), text))
        return orig_sys(text, *a, **kw)
    w.hurt, w._sys = hurt, sys_text
    low, died = [w.player.hits], [None]
    world_tick = clock.on_advance

    def watch(now):
        world_tick(now)
        low[0] = min(low[0], w.player.hits)
        if died[0] is None and w.player.ghost:
            died[0] = w.t
    clock.on_advance = watch
    patched = simclock.install(clock)
    store = memory.Memory(db)
    out = io.StringIO()
    outcome = "done"
    marks = {}
    try:
        with contextlib.redirect_stdout(tracer.tee(out) if tracer else out):
            loop = loop_hunt.HuntLoop(link, store, args)
            fired = [False]

            def return_reason(st):
                if fired[0]:
                    return None
                fired[0] = True
                marks["leave_t"] = w.t
                return WHY, "return"
            loop.return_reason = return_reason
            orig_through = loop.through_gate

            def through_gate(*a, **kw):
                r = orig_through(*a, **kw)
                if r is not None and "gate_t" not in marks:
                    marks["gate_t"] = w.t
                return r
            loop.through_gate = through_gate
            try:
                loop.run()
            except Abort as e:
                outcome = f"aborted: {str(e)[:70]}"
    finally:
        clock.on_advance = world_tick
        store.close()
        simclock.uninstall(patched)
    logl = out.getvalue().splitlines()
    by = {}
    for _, src, n in hits:
        by[src] = by.get(src, 0) + n
    fought = sum("fought through" in ln for ln in logl)
    leave_t = marks.get("leave_t")
    on_leave = died[0] is not None and leave_t is not None and died[0] >= leave_t \
        and (marks.get("gate_t") is None or died[0] <= marks["gate_t"])
    res = {"seed": seed, "dead": w.metrics["deaths"], "on_leave": on_leave, "low": round(low[0]),
           "taken": sum(n for _, _, n in hits), "by": by,
           "ensnared": sum(t == ENSNARED for _, t in texts), "kills": w.metrics["kills"], "fought": fought,
           "out": not (5100 <= w.player.x <= 5400) and not w.player.ghost,
           "leave_s": round((marks.get("gate_t") or w.t) - leave_t) if leave_t is not None else None,
           "sim_s": round(clock.elapsed), "outcome": outcome, "log": logl, "hits": hits}
    if tracer is not None:
        tracer.close({k: v for k, v in res.items() if k not in ("log", "hits")})
    return res


if __name__ == "__main__":
    rows = []
    for s in SEEDS:
        r = one(s)
        log, hl = r.pop("log"), r.pop("hits")
        rows.append(r)
        print(r)
        if SHOW_LOG:
            print("    HITS", hl)
            print("\n".join("    " + line[:170] for line in log))
    if len(rows) > 1:
        n = len(rows)
        tot = {}
        for r in rows:
            for k, v in r["by"].items():
                tot[k] = tot.get(k, 0) + v
        print(f"all {n}: deaths {sum(r['dead'] for r in rows)} (on the leave {sum(r['on_leave'] for r in rows)}), "
              f"out {sum(r['out'] for r in rows)}, lowest hits median {sorted(r['low'] for r in rows)[n // 2]}, "
              f"under 50 {sum(r['low'] < 50 for r in rows)}, ensnares on Logan {sum(r['ensnared'] for r in rows)} "
              f"(in {sum(r['ensnared'] > 0 for r in rows)} runs), kills {sum(r['kills'] for r in rows)}, leave s median "
              f"{sorted(r['leave_s'] or 0 for r in rows)[n // 2]}, damage on Logan {sum(r['taken'] for r in rows)} "
              f"{dict(sorted(tot.items(), key=lambda kv: -kv[1]))}")

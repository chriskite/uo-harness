"""Maus 3, the walk back to the pets through a tormented soul, from the live layout of 2026-11-04 19:48:24
(hunt-20261104-185209-c092, capture session_20261104_184749; docs/NOTES.md "The walk back to the pets into a tormented
soul").

    python harness/sim/fight1948.py [SEEDS] [--log] [--no-hit-stam] [--set MODULE.NAME=VALUE] [--harness DIR]
                                    [-- runner args]

Logan at (5247, 669) on 85 hits and 24/25 stamina after a cover walk, the pets 14 off on an awake blightmare at (5245,
685) (Bussin at (5244, 683), AlaFlambe at (5243, 683)); a tormented soul in war mode on him at (5246, 669), the one-tile
gap between him and the pets (it stood there 24.3-30.2 live); idle round them, as the capture had them: tormented souls
at (5236, 674), (5250, 680), (5238, 688), a bound soul at (5262, 660), a corpse purger at (5263, 651). The hunt's ticks
(HuntLoop.tick) for SECONDS. Live: "back to Bussin ... route 12 steps" into the soul's tile, refused at 24/25 twice, then
"cut by mobiles" 30 times in 3 s, through on a third shove at 25/25, 85 -> 64 -> 66 hits meanwhile.
A hit on Logan takes a stamina point, as live (docs/SIM.md "Shoving and stamina"); `--no-hit-stam` leaves that out.
SEEDS: "1-24" or "1,5,9" (default 1). A row per seed and a summary: deaths, Logan's lowest hits, the walks back, refused
shoves and 'cut by mobiles' lines, seconds on a monster's tile, the damage on him by source.
`--harness DIR`: run another checkout's harness (a before/after pair on the same script).
"""
import ast
import contextlib
import io
import os
import sys
import tempfile

ARGS = sys.argv[1:]
EXTRA_ARGS = ARGS[ARGS.index("--") + 1:] if "--" in ARGS else []
ARGS = ARGS[:ARGS.index("--")] if "--" in ARGS else ARGS
HARNESS = os.path.abspath(ARGS[ARGS.index("--harness") + 1]) if "--harness" in ARGS \
    else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS)
SETS = [ARGS[i + 1] for i, a in enumerate(ARGS) if a == "--set"]


def seeds(arg):
    if "-" in arg:
        a, b = arg.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(s) for s in arg.split(",")]


POS = [a for i, a in enumerate(ARGS) if not a.startswith("--") and (i == 0 or ARGS[i - 1] not in ("--harness", "--set"))]
SEEDS = seeds(POS[-1]) if POS else [1]
SHOW_LOG = "--log" in ARGS
HIT_STAM = "--no-hit-stam" not in ARGS

import loop_hunt  # noqa: E402
import memory  # noqa: E402
import tamer  # noqa: E402
from agent_link import Abort  # noqa: E402
from sim import clock as simclock  # noqa: E402
from sim import pets as simpets  # noqa: E402
from sim import run as simrun  # noqa: E402
from sim.link import SimLink  # noqa: E402
from sim.scenario import load  # noqa: E402
from sim.world import World  # noqa: E402

for s in SETS:
    name, value = s.split("=", 1)
    mod, attr = name.rsplit(".", 1)
    m = sys.modules.get(mod) or __import__(mod)
    setattr(m, attr, ast.literal_eval(value))

EXTRA = ["--heal-in-place", "--vet-range", "5", "--crawl-learn-s", "180", "--human", "off"]
START, HITS, STAM = (5247, 669, 0), 85, 24
PETS = {"Bussin": (5244, 683), "AlaFlambe": (5243, 683)}
SECONDS = 60.0
# (type, tile, whom it fights or None (idle), stood there throughout)
SPAWNS = [("tormented soul", (5246, 669, 0), "Logan", True),
          ("blightmare", (5245, 685, 0), "Bussin", True),
          ("tormented soul", (5236, 674, 0), None, False),
          ("tormented soul", (5250, 680, 0), None, False),
          ("tormented soul", (5238, 688, 0), None, False),
          ("bound soul", (5262, 660, 0), None, False),
          ("corpse purger", (5263, 651, 0), None, False)]


def one(seed):
    sc = load()
    simpets.select(sc, "phoenix_incubus", None, None)
    sc["spawns"] = [{"key": k, "at": list(at), "respawn_s": [99999, 99999]} for k, at, *_ in SPAWNS]
    sc["defaults"]["spawn_spread"] = sc["defaults"]["wander_per_s"] = 0
    sc["player"]["start"] = list(START)
    w = World(sc, seed=seed)
    clock = simclock.SimClock(epoch=simrun.EPOCH)
    link = SimLink(w, clock)
    pl = w.player
    pl.hits, pl.stam = HITS, STAM
    pets = {p.name: p for p in w.pets()}
    for p in pets.values():                    # beside him for the team's setup (pets within 3), placed after it
        p.x, p.y = START[0] + 1, START[1]
    mobs = [m for m in w.mobs.values() if m.kind == "monster"]
    db = os.path.join(tempfile.mkdtemp(prefix="fight_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, EXTRA + EXTRA_ARGS, 10, at_hunt=True)
                                             + ["--memory", db, "--seed", str(seed)])
    hits = []
    orig_hurt = w.hurt

    def hurt(victim, amount, by, kind="melee", *a, **kw):
        before, stam = victim.hits, pl.stam
        r = orig_hurt(victim, amount, by, kind, *a, **kw)
        if victim is pl and before != victim.hits:
            hits.append((round(w.t, 2), by.key if by is not None else kind, round(before - victim.hits)))
            if HIT_STAM and stam > 0:           # live: a hit on Logan takes a stamina point
                pl.stam = stam - 1
                w.stam_next = w.stam_next or w.t + 2.04
        return r
    w.hurt = hurt
    low, on_mob, last = [pl.hits], [0.0], [None]
    world_tick = clock.on_advance

    def watch(now):
        world_tick(now)
        low[0] = min(low[0], pl.hits)
        if last[0] is not None and pl.alive and not pl.ghost \
                and any(m.alive and m.pos[:2] == pl.pos[:2] for m in mobs):
            on_mob[0] += now - last[0]
        last[0] = now
    clock.on_advance = watch
    patched = simclock.install(clock)
    store = memory.Memory(db)
    out = io.StringIO()
    why = None
    try:
        with contextlib.redirect_stdout(out):
            link.send(tamer.command("all guard me"))
            loop = loop_hunt.HuntLoop(link, store, args)
            loop.team.setup(loop.state())
            loop.spot = loop.fight_spot = (5244, 683)
            loop.start_visit(loop.state())
            who = {"Logan": pl, **pets}
            for name, xy in PETS.items():
                w._teleport(pets[name], (*xy, 0))
                w._moved(pets[name])
            for (key, at, on, stays), m in zip(SPAWNS, sorted(mobs, key=lambda m: [s[1][:2] for s in SPAWNS]
                                                              .index(m.pos[:2]))):
                if on is not None:
                    m.target, m.threat = who[on].serial, {who[on].serial: 50}
                    w._warm(m, True)
                if stays:
                    m.spec = {**m.spec, "step_s": 999.0}
                    m.next_step = 1e9
            pets["Bussin"].target = pets["AlaFlambe"].target = mobs[[s[0] for s in SPAWNS].index("blightmare")].serial
            t_end = w.t + SECONDS
            try:
                while w.t < t_end and pl.alive and not pl.ghost and not loop.tick():
                    pass
            except Abort as e:
                why = str(e)[:80]
    finally:
        clock.on_advance = world_tick
        store.close()
        simclock.uninstall(patched)
    logl = out.getvalue().splitlines()
    by = {}
    for _, src, n in hits:
        by[src] = by.get(src, 0) + n
    return {"seed": seed, "dead": w.metrics["deaths"], "low": round(low[0]), "taken": sum(h[2] for h in hits), "by": by,
            "walks_back": sum(" back to " in ln and "tiles" in ln and ln.split(" ", 1)[1].startswith("back to ")
                              for ln in logl),
            "shoves_refused": sum("shove into" in ln for ln in logl), "cut": sum("cut by mobiles" in ln for ln in logl),
            "on_mob_s": round(on_mob[0], 1), "why": why, "s": round(w.t), "log": logl, "hits": hits}


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
    n = len(rows)
    if n > 1:
        tot = {}
        for r in rows:
            for k, v in r["by"].items():
                tot[k] = tot.get(k, 0) + v
        print(f"all {n}{' ' + ' '.join(SETS) if SETS else ''}{'' if HIT_STAM else ' --no-hit-stam'}"
              f"{' -- ' + ' '.join(EXTRA_ARGS) if EXTRA_ARGS else ''}: deaths {sum(r['dead'] for r in rows)}, lowest hits "
              f"median {sorted(r['low'] for r in rows)[n // 2]}, under 30 {sum(r['low'] < 30 for r in rows)}, walks back "
              f"{sum(r['walks_back'] for r in rows)}, refused shoves {sum(r['shoves_refused'] for r in rows)}, 'cut by "
              f"mobiles' {sum(r['cut'] for r in rows)}, on a monster's tile {sum(r['on_mob_s'] for r in rows):.1f} s, "
              f"damage on Logan {sum(r['taken'] for r in rows)} {dict(sorted(tot.items(), key=lambda kv: -kv[1]))}")

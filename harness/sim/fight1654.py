"""Logan in a combustive zoa's scorch on Tidal Tomb level 2, from the live layouts of 2026-11-01 16:54
(hunt-20261101-165054-62a8; docs/NOTES.md "Tidal Tomb level 2: standing in the combustive zoa's scorch").

    python harness/sim/fight1654.py [CASE] [SEEDS] [--log] [--set MODULE.NAME=VALUE] [--harness DIR] [-- runner args]

CASE "1645" (default): 16:54:45, the zoa 18 off coming in war mode on Bussin, the pets on 'All Follow Me' 6-8 off,
'All Guard Me' due; "1652": 16:54:52, Logan 1 from the pets and 2 from the zoa, behind the Invisible Wall at (6891,
2065), the pets on guard. SEEDS: "1-24" or "1,5,9" (default 1). The runner ticks for SECONDS from the layout; the zoa
and the pets at their live hits, nothing else in the area. A row per seed and a summary: deaths, Logan's lowest hits,
the damage on him by source, seconds he stood within 3 of a pet with the zoa alive in war mode.
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
CASES = ("1645", "1652")
CASE = POS[0] if POS and POS[0] in CASES else "1645"
SEEDS = seeds(POS[-1]) if POS and POS[-1] not in CASES else [1]
SHOW_LOG = "--log" in ARGS

import loop_hunt  # noqa: E402
import memory  # noqa: E402
import tamer  # noqa: E402
from sim import clock as simclock  # noqa: E402
from sim import pets as simpets  # noqa: E402
from sim import run as simrun  # noqa: E402
from sim.link import SimLink  # noqa: E402
from sim.scenario import load  # noqa: E402
from sim.world import World, cheb  # noqa: E402

for s in SETS:
    name, value = s.split("=", 1)
    mod, attr = name.rsplit(".", 1)
    m = sys.modules.get(mod) or __import__(mod)
    setattr(m, attr, ast.literal_eval(value))

AREA = [6815, 1907, 6969, 2100]              # level 2 (the batch's --area)
EXTRA = ["--heal-in-place", "--vet-range", "5", "--crawl-learn-s", "180", "--human", "off"]
SECONDS = 40.0
# start, Logan's hits, the pets: name -> ((x, y), hits fraction, mode); the zoa: (x, y, z), hits fraction, its target
LAYOUT = {
    "1645": {"start": (6902, 2061, -100), "hits": 100, "guard_due": True,
             "pets": {"Bussin": ((6895, 2063), 0.74, "follow"), "AlaFlambe": ((6893, 2063), 0.72, "follow")},
             "zoa": ((6884, 2070, -100), 0.76, "Bussin")},
    "1652": {"start": (6892, 2066, -100), "hits": 100, "guard_due": False,
             "pets": {"Bussin": ((6893, 2066), 0.69, "guard"), "AlaFlambe": ((6893, 2066), 0.70, "guard")},
             "zoa": ((6891, 2064, -100), 0.55, "Bussin")},
}


def one(case, seed):
    lay = LAYOUT[case]
    sc = load("tidal_tomb")
    simpets.select(sc, "phoenix_incubus", None, None)
    sc["area"] = list(AREA)
    zat, zfrac, zon = lay["zoa"]
    sc["spawns"] = [{"key": "combustive zoa", "at": list(zat), "respawn_s": [99999, 99999]}]
    sc["defaults"]["spawn_spread"] = sc["defaults"]["wander_per_s"] = 0
    sc["player"]["start"] = list(lay["start"])
    w = World(sc, seed=seed)
    clock = simclock.SimClock(epoch=simrun.EPOCH)
    link = SimLink(w, clock)
    pl = w.player
    pl.hits = lay["hits"]
    pets = {p.name: p for p in w.pets()}
    link.send(tamer.command("all guard me"))
    for name, ((x, y), frac, mode) in lay["pets"].items():
        p = pets[name]
        p.x, p.y = x, y
        p.hits = p.hits_max * frac
        p.mode = mode
    zoa = next(m for m in w.mobs.values() if m.kind == "monster")
    zoa.hits = zoa.hits_max * zfrac
    zoa.target, zoa.threat = pets[zon].serial, {pets[zon].serial: 50}
    w._warm(zoa, True)
    db = os.path.join(tempfile.mkdtemp(prefix="fight_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, EXTRA + EXTRA_ARGS, 10, at_hunt=True)
                                             + ["--memory", db, "--seed", str(seed)])
    hits = []
    orig_hurt = w.hurt

    def hurt(victim, amount, by, kind="melee", *a, **kw):
        before = victim.hits
        r = orig_hurt(victim, amount, by, kind, *a, **kw)
        if victim is pl and before != victim.hits:
            hits.append((round(w.t, 2), by.key if by is not None else kind, round(before - victim.hits)))
        return r
    w.hurt = hurt
    low, near = [pl.hits], [0.0, None]
    world_tick = clock.on_advance

    def watch(now):
        world_tick(now)
        low[0] = min(low[0], pl.hits)
        inside = zoa.alive and zoa.warmode and any(cheb(pl.pos, p.pos) <= 3 for p in w.pets() if p.alive and not p.ghost)
        if inside and near[1] is not None:
            near[0] += w.t - near[1]
        near[1] = w.t
    clock.on_advance = watch
    patched = simclock.install(clock)
    store = memory.Memory(db)
    out = io.StringIO()
    t0 = w.t
    try:
        with contextlib.redirect_stdout(out):
            loop = loop_hunt.HuntLoop(link, store, args)
            loop.team.setup(loop.state())
            loop.spot = loop.fight_spot = tuple(pl.pos[:2])
            loop.start_visit(loop.state())
            for p in w.pets():                    # setup's guard: back to the live modes
                p.mode = lay["pets"][p.name][2]
            loop.team.guard_needed = lay["guard_due"]
            while w.t - t0 < SECONDS and pl.alive and not pl.ghost:
                loop.tick()
    finally:
        clock.on_advance = world_tick
        store.close()
        simclock.uninstall(patched)
    by = {}
    for _, src, n in hits:
        by[src] = by.get(src, 0) + n
    return {"seed": seed, "dead": w.metrics["deaths"], "low": round(low[0]), "taken": sum(h[2] for h in hits),
            "by": by, "near_s": round(near[0], 1), "zoa_dead": not zoa.alive, "log": out.getvalue().splitlines(),
            "hits": hits}


if __name__ == "__main__":
    rows = []
    for s in SEEDS:
        r = one(CASE, s)
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
        print(f"all {n} ({CASE}{' ' + ' '.join(SETS) if SETS else ''}{' -- ' + ' '.join(EXTRA_ARGS) if EXTRA_ARGS else ''}): "
              f"deaths {sum(r['dead'] for r in rows)}, lowest hits median {sorted(r['low'] for r in rows)[n // 2]}, "
              f"under 50 {sum(r['low'] < 50 for r in rows)}, damage on Logan {sum(r['taken'] for r in rows)} "
              f"{dict(sorted(tot.items(), key=lambda kv: -kv[1]))}, within 3 of a pet in the zoa's fight "
              f"{sum(r['near_s'] for r in rows):.0f} s, zoa killed {sum(r['zoa_dead'] for r in rows)}")

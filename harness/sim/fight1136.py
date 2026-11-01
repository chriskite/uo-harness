"""The planned walk out through Tidal Tomb level 2's gate pocket, from two live layouts (docs/NOTES.md "Tidal Tomb level
2: the gate pocket's crossfire"). Level 2's crimson gate (6913, 2071) sits among a combustive zoa (6886, 2068), a
drowned mariner (6900, 2067), a voidfin (6901, 2061), a giant pincer crab (6920, 2066) and shellpods; both walks went
east along y 2064 to (6894, 2060), 4-6 from the zoa, 6 from the mariner, 3-6 from the voidfin, Logan ahead of the pets.

Case 1136 (the default; 2026-11-01 11:36:28.0, hunt-20261101-104822-bfc9, capture 20261101_103015): "to the moongate:
route 49 steps from (6863, 2065)" after a razorfin's peace; AlaFlambe (6864, 2065) and GraveRobber (6868, 2065) at full;
the razorfin (6869, 2065) calmed 44 s; then idle: the zoa (6887, 2067), a scourgeon (6899, 2046), the mariner
(6900, 2067), the voidfin (6901, 2062), the crab (6913, 2060). Live: the zoa to war at d 4, the mariner's first shot on
Logan (-30) at d 6, the voidfin to war at d 6; the stand fought the three (the user: "a 3 way fight").
Case 1507 (2026-10-31 15:06:53, hunt-20261031-144244-9e62, capture 20261031_132152): "to the moongate: route 73 steps
from (6855, 2046)"; Bussin (6856, 2046) 70 %, AlaFlambe (6857, 2045) 86 %; idle: a frenzyfin (6851, 2080), the zoa
(6885, 2070), the mariner (6901, 2067), the voidfin (6902, 2061), a soulcatcher (6912, 2044). Live: the zoa -29, the
mariner -30, the voidfin -22, -32, -32: Logan down to 17.

The live command's runner arguments, the live team of each case, every monster at its full hits (unknown live), no
respawns. Per seed: death, Logan's lowest hits, his damage by source, the most monsters on him within 10 s
(CROSS_S), through the gate or not, the walk's seconds; then the totals.

    python harness/sim/fight1136.py [1136|1507] <seeds> [--log] [--harness DIR] [--set MODULE.NAME=VALUE ...]
        [-- runner arguments added, e.g. --leave-gate 6897 1992]

--harness runs another checkout's runner (a snapshot, with this simulator copied in) for a before/after comparison;
--set changes a runner constant for the runs (as run.py's).
"""
import ast
import contextlib
import importlib
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
CASES = ("1136", "1507")
CASE = POS[0] if POS and POS[0] in CASES else "1136"
SEEDS = seeds(POS[-1]) if POS and POS[-1] not in CASES else [1]
SHOW_LOG = "--log" in ARGS

import loop_hunt  # noqa: E402
import memory  # noqa: E402
import tamer  # noqa: E402
from agent_link import Abort  # noqa: E402
from sim import clock as simclock  # noqa: E402
from sim import pets as simpets  # noqa: E402
from sim import run as simrun  # noqa: E402
from sim.link import SimLink  # noqa: E402
from sim.scenario import load  # noqa: E402
from sim.world import World, cheb  # noqa: E402

for s in SETS:
    name, value = s.split("=", 1)
    mod, attr = name.rsplit(".", 1)
    m = importlib.import_module(mod)
    if not hasattr(m, attr):
        raise SystemExit(f"--set {s}: {mod} has no {attr}")
    setattr(m, attr, ast.literal_eval(value))

AREA = [6815, 1907, 6969, 2100]              # level 2 (the batch's --area)
EXTRA = ["--heal-in-place", "--vet-range", "5", "--crawl-learn-s", "180", "--human", "off",
         "--leave-gate", "6913", "2071"]
CROSS_S = 10.0
# (type, (x, y, z), hits fraction or None, its target: a pet's name, "Logan", or None idle; calmed seconds)
LAYOUT = {
    "1136": {"start": (6863, 2065, -56), "hits": 100, "team": "GraveRobber, AlaFlambe",
             "pets": {"AlaFlambe": ((6864, 2065), 1.0), "GraveRobber": ((6868, 2065), 1.0)},
             "spawns": [("razorfin", (6869, 2065, -70), None, "GraveRobber", 44.0),   # "Peacemaking ... success (44 s)"
                        ("combustive zoa", (6887, 2067, -100), None, None, 0),
                        ("scourgeon", (6899, 2046, -100), None, None, 0),
                        ("drowned mariner", (6900, 2067, -100), None, None, 0),
                        ("voidfin", (6901, 2062, -100), None, None, 0),
                        ("giant pincer crab", (6913, 2060, -100), None, None, 0)]},
    "1507": {"start": (6855, 2046, -71), "hits": 100, "team": "phoenix_incubus",
             "pets": {"Bussin": ((6856, 2046), 0.70), "AlaFlambe": ((6857, 2045), 0.86)},
             "spawns": [("frenzyfin", (6851, 2080, -100), None, None, 0),
                        ("combustive zoa", (6885, 2070, -100), None, None, 0),
                        ("drowned mariner", (6901, 2067, -100), None, None, 0),
                        ("voidfin", (6902, 2061, -100), None, None, 0),
                        ("soulcatcher", (6912, 2044, -100), None, None, 0)]},
}


def one(case, seed):
    lay = LAYOUT[case]
    sc = load("tidal_tomb")
    simpets.select(sc, lay["team"], None, None)
    sc["area"] = list(AREA)
    sc["spawns"] = [{"key": k, "at": list(at), "respawn_s": [99999, 99999]} for k, at, *_ in lay["spawns"]]
    sc["defaults"]["spawn_spread"] = sc["defaults"]["wander_per_s"] = 0     # each where the capture had it
    sc["player"]["start"] = list(lay["start"])
    w = World(sc, seed=seed)
    clock = simclock.SimClock(epoch=simrun.EPOCH)
    link = SimLink(w, clock)
    pl = w.player
    pl.hits = lay["hits"]
    pets = {p.name: p for p in w.pets()}
    for name, ((x, y), frac) in lay["pets"].items():
        p = pets[name]
        p.x, p.y = x, y
        p.hits = p.hits_max * frac
    link.send(tamer.command("all guard me"))
    mobs = [m for m in w.mobs.values() if m.kind == "monster"]
    for key, at, frac, on, calm in lay["spawns"]:
        m = next(m for m in mobs if m.key == key and m.pos[:2] == at[:2])
        if frac is not None:
            m.hits = m.hits_max * frac
        if on is not None:
            who = pl if on == "Logan" else pets[on]
            m.target, m.threat = who.serial, {who.serial: 50}
            w._warm(m, True)
        if calm:
            m.calm_until = w.t + calm
    db = os.path.join(tempfile.mkdtemp(prefix="fight_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, EXTRA + EXTRA_ARGS, 10, at_hunt=True)
                                             + ["--memory", db, "--seed", str(seed)])
    hits = []                 # (t, source key, serial, amount)
    orig_hurt = w.hurt

    def hurt(victim, amount, by, kind="melee", *a, **kw):
        before = victim.hits
        r = orig_hurt(victim, amount, by, kind, *a, **kw)
        if victim is pl and before != victim.hits:
            hits.append((round(w.t, 2), by.key if by is not None else kind,
                         by.serial if by is not None and by.kind == "monster" else None, round(before - victim.hits)))
        return r
    w.hurt = hurt
    low = [pl.hits]
    world_tick = clock.on_advance

    def watch(now):
        world_tick(now)
        low[0] = min(low[0], pl.hits)
    clock.on_advance = watch
    patched = simclock.install(clock)
    store = memory.Memory(db)
    out = io.StringIO()
    res, t0 = {}, w.t
    try:
        with contextlib.redirect_stdout(out):
            loop = loop_hunt.HuntLoop(link, store, args)
            loop.team.setup(loop.state())
            loop.spot = loop.fight_spot = tuple(pl.pos[:2])
            loop.start_visit(loop.state())
            try:
                res = loop.leave_by_gate("return")
            except Abort as e:
                res = {"ok": False, "error": f"aborted: {str(e)[:60]}"}
    finally:
        clock.on_advance = world_tick
        store.close()
        simclock.uninstall(patched)
    logl = out.getvalue().splitlines()
    by = {}
    for _, src, _s, n in hits:
        by[src] = by.get(src, 0) + n
    cross = 0
    for i, (t, *_r) in enumerate(hits):
        cross = max(cross, len({s for tt, _k, s, _n in hits[i:] if s is not None and tt - t <= CROSS_S}))
    return {"seed": seed, "dead": w.metrics["deaths"], "low": round(low[0]), "taken": sum(h[3] for h in hits),
            "by": by, "cross": cross, "through": bool(res.get("ok")) and pl.alive and not pl.ghost,
            "why": res.get("error") or res.get("called_off"), "walk_s": round(w.t - t0),
            "pet_deaths": w.metrics.get("pet_deaths", 0) if isinstance(w.metrics.get("pet_deaths"), int)
            else len(w.metrics.get("pet_deaths") or []),
            "log": logl, "hits": hits}


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
              f"deaths {sum(r['dead'] for r in rows)}, through the gate {sum(r['through'] for r in rows)}, lowest hits "
              f"median {sorted(r['low'] for r in rows)[n // 2]}, under 50 {sum(r['low'] < 50 for r in rows)}, damage on "
              f"Logan {sum(r['taken'] for r in rows)} {dict(sorted(tot.items(), key=lambda kv: -kv[1]))}, 3+ monsters on "
              f"him within {CROSS_S:.0f} s in {sum(r['cross'] >= 3 for r in rows)}, 2+ in {sum(r['cross'] >= 2 for r in rows)}, "
              f"pet deaths {sum(r['pet_deaths'] for r in rows)}, walk s median {sorted(r['walk_s'] for r in rows)[n // 2]}")

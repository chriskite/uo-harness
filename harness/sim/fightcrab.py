"""The giant pincer crab by Tidal Tomb level 2's south gate, met from the east (user 2026-11-11: Logan "has some bad
positioning near the giant pincer crab near the gate, where he stands right where it will aggro on him"; docs/NOTES.md
"Tidal Tomb level 2: the pets first at every wake").

    python harness/sim/fightcrab.py [SEEDS] [--log] [--set MODULE.NAME=VALUE] [--harness DIR]

The layout is the simulator's own (level 2, seed 1002, at 10:13 and 22:40 of the run; no live capture): Logan at
(6927, 2057) with Bussin and AlaFlambe beside him on guard, a fought walk (HuntLoop.walk_in_fought, the crawl patrol's
stops) to the waypoint (6914, 2060) by the gate. The crab stands idle at (6917, 2063), out of sight from the east until
(6920, 2064), 3 tiles from it, with the pets a tile behind and still out of its sight: before, "Holding: giant pincer
crab in sight at 3 tiles: letting it come", and it woke on Logan. Its neighbours in sight of the fight where the
scenario has them: the voidfin (6901, 2060), the drowned mariner (6900, 2068); the lighthouse shellpod of (6921, 2067)
left out (dead at both times of the run; with it the walk meets it first and comes at the crab another way). No
respawns, no wander.
Per seed: whom the crab took first and Logan's distance from it then, the damage on Logan (by source), its pinches on
him, the crab's death; then the totals. --set tamer.FRONT=False is the runner before the rule (seeds 1-24: on Logan 24
of 24, 2 tiles off, 546 on him, 6 pinches; after: on a pet 24 of 24, none); --harness runs another checkout's runner
on this simulator. harness/test_sim.py test_front_gate_crab runs seeds 1-3 both ways.
"""
import ast
import contextlib
import importlib
import io
import os
import sys
import tempfile

ARGS = sys.argv[1:] if __name__ == "__main__" else []
HARNESS = os.path.abspath(ARGS[ARGS.index("--harness") + 1]) if "--harness" in ARGS \
    else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS)
SETS = [ARGS[i + 1] for i, a in enumerate(ARGS) if a == "--set"]


def seeds(arg):
    if "-" in arg:
        lo, hi = arg.split("-")
        return list(range(int(lo), int(hi) + 1))
    return [int(s) for s in arg.split(",")]


POS = [a for i, a in enumerate(ARGS) if not a.startswith("--") and (i == 0 or ARGS[i - 1] not in ("--harness", "--set"))]
SEEDS = seeds(POS[0]) if POS else [1]
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

TEAM = "phoenix_incubus"
AREA = [6815, 1907, 6969, 2100]              # level 2 (the batch's --area)
EXTRA = ["--heal-in-place", "--vet-range", "5", "--crawl-learn-s", "180", "--human", "off",
         "--leave-gate", "6913", "2071"]
START = (6927, 2057, -100)
PETS = {"Bussin": (6928, 2057), "AlaFlambe": (6928, 2056)}
NEAR = (6914, 2060)                          # the crawl's waypoint by the gate
CRAB = ("giant pincer crab", (6917, 2063, -100))
SPAWNS = [CRAB, ("voidfin", (6901, 2060, -100)), ("drowned mariner", (6900, 2068, -100))]
SECS = 90.0


def one(seed):
    sc = load("tidal_tomb")
    simpets.select(sc, TEAM, None, None)
    sc["area"] = list(AREA)
    sc["spawns"] = [{"key": k, "at": list(at), "respawn_s": [99999, 99999]} for k, at in SPAWNS]
    sc["defaults"]["spawn_spread"] = sc["defaults"]["wander_per_s"] = 0
    sc["player"]["start"] = list(START)
    w = World(sc, seed=seed)
    clock = simclock.SimClock(epoch=simrun.EPOCH)
    link = SimLink(w, clock)
    pl = w.player
    pets = {p.name: p for p in w.pets()}
    for name, (x, y) in PETS.items():
        pets[name].x, pets[name].y = x, y
    link.send(tamer.command("all guard me"))
    crab = next(m for m in w.mobs.values() if m.kind == "monster" and m.key == CRAB[0])
    db = os.path.join(tempfile.mkdtemp(prefix="fight_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, EXTRA, 10, at_hunt=True)
                                             + ["--memory", db, "--seed", str(seed)])
    hits = []                 # (t, source, kind, amount)
    orig_hurt = w.hurt

    def hurt(victim, amount, by, kind="melee", *a, **kw):
        before = victim.hits
        r = orig_hurt(victim, amount, by, kind, *a, **kw)
        if victim is pl and before != victim.hits:
            hits.append((round(w.t, 2), by.key if by is not None else kind, kind, round(before - victim.hits)))
        return r
    w.hurt = hurt
    first = {}                # the crab's first target: who, Logan's distance from it, the nearest pet's
    world_tick = clock.on_advance

    def watch(now):
        world_tick(now)
        if not first and crab.alive and crab.target is not None:
            who = w.mobs.get(crab.target)
            live = [p for p in w.pets() if p.alive and not p.ghost]
            first.update(who="Logan" if who is pl else getattr(who, "name", "?"), t=round(w.t, 1),
                         logan=cheb(pl.pos, crab.pos), pet=min((cheb(p.pos, crab.pos) for p in live), default=None))
    clock.on_advance = watch
    patched = simclock.install(clock)
    store = memory.Memory(db)
    out = io.StringIO()
    outcome = "done"
    try:
        with contextlib.redirect_stdout(out):
            loop = loop_hunt.HuntLoop(link, store, args)
            loop.team.setup(loop.state())
            loop.spot = loop.fight_spot = tuple(pl.pos[:2])
            loop.start_visit(loop.state())
            t_end = w.t + SECS
            loop.walk_hits, loop.walk_hurt_t = None, -1e9      # walk_hurt's baseline, as oxygen_leg sets it
            try:
                loop.walk_in_fought("to the gate's waypoint", NEAR, bail=lambda st: w.t >= t_end or not crab.alive)
                while w.t < t_end and crab.alive and pl.alive and not pl.ghost:
                    loop.tick()
            except Abort as e:
                outcome = f"aborted: {str(e)[:60]}"
    finally:
        clock.on_advance = world_tick
        store.close()
        simclock.uninstall(patched)
    by = {}
    for _, src, _kind, n in hits:
        by[src] = by.get(src, 0) + n
    return {"seed": seed, "first": first.get("who"), "logan_d": first.get("logan"), "pet_d": first.get("pet"),
            "woke_s": first.get("t"), "taken": sum(h[3] for h in hits), "crab": by.get(CRAB[0], 0),
            "pinches": sum(1 for h in hits if h[1] == CRAB[0] and h[2] == "pinch"), "by": by,
            "crab_dead": not crab.alive, "dead": w.metrics["deaths"], "secs": round(w.t), "outcome": outcome,
            "log": out.getvalue().splitlines(), "hits": hits}


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
        woke = [r for r in rows if r["first"]]
        on_logan = [r for r in woke if r["first"] == "Logan"]
        print(f"all {n}{' ' + ' '.join(SETS) if SETS else ''}: the crab woke in {len(woke)}, on Logan {len(on_logan)} "
              f"(his distance then {sorted(r['logan_d'] for r in on_logan)}), on a pet {len(woke) - len(on_logan)}; "
              f"damage on Logan {sum(r['taken'] for r in rows)} (the crab's {sum(r['crab'] for r in rows)}, "
              f"{sum(r['pinches'] for r in rows)} pinches); the crab dead in {sum(r['crab_dead'] for r in rows)}, "
              f"Logan dead in {sum(r['dead'] for r in rows)}")

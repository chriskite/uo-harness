"""The 2026-11-12 12:10 death on Tidal Tomb level 2 (hunt-20261112-113211-43aa, capture session_20261112_101715;
docs/NOTES.md "Tidal Tomb level 2: dead to an unseen voidfin"). Live at 12:10:05 Logan stood at (6875, 2023), 5 steps
off his pets (6869-6870, 2017-2019) out of a soulcatcher's shot ("cover"); the pets on a giant pincer crab (6869, 2016)
at 40 %, the soulcatcher (6865, 2012) awake. A voidfin in war mode came up 2 tiles from Logan at 12:10:09.7 with its
strike (-23), spun (-35), dove, came up 2 off again (-35), spun (-32: dead). No 0x2F from it: the runner's attackers()
took a war-mode monster as on us only beside us, so it was never a target, never the pets' 'All Kill', and Logan stood
off the pets twice more ("stand-off ... beyond the vet supplies' reach of the pets").

The layout: level 2, the live tiles at 12:10:05, the three monsters awake (the crab and the soulcatcher on a pet, the
voidfin on Logan) at their live tiles, no respawn, no wander; the team phoenix_incubus (the simulator has no Clicquot
or Borealish) on 'All Guard Me'. The runner's tick for SECS.

    python harness/sim/fight1210.py [SEEDS] [--log] [--set MODULE.NAME=VALUE] [--harness DIR]

Per seed: Logan dead, his damage by source, the voidfin's, whether the voidfin died, his lowest hits. f46fada
(with another session's uncommitted tamer.py dip work in the tree), seeds 1-24: no death, 6,972 on Logan (the voidfin 6,946), lowest 28, the voidfin never dead in 45 s: the
pets' 'All Kill' at it ends at each dive. Tried and dropped 2026-11-12 (docs/NOTES.md): its strike reach (2) in
attackers(), no change; Logan to within 2 of the pets while a diver is on him, 15 deaths of 24 (the crab's reach).
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
from sim.world import World  # noqa: E402

for s in SETS:
    name, value = s.split("=", 1)
    mod, attr = name.rsplit(".", 1)
    m = importlib.import_module(mod)
    if not hasattr(m, attr):
        raise SystemExit(f"--set {s}: {mod} has no {attr}")
    setattr(m, attr, ast.literal_eval(value))

TEAM = "phoenix_incubus"
AREA = [6815, 1907, 6969, 2100]
EXTRA = ["--heal-in-place", "--vet-range", "5", "--crawl-learn-s", "180", "--human", "off",
         "--leave-gate", "6913", "2071", "--leave-gate", "6897", "1992"]
START = (6875, 2023, -90)
PETS = [(6870, 2018), (6869, 2017)]
# (type, tile, hits share, on: "pet" | "logan")
SPAWNS = [("giant pincer crab", (6869, 2016, -70), 0.4, "pet"), ("soulcatcher", (6865, 2012, -62), 1.0, "pet"),
          ("voidfin", (6878, 2025, -90), 1.0, "logan")]
SECS = 45.0


def one(seed):
    sc = load("tidal_tomb")
    simpets.select(sc, TEAM, None, None)
    sc["area"] = list(AREA)
    sc["spawns"] = [{"key": k, "at": list(at), "respawn_s": [99999, 99999]} for k, at, _, _ in SPAWNS]
    sc["defaults"]["spawn_spread"] = sc["defaults"]["wander_per_s"] = 0
    sc["player"]["start"] = list(START)
    w = World(sc, seed=seed)
    clock = simclock.SimClock(epoch=simrun.EPOCH)
    link = SimLink(w, clock)
    pl = w.player
    pets = list(w.pets())
    for p, (x, y) in zip(pets, PETS):
        p.x, p.y = x, y
    link.send(tamer.command("all guard me"))
    mobs = [m for m in w.mobs.values() if m.kind == "monster"]
    fin = None
    for key, at, frac, on in SPAWNS:
        m = next(m for m in mobs if m.key == key and m.pos[:2] == tuple(at[:2]))
        m.hits = m.hits_max * frac
        victim = pl if on == "logan" else pets[0]
        m.target, m.threat = victim.serial, {victim.serial: 50}
        w._warm(m, True)
        if key == "voidfin":
            fin = m
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
    low = [pl.hits]
    world_tick = clock.on_advance

    def watch(now):
        world_tick(now)
        low[0] = min(low[0], pl.hits)
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
            try:
                while w.t < t_end and pl.alive and not pl.ghost and fin.alive:
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
    log = out.getvalue().splitlines()
    return {"seed": seed, "dead": w.metrics["deaths"], "low": round(low[0]), "taken": sum(h[3] for h in hits),
            "voidfin": by.get("voidfin", 0), "by": by, "fin_dead": not fin.alive, "secs": round(w.t),
            "fin_target": any("voidfin" in line and ("target a voidfin" in line or "All Kill" in line) for line in log),
            "outcome": outcome, "log": log, "hits": hits}


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
        print(f"all {n}{' ' + ' '.join(SETS) if SETS else ''}: Logan dead in {sum(r['dead'] for r in rows)}, damage on "
              f"him {sum(r['taken'] for r in rows)} (the voidfin's {sum(r['voidfin'] for r in rows)}), lowest hits "
              f"{sorted(r['low'] for r in rows)[:6]}; the voidfin a target in {sum(r['fin_target'] for r in rows)}, "
              f"dead in {sum(r['fin_dead'] for r in rows)}")

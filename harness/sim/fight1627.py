"""The walk across Tidal Tomb level 1 to its teleporter down to level 2, from the live layout of 2026-11-02 16:27:53
(hunt-20261102-142649-1bdb; docs/NOTES.md "Tidal Tomb: dead dashing for the teleporter on level 1").

    python harness/sim/fight1627.py [SEEDS] [--log] [--set MODULE.NAME=VALUE] [--harness DIR] [-- runner args]

Logan at (6705, 1995) with the pets beside him walks the --enter-via leg to (6718, 1984) (HuntLoop.walk_in_legs); the
monsters that stood by its last steps live, idle until they woke: a dungeon-nest crab, a lowtide scavenger, a
bottomfeeder and a pincer crab round (6708-6714, 1979-1982), an oozing noughtilus at (6702, 1984). Live: "attacked 11
steps from it; through it now", the bottomfeeder's grip and the crabs on him 4 from the teleporter, 74 -> 0 in 4 s.
SEEDS: "1-24" or "1,5,9" (default 1). A row per seed and a summary: deaths, Logan's lowest hits, the damage on him by
source, through the teleporter (on level 2) or not.
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

AREA = [6608, 1908, 6738, 2221]               # level 1
EXTRA = ["--heal-in-place", "--vet-range", "5", "--crawl-learn-s", "180", "--human", "off"]
START = (6705, 1995, 22)
TELEPORTER = (6718, 1984)
# where each stood live at 16:27:55-58 (capture 20261102_121104)
SPAWNS = [("dungeon-nest crab", (6709, 1983, 14)), ("lowtide scavenger", (6709, 1979, 0)),
          ("bottomfeeder", (6714, 1980, 0)), ("pincer crab", (6710, 1980, 0)), ("oozing noughtilus", (6702, 1984, 14))]
SECONDS = 60.0
WAKE_R = 6
GRIP = "--no-grip" not in ARGS     # the bottomfeeder's live grip on Logan (its feed beside him)
GRIP_AFTER_S = 0.3                # its feed starts this long after it stands beside him (the '*feeds*' line then)


def one(seed):
    sc = load("tidal_tomb")
    simpets.select(sc, "phoenix_incubus", None, None)
    sc["area"] = list(AREA)
    sc["spawns"] = [{"key": k, "at": list(at), "respawn_s": [99999, 99999]} for k, at in SPAWNS]
    sc["defaults"]["spawn_spread"] = sc["defaults"]["wander_per_s"] = 0
    sc["player"]["start"] = list(START)
    w = World(sc, seed=seed)
    clock = simclock.SimClock(epoch=simrun.EPOCH)
    link = SimLink(w, clock)
    pl = w.player
    for p in w.pets():
        p.x, p.y = START[0], START[1] + 1
    link.send(tamer.command("all guard me"))
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
    low = [pl.hits]
    world_tick = clock.on_advance
    mobs = [m for m in w.mobs.values() if m.kind == "monster"]

    def watch(now):
        world_tick(now)
        low[0] = min(low[0], pl.hits)
        for m in mobs:                          # live they woke on him 4-6 tiles off (16:27:57.9-16:28:00.4)
            if m.alive and not m.warmode and max(abs(m.x - pl.x), abs(m.y - pl.y)) <= WAKE_R:
                m.target, m.threat = pl.serial, {pl.serial: 50}
                w._warm(m, True)
            if GRIP and m.key == "bottomfeeder" and m.alive and m.feed is None and not gripped \
                    and max(abs(m.x - pl.x), abs(m.y - pl.y)) <= 1:
                # live its '*feeds*' (no attempt line before it) came 1.7 s after it woke, beside him: the grip
                sp = {**next(s for s in m.spec["specials"] if s["kind"] == "feed"), "chance": 1.0}
                m.feed = [w.t + GRIP_AFTER_S, None, pl.serial, sp]
                gripped.append(w.t)
    gripped = []
    clock.on_advance = watch
    patched = simclock.install(clock)
    store = memory.Memory(db)
    out = io.StringIO()
    why = None
    try:
        with contextlib.redirect_stdout(out):
            loop = loop_hunt.HuntLoop(link, store, args)
            loop.team.setup(loop.state())
            try:
                loop.walk_in_legs([TELEPORTER])
            except Abort as e:
                why = str(e)[:80]
    finally:
        clock.on_advance = world_tick
        store.close()
        simclock.uninstall(patched)
    by = {}
    for _, src, n in hits:
        by[src] = by.get(src, 0) + n
    return {"seed": seed, "dead": w.metrics["deaths"], "low": round(low[0]), "taken": sum(h[2] for h in hits),
            "by": by, "through": pl.x > AREA[2], "why": why, "s": round(w.t), "log": out.getvalue().splitlines(),
            "hits": hits}


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
        print(f"all {n}{' ' + ' '.join(SETS) if SETS else ''}{' -- ' + ' '.join(EXTRA_ARGS) if EXTRA_ARGS else ''}: "
              f"deaths {sum(r['dead'] for r in rows)}, through {sum(r['through'] for r in rows)}, lowest hits median "
              f"{sorted(r['low'] for r in rows)[n // 2]}, under 50 {sum(r['low'] < 50 for r in rows)}, damage on Logan "
              f"{sum(r['taken'] for r in rows)} {dict(sorted(tot.items(), key=lambda kv: -kv[1]))}")

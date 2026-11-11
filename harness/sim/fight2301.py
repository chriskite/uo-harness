"""The step onto Tidal Tomb level 1's teleporter down (6718, 1984) refused by a bottomfeeder standing on it, from the live
layout of 2026-11-10 23:01:05-25 (hunt-20261110-224149-29c6, capture 20261110_202925; docs/NOTES.md "Tidal Tomb: dead
at the teleporter down, gripped").

    python harness/sim/fight2301.py [SEEDS] [--log] [--no-hit-stam] [--no-refresh] [--set MODULE.NAME=VALUE] [--harness DIR]
        [-- runner args]

Logan at (6709, 1986) with the pets beside him walks the --enter-via leg to (6718, 1984) (HuntLoop.walk_in_legs, the level
2 runner's arguments); an error out of it goes to HuntLoop.on_error (the escape home), as the runner's main does. Where
they stood live at 23:01:05: a bottomfeeder idle on the teleporter tile at z -24 (it walked onto it at 23:01:05.98 and
woke on Logan 1.0 s after he stood beside it, its first '*feeds*' and grip 0.45 s later, the second 10.4 s after that), a
flying manta in war mode on him 7 tiles behind (it woke as he passed it at 23:01:02.97). Logan's stamina 24 of 25 as he
comes beside the teleporter (live: the step onto (6718, 1982) took the point 0.2 s before; a shove needs full), and a
hit over 5 on him at full stamina takes a point (live 75 of 102, docs/SIM.md "Shoving and stamina"; as sim/fight2321.py;
off with --no-hit-stam). `--no-refresh`: no stamina potions in the pack (the fight for the tile). Live: the step onto
the teleporter refused, "no teleport after 3 steps", the escape tried the same step twice more, dead at 23:01:23.6 with
no fight. A runner that gives up (its escape failed) leaves him standing there STAND_S, no runner (the Seer's next run
comes later than that).
SEEDS: "1-24" or "1,5,9" (default 1). A row per seed and a summary: deaths, through the teleporter (on level 2), the
runs with the error, those left on level 1 by a failed escape (and dead within STAND_S of it), Logan's lowest hits, the
damage on him by source.
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
REFRESH = "--no-refresh" not in ARGS

import loop_hunt  # noqa: E402
import memory  # noqa: E402
from agent_link import Abort  # noqa: E402
from sim import clock as simclock  # noqa: E402
from sim import packets as P  # noqa: E402
from sim import pets as simpets  # noqa: E402
from sim import run as simrun  # noqa: E402
from sim.link import SimLink  # noqa: E402
from sim.scenario import load, select_level  # noqa: E402
from sim.world import World  # noqa: E402

for s in SETS:
    name, value = s.split("=", 1)
    mod, attr = name.rsplit(".", 1)
    m = sys.modules.get(mod) or __import__(mod)
    setattr(m, attr, ast.literal_eval(value))

AREA = [6608, 1908, 6738, 2221]               # level 1
EXTRA = ["--vet-range", "5", "--crawl-learn-s", "180", "--human", "off"]
START = (6709, 1986, 0)
TELEPORTER = (6718, 1984)
STAM = 24                                     # live 24/25 at the deny (capture 20261110_202925, 0xA3 23:01:08.075)
STAM_SINCE_S = 0.2                            # ... the point gone 0.2 s before he stood beside the teleporter (08.275)
STAM_REGEN_S = 2.04                           # sim.world's
# where each stood live at 23:01:05 (capture 20261110_202925): the bottomfeeder on the teleporter from 23:01:05.98, the
# manta in war mode on Logan since 23:01:02.97
FEEDER = ("bottomfeeder", (6718, 1984, -24))
MANTA = ("flying manta", (6702, 1992, 22))
SECONDS = 120.0
WAKE_AFTER_S = 1.0                # live the bottomfeeder's war mode and first swing 1.0 s after Logan stood beside it
GRIP_AFTER_S = 0.45               # ... its '*feeds*' and the grip 0.45 s after that
GRIP_EVERY_S = 10.4               # ... the second 10.4 s after the first (23:01:09.72, 20.09)
STAND_S = 30.0                    # the runner gone (its escape failed): he stands there this long, no heals (the Seer's next
#                                   run comes later than that)


class Stop(BaseException):
    """The replay's end (Logan dead, on level 2 a while, or SECONDS gone): out through the runner, past its excepts."""


def one(seed):
    sc = load("tidal_tomb")
    select_level(sc, "2")                     # the level 2 runner's ways: --enter-via 6718 1984, the crimson gates
    simpets.select(sc, "phoenix_incubus", None, None)
    sc["area"] = list(AREA)
    sc["spawns"] = [{"key": k, "at": list(at), "respawn_s": [99999, 99999]} for k, at in (FEEDER, MANTA)]
    sc["defaults"]["spawn_spread"] = sc["defaults"]["wander_per_s"] = 0
    sc["player"]["start"] = list(START)
    if not REFRESH:
        sc["loadout"]["items"] = [it for it in sc["loadout"]["items"] if it.get("kind") != "refresh_potion"]
    w = World(sc, seed=seed)
    pl = w.player
    clock = simclock.SimClock(epoch=simrun.EPOCH)
    link = SimLink(w, clock)
    for p in w.pets():
        p.x, p.y, p.z = START[0], START[1] + 1, 4
    feeder = next(m for m in w.mobs.values() if m.key == FEEDER[0])
    feeder.spec = {**feeder.spec, "aggro_range": 0}   # live idle on the tile until Logan stood beside it a second
    manta = next(m for m in w.mobs.values() if m.key == MANTA[0])
    manta.target, manta.threat = pl.serial, {pl.serial: 50}
    w._warm(manta, True)
    db = os.path.join(tempfile.mkdtemp(prefix="fight_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, EXTRA + EXTRA_ARGS, 10)
                                             + ["--memory", db, "--seed", str(seed)])
    hits = []
    orig_hurt = w.hurt

    def hurt(victim, amount, by, kind="melee", *a, **kw):
        before, stam = victim.hits, pl.stam
        r = orig_hurt(victim, amount, by, kind, *a, **kw)
        if victim is pl and before != victim.hits:
            hits.append((round(w.t, 2), (by.key if by is not None else "?") + f" {kind}", round(before - victim.hits)))
            if HIT_STAM and stam >= pl.stam_max and before - victim.hits > 5 and w.rng.random() < 75 / 102:
                pl.stam = stam - 1                 # live: a hit over 5 at full stamina takes a point (75 of 102)
                w.send(P.stam(pl.serial, pl.stam, pl.stam_max))
                w.stam_next = w.stam_next or w.t + STAM_REGEN_S
        return r
    w.hurt = hurt
    low = [pl.hits]
    world_tick = clock.on_advance
    beside, grips, t0, down, tired, ended = [None], [], w.t, [None], [], [None]

    def watch(now):
        world_tick(now)
        low[0] = min(low[0], pl.hits)
        if pl.ghost or not pl.alive or w.t - t0 > SECONDS or (ended[0] is not None and w.t - ended[0] > STAND_S):
            raise Stop()
        if pl.x > AREA[2]:
            down[0] = down[0] or w.t
            if w.t - down[0] > 3.0:
                raise Stop()
            return
        if not tired and max(abs(TELEPORTER[0] - pl.x), abs(TELEPORTER[1] - pl.y)) <= 1:
            tired.append(w.t)                        # live 24/25 as he came beside it
            pl.stam, w.stam_next = STAM, w.t + STAM_REGEN_S - STAM_SINCE_S
            w.send(P.stam(pl.serial, pl.stam, pl.stam_max))
        if not feeder.alive:
            return
        adj = max(abs(feeder.x - pl.x), abs(feeder.y - pl.y)) <= 1
        beside[0] = (beside[0] or w.t) if adj else None
        if adj and not feeder.warmode and w.t - beside[0] >= WAKE_AFTER_S:
            feeder.target, feeder.threat = pl.serial, {pl.serial: 50}
            w._warm(feeder, True)
            feeder.cd["feed"] = 1e18                 # its feeds are the live ones below
            grips.append(w.t + GRIP_AFTER_S - GRIP_EVERY_S)
        if adj and feeder.warmode and feeder.feed is None and grips and w.t - grips[-1] >= GRIP_EVERY_S:
            sp = {**next(s for s in feeder.spec["specials"] if s["kind"] == "feed"), "chance": 1.0}
            feeder.feed = [w.t, None, pl.serial, sp]
            grips.append(w.t)
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
                try:
                    loop.walk_in_legs([TELEPORTER])
                except Abort as e:
                    why = str(e)[:90]
                    loop.on_error(e)                 # as main: the escape home from inside the dungeon
                ended[0] = w.t                       # the runner gone (escape failed): he stands there, the pets with him
                while True:
                    clock.advance(0.25)
            except Stop:
                pass
    finally:
        clock.on_advance = world_tick
        store.close()
        simclock.uninstall(patched)
    by = {}
    for _, src, n in hits:
        by[src] = by.get(src, 0) + n
    stranded = ended[0] is not None and down[0] is None
    return {"seed": seed, "dead": w.metrics["deaths"], "through": down[0] is not None,
            "s": round((down[0] or w.t) - t0, 1), "low": round(low[0]), "taken": sum(h[2] for h in hits),
            "grips": len(grips) - 1 if grips else 0, "feeder_dead": not feeder.alive, "why": why, "by": by,
            "stranded": stranded, "dead_stranded": stranded and bool(w.metrics["deaths"]),
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
        tot = {}
        for r in rows:
            for k, v in r["by"].items():
                tot[k] = tot.get(k, 0) + v
        print(f"all {n}{' ' + ' '.join(SETS) if SETS else ''}{'' if HIT_STAM else ' --no-hit-stam'}"
              f"{'' if REFRESH else ' --no-refresh'}"
              f"{' -- ' + ' '.join(EXTRA_ARGS) if EXTRA_ARGS else ''}: deaths {sum(r['dead'] for r in rows)}, through "
              f"{sum(r['through'] for r in rows)}, the error {sum(r['why'] is not None for r in rows)}, left on level 1 "
              f"by a failed escape {sum(r['stranded'] for r in rows)} (dead within {STAND_S:.0f} s of it "
              f"{sum(r['dead_stranded'] for r in rows)}), lowest hits "
              f"median {sorted(r['low'] for r in rows)[n // 2]}, under 50 {sum(r['low'] < 50 for r in rows)}, damage on "
              f"Logan {sum(r['taken'] for r in rows)} {dict(sorted(tot.items(), key=lambda kv: -kv[1]))}")

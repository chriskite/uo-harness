"""The walk in on Maus 2 to the teleporter at (5522, 637), from the live layout of 2026-11-03 23:20:58.7
(hunt-20261103-224044-73bd, capture session_20261103_101743; docs/NOTES.md "A cover onto an abomination").

    python harness/sim/fight2321.py [SEEDS] [--log] [--stam N] [--no-hit-stam] [--set MODULE.NAME=VALUE]
                                    [--harness DIR] [-- runner args]

Logan at (5513, 651) on 47 hits, 25 s into the walk-in fight ("attacked on the way in; the pets guard" at 23:20:33), the
heal potion's clock 5.6 s in, Bussin on his tile, AlaFlambe at (5511, 653); the walk-in fight's order (walk_in_fight),
then the walk on and through the teleporter down to Maus 3 (walk_in). Each monster where the capture had it at
23:20:58.7, in war mode: an insane reanimator on its ledge at (5507, 657, 30) on Logan, the abomination 0x004A0D93 at
(5511, 652) at 44 % and Discorded, standing beside AlaFlambe, a calamity at (5518, 662) on AlaFlambe (those three stood
there throughout live), a second abomination coming from (5518, 641) on Bussin. Live: "cover: ... to (5511, 652)" (the
first abomination's tile), the step into it refused (Entangle, then the shove at 24/25), 6 s in the reanimator's shot
at (5512, 652), 47 -> 8. Here the shove at full stamina gets through and Logan stands on the abomination's tile instead.
A hit on Logan takes a stamina point, as live (75 of 102 hits over 5 at full stamina in the capture; World doesn't model
it); `--no-hit-stam` leaves that out. `--stam 24`: Logan's stamina at the start as at the live refusal (23:21:02.3).
SEEDS: "1-24" or "1,5,9" (default 1). A row per seed and a summary: deaths, Logan's lowest hits, covers aimed at a
monster's tile, seconds on one, the damage on him by source, through the teleporter (on Maus 3) or not.
`--harness DIR`: run another checkout's harness (a before/after pair on the same script).
"""
import ast
import contextlib
import io
import os
import re
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


POS = [a for i, a in enumerate(ARGS) if not a.startswith("--")
       and (i == 0 or ARGS[i - 1] not in ("--harness", "--set", "--stam"))]
SEEDS = seeds(POS[-1]) if POS else [1]
SHOW_LOG = "--log" in ARGS
HIT_STAM = "--no-hit-stam" not in ARGS
STAM = int(ARGS[ARGS.index("--stam") + 1]) if "--stam" in ARGS else None   # Logan's stamina at the start (else full)

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
    m = sys.modules.get(mod) or __import__(mod)
    setattr(m, attr, ast.literal_eval(value))

EXTRA = ["--heal-in-place", "--vet-range", "5", "--crawl-learn-s", "180", "--human", "off"]
START, HITS = (5513, 651, -10), 47
PETS = {"Bussin": (5513, 651), "AlaFlambe": (5511, 653)}
TELEPORTER, ARRIVAL = (5522, 637), (5199, 737)
# where each stood at 23:20:58.7 (0x77 / 0x20 / its 0xC0s), whom it fought, its bar (0xA1 share), whether it stood
# there throughout (the reanimator on its ledge; the first abomination from 23:20:47.8 to its death at 23:21:04.2, beside
# AlaFlambe [INFERENCE: on her, the pet beside it; Bussin's spells on it came from 2 off])
SPAWNS = [("insane reanimator", (5507, 657, 30), "Logan", 1.0, True),
          ("abomination", (5511, 652, -8), "AlaFlambe", 0.44, True),
          ("calamity", (5518, 662, -10), "AlaFlambe", 1.0, True),
          ("abomination", (5518, 641, -10), "Bussin", 1.0, False)]
DISCORD_S = 74.0              # "Discordance on an abomination: success (74 s)" at 23:20:58 on the first abomination
COVER = re.compile(r"cover: the .* shoots us here; to \((\d+), (\d+)\)")


class Log(io.StringIO):
    """The runner's stdout; each cover's tile checked against the live monsters' tiles as it is written."""

    def __init__(self, w):
        super().__init__()
        self.w, self.onto = w, []

    def write(self, s):
        for x, y in COVER.findall(s):
            tile = (int(x), int(y))
            on = [m.key for m in self.w.mobs.values() if m.kind == "monster" and m.alive and m.pos[:2] == tile]
            if on:
                self.onto.append((round(self.w.t, 1), tile, on[0]))
        return super().write(s)


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
    pl.hits = HITS
    if STAM is not None:
        pl.stam = STAM
    pets = {p.name: p for p in w.pets()}
    for name, xy in PETS.items():
        pets[name].x, pets[name].y = xy
    who = {"Logan": pl, **pets}
    mobs = [m for m in w.mobs.values() if m.kind == "monster"]
    for (key, at, on, share, stays), m in zip(SPAWNS, sorted(mobs, key=lambda m: [s[1][:2] for s in SPAWNS]
                                                              .index(m.pos[:2]))):
        m.hits = round(m.hits_max * share)
        m.target, m.threat = who[on].serial, {who[on].serial: 50}
        w._warm(m, True)
        if share < 1.0:
            m.discord_until = w.t + DISCORD_S
        if stays:
            m.spec = {**m.spec, "step_s": 999.0}
            m.next_step = 1e9
    link.send(tamer.command("all guard me"))
    db = os.path.join(tempfile.mkdtemp(prefix="fight_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, EXTRA + EXTRA_ARGS, 10, at_hunt=False)
                                             + ["--memory", db, "--seed", str(seed)])
    hits = []
    orig_hurt = w.hurt

    def hurt(victim, amount, by, kind="melee", *a, **kw):
        before, stam = victim.hits, pl.stam
        r = orig_hurt(victim, amount, by, kind, *a, **kw)
        if victim is pl and before != victim.hits:
            hits.append((round(w.t, 2), by.key if by is not None else kind, round(before - victim.hits)))
            if HIT_STAM and stam > 0:           # live: a hit on Logan takes a stamina point (the capture's 75 of 102)
                pl.stam = stam - 1
                w.stam_next = w.stam_next or w.t + 2.04
        return r
    w.hurt = hurt
    low, on_mob = [pl.hits], [0.0]
    world_tick = clock.on_advance
    last = [None]

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
    out = Log(w)
    why = None
    try:
        with contextlib.redirect_stdout(out):
            loop = loop_hunt.HuntLoop(link, store, args)
            loop.team.setup(loop.state())
            loop.potions.last = loop_hunt.time.monotonic() - (58.7 - 53.1)   # live the last potion at 23:20:53.1
            try:
                # live the walk-in fight had begun at 23:20:33 ("attacked on the way in; the pets guard"): its order
                # from here, then the walk on and through
                loop.walking_in, loop.walk_hits, loop.walk_hurt_t = True, None, -1e9    # as walk_in_legs sets them
                loop.walk_in_fight(f"to the teleporter at {TELEPORTER[0]},{TELEPORTER[1]}")
                loop.walk_in([TELEPORTER])
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
            "by": by, "cover_onto": len(out.onto), "on_mob_s": round(on_mob[0], 1),
            "through": cheb(pl.pos, (*ARRIVAL, 0)) <= 5, "why": why, "s": round(w.t),
            "log": out.getvalue().splitlines(), "hits": hits, "onto": out.onto}


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
              f"{' -- ' + ' '.join(EXTRA_ARGS) if EXTRA_ARGS else ''}: deaths {sum(r['dead'] for r in rows)}, through "
              f"{sum(r['through'] for r in rows)}, lowest hits median {sorted(r['low'] for r in rows)[n // 2]}, under 20 "
              f"{sum(r['low'] < 20 for r in rows)}, covers onto a monster {sum(r['cover_onto'] for r in rows)} (in "
              f"{sum(r['cover_onto'] > 0 for r in rows)} runs), on a monster's tile {sum(r['on_mob_s'] for r in rows):.1f} s, "
              f"damage on Logan {sum(r['taken'] for r in rows)} {dict(sorted(tot.items(), key=lambda kv: -kv[1]))}")

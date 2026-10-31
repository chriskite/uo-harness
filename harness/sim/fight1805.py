"""The 18:05 death on Tidal Tomb level 2 (hunt-20261031-171847-d9b0, capture session_20261031_170158, 2026-10-31) in the
simulator (docs/NOTES.md "Tidal Tomb level 2: dead between two zoas"). The live team (phoenix_incubus: Bussin and
AlaFlambe), the live command's runner arguments, each monster where the capture had it, no respawns.

Live from 18:05:03.4 (case 1805, the default): Logan at (6899, 2019) on 100 hits, 10 from an awake astral zoa at
(6909, 2024) (46 %) and 11 from the pets beside it at (6910, 2022) (AlaFlambe 19 %, Bussin 50 %, three Backlash
stacks), on it; a biolumen shellpod at (6889, 2010), 10 off, just gone to war on Logan; an idle drowned mariner
(6888, 2024), voidfin (6882, 2013) and scourgeon (6880, 2029). The runner targeted the shellpod and walked 2 toward it
(its barrage -17/-21 from 4), walked to AlaFlambe below --pet-leave-at (the zoa's volley -36 at 6 from the pets),
back to cover beside the shellpod (-22/-17), left at 7 hits and died on the walk out (the zoa's volley, -9). Case 1804
starts at 18:04:36.8 (Logan arrived at the same cover tile, AlaFlambe 90 %, Bussin 100 %, the zoa 100 %, a lighthouse
shellpod on the pets at 47 %; the biolumen shellpod idle where first seen at 18:04:50, (6885, 2001) [INFERENCE: it
drifted toward him live; idle here]). The awake combustive zoa at (6917, 2023) is left out: live it hit none of ours
(no scorch, no effect of it 18:04:34-18:05:19). The zoa's hits are doubled (ZOA_HITS_X) and the pets held on it as
live. Per seed: death, Logan's lowest hits, his damage by source (a burst by its id), the runner's walks to a hurt pet
(and its holds), the covers, the targets taken, the pet deaths, the leave and its hits; then the totals.

    python harness/sim/fight1805.py [1805|1804] <seeds> [--log] [--harness DIR]

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


def seeds(arg):
    if "-" in arg:
        lo, hi = arg.split("-")
        return list(range(int(lo), int(hi) + 1))
    return [int(s) for s in arg.split(",")]


POS = [a for i, a in enumerate(ARGS) if not a.startswith("--") and (i == 0 or ARGS[i - 1] != "--harness")]
CASE = POS[0] if POS and POS[0] in ("1805", "1804") else "1805"
SEEDS = seeds(POS[-1]) if POS and POS[-1] not in ("1805", "1804") else [1]
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

TEAM = "phoenix_incubus"                     # live: Bussin (incubus) and AlaFlambe (phoenix)
AREA = [6815, 1907, 6969, 2100]              # level 2 (the batch's --area)
EXTRA = ["--heal-in-place", "--vet-range", "5", "--crawl-learn-s", "180", "--human", "off",
         "--leave-gate", "6913", "2071"]
# (type, (x, y), hits fraction or None, its target: a pet's name, "Logan", or None idle)
LAYOUT = {
    "1805": {"start": (6899, 2019), "hits": 100, "pets": {"AlaFlambe": ((6910, 2022), 0.19),
                                                          "Bussin": ((6910, 2022), 0.50)},
             "spawns": [("astral zoa", (6909, 2024), 0.46, "AlaFlambe"),
                        ("biolumen shellpod", (6889, 2010), 0.79, "Logan"),     # war mode at 18:05:03.4, 10 off him
                        ("drowned mariner", (6888, 2024), 0.88, None),
                        ("voidfin", (6882, 2013), 0.63, None),
                        ("scourgeon", (6880, 2029), None, None)],
             # live the astral spell's Backlash on both pets at 18:04:48.0, 55.8 and 18:05:03.0: 30 s each, so left
             "backlash": (14.6, 22.4, 29.6),
             "secs": 40.0},
    "1804": {"start": (6899, 2019), "hits": 100, "pets": {"AlaFlambe": ((6910, 2022), 0.90),
                                                          "Bussin": ((6910, 2022), 1.0)},
             "spawns": [("astral zoa", (6909, 2024), 1.0, "AlaFlambe"),
                        ("lighthouse shellpod", (6911, 2023), 0.47, "Bussin"),
                        ("biolumen shellpod", (6885, 2001), 0.79, None),
                        ("drowned mariner", (6886, 2021), 0.88, None),
                        ("voidfin", (6881, 2013), 0.63, None),
                        ("scourgeon", (6881, 2029), None, None)],
             "backlash": (),
             "secs": 70.0},
}
LEAVING = re.compile(r"LEAVING .*hits (\d+)/")
# The zoa lasts as long as live: 46 -> 25 % in 13 s (18:05:03.4-16.4) under these pets with the Backlash on them, where
# the simulator's took 46 -> 0 in ~10 s (fight1805 1805 1-24 at first): its hits doubled here, a fight replay's knob
ZOA_HITS_X = 2.0


def one(case, seed):
    lay = LAYOUT[case]
    sc = load("tidal_tomb")
    simpets.select(sc, TEAM, None, None)
    sc["area"] = list(AREA)
    sc["spawns"] = [{"key": k, "at": [x, y, -90], "respawn_s": [99999, 99999]} for k, (x, y), _f, _t in lay["spawns"]]
    sc["defaults"]["spawn_spread"] = sc["defaults"]["wander_per_s"] = 0     # each where the capture had it
    sc["player"]["start"] = [*lay["start"], -90]
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
        p.backlash = [[w.t + left, 0.2] for left in lay["backlash"]]
    link.send(tamer.command("all guard me"))
    mobs = [m for m in w.mobs.values() if m.kind == "monster"]
    for key, (x, y), frac, on in lay["spawns"]:
        m = next(m for m in mobs if m.key == key and m.pos[:2] == (x, y))
        if key == "astral zoa":
            m.hits_max *= ZOA_HITS_X
        if frac is not None:
            m.hits = m.hits_max * frac
        if on is not None:
            who = pl if on == "Logan" else pets[on]
            m.target, m.threat = who.serial, {who.serial: 50}
            w._warm(m, True)
            if on != "Logan" and key == "astral zoa":
                # live both pets stood on it at (6910, 2022) through the shellpod's 10 s on Logan (18:05:05-15): the
                # simulator's guarding pets take a monster on Logan first, so here they hold it as after an 'All Kill'
                for p in pets.values():
                    p.mode, p.kill, p.target = "kill", m.serial, m.serial
    db = os.path.join(tempfile.mkdtemp(prefix="fight_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, EXTRA, 10, at_hunt=True)
                                             + ["--memory", db, "--seed", str(seed)])
    hits, burst_id = [], [None]               # (t, source, kind, amount, distance from the source, nearest pet)
    orig_hurt, orig_burst = w.hurt, w._burst

    def burst(m, sp, tgt=None):
        burst_id[0] = sp.get("id")
        try:
            return orig_burst(m, sp, tgt)
        finally:
            burst_id[0] = None
    w._burst = burst

    def hurt(victim, amount, by, kind="melee", *a, **kw):
        before = victim.hits
        r = orig_hurt(victim, amount, by, kind, *a, **kw)
        if victim is pl and before != victim.hits:
            live = [p for p in w.pets() if p.alive and not p.ghost]
            hits.append((round(w.t, 2), by.key if by is not None else kind,
                         burst_id[0] if kind == "burst" and burst_id[0] else kind, round(before - victim.hits),
                         cheb(by.pos, pl.pos) if by is not None else None,
                         min(cheb(p.pos, pl.pos) for p in live) if live else None))
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
            t_end = w.t + lay["secs"]
            try:
                while w.t < t_end and pl.alive and not pl.ghost:
                    loop.tick()
            except Abort as e:
                outcome = f"aborted: {str(e)[:60]}"
    finally:
        clock.on_advance = world_tick
        store.close()
        simclock.uninstall(patched)
    logl = out.getvalue().splitlines()
    by = {}
    for _, src, kind, n, _d, _p in hits:
        by[f"{src}:{kind}"] = by.get(f"{src}:{kind}", 0) + n
    leave = next((int(m.group(1)) for ln in logl if (m := LEAVING.search(ln))), None)
    return {"seed": seed, "dead": w.metrics["deaths"], "low": round(low[0]), "taken": sum(h[3] for h in hits),
            "by": by, "volley": sum(n for _, s, k, n, _d, _p in hits if k == "volley"),
            "shellpod": sum(n for _, s, _k, n, _d, _p in hits if s == "biolumen shellpod"),
            "to_pet": sum("to a hurt pet:" in ln and "route" not in ln and "arrived" not in ln
                          and "staying out" not in ln for ln in logl),
            "to_pet_held": sum("to a hurt pet:" in ln and "staying out" in ln for ln in logl),
            "covers": sum(ln.split(" ", 1)[-1].startswith("cover: ") and "route" not in ln and "arrived" not in ln
                          for ln in logl),
            "new_targets": sum(" target a " in ln for ln in logl),
            "pet_deaths": sum("is dead" in ln and "ghost" in ln for ln in logl),
            "leave_at": leave, "outcome": outcome, "log": logl, "hits": hits}


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
        print(f"all {n} ({CASE}): deaths {sum(r['dead'] for r in rows)}, lowest hits median "
              f"{sorted(r['low'] for r in rows)[n // 2]}, damage on Logan {sum(r['taken'] for r in rows)} (the zoa's "
              f"volley {sum(r['volley'] for r in rows)}, the biolumen shellpod {sum(r['shellpod'] for r in rows)}), "
              f"walks to a hurt pet {sum(r['to_pet'] for r in rows)} (held {sum(r['to_pet_held'] for r in rows)}), "
              f"covers {sum(r['covers'] for r in rows)}, targets taken {sum(r['new_targets'] for r in rows)}, "
              f"pet deaths {sum(r['pet_deaths'] for r in rows)}, leaves {sum(r['leave_at'] is not None for r in rows)}")

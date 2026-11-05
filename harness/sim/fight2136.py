"""The --go-home run in Maus 3's mini-boss room, 2026-11-04 21:36:25 (hunt-20261104-213625-eb1f, capture
session_20261104_184749; docs/NOTES.md "Dead in Maus 3's mini-boss room after a double step back").

    python harness/sim/fight2136.py [SEEDS] [--live-box] [--log] [--harness DIR]

Live: a double step back had left Logan in the room at (5242, 587, 0) at 21:35:33 with both pets; the custodian
0x0008C244 (body 22, notoriety 3, '*searches*' every 7-12 s) stood on the dais at (5242, 574-576, 17-20) and never came
down; Logan's hits fell 35-45 at a time every 3-11 s (21:35:45 97 -> 62, :55 100 -> 57, 21:36:02 90 -> 55, :13 100 -> 59,
:17 100 -> 55, 21:37:02 100 -> 59, :05 59 -> 17, :10 19 -> 0). The Seer's plain stop at 21:36:20; the --go-home run at
21:36:25 (Logan 100/100) tried the tome four times ('Magical travel from inside a dungeon is only possible near golden
moongates.'), aborted at 21:36:49 and left him there; dead at 21:37:10.

The simulator has no custodian: a stand-in from the capture, a monster that stays on (5242, 575, 17), awake on Logan,
a ranged hit of 35-45 every 10 s within 14 tiles, no melee, 100,000 hits (the pets never hurt it live) [INFERENCE: its
real hits, its kind of attack (no spell effect was decoded) and its pace beyond these eight drops]. The run is
loop_hunt.main() with --go-home as `ctl run hunt --go-home` starts it, the store knowing the scenario's teleporters (as
the live one did); when it ends with Logan alive in the dungeon, IDLE_S more with no runner (live: 21 s from the abort to
the death). `--live-box`: the runner's dungeon box as it was (y 600 up: the room outside it, so --go-home recalls);
the simulator refuses a recall in the room either way, as the server did. `--harness DIR`: run another checkout's harness
(a before/after pair). A row per seed: home, dead, the way, Logan's lowest hits, the seconds until home or the end.
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


POS = [a for i, a in enumerate(ARGS) if not a.startswith("--") and (i == 0 or ARGS[i - 1] != "--harness")]
SEEDS = seeds(POS[-1]) if POS else [1]
SHOW_LOG = "--log" in ARGS
LIVE_BOX = "--live-box" in ARGS

import escape  # noqa: E402
import loop_hunt  # noqa: E402
import memory  # noqa: E402
from sim import clock as simclock  # noqa: E402
from sim import run as simrun  # noqa: E402
from sim.link import SimLink  # noqa: E402
from sim.scenario import load  # noqa: E402
from sim.world import World, cheb  # noqa: E402

START = (5242, 587, 0)
PETS = ((5241, 586, 0), (5243, 586, 0))
CUSTODIAN = (5242, 575, 17)
IDLE_S = 120.0
HOME = (4134, 1429)                 # the scenario's home row (test_sim SIM_HOME)
SPEC = {"key": "custodian", "name": "a custodian", "body": 22, "notoriety": 3, "hits": 100000, "melee": None,
        "melee_ref": None, "melee_player": None, "step_s": 999.0, "aggro_range": 14, "specials": [], "gold": [0, 0],
        "ranged": {"range": 14, "damage": [35, 45], "damage_player": [35, 45], "every_s": 10.0, "keep": 14,
                   "cast_s": [0.5, 0.5, 0.5], "flight_s": [0.3, 0.3, 0.3], "cast_action": 4,
                   "effect": {"graphic": 14036, "speed": 7, "duration": 0, "hue": 0, "render": 0, "explode": 1}}}


def one(seed):
    sc = load()
    sc["creatures"]["custodian"] = {**copy.deepcopy(sc["creatures"]["insane reanimator"]), **SPEC}
    sc["spawns"] = [{"key": "custodian", "at": list(CUSTODIAN), "respawn_s": [99999, 99999]}]
    sc["defaults"]["spawn_spread"] = sc["defaults"]["wander_per_s"] = 0
    sc["player"]["start"] = list(START)
    w = World(sc, seed=seed)
    for p, at in zip(w.pets(), PETS):
        w._teleport(p, at)
    cust = next(m for m in w.mobs.values() if m.kind == "monster")
    cust.target, cust.threat = w.player.serial, {w.player.serial: 50}
    w._warm(cust, True)
    cust.next_step = 1e9
    clock = simclock.SimClock(epoch=simrun.EPOCH)
    link = SimLink(w, clock)
    db = os.path.join(tempfile.mkdtemp(prefix="fight2136_"), "harness.db")
    store = memory.Memory(db)
    for tp in sc["teleporters"]:
        store.teleporter_record(0, tp["from"][0], tp["from"][1], 0, *tp["to"])
    store.close()
    low, hits = [w.player.hits], []
    orig_hurt = w.hurt

    def hurt(victim, amount, by, kind="melee", *a, **kw):
        before = victim.hits
        r = orig_hurt(victim, amount, by, kind, *a, **kw)
        if victim is w.player and before != victim.hits:
            hits.append((round(w.t, 1), by.key if by is not None else kind, round(before - victim.hits)))
        return r
    w.hurt = hurt
    argv = simrun.runner_args(sc, ["--no-songs", "--heal-in-place", "--vet-range", "5", "--crawl-learn-s", "180",
                                   "--go-home"], 10) + ["--memory", db, "--seed", str(seed), "--human", "off"]
    saved = loop_hunt.Link, sys.argv, escape._DUNGEONS
    loop_hunt.Link = lambda *a, **k: link
    sys.argv = ["loop_hunt.py", *argv]
    if LIVE_BOX:
        live = [dict(d) for d in escape._dungeons()]
        for d in live:
            if d.get("name") == "the Mausoleum":
                d["boxes"] = [[5150, 600, 5760, 760]]
        escape._DUNGEONS = live
    tick = clock.on_advance

    def watch(now):
        tick(now)
        low[0] = min(low[0], w.player.hits)
    clock.on_advance = watch
    patched = simclock.install(clock)
    buf, code = io.StringIO(), None
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            try:
                loop_hunt.main()
            except SystemExit as e:
                code = e.code
        t_end = w.t
        inside = escape.recall_forbidden(w.player.pos, 0) is not None or cheb(w.player.pos, START) <= 40
        if inside and not w.player.ghost:
            end = w.t + IDLE_S
            while w.t < end and not w.player.ghost:
                clock.advance(0.5)
    finally:
        loop_hunt.Link, sys.argv, escape._DUNGEONS = saved
        clock.on_advance = tick
        simclock.uninstall(patched)
    log_ = buf.getvalue()
    way = next((ln.split("escape: ", 1)[1][:60] for ln in log_.splitlines()
                if "escape: " in ln and ("is in reach" in ln or "is within" in ln or "on through" in ln)), None)
    return {"seed": seed, "exit": code, "home": cheb(w.player.pos, (*HOME, 0)) <= 5 and not w.player.ghost,
            "dead": w.metrics["deaths"] > 0, "recalls_tried": log_.count("recall try "),
            "way": way, "low": round(low[0]), "taken": sum(h[2] for h in hits), "s": round(t_end, 1),
            "log": log_.splitlines() + [f"HITS {hits}"]}


if __name__ == "__main__":
    rows = []
    for s in SEEDS:
        r = one(s)
        log = r.pop("log")
        rows.append(r)
        print(r)
        if SHOW_LOG:
            print("\n".join("    " + line[:170] for line in log))
    n = len(rows)
    print(f"{n} runs{' (--live-box)' if LIVE_BOX else ''}: home {sum(r['home'] for r in rows)}, "
          f"dead {sum(r['dead'] for r in rows)}, recall tries {sum(r['recalls_tried'] for r in rows)}, "
          f"lowest hits median {sorted(r['low'] for r in rows)[n // 2]}")

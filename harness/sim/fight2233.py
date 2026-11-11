"""The 2026-11-10 22:33 death on Tidal Tomb level 2 fleeing reds (hunt-20261110-222345-a69a, capture
session_20261110_202925; docs/NOTES.md "Tidal Tomb level 2: dead fleeing reds"). Live: at 22:32:59.55 Logan at (6856,
1993, -56) with both pets beside him on a calmed voidfin; Silent Assassin came into view at (6844, 1975) 18 tiles NW,
Vowxin Retp behind him (the two reds in view; the user saw three); "LEAVING by the moongate", the north crimson gate
(6897, 1992) 50 steps, its way north-east up past where they came down. The flight ran south away from them, stood 13 s
at (6852, 2012) "22+ tiles from them here, looking again" with a scourgeon hitting him (its Pressure stacks) while the
reds, out of view, killed Petal (a blue), then roped beside a toxic zoa and a lighthouse shellpod with Silent Assassin
back on him, switched to the south gate (6913, 2071) at 22:33:32 and died at (6872, 2066) to a frenzyfin, a razorfin,
the scourgeon's Pressure release and one Energy Bolt, Silent Assassin 5-6 tiles behind him from 22:33:25.

The layout: level 2's scenario spawns (no respawn, no wander), the monsters seen live in the window at their first
tiles (each replacing the nearest scenario spawn of its type within 12, else added), the scenario spawns near the live
track that live showed absent dropped. The reds' tiles and step times are the capture's up to where they stood by
Petal's body (22:33:06; out of our view until 22:33:21.8 / 23.3), held there that long, then they come at us
(sim.world.add_red, the mage kit, a spellbook in hand; the scenario's `reds` levels, so they track us). The Wall of
Stone at (6860-6862, 2057) as live (22:33:34.86). The runner starts REACT_S after the first sight, as live.

Per seed, from the leave (HuntLoop.leave_by_gate "urgent", the runner's own flight): dead or through which gate, the
walk's seconds, Logan's lowest hits, damage on him by source (monsters by type, reds by name), the nearest a red came,
the seconds he stood still (STILL_S) while hurt by a monster with no red within 12, the gate switches, ropes.

    python harness/sim/fight2233.py <seeds: 1,2,3 or 1-24> [--chase] [--log] [--trace DIR] [--harness DIR]
        [--set M.N=V]

--chase (not live): the reds come at us at once, no stand by Petal's body. --harness runs another checkout's runner (a
snapshot, with this simulator copied in) for a before/after comparison; --set changes a runner constant for the runs
(as run.py's).
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
        a, b = arg.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(s) for s in arg.split(",")]


import loop_hunt  # noqa: E402
import memory  # noqa: E402
from agent_link import Abort  # noqa: E402
from sim import clock as simclock  # noqa: E402
from sim import run as simrun  # noqa: E402
from sim import trace  # noqa: E402
from sim.link import SimLink  # noqa: E402
from sim.scenario import load, select_level  # noqa: E402
from sim.world import World, cheb  # noqa: E402
STAMP = trace.stamp()

for s in SETS:
    name, value = s.split("=", 1)
    mod, attr = name.rsplit(".", 1)
    m = importlib.import_module(mod)
    if not hasattr(m, attr):
        raise SystemExit(f"--set {s}: {mod} has no {attr}")
    setattr(m, attr, ast.literal_eval(value))

START = (6856, 1993, -56)
PETS = ((6859, 1993), (6859, 1993))           # both on one tile 3 E of him at 22:32:57.65, guarding on the voidfin
EXTRA = ["--heal-in-place", "--vet-range", "5", "--crawl-learn-s", "180"]
SPELLBOOK = {"hands": [[0x0EFA, 1, 1]]}       # as the other replays' casters (Silent Assassin cast live)
MATCH_R = 12                                  # a live monster replaces the nearest scenario spawn of its type this near
SEEN_R = 15                                   # a scenario spawn this near the live track and not seen live: absent
# (type, (x, y, z), hits fraction, calmed s, on a pet): first sight in 22:32:30-22:33:39 (the capture's 0x77/0x20)
LIVE = [
    ("voidfin", (6860, 1992, -56), 0.45, 38.0, True),    # 42/100 at 22:33:00.91; "Peacemaking ... success (42 s)" 22:32:55
    ("frenzyfin", (6861, 1984, -86), None, 0, False),    # in war mode after a blue (Framing Dad, Petal), not on us
    ("scourgeon", (6850, 2007, -56), None, 0, False),
    ("astral zoa", (6841, 2005, -56), None, 0, False),
    ("soulcatcher", (6865, 2013, -70), None, 0, False),
    ("kelpdweller", (6840, 2015, -56), None, 0, False),
    ("toxic zoa", (6841, 2024, -56), None, 0, False),
    ("lighthouse shellpod", (6868, 2024, -70), None, 0, False),
    ("giant pincer crab", (6863, 2030, -70), None, 0, False),
    ("voidfin", (6851, 2030, -56), None, 0, False),
    ("scourgeon", (6862, 2041, -70), None, 0, False),
    ("astral zoa", (6858, 2042, -71), None, 0, False),
    ("lighthouse shellpod", (6862, 2046, -70), None, 0, False),
    ("biolumen shellpod", (6870, 2034, -78), 0.53, 0, False),
    ("scourgeon", (6873, 2034, -90), None, 0, False),
    ("combustive zoa", (6849, 2054, -71), None, 0, False),
    ("frenzyfin", (6860, 2063, -70), None, 0, False),
    ("razorfin", (6858, 2065, -70), None, 0, False),
    ("razorfin", (6871, 2065, -70), None, 0, False),
    ("combustive zoa", (6886, 2069, -100), None, 0, False),
]
# Logan's live track (the capture), for the spawns live showed absent
TRACK = [(6856, 1993), (6854, 1987), (6852, 1989), (6857, 1998), (6852, 2002), (6852, 2012), (6843, 2021),
         (6851, 2029), (6851, 2036), (6858, 2042), (6863, 2048), (6863, 2056), (6863, 2064), (6872, 2066)]
# (x, y, seconds after the one before), single steps; t 0 = 22:32:59.55, Silent Assassin's first sight and the leave.
# Each ends by Petal's body (she died 22:33:04.30 at (6854, 1983)), its last step one tile on after as long as the red
# was out of our view; then it comes at us. Gaps in the capture's steps (a run's missed updates, a Teleport) are filled
# tile by tile over their time.
SILENT_AT = (6844, 1975)                      # 22:32:59.55
SILENT = [(6845, 1976, 1.96), (6846, 1977, 0.23), (6846, 1978, 1.94), (6846, 1979, 0.27), (6846, 1980, 0.20),
          (6846, 1981, 0.19), (6847, 1981, 0.30), (6848, 1981, 0.18), (6849, 1981, 0.23), (6850, 1982, 0.23),
          (6851, 1982, 0.23), (6852, 1983, 0.20), (6853, 1983, 0.27), (6853, 1984, 15.79)]   # next seen 22:33:21.77
VOWXIN_T, VOWXIN_AT = 3.20, (6844, 1971)      # 22:33:02.75
VOWXIN = [(6844, 1972, 0.30), (6844, 1973, 0.20), (6844, 1974, 0.21), (6844, 1975, 0.19), (6844, 1976, 0.23),
          *[(6844 + i, 1976 + i, 0.27) for i in range(1, 7)], (6851, 1982, 0.30), (6852, 1982, 0.21),
          (6852, 1983, 17.25)]                                        # next seen 22:33:23.26
WALL_T, WALL = 35.31, [(6860, 2057), (6861, 2057), (6862, 2057)]   # three 0x4D1B in view at 22:33:34.86
STILL_S = 1.0
REACT_S = 0.65                                # live: Silent Assassin in view 22:32:59.55, "All Follow Me" 22:33:00.20


def layout(sc):
    """Level 2's spawns with the live layout over them -> (spawns, [(spawn index, hits frac, calm s, on a pet)])."""
    spawns = [dict(s, respawn_s=[99999, 99999]) for s in sc["spawns"]]
    used, marks = set(), []
    for key, at, frac, calm, on in LIVE:
        cands = [(cheb(s["at"], at), i) for i, s in enumerate(spawns) if s["key"] == key and i not in used
                 and cheb(s["at"], at) <= MATCH_R]
        if cands:
            i = min(cands)[1]
            spawns[i] = {**spawns[i], "at": list(at)}
        else:
            spawns.append({"key": key, "at": list(at), "respawn_s": [99999, 99999]})
            i = len(spawns) - 1
        used.add(i)
        marks.append((i, frac, calm, on))
    keep = [i for i, s in enumerate(spawns) if i in used or min(cheb(s["at"], t) for t in TRACK) > SEEN_R]
    index = {i: n for n, i in enumerate(keep)}
    return [spawns[i] for i in keep], [(index[i], frac, calm, on) for i, frac, calm, on in marks]


def one(seed, chase=False, trace_dir=None, reds_log=False):
    """One run of the flight (seed): its summary, with the runner's log lines (`log`), Logan's hits by source (`hits`)
    and, `reds_log`, each second's tiles of Logan and the reds (`reds`). `chase`: --chase."""
    sc = load("tidal_tomb")
    select_level(sc, "2")
    sc["spawns"], marks = layout(sc)
    sc["defaults"]["spawn_spread"] = sc["defaults"]["wander_per_s"] = 0
    sc["player"]["start"] = list(START)
    sc["reds"] = {**sc["reds"], "enabled": False}            # its levels and the kits; no arrivals
    clock = simclock.SimClock(epoch=simrun.EPOCH)
    w = World(sc, seed=seed)
    pl = w.player
    pets = list(w.pets())
    for p, xy in zip(pets, PETS):
        p.x, p.y = xy
    link = SimLink(w, clock)
    mobs = [m for m in w.mobs.values() if m.kind == "monster"]
    for i, frac, calm, on in marks:
        at = sc["spawns"][i]["at"]
        m = next(m for m in mobs if m.key == sc["spawns"][i]["key"] and m.pos[:2] == tuple(at[:2]))
        if frac is not None:
            m.hits = m.hits_max * frac
        if on:
            m.target, m.threat = pets[0].serial, {pets[0].serial: 50}
            w._warm(m, True)
        if calm:
            m.calm_until = w.t + calm
    tracer = trace.Tracer(w, trace.path_for(trace_dir, STAMP, "fight2233", seed), label="fight2233", seed=seed,
                          epoch=simrun.EPOCH) if trace_dir else None
    db = os.path.join(tempfile.mkdtemp(prefix="fight_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(sc, EXTRA, 10, at_hunt=True)
                                             + ["--memory", db, "--seed", str(seed)])
    hits = []                                  # (t, source, amount)
    orig_hurt = w.hurt

    def hurt(victim, amount, by, kind="melee", *a, **kw):
        before = victim.hits
        r = orig_hurt(victim, amount, by, kind, *a, **kw)
        if victim is pl and before != victim.hits:
            src = (by.name if by.kind == "red" else by.key) if by is not None else kind
            hits.append((round(w.t, 2), src, by.kind if by is not None else None, round(before - victim.hits)))
        return r
    w.hurt = hurt
    w.add_red(*SILENT_AT, name="Silent Assassin", route=SILENT[:-1] if chase else list(SILENT), spec=SPELLBOOK)
    patched = simclock.install(clock)
    store = memory.Memory(db)
    world_tick, t0, wall, late = clock.on_advance, w.t, [False], [False]
    low = [pl.hits]
    still = {"s": 0.0, "run": 0.0, "last": None}
    near = {"d": 99, "log": []}                # the nearest any red came; (s, red, its tile, d) each second

    def watch(now):
        world_tick(now)
        el = w.t - t0
        if not late[0] and el >= VOWXIN_T:
            late[0] = True
            w.add_red(*VOWXIN_AT, name="Vowxin Retp", route=VOWXIN[:-1] if chase else list(VOWXIN), spec=SPELLBOOK)
        if not wall[0] and el >= WALL_T:
            wall[0] = True
            w.add_wall(WALL)
        low[0] = min(low[0], pl.hits)
        last, still["last"] = still["last"], (w.t, pl.pos)
        reds_near = any(r.alive and cheb(r.pos, pl.pos) <= 12 for r in w.reds.mobs())
        for r in w.reds.mobs():
            near["d"] = min(near["d"], cheb(r.pos, pl.pos))
        if reds_log and int(el) > len(near["log"]):
            near["log"].append((int(el), pl.pos[:2], [(r.name, r.pos[:2], cheb(r.pos, pl.pos)) for r in w.reds.mobs()]))
        hurt_now = any(k == "monster" and w.t - t <= 3.0 for t, _s, k, _n in hits[-6:])
        standing = last is not None and not pl.ghost and last[1] == pl.pos and hurt_now and not reds_near
        dt = w.t - last[0] if last is not None else 0.0
        if standing:
            still["run"] += dt
        else:
            if still["run"] >= STILL_S:
                still["s"] += still["run"]
            still["run"] = 0.0
    clock.on_advance = watch
    out = io.StringIO()
    res = {}
    try:
        with contextlib.redirect_stdout(tracer.tee(out) if tracer else out):
            loop = loop_hunt.HuntLoop(link, store, args)
            loop.team.setup(loop.state())
            loop.spot = loop.fight_spot = tuple(pl.pos[:2])
            loop.start_visit(loop.state())
            loop.fleeing = True
            clock.advance(REACT_S)
            try:
                res = loop.leave_by_gate("urgent")
            except Abort as e:
                res = {"ok": False, "error": f"aborted: {str(e)[:60]}"}
    finally:
        clock.on_advance = world_tick
        store.close()
        simclock.uninstall(patched)
    logl = out.getvalue().splitlines()
    by = {}
    for _, src, _k, n in hits:
        by[src] = by.get(src, 0) + n
    result = {"seed": seed, "dead": w.metrics["deaths"],
              "gate": tuple(res.get("gate") or ()) if res.get("ok") and pl.alive and not pl.ghost else None,
              "why": None if res.get("ok") else (res.get("error") or "")[:60], "walk_s": round(w.t - t0, 1),
              "low": round(low[0]), "taken": sum(h[3] for h in hits),
              "by_monsters": sum(h[3] for h in hits if h[2] == "monster"),
              "by_reds": sum(h[3] for h in hits if h[2] == "red"),
              "red_near": near["d"],
              "still_hurt_s": round(still["s"] + (still["run"] if still["run"] >= STILL_S else 0.0), 1),
              "switches": sum("instead" in ln for ln in logl), "ropes": sum("rope to" in ln for ln in logl),
              "pets_left": len(w.metrics.get("pets_left") or []) if isinstance(w.metrics.get("pets_left"), list)
              else w.metrics.get("pets_left", 0), "by": dict(sorted(by.items(), key=lambda kv: -kv[1])),
              "log": logl, "hits": hits, "reds": near["log"]}
    if tracer is not None:
        tracer.close({k: v for k, v in result.items() if k not in ("log", "hits")})
    return result


if __name__ == "__main__":
    POS = [a for i, a in enumerate(ARGS) if not a.startswith("--")
           and (i == 0 or ARGS[i - 1] not in ("--harness", "--set", "--trace"))]
    SHOW_LOG = "--log" in ARGS
    TRACE = ARGS[ARGS.index("--trace") + 1] if "--trace" in ARGS else None
    rows = []
    for s in seeds(POS[0]) if POS else [1]:
        r = one(s, chase="--chase" in ARGS, trace_dir=TRACE, reds_log=SHOW_LOG)
        log, hl, reds = r.pop("log"), r.pop("hits"), r.pop("reds")
        rows.append(r)
        print(r, flush=True)
        if SHOW_LOG:
            print("    HITS", hl)
            print("    REDS", reds)
            print("\n".join("    " + line[:200] for line in log))
    n = len(rows)
    if n > 1:
        tot = {}
        for r in rows:
            for k, v in r["by"].items():
                tot[k] = tot.get(k, 0) + v
        gates = {}
        for r in rows:
            gates[str(r["gate"])] = gates.get(str(r["gate"]), 0) + 1
        print(f"all {n}{' (' + ' '.join(SETS) + ')' if SETS else ''}: deaths {sum(r['dead'] for r in rows)}, out by "
              f"{gates}, lowest hits median {sorted(r['low'] for r in rows)[n // 2]}, under 30 "
              f"{sum(r['low'] < 30 for r in rows)}, damage on Logan {sum(r['taken'] for r in rows)} (monsters "
              f"{sum(r['by_monsters'] for r in rows)}, reds {sum(r['by_reds'] for r in rows)}) "
              f"{dict(sorted(tot.items(), key=lambda kv: -kv[1]))}, standing hurt by a monster with no red near "
              f"{sum(r['still_hurt_s'] for r in rows):.1f} s, walk s median {sorted(r['walk_s'] for r in rows)[n // 2]}, "
              f"switches {sum(r['switches'] for r in rows)}, ropes {sum(r['ropes'] for r in rows)}")

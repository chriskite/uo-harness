"""Run the hunt runner against the simulator (docs/SIM.md "Running").

    python harness/sim/run.py [--scenario mausoleum] [--seed 1] [--minutes 30] [--runs 1] [--jobs 4]
                              [--log FILE] [--json] [--trace [DIR]] -- <loop_hunt arguments>

The runner is the real `loop_hunt.HuntLoop` (with `--pets`, `tamer.Team`) in this process, its
`time` replaced by the simulator's virtual clock (sim.clock), its Link by `SimLink`, its memory store
a fresh temporary SQLite file. The character starts at home with its pets (the scenario's first tome
row) and recalls in, as a live trip does; `--at-hunt` starts on the hunting level instead (then the run
ends at the first leave). Defaults added to the runner's arguments unless given: `--pets
--leave-recall <the scenario's tome> --enter-recall <it> --enter-rune <the hunting row> --crawl
--timeout <minutes x 60> --no-drop-off`. Several runs (`--runs N`, seeds seed..seed+N-1) go to
a pool of `--jobs` processes; the table (or `--json` lines) lists one summary per run. `--trace` records each run's
trace (sim/trace.py) to DIR (default logs/sim_traces) for the viz Sim page.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time as _wall

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import cpu_budget  # noqa: E402
from sim import trace as simtrace  # noqa: E402

# game time: after the live-server cut (hunt_return.LIVE_SINCE), so the return rule reads a career's own visits as it
# reads the live store's (from 2026-10-12 21:16:30, the trip's start, it read none: "a death every 80 min", its prior,
# whatever the career had seen), and after every live row a --prior store brings (none later than 2026-10-20)
EPOCH = 1792558800.0          # 2026-10-21 00:00 local
RUN_GAP_S = 600.0             # a career's next run starts this long after the last ended (live: median 685 s between
                              # Logan's pets runs, 43 gaps under 3 h, 2026-10-15..20); not under crawl.RESPAWN_S: the
                              # world starts fresh each run, so the runner mustn't think its last kills' areas still rest
# careers when --careers isn't given: the old --jobs default (CPU count - 1), so a batch's draws (each career one
# memory store) stay those of the batches in docs/NOTES.md, whatever --jobs is now
DEFAULT_CAREERS = max(1, (os.cpu_count() or 2) - 1)


def runner_args(sc: dict, extra: list[str], minutes: float, at_hunt: bool = False) -> list[str]:
    tome = f"0x{sc['recall']['tome']:08X}"
    hunt_row = sc["recall"]["rows"][1]["name"]
    have = set(a for a in extra if a.startswith("--"))
    out = list(extra)
    defaults = [("--pets", []), ("--leave-recall", [tome]), ("--timeout", [str(minutes * 60)]),
                ("--no-drop-off", [])]   # no home in the scenario
    rn = sc.get("runner") or {}
    if rn.get("leave_gate"):
        defaults += [("--leave-gate", [str(v) for v in rn["leave_gate"]]), ("--gate-dest", [rn["gate_dest"]])]
    if not at_hunt:
        defaults += [("--enter-recall", [tome]), ("--enter-rune", [hunt_row])]
    for flag, vals in defaults:
        if flag not in have:
            out += [flag, *vals]
    if not at_hunt and "--enter-via" not in have:
        for x, y in rn.get("enter_via") or []:
            out += ["--enter-via", str(x), str(y)]
    if rn.get("heal_in_place") and "--heal-in-place" not in have and "--no-heal-in-place" not in have:
        out.append("--heal-in-place")
    if "--crawl-avoid" not in have and "--no-crawl-avoid" not in have:
        for box in rn.get("crawl_avoid") or []:
            out += ["--crawl-avoid", *map(str, box)]
    out = [a for a in out if a not in ("--no-heal-in-place", "--no-crawl-avoid")]
    if "--crawl" not in have and "--fight-spot" not in have and "--no-crawl" not in have:
        out.append("--crawl")
    return [a for a in out if a != "--no-crawl"]


def seed_prior(db: str, prior: str, serial: int) -> int:
    """Copy the hunt history of character `serial` from the store `prior` (the live one) into `db`: the hunt episodes
    and job events the runner learns from (crawl.load_prior's fights, loot and zone stats, hunt_return.model's visits,
    deaths and leaves, the kills of tamer's spawn spots), rows older than EPOCH only. The rows read; 0 when none."""
    import memory
    import sqlite3
    src = sqlite3.connect(f"file:{prior}?mode=ro", uri=True)
    eps = src.execute("SELECT loop, t_start, t_end, data, char_serial FROM episodes WHERE loop='hunt' "
                      "AND (char_serial=? OR char_serial IS NULL) AND t_start<?", (serial, EPOCH)).fetchall()
    evs = src.execute("SELECT t, job, kind, facet, x, y, data, char_serial FROM job_events WHERE job='hunt' "
                      "AND (char_serial=? OR char_serial IS NULL) AND t<?", (serial, EPOCH)).fetchall()
    src.close()
    store = memory.Memory(db)
    with store.con:
        store.con.executemany("INSERT INTO episodes(loop, t_start, t_end, data, char_serial) VALUES(?,?,?,?,?)", eps)
        store.con.executemany("INSERT INTO job_events(t, job, kind, facet, x, y, data, char_serial) "
                              "VALUES(?,?,?,?,?,?,?,?)", evs)
    store.close()
    return len(eps) + len(evs)


def run_once(seed: int, extra: list[str], scenario: str = "mausoleum", minutes: float = 30.0,
             at_hunt: bool = False, sets: tuple = (), db: str | None = None, others: bool = False,
             eerie: bool = False, epoch: float = EPOCH, prior: str | None = None, reds: bool = False,
             trace: str | None = None, area: tuple | None = None) -> dict:
    """One simulated hunt; the summary dict. The runner starts at home and recalls in, or (at_hunt)
    on the hunting level. `sets`: "module.NAME=value" overrides of runner constants for this run
    (experiments, e.g. "tamer.FALLBACK_S=0"; the value is a Python literal). `db`: the runner's
    memory store (a career's, kept across its runs: crawl priors, fights, avoided types, learnt
    teleporters), else a fresh one (with `prior`'s hunt history: seed_prior). `others`: other players on the
    level (the scenario's `others`, off by default so earlier batches stay comparable); `eerie`: the Field of
    Souls' eerie corpses (the scenario's `eerie`, off by default too); `reds`: red players (the scenario's `reds`, off
    by default). `epoch`: the game time it starts at. `trace`: record the run's trace (sim/trace.py) to this path.
    `area`: the hunting level's box (x0, y0, x1, y1) for the per-hour numbers instead of the scenario's."""
    import ast
    import importlib
    import loop_hunt
    import memory
    import tamer  # noqa: F401 - loaded before the clock is installed
    import crawl  # noqa: F401
    import escape  # noqa: F401
    from agent_link import Abort
    from sim import clock as simclock
    from sim.link import SimLink
    from sim.scenario import load
    from sim.world import World

    for s in sets:
        name, value = s.split("=", 1)
        mod, attr = name.rsplit(".", 1)
        m = importlib.import_module(mod)
        if not hasattr(m, attr):
            raise ValueError(f"--set {s}: {mod} has no {attr}")
        setattr(m, attr, ast.literal_eval(value))
    sc = load(scenario)
    if not at_hunt:
        sc["player"]["start"] = sc["recall"]["rows"][0]["at"]
    for flag, on in (("others", others), ("eerie", eerie), ("reds", reds)):
        if on:
            if flag not in sc:
                raise ValueError(f"--{flag}: scenario {scenario} has no `{flag}` block")
            sc[flag]["enabled"] = True
    if area:
        sc["area"] = list(area)
    if "--vet-range" in extra:
        # the runner's --vet-range is the character's (the Healer's Codex "Without Borders" adds 1/2/3 tiles,
        # knowledge #11343): the simulated supplies reach as far, else the runner stands where they don't
        sc["vet"]["range"] = int(extra[extra.index("--vet-range") + 1])
    clock = simclock.SimClock(epoch=epoch)
    world = World(sc, seed=seed)
    link = SimLink(world, clock)
    tracer, stdout = None, sys.stdout
    if trace:
        tracer = simtrace.Tracer(world, trace, label=scenario, seed=seed,
                                 runner_args=runner_args(sc, extra, minutes, at_hunt), epoch=epoch)
        sys.stdout = tracer.tee(sys.stdout)
    if db is None:
        db = os.path.join(tempfile.mkdtemp(prefix="sim_"), "harness.db")
        if prior:
            seed_prior(db, prior, sc["player"]["serial"])
    args = loop_hunt.arg_parser().parse_args(runner_args(sc, extra, minutes, at_hunt)
                                             + ["--memory", db, "--seed", str(seed)])
    patched = simclock.install(clock)
    store = memory.Memory(db)
    # a career's store holds its earlier runs: this run's rows are those past these ids (junctures too: before, a
    # pet left behind in a career's run counted again in each later run of it, the batch's `runs_with_pet_left_behind`)
    first_ep, first_ev, first_j = (store.con.execute(f"SELECT COALESCE(MAX(id), 0) FROM {t}").fetchone()[0]
                                   for t in ("episodes", "job_events", "junctures"))
    wall0 = _wall.perf_counter()
    outcome, error = "done", None
    loop = None
    try:
        loop = loop_hunt.HuntLoop(link, store, args)
        world.engaged_fn = lambda: loop.engaged
        loop.run()
    except Abort as e:
        outcome, error = "aborted", str(e)
    except Exception as e:  # noqa: BLE001 - a crash is a result too
        import traceback
        outcome, error = "crashed", f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
    finally:
        if loop is not None:
            try:
                loop.end_visit("stopped")
            except Exception:  # noqa: BLE001
                pass
        simclock.uninstall(patched)
        sys.stdout = stdout
    wall = _wall.perf_counter() - wall0
    juncs = [{"kind": j["kind"], "severity": j["severity"], "summary": j["summary"][:160]}
             for j in store.junctures(after_id=first_j, limit=200)]
    episodes = store.episodes("hunt")
    visits = [json.loads(d) for (d,) in store.con.execute(
        "SELECT data FROM episodes WHERE loop='hunt' AND id>? ORDER BY id", (first_ep,))]
    records = [{"kind": k, "x": x, "y": y, **json.loads(d)} for k, x, y, d in store.con.execute(
        "SELECT kind, x, y, data FROM job_events WHERE job='hunt' AND kind IN ('fight', 'death', 'leave') AND id>? "
        "ORDER BY id", (first_ev,))]
    store.close()
    m = world.metrics
    pl = world.player
    r = {"seed": seed, "outcome": outcome, "error": error, "sim_s": round(clock.elapsed, 1),
            "wall_s": round(wall, 2), "speed_x": round(clock.elapsed / wall, 1) if wall else None,
            "kills": m["kills"], "kills_by": m["kills_by"], "gold_looted": m["gold_looted"],
            # banked: what reached home (World._land); lost: what a death left on the corpse
            "gold_banked": m["gold_banked"], "gold_lost": m["gold_looted"] - m["gold_banked"] if m["deaths"] else 0,
            "hunt_s": round(m["hunt_s"], 1), "place_s": {k: round(v, 1) for k, v in m.get("place_s", {}).items()},
            "kills_per_min": round(m["kills"] / max(clock.elapsed / 60, 1e-9), 2),
            "player_damage_taken": round(m["player_damage_taken"]), "player_hits_end": int(pl.hits),
            "deaths": m["deaths"], "pet_deaths": m["pet_deaths"],
            "pets_end": {p.name: p.pct() for p in world.pets()},
            "pet_damage_taken": m["pet_damage_taken"], "player_damage_by": m.get("player_damage_by", {}),
            "pet_damage_by": m.get("pet_damage_by", {}),
            "vet_uses": m["vet_uses"], "potions": m["potions"],
            "bard": m["bard"], "guard_commands": m["guard_commands"], "kill_commands": m["kill_commands"],
            "patrol_commands": m["patrol_commands"],
            "follow_commands": m["follow_commands"], "aggressive_acts": m["aggressive_acts"],
            "recalls": m["recalls"], "steps": m["steps"], "denied_steps": m["denied_steps"],
            "shoves": m["shoves"], "shoves_by": m.get("shoves_by", {}), "refresh_potions": m["refresh_potions"],
            "stam_below_s": round(m["stam_below_s"], 1),
            "death_causes": m.get("death_causes", []),
            "others": {k: m[k] for k in ("others_arrived", "others_kills", "others_kills_in_view", "others_kills_unseen",
                                         "others_view_s",
                                         "others_dwell",
                                         "kill_on_others", "blue_corpse_opens", "wait_on_others_s",
                                         "wait_on_others_mobs") if k in m} if world.others_on else None,
            "eerie": {k: m.get(k, 0) for k in ("eerie_spawned", "eerie_seen", "soul_jars")} if world.eerie_on else None,
            "reds": world.reds.summary() if world.reds.on else None,
            # Tidal Tomb's: seconds under water, oxygen deprivation damage by victim, broodlings by type
            "oxygen": {"underwater_s": round(m.get("underwater_s", 0.0), 1), "damage": m.get("oxygen_damage", {})}
            if world.sc.get("oxygen") else None,
            "broodlings": m.get("broodlings", {}),
            "met": m.get("met", {}),
            "violations": m["violations"], "junctures": juncs,
            "runner_totals": dict(loop.totals) if loop is not None else None,
            "episodes": len(episodes),
            # the runner's own records, as the live store keeps them (sim/parity.py compares the two)
            "visits": visits, "fights": [{k: r.get(k) for k in ("name", "fight_s", "hits_lost", "outcome")}
                                         for r in records if r["kind"] == "fight"],
            "death_events": [{k: r.get(k) for k in ("x", "y", "cause")} for r in records if r["kind"] == "death"],
            "leave_events": [{k: r.get(k) for k in ("x", "y", "why", "hits")} | {"died": "died" in str(r.get("gate"))}
                             for r in records if r["kind"] == "leave"]}
    if tracer is not None:
        tracer.close({k: r[k] for k in ("outcome", "kills", "deaths", "player_damage_taken")}
                     | {"pet_deaths": len(r["pet_deaths"])})
        print(f"trace: {trace}", file=sys.stderr)
    return r


def _pool_run(job):
    """One career in a pool worker: its runs one after another on one memory store (each run's
    log to its `log`, or nowhere), each starting RUN_GAP_S of game time after the last ended, as live (the
    store's times read like the live one's: recent kills, the return rule's visits); their summaries, with
    `career` and `career_run`. With a `prior` store the career's store starts with its hunt history."""
    career, runs = job
    db = os.path.join(tempfile.mkdtemp(prefix="sim_career_"), "harness.db")
    out_all = []
    epoch = EPOCH
    for k, (seed, extra, scenario, minutes, log, at_hunt, sets, others, eerie, prior, reds, trace, area) in enumerate(runs):
        if career is not None and k == 0 and prior:
            from sim.scenario import load
            seed_prior(db, prior, load(scenario)["player"]["serial"])
        out = open(log, "w", encoding="utf-8") if log else open(os.devnull, "w")
        old = sys.stdout
        sys.stdout = out
        try:
            r = run_once(seed, extra, scenario, minutes, at_hunt, sets, db=db if career is not None else None,
                         others=others, eerie=eerie, epoch=epoch if career is not None else EPOCH,
                         prior=prior if career is None else None, reds=reds, trace=trace, area=area)
        finally:
            sys.stdout = old
            out.close()
        epoch += r.get("sim_s", 0.0) + RUN_GAP_S
        out_all.append({**r, "career": career, "career_run": k})
    return out_all


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    extra = []
    if "--" in argv:
        i = argv.index("--")
        argv, extra = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scenario", default="mausoleum")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--jobs", type=int, default=max(1, cpu_budget.workers() - 1),
                    help="worker processes (default: CPU count - 3: two logical CPUs left to the game, cpu_budget)")
    ap.add_argument("--minutes", type=float, default=30.0, help="the runner's --timeout, in game minutes")
    ap.add_argument("--at-hunt", action="store_true", help="start on the hunting level (no recall in)")
    ap.add_argument("--log", default=None, help="the runner's log (one run), or a directory (several)")
    ap.add_argument("--json", action="store_true", help="print the summaries as JSON lines")
    ap.add_argument("--compare", action="store_true",
                    help="also print the runs per hour on the hunting level beside the live trip's (scenario `trip`)")
    ap.add_argument("--set", action="append", default=[], metavar="MODULE.NAME=VALUE",
                    help="override a runner constant for the runs (an experiment), e.g. tamer.FALLBACK_S=0")
    ap.add_argument("--careers", type=int, default=0,
                    help="split the runs into this many careers (default: CPU count - 1), each one after another on "
                         "one memory store, as the live runner keeps its memory between runs")
    ap.add_argument("--fresh", action="store_true", help="every run on a fresh memory store (no careers)")
    ap.add_argument("--others", action="store_true",
                    help="other players on the level (the scenario's `others`; off by default, docs/SIM.md)")
    ap.add_argument("--eerie", action="store_true",
                    help="the Field of Souls' eerie corpses on the level, empty soul jars in the pack (the scenario's "
                         "`eerie`; off by default)")
    ap.add_argument("--reds", action="store_true",
                    help="red players (player killers): the scenario's `reds`, off by default (docs/SIM.md 'Red players')")
    ap.add_argument("--death-downtime-min", type=float, default=None,
                    help="game minutes a death costs beyond the run (resurrection, resupply, the restart) in "
                         "banked_per_h_net; default: the scenario's reds.death_downtime_s")
    ap.add_argument("--prior", default=None, metavar="DB",
                    help="start every career's memory with this store's hunt history for the scenario's character (the "
                         "live store: the crawl's zone knowledge, the return rule's visits; docs/SIM.md 'Parity')")
    ap.add_argument("--trace", nargs="?", const=simtrace.DEFAULT_DIR, default=None, metavar="DIR",
                    help="record each run's trace for the viz Sim page (sim/trace.py) to DIR (default logs/sim_traces)")
    ap.add_argument("--area", type=int, nargs=4, default=None, metavar=("X0", "Y0", "X1", "Y1"),
                    help="the hunting level's box the per-hour numbers count on (default the scenario's `area`; Tidal "
                         "Tomb's level 2: 6815 1907 6969 2100)")
    a = ap.parse_args(argv)
    if a.runs > 1:
        cpu_budget.limit()
    seeds = list(range(a.seed, a.seed + a.runs))
    logdir = a.log if a.runs > 1 else None
    if logdir:
        os.makedirs(logdir, exist_ok=True)
    st = simtrace.stamp()
    jobs = [(s, extra, a.scenario, a.minutes, os.path.join(logdir, f"run_{s}.log") if logdir else a.log, a.at_hunt,
             tuple(a.set), a.others, a.eerie, a.prior, a.reds,
             simtrace.path_for(a.trace, st, a.scenario, s) if a.trace else None, tuple(a.area) if a.area else None)
            for s in seeds]
    n = len(jobs) if a.fresh else max(1, min(a.careers or DEFAULT_CAREERS, len(jobs)))
    careers = [(None if a.fresh else c, jobs[c::n]) for c in range(n)]
    if len(careers) == 1 or a.jobs <= 1:
        results = [r for c in careers for r in _pool_run(c)]
    else:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=min(a.jobs, len(careers))) as ex:
            results = [r for rs in ex.map(_pool_run, careers) for r in rs]
    results.sort(key=lambda r: r["seed"])
    if a.json:
        for r in results:
            print(json.dumps(r))
        return 0
    print(f"{'seed':>4} {'outcome':>8} {'game min':>8} {'x real':>7} {'kills':>5} {'k/min':>5} {'gold':>6} "
          f"{'banked':>6} {'dmg us':>6} {'deaths':>6} {'pet deaths':>10} {'vet':>4} {'pots':>4} {'viol':>4}")
    for r in results:
        if "sim_s" not in r:
            print(f"{r['seed']:>4} {r['outcome']}")
            continue
        print(f"{r['seed']:>4} {r['outcome']:>8} {r['sim_s'] / 60:>8.1f} {r['speed_x'] or 0:>7.1f} {r['kills']:>5} "
              f"{r['kills_per_min']:>5} {r['gold_looted']:>6} {r['gold_banked']:>6} {r['player_damage_taken']:>6} "
              f"{r['deaths']:>6} {len(r['pet_deaths']):>10} {r['vet_uses']:>4} {r['potions']:>4} {len(r['violations']):>4}")
        if r.get("error"):
            print("      " + r["error"].splitlines()[0][:150])
    from sim.scenario import load
    downtime = a.death_downtime_min * 60 if a.death_downtime_min is not None \
        else (load(a.scenario).get("reds") or {}).get("death_downtime_s", 0.0)
    print("all: " + json.dumps(aggregate(results, downtime)))
    if a.compare:
        print(compare(results, load_trip(a.scenario)))
    return 0


def aggregate(results: list[dict], downtime_s: float = 0.0) -> dict:
    """Across runs: game hours, kills per game hour, deaths per game hour, the share of runs with a death, a pet
    death, a pet left behind, a crash or a rule violation; gold looted and gold banked (what reached home) per game
    hour, and banked per hour with `downtime_s` of game time added for each death (banked_per_h_net)."""
    ok = [r for r in results if "sim_s" in r]
    hours = sum(r["sim_s"] for r in ok) / 3600 or 1e-9
    deaths = sum(r["deaths"] for r in ok)
    left = sum(1 for r in ok if any(j["kind"] == "pet_left_behind" for j in r["junctures"]))
    banked = sum(r.get("gold_banked", 0) for r in ok)
    return {"runs": len(results), "game_h": round(hours, 2), "kills_per_h": round(sum(r["kills"] for r in ok) / hours, 1),
            "gold_per_h": round(sum(r["gold_looted"] for r in ok) / hours),
            "banked_per_h": round(banked / hours),
            "banked_per_h_net": round(banked / (hours + deaths * downtime_s / 3600)),
            "gold_lost": sum(r.get("gold_lost", 0) for r in ok),
            # the scenario's `places`: their share of our time alive on the level
            "place_share": {k: round(sum((r.get("place_s") or {}).get(k, 0) for r in ok)
                                     / (sum(r["hunt_s"] for r in ok) or 1e-9), 3)
                            for k in sorted({k for r in ok for k in (r.get("place_s") or {})})},
            "deaths_per_h": round(deaths / hours, 2),
            "runs_with_death": sum(1 for r in ok if r["deaths"]),
            "runs_with_pet_death": sum(1 for r in ok if r["pet_deaths"]),
            "runs_with_pet_left_behind": left,
            "crashed": sum(1 for r in results if r.get("outcome") == "crashed"),
            "violations": sum(len(r["violations"]) for r in ok),
            **({"others": others_summary(ok)} if any(r.get("others") for r in ok) else {}),
            **({"reds": reds_summary(ok)} if any(r.get("reds") for r in ok) else {})}


def _median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else None


def red_deaths(r: dict) -> list:
    """A run's deaths with a red's damage in their last 15 s (World.die's death_causes)."""
    return [d for d in r.get("death_causes", []) if d["last_15s"].get("red player")]


EAST_WING = (5224, 655, 5247, 737)      # Maus 3's dead-end wing (docs/NOTES.md "Dead in the east wing's dead end")


def in_box(p, b) -> bool:
    return b[0] <= p[0] <= b[2] and b[1] <= p[1] <= b[3]


def reds_summary(ok: list[dict]) -> dict:
    """Red players across runs, as docs/NOTES.md "Red players met live" measured them: group sightings an hour on the
    fight level and an hour of walk-in, their sizes, attack lines (seconds after first sight, distance), red deaths an
    hour on the level, the share of sightings that ended in our death and first sight to death."""
    rs = [(r, r["reds"]) for r in ok if r.get("reds")]
    level_h = sum(x["level_s"] for _, x in rs) / 3600 or 1e-9
    walk_h = sum(x["walkin_s"] for _, x in rs) / 3600 or 1e-9
    hunt = ("maus3",)
    sights = [(r, sg) for r, x in rs for sg in x["sightings"]]
    lv = [(r, sg) for r, sg in sights if sg["level"] in hunt]
    wk = [(r, sg) for r, sg in sights if sg["level"] not in hunt and sg["level"] is not None]
    apps = [a for _, x in rs for a in x["appearances"] if a["level"] in hunt]
    att = [a for a in apps if a["attack_s"] is not None]
    sizes = {}
    for _, sg in lv:
        sizes[sg["seen"]] = sizes.get(sg["seen"], 0) + 1
    to_death, fatal = [], 0
    deaths_level = deaths_walk = 0
    split = {"between": [0, 0], "not_between": [0, 0], "east_wing": [0, 0], "elsewhere": [0, 0]}   # [sightings, fatal]
    for r, x in rs:
        for d in red_deaths(r):
            before = [sg for sg in x["sightings"] if sg["t"] <= d["t"]]
            if before:
                sg = max(before, key=lambda s: s["t"])
                to_death.append(round(d["t"] - sg["t"], 1))
                if sg["level"] in hunt:
                    deaths_level += 1
                else:
                    deaths_walk += 1
        for sg in x["sightings"]:
            if sg["level"] not in hunt:
                continue
            dead = any(0 <= d["t"] - sg["t"] <= 60 for d in red_deaths(r))
            fatal += dead
            keys = ["east_wing" if in_box(sg["at"], EAST_WING) else "elsewhere"]
            if sg.get("between") is not None:
                keys.append("between" if sg["between"] else "not_between")
            for k in keys:
                split[k][0] += 1
                split[k][1] += dead
    n_app = len(apps) or 1
    return {"level_h": round(level_h, 2), "walkin_h": round(walk_h, 2),
            "groups_in_view_per_h": round(len(lv) / level_h, 2), "walkin_groups_per_h": round(len(wk) / walk_h, 2),
            "group_sizes": dict(sorted(sizes.items())),
            "appearances": len(apps), "attacked_share": round(len(att) / n_app, 2),
            "attack_s_median": _median([a["attack_s"] for a in att]),
            "attack_d_median": _median([a["attack_d"] for a in att]),
            "red_deaths": deaths_level + deaths_walk, "red_deaths_per_level_h": round(deaths_level / level_h, 2),
            "walkin_red_deaths": deaths_walk,
            "fatal_share": round(fatal / (len(lv) or 1), 2), "sight_to_death_median": _median(to_death),
            # [sightings, fatal]: a red nearer the leave gate than we were at first sight (live 12 / 7 against 5 / 0);
            # Logan in the east wing (x 5224-5247, y 655-737) at first sight (live 7 / 4)
            "fatal_by_place": split,
            # the share of sightings within 2.6 min of our arrival on the level (live 9 of 24), and of a group met
            # before in the run (live ~2-3 of 20)
            "early_share": round(sum(1 for _, sg in lv if sg.get("since_arrival") is not None
                                     and sg["since_arrival"] <= 156) / (len(lv) or 1), 2),
            "repeat_share": round(sum(sum(1 for i, sg in enumerate(gs) if sg["group"] in {g["group"] for g in gs[:i]})
                                      for gs in ([sg for sg in sorted(x["sightings"], key=lambda s: s["t"])
                                                  if sg["level"] in hunt] for _, x in rs)) / (len(lv) or 1), 2),
            "jumps_per_appearance": round(sum(x["jumps"] for _, x in rs) / n_app, 2),
            # gross damage a second on us from its attack line while in view (live: one caster 12-19, melee 6-7)
            "dps_by_kit": {k: round(sum(a.get("dmg", 0) for a in att if a["kit"] == k)
                                    / (sum(a.get("fight_s", 0) for a in att if a["kit"] == k) or 1e-9), 1)
                           for k in sorted({a["kit"] for a in att})},
            "hamstrings": sum(x["hamstrings"] for _, x in rs), "paralyzes": sum(x["paralyzes"] for _, x in rs),
            "potions_stuck": sum(x["potions_stuck"] for _, x in rs),
            "disturbed_recalls": sum(x["disturbed_recalls"] for _, x in rs),
            "monster_on_red_s_per_h": round(sum(x["monster_on_red_s"] for _, x in rs) / level_h),
            "groups_arrived": sum(x["groups_arrived"] for _, x in rs),
            "gold_lost_to_reds": sum(r.get("gold_lost", 0) for r, _ in rs if red_deaths(r))}


def others_summary(ok: list[dict]) -> dict:
    """Other players across runs: in our view on the level (the share of seconds with one or more, the mean count,
    in-view arrivals an hour and stays: the capture's numbers in docs/NOTES.md), their kills, and the runner's
    dealings with them ("All Kill" at a monster on one, opens of their corpses, seconds engaged on a monster
    fighting one before any of ours hurt it, the runner's own kills lost to their loot rights)."""
    rs = [r["others"] for r in ok if r.get("others")]
    view = {}
    for o in rs:
        for n, s in o["others_view_s"].items():
            view[int(n)] = view.get(int(n), 0) + s
    secs = sum(view.values()) or 1e-9
    dwell = sorted(d for o in rs for d in o["others_dwell"])
    return {"view_any": round(sum(s for n, s in view.items() if n) / secs, 3),
            "view_mean": round(sum(n * s for n, s in view.items()) / secs, 2),
            "arrivals_in_view_per_h": round(len(dwell) / (secs / 3600), 1),
            "dwell_q": [dwell[int(p * (len(dwell) - 1))] for p in (0.25, 0.5, 0.75, 0.9)] if dwell else [],
            "arrived": sum(o["others_arrived"] for o in rs), "their_kills": sum(o["others_kills"] for o in rs),
            "their_kills_in_view_per_h": round(sum(o.get("others_kills_in_view", 0) for o in rs) / (secs / 3600), 1),
            "kill_on_others": sum(o["kill_on_others"] for o in rs),
            "blue_corpse_opens": sum(o["blue_corpse_opens"] for o in rs),
            "wait_on_others_s": round(sum(o["wait_on_others_s"] for o in rs)),
            "wait_on_others_mobs": sum(o["wait_on_others_mobs"] for o in rs),
            "lost_kills": sum((r.get("runner_totals") or {}).get("lost_kills", 0) for r in ok)}


def load_trip(scenario: str) -> dict:
    from sim.scenario import load
    return load(scenario).get("trip") or {}


def compare(results: list[dict], trip: dict) -> str:
    """A table: per hour on the hunting level, the runs' totals beside the live trip's."""
    ok = [r for r in results if "sim_s" in r]
    h = sum(r["hunt_s"] for r in ok) / 3600 or 1e-9
    th = trip.get("hunt_s", 0) / 3600 or 1e-9

    def tot(key, sub=None):
        return sum((r[key].get(sub, 0) if sub else r[key]) for r in ok)
    rows = [("kills", tot("kills") / h, trip.get("kills", 0) / th),
            ("damage to us", tot("player_damage_taken") / h, trip.get("player_damage", 0) / th),
            ("vet supplies", tot("vet_uses") / h, trip.get("vet_uses", 0) / th),
            ("heal potions", tot("potions") / h, trip.get("potions", 0) / th),
            ("provocations", (tot("bard", "provoke_ok") + tot("bard", "provoke_fail")) / h, trip.get("provoke", 0) / th)]
    share = [("discord success", tot("bard", "discord_ok"), tot("bard", "discord_fail"),
              trip.get("discord_ok", 0), trip.get("discord_fail", 0)),
             ("peace success", tot("bard", "peace_ok"), tot("bard", "peace_fail"),
              trip.get("peace_ok", 0), trip.get("peace_fail", 0))]
    gb = tot("pet_damage_taken", "GraveRobber")
    ph = tot("pet_damage_taken", "AlaFlambe")
    out = [f"per hour on the level: sim {h:.2f} h over {len(ok)} runs, trip {th * 60:.1f} min",
           f"{'':>16} {'sim':>8} {'trip':>8}"]
    out += [f"{name:>16} {s:>8.1f} {t:>8.1f}" for name, s, t in rows]
    out += [f"{name:>16} {ok_ / max(1, ok_ + f):>8.2f} {tok / max(1, tok + tf):>8.2f}" for name, ok_, f, tok, tf in share]
    out.append(f"{'gravebug:phoenix':>16} {gb / max(1, ph):>8.1f} "
               f"{trip.get('gravebug_per_war_min', 0) / max(1, trip.get('phoenix_per_war_min', 1)):>8.1f}"
               "  (damage taken; the trip's per war-minute)")
    return "\n".join(out)


if __name__ == "__main__":
    sys.exit(main())

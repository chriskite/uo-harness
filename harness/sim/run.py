"""Run the hunt runner against the simulator (docs/SIM.md "Running").

    python harness/sim/run.py [--scenario mausoleum] [--seed 1] [--minutes 30] [--runs 1] [--jobs 4]
                              [--log FILE] [--json] -- <loop_hunt arguments>

The runner is the real `loop_hunt.HuntLoop` (with `--pets`, `tamer.Team`) in this process, its
`time` replaced by the simulator's virtual clock (sim.clock), its Link by `SimLink`, its memory store
a fresh temporary SQLite file. The character starts at home with its pets (the scenario's first tome
row) and recalls in, as a live trip does; `--at-hunt` starts on the hunting level instead (then the run
ends at the first leave). Defaults added to the runner's arguments unless given: `--pets
--leave-recall <the scenario's tome> --enter-recall <it> --enter-rune <the hunting row> --crawl
--timeout <minutes x 60>`. Several runs (`--runs N`, seeds seed..seed+N-1) go to
a pool of `--jobs` processes; the table (or `--json` lines) lists one summary per run.
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

EPOCH = 1791857790.0          # 2026-10-12 21:16:30 local: the trip's start (log lines read as game time)


def runner_args(sc: dict, extra: list[str], minutes: float, at_hunt: bool = False) -> list[str]:
    tome = f"0x{sc['recall']['tome']:08X}"
    hunt_row = sc["recall"]["rows"][1]["name"]
    have = set(a for a in extra if a.startswith("--"))
    out = list(extra)
    defaults = [("--pets", []), ("--leave-recall", [tome]), ("--timeout", [str(minutes * 60)])]
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


def run_once(seed: int, extra: list[str], scenario: str = "mausoleum", minutes: float = 30.0,
             at_hunt: bool = False, sets: tuple = (), db: str | None = None) -> dict:
    """One simulated hunt; the summary dict. The runner starts at home and recalls in, or (at_hunt)
    on the hunting level. `sets`: "module.NAME=value" overrides of runner constants for this run
    (experiments, e.g. "tamer.FALLBACK_S=0"; the value is a Python literal). `db`: the runner's
    memory store (a career's, kept across its runs: crawl priors, fights, avoided types, learnt
    teleporters), else a fresh one."""
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
    if "--vet-range" in extra:
        # the runner's --vet-range is the character's (the Healer's Codex "Without Borders" adds 1/2/3 tiles,
        # knowledge #11343): the simulated supplies reach as far, else the runner stands where they don't
        sc["vet"]["range"] = int(extra[extra.index("--vet-range") + 1])
    clock = simclock.SimClock(epoch=EPOCH)
    world = World(sc, seed=seed)
    link = SimLink(world, clock)
    if db is None:
        db = os.path.join(tempfile.mkdtemp(prefix="sim_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(runner_args(sc, extra, minutes, at_hunt)
                                             + ["--memory", db, "--seed", str(seed)])
    patched = simclock.install(clock)
    store = memory.Memory(db)
    wall0 = _wall.perf_counter()
    outcome, error = "done", None
    loop = None
    try:
        loop = loop_hunt.HuntLoop(link, store, args)
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
    wall = _wall.perf_counter() - wall0
    juncs = [{"kind": j["kind"], "severity": j["severity"], "summary": j["summary"][:160]}
             for j in store.junctures(limit=200)]
    episodes = store.episodes("hunt")
    store.close()
    m = world.metrics
    pl = world.player
    return {"seed": seed, "outcome": outcome, "error": error, "sim_s": round(clock.elapsed, 1),
            "wall_s": round(wall, 2), "speed_x": round(clock.elapsed / wall, 1) if wall else None,
            "kills": m["kills"], "kills_by": m["kills_by"], "gold_looted": m["gold_looted"],
            "hunt_s": round(m["hunt_s"], 1),
            "kills_per_min": round(m["kills"] / max(clock.elapsed / 60, 1e-9), 2),
            "player_damage_taken": round(m["player_damage_taken"]), "player_hits_end": int(pl.hits),
            "deaths": m["deaths"], "pet_deaths": m["pet_deaths"],
            "pets_end": {p.name: p.pct() for p in world.pets()},
            "pet_damage_taken": m["pet_damage_taken"], "player_damage_by": m.get("player_damage_by", {}),
            "pet_damage_by": m.get("pet_damage_by", {}),
            "vet_uses": m["vet_uses"], "potions": m["potions"],
            "bard": m["bard"], "guard_commands": m["guard_commands"], "kill_commands": m["kill_commands"],
            "follow_commands": m["follow_commands"], "aggressive_acts": m["aggressive_acts"],
            "recalls": m["recalls"], "steps": m["steps"], "denied_steps": m["denied_steps"],
            "death_causes": m.get("death_causes", []),
            "violations": m["violations"], "junctures": juncs,
            "runner_totals": dict(loop.totals) if loop is not None else None,
            "episodes": len(episodes)}


def _pool_run(job):
    """One career in a pool worker: its runs one after another on one memory store (each run's
    log to its `log`, or nowhere); their summaries, with `career` and `career_run`."""
    career, runs = job
    db = os.path.join(tempfile.mkdtemp(prefix="sim_career_"), "harness.db")
    out_all = []
    for k, (seed, extra, scenario, minutes, log, at_hunt, sets) in enumerate(runs):
        out = open(log, "w", encoding="utf-8") if log else open(os.devnull, "w")
        old = sys.stdout
        sys.stdout = out
        try:
            r = run_once(seed, extra, scenario, minutes, at_hunt, sets, db=db if career is not None else None)
        finally:
            sys.stdout = old
            out.close()
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
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--minutes", type=float, default=30.0, help="the runner's --timeout, in game minutes")
    ap.add_argument("--at-hunt", action="store_true", help="start on the hunting level (no recall in)")
    ap.add_argument("--log", default=None, help="the runner's log (one run), or a directory (several)")
    ap.add_argument("--json", action="store_true", help="print the summaries as JSON lines")
    ap.add_argument("--compare", action="store_true",
                    help="also print the runs per hour on the hunting level beside the live trip's (scenario `trip`)")
    ap.add_argument("--set", action="append", default=[], metavar="MODULE.NAME=VALUE",
                    help="override a runner constant for the runs (an experiment), e.g. tamer.FALLBACK_S=0")
    ap.add_argument("--careers", type=int, default=0,
                    help="split the runs into this many careers (default: --jobs), each one after another on "
                         "one memory store, as the live runner keeps its memory between runs")
    ap.add_argument("--fresh", action="store_true", help="every run on a fresh memory store (no careers)")
    a = ap.parse_args(argv)
    seeds = list(range(a.seed, a.seed + a.runs))
    logdir = a.log if a.runs > 1 else None
    if logdir:
        os.makedirs(logdir, exist_ok=True)
    jobs = [(s, extra, a.scenario, a.minutes, os.path.join(logdir, f"run_{s}.log") if logdir else a.log, a.at_hunt,
             tuple(a.set)) for s in seeds]
    n = len(jobs) if a.fresh else max(1, min(a.careers or a.jobs, len(jobs)))
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
          f"{'dmg us':>6} {'deaths':>6} {'pet deaths':>10} {'vet':>4} {'pots':>4} {'viol':>4}")
    for r in results:
        if "sim_s" not in r:
            print(f"{r['seed']:>4} {r['outcome']}")
            continue
        print(f"{r['seed']:>4} {r['outcome']:>8} {r['sim_s'] / 60:>8.1f} {r['speed_x'] or 0:>7.1f} {r['kills']:>5} "
              f"{r['kills_per_min']:>5} {r['gold_looted']:>6} {r['player_damage_taken']:>6} {r['deaths']:>6} "
              f"{len(r['pet_deaths']):>10} {r['vet_uses']:>4} {r['potions']:>4} {len(r['violations']):>4}")
        if r.get("error"):
            print("      " + r["error"].splitlines()[0][:150])
    print("all: " + json.dumps(aggregate(results)))
    if a.compare:
        print(compare(results, load_trip(a.scenario)))
    return 0


def aggregate(results: list[dict]) -> dict:
    """Across runs: game hours, kills per game hour, deaths per game hour, the share of runs with a
    death, a pet death, a pet left behind, a crash or a rule violation."""
    ok = [r for r in results if "sim_s" in r]
    hours = sum(r["sim_s"] for r in ok) / 3600 or 1e-9
    left = sum(1 for r in ok if any(j["kind"] == "pet_left_behind" for j in r["junctures"]))
    return {"runs": len(results), "game_h": round(hours, 2), "kills_per_h": round(sum(r["kills"] for r in ok) / hours, 1),
            "gold_per_h": round(sum(r["gold_looted"] for r in ok) / hours),
            "deaths_per_h": round(sum(r["deaths"] for r in ok) / hours, 2),
            "runs_with_death": sum(1 for r in ok if r["deaths"]),
            "runs_with_pet_death": sum(1 for r in ok if r["pet_deaths"]),
            "runs_with_pet_left_behind": left,
            "crashed": sum(1 for r in results if r.get("outcome") == "crashed"),
            "violations": sum(len(r["violations"]) for r in ok)}


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

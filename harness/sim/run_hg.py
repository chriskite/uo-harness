"""Run the Hunting Grounds runner (loop_hg.HGLoop) against the simulator (docs/SIM.md "Hunting Grounds simulator").

    python harness/sim/run_hg.py [--runs N] [--seed S] [--permits P] [--jobs J] [--followers] [--log PATH] [--json]
                                 -- <loop_hg arguments>

The runner is the real `loop_hg.HGLoop` in this process, its `time` replaced by the simulator's virtual clock
(sim.clock), its Link by `SimLink` over `sim.hgworld.HGWorld`, its memory store a fresh temporary SQLite file. The
character starts by the Prevalia bank with `--permits` Hunting Permits and plays until the runner returns (no permit
left, `--max-runs`) or raises: `agent_link.Abort` ends the run ("aborted"), any other exception is a crash. Added to
the runner's arguments unless given: `--human off --seed <seed> --memory <temp db>`. Several runs (`--runs N`, seeds
seed..seed+N-1) go to a pool of `--jobs` processes; each prints its sessions' scores, then `all:` the aggregate (mean
score per permit, trophies, points per trophy, points lost, shots, kill rate).
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

EPOCH = 1795384800.0          # 2026-11-22 16:00 local: inside the event week
SIM_CAP_S = 600.0             # game time beyond PERMIT_CAP_S a permit after which a run is cut ("timeout")
PERMIT_CAP_S = 1500.0         # a permit's session (900 s) plus the walk back to the official, generously


class SimTimeout(BaseException):
    """The run's game time ran past its cap (a runner that never returns); BaseException so the runner's own
    `except Exception` can't swallow it."""


def _runner_args(ap: argparse.ArgumentParser, extra: list[str], seed: int, db: str) -> list[str]:
    have = {a.split("=", 1)[0] for a in extra if a.startswith("--")}
    out = list(extra)
    for flag, val in (("--human", "off"), ("--seed", str(seed)), ("--memory", db)):
        if flag not in have and flag in ap._option_string_actions:
            out += [flag, val]
    return out


def run_once(seed: int, permits: int, extra_args: list[str], followers: bool = False, epoch: float = EPOCH,
             log: str | None = None) -> dict:
    """One simulated career of `permits` Hunting Grounds runs; its summary dict (world.metrics and the run's
    outcome). `log`: the runner's output to this file (else nowhere)."""
    import loop_hg
    import memory
    from agent_link import Abort
    from sim import clock as simclock
    from sim.hgscenario import load
    from sim.hgworld import HGWorld
    from sim.link import SimLink

    world = HGWorld(load(), seed=seed, permits=permits, followers=followers)
    clock = simclock.SimClock(epoch=epoch)
    link = SimLink(world, clock)
    cap = SIM_CAP_S + PERMIT_CAP_S * permits

    def advance(t):
        world.run_until(t)
        if t > cap:
            raise SimTimeout(f"past {cap:.0f} s of game time")
    clock.on_advance = advance
    db = os.path.join(tempfile.mkdtemp(prefix="sim_hg_"), "harness.db")
    ap = loop_hg.arg_parser()
    args = ap.parse_args(_runner_args(ap, extra_args, seed, db))
    out = open(log, "w", encoding="utf-8") if log else open(os.devnull, "w")
    stdout, sys.stdout = sys.stdout, out
    patched = simclock.install(clock)
    store = memory.Memory(db)
    wall0 = _wall.perf_counter()
    outcome, error = "done", None
    try:
        loop_hg.HGLoop(link, store, args).run()
    except Abort as e:
        outcome, error = "aborted", str(e)
    except SimTimeout as e:
        outcome, error = "timeout", str(e)
    except Exception as e:  # noqa: BLE001 - a crash is a result too
        import traceback
        outcome, error = "crashed", f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
    finally:
        wall = _wall.perf_counter() - wall0
        simclock.uninstall(patched)
        sys.stdout = stdout
        out.close()
        store.close()
    return summary(world, seed, permits, outcome, error, clock.elapsed, wall)


def summary(world, seed: int, permits: int, outcome: str, error: str | None, sim_s: float, wall: float) -> dict:
    m = world.metrics
    scores = list(m["score"])
    shots = m["shots"]
    return {"seed": seed, "permits": permits, "outcome": outcome, "error": error, "sim_s": round(sim_s, 1),
            "wall_s": round(wall, 2), "speed_x": round(sim_s / wall, 1) if wall else None,
            "wall_s_per_session": round(wall / len(scores), 2) if scores else None,
            "runs": m["runs"], "scores": scores, "score": sum(scores),
            "score_per_permit": round(sum(scores) / len(scores), 1) if scores else 0.0,
            "trophies": m["trophies"], "trophy_points": sum(m["trophy_points"]),
            "points_per_trophy": round(sum(m["trophy_points"]) / m["trophies"], 2) if m["trophies"] else None,
            "lost_at_expiry": m["points_lost_at_expiry"], "lost_at_gate": m["points_lost_at_gate"],
            "shots": shots, "kills": m["kills"], "grazes": m["grazes"], "misses": m["misses"],
            "kill_rate": round(m["kills"] / shots, 3) if shots else None,
            "shots_by_mood": m["shots_by_mood"], "outcomes_by_mood": m["outcomes_by_mood"],
            "refusals": m["refusals"], "permits_left": m["permits_left"], "steps": m["steps"],
            "run_steps": m["run_steps"], "denied_steps": m["denied_steps"], "fast_steps": m["fast_steps"],
            "hides": m["hides"], "carves": m["carves"], "tracks": m["tracks"], "turn_ins": m["turn_ins"],
            "violations": m["violations"], "session_open": world.session is not None, "sessions": m["sessions"]}


def aggregate(results: list[dict]) -> dict:
    sessions = [s for r in results for s in r["sessions"]]
    scores = [s["score"] for s in sessions]
    trophies = sum(r["trophies"] for r in results)
    points = sum(r["trophy_points"] for r in results)
    shots = sum(r["shots"] for r in results)
    kills = sum(r["kills"] for r in results)
    refusals: dict[str, int] = {}
    outcomes: dict[str, dict[str, int]] = {}
    for r in results:
        for k, v in r["refusals"].items():
            refusals[k] = refusals.get(k, 0) + v
        for mood, o in r["outcomes_by_mood"].items():
            acc = outcomes.setdefault(mood, {})
            for k, v in o.items():
                acc[k] = acc.get(k, 0) + v
    n = len(sessions)
    mean = sum(scores) / n if n else 0.0
    sd = (sum((s - mean) ** 2 for s in scores) / (n - 1)) ** 0.5 if n > 1 else 0.0
    wall = sum(r["wall_s"] for r in results)
    return {"careers": len(results), "sessions": n,
            "outcomes": {o: sum(r["outcome"] == o for r in results) for o in sorted({r["outcome"] for r in results})},
            "score_per_permit": round(mean, 1), "score_sd": round(sd, 1),
            "score_min": min(scores) if scores else None, "score_max": max(scores) if scores else None,
            "trophies_per_session": round(trophies / n, 2) if n else None,
            "points_per_trophy": round(points / trophies, 2) if trophies else None,
            "lost_per_session": round(sum(r["lost_at_expiry"] + r["lost_at_gate"] for r in results) / n, 1) if n else None,
            "shots_per_session": round(shots / n, 1) if n else None,
            "kill_rate": round(kills / shots, 3) if shots else None, "outcomes_by_mood": outcomes,
            "refusals": dict(sorted(refusals.items(), key=lambda kv: -kv[1])),
            "violations": sum(len(r["violations"]) for r in results),
            "fast_steps": sum(r["fast_steps"] for r in results),
            "wall_s_per_session": round(wall / n, 2) if n else None}


def _pool_run(job):
    seed, permits, extra, followers, log = job
    return run_once(seed, permits, extra, followers=followers, log=log)


def arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs", type=int, default=1, help="careers, seeds seed..seed+runs-1")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--permits", type=int, default=5, help="Hunting Permits in the pack at the start of each career")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 3),
                    help="worker processes (default CPU count - 3: logical CPUs left to the game)")
    ap.add_argument("--followers", action="store_true", help="the character has followers out (the entry refuses)")
    ap.add_argument("--log", default=None, help="the runner's log: a file (one run) or a directory (several)")
    ap.add_argument("--json", action="store_true", help="print the summaries as JSON lines")
    return ap


def _row(r: dict) -> str:
    rate = f"{r['kill_rate']:.0%}" if r["kill_rate"] is not None else "-"
    ppt = f"{r['points_per_trophy']:.1f}" if r["points_per_trophy"] is not None else "-"
    return (f"seed {r['seed']}: {r['outcome']:8s} scores {r['scores']} mean {r['score_per_permit']} trophies "
            f"{r['trophies']} ({ppt} pt) lost {r['lost_at_expiry'] + r['lost_at_gate']} shots {r['shots']} "
            f"kills {r['kills']} ({rate}) violations {len(r['violations'])} wall {r['wall_s']} s")


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    extra = []
    if "--" in argv:
        i = argv.index("--")
        argv, extra = argv[:i], argv[i + 1:]
    a = arg_parser().parse_args(argv)
    jobs = []
    for k in range(a.runs):
        seed = a.seed + k
        log = None
        if a.log:
            if a.runs == 1:
                log = a.log
            else:
                os.makedirs(a.log, exist_ok=True)
                log = os.path.join(a.log, f"hg_seed{seed}.log")
        jobs.append((seed, a.permits, extra, a.followers, log))
    if a.jobs > 1 and len(jobs) > 1:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=min(a.jobs, len(jobs))) as pool:
            results = list(pool.map(_pool_run, jobs))
    else:
        results = [_pool_run(j) for j in jobs]
    for r in results:
        print(json.dumps(r) if a.json else _row(r))
        if r["error"] and not a.json:
            print("   " + r["error"].strip().splitlines()[-1])
    agg = aggregate(results)
    print(json.dumps({"all": agg}) if a.json else "all: " + json.dumps(agg))
    return 1 if any(r["outcome"] == "crashed" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())

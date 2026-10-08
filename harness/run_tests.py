"""Run the Python test suite: every test script (harness/test_*.py and the root-level
end-to-end test_*.py), several at once, each in its own process.

Each test file is a standalone script (its own check() + exit code) using free ports and
its own temp/log dirs, so any of them can run next to any other (and next to the live
proxy). Files start longest-first by their last measured time (logs/test_times.json,
runtime data), so the slow end-to-end tests don't end up last in the queue.

Usage:
  python harness/run_tests.py              # whole suite, jobs = CPU count // 2
  python harness/run_tests.py -j 8         # at most 8 files at once
  python harness/run_tests.py nav mover    # only files whose name contains a pattern
  python harness/run_tests.py -v           # print the full output of failing files

Each file's output goes to logs/tests/<file>.txt; exit code 1 if any file failed.
test_world.py is skipped (it only re-runs test_world_units.py + test_world_replay.py).
Children get UO_TEST_WORKERS = CPU count // jobs (at least 2): the tests that fan out
over the session logs in worker processes (test_nav, test_pathfind, test_uomap,
test_world_replay) cap their pools at it, so the suite doesn't oversubscribe the CPU
(that starves timing checks such as test_ctl's 0.4 s heartbeat window).
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_DIR = os.path.join(ROOT, "logs", "tests")
TIMES_PATH = os.path.join(ROOT, "logs", "test_times.json")
SKIP = {"harness/test_world.py"}           # umbrella: units + replay, both run on their own
TIMEOUT_S = 1800


def discover(patterns=()) -> list[str]:
    files = sorted(glob.glob("harness/test_*.py", root_dir=ROOT)) + sorted(glob.glob("test_*.py", root_dir=ROOT))
    files = [f.replace("\\", "/") for f in files]
    files = [f for f in files if f not in SKIP]
    if patterns:
        files = [f for f in files if any(p in os.path.basename(f) for p in patterns)]
    return files


def load_times() -> dict:
    try:
        with open(TIMES_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def run_one(rel: str, env: dict) -> tuple[str, int | str, float, str]:
    out_path = os.path.join(OUT_DIR, os.path.basename(rel) + ".txt")
    t0 = time.monotonic()
    with open(out_path, "wb") as out:
        try:
            rc = subprocess.run([sys.executable, "-u", rel], cwd=ROOT, stdin=subprocess.DEVNULL, env=env,
                                stdout=out, stderr=subprocess.STDOUT, timeout=TIMEOUT_S).returncode
        except subprocess.TimeoutExpired:
            rc = "TIMEOUT"
    return rel, rc, time.monotonic() - t0, out_path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("patterns", nargs="*", help="only files whose name contains one of these")
    ap.add_argument("-j", "--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("-v", "--verbose", action="store_true", help="print failing files' output")
    a = ap.parse_args()

    files = discover(a.patterns)
    if not files:
        print("no test files match", a.patterns)
        return 1
    times = load_times()
    files.sort(key=lambda f: -times.get(f, 60.0))     # unknown: assume slow, start early
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"{len(files)} test files, {min(a.jobs, len(files))} at a time")
    env = {**os.environ, "UO_TEST_WORKERS": str(max(2, (os.cpu_count() or 2) // max(1, a.jobs)))}
    t0 = time.monotonic()
    failed = []
    with ThreadPoolExecutor(max_workers=a.jobs) as pool:
        for fut in as_completed([pool.submit(run_one, f, env) for f in files]):
            rel, rc, dt, out_path = fut.result()
            if rc != "TIMEOUT":
                times[rel] = round(dt, 1)
            if rc != 0:
                failed.append((rel, rc, out_path))
            print(f"{'ok  ' if rc == 0 else 'FAIL'} {dt:6.1f}s  {rel}" + ("" if rc == 0 else f"  (rc {rc})"),
                  flush=True)
    with open(TIMES_PATH, "w") as f:
        json.dump(dict(sorted(times.items())), f, indent=1)
    print(f"\n{len(files) - len(failed)}/{len(files)} files passed in {time.monotonic() - t0:.1f}s")
    for rel, rc, out_path in failed:
        print(f"FAILED {rel} (rc {rc}): {os.path.relpath(out_path, ROOT)}")
        if a.verbose:
            with open(out_path, encoding="utf-8", errors="replace") as f:
                print(f.read())
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

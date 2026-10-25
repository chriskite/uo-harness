"""Simulator runs started from the viz Sim page (harness/viz_server.py /api/sim/options, /api/sim/jobs): run.py or a
scripted fight (sim/fight*.py) as a child process, one at a time, with the options the page picked, writing their traces
to the Sim page's trace dir.

Offline only: the child is the simulator and the runner against it (docs/SIM.md); nothing reaches the game. A batch
(run.py --runs > 1) keeps itself off the game's CPUs (cpu_budget.limit); a single run or a fight script is one process.
Jobs live in memory (the newest MAX_JOBS); their traces stay in the trace dir.
"""
from __future__ import annotations

import collections
import glob
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time

from sim import pets

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DATA = os.path.join(os.path.dirname(HERE), "data", "sim")
MAX_JOBS = 20
OUT_LINES = 200
MAX_SEEDS = 500
MAX_ARGS = 60
DEFAULTS = {"scenario": "mausoleum", "seed": 1, "runs": 1, "minutes": 30, "at_hunt": False, "others": False,
            "eerie": False, "reds": False, "prior": False, "fresh": False, "careers": 0, "trace": True, "sets": [],
            "runner_args": "", "team": "", "bestiary": ""}
LIMITS = {"seed": [0, 1_000_000], "runs": [1, 288], "minutes": [0.5, 240], "careers": [0, 64]}
_SET = re.compile(r"^[A-Za-z_][\w.]*\.[A-Za-z_]\w*=.+$")
_SEEDS = re.compile(r"^\s*\d+(\s*-\s*\d+)?(\s*,\s*\d+(\s*-\s*\d+)?)*\s*$")
_USAGE = re.compile(r"^\s*python harness/sim/(fight\w+)\.py (.*)$", re.M)
_BRACKET = re.compile(r"\[([^\]]+)\]")
_FIXED = {"--log", "--trace DIR", "--harness DIR"}   # the scripts' own plumbing, not a fight option


class BadRequest(ValueError):
    """An option the page sent that can't be run; the message names it."""


def scenarios() -> list[str]:
    return sorted(os.path.basename(p)[:-5] for p in glob.glob(os.path.join(DATA, "*.json"))
                  if not re.search(r"_(kb|observed)\.json$", p))


def fights() -> list[dict]:
    """Each fight*.py: its name, its docstring's first line, its usage line and the options in it ([--flag] a
    checkbox, [a|b] a choice)."""
    out = []
    for p in sorted(glob.glob(os.path.join(HERE, "fight*.py"))):
        with open(p, encoding="utf-8") as f:
            src = f.read()
        doc = re.match(r'\s*"""(.*?)"""', src, re.S)
        text = doc.group(1) if doc else ""
        name = os.path.basename(p)[:-3]
        usage = next((m for m in _USAGE.finditer(text) if m.group(1) == name), None)
        options = []
        for b in _BRACKET.findall(usage.group(2) if usage else ""):
            if b in _FIXED:
                continue
            if "|" in b:
                options.append({"type": "choice", "args": b.split("|")})
            elif re.fullmatch(r"--[\w-]+", b):
                options.append({"type": "flag", "arg": b})
        title = " ".join(text.split("\n\n", 1)[0].split())
        out.append({"name": name, "title": title, "usage": usage.group(0).strip() if usage else "", "options": options})
    return out


def _seeds(text) -> str:
    if not isinstance(text, str) or not _SEEDS.match(text):
        raise BadRequest("seeds: numbers and ranges, e.g. 1,2,5-8")
    seeds = []
    for part in text.replace(" ", "").split(","):
        a, _, b = part.partition("-")
        lo, hi = int(a), int(b or a)
        if hi < lo:
            raise BadRequest(f"seeds: {part} runs backwards")
        seeds.extend(range(lo, hi + 1))
        if len(seeds) > MAX_SEEDS:
            raise BadRequest(f"seeds: at most {MAX_SEEDS}")
    return ",".join(map(str, seeds))


def _num(o: dict, k: str, kind):
    v = o.get(k, DEFAULTS[k])
    if isinstance(v, bool) or not isinstance(v, (int, float)) or (kind is int and v != int(v)):
        raise BadRequest(f"{k}: a number")
    lo, hi = LIMITS[k]
    if not lo <= v <= hi:
        raise BadRequest(f"{k}: {lo}-{hi}")
    return kind(v)


def _flag(o: dict, k: str) -> bool:
    v = o.get(k, DEFAULTS[k])
    if not isinstance(v, bool):
        raise BadRequest(f"{k}: true or false")
    return v


class SimJobs:
    def __init__(self, trace_dir: str, prior_db: str | None):
        self.trace_dir, self.prior_db = trace_dir, prior_db
        self.lock = threading.Lock()
        self.jobs: collections.OrderedDict[int, dict] = collections.OrderedDict()
        self.next_id = 1

    # ------------------------------------------------------------ the page's choices
    def options(self) -> dict:
        prior = bool(self.prior_db) and os.path.exists(self.prior_db)
        return {"scenarios": scenarios(), "prior": {"available": prior, "path": self.prior_db or ""},
                "run": {"defaults": DEFAULTS, "limits": LIMITS}, "fights": fights(), "tamer": pets.options()}

    def argv(self, req: dict) -> tuple[str, str, list[str]]:
        """(kind, label, argv after the interpreter) for a POST body; BadRequest when it can't run."""
        kind = req.get("kind")
        if kind == "run":
            o = req.get("options") or {}
            if not isinstance(o, dict):
                raise BadRequest("options: an object")
            unknown = set(o) - set(DEFAULTS)
            if unknown:
                raise BadRequest(f"unknown option {sorted(unknown)[0]!r}")
            sc = o.get("scenario", DEFAULTS["scenario"])
            if sc not in scenarios():
                raise BadRequest(f"scenario: one of {scenarios()}")
            seed, runs = _num(o, "seed", int), _num(o, "runs", int)
            minutes, careers = _num(o, "minutes", float), _num(o, "careers", int)
            args = [os.path.join("harness", "sim", "run.py"), "--scenario", sc, "--seed", str(seed), "--runs", str(runs),
                    "--minutes", f"{minutes:g}"]
            for k, flag in (("at_hunt", "--at-hunt"), ("others", "--others"), ("eerie", "--eerie"), ("reds", "--reds"),
                            ("fresh", "--fresh")):
                if _flag(o, k):
                    args.append(flag)
            if careers:
                args += ["--careers", str(careers)]
            team, bestiary = o.get("team", ""), o.get("bestiary", "")
            if not isinstance(team, str):
                raise BadRequest("team: a preset name or pet names")
            if team:
                try:
                    pets.parse_team(team, pets.load_tamer())
                except ValueError as e:
                    raise BadRequest(f"team: {e}") from None
                args += ["--team", team]
            if not isinstance(bestiary, str):
                raise BadRequest("bestiary: a loadout name")
            if bestiary:
                allowed = pets.options()["bestiary"]
                if bestiary not in allowed:
                    raise BadRequest(f"bestiary: one of {allowed}")
                args += ["--bestiary", bestiary]
            if _flag(o, "prior"):
                if not (self.prior_db and os.path.exists(self.prior_db)):
                    raise BadRequest("prior: no live store here")
                args += ["--prior", self.prior_db]
            sets = o.get("sets", [])
            if not isinstance(sets, list) or not all(isinstance(s, str) and _SET.match(s.strip()) for s in sets):
                raise BadRequest("sets: lines of module.NAME=value")
            for s in sets:
                args += ["--set", s.strip()]
            if _flag(o, "trace"):
                args += ["--trace", self.trace_dir]
            ra = o.get("runner_args", "")
            try:
                extra = shlex.split(ra) if isinstance(ra, str) else None
            except ValueError as e:
                raise BadRequest(f"runner_args: {e}") from None
            if extra is None or len(extra) > MAX_ARGS:
                raise BadRequest(f"runner_args: text, at most {MAX_ARGS} words")
            if extra:
                args += ["--", *extra]
            label = f"run.py {sc} seed {seed}" + (f"-{seed + runs - 1}" if runs > 1 else "") + f", {minutes:g} min"
            return "run", label, args
        if kind == "fight":
            spec = next((f for f in fights() if f["name"] == req.get("fight")), None)
            if spec is None:
                raise BadRequest("fight: one of " + ", ".join(f["name"] for f in fights()))
            seeds = _seeds(req.get("seeds", "1"))
            allowed = {o["arg"] for o in spec["options"] if o["type"] == "flag"}
            choices = [set(o["args"]) for o in spec["options"] if o["type"] == "choice"]
            extra = req.get("args", [])
            if not isinstance(extra, list) or any(not isinstance(a, str) for a in extra):
                raise BadRequest("args: a list of the fight's options")
            for a in extra:
                if a not in allowed and not any(a in c for c in choices):
                    raise BadRequest(f"args: {a!r} isn't an option of {spec['name']}")
            trace = req.get("trace", True)
            if not isinstance(trace, bool):
                raise BadRequest("trace: true or false")
            args = [os.path.join("harness", "sim", f"{spec['name']}.py"), seeds, *extra]
            if trace:
                args += ["--trace", self.trace_dir]
            n = seeds.count(",") + 1
            return "fight", f"{spec['name']} {n} seed{'s' if n > 1 else ''}" + (f" {' '.join(extra)}" if extra else ""), args
        raise BadRequest("kind: run or fight")

    # ------------------------------------------------------------ jobs
    def start(self, req: dict) -> dict:
        kind, label, args = self.argv(req)
        with self.lock:
            if any(j["status"] == "running" for j in self.jobs.values()):
                raise RuntimeError("a simulator job is already running (one at a time)")
            os.makedirs(self.trace_dir, exist_ok=True)
            before = set(os.listdir(self.trace_dir))
            kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
            proc = subprocess.Popen([sys.executable, "-u", *args], cwd=REPO, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                    errors="replace", env={**os.environ, "PYTHONIOENCODING": "utf-8"}, **kw)
            job = {"id": self.next_id, "kind": kind, "label": label, "argv": ["python", *args], "status": "running",
                   "started": time.time(), "ended": None, "exit": None, "out": collections.deque(maxlen=OUT_LINES),
                   "proc": proc, "cancelled": False, "before": before}
            self.next_id += 1
            self.jobs[job["id"]] = job
            while len(self.jobs) > MAX_JOBS:
                old = next(iter(self.jobs.values()))
                if old["status"] == "running":
                    break
                self.jobs.popitem(last=False)
        threading.Thread(target=self._pump, args=(job,), daemon=True).start()
        return self.view(job)

    def _pump(self, job):
        proc = job["proc"]
        for line in proc.stdout:
            job["out"].append(line.rstrip("\n"))
        code = proc.wait()
        with self.lock:
            job["exit"], job["ended"] = code, time.time()
            job["status"] = "cancelled" if job["cancelled"] else "done" if code == 0 else "failed"

    def cancel(self, job_id) -> bool | None:
        """True when stopped; None for an unknown id, False when it isn't running."""
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            if job["status"] != "running":
                return False
            job["cancelled"] = True
        _kill_tree(job["proc"])
        return True

    def stop_all(self):
        for j in list(self.jobs.values()):
            if j["status"] == "running":
                self.cancel(j["id"])

    def traces(self, job) -> list[str]:
        """Trace files new since the job started and written before it ended (one job runs at a time, so they are
        its), newest first."""
        end = job["ended"] or time.time()
        out = []
        for p in glob.glob(os.path.join(self.trace_dir, "*.trace.jsonl.gz")):
            if os.path.basename(p) in job["before"]:
                continue
            try:
                ct = os.path.getctime(p)
            except OSError:
                continue
            if ct <= end + 1:
                out.append((ct, os.path.basename(p)))
        return [n for _, n in sorted(out, reverse=True)]

    def view(self, job) -> dict:
        return {k: (list(v) if k == "out" else v) for k, v in job.items() if k not in ("proc", "cancelled", "before")} \
            | {"traces": self.traces(job)}

    def listing(self) -> dict:
        with self.lock:
            jobs = list(self.jobs.values())
        return {"jobs": [self.view(j) for j in reversed(jobs)]}


def _kill_tree(proc):
    """The child and its pool workers."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            proc.terminate()


if __name__ == "__main__":                       # the page's choices, for a look from the shell
    print(json.dumps(SimJobs(os.path.join(REPO, "logs", "sim_traces"), None).options(), indent=1))

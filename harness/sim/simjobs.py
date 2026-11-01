"""Simulator runs started from the viz Sim page (harness/viz_server.py /api/sim/options, /api/sim/jobs): run.py or a
scripted fight (sim/fight*.py) as a child process, one at a time, with the options the page picked, writing their traces
to the Sim page's trace dir.

The page's choices come from the code itself, so it never needs updating by hand: the scenarios and their dungeon levels
(the runner block's `levels`, run.py --level), run.py's options and the hunt runner's (their argparse parsers:
`run_schema`, `runner_schema`), the runner modules' constants for `--set` (`constants`: the UPPER_CASE literals at the
top of loop_hunt.py and the harness modules it imports, with their comments) and each fight script's usage line. The
arguments the page sends are checked by those same parsers.

Offline only: the child is the simulator and the runner against it (docs/SIM.md); nothing reaches the game. A batch
(run.py --runs > 1) keeps itself off the game's CPUs (cpu_budget.limit); a single run or a fight script is one process.
Jobs live in memory (the newest MAX_JOBS); their traces stay in the trace dir.
"""
from __future__ import annotations

import argparse
import ast
import collections
import glob
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import tokenize

from sim import pets

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.dirname(HERE)
REPO = os.path.dirname(HARNESS)
DATA = os.path.join(HARNESS, "data", "sim")
MAX_JOBS = 20
OUT_LINES = 200
MAX_SEEDS = 500
MAX_ARGS = 60
DEFAULTS = {"scenario": "mausoleum", "level": "", "seed": 1, "runs": 1, "minutes": 30, "at_hunt": False,
            "others": False, "eerie": False, "reds": False, "prior": False, "fresh": False, "careers": 0, "trace": True,
            "sets": [], "run_args": [], "runner_args": [], "team": "", "bestiary": ""}
LIMITS = {"seed": [0, 1_000_000], "runs": [1, 288], "minutes": [0.5, 240], "careers": [0, 64]}
# run.py's options the page sets with its own fields (or that are plumbing: the log, --json's output the page doesn't
# read, --jobs past the CPU budget), so not among its other options
RUN_OWN = {"--scenario", "--level", "--seed", "--runs", "--minutes", "--at-hunt", "--others", "--eerie", "--reds",
           "--prior", "--fresh", "--careers", "--trace", "--set", "--team", "--bestiary", "--log", "--json", "--jobs",
           "-h", "--help"}
# the runner's options run.py sets itself (--timeout from the minutes), or that mean nothing against the simulator
RUNNER_OWN = {"--memory", "--seed", "--timeout", "--control-port", "--state-port", "--char", "-h", "--help"}
RUNNER_MODULE = "loop_hunt"
_CONST = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SET = re.compile(r"^[A-Za-z_][\w.]*\.[A-Za-z_]\w*=.+$")
_SEEDS = re.compile(r"^\s*\d+(\s*-\s*\d+)?(\s*,\s*\d+(\s*-\s*\d+)?)*\s*$")
_USAGE = re.compile(r"^\s*python harness/sim/(fight\w+)\.py (.*)$", re.M)
_BRACKET = re.compile(r"\[([^\]]+)\]")
_FIXED = {"--log", "--trace DIR", "--harness DIR"}   # the scripts' own plumbing, not a fight option


class BadRequest(ValueError):
    """An option the page sent that can't be run; the message names it."""


def _scenario_files() -> dict[str, dict]:
    """Each scenario's JSON by name (a data/sim file with spawns: not its wiki stats, observations or other tables)."""
    out = {}
    for p in sorted(glob.glob(os.path.join(DATA, "*.json"))):
        if re.search(r"_(kb|observed)\.json$", p):
            continue
        with open(p, encoding="utf-8") as f:
            sc = json.load(f)
        if "spawns" in sc:
            out[os.path.basename(p)[:-5]] = sc
    return out


def scenarios() -> list[str]:
    return list(_scenario_files())


def levels() -> dict[str, list[dict]]:
    """Per scenario, the dungeon levels run.py --level can hunt (sim/scenario.levels): name, note, area and whether it
    is the scenario's own."""
    return {name: [{"name": lv["name"], "default": bool(lv.get("default")), "note": lv.get("note", ""),
                    "area": lv.get("area") or sc.get("area")}
                   for lv in (sc.get("runner") or {}).get("levels") or []]
            for name, sc in _scenario_files().items()}


def _jsonable(v):
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (list, tuple, range)):
        return [_jsonable(x) for x in v]
    return str(v)


def parser_schema(ap: argparse.ArgumentParser, own: set[str]) -> list[dict]:
    """An argparse parser's options for the page, but those in `own`: per option its flag, help, whether it repeats,
    and its kind: "flag" (no value), "bool" (--x / its `off` --no-x, `default` the parser's) or "value" (`nargs` a
    count or "?"/"*"/"+", `metavar` per value, `choices` or null, `default`)."""
    fmt = ap._get_formatter()
    out = []
    for a in ap._actions:
        if not a.option_strings or own & set(a.option_strings):
            continue
        flag = next((s for s in a.option_strings if s.startswith("--")), a.option_strings[0])
        try:
            help_text = fmt._expand_help(a) if a.help else ""
        except (KeyError, TypeError, ValueError):
            help_text = a.help or ""
        e = {"flag": flag, "help": help_text, "repeat": isinstance(a, argparse._AppendAction)}
        if isinstance(a, argparse.BooleanOptionalAction):
            e |= {"kind": "bool", "off": next(s for s in a.option_strings if s.startswith("--no-")),
                  "default": a.default}
        elif a.nargs == 0:
            e |= {"kind": "flag"}
        else:
            n = 1 if a.nargs is None else a.nargs
            mv = a.metavar if isinstance(a.metavar, tuple) else \
                (a.metavar or a.dest.upper(),) * (n if isinstance(n, int) else 1)
            e |= {"kind": "value", "nargs": n, "metavar": list(mv),
                  "choices": None if a.choices is None else [str(c) for c in a.choices], "default": _jsonable(a.default)}
        out.append(e)
    return out


def _run_parser() -> argparse.ArgumentParser:
    from sim import run
    return run.arg_parser()


def _runner_parser() -> argparse.ArgumentParser:
    import loop_hunt
    return loop_hunt.arg_parser()


def run_schema() -> list[dict]:
    """run.py's options the page has no field of its own for (--area, --armor, --instrument, --links, ...)."""
    return parser_schema(_run_parser(), RUN_OWN)


def runner_schema() -> list[dict]:
    """The hunt runner's options (loop_hunt.arg_parser), the arguments after run.py's `--`."""
    return parser_schema(_runner_parser(), RUNNER_OWN)


def _check_args(ap: argparse.ArgumentParser, args, own: set[str], what: str) -> list[str]:
    """`args` (a list of strings) as `ap` parses them, none of `own`; BadRequest naming what's wrong."""
    if not isinstance(args, list) or any(not isinstance(a, str) for a in args) or len(args) > MAX_ARGS:
        raise BadRequest(f"{what}: a list of at most {MAX_ARGS} arguments")
    for a in args:
        if a == "--":
            raise BadRequest(f"{what}: no '--'")
        flag = a.split("=", 1)[0]
        if a.startswith("--") and flag not in ap._option_string_actions:
            raise BadRequest(f"{what}: unknown option {flag}")      # no abbreviations: `own` must hold
        if a.startswith("-") and flag in own:
            raise BadRequest(f"{what}: {flag} isn't one the page passes here")

    def error(msg):
        raise BadRequest(f"{what}: {msg}")
    ap.error = error
    ap.parse_args(args)
    return list(args)


def _comments(src: str) -> dict[int, str]:
    """Line number -> the comment on it."""
    out = {}
    try:
        for tok in tokenize.generate_tokens(iter(src.splitlines(keepends=True)).__next__):
            if tok.type == tokenize.COMMENT:
                out[tok.start[0]] = tok.string.lstrip("#").strip()
    except (tokenize.TokenError, SyntaxError):
        pass
    return out


def _module_constants(mod: str, path: str) -> list[dict]:
    """A module's top-level UPPER_CASE names bound to a literal: {name: module.NAME, value: as `--set` takes it (the
    source when on one line), doc: its comment (the one on its lines and the indented comment lines after, else the
    comment block right above)}."""
    with open(path, encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)
    lines = src.splitlines()
    comments = _comments(src)
    out = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            name = node.target.id
        else:
            continue
        if not _CONST.match(name):
            continue
        try:
            v = ast.literal_eval(node.value)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            continue
        seg = ast.get_source_segment(src, node.value) or ""
        value = seg if "\n" not in seg else repr(v)
        doc = [comments[i] for i in range(node.lineno, node.end_lineno + 1) if i in comments]
        i = node.end_lineno + 1
        while doc and i <= len(lines) and lines[i - 1][:1].isspace() and lines[i - 1].strip().startswith("#"):
            doc.append(comments.get(i, lines[i - 1].strip().lstrip("#").strip()))
            i += 1
        if not doc:
            i = node.lineno - 1
            while i >= 1 and lines[i - 1].startswith("#"):
                doc.insert(0, comments.get(i, ""))
                i -= 1
        out.append({"name": f"{mod}.{name}", "value": value, "doc": " ".join(d for d in doc if d)})
    return out


_constants_cache: tuple[tuple, list[dict]] | None = None


def runner_modules() -> list[tuple[str, str]]:
    """(module, path): loop_hunt and the harness modules it imports, the ones a run's `--set` reaches."""
    path = os.path.join(HARNESS, f"{RUNNER_MODULE}.py")
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module.split(".")[0])
    mods = [(RUNNER_MODULE, path)]
    for n in sorted(names):
        p = os.path.join(HARNESS, f"{n}.py")
        if os.path.exists(p):
            mods.append((n, p))
    return mods


def constants() -> list[dict]:
    """The runner modules' constants (`_module_constants`), read again when a module file changes."""
    global _constants_cache
    mods = runner_modules()
    key = tuple((m, os.path.getmtime(p)) for m, p in mods)
    if _constants_cache is None or _constants_cache[0] != key:
        _constants_cache = (key, [c for m, p in mods for c in _module_constants(m, p)])
    return _constants_cache[1]


def _check_sets(sets) -> list[str]:
    if not isinstance(sets, list) or not all(isinstance(s, str) and _SET.match(s.strip()) for s in sets):
        raise BadRequest("sets: lines of module.NAME=value")
    out = []
    for s in sets:
        s = s.strip()
        try:
            ast.literal_eval(s.split("=", 1)[1])
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            raise BadRequest(f"sets: {s}: the value isn't a Python literal (a number, True/False, None, a quoted "
                             f"string, a tuple ...)") from None
        out.append(s)
    return out


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
        lv = levels()
        return {"scenarios": list(lv), "levels": lv, "prior": {"available": prior, "path": self.prior_db or ""},
                "run": {"defaults": DEFAULTS, "limits": LIMITS}, "run_args": run_schema(),
                "runner_args": runner_schema(), "constants": constants(), "fights": fights(), "tamer": pets.options()}

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
            lv = levels()
            sc = o.get("scenario", DEFAULTS["scenario"])
            if sc not in lv:
                raise BadRequest(f"scenario: one of {list(lv)}")
            level = o.get("level", "")
            names = [v["name"] for v in lv[sc]]
            if not isinstance(level, str) or (level and level not in names):
                raise BadRequest(f"level: one of {names} or '' (the scenario's)")
            seed, runs = _num(o, "seed", int), _num(o, "runs", int)
            minutes, careers = _num(o, "minutes", float), _num(o, "careers", int)
            args = [os.path.join("harness", "sim", "run.py"), "--scenario", sc, "--seed", str(seed), "--runs", str(runs),
                    "--minutes", f"{minutes:g}"] + (["--level", level] if level else [])
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
            for s in _check_sets(o.get("sets", [])):
                args += ["--set", s]
            if _flag(o, "trace"):
                args += ["--trace", self.trace_dir]
            args += _check_args(_run_parser(), o.get("run_args", []), RUN_OWN, "run_args")
            extra = _check_args(_runner_parser(), o.get("runner_args", []), RUNNER_OWN, "runner_args")
            if extra:
                args += ["--", *extra]
            label = f"run.py {sc}" + (f" level {level}" if level else "") + f" seed {seed}" \
                + (f"-{seed + runs - 1}" if runs > 1 else "") + f", {minutes:g} min"
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

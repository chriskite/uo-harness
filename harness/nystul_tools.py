"""Nystul's lookup tools: the read-only, secret-free surface of the viz AI assistant.

harness/nystul_ext.ts (an omp extension) runs this script once per tool call, with an argv
and no shell:

    python harness/nystul_tools.py TOOL BASE64(JSON params)

It prints the result text (capped at OUT_MAX chars) and exits 0, or prints
`error: <reason>` and exits 1. The model gets no other tools, so this file is the boundary
(docs/PLAN.md "Nystul the Wizard"):
- files: only under the repository root, never .git, ClassicUO, the Discord browser profile,
  settings.json*, telegram.json*, handoff.json* or sqlite files (`_resolve`);
- sqlite: opened read-only, query_only, and an authorizer that permits reads only;
- ctl: a read-only subset. `know`, `lumber` and every acting command are excluded: they
  write, inject game input, or bump the Seer's heartbeat (ctl.py `heartbeat(mem)`);
- viz: GET on an allowlist of read routes;
- uo_propose: checks a memory-store change on a read-only connection and writes nothing;
  only the operator's Approve click in the viz applies it (harness/nystul_memory.py).

Env (set by harness/nystul.py; fallbacks for a manual run): NYSTUL_ROOT, NYSTUL_MEMORY_DB,
NYSTUL_VIZ, NYSTUL_STATE_PORT, NYSTUL_DATA (tests only).
"""
import base64
import datetime
import fnmatch
import itertools
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

OUT_MAX = 30_000
HTTP_TIMEOUT_S = 20
CTL_TIMEOUT_S = 60
SQL_TIMEOUT_S = 20
GREP_TIMEOUT_S = 20
SQL_ROWS_DEFAULT, SQL_ROWS_MAX = 100, 500
SQL_CELL_MAX = 1000
READ_SPAN_DEFAULT, READ_SPAN_MAX = 400, 1000
READ_LINE_MAX = 2000
GREP_DEFAULT, GREP_MAX = 50, 200
GREP_LINE_MAX = 300
GREP_FILE_MAX = 64 << 20
LIST_MAX = 300

API_ROUTES = {"/api/state", "/api/health", "/api/gate", "/api/jobs", "/api/jobs/plan", "/api/overseer",
              "/api/captcha", "/api/lumber/grove", "/api/skillnames", "/api/cliloc", "/api/facet"}
CTL_COMMANDS = ("status", "journal", "npcs", "map", "runes", "junctures", "chat")
RUNES_OPS = ("libraries", "find", "near")
SQL_PRAGMAS = {"table_info", "table_xinfo", "index_list", "index_info", "table_list"}
SEALED_PARTS = {".git", "discord_profile", "classicuo"}
SEALED_PREFIXES = ("settings.json", "telegram.json", "handoff.json")
SEALED_SUFFIXES = (".db", ".db-wal", ".db-shm", ".db.prev", ".db.pull.part")
SKIP_DIRS = {".git", "node_modules", "__pycache__", "ghidra", "decompiled", "ClassicUO-main", "Razor-master",
             "extracted", "ref", "models", "discord_media", "discord_profile"}


class ToolError(Exception):
    pass


# ---------------------------------------------------------------------------- config (read at call time)

def root() -> str:
    return os.path.realpath(os.environ.get("NYSTUL_ROOT") or os.path.dirname(HERE))


def memory_db() -> str:
    if os.environ.get("NYSTUL_MEMORY_DB"):
        return os.environ["NYSTUL_MEMORY_DB"]
    import memory
    return memory.DEFAULT_DB


def data_dir() -> str:
    return os.environ.get("NYSTUL_DATA") or os.path.join(root(), "harness", "data")


def dbs() -> dict:
    d = data_dir()
    return {"harness": memory_db(), "discord": os.path.join(d, "discord.db"),
            "discord_kb": os.path.join(d, "discord_kb.db")}


def viz() -> str:
    return (os.environ.get("NYSTUL_VIZ") or "http://127.0.0.1:8080").rstrip("/")


def state_port() -> int:
    return int(os.environ.get("NYSTUL_STATE_PORT") or 25942)


# ---------------------------------------------------------------------------- param helpers

def _str(p, key, required=True) -> str | None:
    v = p.get(key)
    if v is None or v == "":
        if required:
            raise ToolError(f"{key} is required")
        return None
    if not isinstance(v, str):
        raise ToolError(f"{key} must be a string")
    return v


def _int(p, key, default, lo, hi) -> int:
    v = p.get(key)
    if v is None:
        return default
    if isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) != v:
        raise ToolError(f"{key} must be an integer")
    return max(lo, min(int(v), hi))


def _cap(text: str, n: int = OUT_MAX) -> str:
    if len(text) <= n:
        return text
    return text[:n] + f"\n…[truncated {len(text) - n} chars]"


def _local(t) -> str:
    if t is None:
        return "?"
    return datetime.datetime.fromtimestamp(float(t)).strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------- paths

def _sealed(rel_parts) -> bool:
    if any(p.lower() in SEALED_PARTS for p in rel_parts):
        return True
    base = rel_parts[-1].lower() if rel_parts else ""
    return base.startswith(SEALED_PREFIXES) or base.endswith(SEALED_SUFFIXES)


def _resolve(rel: str | None) -> tuple[str, str]:
    """(absolute real path, POSIX path relative to the root) of a repository path."""
    base = root()
    full = os.path.realpath(os.path.join(base, rel or "."))
    try:
        inside = os.path.commonpath([os.path.normcase(base), os.path.normcase(full)]) == os.path.normcase(base)
    except ValueError:                                  # another drive
        inside = False
    if not inside:
        raise ToolError("outside the repository")
    relp = os.path.relpath(full, base).replace("\\", "/")
    parts = [] if relp == "." else relp.split("/")
    if _sealed(parts):
        raise ToolError(f"{relp} is sealed")
    return full, relp


def _skip_dir(name: str, rel: str) -> bool:
    return name in SKIP_DIRS or name.startswith(".venv-") or rel == "viz/dist"


def _binary(path) -> bool:
    with open(path, "rb") as f:
        return b"\0" in f.read(4096)


# ---------------------------------------------------------------------------- uo_api

def _project(data, path: str):
    prefix = ""
    for seg in path.split("."):
        if seg == "":
            continue
        if isinstance(data, list):
            try:
                data = data[int(seg)]
            except (ValueError, IndexError):
                raise ToolError(f"no {seg} at {prefix or '(top)'}")
        elif isinstance(data, dict) and seg in data:
            data = data[seg]
        else:
            raise ToolError(f"no {seg} at {prefix or '(top)'}")
        prefix = f"{prefix}.{seg}" if prefix else seg
    return data


def uo_api(p) -> str:
    route = _str(p, "route")
    if not route.startswith("/api/") or route.split("?", 1)[0] not in API_ROUTES:
        raise ToolError("route not allowed")
    try:
        with urllib.request.urlopen(viz() + route, timeout=HTTP_TIMEOUT_S) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        raise ToolError(f"HTTP {e.code}: {e.read()[:300].decode('utf-8', 'replace')}")
    except (OSError, ValueError) as e:
        raise ToolError(f"the viz is not reachable at {viz()}: {e}")
    try:
        data = json.loads(body)
    except ValueError:
        raise ToolError("the route did not return JSON")
    path = _str(p, "path", required=False)
    if path:
        data = _project(data, path)
    cap = min(_int(p, "max_chars", 20_000, 100, OUT_MAX), OUT_MAX)
    return _cap(json.dumps(data, separators=(",", ":"), ensure_ascii=False), cap)


# ---------------------------------------------------------------------------- uo_ctl

def uo_ctl(p) -> str:
    command = _str(p, "command")
    if command not in CTL_COMMANDS:
        raise ToolError(f"command not allowed; allowed: {', '.join(CTL_COMMANDS)}")
    args = p.get("args") or []
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise ToolError("args must be a list of strings")
    if command == "runes" and (not args or args[0] not in RUNES_OPS):
        raise ToolError(f"runes needs args[0] in {', '.join(RUNES_OPS)}")
    cmd = [sys.executable, os.path.join(root(), "harness", "ctl.py"), "--db", memory_db(),
           "--state-port", str(state_port()), command, *args]
    try:
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=CTL_TIMEOUT_S, cwd=root())
    except subprocess.TimeoutExpired:
        raise ToolError(f"ctl {command} ran over {CTL_TIMEOUT_S} s")
    if r.returncode != 0:
        raise ToolError((r.stdout.strip() or r.stderr.strip()[-1000:] or f"ctl exit {r.returncode}"))
    return r.stdout


# ---------------------------------------------------------------------------- uo_sql

def _ro(path):
    return sqlite3.connect(Path(os.path.abspath(path)).as_uri() + "?mode=ro", uri=True, timeout=10)


_SQL_OK = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
           getattr(sqlite3, "SQLITE_RECURSIVE", 33)}


def _authorizer(action, arg1, _arg2, _db, _trigger):
    if action in _SQL_OK:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and (arg1 or "").lower() in SQL_PRAGMAS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _cell(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bytes):
        return f"<blob {len(v)} bytes>"
    s = str(v)
    if len(s) > SQL_CELL_MAX:
        s = s[:SQL_CELL_MAX] + f"…[{len(s) - SQL_CELL_MAX} more]"
    return s.replace("\\", "\\\\").replace("\t", "\\t").replace("\r", "\\r").replace("\n", "\\n")


def uo_sql(p) -> str:
    name = _str(p, "db")
    paths = dbs()
    if name not in paths:
        raise ToolError(f"db must be one of {', '.join(paths)}")
    path = paths[name]
    if not os.path.exists(path):
        raise ToolError(f"{name} database is not on this computer")
    query = _str(p, "query")
    n = _int(p, "limit", SQL_ROWS_DEFAULT, 1, SQL_ROWS_MAX)
    con = _ro(path)
    try:
        con.execute("PRAGMA query_only=ON")
        con.set_authorizer(_authorizer)
        deadline = time.monotonic() + SQL_TIMEOUT_S
        con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
        try:
            cur = con.execute(query)
            rows = cur.fetchmany(n + 1)
        except sqlite3.OperationalError as e:
            if time.monotonic() > deadline:
                raise ToolError(f"query ran over {SQL_TIMEOUT_S} s; filter on indexed columns or add LIMIT")
            raise ToolError(f"sql: {e}")
        except (sqlite3.DatabaseError, sqlite3.ProgrammingError, sqlite3.Warning) as e:
            raise ToolError(f"sql: {e}")
        if cur.description is None:
            return "(no result columns)"
        out = ["\t".join(d[0] for d in cur.description)]
        out += ["\t".join(_cell(v) for v in r) for r in rows[:n]]
        out.append(f"({min(len(rows), n)} rows shown; more available)" if len(rows) > n
                   else f"({len(rows)} rows)")
        return "\n".join(out)
    finally:
        con.close()


# ---------------------------------------------------------------------------- uo_knowledge

def _entry(e) -> str:
    s = (f"#{e['id']} {e['kind']} [{e['topic']}] (conf {e['confidence']:.2f}, imp {e['importance']}, "
         f"status {e['status']}, updated {_local(e['updated_t'])}): {e['content']}")
    if e.get("tags"):
        s += f"\n    tags: {' '.join(e['tags'])}"
    if e.get("entities"):
        s += f"\n    entities: {json.dumps(e['entities'], ensure_ascii=False)}"
    if e.get("x") is not None:
        s += f"\n    at facet {e.get('facet') or 0} {e['x']},{e['y']}"
    return s


def uo_knowledge(p) -> str:
    import embedder
    import knowledge
    query = _str(p, "query")
    kind = _str(p, "kind", required=False)
    if kind and kind not in knowledge.KINDS:
        raise ToolError(f"kind must be one of {', '.join(knowledge.KINDS)}")
    limit = _int(p, "limit", 10, 1, 30)
    path = memory_db()
    if not os.path.exists(path):
        raise ToolError("harness database is not on this computer")
    con = _ro(path)
    note = ""
    try:
        emb = embedder if embedder.available() else None
        try:
            hits = knowledge.Knowledge(con, embed=emb).search(query, kind=kind, limit=limit, touch=False)
        except sqlite3.OperationalError:
            if emb is None:
                raise
            # the vector refresh writes; on a read-only connection fall back to words
            hits = knowledge.Knowledge(con).search(query, kind=kind, limit=limit, touch=False)
            note = "recall: words only (vectors stale)\n"
        if emb is None:
            note = "recall: words only (no embedder)\n"
    finally:
        con.close()
    if not hits:
        return note + "no entries match"
    return note + "\n".join(_entry(e) for e in hits)


# ---------------------------------------------------------------------------- uo_discord

def uo_discord(p) -> str:
    query = _str(p, "query")
    source = _str(p, "source", required=False) or "facts"
    k = _int(p, "k", 8, 1, 20)
    paths = dbs()
    if source == "facts":
        if not (os.path.exists(paths["discord_kb"]) and os.path.exists(paths["discord"])):
            raise ToolError("Discord KB is not on this computer")
        import discord_kb
        kb = _ro(paths["discord_kb"])
        try:
            res = discord_kb.search(kb, discord_kb.Msgs(paths["discord"]), query, k, all_verdicts=False)
        finally:
            kb.close()
        if not res:
            return "no facts match"
        return "\n".join(f"[{r['verdict']} {r['confidence']:.2f}] {r['topic']}: {r['statement']}\n    {r['link'] or ''}"
                         for r in res)
    if source == "messages":
        vec = os.path.join(data_dir(), "discord_vec.db")
        if not (os.path.exists(paths["discord"]) and os.path.exists(vec)):
            raise ToolError("Discord messages are not on this computer")
        import discord_search
        try:
            hits = discord_search.search(query, k, _str(p, "channel", required=False),
                                         _str(p, "since", required=False), db_path=paths["discord"], vec_path=vec)
        except SystemExit as e:                         # unknown channel
            raise ToolError(str(e))
        except ValueError as e:                         # bad since date
            raise ToolError(str(e))
        return discord_search.format_hits(hits) or "no messages match"
    raise ToolError("source must be facts or messages")


# ---------------------------------------------------------------------------- uo_propose

def uo_propose(p) -> str:
    """Check a memory-store change for the operator to approve. Writes nothing: nystul.py
    keeps the proposal and only the operator's Approve click applies it (nystul_memory)."""
    import knowledge
    import nystul_memory
    try:
        prop = nystul_memory.normalize(p)
    except nystul_memory.ProposalError as e:
        raise ToolError(f"bad proposal: {e}")
    path = memory_db()
    if not os.path.exists(path):
        raise ToolError("harness database is not on this computer")
    con = _ro(path)
    try:
        k = knowledge.Knowledge(con)
        before = {}
        for kid in nystul_memory.targets(prop):
            e = k.get(kid)
            if e is None or e["status"] != "active":
                raise ToolError(f"#{kid} isn't an active entry" + (f" (it is {e['status']})" if e else ""))
            before[kid] = e
    finally:
        con.close()
    lines = [f"Proposal ready: {prop['op']}" + (f" #{prop['id']}" if prop.get("id") else "") + f". Why: {prop['why']}"]
    for kid, e in before.items():
        lines.append(f"Current #{kid} {e['kind']} [{e['topic']}]: {e['content']}")
    if prop["op"] != "retract":
        shown = {k: v for k, v in prop.items() if k not in ("op", "why", "id") and v not in (None, [])}
        lines.append("Proposed: " + json.dumps(shown, ensure_ascii=False))
    lines.append("Nothing is written yet. The operator sees an Approve button under your answer; "
                 "tell them it waits for their approval.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------- uo_read / uo_grep / uo_list

def uo_read(p) -> str:
    full, rel = _resolve(_str(p, "path"))
    if not os.path.isfile(full):
        raise ToolError(f"{rel} is not a file")
    if _binary(full):
        raise ToolError("binary file")
    start = _int(p, "start", 1, 1, 10**9)
    end = _int(p, "end", start + READ_SPAN_DEFAULT - 1, start, 10**9)
    end = min(end, start + READ_SPAN_MAX - 1)
    out = []
    with open(full, encoding="utf-8", errors="replace") as f:
        for n, line in enumerate(itertools.islice(f, start - 1, end), start):
            line = line.rstrip("\r\n")
            if len(line) > READ_LINE_MAX:
                line = line[:READ_LINE_MAX] + f"…[{len(line) - READ_LINE_MAX} more]"
            out.append(f"{n}: {line}")
    if not out:
        return f"(no lines from {start}: the file is shorter)"
    return "\n".join(out)


def _walk(full, rel):
    """(absolute path, POSIX relative path) of the files under full, skipping sealed and bulk dirs."""
    if os.path.isfile(full):
        yield full, rel
        return
    for d, dirs, files in os.walk(full):
        drel = os.path.relpath(d, root()).replace("\\", "/")
        drel = "" if drel == "." else drel + "/"
        dirs[:] = sorted(x for x in dirs if not _skip_dir(x, drel + x) and not _sealed((drel + x).split("/")))
        for f in sorted(files):
            frel = drel + f
            if not _sealed(frel.split("/")):
                yield os.path.join(d, f), frel


def uo_grep(p) -> str:
    try:
        rx = re.compile(_str(p, "pattern"), re.IGNORECASE)
    except re.error as e:
        raise ToolError(f"bad pattern: {e}")
    full, rel = _resolve(_str(p, "path", required=False))
    glob = _str(p, "glob", required=False)
    most = _int(p, "max", GREP_DEFAULT, 1, GREP_MAX)
    deadline = time.monotonic() + GREP_TIMEOUT_S
    out, stopped = [], None
    for path, frel in _walk(full, rel):
        if glob and not fnmatch.fnmatch(frel, glob):
            continue
        if time.monotonic() > deadline:
            stopped = f"{GREP_TIMEOUT_S} s limit"
            break
        try:
            if os.path.getsize(path) > GREP_FILE_MAX or _binary(path):
                continue
            with open(path, encoding="utf-8", errors="replace") as f:
                for n, line in enumerate(f, 1):
                    if rx.search(line):
                        line = line.rstrip("\r\n")
                        if len(line) > GREP_LINE_MAX:
                            line = line[:GREP_LINE_MAX] + "…"
                        out.append(f"{frel}:{n}: {line}")
                        if len(out) >= most:
                            stopped = f"{most} matches"
                            break
                    if n % 50_000 == 0 and time.monotonic() > deadline:
                        stopped = f"{GREP_TIMEOUT_S} s limit"
                        break
        except OSError:
            continue
        if stopped:
            break
    if not out:
        return "no matches" + (f" (stopped: {stopped})" if stopped else "")
    return "\n".join(out) + (f"\n(stopped: {stopped})" if stopped else "")


def _row(path, rel, is_dir) -> str:
    st = os.stat(path)
    size = "-" if is_dir else str(st.st_size)
    return f"{size:>12}  {_local(st.st_mtime)}  {rel}{'/' if is_dir else ''}"


def uo_list(p) -> str:
    full, rel = _resolve(_str(p, "path", required=False))
    if not os.path.isdir(full):
        raise ToolError(f"{rel} is not a directory")
    glob = _str(p, "glob", required=False)
    base = root()
    out, more = [], False
    if glob:
        it = Path(full).glob(glob)
    else:
        it = sorted(Path(full).iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
    for x in it:
        xr = os.path.relpath(x, base).replace("\\", "/")
        parts = xr.split("/")
        if _sealed(parts) or any(_skip_dir(seg, "/".join(parts[:i + 1])) for i, seg in enumerate(parts[:-1])):
            continue
        if len(out) >= LIST_MAX:
            more = True
            break
        try:
            out.append(_row(str(x), xr, x.is_dir()))
        except OSError:
            continue
    if not out:
        return "(empty)"
    return "\n".join(out) + (f"\n(stopped at {LIST_MAX} entries)" if more else "")


# ---------------------------------------------------------------------------- entry points

TOOLS = {"uo_api": uo_api, "uo_ctl": uo_ctl, "uo_sql": uo_sql, "uo_knowledge": uo_knowledge,
         "uo_discord": uo_discord, "uo_read": uo_read, "uo_grep": uo_grep, "uo_list": uo_list,
         "uo_propose": uo_propose}


def run(tool: str, params) -> tuple[bool, str]:
    """(ok, text) of one tool call; refusals and failures are (False, "error: ...")."""
    fn = TOOLS.get(tool)
    if fn is None:
        return False, f"error: unknown tool {tool}"
    if not isinstance(params, dict):
        return False, "error: params must be an object"
    try:
        return True, _cap(fn(params))
    except ToolError as e:
        return False, f"error: {e}"
    except Exception as e:                              # a tool bug: report it, never a traceback dump
        return False, f"error: {type(e).__name__}: {e}"


def main(argv) -> int:
    if len(argv) != 3:
        print("usage: nystul_tools.py TOOL BASE64JSON")
        return 2
    try:
        params = json.loads(base64.b64decode(argv[2]).decode("utf-8"))
    except ValueError as e:
        print(f"error: bad params: {e}")
        return 1
    ok, text = run(argv[1], params)
    sys.stdout.reconfigure(encoding="utf-8")
    print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

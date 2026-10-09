"""Task wrapper: runs one whitelisted task for the overseer (docs/OVERSEER.md).

Started detached by `ctl.py run`; never run by hand. It runs the task script
with its output in logs/tasks/<task_id>.log, honours a stop request from
`ctl.py stop` (meta key `task_stop:<task_id>`), passes the task its id (env UO_TASK_ID,
for `ctl stop --after-trip`'s meta key `task_finish:<task_id>`; it clears both at the
end), and on exit removes the task from the
`meta` key `tasks` and posts a juncture:
  task_done   (exit 0, severity info)
  task_failed (non-zero exit or stopped, severity attention)
with data {task_id, task, args, exit_code, log, tail[, stopped]}.

Also the shared process helpers (liveness with identity check, terminate)
that ctl.py uses, so a recycled pid is never mistaken for our task.

Usage (internal): python task_wrap.py --spec '<json>'
  spec = {task_id, task, script, args, argv, log, db, char_serial}
"""
import argparse
import json
import os
import subprocess
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from memory import Memory  # noqa: E402

TASKS_KEY = "tasks"
STOP_KEY = "task_stop"        # stop_key(task_id): ctl stop asks that task's wrapper to terminate it
FINISH_KEY = "task_finish"    # finish_key(task_id): ctl stop --after-trip; the task ends itself at home
TAIL_LINES = 20
STOP_GRACE_S = 10.0
POLL_S = 0.5
SPAWN_GRACE_S = 30.0   # an entry without a pid yet is "starting" this long
IDENTITY_SLACK_S = 5.0  # recorded vs OS creation time of the same process

WINDOWS = os.name == "nt"
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


# ----------------------------------------------------------------------- meta
def meta_get(mem: Memory, key: str, default=None):
    row = mem.con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return default if row is None else row[0]


def meta_set(mem: Memory, key: str, value: str | None):
    if value is None:
        mem.con.execute("DELETE FROM meta WHERE key=?", (key,))
    else:
        mem.con.execute("INSERT OR REPLACE INTO meta VALUES(?, ?)", (key, value))
    mem.con.commit()


def meta_update_json(mem: Memory, key: str, fn, default):
    """Atomic read-modify-write of a JSON meta value: fn(value) -> (new, result).
    new None deletes the key. Returns result."""
    con = mem.con
    con.commit()
    con.execute("BEGIN IMMEDIATE")
    try:
        row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        cur = default if row is None else json.loads(row[0])
        new, result = fn(cur)
        if new is None:
            con.execute("DELETE FROM meta WHERE key=?", (key,))
        else:
            con.execute("INSERT OR REPLACE INTO meta VALUES(?, ?)", (key, json.dumps(new)))
        con.commit()
    except BaseException:
        con.rollback()
        raise
    return result


def task_entries(mem: Memory) -> list:
    raw = meta_get(mem, TASKS_KEY)
    return [] if raw is None else json.loads(raw)


def remove_entry(mem: Memory, task_id: str) -> bool:
    def fn(tasks):
        keep = [t for t in tasks if t.get("task_id") != task_id]
        return keep, len(keep) != len(tasks)
    return meta_update_json(mem, TASKS_KEY, fn, [])


def patch_entry(mem: Memory, task_id: str, **fields) -> bool:
    """Update an existing entry only (never re-adds one that already ended)."""
    def fn(tasks):
        hit = False
        for t in tasks:
            if t.get("task_id") == task_id:
                t.update(fields)
                hit = True
        return tasks, hit
    return meta_update_json(mem, TASKS_KEY, fn, [])


# ------------------------------------------------------------------ processes
def proc_created(pid: int) -> float | None:
    """OS creation time (epoch s) of a running process, None if it isn't running."""
    if not pid:
        return None
    if WINDOWS:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        h = k32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return None
        try:
            code = wintypes.DWORD()
            if not k32.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != 259:  # STILL_ACTIVE
                return None
            c, e, k, u = (wintypes.FILETIME() for _ in range(4))
            if not k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(k), ctypes.byref(u)):
                return None
            ticks = (c.dwHighDateTime << 32) | c.dwLowDateTime
            return ticks / 1e7 - 11644473600.0
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    try:
        with open(f"/proc/{pid}/stat") as f:
            start_ticks = int(f.read().rsplit(")", 1)[1].split()[19])
        with open("/proc/stat") as f:
            btime = next(int(line.split()[1]) for line in f if line.startswith("btime"))
        return btime + start_ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, StopIteration, IndexError):
        return time.time()       # alive; identity unknown on this platform


def is_ours(pid: int | None, created: float | None) -> bool:
    """pid is running and is the process we recorded (not a recycled pid)."""
    c = proc_created(pid) if pid else None
    if c is None:
        return False
    return created is None or abs(c - created) <= IDENTITY_SLACK_S


def terminate(pid: int, created: float | None) -> bool:
    """Terminate pid if it is still the recorded process."""
    if not is_ours(pid, created):
        return False
    if WINDOWS:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        h = k32.OpenProcess(0x0001, False, pid)   # PROCESS_TERMINATE
        if not h:
            return False
        try:
            return bool(k32.TerminateProcess(h, 1))
        finally:
            k32.CloseHandle(h)
    import signal
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except OSError:
        return False


def entry_alive(entry: dict, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    if entry.get("pid") is None:
        return now - entry.get("started", 0) < SPAWN_GRACE_S
    return is_ours(entry["pid"], entry.get("pid_created"))


# ----------------------------------------------------------------------- tail
def read_tail(path: str, n: int = TAIL_LINES) -> list[str]:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 16384))
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    return [ln.rstrip("\r") for ln in text.split("\n") if ln.strip()][-n:]


def summary_line(tail: list[str]) -> str:
    """The line that explains the ending: the runner's 'ABORTED: ...' line, else
    the exception line after a traceback, else the last line."""
    for ln in reversed(tail):
        if "ABORTED:" in ln:
            return ln.strip()
    if any(ln.startswith("Traceback") for ln in tail):
        return tail[-1].strip()
    return tail[-1].strip() if tail else "(no output)"


def post_end(mem: Memory, spec: dict, code: int, stopped: bool, source: str = None, note: str = ""):
    tail = read_tail(spec["log"])
    task = spec["task"]
    if stopped:
        kind, sev = "task_failed", "attention"
        head = f"{task} stopped by the overseer{note} (exit {code})"
    elif code == 0:
        kind, sev = "task_done", "info"
        head = f"{task} finished (exit 0)"
    else:
        kind, sev = "task_failed", "attention"
        head = f"{task} failed (exit {code})"
    summary = f"{head}: {summary_line(tail)}"[:300]
    data = {"task_id": spec["task_id"], "task": task, "args": spec.get("args", []),
            "exit_code": code, "log": spec["log"], "tail": tail}
    if stopped:
        data["stopped"] = True
    return mem.juncture(source or task, kind, summary, severity=sev, data=data,
                        char_serial=spec.get("char_serial"))


def stop_key(task_id: str) -> str:
    return f"{STOP_KEY}:{task_id}"


def finish_key(task_id: str) -> str:
    return f"{FINISH_KEY}:{task_id}"


def in_scope(entry_char: int | None, mine: int | None) -> bool:
    """A task entry of character `entry_char` concerns a reader of character `mine`:
    either side without a character sees/conflicts with everything."""
    return mine is None or entry_char is None or entry_char == mine


def stop_requested(mem: Memory, task_id: str) -> bool:
    return meta_get(mem, stop_key(task_id)) is not None


def finish_requested(mem: Memory, task_id: str) -> bool:
    """`ctl stop --after-trip` asked task `task_id` to end at home after its trip (finish_key). The task
    itself reads it (the lumber runner: LumberLoop.ending); the wrapper only clears it at the end."""
    return meta_get(mem, finish_key(task_id)) is not None


# ----------------------------------------------------------------------- main
def run(spec: dict) -> int:
    mem = Memory(spec["db"], char_serial=spec.get("char_serial"))
    os.makedirs(os.path.dirname(os.path.abspath(spec["log"])), exist_ok=True)
    code, stopped = -1, False
    try:
        with open(spec["log"], "ab") as logf:
            logf.write(f"== task {spec['task_id']}: {spec['task']} {' '.join(spec.get('args', []))} "
                       f"({time.strftime('%Y-%m-%d %H:%M:%S')})\n".encode())
            logf.flush()
            env = dict(os.environ, PYTHONUNBUFFERED="1", UO_TASK_ID=spec["task_id"])   # finish_requested
            child = subprocess.Popen(
                [sys.executable, "-u", spec["script"], *spec.get("argv", [])],
                stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT, cwd=ROOT, env=env,
                creationflags=CREATE_NO_WINDOW if WINDOWS else 0)
            patch_entry(mem, spec["task_id"], child_pid=child.pid, child_created=proc_created(child.pid))
            while child.poll() is None:
                if not stopped and stop_requested(mem, spec["task_id"]):
                    stopped = True
                    child.terminate()
                    try:
                        child.wait(STOP_GRACE_S)
                    except subprocess.TimeoutExpired:
                        child.kill()
                    break
                time.sleep(POLL_S)
            code = child.wait()
            logf.write(f"== exit {code}{' (stopped)' if stopped else ''}\n".encode())
    except BaseException:
        with open(spec["log"], "a", encoding="utf-8") as f:
            f.write("== task_wrap error\n" + traceback.format_exc())
        code = code if code != -1 else 1
    finally:
        remove_entry(mem, spec["task_id"])
        meta_set(mem, stop_key(spec["task_id"]), None)       # per-task keys: nothing else would clear them
        meta_set(mem, finish_key(spec["task_id"]), None)
        post_end(mem, spec, code, stopped)
        mem.close()
    return code


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="overseer task wrapper (internal; see ctl.py run)")
    ap.add_argument("--spec", required=True, help="JSON task spec")
    a = ap.parse_args(argv)
    run(json.loads(a.spec))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Nystul the Wizard: the viz AI assistant's conversations and runs (docs/VISUALIZER.md).

Each question is one headless `omp` run (`--mode json`, no session) that loads only the
harness/nystul_ext.ts tools, so the model can look things up (harness/nystul_tools.py) but
never act or write. The prior transcript goes into an @prompt file. Conversations live in
their own sqlite file (DEFAULT_DB), never in the memory store, so the Seer's chat bus and
heartbeat stay untouched.

viz_server owns one Nystul; the UI polls list()/get() while a run streams.
"""
import datetime
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

import nystul_memory

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(ROOT, "harness", "data", "nystul.db")
PROMPT_PATH = os.path.join(ROOT, "harness", "nystul_prompt.md")
EXT_PATH = os.path.join(ROOT, "harness", "nystul_ext.ts")

MAX_CHARS = 4000                      # question length
MAX_RUNS = 2                          # concurrent runs across conversations
RUN_TIMEOUT_S = 300
TRANSCRIPT_MAX_CHARS = 24_000
TRANSCRIPT_MAX_MESSAGES = 12
LIST_MAX = 100
STDERR_TAIL = 2048
TOOL_NAMES = ("uo_api", "uo_ctl", "uo_sql", "uo_knowledge", "uo_discord", "uo_read", "uo_grep", "uo_list",
              "uo_propose")

# omp --mode json event shape (probed 2026-10-08, omp 18.8.3; docs/NOTES.md "Nystul")
EV_TOOL_START = "tool_execution_start"
EV_TOOL_END = "tool_execution_end"
F_TOOL_NAME = "toolName"
F_TOOL_ID = "toolCallId"
F_TOOL_ARGS = "args"
F_TOOL_ERR = "isError"

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations(id INTEGER PRIMARY KEY, t_created REAL NOT NULL, t_updated REAL NOT NULL,
  title TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, conv INTEGER NOT NULL REFERENCES conversations(id),
  t REAL NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL, status TEXT NOT NULL, steps TEXT NOT NULL DEFAULT '[]',
  model TEXT, cost REAL, tokens_in INTEGER, tokens_out INTEGER, seconds REAL, error TEXT);
CREATE INDEX IF NOT EXISTS messages_conv ON messages(conv, id);
-- memory-store changes Nystul proposed (uo_propose) in a finished answer; status pending ->
-- applying -> applied | failed, only through approve() (the operator's click)
CREATE TABLE IF NOT EXISTS proposals(id INTEGER PRIMARY KEY, conv INTEGER NOT NULL, msg INTEGER NOT NULL,
  t REAL NOT NULL, params TEXT NOT NULL, before TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL,
  t_decided REAL, result TEXT, error TEXT);
CREATE INDEX IF NOT EXISTS proposals_msg ON proposals(conv, msg);
"""
MSG_COLS = ("id", "role", "t", "text", "status", "steps", "model", "cost", "tokens_in", "tokens_out", "seconds",
            "error")


class NystulError(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code = code


class Run:
    """One live omp run; the reader thread fills it from the JSON event stream."""

    def __init__(self, conv: int, user_id: int, msg_id: int, question: str):
        self.conv, self.user_id, self.msg_id, self.question = conv, user_id, msg_id, question
        self.t0 = time.time()
        self.proc = None
        self.partial = ""             # the assistant message being streamed
        self.final = ""               # text of the newest finished assistant message
        self.steps = []               # {tool, args, t, ok}
        self.cost = None
        self.tokens_in = None
        self.tokens_out = None
        self.model = None
        self.stop = None
        self.cancelled = False
        self.stderr_tail = ""


def _text(message) -> str:
    return "".join(c.get("text") or "" for c in (message.get("content") or [])
                   if isinstance(c, dict) and c.get("type") == "text")


def parse_event(run: Run, line: str) -> None:
    """Apply one stdout line of `omp --mode json` to run; other lines are ignored."""
    line = line.strip()
    if not line.startswith("{"):
        return
    try:
        ev = json.loads(line)
    except ValueError:
        return
    if not isinstance(ev, dict):
        return
    kind = ev.get("type")
    msg = ev.get("message") if isinstance(ev.get("message"), dict) else {}
    if kind == "message_start" and msg.get("role") == "assistant":
        run.partial = ""
    elif kind == "message_update":
        ame = ev.get("assistantMessageEvent") or {}
        if ame.get("type") == "text_delta":
            run.partial += ame.get("delta") or ""
    elif kind == EV_TOOL_START:
        args = ev.get(F_TOOL_ARGS)
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                pass
        run.steps.append({"tool": ev.get(F_TOOL_NAME), "args": args if args is not None else {},
                          "t": time.time(), "ok": None, "id": ev.get(F_TOOL_ID)})
    elif kind == EV_TOOL_END:
        # Parallel calls end in any order: match the call id (the oldest open step without one).
        cid = ev.get(F_TOOL_ID)
        for s in run.steps:
            if s["ok"] is None and (cid is None or s.get("id") in (cid, None)):
                s["ok"] = not ev.get(F_TOOL_ERR)
                break
    elif kind == "message_end" and msg.get("role") == "assistant":
        usage = msg.get("usage") or {}
        if usage:
            # usage.input excludes prompt-cache reads/writes; the sum is the real prompt size
            run.cost = (run.cost or 0.0) + ((usage.get("cost") or {}).get("total") or 0.0)
            run.tokens_in = (run.tokens_in or 0) + sum(usage.get(x) or 0 for x in ("input", "cacheRead", "cacheWrite"))
            run.tokens_out = (run.tokens_out or 0) + (usage.get("output") or 0)
        run.model = msg.get("model") or run.model
        run.stop = msg.get("stopReason")
        text = _text(msg)
        if text.strip():
            run.final = text


def _kill(proc) -> None:
    if proc is None or proc.poll() is not None:
        return
    if os.name == "nt":                # the whole tree: omp's tool children too
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if proc.poll() is None:
        proc.kill()


def _now_header() -> str:
    now = datetime.datetime.now().astimezone()
    z = now.strftime("%z")
    off = f"{z[:3]}:{z[3:]}" if len(z) == 5 else z
    return f"Now: {now.strftime('%Y-%m-%d %H:%M %Z')} (UTC offset {off}, epoch {int(now.timestamp())})"


class Nystul:
    def __init__(self, db_path=DEFAULT_DB, *, model="sonnet", thinking="medium", omp_cmd=None, env=None,
                 context=None, lookup=None, apply=None):
        self.db_path = db_path
        self.model, self.thinking = model, thinking
        self.omp_cmd = list(omp_cmd) if omp_cmd else [shutil.which("omp")]
        self.available = self.omp_cmd[0] is not None
        # the extension runs nystul_tools.py with this interpreter from this checkout
        self.env = {"NYSTUL_PY": sys.executable, "NYSTUL_ROOT": ROOT, **(env or {})}
        self.context = context
        # memory store access, for proposals only: lookup(id) -> compact entry | None (the
        # "before" an Approve card shows), apply(normalized proposal) -> {id, action}
        # (raises knowledge.KnowledgeError, or LookupError without a store)
        self.lookup = lookup
        self.apply = apply
        self._lock = threading.Lock()
        self._con = None
        self._live = {}               # conv id -> Run
        self._threads = []
        self._closed = False

    # ------------------------------------------------------------------ store
    def _open(self, create=True):
        """The connection (self._lock held), opened on first use; None when the file doesn't
        exist and create is False, so GETs never create it."""
        if self._con is None and (create or os.path.exists(self.db_path)):
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
            con = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30)
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(SCHEMA)
            con.execute("UPDATE messages SET status='error', error='interrupted: the viz restarted' "
                        "WHERE status='running'")
            # an approval cut off by a restart: offer it again (re-applying an add confirms the entry)
            con.execute("UPDATE proposals SET status='pending' WHERE status='applying'")
            con.commit()
            self._con = con
        return self._con

    def list(self) -> dict:
        convs = []
        with self._lock:
            if self._open(create=False) is not None:
                rows = self._con.execute(
                    "SELECT c.id, c.title, c.t_updated, (SELECT count(*) FROM messages m WHERE m.conv=c.id) "
                    "FROM conversations c ORDER BY c.t_updated DESC, c.id DESC LIMIT ?", (LIST_MAX,)).fetchall()
                convs = [{"id": i, "title": t, "t_updated": tu, "messages": n, "running": i in self._live}
                         for i, t, tu, n in rows]
        return {"conversations": convs, "available": self.available, "model": self.model,
                "thinking": self.thinking}

    def get(self, conv_id: int) -> dict | None:
        with self._lock:
            if self._open(create=False) is None:
                return None
            c = self._con.execute("SELECT id, title, t_created, t_updated FROM conversations WHERE id=?",
                                  (conv_id,)).fetchone()
            if c is None:
                return None
            rows = self._con.execute(f"SELECT {', '.join(MSG_COLS)} FROM messages WHERE conv=? ORDER BY id",
                                     (conv_id,)).fetchall()
            run = self._live.get(conv_id)
            props = {}
            for pid, msg, t, params, before, pstatus, td, result, perr in self._con.execute(
                    "SELECT id, msg, t, params, before, status, t_decided, result, error FROM proposals "
                    "WHERE conv=? ORDER BY id", (conv_id,)):
                props.setdefault(msg, []).append({
                    "id": pid, "t": t, "params": json.loads(params), "before": json.loads(before),
                    "status": pstatus, "t_decided": td, "result": json.loads(result) if result else None,
                    "error": perr})
            msgs = []
            for r in rows:
                m = dict(zip(MSG_COLS, r))
                m["steps"] = json.loads(m["steps"] or "[]")
                if run is not None and m["id"] == run.msg_id and m["status"] == "running":
                    m["text"] = run.partial or run.final
                    m["steps"] = [dict(s) for s in run.steps]
                    m["seconds"] = round(time.time() - run.t0, 1)
                    m["cost"] = run.cost
                m["proposals"] = props.get(m["id"], [])
                msgs.append(m)
        return {"conversation": dict(zip(("id", "title", "t_created", "t_updated"), c)), "messages": msgs}

    # ------------------------------------------------------------------ runs
    def ask(self, conv_id, text: str) -> dict:
        text = (text or "").strip()
        if not 1 <= len(text) <= MAX_CHARS:
            raise NystulError(400, f"the question must be 1..{MAX_CHARS} characters")
        if not self.available:
            raise NystulError(503, "omp is not on this computer's PATH")
        now = time.time()
        with self._lock:
            if self._closed:
                raise NystulError(503, "the viz is shutting down")
            con = self._open()
            if conv_id is not None:
                if con.execute("SELECT 1 FROM conversations WHERE id=?", (conv_id,)).fetchone() is None:
                    raise NystulError(404, "no such conversation")
                if conv_id in self._live:
                    raise NystulError(409, "Nystul is still answering")
            if len(self._live) >= MAX_RUNS:
                raise NystulError(409, "Nystul is busy with another question")
            if conv_id is None:
                title = re.sub(r"\s+", " ", text)[:60]
                conv_id = con.execute("INSERT INTO conversations(t_created, t_updated, title) VALUES (?, ?, ?)",
                                      (now, now, title)).lastrowid
            else:
                con.execute("UPDATE conversations SET t_updated=? WHERE id=?", (now, conv_id))
            user_id = con.execute("INSERT INTO messages(conv, t, role, text, status) VALUES (?, ?, 'user', ?, 'done')",
                                  (conv_id, now, text)).lastrowid
            msg_id = con.execute("INSERT INTO messages(conv, t, role, text, status) VALUES (?, ?, 'nystul', '', "
                                 "'running')", (conv_id, now)).lastrowid
            con.commit()
            run = Run(conv_id, user_id, msg_id, text)
            self._live[conv_id] = run
            th = threading.Thread(target=self._run, args=(run,), name=f"nystul-{conv_id}", daemon=True)
            self._threads = [t for t in self._threads if t.is_alive()] + [th]
        th.start()
        return {"conversation": conv_id, "message": msg_id}

    def approve(self, prop_id: int) -> dict:
        """Apply a pending proposal to the memory store (the operator clicked Approve)."""
        with self._lock:
            if self._open(create=False) is None:
                raise NystulError(404, "no such proposal")
            row = self._con.execute("SELECT params, status FROM proposals WHERE id=?", (prop_id,)).fetchone()
            if row is None:
                raise NystulError(404, "no such proposal")
            if row[1] != "pending":
                raise NystulError(409, f"the proposal is {row[1]}")
            if self.apply is None:
                raise NystulError(503, "no memory store to apply it to")
            self._con.execute("UPDATE proposals SET status='applying' WHERE id=?", (prop_id,))
            self._con.commit()
        status, result, error = "failed", None, None
        try:
            result = self.apply(nystul_memory.normalize(json.loads(row[0])))
            status = "applied"
        except LookupError as e:                    # no store: leave it pending
            status, error = "pending", str(e)
        except ValueError as e:                     # KnowledgeError / ProposalError: the store refused
            error = str(e)
        with self._lock:
            if self._con is not None:
                self._con.execute("UPDATE proposals SET status=?, t_decided=?, result=?, error=? WHERE id=?",
                                  (status, None if status == "pending" else time.time(),
                                   None if result is None else json.dumps(result), error, prop_id))
                self._con.commit()
        if status == "pending":
            raise NystulError(503, error)
        if status == "failed":
            raise NystulError(400, error)
        return result

    def cancel(self, conv_id: int) -> bool:
        with self._lock:
            run = self._live.get(conv_id)
            if run is None:
                return False
            run.cancelled = True
            proc = run.proc
        _kill(proc)
        return True

    def close(self) -> None:
        with self._lock:
            self._closed = True
            runs = list(self._live.values())
            threads = list(self._threads)
        for r in runs:
            r.cancelled = True
            _kill(r.proc)
        for t in threads:
            t.join(timeout=5)
        with self._lock:
            if self._con is not None:
                self._con.close()
                self._con = None

    def _prompt(self, run: Run) -> str:
        with self._lock:
            rows = self._con.execute("SELECT id, role, text, status FROM messages WHERE conv=? AND id<? "
                                     "ORDER BY id DESC LIMIT ?",
                                     (run.conv, run.user_id, TRANSCRIPT_MAX_MESSAGES)).fetchall()
            props = {}
            for msg, params, pstatus, result, perr in self._con.execute(
                    "SELECT msg, params, status, result, error FROM proposals WHERE conv=? ORDER BY id", (run.conv,)):
                p = json.loads(params)
                res = json.loads(result) if result else {}
                what = f"{p.get('op')}" + (f" #{p['id']}" if p.get("id") else "") + (
                    f" [{p['topic']}]" if p.get("topic") else "")
                outcome = {"applied": f"approved and applied as #{res.get('id')}",
                           "failed": f"approval failed: {perr}"}.get(pstatus, "awaiting the operator's approval")
                props.setdefault(msg, []).append(f"(Codex proposal {what}: {outcome})")
        lines = []
        for mid, role, text, status in reversed(rows):
            if role == "user":
                lines.append(f"**Operator:** {text}")
            else:
                body = text.strip() or "(no answer)"
                if status == "cancelled":
                    body += "\n(cancelled by the operator)"
                elif status == "error":
                    body += "\n(the answer failed)"
                body += "".join("\n" + x for x in props.get(mid, []))
                lines.append(f"**Nystul:** {body}")
        while lines and sum(len(x) + 2 for x in lines) > TRANSCRIPT_MAX_CHARS:
            lines.pop(0)
        ctx = {}
        if self.context is not None:
            try:
                ctx = self.context() or {}
            except Exception:
                ctx = {}
        out = [_now_header(), f"Viz: {ctx.get('mode') or '?'} {ctx.get('session') or ''}".rstrip(), ""]
        out += ["## Conversation so far", ""]
        out += ["\n\n".join(lines) if lines else "(none: this is the first question)", ""]
        out += ["## Question", "", run.question, ""]
        return "\n".join(out)

    def _run(self, run: Run) -> None:
        path = None
        timer = None
        rc = None
        try:
            fd, path = tempfile.mkstemp(prefix="nystul_", suffix=".md")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(self._prompt(run))
            with open(PROMPT_PATH, encoding="utf-8") as f:
                system = f.read()
            cmd = self.omp_cmd + [
                "-p", "--mode", "json", "--no-session", "--no-extensions", "--no-skills", "--no-rules", "--no-title",
                "--no-lsp", "--auto-approve", "--model", self.model, "--thinking", self.thinking,
                "--max-time", str(RUN_TIMEOUT_S), "-e", EXT_PATH, "--tools", ",".join(TOOL_NAMES),
                "--system-prompt", system, f"@{path}", "Answer the operator's question at the end of the attached file."]
            # cwd = temp dir so no repo context files load; stdin closed or omp waits on it
            proc = subprocess.Popen(cmd, cwd=tempfile.gettempdir(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                    env={**os.environ, **self.env})
            with self._lock:
                run.proc = proc
                cancelled = run.cancelled
            if cancelled:
                _kill(proc)

            def drain():
                for chunk in iter(lambda: proc.stderr.read(512), ""):
                    run.stderr_tail = (run.stderr_tail + chunk)[-STDERR_TAIL:]

            err_th = threading.Thread(target=drain, daemon=True)
            err_th.start()
            timer = threading.Timer(RUN_TIMEOUT_S + 30, _kill, args=(proc,))
            timer.daemon = True
            timer.start()
            for line in proc.stdout:
                parse_event(run, line)
            rc = proc.wait()
            err_th.join(timeout=5)
        except Exception as e:
            run.stderr_tail = (run.stderr_tail + f"\n{type(e).__name__}: {e}")[-STDERR_TAIL:]
        finally:
            if timer is not None:
                timer.cancel()
            if path:
                try:
                    os.remove(path)
                except OSError:
                    pass
            self._finish(run, rc)

    def _finish(self, run: Run, rc) -> None:
        if run.cancelled:
            status, text, error = "cancelled", run.final or run.partial, None
        elif rc == 0 and run.final.strip() and run.stop not in ("error", "aborted"):
            status, text, error = "done", run.final, None
        else:
            status, text = "error", run.partial or run.final
            error = f"exit {rc} stop {run.stop}: {run.stderr_tail.strip()[-300:]}"
        now = time.time()
        props = []
        if status == "done":
            for s in run.steps:
                if s["tool"] != "uo_propose" or s["ok"] is not True:
                    continue
                try:
                    p = nystul_memory.normalize(s["args"])
                except nystul_memory.ProposalError:
                    continue
                before = {}
                if self.lookup is not None:
                    for kid in nystul_memory.targets(p):
                        try:
                            before[str(kid)] = self.lookup(kid)
                        except Exception:
                            before[str(kid)] = None
                props.append((s["args"], before))
        with self._lock:
            try:
                if self._con is not None:
                    self._con.execute(
                        "UPDATE messages SET text=?, status=?, steps=?, model=?, cost=?, tokens_in=?, tokens_out=?, "
                        "seconds=?, error=? WHERE id=?",
                        (text, status, json.dumps(run.steps), run.model, run.cost, run.tokens_in, run.tokens_out,
                         round(now - run.t0, 1), error, run.msg_id))
                    self._con.executemany(
                        "INSERT INTO proposals(conv, msg, t, params, before, status) VALUES (?, ?, ?, ?, ?, 'pending')",
                        [(run.conv, run.msg_id, now, json.dumps(a), json.dumps(b)) for a, b in props])
                    self._con.execute("UPDATE conversations SET t_updated=? WHERE id=?", (now, run.conv))
                    self._con.commit()
            finally:
                self._live.pop(run.conv, None)


if __name__ == "__main__":
    # manual check: python harness/nystul.py "question"
    n = Nystul(os.path.join(tempfile.mkdtemp(), "nystul.db"))
    r = n.ask(None, " ".join(sys.argv[1:]) or "Which tools do you have?")
    while n.get(r["conversation"])["messages"][-1]["status"] == "running":
        time.sleep(1)
    print(json.dumps(n.get(r["conversation"])["messages"][-1], indent=1, ensure_ascii=False))
    n.close()

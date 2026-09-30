"""Overseer control CLI (docs/OVERSEER.md): the AI overseer's hands and ears.

Tasks stay programmatic (loop_lumber.py, errand_bank.py); an AI overseer
supervises them through this CLI. There is no daemon: the SQLite store
(harness/memory.py) is the bus. Runners and task_wrap.py post junctures, the
user posts chat, and `ctl wait` blocks until one of them needs the overseer.

Usage: python harness/ctl.py [--db P] [--state-port N] [--control-port N] <cmd> ...
  status                      proxy snapshot + running tasks + open junctures
  run <task> [args...]        start a whitelisted task (lumber, bank) detached
  stop [task_id]              stop the running task (-> task_failed juncture)
  wait [--timeout S] [--include-info]
                              block until a juncture (>= attention) or user chat
  ack <juncture_id>           close a juncture
  junctures [--open] [--after N] [--limit N]
  chat [--after N] [--limit N] [--role R]
  say <text> | think <text> | note-action <text>
                              chat rows (role overseer; message/thought/action)
  act <name> [args]           one stock action through the proxy control port:
                              walk <dir 0-7> [n] [--run], say <allowlisted>,
                              dclick <serial>, single_click <serial>, open_door,
                              target_cancel
Every call prints exactly one JSON object on stdout; exit 0 iff "ok" is true.
Global options go before the command.

Safety: `act` never sends gump responses (the captcha is always a human's,
ANTICHEAT.md §8.8; §8.13 decoy captcha gumps flag any bot reply) or raw
packets; speech is allowlisted; nothing is sent while a task runs (one
character, no interleaving).
"""
import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import actions  # noqa: E402
import nav  # noqa: E402
import task_wrap as tw  # noqa: E402
from humanize import PROFILES, Human  # noqa: E402
from memory import DEFAULT_DB, Memory  # noqa: E402

HOST = "127.0.0.1"
TASKS = {"lumber": os.path.join(HERE, "loop_lumber.py"),
         "bank": os.path.join(HERE, "errand_bank.py")}
# Tests only: JSON {name: script path} replacing TASKS. Production never sets it.
TEST_TASKS_ENV = "UO_CTL_TEST_TASKS"
LOG_DIR = os.path.join(ROOT, "logs", "tasks")
# In-game speech allowlist (docs/PLAN.md "In-game speech is allowlisted keywords/
# commands only"; LUMBER_LOOP.md adds `room`). No code allowlist existed before
# this; these are the only phrases `act say` sends.
SPEECH_ALLOWLIST = ("bank", "room", "hello")

HEARTBEAT_KEY = "overseer_heartbeat"
JUNCTURE_CURSOR_KEY = "overseer_juncture_cursor"
CHAT_CURSOR_KEY = "overseer_chat_cursor"
SEVERITY_RANK = {"info": 0, "attention": 1, "urgent": 2}
WAIT_MAX_EVENTS = 20
NEARBY_RANGE = 18
NEARBY_MAX = 30
WALK_MAX_STEPS = 20
LAYER_BACKPACK = 0x15
DIR_NAMES = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
# RunUO Notoriety constants (Innocent 1 .. Invulnerable 7) [INFERENCE: not in the
# local ClassicUO tree; the client only switches on the named enum].
NOTORIETY = {1: "innocent", 2: "ally", 3: "attackable", 4: "criminal", 5: "enemy",
             6: "murderer", 7: "invulnerable"}
ACTS = ("walk", "say", "dclick", "single_click", "open_door", "target_cancel")


class CtlError(Exception):
    pass


# ------------------------------------------------------------------- store
def heartbeat(mem: Memory):
    tw.meta_set(mem, HEARTBEAT_KEY, f"{time.time():.2f}")


def running_tasks(mem: Memory) -> list:
    """Live task entries. An entry whose wrapper is gone without reporting
    (crash, reboot) is removed and reported as task_failed once."""
    alive = []
    for e in tw.task_entries(mem):
        if tw.entry_alive(e):
            alive.append(e)
        elif tw.remove_entry(mem, e["task_id"]):
            mem.juncture("ctl", "task_failed",
                         f"{e['task']} ended without a report (its wrapper is gone)"[:300],
                         severity="attention",
                         data={"task_id": e["task_id"], "task": e["task"], "args": e.get("args", []),
                               "exit_code": None, "log": e.get("log"), "tail": tw.read_tail(e.get("log", ""))})
    return alive


def task_whitelist() -> dict:
    raw = os.environ.get(TEST_TASKS_ENV)
    return json.loads(raw) if raw else dict(TASKS)


def open_juncture_count(mem: Memory) -> int:
    return mem.con.execute("SELECT COUNT(*) FROM junctures WHERE acked_t IS NULL").fetchone()[0]


# ------------------------------------------------------------------- proxy
def state_query(port: int, timeout: float = 5.0) -> dict:
    """One state-port snapshot without the event backlog."""
    with socket.create_connection((HOST, port), timeout=timeout) as s:
        s.sendall((json.dumps({"op": "state", "since": 1 << 62}) + "\n").encode())
        resp = json.loads(s.makefile("rb").readline() or b"{}")
    return resp


class Control:
    """Proxy control port, framed like agent_link.Link.send (u16be length + packet;
    reply u16be length + text). Gate refusals are returned, never waited out."""

    def __init__(self, port: int):
        self.sock = socket.create_connection((HOST, port), timeout=10)

    def send(self, pkt: bytes) -> str:
        self.sock.sendall(len(pkt).to_bytes(2, "big") + pkt)
        n = int.from_bytes(self._recv(2), "big")
        return self._recv(n).decode()

    def _recv(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise CtlError("proxy closed the control connection")
            buf += chunk
        return buf

    def close(self):
        self.sock.close()


class StateConn:
    """Persistent state-port connection for step outcomes."""

    def __init__(self, port: int):
        self.sock = socket.create_connection((HOST, port), timeout=10)
        self.f = self.sock.makefile("rb")

    def state(self) -> dict:
        self.sock.sendall((json.dumps({"op": "state", "since": 1 << 62}) + "\n").encode())
        resp = json.loads(self.f.readline() or b"{}")
        if not resp.get("ok"):
            raise CtlError(f"state port: {resp.get('error')}")
        return resp

    def close(self):
        self.sock.close()


def _serial(v) -> int:
    return int(v, 16) if isinstance(v, str) else int(v)


def summarize(resp: dict) -> dict:
    mv = resp.get("movement") or {}
    world = resp.get("world") or {}
    me = world.get("self") or {}
    pos = mv.get("pos")
    self_serial = mv.get("self_serial")
    mobiles = []
    for key, m in (world.get("mobiles") or {}).items():
        s = _serial(key)
        if s == self_serial or m.get("x") is None:
            continue
        dist = nav.chebyshev((m["x"], m["y"]), (pos[0], pos[1])) if pos else None
        if dist is not None and dist > NEARBY_RANGE:
            continue
        mobiles.append({"serial": f"0x{s:08X}", "name": m.get("name"), "graphic": m.get("graphic"),
                        "notoriety": m.get("notoriety"), "notoriety_name": NOTORIETY.get(m.get("notoriety")),
                        "hits": m.get("hits"), "hits_max": m.get("hits_max"),
                        "x": m["x"], "y": m["y"], "z": m.get("z"), "dist": dist})
    mobiles.sort(key=lambda m: (m["dist"] is None, m["dist"] or 0))
    items = world.get("items") or {}
    pack = next((_serial(k) for k, it in items.items()
                 if it.get("layer") == LAYER_BACKPACK and it.get("container") is not None
                 and _serial(it["container"]) == self_serial), None)
    counts = {}
    if pack is not None:
        parent = {_serial(k): (_serial(it["container"]) if it.get("container") else None)
                  for k, it in items.items()}
        for k, it in items.items():
            c, depth = parent[_serial(k)], 0
            while c is not None and c != pack and depth < 8:
                c, depth = parent.get(c), depth + 1
            if c == pack and it.get("graphic") is not None:
                g = counts.setdefault(f"0x{it['graphic']:04X}", {"stacks": 0, "amount": 0})
                g["stacks"] += 1
                g["amount"] += it.get("amount") or 1
    gumps = [{"serial": g.get("serial"), "gump_id": g.get("gump_id")}
             for g in world.get("gumps") or [] if g.get("open")]
    hp = lambda a, b: None if me.get(a) is None else [me.get(a), me.get(b)]  # noqa: E731
    return {
        "name": me.get("name"), "serial": me.get("serial"),
        "pos": pos, "facet": me.get("map"),
        "hits": hp("hits", "hits_max"), "stam": hp("stam", "stam_max"), "mana": hp("mana", "mana_max"),
        "weight": me.get("weight"), "gold": me.get("gold"), "warmode": me.get("warmode"),
        "movement": {k: mv.get(k) for k in ("inflight", "stalled", "resync_pending", "client_stale")},
        "gate": resp.get("gate"),
        "intent": resp.get("intent"), "intents": (resp.get("intents") or [])[-5:],
        "mobiles": mobiles[:NEARBY_MAX],
        "backpack": {"serial": None if pack is None else f"0x{pack:08X}", "counts": counts},
        "target": world.get("target"),
        "gumps_open": gumps,
    }


# ------------------------------------------------------------------ commands
def cmd_status(a, mem):
    hb = tw.meta_get(mem, HEARTBEAT_KEY)
    out = {"tasks": running_tasks(mem), "open_junctures": open_juncture_count(mem),
           "overseer_heartbeat": None if hb is None else float(hb)}
    try:
        resp = state_query(a.state_port)
    except (OSError, ValueError) as e:
        return {"ok": False, "error": f"proxy state port {a.state_port} unreachable: {e}", **out}
    if not resp.get("ok"):
        return {"ok": False, "error": f"state port: {resp.get('error')}", "gate": resp.get("gate"), **out}
    return {"ok": True, **summarize(resp), **out}


def cmd_run(a, mem):
    heartbeat(mem)
    tasks = task_whitelist()
    if a.task not in tasks:
        raise CtlError(f"unknown task {a.task!r}; allowed: {sorted(tasks)}")
    alive = running_tasks(mem)
    if alive:
        raise CtlError(f"task {alive[0]['task_id']} is running; one task at a time (stop it first)")
    task_id = f"{a.task}-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
    log = os.path.join(a.log_dir, f"{task_id}.log")
    user_args = list(a.args)
    argv = list(user_args)
    for flag, val in (("--control-port", a.control_port), ("--state-port", a.state_port), ("--memory", a.db)):
        if flag not in argv:
            argv += [flag, str(val)]
    entry = {"task_id": task_id, "task": a.task, "args": user_args, "pid": None,
             "started": time.time(), "log": log}

    def add(cur):
        live = [t for t in cur if tw.entry_alive(t)]
        if live:
            return cur, live[0]["task_id"]
        return cur + [entry], None
    busy = tw.meta_update_json(mem, tw.TASKS_KEY, add, [])
    if busy:
        raise CtlError(f"task {busy} is running; one task at a time (stop it first)")
    os.makedirs(a.log_dir, exist_ok=True)
    spec = {"task_id": task_id, "task": a.task, "script": os.path.abspath(tasks[a.task]),
            "args": user_args, "argv": argv, "log": log, "db": os.path.abspath(a.db)}
    kw = {"creationflags": tw.DETACHED_PROCESS | tw.CREATE_NEW_PROCESS_GROUP} if tw.WINDOWS \
        else {"start_new_session": True}
    try:
        p = subprocess.Popen([sys.executable, os.path.join(HERE, "task_wrap.py"), "--spec", json.dumps(spec)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             cwd=ROOT, close_fds=True, **kw)
    except OSError as e:
        tw.remove_entry(mem, task_id)
        raise CtlError(f"could not start the task wrapper: {e}")
    tw.patch_entry(mem, task_id, pid=p.pid, pid_created=tw.proc_created(p.pid))
    mem.chat_post("overseer", f"run {a.task} {' '.join(user_args)}".strip() + f" ({task_id})", "action",
                  data={"cmd": "run", "task_id": task_id, "task": a.task, "args": user_args})
    return {"ok": True, "task_id": task_id, "task": a.task, "args": user_args, "pid": p.pid, "log": log}


def _task_juncture(mem, task_id):
    rows = mem.con.execute("SELECT id FROM junctures WHERE kind IN ('task_done','task_failed') "
                           "ORDER BY id DESC LIMIT 50").fetchall()
    for (jid,) in rows:
        j = mem.junctures(after_id=jid - 1, limit=1)[0]
        if j["data"].get("task_id") == task_id:
            return j
    return None


def cmd_stop(a, mem):
    heartbeat(mem)
    alive = running_tasks(mem)
    if a.task_id:
        alive = [t for t in alive if t["task_id"] == a.task_id]
    if not alive:
        raise CtlError("no running task" + (f" {a.task_id}" if a.task_id else ""))
    e = alive[0]
    tid = e["task_id"]
    tw.meta_set(mem, tw.STOP_KEY, json.dumps({"task_id": tid, "t": time.time()}))
    mem.chat_post("overseer", f"stop {tid}", "action", data={"cmd": "stop", "task_id": tid})
    end = time.monotonic() + a.grace
    while time.monotonic() < end:
        if not any(t["task_id"] == tid for t in tw.task_entries(mem)):
            return {"ok": True, "task_id": tid, "forced": False, "juncture": _task_juncture(mem, tid)}
        time.sleep(0.2)
    # the wrapper didn't finish in time: end both processes ourselves
    cur = next((t for t in tw.task_entries(mem) if t["task_id"] == tid), e)
    tw.terminate(cur.get("child_pid"), cur.get("child_created"))
    tw.terminate(cur.get("pid"), cur.get("pid_created"))
    if tw.remove_entry(mem, tid):
        tw.meta_set(mem, tw.STOP_KEY, None)
        tw.post_end(mem, cur, -1, True, source="ctl", note=" (forced)")
    return {"ok": True, "task_id": tid, "forced": True, "juncture": _task_juncture(mem, tid)}


def _open_junctures(mem, after_id, min_rank, limit):
    sev = [s for s, r in SEVERITY_RANK.items() if r >= min_rank]
    q = ("SELECT id FROM junctures WHERE id > ? AND acked_t IS NULL AND severity IN (%s) "
         "ORDER BY id LIMIT ?" % ",".join("?" * len(sev)))
    ids = [r[0] for r in mem.con.execute(q, (after_id, *sev, limit))]
    return [mem.junctures(after_id=i - 1, limit=1)[0] for i in ids]


def cmd_wait(a, mem):
    jcur = int(tw.meta_get(mem, JUNCTURE_CURSOR_KEY, "0"))
    ccur = int(tw.meta_get(mem, CHAT_CURSOR_KEY, "0"))
    min_rank = 0 if a.include_info else SEVERITY_RANK["attention"]
    end = None if a.timeout <= 0 else time.monotonic() + a.timeout
    while True:
        heartbeat(mem)
        js = _open_junctures(mem, jcur, min_rank, WAIT_MAX_EVENTS)
        cs = mem.chat(after_id=ccur, limit=WAIT_MAX_EVENTS, role="user")
        if js or cs:
            if js:
                jcur = js[-1]["id"]
                tw.meta_set(mem, JUNCTURE_CURSOR_KEY, str(jcur))
            if cs:
                ccur = cs[-1]["id"]
                tw.meta_set(mem, CHAT_CURSOR_KEY, str(ccur))
            events = sorted([{"type": "juncture", **j} for j in js] + [{"type": "chat", **c} for c in cs],
                            key=lambda ev: ev["t"])
            return {"ok": True, "event": events[0], "events": events,
                    "cursors": {"juncture": jcur, "chat": ccur}}
        if end is not None and time.monotonic() >= end:
            return {"ok": True, "event": None, "cursors": {"juncture": jcur, "chat": ccur}}
        time.sleep(a.poll)


def cmd_ack(a, mem):
    heartbeat(mem)
    ok = mem.juncture_ack(a.juncture_id)
    return {"ok": ok, "id": a.juncture_id, **({} if ok else {"error": "no such open juncture"})}


def cmd_junctures(a, mem):
    return {"ok": True, "junctures": mem.junctures(after_id=a.after, open_only=a.open, limit=a.limit)}


def cmd_chat(a, mem):
    return {"ok": True, "chat": mem.chat(after_id=a.after, limit=a.limit, role=a.role)}


def _post(kind):
    def cmd(a, mem):
        heartbeat(mem)
        text = " ".join(a.text).strip()
        if not text:
            raise CtlError("empty text")
        return {"ok": True, "id": mem.chat_post("overseer", text, kind), "kind": kind}
    return cmd


# ---------------------------------------------------------------------- act
def _parse_serial(s: str) -> int:
    try:
        v = int(s, 0)
    except ValueError:
        raise CtlError(f"bad serial {s!r} (hex 0x... or decimal)")
    if not 0 < v <= 0xFFFFFFFF:
        raise CtlError(f"serial {s!r} out of range")
    return v


def _act_walk(a, ctl: Control, stc: StateConn) -> dict:
    if not 1 <= len(a.args) <= 2:
        raise CtlError("walk <dir 0-7> [n]")
    try:
        d = int(a.args[0])
        n = int(a.args[1]) if len(a.args) > 1 else 1
    except ValueError:
        raise CtlError("walk <dir 0-7> [n]: integers")
    if not 0 <= d <= 7:
        raise CtlError(f"direction {d} out of range 0-7")
    if not 1 <= n <= WALK_MAX_STEPS:
        raise CtlError(f"steps {n} out of range 1-{WALK_MAX_STEPS}")
    human = Human(a.human, seed=a.seed)
    pos = stc.state()["movement"].get("pos")
    if pos is None:
        raise CtlError("player position unknown")
    start, moved, outcomes, stop = list(pos), 0, [], None
    pkt = actions.walk(d, run=a.run)
    for _ in range(n + 2):          # a turn costs one extra send
        before = pos
        for _ in range(40):          # proxy pacing / resync gates, like Mover.step
            resp = ctl.send(pkt)
            if not resp.startswith(("ERR walk gated: pacing", "ERR walk gated: awaiting")):
                break
            time.sleep(0.1)
        if resp != "OK":
            stop = resp
            break
        end = time.monotonic() + 3.0
        while True:
            st = stc.state()
            if st["movement"].get("inflight", 0) == 0 or time.monotonic() > end:
                break
            time.sleep(0.05)
        pos = st["movement"].get("pos") or before
        if list(pos[:2]) != list(before[:2]):
            moved += 1
            outcomes.append("moved")
        elif len(pos) > 3 and len(before) > 3 and pos[3] != before[3]:
            outcomes.append("turned")
        else:
            outcomes.append("blocked")
            stop = "blocked"
            break
        if moved >= n:
            break
        time.sleep(human.step_delay(a.run))
        human.after_step()
    return {"ok": moved == n, "dir": d, "dir_name": DIR_NAMES[d], "steps": n, "moved": moved,
            "outcomes": outcomes, "from": start, "to": pos, "stopped": stop}


def cmd_act(a, mem):
    heartbeat(mem)
    desc = f"act {a.name} {' '.join(a.args)}".strip()
    try:
        out = _act(a, mem)
    except CtlError as e:
        mem.chat_post("overseer", f"{desc}: refused: {e}", "action",
                      data={"cmd": "act", "act": a.name, "args": a.args, "ok": False, "error": str(e)})
        raise
    note = (f"moved {out['moved']}/{out['steps']} {out['dir_name']}"
            + (f" (stopped: {out['stopped']})" if out.get("stopped") else "")) if a.name == "walk" \
        else out.get("reply", "")
    mem.chat_post("overseer", f"{desc}: {note}", "action",
                  data={"cmd": "act", "act": a.name, "args": a.args, **out})
    return {"act": a.name, **out}


def _act(a, mem) -> dict:
    alive = running_tasks(mem)
    if alive:
        raise CtlError(f"task {alive[0]['task_id']} is running; no interleaved actions (stop it first)")
    pkt = None
    if a.name == "say":
        text = " ".join(a.args).strip().lower()
        if text not in SPEECH_ALLOWLIST:
            raise CtlError(f"speech not allowlisted: {' '.join(a.args)!r}; allowed: {list(SPEECH_ALLOWLIST)}")
        pkt = actions.say_unicode(text)
    elif a.name in ("dclick", "single_click"):
        if len(a.args) != 1:
            raise CtlError(f"{a.name} <serial>")
        s = _parse_serial(a.args[0])
        pkt = actions.dclick(s) if a.name == "dclick" else actions.single_click(s)
    elif a.name == "open_door":
        if a.args:
            raise CtlError("open_door takes no arguments")
        pkt = actions.open_door()
    elif a.name == "target_cancel":
        if a.args:
            raise CtlError("target_cancel takes no arguments")
    elif a.name != "walk":
        raise CtlError(f"unknown act {a.name!r}; allowed: {list(ACTS)}")
    try:
        ctl = Control(a.control_port)
    except OSError as e:
        raise CtlError(f"proxy control port {a.control_port} unreachable: {e}")
    stc = None
    try:
        if a.name in ("walk", "target_cancel"):
            try:
                stc = StateConn(a.state_port)
            except OSError as e:
                raise CtlError(f"proxy state port {a.state_port} unreachable: {e}")
        if a.name == "walk":
            return _act_walk(a, ctl, stc)
        if a.name == "target_cancel":
            cur = (stc.state().get("world") or {}).get("target") or {}
            if not cur.get("active") or cur.get("cursor_id") is None:
                raise CtlError("no target cursor is up")
            pkt = actions.target_cancel(cur["cursor_id"], cur.get("target_type") or 0,
                                        cur.get("cursor_type") or 0)
        resp = ctl.send(pkt)
        return {"ok": resp == "OK", "reply": resp}
    finally:
        ctl.close()
        if stc is not None:
            stc.close()


# ---------------------------------------------------------------------- main
class JsonParser(argparse.ArgumentParser):
    """Usage errors come back as the one JSON object too."""

    def error(self, message):
        raise CtlError(f"usage: {message}")


def build_parser() -> argparse.ArgumentParser:
    ap = JsonParser(prog="ctl.py", description="overseer control CLI (docs/OVERSEER.md)")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--state-port", type=int, default=25942)
    ap.add_argument("--control-port", type=int, default=25941)
    ap.add_argument("--log-dir", default=LOG_DIR, help="task logs (default logs/tasks)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    p = sub.add_parser("run")
    p.add_argument("task")
    p.add_argument("args", nargs=argparse.REMAINDER)
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("stop")
    p.add_argument("task_id", nargs="?")
    p.add_argument("--grace", type=float, default=20.0, help="seconds to let the wrapper report")
    p.set_defaults(fn=cmd_stop)
    p = sub.add_parser("wait")
    p.add_argument("--timeout", type=float, default=1800.0, help="seconds; <= 0 waits forever")
    p.add_argument("--include-info", action="store_true")
    p.add_argument("--poll", type=float, default=1.0, help=argparse.SUPPRESS)
    p.set_defaults(fn=cmd_wait)
    p = sub.add_parser("ack")
    p.add_argument("juncture_id", type=int)
    p.set_defaults(fn=cmd_ack)
    p = sub.add_parser("junctures")
    p.add_argument("--open", action="store_true")
    p.add_argument("--after", type=int, default=0)
    p.add_argument("--limit", type=int, default=100)
    p.set_defaults(fn=cmd_junctures)
    p = sub.add_parser("chat")
    p.add_argument("--after", type=int, default=0)
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--role", choices=Memory.CHAT_ROLES)
    p.set_defaults(fn=cmd_chat)
    for name, kind in (("say", "message"), ("think", "thought"), ("note-action", "action")):
        p = sub.add_parser(name)
        p.add_argument("text", nargs="+")
        p.set_defaults(fn=_post(kind))
    p = sub.add_parser("act")
    p.add_argument("name", help=f"one of {', '.join(ACTS)}")
    p.add_argument("args", nargs="*")
    p.add_argument("--run", action="store_true", help="walk: run instead of walk")
    p.add_argument("--human", choices=sorted(PROFILES), default="normal", help="walk pacing profile")
    p.add_argument("--seed", type=int, default=None)
    p.set_defaults(fn=cmd_act)
    return ap


def main(argv=None) -> int:
    try:
        a = build_parser().parse_args(argv)
    except CtlError as e:
        out = {"ok": False, "error": str(e)}
    else:
        mem = Memory(a.db)
        try:
            out = a.fn(a, mem)
        except CtlError as e:
            out = {"ok": False, "error": str(e)}
        finally:
            mem.close()
    print(json.dumps(out, default=str), flush=True)
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())

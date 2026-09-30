"""Tests for harness/ctl.py + harness/task_wrap.py: the overseer bus CLI
(docs/OVERSEER.md).

Covers what the overseer relies on:
  * `wait` wakes on a new open juncture (>= attention) and on user chat, not on
    overseer chat, acked or info junctures (unless --include-info); cursors
    persist in meta; timeout -> event null; the heartbeat advances while waiting
  * `run` (stub tasks through the test-only whitelist override) -> task_done /
    task_failed juncture with exit code, summary and tail; one task at a time;
    `stop` ends it with a stopped task_failed; a vanished wrapper is reported
  * `act`: allowlisted speech only, no actions while a task runs, walk sends
    the framed 0x02 packets and follows the step outcomes, target_cancel uses
    the live cursor, no gump responses
  * `status` against the fake proxy, and ok:false when it's unreachable

Offline: temp DB, stub task scripts in a temp dir, a fake proxy (control +
state ports) on private ports >= 12700. Never touches the live ports.
Run: python harness/test_ctl.py   (~30 s)
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
INSTALL_TILEDATA = "C:/Program Files (x86)/Ultima Online Outlands/artdata.uoo"   # tiledata items; read-only

import actions  # noqa: E402
import ctl  # noqa: E402
import nav  # noqa: E402
import task_wrap as tw  # noqa: E402
from memory import Memory  # noqa: E402

PY = sys.executable
CTL = os.path.join(HERE, "ctl.py")
FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def free_port(start):
    for p in range(start, start + 200):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            return s
        except OSError:
            s.close()
    raise RuntimeError("no free private port")


class FakeProxy:
    """Control port (u16be-framed packets -> "OK") and state port (JSON lines)."""
    SELF = 0x00000001
    PACK = 0x40000010

    def __init__(self):
        self.frames = []          # (declared length, packet bytes)
        self.pos = [100, 100, 0, 2]
        self.target = {"active": False, "target_type": None, "cursor_id": None, "cursor_type": None}
        self.gumps = []           # world.gumps rows
        self.fixed_mobiles = {}   # extra mobiles at fixed positions (goto tests)
        self.ground_items = {}    # extra ground items (goto/map tests)
        self.warmode = False
        self.self_hits = 50
        self.self_noto = 1
        self.gold = 110
        self.buy_list = None      # {"container": int, "items": [{"price", "name"}]} sent on a menu pick
        self.buy_content = None   # [[container, [serials in 0x3C packet order]]] sent just before it
        self.prices = {}          # item serial -> price charged by a 0x3B
        self.intents = []         # intents posted on the state port (op "intent")
        self.buffs = {}           # icon id (str) -> buff record (world.buffs for self)
        self.stats = {}           # extra self stats (0x11 fields)
        self.events = []          # event envelopes; seq = index
        self.lock = threading.Lock()
        cs, ss = free_port(12710), free_port(12910)
        self.control_port, self.state_port = cs.getsockname()[1], ss.getsockname()[1]
        for s, h in ((cs, self._control), (ss, self._state)):
            s.listen(8)
            threading.Thread(target=self._accept, args=(s, h), daemon=True).start()

    def _accept(self, srv, handler):
        while True:
            c, _ = srv.accept()
            threading.Thread(target=handler, args=(c,), daemon=True).start()

    @staticmethod
    def _recv(c, n):
        buf = b""
        while len(buf) < n:
            chunk = c.recv(n - len(buf))
            if not chunk:
                raise EOFError
            buf += chunk
        return buf

    def _control(self, c):
        try:
            while True:
                n = int.from_bytes(self._recv(c, 2), "big")
                pkt = self._recv(c, n)
                with self.lock:
                    self.frames.append((n, pkt))
                    if pkt[0] == 0x02:
                        d = pkt[1] & 7
                        if d != self.pos[3]:
                            self.pos[3] = d                      # a new direction only turns
                        else:
                            self.pos[0], self.pos[1] = nav.step(tuple(self.pos[:2]), d)
                    elif pkt[:5] == bytes.fromhex("bf00090013"):  # context menu request -> menu (mode 2)
                        s = int.from_bytes(pkt[5:9], "big")
                        self.add_event({"ev": "popup", "serial": s, "entries": [
                            {"cliloc": 3006123, "index": 0, "flags": 0}, {"cliloc": 3006103, "index": 1, "flags": 0}]})
                    elif pkt[:5] == bytes.fromhex("bf000b0015") and self.buy_list:   # menu pick -> 0x3C, 0x74
                        if self.buy_content is not None:
                            self.add_event({"ev": "container_content", "count": 0, "containers": self.buy_content})
                        self.add_event({"ev": "buy_list", **self.buy_list})
                    elif pkt[0] == 0x3B:                              # buy: charge the configured price
                        n = len(pkt)
                        for off in range(8, n, 7):
                            s = f"0x{int.from_bytes(pkt[off + 1:off + 5], 'big'):08X}"
                            self.gold -= self.prices.get(s, 0) * int.from_bytes(pkt[off + 5:off + 7], "big")
                    elif pkt[0] == 0x72:
                        self.warmode = bool(pkt[1])
                    elif pkt[0] == 0x08 and len(pkt) == 22:          # drop into a container
                        key = f"0x{int.from_bytes(pkt[1:5], 'big'):08X}"
                        it = self.ground_items.get(key)
                        if it is not None and it.get("graphic") == 0x0EED:
                            del self.ground_items[key]                # gold merges into the pack's pile
                        elif it is not None:
                            it.update(container=f"0x{int.from_bytes(pkt[18:22], 'big'):08X}", layer=None)
                    elif pkt[0] == 0x13:                              # equip request
                        it = self.ground_items.get(f"0x{int.from_bytes(pkt[1:5], 'big'):08X}")
                        if it is not None:
                            it.update(container=f"0x{int.from_bytes(pkt[6:10], 'big'):08X}", layer=pkt[5])
                reply = b"OK"
                c.sendall(len(reply).to_bytes(2, "big") + reply)
        except (EOFError, OSError):
            c.close()

    def snapshot(self):
        p = f"0x{self.PACK:08X}"
        with self.lock:
            return {
                "ok": True,
                "movement": {"pos": list(self.pos), "self_serial": self.SELF, "inflight": 0,
                             "stalled": False, "resync_pending": False, "client_stale": False},
                "world": {
                    "self": {"serial": "0x00000001", "name": "TestWorth", "hits": self.self_hits, "hits_max": 60,
                             "stam": 40, "stam_max": 45, "mana": 20, "mana_max": 25, "weight": 123, "map": 0,
                             "warmode": self.warmode, "notoriety": self.self_noto, "gold": self.gold,
                             "body": 0x190, "skill_names": [],
                             "skills": {"25": {"value": 600, "base": 600, "lock": 0, "cap": 1000},
                                        "17": {"value": 0, "base": 0, "lock": 0, "cap": 1000}},
                             "stats": dict(self.stats)},
                    "mobiles": {"0x00000001": {"x": self.pos[0], "y": self.pos[1], "notoriety": 1},
                                "0x00000002": {"x": self.pos[0] + 3, "y": self.pos[1], "name": "a PK",
                                               "notoriety": 6, "graphic": 400},
                                "0x00000003": {"x": self.pos[0] + 50, "y": self.pos[1], "name": "far"},
                                **self.fixed_mobiles},
                    "items": {p: {"graphic": 0x0E75, "layer": 0x15, "container": "0x00000001"},
                              "0x40000011": {"graphic": 0x1BDD, "amount": 5, "container": p},
                              "0x40000012": {"graphic": 0x0E76, "container": p},
                              "0x40000013": {"graphic": 0x1BD7, "amount": 10, "container": "0x40000012"},
                              "0x40000014": {"graphic": 0x1BD7, "amount": 99, "x": 1, "y": 1},
                              **self.ground_items},
                    "target": dict(self.target), "gumps": list(self.gumps),
                    "buffs": {"0x00000001": dict(self.buffs)}},
                "events": [], "next": len(self.events),
                "gate": {"state": "open"},
                "intent": {"text": "idle"}, "intents": [{"text": f"i{k}"} for k in range(7)],
            }

    def _state(self, c):
        f = c.makefile("rb")
        try:
            for line in f:
                req = json.loads(line)
                if req.get("op") == "intent":
                    with self.lock:
                        self.intents.append(req.get("intent"))
                    resp = {"ok": True}
                elif req.get("op") != "state":
                    resp = {"ok": False, "error": "op"}
                else:
                    resp = self.snapshot()
                    if req.get("snapshot") is False:
                        del resp["world"]
                    since = req.get("since", 0)
                    with self.lock:
                        resp["events"] = [e for e in self.events if e["seq"] >= since]
                c.sendall((json.dumps(resp) + "\n").encode())
        except OSError:
            pass
        finally:
            c.close()

    def take(self):
        with self.lock:
            out, self.frames = self.frames, []
        return out

    def add_event(self, data, origin="world"):
        """Append an event envelope (call with self.lock held or from tests)."""
        self.events.append({"seq": len(self.events), "t": time.time(), "origin": origin, "data": data})


def env_with(tasks=None):
    env = dict(os.environ)
    env.pop(ctl.TEST_TASKS_ENV, None)
    if tasks is not None:
        env[ctl.TEST_TASKS_ENV] = json.dumps(tasks)
    return env


class Ctl:
    def __init__(self, db, logdir, proxy, tasks=None):
        self.base = [PY, CTL, "--db", db, "--log-dir", logdir,
                     "--control-port", str(proxy.control_port), "--state-port", str(proxy.state_port)]
        self.env = env_with(tasks)

    def __call__(self, *args, timeout=60):
        r = subprocess.run(self.base + list(args), capture_output=True, text=True, env=self.env, timeout=timeout)
        lines = r.stdout.strip().splitlines()
        try:
            out = json.loads(lines[-1]) if len(lines) == 1 else {"_bad_stdout": r.stdout, "_stderr": r.stderr}
        except ValueError:
            out = {"_bad_stdout": r.stdout, "_stderr": r.stderr}
        return r.returncode, out

    def spawn(self, *args):
        return subprocess.Popen(self.base + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, env=self.env)


def finish(p, timeout=30):
    out, _ = p.communicate(timeout=timeout)
    return json.loads(out.strip())


def meta(db, key):
    m = Memory(db)
    try:
        return tw.meta_get(m, key)
    finally:
        m.close()


def wait_for(pred, timeout=20.0, poll=0.1):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = pred()
        if v:
            return v
        time.sleep(poll)
    return None


# ----------------------------------------------------------------------- tests
def test_wait(proxy):
    print("== wait ==")
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "harness.db")
    c = Ctl(db, os.path.join(tmp, "logs"), proxy)
    m = Memory(db)
    info_id = m.juncture("lumber", "trip_done", "trip 1 stored 30", severity="info")
    m.chat_post("overseer", "I am watching", "message")
    t0 = time.monotonic()
    code, out = c("wait", "--timeout", "1", "--poll", "0.1")
    check("info juncture + overseer chat do not wake; timeout -> event null",
          code == 0 and out.get("ok") is True and out.get("event") is None, str(out))
    check("timeout honoured", time.monotonic() - t0 < 8, f"{time.monotonic() - t0:.1f}s")
    code, out = c("wait", "--timeout", "1", "--poll", "0.1", "--include-info")
    check("--include-info wakes on the info juncture",
          (out.get("event") or {}).get("id") == info_id and out["event"]["type"] == "juncture", str(out))
    check("juncture cursor persisted", meta(db, ctl.JUNCTURE_CURSOR_KEY) == str(info_id))
    code, out = c("wait", "--timeout", "1", "--poll", "0.1", "--include-info")
    check("same juncture not delivered twice", out.get("event") is None, str(out))

    acked = m.juncture("lumber", "stuck", "stuck at 1,1", severity="attention")
    m.juncture_ack(acked)
    code, out = c("wait", "--timeout", "1", "--poll", "0.1")
    check("acked juncture does not wake", out.get("event") is None, str(out))

    hb0 = time.time()
    p = c.spawn("wait", "--timeout", "20", "--poll", "0.1")
    time.sleep(1.0)
    hb1 = wait_for(lambda: (lambda v: v and float(v) >= hb0 and float(v))(meta(db, ctl.HEARTBEAT_KEY)), 5)
    time.sleep(1.2)
    hb2 = float(meta(db, ctl.HEARTBEAT_KEY) or 0)
    check("heartbeat written while waiting (epoch seconds)", bool(hb1) and abs(float(hb1) - time.time()) < 5,
          str(hb1))
    check("heartbeat advances on every poll", bool(hb1) and hb2 > float(hb1), f"{hb1} -> {hb2}")
    jid = m.juncture("lumber", "threat", "red PK 5 tiles east", severity="urgent", data={"serial": "0x2"})
    t0 = time.monotonic()
    out = finish(p)
    ev = out.get("event") or {}
    check("wakes on a new juncture", ev.get("type") == "juncture" and ev.get("id") == jid
          and ev.get("kind") == "threat" and ev.get("data") == {"serial": "0x2"}, str(out))
    check("woke promptly", time.monotonic() - t0 < 5, f"{time.monotonic() - t0:.1f}s")

    p = c.spawn("wait", "--timeout", "20", "--poll", "0.1")
    time.sleep(0.8)
    m.chat_post("overseer", "noted", "message")
    time.sleep(0.5)
    check("overseer chat does not wake a running wait", p.poll() is None)
    cid = m.chat_post("user", "please go chop on the mainland", "message")
    out = finish(p)
    ev = out.get("event") or {}
    check("wakes on a user chat message", ev.get("type") == "chat" and ev.get("id") == cid
          and ev.get("role") == "user", str(out))
    check("chat cursor persisted", meta(db, ctl.CHAT_CURSOR_KEY) == str(cid))
    check("juncture cursor persisted", meta(db, ctl.JUNCTURE_CURSOR_KEY) == str(jid))

    j2 = m.juncture("lumber", "stuck", "a", severity="attention")
    c2 = m.chat_post("user", "b")
    code, out = c("wait", "--timeout", "1", "--poll", "0.1")
    check("everything pending comes back in one wake",
          [(e["type"], e["id"]) for e in out.get("events", [])] == [("juncture", j2), ("chat", c2)], str(out))

    before = time.time()
    code, out = c("say", "all good")
    rows = m.chat(after_id=0, role="overseer")
    check("say -> overseer message row", code == 0 and rows[-1]["text"] == "all good"
          and rows[-1]["kind"] == "message", str(rows[-1:]))
    c("think", "the PK left; resuming")
    c("note-action", "moved 3 tiles west")
    kinds = [r["kind"] for r in m.chat(after_id=0, role="overseer")][-2:]
    check("think/note-action kinds", kinds == ["thought", "action"], str(kinds))
    check("say updates the heartbeat", float(meta(db, ctl.HEARTBEAT_KEY)) >= before - 0.01)
    code, out = c("ack", str(j2))
    check("ack closes a juncture", code == 0 and out["ok"] and m.junctures(after_id=j2 - 1)[0]["acked_t"])
    code, out = c("ack", str(j2))
    check("ack twice -> ok false", code == 1 and out["ok"] is False)
    code, out = c("junctures", "--open")
    check("junctures --open lists only open", all(j["acked_t"] is None for j in out["junctures"])
          and jid in [j["id"] for j in out["junctures"]], str(out)[:200])
    code, out = c("chat", "--after", str(cid))
    check("chat --after", [r["id"] for r in out["chat"]][0] > cid, str(out)[:200])
    code, out = c("frobnicate")
    check("usage error is one JSON object too", code == 1 and out.get("ok") is False
          and "usage" in out.get("error", ""), str(out))
    m.close()


def test_status(proxy):
    print("== status ==")
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "harness.db")
    c = Ctl(db, os.path.join(tmp, "logs"), proxy)
    code, out = c("status")
    mob = out.get("mobiles", [])
    check("status ok", code == 0 and out.get("ok"), str(out)[:300])
    check("vitals/weight/facet", out.get("hits") == [50, 60] and out.get("weight") == 123
          and out.get("facet") == 0 and out.get("pos") == proxy.pos, str(out)[:300])
    check("nearby mobiles: self and far ones excluded, distance + notoriety",
          [(x["serial"], x["dist"], x["notoriety_name"]) for x in mob] == [("0x00000002", 3, "murderer")], str(mob))
    check("backpack counts include nested bags, not ground items",
          out["backpack"]["counts"].get("0x1BDD") == {"stacks": 1, "amount": 5}
          and out["backpack"]["counts"].get("0x1BD7") == {"stacks": 1, "amount": 10}, str(out["backpack"]))
    check("equipment lists worn items by layer name (the backpack is worn, pack contents aren't)",
          set(out["equipment"]) == {"backpack"} and out["equipment"]["backpack"]["graphic"] == "0x0E75",
          str(out["equipment"]))
    check("last 5 intents", [i["text"] for i in out["intents"]] == ["i2", "i3", "i4", "i5", "i6"])
    check("tasks + open juncture count", out["tasks"] == [] and out["open_junctures"] == 0)
    proxy.stats = {"str": 80, "dex": 21, "int": 72, "stats_cap": 225, "luck": 0, "physical_resist": 9,
                   "fire_resist": 1, "damage_min": 17, "damage_max": 32, "followers": 0, "followers_max": 5}
    proxy.buffs = {"277": {"icon_id": 277, "f1": 4620, "f2": 1, "f3": 0, "f4": 0,
                           "timers": [{"seconds": 5.0, "end": 0}], "title": "Stationary Penalty",
                           "description": "Move {value} more steps", "category": 0, "mode": 1, "scalar": 0.0},
                   "140": {"icon_id": 140, "f2": 2, "timers": [], "title": "", "cliloc": 1075655}}
    code, out = c("status")
    check("status.stats: Str/Dex/Int, cap, luck, resists, damage, followers",
          out.get("stats") == {"str": 80, "dex": 21, "int": 72, "stats_cap": 225, "luck": 0,
                               "resists": {"physical": 9, "fire": 1}, "damage": [17, 32], "followers": [0, 5]},
          str(out.get("stats")))
    b = out.get("buffs") or []
    check("status.buffs: your buffs by icon, titles from text or cliloc, with the raw numbers",
          [x["icon"] for x in b] == [140, 277] and b[1]["title"] == "Stationary Penalty"
          and b[1]["timers_s"] == [5.0] and b[1]["raw"]["f2"] == 1 and b[0]["title"], str(b))
    proxy.stats, proxy.buffs = {}, {}
    dead = free_port(13100)
    port = dead.getsockname()[1]
    dead.close()
    r = subprocess.run([PY, CTL, "--db", db, "--state-port", str(port), "status"],
                       capture_output=True, text=True, env=env_with(), timeout=30)
    out = json.loads(r.stdout)
    check("proxy unreachable -> ok false", r.returncode == 1 and out["ok"] is False
          and "unreachable" in out["error"] and out["tasks"] == [], str(out))


STUBS = {
    "ok": "import sys\nprint('stub start', sys.argv[1:3])\nprint('stored 30 boards')\n",
    "fail": "import sys\nprint('working')\nprint('12:00:00 ABORTED: stub abort')\nsys.exit(1)\n",
    "slow": "import time\nprint('slow start', flush=True)\nfor _ in range(600):\n    time.sleep(0.1)\n",
}


def task_juncture(db, task_id, timeout=20):
    def find():
        m = Memory(db)
        try:
            return next((j for j in m.junctures() if j["data"].get("task_id") == task_id), None)
        finally:
            m.close()
    return wait_for(find, timeout)


def test_run_act(proxy):
    print("== run / stop ==")
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "harness.db")
    logdir = os.path.join(tmp, "logs")
    tasks = {}
    for name, body in STUBS.items():
        path = os.path.join(tmp, f"stub_{name}.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        tasks[name] = path
    c = Ctl(db, logdir, proxy, tasks=tasks)
    prod = Ctl(db, logdir, proxy)

    code, out = prod("run", "evil")
    check("unknown task refused", code == 1 and "unknown task" in out.get("error", ""), str(out))
    code, out = c("run", "lumber")
    check("the test override replaces the whitelist", code == 1 and "unknown task" in out.get("error", ""), str(out))

    code, out = c("run", "ok", "a", "b")
    check("run ok -> started", code == 0 and out.get("ok") and out.get("args") == ["a", "b"], str(out))
    j = task_juncture(db, out.get("task_id"))
    check("task_done juncture, info", j is not None and j["kind"] == "task_done" and j["severity"] == "info", str(j))
    if j:
        d = j["data"]
        check("data: exit code, task, args, log", d["exit_code"] == 0 and d["task"] == "ok"
              and d["args"] == ["a", "b"] and d["log"] == out["log"] and os.path.exists(d["log"]), str(d))
        check("tail has the task output", any("stored 30 boards" in ln for ln in d["tail"]), str(d["tail"]))
        check("the task gets the user args first", any("['a', 'b']" in ln for ln in d["tail"]), str(d["tail"]))
    check("finished task leaves the running list", wait_for(lambda: meta(db, tw.TASKS_KEY) == "[]", 5) is not None,
          str(meta(db, tw.TASKS_KEY)))
    if j:
        code, w = c("wait", "--timeout", "3", "--poll", "0.1")
        check("a finished task wakes a plain `wait` (no --include-info; live 2026-09-30 regression)",
              (w.get("event") or {}).get("id") == j["id"], str(w))

    code, out = c("run", "fail")
    j = task_juncture(db, out.get("task_id"))
    check("task_failed juncture, attention", j is not None and j["kind"] == "task_failed"
          and j["severity"] == "attention" and j["data"]["exit_code"] == 1, str(j))
    check("summary is the runner's ABORTED line", j is not None and "ABORTED: stub abort" in j["summary"],
          j and j["summary"])

    code, out = c("run", "slow")
    slow_id = out.get("task_id")
    check("slow task started", code == 0 and out.get("ok"), str(out))
    wait_for(lambda: any(e.get("child_pid") for e in json.loads(meta(db, tw.TASKS_KEY) or "[]")), 10)
    code, out = c("run", "ok")
    check("second concurrent run refused", code == 1 and "is running" in out.get("error", ""), str(out))
    code, out = c("status")
    check("status lists the running task", [t["task_id"] for t in out.get("tasks", [])] == [slow_id], str(out)[:300])

    print("== act ==")
    proxy.take()
    code, out = c("act", "walk", "2", "1", "--human", "off")
    check("act refused while a task runs", code == 1 and "running" in out.get("error", ""), str(out))
    check("nothing sent while a task runs", proxy.take() == [])

    t0 = time.time()
    code, out = c("stop")
    j = out.get("juncture") or {}
    check("stop ok", code == 0 and out.get("ok") and out.get("task_id") == slow_id and not out.get("forced"), str(out))
    check("stop -> task_failed juncture marked stopped", j.get("kind") == "task_failed"
          and j["data"].get("stopped") is True and "stopped by the overseer" in j.get("summary", ""), str(j))
    check("stopped promptly", time.time() - t0 < 15, f"{time.time() - t0:.1f}s")
    check("no running task after stop", meta(db, tw.TASKS_KEY) == "[]" and meta(db, tw.STOP_KEY) is None)
    code, out = c("stop")
    check("stop with nothing running -> ok false", code == 1 and out["ok"] is False)

    # a wrapper that died without reporting (crash, reboot) must not lock the character out
    gone = subprocess.Popen([PY, "-c", "pass"])
    gone.wait()
    m = Memory(db)
    tw.meta_set(m, tw.TASKS_KEY, json.dumps([{"task_id": "ghost-1", "task": "lumber", "args": [], "pid": gone.pid,
                                              "pid_created": time.time() - 3, "started": time.time() - 3,
                                              "log": os.path.join(logdir, "ghost-1.log")}]))
    m.close()
    code, out = c("run", "ok")
    check("run proceeds past a dead wrapper's entry", code == 0 and out.get("ok"), str(out))
    g = task_juncture(db, "ghost-1", 2)
    check("vanished wrapper reported once as task_failed", g is not None and g["kind"] == "task_failed"
          and g["source"] == "ctl", str(g))
    task_juncture(db, out.get("task_id"))
    wait_for(lambda: meta(db, tw.TASKS_KEY) == "[]", 5)

    proxy.take()
    m = Memory(db)
    n_chat = len(m.chat(role="overseer"))
    code, out = c("act", "say", "hello", "there")
    check("say refuses non-allowlisted text", code == 1 and "not allowlisted" in out.get("error", ""), str(out))
    code, out = c("act", "say", "vendor sell all my stuff")
    check("say refuses free text", code == 1, str(out))
    check("refused speech sends nothing", proxy.take() == [])
    rows = m.chat(role="overseer")
    check("refused acts still leave an action row", len(rows) == n_chat + 2
          and all(r["kind"] == "action" and "refused" in r["text"] for r in rows[-2:]), str(rows[-2:]))
    if os.path.exists("C:/Program Files (x86)/Ultima Online Outlands/speech.mul"):
        code, out = c("act", "say", "Bank")
        fr = proxy.take()
        check("allowlisted speech sent as the stock keyword-encoded 0xAD",
              code == 0 and [p for _, p in fr] == [actions.say_unicode("bank")], str(fr))
    else:
        print("  SKIP  say bank (speech.mul not installed)")

    proxy.pos = [100, 100, 0, 2]
    code, out = c("act", "walk", "2", "3", "--human", "off")
    fr = proxy.take()
    check("walk 3 east: moved 3", code == 0 and out.get("moved") == 3 and out.get("to")[:2] == [103, 100], str(out))
    check("walk frames: 3 x 0x02 dir 2, length-prefixed like Link.send",
          fr == [(7, actions.walk(2))] * 3 and actions.walk(2) == bytes.fromhex("02020000000000"), str(fr))
    code, out = c("act", "walk", "4", "1", "--human", "off", "--run")
    fr = proxy.take()
    check("walk with a turn: turn + step, run flag set",
          out.get("outcomes") == ["turned", "moved"] and [p for _, p in fr] == [actions.walk(4, run=True)] * 2,
          str(out))
    rows = m.chat(role="overseer")
    check("every act posts an overseer action row", rows[-1]["kind"] == "action"
          and rows[-1]["text"].startswith("act walk 4 1") and rows[-1]["data"].get("moved") == 1, str(rows[-1]))
    code, out = c("act", "walk", "9")
    check("walk dir out of range refused", code == 1 and proxy.take() == [], str(out))
    code, out = c("act", "walk", "2", "50")
    check("walk step cap", code == 1 and proxy.take() == [], str(out))

    code, out = c("act", "dclick", "0x40000011")
    check("dclick frame", code == 0 and [p for _, p in proxy.take()] == [bytes.fromhex("0640000011")], str(out))
    code, out = c("act", "single_click", "2")
    check("single_click frame", code == 0 and [p for _, p in proxy.take()] == [bytes.fromhex("0900000002")], str(out))
    code, out = c("act", "open_door")
    check("open_door frame", code == 0 and [p for _, p in proxy.take()] == [actions.open_door()], str(out))
    code, out = c("act", "target_cancel")
    check("target_cancel without a cursor refused", code == 1 and proxy.take() == [], str(out))
    proxy.target = {"active": True, "target_type": 0, "cursor_id": 0x1234, "cursor_type": 0}
    code, out = c("act", "target_cancel")
    check("target_cancel uses the live cursor",
          code == 0 and [p for _, p in proxy.take()] == [actions.target_cancel(0x1234, 0, 0)], str(out))
    for bad in (("gump_response", "1", "2", "3"), ("raw", "b10000"), ("dclick", "0")):
        code, out = c("act", *bad)
        check(f"act {bad[0]} {bad[1]} refused", code == 1 and proxy.take() == [], str(out))
    m.close()


def gump_row(serial, gump_id, layout, lines=()):
    return {"serial": f"0x{serial:08X}", "gump_id": f"0x{gump_id:08X}", "layout": layout,
            "lines": list(lines), "open": True}


def test_overseer_acts(proxy):
    print("== journal / menu / gump / goto ==")
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "harness.db")
    c = Ctl(db, os.path.join(tmp, "tasks"), proxy)
    have_cliloc = os.path.exists("C:/Program Files (x86)/Ultima Online Outlands/Cliloc.enu")

    with proxy.lock:
        proxy.add_event({"ev": "speech_heard", "name": "System", "text": "(500 uses remaining)"})
        proxy.add_event({"ev": "cliloc", "name": "System", "cliloc": 500495, "args": ""})
        proxy.add_event({"ev": "step", "from": [1, 1], "to": [1, 2]}, origin="proxy")
        proxy.add_event({"ev": "keepalive"})
    code, out = c("journal", "--n", "10")
    j = out.get("journal", [])
    check("journal: what a player reads (speech, rendered cliloc), no proxy/keepalive noise",
          code == 0 and [r["ev"] for r in j] == ["speech_heard", "cliloc"]
          and j[0]["text"] == "(500 uses remaining)", str(j))
    if have_cliloc:
        check("journal renders cliloc text like the client", "useable wood" in j[1]["text"], j[1]["text"])

    proxy.take()
    code, out = c("act", "menu", "0x000001E5")
    fr = proxy.take()
    check("menu sends the stock popup request", [p for _, p in fr] == [actions.request_popup(0x1E5)], str(fr))
    ents = (out.get("menu") or {}).get("entries", [])
    check("menu returns the server's entries with index", code == 0 and [e["index"] for e in ents] == [0, 1],
          str(out))
    if have_cliloc:
        check("menu entries rendered (Buy)", ents[1]["text"] == "Buy", str(ents))
    code, out = c("act", "menu_pick", "0x000001E5", "1")
    check("menu_pick sends the stock popup selection",
          code == 0 and [p for _, p in proxy.take()] == [actions.popup_selection(0x1E5, 1)], str(out))

    btns = "{ button 10 10 1 2 1 0 1 }{ button 10 30 1 2 1 0 2 }"
    proxy.gumps = [
        gump_row(0x100, 0x00000001, "{ textentrylimited 1 1 40 20 0 2 2 3 }" + btns),        # captcha
        gump_row(0x101, 0x5E11A1, "{ noclose }{ croppedtext -300 -200 1 1 0 0 }", ["Captcha"]),  # decoy
        gump_row(0x102, 0x22, btns, ["You have chosen to renounce your Young player status?"]),
        gump_row(0x103, 0x33, "{ noclose }" + btns, ["Resurrection"]),
    ]
    for serial, button, why in ((0x100, 2, "captcha"), (0x101, 0, "no reply buttons"), (0x102, 1, "renounce"),
                                (0x103, 0, "noclose"), (0x103, 9, "not in the gump's reply buttons"),
                                (0x104, 1, "no open gump")):
        code, out = c("act", "gump", f"0x{serial:X}", str(button))
        check(f"gump refused: {why}", code == 1 and proxy.take() == [], str(out))
    code, out = c("act", "gump", "0x102", "0")
    check("renounce prompt: closing it (button 0) is allowed",
          code == 0 and [p for _, p in proxy.take()] == [actions.gump_response(0x102, 0x22, 0)], str(out))
    code, out = c("act", "gump", "0x103", "1")
    check("a normal gump: an offered button is sent as the stock 0xB1",
          code == 0 and [p for _, p in proxy.take()] == [actions.gump_response(0x103, 0x33, 1)], str(out))
    proxy.gumps = []

    proxy.pos = [100, 100, 0, 2]
    code, out = c("act", "goto", "104", "100", "--human", "off", "--no-map")
    fr = proxy.take()
    check("goto x y: the Mover walks there (2D fallback in this test)",
          code == 0 and out.get("to", [])[:2] == [104, 100] and out.get("steps") == 4, str(out))
    check("goto sends only stock walks", fr and all(p[0] == 0x02 for _, p in fr), str(fr[:3]))
    it = [i for i in proxy.intents if i][-2:]
    check("goto reports 'Walking to' then 'Arrived at' (so the viz doesn't stay on Walking)",
          [(i["kind"], i["text"]) for i in it] == [("goto", "Walking to 104,100"), ("arrived", "Arrived at 104,100")],
          str(it))
    proxy.fixed_mobiles = {"0x00000004": {"x": 110, "y": 100, "z": 0, "name": "Zara", "notoriety": 7}}
    code, out = c("act", "goto", "0x00000004", "--human", "off", "--no-map")
    check("goto mobile: walks until within 2 tiles of it",
          code == 0 and nav.chebyshev(tuple(out["to"][:2]), (110, 100)) <= 2
          and nav.chebyshev(tuple(out["from"][:2]), (110, 100)) > 2, str(out))
    proxy.fixed_mobiles = {}
    proxy.take()
    code, out = c("act", "goto", "0x00000099", "--no-map")
    check("goto unknown serial refused", code == 1 and proxy.take() == [], str(out))
    gate = "0x40000099"
    proxy.ground_items = {gate: {"graphic": 0x0F6C, "x": 113, "y": 100, "z": 0}}
    code, out = c("act", "goto", gate, "--human", "off", "--no-map")
    check("goto ground item: onto its tile", code == 0 and out.get("to", [])[:2] == [113, 100], str(out))
    code, out = c("status")
    g = next((i for i in out.get("ground_items", []) if i["serial"] == gate), None)
    check("status lists nearby ground items, named from tiledata",
          g is not None and g["dist"] == 0 and (g["name"] == "blue moongate" or not os.path.exists(INSTALL_TILEDATA)),
          str(out.get("ground_items")))
    proxy.ground_items = {}
    proxy.take()

    print("== unequip / equip (the hatchet) ==")
    hatchet, pack = 0x44ADB57A, proxy.PACK
    proxy.ground_items = {f"0x{hatchet:08X}": {"graphic": 0x0F43, "container": "0x00000001", "layer": 2}}
    code, out = c("act", "unequip", f"0x{hatchet:08X}", "--human", "off")
    fr = [p for _, p in proxy.take()]
    check("unequip: stock lift + drop into the backpack, item now in the pack",
          code == 0 and out.get("moved") and fr == [actions.lift(hatchet, 1),
                                                    actions.drop(hatchet, ctl.DROP_AUTO, ctl.DROP_AUTO, 0, 0, pack)],
          f"{out} {fr}")
    code, out = c("act", "unequip", f"0x{hatchet:08X}")
    check("unequip refused for an item you don't wear", code == 1 and proxy.take() == [], str(out))
    code, out = c("act", "equip", f"0x{hatchet:08X}", "--human", "off")
    fr = [p for _, p in proxy.take()]
    layer = uomap_layer(0x0F43)
    check("equip: stock lift + 0x13 on the tiledata layer, item worn again",
          code == 0 and out.get("moved") and layer == 2
          and fr == [actions.lift(hatchet, 1), actions.equip_request(hatchet, 2, proxy.SELF)], f"{out} {fr}")
    code, out = c("act", "unequip", f"0x{pack:08X}")
    check("the backpack itself can't be unequipped", code == 1 and proxy.take() == [], str(out))
    proxy.ground_items = {}


def test_combat(proxy):
    print("== combat: attack monsters only, warmode, loot monster corpses ==")
    tmp = tempfile.mkdtemp()
    c = Ctl(os.path.join(tmp, "harness.db"), os.path.join(tmp, "tasks"), proxy)
    x, y = proxy.pos[:2]
    proxy.fixed_mobiles = {
        "0x00000010": {"x": x + 1, "y": y, "graphic": 0x27, "notoriety": 3, "name": "a mongbat"},
        "0x00000011": {"x": x + 1, "y": y + 1, "graphic": 0xC9, "notoriety": 1, "name": "a cat"},   # a pet
        "0x00000012": {"x": x, "y": y + 1, "graphic": 0x190, "notoriety": 7, "name": "Minka"},
        "0x00000013": {"x": x - 1, "y": y, "graphic": 0x191, "notoriety": 1, "name": "Someone"},  # a player
    }
    proxy.take()
    for serial, why in (("0x00000013", "not a hostile monster"), ("0x00000002", "not a hostile monster"),
                        ("0x00000012", "not a hostile monster"), ("0x00000011", "notoriety 1"),
                        ("0x00000099", "not known")):
        code, out = c("act", "attack", serial)
        check(f"attack refused ({why}): {serial}", code == 1 and why in out.get("error", "")
              and proxy.take() == [], str(out))
    proxy.self_hits = 10
    code, out = c("act", "attack", "0x00000010")
    check("attack refused below 30% hits", code == 1 and "below" in out.get("error", "") and proxy.take() == [],
          str(out))
    proxy.self_hits = 50
    code, out = c("act", "attack", "0x00000010", "--human", "off")
    fr = [p for _, p in proxy.take()]
    check("attack a monster: war mode on first, then the stock 0x05 (Tab, double-click)",
          code == 0 and fr == [actions.war_mode(True), actions.attack(0x10)] and out["warmode_turned_on"]
          and out["target"]["kind"] == "monster", f"{out} {fr}")
    last = proxy.intents[-1] or {}
    check("attack reports an 'attack' intent that follows the mob (target_serial)",
          last.get("kind") == "attack" and last.get("target_serial") == "0x00000010"
          and last.get("text") == "Attacking a mongbat", str(last))
    code, out = c("act", "attack", "0x00000010", "--human", "off")
    check("already in war mode: only the attack", code == 0 and [p for _, p in proxy.take()]
          == [actions.attack(0x10)] and not out["warmode_turned_on"], str(out))
    code, out = c("act", "warmode", "off")
    check("warmode off: 0x72 and confirmed", code == 0 and out["warmode"] is False
          and [p for _, p in proxy.take()] == [actions.war_mode(False)], str(out))
    proxy.fixed_mobiles = {}

    corpse, far, human = 0x40000100, 0x40000101, 0x40000102
    proxy.ground_items = {
        f"0x{corpse:08X}": {"graphic": 0x2006, "amount": 0x27, "x": x + 1, "y": y, "name": "a mongbat corpse"},
        "0x40000110": {"graphic": 0x1F03, "container": f"0x{corpse:08X}"},
        "0x40000111": {"graphic": 0x0EED, "amount": 12, "container": f"0x{corpse:08X}"},
        f"0x{far:08X}": {"graphic": 0x2006, "amount": 0x27, "x": x + 5, "y": y},
        f"0x{human:08X}": {"graphic": 0x2006, "amount": 0x190, "x": x, "y": y + 1},
    }
    code, out = c("act", "loot", f"0x{far:08X}")
    check("loot refused: corpse out of reach", code == 1 and "tiles away" in out.get("error", "")
          and proxy.take() == [], str(out))
    code, out = c("act", "loot", f"0x{human:08X}")
    check("loot refused: a human corpse", code == 1 and "human corpse" in out.get("error", "")
          and proxy.take() == [], str(out))
    code, out = c("act", "loot", "0x40000014")
    check("loot refused: not a corpse", code == 1 and proxy.take() == [], str(out))
    code, out = c("act", "loot", f"0x{corpse:08X}", "--human", "off")
    fr = [p for _, p in proxy.take()]
    pack = proxy.PACK
    check("loot: open the corpse, then gold first, each lifted and dropped into the backpack",
          code == 0 and fr == [actions.dclick(corpse),
                               actions.lift(0x40000111, 12), actions.drop(0x40000111, ctl.DROP_AUTO, ctl.DROP_AUTO,
                                                                          0, 0, pack),
                               actions.lift(0x40000110, 1), actions.drop(0x40000110, ctl.DROP_AUTO, ctl.DROP_AUTO,
                                                                         0, 0, pack)]
          and [t["serial"] for t in out["taken"]] == ["0x40000111", "0x40000110"] and out["left"] == 0,
          f"{out} {fr}")
    proxy.ground_items = {}
    proxy.take()


def test_intent_cmd(proxy):
    print("== ctl intent (the overseer's goal) ==")
    tmp = tempfile.mkdtemp()
    c = Ctl(os.path.join(tmp, "harness.db"), os.path.join(tmp, "tasks"), proxy)
    code, out = c("intent", "Hunting", "mongbats", "for", "100", "gold", "--follow", "0x10")
    check("intent posts a goal with a followed serial",
          code == 0 and proxy.intents[-1] == {"text": "Hunting mongbats for 100 gold", "loop": "overseer",
                                              "kind": "goal", "target_serial": "0x00000010"}, str(proxy.intents[-1:]))
    code, out = c("intent", "--clear")
    check("intent --clear clears it", code == 0 and proxy.intents[-1] is None, str(proxy.intents[-1:]))
    code, out = c("intent")
    check("intent without text refused", code == 1, str(out))


def test_heal_buy(proxy):
    print("== skills, cast, target, buy (self-healing supplies) ==")
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "harness.db")
    c = Ctl(db, os.path.join(tmp, "tasks"), proxy)
    code, out = c("status")
    if os.path.exists(INSTALL_TILEDATA):
        check("status: skills by name from the client's skills.mul, only those above 0",
              out.get("skills") == {"Magery": 60.0}, str(out.get("skills")))
    proxy.take()
    code, out = c("act", "cast", "greater", "heal")
    check("cast by name: the Outlands cast request (0xFF sub 4) for Greater Heal (29)",
          code == 0 and out["spell_id"] == 29 and [p for _, p in proxy.take()] == [actions.cast_spell(29)], str(out))
    code, out = c("act", "cast", "Fireballz")
    check("unknown spell refused", code == 1 and proxy.take() == [], str(out))
    proxy.target = {"active": False, "target_type": None, "cursor_id": None, "cursor_type": None}
    code, out = c("act", "target", "self")
    check("target with no cursor up refused", code == 1 and "no target cursor" in out.get("error", ""), str(out))
    proxy.target = {"active": True, "target_type": 0, "cursor_id": 0x77, "cursor_type": 2}
    x, y, z = proxy.pos[:3]
    code, out = c("act", "target", "self")
    check("target self: 0x6C on your own serial, position and body",
          code == 0 and [p for _, p in proxy.take()] == [actions.target_object(0x77, 1, x, y, z, 0x190, 2)], str(out))
    code, out = c("act", "target", "0x00000002")
    check("target a player refused", code == 1 and "not a hostile monster" in out.get("error", "")
          and proxy.take() == [], str(out))
    code, out = c("act", "target", "0x40000011")
    check("target an item in your backpack: 0x6C with its container-local coordinates",
          code == 0 and [p for _, p in proxy.take()] == [actions.target_object(0x77, 0x40000011, 0, 0, 0,
                                                                               0x1BDD, 2)], str(out))
    proxy.target = {"active": False, "target_type": None, "cursor_id": None, "cursor_type": None}

    vendor, cont = 0x00087D3B, 0x40000200
    proxy.fixed_mobiles = {f"0x{vendor:08X}": {"x": proxy.pos[0] + 2, "y": proxy.pos[1], "graphic": 0x191,
                                                "notoriety": 7, "name": "Minka"}}
    # The world model's item order (first seen) differs from the vendor's latest 0x3C order, which
    # is what the price list refers to, reversed (ClassicUO; live bug 2026-09-30: heal/refresh swapped).
    proxy.ground_items = {f"0x{cont:08X}": {"graphic": 0x0E75, "layer": 0x1A, "container": f"0x{vendor:08X}"},
                          "0x40000202": {"graphic": 0x0F0C, "amount": 20, "container": f"0x{cont:08X}"},
                          "0x40000201": {"graphic": 0x0E21, "amount": 100, "container": f"0x{cont:08X}"},
                          "0x40000203": {"graphic": 0x0F0B, "amount": 20, "container": f"0x{cont:08X}"}}
    proxy.buy_content = [[cont, [0x40000203, 0x40000202, 0x40000201]]]
    proxy.buy_list = {"container": cont, "items": [{"price": 3, "name": "Bandage"},
                                                   {"price": 10, "name": "Lesser Heal Potion"},
                                                   {"price": 10, "name": "Refresh Potion"}]}
    proxy.prices = {"0x40000201": 3, "0x40000202": 10, "0x40000203": 10}
    proxy.gold = 110
    proxy.take()
    code, out = c("act", "buy", f"0x{vendor:08X}", "--human", "off")
    check("buy without an item: the price list mapped to the container items (reversed), nothing bought",
          code == 0 and [(o["name"], o["price"], o["serial"]) for o in out["list"]]
          == [("Bandage", 3, "0x40000201"), ("Lesser Heal Potion", 10, "0x40000202"),
              ("Refresh Potion", 10, "0x40000203")]
          and not any(p[0] == 0x3B for _, p in proxy.take()), str(out))
    code, out = c("act", "buy", f"0x{vendor:08X}", "heal", "potion", "--amount", "3", "--human", "off")
    fr = [p for _, p in proxy.take()]
    check("buy 3 Lesser Heal Potions: popup, Buy entry, then the stock 0x3B for that item",
          code == 0 and fr == [actions.request_popup(vendor), actions.popup_selection(vendor, 1),
                               actions.buy_request(vendor, [(0x40000202, 3)])]
          and out["paid"] == 30 and out["gold"] == 80, f"{out} {fr}")
    spends = Memory(db).job_events("gold")
    check("the spend is recorded for the daily cap", [(e["kind"], e["data"]["total"]) for e in spends]
          == [("spend", 30)], str(spends))
    code, out = c("act", "buy", f"0x{vendor:08X}", "potion", "--human", "off")
    check("ambiguous item refused", code == 1 and "ambiguous" in out.get("error", ""), str(out))
    code, out = c("act", "buy", f"0x{vendor:08X}", "bandage", "--amount", "50", "--human", "off")
    check("not enough gold refused (50 x 3 > 80)", code == 1 and "you have 80" in out.get("error", "")
          and not any(p[0] == 0x3B for _, p in proxy.take()), str(out))
    m = Memory(db)
    m.job_event("gold", "spend", {"total": 49990})
    m.close()
    code, out = c("act", "buy", f"0x{vendor:08X}", "bandage", "--amount", "10", "--human", "off")
    check("the policy's daily cap refuses (49990 + 30 > 50000)", code == 1 and "daily gold cap" in out.get("error", "")
          and not any(p[0] == 0x3B for _, p in proxy.take()), str(out))
    proxy.buy_content = None
    code, out = c("act", "buy", f"0x{vendor:08X}", "bandage", "--human", "off")
    check("no container packet before the list: refuses to buy on a guess",
          code == 1 and "not buying on a guess" in out.get("error", "")
          and not any(p[0] == 0x3B for _, p in proxy.take()), str(out))
    proxy.fixed_mobiles, proxy.ground_items, proxy.buy_list, proxy.prices = {}, {}, None, {}
    proxy.take()

    pack = f"0x{proxy.PACK:08X}"
    proxy.ground_items = {"0x40000301": {"graphic": 0x0F0C, "amount": 3, "container": pack, "name": "Lesser Heal Potion"},
                          "0x40000302": {"graphic": 0x0F0C, "amount": 1, "container": pack, "name": "Lesser Heal Potion"},
                          "0x40000303": {"graphic": 0x0F0B, "amount": 2, "container": pack, "name": "Refresh Potion"}}
    code, out = c("status")
    rows = {r["serial"]: r for r in out["backpack"]["items"]}
    check("status.backpack.items: serials with names, amounts and the sub-bag",
          rows.get("0x40000301", {}).get("name") == "Lesser Heal Potion" and rows["0x40000301"]["in"] is None
          and rows.get("0x40000013", {}).get("in") == "0x40000012" and rows["0x40000013"]["amount"] == 10,
          str(out["backpack"]["items"]))
    code, out = c("act", "use", "heal", "potion")
    check("use by name: double-clicks the smallest matching stack",
          code == 0 and out["used"]["serial"] == "0x40000302"
          and [p for _, p in proxy.take()] == [actions.dclick(0x40000302)], str(out))
    code, out = c("act", "use", "0x0F0B")
    check("use by graphic", code == 0 and [p for _, p in proxy.take()] == [actions.dclick(0x40000303)], str(out))
    code, out = c("act", "use", "potion")
    check("a name matching different items is refused", code == 1 and "different items" in out.get("error", "")
          and proxy.take() == [], str(out))
    code, out = c("act", "use", "dragon", "scale")
    check("nothing matching refused", code == 1 and proxy.take() == [], str(out))
    proxy.ground_items = {}


def reset_events(proxy):
    """Drop the fake's event ring (tests that read `journal` expect only their own events)."""
    with proxy.lock:
        proxy.events.clear()


def uomap_layer(graphic):
    import uomap
    return uomap.tiledata().item(graphic).layer


def test_map(proxy):
    print("== map: the cave under the Shelter moongate (real map) ==")
    if not os.path.exists(INSTALL_TILEDATA):
        print("  (skipped: no install dir)")
        return
    tmp = tempfile.mkdtemp()
    c = Ctl(os.path.join(tmp, "harness.db"), os.path.join(tmp, "tasks"), proxy)
    proxy.pos = [1989, 2535, 0, 0]                   # live 2026-09-30: the overseer ended up here
    proxy.ground_items = {"0x40000099": {"graphic": 0x0F6C, "x": 1985, "y": 2533, "z": 40}}
    code, out = c("map", "--radius", "8", "--to", "1985", "2533", "--z", "40")
    rows = out.get("map", [])
    check("map: you are under cover with the hill (z 40) above",
          code == 0 and out.get("under_cover") and out.get("levels_above_you") == [40], str(out)[:300])
    check("map: 2 header rows + 17 rows of 17 tiles, '@' at the centre",
          len(rows) == 19 and all(len(r) == 6 + 17 for r in rows) and rows[2 + 8][6 + 8] == "@", str(rows[:3]))
    check("map: the route to the gate at z 40 is found and leaves the cave (ends at z 40)",
          (out.get("route") or {}).get("found") and out["route"]["end"][2] >= 30, str(out.get("route"))[:300])
    check("map: the moongate is listed as a ground item",
          any(i["name"] == "blue moongate" for i in out.get("items", [])), str(out.get("items")))
    proxy.ground_items = {}
    proxy.pos = [100, 100, 0, 2]


def test_know(proxy):
    print("== know (long-term memory via ctl) ==")
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "harness.db")
    c = Ctl(db, os.path.join(tmp, "tasks"), proxy)
    code, out = c("know", "add", "--kind", "fact", "--topic", "innkeeper", "--entity", "Jayne", "--at", "1932",
                  "2595", "--ref", "chat#12", "--importance", "7", "Jayne", "rents", "rooms")
    check("know add -> added, written to the store", code == 0 and out.get("action") == "added", str(out))
    m = Memory(db)
    rows = m.chat(role="overseer")
    check("a knowledge write shows in the chat as a memory row with the entry",
          rows and rows[-1]["kind"] == "memory" and rows[-1]["text"].startswith("remembered #1 fact [innkeeper]")
          and rows[-1]["data"]["op"] == "add" and rows[-1]["data"]["entry"]["id"] == 1, str(rows[-1:]))
    m.close()
    code, out = c("know", "search", "room", "--near", "1930", "2590")
    check("know search finds it (stemmed), with location", code == 0 and out["results"][0]["id"] == 1
          and out["results"][0]["at"] == [0, 1932, 2595], str(out))
    m = Memory(db)
    r = m.chat(role="overseer")[-1]
    m.close()
    check("a lookup shows in the chat with its query and ranked results",
          r["kind"] == "memory" and r["data"]["op"] == "search" and r["data"]["query"] == "room"
          and [e["id"] for e in r["data"]["results"]] == [1] and "score" in r["data"]["results"][0]
          and r["text"] == "recalled 'room' (near 1930,2590): 1 result(s)", str(r))
    proxy.pos = [1933, 2596, 0, 2]
    code, out = c("know", "brief")
    check("know brief uses the live situation (position from the proxy)",
          code == 0 and out["near"] == [0, 1933, 2596] and [e["id"] for e in out["relevant"]] == [1], str(out))
    m = Memory(db)
    r = m.chat(role="overseer")[-1]
    m.close()
    check("the brief shows in the chat with its relevant/standing lists",
          r["data"]["op"] == "brief" and [e["id"] for e in r["data"]["relevant"]] == [1]
          and r["data"]["standing"] == [], str(r))
    n = len(Memory(db).chat())
    code, out = c("know", "retract", "1", "--reason", "")
    check("errors come back as JSON (retract without a reason), and post nothing",
          code == 1 and "reason" in out["error"] and len(Memory(db).chat()) == n, str(out))
    proxy.pos = [100, 100, 0, 2]


def main():
    proxy = FakeProxy()
    for port in (proxy.control_port, proxy.state_port):
        assert port >= 12700 and port not in (25941, 25942)
    test_wait(proxy)
    test_status(proxy)
    test_run_act(proxy)
    test_map(proxy)
    test_know(proxy)
    test_combat(proxy)
    test_heal_buy(proxy)
    test_intent_cmd(proxy)
    reset_events(proxy)
    test_overseer_acts(proxy)
    if FAILURES:
        print(f"FAILED: {len(FAILURES)}: {FAILURES}")
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()

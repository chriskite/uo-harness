"""Tests for harness/ctl.py + harness/task_wrap.py: the overseer bus CLI
(docs/OVERSEER.md).

Covers what the overseer relies on:
  * `wait` wakes on a new open juncture (>= attention) and on user chat, not on
    overseer chat, acked or info junctures (unless --include-info); cursors
    persist in meta; timeout -> event null; the heartbeat advances while waiting
  * `run` (stub tasks through the test-only whitelist override) -> task_done /
    task_failed juncture with exit code, summary and tail; one task per character;
    `stop` ends it with a stopped task_failed; a vanished wrapper is reported
  * `act`: allowlisted speech only, no actions while a task runs, walk sends
    the framed 0x02 packets and follows the step outcomes, target_cancel uses
    the live cursor, no gump responses
  * `status` against the fake proxy, and ok:false when it's unreachable

Offline: temp DB, stub task scripts in a temp dir, a fake proxy (control +
state ports) on private ports >= 12700. Never touches the live ports.

Speed: commands run in-process through ctl.main(argv) (stdout captured, exit code
returned) with a skip-ahead clock (FastClock) in the modules a command waits in, so
ctl's fixed listen windows don't cost real seconds against a fake that answers
synchronously; loopback connects without Windows' timeout-connect delay (LoopbackSocket);
one idle connection per store held open (_connect_kept_open). The `wait` wake tests and
the unreachable-proxy `status` (Windows refuses a closed port only after ~2 s; it runs
alongside from the start) spawn the real `python ctl.py` CLI.
Run: python harness/test_ctl.py   (~7 s)
"""
import contextlib
import functools
import io
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["UO_QUIET"] = "1"        # alerts.QUIET_ENV: no alarm sounds from the ctl subprocesses
INSTALL_TILEDATA = "C:/Program Files (x86)/Ultima Online Outlands/artdata.uoo"   # tiledata items; read-only

import actions  # noqa: E402
import agent_link  # noqa: E402
import convert  # noqa: E402
import ctl  # noqa: E402
import healing  # noqa: E402
import humanize  # noqa: E402
import memory  # noqa: E402
import nav  # noqa: E402
import room  # noqa: E402
import shelf  # noqa: E402
import task_wrap as tw  # noqa: E402
import tracking  # noqa: E402
from memory import Memory  # noqa: E402

_memory_connect = memory.connect
_KEPT_OPEN = {}     # normalized store path -> an idle connection held for the whole run


def _connect_kept_open(path):
    """memory.connect, with one idle connection to each store held open for the whole run,
    the way the proxy's MemoryWriter holds the live store: a closing Memory (every ctl
    command, every meta() check) is then never the store's last connection, so it doesn't
    checkpoint and delete the WAL, and the next open doesn't start a new WAL (each one a
    disk flush: ~10 ms on Windows, ~1 s under load). The idle connection holds no lock and
    no read snapshot; what every connection reads is the same."""
    con = _memory_connect(path)
    key = os.path.normcase(os.path.abspath(path))
    if key not in _KEPT_OPEN:
        keep = sqlite3.connect(path, check_same_thread=False)
        keep.execute("SELECT COUNT(*) FROM meta").fetchall()    # attach to the WAL, then go idle
        _KEPT_OPEN[key] = keep
    return con


memory.connect = _connect_kept_open

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
        self.last_seen = {}       # world.last_seen: mobiles the client dropped (npcs/goto tests)
        self.swings = {}          # world.swings: attacker -> {"defender", "t"} (status attackers)
        self.ground_items = {}    # extra ground items (goto/map tests)
        self.item_view = None     # Chebyshev range beyond which ground items aren't sent (the client's view)
        self.gate_gumps = {}      # tile -> (gump_id, layout, lines): the gump a moongate opens on arrival
        self.warmode = False
        self.status_requested = []   # world.status_requested (the client's outstanding 0x34s)
        self.worn_layers = {}        # world.worn_layers (the layer each item was last worn on)
        self.gate = {"state": "open"}
        self.gate_actions = []
        self.self_hits = 50
        self.mana = 20
        self.cast_cursor = False  # heal tests: a cast raises a beneficial cursor (event "target"), 0x6C clears it
        self.potion_answers = {}  # item key -> cliloc the server answers its double-click with
        self.self_noto = 1
        self.gold = 110
        self.dead = False         # world.self.dead (a ghost); the Resurrection gump's Accept revives
        self.buy_list = None      # {"container": int, "items": [{"price", "name"}]} sent on a menu pick
        self.buy_content = None   # [[container, [serials in 0x3C packet order]]] sent just before it
        self.prices = {}          # item serial -> price charged by a 0x3B
        self.intents = []         # intents posted on the state port (op "intent")
        self.buffs = {}           # icon id (str) -> buff record (world.buffs for self)
        self.opened = [self.PACK]  # world.containers: serials the server opened (0x24); the client opens the pack at login
        self.labels = {}          # world.labels: serial -> clicked title ("Sherwin the mage")
        self.stats = {}           # extra self stats (0x11 fields)
        self.deny_jumps = {}      # tile -> destination: a teleporter that denies the step, then moves you
        self.pending_jump, self.jump_polls = None, 0
        self.events = []          # event envelopes; seq = index
        # a server that converts logs (act convert): the hatchet's double-click raises a cursor (none
        # once with no_cursor_once), a live trapped pouch (hue 38) goes off on its double-click (no
        # 0x24), a log stack targeted outside a live pouch becomes boards in its container
        self.convert_sim = None   # {"hatchet": serial, "no_cursor_once": bool, "refused": n} when on
        self.gone = set()         # keys of the fixed items the server deleted (the pack's 5 logs, converted)
        # a server with the Tracking gump (live 20261001_214649): {"mode": index into tracking.MODES
        # (server truth), "heard": the client has seen a "You will now hunt" line, "hunting": bool}
        self.tracker = None
        self.gump_seq = 0x02C80000
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
                            nxt = nav.step(tuple(self.pos[:2]), d)
                            if nxt in self.deny_jumps:            # denied, then moved a moment later
                                self.pending_jump, self.jump_polls = self.deny_jumps[nxt], 0
                            else:
                                self.pos[0], self.pos[1] = nxt
                                if nxt in self.gate_gumps:
                                    self._gate_gump(*self.gate_gumps[nxt])
                    elif pkt[:5] == bytes.fromhex("bf00090013"):  # context menu request -> menu (mode 2)
                        s = int.from_bytes(pkt[5:9], "big")
                        self.add_event({"ev": "popup", "serial": s, "entries": [
                            {"cliloc": 3006123, "index": 0, "flags": 0}, {"cliloc": 3006103, "index": 1, "flags": 0}]})
                    elif pkt[:5] == bytes.fromhex("bf000b0015") and self.buy_list:   # menu pick -> 0x3C, 0x74
                        if self.buy_content is not None:
                            self.add_event({"ev": "container_content", "count": 0, "containers": self.buy_content})
                        self.add_event({"ev": "buy_list", **self.buy_list})
                    elif pkt[0] == 0x3B:     # buy: charge the configured price; the bank pays when the pack can't
                        n, total = len(pkt), 0
                        for off in range(8, n, 7):
                            s = f"0x{int.from_bytes(pkt[off + 1:off + 5], 'big'):08X}"
                            total += self.prices.get(s, 0) * int.from_bytes(pkt[off + 5:off + 7], "big")
                        if total <= self.gold:
                            self.gold -= total
                        else:                # live 2026-10-04 (Errol, 0 gp in the pack): the line as captured
                            self.add_event({"ev": "speech_heard", "serial": 0x16, "name": "Errol", "type": 0,
                                            "hue": 0x3B2, "text": f"The total of thy purchase is {total} gold, which "
                                            "has been withdrawn from your bank account.  My thanks for the "
                                            "patronage."})
                    elif pkt[0] == 0x72:
                        self.warmode = bool(pkt[1])
                    elif pkt[0] == 0x06 and self.convert_sim is not None and self._convert_dclick(
                            int.from_bytes(pkt[1:5], "big")):
                        pass
                    elif pkt[0] == 0x06:                              # dclick a container -> the server's 0x24
                        s = int.from_bytes(pkt[1:5], "big")
                        key = f"0x{s:08X}"
                        holds = any(v.get("container") in (key, s) for v in self.ground_items.values())
                        if (s == self.PACK or holds) and s not in self.opened and key not in self.opened:
                            self.opened.append(s)
                        if key in self.potion_answers:
                            self.add_event({"ev": "cliloc", "serial": 0xFFFFFFFF, "cliloc": self.potion_answers[key],
                                            "args": ""})
                    elif pkt[0] == 0xFF and pkt[3:7] == b"\x00\x00\x00\x04" and self.cast_cursor:
                        self.target = {"active": True, "target_type": 0, "cursor_id": 0x78, "cursor_type": 2}
                        self.add_event({"ev": "target", "target_type": 0, "cursor_id": 0x78, "cursor_type": 2})
                    elif pkt[0] == 0x6C and self.cast_cursor:
                        self.target = {"active": False, "target_type": None, "cursor_id": None, "cursor_type": None}
                    elif pkt[0] == 0x6C and self.convert_sim is not None:
                        self._convert_target(int.from_bytes(pkt[7:11], "big"))
                    elif pkt[0] == 0x07:
                        self.lifted = (f"0x{int.from_bytes(pkt[1:5], 'big'):08X}", int.from_bytes(pkt[5:7], "big"))
                        if self.ground_items.get(self.lifted[0], {}).get("graphic") == 0x1F03:
                            del self.ground_items[self.lifted[0]]    # a death robe is deleted when lifted (live)
                    elif pkt[0] == 0x08 and len(pkt) == 22:          # drop into a container
                        key = f"0x{int.from_bytes(pkt[1:5], 'big'):08X}"
                        dest = f"0x{int.from_bytes(pkt[18:22], 'big'):08X}"
                        it = self.ground_items.get(key)
                        n = self.lifted[1] if getattr(self, "lifted", (None,))[0] == key else None
                        if it is not None and n is not None and n < (it.get("amount") or 1):
                            # a partial lift: this serial goes with n, the rest stays behind as a new item
                            # (live 2026-10-05, RunUO's split)
                            self.ground_items["0x4000FFFF"] = {**it, "amount": it["amount"] - n}
                            it.update(amount=n, container=dest)
                        elif it is not None and it.get("graphic") == 0x0EED:
                            del self.ground_items[key]                # gold merges into the pack's pile
                        elif it is not None:
                            it.update(container=dest, layer=None)
                    elif pkt[0] == 0x13:                              # equip request
                        it = self.ground_items.get(f"0x{int.from_bytes(pkt[1:5], 'big'):08X}")
                        if it is not None:
                            it.update(container=f"0x{int.from_bytes(pkt[6:10], 'big'):08X}", layer=pkt[5])
                    elif pkt[0] == 0x12 and pkt[3] == 0x24 and self.tracker is not None:
                        self._tracking_gump()
                    elif pkt[0] == 0xB1 and int.from_bytes(pkt[7:11], "big") == ctl.RESURRECT_GUMP_ID \
                            and int.from_bytes(pkt[11:15], "big") == 1:   # Accept: up again, in a death robe
                        self.dead = False
                        self.ground_items["0x40000099"] = {"graphic": 0x1F03, "layer": 0x16, "name": "death robe",
                                                           "container": "0x00000001"}
                    elif pkt[0] == 0xB1 and self.tracker is not None \
                            and int.from_bytes(pkt[7:11], "big") == tracking.GUMP_ID:
                        self._tracking_click(int.from_bytes(pkt[3:7], "big"), int.from_bytes(pkt[11:15], "big"))
                reply = b"OK"
                c.sendall(len(reply).to_bytes(2, "big") + reply)
        except (EOFError, OSError):
            c.close()

    def _convert_dclick(self, s) -> bool:
        """The convert server's answer to a double-click (lock held); False: not its business."""
        key, sim = f"0x{s:08X}", self.convert_sim
        it = self.ground_items.get(key)
        if s == sim["hatchet"]:
            if sim.get("no_cursor_once"):
                sim["no_cursor_once"] = False
            else:
                self.target = {"active": True, "target_type": 0, "cursor_id": 0x99, "cursor_type": 0}
                self.add_event({"ev": "target", "target_type": 0, "cursor_id": 0x99, "cursor_type": 0})
            return True
        if it is not None and it.get("graphic") == 0x0E79 and it.get("hue") == 38:
            it["hue"] = 0                                     # it goes off: a hit, no 0x24
            self.self_hits -= 1
            return True
        return False

    def _convert_target(self, s):
        """A log stack targeted with the hatchet's cursor (lock held)."""
        key = f"0x{s:08X}"
        self.target = {"active": False, "target_type": None, "cursor_id": None, "cursor_type": None}
        fixed = {"0x40000011": {"graphic": 0x1BDD, "amount": 5, "container": f"0x{self.PACK:08X}"}}
        it = self.ground_items.get(key) or (fixed.get(key) if key not in self.gone else None)
        if it is None or it.get("graphic") not in range(0x1BDD, 0x1BE3):
            return
        box = self.ground_items.get(it["container"]) or {}
        if box.get("hue") == 38:                               # inside a live pouch: can't be seen into
            self.convert_sim["refused"] = self.convert_sim.get("refused", 0) + 1
            return
        if key in self.ground_items:
            del self.ground_items[key]
        else:
            self.gone.add(key)
        self.convert_sim["boards"] = n = self.convert_sim.get("boards", 0) + 1     # a new serial per stack
        self.ground_items[f"0x4000{0xB000 + n:04X}"] = {"graphic": 0x1BD7, "amount": it.get("amount") or 1,
                                                       "hue": it.get("hue"), "container": it["container"]}
        self.add_event({"ev": "speech_heard", "serial": 0xFFFFFFFF, "name": "System", "type": 0,
                        "text": "You shape the logs into boards."})

    TRACK_LAYOUT = ("{ resizepic 31 24 11571 662 150 }{ button 25 19 2094 2095 1 0 1 }"
                    "{ button 66 130 4017 4019 1 0 2 }{ button 416 130 4008 4010 1 0 6 }"
                    "{ button 376 59 9909 9909 1 0 7 }{ button 470 59 9903 9903 1 0 8 }")

    def _tracking_gump(self):
        """The server (re)sends the Tracking gump (lock held)."""
        self.gump_seq += 1
        lines = ["Guide", "Aggressive", "Passive", "Townsfolk", "Players", "Hide Party / Guild", "Hide Allies",
                 "Hide House", "Stop Hunting" if self.tracker["hunting"] else "Begin Hunting", "Hunting Mode",
                 "Always Get Closest"]
        g = {"serial": f"0x{self.gump_seq:08X}", "gump_id": f"0x{tracking.GUMP_ID:08X}",
             "layout": self.TRACK_LAYOUT, "lines": lines, "open": True}
        self.gumps.append(g)
        self.add_event({"ev": "gump_open", "serial": self.gump_seq, "gump_id": tracking.GUMP_ID,
                        "layout": self.TRACK_LAYOUT, "lines": lines})

    def _tracking_click(self, serial, button):
        tr, n = self.tracker, len(tracking.MODES)
        for g in self.gumps:
            if g["serial"] == f"0x{serial:08X}":
                g["open"] = False
        if button in (tracking.BTN_NEXT, tracking.BTN_PREV):
            tr["mode"] = (tr["mode"] + (1 if button == tracking.BTN_NEXT else -1)) % n
            tr["heard"] = True
            self.add_event({"ev": "speech_heard", "serial": 0xFFFFFFFF, "name": "System", "type": 0,
                            "text": f"You will now hunt {tracking.MODES[tr['mode']]}."})
        elif button == tracking.BTN_HUNT:
            tr["hunting"] = not tr["hunting"]
            self.add_event({"ev": "speech_heard", "serial": self.SELF, "name": "TestWorth", "type": 0,
                            "text": "You begin hunting." if tr["hunting"] else "You stop hunting."})
        if button:
            self._tracking_gump()

    def _gate_gump(self, gump_id, layout, lines):
        """The server opens a moongate's gump on the step onto it (lock held)."""
        self.gump_seq += 1
        self.gumps.append(gump_row(self.gump_seq, gump_id, layout, lines))
        self.add_event({"ev": "gump_open", "serial": self.gump_seq, "gump_id": gump_id,
                        "layout": layout, "lines": list(lines)})

    def snapshot(self):
        p = f"0x{self.PACK:08X}"
        with self.lock:
            if self.pending_jump is not None:
                self.jump_polls += 1
                if self.jump_polls > 1:
                    self.pos[0], self.pos[1] = self.pending_jump
                    self.pending_jump = None
            return {
                "ok": True,
                "movement": {"pos": list(self.pos), "self_serial": self.SELF, "inflight": 0,
                             "stalled": False, "resync_pending": False, "client_stale": False},
                "world": {
                    "self": {"serial": "0x00000001", "name": "TestWorth", "hits": self.self_hits, "hits_max": 60,
                             "stam": 40, "stam_max": 45, "mana": self.mana, "mana_max": 25, "weight": 123, "map": 0,
                             "warmode": self.warmode, "notoriety": self.self_noto, "gold": self.gold,
                             "body": 0x192 if self.dead else 0x190, "dead": self.dead, "skill_names": [],
                             "skills": {"25": {"value": 600, "base": 600, "lock": 0, "cap": 1000},
                                        "17": {"value": 0, "base": 0, "lock": 0, "cap": 1000}},
                             "stats": dict(self.stats)},
                    "mobiles": {"0x00000001": {"x": self.pos[0], "y": self.pos[1], "notoriety": 1},
                                "0x00000002": {"x": self.pos[0] + 3, "y": self.pos[1], "name": "a PK",
                                               "notoriety": 6, "graphic": 400},
                                "0x00000003": {"x": self.pos[0] + 50, "y": self.pos[1], "name": "far"},
                                **self.fixed_mobiles},
                    "last_seen": dict(self.last_seen), "swings": dict(self.swings),
                    "items": {p: {"graphic": 0x0E75, "layer": 0x15, "container": "0x00000001"},
                              **({} if "0x40000011" in self.gone else
                                 {"0x40000011": {"graphic": 0x1BDD, "amount": 5, "container": p}}),
                              "0x40000012": {"graphic": 0x0E76, "container": p},
                              "0x40000013": {"graphic": 0x1BD7, "amount": 10, "container": "0x40000012"},
                              "0x40000014": {"graphic": 0x1BD7, "amount": 99, "x": 1, "y": 1},
                              **{k: v for k, v in self.ground_items.items()
                                 if self.item_view is None or v.get("x") is None
                                 or nav.chebyshev((v["x"], v["y"]), tuple(self.pos[:2])) <= self.item_view}},
                    "target": dict(self.target), "gumps": list(self.gumps),
                    "tracking": None if self.tracker is None else {
                        "hunting": self.tracker["hunting"], "arrow": None, "hits": [],
                        "mode": tracking.MODES[self.tracker["mode"]] if self.tracker["heard"] else None},
                    "buffs": {"0x00000001": dict(self.buffs)}, "labels": dict(self.labels),
                    "containers": list(self.opened), "status_requested": list(self.status_requested),
                    "worn_layers": dict(self.worn_layers)},
                "events": [], "next": len(self.events),
                "gate": dict(self.gate),
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
                elif req.get("op") == "gate":
                    with self.lock:
                        if req.get("action") == "break":
                            self.gate = {**self.gate, "state": "break", "blocked": True, "break_due_at": None,
                                         "break_starts_in_s": None, "break_until": time.time() + 900}
                            self.gate_actions.append("break")
                        resp = {"ok": True, "gate": dict(self.gate)}
                elif req.get("op") == "sessions":
                    resp = {"ok": True, "sessions": [{"tag": "fake", "serial": f"0x{self.SELF:08X}",
                                                      "name": "Hackworth"}]}
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


REAL_CLOCK_CMDS = {"stop"}    # waits on the task wrapper, a real process: keep its grace in real seconds


class FastClock:
    """`time` for the modules a ctl command waits in (ctl, agent_link's Mover, humanize,
    tracking, convert): sleep() returns at once and moves monotonic() on by the seconds it
    was asked to sleep (real time still passes on top), so the fixed windows (1.5 s listening
    after a send, 4 s for a cast cursor, the 3 s move confirmations) cost no real seconds
    against fakes that answer synchronously, and a window never ends sooner in real time
    than its polls take. One clock for all of them: they hand each other monotonic stamps
    (Human.pace_step), and it never runs back. time() stays the wall clock (meta stamps,
    buff ends, ages)."""

    def __init__(self):
        self.skipped = 0.0

    def __getattr__(self, name):
        return getattr(time, name)

    def monotonic(self):
        return time.monotonic() + self.skipped

    def sleep(self, s):
        if s < 0:
            time.sleep(s)            # ValueError, like time.sleep
        self.skipped += s


FAST_CLOCK = FastClock()
CLOCKED = (ctl, agent_link, humanize, tracking, convert)


class LoopbackSocket:
    """`socket` for ctl and agent_link in this process: create_connection to the fake proxy
    connects blocking, then sets the timeout. Windows reports a loopback connect made with a
    timeout (non-blocking connect + select) done ~10 ms late, a blocking one at once; a
    loopback connect ends long before the timeout either way, refused or accepted."""

    def __getattr__(self, name):
        return getattr(socket, name)

    @staticmethod
    def create_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, *args, **kw):
        if args or kw or address[0] != "127.0.0.1":
            return socket.create_connection(address, timeout, *args, **kw)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.connect(address)
        except BaseException:
            s.close()
            raise
        if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
            s.settimeout(timeout)
        return s


LOOPBACK = LoopbackSocket()
CONNECTING = (ctl, agent_link)


@contextlib.contextmanager
def swapped(mods, name, value):
    """Each module's global `name` is `value` inside the block."""
    saved = [getattr(mod, name) for mod in mods]
    for mod in mods:
        setattr(mod, name, value)
    try:
        yield
    finally:
        for mod, v in zip(mods, saved):
            setattr(mod, name, v)


def fast_clock(mods=CLOCKED):
    return swapped(mods, "time", FAST_CLOCK)


# ctl.main builds its argparse tree (and argparse looks up gettext catalogs on disk for every
# help string) on each call: one tree serves every in-process command, parse_args leaves it as is
ctl.build_parser = functools.cache(ctl.build_parser)


def ctl_result(out, err):
    """`python ctl.py`'s one JSON stdout line; anything else comes back as _bad_stdout."""
    lines = out.strip().splitlines()
    try:
        return json.loads(lines[-1]) if len(lines) == 1 else {"_bad_stdout": out, "_stderr": err}
    except ValueError:
        return {"_bad_stdout": out, "_stderr": err}


def run_ctl(argv, tasks=None, fast=True):
    """ctl.main(argv) in this process, like `python ctl.py ARGV`: (exit code, its one JSON
    stdout line). Anything else on stdout, or an exception, comes back as _bad_stdout."""
    out, err = io.StringIO(), io.StringIO()
    saved_env = os.environ.pop(ctl.TEST_TASKS_ENV, None)
    if tasks is not None:
        os.environ[ctl.TEST_TASKS_ENV] = json.dumps(tasks)
    try:
        with fast_clock(CLOCKED if fast else ()), swapped(CONNECTING, "socket", LOOPBACK), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ctl.main(argv)
    except Exception:
        code = 1
        err.write(traceback.format_exc())
    finally:
        os.environ.pop(ctl.TEST_TASKS_ENV, None)
        if saved_env is not None:
            os.environ[ctl.TEST_TASKS_ENV] = saved_env
    return code, ctl_result(out.getvalue(), err.getvalue())


def spawn_ctl(argv):
    """`python ctl.py ARGV` in its own process (no test task override), to run alongside the
    in-process commands; finish_ctl() gives what run_ctl(argv) would."""
    return subprocess.Popen([PY, CTL] + argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            env=env_with())


def finish_ctl(p, timeout=60):
    out, err = p.communicate(timeout=timeout)
    return p.returncode, ctl_result(out, err)


class Ctl:
    def __init__(self, db, logdir, proxy, tasks=None, char=None):
        self.args = ["--db", db, "--log-dir", logdir,
                     "--control-port", str(proxy.control_port), "--state-port", str(proxy.state_port)]
        if char is not None:
            self.args += ["--char", char]
        self.tasks = tasks
        self.env = env_with(tasks)

    def __call__(self, *args):
        return run_ctl(self.args + list(args), self.tasks, fast=args[0] not in REAL_CLOCK_CMDS)

    def spawn(self, *args):
        """The real CLI in its own process (real clock), for waits that run alongside the test."""
        return subprocess.Popen([PY, CTL] + self.args + list(args), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, env=self.env)


def finish(p, timeout=30):
    out, _ = p.communicate(timeout=timeout)
    return json.loads(out.strip())


def meta(db, key):
    m = Memory(db)
    try:
        return tw.meta_get(m, key)
    finally:
        m.close()


def wait_for(pred, timeout=20.0, poll=0.05):
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

    def beat_since(t):    # the spawned wait's heartbeat, once it is polling
        return wait_for(lambda: (lambda v: v and float(v) >= t and float(v))(meta(db, ctl.HEARTBEAT_KEY)), 10, 0.05)

    hb0 = time.time()
    p = c.spawn("wait", "--timeout", "20", "--poll", "0.1")
    hb1 = beat_since(hb0)
    # the 0.4 s the heartbeat gets to advance, ended early once it has
    hb2 = wait_for(lambda: (lambda v: v > float(hb1 or 0) and v)(float(meta(db, ctl.HEARTBEAT_KEY) or 0)), 0.4) \
        or float(meta(db, ctl.HEARTBEAT_KEY) or 0)
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

    t_spawn = time.time()
    p = c.spawn("wait", "--timeout", "20", "--poll", "0.1")
    beat_since(t_spawn)
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

    print("== break due: wait wakes once, the overseer starts the break ==")
    due = time.time()
    proxy.gate = {"state": "break_due", "blocked": False, "reason": None, "break_due_at": due,
                  "break_starts_in_s": 540.0, "break_until": None}
    code, out = c("wait", "--timeout", "3", "--poll", "0.1")
    ev = out.get("event") or {}
    check("a due break wakes a plain `wait` with a break_due juncture (attention) and when it starts by itself",
          ev.get("type") == "juncture" and ev.get("kind") == "break_due" and ev.get("severity") == "attention"
          and "starts by itself" in ev.get("summary", ""), str(out))
    code, out = c("wait", "--timeout", "1", "--poll", "0.1")
    check("the same due break is announced once", out.get("event") is None, str(out))
    code, out = c("break")
    check("`ctl break` starts the break through the gate", code == 0 and out.get("ok")
          and proxy.gate_actions == ["break"] and out["gate"]["state"] == "break", str(out))

    print("== alert: the overseer suspects staff ==")
    code, out = c("alert", "asked", "if", "I'm", "at", "my", "keyboard", "--serial", "0xBEEF")
    g = m.junctures(after_id=out.get("id", 1) - 1, limit=1)[0] if out.get("id") else {}
    check("`ctl alert` posts an urgent gm_suspected juncture with the reason and speaker, and stamps the alarm",
          code == 0 and g.get("kind") == "gm_suspected" and g.get("severity") == "urgent"
          and g["data"] == {"reason": "asked if I'm at my keyboard", "serial": "0x0000BEEF"}
          and meta(db, "gm_alarm_t") is not None, f"{out} {g}")
    code, out2 = c("alert", "still", "talking")
    check("a second alert while one is open re-sounds, posts nothing new",
          code == 0 and out2.get("already_open") and out2.get("id") == out.get("id")
          and len([j for j in m.junctures() if j["kind"] == "gm_suspected"]) == 1, str(out2))
    tw.meta_set(m, "gm_alarm_t", "0")
    c("wait", "--timeout", "0.5", "--poll", "0.1")
    check("`wait` sounds it again while it stays open (the alarm repeats)",
          float(meta(db, "gm_alarm_t") or 0) > time.time() - 30, meta(db, "gm_alarm_t"))
    m.juncture_ack(out["id"])
    tw.meta_set(m, "gm_alarm_t", "0")
    c("wait", "--timeout", "0.5", "--poll", "0.1")
    check("acked: `wait` stays silent", meta(db, "gm_alarm_t") == "0", meta(db, "gm_alarm_t"))
    proxy.gate = {"state": "open"}

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


def test_status(proxy, unreachable):
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
    # live 2026-10-03 22:17:35 (session 20261003_213125): Spell Siphon after a gazer larva's hit,
    # value 0.06 and an end one hour on (ends_t from the world model); the same buff left
    # unremoved by the server 15 min past its end (capture 20261003_111419)
    siphon = {"icon_id": 167, "f1": 4628, "f2": 0, "timers": [{"value": 0.06, "end": 54635744434}],
              "title": "", "cliloc": 1095588}
    proxy.buffs = {"277": {"icon_id": 277, "f1": 4620, "f2": 1, "f3": 0, "f4": 0,
                           "timers": [{"value": 5.0, "end": 0}], "title": "Stationary Penalty",
                           "description": "Move {value} more steps", "category": 0, "mode": 1, "scalar": 0.0,
                           "ends_t": None},
                   "140": {"icon_id": 140, "f2": 2, "timers": [], "title": "", "cliloc": 1075655, "ends_t": None},
                   "167": {**siphon, "ends_t": time.time() + 3600}}
    code, out = c("status")
    check("status.stats: Str/Dex/Int, cap, luck, resists, damage, followers",
          out.get("stats") == {"str": 80, "dex": 21, "int": 72, "stats_cap": 225, "luck": 0,
                               "resists": {"physical": 9, "fire": 1}, "damage": [17, 32], "followers": [0, 5]},
          str(out.get("stats")))
    b = out.get("buffs") or []
    check("status.buffs: your buffs by icon, titles from text or cliloc, with the raw numbers",
          [x["icon"] for x in b] == [140, 167, 277] and b[2]["title"] == "Stationary Penalty"
          and b[2]["values"] == [5.0] and b[2]["raw"]["f2"] == 1 and b[0]["title"], str(b))
    check("status.buffs: a buff's numbers are values, its time left comes from the server's end",
          b[1]["values"] == [0.06] and 3590 < b[1]["ends_in_s"] <= 3600 and not b[1]["expired"]
          and b[2]["ends_in_s"] is None and not b[2]["expired"], str(b))
    proxy.buffs = {"167": {**siphon, "ends_t": time.time() - 888}}
    code, out = c("status")
    b = out.get("buffs") or []
    check("status.buffs: past its end and not removed by the server: still listed, expired",
          [(x["icon"], x["expired"]) for x in b] == [(167, True)] and b[0]["ends_in_s"] < -880, str(b))
    proxy.stats, proxy.buffs = {}, {}
    code, out = finish_ctl(unreachable)
    check("proxy unreachable -> ok false", code == 1 and out.get("ok") is False
          and "unreachable" in out["error"] and out["tasks"] == [], str(out))


STUBS = {
    "ok": "import sys\nprint('stub start', sys.argv[1:3])\nprint('stored 30 boards')\n",
    "fail": "import sys\nprint('working')\nprint('12:00:00 ABORTED: stub abort')\nsys.exit(1)\n",
    "slow": "import time\nprint('slow start', flush=True)\nfor _ in range(600):\n    time.sleep(0.1)\n",
    # `ctl stop --after-trip`: a lumber run that ends itself once the wrapper's UO_TASK_ID is named in task_finish
    "lumber": (f"import os, sys, time\nsys.path.insert(0, {os.path.dirname(os.path.abspath(__file__))!r})\n"
               "import task_wrap\nfrom memory import Memory\n"
               "db = sys.argv[sys.argv.index('--memory') + 1]\nprint('lumber stub', flush=True)\n"
               "for _ in range(300):\n    m = Memory(db)\n"
               "    asked = task_wrap.finish_requested(m, os.environ['UO_TASK_ID'])\n    m.close()\n"
               "    if asked:\n        print('stop requested: boards stored after trip 1', flush=True)\n"
               "        sys.exit(0)\n    time.sleep(0.1)\nsys.exit(3)\n"),
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
    code, out = c("run", "hunt")
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
    code, out = c("act", "say", "where are the trees", "--human", "off")
    check("free text refused without a speech hold (allowlist only)", code == 1 and proxy.take() == [], str(out))

    print("== a harvest job holding for speech: the overseer may answer the speaker ==")
    m = Memory(db)
    hold = m.juncture("lumber", "speech_nearby", "Vorn said 'hi there' nearby; harvesting paused", "urgent",
                      {"hold": True, "task": "lumber", "speakers": [{"serial": "0x0000ABCD", "text": "hi there"}]})
    code, out = c("act", "say", "Hi! Just chopping some wood.", "--human", "off")
    fr = [p for _, p in proxy.take()]
    check("free text while the job holds: sent as the stock 0xAD, case kept",
          code == 0 and fr == [actions.say_unicode("Hi! Just chopping some wood.")], f"{out} {fr}")
    for text, word in (("No, I'm not a bot", "bot"), ("just testing stuff", "testing"),
                       ("my script does it", "script"), ("I'm an AI", "AI")):
        code, out = c("act", "say", text, "--human", "off")
        check(f"no-reveal: {word!r} refused, nothing sent", code == 1 and word in out.get("error", "")
              and proxy.take() == [], str(out))
    code, out = c("act", "say", "x" * (ctl.SAY_MAX + 1), "--human", "off")
    check("over-long speech refused", code == 1 and proxy.take() == [], str(out))
    code, out = c("act", "walk", "2", "1", "--human", "off")
    check("other acts stay refused while the job holds", code == 1 and "say" in out.get("error", "")
          and proxy.take() == [], str(out))
    m.juncture_ack(hold)
    code, out = c("act", "say", "Hi again", "--human", "off")
    check("after the all-clear free text is refused again (the job is acting)", code == 1 and proxy.take() == [],
          str(out))
    m.close()

    code, out = c("stop", "--after-trip")
    check("stop --after-trip refused for a task that isn't a lumber run; nothing asked, the task runs on",
          code == 1 and "lumber runs only" in out.get("error", "") and meta(db, tw.finish_key(slow_id)) is None
          and json.loads(meta(db, tw.TASKS_KEY) or "[]"), str(out))
    t0 = time.time()
    code, out = c("stop")
    j = out.get("juncture") or {}
    check("stop ok", code == 0 and out.get("ok") and out.get("task_id") == slow_id and not out.get("forced"), str(out))
    check("stop -> task_failed juncture marked stopped", j.get("kind") == "task_failed"
          and j["data"].get("stopped") is True and "stopped by the overseer" in j.get("summary", ""), str(j))
    check("stopped promptly", time.time() - t0 < 15, f"{time.time() - t0:.1f}s")
    check("no running task after stop", meta(db, tw.TASKS_KEY) == "[]" and meta(db, tw.stop_key(slow_id)) is None)
    code, out = c("stop")
    check("stop with nothing running -> ok false", code == 1 and out["ok"] is False)

    code, out = c("run", "lumber")
    lumber_id = out.get("task_id")
    wait_for(lambda: any(e.get("child_pid") for e in json.loads(meta(db, tw.TASKS_KEY) or "[]")), 10)
    t0 = time.time()
    code, out = c("stop", "--after-trip")
    check("stop --after-trip: returns at once, asking the lumber run (meta task_finish names it)",
          code == 0 and out.get("after_trip") is True and out.get("task_id") == lumber_id and time.time() - t0 < 5,
          str(out))
    j = task_juncture(db, lumber_id)
    check("the run ended itself: task_done, exit 0, not marked stopped (the wrapper gave it UO_TASK_ID)",
          j is not None and j["kind"] == "task_done" and j["data"]["exit_code"] == 0 and not j["data"].get("stopped")
          and any("stop requested" in ln for ln in j["data"]["tail"]), str(j))
    check("the request cleared and no task left", wait_for(lambda: meta(db, tw.TASKS_KEY) == "[]", 5) is not None
          and meta(db, tw.finish_key(lumber_id)) is None,
          f"{meta(db, tw.TASKS_KEY)} {meta(db, tw.finish_key(lumber_id))}")

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
    code, out = c("act", "walk", "2", "1", "--human", "off")
    check("a step the map rules refuse is never sent (the client checks CanWalk first; (101,100) is off the map)",
          out.get("outcomes") == ["blocked"] and "map" in (out.get("stopped") or "") and proxy.take() == [],
          str(out))
    code, out = c("act", "walk", "2", "3", "--human", "off", "--no-map")
    fr = proxy.take()
    check("walk 3 east: moved 3", code == 0 and out.get("moved") == 3 and out.get("to")[:2] == [103, 100], str(out))
    check("walk frames: 3 x 0x02 dir 2 with the run flag (the client's Always Run), length-prefixed like Link.send",
          fr == [(7, actions.walk(2, run=True))] * 3 and actions.walk(2, run=True) == bytes.fromhex("02820000000000"),
          str(fr))
    code, out = c("act", "walk", "4", "1", "--human", "off", "--walk", "--no-map")
    fr = proxy.take()
    check("walk with a turn: turn + step; --walk clears the run flag",
          out.get("outcomes") == ["turned", "moved"] and [p for _, p in fr] == [actions.walk(4)] * 2,
          str(out))
    rows = m.chat(role="overseer")
    check("every act posts an overseer action row", rows[-1]["kind"] == "action"
          and rows[-1]["text"].startswith("act walk 4 1") and rows[-1]["data"].get("moved") == 1, str(rows[-1]))
    code, out = c("act", "walk", "9")
    check("walk dir out of range refused", code == 1 and proxy.take() == [], str(out))
    x0, y0 = proxy.pos[:2]
    proxy.pos[3] = 4
    proxy.deny_jumps = {(x0, y0 + 1): (1911, 2556)}
    code, out = c("act", "walk", "4", "--human", "off", "--no-map")
    check("walk onto a teleporter that denies, then moves you: reported as teleported (NPD exit, live)",
          code == 0 and out["outcomes"] == ["teleported"] and out["to"][:2] == [1911, 2556], str(out))
    proxy.deny_jumps = {}
    proxy.pos = [x0, y0, 0, 2]
    proxy.take()
    code, out = c("act", "walk", "2", "50")
    check("walk step cap", code == 1 and proxy.take() == [], str(out))

    code, out = c("act", "dclick", "0x40000011")
    check("dclick frame", code == 0 and [p for _, p in proxy.take()] == [bytes.fromhex("0640000011")], str(out))
    code, out = c("act", "single_click", "2")
    check("single_click on a mobile: 0x09 with its 0x34 status request, as the client pairs them",
          code == 0 and [p for _, p in proxy.take()] == [bytes.fromhex("0900000002"),
                                                         actions.status_request(2)], str(out))
    code, out = c("act", "single_click", "0x40000011")
    check("single_click on an item: 0x09 only", code == 0 and [p for _, p in proxy.take()]
          == [actions.single_click(0x40000011)], str(out))
    for serial, why in (("3", "tiles away"), ("0x00000099", "not in the client's world"),
                        ("0x40000077", "not in the client's world")):
        code, out = c("act", "dclick", serial)
        check(f"dclick refused ({why}): {serial}", code == 1 and why in out.get("error", "")
              and proxy.take() == [], str(out))
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
    proxy.fixed_mobiles = {"0x000001E5": {"x": proxy.pos[0] + 2, "y": proxy.pos[1], "name": "Len"}}
    code, out = c("act", "menu_pick", "0x000001E5", "1")
    check("menu_pick without an open context menu refused", code == 1 and "no context menu" in out.get("error", "")
          and proxy.take() == [], str(out))
    code, out = c("act", "menu", "0x00000003")
    check("menu on a mobile beyond view range refused", code == 1 and "tiles away" in out.get("error", "")
          and proxy.take() == [], str(out))
    code, out = c("act", "menu", "0x000001E5")
    fr = proxy.take()
    check("menu sends the stock right-click: 0x09 then the popup request",
          [p for _, p in fr] == [actions.single_click(0x1E5), actions.request_popup(0x1E5)], str(fr))
    ents = (out.get("menu") or {}).get("entries", [])
    check("menu returns the server's entries with index", code == 0 and [e["index"] for e in ents] == [0, 1],
          str(out))
    if have_cliloc and len(ents) > 1:
        check("menu entries rendered (Buy)", ents[1]["text"] == "Buy", str(ents))
    code, out = c("act", "menu_pick", "0x000001E5", "7")
    check("menu_pick of an index the menu doesn't offer refused", code == 1 and proxy.take() == [], str(out))
    code, out = c("act", "menu_pick", "0x000001E5", "1")
    check("menu_pick sends the stock popup selection",
          code == 0 and [p for _, p in proxy.take()] == [actions.popup_selection(0x1E5, 1)], str(out))
    proxy.fixed_mobiles = {}

    btns = "{ button 10 10 1 2 1 0 1 }{ button 10 30 1 2 1 0 2 }"
    proxy.gumps = [
        gump_row(0x100, 0x00000001, "{ textentrylimited 1 1 40 20 0 2 2 3 }" + btns),        # captcha
        gump_row(0x101, 0x5E11A1, "{ noclose }{ croppedtext -300 -200 1 1 0 0 }", ["Captcha"]),  # decoy
        gump_row(0x102, 0x22, btns, ["You have chosen to renounce your Young player status?"]),
        gump_row(0x103, 0x33, "{ noclose }" + btns, ["Resurrection"]),
        gump_row(0x106, 0x8EAEFBDB, "".join(f"{{ button 10 {10 * b} 1 2 1 0 {b} }}" for b in (3, 4, 6, 7)),
                 ["Rental Room", "End Rental Contract", "Expand", "Exit to Town", "Exit to House Steward"]),
        gump_row(0x107, 0xC0B1026D, _shelf_gumps()["dtf"]["layout"], _shelf_gumps()["dtf"]["lines"]),
        gump_row(0x108, ctl.REPORT_MURDER_GUMP_ID, "{ button 10 10 1 2 1 0 2 }{ button 10 30 1 2 1 0 3 }",
                 ["Report Murder", "Accept", "Decline"]),
    ]
    for serial, button, why in ((0x100, 2, "captcha"), (0x101, 0, "no reply buttons"), (0x102, 1, "renounce"),
                                (0x103, 0, "noclose"), (0x103, 9, "not in the gump's reply buttons"),
                                (0x104, 1, "no open gump"), (0x106, 3, "rental room: End Rental Contract"),
                                (0x106, 7, "rental room: Expand"), (0x107, 1000, "storage shelf: Restock"),
                                (0x107, 16, "storage shelf: Clear"), (0x108, 2, "Report Murder: Accept")):
        code, out = c("act", "gump", f"0x{serial:X}", str(button))
        check(f"gump refused: {why}", code == 1 and proxy.take() == [], str(out))
    code, out = c("act", "gump", "0x107", "9")
    check("storage shelf: the loadout page arrow (9) is sent",
          code == 0 and len([p for _, p in proxy.take()]) == 1, str(out))
    code, out = c("act", "gump", "0x108", "3")
    check("Report Murder: Decline (3) is sent",
          code == 0 and [p for _, p in proxy.take()] == [actions.gump_response(0x108, ctl.REPORT_MURDER_GUMP_ID, 3)],
          str(out))
    code, out = c("act", "gump", "0x102", "0")
    check("renounce prompt: closing it (button 0) is allowed",
          code == 0 and [p for _, p in proxy.take()] == [actions.gump_response(0x102, 0x22, 0)], str(out))
    code, out = c("act", "gump", "0x103", "1")
    check("a normal gump: an offered button is sent as the stock 0xB1",
          code == 0 and [p for _, p in proxy.take()] == [actions.gump_response(0x103, 0x33, 1)], str(out))
    code, out = c("act", "gump", "0x106", "6")
    check("rental room menu: Exit to House Steward (6) is sent",
          code == 0 and [p for _, p in proxy.take()] == [actions.gump_response(0x106, 0x8EAEFBDB, 6)], str(out))
    # the healer's Resurrection gump (live 0xB04C9A31, Accept 1): up again in a death robe, which comes off
    res = "{ button 10 10 1 2 1 0 1 }{ button 10 30 1 2 1 0 2 }"
    proxy.gumps.append(gump_row(0x109, ctl.RESURRECT_GUMP_ID, res, ["Resurrection", "Accept", "Decline"]))
    proxy.dead = True
    code, out = c("act", "gump", "0x109", "1")
    sent = [p for _, p in proxy.take()]
    check("resurrection Accept: the 0xB1, then the death robe dragged to the pack (lift, drop); the server takes "
          "it away (live), so it's `gone` and counts as off",
          code == 0 and out.get("resurrected") and (out.get("death_robe") or {}).get("ok")
          and out["death_robe"].get("gone") and sent[0] == actions.gump_response(0x109, ctl.RESURRECT_GUMP_ID, 1)
          and [p[0] for p in sent[1:]] == [0x07, 0x08] and sent[1][1:5] == bytes.fromhex("40000099")
          and "0x40000099" not in proxy.ground_items, (out, [p.hex() for p in sent]))
    proxy.gumps.append(gump_row(0x10A, ctl.RESURRECT_GUMP_ID, res, ["Resurrection", "Accept", "Decline"]))
    code, out = c("act", "gump", "0x10A", "2")
    check("Decline (2): only the 0xB1, nothing taken off",
          code == 0 and "death_robe" not in out and [p for _, p in proxy.take()]
          == [actions.gump_response(0x10A, ctl.RESURRECT_GUMP_ID, 2)], str(out))
    # Retrieve Items-style gump (live 0xBEC6217A): an amount entry (id 1, default "", limit 5),
    # a label to its left, a checked checkbox (id 7) and an OKAY button (2)
    shelf = ("{ text 58 99 2599 3 18 0 1 0 0 0 }{ textentrylimited 147 100 78 20 2655 1 4 5 2 2 }"
             "{ textentrylimited 147 130 78 20 2655 9 5 5 2 2 }{ checkbox 20 20 210 211 1 7 }"
             "{ button 158 134 247 248 1 0 2 }")
    proxy.gumps = [gump_row(0x105, 0xBEC6217A, shelf, ["Retrieve Items", "Available", "170", "Retrieve", "", "x"]),
                   proxy.gumps[0]]
    code, out = c("act", "gump", "0x105", "2", "--text", "1=20")
    check("--text: every entry sent like the stock client (the edited one overridden) + checked switches",
          code == 0 and [p for _, p in proxy.take()]
          == [actions.gump_response(0x105, 0xBEC6217A, 2, switches=[7], text_entries=[(1, "20"), (9, "x")])],
          str(out))
    code, out = c("act", "gump", "0x105", "2")
    check("no --text: entries still sent with their current text",
          code == 0 and [p for _, p in proxy.take()]
          == [actions.gump_response(0x105, 0xBEC6217A, 2, switches=[7], text_entries=[(1, ""), (9, "x")])],
          str(out))
    for spec, why in (("3=5", "not in the gump's entries"), ("1=123456", "limit is 5"), ("1", "ID=VALUE")):
        code, out = c("act", "gump", "0x105", "2", "--text", spec)
        check(f"--text refused: {why}", code == 1 and why in out.get("error", "") and proxy.take() == [], str(out))
    code, out = c("act", "gump", "0x100", "2", "--text", "2=326")
    check("the captcha stays refused with --text", code == 1 and "captcha" in out.get("error", "")
          and proxy.take() == [], str(out))
    code, out = c("status")
    view = next(g for g in out["gumps_open"] if g["serial"] == "0x00000105")
    ent = {e["id"]: e for e in view["controls"]["entries"]}
    check("status controls: entry value, limit and the label left of it",
          ent[1]["value"] == "" and ent[1]["limit"] == 5 and ent[1]["near"][0] == {"text": "Retrieve", "dx": -89,
                                                                                     "dy": -1}, str(view["controls"]))
    proxy.gumps = []

    proxy.pos = [100, 100, 0, 2]
    proxy.take()
    code, out = c("act", "goto", "104", "100", "--human", "off", "--no-map")
    check("goto (guarded by default): the red 3 tiles away stops it before any step (travel_guard)",
          code == 1 and "hostile player red a PK" in out.get("error", "") and out.get("steps") == 0
          and not [p for _, p in proxy.take() if p[0] == 0x02], str(out))
    # the walking mechanics below, with the red still standing there: --no-guard
    code, out = c("act", "goto", "104", "100", "--human", "off", "--no-map", "--no-guard")
    fr = proxy.take()
    check("goto x y: the Mover walks there (2D fallback in this test)",
          code == 0 and out.get("to", [])[:2] == [104, 100] and out.get("steps") == 4, str(out))
    check("goto sends only stock walks", fr and all(p[0] == 0x02 for _, p in fr), str(fr[:3]))
    it = [i for i in proxy.intents if i][-2:]
    check("goto reports 'Walking to' then 'Arrived at' (so the viz doesn't stay on Walking)",
          [(i["kind"], i["text"]) for i in it] == [("goto", "Walking to 104,100"), ("arrived", "Arrived at 104,100")],
          str(it))
    proxy.fixed_mobiles = {"0x00000004": {"x": 110, "y": 100, "z": 0, "name": "Zara", "notoriety": 7}}
    code, out = c("act", "goto", "0x00000004", "--human", "off", "--no-map", "--no-guard")
    check("goto mobile: walks until within 2 tiles of it",
          code == 0 and nav.chebyshev(tuple(out["to"][:2]), (110, 100)) <= 2
          and nav.chebyshev(tuple(out["from"][:2]), (110, 100)) > 2, str(out))
    proxy.fixed_mobiles = {}
    tx = proxy.pos[0] - 6
    proxy.last_seen = {"0x00000005": {"x": tx, "y": 100, "z": 0, "name": "Limmon",
                                      "t": time.time() - 60, "facet": 0, "why": "range"},
                       "0x00000006": {"x": 90, "y": 100, "z": 0, "name": "a mongbat", "t": time.time(),
                                      "facet": 0, "why": "dead"}}
    code, out = c("act", "goto", "0x00000005", "--human", "off", "--no-map", "--no-guard")
    check("goto a mobile out of view: walks to where the client last had it",
          code == 0 and nav.chebyshev(tuple(out["to"][:2]), (tx, 100)) <= 2
          and nav.chebyshev(tuple(out["from"][:2]), (tx, 100)) > 2, str(out))
    proxy.take()
    code, out = c("act", "goto", "0x00000006", "--no-map")
    check("goto a dead mobile refused", code == 1 and proxy.take() == [], str(out))
    proxy.last_seen = {}
    code, out = c("act", "goto", "0x00000099", "--no-map")
    check("goto unknown serial refused", code == 1 and proxy.take() == [], str(out))
    gate = "0x40000099"
    proxy.ground_items = {gate: {"graphic": 0x0F6C, "x": 113, "y": 100, "z": 0}}
    code, out = c("act", "goto", gate, "--human", "off", "--no-map", "--no-guard")
    check("goto ground item: onto its tile", code == 0 and out.get("to", [])[:2] == [113, 100], str(out))
    code, out = c("status")
    g = next((i for i in out.get("ground_items", []) if i["serial"] == gate), None)
    check("status lists nearby ground items, named from tiledata",
          g is not None and g["dist"] == 0 and (g["name"] == "blue moongate" or not os.path.exists(INSTALL_TILEDATA)),
          str(out.get("ground_items")))
    # live 2026-10-03 (session 20261003_213125): `act goto 2974 611` (and 2025,2077 / 1693,3153)
    # started 28+ tiles from the gate, out of the client's view, so the gate wasn't in the world
    # model when the goto decided which gate it meant to use; on arrival the Mover closed the
    # Moongate Destinations gump (B1 button 0) and the overseer had to double-click the gate.
    # The pre-fix ctl sends that B1 here; closing a gate only passed over: test_mover.py
    moongate_gump = (0xE0E675B8, "{ resizepic 0 0 9200 300 200 }{ button 20 20 4005 4007 1 0 1 }",
                     ["Moongate Destinations"])
    saved = list(proxy.pos), proxy.gumps
    proxy.pos, proxy.item_view, proxy.gumps = [100, 100, 0, 2], 5, []
    proxy.gate_gumps = {(113, 100): moongate_gump}
    proxy.take()
    code, out = c("act", "goto", "113", "100", "--human", "off", "--no-map", "--no-guard")
    fr = [p for _, p in proxy.take()]
    check("goto x y onto a moongate first seen on the way: its gump stays open for `act gump`",
          code == 0 and out.get("to", [])[:2] == [113, 100] and out.get("gate_gumps_closed") == 0
          and not [p for p in fr if p[0] == 0xB1] and [g["open"] for g in proxy.gumps] == [True],
          f"{out} {[p.hex() for p in fr if p[0] != 0x02]} {proxy.gumps}")
    proxy.ground_items, proxy.item_view, proxy.gate_gumps = {}, None, {}
    proxy.pos, proxy.gumps = saved
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
    # Outlands' prismatic staff (arcane staff): tiledata layer 0, worn on 2 by the server
    staff, other = 0x57064E05, 0x57064E06
    proxy.ground_items = {f"0x{staff:08X}": {"graphic": 31038, "container": f"0x{pack:08X}"},
                          f"0x{other:08X}": {"graphic": 0x1BDD, "container": f"0x{pack:08X}"}}
    proxy.worn_layers = {f"0x{staff:08X}": 2}
    code, out = c("act", "equip", f"0x{staff:08X}", "--human", "off")
    fr = [p for _, p in proxy.take()]
    check("equip: tiledata layer 0 -> the layer the server last wore it on (2), same stock lift + 0x13",
          code == 0 and out.get("moved") and uomap_layer(31038) == 0
          and fr == [actions.lift(staff, 1), actions.equip_request(staff, 2, proxy.SELF)], f"{out} {fr}")
    code, out = c("act", "equip", f"0x{other:08X}", "--human", "off")
    check("equip refused: no tiledata layer and never seen worn (logs: not wearable)",
          code == 1 and "never seen worn" in out.get("error", "") and proxy.take() == [], str(out))
    proxy.ground_items, proxy.worn_layers = {}, {}


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
    check("attack a monster: war mode on first, then 0x34 (hits unknown) and 0x05 (Tab, double-click)",
          code == 0 and fr == [actions.war_mode(True), actions.status_request(0x10), actions.attack(0x10)]
          and out["warmode_turned_on"] and out["target"]["kind"] == "monster", f"{out} {fr}")
    last = proxy.intents[-1] or {}
    check("attack reports an 'attack' intent that follows the mob (target_serial)",
          last.get("kind") == "attack" and last.get("target_serial") == "0x00000010"
          and last.get("text") == "Attacking a mongbat", str(last))
    proxy.fixed_mobiles["0x00000010"].update(hits=4, hits_max=4)
    code, out = c("act", "attack", "0x00000010", "--human", "off")
    check("hits already known (unsolicited updates) but no status request out: 0x34 then 0x05, as the "
          "client's RequestMobileStatus", code == 0 and [p for _, p in proxy.take()]
          == [actions.status_request(0x10), actions.attack(0x10)] and not out["warmode_turned_on"], str(out))
    proxy.status_requested = ["0x00000010"]
    code, out = c("act", "attack", "0x00000010", "--human", "off")
    check("status request already out for the mob: only the attack", code == 0 and [p for _, p in proxy.take()]
          == [actions.attack(0x10)], str(out))
    proxy.status_requested = []
    code, out = c("act", "dclick", "0x00000010")
    check("dclick on a mobile in war mode refused (the client would attack)",
          code == 1 and "war mode" in out.get("error", "") and proxy.take() == [], str(out))
    code, out = c("act", "warmode", "on")
    check("warmode on while already on: nothing sent (Tab only flips it)",
          code == 0 and out["warmode"] is True and proxy.take() == [], str(out))
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


def test_npcs(proxy):
    print("== npcs: mobiles in view plus the ones the client dropped (last_seen) ==")
    tmp = tempfile.mkdtemp()
    c = Ctl(os.path.join(tmp, "harness.db"), os.path.join(tmp, "tasks"), proxy)
    now = time.time()
    proxy.last_seen = {
        "0x00000020": {"x": proxy.pos[0] - 40, "y": proxy.pos[1], "name": "Sherwin", "notoriety": 7,
                       "t": now - 30, "facet": 0, "why": "range"},
        "0x00000021": {"x": proxy.pos[0] - 5, "y": proxy.pos[1], "name": "a mongbat", "notoriety": 3,
                       "t": now - 5, "facet": 0, "why": "dead"}}
    proxy.labels = {"0x00000020": "Sherwin the mage"}
    code, out = c("status")
    check("status: only the mobiles the client has (no last_seen ones)",
          not {"0x00000020", "0x00000021"} & {m["serial"] for m in out["mobiles"]}, str(out["mobiles"]))
    code, out = c("npcs", "mage")
    r = (out.get("npcs") or [{}])[0]
    check("npcs finds the far mage by title: last-seen position, in_view False, why and age",
          code == 0 and len(out["npcs"]) == 1
          and (r["serial"], r["label"], r["dist"], r["in_view"], r["why"]) == ("0x00000020", "Sherwin the mage", 40,
                                                                            False, "range")
          and 29 <= r["age_s"] <= 40, str(out))
    code, out = c("npcs")
    check("npcs without words: live and last-seen but you and the dead, nearest first",
          [(r["serial"], r["in_view"]) for r in out["npcs"]]
          == [("0x00000002", True), ("0x00000020", False), ("0x00000003", True)], str(out["npcs"]))
    proxy.last_seen, proxy.labels = {}, {}


def test_attackers(proxy):
    print("== status: attackers (S2C 0x2F swings at us) and mobile age ==")
    tmp = tempfile.mkdtemp()
    c = Ctl(os.path.join(tmp, "harness.db"), os.path.join(tmp, "tasks"), proxy)
    now = time.time()
    x, y = proxy.pos[0], proxy.pos[1]
    proxy.fixed_mobiles = {
        "0x00000030": {"x": x + 2, "y": y, "name": "a mongbat", "hits": 80, "hits_max": 100, "seen_t": now - 1.5},
        "0x00000031": {"x": x + 1, "y": y, "name": "a mongbat", "seen_t": now},
        "0x00000032": {"x": x + 4, "y": y, "name": "a ratman", "seen_t": now},
        "0x00000033": {"x": x + 5, "y": y, "name": "a guard", "seen_t": now}}
    me, other = f"0x{proxy.SELF:08X}", "0x00000002"
    proxy.swings = {"0x00000030": {"defender": me, "t": now - 3},
                    "0x00000031": {"defender": me, "t": now - 0.5},
                    "0x00000032": {"defender": me, "t": now - 20},       # stale: not attacking now
                    "0x00000033": {"defender": other, "t": now - 1},     # fighting someone else
                    "0x00000034": {"defender": me, "t": now - 1}}        # not in the client's world
    code, out = c("status")
    att = out.get("attackers") or []
    check("attackers: swings at us within 10 s by mobiles the client has, nearest first",
          [(a["serial"], a["dist"]) for a in att] == [("0x00000031", 1), ("0x00000030", 2)], str(att))
    check("attackers carry hits and the swing age",
          att[1]["hits"] == [80, 100] and 2.5 <= att[1]["last_swing_age_s"] <= 5, str(att))
    ages = {m["serial"]: m["age_s"] for m in out["mobiles"]}
    check("mobiles[].age_s since the server last updated it (None when unknown)",
          1.4 <= ages["0x00000030"] <= 4 and ages["0x00000002"] is None, str(ages))
    proxy.fixed_mobiles, proxy.swings = {}, {}


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
          code == 0 and fr == [actions.single_click(vendor), actions.request_popup(vendor),
                               actions.popup_selection(vendor, 1), actions.buy_request(vendor, [(0x40000202, 3)])]
          and out["paid"] == 30 and out["gold"] == 80, f"{out} {fr}")
    spends = Memory(db).job_events("gold")
    check("the spend is recorded for the daily cap", [(e["kind"], e["data"]["total"]) for e in spends]
          == [("spend", 30)], str(spends))
    code, out = c("act", "buy", f"0x{vendor:08X}", "potion", "--human", "off")
    check("ambiguous item refused", code == 1 and "ambiguous" in out.get("error", ""), str(out))
    code, out = c("act", "buy", f"0x{vendor:08X}", "bandage", "--amount", "50", "--human", "off")
    fr = [p for _, p in proxy.take()]
    check("more than the pack's gold (50 x 3 > 80): the vendor takes it from the bank, the spend is recorded",
          code == 0 and out["paid"] == 150 and out["from_bank"] == 150 and out["gold"] == 80
          and fr[-1] == actions.buy_request(vendor, [(0x40000201, 50)])
          and [e["data"]["total"] for e in Memory(db).job_events("gold")] == [30, 150], str(out))
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


def test_heal(proxy):
    print("== heal: a potion whenever possible, else Heal / Greater Heal by the missing hits ==")
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "harness.db")
    c = Ctl(db, os.path.join(tmp, "tasks"), proxy)
    bag, pot = 0x40000012, 0x40000320
    x, y, z = proxy.pos[:3]
    me_target = actions.target_object(0x78, 1, x, y, z, 0x190, 2)
    proxy.opened, proxy.cast_cursor = [proxy.PACK], True
    proxy.ground_items = {f"0x{pot:08X}": {"graphic": healing.HEAL_POTION_GRAPHIC, "amount": 2,
                                            "container": f"0x{bag:08X}"},
                          # a spellstone pays for the spells (combat.can_cast; no reagents carried)
                          "0x40000321": {"graphic": 0x3F1F, "container": f"0x{proxy.PACK:08X}",
                                         "name": "arielle's bauble"}}
    proxy.potion_answers = {f"0x{pot:08X}": healing.CLILOC_HEALED}
    proxy.self_hits = 50                                    # 10 of 60 missing; Magery 60, mana 20
    proxy.take()
    code, out = c("act", "heal", "--human", "off")
    check("a potion first: its bag opened (stock double-click), then the potion's; nothing cast",
          code == 0 and out.get("used") == "potion"
          and [p for _, p in proxy.take()] == [actions.dclick(bag), actions.dclick(pot)], str(out))
    code, out = c("act", "heal", "--human", "off")
    check("within 10 s of that drink, 10 missing (below the break-even 19 at Magery 60): Heal on yourself",
          code == 0 and out.get("spell") == "Heal"
          and [p for _, p in proxy.take()] == [actions.cast_spell(healing.HEAL), me_target], str(out))
    proxy.self_hits = 30
    code, out = c("act", "heal", "--human", "off")
    check("30 missing, potion still cooling down: Greater Heal on yourself",
          code == 0 and out.get("spell") == "Greater Heal"
          and [p for _, p in proxy.take()] == [actions.cast_spell(healing.GREATER_HEAL), me_target], str(out))
    m = Memory(db)
    tw.meta_set(m, ctl.HEAL_POTION_KEY, f"{time.time() - 60:.2f}")   # ctl's clock: ready again
    m.close()
    proxy.potion_answers = {f"0x{pot:08X}": healing.CLILOC_POTION_WAIT}   # but the client drank one
    code, out = c("act", "heal", "--human", "off")
    check("the server refuses the potion (500235): Greater Heal in the same call",
          code == 0 and out.get("potion_refused") == f"0x{pot:08X}" and out.get("spell") == "Greater Heal"
          and [p for _, p in proxy.take()] == [actions.dclick(pot), actions.cast_spell(healing.GREATER_HEAL),
                                               me_target], str(out))
    proxy.mana, proxy.ground_items = 3, {}
    code, out = c("act", "heal", "--human", "off")
    check("no potion and 3 mana: refused, nothing sent",
          code == 1 and "no heal possible" in out.get("error", "") and proxy.take() == [], str(out))
    proxy.mana, proxy.self_hits = 20, 60
    code, out = c("act", "heal", "--human", "off")
    check("at full health: nothing sent", code == 0 and out.get("missing") == 0 and proxy.take() == [], str(out))
    proxy.self_hits, proxy.cast_cursor, proxy.potion_answers, proxy.opened = 50, False, {}, [proxy.PACK]
    proxy.target = {"active": False, "target_type": None, "cursor_id": None, "cursor_type": None}
    reset_events(proxy)


def reset_events(proxy):
    """Drop the fake's event ring (tests that read `journal` expect only their own events)."""
    with proxy.lock:
        proxy.events.clear()


def test_drop(proxy):
    print("== drop: move items between containers (no harness-side container limits) ==")
    tmp = tempfile.mkdtemp()
    c = Ctl(os.path.join(tmp, "harness.db"), os.path.join(tmp, "tasks"), proxy)
    pack, bank = f"0x{proxy.PACK:08X}", "0x40000500"
    proxy.ground_items = {
        bank: {"graphic": 0x0E7C, "layer": 0x1D, "container": "0x00000001"},
        "0x40000501": {"graphic": 0x0EED, "amount": 98, "container": pack, "name": "Gold"},
        "0x40000502": {"graphic": 0x1BD7, "amount": 10, "container": pack, "name": "Boards"},
        "0x40000503": {"graphic": 0x0E75, "container": "0x00000009"},                 # someone else's bag
        "0x40000504": {"graphic": 0x0E75, "container": bank},                          # a bag in the bank
        "0x40000505": {"graphic": 0x0F0C, "amount": 1, "container": "0x40000504"},    # an item in the bank
    }
    proxy.opened = [proxy.PACK, 0x40000500]          # the bank box opened by saying `bank`
    proxy.take()
    code, out = c("act", "drop", "0x40000501", bank, "--human", "off")
    fr = [p for _, p in proxy.take()]
    check("gold into the open bank box: stock lift + drop into the box; merged gold counts as moved",
          code == 0 and out["into"] == "bank" and out["moved"]
          and fr == [actions.lift(0x40000501, 98), actions.drop(0x40000501, ctl.DROP_AUTO, ctl.DROP_AUTO, 0, 0,
                                                                0x40000500)], f"{out} {fr}")
    code, out = c("act", "drop", "0x40000502", "0x40000504", "--amount", "4", "--human", "off")
    fr = [p for _, p in proxy.take()]
    check("part of a stack into a closed bag in the bank (--amount): the bag is opened first, then the stock "
          "lift + drop; the lifted serial moved with 4, 6 stayed behind (live split)",
          code == 0 and out["moved"] and proxy.ground_items["0x40000502"]["amount"] == 4
          and proxy.ground_items["0x40000502"]["container"] == "0x40000504"
          and proxy.ground_items["0x4000FFFF"]["amount"] == 6
          and out.get("opened") == ["0x40000504"]
          and fr[:2] == [actions.dclick(0x40000504), actions.lift(0x40000502, 4)], f"{out} {fr}")
    proxy.ground_items["0x40000507"] = {"graphic": 0x2006, "amount": 0x27, "x": 5, "y": 5}          # a corpse
    proxy.ground_items["0x40000508"] = {"graphic": 0x0EED, "amount": 22, "container": "0x40000507"}
    proxy.ground_items["0x40000509"] = {"graphic": 0x0E75, "layer": 0x1A, "container": "0x00000009"}  # vendor stock
    proxy.opened = [0x40000507, 0x40000509, proxy.PACK]
    code, out = c("status")
    boxes = {b["serial"]: b for b in out.get("containers", [])}
    bank_rows = {r["serial"]: r for r in boxes.get(bank, {}).get("items", [])}
    check("status.containers: the bank box with its items at any depth (sub-bag noted)",
          boxes.get(bank, {}).get("kind") == "bank" and bank_rows.get("0x40000502", {}).get("amount") == 4
          and bank_rows.get("0x40000505", {}).get("in") == "0x40000504", str(boxes.get(bank)))
    check("status.containers: an opened corpse listed; the backpack and vendor stock left out",
          set(boxes) == {bank, "0x40000507"} and boxes["0x40000507"]["kind"] == "corpse"
          and boxes["0x40000507"]["items"][0]["amount"] == 22, str(sorted(boxes)))
    proxy.opened = [proxy.PACK, 0x40000500, 0x40000504]
    for s in ("0x40000507", "0x40000508", "0x40000509"):
        del proxy.ground_items[s]
    code, out = c("act", "drop", "0x40000505", pack, "--human", "off")
    check("from a bag in the bank into the backpack (bank -> pack, overseer request)",
          code == 0 and out["from"] == "bank" and out["into"] == "backpack"
          and proxy.ground_items["0x40000505"]["container"] == pack
          and [p for _, p in proxy.take()][0] == actions.lift(0x40000505, 1), str(out))
    proxy.ground_items["0x40000506"] = {"graphic": 0x0EED, "amount": 5, "container": "0x40000503"}
    proxy.opened.append(0x40000503)                  # opened earlier (a chest the server showed us)
    code, out = c("act", "drop", "0x40000506", pack, "--human", "off")
    check("from any other container (user: no container limits; the server decides)",
          code == 0 and out["from"] == "0x00000009" and out["into"] == "backpack"
          and [p for _, p in proxy.take()][0] == actions.lift(0x40000506, 5), str(out))
    proxy.ground_items["0x40000504"]["container"] = bank
    for args, why in ((("0x40000504", "0x40000504"), "into itself"),
                      ((bank, "0x40000504"), "into itself"),
                      (("0x40009998", pack), "not known"),
                      (("0x4000FFFF", "0x40009999"), "not known"),
                      (("0x4000FFFF", pack, "--amount", "7"), "--amount must be 1..6")):
        code, out = c("act", "drop", *args, "--human", "off")
        check(f"drop refused: {why}", code == 1 and why in out.get("error", "") and proxy.take() == [], str(out))

    print("== containers are opened like a player would, outermost first ==")
    proxy.ground_items["0x4000050A"] = {"graphic": 0x0E76, "container": pack}                  # a pouch
    proxy.ground_items["0x4000050B"] = {"graphic": 0x0F0C, "amount": 2, "container": "0x4000050A"}
    proxy.opened = []                                # nothing opened yet: not even the backpack
    code, out = c("act", "drop", "0x4000050B", pack, "--human", "off")
    fr = [p for _, p in proxy.take()]
    check("an item in a pouch in the closed backpack: backpack, then pouch, then lift + drop",
          code == 0 and out.get("opened") == [pack, "0x4000050A"]
          and fr == [actions.dclick(proxy.PACK), actions.dclick(0x4000050A), actions.lift(0x4000050B, 2),
                     actions.drop(0x4000050B, ctl.DROP_AUTO, ctl.DROP_AUTO, 0, 0, proxy.PACK)], f"{out} {fr}")
    code, out = c("act", "drop", "0x4000050B", "0x4000050A", "--human", "off")
    fr = [p for _, p in proxy.take()]
    check("opened once is open as far as the server knows: nothing re-opened",
          code == 0 and "opened" not in out and fr[0] == actions.lift(0x4000050B, 2), f"{out} {fr}")
    code, out = c("act", "drop", "0x40000502", bank, "--human", "off")
    check("into the bank box the server never opened: refused (only saying `bank` opens it), nothing sent",
          code == 1 and "say `bank`" in out.get("error", "") and proxy.take() == [], str(out))
    proxy.ground_items = {}
    proxy.opened = [proxy.PACK]


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
          rows and rows[-1]["kind"] == "memory" and rows[-1]["text"].startswith("inscribing #1 fact [innkeeper]")
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


def test_track(proxy):
    print("== track: Tracking's Hunting mode through the gump, like a player ==")
    tmp = tempfile.mkdtemp()
    c = Ctl(os.path.join(tmp, "harness.db"), os.path.join(tmp, "tasks"), proxy)
    proxy.take()
    proxy.tracker = {"mode": tracking.MODES.index("innocent players"), "heard": False, "hunting": False}
    code, out = c("act", "track", "reds", "--human", "off")
    fr = [p for _, p in proxy.take()]
    check("skill use is the stock packet seen live (12 0009 24 '38 0')",
          fr[:1] == [bytes.fromhex("120009243338203000")], str([p.hex() for p in fr[:1]]))
    # mode unknown to the client: one step forward tells it (innocent -> friendly), then the short
    # way round to murderer (3 back: criminal wraps to murderer), then Begin Hunting
    check("reds: learn the mode in one step, take the shorter way round, then begin hunting",
          code == 0 and out["clicks"] == ["use Tracking", "next mode", "previous mode", "previous mode",
                                          "previous mode", "begin hunting"]
          and out["tracking"]["hunting"] and out["tracking"]["mode"] == "murderer players"
          and proxy.tracker == {"mode": 9, "heard": True, "hunting": True}, str(out)[:400])
    check("every click is a stock 0xB1 on the gump the server sent last",
          all(p[0] == 0xB1 for p in fr[1:]) and len({p[3:7] for p in fr[1:]}) == len(fr) - 1, str(len(fr)))
    code, out = c("act", "track", "murderer", "--human", "off")
    check("already hunting that mode: nothing sent (the open gump says Stop Hunting)",
          code == 0 and out["clicks"] == [] and proxy.take() == [], str(out)[:300])
    code, out = c("act", "track", "all", "hostile", "--human", "off")
    check("another mode while hunting: stop, two steps back, begin again (no skill use: the gump is open)",
          code == 0 and out["clicks"] == ["stop hunting", "previous mode", "previous mode", "begin hunting"]
          and proxy.tracker["mode"] == tracking.MODES.index("all hostile players") and proxy.tracker["hunting"],
          str(out)[:300])
    proxy.take()
    code, out = c("act", "track", "off", "--human", "off")
    check("off: Stop Hunting", code == 0 and out["clicks"] == ["stop hunting"] and not proxy.tracker["hunting"],
          str(out)[:300])
    proxy.take()
    code, out = c("act", "track", "off", "--human", "off")
    check("off when not hunting: nothing sent", code == 0 and out["clicks"] == [] and proxy.take() == [],
          str(out)[:300])
    code, out = c("act", "track", "players", "--human", "off")
    check("an ambiguous mode is refused, nothing sent", code == 1 and "ambiguous" in out.get("error", "")
          and proxy.take() == [], str(out)[:300])
    proxy.tracker, proxy.gumps = None, []


def _room_gumps() -> dict:
    with open(os.path.join(HERE, "testdata", "room_gumps.json"), encoding="utf-8") as f:
        return json.load(f)


def test_room_buttons():
    print("== room: the rental room menus' buttons by their labels (live gumps, DTF guild house) ==")
    g = _room_gumps()
    find = lambda key, label: room.labelled_button(g[key], label)  # noqa: E731
    check("steward menu without a room: 'Visit Other Rooms' is 2, no 'Enter Your Room'",
          find("steward_no_room", "Visit Other Rooms") == 2 and find("steward_no_room", "Enter Your Room") is None)
    check("the visit list: Logan Wolf's row is 100", find("visit_list", "Logan Wolf") == 100)
    check("the door: Exit to House Steward 6 and Exit to Town 4, not the View Players button between them",
          find("door", "Exit to House Steward") == 6 and find("door", "Exit to Town") == 4)


class RoomIO:
    """room.enter/leave's IO over a scripted server: each packet the flow sends is
    answered by `answer(pkt)` -> world events (the live room gumps)."""

    KEEPER, DOOR = 0x009F57FB, 0x5CDC6B4F

    def __init__(self, facet: int, pos, keeper_at=(4138, 1431), gumps=None):
        self.gumps = gumps or _room_gumps()
        self.state = {"movement": {"pos": list(pos)},
                      "world": {"self": {"serial": "0x00001234", "map": facet},
                                "mobiles": {f"0x{self.KEEPER:08X}": {"x": keeper_at[0], "y": keeper_at[1]},
                                            "0x00000777": {"x": pos[0], "y": pos[1] + 1}},
                                "labels": {f"0x{self.KEEPER:08X}": "Chase the house steward",
                                           "0x00000777": "a rabbit"},
                                "items": {f"0x{self.DOOR:08X}": {"x": 403, "y": 929, "graphic": 0x0675},
                                          "0x4AE0DD2C": {"x": 404, "y": 922, "graphic": 0x0E43}}}}
        self.sent, self.pending, self.walked = [], [], []
        self.menu = "steward_no_room"           # what saying "room" by the keeper opens

    def gump(self, key: str, serial: int) -> dict:
        return {"ev": "gump_open", "serial": f"0x{serial:08X}", **self.gumps[key]}

    def send(self, pkt: bytes):
        self.sent.append(pkt)
        if pkt == actions.say_unicode(room.ROOM_WORD):
            self.pending.append(self.gump(self.menu, 0x501))
        elif pkt == actions.dclick(self.DOOR):
            self.pending.append(self.gump("door", 0x502))
        elif pkt[0] == 0xB1:
            serial, button = int.from_bytes(pkt[3:7], "big"), int.from_bytes(pkt[11:15], "big")
            if (serial, button) == (0x501, 2):
                self.pending.append(self.gump("visit_list", 0x503))
            elif (serial, button) in ((0x503, 100), (0x501, 9)):
                self.state["world"]["self"]["map"] = 3
                self.state["movement"]["pos"] = [403, 923, 1]
                self.pending += [{"ev": "map_change", "map": 3},
                                 {"ev": "speech_heard", "serial": 0xFFFFFFFF, "text": room.ENTERED}]
            elif serial == 0x502 and button in (4, 6):
                self.state["world"]["self"]["map"] = 0
                self.state["movement"]["pos"] = [4134, 1429, 6]
                self.pending += [{"ev": "speech_heard", "serial": 0xFFFFFFFF, "text": room.EXITED},
                                 {"ev": "map_change", "map": 0}]

    def poll(self):
        new, self.pending = self.pending, []
        return self.state, new

    def walk(self, serial: int, rng: int):
        self.walked.append((serial, rng))
        self.state["movement"]["pos"] = [4138, 1433, 6]

    def replies(self) -> list:
        """(gump serial, button) of every 0xB1 sent."""
        return [(int.from_bytes(p[3:7], "big"), int.from_bytes(p[11:15], "big")) for p in self.sent if p[0] == 0xB1]


def test_room_flow():
    print("== room: enter / leave against the live room gumps (fake io, no proxy) ==")
    human = humanize.Human("off", seed=1)
    tile_name = room._tile_name
    room._tile_name = lambda graphic: "wooden door" if graphic == 0x0675 else "paragon chest"
    try:
        io = RoomIO(0, (4134, 1429, 6))
        check("find_keeper: the steward by his click label, not the nearer rabbit",
              room.find_keeper(io.state) == (RoomIO.KEEPER, "Chase the house steward"))
        out = room.enter(io, human, ["logan"], walk=io.walk, timeout=0.5)
        k = RoomIO.KEEPER
        check("enter logan: walked to the steward (2 tiles), said 'room' (no click, no context menu), "
              "Visit Other Rooms, the row",
              out["ok"] and io.walked == [(k, room.KEEPER_RANGE)]
              and [p for p in io.sent if p[0] != 0xB1] == [actions.say_unicode("room")]
              and io.replies() == [(0x501, 2), (0x503, 100)], str(out)[:300])
        check("enter: arrival on facet 3 at 403,923 with the room and keeper named",
              out["facet"] == 3 and out["pos"][:2] == [403, 923] and out["room"] == "Logan Wolf (DTF)"
              and out["via"] == "Chase the house steward"
              and any(e.get("text") == room.ENTERED for e in out["heard"]), str(out)[:300])
        check("enter when already inside: nothing sent",
              room.enter(io, human, walk=io.walk)["already"] and len(io.sent) == 3)

        io = RoomIO(0, (4137, 1431, 6))
        own = dict(io.gumps["steward_no_room"])
        own["layout"] = own["layout"].replace("\x00", "") + "{ text 223 478 2599 8 18 0 1 0 0 0 }{ button 188 475 2151 2154 1 0 9 }"
        own["lines"] = own["lines"] + ["Enter Your Room"]
        io.gumps = {**io.gumps, "own_room": own}
        io.menu = "own_room"
        out = room.enter(io, human, walk=None, timeout=0.5)
        check("enter with no owner and an 'Enter Your Room' button: that button, no walk (in range)",
              out["ok"] and out["room"] == "your own" and io.replies() == [(0x501, 9)], str(out)[:300])

        io = RoomIO(0, (4137, 1431, 6))
        try:
            room.enter(io, human, ["nobody"], timeout=0.5)
            check("enter nobody: refused", False)
        except room.RoomError as e:
            check("enter with no matching row: the list is closed with 0 and the error names the rooms",
                  io.replies() == [(0x501, 2), (0x503, 0)] and "Logan Wolf (DTF)" in str(e), str(e))
        io = RoomIO(0, (4137, 1431, 6))
        io.menu = "visit_list"            # a menu without 'Visit Other Rooms' (nor 'Enter Your Room')
        io.gumps = {**io.gumps, "visit_list": {**io.gumps["visit_list"],
                                               "lines": [t.replace("Logan", "x") for t in io.gumps["visit_list"]["lines"]]}}
        try:
            room.enter(io, human, ["logan"], timeout=0.5)
            check("menu without the step: refused", False)
        except room.RoomError as e:
            check("a menu missing the step is closed with 0 and raises", io.replies() == [(0x501, 0)]
                  and "Visit Other Rooms" in str(e), str(e))
        io = RoomIO(0, (4134, 1429, 6))
        try:
            room.enter(io, human, ["logan"], timeout=0.5)
            check("keeper far, no walker: refused", False)
        except room.RoomError as e:
            check("keeper farther than 2 tiles and no walker: raises before any packet", io.sent == [], str(e))

        io = RoomIO(3, (403, 923, 1))
        out = room.leave(io, human, timeout=0.5)
        check("leave: the door double-clicked, Exit to House Steward (6), back on facet 0",
              out["ok"] and io.sent[0] == actions.dclick(RoomIO.DOOR) and io.replies() == [(0x502, 6)]
              and out["exit"] == "the house steward" and out["facet"] == 0, str(out)[:300])
        io = RoomIO(3, (403, 923, 1))
        out = room.leave(io, human, ("town", "steward"), timeout=0.5)
        check("leave preferring town: Exit to Town (4)", out["ok"] and io.replies() == [(0x502, 4)]
              and out["exit"] == "town", str(out)[:300])
        flow = room._Flow(RoomIO(3, (403, 923, 1)), human, 0.5)
        door = {"ev": "gump_open", "serial": "0x00000502", **_room_gumps()["door"]}
        for b in room.ROOM_REFUSED:
            try:
                flow.press(door, b, lambda e: False, "test", timeout=0.0)
                check(f"refused button {b} pressed", False)
            except room.RoomError:
                check(f"never a refused button: {b} ({room.ROOM_REFUSED[b]}) raises, nothing sent",
                      flow.io.sent == [])
        try:
            room.leave(RoomIO(0, (4134, 1429, 6)), human)
            check("leave outside: refused", False)
        except room.RoomError as e:
            check("leave when not in a room: raises", "not in a rental room" in str(e), str(e))
    finally:
        room._tile_name = tile_name


def _shelf_gumps() -> dict:
    with open(os.path.join(HERE, "testdata", "shelf_gumps.json"), encoding="utf-8") as f:
        return json.load(f)


class ShelfIO:
    """shelf.resupply's IO over a scripted server: the DTF guild house's two shelves on one
    tile (live 2026-10-04: the first answers "That is secure.", the second opens the live
    gump); Resupply (7) adds a trapped pouch to the pack and says what it couldn't give."""

    ME, PACK, SECURED, OPEN = 0x00001234, 0x46F90508, 0x40050A3B, 0x40B84C55

    def __init__(self, missing=("Hatchet",), adds=True):
        self.state = {"movement": {"pos": [4134, 1429, 6], "self_serial": self.ME},
                      "world": {"self": {"serial": f"0x{self.ME:08X}", "map": 0}, "labels": {},
                                "items": {f"0x{self.PACK:08X}": {"layer": 0x15, "container": f"0x{self.ME:08X}",
                                                                 "graphic": 0x0E75},
                                          "0x5CF80710": {"container": f"0x{self.PACK:08X}", "graphic": 0x0E79,
                                                         "hue": 38, "amount": 1},
                                          f"0x{self.SECURED:08X}": {"x": 4133, "y": 1427, "graphic": 0xDC38},
                                          f"0x{self.OPEN:08X}": {"x": 4133, "y": 1427, "graphic": 0xDC38}}}}
        self.sent, self.pending, self.missing, self.adds = [], [], list(missing), adds

    def gump(self, serial: int) -> dict:
        return {"ev": "gump_open", "serial": f"0x{serial:08X}", **_shelf_gumps()["dtf"]}

    def send(self, pkt: bytes):
        self.sent.append(pkt)
        if pkt == actions.dclick(self.SECURED):
            self.pending.append({"ev": "cliloc", "cliloc": shelf.SECURE_CLILOC, "text": "That is secure."})
        elif pkt == actions.dclick(self.OPEN):
            self.pending.append(self.gump(0x601))
        elif pkt[0] == 0xB1 and int.from_bytes(pkt[11:15], "big") == 7:
            if self.adds:
                self.state["world"]["items"]["0x6064D7DE"] = {"container": f"0x{self.PACK:08X}", "graphic": 0x0E79,
                                                              "hue": 38, "amount": 1}
            self.pending += [{"ev": "speech_heard", "serial": 0xFFFFFFFF, "text": f"No resupply: {m}"}
                             for m in self.missing]
            if not self.adds and not self.missing:
                self.pending.append({"ev": "speech_heard", "serial": 0xFFFFFFFF, "text": shelf.NONE_AVAILABLE})
            self.pending.append(self.gump(0x602))

    def poll(self):
        new, self.pending = self.pending, []
        return self.state, new

    def replies(self) -> list:
        return [(int.from_bytes(p[3:7], "big"), int.from_bytes(p[11:15], "big")) for p in self.sent if p[0] == 0xB1]


def test_convert(proxy):
    print("== act convert: logs in a live trapped pouch and loose in the pack into boards (convert.py) ==")
    tmp = tempfile.mkdtemp()
    c = Ctl(os.path.join(tmp, "harness.db"), os.path.join(tmp, "tasks"), proxy)
    pack = f"0x{proxy.PACK:08X}"
    hatchet, pch, colored, plain = 0x44AD0001, 0x44AD0002, 0x44AD0003, 0x44AD0004
    proxy.ground_items = {
        f"0x{hatchet:08X}": {"graphic": 0x0F43, "hue": 0, "container": pack},
        f"0x{pch:08X}": {"graphic": 0x0E79, "hue": 38, "container": pack},
        f"0x{colored:08X}": {"graphic": 0x1BDE, "amount": 7, "hue": 0x7DA, "container": f"0x{pch:08X}"},
        f"0x{plain:08X}": {"graphic": 0x1BDD, "amount": 3, "container": f"0x{pch:08X}"}}
    proxy.convert_sim = {"hatchet": hatchet, "no_cursor_once": True}
    proxy.opened, hits = [proxy.PACK], proxy.self_hits
    proxy.take()
    code, out = c("act", "convert", "--human", "off")
    fr = [p for _, p in proxy.take()]
    targets = [int.from_bytes(p[7:11], "big") for p in fr if p[0] == 0x6C]
    check("every stack converted: the 5 loose logs and the 7 + 3 in the trapped pouch, 15 boards, none left",
          code == 0 and out["ok"] and out["boards"] == 15 and out["logs_left"] == 0
          and sorted((s["serial"], s["logs"], s["boards"]) for s in out["converted"])
          == [("0x40000011", 5, 5), (f"0x{colored:08X}", 7, 7), (f"0x{plain:08X}", 3, 3)]
          and out["hatchet"] == f"0x{hatchet:08X}", str(out))
    check("the live pouch set off first by our double-click (a hit), then opened like a bag; no log targeted "
          "while it was live",
          fr[0] == actions.dclick(pch) and out["pouches"] == [f"0x{pch:08X}"] and proxy.self_hits == hits - 1
          and fr.count(actions.dclick(pch)) == 2 and pch in proxy.opened
          and not proxy.convert_sim.get("refused"), [p.hex() for p in fr[:4]])
    check("a hatchet use without a cursor is tried again (convert.CONVERT_RETRIES); one target per stack",
          out["no_cursor"] == 1 and out["tries"] == 4 and fr.count(actions.dclick(hatchet)) == 4
          and sorted(targets) == sorted([0x40000011, colored, plain]), str(out))
    code, out = c("act", "convert", "--human", "off")
    check("no logs left: refused, nothing sent", code == 1 and "no logs" in out.get("error", "")
          and proxy.take() == [], str(out))
    proxy.ground_items["0x44AD0005"] = {"graphic": 0x1BDD, "amount": 4, "container": pack}
    code, out = c("act", "convert", "--store", "--human", "off")
    check("--store: converted, then the stockpile (TestWorth has no home with one: its error), no restock",
          code == 1 and out["boards"] == 4 and out["logs_left"] == 0 and not out["stockpile"]["ok"]
          and "Resource Stockpile" in out["stockpile"]["error"] and out["restock"] is None
          and "no restock" in out["reply"], str(out))
    proxy.ground_items, proxy.gone, proxy.convert_sim, proxy.self_hits = {}, set(), None, hits
    proxy.opened = [proxy.PACK]
    proxy.target = {"active": False, "target_type": None, "cursor_id": None, "cursor_type": None}
    proxy.take()


def test_shelf_flow():
    print("== shelf: Resupply against the live Storage Shelf gump (fake io, no proxy) ==")
    human = humanize.Human("off", seed=1)
    tile_name = shelf._tile_name
    shelf._tile_name = lambda graphic: "spring storage shelf" if graphic == 0xDC38 else "pouch"
    try:
        check("the live gump's Resupply button by its label: 7",
              room.labelled_button(_shelf_gumps()["dtf"], "Resupply") == 7)
        io = ShelfIO()
        out = shelf.resupply(io, human, timeout=0.3)
        check("the secured shelf ('That is secure.') is skipped, the next opened, Resupply (7) pressed, the "
              "shelf's answer closed (0); nothing else sent (never Restock 1000 or Clear 16)",
              out["ok"] and out["shelf"] == "0x40B84C55" and out["secure"] == ["0x40050A3B"]
              and io.sent[:2] == [actions.dclick(ShelfIO.SECURED), actions.dclick(ShelfIO.OPEN)]
              and io.replies() == [(0x601, 7), (0x602, 0)], str(out)[:300])
        check("added: the new trapped pouch; missing: 'No resupply: Hatchet' -> Hatchet",
              [(x["serial"], x["hue"], x["amount"]) for x in out["added"]] == [("0x6064D7DE", 38, 1)]
              and out["missing"] == ["Hatchet"] and not out["none_available"], str(out)[:300])
        out = shelf.resupply(ShelfIO(missing=(), adds=False), human, timeout=0.3)
        check("nothing to give: 'Unable to resupply: no items available.' -> none_available, nothing added",
              out["ok"] and out["none_available"] and out["added"] == [] and out["missing"] == [], str(out)[:300])
        io = ShelfIO()
        io.state["movement"]["pos"] = [4140, 1429, 6]
        try:
            shelf.resupply(io, human, timeout=0.3)
            check("a shelf beyond reach without a walker raises", False)
        except shelf.ShelfError as e:
            check("a shelf beyond reach without a walker raises, nothing sent", io.sent == [], str(e))
    finally:
        shelf._tile_name = tile_name


def spawn_unreachable_status():
    """test_status's last check, `status` against a closed state port, as the real CLI started
    first: Windows refuses a connection to a closed local port only after its SYN retries
    (~2 s), which this way pass while the other tests run."""
    tmp = tempfile.mkdtemp()
    dead = free_port(13100)
    port = dead.getsockname()[1]
    dead.close()
    return spawn_ctl(["--db", os.path.join(tmp, "harness.db"), "--state-port", str(port), "status"])


def test_characters(proxy):
    print("== characters: --char scopes tasks, wait and its cursors; names resolve through sessions ==")
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "harness.db")
    logdir = os.path.join(tmp, "logs")
    slow = os.path.join(tmp, "stub_slow.py")
    with open(slow, "w", encoding="utf-8") as f:
        f.write("import sys, time\nprint('slow start', sys.argv[1:], flush=True)\n"
                "for _ in range(600):\n    time.sleep(0.1)\n")
    tasks = {"slow": slow}
    m = Memory(db)
    m.con.execute("INSERT INTO sessions(tag, source, account, char_serial, char_name) "
                  "VALUES ('20261008_120000', 'live', 'acct', 1, 'Hackworth')")
    m.con.commit()
    plain = Ctl(db, logdir, proxy, tasks=tasks)
    one = Ctl(db, logdir, proxy, tasks=tasks, char="0x00000001")
    two = Ctl(db, logdir, proxy, tasks=tasks, char="0x00000002")

    code, out = Ctl(db, logdir, proxy, char="Nobody")("status")
    check("--char Nobody -> unknown character", code == 1 and "unknown character" in out.get("error", ""), str(out))
    code, out = Ctl(db, logdir, proxy, char="hackworth")("status")
    check("--char by name (any case) resolves through sessions; status lists the proxy's sessions",
          code == 0 and out.get("sessions") == [{"tag": "fake", "serial": "0x00000001", "name": "Hackworth"}],
          str(out)[:300])

    code, r1 = one("run", "slow")
    check("a run for character 1 starts", code == 0 and r1.get("ok"), str(r1))
    code, r2 = two("run", "slow")
    check("a run for character 2 starts alongside it", code == 0 and r2.get("ok"), str(r2))
    code, out = one("run", "slow")
    check("a second run for character 1 is refused", code == 1 and "one task per character" in out.get("error", "")
          and r1.get("task_id", "?") in out.get("error", ""), str(out))
    code, out = plain("run", "slow")
    check("a run without --char is refused while either runs", code == 1 and "is running" in out.get("error", ""),
          str(out))
    entries = {e["task_id"]: e for e in json.loads(meta(db, tw.TASKS_KEY) or "[]")}
    check("task entries carry their character", entries.get(r1.get("task_id"), {}).get("char_serial") == 1
          and entries.get(r2.get("task_id"), {}).get("char_serial") == 2, str(entries))
    code, out = one("status")
    check("status --char lists only that character's task",
          [t["task_id"] for t in out.get("tasks", [])] == [r1.get("task_id")], str(out.get("tasks")))
    wait_for(lambda: all(e.get("child_pid") for e in json.loads(meta(db, tw.TASKS_KEY) or "[]")), 10)
    code, out = one("stop")
    check("stop --char stops that character's task only", code == 0 and out.get("task_id") == r1.get("task_id")
          and [e["task_id"] for e in json.loads(meta(db, tw.TASKS_KEY) or "[]")] == [r2.get("task_id")], str(out))
    j = out.get("juncture") or {}
    check("its end juncture is tagged with the character", j.get("char_serial") == 1, str(j))
    check("the runner was told its character (--char forwarded)",
          any("'--char', '0x00000001'" in ln for ln in j.get("data", {}).get("tail", [])), str(j.get("data")))
    code, out = two("stop")
    check("stop --char 2 stops the other", code == 0 and out.get("task_id") == r2.get("task_id"), str(out))

    print("== characters: wait --char ==")
    db = os.path.join(tmp, "wait.db")
    m.close()
    m = Memory(db)
    one = Ctl(db, logdir, proxy, char="0x00000001")
    m.juncture("lumber", "stuck", "the other character's", severity="attention", char_serial=2)
    code, out = one("wait", "--timeout", "1", "--poll", "0.1")
    check("wait --char 1 is not woken by character 2's juncture", code == 0 and out.get("event") is None, str(out))
    mine = m.juncture("lumber", "stuck", "mine", severity="attention", char_serial=1)
    code, out = one("wait", "--timeout", "3", "--poll", "0.1")
    check("wait --char 1 is woken by its own juncture", (out.get("event") or {}).get("id") == mine, str(out))
    check("the cursor is per character", meta(db, "overseer_juncture_cursor:0x00000001") == str(mine)
          and meta(db, ctl.JUNCTURE_CURSOR_KEY) is None, str(meta(db, "overseer_juncture_cursor:0x00000001")))
    check("the heartbeat is per character", meta(db, "overseer_heartbeat:0x00000001") is not None
          and meta(db, ctl.HEARTBEAT_KEY) is None)
    shared = m.juncture("gate", "stuck", "everyone's", severity="attention", char_serial=None)
    code, out = one("wait", "--timeout", "3", "--poll", "0.1")
    check("wait --char 1 is woken by an unscoped juncture", (out.get("event") or {}).get("id") == shared, str(out))
    # a character's first wait continues from the shared cursor the pre-character overseer left
    old = m.chat_post("user", "answered before characters existed", char_serial=None)
    m.con.execute("INSERT OR REPLACE INTO meta VALUES(?, ?)", (ctl.CHAT_CURSOR_KEY, str(old)))
    m.con.commit()
    two = Ctl(db, logdir, proxy, char="0x00000002")
    code, out = two("wait", "--timeout", "1", "--poll", "0.1", "--include-info")
    check("first wait --char 2 starts at the shared cursor (no replay of old messages)",
          code == 0 and not any(e.get("id") == old and e.get("type") == "chat" for e in out.get("events") or []),
          str(out))
    m.close()


def main():
    os.environ.pop("UO_CHAR", None)       # ctl's --char default: these tests pick characters explicitly
    proxy = FakeProxy()
    for port in (proxy.control_port, proxy.state_port):
        assert port >= 12700 and port not in (25941, 25942)
    unreachable = spawn_unreachable_status()
    test_wait(proxy)
    test_status(proxy, unreachable)
    test_run_act(proxy)
    test_characters(proxy)
    test_map(proxy)
    test_know(proxy)
    test_combat(proxy)
    test_heal_buy(proxy)
    test_heal(proxy)
    test_intent_cmd(proxy)
    test_npcs(proxy)
    test_attackers(proxy)
    test_drop(proxy)
    test_track(proxy)
    test_room_buttons()
    with fast_clock((room, shelf, humanize)):     # their fake IO answers synchronously, like FakeProxy
        test_room_flow()
        test_shelf_flow()
    reset_events(proxy)
    test_convert(proxy)
    reset_events(proxy)
    test_overseer_acts(proxy)
    if FAILURES:
        print(f"FAILED: {len(FAILURES)}: {FAILURES}")
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()

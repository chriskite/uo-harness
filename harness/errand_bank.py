"""Unattended bank run: the Phase 3 closed-loop acceptance task (docs/PLAN.md).

  1. (optional --start x,y) walk to the start tile
  2. find a banker: a "... the banker" label already heard, else single-click
     nearby human NPCs (nearest first, human pace) until one answers with one
  3. walk to within --range tiles of the banker (RunUO banker speech range 12)
  4. say "bank" (keyword-encoded exactly like the stock client) and verify the
     server opened the player's bank box (0x24 on the layer-0x1D item)
  5. walk back to the start tile and verify the position

Feedback comes from the proxy's state port (server-true position, live world
model), actions go through the control port. Walking uses walk memory
(harness/nav.py): known-walkable ground from past captures. Server-denied moves
are learned and trigger a replan; the updated memory is saved.

Guards: jittered human pacing, --timeout overall cap, abort on a movement
stall, hit-point loss, or an assistant-restriction system message
(ANTICHEAT.md §8 rule 4). The only thing ever said in game is "bank".

Run:  python harness/errand_bank.py [--start 1963,2597] [--range 8]
"""
import argparse
import json
import random
import socket
import sys
import time

sys.path.insert(0, r"C:/Users/chris/uo-harness/harness")
import actions  # noqa: E402
import nav  # noqa: E402

HOST = "127.0.0.1"
MEMORY_PATH = r"C:/Users/chris/uo-harness/harness/data/walkmem.json"
BANKBOX_LAYER = 0x1D
HUMAN_BODIES = (0x190, 0x191)
GATING_WORDS = ("razor", "assistant", "macro", "script")


class Abort(Exception):
    pass


def log(msg: str):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def cheb(a, b) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def pace(lo: float, hi: float):
    time.sleep(random.uniform(lo, hi))


def _serial(v) -> int:
    return int(v, 16) if isinstance(v, str) else int(v)


class Link:
    """Control-port actions + state-port feedback, with an event cursor."""

    def __init__(self, control_port: int, state_port: int):
        self.ctl = socket.create_connection((HOST, control_port), timeout=10)
        self.st = socket.create_connection((HOST, state_port), timeout=10)
        self.st_file = self.st.makefile("rb")
        self.since = 0
        self.events = []
        self.last = None

    def send(self, pkt: bytes) -> str:
        self.ctl.sendall(len(pkt).to_bytes(2, "big") + pkt)
        n = int.from_bytes(self._recv(self.ctl, 2), "big")
        return self._recv(self.ctl, n).decode()

    @staticmethod
    def _recv(sock, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise Abort("proxy closed the control connection")
            buf += chunk
        return buf

    def state(self) -> dict:
        self.st.sendall((json.dumps({"op": "state", "since": self.since}) + "\n").encode())
        resp = json.loads(self.st_file.readline())
        if not resp.get("ok"):
            raise Abort(f"state port: {resp.get('error')}")
        self.events.extend(env["data"] for env in resp["events"] if env["origin"] == "world")
        self.since = resp["next"]
        self.last = resp
        return resp

    def wait(self, pred, timeout: float, poll: float = 0.1):
        end = time.monotonic() + timeout
        while True:
            st = self.state()
            if pred(st):
                return st
            if time.monotonic() > end:
                return None
            time.sleep(poll)


class Errand:
    def __init__(self, link: Link, memory: nav.WalkMemory, args):
        self.link = link
        self.mem = memory
        self.args = args
        self.deadline = time.monotonic() + args.timeout
        self.start_hits = None
        self.blocked_count = 0
        self.steps = 0
        self.heard_upto = 0

    # ---- guards ----
    def check_guards(self, st: dict):
        if time.monotonic() > self.deadline:
            raise Abort(f"overall timeout ({self.args.timeout}s)")
        mv = st["movement"]
        if mv["stalled"]:
            raise Abort(f"movement stalled ({mv['rejects_in_row']} walks rejected in a row)")
        me = st["world"]["self"]
        hits = me.get("hits")
        if hits is not None:
            if self.start_hits is None:
                self.start_hits = hits
            elif hits < self.start_hits:
                raise Abort(f"hit points dropped ({self.start_hits} -> {hits}); stopping")
        for ev in self.link.events[self.heard_upto:]:
            if ev.get("ev") == "speech_heard" and ev.get("serial") in (None, 0, 0xFFFFFFFF, "0xFFFFFFFF"):
                text = (ev.get("text") or "").lower()
                if any(w in text for w in GATING_WORDS):
                    raise Abort(f"server restriction message: {ev.get('text')!r}")
        self.heard_upto = len(self.link.events)

    def pos(self, st=None):
        st = st or self.link.state()
        p = st["movement"]["pos"]
        if p is None:
            raise Abort("player position unknown (no login/anchor seen by the proxy)")
        return p

    # ---- walking ----
    def step(self, d: int, run: bool = True) -> str:
        """Send one walk; wait for its outcome. Returns 'moved', 'turned' or 'blocked'."""
        st = self.link.state()
        before = self.pos(st)
        pkt = actions.walk(d, run=run)
        for _ in range(40):  # retry pacing / resync-reply gates
            resp = self.link.send(pkt)
            if resp == "OK":
                break
            if resp.startswith("ERR walk gated: pacing") or resp.startswith("ERR walk gated: awaiting"):
                time.sleep(0.1)
                continue
            raise Abort(f"walk refused: {resp}")
        else:
            raise Abort("walk gated too long")
        st = self.link.wait(lambda s: s["movement"]["inflight"] == 0, timeout=3.0, poll=0.05)
        if st is None:
            raise Abort("walk outcome never arrived")
        self.check_guards(st)
        after = self.pos(st)
        if after[:2] != before[:2]:
            self.steps += 1
            return "moved"
        if after[3] != before[3]:
            return "turned"
        return "blocked"

    def occupied(self, st=None) -> set:
        """Tiles currently occupied by other mobiles (cannot be walked through)."""
        st = st or self.link.state()
        me = st["movement"]["self_serial"]
        return {(m["x"], m["y"]) for key, m in st["world"]["mobiles"].items()
                if m.get("x") is not None and _serial(key) != me}

    def walk_to(self, center_fn, radius: int, label: str, max_moves: int = 150):
        """Walk until within `radius` (Chebyshev) of center_fn(), re-evaluated
        on every replan (the banker may wander)."""
        replans = 0
        while True:
            st = self.link.state()
            cur = tuple(self.pos(st)[:2])
            goal = nav.within(tuple(center_fn()), radius)
            if goal(cur):
                log(f"{label}: arrived at {cur}")
                return
            path = nav.plan(self.mem, cur, goal, extra_blocked=self.occupied(st) - {cur})
            if path is None:
                raise Abort(f"{label}: no route from {cur}")
            log(f"{label}: route {len(path) - 1} steps from {cur}")
            for nxt in path[1:]:
                cur = tuple(self.pos()[:2])
                d = nav.direction(cur, nxt)
                outcome = self.step(d)
                if outcome == "turned":
                    pace(*self.args.pace)
                    outcome = self.step(d)
                if outcome == "moved":
                    new = tuple(self.pos()[:2])
                    self.mem.add_step(cur, new)
                    if new != nxt:
                        log(f"{label}: landed on {new}, expected {nxt}; replanning")
                        break
                    if self.steps > max_moves:
                        raise Abort(f"{label}: exceeded {max_moves} moves")
                    pace(*self.args.pace)
                    continue
                self.blocked_count += 1
                if nxt in self.occupied():
                    log(f"{label}: {nxt} occupied by a mobile; replanning")
                else:
                    self.mem.add_blocked(cur, d)
                    log(f"{label}: move {d} from {cur} blocked ({self.blocked_count} total); replanning")
                if self.blocked_count > self.args.max_blocked:
                    raise Abort(f"too many blocked moves ({self.blocked_count})")
                pace(0.6, 1.2)
                break
            replans += 1
            if replans > 30:
                raise Abort(f"{label}: too many replans")

    def look_at(self, serial: int, known_name: bool):
        """Single-click an entity exactly like the stock client: 0x09, then
        0x34 status request, plus 0x98 name request when the name is unknown."""
        self.link.send(actions.single_click(serial))
        self.link.send(actions.status_request(serial))
        if not known_name:
            self.link.send(actions.name_request(serial))

    # ---- banker ----
    def find_banker(self):
        st = self.link.state()
        mobiles = st["world"]["mobiles"]
        known = self._banker_from_labels(st["world"])
        if known:
            return known
        me = tuple(self.pos(st)[:2])
        cands = []
        for key, m in mobiles.items():
            if m.get("x") is None or m.get("graphic") not in HUMAN_BODIES:
                continue
            serial = _serial(key)
            if serial == st["movement"]["self_serial"]:
                continue
            dist = cheb(me, (m["x"], m["y"]))
            if dist <= self.args.search_radius:
                cands.append((dist, serial, m.get("name")))
        cands.sort()
        log(f"banker search: {len(cands)} nearby NPCs to look at")
        for dist, serial, name in cands[: self.args.max_clicks]:
            pace(0.8, 1.6)
            mark = len(self.link.events)
            self.look_at(serial, known_name=bool(name))
            st = self.link.wait(lambda s: self._label_for(serial, mark) is not None, timeout=1.5)
            label = self._label_for(serial, mark)
            log(f"  looked at {name or hex(serial)} ({dist} tiles): {label!r}")
            if label and "banker" in label.lower():
                m = self.link.state()["world"]["mobiles"].get(f"0x{serial:08X}") or {}
                return serial, label, (m.get("x"), m.get("y"))
        raise Abort("no banker found nearby")

    def _label_for(self, serial: int, since_idx: int):
        for ev in self.link.events[since_idx:]:
            if ev.get("ev") == "speech_heard" and ev.get("serial") in (serial, f"0x{serial:08X}"):
                return ev.get("text")
        return None

    def _banker_from_labels(self, world):
        """A positioned mobile whose latest click label (world.labels) names it a banker."""
        for key, text in world.get("labels", {}).items():
            m = world["mobiles"].get(key)
            if "the banker" in text.lower() and m and m.get("x") is not None:
                return _serial(key), text, (m["x"], m["y"])
        return None

    def banker_pos(self, serial: int, fallback):
        m = self.link.state()["world"]["mobiles"].get(f"0x{serial:08X}") or {}
        return (m["x"], m["y"]) if m.get("x") is not None else fallback

    # ---- bank ----
    def open_bank(self) -> int:
        mark = len(self.link.events)
        pace(0.6, 1.2)
        resp = self.link.send(actions.say_unicode("bank"))
        if resp != "OK":
            raise Abort(f"speech refused: {resp}")
        log('said "bank"')
        st = self.link.wait(lambda s: self._bank_opened(s, mark) is not None, timeout=5.0)
        if st is None:
            raise Abort("bank box did not open within 5 s")
        return self._bank_opened(st, mark)

    def _bank_opened(self, st, since_idx: int):
        self_serial = st["movement"]["self_serial"]
        items = st["world"]["items"]
        for ev in self.link.events[since_idx:]:
            if ev.get("ev") != "container_open":
                continue
            serial = _serial(ev["serial"])
            it = items.get(f"0x{serial:08X}") or {}
            parent = it.get("container")
            if it.get("layer") == BANKBOX_LAYER and parent is not None and _serial(parent) == self_serial:
                return serial
        return None

    # ---- the errand ----
    def run(self):
        st = self.link.wait(lambda s: s["movement"]["pos"] is not None
                            and s["movement"]["self_serial"] is not None, timeout=5.0)
        if st is None:
            raise Abort("proxy has no player position yet (log in first)")
        self.check_guards(st)
        pace(1.0, 2.5)
        if self.args.start:
            target = self.args.start
            self.walk_to(lambda: target, 0, "to start")
        home = tuple(self.pos()[:2])
        log(f"errand start at {home}")
        serial, label, bpos = self.find_banker()
        log(f"banker: {label} at {bpos}")
        self.walk_to(lambda: self.banker_pos(serial, bpos), self.args.range, "to bank")
        box = self.open_bank()
        log(f"bank box opened (0x{box:08X})")
        pace(1.5, 3.0)
        self.walk_to(lambda: home, 0, "home")
        final = tuple(self.pos()[:2])
        if final != home:
            raise Abort(f"ended at {final}, not {home}")
        log(f"errand complete: back at {home}; {self.steps} moves, {self.blocked_count} blocked")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start", type=lambda s: tuple(int(v) for v in s.split(",")),
                    help="walk to this x,y first; the errand returns here")
    ap.add_argument("--range", type=int, default=8, help="speak within this many tiles of the banker")
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--max-blocked", type=int, default=12)
    ap.add_argument("--search-radius", type=int, default=18)
    ap.add_argument("--max-clicks", type=int, default=12)
    ap.add_argument("--pace", type=float, nargs=2, default=(0.28, 0.45), metavar=("MIN", "MAX"),
                    help="seconds between steps (run)")
    ap.add_argument("--control-port", type=int, default=25941)
    ap.add_argument("--state-port", type=int, default=25942)
    ap.add_argument("--memory", default=MEMORY_PATH)
    args = ap.parse_args()

    memory = nav.WalkMemory.load(args.memory)
    link = Link(args.control_port, args.state_port)
    errand = Errand(link, memory, args)
    code = 0
    try:
        errand.run()
    except Abort as e:
        log(f"ABORTED: {e}")
        code = 1
    finally:
        memory.save(args.memory)
        log(f"walk memory saved ({len(memory.tiles)} tiles, {len(memory.blocked)} blocked moves)")
    sys.exit(code)


if __name__ == "__main__":
    main()

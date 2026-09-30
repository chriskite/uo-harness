"""Shared plumbing for closed-loop agent runners (errand_bank.py, loop_lumber.py).

- Link: the proxy's control port (actions) + state port (movement truth, world
  model, events), with an event cursor. Actions refused by the agent gate
  while it is paused or on a scheduled break wait for it to reopen; a kill or
  an exhausted daily budget aborts.
- Mover: server-confirmed walking over walk memory (harness/nav.py):
  optimistic A*, learns blocked moves from server denies, replans, and
  (optionally) sends the stock client's open-door request once when a door
  item stands on the blocked tile, so closed doors aren't learned as walls.
"""
import json
import socket
import time

import actions
import nav

HOST = "127.0.0.1"
GATE_WAIT = ("ERR agent paused", "ERR scheduled break")  # reopen on their own
GATE_POLL_S = 5.0
# Classic UO door art (0x0675-0x06F4). Shelter's inn doors in the demonstration
# capture (0x06A5, 0x06AD, 0x06ED, 0x06EF) and the rental-room door (0x06E5) are in it.
DOOR_GRAPHICS = range(0x0675, 0x06F5)


class Abort(Exception):
    pass


def log(msg: str):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def cheb(a, b) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def serial_of(v) -> int:
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
        """One control-port frame; the proxy's reply ("OK" or "ERR ...")."""
        self.ctl.sendall(len(pkt).to_bytes(2, "big") + pkt)
        n = int.from_bytes(self._recv(self.ctl, 2), "big")
        return self._recv(self.ctl, n).decode()

    def act(self, pkt: bytes):
        """Send an action; wait out a paused/break gate, abort on any other refusal."""
        waited = False
        while True:
            resp = self.send(pkt)
            if resp == "OK":
                if waited:
                    log("agent gate open again; resuming")
                return
            if resp.startswith(GATE_WAIT):
                if not waited:
                    log(f"agent gate closed ({resp[4:]}); waiting")
                    waited = True
                time.sleep(GATE_POLL_S)
                continue
            raise Abort(f"action 0x{pkt[0]:02X} refused: {resp}")

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

    def pos(self, st=None):
        st = st or self.state()
        p = st["movement"]["pos"]
        if p is None:
            raise Abort("player position unknown (no login/anchor seen by the proxy)")
        return p


class Mover:
    """Walks over walk memory with server-confirmed steps, with human texture
    from a humanize.Human: per-plan route noise, run/walk routes, lognormal
    step rhythm, pauses and occasional sidesteps onto known tiles.

    guard(st) is called after every step outcome (timeouts, HP, stalls...)."""

    def __init__(self, link: Link, memory: nav.WalkMemory, human,
                 max_blocked: int = 12, guard=None, doors: bool = False):
        self.link = link
        self.mem = memory
        self.human = human
        self.max_blocked = max_blocked
        self.guard = guard or (lambda st: None)
        self.doors = doors
        self.steps = 0
        self.blocked_count = 0
        self.doors_opened = 0
        self.bumps = 0

    def step(self, d: int, run: bool = True) -> str:
        """Send one walk; wait for its outcome. Returns 'moved', 'turned' or 'blocked'."""
        st = self.link.state()
        before = self.link.pos(st)
        pkt = actions.walk(d, run=run)
        for _ in range(40):  # retry pacing / resync-reply gates
            resp = self.link.send(pkt)
            if resp == "OK":
                break
            if resp.startswith("ERR walk gated: pacing") or resp.startswith("ERR walk gated: awaiting"):
                time.sleep(0.1)
                continue
            if resp.startswith(GATE_WAIT):
                log(f"agent gate closed ({resp[4:]}); waiting")
                time.sleep(GATE_POLL_S)
                continue
            raise Abort(f"walk refused: {resp}")
        else:
            raise Abort("walk gated too long")
        st = self.link.wait(lambda s: s["movement"]["inflight"] == 0, timeout=3.0, poll=0.05)
        if st is None:
            raise Abort("walk outcome never arrived")
        self.guard(st)
        after = self.link.pos(st)
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
                if m.get("x") is not None and serial_of(key) != me}

    def door_at(self, tile, st=None) -> bool:
        """A ground item with door art stands on `tile` (world model)."""
        st = st or self.link.state()
        return any(it.get("container") is None and (it.get("x"), it.get("y")) == tuple(tile)
                   and it.get("graphic") in DOOR_GRAPHICS for it in st["world"]["items"].values())

    def _try_door(self, cur, d, run) -> bool:
        """Blocked by a door: send the stock open-door request once for this
        door and retry the move. True if the retry moved."""
        log(f"move {d} from {cur} blocked by a door; opening it")
        self.link.act(actions.open_door())
        self.doors_opened += 1
        self.human.wait("read")
        return self.step(d, run) == "moved"

    def _sidestep(self, cur, avoid, run) -> bool:
        """Human wander: one step onto a known-walkable neighbour off the route."""
        occupied = self.occupied()
        options = [nav.step(cur, d) for d in range(8)
                   if (cur, d) not in self.mem.blocked and nav.step(cur, d) in self.mem.tiles
                   and nav.step(cur, d) not in occupied and nav.step(cur, d) not in avoid]
        if not options:
            return False
        side = self.human.choice(options)
        d = nav.direction(cur, side)
        outcome = self.step(d, run)
        if outcome == "turned":
            time.sleep(self.human.step_delay(run))
            outcome = self.step(d, run)
        if outcome == "moved":
            self.mem.add_step(cur, tuple(self.link.pos()[:2]))
            return True
        return False

    def obstacle_ahead(self, cur, d) -> bool:
        """A known obstacle one step from `cur` in direction `d` (walk memory's
        server-denied moves; map data plugs in here later)."""
        return (cur, d) in self.mem.blocked

    def _bump(self, cur, d, run, label) -> str:
        """Missed the turn: run straight into the obstacle ahead. Returns the
        step outcome ('blocked' as expected; 'moved' if memory was wrong)."""
        outcome = self.step(d, run)
        if outcome == "blocked":
            self.bumps += 1
            log(f"{label}: (ran into the obstacle at {nav.step(cur, d)}, turning)")
            time.sleep(self.human.reaction("read") * 0.5)
        elif outcome == "moved":
            new = tuple(self.link.pos()[:2])
            self.mem.add_step(cur, new)
            log(f"{label}: (overshot the turn to {new}; replanning)")
        return outcome

    def walk_to(self, center_fn, radius: int, label: str, max_moves: int = 250):
        """Walk until within `radius` (Chebyshev) of center_fn(), re-evaluated
        on every replan (NPCs wander)."""
        replans = 0
        start_steps = self.steps
        tried_doors = set()
        run = self.human.route_runs()
        while True:
            st = self.link.state()
            cur = tuple(self.link.pos(st)[:2])
            goal = nav.within(tuple(center_fn()), radius)
            if goal(cur):
                log(f"{label}: arrived at {cur}")
                return
            path = nav.plan(self.mem, cur, goal, extra_blocked=self.occupied(st) - {cur},
                            cost_scale=self.human.cost_scale())
            if path is None:
                raise Abort(f"{label}: no route from {cur}")
            log(f"{label}: route {len(path) - 1} steps from {cur}{'' if run else ' (walking)'}")
            replan = False
            prev_d = None
            for i, nxt in enumerate(path[1:], start=1):
                cur = tuple(self.link.pos()[:2])
                d = nav.direction(cur, nxt)
                if prev_d is not None and d != prev_d and self.obstacle_ahead(cur, prev_d) \
                        and self.human.bump():
                    if self._bump(cur, prev_d, run, label) == "moved":
                        replan = True
                        break
                prev_d = d
                outcome = self.step(d, run)
                if outcome == "turned":
                    time.sleep(self.human.step_delay(run))
                    outcome = self.step(d, run)
                if outcome == "blocked" and self.doors and nxt not in tried_doors \
                        and self.door_at(nxt):
                    tried_doors.add(nxt)
                    if self._try_door(cur, d, run):
                        outcome = "moved"
                if outcome == "moved":
                    new = tuple(self.link.pos()[:2])
                    self.mem.add_step(cur, new)
                    if new != nxt:
                        log(f"{label}: landed on {new}, expected {nxt}; replanning")
                        replan = True
                        break
                    if self.steps - start_steps > max_moves:
                        raise Abort(f"{label}: exceeded {max_moves} moves")
                    time.sleep(self.human.step_delay(run))
                    self.human.after_step()
                    if len(path) - i > 3 and self.human.wander():
                        if self._sidestep(new, set(path[i:i + 2]), run):
                            log(f"{label}: sidestepped at {new}; replanning")
                            time.sleep(self.human.step_delay(run))
                            break
                    continue
                self.blocked_count += 1
                if nxt in self.occupied():
                    log(f"{label}: {nxt} occupied by a mobile; replanning")
                else:
                    self.mem.add_blocked(cur, d)
                    log(f"{label}: move {d} from {cur} blocked ({self.blocked_count} total); replanning")
                if self.blocked_count > self.max_blocked:
                    raise Abort(f"too many blocked moves ({self.blocked_count})")
                self.human.wait("read")
                replan = True
                break
            if replan:
                replans += 1
            if replans > 30:
                raise Abort(f"{label}: too many replans")

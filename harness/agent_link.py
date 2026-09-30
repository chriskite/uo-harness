"""Shared plumbing for closed-loop agent runners (errand_bank.py, loop_lumber.py).

- Link: the proxy's control port (actions) + state port (movement truth, world
  model, events), with an event cursor. Actions refused by the agent gate
  while it is paused or on a scheduled break wait for it to reopen; a kill or
  an exhausted daily budget aborts.
- Mover: server-confirmed walking. On facets with map data (harness/uomap.py)
  it plans in 3D with the client's walkability rules (harness/pathfind.py);
  elsewhere (the blank rental-room facet) it falls back to 2D walk memory
  (harness/nav.py). Either way it learns blocked moves from server denies,
  replans, and sends the stock open-door request once when a door blocks the
  step, so closed doors aren't learned as walls. Mobiles are not walls: UOO
  lets you shove through them with enough stamina (user, 2026-09-29), so a
  mobile's tile only costs extra; a denied shove blocks that tile for a while.
"""
import json
import socket
import time

import actions
import nav
import pathfind
import uomap

HOST = "127.0.0.1"
GATE_WAIT = ("ERR agent paused", "ERR scheduled break")  # reopen on their own
GATE_POLL_S = 5.0
# Classic UO door art (0x0675-0x06F4). Shelter's inn doors in the demonstration
# capture (0x06A5, 0x06AD, 0x06ED, 0x06EF) and the rental-room door (0x06E5) are in it.
DOOR_GRAPHICS = range(0x0675, 0x06F5)
MAP_FACETS = (0, 1, 4, 5)     # facets with geometry in mapN.uoo (2 and 3 are blank; docs/MAP.md)
# Mobiles: shoving through one needs stamina (UOO; threshold unknown, RunUO needs
# full stamina [INFERENCE]). Plans prefer going around (entering a mobile's tile
# costs MOBILE_COST_X normal steps); after a denied shove that tile is a wall for
# SHOVE_RETRY_S. A route cut only by such tiles is waited out up to MOBILE_WAIT_S.
MOBILE_COST_X = 4.0
SHOVE_RETRY_S = 15.0
MOBILE_WAIT_S = 90.0


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

    def intent(self, text: str | None, kind: str | None = None, target=None, **extra):
        """Report what the agent is trying to do now (the visualizer shows it).
        Proxy-side only: nothing reaches the server. None clears it. A display
        failure never stops the agent: it's logged and ignored."""
        body = None
        if text is not None:
            body = {"text": text, **{k: v for k, v in extra.items() if v is not None}}
            if kind is not None:
                body["kind"] = kind
            if target is not None and None not in tuple(target)[:2]:
                body["target"] = [int(target[0]), int(target[1])]
        self.st.sendall((json.dumps({"op": "intent", "intent": body}) + "\n").encode())
        resp = json.loads(self.st_file.readline())
        if not resp.get("ok"):
            log(f"intent not shown: {resp.get('error')}")

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
    """Server-confirmed walking with human texture from a humanize.Human:
    per-plan route noise, run/walk routes, lognormal step rhythm, pauses,
    sidesteps and missed-turn bumps. Plans on map data when the facet has it
    (use_map), else on walk memory.

    guard(st) is called after every step outcome (timeouts, HP, stalls...)."""

    def __init__(self, link: Link, memory: nav.WalkMemory, human,
                 max_blocked: int = 12, guard=None, doors: bool = False, use_map: bool = True):
        self.link = link
        self._source = memory        # memory.Memory (per-facet projection) or a fixed nav.WalkMemory
        self._mems = {}
        self.mem = self.mem_for(0)
        self.human = human
        self.max_blocked = max_blocked
        self.guard = guard or (lambda st: None)
        self.doors = doors
        self.use_map = use_map
        self._walks = {}
        self.denied = set()          # (x, y, d) server denies seen this session (map planner)
        self.shove_denied = {}       # (x, y) -> monotonic time a shove into it was denied
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
        """Tiles currently occupied by other mobiles."""
        st = st or self.link.state()
        me = st["movement"]["self_serial"]
        return {(m["x"], m["y"]) for key, m in st["world"]["mobiles"].items()
                if m.get("x") is not None and serial_of(key) != me}

    # ---------------------------------------------------------------- map
    def walk_map(self, st):
        """pathfind.Walk for the player's facet, or None (no map data there).
        Ground items from the world model are added as dynamic objects."""
        if not self.use_map:
            return None
        facet = st["world"]["self"].get("map")
        facet = 0 if facet is None else facet
        if facet not in MAP_FACETS:
            return None
        w = self._walks.get(facet)
        if w is None:
            w = pathfind.Walk(uomap.UoMap(facet))
            self._walks[facet] = w
        ground = {}
        for it in st["world"]["items"].values():
            if it.get("container") is None and it.get("x") is not None and it.get("graphic") is not None:
                ground.setdefault((it["x"], it["y"]), []).append((it["graphic"], it.get("z") or 0))
        w.dynamic = lambda x, y: ground.get((x, y), ())
        w.clear()
        return w

    def door_at(self, tile, st=None) -> bool:
        """A ground item that is a door (tiledata Door flag, or classic door art)
        stands on `tile` (world model)."""
        st = st or self.link.state()
        td = uomap.tiledata() if self.use_map else None
        for it in st["world"]["items"].values():
            if it.get("container") is None and (it.get("x"), it.get("y")) == tuple(tile):
                g = it.get("graphic")
                if g in DOOR_GRAPHICS or (td is not None and g is not None and td.item(g)
                                          and td.item(g).flags & uomap.DOOR):
                    return True
        return False

    def _try_door(self, cur, d, run) -> bool:
        """Blocked by a door: send the stock open-door request once for this
        door and retry the move. True if the retry moved."""
        log(f"move {d} from {cur} blocked by a door; opening it")
        self.link.act(actions.open_door())
        self.doors_opened += 1
        self.human.wait("read")
        return self.step(d, run) == "moved"

    def _sidestep(self, cur, avoid, run, walk=None, z=0) -> bool:
        """Human wander: one step onto a walkable neighbour off the route."""
        occupied = self.occupied()
        if walk is not None:
            options = [(x, y) for x, y, _ in filter(None, (walk.can_walk(cur[0], cur[1], z, d)
                                                             for d in range(8)))]
        else:
            options = [nav.step(cur, d) for d in range(8)
                       if (cur, d) not in self.mem.blocked and nav.step(cur, d) in self.mem.tiles]
        options = [t for t in options if t not in occupied and t not in avoid]
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

    def obstacle_ahead(self, cur, d, walk=None, z=0) -> bool:
        """An obstacle one step from `cur` in direction `d`: the map says the
        step isn't walkable, or (no map) walk memory holds a server deny."""
        if walk is not None:
            return walk.can_walk(cur[0], cur[1], z, d) is None
        return (cur, d) in self.mem.blocked

    def _bump(self, cur, d, run, label) -> str:
        """Missed the turn: run straight into the obstacle ahead. Returns the
        step outcome ('blocked' as expected; 'moved' if the obstacle wasn't one)."""
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

    def mem_for(self, facet) -> nav.WalkMemory:
        """Session walk memory for the 2D fallback planner on `facet`: the durable
        store's projection (docs/MEMORY.md) plus what this session learned."""
        facet = 0 if facet is None else facet
        m = self._mems.get(facet)
        if m is None:
            src = self._source
            m = src.walk_memory(facet) if hasattr(src, "walk_memory") else src
            self._mems[facet] = m
        return m

    def plan(self, st, goal, mobiles: bool = True):
        """(path of (x, y) tiles including the start, walk or None). Also points
        self.mem at the current facet's walk memory. Mobiles' tiles cost
        MOBILE_COST_X (shove through them if going around is longer); tiles
        where a shove was denied in the last SHOVE_RETRY_S are walls unless
        mobiles=False (is the route only temporarily cut?)."""
        pos = st["movement"]["pos"]
        cur = (pos[0], pos[1])
        occ = self.occupied(st) - {cur}
        now = time.monotonic()
        hard = {t for t in occ if now - self.shove_denied.get(t, -1e9) < SHOVE_RETRY_S} if mobiles else set()
        noise = self.human.cost_scale()

        def cost(a, b):
            return (noise(a, b) if noise else 1.0) * (MOBILE_COST_X if b in occ else 1.0)

        self.mem = self.mem_for(st["world"]["self"].get("map"))
        walk = self.walk_map(st)
        if walk is not None:
            path = pathfind.plan(walk, (pos[0], pos[1], pos[2]), goal,
                                 blocked_moves=self.denied, occupied=hard, cost_scale=cost)
            return (None if path is None else [(x, y) for x, y, _ in path]), walk
        return nav.plan(self.mem, cur, goal, extra_blocked=hard, cost_scale=cost), None

    def walk_to(self, center_fn, radius: int, label: str, max_moves: int = 250):
        """Walk until within `radius` (Chebyshev) of center_fn(), re-evaluated
        on every replan (NPCs wander)."""
        replans = 0
        start_steps = self.steps
        tried_doors = set()
        run = self.human.route_runs()
        mobile_wait_until = None
        while True:
            st = self.link.state()
            self.guard(st)
            cur = tuple(self.link.pos(st)[:2])
            goal = nav.within(tuple(center_fn()), radius)
            if goal(cur):
                log(f"{label}: arrived at {cur}")
                return
            path, walk = self.plan(st, goal)
            if path is None:
                if self.plan(st, goal, mobiles=False)[0] is None:
                    raise Abort(f"{label}: no route from {cur}")
                now = time.monotonic()
                if mobile_wait_until is None:
                    mobile_wait_until = now + MOBILE_WAIT_S
                    log(f"{label}: route from {cur} cut by mobiles we couldn't shove; waiting")
                elif now > mobile_wait_until:
                    raise Abort(f"{label}: route from {cur} cut by mobiles for {MOBILE_WAIT_S:.0f} s")
                self.human.wait("between")
                continue
            mobile_wait_until = None
            log(f"{label}: route {len(path) - 1} steps from {cur}{'' if run else ' (walking)'}"
                f"{'' if walk is not None else ' [walk memory]'}")
            replan = False
            prev_d = None
            for i, nxt in enumerate(path[1:], start=1):
                pos = self.link.pos()
                cur, z = (pos[0], pos[1]), pos[2]
                d = nav.direction(cur, nxt)
                if prev_d is not None and d != prev_d and self.obstacle_ahead(cur, prev_d, walk, z) \
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
                        if self._sidestep(new, set(path[i:i + 2]), run, walk, self.link.pos()[2]):
                            log(f"{label}: sidestepped at {new}; replanning")
                            time.sleep(self.human.step_delay(run))
                            break
                    continue
                self.blocked_count += 1
                if nxt in self.occupied():
                    self.shove_denied[nxt] = time.monotonic()
                    me = self.link.state()["world"]["self"]
                    log(f"{label}: shove into {nxt} denied (stamina {me.get('stam')}/{me.get('stam_max')}); "
                        f"replanning")
                else:
                    if walk is not None:
                        self.denied.add((cur[0], cur[1], d))   # z-aware plans; don't pollute 2D memory
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

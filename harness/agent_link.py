"""Shared plumbing for closed-loop agent runners (errand_bank.py, loop_lumber.py).

- Link: the proxy's control port (actions) + state port (movement truth, world
  model, events), with an event cursor. Actions refused by the agent gate
  while it is paused or on a scheduled break wait for it to reopen; a kill or
  an exhausted daily budget aborts.
- Mover: server-confirmed walking. On facets with map data (harness/uomap.py)
  it plans in 3D with the client's walkability rules (harness/pathfind.py);
  elsewhere (the blank rental-room facet) it falls back to 2D walk memory
  (harness/nav.py). Either way it learns blocked moves from server denies and
  replans. Doors: like the client's auto-open (PlayerMobile.TryOpenDoors) it
  sends the stock open-door request when it faces a door on the next tile,
  before stepping into it; a door that still denies the step gets one more
  request after a player's reaction time. Mobiles are not walls: UOO lets you
  shove through them with enough stamina (user, 2026-09-29), so a mobile's
  tile only costs extra; a denied shove blocks that tile for a while.
  Moongates are walkable: stepping onto one opens its gump (destinations, or the
  renounce-Young prompt) and nothing more. A gate the route only passes over is
  a stray: the Mover closes its gump like a player would (stock 0xB1, button 0).
"""
import json
import socket
import time

import actions
import nav
import pathfind
import uomap
from uo.gumps import parse_layout

HOST = "127.0.0.1"
GATE_WAIT = ("ERR agent paused", "ERR scheduled break")  # reopen on their own
GATE_POLL_S = 5.0
# Movement gates that clear on their own within seconds (pacing, a client resync's reply,
# an expired walk's grace period, the stock 5-unconfirmed-steps limit); a stall does not.
MOVE_GATE = "ERR walk gated:"
MOVE_GATE_WAIT_S = 10.0
# A walk's outcome: confirm or deny, else the proxy's CONFIRM_TIMEOUT_S (3 s) rejection.
OUTCOME_WAIT_S = 5.0
# Polling the outcome with light movement queries: the confirm takes ~57 ms (live 2026-10-02),
# and a mounted step is due 100 ms after the last one.
OUTCOME_POLL_S = 0.01
# A full state the last step ended with stands in for a new one this long (Mover.fresh_state).
STATE_REUSE_S = 0.1
# Classic UO door art (0x0675-0x06F4). Shelter's inn doors in the demonstration
# capture (0x06A5, 0x06AD, 0x06ED, 0x06EF) and the rental-room door (0x06E5) are in it.
DOOR_GRAPHICS = range(0x0675, 0x06F5)
# Mobiles: shoving through one needs stamina (UOO; threshold unknown, RunUO needs
# full stamina [INFERENCE]). Plans prefer going around (entering a mobile's tile
# costs MOBILE_COST_X normal steps); after a denied shove that tile is a wall for
# SHOVE_RETRY_S. A route cut only by such tiles is waited out up to MOBILE_WAIT_S.
MOBILE_COST_X = 4.0
SHOVE_RETRY_S = 15.0
MOBILE_WAIT_S = 90.0
# Some teleporters deny the step and then move you (S2C 0x21 at the current tile, then the
# new position; the New Player Dungeon exit, live 2026-09-30). After a deny, look this long
# for such a jump before calling the step blocked.
DENY_TELEPORT_GRACE_S = 0.4
# Heights (z units). A body is 16 high (ClassicUO DEFAULT_CHARACTER_HEIGHT); a
# storey is ~20 (Shelter inn: ground floor planks z 0-1, upstairs boards z 20-21).
BODY_HEIGHT = 16
FLOOR_SPAN = 22
# Moongates (tiledata name "moongate"; this art without tiledata). Stepping onto one only opens a
# gump, sent before the step's confirm and never closed by the server: "Moongate Destinations"
# 0xE0E675B8, or for a Young character the renounce prompt 0xE2544541 (captures 20260930_100200
# ... 20261001_191355). Nothing moves you until it's answered (user, 2026-10-01).
MOONGATE_GRAPHICS = frozenset([*range(0x0DDA, 0x0DDF), *range(0x0F6C, 0x0F71)])
GATE_GUMP_WAIT_S = 1.0       # after landing on a gate, how long to look for its gump
CAPTCHA_GUMP_ID = 0x00000001  # never closed here (ANTICHEAT.md §8.8)


def reach_z(obj_z: int, obj_height: int):
    """z_ok for working an object (a tree): the standing body must overlap it
    vertically, i.e. stand on its level, as a player who can see and click it
    would. A cave below it or a floor above it doesn't count (live 2026-09-29:
    the agent chopped surface trees at z 5 from a cave at z -20)."""
    return lambda z: obj_z - BODY_HEIGHT < z < obj_z + max(obj_height, 1)


def same_floor(ref_z: int):
    """z_ok for dealing with an NPC: at most one storey above or below it."""
    return lambda z: abs(z - ref_z) <= FLOOR_SPAN


class Abort(Exception):
    pass


def log(msg: str):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def cheb(a, b) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def serial_of(v) -> int:
    return int(v, 16) if isinstance(v, str) else int(v)


LAYER_BANK = 0x1D
OPEN_WAIT_S = 3.0                # double-click -> the server's 0x24 container open


def containers_to_open(world: dict, serial: int, itself: bool = False) -> list[int]:
    """Containers the stock client must show before it can lift, use or target
    `serial`, outermost first, that the server hasn't opened this session.
    A player only reaches an item through an open container gump (the
    backpack, then the bag inside it...), and opening one is the stock
    double-click answered by the server's 0x24, which the world model records
    in world.containers. The server can't see a gump being closed, so opened
    once is open as far as it knows. With itself=True `serial` (a drop
    destination: you drag into its open gump) is included. Mobiles end the
    chain (worn items, your backpack on you)."""
    items = world["items"]
    opened = {serial_of(s) for s in world.get("containers") or []}
    chain, seen = [], set()
    ent = items.get(f"0x{serial:08X}")
    if itself and ent is not None:
        chain.append(serial)
    while ent is not None and ent.get("container") is not None:
        parent = serial_of(ent["container"])
        ent = items.get(f"0x{parent:08X}")
        if ent is None or parent in seen:      # a mobile (you, a vendor) or unknown
            break
        seen.add(parent)
        chain.append(parent)
    return [s for s in reversed(chain) if s not in opened]


def opens_as_container(world: dict, serial: int) -> bool:
    """Whether a drop destination is dragged into through its own container gump.
    Books (runebook, rune tome, spellbook, atlas, codex) carry the tiledata
    container flag, but a double-click opens their gump, never a 0x24. A rune
    or scroll is dropped onto the book itself (live 2026-10-02: waiting for the
    runebook's 0x24 refused `drop <rune> <runebook>`)."""
    ent = world["items"].get(f"0x{serial:08X}") or {}
    graphic = ent.get("graphic")
    if graphic is None:
        return True
    name = (uomap.tiledata().item(serial_of(graphic)).name or "").lower()
    return not (name.endswith("book") or any(w in name for w in ("tome", "atlas", "codex")))


def closed_bank(world: dict, serials) -> int | None:
    """The bank box among `serials`, if any: a double-click can't open it (only
    saying `bank` near a banker does), so callers refuse instead."""
    for s in serials:
        if (world["items"].get(f"0x{s:08X}") or {}).get("layer") == LAYER_BANK:
            return s
    return None


def bank_opened(world: dict, self_serial: int, events) -> int | None:
    """The bank box serial when `events` hold the server's 0x24 for it (the
    layer-0x1D item on `self_serial`), which saying `bank` near a banker gets."""
    for ev in events:
        if ev.get("ev") != "container_open":
            continue
        serial = serial_of(ev["serial"])
        it = world["items"].get(f"0x{serial:08X}") or {}
        if it.get("layer") == LAYER_BANK and it.get("container") is not None \
                and serial_of(it["container"]) == self_serial:
            return serial
    return None


class Link:
    """Control-port actions + state-port feedback, with an event cursor."""

    def __init__(self, control_port: int, state_port: int):
        self.ctl = socket.create_connection((HOST, control_port), timeout=10)
        self.state_port = state_port
        self.st = socket.create_connection((HOST, state_port), timeout=10)
        self.st_file = self.st.makefile("rb")
        self.since = 0
        self.events = []
        self.event_t = []            # each event's time (envelope t), parallel to events
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

    def _query(self, snapshot: bool) -> dict:
        self.st.sendall((json.dumps({"op": "state", "since": self.since, "snapshot": snapshot}) + "\n").encode())
        resp = json.loads(self.st_file.readline())
        if not resp.get("ok"):
            raise Abort(f"state port: {resp.get('error')}")
        world = [env for env in resp["events"] if env["origin"] == "world"]
        self.events.extend(env["data"] for env in world)
        self.event_t.extend(env.get("t", 0.0) for env in world)
        self.since = resp["next"]
        return resp

    def state(self) -> dict:
        """Movement truth, the world model and new events (`last` keeps it)."""
        self.last = self._query(True)
        return self.last

    def movement(self) -> dict:
        """Movement truth and new events without the world snapshot: a few ms where
        a full state takes 20-60 ms with a busy screen (live 2026-10-02, 600 kB).
        For waiting on a step's outcome at the mounted cadence. `last` is untouched."""
        return self._query(False)

    def intent(self, text: str | None, kind: str | None = None, target=None, **extra):
        """Report what the agent is trying to do now (the visualizer shows it).
        Proxy-side only: nothing reaches the server. None clears it. A display
        failure never stops the agent: it's logged and ignored. If the proxy
        drops the state connection (one that can't take an optional field such
        as target_serial), this reconnects and retries without the extras."""
        body = None
        if text is not None:
            body = {"text": text, **{k: v for k, v in extra.items() if v is not None}}
            if kind is not None:
                body["kind"] = kind
            if target is not None and None not in tuple(target)[:2]:
                body["target"] = [int(target[0]), int(target[1])]
        plain = None if body is None else {k: v for k, v in body.items()
                                          if k in ("text", "kind", "target", "loop", "trip", "trips")}
        for b in ([body, plain] if body != plain else [body]):
            try:
                self.st.sendall((json.dumps({"op": "intent", "intent": b}) + "\n").encode())
                line = self.st_file.readline()
            except OSError:
                line = b""
            if line:
                resp = json.loads(line)
                if not resp.get("ok"):
                    log(f"intent not shown: {resp.get('error')}")
                return
            self.st.close()
            self.st = socket.create_connection((HOST, self.state_port), timeout=10)
            self.st_file = self.st.makefile("rb")
        log("intent not shown: the proxy dropped the state connection")

    def wait(self, pred, timeout: float, poll: float = 0.1, full: bool = True):
        """Poll until pred(state) holds; None on timeout. full=False polls
        `movement()` (pred may only read `movement` and the events)."""
        end = time.monotonic() + timeout
        while True:
            st = self.state() if full else self.movement()
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

    def open_containers(self, serials, human) -> list[int]:
        """Open each container (containers_to_open order) with the stock
        double-click and wait for the server's 0x24, then a moment to find the
        item in the gump. Abort if one doesn't open."""
        for s in serials:
            log(f"opening container 0x{s:08X}")
            self.act(actions.dclick(s))
            if self.wait(lambda st: s in {serial_of(c) for c in st["world"].get("containers") or []},
                         OPEN_WAIT_S) is None:
                raise Abort(f"container 0x{s:08X} didn't open")
            human.wait("find")
        return list(serials)


class Mover:
    """Server-confirmed walking with human texture from a humanize.Human:
    per-plan route noise, straightened runs (nav.straighten), the stock
    held-key step cadence, run/walk routes, pauses and sidesteps. Plans on
    map data when the facet has it (use_map), else on walk memory.

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
        self.walkers = pathfind.Walkers()
        self.denied = set()          # (x, y, d) server denies seen this session (map planner)
        self.shove_denied = {}       # (x, y) -> monotonic time a shove into it was denied
        self.steps = 0
        self.blocked_count = 0
        self.doors_opened = 0
        self.sent_at = 0.0           # monotonic time the last walk went out (step cadence)
        self.refused_steps = 0       # steps not sent: the map with current objects refused them
        self.teleports = 0
        self._teleporters = {}       # facet -> {(x, y)} known teleporter tiles (store + session)
        self.gate_gumps_closed = 0
        self.step_mark = 0           # len(link.events) when the last walk went out
        self._after = None           # (full state, monotonic time) fetched at the end of the last step

    def step(self, d: int, run: bool = True, st=None) -> str:
        """Send one walk; wait for its outcome. Returns 'moved', 'turned',
        'blocked' or 'teleported' (the step landed us somewhere other than the
        next tile: an invisible server teleporter, remembered and avoided).
        `st`: a current full state (the caller's, fetched before pacing), so the
        walk goes out right when it is due. The outcome is polled with light
        movement queries; one full state follows (guard, facet, and `fresh_state`
        for the next step), so a step fits the mounted 0.1 s cadence."""
        st = st or self.link.state()
        before = self.link.pos(st)
        facet_before = st["world"]["self"].get("map")
        pkt = actions.walk(d, run=run)
        self.step_mark = len(self.link.events)
        deadline = time.monotonic() + MOVE_GATE_WAIT_S
        gate_waits = 0
        while True:
            resp = self.link.send(pkt)
            if resp == "OK":
                self.sent_at = time.monotonic()
                break
            if resp.startswith(MOVE_GATE) and "stalled" not in resp:
                if time.monotonic() > deadline:
                    raise Abort(f"walk gated too long ({resp[4:]})")
                time.sleep(0.02 if "pacing" in resp else 0.1)
                continue
            if resp.startswith(GATE_WAIT):
                gate_waits += 1
                if gate_waits > 40:
                    raise Abort("walk gated too long")
                log(f"agent gate closed ({resp[4:]}); waiting")
                time.sleep(GATE_POLL_S)
                continue
            raise Abort(f"walk refused: {resp}")
        mv = self.link.wait(lambda s: s["movement"]["inflight"] == 0, timeout=OUTCOME_WAIT_S,
                            poll=OUTCOME_POLL_S, full=False)
        if mv is None:
            raise Abort("walk outcome never arrived")
        after = self.link.pos(mv)
        if after[:2] == before[:2] and after[3] == before[3]:
            # denied: a teleporter may be about to move us (its position packet follows the deny)
            self.link.wait(lambda s: tuple(self.link.pos(s)[:2]) != tuple(before[:2]),
                           timeout=DENY_TELEPORT_GRACE_S, poll=0.05, full=False)
        st = self.link.state()
        self._after = (st, time.monotonic())
        self.guard(st)
        after = self.link.pos(st)
        if after[:2] != before[:2]:
            self.steps += 1
            facet_after = st["world"]["self"].get("map")
            if nav.chebyshev(tuple(before[:2]), tuple(after[:2])) > 1 or facet_after != facet_before:
                self._teleported(facet_before, nav.step(tuple(before[:2]), d), facet_after, after)
                return "teleported"
            return "moved"
        if after[3] != before[3]:
            return "turned"
        return "blocked"

    def fresh_state(self) -> dict:
        """The full state the last step ended with while it is still current
        (STATE_REUSE_S), else a new one: one world snapshot per step."""
        if self._after is not None and time.monotonic() - self._after[1] <= STATE_REUSE_S:
            return self._after[0]
        return self.link.state()

    def _teleported(self, facet, tile, to_facet, after):
        """Walking onto `tile` put us at `after`: remember the teleporter so
        plans stop walking over it by accident."""
        self.teleports += 1
        self.teleporter_tiles(facet).add(tuple(tile))
        if hasattr(self._source, "teleporter_record"):
            self._source.teleporter_record(facet, tile[0], tile[1], to_facet, after[0], after[1], after[2])
        log(f"teleported: stepping onto {tuple(tile)} put us at {tuple(after[:3])} (facet {to_facet}); "
            f"remembered, replanning")

    def teleporter_tiles(self, facet) -> set:
        """Known teleporter tiles on facet (memory store + this session)."""
        facet = 0 if facet is None else facet
        s = self._teleporters.get(facet)
        if s is None:
            src = self._source
            s = set(src.teleporters(facet)) if hasattr(src, "teleporters") else set()
            self._teleporters[facet] = s
        return s

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
        return self.walkers.get(st["world"]["self"].get("map"),
                                pathfind.ground_items(st["world"]["items"].values()))

    def step_walkable(self, st, cur, z, d) -> bool:
        """The client's own check right before a step: walkable from (cur, z) in
        direction d with the ground items the world model has *now* (objects that
        arrived after the plan count). True when there is no map for the facet."""
        walk = self.walk_map(st)
        return walk is None or walk.can_walk(cur[0], cur[1], z, d) is not None

    def door_at(self, tile, st=None, z=None) -> bool:
        """A ground item that is a door (tiledata Door flag, or classic door art)
        stands on `tile` (world model), within 15 z of `z` when given (the
        client's auto-open test)."""
        st = st or self.link.state()
        td = uomap.tiledata() if self.use_map else None
        for it in st["world"]["items"].values():
            if it.get("container") is None and (it.get("x"), it.get("y")) == tuple(tile):
                if z is not None and it.get("z") is not None and abs(it["z"] - z) > 15:
                    continue
                g = it.get("graphic")
                if g in DOOR_GRAPHICS or (td is not None and g is not None and td.item(g)
                                          and td.item(g).flags & uomap.DOOR):
                    return True
        return False

    def moongate_at(self, tile, st=None, z=None) -> bool:
        """A moongate (tiledata name, or MOONGATE_GRAPHICS without tiledata) stands
        on `tile` (world model), within 15 z of `z` when given. Player-cast gates
        come and go, so this reads the world model of the moment."""
        st = st or self.link.state()
        td = uomap.tiledata() if self.use_map else None
        for it in st["world"]["items"].values():
            if it.get("container") is None and (it.get("x"), it.get("y")) == tuple(tile):
                if z is not None and it.get("z") is not None and abs(it["z"] - z) > 15:
                    continue
                g = it.get("graphic")
                if g in MOONGATE_GRAPHICS or (td is not None and g is not None and td.item(g)
                                              and "moongate" in (td.item(g).name or "").lower()):
                    return True
        return False

    def close_gate_gumps(self, mark: int, tile, label: str) -> int:
        """The route passed over the moongate on `tile`: close the gump(s) it
        opened since event `mark` (the step that landed there), as a player
        right-clicks a popup away after a look: the stock 0xB1 with button 0
        (actions.gump_reply; the proxy closes the client's copy). Only gumps still
        open that a client can close: never the captcha, a noclose gump or one
        without reply buttons (the decoy shape). Returns how many were closed."""
        def opened():
            return [ev for ev in self.link.events[mark:] if ev.get("ev") == "gump_open"]
        if self.link.wait(lambda s: opened(), timeout=GATE_GUMP_WAIT_S) is None:
            return 0
        self.human.wait("menu")
        live = {(serial_of(g["serial"]), serial_of(g["gump_id"])): g
                for g in self.link.state()["world"].get("gumps") or [] if g.get("open")}
        closed = 0
        for ev in opened():
            key = (serial_of(ev["serial"]), serial_of(ev["gump_id"]))
            g = live.pop(key, None)              # pop: one reply per gump
            if g is None:                        # answered in the client meanwhile, or gone
                continue
            layout = g.get("layout") or ""
            if key[1] == CAPTCHA_GUMP_ID or "noclose" in layout.lower() or not parse_layout(layout)["buttons"]:
                continue
            log(f"{label}: closing gump 0x{key[1]:08X} from the moongate at {tuple(tile)} (passing through)")
            self.link.act(actions.gump_reply(key[0], key[1], 0, layout, g.get("lines") or []))
            self.gate_gumps_closed += 1
            closed += 1
        return closed

    def _open_ahead(self, tile, z, opened: set, label: str, st=None):
        """The client's auto-open: facing a door on the next tile (right after the
        turn or step that faces it), the stock open-door request, once per door per
        route (PlayerMobile.TryOpenDoors on direction/position change). The client's
        own Auto Open Doors must be off: it follows the character (per-step
        re-anchor) and would send a second request that shuts the door again."""
        if not self.doors or tile in opened or not self.door_at(tile, st, z):
            return
        opened.add(tile)
        log(f"{label}: facing the door at {tile}; opening it")
        self.link.act(actions.open_door())
        self.doors_opened += 1

    def pace(self, run: bool, st=None):
        """Wait until the next step is due at the stock held-key cadence, mounted or
        not as the proxy's world model says (state `movement.mounted`, the same
        source as its pacing floor)."""
        st = st or self.link.state()
        self.human.pace_step(run, self.sent_at, bool(st["movement"].get("mounted")))

    def _try_door(self, cur, d, run) -> bool:
        """A door still denied the step (closed again, or unseen): after a player's
        reaction time, send the open-door request once more and retry the move.
        True if the retry moved."""
        log(f"move {d} from {cur} blocked by a door; opening it")
        self.human.wait("read")
        self.link.act(actions.open_door())
        self.doors_opened += 1
        self.pace(run)
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
        st = self.link.state()
        tele = self.teleporter_tiles(st["world"]["self"].get("map"))
        options = [t for t in options if t not in occupied and t not in avoid and t not in tele
                   and not self.moongate_at(t, st)]
        if not options:
            return False
        side = self.human.choice(options)
        d = nav.direction(cur, side)
        outcome = self.step(d, run)
        if outcome == "turned":
            self.pace(run, st)
            outcome = self.step(d, run)
        if outcome == "moved":
            self.mem.add_step(cur, tuple(self.link.pos()[:2]))
            return True
        return outcome == "teleported"

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
        mobiles=False (is the route only temporarily cut?). Zig-zag stretches are
        regrouped into straight runs (nav.straighten), as a player walks them."""
        pos = st["movement"]["pos"]
        cur = (pos[0], pos[1])
        occ = self.occupied(st) - {cur}
        now = time.monotonic()
        hard = {t for t in occ if now - self.shove_denied.get(t, -1e9) < SHOVE_RETRY_S} if mobiles else set()
        noise = self.human.cost_scale()

        def cost(a, b):
            return (noise(a, b) if noise else 1.0) * (MOBILE_COST_X if b in occ else 1.0)

        facet = st["world"]["self"].get("map")
        self.mem = self.mem_for(facet)
        # never walk over a known teleporter tile by accident (only onto one as the goal)
        goal_tile = getattr(goal, "center", None) if getattr(goal, "radius", None) == 0 else None
        hard = hard | (self.teleporter_tiles(facet) - {goal_tile})
        diagonal_first = lambda: self.human.choice((True, False))  # noqa: E731
        walk = self.walk_map(st)
        if walk is not None:
            path = pathfind.plan(walk, (pos[0], pos[1], pos[2]), goal,
                                 blocked_moves=self.denied, occupied=hard, cost_scale=cost)
            if path is None:
                return None, walk
            path = nav.straighten(
                path, lambda s, d: None if (s[0], s[1], d) in self.denied else walk.can_walk(s[0], s[1], s[2], d),
                diagonal_first, hard | occ)
            return [(x, y) for x, y, _ in path], walk
        path = nav.plan(self.mem, cur, goal, extra_blocked=hard, cost_scale=cost)
        if path is None:
            return None, None
        mem = self.mem

        def mem_step(s, d):
            if (s, d) in mem.blocked or (d & 1 and ((s, (d - 1) % 8) in mem.blocked
                                                   or (s, (d + 1) % 8) in mem.blocked)):
                return None
            n = nav.step(s, d)
            return n if n in mem.tiles else None
        return nav.straighten(path, mem_step, diagonal_first, hard | occ), None

    def walk_to(self, center_fn, radius: int, label: str, max_moves: int = 250, z_ok=None, gate=None):
        """Walk until within `radius` (Chebyshev) of center_fn(), re-evaluated
        on every replan (NPCs wander). `z_ok(z)` also requires the standing
        height (same level as the target, not a cave below or a floor above);
        only the map planner can honour it. `gate`: the (x, y) of a moongate
        this walk means to use; its gump is left for the caller. Every other
        moongate the route steps onto gets its gump closed (close_gate_gumps)."""
        gate = tuple(gate) if gate is not None else None
        replans = 0
        start_steps = self.steps
        tried_doors = set()          # doors that denied a step: one more open request each
        opened = set()               # doors opened ahead on this route (the client's auto-open)
        run = True                   # the client's Always Run is on: a player never walks a route
        mobile_wait_until = None
        while True:
            st = self.link.state()
            self.guard(st)
            cur = tuple(self.link.pos(st)[:2])
            goal = nav.within(tuple(center_fn()), radius, z_ok)
            if goal(cur) and (z_ok is None or self.walk_map(st) is None or z_ok(self.link.pos(st)[2])):
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
            for i, nxt in enumerate(path[1:], start=1):
                st = self.fresh_state()
                pos = self.link.pos(st)
                cur, z = (pos[0], pos[1]), pos[2]
                d = nav.direction(cur, nxt)
                occupied_at_step = self.occupied(st)       # who stood there when we tried (a shove)
                if walk is not None and not self.step_walkable(st, cur, z, d):
                    # an object arrived after the plan (live 20260930_182751: a barrel). The stock
                    # client checks every step (PlayerMobile.Walk -> CanWalk) and sends nothing
                    self.refused_steps += 1
                    self.denied.add((cur[0], cur[1], d))           # plan around it (nothing was sent)
                    log(f"{label}: step {d} from {cur} now blocked (an object arrived after the plan); "
                        f"replanning without sending it")
                    replan = True
                    break
                if pos[3] == d:              # already facing it (e.g. at the start of a route)
                    self._open_ahead(nxt, z, opened, label, st)
                self.pace(run, st)
                outcome = self.step(d, run, st)
                if outcome == "turned":      # facing it now: the client's auto-open fires on the turn
                    self._open_ahead(nxt, z, opened, label)
                    self.pace(run, st)
                    outcome = self.step(d, run)
                if outcome == "blocked" and self.doors and nxt not in tried_doors \
                        and self.door_at(nxt):
                    tried_doors.add(nxt)
                    if self._try_door(cur, d, run):
                        outcome = "moved"
                if outcome == "teleported":
                    replan = True
                    break
                if outcome == "moved":
                    after = self.fresh_state()
                    here = self.link.pos(after)
                    new = tuple(here[:2])
                    self.mem.add_step(cur, new)
                    if new != gate and self.moongate_at(new, after, z=here[2]):
                        self.close_gate_gumps(self.step_mark, new, label)
                    if new != nxt:
                        log(f"{label}: landed on {new}, expected {nxt}; replanning")
                        replan = True
                        break
                    if self.steps - start_steps > max_moves:
                        raise Abort(f"{label}: exceeded {max_moves} moves")
                    if i + 1 < len(path) and nav.direction(new, path[i + 1]) == d:
                        # landed facing the next tile: the client's auto-open fires on the step
                        self._open_ahead(path[i + 1], here[2], opened, label, after)
                    self.human.after_step()
                    if len(path) - i > 3 and self.human.wander():
                        st = self.link.state()
                        if self._sidestep(new, set(path[i:i + 2]), run, walk, self.link.pos(st)[2]):
                            log(f"{label}: sidestepped at {new}; replanning")
                            break
                    continue
                self.blocked_count += 1
                if nxt in occupied_at_step:
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

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
import os
import socket
import time

import actions
import charsel
import guards
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
# The server's answer to an open-door request at a door we may not use (live 2026-10-11 16:43:37,
# a foreign house's door on an escape route): 500024 "Opening door..." then 1061637 "You are not
# allowed to access this.", and the step stays denied. The door is a wall for the rest of the walk.
DOOR_REFUSED_CLILOC = 1061637
# Spell fields on the ground: walls for the planner (Mover.plan), dispelled when they cut the route
# (Mover dispel). Wall of Stone ("In Sanct Ylem") is art 0x4D1B on Outlands, tiledata "wall of stone"
# (live 2026-10-11 16:46:04: three in a row at 4186-4188,799 1.5 s after Jimii Hendrix's words, all
# deleted 15.9 s later); not RunUO's 0x0080-0x0082 [INFERENCE: none of those seen]. The field spells'
# art by tiledata name: "Field of Poison" 0x3914-0x3929, "field of energy" 0x3946-0x3964 (Impassable),
# "field of paralysis" 0x3967-0x398A, "Field of fire" 0x398C-0x399F (the server lets you walk into
# fire, poison and paralysis fields; tiledata doesn't flag them). Some are lasting decoration (fire
# field art at 1176-1182,1340-1349, seen 12:54-13:29 the same day): they count too.
FIELD_GRAPHICS = frozenset([0x4D1B, *range(0x3914, 0x392A), *range(0x3946, 0x3965), *range(0x3967, 0x398B),
                            *range(0x398C, 0x39A0)])
# After a dispel went out: how long to look for the field's delete before replanning.
FIELD_GONE_WAIT_S = 1.0
# Mobiles: shoving through one needs stamina (UOO; threshold unknown, RunUO needs
# full stamina [INFERENCE]). Plans prefer going around (entering a mobile's tile
# costs MOBILE_COST_X normal steps); after a denied shove that tile is a wall for
# SHOVE_RETRY_S. A route cut only by such tiles is waited out up to MOBILE_WAIT_S.
MOBILE_COST_X = 4.0
SHOVE_RETRY_S = 15.0
MOBILE_WAIT_S = 90.0
# A step the server refused where the map has it walkable (a mobile we didn't see in time: live 2026-10-17 11:12:45 a
# player, King Vitaman, showed on the tile 0.4 s after the refusal) blocks that move only for DENY_RETRY_S, and is tried again at once when nothing else gets through.
DENY_RETRY_S = 30.0
# Danger zones (Mover.danger, set by guards: creatures we must not walk past): entering a
# tile inside one costs DANGER_COST_X normal steps, so routes bend around monster groups
# when a way around exists (live 2026-10-03: a straight map route through a harpy nest).
DANGER_COST_X = 30.0
# A walk replanned round danger zones this many times in a row without its route getting shorter
# is boxed in and given up (live 2026-10-05, witcher_137: 178 replans in 4.5 min swinging between
# two routes round creatures on every side, while the user watched Dan run back and forth).
DANGER_STALL_REPLANS = 10
# After a danger replan, tiles off the route we were on cost this much more: a creature that moves
# a tile shouldn't flip the route to the other side of it and back (live 2026-10-05, witcher_36: a
# wandering wisp swung the walk between routes of 31 and 93 steps every second or two).
STICKY_X = 1.25
# A teleport no walk aimed at (Mover.step_back): step back through a known teleporter within STEP_BACK_R
# that leads within STEP_BACK_R of where we were; jumps under STEP_BACK_MIN tiles on the same facet are
# short hops a walk just carries on from.
STEP_BACK_R = 4
STEP_BACK_MIN = 8
TELEPORTER_SEEDS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "teleporters.json")
_SEEDS = None
# A creature's zone that moved asks for a replan only when it touches the next this many tiles of
# the route; farther ahead the walk goes on and looks again as it gets there (live 2026-10-05,
# witcher_46: wolves and a wolfhound moving round a reaper swung the walk between routes of 62 and
# 86 steps on every step; whole-route checks replanned on every far-off zone move).
DANGER_LOOKAHEAD = 8
# After a danger replan the new route is kept for this many steps unless a zone covers the next
# DANGER_NEXT tiles: a fresh route abandoned after one step swings back and forth (live 2026-10-05,
# witcher_253: one wolf, the walk alternating between (861,834) and (865,833), routes 74 and 56)
DANGER_COMMIT_STEPS = 5
DANGER_NEXT = 2
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
# A world save (every ~15 min live: "The world is saving, please wait." ... "World save complete. ...") stops the
# server for 2-4 s; it answers what came in meanwhile only afterwards. A wait's deadline doesn't run during one
# (Deadline), at most WORLD_SAVE_MAX_S past its start line in case the end line is missed.
WORLD_SAVE_MAX_S = 15.0


def world_save(ev: dict) -> str | None:
    """'start' / 'done' for the server's world save lines, else None."""
    if ev.get("ev") != "speech_heard":
        return None
    text = (ev.get("text") or "").strip()
    if text.startswith("The world is saving"):
        return "start"
    if text.startswith("World save complete"):
        return "done"
    return None


class Deadline:
    """A wait's deadline that a world save doesn't use up: `see` the new events, then `over()`. While a save is
    under way (its start line seen, not its end; `save_t0`: the monotonic time a save began before the wait, e.g.
    Link.save_t0) the wait goes on, for at most WORLD_SAVE_MAX_S; its end adds the save's length to the deadline.
    Live 2026-10-20 11:58:04-07 (mining-20261020-114825-eee6): a rune library tome's double-clicks went out 0.6 s
    into a save, its gump came 0.1 s after "World save complete", and the 2.5 s wait had already given up ("the
    book's gump didn't open"): the run aborted."""

    def __init__(self, timeout: float, save_t0: float | None = None):
        self.end = time.monotonic() + timeout
        self.save_t0 = save_t0

    def see(self, evs):
        for ev in evs:
            kind = world_save(ev)
            if kind == "start" and self.save_t0 is None:
                self.save_t0 = time.monotonic()
            elif kind == "done" and self.save_t0 is not None:
                self.end += time.monotonic() - self.save_t0
                self.save_t0 = None

    @property
    def saving(self) -> bool:
        return self.save_t0 is not None and time.monotonic() - self.save_t0 < WORLD_SAVE_MAX_S

    def over(self) -> bool:
        return not self.saving and time.monotonic() > self.end + (WORLD_SAVE_MAX_S if self.save_t0 else 0.0)



def reach_z(obj_z: int, obj_height: int):
    """z_ok for working an object (a tree): the standing body must overlap it
    vertically, i.e. stand on its level, as a player who can see and click it
    would. A cave below it or a floor above it doesn't count (live 2026-09-29:
    the agent chopped surface trees at z 5 from a cave at z -20)."""
    return lambda z: obj_z - BODY_HEIGHT < z < obj_z + max(obj_height, 1)


def same_floor(ref_z: int):
    """z_ok for dealing with an NPC: at most one storey above or below it."""
    return lambda z: abs(z - ref_z) <= FLOOR_SPAN


def not_above(obj_z: int):
    """z_ok for using an object that stands above the floor, like a tome on a bookshelf: on a floor at
    or below it, at most two storeys down, never on a floor above it. The server refuses an object it
    judges out of sight with "That cannot be seen." (live 2026-10-19: the walk to the DTF rune library
    ended on the floor above the shelves, z 26 over tomes at z 7-22, and no tome opened; Cambria's
    tomes stand at z 25-37 over a floor at z 0-6)."""
    return lambda z: obj_z - 2 * FLOOR_SPAN <= z <= obj_z


class Abort(Exception):
    pass


class GatePaused(BaseException):
    """The agent gate was paused while Link.end_on_pause (the hunt runner: user 2026-10-18, after the run walked on
    out of the Mausoleum while he played Logan, "have the run end once I pause the agent gate"). A BaseException,
    as KeyboardInterrupt for `ctl stop`, so no `except Exception` / `except Abort` on the way takes it for a failed
    action."""


def log(msg: str):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def teleporter_seeds() -> list:
    """harness/data/teleporters.json's known teleporters (loaded once)."""
    global _SEEDS
    if _SEEDS is None:
        try:
            with open(TELEPORTER_SEEDS, encoding="utf-8") as f:
                _SEEDS = json.load(f)["teleporters"]
        except OSError:
            _SEEDS = []
    return _SEEDS


def cheb(a, b) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def serial_of(v) -> int:
    return int(v, 16) if isinstance(v, str) else int(v)


def stamina_ok(st) -> bool:
    """Whether the stock client would run now: PlayerMobile.Walk drops a run to a
    walk at stamina <= 1 (e.g. hamstrung: stamina 0 for 3 s)."""
    stam = st["world"]["self"].get("stam")
    return stam is None or stam > 1


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


BANKER_HUMAN_BODIES = (0x190, 0x191)


def banker_from_labels(world: dict):
    """(serial, label, (x, y)) of a positioned mobile whose latest click label
    (world.labels) names it a banker, else None."""
    for key, text in world.get("labels", {}).items():
        m = world["mobiles"].get(key)
        if "the banker" in text.lower() and m and m.get("x") is not None:
            return serial_of(key), text, (m["x"], m["y"])
    return None


def look_at(link, serial: int, known_name: bool):
    """Single-click an entity exactly like the stock client: 0x09, then 0x34
    status request, plus 0x98 name request when the name is unknown."""
    link.act(actions.single_click(serial))
    link.act(actions.status_request(serial))
    if not known_name:
        link.act(actions.name_request(serial))


def label_since(link, serial: int, since_idx: int):
    """The first text `serial` said (its click label) since link.events[since_idx]."""
    for ev in link.events[since_idx:]:
        if ev.get("ev") == "speech_heard" and ev.get("serial") in (serial, f"0x{serial:08X}"):
            return ev.get("text")
    return None


def find_banker(link, human, radius: int, max_clicks: int, log=print):
    """(serial, label, (x, y)) of a banker: one whose click label is already known,
    else single-click the human NPCs within `radius` (nearest first, human pace)
    until one's label says banker. Abort when none does (errand_bank.py, loop_hunt.py)."""
    st = link.state()
    known = banker_from_labels(st["world"])
    if known:
        return known
    me = tuple(link.pos(st)[:2])
    cands = []
    for key, m in st["world"]["mobiles"].items():
        if m.get("x") is None or m.get("graphic") not in BANKER_HUMAN_BODIES:
            continue
        serial = serial_of(key)
        if serial == st["movement"]["self_serial"]:
            continue
        dist = cheb(me, (m["x"], m["y"]))
        if dist <= radius:
            cands.append((dist, serial, m.get("name")))
    cands.sort()
    log(f"banker search: {len(cands)} nearby NPCs to look at")
    for dist, serial, name in cands[:max_clicks]:
        human.wait("use")
        mark = len(link.events)
        look_at(link, serial, known_name=bool(name))
        link.wait(lambda s: label_since(link, serial, mark) is not None, timeout=1.5)
        label = label_since(link, serial, mark)
        log(f"  looked at {name or hex(serial)} ({dist} tiles): {label!r}")
        if label and "banker" in label.lower():
            m = link.state()["world"]["mobiles"].get(f"0x{serial:08X}") or {}
            return serial, label, (m.get("x"), m.get("y"))
    raise Abort("no banker found nearby")


class Link:
    """Control-port actions + state-port feedback, with an event cursor. `char`
    (name or 0xSERIAL, harness/charsel.py) picks the character when several are
    logged in through the proxy: the control connection is bound to it with an
    `@char` frame and every state/intent request names it."""

    def __init__(self, control_port: int, state_port: int, char: str | None = None):
        self.char = char
        self.ctl = socket.create_connection((HOST, control_port), timeout=10)
        if char:
            r = self.send(charsel.CONTROL_PREFIX + char.encode("utf-8"))
            if not r.startswith("OK"):
                self.ctl.close()
                raise Abort(f"control port: {r}")
        self.state_port = state_port
        self.st = socket.create_connection((HOST, state_port), timeout=10)
        self.st_file = self.st.makefile("rb")
        self.since = 0
        self.events = []
        self.event_t = []            # each event's time (envelope t), parallel to events
        self.last = None
        self.end_on_pause = False    # the agent gate paused (the user took over): raise GatePaused, don't wait
        self.save_t0 = None          # monotonic start of the world save under way (its end line not seen yet)

    def _req(self, req: dict) -> bytes:
        if self.char:
            req["char"] = self.char
        return (json.dumps(req) + "\n").encode()

    def send(self, pkt: bytes) -> str:
        """One control-port frame; the proxy's reply ("OK" or "ERR ...")."""
        self.ctl.sendall(len(pkt).to_bytes(2, "big") + pkt)
        n = int.from_bytes(self._recv(self.ctl, 2), "big")
        return self._recv(self.ctl, n).decode()

    def act(self, pkt: bytes):
        """Send an action; wait out a paused/break gate (a pause raises GatePaused under end_on_pause), abort on any
        other refusal."""
        waited = False
        while True:
            resp = self.send(pkt)
            if resp == "OK":
                if waited:
                    log("agent gate open again; resuming")
                return
            self.check_pause(resp)
            if resp.startswith(GATE_WAIT):
                if not waited:
                    log(f"agent gate closed ({resp[4:]}); waiting")
                    waited = True
                time.sleep(GATE_POLL_S)
                continue
            raise Abort(f"action 0x{pkt[0]:02X} refused: {resp}")

    def check_pause(self, resp: str):
        """end_on_pause and the gate refused us as paused: GatePaused."""
        if self.end_on_pause and resp.startswith(GATE_WAIT[0]):
            raise GatePaused("the agent gate was paused (the user took over)")

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
        self.st.sendall(self._req({"op": "state", "since": self.since, "snapshot": snapshot}))
        resp = json.loads(self.st_file.readline())
        if not resp.get("ok"):
            raise Abort(f"state port: {resp.get('error')}")
        if self.end_on_pause and (resp.get("gate") or {}).get("paused"):
            raise GatePaused("the agent gate was paused (the user took over)")
        world = [env for env in resp["events"] if env["origin"] == "world"]
        self.events.extend(env["data"] for env in world)
        for env in world:
            kind = world_save(env["data"])
            if kind == "start":
                self.save_t0 = time.monotonic()
            elif kind == "done":
                self.save_t0 = None
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
                self.st.sendall(self._req({"op": "intent", "intent": b}))
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

    def wait(self, pred, timeout: float, poll: float = 0.05, full: bool = True):
        """Poll until pred(state) holds; None on timeout (a world save doesn't count: Deadline). full=False
        polls `movement()` (pred may only read `movement` and the events)."""
        deadline, mark = Deadline(timeout, self.save_t0), len(self.events)
        while True:
            st = self.state() if full else self.movement()
            if pred(st):
                return st
            deadline.see(self.events[mark:])
            mark = len(self.events)
            if deadline.over():
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

    guard(st) is called after every step outcome (timeouts, HP, stalls...).

    Spell fields (FIELD_GRAPHICS) are walls for every plan. `dispel(serial, item) -> bool`, when
    given, is called when fields cut the route and no way round exists within the walk's bound
    (max_route): once per field serial per walk_to, True when the cast went out (the walk then
    looks briefly for the field to go and replans). Without it a cut route fails as before."""

    def __init__(self, link: Link, memory: nav.WalkMemory, human,
                 max_blocked: int = 12, guard=None, doors: bool = False, use_map: bool = True, dispel=None):
        self.link = link
        self._source = memory        # memory.Memory (per-facet projection) or a fixed nav.WalkMemory
        self._mems = {}
        self.mem = self.mem_for(0)
        self.human = human
        self.max_blocked = max_blocked
        self.guard = guard or (lambda st: None)
        self.doors = doors
        self.use_map = use_map
        self.dispel = dispel
        self.walkers = pathfind.Walkers()
        self.denied = set()          # (x, y, d) server denies seen this session (map planner)
        self.denied_t = {}           # the ones of them on map-walkable steps -> monotonic time (expire: DENY_RETRY_S)
        self.shove_denied = {}       # (x, y) -> monotonic time a shove into it was denied
        self.refresh = None          # host's stamina potion: () -> True when it drank (walk_to, a denied shove)
        self.no_idle = None          # host's (state) -> True: no walk pause or sidestep now (walk_to)
        self.steps = 0
        self.blocked_count = 0
        self.doors_opened = 0
        self.sent_at = 0.0           # monotonic time the last walk went out (step cadence)
        self.refused_steps = 0       # steps not sent: the map with current objects refused them
        self.teleports = 0
        self._teleporters = {}       # facet -> {(x, y)} known teleporter tiles (seeds + store + session)
        self._tele_to = {}           # facet -> {(x, y): (to_facet, x, y, z)} where each one puts us
        self.gate_gumps_closed = 0
        self.step_mark = 0           # len(link.events) when the last walk went out
        self._after = None           # (full state, monotonic time) fetched at the end of the last step
        self.danger = {}             # key -> ((x, y), radius): tiles to route around (danger_cost)
        self.sticky = set()          # tiles of the route a danger replan left: cost STICKY_X (walk_to)
        self.danger_tiles = set()    # (x, y) around remembered monster sightings (travel_guard.remembered)
        self.replan_requested = False   # set by a guard (new danger): replan after this step
        self.walled = set()          # (x, y) walls for this walk_to only: doors that refused us (walk_to)

    def in_danger(self, tile) -> bool:
        return tile in self.danger_tiles or any(cheb(tile, c) <= r for c, r in self.danger.values())

    def danger_cost(self, tile) -> float:
        """A tile's cost factor from the danger zones: inside a zone half of DANGER_COST_X flat
        (a detour of up to ~15 steps per zone tile still beats entering) and half by depth, from
        the rim to the centre, summed over overlapping zones; so a route that can't stay out keeps
        to the rims, as far from the creatures as it can (user, 2026-10-05: "he kept trying to go
        that way instead of going around"; a flat factor made any crossing as good as straight
        through the middle). Remembered sightings (danger_tiles) cost DANGER_COST_X flat."""
        f = DANGER_COST_X if tile in self.danger_tiles else 1.0
        for c, r in self.danger.values():
            d = cheb(tile, c)
            if d <= r:
                f += DANGER_COST_X * (0.5 + 0.5 * (r + 1 - d) / (r + 1))
        return f

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
        gated = False
        while True:
            resp = self.link.send(pkt)
            if resp == "OK":
                self.sent_at = time.monotonic()
                if gated:
                    log("agent gate open again; resuming")
                break
            if resp.startswith(MOVE_GATE) and "stalled" not in resp:
                if time.monotonic() > deadline:
                    raise Abort(f"walk gated too long ({resp[4:]})")
                time.sleep(0.02 if "pacing" in resp else 0.1)
                continue
            if resp.startswith(GATE_WAIT):
                if getattr(self.link, "end_on_pause", False):
                    self.link.check_pause(resp)
                # paused or on a break: wait it out, as Link.act does (live 2026-10-17 17:26:28-17:29:48 the user
                # took over mid-walk to fill a soul jar; the old 40-poll cap aborted the run in the Mausoleum). The
                # hunt runner ends on a pause instead (Link.end_on_pause)
                if not gated:
                    log(f"agent gate closed ({resp[4:]}); waiting")
                    gated = True
                time.sleep(GATE_POLL_S)
                deadline = time.monotonic() + MOVE_GATE_WAIT_S
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
        after = self.link.pos(st)
        if after[:2] != before[:2] and nav.chebyshev(tuple(before[:2]), tuple(after[:2])) == 1 \
                and st["world"]["self"].get("map") == facet_before:
            self._guard_entries(facet_before, before, after)   # before guard(): a flight stops on it
        self.guard(st)
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

    def _guard_entries(self, facet, before, after):
        """A one-tile step: remember the tile if the server said since it went out
        that we're under the guards' protection (guards.entries)."""
        if not hasattr(self._source, "guard_point_record"):
            return
        events = [{"ev": "step", "from": list(before[:2]), "to": list(after[:2])}]
        events += [e for e in self.link.events[self.step_mark:] if e.get("ev") == "cliloc"]
        for x, y in guards.entries(events):
            self._source.guard_point_record(facet, x, y)

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
        self._tele_to.setdefault(0 if facet is None else facet, {})[tuple(tile)] = (to_facet, *after[:3])
        if hasattr(self._source, "teleporter_record"):
            self._source.teleporter_record(facet, tile[0], tile[1], to_facet, after[0], after[1], after[2])
        log(f"teleported: stepping onto {tuple(tile)} put us at {tuple(after[:3])} (facet {to_facet}); "
            f"remembered, replanning")

    def teleporter_tiles(self, facet) -> set:
        """Known teleporter tiles on facet: TELEPORTER_SEEDS, the memory store's, this session's."""
        facet = 0 if facet is None else facet
        s = self._teleporters.get(facet)
        if s is None:
            to = {tuple(t["from"]): tuple(t["to"]) for t in teleporter_seeds() if t["facet"] == facet}
            src = self._source
            if hasattr(src, "teleporters"):
                to.update({tuple(k): tuple(v) for k, v in src.teleporters(facet).items()})
            self._tele_to[facet] = to
            s = self._teleporters[facet] = set(to)
        return s

    def step_back(self, before, before_facet, label) -> bool:
        """After a teleport the walk didn't aim at: a known teleporter within STEP_BACK_R of where it put us
        whose destination is within STEP_BACK_R of where we were, stepped onto at once (live 20261015_115604:
        a Stationary Penalty walk took Logan up the Mausoleum stairs; the walk found no route on level 2,
        the run stopped there and an abomination killed him). True when we are back."""
        st = self.link.state()
        here, facet = self.link.pos(st), st["world"]["self"].get("map")
        if facet == before_facet and cheb(here, before) < STEP_BACK_MIN:
            return False
        self.teleporter_tiles(facet)
        back = [t for t, to in self._tele_to.get(0 if facet is None else facet, {}).items()
                if cheb(t, here) <= STEP_BACK_R and to[0] == (before_facet or 0) and cheb(to[1:3], before) <= STEP_BACK_R]
        if not back:
            log(f"{label}: teleported to {tuple(here[:2])}; no known way back")
            return False
        t = min(back, key=lambda t: cheb(t, here))
        log(f"{label}: an unplanned teleport to {tuple(here[:2])}; stepping back through {t}")
        sticky, walled = self.sticky, self.walled
        try:
            self.walk_to(lambda: t, 0, f"{label}: back", max_moves=2 * STEP_BACK_R, urgent=True)
        except Abort as e:
            log(f"{label}: stepping back: {e}")
        finally:
            self.sticky, self.walled = sticky, walled
        now = self.link.pos(self.link.state())
        return cheb(now, before) <= STEP_BACK_R

    def occupied(self, st=None) -> set:
        """Tiles currently occupied by other mobiles."""
        st = st or self.link.state()
        me = st["movement"]["self_serial"]
        return {(m["x"], m["y"]) for key, m in st["world"]["mobiles"].items()
                if m.get("x") is not None and serial_of(key) != me}

    # ---------------------------------------------------------------- map
    def walk_map(self, st, skip=()):
        """pathfind.Walk for the player's facet, or None (no map data there).
        Ground items from the world model are added as dynamic objects (but those whose
        world key is in `skip`); the doors of houses we're not shut in are locked
        (Walkers.get `inside`)."""
        if not self.use_map:
            return None
        items = st["world"]["items"]
        return self.walkers.get(st["world"]["self"].get("map"),
                                pathfind.ground_items(v for k, v in items.items() if k not in skip),
                                inside=tuple(self.link.pos(st)[:3]))

    @staticmethod
    def fields(st) -> dict:
        """Spell fields on the ground now (FIELD_GRAPHICS): world key -> item."""
        return {k: it for k, it in st["world"]["items"].items()
                if it.get("graphic") in FIELD_GRAPHICS and it.get("container") is None and it.get("x") is not None}

    @staticmethod
    def fields_on(path, fields: dict) -> list:
        """[(key, item)] of the `fields` a route of (x, y) tiles runs into, in route order: on a
        tile it steps onto, or on a corner tile of a diagonal step (an Impassable field there
        refuses the diagonal, pathfind.Walk.can_walk)."""
        at = {}
        for k, it in fields.items():
            at.setdefault((it["x"], it["y"]), []).append((k, it))
        out, seen = [], set()
        for a, b in zip(path, path[1:]):
            tiles = [tuple(b)]
            if a[0] != b[0] and a[1] != b[1]:
                tiles += [(b[0], a[1]), (a[0], b[1])]
            for t in tiles:
                for k, it in at.get(t, ()):
                    if k not in seen:
                        seen.add(k)
                        out.append((k, it))
        return out

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

    def _open_ahead(self, tile, z, opened: dict, label: str, st=None):
        """The client's auto-open: facing a door on the next tile (right after the
        turn or step that faces it), the stock open-door request, once per door per
        route (PlayerMobile.TryOpenDoors on direction/position change). The client's
        own Auto Open Doors must be off: it follows the character (per-step
        re-anchor) and would send a second request that shuts the door again.
        `opened`: door tile -> len(link.events) when its request went out (door_refused)."""
        if not self.doors or tile in opened or not self.door_at(tile, st, z):
            return
        opened[tile] = len(self.link.events)
        log(f"{label}: facing the door at {tile}; opening it")
        self.link.act(actions.open_door())
        self.doors_opened += 1

    def door_refused(self, mark: int) -> bool:
        """The server refused an open-door request since event `mark` (DOOR_REFUSED_CLILOC)."""
        return any(ev.get("ev") == "cliloc" and ev.get("cliloc") == DOOR_REFUSED_CLILOC
                   for ev in self.link.events[mark:])

    def _wall_door(self, tile, label: str, why: str):
        """A door we may not use: its tile, and the door leaves next to it (a double door; a
        house's doors share its access rule [INFERENCE: RunUO BaseHouse.CheckAccess]), are walls
        for the rest of this walk_to. Not remembered past it (user, 2026-10-11)."""
        st = self.link.state()
        leaves = {tile} | {n for n in (nav.step(tuple(tile), d) for d in range(8)) if self.door_at(n, st)}
        self.walled |= leaves
        log(f"{label}: the door at {tuple(tile)} {why}; a wall for this walk ({sorted(leaves)})")

    def pace(self, run: bool, st=None):
        """Wait until the next step is due at the stock held-key cadence, mounted or
        not as the proxy's world model says (state `movement.mounted`, the same
        source as its pacing floor)."""
        st = st or self.link.state()
        self.human.pace_step(run, self.sent_at, bool(st["movement"].get("mounted")))

    def _try_door(self, cur, d, run, urgent: bool = False) -> bool:
        """A door still denied the step (closed again, or unseen): after a player's
        reaction time (none in an urgent walk), send the open-door request once more and
        retry the move. True if the retry moved."""
        log(f"move {d} from {cur} blocked by a door; opening it")
        if not urgent:
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

    def plan(self, st, goal, mobiles: bool = True, max_steps: int | None = None, through_fields: bool = False):
        """(path of (x, y) tiles including the start, walk or None). Also points
        self.mem at the current facet's walk memory. Mobiles' tiles cost
        MOBILE_COST_X (shove through them if going around is longer); tiles
        where a shove was denied in the last SHOVE_RETRY_S are walls unless
        mobiles=False (is the route only temporarily cut?). Zig-zag stretches are
        regrouped into straight runs (nav.straighten), as a player walks them.
        `max_steps`: the map planner looks only for routes that short (None when there's
        none). A long map search runs the guard every pathfind.TICK_EVERY expansions, so a
        threat raises out of it instead of waiting for the search to end. Spell fields
        (fields) and doors that refused us (walled) are walls; `through_fields`: as if the
        fields weren't there (would a dispel open the way?)."""
        pos = st["movement"]["pos"]
        cur = (pos[0], pos[1])
        occ = self.occupied(st) - {cur}
        now = time.monotonic()
        hard = {t for t in occ if now - self.shove_denied.get(t, -1e9) < SHOVE_RETRY_S} if mobiles else set()
        noise = self.human.cost_scale()

        sticky = self.sticky

        def cost(a, b):
            return ((noise(a, b) if noise else 1.0) * (MOBILE_COST_X if b in occ else 1.0)
                    * (self.danger_cost(b) if self.danger or self.danger_tiles else 1.0)
                    * (STICKY_X if sticky and b not in sticky else 1.0))

        facet = st["world"]["self"].get("map")
        self.mem = self.mem_for(facet)
        # never walk over a known teleporter tile by accident (only onto one as the goal)
        goal_tile = getattr(goal, "center", None) if getattr(goal, "radius", None) == 0 else None
        hard = hard | (self.teleporter_tiles(facet) - {goal_tile}) | self.walled
        fields = self.fields(st)
        if not through_fields:
            hard |= {(it["x"], it["y"]) for it in fields.values()}
        diagonal_first = lambda: self.human.choice((True, False))  # noqa: E731
        walk = self.walk_map(st, skip=fields if through_fields else ())
        if walk is not None:
            for edge in [e for e, t in self.denied_t.items() if now - t > DENY_RETRY_S]:
                self.denied.discard(edge)
                del self.denied_t[edge]
            start = (pos[0], pos[1], pos[2])
            path = pathfind.plan(walk, start, goal,
                                 blocked_moves=self.denied, occupied=hard, cost_scale=cost, max_steps=max_steps,
                                 tick=lambda: self.guard(self.link.state()))
            if path is None and self.denied_t:
                # live 2026-10-17 11:12:45: one step south refused at a one-tile passage (a player stepped onto it),
                # kept as a wall, and no route anywhere for minutes; the map says those steps go: try them again
                log(f"no route from {cur} past {len(self.denied_t)} step(s) the server refused on walkable "
                    f"tiles; trying them again")
                self.denied.difference_update(self.denied_t)
                self.denied_t.clear()
                path = pathfind.plan(walk, start, goal,
                                     blocked_moves=self.denied, occupied=hard, cost_scale=cost, max_steps=max_steps,
                                     tick=lambda: self.guard(self.link.state()))
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

    def walk_to(self, center_fn, radius: int, label: str, max_moves: int | None = None, z_ok=None, gate=None,
                goal_fn=None, urgent: bool = False, stop=None, max_route: int | None = None,
                mobile_wait: float | None = None):
        """Walk until within `radius` (Chebyshev) of center_fn(), re-evaluated
        on every replan (NPCs wander). `z_ok(z)` also requires the standing
        height (same level as the target, not a cave below or a floor above);
        only the map planner can honour it. `gate`: the (x, y) whose moongate this
        walk means to use (a gate there or not: the caller may not see it yet); its
        gump is left for the caller. Every other moongate the route steps onto gets
        its gump closed (close_gate_gumps).
        `goal_fn`: a goal predicate (e.g. nav.any_of) used instead of
        center_fn/radius; center_fn may then be None. `urgent`: a flight, with no
        pauses, sidesteps or reading waits, running whenever stamina allows.
        `stop(state)`: checked before planning and before every step; a truthy
        answer ends the walk there (a patrol meeting something to do), and is
        returned. `max_moves`: abort after this many steps; None (the default,
        user decision 2026-10-03) walks as far as the route goes; the replan
        cap still ends walks that make no progress. `max_route`: a map-planned route longer than
        this many steps raises Abort("... detour ...") before a step of it is taken (a walk-memory
        route keeps to tiles walked before, so its length says nothing about a detour). None when
        the walk arrived. A danger replan favours the route it leaves (sticky, STICKY_X); a walk
        that keeps replanning round creatures without getting nearer is boxed in (Abort). A door
        the server refuses (DOOR_REFUSED_CLILOC, or still denied after a second open request) is a
        wall until the walk ends (walled); spell fields are walls, dispelled when they cut the
        route and there's no way round (Mover `dispel`). `mobile_wait`: how long a route cut only by
        mobiles we can't shove waits for them to move (after a stamina potion when short; None:
        MOBILE_WAIT_S), then Abort; 0 aborts at once (a fight's step away: standing there waiting is
        the worst of it, live 2026-10-15 22:57:56 and simulator fight1822 seed 5, a retreat 'waiting'
        until dead)."""
        self.sticky = set()
        self.walled = set()
        try:
            return self._walk_to(center_fn, radius, label, max_moves, z_ok, gate, goal_fn, urgent, stop, max_route,
                                 MOBILE_WAIT_S if mobile_wait is None else mobile_wait)
        finally:
            self.sticky = set()
            self.walled = set()

    def _clear_fields(self, st, goal, max_route, cur, label: str, dispelled: set) -> bool:
        """No route (within max_route): if one runs through spell fields, dispel the first field on
        it not tried in this walk_to (Mover `dispel`), then look for it to go (FIELD_GONE_WAIT_S).
        True when a dispel went out (replan); False: fields aren't what cuts the route, none is left
        to try, or the cast didn't go out."""
        if self.dispel is None:
            return False
        fields = self.fields(st)
        if not fields:
            return False
        path, _ = self.plan(st, goal, mobiles=False, max_steps=max_route, through_fields=True)
        if path is None:
            return False
        todo = [(k, it) for k, it in self.fields_on(path, fields) if serial_of(k) not in dispelled]
        if not todo:
            return False
        key, it = todo[0]
        serial = serial_of(key)
        dispelled.add(serial)
        log(f"{label}: route from {cur} cut by a field at ({it['x']}, {it['y']}) (0x{serial:08X}, graphic "
            f"0x{it['graphic']:04X}); {len(path) - 1} steps through it; dispelling it")
        if not self.dispel(serial, it):
            log(f"{label}: the dispel didn't go out")
            return False
        gone = self.link.wait(lambda s: key not in s["world"]["items"], timeout=FIELD_GONE_WAIT_S, poll=0.05)
        log(f"{label}: the field at ({it['x']}, {it['y']}) {'is gone' if gone is not None else 'is still there'}; "
            f"replanning")
        return True

    def _walk_to(self, center_fn, radius, label, max_moves, z_ok, gate, goal_fn, urgent, stop, max_route, mobile_wait):
        gate = tuple(gate) if gate is not None else None
        replans = 0
        best_route, stalled = None, 0  # danger replans since the planned route last got shorter (boxed in)
        committed_until = 0            # self.steps up to which a danger replan's route is kept (DANGER_COMMIT_STEPS)
        start_steps = self.steps
        opened = {}                  # door tile -> event mark of its open request in this walk (_open_ahead)
        dispelled = set()            # field serials a dispel was tried on in this walk (_clear_fields)
        run = True                   # the client's Always Run is on: a player never walks a route
        mobile_wait_until = None
        while True:
            st = self.link.state()
            self.guard(st)
            self.replan_requested = False        # this plan sees whatever the guard just set
            if stop is not None and (why := stop(st)):
                return why
            cur = tuple(self.link.pos(st)[:2])
            goal = goal_fn if goal_fn is not None else nav.within(tuple(center_fn()), radius, z_ok)
            if goal(cur) and (z_ok is None or self.walk_map(st) is None or z_ok(self.link.pos(st)[2])):
                log(f"{label}: arrived at {cur}")
                return
            path, walk = self.plan(st, goal, max_steps=max_route)
            if path is None:
                if self.plan(st, goal, mobiles=False, max_steps=max_route)[0] is None:
                    if self._clear_fields(st, goal, max_route, cur, label, dispelled):
                        continue
                    if max_route is not None and walk is not None:
                        raise Abort(f"{label}: no route within {max_route} steps from {cur} (a longer detour, "
                                    f"if any)")
                    raise Abort(f"{label}: no route from {cur}")
                now = time.monotonic()
                if self._refreshed(label):
                    continue                 # full stamina: the denied tiles are tried again
                if mobile_wait_until is None:
                    if mobile_wait <= 0:
                        raise Abort(f"{label}: route from {cur} cut by mobiles we couldn't shove")
                    mobile_wait_until = now + mobile_wait
                    log(f"{label}: route from {cur} cut by mobiles we couldn't shove; waiting")
                elif now > mobile_wait_until:
                    raise Abort(f"{label}: route from {cur} cut by mobiles for {mobile_wait:.0f} s")
                if urgent:
                    time.sleep(0.05)         # a poll interval, not a player's pause
                else:
                    self.human.wait("between")
                continue
            mobile_wait_until = None
            if max_route is not None and walk is not None and len(path) - 1 > max_route:
                raise Abort(f"{label}: only a {len(path) - 1}-step detour from {cur} (more than {max_route})")
            if best_route is None or len(path) < best_route:
                best_route, stalled = len(path), 0
            log(f"{label}: route {len(path) - 1} steps from {cur}{'' if run else ' (walking)'}"
                f"{'' if walk is not None else ' [walk memory]'}")
            replan = False
            danger_replan = False
            zone_moved = False            # a guard reported a new or moved zone since this plan
            for i, nxt in enumerate(path[1:], start=1):
                st = self.fresh_state()
                if stop is not None and (why := stop(st)):
                    return why
                pos = self.link.pos(st)
                cur, z = (pos[0], pos[1]), pos[2]
                if cheb(cur, nxt) != 1:
                    # moved off the plan by something other than this walk (live 20261015_171928
                    # 18:57:40: the user's own client steps mid-walk); plan again from here
                    log(f"{label}: at {cur}, off the planned route (next {nxt}); replanning")
                    replan = True
                    break
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
                if nxt in {(it["x"], it["y"]) for it in self.fields(st).values()}:
                    # a field cast on the route after the plan: it goes away, so no lasting deny
                    self.refused_steps += 1
                    log(f"{label}: a field lies on {nxt} now; replanning without stepping into it")
                    replan = True
                    break
                if nxt in opened and self.door_refused(opened[nxt]):
                    self._wall_door(nxt, label, "refused to open (cliloc 1061637)")
                    replan = True
                    break
                if pos[3] == d:              # already facing it (e.g. at the start of a route)
                    self._open_ahead(nxt, z, opened, label, st)
                if urgent:
                    run = stamina_ok(st)
                self.pace(run, st)
                outcome = self.step(d, run, st)
                if outcome == "turned":      # facing it now: the client's auto-open fires on the turn
                    self._open_ahead(nxt, z, opened, label)
                    self.pace(run, st)
                    outcome = self.step(d, run)
                walled = False               # the step was denied by a door we may not use
                if outcome == "blocked" and self.doors and self.door_at(nxt):
                    if nxt in opened and self.door_refused(opened[nxt]):
                        self._wall_door(nxt, label, "refused to open (cliloc 1061637)")
                        walled = True
                    else:
                        mark = len(self.link.events)
                        if self._try_door(cur, d, run, urgent):
                            outcome = "moved"
                        else:
                            self._wall_door(nxt, label, "refused to open (cliloc 1061637)" if self.door_refused(mark)
                                            else "still denies the step after opening it")
                            walled = True
                if outcome == "teleported":
                    aimed = goal_fn is None and radius == 0 and center_fn is not None \
                        and tuple(center_fn()[:2]) == tuple(nxt)
                    if not aimed:                       # the teleporter was the goal (a walk-in): no undo
                        self.step_back(cur, st["world"]["self"].get("map"), label)
                    replan = True
                    break
                if outcome == "moved":
                    after = self.fresh_state()
                    here = self.link.pos(after)
                    new = tuple(here[:2])
                    self.mem.add_step(cur, new)
                    if new != gate and self.moongate_at(new, after, z=here[2]):
                        self.close_gate_gumps(self.step_mark, new, label)
                    if self.replan_requested:
                        zone_moved, self.replan_requested = True, False
                    ahead = DANGER_NEXT if self.steps < committed_until else DANGER_LOOKAHEAD
                    if zone_moved and new == nxt and any(self.in_danger(t) for t in path[i + 1:i + 1 + ahead]):
                        self.replan_requested = True      # a zone that moved since the plan is close ahead
                    if new != nxt or self.replan_requested:
                        if self.replan_requested:
                            log(f"{label}: danger ahead changed; replanning from {new}")
                            danger_replan = True      # not a failure: don't count it as one
                            self.sticky = set(path[i:])
                            self.sticky.add(new)
                            committed_until = self.steps + DANGER_COMMIT_STEPS
                        else:
                            log(f"{label}: landed on {new}, expected {nxt}; replanning")
                        self.replan_requested = False
                        replan = True
                        break
                    if max_moves is not None and self.steps - start_steps > max_moves:
                        raise Abort(f"{label}: exceeded {max_moves} moves")
                    if i + 1 < len(path) and nav.direction(new, path[i + 1]) == d:
                        # landed facing the next tile: the client's auto-open fires on the step
                        self._open_ahead(path[i + 1], here[2], opened, label, after)
                    if urgent or self.danger or (self.no_idle is not None and self.no_idle(after)):
                        # no idling or sidesteps with a creature's zone set (live 2026-10-05, witcher_23: an
                        # 8.9 s walk pause with an air dragon closing in 10 tiles off, then its breath), or when
                        # the host says so (live 2026-10-17 19:04:08-15: ~7 s still 2 tiles from the leave gate
                        # while an apparition in war mode came 6 tiles onto us; dead at 19:04:18.7)
                        continue
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
                    self._refreshed(label)
                elif not walled:
                    if walk is not None:
                        edge = (cur[0], cur[1], d)               # z-aware plans; don't pollute 2D memory
                        self.denied.add(edge)
                        self.denied_t[edge] = time.monotonic()   # the map has it walkable: a mobile, likely (DENY_RETRY_S)
                    else:
                        self.mem.add_blocked(cur, d)
                    log(f"{label}: move {d} from {cur} blocked ({self.blocked_count} total); replanning")
                if self.blocked_count > self.max_blocked:
                    raise Abort(f"too many blocked moves ({self.blocked_count})")
                if not urgent:
                    self.human.wait("read")
                replan = True
                break
            if replan and not danger_replan:
                replans += 1
            if danger_replan:
                stalled += 1
                if stalled > DANGER_STALL_REPLANS:
                    raise Abort(f"{label}: boxed in by creatures ({stalled} replans round them without getting "
                                f"nearer than {best_route - 1} steps)")
            if replans > 30:
                raise Abort(f"{label}: too many replans")

    def _refreshed(self, label) -> bool:
        """Short of stamina to shove (live 22:57:56: a retreat denied at 15/25, 'cut by mobiles … waiting',
        dead a second later; user: "We carry stamina potions so if we get stamina limited and need to shove
        we can drink a pot"): the host's `refresh` (a stamina potion) when stamina is below max. True when
        it drank: the tiles a shove was denied into are tried again."""
        me = self.link.state()["world"]["self"]
        stam, top = me.get("stam"), me.get("stam_max")
        if self.refresh is None or stam is None or not top or stam >= top or not self.refresh():
            return False
        self.shove_denied.clear()
        log(f"{label}: drank for stamina to shove ({stam}/{top} -> "
            f"{self.link.state()['world']['self'].get('stam')})")
        return True

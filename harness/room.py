"""The rental room menu as one deterministic flow each way: in through a house
steward or innkeeper, out through the room's door (docs/NOTES.md "Rental room via
the DTF house steward"). Shared by `ctl act room` and the lumber runner.

Live 2026-10-04 (Outland Dan, DTF guild house): the keeper's context menu entry
"Room" (steward) / "Rent" (innkeeper) opens gump 0x8EAEFBDB, and so does saying
"room" by him, which is what `enter` does: the injected right-click left the menu
open on the client's screen (user, 2026-10-06). On it "Enter Your Room"
when the character rents one, else "Visit Other Rooms" and a row per room he may
visit ("Logan Wolf (DTF)", button 100). Arrival: "You enter the rental room.",
facet 3. The door's menu offers "Exit to House Steward" / "Exit to Town"
("You exit the rental room.", back on the keeper's facet). Buttons are found by
their labels, never fixed ids; ROOM_REFUSED (End Rental Contract, Expand) are
never pressed.

IO as escape.py / tracking.py: `send(pkt)` (raises on a proxy refusal) and
`poll()` -> (full state, world events since the last poll). `human` is a
humanize.Human: a reading pause (`wait`) before every click.
"""

from __future__ import annotations

import time

import actions
from agent_link import cheb
from uo import cliloc as cliloc_mod
from uo.gumps import controls as gump_controls

ROOM_GUMP_ID = 0x8EAEFBDB            # rental room menu (innkeeper, house steward, the room's door)
# Its buttons that change a rental contract (live 2026-10-04 in Logan Wolf's room, which Outland Dan
# co-owns): never pressed here, and `ctl act gump` refuses them when the menu shows that label.
ROOM_REFUSED = {3: "End Rental Contract", 7: "Expand"}
ROOM_FACET = 3                       # rental rooms are on facet 3 (no map geometry)
ROOM_KEEPERS = ("house steward", "innkeeper")   # who opens the room menu (their click label)
ROOM_WORD = "room"                   # said within KEEPER_RANGE of the keeper: opens the room menu (user, 2026-10-06)
KEEPER_RANGE = 2                     # walk this close first (the steward's menu, live; innkeepers answer from 11)
ENTERED = "You enter the rental room."
EXITED = "You exit the rental room."
EXITS = {"steward": "Exit to House Steward", "town": "Exit to Town"}
HEARD_EVS = ("speech_heard", "cliloc", "gump_open", "gump_response", "popup", "buy_list",
             "menu", "quest_arrow", "quest_arrow_set", "target", "map_change")
EVENT_WAIT_S = 3.0

_CLILOC = None


class RoomError(Exception):
    """The room flow couldn't go on (the message says why and what was offered)."""


def _serial(v) -> int:
    return int(v, 16) if isinstance(v, str) else int(v)


def _cliloc(number) -> str:
    """A cliloc as the client renders it (install-dir Cliloc.enu, read-only)."""
    global _CLILOC
    if _CLILOC is None:
        try:
            _CLILOC = cliloc_mod.load()
        except OSError:
            _CLILOC = {}
    return cliloc_mod.translate(_CLILOC, int(number))


def _tile_name(graphic: int) -> str:
    import uomap
    return uomap.tiledata().item(graphic).name or ""


def labelled_button(g: dict, label: str) -> int | None:
    """The reply button of gump event/row `g` whose own label (the text right of it on its
    row) starts with `label`, case-blind ("Visit Other Rooms", "Exit to House Steward")."""
    want = label.lower()
    for b in gump_controls(g.get("layout") or "", g.get("lines") or [], _cliloc)["buttons"]:
        for n in b.get("near") or []:
            if 0 < n.get("dx", 0) <= 60 and abs(n.get("dy", 0)) <= 8 and n["text"].strip().lower().startswith(want):
                return b["id"]
    return None


def room_rows(g: dict) -> list[tuple[int, str]]:
    """The visit list's rows: (button >= 100, the first text right of it)."""
    return [(b["id"], n["text"]) for b in gump_controls(g.get("layout") or "", g.get("lines") or [], _cliloc)["buttons"]
            if b["id"] >= 100 for n in (b.get("near") or [])[:1] if n.get("dx", 0) > 0]


def keeper_kind(label: str) -> str | None:
    """"house steward" or "innkeeper" from a click label."""
    return next((k for k in ROOM_KEEPERS if k in (label or "").lower()), None)


def _keepers(state: dict) -> list[tuple[int, str, str]]:
    world = state["world"]
    pos = state["movement"].get("pos")
    labels = world.get("labels") or {}
    return sorted((cheb((m["x"], m["y"]), tuple(pos[:2])), key, labels.get(key) or "")
                  for key, m in (world.get("mobiles") or {}).items()
                  if m.get("x") is not None and keeper_kind(labels.get(key)))


def find_keeper(state: dict) -> tuple[int, str] | None:
    """The nearest mobile whose click label (world.labels) names a house steward or innkeeper."""
    keepers = _keepers(state)
    return (_serial(keepers[0][1]), keepers[0][2]) if keepers else None


def _texts(g: dict) -> list[str]:
    return [t for t in g.get("lines") or [] if t]


class _Flow:
    def __init__(self, io, human, timeout: float):
        self.io, self.human, self.timeout, self.heard = io, human, timeout, []

    def state(self) -> dict:
        st, _ = self.io.poll()
        return st

    def wait(self, done, timeout: float, keep_gumps: bool) -> list:
        """World events until done(event) or the timeout; the seen ones go to `heard`."""
        end, got = time.monotonic() + timeout, []
        while True:
            _, evs = self.io.poll()
            got += evs
            if any(done(e) for e in evs) or time.monotonic() > end:
                self.heard += [e for e in got if e.get("ev") in HEARD_EVS
                               and (keep_gumps or e.get("ev") != "gump_open")]
                return got
            time.sleep(0.05)

    def send_wait(self, pkts, done, what: str, timeout: float = EVENT_WAIT_S, keep_gumps=False) -> list:
        self.io.poll()                     # forget what came before the click
        for p in pkts:
            try:
                self.io.send(p)
            except Exception as e:
                raise RoomError(f"{what}: {e}")
        return self.wait(done, timeout, keep_gumps)

    def press(self, g: dict, button: int, done, what: str, timeout: float = EVENT_WAIT_S, keep_gumps=False):
        refused = ROOM_REFUSED.get(button)
        if refused and any(t.strip().lower().startswith(refused.lower()) for t in _texts(g)):
            raise RoomError(f"refusing the rental room button {button} ({refused})")
        self.human.wait("menu")
        return self.send_wait([actions.gump_reply(_serial(g["serial"]), _serial(g["gump_id"]), button,
                                                  g.get("layout") or "", g.get("lines") or [])],
                              done, what, timeout, keep_gumps)

    def close(self, g: dict):
        self.press(g, 0, lambda e: False, "closing the room menu", timeout=0.0)

    def room_gump(self, got: list, what: str) -> dict:
        g = next((e for e in reversed(got) if _is_room_gump(e)), None)
        if g is None:
            raise RoomError(f"no rental room menu after {what}")
        return g

    def moved(self, got: list, done, text: str) -> dict:
        st = self.state()
        ok = any(done(e) for e in got)
        return {"ok": ok, "pos": st["movement"].get("pos"), "facet": (st["world"].get("self") or {}).get("map"),
                "heard": self.heard, **({} if ok else {"error": f"no '{text}' within {self.timeout:g} s"})}


def _is_room_gump(e: dict) -> bool:
    return e.get("ev") == "gump_open" and e.get("gump_id") is not None and _serial(e["gump_id"]) == ROOM_GUMP_ID


def enter(io, human, owner_words=(), *, walk=None, timeout: float = 5.0) -> dict:
    """Into a rental room via the nearest house steward or innkeeper in view: walked to
    (walk(serial, KEEPER_RANGE), the caller's guarded walker) when farther than
    KEEPER_RANGE, then "room" said by him (ROOM_WORD; not his context menu "Room"/"Rent",
    which a right-click the client never made leaves open on its screen: user,
    2026-10-06); on the room menu "Enter Your Room" when one is offered and no owner is
    named, else "Visit Other Rooms" and the row whose name holds every owner word (none:
    the only row). Done on "You enter the rental room." or facet 3. Returns {ok, room,
    via, pos, facet, heard[, error | already]}."""
    flow = _Flow(io, human, timeout)
    st = flow.state()
    facet = (st["world"].get("self") or {}).get("map")
    if facet == ROOM_FACET:
        return {"ok": True, "already": "inside a rental room", "room": None, "via": None,
                "pos": st["movement"].get("pos"), "facet": facet, "heard": []}
    keepers = _keepers(st)
    if not keepers:
        raise RoomError("no house steward or innkeeper in view (by their click label): go to one first "
                        "(`ctl npcs steward`, `ctl npcs innkeeper`)")
    dist, key, label = keepers[0]
    keeper = _serial(key)
    if dist > KEEPER_RANGE:
        if walk is None:
            raise RoomError(f"{label} is farther than {KEEPER_RANGE} tiles and no walker was given")
        walk(keeper, KEEPER_RANGE)
    human.wait("speak")
    got = flow.send_wait([actions.say_unicode(ROOM_WORD)], _is_room_gump, f"saying '{ROOM_WORD}' by {label}")
    g = flow.room_gump(got, f"saying '{ROOM_WORD}' by {label}")
    owner = " ".join(owner_words).strip()
    arrived = (lambda e: e.get("ev") == "map_change" and e.get("map") == ROOM_FACET
               or e.get("ev") == "speech_heard" and e.get("text") == ENTERED)
    own = labelled_button(g, "Enter Your Room")
    if own is not None and not owner:
        got = flow.press(g, own, arrived, "'Enter Your Room'", timeout, keep_gumps=True)
        room = "your own"
    else:
        visit = labelled_button(g, "Visit Other Rooms")
        if visit is None:
            flow.close(g)
            raise RoomError(f"the room menu offers no 'Visit Other Rooms': {_texts(g)}")
        g = flow.room_gump(flow.press(g, visit, _is_room_gump, "'Visit Other Rooms'"), "'Visit Other Rooms'")
        rows = room_rows(g)
        words = [w.lower() for w in owner.split()]
        hit = [r for r in rows if all(w in r[1].lower() for w in words)]
        if len(hit) != 1:
            flow.close(g)
            raise RoomError(f"{'no' if not hit else len(hit)} room(s) match {owner or '(no owner given)'}; "
                            f"rooms you may visit: {[r[1] for r in rows]}")
        got = flow.press(g, hit[0][0], arrived, f"'{hit[0][1]}'", timeout, keep_gumps=True)
        room = hit[0][1]
    return {**flow.moved(got, arrived, ENTERED), "room": room, "via": label}


def leave(io, human, exit="steward", *, timeout: float = 5.0) -> dict:
    """Out of the rental room: the nearest door in view (tiledata name holds "door") is
    double-clicked and its menu's exit pressed: `exit` "steward" (Exit to House Steward)
    or "town" (Exit to Town), or a tuple of them tried in order (the first one the menu
    offers; None = ("steward", "town")). Done on "You exit the rental room." or a facet
    other than 3. Returns {ok, exit ("the house steward" | "town"), pos, facet, heard[, error]}."""
    prefs = ("steward", "town") if exit is None else (exit,) if isinstance(exit, str) else tuple(exit)
    if not prefs or any(p not in EXITS for p in prefs):
        raise RoomError(f"unknown exit {exit!r}: steward or town")
    flow = _Flow(io, human, timeout)
    st = flow.state()
    facet = (st["world"].get("self") or {}).get("map")
    if facet != ROOM_FACET:
        raise RoomError(f"not in a rental room (facet {facet})")
    pos = st["movement"].get("pos")
    doors = sorted((cheb((it["x"], it["y"]), tuple(pos[:2])), key)
                   for key, it in (st["world"].get("items") or {}).items()
                   if it.get("container") is None and it.get("x") is not None and it.get("graphic") is not None
                   and "door" in _tile_name(it["graphic"]).lower())
    if not doors:
        raise RoomError("no door in view in this room")
    human.wait("use")
    got = flow.send_wait([actions.dclick(_serial(doors[0][1]))], _is_room_gump, "double-clicking the door")
    g = flow.room_gump(got, "double-clicking the door")
    buttons = {k: labelled_button(g, label) for k, label in EXITS.items()}
    pick = next((k for k in prefs if buttons[k] is not None), None)
    if pick is None:
        flow.close(g)
        raise RoomError(f"the door's menu offers no {exit if isinstance(exit, str) else 'exit'} button: {_texts(g)}")
    left = (lambda e: e.get("ev") == "map_change" and e.get("map") != ROOM_FACET
            or e.get("ev") == "speech_heard" and e.get("text") == EXITED)
    got = flow.press(g, buttons[pick], left, f"'{EXITS[pick]}'", timeout, keep_gumps=True)
    return {**flow.moved(got, left, EXITED), "exit": "the house steward" if pick == "steward" else "town"}

"""The home drop-off as one deterministic flow: the user's Razor script "New Drop Off" (F5 in
the profile "Logan Wolf Tribard Tamer"; ClassicUO/Data/Plugins/Assistant/Scripts/New Drop Off.razor),
plus what it leaves in the pack (user 2026-10-15: research materials and resource maps, into the home
chest "for now, we'll buy tomes later").

The script is a list of `menu <serial> <index>` lines: each opens that item's context menu (0xBF sub
0x13) and picks an entry (0xBF sub 0x15); the resource stockpile's "Restock" brings a target cursor
answered with the backpack. Live (capture 20261015_210709, 21:29:49-22:45:44, five runs) every step
answered with one system line: "No undecoded …" / "You deposit …" / "N recycled …" / "You add …". The
steps and their entries come from the character's home (harness/data/homes.json `dropoff`); an entry is
picked by its cliloc, not its index, so a menu that changes order can't press the wrong one.

Pace (user 2026-10-17: "My Razor dropoff script finishes like instantly"): Razor sent all eight requests and
picks in 0.12 s (21:29:49.407-.526, 3-4 ms apart, the backpack 94 ms after the stockpile's pick). Here each pick
waits only for its menu (one round trip: the entry is chosen by cliloc) and the stockpile's cursor, nothing
else; a step's line is the system text heard before the next step's menu (the server answers in order).

Then every item in the backpack's top level (where loot lands; never the user's own bags or anything
worn) that `to_chest` names goes into the home chest: lift, a drag pause, drop (the stock drag).

IO-agnostic like shelf.py and stockpile.py: `io` has send(pkt) and poll() -> (state, new world
events) (escape.LinkIO for runners, ctl._CtlIO for ctl)."""

from __future__ import annotations

import re
import time

import actions
import combat

EVENT_WAIT_S = 3.0
LINE_WAIT_S = 1.5             # after the last pick: its line (the server's lines come within ~0.1 s live)
RANGE = 2                     # every drop-off item stands within 2 tiles of the home landing tile


class DropoffError(Exception):
    """The drop-off couldn't go on (the message says why)."""


def _serial(v) -> int:
    return int(v, 16) if isinstance(v, str) else int(v)


def _lines(evs: list) -> list[str]:
    return [(e.get("text") or "").strip() for e in evs if e.get("ev") == "speech_heard"
            and _serial(e.get("serial") or 0xFFFFFFFF) == 0xFFFFFFFF]


class _Flow:
    def __init__(self, io):
        self.io, self.heard = io, []

    def wait(self, done, timeout: float, seen: list = ()) -> list:
        """The events until one is `done` or the timeout; a world save doesn't use up the timeout (agent_link.Deadline:
        live 2026-10-29 16:57:47, hunt-20261029-160655-f26a, the shelf's open went out during "The world is saving,
        please wait." and the 3 s wait gave up before the server answered: the run aborted with 41k gp carried)."""
        from agent_link import Deadline
        deadline, got = Deadline(timeout, getattr(self.io, "save_t0", None)), []
        deadline.see(seen)            # a save's start line heard just before the send
        while True:
            _, evs = self.io.poll()
            got += evs
            deadline.see(evs)
            if any(done(e) for e in evs) or deadline.over():
                self.heard += [e for e in got if e.get("ev") in ("speech_heard", "cliloc")]
                return got
            time.sleep(0.05)

    def send_wait(self, pkt: bytes, done, what: str, timeout: float) -> list:
        _, pre = self.io.poll()       # what came since the last look (a previous pick's line) is kept
        try:
            self.io.send(pkt)
        except Exception as e:
            raise DropoffError(f"{what}: {e}")
        self.heard += [e for e in pre if e.get("ev") in ("speech_heard", "cliloc")]
        return pre + self.wait(done, timeout, pre)


def chest_items(state: dict, rules: dict) -> list[tuple[int, dict]]:
    """(serial, item) in the backpack's top level that `rules` sends to the chest: a graphic of
    rules["graphics"], or a name holding one of rules["names"] as words (case-blind). Worn items and
    whatever lies in a bag inside the pack are never moved."""
    items = state["world"]["items"]
    pack = combat.backpack(items, state["movement"].get("self_serial"))
    if pack is None:
        return []
    graphics = {_serial(g) for g in rules.get("graphics") or []}
    names = [re.compile(rf"\b{re.escape(n.lower())}s?\b") for n in rules.get("names") or []]
    out = []
    for k, it in items.items():
        if it.get("container") is None or _serial(it["container"]) != pack or it.get("layer"):
            continue
        name = (it.get("name") or "").lower()
        if _serial(it.get("graphic") or 0) in graphics or any(n.search(name) for n in names):
            out.append((_serial(k), it))
    return out


def ground_item(items: dict, serial: int) -> dict | None:
    """The item on the ground that holds `serial` (itself when it lies there; else the outermost container it
    sits in, e.g. the wooden shelf the user put the tomes on, 2026-10-21): the one whose tile counts for reach.
    None when unknown or carried by a mobile."""
    it, seen = items.get(f"0x{serial:08X}"), set()
    while it is not None and it.get("container") is not None:
        parent = _serial(it["container"])
        if parent in seen:
            return None
        seen.add(parent)
        it = items.get(f"0x{parent:08X}")
    return it if it is not None and it.get("x") is not None else None


def run(io, human, plan: dict, *, timeout: float = EVENT_WAIT_S) -> dict:
    """The drop-off `plan` (homes.json `dropoff`: steps [{serial, name, entry (cliloc), target?:
    "pack", in?: container serial}], chest {serial}, to_chest {graphics, names}) from where we stand. Each step: its
    context menu, the entry with that cliloc (refused when missing or disabled), the backpack targeted when
    the step says so, then its line. Then the chest items (chest_items). A step's item may sit in a container
    on the ground (the user's wooden shelf holds the tomes since 2026-10-21): its reach is the container's tile,
    and a container the server hasn't opened for us yet is opened first, outermost first, like a player
    (agent_link.containers_to_open). The client forgets a container's contents when it leaves view (0x1D): back
    from a trip, the tomes in the shelf are unknown until the shelf is opened again (live 2026-10-21 22:32:36, the
    hunt's drop-off after the recall home: "a treasure map tome … isn't in view within 2 tiles"). So a step whose
    item is unknown is reached through the container its `in` names: that one opened, then its item looked for.
    A step whose item (or `in` container) isn't in view within RANGE stops the drop-off before anything is sent.
    Returns {ok, steps [{serial, name, lines}], chest [{serial, name, amount}], left [serials not moved], error?,
    heard}."""
    from agent_link import Deadline, containers_to_open
    st, _ = io.poll()
    items, pos = st["world"]["items"], st["movement"].get("pos")
    shut, unseen = [], []
    for s in plan["steps"]:
        serial = _serial(s["serial"])
        it = ground_item(items, serial)
        box = _serial(s["in"]) if it is None and s.get("in") else None
        if box is not None:
            it = ground_item(items, box)
        if it is None or not pos or max(abs(it["x"] - pos[0]), abs(it["y"] - pos[1])) > RANGE:
            raise DropoffError(f"{s['name']} 0x{serial:08X} isn't in view within {RANGE} tiles: "
                               "stand on the home landing tile")
        if box is None:
            shut += [c for c in containers_to_open(st["world"], serial) if c not in shut]
        else:
            shut += [c for c in containers_to_open(st["world"], box, itself=True) if c not in shut]
            unseen.append(s)
    pack = combat.backpack(items, st["movement"].get("self_serial"))
    flow, done, error = _Flow(io), [], None
    for c in shut:                    # a player opens the shelf before using what's in it
        def opened(e, c=c):
            return e.get("ev") == "container_open" and _serial(e.get("serial")) == c
        human.wait("use")
        got = flow.send_wait(actions.dclick(c), opened, f"opening the container 0x{c:08X}", timeout)
        if not any(opened(e) for e in got):
            raise DropoffError(f"the container 0x{c:08X} holding drop-off items didn't open: {_lines(got)}")
    deadline = Deadline(timeout, getattr(io, "save_t0", None))
    while unseen:                     # its contents come right after the open (0x3C)
        st, evs = io.poll()
        deadline.see(evs)
        unseen = [s for s in unseen if f"0x{_serial(s['serial']):08X}" not in st["world"]["items"]]
        if unseen and deadline.over():
            raise DropoffError(f"{unseen[0]['name']} 0x{_serial(unseen[0]['serial']):08X} isn't in the container "
                               f"0x{_serial(unseen[0]['in']):08X}")
        if unseen:
            time.sleep(0.05)
    human.wait("hotkey")              # the script's hotkey (F5); its lines then run back to back
    for s in plan["steps"]:
        serial = _serial(s["serial"])
        got = flow.send_wait(actions.request_popup(serial),
                             lambda e: e.get("ev") == "popup" and _serial(e.get("serial")) == serial,
                             f"opening the menu of {s['name']}", timeout)
        if done:                      # the previous pick's line came before this menu
            done[-1]["lines"] += _lines(got)
        menu = next((e for e in reversed(got) if e.get("ev") == "popup" and _serial(e.get("serial")) == serial), None)
        entry = next((x for x in (menu or {}).get("entries") or [] if x.get("cliloc") == s["entry"]), None)
        if entry is None or (entry.get("flags") or 0) & 0x01:
            error = (f"{s['name']}: no menu" if menu is None else
                     f"{s['name']}: entry {s['entry']} {'disabled' if entry else 'not offered'}")
            break
        if s.get("target") == "pack":
            got = flow.send_wait(actions.popup_selection(serial, entry["index"]),
                                 lambda e: e.get("ev") == "target" and e.get("active"), f"{s['name']}: the pick", timeout)
            cur = next((e for e in reversed(got) if e.get("ev") == "target" and e.get("active")), None)
            if cur is None or pack is None:
                error = f"{s['name']}: no target cursor after the pick: {_lines(got)}"
                break
            got += flow.send_wait(actions.target_object(cur["cursor_id"], pack, 0, 0, 0, 0x0E75,
                                                        cur.get("cursor_type") or 0),
                                  lambda e: False, f"{s['name']}: targeting the backpack", 0.0)
        else:
            got = flow.send_wait(actions.popup_selection(serial, entry["index"]),
                                 lambda e: False, f"{s['name']}: the pick", 0.0)
        done.append({"serial": f"0x{serial:08X}", "name": s["name"], "lines": _lines(got)})
    if done and error is None and not done[-1]["lines"]:     # the last pick's line, unless it came already
        done[-1]["lines"] += _lines(flow.wait(lambda e: bool(_lines([e])), LINE_WAIT_S))
    moved, left = [], []
    if error is None and plan.get("chest") and plan.get("to_chest"):
        chest = _serial(plan["chest"]["serial"])
        st, _ = io.poll()
        todo = chest_items(st, plan["to_chest"])
        if todo and f"0x{chest:08X}" not in (st["world"].get("containers") or {}):
            human.wait("use")              # a player opens the chest before dragging into it
            flow.send_wait(actions.dclick(chest), lambda e: e.get("ev") == "container_open"
                           and _serial(e.get("serial")) == chest, "opening the chest", timeout)
        for serial, it in todo:
            human.wait("use")
            flow.send_wait(actions.lift(serial, it.get("amount") or 1), lambda e: False, "lifting", 0.0)
            human.wait("drag")
            flow.send_wait(actions.drop(serial, combat.DROP_AUTO, combat.DROP_AUTO, 0, 0, chest),
                           lambda e: False, "dropping into the chest", 0.0)
            deadline = Deadline(timeout, getattr(io, "save_t0", None))
            while not deadline.over():
                st, evs = io.poll()
                deadline.see(evs)
                now = st["world"]["items"].get(f"0x{serial:08X}")
                if now is None or _serial(now.get("container") or 0) == chest:
                    break
                time.sleep(0.1)
            if now is None or _serial(now.get("container") or 0) == chest:   # gone: merged into a stack there
                moved.append({"serial": f"0x{serial:08X}", "name": it.get("name"), "amount": it.get("amount") or 1})
            else:
                left.append(f"0x{serial:08X}")
        if left:
            error = f"not moved into the chest: {', '.join(left)}"
    return {"ok": error is None, "steps": done, "chest": moved, "left": left, "heard": flow.heard,
            **({"error": error} if error else {})}

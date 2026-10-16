"""The rental-room drop-off as one deterministic flow: the user's Razor script "New Drop Off" (F5 in
the profile "Logan Wolf Tribard Tamer"; ClassicUO/Data/Plugins/Assistant/Scripts/New Drop Off.razor),
plus what it leaves in the pack (user 2026-10-15: research materials and resource maps, into the room
chest "for now, we'll buy tomes later").

The script is a list of `menu <serial> <index>` lines: each opens that item's context menu (0xBF sub
0x13) and picks an entry (0xBF sub 0x15); the resource stockpile's "Restock" brings a target cursor
answered with the backpack. Live (capture 20261015_210709, 21:29:49-22:45:44, five runs) every step
answered with one system line: "No undecoded …" / "You deposit …" / "N recycled …" / "You add …". The
steps and their entries come from the character's home (harness/data/homes.json `dropoff`); an entry is
picked by its cliloc, not its index, so a menu that changes order can't press the wrong one.

Then every item in the backpack's top level (where loot lands; never the user's own bags or anything
worn) that `to_chest` names goes into the room chest: lift, a drag pause, drop (the stock drag).

IO-agnostic like shelf.py and stockpile.py: `io` has send(pkt) and poll() -> (state, new world
events) (escape.LinkIO for runners, ctl._CtlIO for ctl)."""

from __future__ import annotations

import re
import time

import actions
import combat

EVENT_WAIT_S = 3.0
LINE_WAIT_S = 1.5             # after a pick: the server's line comes within ~0.1 s live
RANGE = 2                     # every drop-off item stands within 2 tiles of the room's arrival tile


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

    def wait(self, done, timeout: float) -> list:
        end, got = time.monotonic() + timeout, []
        while True:
            _, evs = self.io.poll()
            got += evs
            if any(done(e) for e in evs) or time.monotonic() > end:
                self.heard += [e for e in got if e.get("ev") in ("speech_heard", "cliloc")]
                return got
            time.sleep(0.05)

    def send_wait(self, pkt: bytes, done, what: str, timeout: float) -> list:
        self.io.poll()
        try:
            self.io.send(pkt)
        except Exception as e:
            raise DropoffError(f"{what}: {e}")
        return self.wait(done, timeout)


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


def run(io, human, plan: dict, *, timeout: float = EVENT_WAIT_S) -> dict:
    """The drop-off `plan` (homes.json `dropoff`: steps [{serial, name, entry (cliloc), target?:
    "pack"}], chest {serial}, to_chest {graphics, names}) from where we stand. Each step: its context
    menu, the entry with that cliloc (refused when missing or disabled), the backpack targeted when
    the step says so, then its line. Then the chest items (chest_items). A step whose item isn't in view
    within RANGE stops the drop-off before anything is sent. Returns {ok, steps [{serial, name, lines}],
    chest [{serial, name, amount}], left [serials not moved], error?, heard}."""
    st, _ = io.poll()
    items, pos = st["world"]["items"], st["movement"].get("pos")
    for s in plan["steps"]:
        it = items.get(f"0x{_serial(s['serial']):08X}")
        if it is None or it.get("x") is None or not pos \
                or max(abs(it["x"] - pos[0]), abs(it["y"] - pos[1])) > RANGE:
            raise DropoffError(f"{s['name']} 0x{_serial(s['serial']):08X} isn't in view within {RANGE} tiles: "
                               "stand on the room's arrival tile")
    pack = combat.backpack(items, st["movement"].get("self_serial"))
    flow, done, error = _Flow(io), [], None
    for s in plan["steps"]:
        serial = _serial(s["serial"])
        human.wait("menu")
        got = flow.send_wait(actions.request_popup(serial),
                             lambda e: e.get("ev") == "popup" and _serial(e.get("serial")) == serial,
                             f"opening the menu of {s['name']}", timeout)
        menu = next((e for e in reversed(got) if e.get("ev") == "popup" and _serial(e.get("serial")) == serial), None)
        entry = next((x for x in (menu or {}).get("entries") or [] if x.get("cliloc") == s["entry"]), None)
        if entry is None or (entry.get("flags") or 0) & 0x01:
            error = (f"{s['name']}: no menu" if menu is None else
                     f"{s['name']}: entry {s['entry']} {'disabled' if entry else 'not offered'}")
            break
        human.wait("menu")
        if s.get("target") == "pack":
            got = flow.send_wait(actions.popup_selection(serial, entry["index"]),
                                 lambda e: e.get("ev") == "target" and e.get("active"), f"{s['name']}: the pick", timeout)
            cur = next((e for e in reversed(got) if e.get("ev") == "target" and e.get("active")), None)
            if cur is None or pack is None:
                error = f"{s['name']}: no target cursor after the pick: {_lines(got)}"
                break
            human.wait("aim")
            got = flow.send_wait(actions.target_object(cur["cursor_id"], pack, 0, 0, 0, 0x0E75,
                                                       cur.get("cursor_type") or 0),
                                 lambda e: bool(_lines([e])), f"{s['name']}: targeting the backpack", LINE_WAIT_S)
        else:
            got = flow.send_wait(actions.popup_selection(serial, entry["index"]),
                                 lambda e: bool(_lines([e])), f"{s['name']}: the pick", LINE_WAIT_S)
        done.append({"serial": f"0x{serial:08X}", "name": s["name"], "lines": _lines(got)})
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
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                st, _ = io.poll()
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

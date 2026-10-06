"""Deposit items into a Resource Stockpile as one deterministic flow (live 2026-10-05, Outland
Dan in Logan Wolf's rental room; docs/NOTES.md "Resource Stockpile"; wiki Resource_Stockpile).

A Resource Stockpile holds crafting materials (boards, ingots, leather, ...) for the house's
owners and co-owners; it is no container. Double-clicking it opens its menu (gump 0x6ECE2ABE);
the "Add Items" button (2, the blue one bottom left, no text label) brings the server's line
"Target an item or container of items you wish to add to this Resource Stockpile. Target
yourself to add all valid items in your backpack." and a target cursor. Targeting a stack
answers "You add 1 item(s) to the Resource Stockpile.", the stack leaves the pack and the menu
comes back with the counts (no cursor). Targeting a pouch adds every stack in it in one go: "You
add 2 item(s) ..." for two board stacks in a live (unsprung) trapped pouch, which stayed armed and
in the pack (user's suggestion, live test 2026-10-05). So one press and one target per pouch
holding boards (`targets`), and per loose stack. Never ourselves or a bag of supplies: that would
add every valid item in it, reagents and tools too (per the player's Settings).

IO-agnostic like shelf.py: `io` has send(pkt) and poll() -> (state, new world events)
(escape.LinkIO for runners, ctl._CtlIO for ctl)."""

from __future__ import annotations

import re
import time

import actions

STOCKPILE_GUMP_ID = 0x6ECE2ABE        # "Resource Stockpile" (live 2026-10-05)
ADD_BUTTON = 2                        # "Add Items" (live: its prompt and cursor follow)
ADD_PROMPT = "Target an item or container of items you wish to add to this Resource Stockpile"
ADDED = re.compile(r"^You add (\d+) item\(s\) to the Resource Stockpile\.")
STOCKPILE_RANGE = 2                   # tiles: opened from 2 in the room (live)
EVENT_WAIT_S = 4.0
POUCH_GRAPHIC = 0x0E79                # pouch (trapped or spent): the logs' and boards' container on a trip


class StockpileError(Exception):
    """The deposit couldn't go on (the message says why)."""


def _serial(v) -> int:
    return int(v, 16) if isinstance(v, str) else int(v)


def is_stockpile_gump(e: dict) -> bool:
    return e.get("ev") == "gump_open" and e.get("gump_id") is not None and _serial(e["gump_id"]) == STOCKPILE_GUMP_ID


def _said(evs: list) -> list[str]:
    return [(e.get("text") or "").strip() for e in evs if e.get("ev") == "speech_heard"]


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
            raise StockpileError(f"{what}: {e}")
        return self.wait(done, timeout)


def _reply(g: dict, button: int) -> bytes:
    return actions.gump_reply(_serial(g["serial"]), _serial(g["gump_id"]), button,
                              g.get("layout") or "", g.get("lines") or [])


def targets(world: dict, stacks: list[int], pack: int | None) -> list[int]:
    """What to target for `stacks` (board serials): the pouch a stack lies in (only boards and
    logs go into it on a trip), once for all of its stacks; a stack anywhere else (the backpack, a
    bag that also holds supplies) on its own. In stack order."""
    items, out = world.get("items") or {}, []
    for s in stacks:
        c = (items.get(f"0x{s:08X}") or {}).get("container")
        c = int(c, 16) if isinstance(c, str) else c
        box = items.get(f"0x{c:08X}") if c is not None else None
        t = c if box is not None and box.get("graphic") == POUCH_GRAPHIC and c != pack else s
        if t not in out:
            out.append(t)
    return out


def deposit(io, human, stockpile: int, stacks: list[int], *, timeout: float = EVENT_WAIT_S) -> dict:
    """Add each item of `stacks` (serials in the backpack: stacks, or the pouches holding them,
    see targets) to `stockpile`: its menu is opened, then per item Add Items, a reading pause,
    the item targeted; the menu that comes back is used for the next one and closed at the end.
    Stops at the first one the server doesn't take. Returns {ok, added [{serial, items}: how
    many stacks the server said it added], left [serials not added], error?, heard}."""
    flow = _Flow(io)
    human.wait("use")
    got = flow.send_wait(actions.dclick(stockpile), is_stockpile_gump,
                         f"double-clicking the stockpile 0x{stockpile:08X}", timeout)
    g = next((e for e in reversed(got) if is_stockpile_gump(e)), None)
    if g is None:
        raise StockpileError(f"the stockpile 0x{stockpile:08X} didn't open within {timeout:g} s: {_said(got)}")
    added, error = [], None
    for serial in stacks:
        st, _ = io.poll()
        it = st["world"]["items"].get(f"0x{serial:08X}")
        if it is None:
            continue
        human.wait("menu")
        got = flow.send_wait(_reply(g, ADD_BUTTON), lambda e: e.get("ev") == "target", "pressing Add Items", timeout)
        cur = next((e for e in reversed(got) if e.get("ev") == "target"), None)
        if cur is None:
            error = f"no target cursor after Add Items: {_said(got)}"
            g = next((e for e in reversed(got) if is_stockpile_gump(e)), None)
            break
        human.wait("aim")
        got = flow.send_wait(actions.target_object(cur["cursor_id"], serial, it.get("x") or 0, it.get("y") or 0,
                                                   it.get("z") or 0, it.get("graphic") or 0,
                                                   cur.get("cursor_type") or 0),
                             is_stockpile_gump, f"targeting 0x{serial:08X}", timeout)
        g = next((e for e in reversed(got) if is_stockpile_gump(e)), None)
        m = next((m for t in _said(got) for m in [ADDED.match(t)] if m), None)
        if m is None:
            error = f"0x{serial:08X} not added: {_said(got) or 'no answer'}"
            break
        added.append({"serial": f"0x{serial:08X}", "items": int(m.group(1))})
    if g is not None:
        human.wait("read")
        io.send(_reply(g, 0))
    done = {int(a["serial"], 16) for a in added}
    return {"ok": error is None, "added": added, "left": [f"0x{s:08X}" for s in stacks if s not in done],
            "heard": flow.heard, **({"error": error} if error else {})}

"""Travel through a moongate's "Moongate Destinations" menu (docs/NOTES.md "Moongate gumps" and
"Getting in and out of the Mausoleum").

Stepping onto a public, red or gold moongate only opens its menu (gump 0xE0E675B8, sent before the
step's confirm, never closed by the server). As Logan Wolf left the Mausoleum at 21:31:00 (capture
20261012_210013): the destination's button (by its label, e.g. "Totem" = 17 there), the menu comes
back with a new serial, then "Travel to Destination" (button 2) on it. The arrival is a position
jump; the pets within range come along. The menu opens with the character's last destination
already selected (its radio button drawn 9724/9721, the others 9721/9724: Prevalia there, Totem at
17:40:36 in 20261013_173449, where Logan pressed Travel at once, 1.5 s after the menu, chased by two
reds): then Travel goes on the first menu. The gate needn't be stepped on: at 20:58:44
(20261013_204518) Logan double-clicked it from a tile away, Travel 0.1 s after the menu, and the jump
came from that tile.

`travel(link, human, dest)` does that on or beside a gate, its menu already up or about to be.
`link` is an agent_link.Link (events, state, act, wait), `human` a humanize.Human (a reading pause
before each click).
"""
from __future__ import annotations

import re
import time

import actions
from agent_link import cheb, serial_of
from room import labelled_button

DESTINATIONS_GUMP = 0xE0E675B8
TRAVEL_LABEL = "Travel to Destination"
MENU_WAIT_S = 3.0                    # the menu after the step onto the gate (live: before the step's confirm)
RESEND_WAIT_S = 3.0                  # the menu re-sent after a destination pick (live 0.05 s)
ARRIVAL_WAIT_S = 5.0                 # Travel -> the position jump (live 0.75 s)
JUMP_TILES = 10
SELECTED_ART = 9724                  # a radio button's normal art when selected (live, both captures)
GATE_GRAPHICS = (0x0F6C, 0x0DDA)     # moongate art (the Mausoleum's red and gold gates: 0x0F6C)


def gate_at(st: dict, tile) -> int | None:
    """The serial of a moongate item on `tile` in the world model, else None."""
    for key, it in (st["world"].get("items") or {}).items():
        if it.get("graphic") in GATE_GRAPHICS and (it.get("x"), it.get("y")) == tuple(tile[:2]) \
                and not it.get("container"):
            return serial_of(key)
    return None


class GateError(Exception):
    pass


class NoDestination(GateError):
    """The menu has no row for the destination asked (closed again: button 0). The hunt runner's leave then takes its
    next --leave-gate (loop_hunt.HuntLoop.leave_by_gate)."""


def selected(g: dict, button: int) -> bool:
    """Whether radio button `button` of gump `g` is drawn selected."""
    m = re.search(rf"\{{\s*button\s+-?\d+\s+-?\d+\s+(\d+)\s+\d+\s+1\s+0\s+{button}\s*\}}", g.get("layout") or "")
    return m is not None and int(m.group(1)) == SELECTED_ART


def _menus(link, since: int) -> list[dict]:
    """The destinations menus opened since event `since`, oldest first, that are still open."""
    live = {(serial_of(g["serial"]), serial_of(g["gump_id"])): g
            for g in link.state()["world"].get("gumps") or [] if g.get("open")}
    out = []
    for ev in link.events[since:]:
        if ev.get("ev") == "gump_open" and serial_of(ev.get("gump_id") or 0) == DESTINATIONS_GUMP:
            g = live.get((serial_of(ev["serial"]), DESTINATIONS_GUMP))
            if g is not None:
                out.append(g)
    return out


def _press(link, human, g: dict, button: int, urgent: bool = False):
    human.wait("gate_macro" if urgent else "menu")
    link.act(actions.gump_reply(serial_of(g["serial"]), DESTINATIONS_GUMP, button, g.get("layout") or "",
                                g.get("lines") or []))


def travel(link, human, dest: str, since: int = 0, log=print, urgent: bool = False) -> dict:
    """Pick `dest` on the destinations menu opened since event `since` (waits MENU_WAIT_S for it),
    then Travel on the re-sent menu (on this one when `dest` is already selected); done when we
    jumped JUMP_TILES or more. `urgent` (a leave to survive): the buttons at a macro's pace (the user's escape
    macro, humanize "gate_macro"). -> {ok, from, to, elapsed_s[, error]}; GateError when there's no menu,
    NoDestination (the menu closed again) when it has no such destination."""
    t0 = time.monotonic()
    start = tuple(link.state()["movement"]["pos"][:2])
    if link.wait(lambda s: _menus(link, since), MENU_WAIT_S) is None:
        raise GateError(f"no 'Moongate Destinations' menu at {start}")
    g = _menus(link, since)[-1]
    pick = labelled_button(g, dest)
    if pick is None:
        link.act(actions.gump_reply(serial_of(g["serial"]), DESTINATIONS_GUMP, 0, g.get("layout") or "",
                                    g.get("lines") or []))
        raise NoDestination(f"no destination {dest!r} on the menu ({[t for t in g.get('lines') or [] if t][:14]})")
    if not selected(g, pick):
        mark = len(link.events)
        _press(link, human, g, pick, urgent)
        if link.wait(lambda s: _menus(link, mark), RESEND_WAIT_S) is None:
            raise GateError(f"the menu didn't come back after picking {dest!r}")
        g = _menus(link, mark)[-1]
    go = labelled_button(g, TRAVEL_LABEL)
    if go is None:
        raise GateError("no 'Travel to Destination' button on the menu")
    _press(link, human, g, go, urgent)
    st = link.wait(lambda s: cheb(s["movement"]["pos"], start) >= JUMP_TILES, ARRIVAL_WAIT_S)
    to = link.state()["movement"]["pos"][:3]
    out = {"ok": st is not None, "from": list(start), "to": list(to), "dest": dest,
           "elapsed_s": round(time.monotonic() - t0, 2)}
    if st is None:
        out["error"] = f"no arrival within {ARRIVAL_WAIT_S:g} s of Travel"
    log(f"moongate to {dest}: " + (f"arrived at {tuple(to)}" if st is not None else out["error"]))
    return out

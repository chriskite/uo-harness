"""Travel through a moongate's "Moongate Destinations" menu (docs/NOTES.md "Moongate gumps" and
"Getting in and out of the Mausoleum").

Stepping onto a public, red or gold moongate only opens its menu (gump 0xE0E675B8, sent before the
step's confirm, never closed by the server). As Logan Wolf left the Mausoleum at 21:31:00 (capture
20261012_210013): the destination's button (by its label, e.g. "Totem" = 17 there), the menu comes
back with a new serial, then "Travel to Destination" (button 2) on it. The arrival is a position
jump; the pets within range come along.

`travel(link, human, dest)` does that from a gate we stand on, its menu already up or about to be.
`link` is an agent_link.Link (events, state, act, wait), `human` a humanize.Human (a reading pause
before each click).
"""
from __future__ import annotations

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


class GateError(Exception):
    pass


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


def _press(link, human, g: dict, button: int):
    human.wait("menu")
    link.act(actions.gump_reply(serial_of(g["serial"]), DESTINATIONS_GUMP, button, g.get("layout") or "",
                                g.get("lines") or []))


def travel(link, human, dest: str, since: int = 0, log=print) -> dict:
    """Pick `dest` on the destinations menu opened since event `since` (waits MENU_WAIT_S for it),
    then Travel on the re-sent menu; done when we jumped JUMP_TILES or more. -> {ok, from, to,
    elapsed_s[, error]}; GateError when there's no menu or no such destination."""
    t0 = time.monotonic()
    start = tuple(link.state()["movement"]["pos"][:2])
    if link.wait(lambda s: _menus(link, since), MENU_WAIT_S) is None:
        raise GateError(f"no 'Moongate Destinations' menu at {start}")
    g = _menus(link, since)[-1]
    pick = labelled_button(g, dest)
    if pick is None:
        raise GateError(f"no destination {dest!r} on the menu ({[t for t in g.get('lines') or [] if t][:14]})")
    mark = len(link.events)
    _press(link, human, g, pick)
    if link.wait(lambda s: _menus(link, mark), RESEND_WAIT_S) is None:
        raise GateError(f"the menu didn't come back after picking {dest!r}")
    g = _menus(link, mark)[-1]
    go = labelled_button(g, TRAVEL_LABEL)
    if go is None:
        raise GateError("no 'Travel to Destination' button on the re-sent menu")
    _press(link, human, g, go)
    st = link.wait(lambda s: cheb(s["movement"]["pos"], start) >= JUMP_TILES, ARRIVAL_WAIT_S)
    to = link.state()["movement"]["pos"][:3]
    out = {"ok": st is not None, "from": list(start), "to": list(to), "dest": dest,
           "elapsed_s": round(time.monotonic() - t0, 2)}
    if st is None:
        out["error"] = f"no arrival within {ARRIVAL_WAIT_S:g} s of Travel"
    log(f"moongate to {dest}: " + (f"arrived at {tuple(to)}" if st is not None else out["error"]))
    return out

"""Resupply from a Storage Shelf as one deterministic flow (live 2026-10-04, Outland Dan;
docs/NOTES.md "Storage shelves"; wiki Storage_Shelf).

A Storage Shelf holds what players stocked in it; each character has one Loadout, saved
per character and shared by every shelf. Its "Resupply" button (the wiki's "Begin
Resupply") equips the loadout's missing gear and tops the pack's quantities up to the
loadout amounts. What the shelf lacks isn't given: the server says "No resupply: <item>"
once per item. This module only resupplies: it never edits the loadout and never presses
Restock (that moves the pack's items into the shelf) or Clear.

IO-agnostic like room.py: `io` has send(pkt) and poll() -> (state, new world events)
(escape.LinkIO for runners, ctl._CtlIO for ctl)."""

from __future__ import annotations

import re
import time

import actions
import escape
from agent_link import cheb
from room import labelled_button

SHELF_GUMP_ID = 0xC0B1026D            # "Storage Shelf" (live 2026-10-04; Razor scripts wait for 3232825965)
# Buttons that change the shelf or the loadout [INFERENCE from their labels and the wiki; never
# pressed; live layout 2026-10-04]: never pressed here, and `ctl act gump` refuses them while the
# shelf shows that label.
SHELF_REFUSED = {1000: "Restock", 16: "Clear"}
SHELF_NAME = "storage shelf"          # tiledata name part ("spring storage shelf", "storage shelf")
SHELF_RANGE = 2                       # tiles: the DTF shelf opened from 2 (live)
SECURE_CLILOC = 501647                # "That is secure." (a shelf this character may not use)
MISSING = re.compile(r"^No resupply: (.+)$")   # one per loadout item the shelf couldn't give (live)
NONE_AVAILABLE = "Unable to resupply: no items available."   # nothing it could give (live, both shelves)
EVENT_WAIT_S = 4.0
SETTLE_S = 0.6                        # after the shelf's answer: late item packets and messages


class ShelfError(Exception):
    """The resupply couldn't go on (the message says why)."""


def _serial(v) -> int:
    return int(v, 16) if isinstance(v, str) else int(v)


def _tile_name(graphic: int) -> str:
    import uomap
    return uomap.tiledata().item(graphic).name or ""


def is_shelf_gump(e: dict) -> bool:
    return e.get("ev") == "gump_open" and e.get("gump_id") is not None and _serial(e["gump_id"]) == SHELF_GUMP_ID


def find_shelves(state: dict) -> list[tuple[int, int, str]]:
    """[(distance, serial, name)] of the storage shelves the world model has on the ground,
    nearest first (tiledata name holds 'storage shelf')."""
    pos = state["movement"].get("pos")
    out = []
    for key, it in (state["world"].get("items") or {}).items():
        if it.get("container") is not None or it.get("x") is None or it.get("graphic") is None or not pos:
            continue
        name = _tile_name(it["graphic"])
        if SHELF_NAME in name.lower():
            out.append((cheb((it["x"], it["y"]), tuple(pos[:2])), _serial(key), name))
    return sorted(out)


def carried(state: dict) -> dict:
    """{serial: {graphic, hue, amount, worn}} of what we wear and what is in the backpack at any depth."""
    world = state["world"]
    me = state["movement"].get("self_serial")
    pack = escape.backpack(world, me) if me is not None else None
    out = {}
    for key, it in (world.get("items") or {}).items():
        s = _serial(key)
        worn = it.get("layer") is not None and it.get("container") is not None and _serial(it["container"]) == me
        if worn or (pack is not None and s != pack and escape.in_pack(world, s, pack)):
            out[s] = {"graphic": it.get("graphic"), "hue": it.get("hue"), "amount": it.get("amount") or 1,
                      "worn": worn}
    return out


def gained(before: dict, after: dict, labels: dict | None = None) -> list[dict]:
    """What came in: new serials, and stacks that grew (amount added)."""
    labels = labels or {}
    out = []
    for s, it in after.items():
        old = before.get(s)
        n = it["amount"] - (old["amount"] if old else 0)
        if old is None or n > 0:
            name = labels.get(f"0x{s:08X}") or (_tile_name(it["graphic"]) if it["graphic"] is not None else None)
            out.append({"serial": f"0x{s:08X}", "graphic": None if it["graphic"] is None else f"0x{it['graphic']:04X}",
                        "hue": it["hue"], "name": name, "amount": n if old else it["amount"], "worn": it["worn"]})
    return out


class _Flow:
    def __init__(self, io, human):
        self.io, self.human, self.heard = io, human, []

    def wait(self, done, timeout: float) -> list:
        end, got = time.monotonic() + timeout, []
        while True:
            _, evs = self.io.poll()
            got += evs
            if any(done(e) for e in evs) or time.monotonic() > end:
                self.heard += [e for e in got if e.get("ev") in ("speech_heard", "cliloc")]
                return got
            time.sleep(0.05)

    def send_wait(self, pkt: bytes, done, what: str, timeout: float = EVENT_WAIT_S) -> list:
        self.io.poll()
        try:
            self.io.send(pkt)
        except Exception as e:
            raise ShelfError(f"{what}: {e}")
        return self.wait(done, timeout)

    def reply(self, g: dict, button: int) -> bytes:
        return actions.gump_reply(_serial(g["serial"]), _serial(g["gump_id"]), button,
                                  g.get("layout") or "", g.get("lines") or [])


def resupply(io, human, shelf: int | None = None, *, walk=None, timeout: float = EVENT_WAIT_S) -> dict:
    """Resupply from `shelf` (a serial), else from the nearest storage shelf in view that this
    character may use ("That is secure." moves on to the next one). A shelf farther than
    SHELF_RANGE is walked to first (walk(serial, SHELF_RANGE), the caller's guarded walker).
    The shelf is double-clicked, its "Resupply" button pressed after a reading pause, and the
    shelf's answer read: "No resupply: <item>" per item it couldn't give, then the shelf again,
    which is closed. Returns {ok, shelf, name, missing [item names], none_available ("Unable to
    resupply: no items available.": the shelf had nothing the loadout still wanted), lines [the
    server's lines that mention resupply], added [{serial, graphic, hue, name, amount, worn}],
    secure [shelves refused], heard}."""
    flow = _Flow(io, human)
    st, _ = io.poll()
    shelves = find_shelves(st)
    if shelf is not None:
        shelves = [s for s in shelves if s[1] == shelf] or [(None, shelf, None)]
    if not shelves:
        raise ShelfError("no storage shelf in view")
    secure = []
    for dist, serial, name in shelves:
        if dist is not None and dist > SHELF_RANGE:
            if walk is None:
                raise ShelfError(f"the {name or 'shelf'} 0x{serial:08X} is {dist} tiles away and no walker was given")
            walk(serial, SHELF_RANGE)
        human.wait("use")
        got = flow.send_wait(actions.dclick(serial),
                             lambda e: is_shelf_gump(e) or e.get("ev") == "cliloc" and e.get("cliloc") == SECURE_CLILOC,
                             f"double-clicking the shelf 0x{serial:08X}", timeout)
        g = next((e for e in reversed(got) if is_shelf_gump(e)), None)
        if g is None:
            if any(e.get("ev") == "cliloc" and e.get("cliloc") == SECURE_CLILOC for e in got):
                secure.append(f"0x{serial:08X}")
                continue
            raise ShelfError(f"the shelf 0x{serial:08X} didn't open within {timeout:g} s")
        button = labelled_button(g, "Resupply")
        if button is None:
            io.send(flow.reply(g, 0))
            raise ShelfError(f"the shelf's menu has no Resupply button: {[t for t in g.get('lines') or [] if t]}")
        st, _ = io.poll()
        before = carried(st)
        human.wait("menu")
        got = flow.send_wait(flow.reply(g, button), is_shelf_gump, "pressing Resupply", timeout)
        got += flow.wait(lambda e: False, SETTLE_S)
        again = next((e for e in reversed(got) if is_shelf_gump(e)), None)
        if again is not None:
            human.wait("read")
            io.send(flow.reply(again, 0))
        st, _ = io.poll()
        said = [(e.get("text") or "").strip() for e in got if e.get("ev") == "speech_heard"]
        missing = [m.group(1).strip() for t in said for m in [MISSING.match(t)] if m]
        return {"ok": again is not None, "shelf": f"0x{serial:08X}", "name": name, "missing": missing,
                "none_available": NONE_AVAILABLE in said,
                "lines": [t for t in said if "resupply" in t.lower()],
                "added": gained(before, carried(st), st["world"].get("labels")), "secure": secure,
                "heard": flow.heard,
                **({} if again is not None else {"error": f"no answer from the shelf within {timeout:g} s"})}
    raise ShelfError(f"every storage shelf in view is secured against this character: {secure}")

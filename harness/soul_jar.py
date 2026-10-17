"""Field of Souls (Outlands' Halloween event, 2026-10-17 to 10-24): eerie corpses and soul jars
(docs/NOTES.md "Field of Souls"; user request 2026-10-17).

Live 2026-10-17, Logan Wolf (capture 20261017_151814, 15:42:56-15:47:17, map0):
- An eerie corpse is a ground item, graphic 0xB1F3 (45555, tiledata "dead"), click label "an eerie corpse".
  Blood (0x122A-0x122F), bones (0x1D09, 0x1D0B) and "eerie whispers" (0x6C6F, a spell effect) lie around it as
  items of their own. The one Logan used lay at (5243,667,0) and came into view 18 tiles off.
- An empty soul jar (0xA99F, "an empty soul jar", stackable; monster loot, or 1,000 gp from an Occult Merchant)
  double-clicked within FILL_RANGE of an eerie corpse (the event post) fills at once. Logan stood 2 tiles off and
  everything came in one flush 44 ms after the click: the empty jar and the corpse with its pieces deleted, a
  filled jar (0xA9A3, "a filled soul jar captured by <name>", a new serial) in the backpack, "spirit energy"
  saying "'My name was <spirit>....'", and the system lines FILLED_TEXT and "You increase your [EventScore by 30.
  ...". A filled jar decays in 60 minutes unless taken to an Occult Merchant (Prevalia or Corpse Creek).

Used by the proxy (EerieSeen: alerts.eerie once per corpse) and the hunt runner (loop_hunt fill_soul_jar).
"""
from __future__ import annotations

import re

import healing

EERIE_CORPSE_GRAPHIC = 0xB1F3
EMPTY_JAR_GRAPHIC = 0xA99F
FILLED_JAR_GRAPHIC = 0xA9A3
FILL_RANGE = 4           # the event post: "within 4 tiles of an Eerie Corpse"
FILL_WALK_R = 2          # the runner walks this close first (Logan filled from 2 tiles)
FILL_WAIT_S = 3.0        # the filled jar came 44 ms after the click
FILLED_TEXT = "You should visit the Occult Merchant in Prevalia or Corpse Creek with this filled soul jar " \
              "to put this spirit to rest."
SYSTEM_SERIAL = 0xFFFFFFFF
_SPIRIT = re.compile(r"My name was (.+?)\.*'?$")


def eerie_corpses(world: dict) -> list[tuple[int, dict]]:
    """(serial, item) of the eerie corpses on the ground in the world model (in view)."""
    return [(int(k, 16), it) for k, it in (world.get("items") or {}).items()
            if it.get("graphic") == EERIE_CORPSE_GRAPHIC and it.get("container") is None and it.get("x") is not None]


def empty_jars(world: dict, me: int) -> list[tuple[int, dict]]:
    """(serial, item) of the empty soul jars in our backpack at any bag depth."""
    return healing.in_pack(world, me, EMPTY_JAR_GRAPHIC)


def filled_jars(world: dict, me: int) -> list[tuple[int, dict]]:
    return healing.in_pack(world, me, FILLED_JAR_GRAPHIC)


def spirit_name(events) -> str | None:
    """The spirit a fill captured, from "spirit energy"'s "'My name was <spirit>....'" among `events`."""
    for e in events:
        if e.get("ev") == "speech_heard" and (m := _SPIRIT.search(e.get("text") or "")):
            return m.group(1)
    return None


def system_lines(events) -> list[str]:
    """What the server said to us among `events`: system text, and cliloc numbers as "cliloc N"."""
    out = []
    for e in events:
        if e.get("ev") == "speech_heard" and e.get("serial") == SYSTEM_SERIAL:
            out.append(e.get("text") or "")
        elif e.get("ev") == "cliloc" and e.get("serial") == SYSTEM_SERIAL:
            out.append(f"cliloc {e.get('cliloc')}")
    return out


class EerieSeen:
    """The eerie corpses seen so far, by serial (the proxy keeps one for all its sessions, so a corpse two of our
    characters see sounds once)."""

    def __init__(self):
        self.seen: set[int] = set()

    def new(self, events) -> list[int]:
        """Serials of eerie corpses that first came into view in `events` (world `item_seen` on the ground)."""
        out = []
        for e in events:
            if e.get("ev") == "item_seen" and e.get("graphic") == EERIE_CORPSE_GRAPHIC \
                    and e.get("container") is None and e["serial"] not in self.seen:
                self.seen.add(e["serial"])
                out.append(e["serial"])
        return out

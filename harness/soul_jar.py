"""Field of Souls (Outlands' Halloween event, 2026-10-17 to 10-24): eerie corpses and soul jars
(docs/NOTES.md "Field of Souls"; user request 2026-10-17).

Live 2026-10-17, Logan Wolf (the store's item_seen; captures 20261017_151814 and 20261017_172358):
- An eerie corpse is a ground item of human-corpse art, a random one of tiledata's "dead" range CORPSE_ART
  (seen: 0xB1C5, 0xB1F3, 0xB201, 0xB202, 0xB20B), click label "an eerie corpse". It spawns with its pieces, each
  an item of its own with the next serials: blood 0x122A/0x122F/0x122D, bones 0x1D0B/0x1D09 and last "eerie
  whispers" (WHISPERS_GRAPHIC 0x6C6F, a spell effect) at serial +6..+8. Neither the art nor the whispers is
  unique (64 "dead"-art and 321 whispers ground items in the store since September, decor and spells), but a
  "dead"-art item followed within WHISPERS_GAP serials by a whispers item is: all 5 such pairs in the store are
  that day's (13:38, 15:22, 15:42, 17:05, 17:26), 5 times the template above. The one Logan used first lay at
  (5243,667,0) and came into view 18 tiles off.
- An empty soul jar (0xA99F, "an empty soul jar", stackable; monster loot, or 1,000 gp from an Occult Merchant)
  double-clicked within FILL_RANGE of an eerie corpse (the event post) fills at once. Logan stood 2 tiles off and
  everything came in one flush 44 ms after the click: the empty jar and the corpse with its pieces deleted, a
  filled jar (0xA9A3, "a filled soul jar captured by <name>", a new serial) in the backpack, "spirit energy"
  saying "'My name was <spirit>....'", and the system lines FILLED_TEXT and "You increase your [EventScore by 30.
  ...". A filled jar decays in 60 minutes unless taken to an Occult Merchant (Prevalia or Corpse Creek).

Used by the proxy (EerieSeen: alerts.eerie once per corpse; SpiritWatch: alerts.spirit when our name-match spirit
comes into view) and the hunt runner (loop_hunt fill_soul_jar).
"""
from __future__ import annotations

import re

import healing

CORPSE_ART = range(0xB1C0, 0xB21D)   # tiledata "dead": the eerie corpse's art is one of these
WHISPERS_GRAPHIC = 0x6C6F            # "eerie whispers", the corpse's last piece
WHISPERS_GAP = 10                    # its serial is the corpse's + 1..this (seen +6..+8)
EERIE_NAME = "an eerie corpse"
EMPTY_JAR_GRAPHIC = 0xA99F
FILLED_JAR_GRAPHIC = 0xA9A3
FILL_RANGE = 4           # the event post: "within 4 tiles of an Eerie Corpse"
FILL_WALK_R = 2          # the runner walks this close first (Logan filled from 2 tiles)
FILL_WAIT_S = 3.0        # the filled jar came 44 ms after the click
FILLED_TEXT = "You should visit the Occult Merchant in Prevalia or Corpse Creek with this filled soul jar " \
              "to put this spirit to rest."
SYSTEM_SERIAL = 0xFFFFFFFF
MOBILE_SERIAL_MAX = 0x3FFFFFFF
_SPIRIT = re.compile(r"My name was (.+?)\.*'?$")
# on entering the Field of Souls (live 17:27:09, 3 s after the entry; 15:55:50 for Karin Winckel)
NAMED = re.compile(r"^If you put the spirit of (.+?) to rest, you will receive")
EJECTED_TEXT = "Your body can no longer sustain itself within the Field of Souls."   # over our own head
FIELD_S = 16 * 60.0     # a session lasts 15 minutes: the spirit is forgotten after this


def eerie_corpses(world: dict) -> list[tuple[int, dict]]:
    """(serial, item) of the eerie corpses on the ground in the world model (in view): "dead" art with its eerie
    whispers among the next WHISPERS_GAP serials, or the click label "an eerie corpse"."""
    items = world.get("items") or {}
    out = []
    for k, it in items.items():
        if it.get("container") is not None or it.get("x") is None or it.get("graphic") not in CORPSE_ART:
            continue
        s = int(k, 16)
        if (it.get("name") or "").lower() == EERIE_NAME or any(
                (items.get(f"0x{s + i:08X}") or {}).get("graphic") == WHISPERS_GRAPHIC for i in range(1, WHISPERS_GAP + 1)):
            out.append((s, it))
    return out


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
    """The eerie corpses seen so far, by serial, from world `item_seen` events alone (either piece may come
    first). The proxy keeps one for all its sessions, so a corpse two of our characters see sounds once."""

    def __init__(self):
        self.seen: set[int] = set()
        self.art: set[int] = set()        # ground "dead"-art serials seen
        self.whispers: set[int] = set()   # ground eerie-whispers serials seen

    def new(self, events) -> list[int]:
        """Serials of eerie corpses first recognised in `events`."""
        out = []
        for e in events:
            if e.get("ev") != "item_seen" or e.get("container") is not None:
                continue
            s, g = e["serial"], e.get("graphic")
            if g in CORPSE_ART:
                self.art.add(s)
                hit = s if any(s + i in self.whispers for i in range(1, WHISPERS_GAP + 1)) else None
            elif g == WHISPERS_GRAPHIC:
                self.whispers.add(s)
                hit = next((s - i for i in range(1, WHISPERS_GAP + 1) if s - i in self.art), None)
            else:
                continue
            if hit is not None and hit not in self.seen:
                self.seen.add(hit)
                out.append(hit)
        return out


class SpiritWatch:
    """One character's name-match eerie spirit in the Field of Souls (user request 2026-10-17: "add an alert for
    our named spirit coming into view"). The server names it on entry ("If you put the spirit of <name> to rest,
    you will receive a 33.0% bonus ..."). The stock client single-clicks each mobile as it comes into view and the
    server's click label carries its name (live 17:28:32.18: the spirit's 0x09, its label 43 ms later), so the
    label is the sighting. Once per field session; forgotten at the ejection text or FIELD_S after the entry."""

    def __init__(self):
        self.name: str | None = None
        self.until = 0.0
        self.found = False

    def feed(self, events, now: float, state) -> int | None:
        """The spirit's serial when it first comes into view in `events` (world events; `state` the world model's
        StateStore: its click labels and mobiles), else None."""
        named = False
        for e in events:
            if e.get("ev") != "speech_heard":
                continue
            if e.get("serial") == SYSTEM_SERIAL and (m := NAMED.match(e.get("text") or "")):
                self.name, self.until, self.found, named = m.group(1), now + FIELD_S, False, True
            elif e.get("text") == EJECTED_TEXT:      # over our own head (live 18:47:20.28), not the system's
                self.name = None
        if self.name is None or self.found:
            return None
        if now > self.until:
            self.name = None
            return None
        cands = [e["serial"] for e in events if e.get("ev") == "speech_heard" and self.name in (e.get("name"), e.get("text"))]
        cands += [n["serial"] for e in events if e.get("ev") == "names" for n in e.get("entries") or ()
                  if n.get("name") == self.name]
        if named:            # already in view when the server named it
            cands += [s for s, label in state.labels.items() if label == self.name]
            cands += [s for s, m in state.mobiles.items() if m.name == self.name]
        for s in cands:
            if 0 < s <= MOBILE_SERIAL_MAX and s in state.mobiles:
                self.found = True
                return s
        return None

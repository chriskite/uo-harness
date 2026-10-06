"""Speech guard for automated harvest jobs (user request 2026-10-01).

When a character speaks near us during a harvest job, the job stops acting and
hands control to the overseer (a `speech_nearby` juncture). It acts again only
after the all-clear (the overseer acks the juncture), or the overseer stops it
and talks to the speaker (a GM, say). Only harvest jobs use it: walking through
town with the overseer, people talk all the time (PLAN.md, 2026-09-29).

What counts as "a character speaking" (measured on the 27 captures, 2026-10-01):
- speech types say/emote/focus/whisper/yell (0, 2, 7, 8, 9). Click labels (6),
  system text (1), spell words (10) and guild/alliance chat (13, 14) don't.
- not the server (serial 0 / 0xFFFFFFFF), not us, not an item (serial
  >= 0x40000000: tool uses, "Emptying"), not a bare number (damage numbers
  over mobs, "-57").
- not a click echo: Outlands answers a 0x09 click on a player with type-0
  lines (title "Viceroy", guild tag "[Veteran, J4F]") 0.04-0.06 s later
  (all 66 in the captures); real speech came 19-235 s after any click. A line
  within CLICK_ECHO_S of a click on its speaker is ignored.
- the speaker isn't an NPC or a creature: the player flag 0x20 (threats.py
  heuristic) always counts as a character, even at notoriety 7 (staff may be
  invulnerable [INFERENCE]); without it, notoriety 7, a "Name the <title>" label,
  a creature-style name ("an andarian footman", notoriety 1-3: threats.identify)
  or a non-human body (pets: "(bonded)") is an NPC or a creature.
- a speaker the client doesn't have (hidden or out of view) counts: a hidden
  GM speaks without a body on screen [INFERENCE].
- not a tamer's pet command (`pet_command`): at the New Player Dungeon tamers
  say "all guard me", "All Kill" all the time (junctures 52/56/57/58/60,
  2026-10-02). Such a line still goes into `context`; one from a speaker with
  staff hints still counts.

Every line is also judged by Laya (triage.py) from the recent speech around
it (`context`); a likely attendance check is a staff hint.

Staff on sight (docs/PLAN.md "Staff alarm on an invulnerable player in view"):
an "invulnerable player", notoriety 7 with the player flag 0x20, is a staff
hint without a word said. Across every capture to 2026-10-04 notoriety 7
never carried 0x20 (2,607 sightings: NPCs, vendors, player vendors, criers)
and the flag was on every player (docs/research/THREATS.md §1.1); that staff
show this way is [INFERENCE]. `SpeechGuard.sightings` reports each one coming
into view, shaped like a speaker (type "sighting", no text), so the runners
hold and alarm through the speech-hold path. Notoriety 7 without the flag
(vendors, NPCs) stays ignored.
"""
from __future__ import annotations

import re
import time
from collections import deque

import threats

SPEECH_TYPES = {0: "say", 2: "emote", 7: "focus", 8: "whisper", 9: "yell"}
CLICK_ECHO_S = 1.0
CLEAR_S = 15 * 60.0          # an all-clear covers that speaker this long
RECENT_N = 6                 # lines of context kept for a speaker's line (triage.py)
RECENT_S = 120.0
ITEM_SERIAL_MIN = 0x40000000
_NUMBER = re.compile(r"^\s*[-+]?\d+\s*$")
# names staff are often given on UO shards [INFERENCE: no Outlands staff seen yet]
_STAFF_NAME = re.compile(r"\b(gm|game ?master|seer|counselor|admin|staff|developer|dev)\b", re.I)
STAFF_BODIES = (0x3DB, 0x3DF)    # ClassicUO Mobile.IsHuman includes the GM body 0x3DB [INFERENCE: use]

# Pet command words: the English pet keywords of speech.mul (0x155-0x170, read
# 2026-10-02: come drop fetch get bring follow friend guard kill attack patrol
# report stop "follow me" release transfer stay; "all guard me"), plus unfriend.
PET_COMMANDS = frozenset({
    "kill", "attack", "guard", "guard me", "follow", "follow me", "come", "stop", "stay", "drop",
    "patrol", "release", "transfer", "friend", "unfriend", "fetch", "get", "bring", "report"})
_TRAILING = re.compile(r"[\s.!?,;:]+$")


def pet_command(text: str, world: dict) -> bool:
    """`text` is a whole pet command: "all <command>", or "<name> <command>"
    where <name> is one word, the first word of the name of a non-human mobile
    on screen (a pet), and not a word of our own name ("Hackworth stop" is
    someone talking to us). Case-insensitive, trailing punctuation ignored; a
    bare command ("stop") or any extra word ("all stop please") is not one."""
    words = _TRAILING.sub("", text.strip()).lower().split()
    if len(words) < 2 or " ".join(words[1:]) not in PET_COMMANDS:
        return False
    slot = words[0]
    if slot == "all":
        return True
    if slot in (world["self"].get("name") or "").lower().split():
        return False
    labels = world.get("labels") or {}
    for key, mob in world["mobiles"].items():
        if mob.get("graphic") in threats.HUMAN_BODIES:
            continue
        for name in (mob.get("name"), labels.get(key)):
            if name and name.split()[0].lower() == slot:
                return True
    return False


def _serial(v) -> int:
    return int(v, 16) if isinstance(v, str) else int(v)


def is_character(mob: dict, label: str | None) -> bool:
    """A player character, not an NPC or a creature: the player flag 0x20, or a
    human body with no NPC evidence: not notoriety 7, and not what
    threats.identify calls an NPC or a monster (a "Name the <title>" label, or a
    creature-style name, "an andarian footman", at notoriety 1-3: spawned
    soldiers whose battle barks held a lumber job five times, live 2026-10-06)."""
    if (mob.get("flags") or 0) & threats.FLAG_PLAYER_HINT:
        return True
    if mob.get("notoriety") == 7 or mob.get("graphic") not in threats.HUMAN_BODIES:
        return False
    kind, player, _ = threats.identify(mob, label)
    return player is not False and kind != "monster"


def speaker(world: dict, ev: dict) -> dict | None:
    """Who is speaking, when `ev` (a world `speech_heard` event) is a
    character speaking near us; else None."""
    if ev.get("ev") != "speech_heard" or ev.get("type") not in SPEECH_TYPES:
        return None
    s = ev.get("serial")
    if s is None:
        return None
    s = _serial(s)
    me = world["self"].get("serial")
    if s in (0, 0xFFFFFFFF) or s >= ITEM_SERIAL_MIN or (me is not None and s == _serial(me)):
        return None
    text = ev.get("text") or ""
    if not text.strip() or _NUMBER.match(text):
        return None
    key = f"0x{s:08X}"
    mob = world["mobiles"].get(key)
    label = (world.get("labels") or {}).get(key)
    info = {"serial": key, "name": ev.get("name") or None, "label": label, "text": text,
            "type": SPEECH_TYPES[ev["type"]], "hue": ev.get("hue"), "on_screen": mob is not None}
    if mob is None:
        info["evidence"] = ["not on screen (hidden or out of view)"]
    else:
        body, noto, flags = mob.get("graphic"), mob.get("notoriety"), mob.get("flags") or 0
        player = bool(flags & threats.FLAG_PLAYER_HINT)
        if not is_character(mob, label):
            return None
        info.update(body=None if body is None else f"0x{body:04X}", notoriety=noto,
                    flags=f"0x{flags:02X}", x=mob.get("x"), y=mob.get("y"),
                    evidence=["player flag 0x20"] if player else ["human body, no npc evidence"])
        if body in STAFF_BODIES:
            info["evidence"].append(f"GM body 0x{body:04X}")
    if invulnerable_player(mob or {}):
        info["evidence"].append(INVULNERABLE_EVIDENCE)
    if _STAFF_NAME.search(f"{info['name'] or ''} {label or ''}"):
        info["evidence"].append("staff-like name")
    return info


INVULNERABLE_HINT = "invulnerable player"
INVULNERABLE_EVIDENCE = f"{INVULNERABLE_HINT} (notoriety 7 + player flag 0x20)"
STAFF_HINTS = ("GM body", "staff-like name", "not on screen", "attendance check", INVULNERABLE_HINT)


def invulnerable_player(mob: dict) -> bool:
    """Notoriety 7 with the player flag 0x20: never an NPC, vendor or player
    vendor in our captures; a GM [INFERENCE] (module docstring)."""
    return mob.get("notoriety") == 7 and bool((mob.get("flags") or 0) & threats.FLAG_PLAYER_HINT)


def worn(world: dict, key: str) -> list[dict]:
    """What the world model has on mobile `key` (items parented to it with a
    layer, from 0x78 / 0x2E), by layer."""
    s = _serial(key)
    out = [{"serial": k, "layer": it["layer"], "graphic": it.get("graphic"), "hue": it.get("hue"),
            "name": it.get("name")}
           for k, it in (world.get("items") or {}).items()
           if it.get("layer") is not None and it.get("container") is not None and _serial(it["container"]) == s]
    return sorted(out, key=lambda e: e["layer"])


def sighting(world: dict, key: str) -> dict:
    """An invulnerable player in view as a speaker-shaped entry (type
    "sighting", text None): what it looks like, for the staff_sighting record."""
    mob = world["mobiles"][key]
    label = (world.get("labels") or {}).get(key)
    body, flags = mob.get("graphic"), mob.get("flags") or 0
    info = {"serial": key, "name": mob.get("name") or (world.get("names") or {}).get(key), "label": label,
            "text": None, "type": "sighting", "hue": mob.get("hue"), "on_screen": True,
            "body": None if body is None else f"0x{body:04X}", "notoriety": mob.get("notoriety"),
            "flags": f"0x{flags:02X}", "x": mob.get("x"), "y": mob.get("y"), "z": mob.get("z"),
            "worn": worn(world, key), "evidence": ["player flag 0x20", INVULNERABLE_EVIDENCE]}
    if body in STAFF_BODIES:
        info["evidence"].append(f"GM body 0x{body:04X}")
    if _STAFF_NAME.search(f"{info['name'] or ''} {label or ''}"):
        info["evidence"].append("staff-like name")
    return info


def what(who: dict) -> str:
    """What a held-for entry did: said its line, or came into view."""
    if who.get("type") == "sighting":
        return f"is in view ({INVULNERABLE_EVIDENCE})"
    return f"said {who['text']!r}"


def staff_hints(who: dict) -> list[str]:
    """The evidence entries that suggest staff (a GM body, a staff-like name, a
    speaker not on screen: hidden staff speak without a body; a line Laya
    scores as an attendance check, triage.py; an invulnerable player, notoriety
    7 + player flag 0x20) [INFERENCE: no Outlands staff seen yet]."""
    return [e for e in who.get("evidence") or [] if e.startswith(STAFF_HINTS)]


class SpeechGuard:
    """Scans a runner's world events (agent_link.Link.events / .event_t) for
    characters speaking near us, and its world for invulnerable players in view."""

    def __init__(self, now=time.time):
        self.upto = None             # first scan: what was said before the job started is history
        self.clicked = {}            # serial -> event time of the last 0x09 click on it
        self.cleared = {}            # serial key -> wall time until which it's all-clear
        self.recent = deque(maxlen=RECENT_N)  # {t, name, text}: character lines and ours, cleared or not
        self.names = {}              # serial key -> the name a character spoke under (triage.nearby)
        self.in_view = set()         # serial keys of the invulnerable players in view at the last sightings()
        self.sighted = set()         # ... seen at all this session (each logged once: staff_sighting)
        self.now = now

    def scan(self, world: dict, events: list, times: list) -> list[dict]:
        """New speakers since the last scan (cleared ones left out). Each
        carries `context`: the recent lines up to and including its own."""
        if self.upto is None:
            self.upto = len(events)
            return []
        me = world["self"].get("serial")
        out = []
        for i in range(self.upto, len(events)):
            ev, t = events[i], times[i]
            if ev.get("ev") == "query" and ev.get("kind") == 0x09 and ev.get("serial") is not None:
                self.clicked[_serial(ev["serial"])] = t
                continue
            if t - self.clicked.get(_serial(ev["serial"]) if ev.get("serial") is not None else -1, -1e18) \
                    <= CLICK_ECHO_S:
                continue
            if (me is not None and ev.get("ev") == "speech_heard" and ev.get("type") in SPEECH_TYPES
                    and ev.get("serial") is not None and _serial(ev["serial"]) == _serial(me)
                    and (ev.get("text") or "").strip()):
                self.recent.append({"t": t, "name": world["self"].get("name") or "me", "text": ev["text"]})
                continue
            who = speaker(world, ev)
            if who is None:
                continue
            self.recent.append({"t": t, "name": who["label"] or who["name"] or who["serial"], "text": who["text"]})
            if who["name"]:
                self.names[who["serial"]] = who["name"]
            if self.cleared.get(who["serial"], 0) > self.now():
                continue
            if pet_command(who["text"], world) and not staff_hints(who):
                continue
            who["t"] = t
            who["context"] = [{"name": ln["name"], "text": ln["text"]} for ln in self.recent if t - ln["t"] <= RECENT_S]
            out.append(who)
        self.upto = len(events)
        return out

    def clear(self, serials):
        until = self.now() + CLEAR_S
        for s in serials:
            self.cleared[s] = until

    def sightings(self, world: dict) -> list[dict]:
        """Invulnerable players that came into view since the last call (one
        already in view when the job starts counts), as `sighting` entries; one
        that stays in view is reported once, one cleared (clear()) is left out.
        Each carries `first`: True on its first sighting this session."""
        me = world["self"].get("serial")
        me = None if me is None else _serial(me)
        now_in = {k for k, m in (world.get("mobiles") or {}).items()
                  if invulnerable_player(m) and _serial(k) != me}
        out = []
        for k in sorted(now_in - self.in_view):
            first = k not in self.sighted
            self.sighted.add(k)
            if not first and self.cleared.get(k, 0) > self.now():
                continue
            out.append({**sighting(world, k), "first": first})
        self.in_view = now_in
        return out

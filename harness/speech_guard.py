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
  invulnerable [INFERENCE]); without it, notoriety 7, a "Name the <title>" label
  or a non-human body (pets: "(bonded)") is an NPC or a creature.
- a speaker the client doesn't have (hidden or out of view) counts: a hidden
  GM speaks without a body on screen [INFERENCE].

Every line is also judged by Laya (triage.py) from the recent speech around
it (`context`); a likely attendance check is a staff hint.
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


def _serial(v) -> int:
    return int(v, 16) if isinstance(v, str) else int(v)


def is_character(mob: dict, label: str | None) -> bool:
    """A player character, not an NPC or a creature: the player flag 0x20, or
    a human body with no NPC evidence (notoriety 7, a "Name the <title>" label)."""
    if (mob.get("flags") or 0) & threats.FLAG_PLAYER_HINT:
        return True
    return not (mob.get("notoriety") == 7 or (label and threats._TITLE.match(label))
                or mob.get("graphic") not in threats.HUMAN_BODIES)


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
    if _STAFF_NAME.search(f"{info['name'] or ''} {label or ''}"):
        info["evidence"].append("staff-like name")
    return info


STAFF_HINTS = ("GM body", "staff-like name", "not on screen", "attendance check")


def staff_hints(who: dict) -> list[str]:
    """The evidence entries that suggest staff (a GM body, a staff-like name, a
    speaker not on screen: hidden staff speak without a body; a line Laya
    scores as an attendance check, triage.py) [INFERENCE: no Outlands staff
    seen yet]."""
    return [e for e in who.get("evidence") or [] if e.startswith(STAFF_HINTS)]


class SpeechGuard:
    """Scans a runner's world events (agent_link.Link.events / .event_t) for
    characters speaking near us."""

    def __init__(self, now=time.time):
        self.upto = None             # first scan: what was said before the job started is history
        self.clicked = {}            # serial -> event time of the last 0x09 click on it
        self.cleared = {}            # serial key -> wall time until which it's all-clear
        self.recent = deque(maxlen=RECENT_N)  # {t, name, text}: character lines and ours, cleared or not
        self.names = {}              # serial key -> the name a character spoke under (triage.nearby)
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
            who["t"] = t
            who["context"] = [{"name": ln["name"], "text": ln["text"]} for ln in self.recent if t - ln["t"] <= RECENT_S]
            out.append(who)
        self.upto = len(events)
        return out

    def clear(self, serials):
        until = self.now() + CLEAR_S
        for s in serials:
            self.cleared[s] = until

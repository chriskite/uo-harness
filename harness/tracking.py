"""Tracking's Hunting mode, set the way a player does it in the Tracking gump, and
what the hunt reports (docs/NOTES.md "Tracking", docs/LUMBER_LOOP.md §13).

Live 20261001_214649: UseSkill 38 opens gump 0xFE5C638B; button 8 / 7 step the
hunting mode forward / back through MODES (the server answers "You will now hunt
<mode>."), 6 begins / stops Hunting ("You begin hunting." / "You stop hunting."
from our own serial, buff icon 173 on / off). The server re-sends the gump after
every click. Each hit is a "Now tracking: <name> (N spaces to target)" line plus a
quest arrow at the target's position (world.tracking.hits, with the mode at hit time).

`hunt` is the click sequence `ctl act track` and the lumber runner share. Its IO
object is the one escape.py uses: `send(pkt)` (raises TrackError, or the caller's
own error, when the proxy refuses) and `poll()` -> (full state, world events since
the last poll). `Keeper` is a runner's running view of its own hunt: on/off and
mode from the server's lines and the buff, the time spent hunting, new hits.
"""

from __future__ import annotations

import re
import time

import actions
from agent_link import cheb

SKILL = 38
GUMP_ID = 0xFE5C638B
MODES = ("criminal players", "innocent players", "friendly players", "aggressive creatures",
         "passive creatures", "townsfolk", "all players", "all hostile players",
         "enemy players", "murderer players")
ALIASES = {"red": "murderer players", "reds": "murderer players", "grey": "criminal players",
           "greys": "criminal players", "gray": "criminal players", "grays": "criminal players",
           "blue": "innocent players", "blues": "innocent players", "hostile": "all hostile players"}
MURDERERS = "murderer players"
BTN_HUNT, BTN_PREV, BTN_NEXT = 6, 7, 8
SKILL_BUSY = 500118            # "You must wait a few moments to use another skill."
BUFF_ICON = 173                # "Tracking Hunting" buff on self while Hunting (cliloc 1110004)
WAIT_S = 3.0                   # the server answers a click or skill use within ~0.1 s live
SYSTEM = 0xFFFFFFFF
HUNT_PREFIX = "You will now hunt "
NOW_TRACKING = re.compile(r"^Now tracking: (.+) \((\d+) spaces? to target\)$")


class TrackError(Exception):
    """The hunt couldn't be set (the message says why)."""


class TrackBusy(TrackError):
    """Another skill's cooldown refused the skill use (500118): try again later."""


class NoTracking(TrackError):
    """The Tracking gump never opened: no Tracking skill."""


def parse_mode(words) -> str:
    """A Hunting mode from words: the mode itself ("murderer players"), an alias
    (reds, greys, blues, hostile) or words only one mode contains ("murderer").
    ValueError names the modes."""
    key = " ".join(w.lower() for w in words).strip()
    if key in MODES:
        return key
    if key in ALIASES:
        return ALIASES[key]
    hits = [m for m in MODES if key and all(w in m.split() for w in key.split())]
    if len(hits) != 1:
        raise ValueError(f"track: {'ambiguous' if hits else 'unknown'} mode {key!r}; modes: "
                         f"{', '.join(MODES)} (or reds, greys, blues, hostile)")
    return hits[0]


def said(prefix: str):
    return lambda evs: any(e.get("ev") == "speech_heard" and str(e.get("text", "")).startswith(prefix)
                           for e in evs)


def hunt_mode(e) -> str | None:
    """The mode in the server's "You will now hunt <mode>." (System only, so nobody nearby
    can fake it by speaking)."""
    t = str(e.get("text", ""))
    if e.get("ev") == "speech_heard" and e.get("serial") == SYSTEM and t.startswith(HUNT_PREFIX) \
            and t.endswith("."):
        return t[len(HUNT_PREFIX):-1]
    return None


def _serial(v) -> int | None:
    if v is None:
        return None
    return int(v, 16) if isinstance(v, str) else int(v)


def gump(world: dict) -> dict | None:
    """The newest open Tracking gump."""
    gs = [g for g in world.get("gumps") or [] if g.get("open") and _serial(g.get("gump_id")) == GUMP_ID]
    return max(gs, key=lambda g: _serial(g["serial"])) if gs else None


def buffed(world: dict) -> bool:
    me = (world.get("self") or {}).get("serial") or ""
    return str(BUFF_ICON) in ((world.get("buffs") or {}).get(me) or {})


def skill(world: dict) -> float | None:
    """Tracking's base value from the self skill list (tenths on the wire), or None
    when the list doesn't have it."""
    sk = ((world.get("self") or {}).get("skills") or {}).get(str(SKILL))
    if not sk:
        return None
    v = sk.get("base", sk.get("value"))
    return None if v is None else v / 10.0


class _Clicks:
    """One hunt's clicks over the IO: the gump, sends, and what the server answered."""

    def __init__(self, io, human, busy_tries: int):
        self.io, self.human, self.busy_tries = io, human, busy_tries
        self.clicks, self.events, self.backlog, self.st = [], [], [], None

    def world(self) -> dict:
        self.st, new = self.io.poll()
        self.backlog += new
        return self.st["world"]

    def gump(self):
        return gump(self.world())

    def send(self, pkt, until, what) -> list:
        self.world()
        self.backlog = []
        try:
            self.io.send(pkt)
        except TrackError as e:
            raise TrackError(f"{what}: {e}")
        self.clicks.append(what)
        end, got = time.monotonic() + WAIT_S, []
        while True:
            self.world()
            got, self.backlog = got + self.backlog, []
            if until(got) or time.monotonic() > end:
                self.events += got
                return got
            time.sleep(0.1)

    def open_gump(self) -> dict:
        busy = False
        for _ in range(self.busy_tries):
            g = self.gump()
            if g is not None:
                return g
            self.human.wait("use")
            got = self.send(actions.use_skill(SKILL),
                            lambda evs: _busy(evs) or any(e.get("ev") == "gump_open"
                                                          and e.get("gump_id") == GUMP_ID for e in evs),
                            "use Tracking")
            busy = _busy(got)
            if busy and self.busy_tries > 1:   # another skill's cooldown: try again shortly
                time.sleep(WAIT_S)
        g = self.gump()
        if g is not None:
            return g
        if busy:
            raise TrackBusy("another skill's cooldown (500118): the Tracking gump didn't open")
        raise NoTracking("the Tracking gump didn't open (no Tracking skill?)")

    def press(self, button, until, what) -> list:
        g = self.open_gump()
        self.human.wait("menu")
        return self.send(actions.gump_reply(_serial(g["serial"]), GUMP_ID, button, g.get("layout") or "",
                                            g.get("lines") or []), until, what)


def _busy(evs) -> bool:
    return any(e.get("ev") == "cliloc" and e.get("cliloc") == SKILL_BUSY for e in evs)


def hunt(io, mode: str | None, human, hunting: bool | None = None, busy_tries: int = 3) -> dict:
    """Set Hunting to `mode` (one of MODES), or stop it (mode None), like a player:
    the stock UseSkill only if the gump isn't open, the mode arrows the shorter way
    round (forward on a tie), then Begin. A different mode while hunting: Stop,
    change, Begin. Stopping when not hunting sends nothing. Every click is the stock
    0xB1 after a reaction pause; the gump stays open, as it does for a player.

    `hunting`: what the caller knows of the hunt now (the server stopped it while the
    gump stayed open, so the gump's button text is stale); None reads the open gump.
    busy_tries: skill uses tried while another skill's cooldown (500118) refuses
    them, WAIT_S apart; TrackBusy when they run out. NoTracking: the gump never
    opened. Returns {ok, hunting, mode, arrow, clicks, events (world events heard)}."""
    c = _Clicks(io, human, busy_tries)
    hint = hunting

    def on() -> bool:                         # the open gump says it: "Stop Hunting" while on
        if hint is not None and not c.clicks:
            return hint
        return "Stop Hunting" in (c.open_gump().get("lines") or [])

    w = c.world()
    cur = (w.get("tracking") or {}).get("mode")
    if mode is None:
        if hint or (hint is None and (gump(w) is not None or buffed(w) or (w.get("tracking") or {}).get("hunting"))):
            if on():
                c.press(BTN_HUNT, said("You stop hunting."), "stop hunting")
    else:
        if on() and cur != mode:
            c.press(BTN_HUNT, said("You stop hunting."), "stop hunting")

        def step(btn, what):                  # the mode the server names in its answer to the click
            got = c.press(btn, said(HUNT_PREFIX), what)
            return next((m for m in map(hunt_mode, got) if m), None)
        if cur not in MODES:                  # not heard this session: one step tells it
            cur = step(BTN_NEXT, "next mode")
        n = len(MODES)
        for _ in range(n):
            if cur == mode or cur not in MODES:
                break
            fwd = (MODES.index(mode) - MODES.index(cur)) % n
            btn, what = (BTN_NEXT, "next mode") if fwd <= n - fwd else (BTN_PREV, "previous mode")
            new = step(btn, what)
            if new is None or new == cur:
                raise TrackError(f"the hunting mode didn't change from {cur!r}")
            cur = new
        if cur != mode:
            raise TrackError(f"couldn't set the hunting mode to {mode!r} (server says {cur!r})")
        if not on():
            c.press(BTN_HUNT, said("You begin hunting."), "begin hunting")
    w = c.world()
    g = gump(w)                               # the server re-sent it after the last click
    tr = w.get("tracking") or {}
    if hint is not None and not c.clicks:
        is_on = hint
    elif g is not None:
        is_on = "Stop Hunting" in (g.get("lines") or [])
    else:
        is_on = bool(tr.get("hunting"))
    ok = (not is_on) if mode is None else (is_on and cur == mode)
    return {"ok": bool(ok), "hunting": is_on, "mode": None if mode is None else cur, "arrow": tr.get("arrow"),
            "clicks": c.clicks, "events": c.events}


class Keeper:
    """A runner's view of its own hunt, from the world events it reads: on/off from
    "You begin/stop hunting." (own serial) and buff 173 add/remove on self (a relog
    drops the buff without a stop line, live 2026-10-02/03), the mode from the
    System's "You will now hunt …" lines (world.tracking.mode before the first),
    the seconds hunting `mode` vs not, and new hits (world.tracking.hits with a
    seq above the newest one seen at start: an arrow from before the run is old)."""

    def __init__(self, want: str = MURDERERS, retry_s: float = 30.0):
        self.want = want
        self.retry_s = retry_s
        self.on = None                 # None until the first world read
        self.mode = None
        self.last_try = None           # monotonic time of the last attempt to turn it on
        self.unavailable = None        # why tracking can't run for this character (logged once)
        self.hit_seq = None            # newest hit seq handled
        self.spaces = {}               # name -> N of the newest "Now tracking: <name> (N spaces ...)"
        self._t = None
        self.reset()

    def reset(self):
        """Start a new trip's tally."""
        self.on_s = self.off_s = 0.0
        self.hits = self.murderer_hits = self.attempts = 0

    def tally(self) -> dict:
        total = self.on_s + self.off_s
        out = {"on_s": round(self.on_s, 1), "off_s": round(self.off_s, 1),
               "on_frac": round(self.on_s / total, 3) if total else None,
               "hits": self.hits, "murderer_hits": self.murderer_hits, "attempts": self.attempts}
        if self.unavailable:
            out["unavailable"] = self.unavailable
        return out

    def hunting(self) -> bool:
        return bool(self.on) and self.mode == self.want

    def observe(self, world: dict, events, self_serial, now: float):
        """Fold new world events into on/mode and the time tally."""
        tr = world.get("tracking") or {}
        if self.on is None:
            self.on = buffed(world) or bool(tr.get("hunting"))
            self.mode = tr.get("mode")
            self.hit_seq = tr.get("seq") or 0
        if self._t is not None:
            dt = max(0.0, now - self._t)
            if self.hunting():
                self.on_s += dt
            else:
                self.off_s += dt
        self._t = now
        for e in events:
            ev = e.get("ev")
            if ev == "buff_update" and e.get("icon_id") == BUFF_ICON and e.get("serial") == self_serial:
                self.on = True
            elif ev == "buff_remove" and e.get("buff_id") == BUFF_ICON and e.get("serial") == self_serial:
                self.on = False
            elif ev == "speech_heard":
                text = e.get("text") or ""
                if e.get("serial") == self_serial and text == "You begin hunting.":
                    self.on = True
                elif e.get("serial") == self_serial and text == "You stop hunting.":
                    self.on = False
                elif e.get("serial") == SYSTEM:
                    m = hunt_mode(e)
                    if m:
                        self.mode = m
                    hit = NOW_TRACKING.match(text)
                    if hit:
                        self.spaces[hit[1]] = int(hit[2])

    def due(self, now: float) -> bool:
        """Hunting `want` is off, and the last try is retry_s ago."""
        return not self.unavailable and not self.hunting() \
            and (self.last_try is None or now - self.last_try >= self.retry_s)

    def new_hits(self, world: dict, pos) -> list:
        """Hits since the last call, oldest first: {serial, name, x, y, mode, murderer,
        distance} (distance: Chebyshev to the arrow, else the line's spaces, else None)."""
        out = []
        for h in (world.get("tracking") or {}).get("hits") or []:
            if h.get("seq") is None or h["seq"] <= (self.hit_seq or 0):
                continue
            self.hit_seq = h["seq"]
            name = str(h.get("text") or "")
            name = name[len("[Hunting] "):] if name.startswith("[Hunting] ") else name
            dist = None
            if h.get("x") is not None and pos is not None:
                dist = cheb(pos, (h["x"], h["y"]))
            elif name in self.spaces:
                dist = self.spaces[name]
            murderer = h.get("mode") == MURDERERS
            self.hits += 1
            self.murderer_hits += murderer
            out.append({"serial": _serial(h.get("serial")), "name": name, "x": h.get("x"), "y": h.get("y"),
                        "z": h.get("z"), "mode": h.get("mode"), "murderer": murderer, "distance": dist,
                        "spaces": self.spaces.get(name)})
        return out

"""Tidal Tomb's oxygen for the hunt runner (docs/HUNT_LOOP.md "Oxygen"; user 2026-10-28, docs/PLAN.md "Oxygen: a breath
on level 1 or a dolphin on level 3").

Under water (Tidal Tomb's levels 2+) the server counts our time since the last refill: from 732 s on, Logan and the
pets lose hits every 6 s down to 66 % of their max (docs/NOTES.md "Oxygen, measured"). A refill: dry ground (level 1, the
outside), or a Recovery Dolphin within 12 tiles, with "Your oxygen is refilled." every ~5.25 s.

`Clock` keeps our seconds under water: our own count since the last refill (dry ground, the refill line), held inside
what the server's 'Oxygen' gump says (its second bar loses one art every 30.1 s; scenario `gump`) and moved up to the
deprivation by its lines (a replay of the live stream of 2026-10-24 put the first hit at 733 s: live 732).
`sites` are the refills: the scenario's `hops` (a teleporter up to dry ground and the one back down) and its dolphins,
each with the underwater box it lies in (the runner takes those in its own box only).

The data is the simulator's scenario block (data/sim/tidal_tomb.json `oxygen`), as tamer.py reads the Mausoleum's spawn
spots: one copy of the boxes, dolphins, hops and gump numbers, each with its evidence there."""
from __future__ import annotations

import json
import os
import re

from agent_link import serial_of

SCENARIO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "sim", "tidal_tomb.json")
TEXT_REFILLED = "Your oxygen is refilled."
SYSTEM = (None, 0, 0xFFFFFFFF, "0xFFFFFFFF")
FIRST_BAR_Y, SECOND_BAR_Y = 50, 64       # the gump's two `tilepichue` bars (x 58)
BAR_SLACK_S = 3.0     # the gump fit's play either way (scenario `gump`: steps within ~3 s of (k - 1) x 30.1 + 10)
BAR_RE = re.compile(r"\{\s*tilepichue\s+(\d+)\s+(\d+)\s+(\d+)")
DOLPHIN_STAND_R = 9   # where to wait for a dolphin's refill: live 90 of 96 one-second samples at 7-9 tiles had one
#                       within 5.5 s, 52 of 75 at 10-12 (docs/NOTES.md "The Recovery Dolphin reaches 12 tiles")


def load(path: str | None = None) -> dict | None:
    """The scenario's `oxygen` block with its `facet` (SCENARIO unless `path`), None without the file or the block."""
    try:
        with open(path or SCENARIO, encoding="utf-8") as f:
            sc = json.load(f)
    except OSError:
        return None
    return {**sc["oxygen"], "facet": sc.get("facet", 0)} if sc.get("oxygen") else None


def box_of(ox: dict, pos, facet: int | None = None) -> int | None:
    """The index of the underwater box `pos` (x, y) lies in on `facet` (None: the scenario's), None on dry ground."""
    if facet is not None and facet != ox.get("facet", 0):
        return None
    return next((i for i, b in enumerate(ox["underwater"]) if b[0] <= pos[0] <= b[2] and b[1] <= pos[1] <= b[3]), None)


def bars(layout: str) -> tuple[int | None, int | None]:
    """(first, second) bar art of an 'Oxygen' gump layout."""
    by_y = {int(y): int(art) for _, y, art in BAR_RE.findall(layout or "")}
    return by_y.get(FIRST_BAR_Y), by_y.get(SECOND_BAR_Y)


def sites(ox: dict) -> list[dict]:
    """The refills: {kind "hop"|"dolphin", tile, back (a hop's way down), box}. A hop's `tile` is the teleporter up. A
    hop with a `skip` (its reason) is none: the runner never goes for it."""
    out = [{"kind": "hop", "tile": tuple(h["via"][:2]), "back": tuple(h["back"][:2]), "box": box_of(ox, h["via"])}
           for h in ox.get("hops", ()) if not h.get("skip")]
    out += [{"kind": "dolphin", "tile": tuple(d["at"][:2]), "back": None, "box": box_of(ox, d["at"])}
            for d in ox.get("dolphins", ())]
    return out


class Clock:
    """Our seconds under water since the last refill (module docstring). `update` with each new batch of events and
    our position; `elapsed` None on dry ground."""

    def __init__(self, ox: dict):
        self.ox = ox
        stage = ox["stages"][0]
        self.limit_s = float(stage["after_s"])                 # the deprivation's first hit (732 s live)
        self.floor = float(stage["above"])                     # ... hits never below this share of the max (0.66)
        self.every_s = float(ox["every_s"])                    # its hits' gap; its `warn` line came one gap earlier
        self.texts = {s["text"]: float(s["after_s"]) for s in ox["stages"] if s.get("text")}
        self.texts |= {s["warn"]: float(s["after_s"]) - self.every_s for s in ox["stages"] if s.get("warn")}
        g = ox["gump"]
        self.gump_id, self.full, self.empty, self.step_s, self.first_step_s = \
            g["id"], g["full"], g["empty"], float(g["step_s"]), float(g["first_step_s"])
        self.refill_t = None     # monotonic time of the last refill; None on dry ground
        self.refills = 0         # refills seen (dry ground after water, the dolphin's line)
        self.gump_t = None       # when the last 'Oxygen' gump came

    def bounds(self, layout: str) -> tuple[float, float] | None:
        """(lo, hi) seconds under water an 'Oxygen' gump's bars allow (scenario `gump`: the second bar's k-th step at
        (k - 1) x step_s + first_step_s, the first bar's j-th at the limit + (j - 1) x step_s), give or take BAR_SLACK_S."""
        first, second = bars(layout)
        if second is None:
            return None
        s, f0, e = self.step_s, self.first_step_s, BAR_SLACK_S
        if first is not None and first < self.full:
            j = self.full - first
            return self.limit_s + (j - 1) * s - e, self.limit_s + j * s + e
        k = self.full - max(second, self.empty)
        if k == 0:
            return 0.0, f0 + e
        hi = self.limit_s + e if second <= self.empty else k * s + f0 + e
        return (k - 1) * s + f0 - e, hi

    def update(self, pos, events, now: float, facet: int | None = None):
        if pos is None:
            return
        if box_of(self.ox, pos, facet) is None:
            if self.refill_t is not None:
                self.refills += 1
            self.refill_t = None
            return
        if self.refill_t is None:
            self.refill_t = now          # just under water, or started there (the next gump corrects it)
        for e in events:
            kind = e.get("ev")
            if kind == "speech_heard" and e.get("serial") in SYSTEM:
                text = e.get("text")
                if text == TEXT_REFILLED:
                    self.refill_t = now
                    self.refills += 1
                elif text in self.texts:
                    self.refill_t = min(self.refill_t, now - self.texts[text])
            elif kind == "gump_open" and serial_of(e.get("gump_id") or 0) == self.gump_id:
                self.gump_t = now
                b = self.bounds(e.get("layout") or "")
                if b is not None:
                    # off by less than a step: to the nearer edge; by more (a run started under water): the middle
                    lo, hi = b
                    el = now - self.refill_t
                    if el < lo or el > hi:
                        edge = lo if el < lo else hi
                        far = abs(el - edge) > self.step_s and hi != float("inf")
                        self.refill_t = now - ((lo + hi) / 2 if far else edge)

    def elapsed(self, now: float) -> float | None:
        return None if self.refill_t is None else now - self.refill_t

    def deprived(self, now: float) -> bool:
        el = self.elapsed(now)
        return el is not None and el >= self.limit_s

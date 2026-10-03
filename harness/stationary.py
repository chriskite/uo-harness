"""Outlands' "Stationary Penalty": see it, walk it off, and move before it sets in.

The debuff (Outlands buff 0xFF sub 8, icon 277, title "Stationary Penalty", "All
damage is reduced to 1. Move {value} more steps to remove this effect"; wiki Mining:
it also stops harvesting) as measured in the memory store and the timed captures
(docs/HUNT_LOOP.md "Stationary Penalty", 2026-10-04):

- **Standing still:** it comes 301.0-314.8 s after our last one-tile step (all 40
  inactivity cases), fighting and casting or not. Teleports don't reset that clock:
  the NPD entrance/exit never applied it (0/73), and an exit 29 s before the 300 s
  mark didn't delay it.
- **Login, and most other teleports** (recalls, moongates and the like 23/28, leaving
  a rental room 21/22; entering one 0/22): it comes at once.
- **Clearing:** `{value}` is the buff's first timer value (`timers[0].seconds`), not
  `f2` (always 1): 5 on arrival, re-sent 4, 3, 2, 1 on each step that changes our
  tile, removed (sub 9) on the 5th. Runs count, and a step back onto the tile just
  left counts too (live 10-02 14:00-14:01, 5535,528 -> 5535,529).

`Stationary` keeps the clock from the Mover's own counters (a step that changed our
tile; teleports excluded) and walks a short out-and-back over the Mover, which never
routes over a known teleporter tile; the out tile is never one either, and routes
over tiles we have stood on before (walk memory) are preferred.
"""
import math
import time

import nav
from agent_link import Abort, log

ICON = 277
TITLE = "Stationary Penalty"
STILL_S = 300.0         # measured: applied 301-315 s after the last one-tile step
CLEAR_STEPS = 5         # measured: the count it starts at (when the buff carries none)
MARGIN = 1              # one step more than it asks for
REPOSITION_S = 240.0    # default: reposition after this long without a step (STILL_S - 60)
LULL_FRAC = 0.8         # ...or, with nothing on us, from a draw in [LULL_FRAC, 1] of it
CLEAR_ROUNDS = 3        # out-and-back walks per clear before backing off
RETRY_S = 20.0          # a clear that didn't take: try again after this
REMOVE_WAIT_S = 1.0     # the removal (sub 9) comes with the last step's confirm (live ~0.1 s)


def steps_left(world: dict) -> int | None:
    """Steps the penalty still asks for, or None when it isn't on. Matched by its
    title, icon 277 as the fallback; the count is the buff's first timer value
    (its description's {value}), CLEAR_STEPS when it has none."""
    me = (world.get("self") or {}).get("serial")
    for icon, b in ((world.get("buffs") or {}).get(me or "", {}) or {}).items():
        if b.get("title") == TITLE or (not b.get("title") and int(icon) == ICON):
            timers = b.get("timers") or []
            secs = timers[0].get("seconds") if timers else None
            return max(1, round(secs)) if secs else CLEAR_STEPS
    return None


class Stationary:
    """The penalty and the still-clock for one runner (its Mover and Human)."""

    def __init__(self, mover, human, reposition_s: float = REPOSITION_S):
        self.mover = mover
        self.human = human
        self.reposition_s = reposition_s
        self._seen = None                # the Mover's real-step count last looked at
        self.last_step_t = time.monotonic()
        self.lull_at = None              # drawn once still >= LULL_FRAC x reposition_s, per still spell
        self.retry_t = 0.0

    def still_s(self) -> float:
        """Seconds since our last step that changed our tile (teleports don't count;
        the start of the run counts as one)."""
        n = self.mover.steps - self.mover.teleports
        if n != self._seen:
            if self._seen is not None:
                self.last_step_t = self.mover.sent_at or time.monotonic()
                self.lull_at = None
            self._seen = n
        return time.monotonic() - self.last_step_t

    def due(self, st, lull: bool):
        """('penalty', steps left) when the penalty is on, ('reposition', 2 or 4 steps)
        when we have stood still for reposition_s, or `lull` (nothing on us) and for our
        drawn share of it; else None. Neither within RETRY_S of a walk that failed."""
        left = steps_left(st["world"])
        if left is not None:
            return ("penalty", left) if time.monotonic() >= self.retry_t else None
        still = self.still_s()
        if lull and still >= LULL_FRAC * self.reposition_s and self.lull_at is None:
            # drawn only here, so a runner that never stands this long keeps its Human's
            # random stream untouched (seeded tests)
            self.lull_at = self.reposition_s * self.human.rng.uniform(LULL_FRAC, 1.0)
        if time.monotonic() >= self.retry_t and (
                still >= self.reposition_s or (lull and self.lull_at is not None and still >= self.lull_at)):
            return "reposition", 2 * self.human.choice((1, 2))
        return None

    def out_tile(self, st, home, k: int):
        """A tile k (else k + 1) steps from home to walk out to: not a known
        teleporter, not occupied, with a route. Tiles we have stood on (walk memory)
        are tried first, and one whose whole route runs over such tiles is taken at
        once; else the first with a route at all. None if there is none. (A map plan
        to a walled-off tile took ~1 s in the NPD; known tiles rarely are.)"""
        facet = st["world"]["self"].get("map")
        known = self.mover.mem_for(facet).tiles
        skip = self.mover.teleporter_tiles(facet) | self.mover.occupied(st)
        rings = []
        for r in (k, k + 1):
            ring = [(home[0] + dx, home[1] + dy) for dx in range(-r, r + 1) for dy in range(-r, r + 1)
                    if max(abs(dx), abs(dy)) == r and (home[0] + dx, home[1] + dy) not in skip]
            self.human.rng.shuffle(ring)
            rings += [(r, t) for t in ring]
        fallback = None
        for r, t in [c for c in rings if c[1] in known] + [c for c in rings if c[1] not in known]:
            if fallback is not None and t not in known:
                break
            path, _ = self.mover.plan(st, nav.within(t, 0))
            if path is None or len(path) - 1 > 2 * r + 2:
                continue
            if all(p in known for p in path[1:]):
                return t
            fallback = fallback or t
        return fallback

    def walk_off(self, home, steps: int, what: str) -> int:
        """Out to a tile ceil(steps / 2) away and back to home (Mover routes, human
        pacing); the real steps taken (0 when no out tile was found: nothing more
        for RETRY_S)."""
        st = self.mover.link.state()
        out = self.out_tile(st, tuple(home), max(1, math.ceil(steps / 2)))
        if out is None:
            log(f"{what}: no tile to walk out to around {tuple(home)}; again in {RETRY_S:.0f} s")
            self.retry_t = time.monotonic() + RETRY_S
            return 0
        n0 = self.mover.steps - self.mover.teleports
        try:
            self.mover.walk_to(lambda: out, 0, f"{what}: out")
        except Abort as e:               # the way out got cut (mobiles, a wall): come back anyway
            if "no route" not in str(e) and "cut by mobiles" not in str(e):
                raise
            log(f"{what}: {e}; going back")
        self.mover.walk_to(lambda: tuple(home), 0, f"{what}: back")
        return self.mover.steps - self.mover.teleports - n0

    def clear(self, home, what: str = "stationary penalty") -> tuple[int, bool]:
        """Walk the penalty off around home: out-and-back for the steps it asks for
        plus MARGIN, up to CLEAR_ROUNDS times. (steps taken, cleared); a clear that
        didn't take is retried after RETRY_S."""
        link, taken = self.mover.link, 0
        for _ in range(CLEAR_ROUNDS):
            left = steps_left(link.state()["world"])
            if left is None:
                break
            n = self.walk_off(home, left + MARGIN, what)
            taken += n
            if n == 0:
                break
            link.wait(lambda s: steps_left(s["world"]) is None, REMOVE_WAIT_S)
        cleared = steps_left(link.state()["world"]) is None
        if not cleared:
            self.retry_t = time.monotonic() + RETRY_S
        return taken, cleared

    def handle(self, st, home, lull: bool, doing) -> str | None:
        """Act on due() around `home`: walk the penalty off ('penalty') or reposition
        ('reposition'), logged; `doing(kind, text, target)` tells the visualizer. The
        kind acted on, else None."""
        due = self.due(st, lull)
        if due is None:
            return None
        kind, steps = due
        home = tuple(home)
        if kind == "penalty":
            log(f"Stationary Penalty: {steps} step(s) to go; walking it off around {home}")
            doing("move", f"Walking off the Stationary Penalty ({steps} steps)", home)
            taken, cleared = self.clear(home)
            log(f"Stationary Penalty {'cleared' if cleared else 'still on'} after {taken} step(s)")
        else:
            log(f"no step for {self.still_s():.0f} s: repositioning ({steps} steps) before the Stationary Penalty")
            doing("move", "Repositioning", home)
            self.walk_off(home, steps, "reposition")
        return kind

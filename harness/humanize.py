"""Human-like inefficiency for agent runners (user request 2026-09-29).

Server-side automation detection works on behaviour statistics: step and
click timing, perfectly optimal routes, identical repetitions (ANTICHEAT.md
§8.3). A `Human` gives every runner one seeded source of human texture.
Everything it adds is stock-client traffic or plain waiting.

- Timing: reaction delays are lognormal (right-skewed, like human reaction
  times), per action kind, instead of uniform ranges. A slow drift ("fatigue")
  lengthens them over a session.
- Routes: every plan gets its own random edge-cost noise, so walks pick among
  near-optimal routes instead of the one optimal path. Now and then the walker
  steps to a side tile it knows is walkable and replans from there.
- Walking rhythm: within a straight walk, steps go out at the stock client's
  held-key cadence (ClassicUO MovementSpeed: 200 ms run, 400 ms walk) plus
  frame jitter, like a player holding the key; the texture sits between
  segments: occasional short pauses and rare longer "look around" pauses.
  Routes are always run (the client's Always Run is on).
- Hands: occasional hesitation (a tool cursor cancelled and re-used), and
  small fidgets between tasks: opening the backpack, or looking at a nearby
  mobile with the stock single-click sequence.

The proxy still enforces its minimum step spacing; this layer only ever makes
the agent slower or less direct than the stock client, never faster.
Profiles: "normal" for live play, "off" for deterministic tests (no pauses,
noise, wandering or fidgets; fixed short delays).
"""
import math
import random
import time
import zlib
from dataclasses import dataclass, replace

import actions

# reaction-time medians (s) per action kind; lognormal sigma below
REACTION_MEDIAN = {
    "aim": 0.95,        # tool cursor up -> target clicked
    "menu": 1.3,        # gump open -> button clicked
    "read": 0.8,        # message shown -> next action
    "drag": 0.55,       # lift -> drop
    "use": 0.9,         # deciding to use an item
    "speak": 1.1,       # arrived -> typed speech sent
    "between": 2.2,     # between harvest attempts
}
# the stock client's step cadence for a held key, unmounted (ClassicUO MovementSpeed)
STEP_CADENCE_RUN = 0.200
STEP_CADENCE_WALK = 0.400
NOISE_CELL = 6                     # route noise granularity in tiles (cost_scale)


@dataclass(frozen=True)
class Profile:
    reaction_sigma: float = 0.38
    step_jitter: tuple = (0.003, 0.015)  # added to the stock step cadence (human bins: 200-220 ms)
    micro_pause_p: float = 0.025         # per step
    micro_pause_median: float = 1.1
    look_around_p: float = 0.004         # per step
    look_around_range: tuple = (3.0, 9.0)
    path_noise: float = 0.45             # cost x uniform(1, 1 + noise) per map cell, per plan
    wander_p: float = 0.012              # per step: sidestep to a known tile, then replan
    hesitate_p: float = 0.025            # per tool use: cancel the cursor and use again
    fidget_p: float = 0.05               # per task boundary
    fatigue_per_hour: float = 0.12       # delays grow 12 %/h of activity
    enabled: bool = True


PROFILES = {
    "normal": Profile(),
    "off": Profile(reaction_sigma=0.0, micro_pause_p=0.0,
                   look_around_p=0.0, path_noise=0.0, wander_p=0.0, hesitate_p=0.0,
                   fidget_p=0.0, fatigue_per_hour=0.0, enabled=False),
}


class Human:
    def __init__(self, profile: str | Profile = "normal", seed: int | None = None,
                 fast: float = 1.0, log=None):
        """fast < 1 scales every delay down (offline tests of the normal profile)."""
        self.p = PROFILES[profile] if isinstance(profile, str) else profile
        self.rng = random.Random(seed)
        self.fast = fast
        self.t0 = time.monotonic()
        self.log = log or (lambda msg: None)
        self.plan_salt = 0
        self.stats = {"pauses": 0, "pause_s": 0.0, "wanders": 0, "hesitations": 0,
                      "fidgets": 0}

    def _fatigue(self) -> float:
        hours = (time.monotonic() - self.t0) / 3600.0
        return 1.0 + self.p.fatigue_per_hour * hours

    def _lognormal(self, median: float, sigma: float) -> float:
        if sigma <= 0:
            return median
        return median * math.exp(self.rng.gauss(0.0, sigma))

    def reaction(self, kind: str) -> float:
        """Seconds a person takes for `kind` (see REACTION_MEDIAN)."""
        base = REACTION_MEDIAN[kind] if self.p.enabled else 0.15
        return self._lognormal(base, self.p.reaction_sigma) * self._fatigue() * self.fast

    def wait(self, kind: str):
        time.sleep(self.reaction(kind))

    def step_gap(self, run: bool) -> float:
        """Seconds from one step's send to the next along a straight walk: the stock
        client's held-key cadence (MovementSpeed.STEP_DELAY_RUN / _WALK, unmounted)
        plus frame jitter. Human captures (20260929_204225): run steps median 200 ms,
        565 of 1102 intervals in 200-220 ms. Never below the proxy floor (0.2 / 0.4 s)."""
        base = STEP_CADENCE_RUN if run else STEP_CADENCE_WALK
        if not self.p.enabled:
            return base + 0.01
        return base + self.rng.uniform(*self.p.step_jitter)

    def pace_step(self, run: bool, sent_at: float):
        """Sleep until the next step is due, `sent_at` being when the last one went out."""
        time.sleep(max(0.0, sent_at + self.step_gap(run) - time.monotonic()))

    def cost_scale(self):
        """Per-plan route noise: a deterministic cost multiplier in [1, 1+noise]
        per NOISE_CELL x NOISE_CELL block of the map (by the step's destination
        tile), different for every plan. Whole areas get cheaper or dearer, so
        routes vary at the scale of streets and clearings while staying straight
        inside a block. Per-step noise made every route zig-zag (37 % heading
        changes vs a human's 21 %, 20260930_123206); with 6-tile cells plus
        nav.straighten, Shelter routes come out at ~21 %."""
        self.plan_salt += 1
        if self.p.path_noise <= 0:
            return None
        salt, noise, seed = self.plan_salt, self.p.path_noise, self.rng.random()

        def scale(a, b):
            key = f"{seed}:{salt}:{b[0] // NOISE_CELL},{b[1] // NOISE_CELL}".encode()
            return 1.0 + noise * (zlib.crc32(key) / 0xFFFFFFFF)
        return scale

    def after_step(self):
        """Occasional pause while walking (returns seconds paused)."""
        r = self.rng.random()
        if r < self.p.look_around_p:
            d = self.rng.uniform(*self.p.look_around_range) * self.fast
        elif r < self.p.look_around_p + self.p.micro_pause_p:
            d = self._lognormal(self.p.micro_pause_median, 0.5) * self.fast
        else:
            return 0.0
        self.stats["pauses"] += 1
        self.stats["pause_s"] += d
        time.sleep(d)
        return d

    def wander(self) -> bool:
        if self.rng.random() < self.p.wander_p:
            self.stats["wanders"] += 1
            return True
        return False

    def choice(self, seq):
        return self.rng.choice(seq)

    # ---------------------------------------------------------------- hands
    def hesitate(self) -> bool:
        if self.rng.random() < self.p.hesitate_p:
            self.stats["hesitations"] += 1
            return True
        return False

    def fidget(self, link, st, backpack: int | None):
        """Now and then a harmless, stock-identical idle action between tasks."""
        if self.rng.random() >= self.p.fidget_p:
            return
        me = st["movement"]["self_serial"]
        pos = st["movement"]["pos"]
        near = []
        for key, m in st["world"]["mobiles"].items():
            s = int(key, 16) if isinstance(key, str) else int(key)
            if s != me and m.get("x") is not None and pos \
                    and max(abs(m["x"] - pos[0]), abs(m["y"] - pos[1])) <= 10:
                near.append(s)
        options = (["look"] if near else []) + (["pack"] if backpack else [])
        if not options:
            return
        what = self.rng.choice(options)
        self.stats["fidgets"] += 1
        if what == "look":
            s = self.rng.choice(near)
            self.log(f"(idle: looking at 0x{s:08X})")
            link.act(actions.single_click(s))
            link.act(actions.status_request(s))
        else:
            self.log("(idle: opening the backpack)")
            link.act(actions.dclick(backpack))
        time.sleep(self.reaction("read"))

    @staticmethod
    def with_overrides(name: str, **kw) -> Profile:
        return replace(PROFILES[name], **kw)

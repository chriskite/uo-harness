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
  steps to a side tile it knows is walkable and replans from there, and at a
  turn with a known obstacle straight ahead it sometimes misses the turn and
  runs into the obstacle (the server denies the step) before turning.
- Walking rhythm: occasional short pauses, rare longer "look around" pauses,
  and whole routes walked instead of run.
- Hands: occasional hesitation (a tool cursor cancelled and re-used), and
  small fidgets between tasks: opening the backpack, or looking at a nearby
  mobile with the stock single-click sequence.

The proxy still enforces its minimum step spacing; this layer only ever makes
the agent slower or less direct, never faster. Profiles: "normal" for live
play, "off" for deterministic tests (no pauses, noise, wandering or fidgets;
fixed short delays).
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


@dataclass(frozen=True)
class Profile:
    reaction_sigma: float = 0.38
    step_median_run: float = 0.30        # proxy floor 0.2 s
    step_median_walk: float = 0.47       # proxy floor 0.4 s
    step_sigma: float = 0.22
    walk_route_p: float = 0.07           # whole route walked instead of run
    micro_pause_p: float = 0.025         # per step
    micro_pause_median: float = 1.1
    look_around_p: float = 0.004         # per step
    look_around_range: tuple = (3.0, 9.0)
    path_noise: float = 0.45             # edge cost x uniform(1, 1 + noise), per plan
    wander_p: float = 0.012              # per step: sidestep to a known tile, then replan
    bump_p: float = 0.15                 # per turn with a known obstacle straight ahead: run into it
    hesitate_p: float = 0.025            # per tool use: cancel the cursor and use again
    fidget_p: float = 0.05               # per task boundary
    fatigue_per_hour: float = 0.12       # delays grow 12 %/h of activity
    enabled: bool = True


PROFILES = {
    "normal": Profile(),
    "off": Profile(reaction_sigma=0.0, step_sigma=0.0, walk_route_p=0.0, micro_pause_p=0.0,
                   look_around_p=0.0, path_noise=0.0, wander_p=0.0, bump_p=0.0, hesitate_p=0.0,
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
        self.stats = {"pauses": 0, "pause_s": 0.0, "wanders": 0, "bumps": 0, "hesitations": 0,
                      "fidgets": 0, "walked_routes": 0}

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

    def step_delay(self, run: bool) -> float:
        if not self.p.enabled:
            return 0.22 if run else 0.42
        med = self.p.step_median_run if run else self.p.step_median_walk
        d = self._lognormal(med, self.p.step_sigma) * self._fatigue() * self.fast
        return max(d, 0.21 if run else 0.41)

    # ---------------------------------------------------------------- walking
    def route_runs(self) -> bool:
        """Run (True) or walk this whole route."""
        if self.rng.random() < self.p.walk_route_p:
            self.stats["walked_routes"] += 1
            return False
        return True

    def cost_scale(self):
        """Per-plan edge-cost noise: a deterministic multiplier in [1, 1+noise]
        for each directed step, different for every plan."""
        self.plan_salt += 1
        if self.p.path_noise <= 0:
            return None
        salt, noise, seed = self.plan_salt, self.p.path_noise, self.rng.random()

        def scale(a, b):
            key = f"{seed}:{salt}:{a[0]},{a[1]}>{b[0]},{b[1]}".encode()
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

    def bump(self) -> bool:
        """At a turn with an obstacle straight ahead: miss the turn and run
        into it (the server denies the step), like players do."""
        if self.rng.random() < self.p.bump_p:
            self.stats["bumps"] += 1
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

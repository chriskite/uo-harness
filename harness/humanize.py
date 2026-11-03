"""Human-like inefficiency for agent runners (user request 2026-09-29).

Server-side automation detection works on behaviour statistics: step and
click timing, perfectly optimal routes, identical repetitions (ANTICHEAT.md
§8.3). A `Human` gives every runner one seeded source of human texture.
Everything it adds is stock-client traffic or plain waiting.

- Timing: reaction delays are lognormal (right-skewed, like human reaction
  times), per action kind, instead of uniform ranges. A slow drift ("fatigue")
  lengthens them over a session.
- Routes: the planners take the shortest route with the fewest corners
  (agent_link.TURN_COST; user 2026-11-02: "Humans actually don't sawtooth like
  that when we don't need to"; the per-plan route noise this module used to add
  is gone). Now and then the walker steps to a side tile it knows is walkable
  and replans from there.
- Walking rhythm: within a straight walk, steps go out at exactly the stock
  client's held-key cadence (ClassicUO MovementSpeed: 200 ms run, 400 ms walk,
  half mounted; the client was measured at a flat 100 ms mounted), like a player
  holding the key; the texture sits between segments: occasional short pauses and
  rare longer "look around" pauses. Routes are always run (the client's Always
  Run is on).
- Hands: occasional hesitation (a tool cursor cancelled and re-used).
- The harvest cycle runs at a Razor script's pace, not a person's (user decision
  2026-10-04: every harvester on the shard runs one): SCRIPT_MEDIAN, no fatigue,
  tight jitter. Jaseowns' lumber and mining scripts send "Use item in hand" ~0.2 s
  after each harvest reply and target themselves as soon as the cursor is up
  (ANTICHEAT.md §3).

The proxy still enforces its minimum step spacing; this layer only ever makes
the agent slower or less direct than the stock client, never faster.
Profiles: "normal" for live play, "off" for deterministic tests (no pauses or
wandering; fixed short delays).

Every pause (reaction waits, walking pauses) goes through
`Human.sleep(seconds, kind)`: time.sleep unless the runner passes its own, e.g.
one that keeps reading the state and checking for threats while it waits
(loop_lumber.LumberLoop.pause). The step cadence (pace_step) stays a plain sleep.
"""
import math
import random
import time
from dataclasses import dataclass, replace

# reaction-time medians (s) per action kind; lognormal sigma below
REACTION_MEDIAN = {
    "aim": 0.95,        # tool cursor up -> target clicked
    "menu": 1.3,        # gump open -> button clicked
    "read": 0.8,        # message shown -> next action
    "drag": 0.55,       # lift -> drop
    "use": 0.9,         # deciding to use an item
    "find": 1.2,        # container gump opened -> item found and grabbed (demo 204225: 2.7 s, one sample)
    "speak": 1.1,       # arrived -> typed speech sent
    "between": 2.2,     # between steps of a task (conversions, errands, a mover's retry); not the harvest cycle
    "captcha": 11.5,    # captcha shown -> answer submitted (measured human solves: 7.7-17.5 s)
}
# the harvest cycle at a Razor script's pace (module docstring): the harvest reply -> the tool's
# double-click, and its cursor -> the target. No fatigue; SCRIPT_SIGMA instead of the profile's.
SCRIPT_MEDIAN = {
    "chop_use": 0.2,    # "Use item in hand" ~0.2 s after the harvest reply (Jaseowns' scripts)
    "chop_aim": 0.1,    # waitfortarget -> "Target Self" on the script's next tick [INFERENCE: ~0.1 s]
    "dig_use": 0.2,     # the pickaxe's double-click after the last dig's reply (loop_mining; no cursor to answer)
    "smelt_use": 0.65,  # between ore stacks double-clicked at a forge (Jaseowns' mining script: 650 ms)
    # the user's escape macro through a moongate (2026-10-15: "My macro gets me out basically instantly"): the
    # gate's double-click and its menu's buttons at a macro's pace, not a reader's (live 21:57:21-25.4: 4.2 s)
    "gate_macro": 0.15,
    # what players run off Razor hotkeys in a fight (user 2026-10-17: "we shouldn't delay things that players
    # usually use a macro for"): pet commands (Razor "Say" hotkeys, 56 of 56 in Logan's captures), skills, potions
    # ("I use a Razor hotkey for 'Drink Heal Potion'"), vet supplies, war mode, the rope. The press once the need
    # shows [INFERENCE: ~0.25 s, no capture can time a decision]; the target cursor answered at his measured pace
    # (20261012_210013: cursor -> answer median 0.38 s, n 69; 20261017_183126 by hand 0.26 s, n 106; the
    # runner's person-paced "aim" had 1.08 s, n 59)
    "hotkey": 0.25,
    "hotkey_aim": 0.3,
}
SCRIPT_SIGMA = 0.2
# the stock client's step cadence for a held key (ClassicUO MovementSpeed): on foot, and mounted
# (user ride 20261002_153718: 53 mounted run steps, median 0.100 s)
STEP_CADENCE_RUN = 0.200
STEP_CADENCE_WALK = 0.400
STEP_CADENCE_RUN_MOUNTED = 0.100
STEP_CADENCE_WALK_MOUNTED = 0.200


@dataclass(frozen=True)
class Profile:
    reaction_sigma: float = 0.38
    micro_pause_p: float = 0.00025       # per step (0.025 until 2026-11-02, user: "the goto walk has soooo many pauses")
    micro_pause_median: float = 1.1
    look_around_p: float = 0.00004       # per step (0.004 until 2026-11-02)
    look_around_range: tuple = (3.0, 9.0)
    wander_p: float = 0.00012            # per step: sidestep to a known tile, then replan (0.012 until 2026-11-02)
    hesitate_p: float = 0.025            # per tool use (not in the harvest cycle): cancel the cursor and use again
    fatigue_per_hour: float = 0.12       # delays grow 12 %/h of activity
    enabled: bool = True


PROFILES = {
    "normal": Profile(),
    "off": Profile(reaction_sigma=0.0, micro_pause_p=0.0,
                   look_around_p=0.0, wander_p=0.0, hesitate_p=0.0,
                   fatigue_per_hour=0.0, enabled=False),
}


class Human:
    def __init__(self, profile: str | Profile = "normal", seed: int | None = None,
                 fast: float = 1.0, log=None, sleep=None):
        """fast < 1 scales every delay down (offline tests of the normal profile).
        sleep(seconds, kind): how a pause is spent (default: time.sleep); kind is the
        REACTION_MEDIAN or SCRIPT_MEDIAN key, or "walk_pause" (after_step)."""
        self.p = PROFILES[profile] if isinstance(profile, str) else profile
        self.rng = random.Random(seed)
        self.fast = fast
        self.t0 = time.monotonic()
        self.log = log or (lambda msg: None)
        self.sleep = sleep or (lambda seconds, kind: time.sleep(seconds))
        self.stats = {"pauses": 0, "pause_s": 0.0, "wanders": 0, "hesitations": 0}

    def _fatigue(self) -> float:
        hours = (time.monotonic() - self.t0) / 3600.0
        return 1.0 + self.p.fatigue_per_hour * hours

    def _lognormal(self, median: float, sigma: float) -> float:
        if sigma <= 0:
            return median
        return median * math.exp(self.rng.gauss(0.0, sigma))

    def reaction(self, kind: str) -> float:
        """Seconds a person takes for `kind` (see REACTION_MEDIAN), or a script for a
        SCRIPT_MEDIAN kind (no fatigue, SCRIPT_SIGMA)."""
        if kind in SCRIPT_MEDIAN:
            if not self.p.enabled:
                return 0.05 * self.fast
            return self._lognormal(SCRIPT_MEDIAN[kind], SCRIPT_SIGMA) * self.fast
        base = REACTION_MEDIAN[kind] if self.p.enabled else 0.15
        return self._lognormal(base, self.p.reaction_sigma) * self._fatigue() * self.fast

    def wait(self, kind: str):
        self.sleep(self.reaction(kind), kind)

    def step_gap(self, run: bool, mounted: bool = False) -> float:
        """Seconds from one step's send to the next along a straight walk: the stock
        client's held-key cadence (MovementSpeed.STEP_DELAY_RUN / _WALK; half of it
        mounted), flat. Measured: the client mounted (20261002_153718) median 100 ms, p10
        97, p90 104; Bert, a mounted PK, 0.100 s median (session_20261102_174945). The
        3-15 ms jitter this added until 2026-11-02 made Dan 10 % slower than Bert in the
        flight that killed him (user: "the real client was measured to take exactly 100ms
        per step"). The proxy floor is the same (0.2 / 0.4 s, mounted 0.1 / 0.2 s); a send
        is timed from its OK, after the proxy stamped it, so it never lands under it."""
        if mounted:
            return STEP_CADENCE_RUN_MOUNTED if run else STEP_CADENCE_WALK_MOUNTED
        return STEP_CADENCE_RUN if run else STEP_CADENCE_WALK

    def pace_step(self, run: bool, sent_at: float, mounted: bool = False):
        """Sleep until the next step is due, `sent_at` being when the last one went out."""
        time.sleep(max(0.0, sent_at + self.step_gap(run, mounted) - time.monotonic()))

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
        self.sleep(d, "walk_pause")
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

    @staticmethod
    def with_overrides(name: str, **kw) -> Profile:
        return replace(PROFILES[name], **kw)

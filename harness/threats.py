"""Threat assessment over one proxy state-port response (pure, no I/O).

    assess(state, *, recall_s, margin_s, now=None, params=Params(),
           hits_history=(), first_seen=None) -> Assessment
    Watch(params).update(state, *, recall_s, margin_s, now=None) -> Assessment
        (the same, keeping the self-hits history and first sightings
        between calls)

`state` is a state-port response: `movement.pos`, `world.self`,
`world.mobiles`, `world.items`, `world.labels`, `events` (envelopes
`{seq, t, origin, data: {ev, ...}}`; bare `{ev, ...}` dicts work too).

Notoriety (byte from S2C 0x20, stored raw by harness/world/runtime.py
_h_update_player; the self copy also from 0x22): ClassicUO
Game/Data/NotorietyFlag.cs:9-16 and its hue/HTML-colour switch at :21-60:

    0 unknown     1 innocent (blue)   2 ally (green)     3 gray (attackable)
    4 criminal (gray)   5 enemy (orange)   6 murderer (red)
    7 invulnerable (yellow: vendors, bankers, guards)

Kinds: `red` (6), `grey` (3, 4), `orange` (5), `blue` (1, 2), `npc`,
`monster` (any non-human body: animals too), `ghost`, `unknown` (0 or none).

Player vs NPC (heuristic where marked). Evidence, strongest first:
  - notoriety 7 -> npc. Across the 27 captures, all 47 notoriety-7 mobiles
    were human-bodied "Name the <title>" vendors, bankers or guards.
  - body: ClassicUO Mobile.IsHuman (GameObjects/Mobile.cs:153-166) lists the
    human bodies (0x190-0x193, 0xB7-0xBA, 0x25D-0x260, ...). Anything else
    is a creature (`monster`) unless the flag below says player.
  - [HEURISTIC] mobile flags bit 0x20. ClassicUO calls it `Movable`
    (Game/Data/EntityFlags.cs:17). Of the 96 mobiles seen in 0x20 packets
    across the 27 captures, it was set on all 9 players (flags 0x20/0x22/0x60)
    and on none of the 87 NPCs or creatures. So a set bit counts as "player",
    which also catches a polymorphed player with a non-human body.
  - [HEURISTIC] label (S2C 0x1C type 6, `world.labels`): a "(Young)" suffix
    means player. "Name the <title>" ("Len the banker", "Riane the battle
    trainer", notoriety 3) means npc when the 0x20 bit is clear. "a ..." or
    "an ..." means creature.
  - A human body with no other evidence is taken as a player: the
    conservative reading.
  - Label grace (Params.label_grace_s, 1 s): the click label lags the first
    0x20 by ~60 ms (the client asks with 0x09/0x98 on sight). In that gap a
    gray battle trainer (notoriety 3, war mode while sparring) reads as a
    hostile grey player: juncture 44, 2026-10-01, docs/NOTES.md. So a hostile
    assumed player with no label gets `watch` until it has been in view
    label_grace_s (first sighting from `first_seen`, which Watch keeps). Then
    the conservative reading applies. Plain assess() calls without
    first_seen get no grace.

Ghost bodies: ClassicUO Mobile.IsDead (Mobile.cs:132-140): 0x192, 0x193,
0x25F, 0x260, 0x2B6, 0x2B7. Ghosts can't harm us, so they are ignored.
Mounted: the mobile has an item on layer 0x19 (Layers.cs:32; captured mounts
0x3E9F and 0x3EA6).

Movement and ETA (all in Params):
  run_s_per_tile      0.2  ClassicUO STEP_DELAY_RUN 200 ms (MovementSpeed.cs:11)
  mounted_s_per_tile  0.1  STEP_DELAY_MOUNT_RUN 100 ms (MovementSpeed.cs:9)
  monster_s_per_tile  0.4  [INFERENCE] the on-foot walk delay
                           (STEP_DELAY_WALK, :12). Creature speed on Outlands
                           is unsourced.
  player_strike_range 12   [INFERENCE] the RunUO pre-ML spell range. Archers
                           are similar. The Outlands value is unsourced.
  monster_strike_range 1   melee. Ranged creatures need a larger value.
  eta_s = max(0, distance - strike_range) * s_per_tile. The Chebyshev tile
  distance is the UO move metric, since a diagonal step costs one step.
  Running is assumed: the world model drops the 0x77 run bit.

Flee radius: flee_radius = strike_range + floor((recall_s + margin_s) /
s_per_tile). A hostile mobile inside it, i.e. with eta_s <= recall_s +
margin_s, gets action `flee`. For example, recall 4 s + margin 1 s against a
red on foot gives 12 + 25 = 37 tiles. That is beyond the ~18-tile update range
([INFERENCE]; docs/LUMBER_LOOP.md §11), so any visible red means flee.

Hostile means kind red/grey/orange (Params.hostile_kinds), or a monster judged
aggressive. [INFERENCE] Creature notoriety doesn't show aggression: captured
blood apes were 1 (innocent), sheep and zombies were both 3. So aggression
comes, in order, from: war-mode flag 0x40 (EntityFlags.cs:18), `aggressive_bodies`,
`passive_bodies`/`passive_names`, notoriety 6 (`aggressive_notoriety`), then
`monster_default_aggressive`. The default is False (2026-09-30): a creature that
goes for you enters war mode. In the NPD capture 20260930_110946, 10 of 15
mongbats (the ones that fought) showed 0x40, while sheep, giant rats, hinds,
an eagle, a great hart, zombies and a harpy never did and never attacked.
Treating every unknown creature as dangerous stopped lumber trips for a
wandering goat and a walrus. The HP-damage guard still catches anything the
flag misses.

Other actions:
  - non-hostile players (blue) within watch_radius: `watch`. They could be
    thieves; harness/ledger.py catches actual theft.
  - npcs, ghosts, passive creatures: `ignore`
  - mobiles farther than max_range (32): `ignore`. The world model doesn't
    prune mobiles that leave range. A replay of capture 20260928_164548 ends
    with 28 mobiles (27 NPCs, 1 seagull) held 35-74 tiles away. The cutoff
    value is [INFERENCE].

under_attack: any of
  - self hits dropped by >= damage_threshold within damage_window_s
    (hits_history samples plus the current value)
  - an S2C 0x0B `damage` event on self within the window
  - a 0x2F `swing` event with self as defender within the window
Assessment.action is `flee` if any threat says flee, or if under_attack and
Params.flee_on_attack.
"""
from __future__ import annotations

import math
import re
import time
from dataclasses import asdict, dataclass, field

from world.state import GHOST_BODIES  # ClassicUO Mobile.IsDead

NOTORIETY = {0: "unknown", 1: "innocent", 2: "ally", 3: "gray", 4: "criminal",
             5: "enemy", 6: "murderer", 7: "invulnerable"}
KIND_BY_NOTORIETY = {1: "blue", 2: "blue", 3: "grey", 4: "grey", 5: "orange",
                     6: "red", 7: "npc"}

# ClassicUO Mobile.IsHuman (GameObjects/Mobile.cs:153-166)
HUMAN_BODIES = frozenset([*range(0x190, 0x194), *range(0xB7, 0xBB),
                          *range(0x25D, 0x261), 0x29A, 0x29B, 0x2B6, 0x2B7,
                          0x3DB, 0x3DF, 0x3E2, 0x2E8, 0x2E9, 0x4E5])
LAYER_MOUNT = 0x19               # ClassicUO Game/Data/Layers.cs:32
FLAG_PLAYER_HINT = 0x20          # EntityFlags.Movable; players only in captures
FLAG_WARMODE = 0x40              # EntityFlags.WarMode (EntityFlags.cs:18)

_TITLE = re.compile(r"^\S.* the [A-Za-z][A-Za-z' -]*$")
ASSUMED_PLAYER = "human body, no npc evidence (assumed player)"
_CREATURE = re.compile(r"^(a|an) ", re.IGNORECASE)
_YOUNG = re.compile(r"\(Young\)\s*$")

ACTIONS = ("flee", "watch", "ignore")


@dataclass(frozen=True)
class Params:
    run_s_per_tile: float = 0.2          # MovementSpeed.cs:11
    mounted_s_per_tile: float = 0.1      # MovementSpeed.cs:9
    monster_s_per_tile: float = 0.4      # [INFERENCE]
    player_strike_range: int = 12        # [INFERENCE] RunUO pre-ML spell range
    monster_strike_range: int = 1        # melee
    watch_radius: int = 18               # [INFERENCE] ~ the update range
    max_range: int = 32                  # [INFERENCE] beyond = stale entry
    hostile_kinds: frozenset = frozenset({"red", "grey", "orange"})
    # creature aggression (see module docstring)
    aggressive_bodies: frozenset = frozenset()
    # [INFERENCE] passive in stock UO, captured on Outlands: sheep 0xCF,
    # hind 0xED, great hart 0xEA, eagle/seagull 0x05, magpie/crow 0x06;
    # observed harmless near lumber trips (2026-09-30): goat 0xD1, walrus 0xDD
    passive_bodies: frozenset = frozenset({0xCF, 0xED, 0xEA, 0x05, 0x06, 0xD1, 0xDD})
    passive_names: frozenset = frozenset()   # lower-case labels, e.g. "a sheep"
    aggressive_notoriety: frozenset = frozenset({6})
    monster_default_aggressive: bool = False
    # an unlabeled human that would be hostile only by the assumed-player
    # reading is watched this long after first sight (see module docstring)
    label_grace_s: float = 1.0
    # self damage
    damage_window_s: float = 10.0
    damage_threshold: int = 1
    flee_on_attack: bool = True


@dataclass
class Threat:
    serial: int
    name: str | None
    body: int | None
    notoriety: int | None
    kind: str
    player: bool | None          # None = no opinion (creatures)
    evidence: list = field(default_factory=list)   # why player/npc/creature
    mounted: bool = False
    aggressive: bool | None = None                 # creatures only
    hostile: bool = False
    distance: int = 0
    s_per_tile: float = 0.0
    strike_range: int = 0
    eta_s: float = 0.0
    flee_radius: int = 0
    action: str = "ignore"
    reason: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class Assessment:
    t: float
    pos: tuple | None
    action: str                  # flee | watch | ignore (the worst over all)
    threats: list                # [Threat], flee first, then by eta
    under_attack: bool
    damage: dict                 # {hits, hits_max, lost, window_s, damage_events, swings}
    dead: bool                   # self body is a ghost
    reasons: list

    @property
    def flee(self):
        return [t for t in self.threats if t.action == "flee"]

    @property
    def watch(self):
        return [t for t in self.threats if t.action == "watch"]

    def to_dict(self):
        return {"t": self.t, "pos": list(self.pos) if self.pos else None,
                "action": self.action, "under_attack": self.under_attack,
                "damage": self.damage, "dead": self.dead,
                "reasons": list(self.reasons),
                "threats": [t.to_dict() for t in self.threats]}


def _serial(v) -> int:
    return int(v, 16) if isinstance(v, str) else int(v)


def cheb(a, b) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def flee_radius(s_per_tile: float, strike_range: int, recall_s: float,
                margin_s: float) -> int:
    """Tiles within which a hostile reaches striking range before a recall
    (recall_s + margin_s) completes."""
    return strike_range + int(math.floor((recall_s + margin_s) / s_per_tile))


def self_pos(state):
    mv = (state.get("movement") or {}).get("pos")
    if mv:
        return tuple(mv[:2])
    me = (state.get("world") or {}).get("self") or {}
    if me.get("position_absolute"):
        return (me.get("x"), me.get("y"))
    return None


def self_serial(state):
    s = (state.get("movement") or {}).get("self_serial")
    if s is None:
        s = ((state.get("world") or {}).get("self") or {}).get("serial")
    return None if s is None else _serial(s)


def self_body(state):
    me = (state.get("world") or {}).get("self") or {}
    return (me.get("stats") or {}).get("graphic")


def is_dead(state) -> bool:
    return self_body(state) in GHOST_BODIES


def _events(state):
    for env in state.get("events") or ():
        data = env.get("data") if "data" in env and isinstance(env.get("data"), dict) else env
        yield env.get("t"), data


def _mounted_serials(world):
    out = set()
    for it in (world.get("items") or {}).values():
        if it.get("layer") == LAYER_MOUNT and it.get("container") is not None:
            out.add(_serial(it["container"]))
    return out


def identify(mob: dict, label: str | None) -> tuple[str, bool | None, list]:
    """(kind, player?, evidence) for one mobile dict from world.mobiles."""
    body = mob.get("graphic")
    noto = mob.get("notoriety")
    flags = mob.get("flags") or 0
    text = label or mob.get("name") or ""
    ev = []
    if body in GHOST_BODIES:
        return "ghost", None, ["ghost body"]
    if noto == 7:
        return "npc", False, ["notoriety 7 (invulnerable)"]
    player_flag = bool(flags & FLAG_PLAYER_HINT)
    young = bool(_YOUNG.search(text))
    human = body in HUMAN_BODIES
    if player_flag:
        ev.append("flags 0x20 (heuristic: players only in captures)")
    if young:
        ev.append("label (Young)")
    if player_flag or young:
        return KIND_BY_NOTORIETY.get(noto, "unknown"), True, ev
    if text and _CREATURE.match(text) and not human:
        return "monster", None, [f"creature label {text!r}"]
    if not human:
        return "monster", None, [f"non-human body 0x{body:X}" if body is not None
                                 else "body unknown"]
    if text and _TITLE.match(text):
        return "npc", False, [f"title label {text!r} (heuristic)"]
    return KIND_BY_NOTORIETY.get(noto, "unknown"), True, [ASSUMED_PLAYER]


def _aggressive(mob, text, params: Params) -> tuple[bool, str]:
    body, noto, flags = mob.get("graphic"), mob.get("notoriety"), mob.get("flags") or 0
    if flags & FLAG_WARMODE:
        return True, "war mode"
    if body in params.aggressive_bodies:
        return True, "aggressive body"
    if body in params.passive_bodies:
        return False, "passive body"
    if text and text.lower() in params.passive_names:
        return False, "passive name"
    if noto in params.aggressive_notoriety:
        return True, f"notoriety {noto}"
    return params.monster_default_aggressive, "default"


def damage_signal(state, *, now: float, params: Params, hits_history=()):
    """(under_attack, detail) from the self hits trend and damage/swing events."""
    me = (state.get("world") or {}).get("self") or {}
    hits, hits_max = me.get("hits"), me.get("hits_max")
    me_serial = self_serial(state)
    lo = now - params.damage_window_s
    samples = [h for t, h in hits_history if t >= lo and h is not None]
    peak = max(samples) if samples else hits
    lost = max(0, peak - hits) if (peak is not None and hits is not None) else 0
    dmg = swings = 0
    for t, ev in _events(state):
        if t is not None and t < lo:
            continue
        kind = ev.get("ev")
        if kind == "damage" and me_serial is not None and ev.get("serial") == me_serial:
            dmg += 1
        elif kind == "swing" and me_serial is not None and ev.get("defender") == me_serial:
            swings += 1
    under = lost >= params.damage_threshold or dmg > 0 or swings > 0
    return under, {"hits": hits, "hits_max": hits_max, "lost": lost,
                   "window_s": params.damage_window_s,
                   "damage_events": dmg, "swings": swings}


def assess(state: dict, *, recall_s: float, margin_s: float, now: float | None = None,
           params: Params = Params(), hits_history=(), first_seen=None) -> Assessment:
    """first_seen: {serial: time first in view} for the label grace (Watch)."""
    now = time.time() if now is None else now
    world = state.get("world") or {}
    labels = world.get("labels") or {}
    me = self_serial(state)
    pos = self_pos(state)
    mounted = _mounted_serials(world)
    budget = recall_s + margin_s
    threats = []
    for key, mob in (world.get("mobiles") or {}).items():
        serial = _serial(key)
        if serial == me or mob.get("x") is None or mob.get("y") is None:
            continue
        label = labels.get(key) or labels.get(f"0x{serial:08X}")
        kind, player, evidence = identify(mob, label)
        th = Threat(serial=serial, name=label or mob.get("name"), body=mob.get("graphic"),
                    notoriety=mob.get("notoriety"), kind=kind, player=player,
                    evidence=evidence, mounted=serial in mounted)
        if kind == "monster":
            th.s_per_tile = params.monster_s_per_tile
            th.strike_range = params.monster_strike_range
            th.aggressive, why = _aggressive(mob, th.name, params)
            th.evidence.append(f"aggressive={th.aggressive} ({why})")
            th.hostile = th.aggressive
        else:
            th.s_per_tile = params.mounted_s_per_tile if th.mounted else params.run_s_per_tile
            th.strike_range = params.player_strike_range
            th.hostile = kind in params.hostile_kinds
        th.flee_radius = flee_radius(th.s_per_tile, th.strike_range, recall_s, margin_s)
        if pos is None:
            th.distance, th.eta_s = -1, 0.0
            th.action = "watch" if th.hostile else "ignore"
            th.reason = "own position unknown"
            threats.append(th)
            continue
        th.distance = cheb(pos, (mob["x"], mob["y"]))
        th.eta_s = round(max(0, th.distance - th.strike_range) * th.s_per_tile, 3)
        if th.distance > params.max_range:
            th.action, th.reason = "ignore", f"beyond max_range {params.max_range} (stale?)"
        elif kind in ("npc", "ghost"):
            th.action, th.reason = "ignore", kind
        elif (th.hostile and label is None and ASSUMED_PLAYER in evidence
              and first_seen is not None
              and now - first_seen.get(serial, now) < params.label_grace_s):
            th.action = "watch"
            th.reason = (f"{kind} unlabeled, in view {now - first_seen.get(serial, now):.2f}s;"
                         f" awaiting label (grace {params.label_grace_s:.1f}s)")
        elif th.hostile and th.eta_s <= budget:
            th.action = "flee"
            th.reason = f"{kind} eta {th.eta_s:.1f}s <= recall {recall_s:.1f}s + margin {margin_s:.1f}s"
        elif th.hostile:
            th.action = "watch"
            th.reason = f"{kind} eta {th.eta_s:.1f}s > {budget:.1f}s"
        elif kind == "monster":
            th.action, th.reason = "ignore", "passive creature"
        elif th.distance <= params.watch_radius:
            th.action, th.reason = "watch", f"{kind} player within {params.watch_radius}"
        else:
            th.action, th.reason = "ignore", f"{kind} beyond watch radius"
        threats.append(th)
    order = {a: i for i, a in enumerate(ACTIONS)}
    threats.sort(key=lambda t: (order[t.action], t.eta_s, t.distance))
    under, damage = damage_signal(state, now=now, params=params, hits_history=hits_history)
    reasons = [f"{t.kind} 0x{t.serial:08X} {t.name or ''}: {t.reason}".replace("  ", " ")
               for t in threats if t.action == "flee"]
    action = "flee" if reasons else ("watch" if any(t.action == "watch" for t in threats)
                                     else "ignore")
    if under:
        reasons.append(f"under attack: lost {damage['lost']} hits, "
                       f"{damage['damage_events']} damage / {damage['swings']} swing events")
        if params.flee_on_attack:
            action = "flee"
        elif action == "ignore":
            action = "watch"
    return Assessment(t=now, pos=pos, action=action, threats=threats, under_attack=under,
                      damage=damage, dead=is_dead(state), reasons=reasons)


class Watch:
    """assess() plus the self-hits history and first sightings across calls
    (for under_attack and the label grace)."""

    def __init__(self, params: Params = Params()):
        self.params = params
        self.hits: list[tuple[float, int]] = []
        self.first_seen: dict[int, float] = {}

    def update(self, state, *, recall_s: float, margin_s: float,
               now: float | None = None) -> Assessment:
        now = time.time() if now is None else now
        present = {_serial(k) for k in (state.get("world") or {}).get("mobiles") or ()}
        self.first_seen = {s: self.first_seen.get(s, now) for s in present}
        a = assess(state, recall_s=recall_s, margin_s=margin_s, now=now,
                   params=self.params, hits_history=self.hits,
                   first_seen=self.first_seen)
        hits = ((state.get("world") or {}).get("self") or {}).get("hits")
        if hits is not None:
            self.hits.append((now, hits))
        lo = now - self.params.damage_window_s
        self.hits = [s for s in self.hits if s[0] >= lo]
        return a

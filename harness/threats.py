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
  monster_strike_range 1   melee. Ranged creatures: see Reach below.
  eta_s = max(0, distance - strike_range) * s_per_tile. The Chebyshev tile
  distance is the UO move metric, since a diagonal step costs one step.
  Running is assumed: the world model drops the 0x77 run bit.

Reach (Threat.reach, creature_reach; 2026-10-04): the tiles from which a
creature can damage us. Melee: monster_strike_range. Ranged or caster
creatures: CREATURE_SPELL_RANGE, 12 tiles (user decision 2026-10-04: "spell
range is 12 tiles"), raised (never lowered) to the farthest distance a hit from
that body was recorded at (Params.body_reach, learned from `monster_hit` job
events by travel_guard.learned_params). Ranged bodies: RANGED_BODIES (the
gazer, body 22: live 2026-10-04 it hit us 4 s after a melee-sized walk-away
left us 11-12 tiles from it) plus every body that hit us with no creature
adjacent. Reach doesn't change the flee radius or the action below; the lumber
runner uses it for how far to walk away and which trees to leave alone.

Flee radius: flee_radius = strike_range + floor((recall_s + margin_s) /
s_per_tile). A hostile mobile inside it, i.e. with eta_s <= recall_s +
margin_s, gets action `flee`. For example, recall 4 s + margin 1 s against a
red on foot gives 12 + 25 = 37 tiles. That is beyond the ~18-tile update range
([INFERENCE]; docs/LUMBER_LOOP.md §11), so any visible red means flee.

Hostile means kind red/grey/orange (Params.hostile_kinds), or a monster judged
aggressive. [INFERENCE] Creature notoriety doesn't show aggression: captured
blood apes were 1 (innocent), sheep and zombies were both 3. So aggression
comes, in order, from (_aggressive):
  1. swinging at us (S2C 0x2F with us as defender within damage_window_s, in
     world.swings or a `swing` event): aggressive, whatever else holds
  2. a pet (Mobile.pet: the server's "(tame)" / "(bonded)" / "(summoned)" line
     under its click label): aggressive only with notoriety 6
     (`aggressive_notoriety`; a pet's notoriety is its owner's [INFERENCE:
     RunUO]), else not, war mode and body notwithstanding
  3. war-mode flag 0x40 (EntityFlags.cs:18), unless the body is in
     `passive_bodies` or the label in `passive_names`
  4. `aggressive_bodies`, then `passive_bodies`/`passive_names`, notoriety 6,
     then `monster_default_aggressive`
The default is False (2026-09-30): a creature that goes for you enters war
mode. In the NPD capture 20260930_110946, 10 of 15 mongbats (the ones that
fought) showed 0x40, while sheep, giant rats, hinds, an eagle, a great hart,
zombies and a harpy never did and never attacked. Treating every unknown
creature as dangerous stopped lumber trips for a wandering goat and a walrus.
The HP-damage guard still catches anything the flag misses.

Pets and war-mode passive bodies (2026-10-03, session 20261003_111419 on
Shelter Island): a guarded goto avoided 'a phoenix' (body 832) and 'a
gravebug' (387), stacked one tile from the player Lord Arlabunakti, both
notoriety 1, flags 0x40 from their first 0x20, each announced "(bonded)"; and
two sheep (body 207, passive) whose 0x20 turned 0x40 while 'Billiam Gatherer
(Young)' (flags 0x60, war mode) stood next to them, killing them [INFERENCE:
both became "a sheep corpse" within 31 s and 17 s]. War mode was the only
aggression evidence for all four (no learned bodies yet, notoriety 1 and 3),
none came for us, and the sheep made a 44-step walk 142. The pet line is a
steady signal: the memory store holds 681 "(bonded)", 573 "(tame)" and 116
"(summoned)" lines, always type 0, hue 946.

Swings seen live: every one of the 2172 0x2F swings in the memory store
(sessions 20260929-20261003) is our own; Outlands has never sent one with us as
the defender, nor any 0x0B damage packet (damage shows as "-N" overhead text).
So rule 1 and the swing paths below fire only on servers that send them (and
in the test sims); live, the hits trend is the damage signal.

Fighting someone else (2026-10-01, knowledge #89): a creature whose only
aggression evidence is war mode is `watch`, not `flee`, while it is busy with
another mobile. That takes all of: its latest S2C 0x2F swing in `world.swings`
({attacker_hex: {defender, t}}, the world model's latest swing per attacker)
is at someone other than us and no older than damage_window_s; no `swing`
event in the window has it swinging at us; and it is not within its strike
range of us (eta_s > 0). Live, the lumber runner stopped for 'a great hart'
and 'an eagle' in war mode 8 tiles away that were fighting other players and
never touched Hackworth. A creature swinging at us, a stale fight, or one in
striking range keeps the flee path, and damage to us is under_attack as
before. Captures older than world.swings have no such key: read as no swings.

Other actions:
  - non-hostile players (blue) within watch_radius: `watch`. They could be
    thieves; harness/ledger.py catches actual theft.
  - npcs, ghosts, passive creatures: `ignore`
  - mobiles farther than max_range (32): `ignore`. Kept for captures from
    before the world model pruned mobiles beyond 24 tiles (2026-10-01); a
    replay of capture 20260928_164548 with the old model ended with 28
    mobiles held 35-74 tiles away.

under_attack: any of
  - self hits dropped by >= damage_threshold within damage_window_s
    (hits_history samples plus the current value)
  - an S2C 0x0B `damage` event on self within the window
  - a 0x2F `swing` event with self as defender within the window
Assessment.action is `flee` if any threat says flee, or if under_attack and
Params.flee_on_attack. Watch.acknowledge() marks the damage so far as dealt with
(the lumber runner walked away from it): from then on only new drops, damage
and swings count.

Who hit us (hit_attackers): Outlands names no attacker (no 0x2F at us, no
0x0B), so on damage the attackers are inferred from the creatures in view:
the ones swinging at us, else those in melee range, else (nothing adjacent:
the hit came from afar, so it was ranged) those within their reach, at least
CREATURE_SPELL_RANGE. Candidates are hostile creatures and creatures of unknown
aggression (`default`); pets and passive bodies/names never are.
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
CREATURE_SPELL_RANGE = 12       # tiles: a ranged/caster creature's reach (user decision 2026-10-04)
RANGED_BODIES = frozenset({22})  # gazer (live 2026-10-04: hit us from 11-12 tiles, LUMBER_LOOP.md §13)


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
    # creature reach (module docstring "Reach"): ranged bodies, and the farthest
    # tiles a hit from a body came from ((body, tiles), ...; learned)
    ranged_bodies: frozenset = RANGED_BODIES
    body_reach: tuple = ()
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
    aggression: str = ""                           # creatures: why (aggressive) or not, _aggressive's order
    hostile: bool = False
    distance: int = 0
    s_per_tile: float = 0.0
    strike_range: int = 0
    eta_s: float = 0.0
    flee_radius: int = 0
    reach: int = 0                                 # creatures: tiles from which it can hit us (creature_reach)
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


def creature_reach(body, params: Params, ranged: bool = False) -> int:
    """Tiles from which a creature can damage us (module docstring "Reach"):
    melee monster_strike_range; a ranged body (or `ranged`: it hit us from afar)
    CREATURE_SPELL_RANGE; never below the farthest hit learned for its body."""
    far = dict(params.body_reach).get(body, 0)
    ranged = ranged or body in params.ranged_bodies or far > params.monster_strike_range
    return max(CREATURE_SPELL_RANGE if ranged else params.monster_strike_range, far)


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


def swinging_at_us(serial: int, key: str, swings: dict, state, *, me, now: float, params: Params) -> bool:
    """A 0x2F with us as defender within damage_window_s: the latest swing in
    world.swings or any `swing` event."""
    if me is None:
        return False
    lo = now - params.damage_window_s
    last = swings.get(key)
    if last and _serial(last["defender"]) == me and last.get("t") is not None and last["t"] >= lo:
        return True
    return any(ev.get("ev") == "swing" and ev.get("attacker") == serial and ev.get("defender") == me
               and (t is None or t >= lo) for t, ev in _events(state))


def _aggressive(mob, text, params: Params, at_us: bool = False) -> tuple[bool, str]:
    """(aggressive, why) for a creature: the order in the module docstring."""
    body, noto, flags = mob.get("graphic"), mob.get("notoriety"), mob.get("flags") or 0
    if at_us:
        return True, "swinging at us"
    pet = mob.get("pet")
    if pet:
        if noto in params.aggressive_notoriety:
            return True, f"{pet} pet, notoriety {noto}"
        return False, f"{pet} pet" + (", war mode" if flags & FLAG_WARMODE else "")
    passive_name = bool(text) and text.lower() in params.passive_names
    if flags & FLAG_WARMODE and body not in params.passive_bodies and not passive_name:
        return True, "war mode"
    if body in params.aggressive_bodies:
        return True, "aggressive body"
    if body in params.passive_bodies or passive_name:
        why = "passive body" if body in params.passive_bodies else "passive name"
        return False, why + (", war mode (fighting someone else)" if flags & FLAG_WARMODE else "")
    if noto in params.aggressive_notoriety:
        return True, f"notoriety {noto}"
    return params.monster_default_aggressive, "default"


def fighting_other(serial: int, key: str, mob: dict, text, swings: dict, state, *, me,
                   now: float, params: Params) -> str | None:
    """Why a war-mode creature counts as busy with someone else (module
    docstring), or None. Only for creatures whose sole aggression evidence is
    war mode; the caller checks the strike range."""
    if not (mob.get("flags") or 0) & FLAG_WARMODE:
        return None
    calm = {**mob, "flags": (mob.get("flags") or 0) & ~FLAG_WARMODE}
    if _aggressive(calm, text, params)[0]:
        return None
    last = swings.get(key)
    if not last or me is None or _serial(last["defender"]) == me:
        return None
    age = now - last["t"]
    if age > params.damage_window_s:
        return None
    if swinging_at_us(serial, key, swings, state, me=me, now=now, params=params):
        return None
    return f"fighting 0x{_serial(last['defender']):08X} (last swing {age:.1f}s ago), not us"


def damage_signal(state, *, now: float, params: Params, hits_history=(), since: float | None = None):
    """(under_attack, detail) from the self hits trend and damage/swing events;
    nothing before `since` (Watch.acknowledge) counts."""
    me = (state.get("world") or {}).get("self") or {}
    hits, hits_max = me.get("hits"), me.get("hits_max")
    me_serial = self_serial(state)
    lo = now - params.damage_window_s if since is None else max(now - params.damage_window_s, since)
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
           params: Params = Params(), hits_history=(), first_seen=None, since: float | None = None) -> Assessment:
    """first_seen: {serial: time first in view} for the label grace (Watch);
    since: damage before it is dealt with (Watch.acknowledge)."""
    now = time.time() if now is None else now
    world = state.get("world") or {}
    labels = world.get("labels") or {}
    me = self_serial(state)
    pos = self_pos(state)
    mounted = _mounted_serials(world)
    budget = recall_s + margin_s
    swings = world.get("swings") or {}       # absent in captures from before world.swings
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
            at_us = swinging_at_us(serial, key, swings, state, me=me, now=now, params=params)
            th.aggressive, th.aggression = _aggressive(mob, th.name, params, at_us)
            th.evidence.append(f"aggressive={th.aggressive} ({th.aggression})")
            th.hostile = th.aggressive
            th.reach = creature_reach(th.body, params)
            busy = fighting_other(serial, key, mob, th.name, swings, state, me=me, now=now,
                                  params=params) if th.hostile and not at_us else None
        else:
            busy = None
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
        elif busy is not None and th.distance > th.strike_range:
            th.action, th.reason = "watch", f"war mode, {busy}"
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
    under, damage = damage_signal(state, now=now, params=params, hits_history=hits_history, since=since)
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


def hit_attackers(a: Assessment, params: Params, swung=()) -> tuple[list, bool]:
    """(attackers, ranged) for damage just taken (module docstring "Who hit us"):
    creatures swinging at us (`swung` serials) or in melee range, plus known-ranged
    ones within their reach; with none of those, every candidate within its
    reach (at least CREATURE_SPELL_RANGE), the likeliest first (a ranged body,
    then hostile, then nearest). ranged: no attacker is in melee range."""
    melee = params.monster_strike_range
    mons = [t for t in a.threats if t.kind == "monster" and 0 <= t.distance <= params.max_range]
    cands = [t for t in mons if t.hostile or t.aggression == "default"]
    close = [t for t in mons if t.serial in swung] + [t for t in cands if t.distance <= melee]
    if close:
        out = list({t.serial: t for t in close}.values())
        out += [t for t in cands if t not in out and t.reach > melee and t.distance <= t.reach]
    else:
        out = sorted((t for t in cands if t.distance <= max(t.reach, CREATURE_SPELL_RANGE)),
                     key=lambda t: (t.reach <= melee, not t.hostile, t.distance))
    return out, all(t.distance > melee for t in out)


class Watch:
    """assess() plus the self-hits history and first sightings across calls
    (for under_attack and the label grace)."""

    def __init__(self, params: Params = Params()):
        self.params = params
        self.hits: list[tuple[float, int]] = []
        self.first_seen: dict[int, float] = {}
        self.since: float | None = None      # acknowledge(): damage before this is dealt with

    def update(self, state, *, recall_s: float, margin_s: float,
               now: float | None = None) -> Assessment:
        now = time.time() if now is None else now
        present = {_serial(k) for k in (state.get("world") or {}).get("mobiles") or ()}
        self.first_seen = {s: self.first_seen.get(s, now) for s in present}
        a = assess(state, recall_s=recall_s, margin_s=margin_s, now=now,
                   params=self.params, hits_history=self.hits,
                   first_seen=self.first_seen, since=self.since)
        hits = ((state.get("world") or {}).get("self") or {}).get("hits")
        if hits is not None:
            self.hits.append((now, hits))
        lo = now - self.params.damage_window_s
        self.hits = [s for s in self.hits if s[0] >= lo]
        return a

    def acknowledge(self, now: float | None = None):
        """The damage so far is dealt with: only new drops (below the hits seen at
        the last update), damage and swings count."""
        self.since = time.time() if now is None else now
        self.hits = [(self.since, self.hits[-1][1])] if self.hits else []

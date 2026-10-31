"""Aggro management for the pets runner (`loop_hunt.py --pets`; docs/HUNT_LOOP.md "Aggro").

User 2026-10-13: "positioning and managing aggro is the number one most important thing in dungeon
PvM in Outlands ... not pull too many mobs at once, not let super dangerous mobs like a vampire
consort aggro on us while we're alone, break line of sight strategically". His answers to the
follow-up questions decide the rules here:

- **A weighted pull budget** ("weighted by danger"): a monster's weight grows with its wiki
  difficulty, (difficulty - 50) / 40 clamped to 0.25-3 (a vampire bat 0.43, a shadow minion 0.53, a
  shade wolf 0.92, a nightstalker 1.8, a shadowguard 2.05, the countess 2.4). While the pets fight
  monsters worth E, an advance that would wake more (W: idle monsters that would see us within
  AGGRO_R + WAKE_MARGIN) stops when E + W > --pull-budget. With nothing fighting, a pack is pulled
  whole: it has to be fought some time, and alone it is (Logan at the nightstalker room's edge,
  20261013_204518: three wolves and the stalker came in stages; the knight only 1.5 min later).
- **Never aggro a vampiric consort** (--never-aggro; user: only the consort; the elder vampire and
  the elder vampire countess since 2026-10-15): its sight within AGGRO_R + NEVER_MARGIN is a no-go
  area for the patrol and the routes (crawl.Crawl.observe), and it is never pulled. One that comes
  for us anyway is fought like any other monster on us; until 2026-10-24 it was run from until it
  gave up ("I run until the consort leashes and I'm in an area that I can manage"; user 2026-10-24:
  remove the running; docs/HUNT_LOOP.md "Aggro").
- **Line of sight while fighting** ("situational": between two rooms with the pets fighting in
  room A, stay out of sight of room B; "if it's easy to stand out of LoS of a caster like the
  banshee while still being able to heal our pets, all else being equal it's better"): a care action
  picks a tile within two steps that keeps the pets within vet range and scores fewer idle monsters
  that would see us (weighted) and fewer casters in war mode with sight of us (`stand_score`).

Line of sight is los.Sight (ServUO Map.LineOfSight, checked on the first trip's 61 target answers),
z from the map surface (the world model's own mobile z is often stale). AGGRO_R is the simulator's
10 [INFERENCE: the trip's war-mode switches came 1-13 tiles from us, median 7].
"""
from __future__ import annotations

import glob
import json
import os
import re

import combat
import los
from agent_link import cheb, serial_of

# The aggro range, per creature type, as the captures measured it (each harness/data/sim scenario's `aggro_observed`,
# docs/SIM.md "Monster mechanics audit"): 210 of 211 war-mode flips came with the nearest of us 1-10 tiles off, and
# 102 idle monsters had one of us in our line of sight at 11-13 tiles for over 5 s without waking (none within
# 10). The shadow minion and the calamity had 13 until 2026-10-29: in their 161 wakes one of us was within 10 before
# every flip (docs/NOTES.md "Shooters' and casters' first target"). The user first put it at "12 or 13", then
# (2026-10-15, a shadowguard at 14 and a reanimated knight idle at 7-13 tiles): "aggro range smaller than 14 tiles".
AGGRO_R = 10                  # a type the captures don't list
AGGRO_MAX = 13                # --never-aggro sight is kept beyond this (the farthest range once measured; kept as a margin)
SCENARIO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "sim")
_RANGES = None
_REACH = None


def scenarios() -> list[dict]:
    """Every simulator scenario (harness/data/sim/<name>.json, not its _kb / _observed files): the dungeons known."""
    out = []
    for path in sorted(glob.glob(os.path.join(SCENARIO_DIR, "*.json"))):
        if path.endswith(("_kb.json", "_observed.json")):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                out.append(json.load(f))
        except (OSError, ValueError):
            continue
    return out


def aggro_range(kind: str) -> int:
    """Tiles within which an idle monster of `kind` wakes to one of us it sees (`aggro_observed`), else AGGRO_R."""
    global _RANGES
    if _RANGES is None:
        _RANGES = {k: int(v["range"]) for sc in scenarios() for k, v in (sc.get("aggro_observed") or {}).items()
                   if isinstance(v, dict) and v.get("range")}
    return _RANGES.get(kind, AGGRO_R)


def melee_reach(kind: str) -> int:
    """Tiles from which a monster of `kind` lands its swings (`melee_observed.<type>.reach`: the giant pincer crab's 2,
    docs/NOTES.md "Tidal Tomb: level 2 death 2026-10-30 18:29"), else 1."""
    global _REACH
    if _REACH is None:
        _REACH = {k: int(v["reach"]) for sc in scenarios() for k, v in (sc.get("melee_observed") or {}).items()
                  if isinstance(v, dict) and v.get("reach")}
    return _REACH.get(kind, 1)


WAKE_MARGIN = 2               # the next steps, and the pets a tile or two behind us
# first_on_us's "a pet nearer": None, by Chebyshev (since 2026-10-15); a number, that many tiles Euclidean (the measured
# choice of the nearest at the wake, 2026-10-29). The step-in (tamer.Team.step_back) uses STEP_IN_GAP 1.0 either way;
# the walks' stops, the stand scores and the back-off keep the Chebyshev rule (docs/SIM.md "The step-in")
FIRST_GAP = None
NEVER_MARGIN = 3
WAR = 0x40
# user 2026-10-15 after the eleventh live run (the elder vampire countess froze both pets and killed Logan): "I
# suppose the runner can avoid the elder vampires for now". The consort, avoided from 2026-10-13, no longer
# ("we don't need to avoid any mob specifically", 2026-10-15)
NEVER_AGGRO = ("elder vampire", "elder vampire countess")
# A giant astral zoa's spell stacks Backlash on its victims, a 0.3 chance a stack to fizzle each spellcast for 30 s:
# the phoenix fizzled 54 % of its casts under it and the fight took 111 s against 47 (docs/NOTES.md "Backlash"). User
# 2026-10-27: "Maybe we should just avoid it when our pet team relies on caster(s)": --avoid-backlash puts these into
# the never-aggro set when every attack pet of the team casts (tamer.casters_only).
BACKLASH_TYPES = ("giant astral zoa",)
CASTER_AI = frozenset(("mage", "meleemage"))
_KB = None


def eucl(a, b) -> float:
    """The Euclidean distance on the map (RunUO GetDistanceToSqrt: a monster's nearest)."""
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def type_key(name: str | None) -> str:
    return re.sub(r"^(a|an|the)\s+", "", (name or "").strip().lower())


def creatures() -> dict:
    """The wiki's creature stats of every scenario's dungeon (each one's evidence.kb file: mausoleum_kb.json,
    tidal_tomb_kb.json, ...), {} without them."""
    global _KB
    if _KB is None:
        _KB = {}
        for sc in scenarios():
            kb = (sc.get("evidence") or {}).get("kb")
            if not kb:
                continue
            try:
                with open(os.path.join(SCENARIO_DIR, kb), encoding="utf-8") as f:
                    _KB.update(json.load(f).get("creatures") or {})
            except OSError:
                continue
    return _KB


def weight(kind: str) -> float:
    """A monster's share of the pull budget, from its wiki difficulty (1.0 when unknown)."""
    d = (creatures().get(kind) or {}).get("difficulty")
    return 1.0 if d is None else min(3.0, max(0.25, (d - 50.0) / 40.0))


def caster(kind: str) -> bool:
    return (creatures().get(kind) or {}).get("ai") in CASTER_AI


def ranged(kind: str) -> bool:
    """The wiki's ranged AI (corpse purger, calamity, eldritch experiment, muddled vampire hunter): its melee is a
    ranged attack (the purger's Corpse Barrage up to 12 tiles; a calamity cast from 13)."""
    return (creatures().get(kind) or {}).get("ai") == "ranged"


def melee(kind: str) -> bool:
    """A monster that comes to swing at us: not shy, not a mage or ranged AI (an unknown kind counts as one)."""
    return not shy(kind) and (creatures().get(kind) or {}).get("ai") not in SHY_AI


# Monsters that shoot or cast from where they stand, whose wake we put on the pets (tamer.Team.step_back, back_off,
# patrol_pull): about 2 s after one of us comes into their range and sight (never under 1.5 s) they take the nearest of
# us in their sight then, not the first seen (measured 2026-10-29, docs/NOTES.md "Shooters' and casters' first target"),
# and their attack reaches us wherever the pets stand. The corpse purger (user 2026-10-17: "We're frequently getting
# diseased by the corpse purgers"; its barrage diseases: docs/NOTES.md "Corpse purger diseases"); the blightmare since
# 2026-10-21 (a mage that stood at its spot and cast at Logan from 10-12 with its Spellblight disease in both live
# deaths to it, 10-19 19:55 and 10-21 18:21;
# docs/NOTES.md "Dead in a blightmare's reach again"); since 2026-10-26 the casters that stand and cast (user 2026-10-26
# "I keep the pets between me and mobs so mobs aggro on the pets ... I avoid being sighted by hostiles", his fight in
# the reanimator's room with "All Patrol" from out of sight and no damage, against two deaths there where they saw Logan
# first: docs/NOTES.md "How the user plays the casters and purgers").
# Since 2026-10-26 by the wiki's AI rather than by name (user: Tidal Tomb's drowned mariners, soulcatchers and zoas
# are "conceptually similar to the ranger attackers and casters in Mausoleum 3"; "treat them the same"): a mage or
# ranged AI from SHY_MIN_DIFFICULTY up (the weak casters, a familiar 21 and a reanimator zealot 50, die before they
# matter [INFERENCE]), plus SHY_EXTRA (the shadowguard: a meleemage that casts from its spot, on the user's list).
# On the Mausoleum that keeps the old seven and adds the calamity (it cast at Logan from 13), the fleshweaver and the
# muddled vampire hunter; on Tidal Tomb the drowned mariner and buccaneer, the soulcatcher and every zoa.
SHY_AI = frozenset(("mage", "ranged"))
SHY_MIN_DIFFICULTY = 60.0
SHY_EXTRA = frozenset(("shadowguard",))


def shy(kind: str) -> bool:
    if kind in SHY_EXTRA:
        return True
    c = creatures().get(kind) or {}
    return c.get("ai") in SHY_AI and (c.get("difficulty") or 0.0) >= SHY_MIN_DIFFICULTY


def stealthy(kind: str) -> bool:
    """The wiki marks it a stealther (Vanish, Backstab): the Mausoleum's nightstalker, shade wolf, consort."""
    return bool((creatures().get(kind) or {}).get("stealth"))


# Backspawn (user 2026-10-15: "We know nightstalkers spawn in that room though. When a mob respawns behind you we
# call it backspawn. A human knows not to stand out in the open when stealth mobs are going to respawn there";
# live 20261015_171928 18:40:10 an unseen nightstalker hit Logan -58 from 2 tiles, the side away from the pets):
# a tile within BACKSPAWN_R of a stealther's spawn spot, in its sight, with no pet nearer the spot than the tile,
# scores as if that monster stood there idle. A corpse purger's or blightmare's spot too since 2026-10-26, within its
# reach (BACKSPAWN_SHY_R; user: "I move to spots where if a mob respawns (like the dreaded blightmare) I won't be
# standing in its sight"). Not the other shy casters' spots: with the shadowguards' and banshees' too, the simulator lost
# 8 % gold and doubled the deaths against the purger's and the blightmare's alone (98 runs a side).
BACKSPAWN_R = 7
BACKSPAWN_SHY_R = 12          # tamer.CASTER_R: a purger's or a blightmare's respawn shoots or casts from there
SHOOTERS = ("corpse purger", "blightmare")   # the shy ones before 2026-10-26: their shot rules (tamer.Team.to_pet), spots
RESPAWN_SHY = SHOOTERS
BACKSPAWN_OFF = ()            # stealthers whose spawn spots aren't kept out of at all (the shade wolf's: a test variant)

# The closed area (user 2026-10-31 after the Maus 3 death at 11:57:22, a hidden vampiric consort's -46 and bleed 2 tiles
# from her spawn spot (5243, 633), 3 ahead of the pets: "I'm not going to stand unguarded near where the vampiric
# consort spawns since her stealth backstab + bleed is deadly"; "getting hit by a weaker stealther like the shade wolf
# isn't a huge deal compared to 'forgotten soul and consort are on Logan at the same time' which spells death
# probably"): a tile within STEALTH_CLOSED_R of a live spawn spot of a STEALTH_CLOSED type is closed to Logan unless a
# pet stands within STEALTH_GUARD_R of it or nearer the spot than it (View.stealth_closed). The reach measured by
# sim/stealth_scan.py over 43 Mausoleum captures (docs/NOTES.md "Maus 3 near-deaths 2026-10-31"): of the bites by a
# stealther coming out of hiding within 3 of its known spot the victims stood 1-4 from it (the consort's 8: 2-4; the
# nightstalker's 12: 1-4; the shade wolf's 60: 1-5, 5 once), the 11:57 bite 2; so 4 and a tile's margin. The shade
# wolf and the nightstalker keep the soft backspawn rule (stand_score) only.
STEALTH_CLOSED = ("vampiric consort",)
STEALTH_CLOSED_R = 5
STEALTH_GUARD_R = 1
STEALTH_CLOSED_COST = 100.0   # View.stand_score: a closed tile as a --never-aggro monster's sight


def respawn_r(kind: str) -> int | None:
    """How far a monster respawning at its spot reaches us (the backspawn rule), or None: not one we keep out of."""
    if kind in BACKSPAWN_OFF:
        return None
    return BACKSPAWN_SHY_R if kind in RESPAWN_SHY else BACKSPAWN_R if stealthy(kind) else None


class View:
    """The hostile monsters in view for one state read, with line of sight from tiles.
    `h` is the HuntLoop (mover, team roster, dead set, --never-aggro)."""

    def __init__(self, h, st):
        self.h, self.st = h, st
        self.pos = tuple(h.pos(st)[:3])
        mv = h.mover
        self.walk = mv.walk_map(st)
        self.sight = los.sight_for(mv.walkers, st["world"]["self"].get("map")) if self.walk is not None else None
        self.wall_sight = None           # sees(strict): built on first use
        pets = set(h.team.roster) if h.team is not None else set()
        never = set(getattr(h.args, "never_aggro", None) or NEVER_AGGRO)
        world, me = st["world"], h.me(st)
        self.attacking = {serial_of(a["serial"]) for a in h.attackers(st)}
        if h.team is not None:
            self.attacking |= h.team.pet_attackers(st)
        self.mobs = []
        self.pets = [(p["x"], p["y"]) for p in h.team.pets(st) if not p["dead"]] if h.team is not None else []
        self.spawns = list(getattr(h.team, "backspawn", {}).items()) if h.team is not None else []
        live = getattr(h.team, "spot_live", None) if h.team is not None else None
        self.closed = [(tuple(spot[:2]), kind) for spot, kind in self.spawns
                       if kind in STEALTH_CLOSED and (live is None or live(spot))]
        for key, mob in world["mobiles"].items():
            s = serial_of(key)
            if s == me or s in pets or s in h.dead or mob.get("x") is None:
                continue
            if not combat.attackable(world, key)[0]:
                continue
            name = (world.get("labels") or {}).get(key) or mob.get("name")
            kind = type_key(name)
            x, y = mob["x"], mob["y"]
            z = los.surface_z(self.walk, x, y, mob.get("z") or 0) if self.walk is not None else (mob.get("z") or 0)
            self.mobs.append({"serial": s, "kind": kind, "pos": (x, y, z), "war": bool((mob.get("flags") or 0) & WAR),
                              "weight": weight(kind), "caster": caster(kind), "never": kind in never,
                              "ranged": ranged(kind), "shy": shy(kind), "r": aggro_range(kind)})

    def z_at(self, tile) -> int:
        if len(tile) > 2:
            return tile[2]
        return los.surface_z(self.walk, tile[0], tile[1], self.pos[2]) if self.walk is not None else self.pos[2]

    def sees(self, m: dict, tile, r: int, strict: bool = False) -> bool:
        """Whether monster `m` would see a mobile on `tile` within `r` tiles. `strict`: a Wall-flagged static
        blocks too (los.Sight walls; the hold's sight)."""
        if cheb(m["pos"], tile) > r:
            return False
        if self.sight is None:
            return True
        sight = self.sight
        if strict:
            if self.wall_sight is None:
                self.wall_sight = los.sight_for(self.h.mover.walkers, self.st["world"]["self"].get("map"), walls=True)
            sight = self.wall_sight or sight
        return sight.can_see(m["pos"], (tile[0], tile[1], self.z_at(tile)))

    def we_see(self, m: dict, tile, r: int) -> bool:
        """Whether we, standing on `tile`, see monster `m` within `r` by the server's rule: Map.LineOfSight from
        us to it (a target's check, RunUO `from.InLOS(target)`; the line isn't symmetric at the ends, so not
        `sees`, which is its sight of us)."""
        if cheb(m["pos"], tile) > r:
            return False
        if self.sight is None:
            return True
        return self.sight.can_see((tile[0], tile[1], self.z_at(tile)), m["pos"])

    def engaged(self, r: int = 8) -> list[dict]:
        """Monsters in war mode fighting us or the pets (or within `r`): what the pets fight now."""
        return [m for m in self.mobs if m["war"] and (m["serial"] in self.attacking or cheb(m["pos"], self.pos) <= r)]

    def wakes(self, tile, margin: int = WAKE_MARGIN, strict: bool = False) -> list[dict]:
        """Idle monsters that would wake to us on `tile` (their aggro range and sight, plus `margin`)."""
        return [m for m in self.mobs if not m["war"] and not m["never"] and self.sees(m, tile, m["r"] + margin, strict)]

    def first_on_us(self, tile, margin: int = 1, gap: float | None = None) -> list[dict]:
        """Idle monsters that would take us on `tile` rather than a pet: in their aggro range and sight, and no pet
        nearer them in it (user 2026-10-15: "Any mob that 'sees' Logan before it sees a pet is going to aggro on him
        directly and that's pretty much a death sentence with this build"; stand out of the sight of monsters that
        haven't aggroed on a pet, or with the tank between). Nearer: by Chebyshev (FIRST_GAP None, the rule since
        2026-10-15), or `gap` (default FIRST_GAP) tiles or more Euclidean: measured 2026-10-29, a shy monster takes the
        nearest of us in its sight at the end of its wake delay, a near tie under a tile Logan 2 in 3 when he is the
        slightly nearer (docs/NOTES.md "Shooters' and casters' first target")."""
        gap = FIRST_GAP if gap is None else gap
        out = []
        for m in self.wakes(tile, margin):
            if gap is None:
                if not any(cheb(m["pos"], p) < cheb(m["pos"], tile) and self.sees(m, p, m["r"] + margin)
                           for p in self.pets):
                    out.append(m)
                continue
            d = eucl(m["pos"], tile)
            if not any(d - eucl(m["pos"], p) >= gap and self.sees(m, p, m["r"] + margin) for p in self.pets):
                out.append(m)
        return out

    def never_near(self, tile, margin: int = NEVER_MARGIN) -> list[dict]:
        """--never-aggro monsters, idle, that would see us on `tile`."""
        return [m for m in self.mobs if m["never"] and not m["war"] and self.sees(m, tile, AGGRO_MAX + margin)]

    def stand_score(self, tile) -> float:
        """Lower is better: idle monsters that would see us there before a pet (first_on_us, by weight), a
        --never-aggro one above all, then casters and ranged monsters in war mode with sight of us. A tile the closed
        area takes (stealth_closed) costs STEALTH_CLOSED_COST, as a --never-aggro one's sight: every tile picker that
        ranks by this (the reposition, off the tank, the stand-off, the loot and vet tiles, the retreat, the cover, the
        bard's step) takes it only when no open tile is left."""
        idle = sum(m["weight"] for m in self.first_on_us(tile, margin=1))
        never = len(self.never_near(tile))
        casters = sum(1 for m in self.mobs if m["war"] and (m["caster"] or m["ranged"])
                      and self.sees(m, tile, los.MAX_DISTANCE))
        back = sum(weight(kind) for _, kind in self.backspawn_exposed(tile))
        closed = STEALTH_CLOSED_COST if self.stealth_closed(tile) else 0.0
        return 100.0 * never + 10.0 * (idle + back) + 1.0 * casters + closed

    def backspawn_exposed(self, tile) -> list:
        """[(spot, kind)]: stealthers' and shy monsters' spawn spots (Team.backspawn) within their reach (respawn_r) of
        `tile` that see it, with no pet nearer the spot than the tile."""
        out = []
        for spot, kind in self.spawns:
            r = respawn_r(kind)
            if r is None:
                continue
            d = cheb(spot, tile)
            if d > r or any(cheb(spot, p) < d for p in self.pets):
                continue
            m = {"pos": (spot[0], spot[1], self.z_at(spot[:2]))}
            if self.sees(m, tile, r):
                out.append((spot, kind))
        return out

    def stealth_closed(self, tile, pets=None):
        """The closed area's verdict on `tile` (STEALTH_CLOSED_R): (spot, kind, distance) of the nearest live
        STEALTH_CLOSED spawn spot (Team.backspawn, Team.spot_live) within STEALTH_CLOSED_R of it with no pet (`pets`,
        default the live pets' tiles now) within STEALTH_GUARD_R of it or nearer the spot than it, else None. By
        tiles, not sight: a hidden one walks round to its victim (live 11:57:17 the consort came out a tile from Logan,
        2 from her spot)."""
        if not self.closed:
            return None
        pets = self.pets if pets is None else pets
        best = None
        for spot, kind in self.closed:
            d = cheb(spot, tile)
            if d > STEALTH_CLOSED_R or (best is not None and d >= best[2]):
                continue
            if any(cheb(p, tile) <= STEALTH_GUARD_R or cheb(p, spot) < d for p in pets):
                continue
            best = (spot, kind, d)
        return best

    def overpull(self, budget: float) -> str | None:
        """Why advancing from here pulls too much (module docstring), else None. Nothing fighting: idle
        monsters worth more than the budget that would all wake to us here ("at once": the crawl then
        patrols elsewhere; sim fight1949: two apparitions, or two banshees, seeing the same tile woke together
        and the second cast at or blinked onto Logan while the pets held the first)."""
        never = self.never_near(self.pos)
        if never:
            return f"a {never[0]['kind']} would see us"
        fighting = self.engaged()
        w = sum(m["weight"] for m in self.wakes(self.pos))
        if not fighting:
            n = len(self.wakes(self.pos))
            return f"would wake {n} worth {w:.1f} at once (budget {budget:g})" if n > 1 and w > budget else None
        e = sum(m["weight"] for m in fighting)
        if w > 0 and e + w > budget:
            return f"would wake {w:.1f} more onto {e:.1f} (budget {budget:g})"
        return None

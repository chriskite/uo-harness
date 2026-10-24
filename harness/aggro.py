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
# 10); the shadow minion and the calamity wake at 13. The user first put it at "12 or 13", then (2026-10-15,
# a shadowguard at 14 and a reanimated knight idle at 7-13 tiles): "aggro range smaller than 14 tiles".
AGGRO_R = 10                  # a type the captures don't list
AGGRO_MAX = 13                # the farthest measured (shadow minion, calamity): --never-aggro sight is kept beyond it
SCENARIO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "sim")
_RANGES = None


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


WAKE_MARGIN = 2               # the next steps, and the pets a tile or two behind us
NEVER_MARGIN = 3
WAR = 0x40
# user 2026-10-15 after the eleventh live run (the elder vampire countess froze both pets and killed Logan): "I
# suppose the runner can avoid the elder vampires for now". The consort, avoided from 2026-10-13, no longer
# ("we don't need to avoid any mob specifically", 2026-10-15)
NEVER_AGGRO = ("elder vampire", "elder vampire countess")
CASTER_AI = frozenset(("mage", "meleemage"))
_KB = None


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


# Monsters that shoot from where they stand, whose first sight of us we keep the pets ahead of (tamer.Team.back_off):
# their aggro goes to whoever they see first and their attack reaches us wherever the pets stand. The corpse purger
# (user 2026-10-17: "We're frequently getting diseased by the corpse purgers"; its barrage diseases: docs/NOTES.md
# "Corpse purger diseases"); the blightmare since 2026-10-21 (a mage that stood at its spot and cast at Logan from 10-12
# with its Spellblight disease in both live deaths to it, 10-19 19:55 and 10-21 18:21; docs/NOTES.md "Dead in a
# blightmare's reach again").
SHY = ("corpse purger", "blightmare")


def shy(kind: str) -> bool:
    return kind in SHY


def stealthy(kind: str) -> bool:
    """The wiki marks it a stealther (Vanish, Backstab): the Mausoleum's nightstalker, shade wolf, consort."""
    return bool((creatures().get(kind) or {}).get("stealth"))


# Backspawn (user 2026-10-15: "We know nightstalkers spawn in that room though. When a mob respawns behind you we
# call it backspawn. A human knows not to stand out in the open when stealth mobs are going to respawn there";
# live 20261015_171928 18:40:10 an unseen nightstalker hit Logan -58 from 2 tiles, the side away from the pets):
# a tile within BACKSPAWN_R of a stealther's spawn spot, in its sight, with no pet nearer the spot than the tile,
# scores as if that monster stood there idle.
BACKSPAWN_R = 7


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

    def first_on_us(self, tile, margin: int = 1) -> list[dict]:
        """Idle monsters that would see us on `tile` before a pet: in their aggro range and sight, and no
        pet nearer them (user 2026-10-15: "Any mob that 'sees' Logan before it sees a pet is going to aggro
        on him directly and that's pretty much a death sentence with this build"; stand out of the sight of
        monsters that haven't aggroed on a pet, or with the tank between). [INFERENCE: nearer counts as seen
        first; the pets follow us a tile or two behind on the move.]"""
        return [m for m in self.wakes(tile, margin)
                if not any(cheb(m["pos"], p) < cheb(m["pos"], tile) and self.sees(m, p, m["r"] + margin)
                           for p in self.pets)]

    def never_near(self, tile, margin: int = NEVER_MARGIN) -> list[dict]:
        """--never-aggro monsters, idle, that would see us on `tile`."""
        return [m for m in self.mobs if m["never"] and not m["war"] and self.sees(m, tile, AGGRO_MAX + margin)]

    def stand_score(self, tile) -> float:
        """Lower is better: idle monsters that would see us there before a pet (first_on_us, by weight), a
        --never-aggro one above all, then casters and ranged monsters in war mode with sight of us."""
        idle = sum(m["weight"] for m in self.first_on_us(tile, margin=1))
        never = len(self.never_near(tile))
        casters = sum(1 for m in self.mobs if m["war"] and (m["caster"] or m["ranged"])
                      and self.sees(m, tile, los.MAX_DISTANCE))
        back = sum(weight(kind) for _, kind in self.backspawn_exposed(tile))
        return 100.0 * never + 10.0 * (idle + back) + 1.0 * casters

    def backspawn_exposed(self, tile) -> list:
        """[(spot, kind)]: stealthers' spawn spots (Team.backspawn) within BACKSPAWN_R of `tile` that see it, with
        no pet nearer the spot than the tile."""
        out = []
        for spot, kind in self.spawns:
            d = cheb(spot, tile)
            if d > BACKSPAWN_R or any(cheb(spot, p) < d for p in self.pets):
                continue
            m = {"pos": (spot[0], spot[1], self.z_at(spot[:2]))}
            if self.sees(m, tile, BACKSPAWN_R):
                out.append((spot, kind))
        return out

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

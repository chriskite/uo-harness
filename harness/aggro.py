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
- **Never aggro a vampiric consort** (--never-aggro; user: only the consort): its sight within
  AGGRO_R + NEVER_MARGIN is a no-go area for the patrol and the routes (crawl.Crawl.observe); one
  that comes for us anyway is run from until it gives up ("I run until the consort leashes and I'm
  in an area that I can manage"; live 20:57:55-20:58:18 it chased Logan ~25 tiles from where it
  woke); the leave rules still send us to the gate when that fails.
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

import json
import os
import re

import combat
import los
from agent_link import cheb, serial_of

AGGRO_R = 13                  # user 2026-10-15: "Aggro range I'm sure is longer than 9 tiles, I think its 12 or 13"
WAKE_MARGIN = 2               # the next steps, and the pets a tile or two behind us
NEVER_MARGIN = 3
WAR = 0x40
NEVER_AGGRO = ("vampiric consort",)                  # user 2026-10-13
CASTER_AI = frozenset(("mage", "meleemage"))
KB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "sim", "mausoleum_kb.json")
_KB = None


def type_key(name: str | None) -> str:
    return re.sub(r"^(a|an|the)\s+", "", (name or "").strip().lower())


def creatures() -> dict:
    """The wiki's creature stats (harness/data/sim/mausoleum_kb.json), {} without the file."""
    global _KB
    if _KB is None:
        try:
            with open(KB_PATH, encoding="utf-8") as f:
                _KB = json.load(f).get("creatures") or {}
        except OSError:
            _KB = {}
    return _KB


def weight(kind: str) -> float:
    """A monster's share of the pull budget, from its wiki difficulty (1.0 when unknown)."""
    d = (creatures().get(kind) or {}).get("difficulty")
    return 1.0 if d is None else min(3.0, max(0.25, (d - 50.0) / 40.0))


def caster(kind: str) -> bool:
    return (creatures().get(kind) or {}).get("ai") in CASTER_AI


class View:
    """The hostile monsters in view for one state read, with line of sight from tiles.
    `h` is the HuntLoop (mover, team roster, dead set, --never-aggro)."""

    def __init__(self, h, st):
        self.h, self.st = h, st
        self.pos = tuple(h.pos(st)[:3])
        mv = h.mover
        self.walk = mv.walk_map(st)
        self.sight = los.sight_for(mv.walkers, st["world"]["self"].get("map")) if self.walk is not None else None
        pets = set(h.team.roster) if h.team is not None else set()
        never = set(getattr(h.args, "never_aggro", None) or NEVER_AGGRO)
        world, me = st["world"], h.me(st)
        self.attacking = {serial_of(a["serial"]) for a in h.attackers(st)}
        if h.team is not None:
            self.attacking |= h.team.pet_attackers(st)
        self.mobs = []
        self.pets = [(p["x"], p["y"]) for p in h.team.pets(st) if not p["dead"]] if h.team is not None else []
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
                              "weight": weight(kind), "caster": caster(kind), "never": kind in never})

    def z_at(self, tile) -> int:
        if len(tile) > 2:
            return tile[2]
        return los.surface_z(self.walk, tile[0], tile[1], self.pos[2]) if self.walk is not None else self.pos[2]

    def sees(self, m: dict, tile, r: int) -> bool:
        """Whether monster `m` would see a mobile on `tile` within `r` tiles."""
        if cheb(m["pos"], tile) > r:
            return False
        if self.sight is None:
            return True
        return self.sight.can_see(m["pos"], (tile[0], tile[1], self.z_at(tile)))

    def engaged(self, r: int = 8) -> list[dict]:
        """Monsters in war mode fighting us or the pets (or within `r`): what the pets fight now."""
        return [m for m in self.mobs if m["war"] and (m["serial"] in self.attacking or cheb(m["pos"], self.pos) <= r)]

    def wakes(self, tile, margin: int = WAKE_MARGIN) -> list[dict]:
        """Idle monsters that would wake to us on `tile` (their aggro range and sight, plus `margin`)."""
        return [m for m in self.mobs if not m["war"] and not m["never"] and self.sees(m, tile, AGGRO_R + margin)]

    def first_on_us(self, tile, margin: int = 1) -> list[dict]:
        """Idle monsters that would see us on `tile` before a pet: in their aggro range and sight, and no
        pet nearer them (user 2026-10-15: "Any mob that 'sees' Logan before it sees a pet is going to aggro
        on him directly and that's pretty much a death sentence with this build"; stand out of the sight of
        monsters that haven't aggroed on a pet, or with the tank between). [INFERENCE: nearer counts as seen
        first; the pets follow us a tile or two behind on the move.]"""
        return [m for m in self.wakes(tile, margin)
                if not any(cheb(m["pos"], p) < cheb(m["pos"], tile) and self.sees(m, p, AGGRO_R + margin)
                           for p in self.pets)]

    def never_near(self, tile, margin: int = NEVER_MARGIN) -> list[dict]:
        """--never-aggro monsters, idle, that would see us on `tile`."""
        return [m for m in self.mobs if m["never"] and not m["war"] and self.sees(m, tile, AGGRO_R + margin)]

    def chasing(self, r: int = 18) -> list[dict]:
        """--never-aggro monsters in war mode within `r`: coming for us or the pets."""
        return [m for m in self.mobs if m["never"] and m["war"] and cheb(m["pos"], self.pos) <= r]

    def stand_score(self, tile) -> float:
        """Lower is better: idle monsters that would see us there before a pet (first_on_us, by weight), a
        --never-aggro one above all, then casters in war mode with sight of us."""
        idle = sum(m["weight"] for m in self.first_on_us(tile, margin=1))
        never = len(self.never_near(tile))
        casters = sum(1 for m in self.mobs if m["war"] and m["caster"] and self.sees(m, tile, los.MAX_DISTANCE))
        return 100.0 * never + 10.0 * idle + 1.0 * casters

    def overpull(self, budget: float) -> str | None:
        """Why advancing from here pulls too much (module docstring), else None."""
        never = self.never_near(self.pos)
        if never:
            return f"a {never[0]['kind']} would see us"
        fighting = self.engaged()
        if not fighting:
            return None
        e = sum(m["weight"] for m in fighting)
        w = sum(m["weight"] for m in self.wakes(self.pos))
        if w > 0 and e + w > budget:
            return f"would wake {w:.1f} more onto {e:.1f} (budget {budget:g})"
        return None

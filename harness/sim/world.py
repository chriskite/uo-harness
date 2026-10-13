"""The simulated server: one dungeon level on the real Outlands map, its monsters, our character and
pets, and the mechanics the hunt runner meets there (docs/SIM.md).

It speaks to the client side only in packets: `c2s(pkt)` takes a client packet at the current game
time, `run_until(t)` advances the world, `drain()` returns the S2C packets to feed the client's
world model (sim.link.SimLink does both). Geometry is the server's truth: walking by
`pathfind.Walk.can_walk` on the install's map files (z from the map), line of sight by `los.Sight`
(validated 61/61 against the live server's answers, docs/NOTES.md).

Every number comes from the scenario (harness/data/sim/<name>.json), each with its evidence or an
[INFERENCE] mark there. What is modelled:

- **Monsters**: idle near their spawn; acquire the nearest of us and our live pets within their
  aggro range and in sight (re-checked every THINK_S); chase by a greedy step toward the target with
  an A* fallback; melee when adjacent (swing period, hit chance, damage) or ranged/spell when in
  range and sight; threat decides whom they hit: damage taken from each attacker (x the pet's
  `threat_x`, a tank's draw) plus our aggressive acts (a bard skill at it, "All Kill" at it: user
  2026-10-12, aggro then goes to the character); a leash home when the target is far or unseen;
  specials (bleed, poison, life drain) per type; respawn at the spawn after `respawn_s`.
- **Pets**: follow / guard / kill / stay by our pet commands ("All Guard Me": attack what attacks us
  or the pets; "All Kill": the target, then back to following; guarding stops at a recall);
  melee (gravebug) or ranged (phoenix); bonded: a ghost at 0 hits (0xBF sub 0x19) that follows;
  the gravebug devours a corpse nearby to heal.
- **Bard skills**: use -> prompt + cursor; the target in range and sight, else cliloc 500446/500237;
  success by the scenario's rates; discord (damage dealt down, taken up), peace (no move, no attack;
  a Barding Break chance each second, then immunity), provoke (two monsters fight); the cooldowns
  and refusal texts the live server sends.
- **Veterinary supplies, heal potions, the Stationary Penalty, corpses and loot, the rune tome
  recall** (home and back), and our death.

Not modelled: other players, other tamers' pets, the Society job, item durability, skill gains.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

import combat
import escape
import los
import nav
import pathfind
import tamer
from sim import packets as P

VIEW = 18                 # the client's view range (combat.VIEW_RANGE)
TICK_S = 0.1              # world step
THINK_S = 0.5             # a monster/pet re-decides its target this often (RunUO AI ~0.5-1 s) [INFERENCE]
LEASH = 24                # a monster chasing beyond this from home gives up [INFERENCE]
LOST_S = 8.0              # ...or after its target was out of sight this long [INFERENCE]
REGEN_S = 4.0             # hits regenerate 1 per this out of combat (player and pets) [INFERENCE]
CORPSE_DECAY_S = 420.0    # [INFERENCE] RunUO monster corpses ~7 min
ACTION_DELAY_S = 0.5      # item uses this close together: cliloc 500119 (RunUO ActionDelay) [INFERENCE]
RECALL_S = 1.9            # power words -> arrival (live 1.95-2.09 s, escape.RECALL_CAST_S)
PENALTY_STILL_S = 300.0   # Stationary Penalty after this without a step (live, stationary.py)
PENALTY_STEPS = 5
TELEPORT_DIRS = range(8)
BACKPACK_GUMP = 0x3C
BAG_GUMP = 0x3D
CORPSE_GUMP = 0x09
TOME_ROW_BUTTON0 = 100    # the tome's rows: 100 + row index (test_loop_hunt's layout, escape parses it)
GHOST_BODY = 0x192
ROW_HUES = (63, 2655)     # the default row's text hue, other rows'


def cheb(a, b) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def tile_dir(a, b) -> int:
    return nav.direction(tuple(a[:2]), tuple(b[:2]))


@dataclass
class Mob:
    serial: int
    kind: str                 # "player", "pet", "monster"
    key: str                  # creature type key (scenario `creatures` / `pets`)
    name: str
    body: int
    x: int
    y: int
    z: int
    d: int = 0
    hits: float = 100.0
    hits_max: float = 100.0
    noto: int = 3
    spec: dict = field(default_factory=dict)
    warmode: bool = False
    alive: bool = True
    ghost: bool = False
    target: int | None = None
    threat: dict = field(default_factory=dict)
    damage_by: dict = field(default_factory=dict)   # serial -> damage dealt to us (loot rights, credit)
    next_think: float = 0.0
    next_step: float = 0.0
    next_swing: float = 0.0
    seen_target_t: float = 0.0
    home: tuple = (0, 0, 0)
    spawn: int | None = None
    discord_until: float = 0.0
    calm_until: float = 0.0       # pacified until
    immune_until: float = 0.0     # barding break immunity (peace/provoke)
    provoke: tuple | None = None  # (onto serial, until)
    poison_until: float = 0.0
    poison_next: float = 0.0
    bleed_until: float = 0.0
    bleed_next: float = 0.0
    mode: str = "follow"          # pets: follow, guard, kill, stay
    kill: int | None = None
    devour_t: float = 0.0
    last_hurt_t: float = -1e9
    path: list = field(default_factory=list)
    stuck: int = 0

    @property
    def pos(self):
        return (self.x, self.y, self.z)

    def pct(self) -> int:
        return max(0, min(100, int(round(100 * self.hits / self.hits_max)))) if self.hits_max else 0


class World:
    def __init__(self, scenario: dict, seed: int = 0, walkers: pathfind.Walkers | None = None):
        self.sc = scenario
        self.rng = random.Random(seed)
        self.t = 0.0
        self.out: list[bytes] = []
        self.facet = scenario.get("facet", 0)
        self.walkers = walkers or pathfind.Walkers()
        self.walk = self.walkers.get(self.facet)
        if self.walk is None:
            raise ValueError(f"no map geometry for facet {self.facet} (install files missing?)")
        self.sight = los.Sight(self.walk)
        self.mobs: dict[int, Mob] = {}
        self.items: dict[int, dict] = {}      # serial -> {graphic, amount, container, x, y, z, kind, ...}
        self.viewing: set[int] = set()
        self.viewing_items: set[int] = set()
        self.next_serial = 0x00300000
        self.next_item = 0x60000000
        self.cid = 0x00400000
        self.pending = None                   # the target cursor up: {"kind", "cid", ...}
        self.metrics = {"kills": 0, "kills_by": {}, "player_damage_taken": 0.0, "pet_damage_taken": {},
                        "team_damage": 0.0, "vet_uses": 0, "potions": 0, "bard": {}, "steps": 0, "denied_steps": 0,
                        "recalls": 0, "deaths": 0, "pet_deaths": [], "gold_looted": 0, "violations": [],
                        "aggressive_acts": 0, "kill_commands": 0, "guard_commands": 0, "follow_commands": 0,
                        "fights": [], "hunt_s": 0.0}
        self.skill_ready = 0.0
        self.calm_ready = 0.0
        self.last_use_t = -1e9
        self.potion_ready = 0.0
        self.vet_end = None                   # (finish time, moved during the channel?)
        self.vet_moved = False
        self.last_step_t = 0.0
        self.penalty = 0
        self.gump_n = 0x7700
        self.recall_at = None                 # (arrival time, destination row)
        self.spawn_due = []                   # (time, spawn index)
        self.player_log = []                  # (time, source, damage, hits after): the last 15 s
        self._build()

    # ------------------------------------------------------------ setup
    def _serial(self) -> int:
        self.next_serial += 1
        return self.next_serial

    def _item_serial(self) -> int:
        self.next_item += 1
        return self.next_item

    def stand_z(self, x, y, near_z=0) -> int:
        """The surface z to stand on at (x, y) nearest `near_z` (the map's walkable surfaces)."""
        best = None
        for z, h, flags, avg, kind in self.walk.objects(x, y):
            if flags & pathfind.POF_SURFACE and (best is None or abs(avg - near_z) < abs(best - near_z)):
                best = avg
        return near_z if best is None else best

    def _build(self):
        sc = self.sc
        p = sc["player"]
        x, y = p["start"][:2]
        z = self.stand_z(x, y, p["start"][2] if len(p["start"]) > 2 else 0)
        self.player = Mob(p["serial"], "player", "player", p["name"], p["body"], x, y, z,
                          hits=p["hits"], hits_max=p["hits"], noto=1, spec=p)
        self.player.stam, self.player.stam_max = p.get("stam", 25), p.get("stam", 25)
        self.player.mana, self.player.mana_max = p.get("mana", 100), p.get("mana", 100)
        self.mobs[self.player.serial] = self.player
        for i, pet in enumerate(sc["pets"]):
            px, py = x + (1 if i == 0 else 0), y + (0 if i == 0 else 1)
            m = Mob(pet["serial"], "pet", pet["key"], pet["name"], pet["body"], px, py, self.stand_z(px, py, z),
                    hits=pet["hits"], hits_max=pet["hits"], noto=2, spec=pet, mode="follow")
            self.mobs[m.serial] = m
        lo = sc["loadout"]
        self.backpack = lo["backpack"]
        self.items[self.backpack] = {"graphic": 0x0E75, "amount": 1, "layer": 0x15, "owner": self.player.serial}
        for it in lo["items"]:
            self.items[it["serial"]] = {"graphic": it["graphic"], "amount": it.get("amount", 1),
                                        "container": it.get("container", self.backpack), "kind": it.get("kind")}
        self.gold_pile = lo.get("gold_serial", self._item_serial())
        self.items[self.gold_pile] = {"graphic": combat.GOLD_GRAPHIC, "amount": lo.get("gold", 0),
                                      "container": self.backpack, "kind": "gold"}
        self.spawns = []
        for s in sc["spawns"]:
            self.spawns.append({**s, "mob": None})
        for i in range(len(self.spawns)):
            self._spawn(i)

    def _spawn(self, i):
        sp = self.spawns[i]
        spec = self.sc["creatures"][sp["key"]]
        x, y = sp["at"][:2]
        z = self.stand_z(x, y, sp["at"][2] if len(sp["at"]) > 2 else 0)
        if self._occupied(x, y):
            free = [(x + dx, y + dy) for dx, dy in nav.DIR_DELTA if not self._occupied(x + dx, y + dy)]
            if not free:
                self.spawn_due.append((self.t + 5.0, i))
                return
            x, y = free[0]
        lo, hi = spec["hits"] if isinstance(spec["hits"], list) else (spec["hits"], spec["hits"])
        hp = self.rng.randint(int(lo), int(hi))
        m = Mob(self._serial(), "monster", sp["key"], spec["name"], spec["body"], x, y, z, hits=hp, hits_max=hp,
                noto=spec.get("notoriety", 3), spec=spec, home=(x, y, z), spawn=i)
        m.next_think = self.t + self.rng.uniform(0, THINK_S)
        self.mobs[m.serial] = m
        sp["mob"] = m.serial

    def login_packets(self) -> list[bytes]:
        """What the server sends at login: our character, pack contents, pets and the view."""
        pl = self.player
        out = [P.login(pl.serial, pl.body, pl.x, pl.y, pl.z),
               P.mobile(pl.serial, pl.body, pl.x, pl.y, pl.z, noto=1, d=pl.d),
               P.equip(self.backpack, 0x0E75, 0x15, pl.serial)]
        for s, it in self.items.items():
            if it.get("container") is not None:
                out.append(P.contained(s, it["graphic"], it["amount"], it["container"]))
        out += [P.hits(pl.serial, int(pl.hits), int(pl.hits_max)), P.mana(pl.serial, pl.mana, pl.mana_max),
                P.stam(pl.serial, pl.stam, pl.stam_max)]
        self.out += out
        self.last_step_t = self.t
        self._view()
        return []

    # ------------------------------------------------------------ helpers
    def send(self, *pkts):
        self.out.extend(pkts)

    def drain(self) -> list[bytes]:
        out, self.out = self.out, []
        return out

    def _occupied(self, x, y, but=None) -> bool:
        return any(m.alive and not m.ghost and m.x == x and m.y == y and m.serial != but for m in self.mobs.values())

    def _in_view(self, m) -> bool:
        return cheb(m.pos, self.player.pos) <= VIEW

    def _show(self, m: Mob):
        flags = 0x40 if m.warmode else 0
        body = GHOST_BODY if m.ghost and m.kind == "player" else m.body
        self.send(P.mobile(m.serial, body, m.x, m.y, m.z, noto=m.noto, flags=flags, d=m.d),
                  P.name(m.serial, m.name), P.hits(m.serial, m.pct(), 100))
        if m.kind == "pet":
            self.send(P.says(m.serial, m.name, "(bonded)", kind=0, hue=946, body=m.body))   # its click label's line
        if m.kind == "pet" and m.ghost:
            self.send(P.pet_dead(m.serial))

    def _view(self):
        for s, m in self.mobs.items():
            if s == self.player.serial:
                continue
            near = (m.alive or m.ghost) and self._in_view(m)
            if near and s not in self.viewing:
                self.viewing.add(s)
                self._show(m)
            elif not near and s in self.viewing:
                self.viewing.discard(s)          # the client drops it by range itself
        for s, it in self.items.items():
            if it.get("kind") == "corpse":
                near = cheb((it["x"], it["y"]), self.player.pos) <= VIEW
                if near and s not in self.viewing_items:
                    self.viewing_items.add(s)
                    self.send(P.ground_item(s, combat.CORPSE_GRAPHIC, it["body"], it["x"], it["y"], it["z"]),
                              P.corpse_flags(s, it["owner"], it["name"], it["noto"]))
                elif not near:
                    self.viewing_items.discard(s)

    def _moved(self, m: Mob):
        if m.serial in self.viewing and self._in_view(m):
            self.send(P.move(m.serial, m.x, m.y, m.z, m.d))
        self._view()

    def _warm(self, m: Mob, on: bool):
        if m.warmode != on:
            m.warmode = on
            if m.serial in self.viewing:
                self.send(P.mobile(m.serial, m.body, m.x, m.y, m.z, noto=m.noto, flags=0x40 if on else 0, d=m.d))

    def _overhead(self, m: Mob, text: str, kind=0):
        if m.serial == self.player.serial or m.serial in self.viewing:
            self.send(P.says(m.serial, m.name, text, kind=kind, body=m.body))

    def _sys(self, text):
        self.send(P.system_text(text))

    def can_see(self, a: Mob, b: Mob) -> bool:
        return self.sight.can_see(a.pos, b.pos)

    def pets(self):
        return [m for m in self.mobs.values() if m.kind == "pet"]

    def team(self):
        return [self.player] + [p for p in self.pets() if p.alive and not p.ghost]

    def roll(self, r) -> float:
        """A value from (lo, hi) uniformly, or (lo, hi, mode) triangularly."""
        if len(r) >= 3:
            return self.rng.triangular(r[0], r[1], min(max(r[2], r[0]), r[1]))
        return self.rng.uniform(r[0], r[1])

    def count(self, key, sub=None, n=1):
        if sub is None:
            self.metrics[key] = self.metrics.get(key, 0) + n
        else:
            d = self.metrics.setdefault(key, {})
            d[sub] = d.get(sub, 0) + n

    # ------------------------------------------------------------ damage
    def hurt(self, victim: Mob, amount: float, by: Mob | None, kind="melee"):
        if not victim.alive or victim.ghost or amount <= 0:
            return
        if by is not None and by.kind in ("player", "pet") and self.penalty:
            amount = 1                       # Stationary Penalty: our and our followers' damage is 1
        if victim.discord_until > self.t and by is not None and by.kind != "monster":
            amount *= 1 + self.sc["bard"]["discord_taken"]
        if by is not None and by.provoke and victim.kind == "monster":
            amount *= self.sc["bard"].get("provoke_x", 1.0)
        if by is not None and by.discord_until > self.t:
            amount *= 1 - self.sc["bard"]["discord_dealt"]
        n = max(1, int(round(amount)))
        victim.hits -= n
        victim.last_hurt_t = self.t
        if by is not None:
            victim.damage_by[by.serial] = victim.damage_by.get(by.serial, 0) + n
            if victim.kind == "monster":
                w = by.spec.get("threat_x", 1.0) if by.kind == "pet" else 1.0
                victim.threat[by.serial] = victim.threat.get(by.serial, 0) + n * w
                self.metrics["team_damage"] += n if by.kind in ("player", "pet") else 0
        if victim.kind == "player":
            self.metrics["player_damage_taken"] += n
            self.player_log.append((self.t, by.key if by is not None else kind, n, int(victim.hits)))
            self.player_log = [e for e in self.player_log if self.t - e[0] <= 15.0]
        elif victim.kind == "pet":
            self.count("pet_damage_taken", victim.name, n)
        self._overhead(victim, f"-{n}")
        if victim.kind == "player":
            self.send(P.hits(victim.serial, max(0, int(victim.hits)), int(victim.hits_max)))
        elif victim.serial in self.viewing:
            self.send(P.hits(victim.serial, victim.pct(), 100))
        if victim.hits <= 0:
            self.die(victim, by)

    def heal(self, m: Mob, amount: float):
        if not m.alive or m.ghost:
            return
        n = int(round(min(amount, m.hits_max - m.hits)))
        m.hits += n
        self._overhead(m, f"+{n}")
        if m.kind == "player":
            self.send(P.hits(m.serial, int(m.hits), int(m.hits_max)))
        elif m.serial in self.viewing:
            self.send(P.hits(m.serial, m.pct(), 100))

    def die(self, m: Mob, by: Mob | None):
        if m.kind == "player":
            m.hits, m.ghost = 0, True
            self.metrics["deaths"] += 1
            by_src = {}
            for _, k, n, _h in self.player_log:
                by_src[k] = by_src.get(k, 0) + n
            self.metrics.setdefault("death_causes", []).append(
                {"t": round(self.t, 1), "at": list(m.pos), "last_15s": by_src,
                 "from_hits": max((h + n for _, _, n, h in self.player_log), default=0)})
            self.send(P.hits(m.serial, 0, int(m.hits_max)),
                      P.mobile(m.serial, GHOST_BODY, m.x, m.y, m.z, noto=1, d=m.d))
            return
        if m.kind == "pet":
            m.hits, m.ghost, m.target, m.mode = 0, True, None, "follow"
            self.metrics["pet_deaths"].append({"t": round(self.t, 1), "pet": m.name})
            if m.serial in self.viewing:
                self.send(P.hits(m.serial, 0, 100), P.pet_dead(m.serial))
            for o in self.mobs.values():
                if o.target == m.serial:
                    o.target = None
                o.threat.pop(m.serial, None)
            return
        m.alive = False
        corpse = self._item_serial()
        ours = sum(v for s, v in m.damage_by.items() if s in {t.serial for t in self.team()} or
                   (s in self.mobs and self.mobs[s].kind == "pet"))
        noto = 3 if ours * 2 >= sum(m.damage_by.values()) else 1
        self.items[corpse] = {"kind": "corpse", "x": m.x, "y": m.y, "z": m.z, "body": m.body, "owner": m.serial,
                              "name": f"{m.name} corpse", "noto": noto, "t": self.t}
        lo, hi = m.spec.get("gold", [0, 0])
        gold = self.rng.randint(int(lo), int(hi)) if hi else 0
        if gold:
            g = self._item_serial()
            self.items[g] = {"graphic": combat.GOLD_GRAPHIC, "amount": gold, "container": corpse, "kind": "gold"}
        if m.serial in self.viewing:
            self.send(P.display_death(m.serial, corpse), P.delete(m.serial))
        self.viewing.discard(m.serial)
        self.viewing_items.add(corpse)
        self.send(P.ground_item(corpse, combat.CORPSE_GRAPHIC, m.body, m.x, m.y, m.z),
                  P.corpse_flags(corpse, m.serial, f"{m.name} corpse", noto))
        self.metrics["kills"] += 1
        self.count("kills_by", m.key)
        self.metrics["fights"].append({"key": m.key, "t": round(self.t, 1), "hits": m.hits_max,
                                       "damage_by": {self.mobs[s].name if s in self.mobs else hex(s): v
                                                     for s, v in m.damage_by.items()}})
        for o in self.mobs.values():
            if o.target == m.serial:
                o.target = None
            if o.kill == m.serial:
                o.kill, o.mode = None, "follow"     # "All Kill": back to the owner after the kill
            if o.provoke and o.provoke[0] == m.serial:
                o.provoke = None
        if m.spawn is not None:
            lo_s, hi_s = self.spawns[m.spawn].get("respawn_s", self.sc.get("respawn_s", [240, 360]))
            self.spawn_due.append((self.t + self.rng.uniform(lo_s, hi_s), m.spawn))
        del self.mobs[m.serial]

    # ------------------------------------------------------------ movement
    def _step_toward(self, m: Mob, goal, radius: int) -> bool:
        """One step of `m` toward within `radius` of goal (x, y): greedy, else an A* route (cached)."""
        if cheb(m.pos, goal) <= radius:
            m.path = []
            return False
        if m.path:
            nxt = m.path.pop(0)
            if not self._occupied(nxt[0], nxt[1], m.serial) and cheb(nxt, m.pos) == 1:
                self._place(m, nxt)
                return True
            m.path = []
        here = cheb(m.pos, goal)
        best = None
        for d in TELEPORT_DIRS:
            to = self.walk.can_walk(m.x, m.y, m.z, d)
            if to is None or self._occupied(to[0], to[1], m.serial):
                continue
            score = (cheb(to, goal), math.hypot(to[0] - goal[0], to[1] - goal[1]))
            if best is None or score < best[0]:
                best = (score, to, d)
        if best is not None and best[0][0] < here:
            self._place(m, best[1], best[2])
            m.stuck = 0
            return True
        m.stuck += 1
        if m.stuck >= 2:
            occ = {(o.x, o.y) for o in self.mobs.values() if o.alive and not o.ghost and o.serial != m.serial}
            path = pathfind.plan(self.walk, m.pos, nav.within(goal, radius), occupied=occ - {tuple(goal[:2])},
                                 max_expand=4000, max_steps=40)
            m.stuck = 0
            if path and len(path) > 1:
                m.path = [tuple(p) for p in path[2:]]
                self._place(m, tuple(path[1]))
                return True
        return False

    def _place(self, m: Mob, to, d=None):
        m.d = tile_dir(m.pos, to) if d is None else d
        m.x, m.y, m.z = to[0], to[1], to[2]
        self._moved(m)

    def _teleport(self, m: Mob, to):
        m.x, m.y = to[0], to[1]
        m.z = self.stand_z(to[0], to[1], to[2] if len(to) > 2 else 0)
        m.path = []
        if m.kind == "player":
            self.send(P.mobile(m.serial, GHOST_BODY if m.ghost else m.body, m.x, m.y, m.z, noto=1, d=m.d))
        self._view()

    # ------------------------------------------------------------ the world's clock
    def run_until(self, t: float):
        while self.t + TICK_S <= t + 1e-9:
            self.t += TICK_S
            self._tick()

    def _tick(self):
        t = self.t
        if self.recall_at and t >= self.recall_at[0]:
            _, dest = self.recall_at
            self.recall_at = None
            if not self.player.ghost:            # died during the cast: no arrival
                self._land(dest)
        if self.vet_end and t >= self.vet_end:
            self._vet_finish()
        for due, i in [x for x in self.spawn_due if x[0] <= t]:
            self.spawn_due.remove((due, i))
            self._spawn(i)
        for s, it in list(self.items.items()):
            if it.get("kind") == "corpse" and t - it["t"] > CORPSE_DECAY_S:
                self._remove_corpse(s)
        if not self.penalty and t - self.last_step_t >= PENALTY_STILL_S:
            self._apply_penalty()
        pl = self.player
        for m in list(self.mobs.values()):
            if m.serial not in self.mobs:
                continue
            self._effects(m)
            if m.kind == "monster" and m.alive:
                if cheb(m.pos, pl.pos) <= 2 * VIEW or m.target is not None:
                    self._monster(m)
            elif m.kind == "pet":
                self._pet(m)
        area = self.sc.get("area")
        if area and not pl.ghost and area[0] <= pl.x <= area[2] and area[1] <= pl.y <= area[3]:
            self.metrics["hunt_s"] += TICK_S
        if not pl.ghost and t - pl.last_hurt_t > 10 and pl.hits < pl.hits_max and int(t / TICK_S) % int(REGEN_S / TICK_S) == 0:
            pl.hits += 1
            self.send(P.hits(pl.serial, int(pl.hits), int(pl.hits_max)))

    def _effects(self, m: Mob):
        t = self.t
        if m.poison_until > t and t >= m.poison_next:
            m.poison_next = t + 2.0
            self.hurt(m, m.spec.get("_poison_dmg", 4), None, "poison")
        if m.bleed_until > t and t >= m.bleed_next:
            m.bleed_next = t + 2.0
            self.hurt(m, self.rng.randint(3, 6), None, "bleed")
            if m.kind == "player" and m.bleed_until - t < 2.0:
                self._sys("You are no longer bleeding.")
        if (m.calm_until > t or m.provoke) and int(t / TICK_S) % int(1.0 / TICK_S) == 0:
            diff = m.spec.get("difficulty") or 100
            if self.rng.random() < diff / 100.0 / 100.0:     # (difficulty / 100) % per second (patch 2020-04-07)
                m.calm_until = 0.0
                m.provoke = None
                m.immune_until = t + diff / 10.0
                self._overhead(m, "*Barding Break*")
                m.threat[self.player.serial] = m.threat.get(self.player.serial, 0) + self.sc["bard"]["break_threat"]
        if m.provoke and m.provoke[1] <= t:
            m.provoke = None

    # ------------------------------------------------------------ monsters
    def _monster(self, m: Mob):
        t = self.t
        if m.calm_until > t:
            self._warm(m, False)
            return
        if t >= m.next_think:
            m.next_think = t + THINK_S
            self._think(m)
        tgt = self.mobs.get(m.target) if m.target is not None else None
        if tgt is None or not tgt.alive or tgt.ghost:
            m.target = None
            self._warm(m, False)
            if cheb(m.pos, m.home) > 2 and t >= m.next_step:
                m.next_step = t + m.spec["step_s"] * 2
                self._step_toward(m, m.home, 1)
            elif m.hits < m.hits_max and int(t / TICK_S) % int(REGEN_S / TICK_S) == 0:
                m.hits = min(m.hits_max, m.hits + max(1, m.hits_max * 0.01))
            return
        self._warm(m, True)
        self._attack(m, tgt, m.spec)

    def _think(self, m: Mob):
        """Pick the target: a provoked monster's victim; else the highest threat in sight (damage taken,
        our aggressive acts); else the nearest of us and our pets within aggro range and in sight."""
        t = self.t
        if m.provoke and m.provoke[0] in self.mobs:
            m.target = m.provoke[0]
            return
        cur = self.mobs.get(m.target) if m.target is not None else None
        if cur is not None and (cheb(m.pos, m.home) > LEASH or (t - m.seen_target_t > LOST_S)):
            m.target, m.threat = None, {}
            return
        if cur is not None and self.can_see(m, cur):
            m.seen_target_t = t
        cands = [o for o in self.team() if cheb(o.pos, m.pos) <= max(m.spec["aggro_range"], 12)]
        live = {o.serial: o for o in cands}
        threat = {s: v for s, v in m.threat.items() if s in live}
        if threat:
            best = max(threat, key=threat.get)
            if best != m.target and (cur is None or threat[best] > threat.get(m.target, 0) * 1.1):
                m.target, m.seen_target_t = best, t
            return
        if cur is not None:
            return
        near = [o for o in cands if cheb(o.pos, m.pos) <= m.spec["aggro_range"] and self.can_see(m, o)]
        guards = [o for o in near if o.kind == "pet" and o.mode in ("guard", "kill")]
        if near:
            # guarding pets within reach take the aggro (knowledge #2074: "guarding pets take aggro from mobs
            # within about 10 tiles, though a mob still targets you if you are the only one in range")
            o = min(guards or near, key=lambda o: (cheb(o.pos, m.pos), o.kind != "player"))
            m.target, m.seen_target_t = o.serial, t
            m.threat[o.serial] = m.threat.get(o.serial, 0) + 1

    def _attack(self, m: Mob, tgt: Mob, spec: dict):
        """Swing or cast at `tgt` when in reach and sight, else close in."""
        t = self.t
        ranged = spec.get("ranged")
        d = cheb(m.pos, tgt.pos)
        if ranged and d <= ranged["range"] and self.can_see(m, tgt):
            if t >= m.next_swing:
                m.next_swing = t + ranged["every_s"] * self.rng.uniform(0.85, 1.15)
                self.hurt(tgt, self.roll(ranged["damage"]), m, "spell")
                self._specials(m, tgt, spec)
            if d <= ranged.get("keep", ranged["range"]) or not spec.get("melee"):
                return
        if d <= 1 and abs(m.z - tgt.z) <= 15:
            if t >= m.next_swing and spec.get("melee"):
                m.next_swing = t + spec["swing_s"] * self.rng.uniform(0.9, 1.1)
                if self.rng.random() < spec.get("hit_chance", 0.8):
                    self.hurt(tgt, self.roll(spec["melee"]) * (self.sc["defaults"]["damage_to_player_x"]
                                                               if tgt.kind == "player" else 1.0), m)
                    self._specials(m, tgt, spec)
            return
        if t >= m.next_step:
            m.next_step = t + spec["step_s"]
            self._step_toward(m, tgt.pos, 1)

    def _specials(self, m: Mob, tgt: Mob, spec: dict):
        for sp in spec.get("specials", []):
            if self.rng.random() >= sp.get("chance", 0):
                continue
            kind = sp["kind"]
            if kind == "bleed" and tgt.bleed_until <= self.t:
                tgt.bleed_until, tgt.bleed_next = self.t + sp.get("seconds", 10), self.t + 2
                if tgt.kind == "player":
                    self._sys("The creature strikes ferociously, causing you to bleed!")
            elif kind == "poison" and tgt.poison_until <= self.t:
                tgt.poison_until, tgt.poison_next = self.t + sp.get("seconds", 12), self.t + 2
                tgt.spec = {**tgt.spec, "_poison_dmg": sp.get("damage", 4)}
                self._overhead(tgt, tamer.TEXT_POISONED)
            elif kind == "drain":
                if tgt.kind == "player":
                    self._sys("They begin to draw blood from you!")
                self.hurt(tgt, sp.get("damage", 8), m, "drain")
                self.heal(m, sp.get("damage", 8))

    # ------------------------------------------------------------ pets
    def _pet(self, m: Mob):
        t = self.t
        owner = self.player
        follow = m.spec.get("follow", 1)
        if m.ghost or not m.alive:
            if t >= m.next_step and cheb(m.pos, owner.pos) > follow + 1:
                m.next_step = t + m.spec["step_s"]
                self._step_toward(m, owner.pos, follow + 1)
            return
        if t >= m.next_think:
            m.next_think = t + THINK_S
            self._pet_think(m)
        tgt = self.mobs.get(m.target) if m.target is not None else None
        if tgt is not None and tgt.alive and cheb(owner.pos, tgt.pos) <= m.spec.get("leash", 14):
            self._warm(m, True)
            self._pet_attack(m, tgt)
        else:
            m.target = None
            self._warm(m, False)
            if m.mode != "stay" and t >= m.next_step and cheb(m.pos, owner.pos) > follow:
                m.next_step = t + m.spec["step_s"]
                self._step_toward(m, owner.pos, follow)
            elif m.spec.get("devour") and m.hits < m.hits_max * 0.9 and t >= m.devour_t:
                c = next((s for s, it in self.items.items() if it.get("kind") == "corpse"
                          and cheb((it["x"], it["y"]), m.pos) <= 2), None)
                if c is not None:
                    m.devour_t = t + m.spec["devour"].get("every_s", 14)
                    self._overhead(m, "*devours corpse*")
                    for _ in range(2):
                        self.heal(m, m.spec["devour"].get("heal", 18))
            if m.hits < m.hits_max and t - m.last_hurt_t > 10 and int(t / TICK_S) % int(REGEN_S / TICK_S) == 0:
                m.hits = min(m.hits_max, m.hits + 1)

    def _pet_think(self, m: Mob):
        """Guard: the nearest monster fighting us or the pets (or hurt by us lately); kill: the given
        target; follow/stay: none (a monster hitting the pet itself is still fought back)."""
        owner = self.player
        if m.mode == "kill" and m.kill in self.mobs:
            m.target = m.kill
            return
        team = {o.serial for o in self.team()}
        on_us = [o for o in self.mobs.values() if o.kind == "monster" and o.alive
                 and (o.target in team or self.t - max(o.last_hurt_t, 0) < 5 and any(s in team for s in o.damage_by))
                 and cheb(o.pos, owner.pos) <= m.spec.get("leash", 14)]
        if m.mode == "guard" and on_us:
            if m.target not in {o.serial for o in on_us}:
                m.target = min(on_us, key=lambda o: (o.target != owner.serial, cheb(o.pos, owner.pos))).serial
            return
        if m.mode == "follow":
            m.target = None                      # following: it comes along, whatever hits it [INFERENCE]
            return
        mine = [o for o in on_us if o.target == m.serial]
        if mine and m.target is None:
            m.target = min(mine, key=lambda o: cheb(o.pos, m.pos)).serial

    def _pet_attack(self, m: Mob, tgt: Mob):
        t = self.t
        spec = m.spec
        ranged = spec.get("ranged")
        d = cheb(m.pos, tgt.pos)
        if ranged and d <= ranged["range"] and self.can_see(m, tgt):
            if t >= m.next_swing:
                m.next_swing = t + ranged["every_s"] * self.rng.uniform(0.85, 1.15)
                self.hurt(tgt, self.roll(ranged["damage"]), m, "spell")
            if cheb(m.pos, self.player.pos) > spec.get("follow", 2) + 1 and t >= m.next_step:
                m.next_step = t + spec["step_s"]
                self._step_toward(m, self.player.pos, spec.get("follow", 2))
            return
        if d <= 1:
            if spec.get("melee") and t >= m.next_swing:
                m.next_swing = t + spec["swing_s"] * self.rng.uniform(0.9, 1.1)
                if self.rng.random() < spec.get("hit_chance", 0.85):
                    self.hurt(tgt, self.roll(spec["melee"]), m)
                    for sp in spec.get("specials", []):
                        if sp["kind"] == "poison" and self.rng.random() < sp.get("chance", 0):
                            tgt.poison_until, tgt.poison_next = t + sp.get("seconds", 12), t + 2
                            tgt.spec = {**tgt.spec, "_poison_dmg": sp.get("damage", 4)}
                            self._overhead(tgt, tamer.TEXT_POISONED)
            return
        if t >= m.next_step:
            m.next_step = t + spec["step_s"]
            self._step_toward(m, tgt.pos, 1)

    # ------------------------------------------------------------ client packets
    def c2s(self, p: bytes, seq: int | None = None):
        pid = p[0]
        if pid == 0x02:
            self._walk(p)
        elif pid == 0xAD:
            self._speech(p)
        elif pid == 0x12 and len(p) > 4 and p[3] == 0x24:
            self._skill(int(p[4:-1].decode("ascii").split()[0]))
        elif pid == 0x6C:
            self._target(p)
        elif pid == 0x06:
            self._dclick(int.from_bytes(p[1:5], "big"))
        elif pid == 0x07:
            self._lift(int.from_bytes(p[1:5], "big"), int.from_bytes(p[5:7], "big"))
        elif pid == 0x08:
            self._drop(p)
        elif pid == 0xB1:
            self._gump_reply(p)
        elif pid == 0x72:
            self.metrics["violations"].append({"t": round(self.t, 1), "what": "war mode"})
            self.send(P.warmode(bool(p[1])))
        elif pid == 0x05:
            self.metrics["violations"].append({"t": round(self.t, 1), "what": "attack",
                                               "serial": hex(int.from_bytes(p[1:5], "big"))})
        elif pid == 0xBF and p[3:5] == b"\x00\x15":
            self.metrics["violations"].append({"t": round(self.t, 1), "what": "context menu pick"})
        elif pid == 0xFF and p[3:7] == b"\x00\x00\x00\x04":
            self.metrics["violations"].append({"t": round(self.t, 1), "what": "spell"})
        elif pid == 0x09:
            s = int.from_bytes(p[1:5], "big")
            m = self.mobs.get(s)
            if m is not None:
                self.send(P.says(s, m.name, m.name, kind=6, body=m.body))

    # walking
    def _walk(self, p):
        pl = self.player
        seq, d = p[2], p[1] & 7
        if pl.ghost:
            self.send(P.deny_walk(seq, pl.x, pl.y, pl.z, pl.d))
            return
        if d != pl.d:
            pl.d = d
            self.send(P.confirm_walk(seq))
            return
        to = self.walk.can_walk(pl.x, pl.y, pl.z, d)
        blocker = None if to is None else next((m for m in self.mobs.values() if m.alive and not m.ghost
                                                and (m.x, m.y) == to[:2] and m.serial != pl.serial), None)
        if to is None or (blocker is not None and (blocker.kind == "monster" or pl.stam < pl.stam_max)):
            self.metrics["denied_steps"] += 1
            self.send(P.deny_walk(seq, pl.x, pl.y, pl.z, pl.d))
            return
        pl.x, pl.y, pl.z = to
        self.send(P.confirm_walk(seq))
        self.metrics["steps"] += 1
        self.last_step_t = self.t
        if self.vet_end:
            self.vet_moved = True
        if self.penalty:
            self.penalty -= 1
            self.send(P.penalty_buff(pl.serial, self.penalty) if self.penalty else P.penalty_remove(pl.serial))
        tp = next((tp for tp in self.sc.get("teleporters", []) if tuple(tp["from"][:2]) == (pl.x, pl.y)), None)
        if tp is not None:
            self._teleport(pl, tp["to"])
        self._view()

    # speech: pet commands
    def _speech(self, p):
        from world.parsers import parse_packet
        text = (parse_packet("c2s", p).get("text") or "").strip().lower()
        if tamer.irreversible(text):
            self.metrics["violations"].append({"t": round(self.t, 1), "what": "release/transfer speech",
                                               "text": text})
            return
        pets = [m for m in self.pets() if m.alive and not m.ghost]
        if text == "all guard me":
            self.metrics["guard_commands"] += 1
            for m in pets:
                m.mode, m.kill = "guard", None
        elif text == "all follow me":
            self.metrics["follow_commands"] += 1
            for m in pets:
                m.mode, m.kill, m.target = "follow", None, None
        elif text in ("all stop", "all stay"):
            for m in pets:
                m.mode, m.kill, m.target = "stay", None, None
        elif text == "all kill":
            self.metrics["kill_commands"] += 1
            self._cursor({"kind": "kill"})

    def _cursor(self, pending):
        self.cid += 1
        self.pending = {**pending, "cid": self.cid}
        self.send(P.cursor(self.cid, 1))

    # bard skills
    def _skill(self, sid):
        t = self.t
        skill = {tamer.SKILL_DISCORD: "discord", tamer.SKILL_PEACE: "peace", tamer.SKILL_PROVO: "provoke"}.get(sid)
        if skill is None:
            return
        if t < self.skill_ready:
            self.send(P.cliloc(tamer.CLILOC_SKILL_WAIT))
            return
        if skill != "discord" and t < self.calm_ready:
            self._sys(tamer.TEXT_CALM_WAIT)
            return
        prompt = {"discord": "What do you wish to discord? (you may target yourself for an area effect or the "
                             "ground for a group effect)",
                  "peace": "What do you wish to pacify? (you may target yourself for an area effect or the ground "
                           "for a group effect)",
                  "provoke": "What do you wish to incite? (you may target the ground for a group effect)"}[skill]
        self.skill_ready = t + 1.0
        self._sys(prompt)
        self._cursor({"kind": skill})

    def _target(self, p):
        from world.parsers import parse_packet
        f = parse_packet("c2s", p)
        pend, self.pending = self.pending, None
        if pend is None or f.get("cursor_id") != pend["cid"]:
            return
        serial = f.get("serial") or 0
        tgt = self.mobs.get(serial)
        if tgt is not None and tgt.kind == "pet":
            self.metrics["violations"].append({"t": round(self.t, 1), "what": f"{pend['kind']} at our pet"})
        if tgt is None or not tgt.alive or tgt.kind != "monster":
            return
        pl = self.player
        if cheb(pl.pos, tgt.pos) > self.sc["bard"]["range"]:
            self.send(P.cliloc(500446))
            return
        if not self.can_see(pl, tgt):
            self.send(P.cliloc(500237))
            return
        kind = pend["kind"]
        if kind == "kill":
            for m in self.pets():
                if m.alive and not m.ghost:
                    m.mode, m.kill, m.target = "kill", serial, serial
            tgt.threat[pl.serial] = tgt.threat.get(pl.serial, 0) + self.sc["bard"]["act_threat"]
            self.metrics["aggressive_acts"] += 1
            return
        if kind == "provoke" and "first" not in pend:
            self._sys(tamer.TEXT_INCITE_SECOND)
            self._cursor({"kind": "provoke", "first": serial})
            return
        self._bard(kind, tgt, self.mobs.get(pend.get("first")) if kind == "provoke" else None)

    def _bard(self, kind, tgt: Mob, first: Mob | None):
        t, b, pl = self.t, self.sc["bard"], self.player
        target = first if kind == "provoke" else tgt
        if target is None:
            return
        if kind != "discord" and (target.immune_until > t or (kind == "provoke" and tgt.immune_until > t)):
            self._sys(tamer.TEXT_IMMUNE)
            self.skill_ready = t + tamer.SKILL_GAP_S
            return
        target.threat[pl.serial] = target.threat.get(pl.serial, 0) + b["act_threat"]
        self.metrics["aggressive_acts"] += 1
        diff = target.spec.get("difficulty") or 100.0
        s = b["effective_skill"] + (25 if kind == "discord" and (target.calm_until > t or target.provoke) else 0)
        p = min(0.95, max(s / 100.0 - diff / 100.0, b["min_chance_factor"] * s / 100.0))   # knowledge #8530/#5239
        self.skill_ready = t + tamer.SKILL_GAP_S
        word = {"discord": "discord your opponent", "peace": "pacify your opponent",
                "provoke": "incite anger amongst your opponents"}[kind]
        if diff >= b.get("beyond_difficulty", 1e9):
            self._sys(f"You fail to {word}. You estimate the task to be beyond your skill.")
            self.count("bard", f"{kind}_beyond")
            return
        if self.rng.random() >= p:
            self._sys(f"You fail to {word}. You estimate the task to be fairly reasonable.")
            self.count("bard", f"{kind}_fail")
            if kind == "provoke":
                target.target = pl.serial          # a failed incite turns it on the bard [INFERENCE]
            return
        self.count("bard", f"{kind}_ok")
        secs = (b["seconds_easy"] if diff < b["easy_below_difficulty"] else b["seconds"])[kind]
        if kind == "discord":
            target.discord_until = t + secs
            self._sys(f"You play successfully, disrupting your opponent and weakening them significantly. "
                      f"({secs // 60}m{(' %ds' % (secs % 60)) if secs % 60 else ''})")
        elif kind == "peace":
            target.calm_until, target.target = t + secs, None
            self.calm_ready = t + tamer.CALM_SUCCESS_S
            self._sys(f"You play successfully, pacifying your target. ({secs}s)")
        else:
            first.provoke, tgt.provoke = (tgt.serial, t + secs), (first.serial, t + secs)
            first.target, tgt.target = tgt.serial, first.serial
            self.calm_ready = t + tamer.CALM_SUCCESS_S
            self._sys(f"You play successfully, provoking your targets. ({secs}s)")

    # items: vet supplies, potions, tome, containers, corpses
    def _dclick(self, s):
        it = self.items.get(s)
        pl = self.player
        if s == self.backpack:
            self._open(s, BACKPACK_GUMP)
            return
        if it is None:
            return
        kind = it.get("kind")
        if kind == "corpse":
            if cheb((it["x"], it["y"]), pl.pos) > 2:
                self.send(P.cliloc(500446))
                return
            self._open(s, CORPSE_GUMP)
            return
        if kind == "bag":
            self._open(s, BAG_GUMP)
            return
        if self.t - self.last_use_t < ACTION_DELAY_S:
            self.send(P.cliloc(tamer.CLILOC_ACTION_WAIT))
            return
        self.last_use_t = self.t
        if kind == "vet":
            self._vet(s)
        elif kind == "heal_potion":
            self._drink(s)
        elif kind == "tome":
            self.gump_n += 1
            rows = self.sc["recall"]["rows"]
            layout = ("{ gumppic 10 10 116 }{ text 165 29 2655 0 18 0 1 0 0 0 }{ text 97 59 2655 1 18 0 1 0 0 0 }"
                      "{ button 185 55 9721 9724 1 0 3 }{ gumppic 364 32 2271 }{ text 429 32 2655 2 18 0 1 0 0 0 }")
            for i, _row in enumerate(rows):
                y = 96 + 24 * i
                layout += (f"{{ button 88 {y} 2118 2117 1 0 {TOME_ROW_BUTTON0 + i} }}"
                           f"{{ button 109 {y - 2} 210 211 1 0 {200 + i} }}"
                           f"{{ text 133 {y - 1} {ROW_HUES[0] if i == 0 else ROW_HUES[1]} {3 + i} 18 0 1 0 0 0 }}")
            lines = ["A rune tome", "Manage Runes", f"{it.get('charges', 30)}/50"] + [r["name"] for r in rows]
            self.send(P.gump(self.gump_n, escape.RUNETOME_GUMP, layout, lines))

    def _open(self, s, gump):
        self.send(P.open_container(s, gump))
        for c, it in self.items.items():
            if it.get("container") == s:
                self.send(P.contained(c, it["graphic"], it["amount"], s))

    def _consume(self, s, n=1):
        it = self.items[s]
        it["amount"] -= n
        if it["amount"] <= 0:
            del self.items[s]
            self.send(P.delete(s))
        else:
            self.send(P.contained(s, it["graphic"], it["amount"], it["container"]))

    def _vet(self, s):
        pl, v = self.player, self.sc["vet"]
        if self.vet_end is not None:
            self.send(P.cliloc(tamer.CLILOC_ACTION_WAIT))
            return
        rng = v["range"]
        need = [m for m in self.pets() if m.alive and not m.ghost and cheb(m.pos, pl.pos) <= rng + 3
                and (m.hits < m.hits_max or m.poison_until > self.t)]
        if not need and pl.hits >= pl.hits_max and pl.poison_until <= self.t:
            self._sys(tamer.TEXT_VET_NEEDLESS)
            return
        self._sys(tamer.TEXT_VET_BEGIN)
        self.metrics["vet_uses"] += 1
        self._consume(s)
        self.vet_end, self.vet_moved = self.t + self.rng.uniform(*v["channel_s"]), False

    def _vet_finish(self):
        pl, v = self.player, self.sc["vet"]
        self.vet_end = None
        rng = 1 if self.vet_moved else v["range"]
        pets = [m for m in self.pets() if m.alive and not m.ghost and cheb(m.pos, pl.pos) <= rng
                and (m.hits < m.hits_max or m.poison_until > self.t)]
        hurt_self = pl.hits < pl.hits_max or pl.poison_until > self.t
        if not pets and not hurt_self:
            self._sys(tamer.TEXT_VET_FAR)
            return
        self._sys(tamer.TEXT_VET_FINISH)
        share = 1.0 if len(pets) <= 1 else v.get("multi_share", 0.67)
        for m in pets:
            m.poison_until = 0
            self.heal(m, self.roll(v["heal_pet"]) * share)
        if hurt_self:
            pl.poison_until = 0
            self.heal(pl, self.roll(v["heal_self"]))

    def _drink(self, s):
        pl, pot = self.player, self.sc["potions"]
        if self.t < self.potion_ready:
            wait = max(1, int(math.ceil(self.potion_ready - self.t)))
            self._overhead(pl, f"You must wait {wait} second{'s' if wait > 1 else ''} before using another "
                               f"healing potion.")
            return
        if pl.hits >= pl.hits_max:
            self.send(P.cliloc(1049547))
            return
        self.potion_ready = self.t + pot["cooldown_s"]
        self.metrics["potions"] += 1
        self._sys("You drink a healing potion.")
        n = self.rng.randint(*pot["heal"])
        self.send(P.cliloc(1008158, str(n).encode("utf-16-le")))
        self.heal(pl, n)
        if self.rng.random() < pot.get("satchel_save", 0.0):
            self._sys("Potion satchel used.")
        else:
            self._consume(s)

    def _lift(self, s, amount):
        it = self.items.get(s)
        corpse = self.items.get(it.get("container")) if it else None
        if it is None or corpse is None or corpse.get("kind") != "corpse" \
                or cheb((corpse["x"], corpse["y"]), self.player.pos) > 2:
            self.send(b"\x27\x05")
            return
        self.lifted = s

    def _drop(self, p):
        s = int.from_bytes(p[1:5], "big")
        cont = int.from_bytes(p[18:22], "big") if len(p) >= 22 else None
        if getattr(self, "lifted", None) != s or cont != self.backpack:
            return
        self.lifted = None
        it = self.items[s]
        if it.get("kind") == "gold":
            self.items[self.gold_pile]["amount"] += it["amount"]
            self.metrics["gold_looted"] += it["amount"]
            del self.items[s]
            self.send(P.delete(s), P.contained(self.gold_pile, combat.GOLD_GRAPHIC,
                                               self.items[self.gold_pile]["amount"], self.backpack))
        else:
            it["container"] = self.backpack
            self.send(P.delete(s), P.contained(s, it["graphic"], it["amount"], self.backpack))

    def _remove_corpse(self, s):
        for c in [c for c, it in self.items.items() if it.get("container") == s]:
            del self.items[c]
        del self.items[s]
        self.viewing_items.discard(s)
        self.send(P.delete(s))

    # recall
    def _gump_reply(self, p):
        from world.parsers import parse_packet
        f = parse_packet("c2s", p)
        if f.get("serial") != self.gump_n:
            return
        row = f.get("button_id", 0) - TOME_ROW_BUTTON0
        rows = self.sc["recall"]["rows"]
        if not 0 <= row < len(rows) or self.recall_at is not None:
            return
        self._overhead(self.player, "Kal Ort Por", kind=10)
        allowed = self.sc["recall"].get("allowed_from")
        if allowed and not any(a[0] <= self.player.x <= a[2] and a[1] <= self.player.y <= a[3] for a in allowed):
            self.send(P.cliloc(501802))
            return
        self.recall_at = (self.t + RECALL_S, rows[row])

    def _land(self, dest):
        pl = self.player
        came = [m for m in self.pets() if cheb(m.pos, pl.pos) <= self.sc["recall"].get("pet_range", 3)]
        self.metrics["recalls"] += 1
        self._teleport(pl, dest["at"])
        for i, m in enumerate(came):
            m.target, m.kill, m.mode = None, None, "follow"      # teleported pets default to Follow (wiki)
            dx, dy = nav.DIR_DELTA[(2 + 2 * i) % 8]
            self._teleport(m, (pl.x + dx, pl.y + dy, pl.z))
        for o in self.mobs.values():
            if o.kind == "monster" and o.target in {pl.serial} | {m.serial for m in came}:
                o.target, o.threat = None, {}
        self._apply_penalty()
        self._view()
        for m in came:
            if m.serial in self.viewing:
                self._show(m)

    def _apply_penalty(self):
        self.penalty = PENALTY_STEPS
        self.send(P.penalty_buff(self.player.serial, PENALTY_STEPS))
        self._sys("A stationary penalty is in effect (must move 5 tiles to remove).")

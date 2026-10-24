"""The simulated server: one dungeon level on the real Outlands map, its monsters, our character and
pets, and the mechanics the hunt runner meets there (docs/SIM.md).

It speaks to the client side only in packets: `c2s(pkt)` takes a client packet at the current game
time, `run_until(t)` advances the world, `drain()` returns the S2C packets to feed the client's
world model (sim.link.SimLink does both). Geometry is the server's truth: walking by
`pathfind.Walk.can_walk` on the install's map files (z from the map), line of sight by `los.Sight`
(validated 61/61 against the live server's answers, docs/NOTES.md).

Every number comes from the scenario (harness/data/sim/<name>.json), each with its evidence or an
[INFERENCE] mark there. What is modelled:

- **Monsters**: idle near their spawn; once one of us or our live pets is within their aggro range
  and in sight (re-checked every THINK_S), after a wake delay, go for the nearest of us they see (a
  pet on a tie); chase by a greedy step toward the
  target with an A* fallback; melee when adjacent (swing period, hit chance, damage) or ranged/spell
  when in range and sight, the numbers the gravebug's and Logan's and the phoenix's scaled from them
  (`_taken_x`); threat decides whom they hit: damage taken from each attacker (x the
  pet's `threat_x`, a tank's draw) plus our aggressive acts (a bard skill at it, "All Kill" at it:
  user 2026-10-12, aggro then goes to the character) while no pet has hurt it yet; a leash home when
  the target is far or unseen; specials per type as measured live (docs/SIM.md "Monster mechanics
  audit"): bleeds (the bound soul's on everyone within a tile), the Charge (called, then the jump),
  the vampire countess' transfixed copies of our pets, the apparition's blink with its area hit, the
  banshee's dematerialize, the shade wolf's and nightstalker's Vanish, the corpsebride's wail, the
  vampires' feed, the daemon's bone burst, disease, poison, life drain; respawn after `respawn_s`.
- **Pets**: follow / guard / kill / stay by our pet commands ("All Guard Me": attack what attacks us
  or the pets; "All Kill": the target, then back to following; guarding stops at a recall);
  melee (gravebug) or ranged (phoenix); bonded: a ghost at 0 hits (0xBF sub 0x19) that follows;
  the gravebug devours a corpse nearby to heal.
- **Bard skills**: use -> prompt + cursor (first "What instrument shall you play?" and a cursor for one
  when the instrument last played is gone from the pack, `World.played`); the target in range and sight, else cliloc 500446/500237;
  success by the scenario's rates; discord (damage dealt down, taken up), peace (no move, no attack;
  a Barding Break chance each second, then immunity), provoke (two monsters fight); the cooldowns
  and refusal texts the live server sends.
- **Veterinary supplies, heal potions, the Stationary Penalty, corpses and loot, the rune tome
  recall** (home and back), and our death.
- **Other players** (scenario `others`, off unless `enabled`; docs/SIM.md "Other players"): blue
  players (human body, flags 0x20, a player's name), some with a pet or summon, who arrive on the
  level, run between spawn areas, fight the monsters near them at a measured damage rate and leave;
  monsters take them as targets by the same first-sight and threat rules as us. Their kills' corpses
  are blue (0xFF sub 0xDEAD notoriety 1) and an open of one is refused. They never die [INFERENCE].
- **Red players** (scenario `reds`, off unless `enabled`, `run.py --reds`; `World.add_red` places one; the model and
  its evidence: sim/reds.py, docs/SIM.md "Red players"): player killers who recall in by the first level's gold gates,
  run the teleporters down, find us by Tracking, fight by their kit (mage, melee, archer) under a player's rules
  (cast times, range and sight at targeting, Hamstring, Paralyze, the PvP flag on our rope and potions), disturb our
  recall, and follow us through teleporters but not moongates. Monsters take them as targets; the pets leave them be.

Not modelled: chat or trades with players, the Society job, item durability, skill gains.
"""
from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass, field

import combat
import escape
import los
import nav
import pathfind
import soul_jar
import tamer
from agent_link import HELD_END_TEXT, HELD_SILENCED_TEXT, HELD_TEXT
from sim import packets as P
from sim import armor
from sim.reds import Reds
from sim.scenario import player_armor

VIEW = 18                 # the client's view range (combat.VIEW_RANGE)
TICK_S = 0.1              # world step
_NUMBER = re.compile(r"^[+-]\d+$")  # an overhead damage/heal number: the trace has it as hurt/heal, not as a `say`
THINK_S = 0.5             # a monster/pet re-decides its target this often (RunUO AI ~0.5-1 s) [INFERENCE]
LEASH = 24                # a monster chasing beyond this from home gives up [INFERENCE]
LOST_S = 8.0              # ...or after its target was out of sight this long [INFERENCE]
TANK_DRAW_R = 3           # a guarding Tank this much beyond a waking monster's range can still draw it (_think)
SWITCH_X = 1.1            # a monster changes target when another's threat passes its target's by this factor
RETARGET_P = 0.05         # a monster on a pet turns to the player nearer it, this chance a think (docs/SIM.md "Parity
#                           with the live runs": live Logan took 16 % of our side's damage, the simulator 8 %; of the
#                           attributed hits by type his share was 0.03-0.23 live against 0.00-0.05). With the spawn
#                           spread and wander, 144 runs each: 0 gave his damage 0.75x and deaths 1.2 an hour, 0.05
#                           0.84x and 1.1, 0.1 0.95x and 1.8, 0.15 0.99x and 2.0 (live 0.6, its interval to 1.3): the
#                           simulator's deaths rise faster with his damage than live [INFERENCE: the mechanism, the
#                           server's re-acquire of the nearest]
SHOT_AT_PLAYER_P = 0.2    # a monster shooting at a pet sends a shot at the player in its reach and sight instead: live
#                           his share of the casters' attributed hits was the purger's 0.23, the shadowguard's 0.18, the
#                           banshee's 0.17, the minion's 0.12 (41 visits), simulated 0.00-0.05. 288 runs each: 0 gave
#                           his damage 0.63x and potions 0.64x, 0.1 0.83x and 0.83x, 0.2 0.99x and 1.02x, deaths 0.9-1.1
#                           an hour throughout (live 0.6, its interval to 1.3) [INFERENCE: the mechanism]
REGEN_S = 4.0             # hits regenerate 1 per this out of combat (player and pets) [INFERENCE]
# Stamina (live, Logan's 25 on Maus 3, sim/shove_scan.py over the captures 2026-10-13..21, docs/SIM.md "Shoving and
# stamina"): a step into any live mobile's tile goes through at full stamina only, with "You shove something out of the
# way." and SHOVE_STAM less (83 of 83 shoves into monsters with that cliloc: 25 -> 15); below full it is refused (monsters
# at full stamina 202 confirmed, 1 refused; below, 250 refused); a point less every STEP_STAM_EVERY steps on foot (1,464 of
# 3,393 intervals between those drops, most since 10-18; 19 before 10-17 [INFERENCE: a server change]); a point back
# every STAM_REGEN_S (+1 changes q10-q90 1.98-2.07 s, n=4,255)
SHOVE_STAM = 10
SHOVE_CLILOC, SHOVE_HIDDEN_CLILOC = 1019042, 1019043      # "...something out of the way." / "...something invisible..."
STEP_STAM_EVERY = 15
STAM_REGEN_S = 2.04
PATROL_TAKE_R = 3         # pets on "All Patrol" attack a monster this close to the spot on arrival (live demo 2026-10-18)
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
ON_HIT = {"bleed", "bleed_area", "poison", "drain", "disease"}   # scenario specials rolled on a monster's hit
TIMED = {"charge", "transfix", "vanish", "wail", "burst", "blink", "feed", "ooze", "brood"}   # ...used on a cooldown while it fights
# ("dematerialize" fires from `hurt`: a banshee hit with it ready)
UNPREVENTABLE = {"oxygen"}  # damage no song, discord or penalty changes (Tidal Tomb's oxygen deprivation)
GATE_GUMP = 0xE0E675B8    # "Moongate Destinations" (docs/NOTES.md "Moongate gumps")
SONGS = ("discord", "peace", "provoke")
TEXT_SONG_WAIT = "You must wait a few moments before performing another barding song."
TEXT_ROPE_PROMPT = "Where do you wish to traverse to?"
ROPE_R = 10
ROPE_COOLDOWN_S = 5.0
# A rope used while we are flagged in PvP (a player's harmful act or damage on us within PVP_FLAG_S; the flag is set on
# both sides) starts a 2-minute cooldown on it, refused with "You must wait another 1m 50s before you may use this
# again while in combat." (live 2026-10-19 19:59:10 and 2026-10-22 20:00:58.9, docs/NOTES.md "Hinder, Hamstring and the
# rope"; wiki Adventurer's Rope, knowledge #13326), and our followers don't come along a rope then (patch #5163)
ROPE_COMBAT_COOLDOWN_S = 120.0
PVP_FLAG_S = 60.0         # the PvP flag's PvM restrictions last 60 s (patch 2021-10-02, knowledge #7333)
ROPE_LAND_S = 0.8         # the rope's target answered -> on the landing tile (live 2026-10-21 10:35:06.54 -> 07.33)
OTHERS = ("other", "otherpet")   # Mob kinds of other players and their pets (never ours: not in `team()`)
FLAG_PLAYER = 0x20               # live: every other player's 0x20/0x77 carries it (threats.FLAG_PLAYER_HINT)
ATTACKED_TEXT = " is attacking you!"   # the server's line over us when a player makes us its target (loop_hunt.ATTACKED_BY)
# Our side of a red's specials (docs/NOTES.md "Hinder, Hamstring and the rope"; the reds themselves: sim/reds.py)
HAMSTRING_ICON, HAMSTRING_GUMP = 53, 4523      # "Can Only Walk and Cannot Regain Stamina": 3 s at stamina 0
HAMSTRING_STEP_S = 0.38   # hamstrung, a step at a walk's pace only: live confirms ~0.41 s apart
PARALYZE_ICON = 135      # "You cannot move!" (live 2026-10-21 10:35:06.66, Ticket Tongje); its buff's text unrecorded
FROZEN_CLILOC = 500111    # "You are frozen and cannot move." on a step while paralyzed (live 2026-10-21 10:35:08)
DISTURB_CLILOC = 500641   # our cast disturbed by damage (docs/research/SPELL_INTERRUPTS.md: every player hit breaks it)
TEXT_NOT_YOURS = "You may not loot this corpse."   # live answer to opening another's kill (docs/HUNT_LOOP.md)
HELD_NO_USE_TEXT = "You cannot use items while in this state."   # live 2026-10-20 19:23:27.13, transfixed
LIFT_GAP_S = 0.5         # the server's gap between lifts it takes (combat.LIFT_GAP_S's evidence: refused up to ~0.5 s)


def cheb(a, b) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def tile_dir(a, b) -> int:
    return nav.direction(tuple(a[:2]), tuple(b[:2]))


class _ShotGoal(nav.within):
    """Goal predicate for `World._step_to_shot`: within `radius` of the target and in its sight from the tile's
    surface (pathfind.plan passes (x, y); nav.within's heuristic keeps the search small)."""

    __slots__ = ("sight", "at")

    def __init__(self, sight, at, radius: int):
        super().__init__(at, radius)
        self.sight, self.at = sight, at

    def __call__(self, t) -> bool:
        if not super().__call__(t):
            return False
        return self.sight.can_see((t[0], t[1], los.surface_z(self.sight.walk, t[0], t[1], self.at[2])), self.at)


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
    bleed_until: float = 0.0      # 0: not bleeding
    bleed_next: float = 0.0
    bleed: tuple = ((8, 10), 3.0)  # (tick damage range, seconds between ticks) of the bleed running
    expires: float | None = None  # a summoned monster (transfixed copy) vanishes then
    held_until: float = 0.0       # a transfixed pet stands still and doesn't fight until then
    ar: float = 0.0               # the player's armor rating (scenario `player.armor`, sim/armor.py)
    hidden_until: float = 0.0     # stealthed (Vanish) until then: unseen, unhittable; 0: visible
    blink_to: int | None = None   # an apparition's blink: back on this mobile's tile when hidden_until ends
    relocate: tuple | None = None  # a banshee's dematerialize: (min, max) tiles from here when hidden_until ends
    charge_at: tuple | None = None  # a Charge called ('*charges*'): (landing time, target serial, the special)
    feed: list | None = None      # a vampire's feed: [next time, ticks left (None: still attempting), target, special]
    pulse: list | None = None     # an ooze aura under way: [next burst time, bursts left, special]
    brood_at: tuple | None = None  # a nest crab's dig: (time its broodling emerges, special)
    seen_since: float | None = None   # idle with one of us in its range and sight: it takes a target at this time
    diseases: list = field(default_factory=list)   # [[next tick time, ticks left, damage, every_s]]: they stack
    cd: dict = field(default_factory=dict)   # ability -> next time it is ready
    mode: str = "follow"          # pets: follow, guard, kill, stay, patrol
    kill: int | None = None
    patrol: tuple | None = None   # pets on "All Patrol": the (x, y) clicked
    devour_t: float = 0.0
    last_hurt_t: float = -1e9
    path: list = field(default_factory=list)
    stuck: int = 0
    owner: int | None = None      # another player's pet: its player's serial; a player: its pet's
    dps: float = 0.0              # another player's (and its pet's) damage per second on its target
    leave_t: float = 0.0          # another player leaves the level after this
    dest: tuple | None = None     # ... runs to this spawn area next
    linger_until: float = 0.0     # ... stands about it until then
    casts: list = field(default_factory=list)   # a red player's spells on their way to us: [landing time, spell]
    rs: dict = field(default_factory=dict)      # a red player's state (sim/reds.py)
    cast: list | None = None      # a ranged cast under way (monster or pet): [release time, target serial, start time]

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
        self.met: set[int] = set()            # monsters shown to us once (metrics "met")
        self.next_serial = 0x00300000
        self.next_item = 0x60000000
        self.cid = 0x00400000
        self.pending = None                   # the target cursor up: {"kind", "cid", ...}
        self.lift_ok_t = -1e9                 # the last lift taken (LIFT_GAP_S)
        self.metrics = {"kills": 0, "kills_by": {}, "player_damage_taken": 0.0, "pet_damage_taken": {},
                        "team_damage": 0.0, "vet_uses": 0, "potions": 0, "bard": {}, "steps": 0, "denied_steps": 0,
                        "recalls": 0, "deaths": 0, "pet_deaths": [], "gold_looted": 0, "gold_banked": 0, "violations": [],
                        "aggressive_acts": 0, "kill_commands": 0, "guard_commands": 0, "follow_commands": 0,
                        "patrol_commands": 0, "silenced": 0, "lift_waits": 0, "shoves": 0, "refresh_potions": 0,
                        "stam_below_s": 0.0, "fights": [], "hunt_s": 0.0}
        oth = scenario.get("others") or {}
        self.others_on = bool(oth.get("enabled"))
        self.orng = random.Random(seed * 7919 + 17)  # the other players' own draws: the rest of the world's stay put
        self.wrng = random.Random(seed * 104729 + 3)  # spawn spread and idle wander: the rest of the world's stay put
        self.next_other_t = None              # the next other player's arrival on the level
        self.engaged_fn = None                # sim.run: the runner's engaged target (metrics only)
        self.others_in_view = {}              # other player serial -> time it came into our view
        if self.others_on:
            self.metrics.update({"others_arrived": 0, "others_kills": 0, "others_kills_in_view": 0, "others_view_s": {},
                                 "others_dwell": [],
                                 "kill_on_others": 0, "blue_corpse_opens": 0, "wait_on_others_s": 0.0,
                                 "wait_on_others_mobs": 0, "others_kills_unseen": 0})
        ee = scenario.get("eerie") or {}
        self.eerie_on = bool(ee.get("enabled"))     # the Field of Souls' eerie corpses (off by default)
        self.erng = random.Random(seed * 104729 + 5)  # their own draws: the rest of the world's stay put
        self.next_eerie_t = self.erng.expovariate(ee["per_h"] / 3600.0) if self.eerie_on else None
        self._waited_on = set()
        self.other_serials = set()            # every other player's and pet's serial (loot rights after they left)
        self.skill_ready = 0.0
        self.calm_ready = 0.0
        self.song_ready = 0.0
        self.songs = {}                       # barding song -> until
        self.rope_ready = 0.0
        self.rope_combat_until = 0.0          # the rope's combat cooldown (ROPE_COMBAT_COOLDOWN_S)
        self.attacked_by_player_t = -1e9      # the last "... is attacking you!" a red said over us
        self.pvp_t = -1e9                     # the last harmful act or damage of a player on us (PVP_FLAG_S)
        self.hamstrung_until = 0.0            # Hamstring on us until then (hamstring_player)
        self.hamstring_immune_until = 0.0
        self.stam_before = None               # our stamina when hamstrung (back at its end)
        self.paralyzed_until = 0.0            # a player's Paralyze on us until then, or damage (paralyze_player)
        self.recall_start_t = None            # our recall's power words (a disturbance's recovery)
        self.cast_recover_until = 0.0         # a disturbed cast's recovery (SPELL_INTERRUPTS.md)
        self.rope_landing = None              # (time, (x, y, z)): a rope's jump on its way (ROPE_LAND_S)
        self.last_use_t = -1e9
        self.potion_ready = 0.0
        self.vet_end = None                   # the running vet channel's finish time
        self.last_step_t = 0.0
        self.steps_taken = 0                  # our confirmed steps: a stamina point every STEP_STAM_EVERY of them
        self.stam_next = None                 # when our stamina regains its next point (STAM_REGEN_S), below max
        self.penalty = 0
        self.under_s = 0.0                    # scenario `oxygen`: seconds under water since it was last refilled
        self.oxygen_due = None                # ... the next deprivation hit, once deprived
        self.gump_n = 0x7700
        self.gate_n = 0x7800                  # moongate menus' serials
        self.gate_gump = None                 # (serial, gate, picked destination) of the menu up
        self.gate_last = scenario.get("player", {}).get("gate_last")   # the menu opens with it selected
        self.recall_at = None                 # (arrival time, destination row)
        self.spawn_due = []                   # (time, spawn index)
        self.player_log = []                  # (time, source, damage, hits after): the last 15 s
        self.flights = []                     # ranged attacks released, on their way: (land time, attacker Mob, victim serial)
        self.trace = None                     # the sim/trace.Tracer recording this run, or None
        self.reds = Reds(self, seed, TICK_S)  # red players (sim/reds.py; arrivals only with the scenario's `reds` on)
        self._build()
        self.reds.start()

    # ------------------------------------------------------------ setup
    def _serial(self) -> int:
        self.next_serial += 1
        return self.next_serial

    def _item_serial(self) -> int:
        self.next_item += 1
        return self.next_item

    def stand_z(self, x, y, near_z=0) -> int:
        """The surface z to stand on at (x, y) nearest `near_z` (the map's walkable surfaces)."""
        return los.surface_z(self.walk, x, y, near_z)

    def _build(self):
        sc = self.sc
        p = sc["player"]
        x, y = p["start"][:2]
        z = self.stand_z(x, y, p["start"][2] if len(p["start"]) > 2 else 0)
        self.player = Mob(p["serial"], "player", "player", p["name"], p["body"], x, y, z,
                          hits=p["hits"], hits_max=p["hits"], noto=1, spec=p)
        self.player.stam, self.player.stam_max = p.get("stam", 25), p.get("stam", 25)
        self.player.mana, self.player.mana_max = p.get("mana", 100), p.get("mana", 100)
        arm = player_armor(sc)
        self.player.ar, self.pet_taken_x = arm["ar"], arm["pet_taken_x"]
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
        if self.eerie_on and self.sc["eerie"].get("jars"):
            self.items[self._item_serial()] = {"graphic": soul_jar.EMPTY_JAR_GRAPHIC, "amount": self.sc["eerie"]["jars"],
                                               "container": self.backpack, "kind": "soul_jar"}
        # the instrument last played (the server's): a bard skill without it in the pack asks for one first
        self.played = next((s for s, it in self.items.items() if it.get("kind") == "instrument"), None)
        self.gold_pile = lo.get("gold_serial", self._item_serial())
        self.items[self.gold_pile] = {"graphic": combat.GOLD_GRAPHIC, "amount": lo.get("gold", 0),
                                      "container": self.backpack, "kind": "gold"}
        self.gates = {}                       # (x, y) -> the scenario's gate row: stepping on opens its menu
        for g in sc.get("gates", []):
            x, y = g["at"][:2]
            s = g.get("serial") or self._item_serial()
            self.items[s] = {"kind": "gate", "graphic": g.get("graphic", 0x0F6C), "x": x, "y": y,
                             "z": self.stand_z(x, y, g["at"][2] if len(g["at"]) > 2 else 0), "name": g.get("name")}
            self.gates[(x, y)] = g
        self.spawns = []
        for s in sc["spawns"]:
            self.spawns.append({**s, "mob": None})
        for i in range(len(self.spawns)):
            self._spawn(i)
        if self.others_on:
            self._others_setup()

    def _spawn(self, i):
        sp = self.spawns[i]
        spec = self.sc["creatures"][sp["key"]]
        x, y = sp["at"][:2]
        z = self.stand_z(x, y, sp["at"][2] if len(sp["at"]) > 2 else 0)
        tiles = self._spawn_tiles(i, (x, y, z))
        if tiles:
            x, y, z = self.wrng.choice(tiles)
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
        self._ev("spawn", m=m.serial, key=sp["key"])

    def _spawn_tiles(self, i, at) -> list:
        """The tiles a spawn puts its monster on: walkable, within `defaults.spawn_spread` of its point and in sight
        of it (cached). Live idle first sightings stood a median 3 tiles from their spawn's point (q75 4, q90 6;
        1,037 sightings, 41 visits, 2026-10-20); the simulator's stood on it [INFERENCE: a spawner's home range]."""
        sp = self.spawns[i]
        if "_tiles" not in sp:
            d = self.sc["defaults"].get("spawn_spread", 0)
            r = min(sp.get("spread", d), d)              # a spawn's own (the elder vampires stand put), never wider
            out = []
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    tx, ty = at[0] + dx, at[1] + dy
                    tz = self.stand_z(tx, ty, at[2])
                    if any(self.walk.can_walk(tx, ty, tz, d) for d in range(8)) \
                            and self.sight.can_see((tx, ty, tz), at):
                        out.append((tx, ty, tz))
            sp["_tiles"] = out
        return sp["_tiles"]

    # ------------------------------------------------------------ other players (scenario `others`)
    def others(self) -> list:
        """Other players and their pets on the level (none unless the scenario's `others` is enabled)."""
        return [m for m in self.mobs.values() if m.kind in OTHERS] if self.others_on else []

    def _oroll(self, r) -> float:
        if len(r) >= 3:
            return self.orng.triangular(r[0], r[1], min(max(r[2], r[0]), r[1]))
        return self.orng.uniform(r[0], r[1])

    def _others_setup(self):
        """The players already on the level at the start (Poisson, arrival rate x mean stay), then the first arrival."""
        o = self.sc["others"]
        a = self.sc.get("area")
        self.other_spots = [tuple(s["at"]) for s in self.sc["spawns"]
                            if not a or (a[0] <= s["at"][0] <= a[2] and a[1] <= s["at"][1] <= a[3])]
        self.next_other_serial = o.get("serial0", 0x00200000)
        if not self.other_spots:
            return
        rate = o["arrive_per_h"] / 3600.0
        st = o["stay_s"]
        mean = (st[0] + st[1] + (st[2] if len(st) > 2 else (st[0] + st[1]) / 2)) / 3
        n, p, lim = 0, self.orng.random(), math.exp(-rate * mean)
        while p > lim:
            n += 1
            p *= self.orng.random()
        for _ in range(n):
            self._other_arrive(stay=self.orng.random() * self._oroll(st))
        self.next_other_t = self.orng.expovariate(rate) if rate > 0 else None
        fill = o.get("unseen", {}).get("start_fill", 1.0)
        for i, sp in enumerate(self.spawns):
            m = self.mobs.get(sp["mob"])
            if m is not None and self.orng.random() >= fill:
                self._unseen_kill(m, due=self.orng.uniform(0, self._respawn_s(i)[1]))

    def _respawn_s(self, i) -> list:
        return self.spawns[i].get("respawn_s", self.sc.get("respawn_s", [240, 360]))

    def _unseen_tick(self):
        """Once a second: each idle spawned monster out of our view goes to the level's other hunters at
        1 / `others.unseen.life_s` a second (scenario note)."""
        life = self.sc["others"].get("unseen", {}).get("life_s")
        if not life:
            return
        pl = self.player
        for m in [m for m in self.mobs.values() if m.kind == "monster" and m.alive and m.spawn is not None
                  and m.target is None and not m.warmode and cheb(m.pos, pl.pos) > VIEW]:
            if self.orng.random() < 1.0 / life:
                self._unseen_kill(m)

    def _unseen_kill(self, m: Mob, due: float | None = None):
        """A spawn's monster gone out of our view (no corpse we'd see), its respawn timer started (or `due` from now)."""
        lo_s, hi_s = self._respawn_s(m.spawn)
        self.spawn_due.append((self.t + (self.orng.uniform(lo_s, hi_s) if due is None else due), m.spawn))
        self.viewing.discard(m.serial)
        del self.mobs[m.serial]
        self.count("others_kills_unseen")

    def _other_serial(self) -> int:
        self.next_other_serial += 1
        self.other_serials.add(self.next_other_serial)
        return self.next_other_serial

    def _free_beside(self, x, y, z):
        if not self._occupied(x, y):
            return x, y
        for dx, dy in nav.DIR_DELTA:
            if not self._occupied(x + dx, y + dy) and self.walk.can_walk(x, y, z, nav.direction((x, y), (x + dx, y + dy))):
                return x + dx, y + dy
        return None

    def _other_arrive(self, stay=None):
        """Another player comes onto the level at a spawn area out of our view (with its pet, by `pet_share`)."""
        o, r, pl = self.sc["others"], self.orng, self.player
        spots = [s for s in self.other_spots if cheb(s, pl.pos) > VIEW + 2] or self.other_spots
        sx, sy, sz = r.choice(spots)
        z = self.stand_z(sx, sy, sz)
        at = self._free_beside(sx, sy, z)
        if at is None:
            return
        pk = r.choices(o["pets"], weights=[p.get("w", 1) for p in o["pets"]])[0] \
            if r.random() < o.get("pet_share", 0.0) else None
        self.add_other(at[0], at[1], z, pet=pk, stay=stay if stay is not None else self._oroll(o["stay_s"]))

    def add_other(self, x, y, z, name=None, pet=None, stay=None, dps=None) -> Mob:
        """Another player at (x, y, z) (and `pet`, a scenario `others.pets` row, beside it); `stay` seconds on the
        level, `dps` its damage rate (else drawn)."""
        o, r = self.sc["others"], self.orng
        used = {m.name for m in self.mobs.values() if m.kind == "other"}
        name = name or r.choice([n for n in o["names"] if n not in used] or o["names"])
        hp = o.get("hits", 1000)
        m = Mob(self._other_serial(), "other", "other player", name, r.choice(o.get("bodies", [0x190])), x, y, z,
                hits=hp, hits_max=hp, noto=o.get("notoriety", 1), spec=o)
        m.dps = self._oroll(o["dps"]) if dps is None else dps
        m.leave_t = self.t + (stay if stay is not None else self._oroll(o["stay_s"]))
        m.next_think = self.t + r.uniform(0, THINK_S)
        self.mobs[m.serial] = m
        beside = self._free_beside(x, y, z) if pet is not None else None
        if beside is not None:
            php = pet.get("hits", 2000)
            p = Mob(self._other_serial(), "otherpet", pet["name"], pet["name"], pet["body"], beside[0], beside[1], z,
                    hits=php, hits_max=php, noto=m.noto, spec=pet)
            p.owner, m.owner = m.serial, p.serial
            self.mobs[p.serial] = p
        self.metrics["others_arrived"] += 1
        self._view()
        return m

    def _other_gone(self, m: Mob):
        """Another player (and its pet) leaves the level: gone from the world, out of every monster's mind."""
        for s in (m.serial, m.owner):
            x = self.mobs.get(s)
            if x is None or x.kind not in OTHERS:
                continue
            if s in self.viewing:
                self.send(P.delete(s))
                self.viewing.discard(s)
            for mob in self.mobs.values():
                if mob.target == s:
                    mob.target = None
                mob.threat.pop(s, None)
            del self.mobs[s]

    def _other(self, m: Mob):
        """Another player: fight the monster it picked (`_other_think`), else run between spawn areas, standing
        about each a while; once its stay is up, off out of our view and gone."""
        t, o = self.t, self.sc["others"]
        if t >= m.next_think:
            m.next_think = t + THINK_S
            self._other_think(m)
        tgt = self.mobs.get(m.target) if m.target is not None else None
        if tgt is not None and tgt.alive:
            self._warm(m, True)
            if tgt.hidden_until:
                return
            d = cheb(m.pos, tgt.pos)
            if d <= o["reach"] and self.can_see(m, tgt):
                if t >= m.next_swing:
                    m.next_swing = self._due(m.next_swing, o["every_s"])
                    share = 1.0 - o["pet_part"] if m.owner in self.mobs else 1.0
                    self.hurt(tgt, m.dps * o["every_s"] * share * self.orng.uniform(0.8, 1.2), m, "other")
            elif t >= m.next_step:
                m.next_step = self._due(m.next_step, o["step_s"])
                self._step_toward(m, tgt.pos, 1)
            return
        m.target = None
        self._warm(m, False)
        pl = self.player
        if t >= m.leave_t:
            if cheb(m.pos, pl.pos) > VIEW:
                self._other_gone(m)
                return
            if m.dest is None or cheb(m.dest, pl.pos) <= VIEW + 2:
                far = [s for s in self.other_spots if cheb(s, pl.pos) > VIEW + 2]
                m.dest = self.orng.choice(far) if far else None
                m.linger_until = 0.0
        if m.linger_until > t:
            return
        if m.dest is None:
            near = [s for s in self.other_spots if 3 < cheb(s, m.pos) <= o.get("roam_r", 40)] or self.other_spots
            m.dest = self.orng.choice(near)
        if t < m.next_step:
            return
        m.next_step = self._due(m.next_step, o["step_s"])
        if cheb(m.pos, m.dest) <= 1:
            m.dest, m.linger_until = None, t + self._oroll(o["linger_s"])
        elif not self._step_toward(m, m.dest, 1):
            m.cd["lost"] = m.cd.get("lost", 0) + 1
            if m.cd["lost"] >= 6:                    # no way there: another spot
                m.dest, m.cd["lost"] = None, 0
        else:
            m.cd["lost"] = 0

    def _other_think(self, m: Mob):
        """Another player's target: a monster on it or its pet, else (while its stay lasts) the nearest one within
        `engage_r` in its sight that isn't fighting our party [INFERENCE: they don't take ours]."""
        o = self.sc["others"]
        cur = self.mobs.get(m.target) if m.target is not None else None
        if cur is not None and cur.alive and cheb(cur.pos, m.pos) <= LEASH:
            return
        mine, team = {m.serial, m.owner}, {x.serial for x in self.team()}
        near = [x for x in self.mobs.values() if x.kind == "monster" and x.alive and not x.hidden_until
                and x.expires is None and cheb(x.pos, m.pos) <= o["engage_r"]]
        pick = [x for x in near if x.target in mine]
        if not pick and self.t < m.leave_t:
            pick = [x for x in near if x.target not in team and self.can_see(m, x)]
        m.target = min(pick, key=lambda x: cheb(x.pos, m.pos)).serial if pick else None

    def _other_pet(self, m: Mob):
        """Another player's pet: on its player's target (or a monster on itself), else at its player's side."""
        t, o = self.t, self.sc["others"]
        owner = self.mobs.get(m.owner)
        if owner is None:
            self._other_gone(m)
            return
        tgt = self.mobs.get(owner.target) if owner.target is not None else None
        if tgt is None or not tgt.alive or tgt.hidden_until:
            tgt = next((x for x in self.mobs.values() if x.kind == "monster" and x.alive and x.target == m.serial
                        and not x.hidden_until), None)
        if tgt is not None:
            self._warm(m, True)
            if cheb(m.pos, tgt.pos) <= 1:
                if t >= m.next_swing:
                    m.next_swing = self._due(m.next_swing, o["every_s"])
                    self.hurt(tgt, owner.dps * o["every_s"] * o["pet_part"] * self.orng.uniform(0.8, 1.2), m, "other")
            elif t >= m.next_step:
                m.next_step = self._due(m.next_step, o["step_s"])
                self._step_toward(m, tgt.pos, 1)
            return
        self._warm(m, False)
        if t >= m.next_step and cheb(m.pos, owner.pos) > 1:
            m.next_step = self._due(m.next_step, o["step_s"])
            self._step_toward(m, owner.pos, 1)

    # ------------------------------------------------------------ red players (sim/reds.py; module docstring)
    def add_red(self, x, y, z=None, name="Ticket Tongje", spec=None, route=(), kit="mage") -> Mob:
        """A red player at (x, y) (z: the surface nearest ours) that attacks us on sight and knows where we are, with
        the scenario's `reds.kits[kit]` numbers (`spec` over them); `route`: (x, y, seconds) tiles it walks first, each
        that long after the last (a live red's own path), before it comes at us."""
        return self.reds.add(x, y, z, name=name, kit=kit, spec=spec, route=route, attacker=True)

    def flagged(self) -> bool:
        """We are flagged in PvP: a player's harmful act or damage on us within PVP_FLAG_S."""
        return self.t - max(self.pvp_t, self.attacked_by_player_t) <= PVP_FLAG_S

    def hamstring_player(self, seconds: float, immune_s: float) -> bool:
        """A player's Hamstring on us (wiki Hamstring, knowledge #14382-14390; live 3 times, docs/NOTES.md "Hinder,
        Hamstring and the rope"): "Their attack hamstrings you!", buff 53, stamina 0 for `seconds` (a walk's pace
        only, no regen), then "You are no longer hamstrung." and the stamina it had; immune `immune_s` after. False
        while immune."""
        pl = self.player
        if pl.ghost or self.t < self.hamstring_immune_until or self.hamstrung_until > self.t:
            return False
        self.hamstrung_until = self.t + seconds
        self.hamstring_immune_until = self.t + seconds + immune_s
        self.stam_before = pl.stam
        pl.stam = 0
        self._sys("Their attack hamstrings you!")
        self.send(P.buff(pl.serial, HAMSTRING_ICON, HAMSTRING_GUMP, "Hamstrung",
                         "Can Only Walk and Cannot Regain Stamina", seconds),
                  P.stam(pl.serial, 0, pl.stam_max))
        return True

    def paralyze_player(self, seconds: float):
        """A player's Paralyze on us: "You cannot move!", buff 135, every step refused with FROZEN_CLILOC until
        `seconds` pass or damage breaks it (live 2026-10-21 10:35:06.66, broken by a hit 3.5 s in; wiki Magery)."""
        pl = self.player
        if pl.ghost:
            return
        self.paralyzed_until = self.t + seconds
        self._sys("You cannot move!")
        self.send(P.buff(pl.serial, PARALYZE_ICON, 0, "Paralyze", "You are paralyzed.", seconds))

    def _unparalyze(self):
        self.paralyzed_until = 0.0
        self.send(P.buff_remove(self.player.serial, PARALYZE_ICON))

    def _others_sample(self):
        """Once a second: players in our view while we're on the level (`others_view_s`: count -> seconds, their
        in-view stays `others_dwell`), and the runner's engaged target while it fights another player and no one
        of ours has hurt it (`wait_on_others_s`)."""
        pl, a, mt = self.player, self.sc.get("area"), self.metrics
        here = not pl.ghost and a and a[0] <= pl.x <= a[2] and a[1] <= pl.y <= a[3]
        seen = {m.serial for m in self.mobs.values() if m.kind == "other" and cheb(m.pos, pl.pos) <= VIEW} if here else set()
        if here:
            mt["others_view_s"][len(seen)] = mt["others_view_s"].get(len(seen), 0) + 1
        for s in list(self.others_in_view):
            if s not in seen:
                mt["others_dwell"].append(round(self.t - self.others_in_view.pop(s), 1))
        for s in seen - set(self.others_in_view):
            self.others_in_view[s] = self.t
        e = self.engaged_fn() if self.engaged_fn is not None else None
        mob = self.mobs.get(e) if e is not None else None
        if mob is not None and mob.kind == "monster" and mob.target in self.mobs \
                and self.mobs[mob.target].kind in OTHERS:
            ours = {x.serial for x in self.team()} | {p.serial for p in self.pets()}
            if not any(s in ours for s in mob.damage_by):
                mt["wait_on_others_s"] += 1.0
                if e not in self._waited_on:
                    self._waited_on.add(e)
                    mt["wait_on_others_mobs"] += 1

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

    def _flags(self, m: Mob, war: bool) -> int:
        return (0x40 if war else 0) | (FLAG_PLAYER if m.kind in ("other", "red") else 0)

    def _show(self, m: Mob):
        flags = self._flags(m, m.warmode)
        body = GHOST_BODY if m.ghost and m.kind == "player" else m.body
        self.send(P.mobile(m.serial, body, m.x, m.y, m.z, noto=m.noto, flags=flags, d=m.d),
                  P.name(m.serial, m.name), P.hits(m.serial, m.pct(), 100))
        if m.kind == "pet":
            self.send(P.says(m.serial, m.name, "(bonded)", kind=0, hue=946, body=m.body))   # its click label's line
        elif m.kind == "otherpet":
            # live, other players' pets: "(bonded)" 6308, "(summoned)" 2128, "(tame)" 20 sightings (docs/NOTES.md)
            self.send(P.says(m.serial, m.name, m.spec.get("tag", "(bonded)"), kind=0, hue=946, body=m.body))
        elif m.kind == "monster" and m.serial not in self.met:
            # the monsters we meet on the hunting level, by type (sim/parity.py against the captures' first sightings)
            self.met.add(m.serial)
            if self._on_level():
                self.count("met", m.key)
        if m.kind == "pet" and m.ghost:
            self.send(P.pet_dead(m.serial))
        elif m.kind == "red":
            self.reds.show(m)

    def _view(self):
        for s, m in self.mobs.items():
            if s == self.player.serial:
                continue
            near = (m.alive or m.ghost) and self._in_view(m) and not m.hidden_until
            if near and s not in self.viewing:
                self.viewing.add(s)
                self._show(m)
            elif not near and s in self.viewing:
                self.viewing.discard(s)
                if not self._in_view(m):
                    # out of range: the server's 0x1D, as live (2026-10-21 17:37:10.92 the red I Make Bodies stepping
                    # to 19 tiles, 17:37:13.63 Botan); the client model would keep it at its last tile to 24
                    self.send(P.delete(s))
        for s, it in self.items.items():
            if it.get("kind") in ("corpse", "gate", "fire", "eerie", "eerie_fx"):
                near = cheb((it["x"], it["y"]), self.player.pos) <= VIEW
                if near and s not in self.viewing_items:
                    self.viewing_items.add(s)
                    if it["kind"] == "eerie" and not it.get("seen"):
                        it["seen"] = True
                        self.count("eerie_seen")
                    if it["kind"] in ("gate", "fire", "eerie", "eerie_fx"):
                        self.send(P.ground_item(s, it["graphic"], 1, it["x"], it["y"], it["z"]))
                    else:
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
                self.send(P.mobile(m.serial, m.body, m.x, m.y, m.z, noto=m.noto, flags=self._flags(m, on), d=m.d))

    def _overhead(self, m: Mob, text: str, kind=0):
        if self.trace is not None and not _NUMBER.match(text):
            self._ev("say", m=m.serial, text=text, sk=kind)
        if m.serial == self.player.serial or m.serial in self.viewing:
            self.send(P.says(m.serial, m.name, text, kind=kind, body=m.body))

    def _sys(self, text):
        self._ev("sys", text=text)
        self.send(P.system_text(text))

    def _ev(self, ev: str, /, **f):
        """A trace event (sim/trace.py) when a tracer records this run."""
        if self.trace is not None:
            self.trace.event(self.t, ev, f)

    def can_see(self, a: Mob, b: Mob) -> bool:
        return self.sight.can_see(a.pos, b.pos)

    def pets(self):
        return [m for m in self.mobs.values() if m.kind == "pet"]

    def team(self):
        return [self.player] + [p for p in self.pets() if p.alive and not p.ghost]

    def _due(self, due: float, period: float) -> float:
        """A timer's next fire time: `period` after the last one when that was due within a tick
        (the cadence survives the TICK_S grid: a 0.21 s step stays 0.21 s), else after now."""
        return max(due, self.t - TICK_S) + period

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
    def hurt(self, victim: Mob, amount: float, by: Mob | None, kind="melee", song_pets=True):
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
        if victim.kind in ("player", "pet") and (by is None or by.kind == "monster") \
                and (song_pets or victim.kind == "player") and kind not in UNPREVENTABLE:
            amount *= 1 - self.song("discord")      # also on bleeds and area hits (patch 2022-08-26)
        if victim.kind == "pet" and kind not in UNPREVENTABLE:
            amount *= self.pet_taken_x              # the Command aspect's follower resistance at another tier
        if victim.kind == "monster" and by is not None and by.kind in ("player", "pet"):
            amount *= 1 + self.song("provoke")
        n = max(1, int(round(amount)))
        victim.hits -= n
        self._ev("hurt", v=victim.serial, by=by.serial if by is not None else None, n=n, kind=kind, h=int(victim.hits))
        victim.last_hurt_t = self.t
        if by is not None:
            victim.damage_by[by.serial] = victim.damage_by.get(by.serial, 0) + n
            if victim.kind == "monster":
                w = by.spec.get("threat_x", 1.0) if by.kind == "pet" else 1.0
                victim.threat[by.serial] = victim.threat.get(by.serial, 0) + n * w
                self.metrics["team_damage"] += n if by.kind in ("player", "pet") else 0
        if victim.kind == "player" and self._on_level():
            self.metrics["player_damage_taken"] += n
            self.count("player_damage_by", by.key if by is not None else kind, n)
        if victim.kind == "player":
            self.player_log.append((self.t, by.key if by is not None else kind, n, int(victim.hits)))
            self.player_log = [e for e in self.player_log if self.t - e[0] <= 15.0]
        elif victim.kind == "pet" and self._on_level():
            self.count("pet_damage_taken", victim.name, n)
            self.count("pet_damage_by", by.key if by is not None else kind, n)
        if victim.kind not in OTHERS + ("red",) and (by is None or by.kind not in OTHERS):
            self._overhead(victim, f"-{n}")      # live: no "-N" over another player's fight (20261019_095934 10:11)
        if victim.kind == "player":
            self.send(P.hits(victim.serial, max(0, int(victim.hits)), int(victim.hits_max)))
            if self.paralyzed_until:
                self._unparalyze()               # damage breaks a paralyze (live 10:35:06.66 -> 10.12)
            if by is not None and by.kind == "red":
                self.pvp_t = self.t
                if self.recall_at is not None:   # a player's hit breaks our cast (SPELL_INTERRUPTS.md: 3 of 3)
                    e = self.t - (self.recall_start_t or self.t)
                    self.recall_at = None
                    self.cast_recover_until = self.t + max(0.2, 1 - math.sqrt(max(0.0, e) / 2.0))
                    self.send(P.cliloc(DISTURB_CLILOC))
                    self.reds.m["disturbed_recalls"] += 1
        elif victim.serial in self.viewing:
            self.send(P.hits(victim.serial, victim.pct(), 100))
        if victim.kind == "red" and victim.rs.get("cast") is not None:
            victim.rs["cast"] = None                 # a hit disturbs its cast (RunUO Spell.OnCasterHurt)
            victim.rs["cast_ready"] = self.t + 0.5
        if victim.kind in OTHERS + ("red",):
            if victim.hits < victim.hits_max * 0.25:
                victim.hits = victim.hits_max    # they heal themselves and never die here [INFERENCE]
        elif victim.hits <= 0:
            self.die(victim, by)
        elif victim.kind == "monster" and by is not None and by.kind in ("player", "pet") + OTHERS:
            sp = next((s for s in victim.spec.get("specials", []) if s["kind"] == "dematerialize"), None)
            if sp is not None and not victim.hidden_until and self.t >= victim.cd.get("dematerialize", 0.0):
                self._dematerialize(victim, sp)

    def heal(self, m: Mob, amount: float):
        if not m.alive or m.ghost:
            return
        if m.kind in ("player", "pet"):
            amount *= 1 + self.song("peace")
        n = int(round(min(amount, m.hits_max - m.hits)))
        m.hits += n
        self._ev("heal", m=m.serial, n=n, h=int(m.hits))
        self._overhead(m, f"+{n}")
        if m.kind == "player":
            self.send(P.hits(m.serial, int(m.hits), int(m.hits_max)))
        elif m.serial in self.viewing:
            self.send(P.hits(m.serial, m.pct(), 100))

    def die(self, m: Mob, by: Mob | None):
        self._ev("die", m=m.serial, by=by.serial if by is not None else None)
        self._cancel_cast(m, "gone")
        m.diseases = []
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
        theirs = sum(v for s, v in m.damage_by.items() if s in self.other_serials)   # another player's, here or gone
        others = noto == 1 and theirs > ours
        self.items[corpse] = {"kind": "corpse", "x": m.x, "y": m.y, "z": m.z, "body": m.body, "owner": m.serial,
                              "name": f"{m.name} corpse", "noto": noto, "t": self.t, "others": others}
        lo, hi = m.spec.get("gold", [0, 0])
        gold = self.rng.randint(int(lo), int(hi)) if hi else 0
        if gold:
            g = self._item_serial()
            self.items[g] = {"graphic": combat.GOLD_GRAPHIC, "amount": gold, "container": corpse, "kind": "gold"}
        if m.serial in self.viewing:
            self.send(P.display_death(m.serial, corpse), P.delete(m.serial))
        self.viewing.discard(m.serial)
        if not others or cheb(m.pos, self.player.pos) <= VIEW:   # another's kill out of view: `_view` shows it later
            self.viewing_items.add(corpse)
            self.send(P.ground_item(corpse, combat.CORPSE_GRAPHIC, m.body, m.x, m.y, m.z),
                      P.corpse_flags(corpse, m.serial, f"{m.name} corpse", noto))
        if others:
            self.count("others_kills")                   # another player's kill: blue to us, not ours
            if cheb(m.pos, self.player.pos) <= VIEW and not self.player.ghost:
                self.count("others_kills_in_view")
        elif m.expires is None:                       # a transfixed copy is no kill (kills_by still shows it)
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
            lo_s, hi_s = self._respawn_s(m.spawn)
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
        if self.rope_landing and t >= self.rope_landing[0]:
            _, to = self.rope_landing
            self.rope_landing = None
            self._rope_land(to)
        for due, i in [x for x in self.spawn_due if x[0] <= t]:
            self.spawn_due.remove((due, i))
            self._spawn(i)
        for s, it in list(self.items.items()):
            if it.get("kind") == "corpse" and t - it["t"] > CORPSE_DECAY_S:
                self._remove_corpse(s)
            elif it.get("kind") == "fire" and t >= it["until"]:
                del self.items[s]
                if s in self.viewing_items:
                    self.viewing_items.discard(s)
                    self.send(P.delete(s))
        if not self.penalty and t - self.last_step_t >= PENALTY_STILL_S:
            self._apply_penalty()
        if self.player.held_until and t >= self.player.held_until:
            self.player.held_until = 0.0
            self._sys(HELD_END_TEXT)
        if self.hamstrung_until and t >= self.hamstrung_until:
            self.hamstrung_until = 0.0
            pl = self.player
            if not pl.ghost:
                pl.stam = max(pl.stam, self.stam_before or 0)
                self._sys("You are no longer hamstrung.")
                self.send(P.buff_remove(pl.serial, HAMSTRING_ICON), P.stam(pl.serial, pl.stam, pl.stam_max))
        if self.paralyzed_until and t >= self.paralyzed_until:
            self._unparalyze()
        self.reds.tick()
        self._oxygen()
        pl = self.player
        near_others = []
        if self.others_on:
            if self.next_other_t is not None and t >= self.next_other_t:
                self._other_arrive()
                self.next_other_t = t + self.orng.expovariate(self.sc["others"]["arrive_per_h"] / 3600.0)
            near_others = [(o.x, o.y) for o in self.mobs.values() if o.kind in OTHERS]
        near_others += [(o.x, o.y) for o in self.mobs.values() if o.kind == "red" and not o.hidden_until]
        if self.flights:
            landed = [f for f in self.flights if f[0] <= t]
            if landed:
                self.flights = [f for f in self.flights if f[0] > t]
                for f in landed:
                    self._impact(f)
        for m in list(self.mobs.values()):
            if m.serial not in self.mobs:
                continue
            self._effects(m)
            if m.kind == "monster" and m.alive:
                if cheb(m.pos, pl.pos) <= 2 * VIEW or m.target is not None \
                        or any(cheb(o, m.pos) <= 14 for o in near_others):
                    self._monster(m)
                    if m.target is not None and m.target in self.mobs and self.mobs[m.target].kind == "red" \
                            and not pl.ghost and cheb(m.pos, pl.pos) <= VIEW:
                        self.reds.m["monster_on_red_s"] += TICK_S      # live: in our view, a monster on a red was rare
            elif m.kind == "pet":
                self._pet(m)
            elif m.kind == "other":
                self._other(m)
            elif m.kind == "otherpet":
                self._other_pet(m)
            elif m.kind == "red":
                self.reds.act(m)
        if self.others_on and int(round(t / TICK_S)) % 10 == 0:
            self._others_sample()
            self._unseen_tick()
        if self.eerie_on:
            self._eerie_tick()
        if not pl.ghost and self._on_level():
            self.metrics["hunt_s"] += TICK_S
            for name, b in (self.sc.get("places") or {}).items():
                if b[0] <= pl.x <= b[2] and b[1] <= pl.y <= b[3]:
                    ps = self.metrics.setdefault("place_s", {})
                    ps[name] = ps.get(name, 0.0) + TICK_S
            if pl.stam < pl.stam_max:
                self.metrics["stam_below_s"] += TICK_S      # live 22 % of the time on the level (shove_scan)
        if not pl.ghost and t - pl.last_hurt_t > 10 and pl.hits < pl.hits_max and int(t / TICK_S) % int(REGEN_S / TICK_S) == 0:
            pl.hits += 1
            self.send(P.hits(pl.serial, int(pl.hits), int(pl.hits_max)))
        if pl.ghost or pl.stam >= pl.stam_max or self.hamstrung_until > t:
            self.stam_next = None
        elif self.stam_next is None:
            self.stam_next = t + STAM_REGEN_S
        elif t >= self.stam_next:
            pl.stam += 1
            self.stam_next = None if pl.stam >= pl.stam_max else self.stam_next + STAM_REGEN_S
            self.send(P.stam(pl.serial, pl.stam, pl.stam_max))
        if self.trace is not None:
            self.trace.tick(self)

    def _underwater(self, m: Mob) -> bool:
        return any(b[0] <= m.x <= b[2] and b[1] <= m.y <= b[3] for b in (self.sc.get("oxygen") or {}).get("underwater", ()))

    def _oxygen(self):
        """Tidal Tomb's oxygen (scenario `oxygen`, knowledge #4641-4644): our time under water (its levels 2+) adds
        up and a step out of it refills it; past a stage's `after_s` every `every_s` we lose `player` hits while above
        `above` of our max, and each pet `pet_pct` of its max while above `above` of its own (unpreventable: no song
        cuts it). The live oxygen bar and buff icons aren't sent (the runner reads neither)."""
        ox = self.sc.get("oxygen")
        if not ox:
            return
        pl = self.player
        if pl.ghost or not self._underwater(pl):
            self.under_s, self.oxygen_due = 0.0, None
            return
        self.under_s += TICK_S
        self.count("underwater_s", n=TICK_S)
        stage = next((s for s in reversed(ox["stages"]) if self.under_s >= s["after_s"]), None)
        if stage is None:
            return
        if self.oxygen_due is None:
            self.oxygen_due = self.t + ox["every_s"]
        if self.t < self.oxygen_due:
            return
        self.oxygen_due = self._due(self.oxygen_due, ox["every_s"])
        if pl.hits > stage["above"] * pl.hits_max:
            self.hurt(pl, stage["player"], None, "oxygen")
            self.count("oxygen_damage", "player", stage["player"])
        for p in self.pets():
            if p.alive and not p.ghost and p.hits > stage["above"] * p.hits_max:
                self.hurt(p, stage["pet_pct"] * p.hits_max, None, "oxygen")
                self.count("oxygen_damage", p.name, round(stage["pet_pct"] * p.hits_max))

    def _on_level(self) -> bool:
        """Whether we stand on the hunting level (scenario `area`): the time and the damage counted per hour on it,
        as the live visits are."""
        a, pl = self.sc.get("area"), self.player
        return not a or (a[0] <= pl.x <= a[2] and a[1] <= pl.y <= a[3])

    def _effects(self, m: Mob):
        t = self.t
        if m.poison_until > t and t >= m.poison_next:
            m.poison_next = t + 2.0
            self.hurt(m, m.spec.get("_poison_dmg", 4), None, "poison")
        if m.bleed_until and t >= m.bleed_next:
            self.hurt(m, self.roll(m.bleed[0]), None, "bleed")
            m.bleed_next += m.bleed[1]
            if m.bleed_next > m.bleed_until + 1e-6:
                m.bleed_until = 0.0
                if m.kind == "player":
                    self._sys("You are no longer bleeding.")
        for dz in list(m.diseases):
            if t >= dz[0]:
                self.hurt(m, dz[2], None, "disease")
                dz[0], dz[1] = dz[0] + dz[3], dz[1] - 1
                if dz[1] <= 0 and dz in m.diseases:     # the hit above may have cleared them (a death)
                    m.diseases.remove(dz)
                    if not m.diseases and m.kind == "player":
                        self._sys("You are no longer diseased.")
        if (m.calm_until > t or m.provoke) and int(t / TICK_S) % int(1.0 / TICK_S) == 0:
            diff = m.spec.get("difficulty") or 100
            if self.rng.random() < diff / 100.0 / 100.0:     # (difficulty / 100) % per second (patch 2020-04-07)
                m.calm_until = 0.0
                m.provoke = None
                m.immune_until = t + diff / 10.0
                self._overhead(m, "*Barding Break*")
                self._act(m, self.sc["bard"]["break_threat"], count=False)
        if m.provoke and m.provoke[1] <= t:
            m.provoke = None

    # ------------------------------------------------------------ monsters
    def _monster(self, m: Mob):
        t = self.t
        if m.expires is not None and t >= m.expires:
            self._vanish(m)
            return
        if m.hidden_until:
            if t < m.hidden_until:
                return
            m.hidden_until, m.next_swing = 0.0, t       # out of stealth: back on its target, a swing at once
            to = self.mobs.get(m.blink_to) if m.blink_to is not None else None
            m.blink_to = None
            if to is not None and to.alive and not to.ghost:
                self._teleport(m, to.pos)                # live: onto its target's own tile ...
                for sp in m.spec.get("specials", []):
                    if sp["kind"] == "burst" and sp.get("on_blink"):
                        self._burst(m, sp)               # ... and its area hit at that instant
            elif m.relocate is not None:
                self._teleport(m, self._relocate_tile(m, m.relocate))
            m.relocate = None
            self._view()
        if m.charge_at is not None and t >= m.charge_at[0]:
            _, s, sp = m.charge_at
            m.charge_at = None
            to = self.mobs.get(s)
            if to is not None and to.alive and not to.ghost and cheb(m.pos, to.pos) <= sp.get("range", 12):
                self._charge(m, to, sp)
        if m.feed is not None and t >= m.feed[0]:
            self._feed_step(m)
        if m.pulse is not None and t >= m.pulse[0]:
            self._pulse_step(m)
        if m.brood_at is not None and t >= m.brood_at[0]:
            self._emerge(m)
        if m.calm_until > t:
            self._cancel_cast(m, "peace")
            self._warm(m, False)
            return
        if t >= m.next_think:
            m.next_think = t + THINK_S
            self._think(m)
        tgt = self.mobs.get(m.target) if m.target is not None else None
        if tgt is None or not tgt.alive or tgt.ghost:
            m.target = None
            self._cancel_cast(m, "target")
            self._warm(m, False)
            if cheb(m.pos, m.home) > 2 and t >= m.next_step:
                m.next_step = self._due(m.next_step, m.spec["step_s"] * 2)
                self._step_toward(m, m.home, 1)
                return
            if m.hits < m.hits_max and int(t / TICK_S) % int(REGEN_S / TICK_S) == 0:
                m.hits = min(m.hits_max, m.hits + max(1, m.hits_max * 0.01))
            if m.expires is None and self.wrng.random() < self.sc["defaults"].get("wander_per_s", 0.0) * TICK_S:
                for d in self.wrng.sample(range(8), 8):         # the first open way within 2 of home
                    to = self.walk.can_walk(m.x, m.y, m.z, d)
                    if to is not None and cheb(to, m.home) <= 2 and not self._occupied(to[0], to[1], m.serial):
                        self._place(m, to, d)
                        break
            return
        self._warm(m, True)
        if m.cast is None:
            self._abilities(m, tgt, m.spec)
        if m.serial in self.mobs and tgt.alive and not tgt.ghost:
            self._attack(m, tgt, m.spec)

    def _vanish(self, m: Mob):
        """A summoned monster's time is up (a transfixed copy): gone without a corpse."""
        if m.serial in self.viewing:
            self.send(P.delete(m.serial))
        self.viewing.discard(m.serial)
        for o in self.mobs.values():
            if o.target == m.serial:
                o.target = None
            if o.kill == m.serial:
                o.kill, o.mode = None, "follow"
        del self.mobs[m.serial]

    def _think(self, m: Mob):
        """Pick the target: a provoked monster's victim; a transfixed copy: us; else the highest threat in
        sight (damage taken, our aggressive acts before the pets hurt it); else, once one of us is within
        its aggro range and in sight, a guarding pet near us (the trip: all 11 monsters we hadn't acted
        on went to a pet first, docs/NOTES.md), or the nearest of us when no pet guards nearby. Other players
        and their pets are candidates by the same rules (`others`)."""
        t = self.t
        if m.provoke and m.provoke[0] in self.mobs:
            m.target = m.provoke[0]
            return
        if m.spec.get("random_target"):
            # a transfixed copy: live, its 10 attributed hits went 4 to Logan, 6 to the pets; never its
            # original, which stands transfixed (2026-10-13: the copy walked 6 tiles to the phoenix)
            team = [o for o in self.team() if o.serial != m.spec.get("copy_of")]
            near = [o for o in team if cheb(o.pos, m.pos) <= 3] or team
            m.target = self.rng.choice(near).serial if near else None
            return
        cur = self.mobs.get(m.target) if m.target is not None else None
        held = set()
        if any(sp["kind"] == "transfix" for sp in m.spec.get("specials", [])):
            # live (41 visits, 2026-10-20): of the countesses' 47 hits on us while a copy of the gravebug was up, 2
            # went to the held gravebug, 28 to the phoenix, 17 to Logan (the gravebug free: 107 of 134 on it)
            held = {o.spec["copy_of"] for o in self.mobs.values() if o.spec.get("copy_of") and o.alive}
            if m.target in held:
                m.target, cur = None, None
        if cur is not None and (cheb(m.pos, m.home) > LEASH or (t - m.seen_target_t > LOST_S)):
            m.target, m.threat = None, {}
            return
        if cur is not None and self.can_see(m, cur):
            m.seen_target_t = t
        reds = [o for o in self.reds.mobs() if not o.hidden_until]       # players too (sim/reds.py)
        cands = [o for o in self.team() + self.others() + reds if cheb(o.pos, m.pos) <= max(m.spec["aggro_range"], 12)
                 and o.serial not in held]
        if m.key in self.sc["defaults"].get("pets_first", ()):
            # scenario defaults pets_first_note: the Tank, else a pet, over Logan
            ours = [o for o in cands if o.kind == "pet" and not o.ghost]
            keep = [o for o in ours if o.spec.get("threat_x", 1.0) >= 2.0] or ours
            if keep:
                cands = keep + [o for o in cands if o.kind in OTHERS + ("red",)]
                if cur is not None and cur.serial not in {o.serial for o in cands}:
                    m.target, cur = None, None
        live = {o.serial: o for o in cands}
        threat = {s: v for s, v in m.threat.items() if s in live}
        pl = self.player
        if RETARGET_P and cur is not None and cur.kind == "pet" and pl.serial in live and not pl.ghost \
                and cheb(pl.pos, m.pos) < cheb(cur.pos, m.pos) and self.can_see(m, pl) and self.rng.random() < RETARGET_P:
            m.threat[pl.serial] = max(m.threat.get(pl.serial, 0), threat.get(cur.serial, 0) * SWITCH_X + 1)
            m.target, m.seen_target_t = pl.serial, t
            return
        if threat:
            best = max(threat, key=threat.get)
            if best != m.target and (cur is None or threat[best] > threat.get(m.target, 0) * SWITCH_X):
                m.target, m.seen_target_t = best, t
            return
        if cur is not None:
            return
        near = [o for o in cands if cheb(o.pos, m.pos) <= m.spec["aggro_range"] and self.can_see(m, o)]
        if not near:
            m.seen_since = None
            return
        wake = self.sc["defaults"].get("wake_s")
        if wake and not (held and m.warmode):   # a countess in a fight turns from a held pet at once [INFERENCE]
            # live: war mode 1.8-2.7 s after one of us came into its sight (scenario defaults note)
            if m.seen_since is None:
                m.seen_since = t + self.rng.uniform(*wake)
            if t < m.seen_since:
                return
        m.seen_since = None
        # whichever of us it sees first, the nearest seen, a pet on a tie (user 2026-10-15: "Any mob that
        # 'sees' Logan before it sees a pet is going to aggro on him directly"); before, any guarding pet
        # within guard_draw_r 6 of the player drew it, seen or not
        o = min(near, key=lambda o: (cheb(o.pos, m.pos), o.kind in ("player", "other", "red"),
                                     -o.spec.get("threat_x", 1.0)))
        if o.kind == "player" or (o.kind == "pet" and o.spec.get("threat_x", 1.0) < 2.0):
            # but live the gravebug took the first contact in 61 of the 99 wakes where Logan was the nearest seen; the
            # rule above alone gave it 29 % (docs/SIM.md "Parity with the live runs"; the user's call 2026-10-20 to
            # follow the data): a guarding Tank that the monster sees within its range + TANK_DRAW_R (the pets trail
            # us 1-3 tiles) draws it at the wake with `defaults.tank_draw`, fitted to that 61 %. The phoenix nearest
            # too: live its first victim was the gravebug 74 % and the phoenix 7 % (58 wakes, 2026-10-20)
            tanks = [p for p in cands if p.kind == "pet" and p.mode == "guard" and p.spec.get("threat_x", 1.0) >= 2.0
                     and cheb(p.pos, m.pos) <= m.spec["aggro_range"] + TANK_DRAW_R and self.can_see(m, p)]
            if tanks and self.rng.random() < self.sc["defaults"].get("tank_draw", 0.0):
                o = min(tanks, key=lambda p: cheb(p.pos, m.pos))
        m.target, m.seen_target_t = o.serial, t
        m.threat[o.serial] = m.threat.get(o.serial, 0) + 1

    def _attack(self, m: Mob, tgt: Mob, spec: dict):
        """Swing or cast at `tgt` when in reach and sight, else close in."""
        t = self.t
        if m.cast is not None:                   # standing to cast: no step, no swing until it is let go
            if t >= m.cast[0]:
                self._release(m, spec["ranged"])
            return
        ranged = spec.get("ranged")
        d = cheb(m.pos, tgt.pos)
        if ranged and d <= ranged["range"] and self.can_see(m, tgt):
            if t >= m.next_swing:
                m.next_swing = self._due(m.next_swing, ranged["every_s"] * self.rng.uniform(0.85, 1.15))
                self._cast(m, tgt, ranged)
                if m.cast is not None:
                    return
            if d <= ranged.get("keep", ranged["range"]) or not spec.get("melee"):
                return
        if d <= 1 and abs(m.z - tgt.z) <= 15:
            if t >= m.next_swing and spec.get("melee"):
                m.next_swing = self._due(m.next_swing, spec["swing_s"] * self.rng.uniform(0.9, 1.1))
                hit = self.rng.random() < spec.get("hit_chance", 0.8)
                self._ev("swing", by=m.serial, v=tgt.serial, hit=hit)
                if hit:
                    self.hurt(tgt, self._melee_on(tgt, spec), m)
                    self._specials(m, tgt, spec)
            return
        if t >= m.next_step:
            m.next_step = self._due(m.next_step, spec["step_s"])
            self._step_toward(m, tgt.pos, 1)

    def _melee_on(self, tgt: Mob, spec: dict) -> float:
        """One monster melee hit on `tgt`. On the player: `melee_player` (the wiki melee x `player_melee_k`, uniform)
        less his armor, a uniform share of AR/300 to AR/150 (sim/armor.py; docs/SIM.md "Player armor"). Elsewhere the
        type's `melee` numbers (the gravebug's), x `_taken_x`."""
        if tgt.kind == "player":
            return self.rng.uniform(*spec["melee_player"]) * armor.taken(tgt.ar, self.rng)
        return self.roll(spec["melee"]) * self._taken_x(tgt, "melee")

    def _taken_x(self, tgt: Mob, kind: str) -> float:
        """A monster's spell, feed and (on a pet) melee numbers are the gravebug's (it takes most hits); the others
        take more: Logan `damage_to_player_x` of a spell and `feed_to_player_x` of a feed tick, the phoenix its
        `melee_taken_x` of a melee hit or feed tick (scenario notes). His melee: `_melee_on`."""
        if tgt.kind == "player":
            d = self.sc["defaults"]
            return d["feed_to_player_x"] if kind == "feed" else d["damage_to_player_x"]
        if tgt.kind == "pet" and kind in ("melee", "feed"):
            return tgt.spec.get("melee_taken_x", 1.0)
        return 1.0

    def _cast(self, m: Mob, tgt: Mob, ranged: dict):
        """A ranged attack begins: its animation, then `cast_s` standing (scenario `cast_s`, measured by
        sim/cast_scan.py) before `_release`; at once when the type has no cast time."""
        t = self.t
        cs = ranged.get("cast_s")
        end = t + (self.roll(cs) if cs and cs[1] > 0 else 0.0)
        m.cast = [end, tgt.serial, t]
        if m.serial in self.viewing:
            self.send(P.animation(m.serial, ranged.get("cast_action") or 0))
        self._ev("cast", by=m.serial, v=tgt.serial, end=round(end, 2), action=ranged.get("cast_action"), spell=None)
        if end <= t:
            self._release(m, ranged)

    def _release(self, m: Mob, ranged: dict):
        """A cast's end: lost when its target is gone, out of `range` or out of sight then (the next one
        `every_s` later), else on its way: the 0xC0 effect, landing `flight_s` later (`_impact`). Damage taken
        while casting never breaks it."""
        t, s = self.t, m.cast[1]
        m.cast = None
        victim = self.mobs.get(s)
        why = ("gone" if victim is None or not victim.alive or victim.ghost
               else "range" if cheb(m.pos, victim.pos) > ranged["range"]
               else "sight" if not self.can_see(m, victim) else None)
        if why is not None:
            self._ev("cast_lost", by=m.serial, v=s, why=why)
            m.next_swing = t + ranged["every_s"] * self.rng.uniform(0.85, 1.15)
            return
        pl = self.player
        if m.kind == "monster" and SHOT_AT_PLAYER_P and victim.kind == "pet" and pl.alive and not pl.ghost \
                and cheb(m.pos, pl.pos) <= ranged["range"] and self.can_see(m, pl) \
                and self.rng.random() < SHOT_AT_PLAYER_P:
            victim = pl
        fl = ranged.get("flight_s")
        land = t + (self.roll(fl) if fl and fl[1] > 0 else 0.0)
        e = ranged.get("effect")
        if e and (m.serial in self.viewing or victim.serial in self.viewing or victim is pl or m is pl):
            # [INFERENCE] the live server's broadcast range; the runner reacts only to effects on us
            self.send(P.effect(0, m.serial, victim.serial, e["graphic"], m.x, m.y, m.z, victim.x, victim.y, victim.z,
                               e["speed"], e["duration"], 0, e["explode"], e["hue"], e["render"]))
        self._ev("release", by=m.serial, v=victim.serial, land=round(land, 2), g=e["graphic"] if e else None,
                 x=m.x, y=m.y, z=m.z, spell=None)
        if land <= t:
            self._impact((land, m, victim.serial))
        else:
            self.flights.append((land, m, victim.serial))

    def _impact(self, f):
        """A released ranged attack lands (its attacker may have died since): the spell's damage, and a
        monster's on-hit specials and fire."""
        _, m, s = f
        victim = self.mobs.get(s)
        if victim is None or not victim.alive or victim.ghost:
            return
        ranged = m.spec["ranged"]
        if m.kind == "pet":
            self.hurt(victim, self.roll(ranged["damage"]), m, "spell")
            return
        self.hurt(victim, self.roll(ranged["damage"]) * self._taken_x(victim, "spell"), m, "spell")
        self._specials(m, victim, m.spec)
        for sp in m.spec.get("specials", []):
            if sp["kind"] == "fire_mark":
                self._fire_mark(victim, sp)

    def _cancel_cast(self, m: Mob, why: str):
        """A cast under way stops (pacified, no target, hidden, transfixed, dead): nothing is let go."""
        if m.cast is not None:
            self._ev("cast_lost", by=m.serial, v=m.cast[1], why=why)
            m.cast = None

    def _specials(self, m: Mob, tgt: Mob, spec: dict):
        """On-hit specials (scenario `specials`; the timed ones are `_abilities`)."""
        for sp in spec.get("specials", []):
            kind = sp["kind"]
            chance = sp.get("pet_chance", sp.get("chance", 0)) if tgt.kind == "pet" else sp.get("chance", 0)
            if kind not in ON_HIT or self.rng.random() >= chance:
                continue
            if kind == "bleed" and not tgt.bleed_until:
                self._start_bleed(tgt, sp)
                if tgt.kind == "player":
                    self._sys("The creature strikes ferociously, causing you to bleed!")
            elif kind == "bleed_area":
                # the trip's bound souls: meleeing the gravebug, they bled Logan a tile off too
                # ("They begin to draw blood from you!" with an opening -23/-32) [INFERENCE: an area]
                for o in self.team():
                    if cheb(o.pos, m.pos) <= sp.get("radius", 1) and not o.bleed_until:
                        if o.kind == "player":
                            self._sys("They begin to draw blood from you!")
                        self.hurt(o, self.roll(sp["opener"]), m, "bleed")
                        self._start_bleed(o, sp)
            elif kind == "poison" and tgt.poison_until <= self.t:
                tgt.poison_until, tgt.poison_next = self.t + sp.get("seconds", 12), self.t + 2
                tgt.spec = {**tgt.spec, "_poison_dmg": sp.get("damage", 4)}
                self._overhead(tgt, tamer.TEXT_POISONED)
            elif kind == "drain":
                self.hurt(tgt, sp.get("damage", 8), m, "drain")
                self.heal(m, sp.get("damage", 8))
            elif kind == "disease":
                # corpse purger (20261014_221341 22:33:53): "You have contracted a foul disease!", then -21
                # every 5 s, 4 times; a second infection 4 s later ran beside the first
                tgt.diseases.append([self.t + sp["every_s"], sp["ticks"], sp["tick"], sp["every_s"]])
                if tgt.kind == "player":
                    self._sys("You have contracted a foul disease!")

    def _start_bleed(self, o: Mob, sp: dict):
        """A bleed: `tick` damage every `every_s` for `seconds` (the trip: 8-10 every 3 s, 5 ticks, then
        "You are no longer bleeding.")."""
        if not o.alive or o.ghost:
            return
        every = sp.get("every_s", 3.0)
        o.bleed = (sp.get("tick", [8, 10]), every)
        o.bleed_until, o.bleed_next = self.t + sp.get("seconds", 15.0), self.t + every

    def _fire_mark(self, tgt: Mob, sp: dict):
        """A forgotten soul's spell hit: fire (0x6D70) on the tile of whom it hit, `lifetime_s` (capture
        20261014_210020: 7 of them, 5.4-5.7 s, each at the instant of the hit; none hurt anyone on or beside
        it, so no damage here)."""
        s = self._item_serial()
        self.items[s] = {"kind": "fire", "graphic": sp["graphic"], "x": tgt.x, "y": tgt.y, "z": tgt.z,
                         "until": self.t + self.rng.uniform(*sp["lifetime_s"])}
        if cheb(tgt.pos, self.player.pos) <= VIEW:
            self.viewing_items.add(s)
            self.send(P.ground_item(s, sp["graphic"], 1, tgt.x, tgt.y, tgt.z))
        self.count("fire_marks", "on us" if tgt.kind == "player" else tgt.name)

    def _abilities(self, m: Mob, tgt: Mob, spec: dict):
        """Timed specials while fighting (scenario `specials`, each with its evidence): the Charge, the
        transfix, the wail, the bursts, the Vanish, the apparition's blink, the vampires' feed; Tidal Tomb's ooze aura
        and the nest crab's brood."""
        t = self.t
        for sp in spec.get("specials", []):
            kind = sp["kind"]
            if kind not in TIMED or t < m.cd.get(kind, 0.0):
                continue
            if kind == "charge":
                if m.charge_at is not None or cheb(m.pos, tgt.pos) > sp.get("range", 12) or not self.can_see(m, tgt):
                    continue
                # live: '*charges*', then the jump and its hit 0.8-1.1 s later (scenario note)
                m.cd[kind] = t + sp["cooldown_s"] * self.rng.uniform(0.9, 1.1)
                self._ev("ability", by=m.serial, kind=kind, v=tgt.serial)
                self._overhead(m, "*charges*")
                m.charge_at = (t + self.rng.uniform(*sp.get("delay_s", (0.0, 0.0))), tgt.serial, sp)
            elif kind == "transfix":
                m.cd[kind] = t + sp["every_s"] * self.rng.uniform(0.9, 1.1)
                self._ev("ability", by=m.serial, kind=kind, v=tgt.serial)
                self._overhead(m, "*attempts to transfix*")
                pets = [p for p in self.pets() if p.alive and not p.ghost and p.held_until <= t
                        and cheb(p.pos, m.pos) <= sp.get("range", 12)]
                pl, share = self.player, sp.get("player_share", 0.0)
                me_ok = share > 0 and not pl.ghost and pl.held_until <= t and cheb(pl.pos, m.pos) <= sp.get("range", 12)
                if (pets or me_ok) and self.rng.random() < sp["chance"]:
                    if me_ok and (not pets or self.rng.random() < share):
                        self.transfix_player(self.rng.uniform(*sp["player_s"]))
                    else:
                        self._transfix(self.rng.choice(pets), sp, m)
            elif kind == "wail":
                # corpsebride: an area hit 0.4 s after the emote (scenario note)
                m.cd[kind] = t + sp["every_s"] * self.rng.uniform(0.99, 1.01)
                self._ev("ability", by=m.serial, kind=kind, v=None)
                self._overhead(m, "*wails*")
                near, far = sp.get("radius", 1), sp.get("far_r", 0)
                for o in self.team():
                    d = cheb(o.pos, m.pos)
                    p = sp.get("near_chance", 1.0) if d <= near else sp.get("far_chance", 0.0) if d <= far else 0.0
                    if p and self.rng.random() < p:
                        dmg = sp.get("damage_player" if o.kind == "player" else "damage_pet", sp.get("damage"))
                        self.hurt(o, self.roll(dmg), m, "wail")
            elif kind == "burst":
                if sp.get("on_blink"):
                    continue                             # the apparition's: only where its blink lands
                # reanimated daemon (20261014_221341): bones around it out to ~5 tiles, one hit on each of us
                if kind not in m.cd:
                    m.cd[kind] = t + self.rng.uniform(*sp["first_s"])
                    continue
                m.cd[kind] = t + sp["every_s"] * self.rng.uniform(0.9, 1.1)
                self._burst(m, sp)
            elif kind == "vanish":
                if kind not in m.cd:                     # the first one comes a while into the fight
                    m.cd[kind] = t + self.rng.uniform(*sp["first_s"])
                    continue
                m.cd[kind] = t + self.rng.uniform(*sp["cooldown_s"])
                self._ev("ability", by=m.serial, kind=kind, v=None)
                m.hidden_until = t + self.rng.uniform(*sp["hidden_s"])
                if m.serial in self.viewing:
                    self.send(P.delete(m.serial))
                    self.viewing.discard(m.serial)
                return
            elif kind == "blink":
                # an apparition (scenario note): every ~8 s from its waking, gone a moment, back on its target's
                # tile with its area hit, whether the target was beside it or across the room
                if kind not in m.cd:
                    m.cd[kind] = t + self.rng.uniform(*sp["first_s"])
                    continue
                if cheb(m.pos, tgt.pos) > sp.get("range", 12):
                    continue
                m.cd[kind] = t + self.rng.uniform(*sp["cooldown_s"])
                self._ev("ability", by=m.serial, kind=kind, v=tgt.serial)
                self._overhead(m, "*phases*")
                m.hidden_until, m.blink_to = t + self.rng.uniform(*sp["hidden_s"]), tgt.serial
                if m.serial in self.viewing:
                    self.send(P.delete(m.serial))
                    self.viewing.discard(m.serial)
                return
            elif kind == "feed":
                # a vampire beside its target: '*attempts to feed*', then maybe '*feeds*' and quick ticks on it
                if m.feed is not None or cheb(m.pos, tgt.pos) > 1:
                    continue
                m.cd[kind] = t + self.rng.uniform(*sp["every_s"])
                self._ev("ability", by=m.serial, kind=kind, v=tgt.serial)
                self._overhead(m, "*attempts to feed*")
                m.feed = [t + self.rng.uniform(*sp["delay_s"]), None, tgt.serial, sp]
            elif kind == "ooze":
                # an oozing noughtilus (Tidal Tomb scenario note): its Ooze Aura, 4 bursts 0.3 s apart on
                # everyone within 2 tiles, every ~7-12 s from a while into the fight
                if kind not in m.cd:
                    m.cd[kind] = t + self.rng.uniform(*sp["first_s"])
                    continue
                if m.pulse is not None or not any(cheb(o.pos, m.pos) <= sp["radius"] for o in self.team()):
                    continue
                m.cd[kind] = t + self.rng.uniform(*sp["every_s"])
                self._ev("ability", by=m.serial, kind=kind, v=None)
                m.pulse = [t + sp.get("delay_s", 0.0), sp["bursts"], sp]
            elif kind == "brood":
                # a dungeon-nest crab (Tidal Tomb scenario note): '*digs*', and a pincer crab '*emerges*'
                # beside it 0.5 s later and joins the fight
                if kind not in m.cd:
                    m.cd[kind] = t + self.rng.uniform(*sp["first_s"])
                    continue
                if m.brood_at is not None or sum(1 for o in self.mobs.values() if o.alive and o.kind == "monster"
                                                 and o.spec.get("summoner") == m.serial) >= sp.get("max", 1):
                    continue
                m.cd[kind] = t + self.rng.uniform(*sp["every_s"])
                self._ev("ability", by=m.serial, kind=kind, v=None)
                self._overhead(m, "*digs*")
                m.brood_at = (t + sp.get("delay_s", 0.5), sp)

    def _pulse_step(self, m: Mob):
        """One burst of an ooze aura under way: a hit on each of us within `radius` (the song cuts it as any
        creature damage), the next `burst_s` later."""
        when, left, sp = m.pulse
        for o in self.team():
            if cheb(o.pos, m.pos) <= sp["radius"]:
                self.hurt(o, self.roll(sp["damage_player" if o.kind == "player" else "damage_pet"]), m, "ooze")
        m.pulse = [self._due(when, sp["burst_s"]), left - 1, sp] if left > 1 and m.serial in self.mobs else None

    def _emerge(self, m: Mob):
        """A nest crab's broodling: a `key` monster on a free tile beside it, on its target at once; no spawn of
        its own (no respawn), a kill as any monster's."""
        _, sp = m.brood_at
        m.brood_at = None
        spec = {**self.sc["creatures"][sp["key"]], "summoner": m.serial}
        free = [(m.x + dx, m.y + dy) for dx, dy in nav.DIR_DELTA if not self._occupied(m.x + dx, m.y + dy)]
        tiles = [(x, y, self.stand_z(x, y, m.z)) for x, y in free]
        tiles = [p for p in tiles if abs(p[2] - m.z) <= 10 and any(self.walk.can_walk(*p, d) for d in range(8))]
        if not tiles:
            return
        x, y, z = self.rng.choice(tiles)
        hp = int(spec["hits"][0] if isinstance(spec["hits"], list) else spec["hits"])
        c = Mob(self._serial(), "monster", sp["key"], spec["name"], spec["body"], x, y, z, hits=hp, hits_max=hp,
                noto=spec.get("notoriety", 3), spec=spec, home=(x, y, z))
        c.target, c.next_think = m.target, self.t + THINK_S
        self.mobs[c.serial] = c
        self._view()
        self._overhead(c, "*emerges*")
        self.count("broodlings", sp["key"])

    def _burst(self, m: Mob, sp: dict):
        """One area hit on each of us within `radius` (`song_pets` false: the pets' share isn't cut by the
        Song of Discordance, as the apparition's live)."""
        self._ev("ability", by=m.serial, kind="burst", v=None)
        for o in self.team():
            if cheb(o.pos, m.pos) <= sp["radius"]:
                self.hurt(o, self.roll(sp["damage_player" if o.kind == "player" else "damage_pet"]), m, "burst",
                          song_pets=sp.get("song_pets", True))

    def _dematerialize(self, m: Mob, sp: dict):
        """A banshee hurt with the ability ready: '*dematerializes*', gone for a moment, back `jump` tiles
        from where it was, its target kept (scenario note)."""
        t = self.t
        self._cancel_cast(m, "hidden")
        self._ev("ability", by=m.serial, kind="dematerialize", v=m.target)
        m.cd["dematerialize"] = t + self.rng.uniform(*sp["cooldown_s"])
        self._overhead(m, "*dematerializes*")
        m.hidden_until, m.relocate = t + self.rng.uniform(*sp["hidden_s"]), tuple(sp["jump"])
        if m.serial in self.viewing:
            self.send(P.delete(m.serial))
            self.viewing.discard(m.serial)

    def _relocate_tile(self, m: Mob, jump) -> tuple:
        """A free floor tile `jump` (lo, hi) tiles from `m` in its sight, else where it stands [INFERENCE: random]."""
        lo, hi = jump
        for _ in range(60):
            dx, dy = self.rng.randint(-hi, hi), self.rng.randint(-hi, hi)
            if max(abs(dx), abs(dy)) < lo:
                continue
            x, y = m.x + dx, m.y + dy
            z = self.stand_z(x, y, m.z)
            if abs(z - m.z) > 20 or self._occupied(x, y, m.serial) \
                    or not any(self.walk.can_walk(x, y, z, d) for d in TELEPORT_DIRS) \
                    or not self.sight.can_see(m.pos, (x, y, z)):
                continue
            return (x, y, z)
        return m.pos

    def _feed_step(self, m: Mob):
        """A feed under way: the attempt's outcome, then its ticks 0.5 s apart on the target."""
        t = self.t
        when, left, s, sp = m.feed
        tgt = self.mobs.get(s)
        if tgt is None or not tgt.alive or tgt.ghost or cheb(m.pos, tgt.pos) > 1:
            m.feed = None
            return
        if left is None:
            if self.rng.random() >= sp["chance"]:
                m.feed = None
                return
            self._overhead(m, "*feeds*")
            m.feed = [t + sp.get("first_tick_s", 0.7), self.rng.randint(*sp["ticks"]), s, sp]
            return
        self.hurt(tgt, self.roll(sp["tick"]) * self._taken_x(tgt, "feed"), m, "feed")
        m.feed = [self._due(when, sp.get("tick_s", 0.5)), left - 1, s, sp] if left > 1 else None

    def _charge(self, m: Mob, tgt: Mob, sp: dict):
        """Charge (knowledge #14625: teleports up to 12 tiles, hits up to 3 targets within 1 tile; live the
        jump onto or beside its target 1.0 s after '*charges*', each of us within a tile hit at once)."""
        self._ev("ability", by=m.serial, kind="charge_land", v=tgt.serial)
        if cheb(m.pos, tgt.pos) > 1:
            spots = [(tgt.x + dx, tgt.y + dy) for dx, dy in nav.DIR_DELTA
                     if not self._occupied(tgt.x + dx, tgt.y + dy)
                     and self.walk.can_walk(tgt.x, tgt.y, tgt.z, nav.direction((tgt.x, tgt.y), (tgt.x + dx, tgt.y + dy)))]
            if not spots:
                return
            to = min(spots, key=lambda p: cheb(p, m.pos))
            self._teleport(m, (to[0], to[1], tgt.z))
            self._moved(m)
        hit = [tgt] + [o for o in self.team() if o is not tgt and cheb(o.pos, m.pos) <= 1]
        for o in hit[:sp.get("targets", 3)]:
            if cheb(o.pos, m.pos) <= 1:
                dmg = sp.get("damage_player", sp["damage"]) if o.kind == "player" else sp["damage"]
                self.hurt(o, self.roll(dmg), m, "charge")

    def transfix_player(self, seconds: float):
        """The vampire countess' transfix on us (live 2026-10-17 15:27:03 and 2026-10-20 19:23:23.04, Logan): the
        system line HELD_TEXT, every step denied and no item use for 11.1 s (both), then HELD_END_TEXT."""
        self._ev("ability", by=None, kind="transfix_hit", v=self.player.serial)
        self.player.held_until = self.t + seconds
        self._sys(HELD_TEXT)
        self.count("transfixed", self.player.name)

    def _transfix(self, pet: Mob, sp: dict, by: Mob):
        """The vampire countess' transfix: a hostile copy of one of our pets, named as the pet, on its
        tile, while the pet itself stands transfixed (no step, no swing) until the copy vanishes, after a
        hit on it at once. Live (scenario note): 9.9-11.0 s each; a copy walks and swings at the monsters'
        pace (`copy_step_s`, `swing_s`), not its pet's; a second one can come while the first is up (on
        the other pet)."""
        self._ev("ability", by=by.serial, kind="transfix_hit", v=pet.serial)
        spec = {"name": pet.name, "body": pet.body, "melee": sp["melee"], "swing_s": sp["swing_s"],
                "hit_chance": sp.get("hit_chance", 0.9), "step_s": sp.get("copy_step_s", pet.spec.get("step_s", 0.21)),
                "aggro_range": 12, "gold": [0, 0], "difficulty": sp.get("difficulty", 100.0),
                "melee_player": sp["melee_player"],
                "specials": [], "random_target": True, "copy_of": pet.serial, "summoner": by.serial}
        x, y, z = pet.pos
        hp = sp["hits"]
        c = Mob(self._serial(), "monster", "transfixed copy", pet.name, pet.body, x, y, z, hits=hp, hits_max=hp,
                noto=3, spec=spec, home=(x, y, z))
        c.expires = self.t + self.rng.uniform(*sp["lifetime_s"])
        c.next_think = self.t + self.rng.uniform(*sp.get("wake_s", (0.0, 0.0)))
        pet.held_until, pet.target = c.expires, None
        self.mobs[c.serial] = c
        self._view()
        self._overhead(c, "*transfixed*")
        if sp.get("pet_damage"):
            self.hurt(pet, self.roll(sp["pet_damage"]), by, "transfix")
        self.count("transfixed", pet.name)

    # ------------------------------------------------------------ pets
    def _pet(self, m: Mob):
        t = self.t
        owner = self.player
        follow = m.spec.get("follow", 1)
        if m.ghost or not m.alive:
            if t >= m.next_step and cheb(m.pos, owner.pos) > follow + 1:
                m.next_step = self._due(m.next_step, m.spec["step_s"])
                self._step_toward(m, owner.pos, follow + 1)
            return
        if m.held_until > t and any(o.spec.get("copy_of") == m.serial for o in self.mobs.values()):
            m.target = None                      # transfixed: live, no step and no attack animation
            self._cancel_cast(m, "held")
            return
        dv = m.spec.get("devour")
        if dv and m.hits < m.hits_max and t >= m.devour_t:
            # live (3 captures): '*devours corpse*' every 15.0-15.2 s while a corpse is near, 60 of 67 times
            # mid-fight, then '+18' three times 0.5 s apart (+21 under a Song of Peacemaking)
            c = next((s for s, it in self.items.items() if it.get("kind") == "corpse"
                      and cheb((it["x"], it["y"]), m.pos) <= dv.get("range", 2)), None)
            if c is not None:
                m.devour_t = t + dv.get("every_s", 15.0)
                self._overhead(m, "*devours corpse*")
                for _ in range(dv.get("ticks", 3)):
                    self.heal(m, dv.get("heal", 18))
        if t >= m.next_think:
            m.next_think = t + THINK_S
            self._pet_think(m)
        tgt = self.mobs.get(m.target) if m.target is not None else None
        if tgt is not None and tgt.alive and tgt.hidden_until and tgt.blink_to is not None and m.mode != "follow":
            return                               # its target blinked: it holds (live 20261017_110506: the gravebug
            #                                      moved 0-1 tiles in the 1.6 s after 15 of 20 '*phases*')
        if tgt is not None and tgt.alive and not tgt.hidden_until and cheb(owner.pos, tgt.pos) <= m.spec.get("leash", 14):
            self._warm(m, True)
            self._pet_attack(m, tgt)
        else:
            m.target = None
            self._cancel_cast(m, "target")
            self._warm(m, False)
            if m.mode == "patrol" and m.patrol is not None:
                # live: they run to the spot and stay about it until called back (17:57:37-45)
                if t >= m.next_step and cheb(m.pos, m.patrol) > 0:
                    m.next_step = self._due(m.next_step, m.spec["step_s"])
                    self._step_toward(m, m.patrol, 0)
            elif m.mode != "stay" and t >= m.next_step and cheb(m.pos, owner.pos) > follow:
                m.next_step = self._due(m.next_step, m.spec["step_s"])
                self._step_toward(m, owner.pos, follow)
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
        on_us = [o for o in self.mobs.values() if o.kind == "monster" and o.alive and not o.hidden_until
                 and (o.target in team or self.t - max(o.last_hurt_t, 0) < 5 and any(s in team for s in o.damage_by))
                 and cheb(o.pos, owner.pos) <= m.spec.get("leash", 14)]
        if m.mode == "guard" and on_us:
            if m.target not in {o.serial for o in on_us}:
                m.target = min(on_us, key=lambda o: (o.target != owner.serial, cheb(o.pos, owner.pos))).serial
            return
        if m.mode == "follow":
            m.target = None                      # following: it comes along, whatever hits it [INFERENCE]
            return
        if m.mode == "patrol" and m.patrol is not None and m.target is None and cheb(m.pos, m.patrol) <= 1:
            # live demo 2026-10-18: on arrival they attacked an idle monster 1-3 tiles from the spot (5 of 5)
            near = [o for o in self.mobs.values() if o.kind == "monster" and o.alive and not o.hidden_until
                    and cheb(o.pos, m.patrol) <= PATROL_TAKE_R and self.can_see(m, o)]
            if near:
                m.target = min(near, key=lambda o: cheb(o.pos, m.pos)).serial
                return
        mine = [o for o in on_us if o.target == m.serial]
        if mine and m.target is None:
            m.target = min(mine, key=lambda o: cheb(o.pos, m.pos)).serial

    def _pet_attack(self, m: Mob, tgt: Mob):
        t = self.t
        if m.cast is not None:                   # standing to cast
            if t >= m.cast[0]:
                self._release(m, m.spec["ranged"])
            return
        spec = m.spec
        ranged = spec.get("ranged")
        d = cheb(m.pos, tgt.pos)
        if ranged and d <= ranged["range"] and self.can_see(m, tgt):
            if t >= m.next_swing:
                m.next_swing = self._due(m.next_swing, ranged["every_s"] * self.rng.uniform(0.85, 1.15))
                self._cast(m, tgt, ranged)
            # it holds where it has the shot: live in fights the phoenix stood 3+ tiles from Logan 57 % of the time
            # (one-second samples, 41 visits, 2026-10-20); kept within follow + 1 of him it was 11 %, and a charge
            # at it landed beside him (Logan within a tile of a charge's landing 17 % against the live 8 %)
            return
        if ranged and not spec.get("melee"):
            if t >= m.next_step:
                m.next_step = self._due(m.next_step, spec["step_s"])
                self._step_to_shot(m, tgt, ranged["range"])
            return
        if d <= 1:
            if spec.get("melee") and t >= m.next_swing:
                m.next_swing = self._due(m.next_swing, spec["swing_s"] * self.rng.uniform(0.9, 1.1))
                hit = self.rng.random() < spec.get("hit_chance", 0.85)
                self._ev("swing", by=m.serial, v=tgt.serial, hit=hit)
                if hit:
                    self.hurt(tgt, self.roll(spec["melee"]), m)
                    for sp in spec.get("specials", []):
                        if sp["kind"] == "poison" and self.rng.random() < sp.get("chance", 0):
                            tgt.poison_until, tgt.poison_next = t + sp.get("seconds", 12), t + 2
                            tgt.spec = {**tgt.spec, "_poison_dmg": sp.get("damage", 4)}
                            self._overhead(tgt, tamer.TEXT_POISONED)
            return
        if t >= m.next_step:
            m.next_step = self._due(m.next_step, spec["step_s"])
            self._step_toward(m, tgt.pos, 1)

    def _step_to_shot(self, m: Mob, tgt: Mob, rng: int):
        """A ranged pet without a shot: a step on the way to the nearest tile within `rng` of its target that sees
        it. Live the phoenix went 10+ s without an attack in 1.5 % of the gravebug's fighting time (14 of 948 s an
        hour, 35 visits, 2026-10-20); stepping straight at the target, the simulator's stood 25 % of its targeting
        time behind a pillar or the gravebug in a gap, 2 tiles off and blind, while the gravebug fought alone."""
        if m.path:
            nxt = m.path.pop(0)
            if not self._occupied(nxt[0], nxt[1], m.serial) and cheb(nxt, m.pos) == 1:
                self._place(m, nxt)
                return
            m.path = []
        occ = {(o.x, o.y) for o in self.mobs.values() if o.alive and not o.ghost and o.serial != m.serial}
        path = pathfind.plan(self.walk, m.pos, _ShotGoal(self.sight, tgt.pos, rng), occupied=occ,
                             max_expand=4000, max_steps=40)
        if path and len(path) > 1:
            m.path = [tuple(p) for p in path[2:]]
            self._place(m, tuple(path[1]))

    # ------------------------------------------------------------ client packets
    def c2s(self, p: bytes, seq: int | None = None):
        pid = p[0]
        if pid == 0x02:
            self._walk(p)
        elif pid == 0xAD:
            self._speech(p)
        elif pid == 0x03 and p[8:-1].decode("ascii", "replace").strip().lower() == "[rope":
            self._rope()
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
        if pl.ghost or pl.held_until > self.t or self.rope_landing is not None:   # a rope's jump on its way: hindered
            self.send(P.deny_walk(seq, pl.x, pl.y, pl.z, pl.d))
            return
        if self.paralyzed_until > self.t:
            self.send(P.cliloc(FROZEN_CLILOC), P.deny_walk(seq, pl.x, pl.y, pl.z, pl.d))
            return
        if self.hamstrung_until > self.t and self.t - self.last_step_t < HAMSTRING_STEP_S:
            self.send(P.deny_walk(seq, pl.x, pl.y, pl.z, pl.d))      # hamstrung: a walk's pace only
            return
        if d != pl.d:
            pl.d = d
            self.send(P.confirm_walk(seq))
            return
        to = self.walk.can_walk(pl.x, pl.y, pl.z, d)
        # our own pets never block (the captures since 10-13: steps into GraveRobber's or AlaFlambe's tile went through
        # 1,257 of 1,278 times, 244 of 255 below full stamina, 14 with a shove's cliloc) [INFERENCE: the owner walks
        # through, or the pet steps aside first]
        blocker = None if to is None else next((m for m in self.mobs.values() if m.alive and not m.ghost
                                                and m.kind != "pet" and (m.x, m.y) == to[:2]
                                                and m.serial != pl.serial), None)
        if to is None or (blocker is not None and pl.stam < pl.stam_max):
            self.metrics["denied_steps"] += 1
            self.send(P.deny_walk(seq, pl.x, pl.y, pl.z, pl.d))
            return
        if blocker is not None:
            # a shove (user 2026-10-22: "in Outlands I can run through mobs when I have full stamina, and they block me
            # when I don't"): live the cliloc, the confirm, then 0xA3 with SHOVE_STAM less
            self.metrics["shoves"] += 1
            by = self.metrics.setdefault("shoves_by", {})
            k = "player" if blocker.kind in ("other", "red") else blocker.key
            by[k] = by.get(k, 0) + 1
            self.send(P.cliloc(SHOVE_HIDDEN_CLILOC if blocker.hidden_until > self.t else SHOVE_CLILOC))
        pl.x, pl.y, pl.z = to
        self.send(P.confirm_walk(seq))
        self.metrics["steps"] += 1
        self.steps_taken += 1
        lost = (SHOVE_STAM if blocker is not None else 0) + (1 if self.steps_taken % STEP_STAM_EVERY == 0 else 0)
        if lost:
            pl.stam = max(0, pl.stam - lost)
            if self.stam_next is None:
                self.stam_next = self.t + STAM_REGEN_S
            self.send(P.stam(pl.serial, pl.stam, pl.stam_max))
        self.last_step_t = self.t
        if self.penalty:
            self.penalty -= 1
            self.send(P.penalty_buff(pl.serial, self.penalty) if self.penalty else P.penalty_remove(pl.serial))
        tp = next((tp for tp in self.sc.get("teleporters", []) if tuple(tp["from"][:2]) == (pl.x, pl.y)), None)
        if tp is not None:
            self.reds.player_teleported((pl.x, pl.y))
            self._move_party(tp["to"], follow_r=VIEW)
        gate = self.gates.get((pl.x, pl.y))
        if gate is not None:
            self._gate_menu(gate)
        self._view()

    # speech: pet commands
    def _speech(self, p):
        from world.parsers import parse_packet
        text = (parse_packet("c2s", p).get("text") or "").strip().lower()
        self._ev("speech", text=text)
        if tamer.irreversible(text):
            self.metrics["violations"].append({"t": round(self.t, 1), "what": "release/transfer speech",
                                               "text": text})
            return
        if self.player.held_until > self.t:
            # transfixed: live 2026-10-17 15:27:06.20 and 2026-10-21 09:49:20.89, 0.04-0.05 s after a pet command,
            # and the pets didn't take it (09:49:20 "All Follow Me": they fought on at the countess on "All Kill")
            self.metrics["silenced"] += 1
            self._sys(HELD_SILENCED_TEXT)
            return
        pets = [m for m in self.pets() if m.alive and not m.ghost]
        if text == "all guard me":
            self.metrics["guard_commands"] += 1
            for m in pets:
                m.mode, m.kill, m.patrol = "guard", None, None
        elif text == "all follow me":
            self.metrics["follow_commands"] += 1
            for m in pets:
                m.mode, m.kill, m.target, m.patrol = "follow", None, None, None
        elif text in ("all stop", "all stay"):
            for m in pets:
                m.mode, m.kill, m.target, m.patrol = "stay", None, None, None
        elif text == "all kill":
            self.metrics["kill_commands"] += 1
            self._cursor({"kind": "kill"})
        elif text == "all patrol":
            # live 2026-10-18 17:55:57: a location cursor at once, then this line
            self.metrics["patrol_commands"] += 1
            self._cursor({"kind": "patrol"}, cursor_type=0, target_type=1)
            self._sys("Click on the location where you wish your followers to patrol.")

    def _cursor(self, pending, cursor_type=1, target_type=0):
        self.cid += 1
        self.pending = {**pending, "cid": self.cid}
        self.send(P.cursor(self.cid, cursor_type, target_type))

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
        if self.played not in self.items:
            # live 20261015_171928 after the 18:40 death (the lute left on the corpse): "What instrument shall
            # you play?" and a cursor before every skill; the skill's own prompt only after one is picked
            self.send(P.cliloc(tamer.CLILOC_PICK_INSTRUMENT))
            self._cursor({"kind": "instrument", "sid": sid})
            return
        self.skill_ready = t + 1.0
        self._sys(prompt)
        self._cursor({"kind": skill})

    def _song(self, kind: str):
        """A barding song: the bard skill at the ground (knowledge #11233/#11234: 15 min, 10 s between songs,
        independent of the skill timer; live 2026-10-13, three of three played and printed 15.0 %)."""
        t, b = self.t, self.sc["bard"]
        if t < self.song_ready:
            self._sys(TEXT_SONG_WAIT)
            return
        self.song_ready = t + b.get("song_gap_s", 10.0)
        self.songs[kind] = t + b.get("song_s", 900.0)
        self._ev("song", kind=kind, until=round(self.songs[kind], 2))
        self.count("songs", kind)
        x = round(100 * b.get("song_x", 0.15), 1)
        self._sys("You are now under the effect of a Song of " + {
            "discord": f"Discordance ({x}% reduced damage from creatures for 15 minutes).",
            "peace": f"Peacemaking ({x}% healing amounts for 15 minutes).",
            "provoke": f"Provocation (+{x}% damage against creatures for 15 minutes)."}[kind])

    def song(self, kind: str) -> float:
        """The song's bonus share while it runs, else 0."""
        return self.sc["bard"].get("song_x", 0.15) if self.songs.get(kind, 0.0) > self.t else 0.0

    def _target(self, p):
        from world.parsers import parse_packet
        f = parse_packet("c2s", p)
        pend, self.pending = self.pending, None
        if pend is None or f.get("cursor_id") != pend["cid"]:
            return
        if pend["kind"] == "patrol":
            # live: a tile out of our sight is "Target cannot be seen." and nothing happens (3 of 8)
            x, y = f.get("x"), f.get("y")
            if f.get("target_type") != 1 or x is None or x == 0x7FFFFFFF:
                return
            pl = self.player
            if self.sight.point_blocked((pl.x, pl.y, pl.z + los.EYE), (x, y, self.stand_z(x, y, pl.z))) is not None:
                self.send(P.cliloc(500237))
                return
            for m in self.pets():
                if m.alive and not m.ghost:
                    m.mode, m.kill, m.patrol = "patrol", None, (x, y)
            return
        if pend["kind"] == "rope":
            self._rope_target(pend, f)
            return
        if pend["kind"] == "instrument":
            it = self.items.get(f.get("serial") or 0)
            if it is None or it.get("graphic") not in tamer.INSTRUMENT_GRAPHICS:
                self.send(P.cliloc(500619))            # "That is not a musical instrument."
                return
            self.played = f["serial"]
            self._skill(pend["sid"])
            return
        if f.get("target_type") == 1 and pend["kind"] in SONGS and f.get("x") != 0x7FFFFFFF:
            self._song(pend["kind"])
            return
        serial = f.get("serial") or 0
        if pend["kind"] == "peace" and serial == self.player.serial:
            self._area_peace()
            return
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
            if tgt.target in self.mobs and self.mobs[tgt.target].kind in OTHERS:
                self.count("kill_on_others")         # "All Kill" at a monster fighting another player
            for m in self.pets():
                if m.alive and not m.ghost:
                    m.mode, m.kill, m.target = "kill", serial, serial
            self._act(tgt, self.sc["bard"]["act_threat"])
            return
        if kind == "provoke" and "first" not in pend:
            self._sys(tamer.TEXT_INCITE_SECOND)
            self._cursor({"kind": "provoke", "first": serial})
            return
        self._bard(kind, tgt, self.mobs.get(pend.get("first")) if kind == "provoke" else None)

    def _act(self, m: Mob, amount: float, count=True):
        """An aggressive act of ours at `m` (a bard skill, "All Kill", a Barding Break): threat on us,
        but only while no pet has hurt it yet. The trip: of 14 monsters our act reached after the
        pets had damaged them none hit Logan; of 7 it reached first, 3 did (docs/NOTES.md)."""
        pets = {p.serial for p in self.pets()}
        if not any(s in pets for s in m.damage_by):
            m.threat[self.player.serial] = m.threat.get(self.player.serial, 0) + amount
        if count:
            self.metrics["aggressive_acts"] += 1

    def _area_peace(self):
        """Peacemaking targeted on ourselves (wiki Peacemaking "Area Peacemaking"): a check per non-tamed
        creature within 8 tiles, 2 s pacified each; the usual cooldowns (live 20261015_171928 17:22:01: the text
        below, then the calm wait)."""
        t, b, pl = self.t, self.sc["bard"], self.player
        self.skill_ready = t + tamer.SKILL_GAP_S
        hit = 0
        for m in self.mobs.values():
            if m.kind != "monster" or not m.alive or m.hidden_until or cheb(m.pos, pl.pos) > tamer.AREA_PEACE_R \
                    or m.immune_until > t:
                continue
            diff = m.spec.get("difficulty") or 100.0
            s = b["effective_skill"]
            if self.rng.random() < min(0.95, max(s / 100.0 - diff / 100.0, b["min_chance_factor"] * s / 100.0)):
                m.calm_until = max(m.calm_until, t + 2.0)
                hit += 1
        self.count("bard", "area_peace_ok" if hit else "area_peace_fail")
        self._ev("bard", skill="area_peace", v=None, ok=hit)
        if hit:
            self.calm_ready = t + tamer.CALM_SUCCESS_S
            self._sys(tamer.TEXT_AREA_PEACE_OK)
        else:
            self._sys("You fail to pacify your opponent. You estimate the task to be fairly reasonable.")

    def _bard(self, kind, tgt: Mob, first: Mob | None):
        t, b, pl = self.t, self.sc["bard"], self.player
        target = first if kind == "provoke" else tgt
        if target is None:
            return
        if kind != "discord" and (target.immune_until > t or (kind == "provoke" and tgt.immune_until > t)):
            self._sys(tamer.TEXT_IMMUNE)
            self.skill_ready = t + tamer.SKILL_GAP_S
            return
        self._act(target, b["act_threat"])
        diff = target.spec.get("difficulty") or 100.0
        s = b["effective_skill"] + (25 if kind == "discord" and (target.calm_until > t or target.provoke) else 0)
        p = min(0.95, max(s / 100.0 - diff / 100.0, b["min_chance_factor"] * s / 100.0))   # knowledge #8530/#5239
        seen = (b.get("observed") or {}).get(kind, {}).get(target.key)
        if seen:
            p = seen[0] / seen[1]          # the live rate at this type (scenario bard.observed_note)
        elif (shown := (b.get("guide") or {}).get(target.key)) is not None:
            p = shown                      # the game's Guide showed it to Logan (scenario bard.guide_note)
        self.skill_ready = t + tamer.SKILL_GAP_S
        word = {"discord": "discord your opponent", "peace": "pacify your opponent",
                "provoke": "incite anger amongst your opponents"}[kind]
        if diff >= b.get("beyond_difficulty", 1e9):
            self._sys(f"You fail to {word}. You estimate the task to be beyond your skill.")
            self.count("bard", f"{kind}_beyond")
            self._ev("bard", skill=kind, v=target.serial, ok=False)
            return
        if self.rng.random() >= p:
            self._sys(f"You fail to {word}. You estimate the task to be fairly reasonable.")
            self.count("bard", f"{kind}_fail")
            self._ev("bard", skill=kind, v=target.serial, ok=False)
            if kind == "provoke":
                target.target = pl.serial          # a failed incite turns it on the bard [INFERENCE]
            return
        self.count("bard", f"{kind}_ok")
        self._ev("bard", skill=kind, v=target.serial, ok=True)
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
            if it.get("others"):
                # another player's kill (blue): live "You may not loot this corpse." (docs/HUNT_LOOP.md "Loot rights")
                self.count("blue_corpse_opens")
                self._sys(TEXT_NOT_YOURS)
                return
            self._open(s, CORPSE_GUMP)
            return
        if kind == "bag":
            self._open(s, BAG_GUMP)
            return
        if pl.held_until > self.t:
            self._sys(HELD_NO_USE_TEXT)           # transfixed: potions and vet refused (live, 3 tries)
            return
        if kind == "gate":
            # live 20:58:44 (20261013_204518): double-clicked from a tile away, the menu at once
            if cheb((it["x"], it["y"]), pl.pos) > 1:
                self.send(P.cliloc(500446))
                return
            if self.t - self.last_use_t < ACTION_DELAY_S:
                # live 2026-10-19 10:19:32.55-32.82: the vet supplies, then the gate 0.2 s later: 500119, no menu
                self.send(P.cliloc(tamer.CLILOC_ACTION_WAIT))
                return
            self.last_use_t = self.t
            self._gate_menu(self.gates[(it["x"], it["y"])])
            return
        if self.t - self.last_use_t < ACTION_DELAY_S:
            self.send(P.cliloc(tamer.CLILOC_ACTION_WAIT))
            return
        self.last_use_t = self.t
        if kind == "vet":
            self._vet(s)
        elif kind == "heal_potion":
            self._drink(s)
        elif kind == "refresh_potion":
            self._refresh(s)
        elif kind == "soul_jar":
            self._fill_jar(s)
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

    def eerie_corpse(self, x, y, spirit="Karin Winckel", art=0xB202, z=None, until=None) -> int:
        """An eerie corpse on the ground at (x, y) (soul_jar.py: "dead" art, then its eerie whispers a few serials
        on, beside it), on the surface nearest `z` (else ours), gone at `until` (else never); the corpse's serial."""
        s = self._item_serial()
        z = self.stand_z(x, y, self.player.z if z is None else z)
        self.items[s] = {"kind": "eerie", "graphic": art, "x": x, "y": y, "z": z, "spirit": spirit, "until": until}
        self.next_item += 6                                          # the blood and bones pieces, not simulated
        w = self._item_serial()
        self.items[w] = {"kind": "eerie_fx", "graphic": soul_jar.WHISPERS_GRAPHIC, "x": x + 1, "y": y, "z": z,
                         "corpse": s}
        self._view()
        return s

    def _remove_eerie(self, corpse):
        """An eerie corpse and its pieces gone (its time up, or another player filled a jar at it)."""
        for c in [corpse, *(c for c, it in self.items.items() if it.get("corpse") == corpse)]:
            self.items.pop(c, None)
            if c in self.viewing_items:
                self.viewing_items.discard(c)
                self.send(P.delete(c))

    def _eerie_tick(self):
        """The Field of Souls' eerie corpses on the level (the scenario's `eerie`): one at a free spot of `spots` at
        `per_h` an hour, each gone after `lifetime_s`; another player within soul_jar.FILL_WALK_R of one fills his
        jar there [INFERENCE: players race for them, NOTES "Field of Souls"]."""
        ee, t = self.sc["eerie"], self.t
        for s, it in list(self.items.items()):
            if it.get("kind") != "eerie":
                continue
            if it.get("until") is not None and t >= it["until"] or any(
                    cheb((it["x"], it["y"]), o.pos) <= soul_jar.FILL_WALK_R for o in self.mobs.values()
                    if o.kind == "other"):
                self._remove_eerie(s)
        if t < self.next_eerie_t:
            return
        self.next_eerie_t = t + self.erng.expovariate(ee["per_h"] / 3600.0)
        taken = {(it["x"], it["y"]) for it in self.items.values() if it.get("kind") == "eerie"}
        free = [p for p in ee["spots"] if tuple(p[:2]) not in taken]
        if free:
            x, y, z = self.erng.choice(free)
            self.eerie_corpse(x, y, spirit=self.erng.choice(ee["spirits"]), z=z, until=t + self._eroll(ee["lifetime_s"]))
            self.count("eerie_spawned")

    def _eroll(self, r) -> float:
        return self.erng.triangular(r[0], r[1], r[2]) if len(r) >= 3 else self.erng.uniform(r[0], r[1])

    def _fill_jar(self, s):
        """An empty soul jar double-clicked (live 20261017_151814 15:47:16.9, one flush): within
        soul_jar.FILL_RANGE of an eerie corpse the jar and the corpse go, "spirit energy" names the soul, the
        server's line, a filled jar (a new serial) in the pack. Out of range: no answer here (the live one unseen)."""
        pl = self.player
        corpse = next((c for c, it in self.items.items() if it.get("kind") == "eerie"
                       and cheb((it["x"], it["y"]), pl.pos) <= soul_jar.FILL_RANGE), None)
        if corpse is None:
            return
        spirit = self.items.pop(corpse)["spirit"]
        pieces = [c for c, it in self.items.items() if it.get("corpse") == corpse]
        for c in [corpse, *pieces]:
            self.items.pop(c, None)
            self.viewing_items.discard(c)
        self._consume(s)
        filled = self._item_serial()
        self.items[filled] = {"graphic": soul_jar.FILLED_JAR_GRAPHIC, "amount": 1, "container": self.backpack,
                              "kind": "filled_jar"}
        self.send(P.says(self._item_serial(), "spirit energy", f"'My name was {spirit}....'", hue=946, body=0xA428),
                  P.system_text(soul_jar.FILLED_TEXT), *(P.delete(c) for c in [*pieces, corpse]),
                  P.contained(filled, soul_jar.FILLED_JAR_GRAPHIC, 1, self.backpack))
        self.count("soul_jars")

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
            # live 2026-10-18 21:14:30.9 and 33.9, a channel running: this line, not 500119
            self._sys(tamer.TEXT_ITEM_WAIT)
            return
        rng = v["range"]
        need = [m for m in self.pets() if (m.ghost or (m.alive and (m.hits < m.hits_max or m.poison_until > self.t)))
                and cheb(m.pos, pl.pos) <= rng + 3]
        if not need and pl.hits >= pl.hits_max and pl.poison_until <= self.t:
            self._sys(tamer.TEXT_VET_NEEDLESS)
            return
        self._sys(tamer.TEXT_VET_BEGIN)
        self.metrics["vet_uses"] += 1
        self._consume(s)
        self.vet_end = self.t + self.rng.uniform(*v["channel_s"])
        self._ev("vet", phase="start", end=round(self.vet_end, 2))

    def _vet_finish(self):
        """A channel's end: heals every hurt pet within range of us then (and us), or 'too far' with none in range.
        Moving during the channel doesn't shorten the range: live, of 128 channels with steps of ours in them, 126
        finished and healed, 44 of those with the nearest pet 2-8 tiles off (2026-10-20, 35 visits); before, a step
        cut the range to 1 [INFERENCE of the first build] and the simulator's runner got 'too far' 30 times an hour
        (live 0.6). A pet in range that is whole again by the finish (a devour, its regeneration) gets the finish line
        and no heal: live 373 channels since 10-17 23:08, none 'too far'; the simulator said 'too far' for those (93
        of its 111 in 1,417 channels)."""
        pl, v = self.player, self.sc["vet"]
        self.vet_end = None
        self._ev("vet", phase="end", end=round(self.t, 2))
        rng = v["range"]
        ghosts = [m for m in self.pets() if m.ghost and cheb(m.pos, pl.pos) <= rng]
        near = [m for m in self.pets() if m.alive and not m.ghost and cheb(m.pos, pl.pos) <= rng]
        pets = [m for m in near if m.hits < m.hits_max or m.poison_until > self.t]
        hurt_self = pl.hits < pl.hits_max or pl.poison_until > self.t
        if not near and not hurt_self and not ghosts:
            self._sys(tamer.TEXT_VET_FAR)
            return
        for m in ghosts:
            if self.rng.random() < v["res_chance"]:
                # live 20261014_221341 22:35:55.1: the line, 0xBF sub 0x19 alive, 31 %, then this channel's heal
                m.ghost, m.mode, m.hits = False, "follow", m.hits_max * v["res_hits"]
                self._sys(tamer.TEXT_VET_RES)
                self.send(P.pet_dead(m.serial, False), P.hits(m.serial, m.pct(), 100))
                self.count("pet_res", m.name)
                pets.append(m)
        self._sys(tamer.TEXT_VET_FINISH)
        share = 1.0 if len(pets) <= 1 else v.get("multi_share", 0.67)
        for m in pets:
            m.poison_until = 0
            self.heal(m, self.roll(v["heal_pet"]) * share)
        if hurt_self:
            pl.poison_until = 0
            # flagged in PvP the supplies healed Logan 15-22 (median 18, n 8) against 5-39 (29, n 129) otherwise
            # (docs/NOTES.md "Red players met live"; no rule found for it)
            self.heal(pl, self.roll(v["heal_self_pvp"] if self.flagged() and v.get("heal_self_pvp") else v["heal_self"]))

    def _drink(self, s):
        pl, pot = self.player, self.sc["potions"]
        if self.t < self.potion_ready:
            wait = max(1, int(math.ceil(self.potion_ready - self.t)))
            self._overhead(pl, f"You must wait {wait} second{'s' if wait > 1 else ''} before using another "
                               f"healing potion.")
            self._ev("potion", kind="heal", ok=False, n=0)
            return
        if pl.hits >= pl.hits_max:
            self.send(P.cliloc(1049547))
            self._ev("potion", kind="heal", ok=False, n=0)
            return
        self.potion_ready = self.t + pot["cooldown_s"]
        self.metrics["potions"] += 1
        self._sys("You drink a healing potion.")
        # flagged in PvP a heal potion heals 15-25 (patch 2025-06-06, knowledge #4909); Logan's: 21-29, median 24 (n 10)
        n = int(round(self.roll(pot["heal_pvp"]))) if self.flagged() and pot.get("heal_pvp") else self.rng.randint(*pot["heal"])
        self._ev("potion", kind="heal", ok=True, n=n)
        self.send(P.cliloc(1008158, str(n).encode("utf-16-le")))
        self.heal(pl, n)
        if self.rng.random() < pot.get("satchel_save", 0.0):
            self._sys("Potion satchel used.")
        else:
            self._consume(s)

    def _refresh(self, s):
        """A Total Refresh: stamina to full (live 15-17 -> 25 at once). At full it isn't drunk [INFERENCE: ServUO's
        RefreshPotion, cliloc 500767 "You decide against drinking this potion, as you are already at full stamina."]."""
        pl = self.player
        if pl.stam >= pl.stam_max:
            self.send(P.cliloc(500767))
            self._ev("potion", kind="refresh", ok=False, n=0)
            return
        self.metrics["refresh_potions"] += 1
        pl.stam = pl.stam_max
        self.send(P.stam(pl.serial, pl.stam, pl.stam_max))
        self._consume(s)
        self._ev("potion", kind="refresh", ok=True, n=pl.stam_max)

    def _lift(self, s, amount):
        it = self.items.get(s)
        corpse = self.items.get(it.get("container")) if it else None
        if it is None or corpse is None or corpse.get("kind") != "corpse" \
                or cheb((corpse["x"], corpse["y"]), self.player.pos) > 2 or corpse.get("others"):
            self.send(b"\x27\x05")
            return
        if self.t - self.lift_ok_t < LIFT_GAP_S:
            # live (the store, 144 times): the text, then 0x27 05, 0.04-0.05 s after a lift within ~0.5 s of the last taken
            self.metrics["lift_waits"] += 1
            self._sys(combat.TEXT_LIFT_WAIT)
            self.send(b"\x27\x05")
            return
        self.lift_ok_t = self.t
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
        if self.gate_gump is not None and f.get("serial") == self.gate_gump[0]:
            self._gate_reply(f.get("button_id", 0))
            return
        if f.get("serial") != self.gump_n:
            return
        row = f.get("button_id", 0) - TOME_ROW_BUTTON0
        rows = self.sc["recall"]["rows"]
        if not 0 <= row < len(rows) or self.recall_at is not None:
            return
        if self.t < self.cast_recover_until:
            self.send(P.cliloc(502644))     # "You have not yet recovered from casting a spell." (SPELL_INTERRUPTS.md)
            return
        self._overhead(self.player, "Kal Ort Por", kind=10)
        if not self.recall_allowed(self.player.pos):
            self.send(P.cliloc(501802))     # [INFERENCE] the live refusal text in a dungeon is unrecorded
            return
        self.recall_at = (self.t + RECALL_S, rows[row])
        self.recall_start_t = self.t
        self._ev("recall", phase="cast", dest=rows[row].get("name"))

    def recall_allowed(self, pos) -> bool:
        """Outside the dungeon, or within `recall_r` of one of its gold moongates (user 2026-10-13: a
        dungeon allows recall only near a gold gate, never near a red one; the radius 2 is his
        conservative guess)."""
        d = self.sc.get("dungeon")
        if not d:
            return True
        x0, y0, x1, y1 = d["box"]
        if not (x0 <= pos[0] <= x1 and y0 <= pos[1] <= y1):
            return True
        return any(cheb(pos, g) <= d.get("recall_r", 2) for g in d.get("gold_gates", []))

    def _move_party(self, to, follow_r: int | None = None):
        """Us to `to`, and our live pets within pet_range of us with us (a recall, a moongate, a
        teleporter: ServUO TeleportPets [INFERENCE: the range]); `follow_r` (a teleporter) also those on Follow
        this close (user 2026-10-20: on Outlands pets on "All Follow Me" come through a teleporter with Logan
        while in visual range). They default to Follow: a transition stops guarding (wiki, #11539/#11561). What
        fought us there stays behind."""
        pl = self.player
        r = self.sc["recall"].get("pet_range", 3)
        came = [m for m in self.pets() if cheb(m.pos, pl.pos) <= r
                or (follow_r is not None and m.mode == "follow" and cheb(m.pos, pl.pos) <= follow_r)]
        self._teleport(pl, to)
        self._place_beside(came)
        for m in came:
            m.target, m.kill, m.mode = None, None, "follow"
        for o in self.mobs.values():
            if o.kind == "monster" and o.target in {pl.serial} | {m.serial for m in came}:
                o.target, o.threat = None, {}
        self._view()
        for m in came:
            if m.serial in self.viewing:
                self._show(m)

    def _place_beside(self, pets):
        """Pets on tiles beside us that we could step onto and they could step back from."""
        pl = self.player

        def both_ways(t):
            back = t and self.walk.can_walk(t[0], t[1], t[2], nav.direction(t[:2], pl.pos[:2]))
            return t if back and back[:2] == pl.pos[:2] and not self._occupied(t[0], t[1]) else None
        spots = [t for d in (2, 4, 6, 0, 3, 5, 7, 1)
                 if (t := both_ways(self.walk.can_walk(pl.x, pl.y, pl.z, d)))] or [pl.pos]
        for i, m in enumerate(pets):
            m.x, m.y, m.z = spots[i % len(spots)]
            m.path = []

    # Adventurer's Rope ("[Rope"; knowledge #13325: a Teleport, 5 s cooldown; followers on Follow within
    # 12 come along, patch 2025-01-25 #5141). Live: jumps of 7, 9 and 10 tiles, an 11-tile target refused
    # (20261013_173449, 20261014_170144, _173858).
    def _rope(self):
        rope = next((s for s, it in self.items.items() if it.get("kind") == "rope" and it.get("amount", 0) > 0), None)
        if rope is None or self.t < self.rope_ready:
            return                                       # [INFERENCE] the server's answers then weren't captured
        if self.t < self.rope_combat_until:
            left = int(self.rope_combat_until - self.t + 0.999)
            m, s = divmod(left, 60)
            self._sys(f"You must wait another {f'{m}m' if m else ''}{' ' if m and s else ''}{f'{s}s' if s else ''} "
                      f"before you may use this again while in combat.")
            return
        if self.flagged():
            self.rope_combat_until = self.t + ROPE_COMBAT_COOLDOWN_S
        self._sys(TEXT_ROPE_PROMPT)
        self._cursor({"kind": "rope", "item": rope})

    def _rope_target(self, pend, f):
        pl = self.player
        x, y = f.get("x"), f.get("y")
        self.rope_ready = self.t + ROPE_COOLDOWN_S
        if x is None or x == 0x7FFFFFFF:
            return
        z = self.stand_z(x, y, f.get("z") or pl.z)
        # live 2026-10-19 10:29:07.6 and 20.6: "That location is blocked." (501942) and "Target cannot be seen."
        # (500237), each followed by a denied step; sight as for a ground target, our eye to the tile's ground
        if cheb(pl.pos, (x, y)) > ROPE_R:
            self.send(P.cliloc(500446))
            return
        if not self.walk.objects(x, y) or self._occupied(x, y):
            self.send(P.cliloc(501942))
            return
        if self.sight.point_blocked((pl.x, pl.y, pl.z + los.EYE), (x, y, z)) is not None:
            self.send(P.cliloc(500237))
            return
        it = self.items[pend["item"]]
        it["amount"] = it.get("amount", 1) - 1
        self.count("ropes")
        self.rope_landing = (self.t + ROPE_LAND_S, (x, y, z))

    def _rope_land(self, to):
        """The rope's jump lands (ROPE_LAND_S after the target): us there, our followers on Follow beside us (none
        while we are flagged in PvP: knowledge #5163)."""
        pl = self.player
        if pl.ghost:
            return
        came = [] if self.flagged() else [m for m in self.pets() if m.alive and m.mode == "follow"
                                           and cheb(m.pos, pl.pos) <= 12]
        self._teleport(pl, to)
        self._place_beside(came)
        self._view()
        for m in came:
            if m.serial in self.viewing:
                self._show(m)

    def _land(self, dest):
        self.metrics["recalls"] += 1
        self._move_party(dest["at"])
        self._apply_penalty()
        self._ev("recall", phase="land", dest=dest.get("name"))
        if dest is self.sc["recall"]["rows"][0] and not self.player.ghost:
            # home: what we carried is banked (live the drop-off takes it; run.py adds --no-drop-off, the simulator
            # has no house), so a later death loses only what came after (docs/SIM.md "Red players": Gold banked)
            self.metrics["gold_banked"] = self.metrics["gold_looted"]

    # moongates: the "Moongate Destinations" menu (docs/NOTES.md "Moongate gumps"; live 21:31:00)
    def _gate_layout(self, gate, picked=None):
        rows = gate["destinations"]
        parts = ["{ resizepic 20 23 11571 236 560 }{ gumppic 48 18 2446 }{ text 75 19 2655 0 18 0 1 0 0 0 }",
                 "{ button 53 546 4005 4007 1 0 2 }{ text 95 547 63 1 18 0 1 0 0 0 }"]
        for i in range(len(rows)):
            y = 52 + 35 * i
            art = "9724 9721" if i == picked else "9721 9724"   # live: the selected row's radio art swapped
            parts.append(f"{{ button 39 {y} {art} 1 0 {10 + i} }}{{ text 75 {y + 3} 2603 {2 + i} 18 0 1 0 0 0 }}")
        return "".join(parts), ["Moongate Destinations", "Travel to Destination"] + [r["name"] for r in rows]

    def _gate_menu(self, gate, picked=None):
        """The menu; it opens with our last destination selected (live, both captures)."""
        if picked is None:
            picked = next((i for i, r in enumerate(gate["destinations"]) if r["name"] == self.gate_last), None)
        self.gate_n += 1
        layout, lines = self._gate_layout(gate, picked)
        self.gate_gump = (self.gate_n, gate, picked)
        self.send(P.gump(self.gate_n, GATE_GUMP, layout, lines))

    def _gate_reply(self, button: int):
        _, gate, picked = self.gate_gump
        self.gate_gump = None
        rows = gate["destinations"]
        if 10 <= button < 10 + len(rows):
            self._gate_menu(gate, button - 10)          # live: the menu comes back with the pick
        elif button == 2 and picked is not None and cheb(self.player.pos, tuple(gate["at"][:2])) <= 1:
            to = rows[picked].get("at")
            if to is not None:
                self.metrics["gate_travels"] = self.metrics.get("gate_travels", 0) + 1
                self.gate_last = rows[picked]["name"]
                self._move_party(to)
                self._apply_penalty()                   # live: at once after a moongate (stationary.py)

    def _apply_penalty(self):
        self.penalty = PENALTY_STEPS
        self.send(P.penalty_buff(self.player.serial, PENALTY_STEPS))
        self._sys("A stationary penalty is in effect (must move 5 tiles to remove).")

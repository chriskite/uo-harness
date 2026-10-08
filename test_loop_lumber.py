"""Offline end-to-end test of the lumber loop runner (harness/loop_lumber.py).

A simulated server behind the real proxy, with the packet shapes and texts of the
demonstration capture (logs/session_20260929_204225) and of the live rental room
(docs/NOTES.md "Rental room via the DTF house steward", 2026-10-04):

- home (docs/LUMBER_LOOP.md §12.5): the character (CHAR_NAME, a test homes file) starts in its
  rental room (facet 3, ROOM_ARRIVAL) with the secure chest 1 tile off and a door whose menu (the
  live gump) "Exit to House Steward" puts it on the home landing (facet 0); there the house
  steward (click label; "room" said within 2 tiles of him) opens the live room menus: "Visit Other
  Rooms", then the owner's row ("Logan Wolf (DTF)") back into the room; drops into the open chest
  are counted (boards merge like RunUO)
- our runebook (the captured layout, two runes): "Sim Woods" (BOOK_RUNE_POS, by the trees of the
  main run) and the default "Home" (the home landing); the home rune library (one tome at TOME_POS,
  rune 286 by the library trees)
- hatchet dclick → cliloc 1010018 + cursor; the runner answers it with itself (Smart Harvest; the
  stock client's bytes, capture 20261001_214649) and the server chops the nearest tree with wood
  within SIM_RANGE, else says "You do not see any harvestable resources nearby." + "You cannot
  produce any wood from that." over our head (the runner moves to the next stand); a decoy
  "Captcha" gump (no buttons) on every attempt and a real captcha (gump id 1, entry 2, Guide
  button 1 + a submit button whose id isn't the demo's) on the first two: the first a captured
  solver-readable layout (the runner answers it itself) at the dry tree, whose answer is the
  'nothing nearby', the second unreadable (pause + beep fallback), then fail/success results;
  the dry tree never has wood, the good one runs out after GOOD_VISIT attempts per visit
- log stack target → "You shape the logs into boards." (1:1)
- a closed town door between the trees and the home landing that opens on the stock open-door
  request and swings shut once the agent is past it
- a moongate on each tile west of that door, so every walk between the trees and home steps on
  one: like Shelter's player-cast gates (session 20261001_191355) it opens the renounce-Young
  prompt before the step's confirm and never closes it; the agent must close it (button 0)

The main run: two trips, each out of the room, by the runebook's "Sim Woods" rune to the trees,
home on foot (the trees lie within home.NEAR_LANDING of the landing) through the door, into the
room, convert, store in the chest. The "human" answers the unreadable (fallback) captcha through
the client connection. The steward comes into view within 18 tiles and leaves it beyond 24.

More runs on the same simulator (LUMBER_LOOP.md §13), each with its own proxy:
- skirmish: the hatchet in a bag in the pack; 'a great hart' in war mode 4 tiles from the tree
  fighting a player (0x2F both ways) is no threat (passive body); a creature that swings at the agent makes
  it escape and harvest the next tree out of reach; the same creature then hunts it down there
  (escape, kept coming: stop at once, the logs stay logs)
- break: the agent gate (pre-written budget file) announces a break mid-harvest; the trip ends
  in the room with the carried and new logs stored as boards, exit 0
- library: out by the home library's tome (the landing nearest the grove), home with our runebook's
  default rune, into the room, store; twice
- tracking reds: the library trip with the Tracking gump, buff and arrows as captured; Hunting
  murderers before going out, back on after the recall out stops it, a far red logged, a near one
  recalled from
- gazer_run / gazer_rehit (LUMBER_LOOP.md §13 "Running from a creature"): at the library spot a gazer
  casts from 10 tiles; the runner walks out of its 12-tile reach and chops on (stores), or, when it
  outranges the walk-away and hits again, recalls home without converting
- gazer_reflect: the same gazer's first spell is taken by Magic Reflection (the server's "Magic reflect
  removed." and the 0xC0 0x37B9 on us, no hits lost; live 2026-10-03 witcher_280): the runner runs at
  that spell, before any damage, and stores
- wary: a war-mode creature by the nearest tree: the farther tree first, the near one once it has gone
- red_aim (§13 "Blind waits"): at the library spot a red comes into view while the chop's cursor is up
  (--human normal): the cursor is cancelled and the recall home pressed within REACT_MAX_S of sight, no chop target
- flee_aid (§13 "Healing on the run"): at the library spot a red's spells leave us at 55 hits, poisoned and
  paralyzed (steps refused): a trapped pouch pops the paralysis, a cure, then a heal potion on the run; the book's
  double-click never within 0.5 s of them; no potion read as stolen
- staff_in_view (docs/PLAN.md "Staff alarm on an invulnerable player in view"): a vendor (notoriety 7, no
  player flag) next to us mid-harvest raises nothing; an invulnerable player (notoriety 7 + 0x20) coming into
  view raises one gm_suspected + staff alarm, a staff_sighting with its gear, and holds the job until acked
Plus unit checks of hatchet() (worn, else the shallowest in the pack's bags) and hit_verdict(), and
unit_capture_*: the runner's threat and trip-row pieces on packets captured live in session
20261003_213125 (the witcher_280 gazer larva, juncture 222, trip 1's hatchet, buffs and the named players),
and unit_capture_smart_harvest: the runner's self-target 0x6C byte-equal to the stock client's in session
20261001_214649 (23:20 and 23:47), and its 'nothing nearby' answer mapped by outcome().
Named scenarios run alone: `python test_loop_lumber.py gazer_run wary`.

Run: python test_loop_lumber.py   (~2 min; private ports; safe while the live proxy runs). Two or
more scenarios run as parallel child processes (`python test_loop_lumber.py <name>` each, at most
LOOP_TEST_JOBS = 8 at a time; LOOP_TEST_JOBS=1 runs them in this process one after another): each
already has its own ports and temp dir. Serially the suite took ~10 min (2026-10-04); main (two
trips, the human profile, the town wall learned from walk denials) is the critical path at ~2 min.
"""
import asyncio
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
sys.path.insert(0, f"{ROOT}/harness")

import actions  # noqa: E402
import escape  # noqa: E402
import memory  # noqa: E402
import nav  # noqa: E402
import stockpile as stockpile_mod  # noqa: E402
from uo.packets import packet_length, C2S_OVERRIDES  # noqa: E402
from uo.s2c import encode_packet  # noqa: E402
from world.parsers import parse_packet  # noqa: E402
import viz_feed  # noqa: E402

PROXY_PORT, UPSTREAM_PORT, CONTROL_PORT, STATE_PORT = 12670, 12671, 12672, 12673
LOGDIR = f"{ROOT}/logs_test_loop"
C2S_KEY, S2C_KEY = 0x0F, 0x5A
PRELUDE = bytes([0xFF, 0x00, 0x0D] + [0] * 7 + [0x0C, S2C_KEY, C2S_KEY])
SELF, BACKPACK, HATCHET = 0x00094375, 0x44ADA059, 0x44ADB57A
START = (100, 200)                                       # the main run's trees around it; BOOK_RUNE_POS lands here
GOOD_TREE = {"x": 111, "y": 200, "z": 0, "graphic": "0x0CE0", "stand": [110, 200]}
DRY_TREE = {"x": 105, "y": 194, "z": 0, "graphic": "0x0CE0", "stand": [105, 195]}
# home (docs/LUMBER_LOOP.md §12.5; harness/home.py): the landing past the town door, the house steward by it,
# the rental room on facet 3 (live values: Outland Dan's DTF guild house and Logan Wolf's room, 2026-10-04)
CHAR_NAME = "Testwood"                                   # the test homes file's key (the world model's self name)
HOME_RUNE_POS = (125, 205)                                # our runebook's default rune "Home": the home landing
STEWARD, STEWARD_POS = 0x009F57FB, (129, 205)             # Chase the house steward (live serial), 4 tiles from it
STEWARD_LABEL = "Chase the house steward"
ROOM_FACET, ROOM_ARRIVAL = 3, (403, 923)
ROOM_DOOR, ROOM_DOOR_POS = 0x5CDC6B4F, (403, 929)          # the room's wooden door (live serial and tile)
CHEST, CHEST_POS = 0x4AE0DD2C, (404, 922)                 # the secure paragon chest, 1 tile from the arrival
BOOK_RUNE_POS = START                                     # our runebook's rune "Sim Woods"
with open(f"{ROOT}/harness/testdata/room_gumps.json", encoding="utf-8") as _f:
    ROOM_GUMPS = json.load(_f)                             # the live room menus (steward, visit list, door)
# storage shelves (docs/NOTES.md "Storage shelves", live 2026-10-04/05): the room's, and two on one tile by the
# home landing as in the DTF guild house, the first secured against us; the loadout wants 3 trapped pouches
ROOM_SHELF, ROOM_SHELF_POS = 0x6CEB65CD, (402, 921)
SECURED_SHELF, LANDING_SHELF, LANDING_SHELF_POS = 0x40050A3B, 0x40B84C55, (124, 203)
SHELF_POUCHES = (0x44ADD101, 0x44ADD102, 0x44ADD103)      # what a shelf hands out, in this order
LOADOUT_POUCHES = 3
# the room's Resource Stockpile (live 2026-10-05: Logan Wolf's room, 2 tiles north of the arrival): its menu, the
# Add Items button (2) with the server's prompt and a cursor; a targeted stack "You add 1 item(s) ..."
STOCKPILE, STOCKPILE_POS, STOCKPILE_GUMP = 0x62645C82, (403, 921), 0x6ECE2ABE
STOCKPILE_LAYOUT = ("{ resizepic 18 25 11571 666 535 }{ text 305 16 2655 1 }{ button 93 53 2118 2118 1 0 100 }"
                    "{ button 96 508 2151 2154 1 0 2 }{ button 589 516 2118 2117 1 0 12 }{ text 610 513 149 3 }")
with open(f"{ROOT}/harness/testdata/shelf_gumps.json", encoding="utf-8") as _f:
    SHELF_GUMP = json.load(_f)["dtf"]                     # the live Storage Shelf gump (DTF guild house)
DOOR = (122, 200)                                        # a closed town door
WALLS = {(122, y) for y in range(180, 236)} - {DOOR}     # long enough that going round costs more than the door
GATES = {(121, y) for y in (199, 200, 201)}              # every route through the door crosses one
RENOUNCE_ID = 0xE2544541                                 # the renounce-Young prompt (20261001_191355)
RENOUNCE_LAYOUT = ("{ resizepic 28 23 11571 401 501 }{ button 22 24 2094 2095 1 0 1 }"
                   "{ text 64 45 2655 0 18 0 1 0 0 0 }{ button 60 460 247 248 1 0 2 }"
                   "{ button 300 460 241 242 1 0 3 }")
LOGS_PER_SUCCESS = 3
STOLEN = 2                                               # a pickpocket's take, once (the loop must carry on)
# trapped pouches (docs/PLAN.md "Keep thieves off the logs"): hue-38 pouches in the pack, as Errol sells them
POUCHES = (0x44ADD001, 0x44ADD002, 0x44ADD003)
LOG_G, BOARD_G, POUCH_G = 0x1BDD, 0x1BD7, 0x0E79
THIEF, THIEF_NAME = 0x0073C056, "Caputo Wood"            # a blue who walks up to steal (live 2026-10-03)
PASSERBY = 0x0000ABCD                                    # a player who walks up and says hello mid-harvest
GREETING = "hail! good trees here?"
HOLD_S = 3.0                                             # the test overseer's all-clear comes this long after
HIDDEN = 0x0000BEEF                                      # speaks during the hold, never on screen (hidden GM?)
GM_EXTRA_S = 2.0                                         # the GM suspicion is acked this long after the all-clear
GM_SEEN, VENDOR_NEAR = 0x0000D00D, 0x00000B8E             # staff_in_view: notoriety 7 with / without the player flag
GM_ROBE = 0x40000D01                                     # staff_in_view: what the invulnerable player wears
GOOD_VISIT = 6                                           # attempts before the good tree runs dry
SIM_RANGE = 2                                            # the simulated server's Smart Harvest reach
NOTHING_NEAR = ("You do not see any harvestable resources nearby.", "You cannot produce any wood from that.")
DD = nav.DIR_DELTA
FAILURES = []
# skirmish scenario (LUMBER_LOOP.md §13: monsters fighting others, escapes, convert on abort)
BAG = 0x44ADC0DE                                         # the hatchet sits in this bag inside the backpack
# skirmish: the attacker 3 tiles east of the good tree's stand; escapes run ESCAPE_RUN (20) tiles, so west (the
# town wall lies east); the far tree beyond the attacker's aggro zone (AGGRO_R) from where it stood
FAR_TREE = {"x": 80, "y": 214, "z": 0, "graphic": "0x0CE0", "stand": [80, 213]}
FIGHTER, OTHER, ATTACKER = 0x0000F161, 0x0000A0A0, 0x0000BA75
FIGHTER_POS, OTHER_POS = (113, 204), (114, 204)          # 4 tiles from the good tree's stand: in flee range (8)
ATTACKER_POS = (113, 200)                                 # 3 tiles east of the good tree's stand
# break scenario
INITIAL_LOGS = 5                                          # logs carried from an earlier trip
BREAK_AFTER_S = 10.0                                      # agent-active seconds left before the break is due
# library scenario (docs/research/WORLD_LOCATIONS.md): a Witcher-style spot reached from the home rune
# library's tome (the landing nearest the grove), home by our runebook's default rune
TOME, RUNEBOOK = 0x546ACD06, 0x44ADB00C
LIB_START, TOME_POS = (132, 212), (133, 212)              # the home library, 7 tiles from the landing: no door between
RUNE_POS = (40, 250)                                      # where the tome's rune "286" puts us
LIB_TREE = {"x": 40, "y": 253, "z": 0, "graphic": "0x0CE0", "stand": [40, 252]}
with open(f"{ROOT}/harness/testdata/escape_gumps.json", encoding="utf-8") as _f:
    _G = json.load(_f)
TOME_GUMP, BOOK_GUMP = _G["runetome_main_witcher_276"], _G["runebook_charges"]   # captured layouts
LOCKOUT_S = 2                                             # the travel lockout the simulated server reports
# tracking scenario (LUMBER_LOOP.md §13 "Tracking reds"): the Tracking gump as captured live (20261001_214649)
with open(f"{ROOT}/harness/testdata/tracking_gump.json", encoding="utf-8") as _f:
    TRACK_GUMP = json.load(_f)
TRACK_ID = 0xFE5C638B
TRACK_MODES = ("criminal players", "innocent players", "friendly players", "aggressive creatures",
               "passive creatures", "townsfolk", "all players", "all hostile players",
               "enemy players", "murderer players")
RED, RED_NAME = 0x0009E217, "Lord Red"                    # a murderer the hunt finds, never in view
RED_FAR, RED_NEAR = 100, 55                               # tiles from us at the two hits (react range 80; Bastet 10-03: 55)
# creature runs (LUMBER_LOOP.md §13 "Running from a creature"): a gazer (body 22, ranged) casts at us from 10
# tiles at the library spot; a war-mode creature stands by the nearest tree on Shelter
GAZER, GAZER_BODY, GAZER_DMG, GAZER_CAST_S = 0x0000CA5E, 22, 10, 2.5
# south of the gazer's zone (20 tiles from it) and > home.NEAR_LANDING (60) from the home landing: home is a recall
LIB_FAR_TREE = {"x": 46, "y": 272, "z": 0, "graphic": "0x0CE0", "stand": [46, 271]}
# landing_escape (live 2026-10-05 Wintertop): a war-mode creature 6 tiles south of the library rune's landing,
# in view the moment we land; the grove (radius 2) lies 12 south of the landing, so the walk-away north ends
# farther from it than the landing and beyond AT_GROVE: the old code then set out for the home library again
LANDER, LANDER_POS = 0x0000A1A1, (40, 256)
LANDER_SPOT = {"area": {"center": [40, 262], "radius": 2}, "pvp": True}
# ghost_horse (live 2026-10-05, docs/NOTES.md "Our mount"): Outland Dan's bonded horse died with him and its ghost
# followed him home; going into the rental room brought it back alive. Our pet's menu offers Release (cliloc 3006322)
HORSE, HORSE_BODY, MOUNT_ITEM = 0x0154FE11, 0xE4, 0x4816EF0E
PET_POPUP = (bytes.fromhex("bf0024001400020154fe1103") + bytes.fromhex("000f4a1700000000")      # 0 Animal Lore
             + bytes.fromhex("002ddf6a00010000") + bytes.fromhex("002ddf7200090000"))       # 1 Kill, 9 Release
WARY, WARY_POS = 0x0000BA76, (113, 201)                    # 2 tiles from the good tree, 13 from the start
WEST_TREE = {"x": 86, "y": 200, "z": 0, "graphic": "0x0CE0", "stand": [87, 200]}      # 13 steps west; good: 10
# red_aim (LUMBER_LOOP.md §13 "Blind waits"; live 2026-10-03, Bastet came into view during the chop's aim pause)
BASTET, RED_AIM_S, REACT_MAX_S = 0x0009BA57, 0.02, 0.5   # a red, in view this long after the chop's cursor
# (the aim is a script's ~0.1 s since 2026-10-04, humanize SCRIPT_MEDIAN; the live aim pause was 2.1 s)
# faction / precast (live 2026-10-06 witcher_66): a guildmate / a blue healing himself, then CALM_S later the foe
GUILDY, FOE, WAYPOST_MOB, CALM_S = 0x0050CE5C, 0x00447976, 0x000D8F4D, 2.0
# flee_aid (LUMBER_LOOP.md §13 "Healing on the run"): Outland Dan's potions in the pack (ctl status 2026-10-06), the red's
# hit (hits to PK_HITS, poisoned, paralyzed) PK_HURT_S after he comes into view; a heal potion gives HEAL_POT_HP
CURE_G, HEAL_G, REFRESH_G = 0x0F07, 0x0F0C, 0x0F0B
POTIONS = {0x44ADE001: CURE_G, 0x44ADE002: HEAL_G, 0x44ADE003: REFRESH_G}
PK_HURT_S, PK_HITS, HEAL_POT_HP = 0.3, 55, 30
# every tree of the simulated world, whichever the scenario's spot lists (they stand far apart)
SIM_TREES = [(t["x"], t["y"]) for t in (GOOD_TREE, DRY_TREE, FAR_TREE, LIB_TREE, LIB_FAR_TREE, WEST_TREE)]


def check(name, cond, extra=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAILURES.append(name)


def u16(v):
    return (v & 0xFFFF).to_bytes(2, "big")


def u32(v):
    return (v & 0xFFFFFFFF).to_bytes(4, "big")


def var(pid, body):
    return bytes([pid]) + u16(3 + len(body)) + body


def login_pkt(pos):
    body = u32(SELF) + bytes(4) + u32(0x190) + u32(pos[0]) + u32(pos[1]) + u32(0) + bytes([0x80])
    return b"\x1b" + body + bytes(42 - len(body))


def map_change(facet):
    """0xBF sub 8 as live (bf0006000800 on the room exit, 20261004_162411)."""
    return bytes.fromhex("bf00060008") + bytes([facet])


def status_pkt(name):
    """0x11 MobileStatus for us, the captured one (Hackworth, 20261003_213125) with our serial and name:
    the world model's self name keys the home (harness/home.py)."""
    pkt = bytearray(bytes.fromhex(next(h for _, h in CAP_WITCHER if h.startswith("11"))))
    pkt[3:7] = u32(SELF)
    pkt[7:37] = name.encode().ljust(30, b"\x00")
    return bytes(pkt)


def label_pkt(serial, name, text):
    """0x1C type 6: the server's answer to a single click (the click label), as live (Chase, 162411)."""
    return var(0x1C, u32(serial) + u16(0x190) + b"\x06" + u16(0x35) + u16(3)
               + name.encode().ljust(30, b"\x00") + text.encode() + b"\x00")


def book_gump():
    """Our runebook: the captured layout and texts with two runes, entry 0 'Sim Woods' (BOOK_RUNE_POS) and
    the default entry 1 'Home' (the home landing), their sextant lines as the server prints them."""
    lines = list(BOOK_GUMP["lines"])
    lines[1], lines[4] = "10", "Sim Woods"
    lines[6:10] = [*escape.tile_to_sextant(*BOOK_RUNE_POS), *escape.tile_to_sextant(*HOME_RUNE_POS)]
    lines.append("Home")
    return BOOK_GUMP["layout"].replace("{ croppedtext 305 60 115 17 81 4 }", "{ croppedtext 305 60 115 17 81 10 }"), lines


def seed_pkt(token):
    return bytes.fromhex("bf001d0001") + u32(token) + bytes(20)


def self_at(x, y, d, flags=0x20):   # 0x20 V10 for the player (teleport anchor), facing d like a real server's
    return b"\x20" + u32(SELF) + u32(0x190) + b"\x01\x83\xea" + bytes([flags]) + u32(x) + u32(y) + b"\x00\x00" \
        + bytes([d]) + u32(0)


def poison_pkt(on):
    """0x17 health-bar flag for us, type 1 (poisoned), as Outlands sends it (session_20261003_170434:
    17000c0020f1270001000105 / ...0100)."""
    return b"\x17\x00\x0c" + u32(SELF) + b"\x00\x01\x00\x01" + bytes([5 if on else 0])


def mobile_pkt(serial, x, y):
    return b"\x20" + u32(serial) + u32(0x191) + b"\x07\x03\xf1\x02" + u32(x) + u32(y) + b"\x00\x00\x03" + u32(0)


def creature_pkt(serial, body, x, y, noto=3, flags=0x40):
    """0x20 MobileUpdate for a creature (Outlands layout, world/layouts.py): war mode by default."""
    return (b"\x20" + u32(serial) + u32(body) + bytes([noto]) + u16(0) + bytes([flags])
            + u32(x) + u32(y) + b"\x00\x00\x00" + u32(0))


def swing(attacker, defender):
    return b"\x2f\x00" + u32(attacker) + u32(defender)


def hits_pkt(hits, hits_max=100):
    """0xA1 UpdateHitpoints for us: Outlands shows damage only as a hits drop (docs/NOTES.md)."""
    return b"\xa1" + u32(SELF) + u16(hits_max) + u16(hits)


def effect_on_self(graphic, x, y):
    """0xC0 HuedEffect, Outlands 52-byte form (world/layouts.py), fixed on us (type 3) at (x, y):
    Magic Reflection taking a spell is 0x37B9 (live 2026-10-03 22:17:32)."""
    at = u32(x) + u32(y) + u32(0)
    return (b"\xc0\x03" + u32(SELF) + u32(SELF) + u32(graphic) + at + at + b"\x0a\x05" + u16(0) + b"\x01\x00"
            + u32(0) + u32(0))


def equip(item, graphic, layer, parent=SELF, hue=0):
    return b"\x2e" + u32(item) + u32(graphic) + u32(0) + bytes([layer]) + u32(parent) + u16(hue)


def contained(serial, graphic, amount, container, x=50, y=60, hue=0):
    return b"\x25" + u32(serial) + u32(graphic) + b"\x00" + u16(amount) + u16(x) + u16(y) + b"\x00" \
        + u32(container) + u16(hue) + u32(0)


def pop_flush(x, y, pouch, hits=None, left=None):
    """A trapped pouch in our pack going off, as captured (20261004_113229 2:31): the sound on our tile,
    the five 0x36BD location explosions around it (RunUO MagicTrap), the pouch re-sent with hue 0; the
    owner's pop also takes a hit ("-1" over us, 0xA1) and says how many trapped pouches are left."""
    def boom(dx, dy, dz=0):
        at = u32(x + dx) + u32(y + dy) + u32(50 + dz)
        return b"\xc0\x02" + bytes(8) + u32(0x36BD) + at + at + b"\x0a\x0f\x00\x00\x01\x00" + bytes(8)
    pk = [b"\x54\x01\x03\x07\x00\x00" + u32(x) + u32(y) + u32(50)]
    pk += [boom(-1, 0), boom(1, 0), boom(0, -1), boom(0, 1), boom(1, 1, 11)]
    if hits is not None:
        pk = [player_says(SELF, "Hackworth", "-1"), hits_pkt(hits)] + pk
    if left is not None:
        pk.append(sys_text(f"You now have {left} trapped pouches remaining."))
    pk.append(contained(pouch, POUCH_G, 1, BACKPACK, x=40 + 10 * (POUCHES + SHELF_POUCHES).index(pouch), hue=0))
    return pk


def ground_item(serial, graphic, x, y, z):
    return b"\xf3\x00\x01\x00" + u32(serial) + u32(graphic) + b"\x00" + u16(1) + b"\x00\x00" \
        + u32(x) + u32(y) + u32(z) + b"\x00" + u16(0) + u32(0) + u16(0)


def delete(serial):
    return b"\x1d" + u32(serial)


def sys_text(text):
    return var(0x1C, u32(0xFFFFFFFF) + b"\xff\xff\x00" + u16(0x3B2) + u16(3)
               + b"System".ljust(30, b"\x00") + text.encode() + b"\x00")


def player_update(serial, x, y, noto=1):
    """0x20 MobileUpdate (Outlands layout, as captured in 123206): a human with the player
    flag 0x20, notoriety 1 (a blue player; 6: a red)."""
    return (b"\x20" + u32(serial) + u32(0x190) + bytes([noto]) + u16(0x83EA) + b"\x20" + u32(x) + u32(y)
            + b"\x00\x00\x02" + u32(0))


def player_says(serial, name, text, hue=0x3B2, kind=0):
    """0x1C speech from `serial`: type 0 (said; also the title lines under a click label) or 10 (spell words)."""
    return var(0x1C, u32(serial) + u16(0x190) + bytes([kind]) + u16(hue) + u16(3)
               + name.encode().ljust(30, b"\x00") + text.encode() + b"\x00")



def cliloc(number):
    return var(0xC1, u32(0xFFFFFFFF) + b"\xff\xff\x00" + u16(0x3B2) + u16(3) + u32(number)
               + b"System".ljust(30, b"\x00") + b"\x00\x00")


def cursor(cid, target_type=1):
    return b"\x6c" + bytes([target_type]) + u32(cid) + b"\x00" + bytes(20)


def gump(serial, gump_id, layout, lines=()):
    body = u32(serial) + u32(gump_id) + u32(50) + u32(50) + u16(len(layout)) + layout.encode()
    body += u16(len(lines)) + b"".join(u16(len(t.encode("utf-16-be"))) + t.encode("utf-16-be") for t in lines)
    return var(0xB0, body)


def buttons(ids):
    return "".join(f"{{ button 10 {20 * i} 2094 2095 1 0 {b} }}" for i, b in enumerate(ids))


def hunting_buff(on):
    """0xFF sub 8 / 9: the Tracking Hunting buff (icon 173) on / off self, as captured live
    (20261001_214649 at 21:47:30 / 21:48:11, our serial swapped in)."""
    if on:
        return bytes.fromhex("ff003900000008") + u32(SELF) + bytes.fromhex(
            "00ad11ac00020000000000010000000000000000000000000000000cadf5e333000010eff4000000000000000000")
    return bytes.fromhex("ff000d00000009") + u32(SELF) + bytes.fromhex("00ad")


def arrow_set(arrow_id, serial, x, y, z, text):
    """0xFF sub 0x1A mode 0, the hunt's arrow (live: `ff 0034 0000001a 00 0000 00 03 0000 <serial> <x> <y>
    <z> "[Hunting] <name>"`)."""
    body = u32(0x1A) + b"\x00" + u16(arrow_id) + b"\x00\x03" + u16(0) + u32(serial) + u32(x) + u32(y) \
        + u32(z) + text.encode() + b"\x00"
    return b"\xff" + u16(3 + len(body)) + body


def arrow_cancel(arrow_id):
    return bytes.fromhex("ff000a0000001a01") + u16(arrow_id)


def skills_pkt(tracking):
    """0x3A full skill list (type 0, ids 1-based, 0-terminated) with Tracking (38) at `tracking` tenths:
    0 for a character without the skill (the runner then never tries it)."""
    return var(0x3A, b"\x00" + u16(38 + 1) + u16(tracking) + u16(tracking) + b"\x00" + u16(0))


CAPTCHA_SUBMIT = 843        # random per captcha on the server (demo 594, live 843); never 594 here
# a real captured captcha layout (session 20260929_204225, accepted answer "326"),
# with the demo's submit button id swapped for CAPTCHA_SUBMIT
_cap_sample = next(s for s in json.load(open(os.path.join(ROOT, "harness", "data", "captcha_samples.json")))
                   if s["tag"] == "20260929_204225")
REAL_CAPTCHA_LAYOUT = _cap_sample["layout"].replace("{ button 222 248 247 249 1 0 594 }",
                                                    f"{{ button 222 248 247 249 1 0 {CAPTCHA_SUBMIT} }}")
REAL_CAPTCHA_ANSWER = "326"
# captcha-shaped but unreadable (no digit dots): the solver refuses it and the
# runner falls back to pause + beep
UNREADABLE_CAPTCHA_LAYOUT = ("{ resizepic 27 25 11571 391 278 }{ button 21 19 2094 2095 1 0 1 }"
                             "{ tilepic 84 150 572 }{ textentrylimited 163 251 40 20 2655 2 2 3 }"
                             f"{{ button 222 248 247 249 1 0 {CAPTCHA_SUBMIT} }}")
DECOY_LAYOUT = ("{ nomove }{ noclose }{ nodispose }{ noresize }{ page 0 }{ page 1 }"
                "{ croppedtext -324 -203 1 1 0 0 }{ croppedtext -393 -158 1 1 0 1 }")


class World:
    def __init__(self, scenario="home"):
        self.scenario = scenario          # "home" (the main run), "skirmish", "break", "library", "tracking",
        #                                   "gazer" (a ranged creature hits once), "wary" (an aggressive creature
        #                                   by a tree), "red_aim" (a red comes into view during the aim pause),
        #                                   "thief" (a blue walks up while we chop, then follows us),
        #                                   "pouch_pop" (a hidden thief sets our trapped pouch off) or
        #                                   "staff" (a vendor next to us, then an invulnerable player in view)
        self.scripted = scenario == "home"  # captchas, the passer-by's speech, the pickpocket
        self.library = scenario in ("library", "tracking", "gazer", "red_aim", "thief", "pouch_pop", "landing_monster",
                                    "ghost_horse", "faction", "precast", "flee_aid")
        #                                   the library spot (pvp), black pearls in the pack
        self.facet, self.pos = ROOM_FACET, list(ROOM_ARRIVAL)   # every run starts in the rental room
        self.facing = 0
        self.writer = None
        self.c2s = []
        self.c2s_t = []                                     # wall time each C2S packet arrived
        self.spoke_at = None
        self.cid = 0x58B00
        self.cursor_for = None
        self.gump_serial = 0x245000
        self.decoys = set()
        self.decoy_replies = 0
        self.captcha_shown = 0
        self.captcha_answers = 0
        self.captcha_open = None
        self.captcha_auto = False         # trip-1 captcha is solver-readable
        self.auto_answers = []            # the texts the agent's solver submitted
        self.pending_attempt = None       # the attempt's result, sent once the captcha is answered
        self.good_n = 0
        self.stacks = {}                  # log/board stacks in the pack: serial -> [graphic, amount, container]
        self.next_stack = 0x45000001
        self.stolen = 0
        self.chest_stack = None           # (serial, amount): the boards in the home chest
        self.stockpile = None             # {"boards": n, "adds": [stack serials], "gumps": set, "closed": n}: none
        self.pile_cursor = None           # the cursor id Add Items gave
        self.pouch_hue = {p: 38 for p in POUCHES}   # trapped pouches in the pack (hue 38 live, 0 gone off)
        self.pops = []                    # (pouch, time, 'us' | 'thief')
        self.stashed = []                 # (stack serial, amount, pouch) per drop of logs/boards into a pouch
        self.chest_items = []             # other items dropped into the chest (spent pouches): (serial, hue)
        self.chest_opens = 0              # the chest double-clicked (opened) within reach in the room
        self.room_gumps = {}              # rental room menu serial -> which live gump (room_gumps.json key), open
        self.room_serials = set()         # every room menu serial sent
        self.room_presses = []            # (gump kind, button) the agent pressed on room menus
        self.room_log = []                # ("enter" | "exit", time) of every way into and out of the room
        self.keeper_far = 0               # the steward's menu picked from beyond 2 tiles (refused)
        self.shelf_stock = None           # {"room": n, "landing": n} trapped pouches per shelf; None: no shelves
        self.horse = None                 # our bonded horse off the mount: {"dead": bool}; None: no horse
        self.mounted = False              # riding it (the mount item on layer 0x19)
        self.mount_rested = 0             # times the guild house sent the ridden mount to rest
        self.mount_resting = False        # it rests now (no mount item); a recall out or the room returns it
        self.horse_menus = 0              # context menus asked for on it
        self.horse_dclicks = []           # per double-click on it: was it a ghost then
        self.shelf_gumps = {}             # shelf gump serial -> "room" | "landing", open
        self.shelf_seen = False           # the landing's shelves sent (in update range, facet 0)
        self.resupplies = []              # (shelf, pouches given) per Resupply press
        self.shelf_presses = []           # every button pressed on a shelf gump
        self.restock_cursor = None        # (cursor id, shelf) Restock gave
        self.restocks = []                # (shelf, targeted serial) per Restock answer
        self.shelf_pouches = []           # spent pouches the shelves took
        self.secured_tries = 0            # double-clicks on the shelf secured against us
        self.steward_clicks = 0           # single clicks on the steward (each answered with his label)
        self.steward_menus = 0            # right-clicks on the steward (none: "room" is said)
        self.recalls_book = []            # recalls to our runebook's 'Sim Woods' rune
        self.thief_pos = None             # thief: where the blue stands
        self.thief_at = []                # thief: every place he stepped next to us
        self.thief_followed = False       # thief: he came back next to us at the next stand
        self.converts_refused = 0         # log stacks targeted inside a live trapped pouch (never opened)
        self.lifted = None
        self.door_open = False
        self.open_door_reqs = 0
        self.drops_refused = 0
        self.harvested = 0
        self.good_left = GOOD_VISIT       # chops the trees with wood give before 'nothing nearby' (per visit)
        self.self_targets = []            # Smart Harvest answers: (our tile, the packet == the stock client's)
        self.location_answers = 0         # chop cursors answered with a location (per-tree targeting): none now
        self.nothing_near_at = []         # our tile at each 'nothing nearby'
        self.chopped = []                 # (tree tile, time) of every chop the server made
        self.doors_opened = 0
        self.containers_opened = []                          # 0x06 on the backpack (and the bag), in order
        self.gate_gumps = {}              # renounce-prompt serial -> buttons the agent/client replied
        self.attacker_pos = None          # skirmish: the creature that goes for the agent
        self.chase = False                # skirmish: the attacker follows the agent step for step
        self.late_blast = False           # red_aim: a PK's Explosion goes off on us 0.4 s after the recall home lands
        self.attacker_swings = 0
        self.far_attempts = 0             # skirmish: harvest attempts at the far tree
        self.steward_seen = False         # the steward's 0x20 sent since he last left the client's view
        self.book_gumps, self.tome_gumps = set(), set()   # library scenario: gumps we sent
        self.recalls_out, self.recalls_home, self.tome_far = [], [], 0
        self.tome_seen = False
        self.home_disturbed = 1 if scenario == "red_aim" else 0   # library: the first recall home is disturbed
        self.lockout_due = False          # library: the first chop after a recall out meets the travel lockout
        self.lockouts = 0
        # tracking: the server's hunt (mode = index into TRACK_MODES; the proxy hasn't heard it yet)
        self.tracking = scenario == "tracking"
        self.hunt = {"mode": TRACK_MODES.index("passive creatures"), "on": False}
        self.track_gumps = set()
        self.track_c2s = []               # (time, what): skill uses ("use") and gump buttons (int)
        self.hunt_dropped_t = None        # when the recall out stopped the hunt
        self.arrow_id = 0
        self.red_hits = []                # (distance, sent while hunting murderers)
        # gazer: our hits (sent as 0xA1) and the creature's spell range in the simulation (12: what the runner
        # assumes; larger: it outranges the walk-away)
        self.hits = 100
        self.gazer_pos, self.gazer_range = None, 12
        self.gazer_hits = []              # (time, our distance from it) per cast that hit
        self.reflect = False              # gazer_reflect: Magic Reflection is up and takes the next spell
        self.reflected = []               # (time, our distance from it) per spell Magic Reflection took
        self.wary_left_t = None           # wary: when the creature by the near tree left view
        self.wary_flags = 0x40            # wary: its 0x20 flags (war mode; 0: idle, idle_mob)
        self.wary_late = False            # zone_on_way: the creature isn't there at first; it shows up as we come
        self.wary_shown = False           # zone_on_way: it has come into view
        self.carried = []                 # convert_stacks: (log graphic, amount) stacks in the pack at login
        self.no_cursor_once = False       # convert_stacks: the first hatchet use in the room brings no cursor
        self.door_seen = False            # the door and gates go out on facet 0, then again like the steward
        self.red_due = False              # red_aim: the red is on its way (RED_AIM_S after the chop's cursor)
        self.red_t = None                 # red_aim: when the red's 0x20 went out (faction/precast: the foe's tag / words)
        self.calm_t = None                # faction/precast: when the guildmate / the healer showed up
        self.foe_pos = None               # red_aim/faction/precast: where the foe stands (static)
        self.book_dists = []              # ... our distance to him at each runebook double-click
        self.vendor_t = None              # staff: when the vendor's 0x20 went out
        self.gm_t = None                  # staff: when the invulnerable player's 0x20 went out
        # flee_aid: our potions (serial -> [graphic, amount]), the red's hit, and what the runner used
        self.potions = {s: [g, 5] for s, g in POTIONS.items()} if scenario == "flee_aid" else {}
        self.frozen = self.poisoned = False
        self.hurt_t = self.unfrozen_t = None
        self.aid_clicks = []              # (time, "cure" | "heal" | "refresh" | "pouch", frozen then)
        self.steps = []                   # (time, accepted) per walk request after the hit

    # ---- the pack's wood and trapped pouches ----
    @property
    def room_entries(self):
        return sum(1 for k, _ in self.room_log if k == "enter")

    @property
    def logs(self):
        return sum(n for g, n, _ in self.stacks.values() if g == LOG_G)

    @property
    def pack_boards(self):
        return {s: n for s, (g, n, _) in self.stacks.items() if g == BOARD_G}

    def stack_pkt(self, serial):
        g, n, c = self.stacks[serial]
        return contained(serial, g, n, c)

    def add_wood(self, graphic, amount, container):
        """RunUO TryDropItem: onto the container's stack of the same graphic, else a new stack."""
        s = next((k for k, (g, _, c) in self.stacks.items() if g == graphic and c == container), None)
        if s is None:
            s, self.next_stack = self.next_stack, self.next_stack + 1
            self.stacks[s] = [graphic, 0, container]
        self.stacks[s][1] += amount
        return s

    def pouch_pkt(self, p):
        return contained(p, POUCH_G, 1, BACKPACK, x=40 + 10 * (POUCHES + SHELF_POUCHES).index(p),
                         hue=self.pouch_hue[p])

    def pouch_goes_off(self, p, by):
        """Our double-click on a live pouch ('us': a hit, back a moment later as hits regenerate, and the
        "remaining" line) or a thief's snoop."""
        self.pouch_hue[p] = 0
        self.pops.append((p, time.time(), by))
        if by == "us":
            left = sum(1 for h in self.pouch_hue.values() if h == 38)
            self.later(0.05, pop_flush(*self.pos, p, hits=self.hits - 1, left=left))
            # back as hits regenerate: the hits then (a heal potion may have come in between)
            asyncio.get_running_loop().call_later(1.5, lambda: self.send(hits_pkt(self.hits)))
        else:
            self.later(0.05, pop_flush(*self.pos, p))

    def steal(self):
        """The pickpocket takes STOLEN logs from the biggest log stack (in the trapped pouch, which never
        went off: Kataleon's case, docs/PLAN.md), unannounced."""
        s = max((k for k, (g, _, _) in self.stacks.items() if g == LOG_G), key=lambda k: self.stacks[k][1])
        self.stacks[s][1] -= STOLEN
        self.stolen += STOLEN
        self.send(self.stack_pkt(s))

    def send(self, pkt):
        self.writer.write(encode_packet(pkt, S2C_KEY))

    def later(self, delay, pkts):
        async def go():
            await asyncio.sleep(delay)
            for p in pkts:
                self.send(p)
            await self.writer.drain()
        asyncio.get_running_loop().create_task(go())

    def next_gump(self):
        self.gump_serial += 1
        return self.gump_serial

    def cheb(self, p):
        return max(abs(self.pos[0] - p[0]), abs(self.pos[1] - p[1]))

    def update_view(self):
        """On facet 0 the steward comes into view within the server's update range (18) and leaves
        the client's beyond its view range (24, ClassicUO MAX_VIEW_RANGE), as the proxy's world
        model prunes it; so he is sent again on the way back. The library tome, the town door and
        its gates the same way. A facet change prunes all of them (enter_room resets the flags)."""
        if self.facet != 0:
            return
        for flag, d, pkts in (("steward_seen", self.cheb(STEWARD_POS), lambda: [mobile_pkt(STEWARD, *STEWARD_POS)]),
                              ("tome_seen", self.cheb(TOME_POS), lambda: [ground_item(TOME, 0x71AF, *TOME_POS, 0)]),
                              ("door_seen", self.cheb(DOOR), self.door_pkts),
                              ("shelf_seen", self.cheb(LANDING_SHELF_POS), self.landing_shelf_pkts)):
            if d <= 18 and not getattr(self, flag):
                setattr(self, flag, True)
                for p in pkts():
                    self.send(p)
            elif d > 24:
                setattr(self, flag, False)

    def landing_shelf_pkts(self):
        if self.shelf_stock is None:
            return []
        return [ground_item(SECURED_SHELF, 0xDC38, *LANDING_SHELF_POS, 0),     # "spring storage shelf" (live)
                ground_item(LANDING_SHELF, 0xDC38, *LANDING_SHELF_POS, 0)]

    def shelf_gump(self, which):
        self.shelf_gumps[self.next_gump()] = which
        self.send(gump(self.gump_serial, 0xC0B1026D, SHELF_GUMP["layout"], SHELF_GUMP["lines"]))

    def resupply(self, which):
        """The loadout's trapped pouches topped up from this shelf's stock, the server's lines as live."""
        need = max(0, LOADOUT_POUCHES - sum(1 for h in self.pouch_hue.values() if h == 38))
        give = min(need, self.shelf_stock[which])
        self.shelf_stock[which] -= give
        fresh = [s for s in SHELF_POUCHES if s not in self.pouch_hue][:give]
        for s in fresh:
            self.pouch_hue[s] = 38
            self.send(self.pouch_pkt(s))
        self.resupplies.append((which, len(fresh)))
        if not fresh:
            self.send(sys_text("Unable to resupply: no items available."))
        elif len(fresh) < need:
            self.send(sys_text("No resupply: Trapped Pouch"))
        self.shelf_gump(which)

    def restock_target(self, which, f):
        """Restock's cursor answered (the user's routine: our backpack): the shelf takes every pouch in the pack,
        live trapped ones back into its stock, the spent ones as plain pouches; "N items were added." (live), the
        shelf's gump again."""
        self.restocks.append((which, f["serial"]))
        took = 0
        if f["serial"] == BACKPACK:
            for s in [s for s in self.pouch_hue if not any(c == s for _, _, c in self.stacks.values())]:
                if self.pouch_hue.pop(s) == 38:
                    self.shelf_stock[which] += 1
                else:
                    self.shelf_pouches.append(s)
                self.send(delete(s))
                took += 1
        self.send(sys_text(f"{took} items were added." if took else
                           "That container does not contain any items that may be added."))
        self.shelf_gump(which)

    def door_pkts(self):
        return [ground_item(0x40005CE3, 0x06AD, *DOOR, 0)] + [      # the town door (demo art), blue moongates
            ground_item(0x40006000 + i, 0x0F6C, gx, gy, 0) for i, (gx, gy) in enumerate(sorted(GATES))]

    def teleport(self, x, y):
        self.pos = [x, y]
        self.send(self_at(x, y, self.facing))
        self.update_view()

    def recall_to(self, dest, log):
        """Kal Ort Por, then the jump about 2.1 s later (live 2026-10-02/03). Out (the library's
        rune or our book's): the travel lockout comes; at the library rune a player chops nearby
        (crowding)."""
        log.append(dest)
        self.send(sys_text("Kal Ort Por"))
        asyncio.get_running_loop().call_later(2.1, self.teleport, *dest)
        if log is not self.recalls_home:
            self.lockout_due = True
            if dest == RUNE_POS:
                self.later(2.3, [player_update(OTHER, LIB_TREE["x"] + 4, LIB_TREE["y"])])
            if dest == RUNE_POS and self.scenario == "landing_monster":   # one waits at the landing (live Wintertop)
                self.later(2.1, [creature_pkt(LANDER, 0x27, *LANDER_POS)])
            if self.tracking and self.hunt["on"]:      # the hunt stops on landing (simulated: live ones don't)
                asyncio.get_running_loop().call_later(2.2, self.drop_hunt)
            if self.mount_resting:                     # live: "Your mount returns." as we leave the guild house
                asyncio.get_running_loop().call_later(2.15, self.mount_returns)
        else:
            if self.chase:                             # a creature at our heels stays behind (a recall jumps)
                asyncio.get_running_loop().call_later(2.1, self.attacker_left)
            if self.late_blast:                        # red_aim: the PK's Explosion goes off on us at home
                asyncio.get_running_loop().call_later(2.5, self.blast)
            if self.mounted:                           # live: the guild house sends a ridden mount to rest
                asyncio.get_running_loop().call_later(2.15, self.mount_rests)

    def blast(self):
        """A PK's Explosion detonating on us 0.4 s after the recall landed (live 2026-10-06 11:38:55): the effect on
        us, the explosion sound on our tile, -31; no pouch of ours goes off."""
        self.hits -= 31
        self.send(effect_on_self(0x36BD, *self.pos))
        self.send(b"\x54\x01\x03\x07\x00\x00" + u32(self.pos[0]) + u32(self.pos[1]) + u32(0))
        self.send(hits_pkt(self.hits))

    def attacker_left(self):
        self.chase, self.attacker_pos = False, None
        self.send(delete(ATTACKER))

    def mount_rests(self):
        """'Your mount finds a quiet place to rest safely.' (live 2026-10-05: into the DTF guild house by a recall
        or out of the rental room): the mount item goes until we leave the house (recall out, the room)."""
        self.mounted, self.mount_resting, self.mount_rested = False, True, self.mount_rested + 1
        self.send(delete(MOUNT_ITEM))
        self.send(sys_text("Your mount finds a quiet place to rest safely."))

    def mount_returns(self):
        self.mounted, self.mount_resting = True, False
        self.send(equip(MOUNT_ITEM, 0x3EA1, 0x19))
        self.send(sys_text("Your mount returns."))

    # ---- the rental room (live 2026-10-04: docs/NOTES.md "Rental room via the DTF house steward") ----
    def room_gump(self, kind):
        self.room_gumps[self.next_gump()] = kind
        self.room_serials.add(self.gump_serial)
        g = ROOM_GUMPS[kind]
        self.send(gump(self.gump_serial, int(g["gump_id"], 16), g["layout"], g["lines"]))

    def enter_room(self):
        """Into Logan Wolf's room: the map change to facet 3 (the world model prunes everything we
        don't carry), our new tile, the door and the chest, the server's line."""
        self.facet, self.pos = ROOM_FACET, list(ROOM_ARRIVAL)
        self.steward_seen = self.tome_seen = self.door_seen = self.shelf_seen = False
        self.door_open = False
        self.room_log.append(("enter", time.time()))
        self.send(map_change(ROOM_FACET))
        self.send(self_at(*ROOM_ARRIVAL, self.facing))
        self.send_room_items()
        if self.mount_resting:                        # live: "Your mount returns."
            self.mount_returns()
        elif self.horse is not None and not self.mounted:   # live: its ghost came back alive in the room
            self.horse["dead"] = False
            self.send_horse()
        self.send(sys_text("You enter the rental room."))

    def send_horse(self):
        """Our horse following us, a tile off (with the ghost flag, 0xBF sub 0x19, when it's dead); not while
        we ride it or it rests in the guild house."""
        if self.horse is None or self.mounted or self.mount_resting:
            return
        self.send(creature_pkt(HORSE, HORSE_BODY, self.pos[0] + 1, self.pos[1], noto=2, flags=0))
        if self.horse["dead"]:
            self.send(bytes.fromhex("bf000b001900") + u32(HORSE) + b"\x01")

    def send_room_items(self):
        self.send(ground_item(ROOM_DOOR, 0x06A5, *ROOM_DOOR_POS, 1))      # "wooden door"
        self.send(ground_item(CHEST, 0x0E40, *CHEST_POS, 2))               # the secure chest
        if self.shelf_stock is not None:
            self.send(ground_item(ROOM_SHELF, 0xAFC5, *ROOM_SHELF_POS, 2))   # "storage shelf" (live)
        if self.stockpile is not None:
            self.send(ground_item(STOCKPILE, 0x59FA, *STOCKPILE_POS, 2))     # "a resource stockpile" (live)

    def stockpile_gump(self):
        """The Resource Stockpile's menu (a fresh serial each time, as live), the board count on its line."""
        self.stockpile["gumps"].add(self.next_gump())
        self.send(gump(self.gump_serial, STOCKPILE_GUMP, STOCKPILE_LAYOUT,
                       ["Guide", "Resource Stockpile", str(self.stockpile["boards"]), "Settings"]))

    def stockpile_target(self, f):
        """Add Items' cursor answered: a board stack in the pack goes in, or every board stack in a targeted
        pouch ("You add N item(s) ...", live 2026-10-05; the stacks deleted, the menu again); anything else, or
        ourselves, is refused here."""
        s = f["serial"]
        if f["target_type"] == 0 and s in self.pouch_hue:
            inside = [b for b, (g, _, c) in self.stacks.items() if g == BOARD_G and c == s]
        elif f["target_type"] == 0 and s in self.stacks and self.stacks[s][0] == BOARD_G:
            inside = [s]
        else:
            inside = []
        if inside:
            for b in inside:
                self.stockpile["boards"] += self.stacks.pop(b)[1]
                self.send(delete(b))
            self.stockpile["adds"].append(s)
            self.send(sys_text(f"You add {len(inside)} item(s) to the Resource Stockpile."))
        else:
            self.stockpile["refused"].append(s)
            self.send(sys_text("You cannot add that to the Resource Stockpile."))
        self.stockpile_gump()

    def leave_room(self):
        """'Exit to House Steward': back on facet 0 at the home landing (live: 4134,1429)."""
        self.facet, self.pos = 0, list(HOME_RUNE_POS)
        self.room_log.append(("exit", time.time()))
        self.send(map_change(0))
        self.send(self_at(*HOME_RUNE_POS, self.facing))
        self.send(sys_text("You exit the rental room."))
        if self.mounted:                               # live 12:32: out of the room into the guild house
            self.mount_rests()
        self.update_view()
        self.field_mobiles()
        self.send_horse()

    def field_mobiles(self):
        """Mobiles standing in the field, sent on coming out on facet 0 (a facet change prunes them):
        skirmish's 'great hart' in war mode fighting a player (knowledge #89), wary's war-mode creature
        by the near tree (until it has wandered off)."""
        if self.scenario == "skirmish":
            self.send(creature_pkt(FIGHTER, 0xEA, *FIGHTER_POS))
            self.send(player_update(OTHER, *OTHER_POS))
        if self.scenario == "wary" and self.wary_left_t is None and (not self.wary_late or self.wary_shown):
            self.send(creature_pkt(WARY, 0x27, *WARY_POS, flags=self.wary_flags))

    # ---- tracking (live 20261001_214649: docs/NOTES.md "Tracking") ----
    def tracking_gump(self):
        """The server (re)sends the Tracking gump; its Begin/Stop text follows the hunt."""
        self.track_gumps.add(self.next_gump())
        on = self.hunt["on"]
        self.send(gump(self.gump_serial, TRACK_ID, TRACK_GUMP["layout_hunting" if on else "layout_idle"],
                       TRACK_GUMP["lines_hunting" if on else "lines_idle"]))

    def hunt_switch(self, on):
        self.hunt["on"] = on
        self.send(player_says(SELF, "Hackworth", "You begin hunting." if on else "You stop hunting."))
        self.send(hunting_buff(on))

    def drop_hunt(self):
        self.hunt_dropped_t = time.time()
        self.hunt_switch(False)

    def red_hit(self, dist):
        """A Tracking hit on the red `dist` tiles east of us, never sent as a mobile: the
        "Now tracking" line, the old arrow cancelled, the new one set (live order). Only a
        hunt on murderers finds him."""
        murderers = self.hunt["on"] and TRACK_MODES[self.hunt["mode"]] == "murderer players"
        self.red_hits.append((dist, murderers))
        if not murderers:
            return
        pk = [sys_text(f"Now tracking: {RED_NAME} ({dist} spaces to target)")]
        if self.arrow_id:
            pk.append(arrow_cancel(self.arrow_id - 1))
        pk.append(arrow_set(self.arrow_id, RED, self.pos[0] + dist, self.pos[1], 0, f"[Hunting] {RED_NAME}"))
        self.arrow_id += 1
        self.later(0.2, pk)

    # ---- harvest ----
    def smart_harvest(self):
        """The hatchet's cursor answered with ourselves (Smart Harvest): the server chops the
        nearest tree with wood within SIM_RANGE of us (the dry tree never has any; the others
        share good_left chops per visit), else says nothing nearby has wood: the system line,
        then our own overhead line (capture 20261001_214649), and the wood regrows for the
        next visit. The first two attempts of the scripted run raise the real captcha first
        and answer after the solve (the capture's 23:20 attempt: captcha, then 'nothing nearby')."""
        if self.lockout_due:
            self.lockout_due = False
            self.lockouts += 1
            self.later(0.3, [sys_text(f"You have recently traveled and must wait {LOCKOUT_S} seconds "
                                      "before you may begin harvesting.")])
            return
        if self.scenario == "thief" and self.thief_pos is not None and not self.thief_followed \
                and self.cheb(self.thief_pos) > 2:      # he follows us to the next stand
            self.thief_followed = True
            asyncio.get_running_loop().call_later(0.2, self.thief_appears)
        self.decoys.add(self.next_gump())
        self.send(gump(self.gump_serial, 0x50000000 + self.gump_serial, DECOY_LAYOUT,
                       ["Captcha", "Guide", "Type the Value", "Click when complete"]))
        near = sorted((self.cheb(t), t) for t in SIM_TREES
                      if t != (DRY_TREE["x"], DRY_TREE["y"]) and self.cheb(t) <= SIM_RANGE)
        if near and self.good_left > 0:
            tree = near[0][1]
            self.good_left -= 1
            self.chopped.append((tree, time.time()))
            if tree == (FAR_TREE["x"], FAR_TREE["y"]):
                self.far_attempts += 1
            then = self.result
        else:
            then = self.nothing_near
        if self.scripted and self.captcha_shown < 2:
            self.captcha_shown += 1
            self.captcha_open = self.next_gump()
            self.captcha_auto = self.captcha_shown == 1     # the first: auto-solved; the second: fallback
            lay = REAL_CAPTCHA_LAYOUT if self.captcha_auto else UNREADABLE_CAPTCHA_LAYOUT
            self.send(gump(self.captcha_open, 0x00000001, lay, ["Guide", "Captcha", "", "Type The Value"]))
            self.pending_attempt = then
            return
        then()

    def nothing_near(self):
        self.nothing_near_at.append(tuple(self.pos))
        if self.good_left == 0:                          # out of wood; regrown for the next visit
            self.good_left = GOOD_VISIT
        self.later(0.3, [sys_text(NOTHING_NEAR[0]), player_says(SELF, "Hackworth", NOTHING_NEAR[1])])

    def result(self):
        self.good_n += 1
        if self.scenario == "wary" and self.good_n == 1:   # the creature by the near tree wanders off
            self.wary_left_t = time.time() + 0.3
            # it walks off out of view (its last tile far from our routes), then is gone
            self.later(0.3, [creature_pkt(WARY, 0x27, WARY_POS[0], WARY_POS[1] + 60, flags=self.wary_flags),
                             delete(WARY)])
        if self.tracking and self.good_n in (2, 4):   # mid-chop: the hunt finds the red far, then near
            self.red_hit(RED_FAR if self.good_n == 2 else RED_NEAR)
        if self.good_n % 2 == 1:
            self.later(0.3, [cliloc(500495)])
            return
        s = self.add_wood(LOG_G, LOGS_PER_SUCCESS, BACKPACK)     # new logs lie in the pack (AddToBackpack)
        self.harvested += LOGS_PER_SUCCESS
        self.later(0.3, [self.stack_pkt(s), sys_text("You chop some logs and put them in your backpack.")])
        if not self.scripted:
            if self.scenario == "skirmish" and self.good_n == 2:      # a creature goes for the agent
                asyncio.get_running_loop().call_later(0.5, self.attacker_appears, ATTACKER_POS, False)
            elif self.scenario == "skirmish" and self.good_n == 4:    # ... and later hunts it down
                asyncio.get_running_loop().call_later(0.5, self.attacker_appears, None, True)
            elif getattr(self, "chaser", False) and self.good_n == 2:  # a creature hunts us at a far spot
                asyncio.get_running_loop().call_later(0.5, self.attacker_appears, None, True)
                # ... and a pickpocket's grab lands in the same moment (live 2026-10-03: 10 mandrake
                # root gone 75 ms after the thief's flag change; the run fled before booking it)
                self.later(0.5, [delete(0x44ADB0FF)])
            elif self.scenario == "gazer" and self.good_n == 2 and self.gazer_pos is None:
                asyncio.get_running_loop().call_later(0.5, self.gazer_appears)
            elif self.scenario == "staff" and self.good_n == 2:     # a vendor steps next to us: nothing
                self.vendor_t = time.time() + 0.3
                self.later(0.3, [mobile_pkt(VENDOR_NEAR, self.pos[0] + 1, self.pos[1])])
            elif self.scenario == "staff" and self.good_n == 4:     # an invulnerable player comes into view
                self.gm_t = time.time() + 0.5                      # (its robe first: worn when it shows)
                self.later(0.5, [equip(GM_ROBE, 0x204F, 0x16, GM_SEEN, 0x0481),
                                 player_update(GM_SEEN, self.pos[0] + 5, self.pos[1], noto=7)])
            return
        if self.good_n == 2:                 # a player walks up (3 tiles: outside the steal guard) and speaks:
            self.spoke_at = time.time() + 1.0                     # the job must hold for the overseer
            self.later(0.5, [player_update(PASSERBY, self.pos[0] + 3, self.pos[1])])
            self.later(1.0, [player_says(PASSERBY, "Vorn", GREETING)])
            # during the hold, someone the client can't see speaks: a possible hidden GM
            self.later(2.0, [player_says(HIDDEN, "Ann", "what are you up to?")])
            # ... and a pickpocket lifts part of the logs out of the trapped pouch, unannounced (the runner
            # sends nothing while held: no chop or stash runs into the grab)
            asyncio.get_running_loop().call_later(2.5, self.steal)
            self.later(12.0, [delete(PASSERBY)])                    # he walks on

    def thief_appears(self):
        """thief: Caputo Wood (a blue, live 2026-10-03) steps next to us."""
        self.thief_pos = (self.pos[0] + 1, self.pos[1])
        self.thief_at.append(self.thief_pos)
        self.send(player_update(THIEF, *self.thief_pos))

    def attacker_appears(self, pos, chase):
        """The attacker comes into view at pos (None: next to the agent, then at its heels)."""
        self.attacker_pos = pos or (self.pos[0] + 1, self.pos[1])
        self.chase = chase
        self.send(creature_pkt(ATTACKER, 0x27, *self.attacker_pos))

    def red_appears(self):
        """red_aim: a red player comes into view 8 tiles east (Bastet, live 2026-10-03: 10 spaces)."""
        self.red_t = time.time()
        self.foe_pos = (self.pos[0] + 8, self.pos[1])
        self.send(player_update(BASTET, *self.foe_pos, noto=6))

    def pk_attacks(self):
        """flee_aid: the red comes into view (red_appears), PK_HURT_S later his spells land: hits to PK_HITS
        (0xA1), poisoned (0x17 for us) and paralyzed (our 0x20 with flag 0x01: 0x21); steps are refused
        until a trapped pouch of ours goes off."""
        self.red_appears()
        asyncio.get_running_loop().call_later(PK_HURT_S, self.pk_hurts)

    def pk_hurts(self):
        self.hurt_t, self.hits, self.poisoned, self.frozen = time.time(), PK_HITS, True, True
        self.send(hits_pkt(self.hits))
        self.send(poison_pkt(True))
        self.send(self_at(*self.pos, self.facing, flags=0x21))

    def unfreeze(self):
        """Our 0x20 without the frozen flag: steps are taken again."""
        self.frozen, self.unfrozen_t = False, time.time()
        self.send(self_at(*self.pos, self.facing))

    def drink(self, serial):
        """A potion double-clicked [INFERENCE: RunUO BasePotion]: cure clears the poison, heal (not while
        poisoned: RunUO BaseHealPotion, cliloc 1005000) gives HEAL_POT_HP; one less in the stack when it worked."""
        g, n = self.potions[serial]
        kind = {CURE_G: "cure", HEAL_G: "heal", REFRESH_G: "refresh"}[g]
        self.aid_clicks.append((time.time(), kind, self.frozen))
        if kind == "cure" and self.poisoned:
            self.poisoned = False
            self.send(poison_pkt(False))
        elif kind == "heal" and not self.poisoned and self.hits < 100:
            self.hits = min(100, self.hits + HEAL_POT_HP)
            self.send(hits_pkt(self.hits))
        elif kind == "heal":
            self.send(cliloc(1005000))                  # "You can not heal yourself in your current state."
            return
        elif kind == "cure":
            return
        self.potions[serial][1] = n - 1
        self.send(contained(serial, g, n - 1, BACKPACK) if n > 1 else delete(serial))

    def faction_appears(self):
        """faction (live 2026-10-06 witcher_66): our own guild tag; a guildmate whose guild is in a faction
        (his tag "[Cambria]", ours none: we don't take part), 4 tiles off; the faction waypost marker
        "FACTION WP 17" 9 tiles off. CALM_S later a Cambria faction player of another guild 10 tiles off
        (Bee Loga: still blue). The title lines come as the server's answer to the client's click on sight."""
        x, y = self.pos
        self.calm_t = time.time()
        self.send(player_says(SELF, CHAR_NAME, "[Farm Around Find Out, DTF]", hue=690))
        self.send(player_update(GUILDY, x + 4, y))
        self.send(player_says(GUILDY, "Proud Momma", "Banner Captain [Cambria]", hue=50))
        self.send(player_says(GUILDY, "Proud Momma", "[Proud Momma, DTF]", hue=690))
        self.send(mobile_pkt(WAYPOST_MOB, x - 9, y))
        self.send(label_pkt(WAYPOST_MOB, "FACTION WP 17", "FACTION WP 17"))
        asyncio.get_running_loop().call_later(CALM_S, self.foe_tagged)

    def foe_tagged(self):
        x, y = self.pos
        self.foe_pos = (x + 10, y)
        self.send(player_update(FOE, *self.foe_pos))
        self.red_t = time.time()
        self.send(player_says(FOE, "Bee Loga", "Elite Mercenary [Cambria]", hue=50))
        self.send(player_says(FOE, "Bee Loga", "[Officer, LoK]", hue=690))

    def precast_appears(self):
        """precast (live 2026-10-06 witcher_66: "Vas Ort Flam" 5.2 s before the attack): a blue healing
        himself 6 tiles off ("In Vas Mani", not harmful), then CALM_S later a blue 5 tiles off says
        Explosion's words. Neither carries a tag."""
        x, y = self.pos
        self.calm_t = time.time()
        self.send(player_update(GUILDY, x + 6, y))
        self.send(player_says(GUILDY, "Healer", "In Vas Mani", hue=690, kind=10))
        asyncio.get_running_loop().call_later(CALM_S, self.foe_casts)

    def foe_casts(self):
        x, y = self.pos
        self.foe_pos = (x + 5, y)
        self.send(player_update(FOE, *self.foe_pos))
        self.red_t = time.time()
        self.send(player_says(FOE, "Bee Loga", "Vas Ort Flam", hue=690, kind=10))

    def gazer_appears(self):
        """A gazer comes into view 10 tiles west of us, not in war mode (its aggression unknown to
        the runner), and casts at us every GAZER_CAST_S while we're within its range (no line of sight)."""
        self.gazer_pos = (self.pos[0] - 10, self.pos[1])
        self.send(creature_pkt(GAZER, GAZER_BODY, *self.gazer_pos, flags=0))
        asyncio.get_running_loop().create_task(self.gazer_casts())

    async def gazer_casts(self):
        await asyncio.sleep(0.8)
        while not self.writer.is_closing():
            d = self.cheb(self.gazer_pos)
            if d <= self.gazer_range and self.reflect:
                # Magic Reflection takes it (live 22:17:32.136): the System line and 0x37B9 on us, no 0xA1
                self.reflect = False
                self.reflected.append((time.time(), d))
                self.send(sys_text("Magic reflect removed."))
                self.send(effect_on_self(0x37B9, *self.pos))
                await self.writer.drain()
            elif d <= self.gazer_range and self.hits > GAZER_DMG:
                self.hits -= GAZER_DMG
                self.gazer_hits.append((time.time(), d))
                self.send(hits_pkt(self.hits))
                await self.writer.drain()
            await asyncio.sleep(GAZER_CAST_S)

    def convert(self, serial):
        """The log stack targeted, wherever it lies (in the opened pouch), becomes boards in the same
        container [INFERENCE: RunUO ScissorHelper]; a trapped pouch that never went off can't be seen
        into, so its logs can't be targeted (refused)."""
        if serial not in self.stacks or self.stacks[serial][0] not in range(0x1BDD, 0x1BE3):
            return
        g, n, c = self.stacks.pop(serial)
        if self.pouch_hue.get(c) == 38:
            self.stacks[serial] = [g, n, c]
            self.converts_refused += 1
            return
        b = self.add_wood(BOARD_G, n, c)
        self.later(0.2, [delete(serial), self.stack_pkt(b), sys_text("You shape the logs into boards.")])

    # ---- packets ----
    def on_packet(self, p):
        self.c2s.append(p)
        self.c2s_t.append(time.time())
        pid = p[0]
        if pid == 0x02 and self.hurt_t is not None:
            self.steps.append((time.time(), not self.frozen))
        if pid == 0x02 and self.frozen:                  # paralyzed: "You are frozen and can not move." (RunUO 500111)
            self.send(b"\x21" + bytes([p[2]]) + u32(self.pos[0]) + u32(self.pos[1]) + bytes([self.facing]) + u32(0))
            self.send(cliloc(500111))
        elif pid == 0x02:
            seq, d = p[2], p[1] & 7
            if d != self.facing:
                self.facing = d
                self.send(bytes([0x22, seq, 0x01]))
                return
            nx, ny = self.pos[0] + DD[d][0], self.pos[1] + DD[d][1]
            blocked = self.facet == 0 and ((nx, ny) in WALLS or ((nx, ny) == DOOR and not self.door_open)
                                           or (nx, ny) == STEWARD_POS)
            if blocked:
                self.send(b"\x21" + bytes([seq]) + u32(self.pos[0]) + u32(self.pos[1])
                          + bytes([self.facing]) + u32(0))
                return
            old, self.pos = tuple(self.pos), [nx, ny]
            if self.facet == 0 and (nx, ny) in GATES:    # the gate's gump comes before the confirm
                self.gate_gumps[self.next_gump()] = []
                self.send(gump(self.gump_serial, RENOUNCE_ID, RENOUNCE_LAYOUT,
                               ["Young Player Status", "Guide",
                                "Leaving Shelter Island will cause you to renounce your"]))
            if self.door_open and self.cheb(DOOR) > 2:   # the door swings shut behind the agent
                self.door_open = False
            self.send(bytes([0x22, seq, 0x01]))
            self.update_view()
            if self.wary_late and not self.wary_shown and self.facet == 0 and self.pos[0] >= 103 \
                    and abs(self.pos[1] - 200) <= 3:     # zone_on_way: it comes into view by the near tree
                self.wary_shown = True
                self.send(creature_pkt(WARY, 0x27, *WARY_POS, flags=self.wary_flags))
            if self.chase:                               # the attacker keeps at the agent's heels
                self.attacker_pos = old
                self.send(creature_pkt(ATTACKER, 0x27, *old))
        elif pid == 0x12 and p[3] == 0x58:
            self.open_door_reqs += 1
            if self.facet == 0 and self.cheb(DOOR) <= 1:
                self.door_open = True
                self.doors_opened += 1
                self.send(cliloc(500024))
        elif pid == 0x12 and p[3] == 0x24 and p[4:-1] == b"38 0":  # UseSkill Tracking
            self.track_c2s.append((time.time(), "use"))
            if self.tracking:
                self.send(cliloc(1011350))               # "What do you wish to track?"
                self.tracking_gump()
        elif pid == 0x09:                                # single click: the steward answers with his label
            if int.from_bytes(p[1:5], "big") == STEWARD and self.facet == 0 and self.cheb(STEWARD_POS) <= 18:
                self.steward_clicks += 1
                self.later(0.05, [label_pkt(STEWARD, "Chase", STEWARD_LABEL)])
        elif pid == 0xBF and p[3:5] == b"\x00\x13":    # context menu request (right-click)
            if int.from_bytes(p[5:9], "big") == STEWARD:
                self.steward_menus += 1                  # the runner says "room" instead (the menu stays up)
            elif int.from_bytes(p[5:9], "big") == HORSE and self.horse is not None and not self.mounted:
                self.horse_menus += 1
                self.later(0.06, [PET_POPUP])
        elif p == actions.say_unicode("room"):          # "room" by the steward opens the room menu (within 2 tiles)
            if self.facet != 0 or self.cheb(STEWARD_POS) > 2:
                self.keeper_far += 1
                return
            self.room_gump("steward_no_room")
        elif pid == 0x06:
            serial = int.from_bytes(p[1:5], "big")
            if serial == HORSE and self.horse is not None and not self.mounted:
                self.horse_dclicks.append(self.horse["dead"])
                if not self.horse["dead"]:                 # up on it: the mobile goes, the mount item comes
                    self.mounted = True
                    self.send(delete(HORSE))
                    self.send(equip(MOUNT_ITEM, 0x3EA1, 0x19))
            elif serial == HATCHET and self.no_cursor_once and self.facet == ROOM_FACET:
                self.no_cursor_once = False              # convert_stacks: this use brings no cursor
            elif serial == HATCHET:
                self.cid += 1
                self.cursor_for = self.cid
                self.send(cliloc(1010018))
                self.send(cursor(self.cid))
                if self.scenario in ("red_aim", "faction", "precast", "flee_aid") and not self.red_due \
                        and self.cheb(RUNE_POS) <= 5:
                    self.red_due = True
                    act = {"red_aim": self.red_appears, "faction": self.faction_appears,
                           "precast": self.precast_appears, "flee_aid": self.pk_attacks}[self.scenario]
                    asyncio.get_running_loop().call_later(RED_AIM_S, act)
            elif serial == ROOM_DOOR and self.facet == ROOM_FACET:     # the door's menu (opened from 6 tiles live)
                self.room_gump("door")
            elif serial == STOCKPILE and self.stockpile is not None and self.facet == ROOM_FACET \
                    and self.cheb(STOCKPILE_POS) <= 2:
                self.stockpile_gump()
            elif serial == CHEST and self.facet == ROOM_FACET and self.cheb(CHEST_POS) <= 2:
                self.chest_opens += 1
                self.send(b"\x24" + u32(CHEST) + bytes.fromhex("0000003c007d"))
                if self.chest_stack:
                    self.send(contained(self.chest_stack[0], BOARD_G, self.chest_stack[1], CHEST))
            elif serial in (BACKPACK, BAG):
                self.containers_opened.append(serial)
                self.send(b"\x24" + u32(serial) + bytes.fromhex("0000003c007d"))   # as captured (204225)
            elif serial in self.potions:
                self.drink(serial)
            elif serial in self.pouch_hue:               # live: it goes off (no 0x24); gone off: it opens
                if self.pouch_hue[serial] == 38:
                    if self.scenario == "flee_aid":
                        self.aid_clicks.append((time.time(), "pouch", self.frozen))
                    if self.frozen:                      # its damage breaks the paralysis (pop_flush first)
                        asyncio.get_running_loop().call_later(0.08, self.unfreeze)
                    self.pouch_goes_off(serial, "us")
                else:
                    self.containers_opened.append(serial)
                    self.send(b"\x24" + u32(serial) + bytes.fromhex("0000003c007d"))
            elif serial == ROOM_SHELF and self.shelf_stock is not None and self.facet == ROOM_FACET \
                    and self.cheb(ROOM_SHELF_POS) <= 2:
                self.shelf_gump("room")
            elif serial == SECURED_SHELF and self.shelf_stock is not None and self.facet == 0 \
                    and self.cheb(LANDING_SHELF_POS) <= 2:
                self.secured_tries += 1
                self.send(cliloc(501647))                # "That is secure."
            elif serial == LANDING_SHELF and self.shelf_stock is not None and self.facet == 0 \
                    and self.cheb(LANDING_SHELF_POS) <= 2:
                self.shelf_gump("landing")
            elif serial == TOME:                         # a locked-down tome opens within 2 tiles only
                if self.cheb(TOME_POS) > 2:
                    self.tome_far += 1
                    return
                self.tome_gumps.add(self.next_gump())
                self.send(gump(self.gump_serial, 0x09F5976B, TOME_GUMP["layout"], TOME_GUMP["lines"]))
            elif serial == RUNEBOOK:
                if self.foe_pos is not None:                # faction/precast/red_aim: how far the foe was
                    self.book_dists.append(self.cheb(self.foe_pos))
                self.book_gumps.add(self.next_gump())
                self.send(gump(self.gump_serial, 0x5C7DB029, *book_gump()))
        elif pid == 0x6C:
            f = parse_packet("c2s", p)
            if f["cursor_id"] == self.pile_cursor and self.pile_cursor is not None:
                self.pile_cursor = self.cursor_for = None
                self.stockpile_target(f)
                return
            if self.restock_cursor is not None and f["cursor_id"] == self.restock_cursor[0]:
                which, self.restock_cursor, self.cursor_for = self.restock_cursor[1], None, None
                self.restock_target(which, f)
                return
            if f["cursor_id"] != self.cursor_for:
                return
            self.cursor_for = None
            if f["target_type"] == 0 and f["serial"] == SELF:
                # the stock client's self-target: our serial, our tile, our body (z: the proxy's movement z)
                ok = p == actions.target_object(f["cursor_id"], SELF, self.pos[0], self.pos[1], f["z"], 0x190, 0)
                self.self_targets.append((tuple(self.pos), ok))
                self.smart_harvest()
            elif f["target_type"] == 0:
                self.convert(f["serial"])
            elif f["x"] != 0x7FFFFFFF:                   # not a cancel: a location answer
                self.location_answers += 1
        elif pid == 0xB1:
            f = parse_packet("c2s", p)
            if f["serial"] in (self.stockpile or {}).get("gumps", ()):
                self.stockpile["gumps"].discard(f["serial"])
                if f["button_id"] == 2:                  # Add Items: the server's prompt and a cursor
                    self.send(sys_text(stockpile_mod.ADD_PROMPT + ". Target yourself to add all valid items "
                                       "in your backpack."))
                    self.cid += 1
                    self.pile_cursor = self.cursor_for = self.cid
                    self.send(cursor(self.cid, 0))
                else:
                    self.stockpile["closed"] += 1
            elif f["serial"] in self.decoys:
                self.decoy_replies += 1
            elif f["serial"] in self.tome_gumps and f["button_id"] == 110:     # row 10: "286 - Midlands ..."
                self.recall_to(RUNE_POS, self.recalls_out)
            elif f["serial"] in self.shelf_gumps:
                which = self.shelf_gumps.pop(f["serial"])
                self.shelf_presses.append(f["button_id"])
                if f["button_id"] == 7:                                        # Resupply
                    self.resupply(which)
                elif f["button_id"] == 1000:                                   # Restock: a cursor (live)
                    self.send(sys_text("Which container do you wish to restock this container from? (you may "
                                       "target yourself or a nearby friendly pack animal)"))
                    self.cid += 1
                    self.restock_cursor = (self.cid, which)
                    self.cursor_for = self.cid
                    self.send(cursor(self.cid, 0))
            elif f["serial"] in self.room_gumps:
                kind = self.room_gumps.pop(f["serial"])
                self.room_presses.append((kind, f["button_id"]))
                if kind == "steward_no_room" and f["button_id"] == 2:          # Visit Other Rooms
                    self.room_gump("visit_list")
                elif kind == "visit_list" and f["button_id"] == 100 and self.facet == 0:   # Logan Wolf (DTF)
                    asyncio.get_running_loop().call_later(0.1, self.enter_room)
                elif kind == "door" and f["button_id"] == 6 and self.facet == ROOM_FACET:  # Exit to House Steward
                    asyncio.get_running_loop().call_later(0.1, self.leave_room)
            elif f["serial"] in self.book_gumps and f["button_id"] == 2:       # entry 0 'Sim Woods', a charge
                self.recall_to(BOOK_RUNE_POS, self.recalls_book)
            elif f["serial"] in self.book_gumps and f["button_id"] == 8:       # default entry 1 'Home', a charge
                if self.home_disturbed == 0:                                   # the first one is disturbed
                    self.home_disturbed += 1
                    self.send(sys_text("Kal Ort Por"))
                    self.later(0.5, [cliloc(500641)])
                    return
                self.recall_to(HOME_RUNE_POS, self.recalls_home)
            elif f["serial"] in self.gate_gumps:
                self.gate_gumps[f["serial"]].append(f["button_id"])
            elif f["serial"] in self.track_gumps:
                self.track_c2s.append((time.time(), f["button_id"]))
                n = len(TRACK_MODES)
                if f["button_id"] in (7, 8):
                    self.hunt["mode"] = (self.hunt["mode"] + (1 if f["button_id"] == 8 else -1)) % n
                    self.send(sys_text(f"You will now hunt {TRACK_MODES[self.hunt['mode']]}."))
                elif f["button_id"] == 6:
                    self.hunt_switch(not self.hunt["on"])
                if f["button_id"]:
                    self.tracking_gump()
            elif f["serial"] == self.captcha_open and f["button_id"] == CAPTCHA_SUBMIT:
                text = next((t["text"] for t in f.get("texts", []) if t["id"] == 2), "")
                if self.captcha_auto:
                    self.auto_answers.append(text)
                    if text != REAL_CAPTCHA_ANSWER:      # wrong: strike, fresh captcha opens
                        self.captcha_open = self.next_gump()
                        self.send(gump(self.captcha_open, 0x00000001, REAL_CAPTCHA_LAYOUT,
                                       ["Guide", "Captcha", "", "Type The Value"]))
                        return
                self.captcha_answers += 1
                self.captcha_open = None
                self.send(sys_text("Captcha successful."))
                if self.pending_attempt:
                    then, self.pending_attempt = self.pending_attempt, None
                    then()
        elif pid == 0x07:
            self.lifted = parse_packet("c2s", p)
            self.send(delete(self.lifted["serial"]))     # out of our view while on the cursor (live 113229)
        elif pid == 0x08:
            f = parse_packet("c2s", p)
            lf, self.lifted = self.lifted, None
            s, dest = f["serial"], f["container"]
            in_chest = dest == CHEST and self.facet == ROOM_FACET and self.cheb(CHEST_POS) <= 2
            if lf is None or lf["serial"] != s:
                self.drops_refused += 1
            elif in_chest and s in self.stacks and self.stacks[s][0] == BOARD_G:
                amount = self.stacks.pop(s)[1]
                if self.chest_stack is None:
                    self.chest_stack = (s, amount)
                    self.send(contained(s, BOARD_G, amount, CHEST))
                else:                                    # RunUO stacks with the existing pile
                    self.chest_stack = (self.chest_stack[0], self.chest_stack[1] + amount)
                    self.send(contained(self.chest_stack[0], BOARD_G, self.chest_stack[1], CHEST))
                return
            elif in_chest and s in self.pouch_hue:       # a spent pouch put away
                self.chest_items.append((s, self.pouch_hue.pop(s)))
                self.send(contained(s, POUCH_G, 1, CHEST))
                return
            elif dest in self.pouch_hue and s in self.stacks:   # wood dropped onto a pouch: into it,
                g, n, _ = self.stacks.pop(s)                     # onto its stack of that wood (RunUO TryDropItem)
                if any(g2 == g and c2 == dest for g2, _, c2 in self.stacks.values()):
                    into = self.add_wood(g, n, dest)
                else:
                    into, self.stacks[s] = s, [g, n, dest]
                self.stashed.append((s, n, dest))
                self.send(self.stack_pkt(into))
                if self.scenario == "pouch_pop" and not self.pops:       # a hidden thief snoops it a bit later
                    asyncio.get_running_loop().call_later(1.0, self.pouch_goes_off, dest, "thief")
                if self.scenario == "thief" and self.thief_pos is None:  # a blue walks up next to us
                    asyncio.get_running_loop().call_later(0.5, self.thief_appears)
                return
            else:
                self.drops_refused += 1
            if s in self.stacks:                         # refused: it bounces back where it was
                self.send(self.stack_pkt(s))
            elif s in self.pouch_hue:
                self.send(self.pouch_pkt(s))

    async def handle(self, reader, writer):
        await reader.readexactly(5)
        self.writer = writer
        writer.write(PRELUDE)
        self.send(login_pkt(self.pos))
        for token in (5, 6, 7, 8):
            self.send(seed_pkt(token))
        self.send(map_change(ROOM_FACET))                        # logged out in the rental room
        self.send(status_pkt(CHAR_NAME))                         # our name: it keys the home
        self.send(equip(BACKPACK, 0x0E75, 0x15))
        self.send(skills_pkt(600 if self.tracking else 0))      # Tracking 60 (Hackworth's) or none
        if self.scenario == "skirmish":                         # the hatchet is in a bag in the pack
            self.send(contained(BAG, 0x0E76, 1, BACKPACK))
            self.send(contained(HATCHET, 0x0F44, 1, BAG))
            asyncio.get_running_loop().create_task(self.combat())
        else:
            self.send(equip(HATCHET, 0x0F44, 0x02))
        self.send(contained(RUNEBOOK, 0x22C5, 1, BACKPACK))     # the way out and home
        if self.library:
            self.send(contained(0x44ADB0FF, 0x0F7A, 10, BACKPACK))   # black pearl: charges spend none
        for pch in self.pouch_hue:                              # the trapped pouches (hue 38)
            self.send(self.pouch_pkt(pch))
        for s, (g, n) in self.potions.items():                 # flee_aid: cure, heal, refresh potions
            self.send(contained(s, g, n, BACKPACK, x=60 + 10 * list(POTIONS).index(s)))
        if self.scenario == "break":                            # logs carried from an earlier trip
            self.send(self.stack_pkt(self.add_wood(LOG_G, INITIAL_LOGS, BACKPACK)))
        for g, n in self.carried:                              # convert_stacks: logs of other woods carried
            self.send(self.stack_pkt(self.add_wood(g, n, BACKPACK)))
        self.send(hits_pkt(self.hits))                          # our hits: the runner reads damage from them
        self.send_room_items()
        self.send_horse()
        await writer.drain()
        buf = bytearray()
        while True:
            d = await reader.read(65536)
            if not d:
                break
            buf += bytes(b ^ C2S_KEY for b in d)
            while buf:
                n = packet_length(bytes(buf), overrides=C2S_OVERRIDES)
                if n <= 0 or n > len(buf):
                    break
                self.on_packet(bytes(buf[:n]))
                del buf[:n]
            await writer.drain()

    async def combat(self):
        """Skirmish: the hart and the player trade blows (0x2F both ways) every second;
        the attacker, once there, swings at the agent while within 3 tiles."""
        while not self.writer.is_closing():
            await asyncio.sleep(1.0)
            self.send(swing(FIGHTER, OTHER))
            self.send(swing(OTHER, FIGHTER))
            if self.attacker_pos is not None and self.cheb(self.attacker_pos) <= 3:
                self.attacker_swings += 1
                self.send(swing(ATTACKER, SELF))
            await self.writer.drain()


def write_spot(path, trees=(GOOD_TREE, DRY_TREE), more=(), **extra):
    """The simulator's spot 'sim' (lumber_opt spot format, a --spots file): its trees around START, no
    hostile player actions there (extra fields override); `more`: further spots (whole dicts). The area's
    edge lies beyond AT_GROVE from the home landing, so a trip recalls out (our runebook's 'Sim Woods').
    The common knowledge is the committed loops/lumber.json."""
    spot = {"id": "sim", "name": "simulated trees", "facet": 0,
            "area": {"center": list(START), "radius": 12}, "trees": list(trees), "pvp": False, **extra}
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"spots": [spot, *more]}, f)


def write_witcher(path):
    """A Witcher table with the one rune the simulated library holds (286)."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"runes": [{"id": "286", "name": "Midlands Ruins 1 (South)", "x": RUNE_POS[0], "y": RUNE_POS[1]}]}, f)


def write_libraries(path):
    """The home rune library (places.py format): one tome at TOME_POS, its row 10 '286 - Midlands Ruins 1
    (South)' landing at RUNE_POS (the captured tome page; its other rows are left out)."""
    doc = {"libraries": [{"id": "simhome", "name": "Sim Rune Library", "facet": 0, "stand": list(LIB_START),
                          "use_range": 2, "access": "guild",
                          "tomes": [{"serial": f"0x{TOME:08X}", "title": "276-301", "pos": [*TOME_POS, 0],
                                     "rows": [{"name": "286 - Midlands Ruins 1 (South)",
                                               "x": RUNE_POS[0], "y": RUNE_POS[1]}]}]}]}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f)


def write_homes(path, stockpile=False):
    """The test character's home (harness/home.py format): the simulated landing, home library, room
    (Logan Wolf's, as live) and chest; with `stockpile`, the room's Resource Stockpile."""
    home = {"library": "simhome", "landing": [*HOME_RUNE_POS, 0], "facet": 0,
            "room": {"owner": "logan", "facet": ROOM_FACET, "arrival": [*ROOM_ARRIVAL, 1], "exit": "steward"},
            "chest": {"serial": f"0x{CHEST:08X}", "name": "paragon chest (drake)", "pos": [*CHEST_POS, 2]}}
    if stockpile:
        home["stockpile"] = {"serial": f"0x{STOCKPILE:08X}", "pos": [*STOCKPILE_POS, 2]}
    doc = {"homes": {CHAR_NAME: home}}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f)


def write_world_files(tmp, trees=(GOOD_TREE, DRY_TREE), spot_extra=None, stockpile=False, more_spots=(),
                      routes=None) -> dict:
    """The runner's simulated data files in `tmp`: spots, Witcher table, rune libraries, homes, memory db;
    `routes` ({lumber_opt.route_key: tiles}) pre-seeds the db's landing-route cache (the planner's route
    check plans on the real map otherwise, where the simulated landings mean nothing)."""
    paths = {n: os.path.join(tmp, f"{n}.json") for n in ("spots", "witcher", "libraries", "homes")}
    paths["db"] = os.path.join(tmp, "harness.db")
    write_spot(paths["spots"], trees, more_spots, **(spot_extra or {}))
    write_witcher(paths["witcher"])
    write_libraries(paths["libraries"])
    write_homes(paths["homes"], stockpile)
    if routes:
        mem = memory.Memory(paths["db"])
        lumber_opt.save_landing_routes(mem, routes)
        mem.close()
    return paths


def runner_files(paths) -> list:
    return ["--spot", "sim", "--spots", paths["spots"], "--witcher", paths["witcher"],
            "--libraries", paths["libraries"], "--homes", paths["homes"], "--memory", paths["db"]]


async def main():
    os.makedirs(LOGDIR, exist_ok=True)
    for f in os.listdir(LOGDIR):
        if os.path.isfile(os.path.join(LOGDIR, f)):      # the other scenarios keep subdirectories
            os.remove(os.path.join(LOGDIR, f))
    tmp = tempfile.mkdtemp()
    paths = write_world_files(tmp)
    # captcha mode `auto` (the viz toggle; the default is `human`): trip 1's readable captcha is
    # the solver's, trip 2's unreadable one falls back to the human wait
    store = memory.Memory(paths["db"])
    store.set_captcha_mode("auto")
    store.close()

    world = World()
    server = await asyncio.start_server(world.handle, "127.0.0.1", UPSTREAM_PORT)
    proxy = subprocess.Popen(
        [PY, f"{ROOT}/harness/proxy.py", "--listen-port", str(PROXY_PORT),
         "--upstream-host", "127.0.0.1", "--upstream-port", str(UPSTREAM_PORT),
         "--control-port", str(CONTROL_PORT), "--state-port", str(STATE_PORT), "--logdir", LOGDIR,
         "--memory-db", paths["db"]],
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        await asyncio.sleep(1.0)
        reader, writer = await asyncio.open_connection("127.0.0.1", PROXY_PORT)
        writer.write(bytes.fromhex("ef0000000c"))
        await writer.drain()

        async def drain_client():
            while await reader.read(65536):
                pass

        async def human():
            """Answers the fallback (unreadable) captcha through the client. The
            trip-1 captcha is solver-readable: the agent must answer it itself."""
            while True:
                await asyncio.sleep(0.2)
                if world.captcha_open is not None and not world.captcha_auto:
                    await asyncio.sleep(1.0)
                    pkt = actions.gump_response(world.captcha_open, 0x00000001, CAPTCHA_SUBMIT,
                                                text_entries=[(2, "326")])
                    writer.write(bytes(b ^ C2S_KEY for b in pkt))
                    await writer.drain()
                    await asyncio.sleep(0.5)

        drainer = asyncio.create_task(drain_client())
        solver = asyncio.create_task(human())
        held = {}

        async def overseer():
            """Gives the all-clear (acks the speech juncture) HOLD_S after it appears and
            stands the staff alarm down (acks gm_suspected) GM_EXTRA_S after that."""
            store = memory.Memory(paths["db"])
            while True:
                await asyncio.sleep(0.2)
                row = store.con.execute("SELECT id, t FROM junctures WHERE kind='speech_nearby' "
                                        "AND acked_t IS NULL").fetchone()
                if row:
                    held["t"] = row[1]
                    await asyncio.sleep(HOLD_S)
                    held["ack"] = time.time()
                    store.juncture_ack(row[0])
                    await asyncio.sleep(GM_EXTRA_S)
                    for (gid,) in store.con.execute("SELECT id FROM junctures WHERE kind='gm_suspected' "
                                                    "AND acked_t IS NULL").fetchall():
                        held["gm_ack"] = time.time()
                        store.juncture_ack(gid)
        clearer = asyncio.create_task(overseer())
        await asyncio.sleep(0.5)
        runner = await asyncio.create_subprocess_exec(
            PY, f"{ROOT}/harness/loop_lumber.py", "--trips", "2", "--logs-per-trip", "100",
            "--regrow-min", "0.05",
            "--control-port", str(CONTROL_PORT), "--state-port", str(STATE_PORT),
            *runner_files(paths),
            "--human", "normal", "--seed", "11", "--human-fast", "0.25", "--timeout", "300", "--quiet",
            # --no-map: walk memory only, and the town wall (21 tiles) is unknown; per-plan route
            # noise can send the agent along it, learning one denied edge per try (up to 3 a tile)
            "--no-map", "--max-blocked", "80",
            "--triage-url", "",               # no Laya: verdicts would depend on a running laya-serve
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(runner.communicate(), timeout=360)
        text = out.decode(errors="replace")
        print("---- runner output ----\n" + text + "-----------------------")
        bad_intents = [state_req({"op": "intent", "intent": b})
                       for b in ({"text": ""}, {"text": 5}, ["not", "a", "dict"], {"text": "x" * 201})]
        live_hist = state_req({"op": "state", "since": 0, "snapshot": False}).get("intents") or []
        solver.cancel()
        clearer.cancel()
        writer.close()
        drainer.cancel()

        await asyncio.sleep(1.0)                        # proxy memory writer: batched commits
        store = memory.Memory(paths["db"])
        rows = store.episodes("lumber")
        dry_node = store.harvest_node(0, DRY_TREE["x"], DRY_TREE["y"], DRY_TREE["z"]) or {}
        good_node = store.harvest_node(0, GOOD_TREE["x"], GOOD_TREE["y"], GOOD_TREE["z"]) or {}
        walked = store.walk_memory(0)
        speech = [p for p in world.c2s if p[0] == 0xAD]
        log = [json.loads(l) for f in os.listdir(LOGDIR) if f.endswith(".jsonl")
               for l in open(os.path.join(LOGDIR, f), encoding="utf-8")]
        srcs = {e.get("src") for e in log if e.get("dir") == "c2s"}
        b1_client = [e for e in log if e.get("dir") == "c2s" and e.get("id") == "0xB1" and e.get("src") == "client"]
        b1_agent = [e for e in log if e.get("dir") == "c2s" and e.get("id") == "0xB1" and e.get("src") == "agent"]

        check("runner exited 0", runner.returncode == 0, str(runner.returncode))
        check("two trips completed", "loop complete: 2 trip(s)" in text)
        check("two captchas shown (trip 1 readable, trip 2 unreadable), two answers",
              world.captcha_shown == 2 and world.captcha_answers == 2,
              f"{world.captcha_shown} shown, {world.captcha_answers} answered")
        check("trip 1 captcha auto-solved by the agent: stock 0xB1, correct digits, random submit id",
              world.auto_answers == ["326"]
              and "captcha answered '326' (auto-solved from the layout)" in text
              and len([e for e in b1_agent]) >= 1, f"{world.auto_answers}")
        check("the client answers only the unreadable (fallback) captcha",
              len(b1_client) == 1, str(len(b1_client)))
        check("trip 2 captcha fell back to pause + beep",
              text.count("CAPTCHA up: agent paused") == 1
              and sum(r.get("captchas", 0) for r in rows) == 2, str([r.get("captchas") for r in rows]))
        caps = [j for j in store.junctures() if j["kind"] == "captcha"]
        check("captcha juncture only for the fallback (urgent), acked once solved",
              len(caps) == 1 and caps[0]["severity"] == "urgent" and caps[0]["acked_t"] is not None, str(caps))
        alarms = [j for j in store.junctures() if j["kind"] in ("threat", "death")]
        thefts = [j for j in store.junctures() if j["kind"] == "theft_suspected"]
        tev = [e for e in store.job_events("lumber") if e["kind"] == "theft"]
        check("no false threat/death alarms (conversion and storing declared to the ledger)",
              not alarms, str(alarms)[:300])
        check("the pickpocket: one theft_suspected juncture + one theft job event with the stolen amount",
              len(thefts) == 1 and len(tev) == 1 and tev[0]["data"]["amount"] == STOLEN,
              f"{len(thefts)} junctures, {[e['data'].get('amount') for e in tev]}")
        check("the loop carried on after the theft (both trips complete)", "loop complete: 2 trip(s)" in text)
        holds = [j for j in store.junctures() if j["kind"] == "speech_nearby"]
        sp = (holds[0]["data"].get("speakers") or [{}])[0] if holds else {}
        check("a player speaking mid-harvest: one urgent speech_nearby juncture holding the job, with who and what",
              len(holds) == 1 and holds[0]["severity"] == "urgent" and holds[0]["data"].get("hold") is True
              and sp.get("serial") == f"0x{PASSERBY:08X}" and sp.get("text") == GREETING
              and "player flag 0x20" in sp.get("evidence", []), str(holds))
        quiet = [p.hex() for p, t in zip(world.c2s, world.c2s_t)
                 if held.get("t") is not None and held["t"] + 0.3 < t < held.get("gm_ack", 0)]
        check(f"nothing sent while held, even after the speech all-clear ({HOLD_S:.0f} s) while the GM "
              f"suspicion stayed open ({GM_EXTRA_S:.0f} s more)",
              "gm_ack" in held and world.spoke_at is not None and held["t"] - world.spoke_at < 3.0 and quiet == [],
              f"held {held} spoke {world.spoke_at} sent {quiet[:5]}")
        gms = [j for j in store.junctures() if j["kind"] == "gm_suspected"]
        check("the hidden speaker raised one urgent gm_suspected juncture from the runner (not on screen)",
              len(gms) == 1 and gms[0]["source"] == "lumber" and gms[0]["severity"] == "urgent"
              and gms[0]["data"]["speakers"][0]["serial"] == f"0x{HIDDEN:08X}"
              and "not on screen" in gms[0]["summary"], str(gms))
        check("resumed after both all-clears and counted the pause",
              "all-clear after" in text and sum(r.get("speech_holds", 0) for r in rows) == 1
              and sum(r.get("speech_wait_s", 0) for r in rows) >= HOLD_S + GM_EXTRA_S,
              str([r.get("speech_wait_s") for r in rows]))
        check("trip rows carry logs by wood type; the pack's logs at conversion (after the theft)",
              all("ordinary" in (r.get("woods") or {}) for r in rows)
              and sum(sum(r["woods"].values()) for r in rows) == sum(r["logs"] for r in rows) - STOLEN,
              str([(r.get("logs"), r.get("woods")) for r in rows]))
        check("decoy gumps were shown and never answered",
              len(world.decoys) >= 4 and world.decoy_replies == 0, f"{len(world.decoys)} decoys")
        b1_gate = [e for e in b1_agent if int(e["hex"][6:14], 16) in world.gate_gumps]
        b1_book = [e for e in b1_agent if int(e["hex"][6:14], 16) in world.book_gumps]
        b1_room = [e for e in b1_agent if int(e["hex"][6:14], 16) in world.room_serials]
        check("agent gump replies = the auto-solved captcha + the moongate prompts it closed + our runebook "
              "(read once and closed, one recall out a trip) + the room menus",
              len(b1_agent) - len(b1_gate) - len(b1_book) - len(b1_room) == 1 and len(b1_book) == 3,
              f"{len(b1_agent)} agent replies, {len(b1_gate)} to gates, {len(b1_book)} book, {len(b1_room)} room")
        closes = [e for e in log if e.get("ev") == "gump_close_client"]
        check("every moongate prompt the route opened was closed once, with button 0 (the stock right-click), "
              "and the client's copy closed by the proxy",
              len(world.gate_gumps) >= 2 and all(v == [0] for v in world.gate_gumps.values())
              and len(b1_gate) == len(world.gate_gumps) and len(closes) == len(b1_agent),
              f"{world.gate_gumps} client closes {len(closes)}")
        check("each trip left the room by its door (Exit to House Steward) and came back through the steward "
              "(Room, Visit Other Rooms, Logan Wolf's row); never End Rental Contract or Expand; ends in the room",
              [k for k, _ in world.room_log] == ["exit", "enter", "exit", "enter"]
              and world.room_presses == [("door", 6), ("steward_no_room", 2), ("visit_list", 100)] * 2
              and world.keeper_far == 0 and world.facet == ROOM_FACET and "waiting in the rental room" in text,
              f"{world.room_log} {world.room_presses} far {world.keeper_far} facet {world.facet}")
        check("the steward was found by one single click on trip 1 (his label), known on trip 2; never "
              "right-clicked (his context menu stays up on the client's screen)",
              text.count("house steward search") == 1 and world.steward_clicks == 1 and world.steward_menus == 0,
              f"clicks {world.steward_clicks} menus {world.steward_menus}")
        check("out by our runebook's 'Sim Woods' rune (the landing nearest the grove), home on foot (within "
              "NEAR_LANDING of the landing): no library or home recall",
              world.recalls_book == [BOOK_RUNE_POS, BOOK_RUNE_POS] and world.recalls_home == []
              and world.recalls_out == [], f"{world.recalls_book} {world.recalls_home} {world.recalls_out}")
        trav = [e["data"] for e in store.job_events("lumber") if e["kind"] == "travel"]
        check("each trip's travel leg names the landing: our book's rune, its tile",
              [(d["leg"], (d.get("landing") or {}).get("source"), d["landing"].get("name"), d["book"])
               for d in trav] == [("out", "book", "Sim Woods", f"0x{RUNEBOOK:08X}")] * 2
              and all((d["landing"]["x"], d["landing"]["y"]) == BOOK_RUNE_POS and d["ok"] for d in trav),
              str(trav)[:600])
        check("every harvested log not stolen ended in the home chest as boards; no drop refused",
              world.chest_stack is not None and world.harvested == 2 * 3 * LOGS_PER_SUCCESS
              and world.chest_stack[1] == world.harvested - world.stolen and world.drops_refused == 0,
              f"chest {world.chest_stack}, harvested {world.harvested}, stolen {world.stolen}, "
              f"refused {world.drops_refused}")
        check("nothing left in the backpack", world.logs == 0 and not world.pack_boards)
        stash_to = {d for _, _, d in world.stashed}
        check("every chop's logs were dragged into a trapped pouch (one pouch a trip), never a log converted "
              "inside a live one",
              world.stashed and stash_to == {POUCHES[0], POUCHES[1]}
              and sum(n for _, n, _ in world.stashed) >= world.harvested and world.converts_refused == 0,
              f"{world.stashed} refused {world.converts_refused}")
        check("in the room each trip set its own pouch off (a double-click: a hit, no alarm), opened it, converted "
              "there; the spent pouch went into the chest",
              [(p, by) for p, _, by in world.pops] == [(POUCHES[0], "us"), (POUCHES[1], "us")]
              and world.chest_items == [(POUCHES[0], 0), (POUCHES[1], 0)]
              and text.count("set off our trapped pouch") == 2 and text.count("not an attack") >= 1
              and not [e for e in store.job_events("lumber") if e["kind"] == "thief"],
              f"{world.pops} chest {world.chest_items}")
        check("each trip row counts the trapped pouch it used", [(r.get("supplies") or {}).get("trapped_pouches")
                                                                 for r in rows] == [1, 1], str([r.get("supplies")
                                                                                               for r in rows]))
        dry_stand, good_stand = tuple(DRY_TREE["stand"]), tuple(GOOD_TREE["stand"])
        check("every chop cursor answered with ourselves (Smart Harvest): our serial, tile and body, the stock "
              "client's bytes; never a location",
              len(world.self_targets) >= 14 and all(ok for _, ok in world.self_targets)
              and world.location_answers == 0, f"{world.self_targets[:4]} locations {world.location_answers}")
        check("'nothing nearby' at the dry tree once per trip, and at the good tree when its wood ran out; each "
              "time the runner moved on (at the dry stand per visit: the travel lockout after the recall out, "
              "then one self-target)",
              sorted(world.nothing_near_at) == sorted([dry_stand, good_stand, good_stand, dry_stand])
              and world.lockouts == 2 and sum(1 for p, _ in world.self_targets if p == dry_stand) == 2 + 2
              and {p for p, _ in world.self_targets} == {dry_stand, good_stand},
              f"{world.nothing_near_at} {[p for p, _ in world.self_targets]}")
        att = store.con.execute("SELECT x, y, outcome, amount FROM harvest_attempts ORDER BY t").fetchall()
        chops = [a for a in att if a[2] in ("success", "fail")]
        marks = {(a[0], a[1]) for a in att if a[2] == "nothing_near"}
        check("harvest memory (store): every attempt on the stand tile (the server doesn't say which tree), the "
              "trees by a 'nothing nearby' marked out of wood, nothing recorded per tree",
              chops and {(a[0], a[1]) for a in chops} == {good_stand} and sum(a[3] for a in chops) == world.harvested
              and sum(1 for a in chops if a[2] == "success") == 6
              and marks == {(DRY_TREE["x"], DRY_TREE["y"]), (GOOD_TREE["x"], GOOD_TREE["y"])}
              and dry_node.get("depleted_at") is not None and good_node.get("depleted_at") is not None
              and not any(a[2] in ("depleted", "not_tree") for a in att), f"{att[:12]} {dry_node} {good_node}")
        stands = [e["data"] for e in store.job_events("lumber") if e["kind"] == "stand"]
        by_tile = {tuple(s["stand"][:2]): s for s in stands}
        want = {dry_stand: (0, [0, -1, 1, DRY_TREE["graphic"]]), good_stand: (GOOD_VISIT, [1, 0, 1, GOOD_TREE["graphic"]])}
        check("one `stand` job event per stand (the reach measurement): tile, trees around it with offsets, "
              "attempts, logs, the facing per chop, ended by 'nothing nearby'",
              sorted((tuple(s["stand"][:2]), s["trip"]) for s in stands)
              == sorted([(dry_stand, 1), (good_stand, 1), (good_stand, 2), (dry_stand, 2)])
              and all(s["end"] == "nothing_near" and s["range"] == 1 and len(s["faced"]) == s["attempts"]
                      and s["s"] >= 0 and s["attempts"] == want[tuple(s["stand"][:2])][0]
                      and want[tuple(s["stand"][:2])][1] in s["trees"] for s in stands)
              and sum(s["logs"] for s in stands) == sum(r["logs"] for r in rows) and len(by_tile) == 2,
              str(stands)[:900])
        check("walk memory (store): the proxy recorded the agent's walks",
              len(walked.edges) >= 20, str(walked.stats()))
        check("open-door requests only next to a door, like the client's auto-open (never at plain walls)",
              world.open_door_reqs == world.doors_opened, f"{world.open_door_reqs} requests, "
              f"{world.doors_opened} opened")
        check("the town door opened on each of the 2 crossings (the walk home each trip)",
              world.doors_opened >= 2, str(world.doors_opened))
        check("like a player, the agent opened the backpack once before dragging the logs, each trip's spent "
              "pouch once to convert in it, and the chest once a trip (leaving the room closes it)",
              [c for c in world.containers_opened if c != BACKPACK] == [POUCHES[0], POUCHES[1]]
              and world.containers_opened.count(BACKPACK) == 1
              and text.count(f"opening container 0x{BACKPACK:08X}") == 1 and world.chest_opens == 2,
              f"{world.containers_opened} chest opens {world.chest_opens}")
        check("the only speech is 'room' by the steward, once per trip (no 'bank')",
              speech == [actions.say_unicode("room")] * 2, str([p.hex() for p in speech]))
        check("two episode rows with logs and stored boards, none stockpiled (this home has only the chest); a dry "
              "trip asked about a library hop first (the `hop_home` phase, docs/LUMBER_LOOP.md §6: no other spot here)",
              len(rows) == 2 and all(r.get("logs", 0) >= 6 and r.get("stored", 0) >= 6 and "stockpiled" not in r
                                     and set(r["phases_s"]) - ({"hop_home"} if r.get("dry") else set())
                                     == {"harvest", "to_room", "convert", "store"}
                                     for r in rows), str(rows))
        check("trip rows say where, how it ended and with what: spot sim, stored, the worn iron hatchet, "
              "the walk out and the chopping inside the harvest time, nothing carried at the end",
              all(r.get("spot") == "sim" and r.get("outcome") == "stored" and r.get("why") is None
                  and (r.get("hatchet") or {}).get("material") == "iron" and r["hatchet"].get("worn")
                  and 0 < r["walk_out_s"] < r["phases_s"]["harvest"]
                  and 0 < r["chop_s"] < r["phases_s"]["harvest"] - r["walk_out_s"]
                  and r.get("carried_end") == {"logs": 0, "boards": 0} for r in rows),
              str([{k: r.get(k) for k in ("spot", "outcome", "hatchet", "walk_out_s", "chop_s", "phases_s",
                                          "carried_end")} for r in rows]))
        check("every C2S packet came from the client or the agent (none from the proxy)",
              srcs <= {"client", "agent"}, str(srcs))
        intents = [e["intent"] for e in log if e.get("ev") == "agent_intent"]
        kinds = [i["kind"] for i in intents if i]
        check("malformed intents rejected by the proxy (and not recorded)",
              all(not r["ok"] for r in bad_intents) and all(i and i.get("text") for i in intents),
              str(bad_intents))
        phase = ["leave_room", "recall_out", "to_tree", "chop", "to_room", "convert", "store", "trip_done"]
        for n in (1, 2):
            seq = [i["kind"] for i in intents if i and i.get("trip") == n]
            check(f"trip {n}: intents follow the loop's phases in order",
                  is_subsequence(phase, seq), str(dedupe(seq)))
        cap = [k for k, i in enumerate(intents) if i and i["kind"] == "captcha"]
        check("captcha intent shown per captcha, then the previous intent restored",
              len(cap) == 2 and all(0 < k < len(intents) - 1
                                    and intents[k + 1]["text"] == intents[k - 1]["text"] for k in cap), str(cap))
        check("heading/chopping intents carry the tree as target",
              all(i.get("target") for i in intents if i and i["kind"] in ("to_tree", "chop")))
        check("last intent: finished, lumber loop, 2 trips",
              intents and intents[-1]["kind"] == "done" and intents[-1]["loop"] == "lumber"
              and intents[-1]["trips"] == 2, str(intents[-1:]))
        tag = next(f for f in os.listdir(LOGDIR) if f.endswith(".jsonl"))[len("session_"):-len(".jsonl")]
        drv = viz_feed.ReplayDriver(tag, LOGDIR)
        drv.run_to_end()
        replayed = drv.query(0)
        got = replayed.get("intent") or {}
        check("a replay of the capture reproduces the final intent (viz replay)",
              {k: got.get(k) for k in ("kind", "text", "trips")}
              == {k: intents[-1].get(k) for k in ("kind", "text", "trips")}, str(got))
        key = lambda i: tuple(json.dumps(i.get(k)) for k in ("kind", "target", "loop", "trip"))  # noqa: E731
        check("intent history: repeated updates of one step merged (one entry per step)",
              0 < len(live_hist) < len(intents)
              and all(key(a) != key(b) for a, b in zip(live_hist, live_hist[1:])),
              f"{len(live_hist)} entries for {len(intents)} updates")
        check("intent history: every past step closed with until >= since, the current one open",
              all(h.get("until", -1) >= h["since"] for h in live_hist[:-1]) and "until" not in live_hist[-1]
              and live_hist[-1]["text"] == intents[-1]["text"], str(live_hist[-2:]))
        check("intent history: merged chop entry shows the latest count and keeps its start",
              any(h["kind"] == "chop" and h["text"].endswith("logs)") and h["until"] > h["since"]
                  for h in live_hist[:-1]))
        check("intent history reproduced by the replay",
              [(h["kind"], h["text"]) for h in replayed.get("intents", [])]
              == [(h["kind"], h["text"]) for h in live_hist], str(len(replayed.get("intents", []))))
    finally:
        proxy.terminate()
        server.close()


async def run_scenario(world, tag, port_base, trees, runner_args, budget=None, spot_extra=None, during=None,
                       more_spots=(), routes=None):
    """The real proxy in front of `world` on private ports port_base..+3 (logdir
    LOGDIR/<tag>, an optional pre-written agent gate file), then the runner with
    runner_args; `during(db)`, a coroutine function, runs alongside it (a test
    overseer). Returns (runner output, exit code, memory store, capture rows)."""
    logdir = os.path.join(LOGDIR, tag)
    os.makedirs(logdir, exist_ok=True)
    for f in os.listdir(logdir):
        os.remove(os.path.join(logdir, f))
    if budget is not None:
        with open(os.path.join(logdir, "agent_budget.json"), "w", encoding="utf-8") as f:
            json.dump(budget, f)
    paths = write_world_files(tempfile.mkdtemp(), trees, spot_extra, stockpile=world.stockpile is not None,
                              more_spots=more_spots, routes=routes)
    db = paths["db"]
    proxy_port, upstream, control, state = (port_base + i for i in range(4))
    server = await asyncio.start_server(world.handle, "127.0.0.1", upstream)
    proxy = subprocess.Popen(
        [PY, f"{ROOT}/harness/proxy.py", "--listen-port", str(proxy_port),
         "--upstream-host", "127.0.0.1", "--upstream-port", str(upstream),
         "--control-port", str(control), "--state-port", str(state), "--logdir", logdir,
         "--memory-db", db],
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        await asyncio.sleep(1.0)
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
        writer.write(bytes.fromhex("ef0000000c"))
        await writer.drain()

        async def drain_client():
            while await reader.read(65536):
                pass
        drainer = asyncio.create_task(drain_client())
        await asyncio.sleep(0.5)
        runner = await asyncio.create_subprocess_exec(
            PY, f"{ROOT}/harness/loop_lumber.py", "--control-port", str(control), "--state-port", str(state),
            *runner_files(paths), "--timeout", "300",
            "--quiet", "--no-map", "--max-blocked", "80",
            "--triage-url", "", *runner_args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        side = asyncio.create_task(during(db)) if during is not None else None
        out, _ = await asyncio.wait_for(runner.communicate(), timeout=360)
        if side is not None:
            side.cancel()
        text = out.decode(errors="replace")
        print(f"---- runner output ({tag}) ----\n" + text + "-----------------------")
        writer.close()
        drainer.cancel()
        await asyncio.sleep(1.0)                        # proxy memory writer: batched commits
        rows = [json.loads(line) for f in os.listdir(logdir) if f.endswith(".jsonl")
                for line in open(os.path.join(logdir, f), encoding="utf-8")]
        return text, runner.returncode, memory.Memory(db), rows
    finally:
        proxy.terminate()
        server.close()


async def skirmish():
    """LUMBER_LOOP.md §13: the hatchet in a bag in the pack; 'a great hart' in war mode
    4 tiles from the tree, fighting a player (knowledge #89); a creature that goes for
    the agent (escape ESCAPE_RUN tiles, then the next tree out of its reach); the same creature
    hunting it down at that tree (one we already ran from: no second escape, run RECALL_GAP away,
    recall home, no conversion; live 2026-10-05 an air dragon followed two escapes and killed Dan)."""
    print("\n== skirmish: hatchet in a bag, a hart fighting a player, a creature that goes for us ==")
    world = World("skirmish")
    text, code, store, _ = await run_scenario(world, "skirmish", 12680, [GOOD_TREE, FAR_TREE],
                                              ["--trips", "1", "--logs-per-trip", "100", "--human", "off"])
    dclicks = [int.from_bytes(p[1:5], "big") for p in world.c2s if p[0] == 0x06]
    check("hatchet in a bag in the pack: backpack, then the bag opened (once each), before the first hatchet use",
          world.containers_opened[:2] == [BACKPACK, BAG] and HATCHET in dclicks
          and dclicks.index(BACKPACK) < dclicks.index(BAG) < dclicks.index(HATCHET), str(dclicks[:6]))
    threat_js = [j for j in store.junctures() if j["kind"] == "threat"]
    acts = [j["data"].get("action") for j in threat_js if "threats" in j["data"]]
    check("threat junctures (urgent): escape, recall (it followed us after the escape), then 'Recalled away'",
          acts == ["escape", "recall"] and "Recalled away" in threat_js[-1]["summary"]
          and all(j["severity"] == "urgent" for j in threat_js),
          str([(j["summary"], j["data"].get("action")) for j in threat_js]))
    with_threats = [j for j in threat_js if "threats" in j["data"]]
    hart = [next((t for t in j["data"]["threats"] if t["serial"] == FIGHTER), {}) for j in with_threats]
    check("every threat was the attacker; the hart fighting the player (in flee range, war mode) wasn't a "
          "threat (a passive body in war mode is fighting someone else, threats.py)",
          with_threats and all(j["data"]["threats"][0]["serial"] == ATTACKER for j in with_threats)
          and hart[0].get("action") in ("watch", "ignore") and not hart[0].get("hostile")
          and hart[0].get("distance", 99) <= hart[0].get("flee_radius", 0), str(hart[:1]))
    m = re.search(r"escaped to \((\d+), (\d+)\)", text)
    to = (int(m[1]), int(m[2])) if m else None
    check("first escape: ran from the attacker, away from it, at least ESCAPE_RUN (20) tiles (a few steps don't "
          "break aggro; user 2026-10-05)",
          to is not None and to[0] < GOOD_TREE["stand"][0]
          and max(abs(to[0] - ATTACKER_POS[0]), abs(to[1] - ATTACKER_POS[1])) >= 19, str(to))
    far = [s for s in stand_events(store) if s["anchor"] == [FAR_TREE["x"], FAR_TREE["y"]]]
    check("resumed at a stand by the next tree out of the attacker's reach and harvested there",
          world.far_attempts >= 2 and sum(s["successes"] for s in far) >= 1, f"{world.far_attempts} attempts, {far}")
    check("the attacker followed us after the escape: the run stopped (exit 1)",
          code == 1 and "followed us after an escape" in text and world.attacker_swings >= 1,
          f"exit {code}, {world.attacker_swings} swings")
    check("a creature at our heels: no 10 s log conversion next to it (live 2026-10-03: 85 -> 40 hits while "
          "converting); first a run RECALL_GAP away (user 2026-10-05: a few steps don't break aggro), then the "
          "recall home; there, into the room, the logs converted and stored (Seer6 2026-10-05: they stayed logs)",
          world.logs == 0 and world.harvested > 0 and world.chest_stack == (world.chest_stack or (0, 0))[:1]
          + (world.harvested,) and [k for k, _ in world.room_log] == ["exit", "enter"]
          and world.recalls_home == [HOME_RUNE_POS] and "out of reach before the recall" in text
          and text.index("out of reach before the recall") < text.index("recalling out")
          < text.index("into the rental room to convert and store")
          and "converting the carried logs before stopping" not in text,
          f"logs {world.logs}, chest {world.chest_stack}, harvested {world.harvested}, home {world.recalls_home} "
          f"room {world.room_log}")
    eps = store.episodes("lumber")
    check("the stopped trip still left its episode row: aborted, why, the logs it got, now stored; the hatchet "
          "from the bag",
          len(eps) == 1 and eps[0].get("outcome") == "aborted" and "followed us" in (eps[0].get("why") or "")
          and eps[0].get("logs") == world.harvested and eps[0].get("stored") == world.harvested
          and eps[0].get("carried_end") == {"logs": 0, "boards": 0}
          and (eps[0].get("hatchet") or {}).get("worn") is False
          and {"harvest", "to_room", "convert", "store"} <= set(eps[0]["phases_s"]),
          str(eps)[:600])
    store.close()


async def break_due():
    """docs/OVERSEER.md break_due: the agent gate announces a break mid-harvest; the
    trip ends early in the rental room (carried and new logs stored in the chest as boards), exit 0."""
    print("\n== break due: stop harvesting, convert, store, exit 0 ==")
    world = World("break")
    budget = {"day": datetime.date.today().isoformat(), "active_today_s": 0.0,
              "since_break_s": 7200.0 - BREAK_AFTER_S, "next_break_after_s": 7200.0,
              "break_until": None, "break_due_at": None, "last_active": None,
              "paused": False, "killed": False}
    text, code, store, _ = await run_scenario(world, "break", 12690, [GOOD_TREE, DRY_TREE],
                                              ["--trips", "2", "--logs-per-trip", "100", "--human", "off"],
                                              budget=budget)
    eps = store.episodes("lumber")
    check("one trip, 'break due: boards stored', exit 0 (for ctl break) in the room, episode row marked break_due",
          code == 0 and "break due: boards stored" in text and "loop complete" not in text
          and len(eps) == 1 and eps[0].get("break_due") is True and world.facet == ROOM_FACET
          and set(eps[0]["phases_s"]) == {"harvest", "to_room", "convert", "store"},
          f"exit {code}, {len(eps)} episodes, facet {world.facet}")
    check("the harvest stopped early (the good tree still had wood)",
          "break due: stopping the harvest" in text and world.good_left > 0, str(world.good_left))
    check("carried and new logs became boards in the chest; nothing left in the pack",
          world.chest_stack is not None and world.chest_stack[1] == INITIAL_LOGS + world.harvested
          and world.logs == 0 and not world.pack_boards and world.room_log[-1][0] == "enter",
          f"chest {world.chest_stack}, harvested {world.harvested}")
    check("no threat juncture", not [j for j in store.junctures() if j["kind"] == "threat"])
    store.close()


async def library():
    """docs/research/WORLD_LOCATIONS.md, LUMBER_LOOP.md §12.5: a grove whose nearest landing is the home
    rune library's rune 286. Each trip: out of the room, walk to the library tome, recall to rune 286 with
    one of its charges, wait out the travel lockout, chop (a player chops nearby), recall home with the
    runebook's default rune (trip 1's first cast is disturbed), into the room through the steward,
    convert, store. The travel legs (with the landing) and supplies are recorded."""
    print("\n== library: out by the home library's tome, home by our runebook, into the room; twice ==")
    world = World("library")
    spot = LIB_SPOT
    text, code, store, _ = await run_scenario(world, "library", 12700, [LIB_TREE],
                                              ["--trips", "2", "--logs-per-trip", "100", "--human", "off",
                                               "--regrow-min", "0.05"], spot_extra=spot)
    eps = store.episodes("lumber")
    trav = [e for e in store.job_events("lumber") if e["kind"] == "travel"]
    check("two trips stored, exit 0", code == 0 and "loop complete: 2 trip(s)" in text
          and [e.get("outcome") for e in eps] == ["stored", "stored"], f"exit {code}, {[e.get('outcome') for e in eps]}")
    check("each trip recalled out from the tome's row for rune 286 (gem 110), standing within its 2 tiles; "
          "never by our book's 'Sim Woods' (farther from the grove)",
          world.recalls_out == [RUNE_POS, RUNE_POS] and world.tome_far == 0 and world.recalls_book == [],
          f"{world.recalls_out} far {world.tome_far} book {world.recalls_book}")
    check("each trip recalled home with the runebook's default rune (a charge), then went into the room",
          world.recalls_home == [HOME_RUNE_POS, HOME_RUNE_POS]
          and [k for k, _ in world.room_log] == ["exit", "enter", "exit", "enter"],
          f"{world.recalls_home} room {world.room_log}")
    check("everything harvested ended in the chest", world.harvested > 0 and world.chest_stack is not None
          and world.chest_stack[1] == world.harvested and world.logs == 0, f"{world.chest_stack} {world.harvested}")
    check("the travels are job events (out then home, twice, all landed) and the walk out includes the recall",
          [e["data"]["leg"] for e in trav] == ["out", "home", "out", "home"] and all(e["data"]["ok"] for e in trav)
          and all(e["walk_out_s"] and e["walk_out_s"] > 2 for e in eps),
          f"{[(e['data'].get('to'), e['data'].get('ok')) for e in trav]} {[e.get('walk_out_s') for e in eps]}")
    out = [e["data"] for e in trav if e["data"]["leg"] == "out"]
    home = [e["data"] for e in trav if e["data"]["leg"] == "home"]
    check("each travel event names its trip, spot, book, Witcher rune and the landing (the library row), and "
          "what it cost",
          [(d["trip"], d["spot"], d["book"], d.get("witcher_rune"), d["landing"]["source"], d["landing"]["library"],
            d["landing"]["name"], (d["landing"]["x"], d["landing"]["y"])) for d in out]
          == [(n, "sim", f"0x{TOME:08X}", "286", "library", "simhome", "286 - Midlands Ruins 1 (South)", RUNE_POS)
              for n in (1, 2)]
          and [(d["trip"], d["book"]) for d in home] == [(1, f"0x{RUNEBOOK:08X}"), (2, f"0x{RUNEBOOK:08X}")]
          and all(d["walk_s"] is not None and d["s"] >= d["walk_s"] and isinstance(d["charges"], int)
                  and d["reagents_used"] == {} for d in out),
          str(out)[:600])
    check("a disturbed recall home is recorded: two casts, the first failed, then it landed",
          [[(t["method"], t["ok"], t["failure"]) for t in d["tries"]] for d in home]
          == [[("charge", False, "disturbed"), ("charge", True, None)], [("charge", True, None)]]
          and home[0]["attempts"] == 2 and home[0]["ok"], str(home)[:600])
    rows_ok = len(eps) == 2 and all(
        [leg["leg"] for leg in e["travel"]] == ["out", "home"] and e["travel_s"] > 2
        and e["supplies"] == {"library_charges": 1, "own_charges": 1, "recall_casts": 0, "trapped_pouches": 1,
                              "reagents_used": {}}
        and e["lockout_s"] >= LOCKOUT_S and e["players_seen"] >= 1 and "skill_end" in e and "weight_end" in e
        for e in eps)
    check("the trip rows carry the travel legs and their time, the lockout waited, the supplies (a library "
          "charge, an own charge; the disturbed cast spent none), the player seen and the end snapshot",
          rows_ok and world.lockouts == 2 and eps[0]["travel"][1]["tries"][0][1] == "disturbed",
          str([{k: e.get(k) for k in ("travel", "travel_s", "supplies", "lockout_s", "players_seen", "skill_end")}
               for e in eps])[:900])
    tev = [e["data"] for e in store.job_events("lumber") if e["kind"] == "tracking"]
    check("Tracking 0 in the skill list: never tried (no skill use, no click), logged and recorded once",
          world.track_c2s == [] and text.count("tracking unavailable") == 1 and len(tev) == 1
          and tev[0]["unavailable"].startswith("no Tracking skill")
          and all(e["tracking"].get("unavailable") for e in eps), f"{world.track_c2s} {tev}")
    store.close()


async def track_reds():
    """LUMBER_LOOP.md §13 "Tracking reds": the library trip with the Tracking gump, buff and arrows
    as captured live. Hunting murderers starts before going out; the recall out stops it (simulated)
    and the runner turns it back on; mid-chop the hunt finds a red 100 tiles off (logged, no escape),
    then 55 tiles off (the red escape: recall home with our runebook, stop)."""
    print("\n== tracking reds: hunt murderers all run; a far red is logged, a near one sends us home ==")
    import lumber_opt
    world = World("tracking")
    spot = LIB_SPOT
    text, code, store, _ = await run_scenario(world, "tracking", 12710, [LIB_TREE],
                                              ["--trips", "1", "--logs-per-trip", "100", "--human", "off",
                                               "--regrow-min", "0.05", "--track-retry-s", "1"], spot_extra=spot)
    tome_t = next((t for p, t in zip(world.c2s, world.c2s_t) if p[0] == 0x06 and p[1:5] == u32(TOME)), None)
    uses = [t for t, w in world.track_c2s if w == "use"]
    btns = [w for _, w in world.track_c2s if w != "use"]
    begins = [t for t, w in world.track_c2s if w == 6]
    check("Hunting murderers began before going out: the stock UseSkill 38, one step to learn the mode, the short "
          "way on to murderers (4 more), Begin, all before the tome was used",
          tome_t is not None and len(uses) == 1 and uses[0] < tome_t and btns[:6] == [8, 8, 8, 8, 8, 6]
          and begins[0] < tome_t, f"{world.track_c2s} tome at {tome_t}")
    check("the hunt the recall out stopped was turned back on (Begin on the open gump: no skill use), and no "
          "other tracking click in the whole run",
          world.hunt_dropped_t is not None and len(begins) == 2 and begins[1] > world.hunt_dropped_t
          and btns == [8, 8, 8, 8, 8, 6, 6] and len(uses) == 1 and world.hunt["on"],
          f"{world.track_c2s} dropped {world.hunt_dropped_t}")
    check("both mid-chop hits found the red while hunting murderers",
          world.red_hits == [(RED_FAR, True), (RED_NEAR, True)], str(world.red_hits))
    seen = [e["data"] for e in store.job_events("lumber") if e["kind"] == "pk_seen"]
    far = [d for d in seen if d.get("source") == "tracking" and d.get("distance") == RED_FAR]
    near = [d for d in seen if d.get("source") == "tracking" and d.get("distance") == RED_NEAR]
    check("the far hit (100 tiles): a pk_seen event from tracking with the name, serial and arrow, logged only "
          "(beyond the react range 80: no escape, not counted as hazard)",
          len(far) == 1 and far[0]["serial"] == RED and far[0]["name"] == RED_NAME
          and far[0]["x"] == LIB_TREE["stand"][0] + RED_FAR and not far[0]["in_range"] and not far[0]["react"]
          and not far[0]["counted"] and far[0]["spaces"] == RED_FAR and far[0]["mode"] == "murderer players"
          and "logged only" in text, str(far))
    check("the near hit (55 tiles, where Bastet was tracked before his third kill): a second pk_seen for the "
          "same red, in range, reacted, counted",
          len(near) == 1 and near[0]["serial"] == RED and near[0]["in_range"] and near[0]["react"]
          and near[0]["counted"] and len(seen) == 2, str(seen))
    rec = [e["data"] for e in store.job_events("lumber") if e["kind"] == "recall"]
    check("the near hit sent us home: one escape recall with our runebook (the disturbed cast retried), "
          f"reason 'tracking: Lord Red {RED_NEAR} spaces', the red as the threat",
          world.recalls_home == [HOME_RUNE_POS] and len(rec) == 1 and rec[0]["ok"]
          and rec[0]["why"] == f"tracking: {RED_NAME} {RED_NEAR} spaces" and rec[0]["threat"]["serial"] == RED
          and rec[0]["threat"]["kind"] == "red" and rec[0]["attempts"] == 2, str(rec)[:600])
    js = {j["kind"]: j for j in store.junctures()}
    check("urgent threat (action recall, the tracking reason) and pk_escape junctures; the run stopped (exit 1)",
          code == 1 and js.get("threat", {}).get("data", {}).get("action") == "recall"
          and f"tracking: {RED_NAME} {RED_NEAR} spaces" in js["threat"]["summary"] and "pk_escape" in js
          and "escaped by recall" in text, f"exit {code} {[(j['kind'], j['summary']) for j in js.values()]}")
    flee = [e["data"] for e in store.job_events("lumber") if e["kind"] == "flee"]
    check("the threat juncture and its flee event name the tracked red (out of view, so not in the assessment's "
          "threats; live 2026-10-07 juncture 508 listed only the town NPCs in view)",
          (js.get("threat", {}).get("data", {}).get("threat") or {}).get("serial") == RED
          and js["threat"]["data"]["threat"]["name"] == RED_NAME and js["threat"]["data"]["threat"]["kind"] == "red"
          and len(flee) == 1 and (flee[0].get("threat") or {}).get("serial") == RED,
          str(js.get("threat", {}).get("data", {}).get("threat"))[:300])
    eps = store.episodes("lumber")
    tr = (eps[0].get("tracking") or {}) if eps else {}
    check("the trip row has the hunt's coverage: on part of the time (off after the recall until turned back "
          "on), 2 hits, both on murderers, 1 try",
          len(eps) == 1 and tr.get("hits") == 2 and tr.get("murderer_hits") == 2 and tr.get("attempts") == 1
          and 0 < tr.get("on_frac", 0) < 1 and tr["on_s"] > 0 and tr["off_s"] > 0, str(tr))
    sightings = lumber_opt.store_inputs(store)["sightings"]
    t_near = next(e["t"] for e in store.job_events("lumber") if e["kind"] == "pk_seen" and e["data"]["react"])
    check("lumber_opt's hazard counts the near tracked red once, not the far one",
          sightings == [t_near], str(sightings))
    store.close()


async def library_chased():
    """A creature hunts us down at a library-rune spot (live 2026-10-03, witcher_291: the
    runner converted logs for 12 s under attack, 85 -> 40 hits, then exited in the field).
    Now: escape on foot, it keeps coming -> recall home with our own book at once, no
    conversion, an urgent threat juncture (not pk_escape), exit 1 at home."""
    print("\n== library, chased: a creature keeps coming at a far spot: recall home first, no conversion ==")
    world = World("library")
    world.chaser = True
    spot = LIB_SPOT
    text, code, store, _ = await run_scenario(world, "library_chased", 12720, [LIB_TREE],
                                              ["--trips", "1", "--logs-per-trip", "100", "--human", "off",
                                               "--regrow-min", "0.05"], spot_extra=spot)
    js = [j for j in store.junctures() if j["source"] == "lumber" and j["severity"] == "urgent"]
    check("it kept coming: recalled home with our runebook at once (the escape recall), exit 1",
          code == 1 and "kept coming after the escape" in text and world.recalls_home
          and world.recalls_home[-1] == HOME_RUNE_POS and "escaped by recall" in text,
          f"exit {code}, home {world.recalls_home}\n{text[-600:]}")
    check("no 10 s log conversion before leaving; home, into the room, converted and stored there",
          "converting the carried logs before stopping" not in text and world.room_entries == 1
          and world.logs == 0 and world.chest_stack is not None and world.chest_stack[1] == world.harvested > 0,
          f"logs {world.logs} chest {world.chest_stack} harvested {world.harvested} room {world.room_log}")
    check("an urgent threat juncture says it recalled away (a creature: not a pk_escape)",
          any(j["kind"] == "threat" and "Recalled away" in j["summary"] for j in js)
          and not any(j["kind"] == "pk_escape" for j in js), str([(j["kind"], j["summary"]) for j in js]))
    thefts = [e for e in store.job_events("lumber") if e["kind"] == "theft"]
    pearls = [e for e in thefts if any(it.get("graphic") == 0x0F7A for it in e["data"].get("items") or [])]
    check("the pack loss in the same moment as the flight is booked: a theft job event and a "
          "theft_suspected juncture for the black pearls, even though the run ended in the escape; the event "
          "carries the load just before the loss (the chop's logs, no boards)",
          pearls and pearls[0]["data"].get("carried") == {"logs": LOGS_PER_SUCCESS, "boards": 0}
          and any(j["kind"] == "theft_suspected" for j in store.junctures()),
          str([e["data"] for e in thefts])[:400])
    eps = store.episodes("lumber")
    rec = [e["data"] for e in store.job_events("lumber") if e["kind"] == "recall"]
    cr = (eps[0].get("creature") or {}) if eps else {}
    check("the trip row books the creature's cost: one escape, recalled, why; the recall events say a creature",
          cr.get("escapes") == 1 and cr.get("recalled") is True and "kept coming" in (cr.get("why") or "")
          and [d.get("cause") for d in rec] == ["creature", "creature"], f"{cr} {[d.get('cause') for d in rec]}")
    check("the first home recall (disturbed) was one cast, then 'running on' and another that landed; no stop "
          "in the field between them",
          [(d["ok"], d["attempts"]) for d in rec] == [(False, 1), (True, 1)] and cr.get("recall_fails") == 1
          and "disturbed; running on" in text, f"{[(d['ok'], d['attempts']) for d in rec]} {cr}")
    store.close()


# the library spot: a pvp grove by the home library's rune 286 (the landing nearest it), 85 tiles from home
LIB_SPOT = {"area": {"center": [LIB_TREE["x"], LIB_TREE["y"]], "radius": 10}, "pvp": True}
# the gazer's: wider, so the walk-away stays in it; never within home.NEAR_LANDING (60) of home: home is a recall
GAZER_SPOT = {"area": {"center": [LIB_TREE["x"], LIB_TREE["y"]], "radius": 20}, "pvp": True}


async def gazer_run():
    """LUMBER_LOOP.md §13 "Running from a creature" (live 2026-10-03: a gazer hit us 4 s after a
    melee-sized walk-away and the trip was recalled home). A gazer (ranged body 22, not in war mode)
    casts at us from 10 tiles mid-chop, once: the runner walks out of its spell range (12) + margin,
    chops on at the far tree, and the trip banks: no recall away, no stop."""
    print("\n== gazer, run: a ranged creature hits once -> walk out of its reach, chop on, bank ==")
    world = World("gazer")
    text, code, store, _ = await run_scenario(world, "gazer_run", 12730, [LIB_TREE, LIB_FAR_TREE],
                                              ["--trips", "1", "--logs-per-trip", "100", "--human", "off",
                                               "--regrow-min", "0.05"], spot_extra=GAZER_SPOT)
    eps = store.episodes("lumber")
    hits = [e["data"] for e in store.job_events("lumber") if e["kind"] == "monster_hit"]
    check("the trip stored its boards in the room, exit 0 (no recall away, no stop)",
          code == 0 and [e.get("outcome") for e in eps] == ["stored"] and world.room_entries == 1
          and not [e for e in store.job_events("lumber") if e["kind"] == "recall"],
          f"exit {code} {[(e.get('outcome'), e.get('why')) for e in eps]}\n{text[-800:]}")
    check("the first damage is a monster_hit: the gazer, 10 tiles off, ranged, the only attacker, a run",
          hits and hits[0]["body"] == GAZER_BODY and hits[0]["distance"] == 10 and hits[0]["hits_lost"] == GAZER_DMG
          and hits[0]["ranged"] is True and hits[0]["attackers"] == 1 and hits[0]["action"] == "run"
          and hits[0]["trip"] == 1 and hits[0]["spot"] == "sim" and hits[0]["reach"] == 12, str(hits)[:600])
    m = re.search(r"escaped to \((\d+), (\d+)\)", text)
    to = (int(m[1]), int(m[2])) if m else None
    check("walked out of the spell range (12) + margin, not just the melee flee radius (8)",
          to is not None and world.gazer_pos is not None and world.cheb(world.gazer_pos) > 12
          and max(abs(to[0] - world.gazer_pos[0]), abs(to[1] - world.gazer_pos[1])) > 12, f"{to} {world.gazer_pos}")
    far = [s for s in stand_events(store) if s["anchor"] == [LIB_FAR_TREE["x"], LIB_FAR_TREE["y"]]]
    check("chopped on at a stand by the far tree, outside the gazer's reach; everything in the chest",
          sum(s["successes"] for s in far) >= 1 and world.chest_stack is not None
          and world.chest_stack[1] == world.harvested and world.logs == 0, f"{far} chest {world.chest_stack}")
    check("no hit after the walk-away: at most one more cast landed while walking (walk_on)",
          1 <= len(world.gazer_hits) <= 2 and [h["action"] for h in hits] == ["run", "walk_on"][:len(hits)]
          and len(hits) == len(world.gazer_hits), f"{world.gazer_hits} {[h['action'] for h in hits]}")
    js = [j for j in store.junctures() if j["kind"] == "threat"]
    check("one urgent threat juncture: action escape, with the hit",
          len(js) == 1 and js[0]["data"].get("action") == "escape"
          and (js[0]["data"].get("hit") or {}).get("body") == GAZER_BODY, str([(j["summary"], j["data"].get("action")) for j in js]))
    cr = (eps[0].get("creature") or {}) if eps else {}
    check("the trip row's creature cost: 1 escape (a run), the hits lost, not recalled, no why",
          cr.get("escapes") == 1 and cr.get("runs") == 1 and cr.get("hits_lost") == 100 - world.hits
          and cr.get("recalled") is False and cr.get("why") is None, str(cr))
    store.close()


async def landing_escape():
    """Live 2026-10-05 (Wintertop, a death): a war-mode creature waits by the landing; the runner backs away
    from it at once, then must harvest on out there. It used to run harvest_trip again from the top after the
    escape and set out for the home rune library from the field ("to the rune library: no route"), aborting."""
    print("\n== a creature at the landing: escape on arrival, then harvest on (no trip back to the library) ==")
    world = World("landing_monster")
    text, code, store, _ = await run_scenario(world, "landing_escape", 12830, [LIB_TREE],
                                              ["--trips", "1", "--logs-per-trip", "100", "--human", "off",
                                               "--regrow-min", "0.05"], spot_extra=LANDER_SPOT)
    eps = store.episodes("lumber")
    check("escaped from the creature at the landing, then 'already out at the spot': no second travel, one "
          "recall out, no walk back to the tome",
          "ESCAPE 1/" in text and "already out at the spot" in text and world.recalls_out == [RUNE_POS]
          and "to the rune library: no route" not in text, f"{world.recalls_out}\n{text[-900:]}")
    check("the trip went on out there and ended in the room (exit 0), not an abort",
          code == 0 and [e.get("outcome") for e in eps] == ["stored"],
          f"exit {code} {[(e.get('outcome'), e.get('why')) for e in eps]}")
    store.close()


async def ghost_horse():
    """Live 2026-10-05 (docs/NOTES.md "Our mount"): our bonded horse's ghost follows us. The room's storage shelf
    lacks something, so the runner is out at the landing when it looks: its menu offers Release (ours), it's a
    ghost, so into the rental room (revived), a double-click mounts it, and the trip rides. The guild house rests
    the mount whenever we come into it (out of the room, a recall home) and gives it back on the recall out or in
    the room: the second trip finds it resting at the landing (no detour) and rides again after the recall out."""
    print("\n== our horse is a ghost: revived through the rental room, mounted; it rests in the guild house ==")
    world = World("ghost_horse")
    world.horse = {"dead": True}
    world.shelf_stock = {"room": 0, "landing": 0}      # the room's shelf gives nothing: out to the landing's
    text, code, store, _ = await run_scenario(world, "ghost_horse", 12840, [LIB_TREE],
                                              ["--trips", "2", "--logs-per-trip", "100", "--human", "off",
                                               "--regrow-min", "0.05"], spot_extra=LIB_SPOT)
    eps = store.episodes("lumber")
    check("its menu asked once (ours: Release), no double-click on the ghost; into the room revived it; one "
          "double-click mounted it before the first trip",
          world.horse_menus == 1 and world.horse_dclicks == [False]
          and [k for k, _ in world.room_log][:2] == ["exit", "enter"] and "is a ghost: into the rental room" in text,
          f"menus {world.horse_menus} dclicks {world.horse_dclicks} {world.room_log}")
    check("both trips stored (exit 0); rows' mount: riding after the recall out, the second from its rest "
          "(no room detour for it)",
          code == 0 and [e["outcome"] for e in eps] == ["stored", "stored"]
          and eps[0].get("mount") == {"pet": "0x0154FE11", "mounted": True}
          and eps[1].get("mount") == {"pet": "0x0154FE11", "mounted": True, "resting": True}
          and [k for k, _ in world.room_log] == ["exit", "enter", "exit", "enter", "exit", "enter"]
          and not any(j["data"].get("item") == "mount" for j in store.junctures()),
          f"exit {code} {[(e.get('outcome'), e.get('why'), e.get('mount')) for e in eps]} {world.room_log}\n"
          f"{text[-600:]}")
    check("the guild house rested it 4 times (out of the room, home, twice) and gave it back each time: riding "
          "at the end", world.mount_rested == 4 and world.mounted,
          f"rested {world.mount_rested} mounted {world.mounted}")
    store.close()


async def gazer_rehit():
    """The same gazer outranges the walk-away (it casts from 20 tiles in this simulation): damage again
    within --creature-rehit-s of arriving -> recall home at once, no conversion, exit 1."""
    print("\n== gazer, re-hit: still taking damage after the walk-away -> recall home, no conversion ==")
    world = World("gazer")
    world.gazer_range = 20
    text, code, store, _ = await run_scenario(world, "gazer_rehit", 12740, [LIB_TREE, LIB_FAR_TREE],
                                              ["--trips", "1", "--logs-per-trip", "100", "--human", "off",
                                               "--regrow-min", "0.05"], spot_extra=GAZER_SPOT)
    hits = [e["data"] for e in store.job_events("lumber") if e["kind"] == "monster_hit"]
    rec = [e["data"] for e in store.job_events("lumber") if e["kind"] == "recall"]
    check("first hit: a run; a hit after arriving: home ('still taking damage after the walk-away')",
          hits and hits[0]["action"] == "run" and hits[-1]["action"] == "recall"
          and "still taking damage" in hits[-1]["why"] and hits[-1]["since_run_s"] is not None
          and hits[-1]["since_run_s"] <= 10, str([(h["action"], h.get("why"), h["since_run_s"]) for h in hits]))
    check("recalled home with our book (the escape recall, cause creature), exit 1",
          code == 1 and world.recalls_home == [HOME_RUNE_POS] and len(rec) == 1 and rec[0]["ok"]
          and rec[0]["cause"] == "creature" and "escaped by recall" in text,
          f"exit {code} home {world.recalls_home} {str(rec)[:300]}\n{text[-600:]}")
    check("no conversion in the field; home, into the room, converted and stored there",
          "converting the carried logs before stopping" not in text and world.room_entries == 1
          and world.logs == 0 and world.chest_stack is not None and world.chest_stack[1] == world.harvested > 0,
          f"logs {world.logs} chest {world.chest_stack} harvested {world.harvested} room {world.room_log}")
    js = [j for j in store.junctures() if j["source"] == "lumber" and j["severity"] == "urgent"]
    check("urgent threat juncture 'Recalled away' (a creature: no pk_escape)",
          any(j["kind"] == "threat" and "Recalled away" in j["summary"] for j in js)
          and not any(j["kind"] == "pk_escape" for j in js), str([(j["kind"], j["summary"]) for j in js]))
    eps = store.episodes("lumber")
    cr = (eps[0].get("creature") or {}) if eps else {}
    check("the trip row: aborted; creature escapes 1, hits lost, recalled, why",
          len(eps) == 1 and eps[0]["outcome"] == "aborted" and cr.get("escapes") == 1
          and cr.get("hits_lost", 0) >= 2 * GAZER_DMG and cr.get("recalled") is True
          and "still taking damage" in (cr.get("why") or ""), str(cr))
    store.close()


async def gazer_reflect():
    """LUMBER_LOOP.md §13 "Spells on us" (live 2026-10-03 witcher_280: a gazer larva's first spell was
    taken by Magic Reflection, the runner kept it on 'watch' and reacted only to the next one's -14,
    7.75 s after first sight). The same gazer's first spell lands on Magic Reflection: the server's
    "Magic reflect removed." and the 0xC0 0x37B9 on us, no hits lost. The runner runs at that spell (a
    monster_hit with spells, 0 hits lost), out of the gazer's reach, before any damage; the trip banks."""
    print("\n== gazer, reflected: a spell that costs no hits is an attack -> walk out of its reach, bank ==")
    world = World("gazer")
    world.reflect = True
    text, code, store, _ = await run_scenario(world, "gazer_reflect", 12770, [LIB_TREE, LIB_FAR_TREE],
                                              ["--trips", "1", "--logs-per-trip", "100", "--human", "off",
                                               "--regrow-min", "0.05"], spot_extra=GAZER_SPOT)
    eps = store.episodes("lumber")
    hits = [e["data"] for e in store.job_events("lumber") if e["kind"] == "monster_hit"]
    check("Magic Reflection took the first spell", len(world.reflected) == 1, str(world.reflected))
    check("the trip stored its boards in the room, exit 0 (no recall away, no stop)",
          code == 0 and [e.get("outcome") for e in eps] == ["stored"] and world.room_entries == 1
          and not [e for e in store.job_events("lumber") if e["kind"] == "recall"],
          f"exit {code} {[(e.get('outcome'), e.get('why')) for e in eps]}\n{text[-800:]}")
    check("the spell is a monster_hit: the gazer, 10 tiles off, ranged, the only attacker, no hits lost, a run",
          hits and hits[0]["body"] == GAZER_BODY and hits[0]["distance"] == 10 and hits[0]["hits_lost"] == 0
          and hits[0]["spells"] >= 1 and hits[0]["ranged"] is True and hits[0]["attackers"] == 1
          and hits[0]["action"] == "run", str(hits)[:600])
    check("it ran at the spell, before any damage: at most one more cast landed while walking (walk_on)",
          len(world.gazer_hits) <= 1 and [h["action"] for h in hits] == ["run", "walk_on"][:len(hits)]
          and len(hits) == 1 + len(world.gazer_hits), f"{world.gazer_hits} {[h['action'] for h in hits]}")
    js = [j for j in store.junctures() if j["kind"] == "threat"]
    check("the threat juncture: action escape, the spell's hit, and `attackers` names the gazer",
          len(js) == 1 and js[0]["data"].get("action") == "escape"
          and js[0]["data"].get("attackers") == [f"0x{GAZER:08X}"]
          and (js[0]["data"].get("hit") or {}).get("spells", 0) >= 1, str([(j["summary"], j["data"].get("attackers"))
                                                                          for j in js]))
    check("the juncture says it was a spell, -0", js and "spell by" in js[0]["summary"] and ": -0," in js[0]["summary"],
          str([j["summary"] for j in js]))
    store.close()


async def wary():
    """A war-mode creature (known aggressive) stands 2 tiles from the nearest tree, 13 from us: that
    tree waits while it is around; the runner chops the farther west tree first, comes back to the
    near one once the creature has gone, and banks. No damage, no escape."""
    print("\n== wary: an aggressive creature by the nearest tree -> chop one away from it first ==")
    world = World("wary")
    text, code, store, _ = await run_scenario(world, "wary", 12750, [GOOD_TREE, WEST_TREE],
                                              ["--trips", "1", "--logs-per-trip", "100", "--human", "off"])
    targets = world.chopped                              # (tree the server chopped, time)
    good = (GOOD_TREE["x"], GOOD_TREE["y"])
    check("the first tree chopped is the west one, not the nearer tree by the creature",
          targets and targets[0][0] == (WEST_TREE["x"], WEST_TREE["y"])
          and "choosing a tree away from it" in text, str([x for x, _ in targets][:4]))
    check("the near tree was chopped only after the creature had gone",
          world.wary_left_t is not None and any(x == good for x, _ in targets)
          and all(t > world.wary_left_t for x, t in targets if x == good), str(targets)[:400])
    eps = store.episodes("lumber")
    cr = (eps[0].get("creature") or {}) if eps else {}
    check("stored, exit 0; no threat juncture, no escape; the trip row counts the tree left alone",
          code == 0 and [e.get("outcome") for e in eps] == ["stored"] and world.chest_stack is not None
          and not [j for j in store.junctures() if j["kind"] == "threat"]
          and cr.get("escapes") == 0 and cr.get("avoided_trees", 0) >= 1, f"exit {code} {cr}\n{text[-600:]}")
    store.close()


async def idle_mob():
    """User 2026-10-05: never move into aggro range of a creature we can see. The wary scenario's creature, idle (no
    war mode, its aggression unknown): the tree 2 tiles from it waits all the same (AGGRO_R), the west tree first,
    the near one only after it has gone. No threat, no escape."""
    print("\n== an idle creature by the nearest tree -> chop one away from it first ==")
    world = World("wary")
    world.wary_flags = 0
    text, code, store, _ = await run_scenario(world, "idle_mob", 12850, [GOOD_TREE, WEST_TREE],
                                              ["--trips", "1", "--logs-per-trip", "100", "--human", "off"])
    targets = world.chopped
    good = (GOOD_TREE["x"], GOOD_TREE["y"])
    check("the idle creature's tree waited: the west tree first, the near one only after it had gone",
          targets and targets[0][0] == (WEST_TREE["x"], WEST_TREE["y"]) and "choosing a tree away from it" in text
          and world.wary_left_t is not None and all(t > world.wary_left_t for x, t in targets if x == good),
          str([x for x, _ in targets][:4]))
    eps = store.episodes("lumber")
    check("stored, exit 0; no threat juncture, no escape",
          code == 0 and [e.get("outcome") for e in eps] == ["stored"]
          and not [j for j in store.junctures() if j["kind"] == "threat"], f"exit {code}\n{text[-600:]}")
    store.close()


async def zone_on_way():
    """Live 2026-10-05 (witcher_23): an air dragon came back into view by the tree we were walking to; the walk
    replanned round it and went on to that tree, and it found us there. Now: the idle creature shows up by the
    near tree while we walk to it; the walk ends ("a creature's zone covers it now") and the west tree comes first."""
    print("\n== a creature shows up by the tree we walk to -> drop it, chop away from it ==")
    world = World("wary")
    world.wary_flags, world.wary_late = 0, True
    text, code, store, _ = await run_scenario(world, "zone_on_way", 12860, [GOOD_TREE, WEST_TREE],
                                              ["--trips", "1", "--logs-per-trip", "100", "--human", "off"])
    targets = world.chopped
    check("it showed up on the way; the walk to the near tree ended, the west tree was chopped first",
          world.wary_shown and "a creature's zone covers it now" in text
          and targets and targets[0][0] == (WEST_TREE["x"], WEST_TREE["y"]), str([x for x, _ in targets][:4]))
    eps = store.episodes("lumber")
    check("stored, exit 0; no threat juncture",
          code == 0 and [e.get("outcome") for e in eps] == ["stored"]
          and not [j for j in store.junctures() if j["kind"] == "threat"], f"exit {code}\n{text[-600:]}")
    store.close()


async def red_aim():
    """LUMBER_LOOP.md §13 "Blind waits" (live 2026-10-03: Bastet came into view during the chop's 2.1 s
    aim pause; the runner answered the cursor, then recalled 2.5 s after sight and was hit out of the
    cast). Since 2026-10-04 the aim is a script's ~0.1 s (humanize SCRIPT_MEDIAN), so a red comes into
    view RED_AIM_S after the chop's cursor, while it is up (normal profile, full pace): whichever read
    sees him first (the cursor's wait or the aim pause), the cursor goes with the stock 0x6C cancel and no
    chop target is answered after him. Since 2026-10-06 (option E) a red within spell range (8 tiles) is
    run from first: the first step away within REACT_MAX_S of sight, the book only PLAYER_RECALL_GAP off."""
    print("\n== red while the chop cursor is up: cancel it, run out of his spell range, recall ==")
    world = World("red_aim")
    world.late_blast = True                       # live 2026-10-06: the PK's Explosion went off on us at home
    spot = LIB_SPOT
    text, code, store, rows = await run_scenario(world, "red_aim", 12760, [LIB_TREE],
                                                 ["--trips", "1", "--logs-per-trip", "100", "--human", "normal",
                                                  "--seed", "5", "--regrow-min", "0.05"], spot_extra=spot)
    red = world.red_t
    after = [(p, t) for p, t in zip(world.c2s, world.c2s_t) if red is not None and t >= red]
    targets = [(parse_packet("c2s", p), t) for p, t in after if p[0] == 0x6C]
    cancels = [t for f, t in targets if f["x"] == 0x7FFFFFFF]
    answers = [f for f, t in targets if f["x"] != 0x7FFFFFFF]
    step = next((t for p, t in after if p[0] == 0x02), None)
    lat = None if step is None else round(step - red, 3)
    check("the red came into view (RED_AIM_S after the chop's cursor)", red is not None, text[-800:])
    check("the chop cursor was cancelled (stock 0x6C cancel, once) before the first step away, and no chop target "
          "was answered after the red appeared",
          len(cancels) == 1 and step is not None and cancels[0] < step and answers == [],
          f"cancels {cancels} answers {answers} step {step}")
    check(f"the run away started within {REACT_MAX_S} s of the red's 0x20 (simulator clock): {lat} s",
          lat is not None and lat <= REACT_MAX_S, str(lat))
    check(f"the book was only pressed {PLAYER_RECALL_GAP}+ tiles from the red (option E)",
          world.book_dists and min(world.book_dists) >= PLAYER_RECALL_GAP, str(world.book_dists))
    check("the proxy cleared the client's copy of the cursor (fabricated S2C cancel)",
          any(r.get("ev") == "target_cancel_client" for r in rows))
    rec = [e["data"] for e in store.job_events("lumber") if e["kind"] == "recall"]
    check("the recall events: the last landed home, the red as the threat",
          world.recalls_home == [HOME_RUNE_POS] and rec and rec[-1]["ok"]
          and rec[-1]["threat"]["serial"] == BASTET and rec[-1]["threat"]["kind"] == "red", str(rec)[:600])
    check("the run stopped after the escape (exit 1, pk_escape juncture)",
          code == 1 and "escaped by recall" in text
          and any(j["kind"] == "pk_escape" for j in store.junctures()), f"exit {code}")
    check("the PK's Explosion landing on us at home (-31, its sound on our tile, no pouch of ours gone off) neither "
          "kept us out of the room nor read as a thief: into the room",
          world.hits == 69 and world.room_entries == 1 and "THIEF" not in text and "after the recall home" not in text,
          f"hits {world.hits} room {world.room_log}\n{text[-600:]}")
    print(f"  sim latency: red 0x20 -> cancel {round(cancels[0] - red, 3) if cancels else None} s, "
          f"-> first step {lat} s; book at {world.book_dists} tiles")
    store.close()


async def field_foe(tag, port, title, foe_why, calm_who):
    """faction / precast (user, 2026-10-06, after run 16's death at witcher_66): at the library spot (pvp)
    someone harmless shows up first (calm_who), CALM_S later the foe within spell range: the run away
    starts within REACT_MAX_S of the foe's tag / words (option E), the book only PLAYER_RECALL_GAP off;
    nothing for the harmless one; the foe in the recall event and a pk_seen sighting. Returns (world,
    store, text) for the scenario's own checks."""
    print(f"\n== {title} ==")
    world = World(tag)
    text, code, store, _ = await run_scenario(world, tag, port, [LIB_TREE],
                                              ["--trips", "1", "--logs-per-trip", "100", "--human", "normal",
                                               "--seed", "5", "--regrow-min", "0.05"], spot_extra=LIB_SPOT)
    calm, foe = world.calm_t, world.red_t
    sent = [(p, t) for p, t in zip(world.c2s, world.c2s_t) if calm is not None and t >= calm]
    books = [t for p, t in sent if p[0] == 0x06 and p[1:5] == u32(RUNEBOOK)]
    step = next((t for p, t in sent if p[0] == 0x02 and foe is not None and t >= foe), None)
    lat = None if step is None else round(step - foe, 3)
    check(f"{tag}: nothing for {calm_who}; the run away started within {REACT_MAX_S} s of the foe ({lat} s), "
          f"the book only {PLAYER_RECALL_GAP}+ tiles from him",
          foe is not None and books and books[0] >= foe and lat is not None and lat <= REACT_MAX_S
          and world.book_dists and min(world.book_dists) >= PLAYER_RECALL_GAP,
          f"calm {calm} foe {foe} step {step} books {books} at {world.book_dists}")
    rec = [e["data"] for e in store.job_events("lumber") if e["kind"] == "recall"]
    seen = [e["data"] for e in store.job_events("lumber") if e["kind"] == "pk_seen"]
    check(f"{tag}: the recall that landed names the foe and why ({foe_why}); one pk_seen sighting, the foe's",
          rec and rec[-1]["ok"] and rec[-1]["threat"]["serial"] == FOE and foe_why in rec[-1]["threat"]["reason"]
          and [s["serial"] for s in seen] == [FOE], f"{str(rec)[:400]} seen {[s['serial'] for s in seen]}")
    check(f"{tag}: the run stopped after the escape (exit 1, pk_escape), in the room",
          code == 1 and any(j["kind"] == "pk_escape" for j in store.junctures()) and world.room_entries == 1,
          f"exit {code} room {world.room_log}")
    return world, store, text


async def faction():
    world, store, text = await field_foe("faction", 12890, "a faction-tagged player in view at a pvp spot: recall at once",
                                         "faction tag [Cambria]", "a guildmate with a faction tag")
    ev = [e["data"] for e in store.job_events("lumber") if e["kind"] == "faction_waypost"]
    row = next((r for r in store.lumber_spot_rows() if r["id"] == "sim"), None)
    check("the faction waypost marker in view: a faction_waypost event, the spot marked a faction zone (lumber_opt)",
          len(ev) == 1 and ev[0]["label"] == "FACTION WP 17" and row is not None
          and (row["data"].get("faction_zone") or {}).get("label") == "FACTION WP 17" and row["status"] == "active",
          f"{ev} {row}")
    store.close()


async def precast():
    _, store, _ = await field_foe("precast", 12900, "a player saying a harmful spell's words near us: recall at once",
                                  "Explosion", "a blue healing himself")
    store.close()


async def flee_aid():
    """LUMBER_LOOP.md §13 "Healing on the run" (user request 2026-10-06, the Razor 'PK Getaway' script): at the
    library spot a red comes into view within spell range and PK_HURT_S later his spells land: hits 100 -> 55,
    poisoned (0x17 for us), paralyzed (our 0x20 flags 0x21, steps refused). Running from him the runner pops a
    live trapped pouch (it breaks the paralysis), drinks a cure, then a heal potion; the book's double-click never
    within 0.5 s of a potion or pouch click (the server's action delay); the recall home lands (the first is
    disturbed and recast); no potion read as stolen."""
    print("\n== flight aid: paralyzed, poisoned, hurt by a red: pouch, cure, heal on the run; then the recall ==")
    world = World("flee_aid")
    text, code, store, _ = await run_scenario(world, "flee_aid", 12910, [LIB_TREE],
                                              ["--trips", "1", "--logs-per-trip", "100", "--human", "normal",
                                               "--seed", "5", "--regrow-min", "0.05"], spot_extra=LIB_SPOT)
    hurt = world.hurt_t
    clicks = world.aid_clicks
    kinds = [k for _, k, _ in clicks]
    books = [t for p, t in zip(world.c2s, world.c2s_t) if p[0] == 0x06 and p[1:5] == u32(RUNEBOOK)
             and hurt is not None and t >= hurt]
    check("the red's spells landed (hits 55, poisoned, paralyzed)", hurt is not None, text[-800:])
    pouch_t = next((t for t, k, frozen in clicks if k == "pouch" and frozen), None)
    moved = [t for t, ok in world.steps if ok and world.unfrozen_t is not None and t > world.unfrozen_t
             and (not books or t < books[0])]
    check("a live trapped pouch double-clicked while paralyzed, and the run went on afterwards (steps taken)",
          pouch_t is not None and world.unfrozen_t is not None and len(moved) >= 2,
          f"clicks {kinds} unfrozen {world.unfrozen_t} steps after {len(moved)} refused "
          f"{sum(1 for _, ok in world.steps if not ok)}")
    check("the cure before the heal (a heal potion does nothing while poisoned); no heal refused",
          "cure" in kinds and "heal" in kinds and kinds.index("cure") < kinds.index("heal") and not world.poisoned
          and "You can not heal" not in text, str(kinds))
    heal_t = next((t for t, k, _ in clicks if k == "heal"), None)
    check("the heal potion drunk on the run, before the first book double-click",
          heal_t is not None and books and heal_t < books[0], f"heal {heal_t} books {books}")
    gaps = []
    for b in books:
        before = [t for t, _, _ in clicks if t <= b]
        gaps.append(round(b - before[-1], 3) if before else None)
    check("every runebook double-click 0.5 s or more after the last potion/pouch click before it",
          books and all(g is None or g >= 0.5 for g in gaps), f"gaps {gaps}")
    rec = [e["data"] for e in store.job_events("lumber") if e["kind"] == "recall"]
    check("the recall home landed (the red as the threat)", world.recalls_home == [HOME_RUNE_POS] and rec
          and rec[-1]["ok"] and rec[-1]["threat"]["serial"] == BASTET, str(rec)[:600])
    junc = [j["kind"] for j in store.junctures()]
    check("no theft_suspected: the potions drunk were declared to the ledger; the pk_escape juncture",
          "theft_suspected" not in junc and "pk_escape" in junc and "THIEF" not in text, str(junc))
    ev = [e["data"] for e in store.job_events("lumber") if e["kind"] == "flee_aid"]
    check("flee_aid job events: pouch (frozen), cure (poisoned), heal (running), with hits and stamina",
          [e["kind"] for e in ev][:3] == ["pouch", "cure", "heal"] and ev[0]["frozen"] and ev[1]["poisoned"]
          and not ev[2]["standing"] and ev[2]["hits"] is not None and ev[2]["hits_max"] == 100
          and all(k in ev[0] for k in ("stam", "stam_max", "trip", "spot", "why")), str(ev)[:800])
    check("potions spent: one cure, one heal", [n for _, n in world.potions.values()] == [4, 4, 5],
          str(world.potions))
    check("the run stopped after the escape (exit 1), in the room", code == 1 and world.room_entries == 1,
          f"exit {code} room {world.room_log}")
    print(f"  sim: hurt -> {[(k, round(t - hurt, 2), f) for t, k, f in clicks] if hurt else None}; book gaps {gaps}")
    store.close()


async def thief_keep_away():
    """docs/PLAN.md "Keep thieves off the logs" (THREATS.md §7 T3): at the library spot a blue player
    (Caputo Wood, live 2026-10-03) steps next to us while we chop: the runner steps out of his reach after a
    reaction pause (thief_near juncture, a `thief` keep_away event) and chops on at a stand away from him; he
    follows and stands next to us again: recall home (pk_escape, a `thief` closed_again event), exit 1."""
    print("\n== thief: a blue next to us while chopping -> step away; he closes again -> recall ==")
    import lumber_opt
    world = World("thief")
    text, code, store, _ = await run_scenario(world, "thief", 12780, [LIB_TREE, LIB_FAR_TREE],
                                              ["--trips", "1", "--logs-per-trip", "100", "--human", "off",
                                               "--regrow-min", "0.05"], spot_extra=GAZER_SPOT)
    ev = [e for e in store.job_events("lumber") if e["kind"] == "thief"]
    near = [j for j in store.junctures() if j["kind"] == "thief_near"]
    first = ev[0]["data"] if ev else {}
    check("he came within the steal guard while we chopped: an attention thief_near juncture naming him, a "
          "`thief` job event (trigger near, action keep_away) with where we stood",
          len(near) == 1 and near[0]["severity"] == "attention" and f"0x{THIEF:08X}" in near[0]["summary"]
          and first.get("trigger") == "near" and first.get("action") == "keep_away"
          and first["suspects"][0]["serial"] == THIEF and first["suspects"][0]["kind"] == "blue",
          f"{[(j['kind'], j['summary']) for j in near]} {ev[:1]}")
    m = re.search(r"stepped away to \((\d+), (\d+)\)", text)
    to = (int(m[1]), int(m[2])) if m else None
    him = world.thief_at[0] if world.thief_at else (0, 0)
    check("stepped to at least 4 tiles from him (one step beats the 5 s steal cooldown), then chopped on at "
          "the far stand",
          to is not None and max(abs(to[0] - him[0]), abs(to[1] - him[1])) >= 4
          and any(s["anchor"] == [LIB_FAR_TREE["x"], LIB_FAR_TREE["y"]] for s in stand_events(store)),
          f"him {him} -> {to}")
    rec = [e["data"] for e in store.job_events("lumber") if e["kind"] == "recall"]
    check("he followed and stood next to us again: recalled home with our book, a `thief` event "
          "(closed_again, recall), an urgent pk_escape juncture; exit 1",
          code == 1 and world.thief_followed and len(ev) == 2 and ev[1]["data"]["trigger"] == "closed_again"
          and ev[1]["data"]["action"] == "recall" and world.recalls_home == [HOME_RUNE_POS]
          and len(rec) == 1 and rec[0]["ok"] and rec[0]["threat"]["serial"] == THIEF
          and any(j["kind"] == "pk_escape" for j in store.junctures()) and "escaped by recall" in text,
          f"exit {code} {[e['data'].get('trigger') for e in ev]} home {world.recalls_home}\n{text[-600:]}")
    check("the logs stayed in the trapped pouch (never set off in the field); home by the recall, into the room, "
          "converted and stored there",
          world.stashed and world.room_entries == 1 and world.logs == 0 and world.chest_stack is not None
          and world.chest_stack[1] == world.harvested > 0 and all(by == "us" for _, _, by in world.pops),
          f"pops {world.pops} logs {world.logs} harvested {world.harvested} chest {world.chest_stack} "
          f"room {world.room_log}")
    inp = lumber_opt.store_inputs(store)
    check("lumber_opt reads the thief events (the recall one puts the spot on THIEF_COOLDOWN_S)",
          [e["data"]["action"] for e in inp["events"] if e["kind"] == "thief"] == ["keep_away", "recall"],
          str([e["kind"] for e in inp["events"]]))
    store.close()


async def pouch_pop():
    """A hidden thief snoops our trapped pouch at the library spot: the sound and the explosions around us and
    the pouch's hue 38 -> 0, with no double-click of ours (docs/NOTES.md "A trapped pouch popped by its
    owner"): recall home like for a red, a `thief` pouch_pop event, exit 1."""
    print("\n== pouch pop: our trapped pouch goes off without our double-click -> recall ==")
    world = World("pouch_pop")
    text, code, store, _ = await run_scenario(world, "pouch_pop", 12790, [LIB_TREE],
                                              ["--trips", "1", "--logs-per-trip", "100", "--human", "off",
                                               "--regrow-min", "0.05"], spot_extra=GAZER_SPOT)
    pop_t = world.pops[0][1] if world.pops else None
    book = next((t for p, t in zip(world.c2s, world.c2s_t) if p[0] == 0x06 and p[1:5] == u32(RUNEBOOK)
                 and pop_t is not None and t >= pop_t), None)
    ev = [e["data"] for e in store.job_events("lumber") if e["kind"] == "thief"]
    pouch_clicks = [i for i, p in enumerate(world.c2s) if p[0] == 0x06 and p[1:5] == u32(POUCHES[0])]
    book_clicks = [i for i, p in enumerate(world.c2s) if p[0] == 0x06 and p[1:5] == u32(RUNEBOOK)]
    check("the thief's pop (no double-click of ours on the pouch before the recall home; in the room one opens "
          "it to convert) recalled us home within 1.5 s: a `thief` event (pouch_pop, recall) with the signals, "
          "no player in view",
          [(p, by) for p, _, by in world.pops] == [(POUCHES[0], "thief")]
          and book_clicks and all(i > book_clicks[-1] for i in pouch_clicks)
          and book is not None and book - pop_t < 1.5
          and len(ev) == 1 and ev[0]["trigger"] == "pouch_pop" and ev[0]["action"] == "recall"
          and {s["signal"] for s in ev[0]["signals"]} == {"sound", "explosion", "hue"}
          and not any(s["own"] for s in ev[0]["signals"]) and ev[0]["suspect"] is None,
          f"pops {world.pops} book {book} {str(ev)[:500]}\n{text[-600:]}")
    js = [j for j in store.junctures() if j["source"] == "lumber" and j["severity"] == "urgent"]
    check("urgent threat (action recall, 'a thief at our trapped pouch') and pk_escape junctures; exit 1",
          code == 1 and world.recalls_home == [HOME_RUNE_POS] and "escaped by recall" in text
          and any(j["kind"] == "pk_escape" for j in js)
          and any(j["kind"] == "threat" and "a thief at our trapped pouch" in j["summary"] for j in js),
          str([(j["kind"], j["summary"]) for j in js]))
    store.close()


async def no_pouch():
    """No trapped pouch in the pack and none on the shelves: the room's shelf and the landing's (out of the room
    for it, the secured one skipped) give nothing, then a `low_supplies` juncture (item 'trapped pouch'), no
    trip, back into the room, exit 1."""
    print("\n== no trapped pouch, none on the shelves: low_supplies, no trip, back in the room ==")
    world = World("home")
    world.scripted = False
    world.pouch_hue = {}
    world.shelf_stock = {"room": 0, "landing": 0}
    text, code, store, _ = await run_scenario(world, "no_pouch", 12800, [GOOD_TREE],
                                              ["--trips", "1", "--logs-per-trip", "100", "--human", "off"])
    low = [j for j in store.junctures() if j["kind"] == "low_supplies"]
    check("a low_supplies juncture for the trapped pouch, no trip row, nothing sent to chop, exit 1",
          code == 1 and len(low) == 1 and low[0]["data"]["item"] == "trapped pouch"
          and low[0]["severity"] == "attention" and store.episodes("lumber") == [] and not world.self_targets,
          f"exit {code} {low}\n{text[-400:]}")
    check("both shelves tried (the room's, then out of the room the landing's, the secured one skipped), "
          "only Resupply and close pressed; then back into the room",
          world.resupplies == [("room", 0), ("landing", 0)] and world.secured_tries == 1
          and set(world.shelf_presses) == {7, 0} and world.facet == ROOM_FACET
          and [k for k, _ in world.room_log] == ["exit", "enter"],
          f"{world.resupplies} secured {world.secured_tries} presses {world.shelf_presses} log {world.room_log}")
    store.close()


async def convert_stacks():
    """Live 2026-10-05 (lumber-20261005-164216-2557): 4 log stacks came home (other woods, and logs earlier aborted
    trips left), one hatchet use brought no cursor, and the 4-try convert loop aborted with a 9-log stack left and
    nothing stored. Now one try per stack plus CONVERT_RETRIES: every stack becomes boards, all go into the chest."""
    print("\n== several log stacks and a hatchet use without a cursor: everything converted and stored ==")
    world = World("home")
    world.scripted = False
    world.carried = [(0x1BDE, 7), (0x1BDF, 5), (0x1BE0, 3)]
    world.no_cursor_once = True
    text, code, store, _ = await run_scenario(world, "convert_stacks", 12870, [GOOD_TREE],
                                              ["--trips", "1", "--logs-per-trip", "10", "--human", "off"])
    check("the use without a cursor was tried again; 4 stacks converted; exit 0",
          code == 0 and not world.no_cursor_once and "the hatchet brought no cursor" in text
          and text.count("logs to boards") == 4, f"exit {code}\n{text[-800:]}")
    check("every log went into the chest as boards: 15 carried + this trip's",
          world.chest_stack is not None and world.chest_stack[1] == 15 + world.harvested and world.logs == 0,
          f"chest {world.chest_stack}, harvested {world.harvested}, logs {world.logs}")
    store.close()


async def stockpile_store():
    """User 2026-10-05: the boards now go into the Resource Stockpile in the room (live: its menu, Add Items, one
    target per pouch holding boards, else per stack). Carried boards and logs of another wood in the pack and this
    trip's logs in the pouch come home as a loose stack and a pouch of boards: two targets, the menu closed once at
    the end; then the room shelf's
    Restock with our backpack and Resupply (the user's routine): the spent pouch goes into the shelf; no theft
    suspected; the row counts them."""
    print("\n== the boards into the room's Resource Stockpile, one Add Items per stack; then Restock + Resupply ==")
    world = World("home")
    world.scripted = False
    world.carried = [(0x1BDE, 7), (BOARD_G, 4)]
    world.stockpile = {"boards": 0, "adds": [], "refused": [], "gumps": set(), "closed": 0}
    world.shelf_stock = {"room": 0, "landing": 0}
    text, code, store, _ = await run_scenario(world, "stockpile_store", 12880, [GOOD_TREE],
                                              ["--trips", "1", "--logs-per-trip", "10", "--human", "off"])
    pile = world.stockpile
    pouch_used = world.stashed[0][2] if world.stashed else None
    check("every board went into the stockpile: the loose stack in the pack on its own, the pouch's boards by "
          "targeting the pouch (user 2026-10-05), nothing refused, none in the chest",
          code == 0 and pile["boards"] == 11 + world.harvested and len(pile["adds"]) == 2 and not pile["refused"]
          and pouch_used in pile["adds"] and world.chest_stack is None and world.logs == 0,
          f"exit {code} pile {pile} pouch {pouch_used} chest {world.chest_stack} harvested {world.harvested}\n"
          f"{text[-800:]}")
    check("its menu was closed once, at the end (each add brings it back)",
          pile["closed"] == 1 and not pile["gumps"], str(pile))
    eps = store.episodes("lumber")
    check("the row stored them all and stockpiled them all (the Jobs page's boards stored); no theft suspected "
          "(the Restock below took pack items too)",
          len(eps) == 1 and eps[0]["outcome"] == "stored" and eps[0].get("stored") == pile["boards"]
          and eps[0].get("stockpiled") == pile["boards"]
          and not [j for j in store.junctures() if j["kind"] == "theft_suspected"],
          f"{[(e.get('outcome'), e.get('stored'), e.get('stockpiled')) for e in eps]}\n{text[-600:]}")
    rs = (eps[0].get("restock") or {}) if eps else {}
    check("then the room shelf's Restock with our backpack (the user's routine) took the spent pouch and the two "
          "live ones, and Resupply gave two back (all it had): nothing into the chest; the row's `restock`",
          world.restocks == [("room", BACKPACK)] and len(world.shelf_pouches) == 1 and not world.chest_items
          and world.resupplies[-1] == ("room", 2) and rs.get("restocked") == ["3 items were added."]
          and rs.get("missing") == ["Trapped Pouch"],
          f"restocks {world.restocks} shelf {world.shelf_pouches} chest {world.chest_items} "
          f"resupplies {world.resupplies} row {rs}")
    store.close()


async def resupply():
    """No trapped pouch in the pack; the room's shelf has none, the landing's has 3: out of the room, Resupply
    there gives 3, the trip goes, its row says what each shelf gave."""
    print("\n== resupply: the room's shelf is empty, the landing's gives the trapped pouches, the trip goes ==")
    world = World("home")
    world.scripted = False
    world.pouch_hue = {}
    world.shelf_stock = {"room": 0, "landing": 3}
    text, code, store, _ = await run_scenario(world, "resupply", 12820, [GOOD_TREE],
                                              ["--trips", "1", "--logs-per-trip", "10", "--human", "off"])
    eps = store.episodes("lumber")
    res = (eps[0].get("resupply") or []) if eps else []
    check("the room's shelf gave nothing, the landing's (past the secured one) gave 3 trapped pouches",
          world.resupplies[:2] == [("room", 0), ("landing", 3)] and world.secured_tries == 1,
          f"{world.resupplies} secured {world.secured_tries}")
    check("the trip went and stored (exit 0, outcome stored) with a resupplied pouch; at the end the room shelf's "
          "Restock took the spent one (none into the chest)",
          code == 0 and len(eps) == 1 and eps[0]["outcome"] == "stored"
          and world.shelf_pouches and world.shelf_pouches[0] in SHELF_POUCHES and not world.chest_items,
          f"exit {code} {[(e.get('outcome'), e.get('why')) for e in eps]} shelf {world.shelf_pouches} "
          f"chest {world.chest_items}\n{text[-600:]}")
    check("the trip row's resupply: the room's none_available, the landing's 3 pouches (hue 38)",
          [r["where"] for r in res] == ["room", "landing"] and res[0]["none_available"] and res[0]["added"] == []
          and [(a["amount"], a["hue"]) for a in res[1]["added"]] == [(1, 38)] * 3 and res[1]["missing"] == [],
          str(res)[:600])
    store.close()


async def staff_in_view():
    """docs/PLAN.md "Staff alarm on an invulnerable player in view": mid-harvest a vendor (notoriety 7, no
    player flag) steps next to us: nothing. Then an invulnerable player (notoriety 7 + player flag 0x20,
    wearing a robe) comes into view 5 tiles off and stays: one urgent gm_suspected (staff alarm) and one
    speech_nearby hold for it, a staff_sighting job event with what it looks like, nothing sent until the
    test overseer acks both; then the trip banks."""
    print("\n== staff in view: a vendor next to us is nothing; an invulnerable player holds the job ==")
    world = World("staff")
    held = {}

    async def overseer(db):
        """Acks the hold HOLD_S after it appears, and gm_suspected GM_EXTRA_S after that."""
        await asyncio.sleep(1.0)
        store = memory.Memory(db)
        try:
            while "gm_ack" not in held:
                await asyncio.sleep(0.2)
                row = store.con.execute("SELECT id, t FROM junctures WHERE kind='speech_nearby' "
                                        "AND acked_t IS NULL").fetchone()
                if row:
                    held["t"] = row[1]
                    await asyncio.sleep(HOLD_S)
                    held["ack"] = time.time()
                    store.juncture_ack(row[0])
                    await asyncio.sleep(GM_EXTRA_S)
                    for (gid,) in store.con.execute("SELECT id FROM junctures WHERE kind='gm_suspected' "
                                                    "AND acked_t IS NULL").fetchall():
                        store.juncture_ack(gid)
                    held["gm_ack"] = time.time()
        finally:
            store.close()

    # one tree, as before (the walk home from GOOD_TREE crosses the door)
    text, code, store, _ = await run_scenario(world, "staff_in_view", 12810, [GOOD_TREE],
                                              ["--trips", "1", "--logs-per-trip", "100", "--human", "off"],
                                              during=overseer)
    gm_key, vendor_key = f"0x{GM_SEEN:08X}", f"0x{VENDOR_NEAR:08X}"
    js = store.junctures()
    gms = [j for j in js if j["kind"] == "gm_suspected"]
    holds = [j for j in js if j["kind"] == "speech_nearby"]
    check("the vendor stepped next to us before the invulnerable player came: no juncture names it, none came "
          "before the invulnerable player, no thief_near",
          world.vendor_t is not None and world.gm_t is not None and world.vendor_t < world.gm_t
          and not any(vendor_key in json.dumps(j["data"]) or vendor_key in j["summary"] for j in js)
          and all(j["t"] >= world.gm_t for j in js if j["kind"] in ("gm_suspected", "speech_nearby", "thief_near"))
          and not [j for j in js if j["kind"] == "thief_near"],
          f"vendor {world.vendor_t} gm {world.gm_t} {[(j['kind'], j['summary']) for j in js]}")
    g = gms[0] if gms else {}
    check("one urgent gm_suspected from the runner for the invulnerable player, raised on sight (< 3 s), acked",
          len(gms) == 1 and g["source"] == "lumber" and g["severity"] == "urgent"
          and g["data"]["speakers"][0]["serial"] == gm_key and "invulnerable player" in g["summary"]
          and g["t"] - world.gm_t < 3.0 and g["acked_t"] is not None, str(gms))
    h = holds[0] if holds else {}
    sp = (h.get("data") or {}).get("speakers") or [{}]
    check("one urgent speech_nearby hold for it (a sighting: no text), saying it is in view",
          len(holds) == 1 and h["severity"] == "urgent" and h["data"].get("hold") is True
          and sp[0].get("serial") == gm_key and sp[0].get("type") == "sighting" and sp[0].get("text") is None
          and "is in view (invulnerable player" in h["summary"] and h["t"] - world.gm_t < 3.0, str(holds))
    quiet = [p.hex() for p, t in zip(world.c2s, world.c2s_t)
             if held.get("t") is not None and held["t"] + 0.3 < t < held.get("gm_ack", 0)]
    check(f"nothing sent while held: the hold's all-clear ({HOLD_S:.0f} s), then the staff alarm acked "
          f"({GM_EXTRA_S:.0f} s more)", "gm_ack" in held and quiet == [], f"held {held} sent {quiet[:5]}")
    sights = [e for e in store.job_events("lumber") if e["kind"] == "staff_sighting"]
    s = sights[0]["data"] if sights else {}
    check("one staff_sighting job event (the vendor none): serial, body, hue, notoriety, flags, position, "
          "the robe it wears",
          len(sights) == 1 and s["serial"] == gm_key and (s["body"], s["hue"], s["notoriety"], s["flags"])
          == ("0x0190", 0x83EA, 7, "0x20") and s["x"] is not None and s["y"] is not None
          and [(w["serial"], w["layer"], w["graphic"], w["hue"]) for w in s["worn"]]
          == [(f"0x{GM_ROBE:08X}", 0x16, 0x204F, 0x0481)] and s["trip"] == 1, str(sights))
    eps = store.episodes("lumber")
    check("it stayed in view and raised nothing more; the job resumed after the all-clear and stored, exit 0",
          code == 0 and [e.get("outcome") for e in eps] == ["stored"] and "all-clear after" in text
          and sum(r.get("speech_holds", 0) for r in eps) == 1 and text.count("STAFF IN VIEW") == 1,
          f"exit {code} {[(e.get('outcome'), e.get('speech_holds')) for e in eps]}\n{text[-600:]}")
    store.close()


# hop scenario (docs/PLAN.md "hop through the rune library", user decision 2026-10-07): a second spot by the home
# library's rune 286, no hazards; the planner's landing-route cache pre-seeded (its route check plans on the real
# map, where these simulated tiles mean nothing): both landings reach both groves
HOP_SPOT = {"id": "sim2", "name": "simulated trees by rune 286", "facet": 0,
            "area": {"center": [LIB_TREE["x"], LIB_TREE["y"]], "radius": 10}, "trees": [LIB_TREE], "pvp": False}


def hop_routes() -> dict:
    return {lumber_opt.route_key({"x": lx, "y": ly}, s): max(1, lumber_opt.area_dist(s["area"], (lx, ly)))
            for s in ({"area": {"center": list(START), "radius": 12}}, HOP_SPOT)
            for (lx, ly) in (BOOK_RUNE_POS, RUNE_POS)}


async def hop():
    """docs/PLAN.md "Next: hop through the rune library": the 'sim' leg runs dry short of its quota with the logs
    in the pouch; home (on foot: 'sim' lies within home.NEAR_LANDING), the plan (carried, hop_from 'sim') sends
    us on to 'sim2' (the only other eligible spot) instead of the room: leg 1's row 'hopped', then the walk to
    the home library's tome, its rune 286 out, chop, home by our book and into the room once, both legs' logs
    stored. Alongside, --hops 0: the dry leg goes home through the room as before (one 'stored' row, no hop)."""
    print("\n== hop: a dry leg goes on from the home rune library to the next spot, the room once at the end ==")
    world, off = World("hop"), World("hop")
    args, routes = ["--trips", "1", "--logs-per-trip", "100", "--human", "off"], hop_routes()
    (text, code, store, _), (otext, ocode, ostore, _) = await asyncio.gather(
        run_scenario(world, "hop", 12920, [GOOD_TREE, DRY_TREE], args, more_spots=[HOP_SPOT], routes=routes),
        run_scenario(off, "hop_off", 12930, [GOOD_TREE, DRY_TREE], args + ["--hops", "0"], more_spots=[HOP_SPOT],
                     routes=routes))
    eps = store.episodes("lumber")
    brief = [{k: e.get(k) for k in ("spot", "leg", "outcome", "why", "dry", "logs", "carried_in", "hop", "stored",
                                     "woods")} for e in eps]
    check("exit 0; two lumber rows for the one trip", code == 0 and len(eps) == 2,
          f"exit {code} {brief}\n{text[-1200:]}")
    one, two = (eps + [{}, {}])[:2]
    check("leg 1 at 'sim': hopped, dry, nothing carried in, some logs of its own (woods), its hop to 'sim2'",
          one.get("spot") == "sim" and one.get("leg") == 1 and one.get("outcome") == "hopped" and one.get("dry") is True
          and one.get("carried_in") == 0 and (one.get("logs") or 0) > 0
          and sum((one.get("woods") or {}).values()) == one.get("logs")
          and (one.get("hop") or {}).get("spot") == "sim2" and (one["hop"].get("gain_logs") or 0) > 0
          and one["hop"].get("carried") == one.get("logs"), str(brief[:1]))
    check("leg 2 at 'sim2': stored, leg 2, carrying leg 1's logs in (not counted in its own woods)",
          two.get("spot") == "sim2" and two.get("leg") == 2 and two.get("outcome") == "stored"
          and two.get("carried_in") == one.get("logs") and (two.get("logs") or 0) > 0
          and ("woods" not in two or sum(two["woods"].values()) == two["logs"]), str(brief[1:]))
    check("the rental room once (out at the start, in at the end): no room visit between the legs",
          [k for k, _ in world.room_log] == ["exit", "enter"], str(world.room_log))
    check("both legs' logs became boards in the chest, nothing left in the pack",
          world.chest_stack is not None and world.chest_stack[1] == one.get("logs", 0) + two.get("logs", 0)
          == world.harvested and world.logs == 0 and two.get("stored") == world.harvested,
          f"chest {world.chest_stack}, harvested {world.harvested}, logs {world.logs}, stored {two.get('stored')}")
    trav = [e["data"] for e in store.job_events("lumber") if e["kind"] == "travel"]
    outs = [(d["spot"], d["book"], (d.get("landing") or {}).get("source")) for d in trav if d["leg"] == "out"]
    check("out to 'sim' by our book's 'Sim Woods', home on foot after the dry leg (within home.NEAR_LANDING: no "
          "recall), out to 'sim2' by the home library's tome (landing source 'library', rune 286), home by our book",
          world.recalls_book == [BOOK_RUNE_POS] and world.recalls_out == [RUNE_POS] and world.tome_far == 0
          and world.recalls_home == [HOME_RUNE_POS] and len(outs) == 2
          and outs[0][0] == "sim" and outs[1] == ("sim2", f"0x{TOME:08X}", "library"),
          f"book {world.recalls_book} out {world.recalls_out} home {world.recalls_home} travel {outs}")
    hops = [e["data"] for e in store.job_events("lumber") if e["kind"] == "hop"]
    check("a `hop` job event from 'sim' with the hop to 'sim2' and the logs carried",
          any(h["from"] == "sim" and (h.get("hop") or {}).get("spot") == "sim2" and h["carried"] == one.get("logs")
              and h["why_not"] is None for h in hops), str(hops)[:800])
    oeps = ostore.episodes("lumber")
    check("--hops 0: the dry leg goes home through the room as before: one 'stored' row (dry, leg 1, no hop), "
          "every log in the chest, no hop job event with a hop",
          ocode == 0 and len(oeps) == 1 and oeps[0].get("outcome") == "stored" and oeps[0].get("dry") is True
          and oeps[0].get("leg") == 1 and "hop" not in oeps[0] and off.recalls_out == []
          and [k for k, _ in off.room_log] == ["exit", "enter"]
          and off.chest_stack is not None and off.chest_stack[1] == off.harvested > 0
          and not any(e["data"].get("hop") for e in ostore.job_events("lumber") if e["kind"] == "hop"),
          f"exit {ocode} {[(e.get('outcome'), e.get('dry'), e.get('leg'), e.get('hop')) for e in oeps]} "
          f"room {off.room_log} chest {off.chest_stack}\n{otext[-800:]}")
    store.close()
    ostore.close()


def unit_hit_verdict():
    """loop_lumber.hit_verdict: when creature damage sends us home instead of a run."""
    print("\n== hit_verdict: run from one creature at healthy hits, else home ==")
    import loop_lumber
    gz = object()

    def v(**kw):
        base = dict(hits=70, hits_max=100, recall_at=0.6, attackers=[gz], players=[], escapes=0,
                    since_run_s=None, rehit_s=10.0)
        return loop_lumber.hit_verdict(**{**base, **kw})
    check("one creature, hits 70/100: run", v() is None)
    check("hits exactly at the threshold (60/100): run; one below: home",
          v(hits=60) is None and "below 60%" in (v(hits=59) or ""))
    check("two creatures could have hit: home", "2 creatures" in (v(attackers=[gz, gz]) or ""))
    check("nothing in view could have: home", "no creature" in (v(attackers=[]) or ""))
    check("a hostile player in view: home", "hostile player" in (v(players=["Bastet"]) or ""))
    check("again 10 s after arriving from a walk-away: home; 11 s: a new run",
          "still taking damage" in (v(since_run_s=10.0) or "") and v(since_run_s=11.0) is None)
    check("while walking away: walk on after one hit that cost hits (even right after the last arrival); low hits "
          "still home",
          v(walking=True, since_run_s=1.0, walk_hits=1) is None and v(walking=True, hits=50) is not None)
    check("a second hit that cost hits on the same walk-away: home (it outranges the walk; live brackish water)",
          "while walking away" in (v(walking=True, walk_hits=2) or "") and v(walk_hits=2) is None)
    near, far = SimpleNamespace(distance=1), SimpleNamespace(distance=9)
    check("hit by a creature next to us while walking away: home (it caught up; live hoarfrost, a death); one "
          "farther off: walk on; next to us while chopping: a run",
          "caught up" in (v(walking=True, walk_hits=1, attackers=[near]) or "")
          and v(walking=True, walk_hits=1, attackers=[far]) is None and v(attackers=[near]) is None)
    check("escapes used up or a speech hold: home",
          "escapes" in (v(escapes=loop_lumber.ESCAPES_PER_TRIP) or "") and "speech hold" in (v(can_escape=False) or ""))


def unit_tree_rethink():
    """tree_rethink switches to a nearer tree only along a planned route, and next_stand then takes that tree (live
    witcher_98, lumber-20261005-181911-a999: a tree 5 tiles off up a cliff was "nearer and clear" 13 times in 2 min
    while next_stand, finding no route there, sent Dan back to the far trees each time)."""
    print("\n== tree_rethink: nearer by planned route, and next_stand takes the tree it switched to ==")
    import types
    import loop_lumber
    far, cliff, near = {"x": 40, "y": 0}, {"x": 5, "y": 0}, {"x": 8, "y": 2}
    cluster = [{"x": 41, "y": 1}, {"x": 39, "y": 1}, {"x": 40, "y": 2}]
    routes = {(40, 0): 41, (41, 1): 42, (39, 1): 40, (40, 2): 41, (8, 2): 30}   # tiles of each planned path
    plans = []

    def plan(st, goal, max_steps=None):
        plans.append(goal.center)
        n = routes.get(goal.center)
        return (None if n is None or (max_steps is not None and n - 1 > max_steps)
                else [(i, 0) for i in range(n)]), None
    fake = SimpleNamespace(
        tree_guards=lambda st, recent=True: [], add_local_trees=lambda trees: 0, out_of_reach=lambda x, y: True,
        tree_z_ok=lambda t: None, link=SimpleNamespace(pos=lambda st: (0, 0, 0), state=lambda: {}),
        mover=SimpleNamespace(plan=plan), no_route=set(), switch_tree=None, dropped_trees={}, avoided=set(),
        creature={"avoided_trees": 0}, human=SimpleNamespace(rng=SimpleNamespace(uniform=lambda a, b: 1.0)))
    for name in ("tree_rethink", "next_stand", "no_route_tree", "tree_route_max"):
        setattr(fake, name, types.MethodType(getattr(loop_lumber.LumberLoop, name), fake))
    recheck, loop_lumber.TREE_RECHECK_S = loop_lumber.TREE_RECHECK_S, 0
    try:
        trees = [cliff, *cluster]
        why = fake.tree_rethink((40, 0), trees)({})
        check("a nearer tree no route reaches: walk on, and it leaves the candidates for the trip",
              why is None and cliff not in trees and (5, 0) in fake.no_route, repr(why))
        plans.clear()
        check("asked again: no plan for it", fake.tree_rethink((40, 0), trees)({}) is None and (5, 0) not in plans)
        trees.append(near)
        why = fake.tree_rethink((40, 0), trees)({}) or ""
        check("a nearer tree with a route 11 steps shorter: switch", "8,2 is nearer and clear (29 steps)" in why, why)
        pick = fake.next_stand(trees)
        check("next_stand takes the tree switched to, though the far cluster costs less per tree",
              pick is near and near not in trees and fake.switch_tree is None, repr(pick))
        trees.append(near)
        check("without a switch it takes the cluster", fake.next_stand(trees) in cluster)
        routes[(8, 2)] = 36
        check("a nearer tree whose route is only 5 steps shorter: walk on",
              fake.tree_rethink((40, 0), [near, *cluster])({}) is None)
    finally:
        loop_lumber.TREE_RECHECK_S = recheck


def unit_home_on_abort():
    """Live 2026-10-06 (lumber-20261006-101348-70b3): the run aborted with Dan already at a hot landing and exited
    there; a snow elemental killed him 15 s later. A plain abort away from home now recalls home and stores first;
    at home, dead, or without a book it does nothing."""
    print("\n== an abort away from home: recall home, then the room; never left standing in the field ==")
    import loop_lumber
    calls = []

    def recall_out(st, a, worst, swung, pk=True, why=None, what=None, attempts=None):
        calls.append(("recall", pk, why))
        raise loop_lumber.Unsafe("escaped by recall to (4134, 1429)")
    state = {"world": {"self": {"dead": False}}}
    home = [False]
    fake = SimpleNamespace(
        recall_book=0x49865F8F, link=SimpleNamespace(state=lambda: state), at_home=lambda st: home[0],
        watch=SimpleNamespace(update=lambda st, **kw: SimpleNamespace(dead=False)), recall_out=recall_out,
        home_after_recall=lambda e, timed: calls.append(("room", str(e))))
    loop_lumber.LumberLoop.home_on_abort(fake, loop_lumber.Abort("recall to 'X' not possible"), None)
    check("away from home: one recall (a creature-style one, not pk), then the room phase",
          [c[0] for c in calls] == ["recall", "room"] and calls[0][1] is False and "abort away from home" in calls[0][2],
          str(calls))
    calls.clear()
    home[0] = True
    loop_lumber.LumberLoop.home_on_abort(fake, loop_lumber.Abort("x"), None)
    home[0], state["world"]["self"]["dead"] = False, True
    loop_lumber.LumberLoop.home_on_abort(fake, loop_lumber.Abort("x"), None)
    check("at home, or dead: nothing", calls == [], str(calls))


def unit_zone_view_edge():
    """Live 2026-10-05 (witcher_253): a giant rat at the edge of view came into view three tiles west and left it three
    tiles east; its zone came and went with it and the walk swung between the two until boxed in. A creature that
    leaves the view from its edge keeps its zone for routes at its last tile for ROUTE_ZONE_S, without asking for a
    replan; one that vanished nearby (despawned, hidden) leaves none."""
    print("\n== a creature walked out of view keeps its route zone for ROUTE_ZONE_S ==")
    import types
    import loop_lumber
    rat = SimpleNamespace(serial=0x222, name="a giant rat", body=0xD7, kind="monster", hostile=False,
                          aggression="default", distance=17, flee_radius=8)
    mover = SimpleNamespace(danger={}, replan_requested=False)
    fake = SimpleNamespace(last_threats=SimpleNamespace(threats=[rat]), recent_guards={}, mover=mover,
                           watch=threats.Watch(), link=SimpleNamespace(pos=lambda st: (100, 100, 0, 0)))
    for name in ("tree_guards", "may_aggro", "aggro_r", "zone_r", "aggro_zones"):
        setattr(fake, name, types.MethodType(getattr(loop_lumber.LumberLoop, name), fake))
    seen = {"world": {"mobiles": {"0x00000222": {"x": 117, "y": 100}}}}
    gone = {"world": {"mobiles": {}}}
    fake.aggro_zones(seen)
    check("in view: its zone, and a replan for the new zone", mover.danger.get(("seen", 0x222)) == ((117, 100), 13)
          and mover.replan_requested, str(mover.danger))
    mover.replan_requested = False
    fake.aggro_zones(gone)
    check("out of view: the zone stays at its last tile, no replan",
          mover.danger.get(("seen", 0x222)) == ((117, 100), 13) and not mover.replan_requested, str(mover.danger))
    t, xy, r, _ = fake.recent_guards[0x222]
    fake.recent_guards[0x222] = (t, xy, r, time.monotonic() - loop_lumber.ROUTE_ZONE_S - 1)
    fake.aggro_zones(gone)
    check("after ROUTE_ZONE_S: gone", ("seen", 0x222) not in mover.danger, str(mover.danger))
    near = SimpleNamespace(serial=0x223, name="a wolf", body=0xE1, kind="monster", hostile=False,
                           aggression="default", distance=6, flee_radius=8)
    fake.last_threats = SimpleNamespace(threats=[near])
    fake.aggro_zones({"world": {"mobiles": {"0x00000223": {"x": 106, "y": 100}}}})
    fake.last_threats = SimpleNamespace(threats=[])
    fake.aggro_zones(gone)
    check("one that vanished 6 tiles off (despawned, hidden: not walked out of view) leaves no route zone",
          ("seen", 0x223) not in mover.danger, str(mover.danger))
    # live 2026-10-05 (Sacred Pools): landed 6 tiles from a headless; the zone shrank to 5, then a tile more per step in
    head = SimpleNamespace(serial=0x224, name="a headless", body=0x1F, kind="monster", hostile=False,
                           aggression="default", distance=6, flee_radius=8)
    fake.last_threats = SimpleNamespace(threats=[head])
    at = [(100, 100)]
    fake.link = SimpleNamespace(pos=lambda st: (*at[0], 0, 0))
    hl = {"world": {"mobiles": {"0x00000224": {"x": 106, "y": 100}}}}
    fake.aggro_zones(hl)
    first = mover.danger.get(("seen", 0x224))
    at[0] = (101, 100)                                   # a step toward it (5 tiles): it didn't move
    fake.aggro_zones(hl)
    check("a zone we stand in shrinks to just inside us once, not a tile more per step in",
          first == ((106, 100), 5) and mover.danger.get(("seen", 0x224)) == ((106, 100), 5), str(mover.danger))


def unit_boxed_in():
    """User 2026-10-05 (witcher_137, surrounded by mobs on all sides): "He needs to recall home and mark this place
    unworkable". boxed_in disables the spot in the store (lumber_opt plans no trip there), tells the overseer, and
    hands over to monster_stop (run out of reach, recall home)."""
    print("\n== boxed in by creatures: the spot disabled, the overseer told, home by monster_stop ==")
    import loop_lumber
    import lumber_opt
    mem = memory.Memory(os.path.join(tempfile.mkdtemp(), "m.db"))
    stops = []
    wolf = SimpleNamespace(serial=0x111, name="a dire wolf", distance=6)
    fake = SimpleNamespace(
        link=SimpleNamespace(state=lambda: {}, pos=lambda st: (100, 100, 0, 0)),
        watch=SimpleNamespace(update=lambda st, **kw: SimpleNamespace(dead=False)),
        tree_guards=lambda st, recent=True: [(wolf, (106, 100), 13)], k={"spot": {"id": "witcher_137"}},
        memory=mem, trip_n=1, monster_stop=lambda st, a, worst, swung, why: stops.append((worst, why)))
    loop_lumber.LumberLoop.boxed_in(fake, "boxed in by creatures (11 replans round them without getting nearer)")
    row = next((r for r in mem.lumber_spot_rows() if r["id"] == "witcher_137"), None)
    check("the spot is disabled with the reason (out of the planner's picks)",
          row is not None and row["status"] == "disabled" and "boxed in" in (row["reason"] or "")
          and "a dire wolf" in (row["reason"] or "")
          and lumber_opt.load_spots(mem).get("witcher_137", {}).get("status") == "disabled", str(row))
    js = [j for j in mem.junctures() if j["kind"] == "stuck"]
    check("an attention `stuck` juncture says the spot is disabled, with the creatures",
          len(js) == 1 and js[0]["severity"] == "attention" and js[0]["data"]["disabled"] is True
          and js[0]["data"]["creatures"][0]["name"] == "a dire wolf", str(js))
    check("then monster_stop with the nearest creature: out of reach and recall home",
          stops == [(wolf, "boxed in by creatures (11 replans round them without getting nearer)")], str(stops))
    mem.close()


def unit_run_and_recall():
    """run_and_recall never stops in the field (user, 2026-10-05: "If we fail to recall, we should just run away"):
    run, one cast, run again; with nothing after us, a stand-and-recast try, the next one RECALL_RETRY_S later
    unless something comes after us; the overseer hears of it after RECALL_ALERT_TRIES failures; only a recall that
    lands or death ends it."""
    print("\n== run_and_recall: run, cast, run again until a recall lands; the overseer told after 2 failures ==")
    import types
    import loop_lumber
    log_, juncs = [], []
    after_us = [True, True, False, False, True]       # gain_distance: something within RECALL_GAP before each try
    outcome = {"casts": 0, "land_at": 4, "die_at": None, "pk": []}
    state = {"dead": False}

    def gain_distance(st, a, swung, players=False):
        ran = after_us.pop(0) if after_us else False
        log_.append("run" if ran else "look")
        return ran

    def recall_out(st, a, worst, swung, pk=True, why=None, what=None, attempts=None):
        outcome["casts"] += 1
        outcome["pk"].append(pk)
        log_.append(f"cast:{attempts}")
        if outcome["casts"] == outcome["land_at"]:
            raise loop_lumber.Unsafe("escaped by recall")
        if outcome["casts"] == outcome["die_at"]:
            state["dead"] = True
        return "recall failed after 1 cast(s): disturbed"
    fake = SimpleNamespace(
        gain_distance=gain_distance, recall_out=recall_out, creature={"recalled": False, "recall_fails": 0},
        link=SimpleNamespace(state=lambda: {}, pos=lambda st: (5, 6, 0, 0)),
        watch=SimpleNamespace(update=lambda st, **kw: SimpleNamespace(dead=state["dead"])),
        memory=SimpleNamespace(juncture=lambda *a: juncs.append(a)), trip_n=1, k={"spot": {"id": "sim"}})
    fake.threat_name = loop_lumber.LumberLoop.threat_name
    fake.run_and_recall = types.MethodType(loop_lumber.LumberLoop.run_and_recall, fake)
    retry, look = loop_lumber.RECALL_RETRY_S, loop_lumber.KEEP_RUNNING_LOOK_S
    loop_lumber.RECALL_RETRY_S, loop_lumber.KEEP_RUNNING_LOOK_S = 5.0, 0.05
    try:
        landed = False
        try:
            fake.run_and_recall({}, SimpleNamespace(dead=False), None, {}, "it followed us")
        except loop_lumber.Unsafe:
            landed = True
        check("a run before each one-cast try while something is after us; with nothing after us a stand-and-recast "
              "try, then a look-around (no cast) until something came after us again: a run, a cast that landed",
              landed and log_ == ["run", "cast:1", "run", "cast:1", "look", "cast:None", "look", "run", "cast:1"]
              and fake.creature["recalled"] is True and fake.creature["recall_fails"] == 3, str(log_))
        check("one urgent keep_running threat juncture, after the second failure",
              len(juncs) == 1 and juncs[0][1] == "threat" and juncs[0][3] == "urgent"
              and juncs[0][4]["action"] == "keep_running" and juncs[0][4]["fails"] == 2, str(juncs))
        log_.clear(), juncs.clear()
        after_us[:] = [True, True, True]
        outcome.update(casts=0, land_at=None, die_at=3)
        fake.creature["recalled"] = False
        fake.run_and_recall({}, SimpleNamespace(dead=False), None, {}, "it followed us")
        check("death ends it (the caller then stops)", outcome["casts"] == 3 and not fake.creature["recalled"],
              str(log_))
        log_.clear(), juncs.clear()
        after_us[:] = [True, True, True, True]
        outcome.update(casts=0, land_at=None, die_at=None, pk=[])
        fake.creature.update(recalled=False, recall_fails=0)
        state["dead"] = False
        why = fake.run_and_recall({}, SimpleNamespace(dead=False), None, {}, None, players=True)
        check(f"players (option E): run, a pk cast, run again; after {loop_lumber.PLAYER_RECALL_TRIES} failed casts it "
              "returns why (the guard flight follows), no keep_running juncture, the creature tally untouched",
              log_ == ["run", "cast:1"] * loop_lumber.PLAYER_RECALL_TRIES and outcome["pk"] == [True] * 3
              and why and "disturbed" in why and juncs == [] and fake.creature["recall_fails"] == 0,
              f"{log_} {outcome} {why} {juncs}")
    finally:
        loop_lumber.RECALL_RETRY_S, loop_lumber.KEEP_RUNNING_LOOK_S = retry, look


def unit_hatchet():
    """loop_lumber hatchet(): the best tool bonus by material (hue; the shelf hands out GM coloured ones, user
    2026-10-05), worn first among equals, then the shallowest in the pack; never the bank box."""
    print("\n== hatchet(): the best material, worn first among equals, else the shallowest in the backpack's bags ==")
    import loop_lumber
    import lumber_opt
    from agent_link import Abort
    inner, bankbox = 0x44ADC0DF, 0x40000B0B                 # a bank box worn by us (layer 0x1D): outside the pack
    base = {BACKPACK: {"graphic": 0x0E75, "layer": 0x15, "container": SELF},
            BAG: {"graphic": 0x0E76, "container": BACKPACK},
            inner: {"graphic": 0x0E76, "container": BAG},
            bankbox: {"graphic": 0x0E7C, "layer": 0x1D, "container": SELF}}
    hatchets = {"worn": (0x4001, SELF), "bag": (0x4002, BAG), "inner": (0x4003, inner), "bank": (0x4004, bankbox)}
    loop = loop_lumber.LumberLoop.__new__(loop_lumber.LumberLoop)
    loop.hatchets = lumber_opt.load_hatchets()
    bronze, copper, valorite = 2418, 2413, 2219              # hatchets.json hues (bronze seen live 2026-10-05)

    def pick(*which, hue=None):
        hue = hue or {}
        items = {**base, **{hatchets[w][0]: {"graphic": 0x0F43, "container": hatchets[w][1], "hue": hue.get(w, 0)}
                            for w in which}}
        st = {"movement": {"self_serial": SELF},
              "world": {"items": {f"0x{s:08X}": dict(it, container=f"0x{it['container']:08X}")
                                  for s, it in items.items()}}}
        try:
            return loop.hatchet(st)
        except Abort:
            return None
    check("same material: worn beats any in the pack", pick("inner", "bag", "worn", "bank") == hatchets["worn"][0])
    check("the shallowest bag wins", pick("inner", "bag", "bank") == hatchets["bag"][0])
    check("found at any depth", pick("inner", "bank") == hatchets["inner"][0])
    check("one in the bank box doesn't count", pick("bank") is None)
    check("a bronze one in a bag beats a worn iron one; copper deeper loses to bronze shallower",
          pick("worn", "bag", hue={"bag": bronze}) == hatchets["bag"][0]
          and pick("bag", "inner", hue={"bag": bronze, "inner": copper}) == hatchets["bag"][0])
    check("a valorite one in the bank box still doesn't count",
          pick("worn", "bank", hue={"bank": valorite}) == hatchets["worn"][0])


def unit_recall_reagents():
    """A recall cast by the spell on the book spends Recall's reagents like one from the gump: the ledger must not
    call them theft (live 2026-10-05, run lumber-20261005-124519-d565: theft_suspected for 1 black pearl, blood
    moss and mandrake root right after the escape's spell_on_book recall)."""
    print("\n== expect_casts: reagents spent by spell and spell_on_book recalls aren't theft ==")
    import combat
    import ledger as ledger_mod
    import loop_lumber
    regs = combat.SPELL_REAGENTS[loop_lumber.escape_mod.RECALL]

    def state(n):
        items = {f"0x{BACKPACK:08X}": {"graphic": 0x0E75, "layer": 0x15, "container": f"0x{SELF:08X}"},
                 **{f"0x4000{i:04X}": {"graphic": g, "amount": n, "container": f"0x{BACKPACK:08X}"}
                    for i, g in enumerate(regs)}}
        return {"movement": {"self_serial": SELF}, "world": {"items": items, "self": {"serial": f"0x{SELF:08X}"}}}
    for method in ("spell", "spell_on_book"):
        loop = loop_lumber.LumberLoop.__new__(loop_lumber.LumberLoop)
        loop.ledger = ledger_mod.Ledger()
        loop.ledger.observe(state(10))
        loop.expect_casts({"tries": [{"method": "charge", "failure": "disturbed"},
                                     {"method": method, "failure": None}]})
        d = loop.ledger.observe(state(9))
        check(f"one {method} recall: one of each reagent spent, no theft", not d.theft_suspected,
              str(d.unexplained_losses))


# unit_capture_*: packets captured live in session 20261003_213125 (Hackworth 0x0020F127; offsets in s):
#   CAP_WITCHER      witcher_280 (Nusero Island SW) from 22:17:28.0: the recall's arrival, a starling, a
#                    cougar, an eagle, a gazer larva and a raven in view; at +4.136 the larva's spell on
#                    Magic Reflection ("Magic reflect removed.", 0xC0 0x37B9 on us, its bolt reflected onto
#                    it); at +7.869 its next spell (0x374A, "Spell siphon active."), "-14" at +8.391
#   CAP_TRIP1_START  before trip 1 at Horseshoe Bay (from 21:43:43): Hackworth's 0x78 (hatchet 0x5957DE03 in
#                    hand), the Magic Reflection cast moving it to the pack (0x1D + 0x25), the Magic
#                    Reflection and Tracking Hunting buffs
#   CAP_TRIP1_CHOP   the first chop's double-click (21:44:39): 0x1D + 0x2E, in hand again
#   CAP_HB_PLAYERS   'a stinky mongbat' / 'a wet mongbat' at the HB bank (21:43:00)
from types import SimpleNamespace  # noqa: E402
import lumber_opt  # noqa: E402
import threats  # noqa: E402
from loop_lumber import LumberLoop, PLAYER_RECALL_GAP, hit_verdict, in_hand, row_hatchet  # noqa: E402
from uo import cliloc as cliloc_mod  # noqa: E402  (the simulator's cliloc() builds packets)
from world.runtime import WorldRuntime  # noqa: E402

CAP_ME = 0x0020F127
CAP_LARVA, CAP_COUGAR, CAP_RAVEN = 0x0042E6DB, 0x0007031B, 0x00904470
CAP_HATCHET = 0x5957DE03
CAP_STINKY, CAP_WET = 0x003D56E5, 0x003DB217
CAP_TREE_TILE = (335, 2050)        # trip 2's log: "to tree 334,2051: arrived at (335, 2050)"
CAP_HB_BANK = (2008, 2222)         # Sun the banker's bank, where trip 1 started
CAP_RECALL_S, CAP_MARGIN_S = 2.0, 1.0  # loop_lumber RECALL_S, THREAT_MARGIN_S
CAP_RECALL_AT = 0.6                # loop_lumber --creature-recall-at default
CAP_REHIT_S = 10.0                 # loop_lumber --creature-rehit-s default
CAP_WITCHER = [
    (-2760.918, "1b0020f1270000000000000190000000c30000068d000000018600ffffffff000000000a00080000000000"),
    (-28.661, "11005b0020f1274861636b776f727468000000000000000000000000000000000000000000006400640005000064002600570026002600570057000000000000000f023a0100000005000000000000000000000002000800000000"),
    (0.655, "770020f1270000014e000007fa0000000581"),
    (0.657, "20001b08c6000000060308400000000160000007eb0000030000000c"),
    (0.657, "78000b001b08c600000000"),
    (0.657, "200007031b000000d603096e0000000146000007f600000400000005"),
    (0.657, "78000b0007031b00000000"),
    (0.657, "20006bcfd200000005030000000000014f000007f100000600000005"),
    (0.657, "78000b006bcfd200000000"),
    (0.657, "200042e6db0000030a0300000000000145000007fa00000200000005"),
    (0.657, "78000b0042e6db00000000"),
    (0.657, "2000904470000000060309010000000155000008010000020000000c"),
    (0.657, "78000b0090447000000000"),
    (0.658, "200020f127000001900183ea200000014e000007fa00008100000005"),
    (0.726, "1c0037001b08c600060603b200036120737461726c696e6700000000000000000000000000000000000000006120737461726c696e6700"),
    (0.726, "11002b001b08c66120737461726c696e670000000000000000000000000000000000000000006400640000"),
    (0.726, "1c00350007031b00d60603b200036120636f75676172000000000000000000000000000000000000000000006120636f7567617200"),
    (0.726, "11002b0007031b6120636f7567617200000000000000000000000000000000000000000000006400640000"),
    (0.726, "1c0035006bcfd200050603b20003616e206561676c6500000000000000000000000000000000000000000000616e206561676c6500"),
    (0.727, "11002b006bcfd2616e206561676c6500000000000000000000000000000000000000000000006400640000"),
    (0.727, "1c003a0042e6db030a0603b20003612067617a6572206c617276610000000000000000000000000000000000612067617a6572206c6172766100"),
    (0.727, "11002b0042e6db612067617a6572206c617276610000000000000000000000000000000000006400640000"),
    (0.727, "1c00340090447000060603b200036120726176656e00000000000000000000000000000000000000000000006120726176656e00"),
    (0.727, "11002b009044706120726176656e0000000000000000000000000000000000000000000000006400640000"),
    (1.722, "770007031b00000146000007f70000000504"),
    (1.925, "770090447000000156000008010000000a02"),
    (2.039, "770007031b00000146000007f80000000504"),
    (2.039, "77006bcfd20000014f000007f00000000500"),
    (2.392, "200042e6db0000030a0400004000000145000007fa00000300000005"),
    (2.392, "770042e6db00000145000007fa0000000503"),
    (2.392, "200020f127000001900183ea200000014f000007fe0000830000000a"),
    (3.389, "1d006bcfd2"),
    (4.136, "ae005effffffffffff0003b20003454e550053797374656d000000000000000000000000000000000000000000000000004d00610067006900630020007200650066006c006500630074002000720065006d006f007600650064002e0000"),
    (4.136, "c0030020f1270020f127000037b90000014f000008020000000c0000014f000008020000000c0a05000001000000000000000000"),
    (4.136, "c0010042e6db0042e6db0000000000000145000007fa0000000500000145000007fa000000050000000000000000000000000000"),
    (4.385, "770007031b00000146000007f90000000504"),
    (4.389, "a10042e6db00640063"),
    (4.755, "770007031b00000146000007fa0000000504"),
    (4.788, "1d5957de03"),
    (4.79, "11005b0020f1274861636b776f7274680000000000000000000000000000000000000000000064006400050000640026005700260026004e0057000000000000000c023a0100000005000000000000000000000011002000000000"),
    (4.79, "2e5957de0300000f4400000020020020f1270000"),
    (7.041, "770090447000000157000008010000000a02"),
    (7.097, "770007031b00000147000007fb0000000503"),
    (7.097, "770007031b00000147000007fb0000000503"),
    (7.869, "c0030042e6db0042e6db0000374a00000145000007fa0000000500000145000007fa000000050a0f000001000000000000000000"),
    (7.869, "c0030020f1270020f1270000374a0000014f000008020000000c0000014f000008020000000c0a0f000001000000000000000000"),
    (7.869, "ae005c0020f12701900003b20003454e55004861636b776f727468000000000000000000000000000000000000000000002a005300700065006c006c00200053006900700068006f006e0020004100630074006900760065002a0000"),
    (7.869, "ae005affffffffffff0003b20003454e550053797374656d000000000000000000000000000000000000000000000000005300700065006c006c00200073006900700068006f006e0020006100630074006900760065002e0000"),
    (8.391, "ae00380020f12701900003b20003454e55004861636b776f727468000000000000000000000000000000000000000000002d003100340000"),
    (8.393, "a10020f12700640056"),
]
CAP_TRIP1_START = [
    (-735.918, "1b0020f1270000000000000190000000c30000068d000000018600ffffffff000000000a00080000000000"),
    (-140.098, "7800650020f1274b6aa30500000e75150000000002004b6aa30600001517050002000000204b6aa3070000152e180002000000204b6aa3080000170f030000000000205957de0300000f44020000000000207f7c37630000203c0b04550000000000000000"),
    (-25.496, "1d5957de03"),
    (-25.486, "255957de0300000f44020001005b005a004b6aa305000000000020"),
    (-23.981, "ff0039000000080020f127008a755900000000000000010000000000000000000000000000000cb83507d700000fefc0000000000000000000"),
    (6.17, "ff0039000000080020f12700ad11ac00020000000000010000000000000000000000000000000cb8357d9e000010eff4000000000000000000"),
]
CAP_TRIP1_CHOP = [
    (56.11, "1d5957de03"),
    (56.115, "2e5957de0300000f4400000020020020f1270000"),
]
CAP_HB_PLAYERS = [
    (-42.677, "20003d56e5000001900183ea20000007cd000008ad00008200000012"),
    (-42.677, "780074003d56e556e205b600000e7515000000000200549e001100003e9f1907360000000056e205b7000015170500020000002056e205b8000015391800020000002056e205b90000170f030000000000206758e79100000a222a0000000000207f0aa06b0000203b0b044e0000000000000000"),
    (-42.602, "1c003d003d56e50190060059000361207374696e6b79206d6f6e67626174000000000000000000000000000061207374696e6b79206d6f6e6762617400"),
    (-42.463, "20003db217000001900183ea20000007ce000008ab00008200000012"),
    (-42.463, "780056003db217570b20b000000e751500000000020056814e1400003ea01907120000000056d4167f000013ce07000000000020570b20b200001539180002000000207f0933a30000203b0b044e0000000000000000"),
    (-42.358, "1c003a003db217019006005900036120776574206d6f6e6762617400000000000000000000000000000000006120776574206d6f6e6762617400"),
]



def _eq(name, got, want):
    check(name, got == want, "" if got == want else f"(got {got!r}, want {want!r})")


class CaptureFeed:
    """A WorldRuntime fed captured S2C packets on their own clock, keeping each
    event's time like the proxy's event envelopes."""

    def __init__(self):
        self.now = None
        self.rt = WorldRuntime(clock=lambda: self.now)
        self.events = []        # [(t, event)]

    def feed(self, rows, until=None):
        for t, hexpkt in rows:
            if until is not None and t > until:
                continue
            self.now = t
            n = len(self.rt.events)
            self.rt.feed_packet("s2c", bytes.fromhex(hexpkt))
            self.events += [(t, ev) for ev in self.rt.events[n:]]
        return self

    def state(self, pos, since=None):
        """A state-port response: movement truth (pos), the world snapshot, and the
        events from `since` on."""
        evs = [(t, ev) for t, ev in self.events if since is None or t >= since]
        return {"movement": {"pos": [*pos, 0, 0], "self_serial": CAP_ME},
                "world": self.rt.state.snapshot(),
                "events": [{"seq": i, "t": t, "origin": "world", "data": ev} for i, (t, ev) in enumerate(evs)]}


def _threat(a, serial):
    return next(t for t in a.threats if t.serial == serial)


def _verdict(a, attackers, since_run_s=None):
    return hit_verdict(hits=a.damage["hits"], hits_max=a.damage["hits_max"], recall_at=CAP_RECALL_AT,
                       attackers=attackers,
                       players=[], escapes=0, since_run_s=since_run_s, rehit_s=CAP_REHIT_S)


def unit_capture_spell_witcher():
    print("\n== witcher_280: the larva's spell on Magic Reflection is an attack (gap: 7.75 s to react) ==")
    w = threats.Watch()
    f = CaptureFeed().feed(CAP_WITCHER, until=4.0)
    a = w.update(f.state(CAP_TREE_TILE), recall_s=CAP_RECALL_S, margin_s=CAP_MARGIN_S, now=4.0)
    larva = _threat(a, CAP_LARVA)
    _eq("before the spell: the war-mode larva 10 tiles off is only watched (ETA 3.6 s > 3.0 s)",
       (larva.distance, larva.aggression, larva.action, a.under_attack), (10, "war mode", "watch", False))
    f.feed(CAP_WITCHER, until=4.2)
    spells = [(t, threats.spell_on_us(ev, CAP_ME)) for t, ev in f.events if t > 4.0]
    _eq("4.136: the System line and the 0x37B9 effect on us are spells on us; the reflected bolt on the "
       "larva is not", [s for t, s in spells if s[0]], [(True, None), (True, None)])
    st = f.state(CAP_TREE_TILE, since=4.0)
    a = w.update(st, recall_s=CAP_RECALL_S, margin_s=CAP_MARGIN_S, now=4.2)
    _eq("4.2: under attack with no hits lost (Magic Reflection took it), 2 spell events",
       (a.under_attack, a.damage["lost"], a.damage["spells"]), (True, 0, 2))
    attackers, ranged = threats.hit_attackers(a, w.params)
    _eq("who cast it: the larva alone, from afar (the cougar at 9, aggression unknown, isn't blamed)",
       ([t.serial for t in attackers], ranged), ([CAP_LARVA], True))
    _eq("one attacker at 100/100: run (walk out of its reach) at 4.14 s, not the recall at the -14 (8.39 s)",
        _verdict(a, attackers), None)


def unit_capture_juncture_222():
    print("\n== juncture 222: '2 creatures attacking' while attackers was [] ==")
    w = threats.Watch()
    f = CaptureFeed().feed(CAP_WITCHER, until=4.2)
    w.update(f.state(CAP_TREE_TILE), recall_s=CAP_RECALL_S, margin_s=CAP_MARGIN_S, now=4.2)
    w.acknowledge(now=4.2)                      # the first spell is dealt with: the walk-away
    f.feed([r for r in CAP_WITCHER if r[0] > 4.2], until=8.4)
    a = w.update(f.state(CAP_TREE_TILE, since=4.2), recall_s=CAP_RECALL_S, margin_s=CAP_MARGIN_S, now=8.4)
    _eq("8.4: -14 and the second spell", (a.damage["lost"], a.damage["spells"]), (14, 2))
    _eq("the cougar 8 tiles off is 'passive creature (default)', the raven a passive body",
       (_threat(a, CAP_COUGAR).distance, _threat(a, CAP_COUGAR).reason, _threat(a, CAP_RAVEN).reason),
       (8, "passive creature (default)", "passive creature (passive body)"))
    attackers, ranged = threats.hit_attackers(a, w.params)
    _eq("the hit's attackers: the larva only (before: larva + cougar, '2 creatures attacking')",
       [t.serial for t in attackers], [CAP_LARVA])
    _eq("so the verdict is the re-hit soon after the walk-away (4.2 s), still a recall home",
       _verdict(a, attackers, since_run_s=4.2), "still taking damage 4.2 s after the walk-away")
    loop = SimpleNamespace(hit_by=[t.serial for t in attackers])
    _eq("the juncture's `attackers` lists who creature_hit blamed (no 0x2F swings on Outlands)",
       LumberLoop.attacker_list(loop, {}), ["0x0042E6DB"])
    _eq("swingers and blamed ones together, once each",
       LumberLoop.attacker_list(SimpleNamespace(hit_by=[CAP_LARVA]), {CAP_LARVA: 1.0, 0x500: 2.0}),
       ["0x0042E6DB", "0x00000500"])


def unit_capture_hatchet():
    print("\n== trip 1: hatchet 'worn: False' while chopping worked ==")
    f = CaptureFeed().feed(CAP_TRIP1_START)
    world = f.state(CAP_HB_BANK)["world"]
    ch = lumber_opt.character(world, CAP_ME, lumber_opt.load_hatchets())
    start = next(hh for hh in ch["hatchets"] if hh["serial"] == f"0x{CAP_HATCHET:08X}")
    _eq("at the trip start the Magic Reflection cast had put it in the pack (0x1D + 0x25)",
       (start["worn"], in_hand(world, CAP_HATCHET, CAP_ME)), (False, False))
    f.feed(CAP_TRIP1_CHOP)
    world = f.state(CAP_HB_BANK)["world"]
    _eq("the first chop's double-click: the server equips it (0x1D + 0x2E layer 2 on us)",
       in_hand(world, CAP_HATCHET, CAP_ME), True)
    row = row_hatchet(start, True)
    _eq("the trip row records it in hand while chopping, the start reading kept",
       (row["worn"], row["worn_at_start"], row["serial"]), (True, False, f"0x{CAP_HATCHET:08X}"))
    _eq("a trip that never chopped keeps the start reading", row_hatchet(start, None), start)
    _eq("no hatchet: nothing to record", row_hatchet(None, True), None)


def unit_capture_buffs():
    print("\n== trip row buffs: names, not cliloc ids ==")
    world = CaptureFeed().feed(CAP_TRIP1_START).state(CAP_HB_BANK)["world"]
    ch = lumber_opt.character(world, CAP_ME, lumber_opt.load_hatchets())
    if not os.path.exists(cliloc_mod.CLILOC_PATH):
        _eq("no Cliloc.enu here: the numbers", ch["buffs"], ["1044416", "1110004"])
        return
    _eq("Magic Reflection and Tracking Hunting (empty titles, clilocs 1044416 / 1110004)",
       ch["buffs"], ["Magic Reflection", "Tracking Hunting"])
    _eq("a titled buff keeps its title", lumber_opt.buff_name(277, {"title": "Stationary Penalty"}),
       "Stationary Penalty")


def unit_capture_named_players():
    print("\n== 'a stinky mongbat' / 'a wet mongbat' at the HB bank are players, not pets ==")
    f = CaptureFeed().feed(CAP_TRIP1_START).feed(CAP_HB_PLAYERS)
    st = f.state(CAP_HB_BANK)
    a = threats.assess(st, recall_s=CAP_RECALL_S, margin_s=CAP_MARGIN_S, now=0.0)
    got = [(_threat(a, s).name, _threat(a, s).kind, _threat(a, s).player) for s in (CAP_STINKY, CAP_WET)]
    _eq("human body 0x190, player flag 0x20, notoriety 1: blue players", got,
       [("a stinky mongbat", "blue", True), ("a wet mongbat", "blue", True)])
    items = st["world"]["items"].values()
    worn = {s: {it.get("layer") for it in items if it.get("container") == f"0x{s:08X}"} for s in (CAP_STINKY, CAP_WET)}
    check("each wears a backpack (0x15) and rides a mount (0x19), no pet line: a player's character",
          all({0x15, 0x19} <= v for v in worn.values())
          and not any(st["world"]["mobiles"][f"0x{s:08X}"].get("pet") for s in (CAP_STINKY, CAP_WET)), worn)


SH_TAG = "20261001_214649"   # Hackworth, human-driven; Smart Harvest self-targets at 23:20 and 23:47 (docs/NOTES.md)


def unit_capture_smart_harvest():
    """The runner's cursor answer (loop_lumber.self_target, from the state port's state and the
    cursor event) against the stock client's self-targets in the capture, replayed through the
    proxy's own SessionTap; and outcome() on what the server said after each."""
    print("\n== Smart Harvest: our self-target is the stock client's packet; 'nothing nearby' maps to move on ==")
    import loop_lumber
    know = json.load(open(f"{ROOT}/harness/data/loops/lumber.json", encoding="utf-8"))
    drv = viz_feed.ReplayDriver(SH_TAG, f"{ROOT}/logs")
    evs, seen, cursor = [], [], 0
    while drv.position < len(drv.items):
        t, kind, a, b = drv.items[drv.position]
        if kind == "c2s" and a == "client" and b[0] == 0x6C and b[1] == 0:
            st = drv.tap.state(1 << 62)
            if int.from_bytes(b[7:11], "big") == st["movement"]["self_serial"]:
                cur = next(e for e in reversed(evs) if e.get("ev") == "target")
                seen.append((t - drv.t0, b, loop_lumber.self_target(st, cur), len(evs), st))
        drv._advance(None, 1)
        tap = drv.tap
        evs += [env["data"] for env in tap.events[max(cursor - tap.events_base, 0):]]
        cursor = tap.events_base + len(tap.events)
    _eq("the client's self-targets: 23:20 at (1918,2612) and 23:47 at (1905,2616)",
        [(f"{int(o // 60)}:{int(o % 60):02d}", parse_packet("c2s", c)["x"], parse_packet("c2s", c)["y"])
         for o, c, *_ in seen], [("23:20", 1918, 2612), ("23:47", 1905, 2616)])
    for o, client, ours, _, st in seen:
        f = parse_packet("c2s", ours)
        check(f"{int(o // 60)}:{int(o % 60):02d}: ours == the client's, byte for byte "
              f"(type 0, serial 0x{f['serial']:08X}, x/y {f['x']},{f['y']}, z {f['z']}, graphic 0x{f['graphic']:04X})",
              ours == client and f["target_type"] == 0 and f["serial"] == 0x0020F127 and f["graphic"] == 0x0190,
              f"\n    client {client.hex()}\n    ours   {ours.hex()}")
    _eq("z comes from movement truth (0 there), not the world model's self z (10)",
        [(st["movement"]["pos"][2], st["world"]["self"]["z"]) for *_, st in seen], [(0, 10), (0, 10)])
    loop = SimpleNamespace(k=know, since=lambda m: evs[m:m + 3000])
    _eq("outcome(): 23:20 (captcha, solved, then 'nothing nearby') -> nothing_near; 23:47 -> fail (500495)",
        [LumberLoop.outcome(loop, mark) for *_, mark, _ in seen], [("nothing_near", 0), ("fail", 0)])
    said = [e for e in evs[seen[0][3]:] if e.get("ev") == "speech_heard" and e.get("text") in NOTHING_NEAR][:2]
    _eq("after 23:20 both 'nothing nearby' lines, as lumber.json has them: the system line, then ours overhead",
        [(e["text"], e["serial"]) for e in said], [(NOTHING_NEAR[0], 0xFFFFFFFF), (NOTHING_NEAR[1], 0x0020F127)])
    _eq("lumber.json's texts are the simulator's", tuple(know["harvest"]["nothing_near_texts"]), NOTHING_NEAR)
    _eq("facing_to quantizes like RunUO GetDirection (23:47: the one tree, SE of us)",
        [loop_lumber.facing_to((1905, 2616), b) for b in ((1906, 2617), (1905, 2610), (1908, 2617), (1906, 2619))],
        [3, 0, 2, 4])


def stand_events(store):
    """The runner's `stand` job events (one per stand: the Smart Harvest reach measurement)."""
    return [e["data"] for e in store.job_events("lumber") if e["kind"] == "stand"]


def state_req(req):
    import socket
    with socket.create_connection(("127.0.0.1", STATE_PORT), timeout=5) as s:
        s.sendall((json.dumps(req) + "\n").encode())
        return json.loads(s.makefile("rb").readline())


def dedupe(seq):
    return [k for i, k in enumerate(seq) if i == 0 or seq[i - 1] != k]


def is_subsequence(want, seq):
    it = iter(seq)
    return all(any(k == w for k in it) for w in want)


def run_parallel(names, jobs):
    """Each async scenario in its own `python test_loop_lumber.py <name>` (they already have private
    ports and temp dirs), at most `jobs` at a time; output printed per scenario in list order.
    Returns the names whose child failed."""
    import concurrent.futures

    def one(name):
        t = time.time()
        p = subprocess.run([PY, os.path.abspath(__file__), name], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", cwd=ROOT)
        return name, p.returncode, p.stdout + p.stderr, time.time() - t

    failed = []
    with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
        for name, code, out, secs in pool.map(one, names):
            print(out.rstrip().removesuffix("ALL PASS").rstrip())
            print(f"-- {name}: {'ok' if code == 0 else f'FAILED (exit {code})'} in {secs:.0f} s")
            if code != 0:
                failed.append(name)
    return failed


if __name__ == "__main__":
    runs = [main, skirmish, break_due, library, library_chased, track_reds, gazer_run, gazer_rehit, gazer_reflect,
            wary, idle_mob, zone_on_way, red_aim, faction, precast, flee_aid, thief_keep_away, pouch_pop, no_pouch,
            resupply,
            convert_stacks,
            landing_escape, stockpile_store,
            ghost_horse,
            staff_in_view,
            hop,
            unit_hatchet, unit_hit_verdict, unit_recall_reagents, unit_tree_rethink, unit_run_and_recall, unit_boxed_in,
            unit_zone_view_edge, unit_home_on_abort,
            unit_capture_spell_witcher, unit_capture_juncture_222,
            unit_capture_hatchet, unit_capture_buffs, unit_capture_named_players, unit_capture_smart_harvest]
    pick = set(sys.argv[1:])                 # optional: scenario names to run alone, e.g. `gazer_run wary`
    chosen = [fn for fn in runs if not pick or fn.__name__ in pick]
    scenarios = [fn.__name__ for fn in chosen if asyncio.iscoroutinefunction(fn)]
    jobs = int(os.environ.get("LOOP_TEST_JOBS", "8"))
    if len(scenarios) > 1 and jobs > 1:      # each in its own process, several at once
        FAILURES.extend(f"scenario {n}" for n in run_parallel(scenarios, jobs))
        chosen = [fn for fn in chosen if not asyncio.iscoroutinefunction(fn)]
    for fn in chosen:
        if asyncio.iscoroutinefunction(fn):
            asyncio.run(fn())
        else:
            fn()
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILURES: {FAILURES}"))
    sys.exit(0 if not FAILURES else 1)

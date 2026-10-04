"""Offline end-to-end test of the hunt loop runner (harness/loop_hunt.py).

A simulated New Player Dungeon behind the real proxy, with the packet shapes of the
live mongbat fights (session 20261001_214649; docs/NOTES.md "World model keeps dead
and out-of-range mobiles"):

- the character starts on the exit tile (5535,529); the exit is the step south
  (deny + teleport to 1911,2556, as live), the entrance the step north from
  (1912,2557) (confirm, then the teleport to 5536,530)
- mongbat S swings at us and is then killed by someone else: only 0xFF sub 0xDEAD,
  no 0x1D. It must never be targeted (nor its corpse looted)
- mongbat A (3 tiles off) answers the attack: it flies adjacent and hits hard once
  (the heal path: Greater Heal on self), then dies to Lightning or melee:
  corpse item, 0xAF + 0x1D + 0xDEAD; its corpse holds gold
- after A's loot two mongbats D and E come in swinging: two attackers below 80 %
  hits trigger the leave rule (`threat` juncture), the runner rests outside with a
  Greater Heal and goes back in. The server re-sends only D; E (pruned from the
  world model on leaving) must never be targeted again
- D dies; its gold is looted; with --kills 2 the runner leaves and ends outside
- the backpack holds a bag of the Heal / Greater Heal / Lightning reagents (the runner
  casts only what they pay for); the server answers the first Lightning with cliloc
  502630 "More reagents are needed" anyway: no Lightning again that visit, again after
  re-entering

Every attack/cast/target/loot packet is compared byte-wise with the combat.py /
actions.py builders.

Then the same NPD once more wielding the prismatic staff (an arcane staff whose casts put it in
the pack): melee only, potions before heal spells inside, re-equipped before going back in.

Then once more with --fight-spot FIGHT, 6 tiles SE of the exit spot, the mobs around it: attacks
only on the fight spot, both leaves walk back to the spot before the exit step, the episode rows
hold the route margin.

In every run the arrival tile (5536,530) is a teleporter (store and sim, as live) and the server
applies the Stationary Penalty as live (0xFF sub 8, icon 277, byte for byte as capture
20261003_123614; each step that changes our tile counts it down, the 5th removes it with sub 9):
at login, and in the fight-spot run once more when we first hurt A and after 14 s without a
step (--reposition-s 6). No attack goes out while it is on; the runner walks it off (5 + 1
steps out and back) without stepping onto a teleporter, and repositions before it comes.

Then a crawl (--crawl) through a multi-room NPD (CRAWL_FLOOR, walls deny steps; the store's walk
memory knows the floor): patrol through the rooms, zone 2 opened before room R2 is entered, a
troll there fled from (a survival leave from deep, back to the exit spot) and avoided afterwards,
room R1 left as depleted after its mongbat, two mongbats killed and looted (docs/HUNT_LOOP.md).

Then a recall run (--enter-recall / --leave-recall / --bank-gold): from town by the tome's rune to a
landing by a golden gate, out at once from a red in view and from "X is attacking you!", a refused
recall walked to the arrival, gold banked at home, back in after --pk-wait (notes above RECALL_S).

Then, in process (no proxy), the loot-rights run: a blue corpse skipped without a packet, a refused
open with no lift, a rejected lift not counted (docs/HUNT_LOOP.md "Loot rights").

Run: python test_loop_hunt.py [rights|default|staff|fight|crawl|recall]   (a few minutes for all;
     private ports; safe while the live proxy runs)
"""
import asyncio
import json
import os
import re
import subprocess
import sys
import struct
import tempfile
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
sys.path.insert(0, f"{ROOT}/harness")

import actions  # noqa: E402
import combat  # noqa: E402
import escape as escape_mod  # noqa: E402
import memory  # noqa: E402
import nav  # noqa: E402
from uo.packets import packet_length, C2S_OVERRIDES  # noqa: E402
from uo.s2c import encode_packet  # noqa: E402
from world.parsers import parse_packet  # noqa: E402

PROXY_PORT, UPSTREAM_PORT, CONTROL_PORT, STATE_PORT = 12680, 12681, 12682, 12683
LOGDIR = f"{ROOT}/logs_test_hunt"
C2S_KEY, S2C_KEY = 0x0F, 0x5A
PRELUDE = bytes([0xFF, 0x00, 0x0D] + [0] * 7 + [0x0C, S2C_KEY, C2S_KEY])
SELF, BACKPACK, PACK_GOLD = 0x00094375, 0x44ADA059, 0x44AD0001
BODY, MONGBAT = 0x190, 0x27
SPOT, EXIT_TILE, OUTSIDE = (5535, 529), (5535, 530), (1911, 2556)
ENTRY, INSIDE = (1912, 2557), (5536, 530)
FIGHT = (5541, 535)        # the fight-spot run: SE of the spot, the straight route crosses INSIDE
A, S, D, E = 0x002C1001, 0x002C1E27, 0x002C3FB9, 0x002C3FE9
POS = {A: (5536, 526), S: (5534, 527), D: (5534, 528), E: (5536, 528)}
HITS = {A: 80, S: 80, D: 200, E: 80}       # D lasts past the potion cooldown after re-entering
CORPSE = {A: 0x4FE00001, S: 0x4FE00003, D: 0x4FE00002}
GOLD = {A: (0x4FE10001, 21), D: (0x4FE10002, 23)}
LIGHTNING, GREATER_HEAL = combat.spell_id("lightning"), combat.spell_id("greater heal")
HEAL = combat.spell_id("heal")
POT_BAG, POTION = 0x44ADA100, 0x44ADA101            # a bag in the pack holding 3 heal potions
REG_BAG = 0x44ADA200                                # a bag in the pack holding reagents
STAFF, STAFF_GRAPHIC = 0x57064E05, 31038            # the prismatic staff (arcane staff), staff run only
REGS = sorted(set(combat.SPELL_REAGENTS[HEAL]) | set(combat.SPELL_REAGENTS[GREATER_HEAL])
              | set(combat.SPELL_REAGENTS[LIGHTNING]))
GHEAL_MIN = 25                                      # --gheal-min-missing (the sim sends no Magery)
DD = nav.DIR_DELTA
FAILURES = []

# The crawl run (--crawl): a multi-room NPD. The hall around the exit spot, a corridor north to
# room R1, a corridor west to room R2 (deep: zone 2 with --crawl-band 22). Mongbat M1 in R1;
# a troll T in R2's corner that hits hard (too strong: the crawl flees, then avoids it); mongbat
# M2 comes into the west corridor once we are back in and M1 is dead. Steps off the floor are
# denied; the store's walk memory knows the floor (the runner plans with --no-map).
M1, M2, T = 0x002C5001, 0x002C5002, 0x002C5003
TROLL = 0x36
T_HOME = (5503, 521)
CRAWL_POS = {M1: (5538, 509), M2: (5528, 527), T: T_HOME}
CRAWL_HITS = {M1: 60, M2: 60, T: 3000}
HITS.update(CRAWL_HITS)
CORPSE.update({M1: 0x4FE00011, M2: 0x4FE00012})
GOLD.update({M1: (0x4FE10011, 20), M2: (0x4FE10012, 22)})
NAMES = {T: "a troll"}
BODIES = {T: TROLL}

# The recall run (--enter-recall / --leave-recall / --bank-gold): start in town at HOME (the tome's
# default rune, next to the banker), recall in by the tome's row "Urukton Bluffs" to URUK, fight at
# U_FIGHT. As live (2026-10-03) the arrival is near a golden gate (GATE): the server refuses a recall
# farther than 8 tiles from it (RunUO's cliloc 501802, [INFERENCE] for Outlands) after the cast.
# Each recall knocks the prismatic staff into the pack (live: the recall in by a tome charge did).
# Visit 1: A dies, is looted, then the red BASTET comes into view: recall at once (refused at the
# fight spot, walk to the arrival, recall), bank the gold, wait --pk-wait, recall in. Visit 2: D
# attacks; when we first hurt it the server says "Bastet is attacking you!" (Bastet hidden): recall
# at once (walking to the arrival first: refused near there before). Visit 3: D is killed and
# looted: --kills 2, recall home, bank.
UTOME = 0x57C3DEB6                                  # the blessed rune tome "New Player Locations"
HOME, URUK, GATE = (1752, 3001), (5248, 2821), (5246, 2822)
U_FIGHT = (5236, 2821)                              # 10 tiles from GATE: recall refused there
BANKER, BANKBOX, BANK_PILE = 0x000001EA, 0x40000B0B, 0x40000B0C
BANK_POS, BANK_START = (1755, 3001), 177            # the banker; gold coins in the box before the run
BASTET = 0x0001E2B7
RECALL_S = 1.2                                      # power words -> arrival (live 1.95-2.09 s)
TOME_LAYOUT = ("{ gumppic 10 10 116 }{ text 165 29 2655 0 18 0 1 0 0 0 }{ text 97 59 2655 1 18 0 1 0 0 0 }"
               "{ button 185 55 9721 9724 1 0 3 }{ gumppic 364 32 2271 }{ text 429 32 2655 2 18 0 1 0 0 0 }"
               "{ button 88 96 2118 2117 1 0 100 }{ button 109 94 210 211 1 0 200 }"
               "{ text 133 95 63 3 18 0 1 0 0 0 }"
               "{ button 88 120 2118 2117 1 0 101 }{ button 109 118 210 211 1 0 201 }"
               "{ text 133 119 2655 4 18 0 1 0 0 0 }")
TOME_LINES = ["New Player Locations", "Manage Runes", "36/50", "Cambria", "Urukton Bluffs"]


def rect(x0, x1, y0, y1):
    return {(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)}


HALL, NORTH, R1, WEST, R2 = (rect(5530, 5540, 524, 529), rect(5534, 5536, 515, 523), rect(5530, 5540, 507, 514),
                             rect(5514, 5529, 526, 528), rect(5500, 5513, 519, 533))
CRAWL_FLOOR = HALL | NORTH | R1 | WEST | R2
VIEW = 18                  # the server sends a mobile once it is this close (ClassicUO view range)


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


def login_pkt(x=SPOT[0], y=SPOT[1]):
    body = u32(SELF) + bytes(4) + u32(BODY) + u32(x) + u32(y) + u32(0) + bytes([0x80])
    return b"\x1b" + body + bytes(42 - len(body))


def seed_pkt(token):
    return bytes.fromhex("bf001d0001") + u32(token) + bytes(20)


def self_at(x, y, d=0):
    return b"\x20" + u32(SELF) + u32(BODY) + b"\x01\x83\xea\x20" + u32(x) + u32(y) + b"\x00\x00" \
        + bytes([d]) + u32(0)


def mob_pkt(serial, x, y, body=MONGBAT):        # 0x20 for a grey (3) monster
    return b"\x20" + u32(serial) + u32(body) + b"\x03\x00\x00\x00" + u32(x) + u32(y) + b"\x00\x00\x04" + u32(0)


def mob_move(serial, x, y):        # 0x77 V10
    return b"\x77" + u32(serial) + u32(x) + u32(y) + u32(0) + b"\x04"


def name_pkt(serial, name):
    return b"\x98\x00\x25" + u32(serial) + name.encode().ljust(30, b"\x00")


def hits_pkt(serial, cur, top):
    return b"\xa1" + u32(serial) + u16(top) + u16(cur)


def mana_pkt(cur, top):
    return b"\xa2" + u32(SELF) + u16(top) + u16(cur)


def swing(attacker, defender):
    return b"\x2f\x00" + u32(attacker) + u32(defender)


def damage(serial, amount):
    return b"\x0b" + u32(serial) + u16(amount)


def warmode_pkt(on):
    return bytes([0x72, 1 if on else 0, 0, 0x32, 0])


def cursor(cid, cursor_type):
    return b"\x6c\x00" + u32(cid) + bytes([cursor_type]) + bytes(20)


def equip(item, graphic, layer):
    return b"\x2e" + u32(item) + u32(graphic) + u32(0) + bytes([layer]) + u32(SELF) + u16(0)


def contained(serial, graphic, amount, container, x=50, y=60):
    return b"\x25" + u32(serial) + u32(graphic) + b"\x00" + u16(amount) + u16(x) + u16(y) + b"\x00" \
        + u32(container) + u16(0) + u32(0)


def ground_item(serial, graphic, amount, x, y):
    return b"\xf3\x00\x01\x00" + u32(serial) + u32(graphic) + b"\x00" + u16(amount) + b"\x00\x00" \
        + u32(x) + u32(y) + u32(0) + b"\x00" + u16(0) + u32(0) + u16(0)


def open_container(serial, gump):
    return b"\x24" + u32(serial) + u32(gump) + u16(0x7D)


def delete(serial):
    return b"\x1d" + u32(serial)


def display_death(serial, corpse):
    return b"\xaf" + u32(serial) + u32(corpse) + u32(0)


def corpse_flags(corpse, serial, name="a mongbat corpse", noto=3):   # Outlands 0xFF sub 0xDEAD
    body = u32(0xDEAD) + u32(corpse) + u32(serial) + bytes([noto]) + name.encode() + b"\x00"
    return var(0xFF, body)


def system_text(text):            # 0xAE from "System" (serial 0xFFFFFFFF), as live
    return var(0xAE, u32(0xFFFFFFFF) + b"\xff\xff\x00" + u16(0x3B2) + u16(3) + b"ENU\x00"
               + b"System".ljust(30, b"\x00") + text.encode("utf-16-be") + b"\x00\x00")


def cliloc(number, args=b""):
    return var(0xC1, u32(0xFFFFFFFF) + b"\xff\xff\x00" + u16(0x3B2) + u16(3) + u32(number)
               + b"System".ljust(30, b"\x00") + args + b"\x00\x00")


def gump(serial, gump_id, layout, lines=()):
    body = u32(serial) + u32(gump_id) + u32(50) + u32(50) + u16(len(layout)) + layout.encode()
    body += u16(len(lines)) + b"".join(u16(len(t.encode("utf-16-be"))) + t.encode("utf-16-be") for t in lines)
    return var(0xB0, body)


def says(serial, name, text, kind=0, hue=0x3B2):
    """0x1C ASCII speech from `serial` (kind 6: a click label)."""
    return var(0x1C, u32(serial) + u16(0x190) + bytes([kind]) + u16(hue) + u16(3)
               + name.encode().ljust(30, b"\x00") + text.encode() + b"\x00")


def human_pkt(serial, x, y, noto, player=False):
    """0x20 for a human: an NPC (notoriety 7) or a player (flag 0x20; 6: a red)."""
    return (b"\x20" + u32(serial) + u32(0x190) + bytes([noto]) + u16(0x83EA) + (b"\x20" if player else b"\x00")
            + u32(x) + u32(y) + b"\x00\x00\x02" + u32(0))


def penalty_buff(steps):
    """Outlands 0xFF sub 8 "Stationary Penalty" as live (capture 20261003_123614 byte for
    byte, our serial and a zero timestamp): icon 277, f1 4620, f2 1, one timer whose value
    is the steps left."""
    return var(0xFF, u32(8) + u32(SELF) + u16(277) + u16(4620) + u16(1) + u16(0) + u16(0) + u16(1)
               + struct.pack(">f", steps) + bytes(8) + bytes(8) + b"Stationary Penalty\x00"
               + b"All damage is reduced to 1. Move {value} more steps to remove this effect\x00"
               + u16(0) + u16(1) + bytes(4))


def penalty_remove():              # 0xFF sub 9 (OutlandsRemoveBuff), live length 13
    return var(0xFF, u32(9) + u32(SELF) + u16(277))


def cheb(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


class World:
    def __init__(self, staff=False, fight=None, still_after=None, midfight=False, crawl=False, recall=False):
        self.crawl = crawl                   # the crawl run: the multi-room NPD (CRAWL_FLOOR)
        self.recall = recall                 # the recall run: HOME <-> URUK by the tome (RECALL notes above)
        self.staff = staff                   # wielding the prismatic staff (the arcane-staff run)
        self.staff_worn = staff
        self.melee = 40 if staff else 10     # per hit (the staff's Arcane Buildup hits hard)
        self.loot_hit_t = None               # the hit while A's corpse is being looted (time)
        self.cast_log = []                   # (spell, time, inside?, potions in the pack)
        self.disarms, self.rearms = [], []   # staff put in the pack by a cast (time) / (time, lift, 0x13)
        self.staff_lift = None
        self.fight = fight or SPOT           # --fight-spot (the mobs come at it)
        self.pos = list(HOME if recall else SPOT)
        self.facing = 0
        self.writer = None
        self.c2s, self.c2s_t = [], []
        self.hits, self.hits_max = 100, 100
        self.mana, self.mana_max = 100, 100
        self.warmode = False
        self.alive = {A: True, S: not recall, D: False, E: False}
        self.mob_hits = dict(HITS)
        dx, dy = self.fight[0] - SPOT[0], self.fight[1] - SPOT[1]
        self.mob_pos = {s: (x + dx, y + dy) for s, (x, y) in POS.items()}
        if crawl:
            self.alive = {M1: True, M2: False, T: True}
            self.mob_pos = dict(CRAWL_POS)
        self.in_view = set()                 # crawl: mobiles the server has sent (within VIEW)
        self.trace = []                      # crawl: (time, x, y) after every step / teleport
        self.m2_t = None                     # crawl: when M2 came
        self.kill_t = {}                     # serial -> (time, tile) of its death
        self.walls_hit = []                  # crawl: steps the sim denied as walls ((from, dir))
        self.engaged = None                  # last 0x05 serial
        self.swingers = {}                   # serial -> damage per swing
        self.cid = 0x7000
        self.cursors = {}                    # cid -> (spell, cursor_type)
        self.targets = []                    # (spell, packet, expected bytes, time, our hits before)
        self.cancels = []                    # 0x6C cancels: (packet, the stock cancel for that cursor)
        self.casts = []                      # spell ids
        self.lifted = None
        self.pack_gold = 100
        self.looted = {}                     # corpse -> gold dropped into the pack
        self.drops_refused = 0
        self.corpse_items = {}               # corpse serial -> (gold serial, amount) still inside
        self.dclicks = []
        self.spawn_t = None                  # D/E arrival time
        self.loot_a_t = None                 # A's gold dropped into the pack
        self.exits, self.entries = [], []
        self.reentered = None                # time the character came back in
        self.potions = 3
        self.drinks, self.refused = [], []   # potion double-clicks: accepted / refused (cooling down)
        self.client_drank_t = None           # a potion drunk in the client (the runner can't know)
        self.light_t = []                    # Lightning cast requests (time)
        self.reagent_refusals = []           # Lightning casts answered with 502630 (time)
        self.exit_from = []                  # where the step onto the exit teleporter came from
        self.arrival_steps = []              # steps onto the arrival tile (an exit too, as live)
        self.attack_pos = []                 # where we stood for each 0x05
        # the Stationary Penalty as measured live: on at login, after still_after s without a
        # step (live 300; None: off), and (midfight) once when we first hurt A (alive after);
        # each step that changes our tile counts one down, the 5th removes it
        self.penalty = 0                     # steps left (0: off)
        self.still_after, self.midfight = still_after, midfight
        self.last_step_t = time.time()
        self.penalty_log = []                # (time, "apply" / "remove", why, inside?)
        self.attacks_on = []                 # (time, packet) attacks we got while it was on
        # the recall run
        self.recalls = []                    # {t, button, from, ok}: tome presses (100 home, 101 Urukton)
        self.recall_ins = 0                  # arrivals at URUK
        self.arrivals = []                   # (time, dest)
        self.tome_opens = 0
        self.gump_n = 0x7700
        self.red_t = self.notice_t = None    # BASTET came into view / "Bastet is attacking you!"
        self.bank_gold = BANK_START
        self.bank_opens, self.bank_drops = [], []   # times "bank" opened the box / (time, amount) dropped in
        self.far_bank_speech = 0
        self.labels_sent = 0
        self.said = []                       # 0xAD texts

    def apply_penalty(self, why):
        self.penalty = 5
        self.penalty_log.append((time.time(), "apply", why, self.inside))
        self.send(penalty_buff(5))

    def stepped(self):
        """A step that changed our tile (teleports aren't steps: live they never reset it)."""
        self.last_step_t = time.time()
        if not self.penalty:
            return
        self.penalty -= 1
        if self.penalty:
            self.send(penalty_buff(self.penalty))
        else:
            self.penalty_log.append((time.time(), "remove", "steps", self.inside))
            self.send(penalty_remove())

    @property
    def inside(self):
        return self.pos[0] > 5000

    def send(self, pkt):
        self.writer.write(encode_packet(pkt, S2C_KEY))

    def later(self, delay, fn):
        async def go():
            await asyncio.sleep(delay)
            fn()
            await self.writer.drain()
        asyncio.get_running_loop().create_task(go())

    def hurt_self(self, attacker, amount):
        if not self.inside or not self.alive.get(attacker):
            return
        self.hits = max(1, self.hits - amount)
        self.send(swing(attacker, SELF))
        self.send(damage(SELF, amount))
        self.send(hits_pkt(SELF, self.hits, self.hits_max))

    def show_mob(self, serial):
        x, y = self.mob_pos[serial]
        self.send(mob_pkt(serial, x, y, BODIES.get(serial, MONGBAT)))
        self.send(name_pkt(serial, NAMES.get(serial, "a mongbat")))
        self.send(hits_pkt(serial, self.mob_hits[serial], HITS[serial]))

    def update_view(self):
        """Crawl: send a live mobile once it comes within VIEW of us, as the server does."""
        if not self.inside:
            self.in_view.clear()
            return
        for s, alive in self.alive.items():
            near = alive and cheb(self.pos, self.mob_pos[s]) <= VIEW
            if near and s not in self.in_view:
                self.in_view.add(s)
                self.show_mob(s)
            elif not near:
                self.in_view.discard(s)

    def beside(self):
        """A floor tile next to us for a mob to come to (crawl)."""
        x, y = self.pos
        taken = {p for s, p in self.mob_pos.items() if self.alive.get(s)}
        return next((x + dx, y + dy) for dx, dy in DD if (x + dx, y + dy) in CRAWL_FLOOR
                    and (x + dx, y + dy) not in taken)

    def come(self, serial):
        if cheb(self.pos, self.mob_pos[serial]) > 1:
            self.mob_pos[serial] = self.beside()
            self.send(mob_move(serial, *self.mob_pos[serial]))

    def kill(self, serial):
        """In-view death as live (214649): corpse item, 0xAF, 0x1D, 0xFF sub 0xDEAD."""
        self.alive[serial] = False
        self.swingers.pop(serial, None)
        x, y = self.mob_pos[serial]
        self.kill_t[serial] = (time.time(), (x, y))
        corpse = CORPSE[serial]
        self.send(ground_item(corpse, combat.CORPSE_GRAPHIC, MONGBAT, x, y))
        self.send(display_death(serial, corpse))
        self.send(delete(serial))
        self.send(corpse_flags(corpse, serial))
        if serial in GOLD:
            self.corpse_items[corpse] = GOLD[serial]

    def hurt_mob(self, serial, amount):
        if not self.alive.get(serial):
            return
        if self.penalty:
            amount = 1                       # "All damage is reduced to 1."
        self.mob_hits[serial] = max(0, self.mob_hits[serial] - amount)
        self.send(damage(serial, amount))
        self.send(hits_pkt(serial, self.mob_hits[serial], HITS[serial]))
        if self.mob_hits[serial] == 0:
            self.kill(serial)
        elif self.midfight and serial == A:
            self.midfight = False
            self.apply_penalty("mid-fight")
        elif self.recall and serial == D and self.recall_ins == 2 and self.notice_t is None:
            # Bastet attacks hidden: only the server's notice, overhead from our serial as live
            self.notice_t = time.time()
            self.send(says(SELF, "Shackleworth", "Bastet is attacking you!", hue=34))

    # ---- the recall run ----
    def red_appears(self):
        """Visit 1, after A's loot: the red Bastet comes into view 15 tiles east (beyond the 12-tile
        hostile-player rule: only recall mode's 'a red anywhere in view' sees it)."""
        if not self.inside:
            return
        self.red_t = time.time()
        self.send(human_pkt(BASTET, self.pos[0] + 15, self.pos[1], 6, player=True))
        self.send(name_pkt(BASTET, "Bastet"))

    def disarm(self):
        """Live: a cast (a recall by a tome charge too) puts the prismatic staff in the pack."""
        if self.staff_worn:
            self.staff_worn = False
            self.disarms.append(time.time())
            self.send(delete(STAFF))
            self.send(contained(STAFF, STAFF_GRAPHIC, 1, BACKPACK))

    def recall_press(self, button):
        """The tome's row button: 100 the default (Cambria: HOME), 101 Urukton Bluffs. The
        staff goes to the pack at once; inside, more than 8 tiles from GATE the cast is
        refused when it completes (501802), else we land RECALL_S later."""
        dest = {100: HOME, 101: URUK}.get(button)
        if dest is None:
            return
        ok = not (self.inside and cheb(self.pos, GATE) > 8)
        self.recalls.append({"t": time.time(), "button": button, "from": tuple(self.pos), "ok": ok})
        self.disarm()
        self.send(says(SELF, "Shackleworth", "Kal Ort Por", kind=10))
        self.later(RECALL_S, (lambda: self.land(dest)) if ok else (lambda: self.send(cliloc(501802))))

    def land(self, dest):
        self.swingers.clear()
        self.teleport(*dest)
        self.arrivals.append((time.time(), dest))
        self.apply_penalty("recall")         # live: at once after most recalls
        if dest == HOME:
            self.send(human_pkt(BANKER, *BANK_POS, 7))
            return
        self.recall_ins += 1
        self.reentered = time.time()
        if self.recall_ins == 1:
            self.show_mob(A)
        else:                                # D waits beside the fight spot (it swings once attacked)
            self.alive[D] = True
            self.show_mob(D)

    def spawn_pair(self):
        self.spawn_t = time.time()
        for s in (D, E):
            self.alive[s] = True
            self.show_mob(s)
        self.swingers[D] = 8
        self.later(0.4, lambda: self.swingers.__setitem__(E, 8))

    def teleport(self, x, y):
        self.pos = [x, y]
        self.send(self_at(x, y, self.facing))
        if self.crawl:
            self.trace.append((time.time(), x, y))
            self.update_view()

    def came_in(self):
        first = self.reentered is None
        self.reentered = time.time()
        if self.crawl:                       # T went back to its corner while we were out
            self.mob_pos[T] = T_HOME
            self.swingers.clear()
            self.in_view.clear()
            self.update_view()
            return
        self.show_mob(D)                     # E wandered off while we were out: never re-sent
        self.swingers.pop(E, None)
        if not first:
            return
        self.swingers[D] = 2                 # back inside D only grazes us (no leave rule before the hit)

        def client_drinks_then_hit():
            # the player drank a potion in the client (the runner can't know), then D hits
            # hard: below --heal-at, above --leave-at. The runner's own clock says ready,
            # the server refuses (500235)
            self.client_drank_t = time.time()
            self.hurt_self(D, 25)
        ready_at = (self.drinks[-1] + 10.5) if self.drinks else time.time()
        self.later(max(0.5, ready_at - time.time()), client_drinks_then_hit)

    def drink(self):
        """A heal potion double-clicked: refused within 10 s of the last drink (the
        runner's or the client's), else +25 hits, as live (cliloc 1008158 + amount)."""
        now = time.time()
        last = max([t for t in self.drinks + [self.client_drank_t] if t is not None], default=None)
        if last is not None and now - last < 10.0:
            self.refused.append(now)
            self.send(cliloc(500235))
            return
        self.drinks.append(now)
        self.hits = min(self.hits_max, self.hits + 25)
        self.potions -= 1
        self.send(hits_pkt(SELF, self.hits, self.hits_max))
        self.send(cliloc(1008158, "25".encode("utf-16-le")))
        self.send(contained(POTION, 0x0F0C, self.potions, POT_BAG) if self.potions else delete(POTION))

    # ---- packets ----
    def on_packet(self, p):
        self.c2s.append(p)
        self.c2s_t.append(time.time())
        pid = p[0]
        if self.penalty and (pid == 0x05 or (pid == 0xFF and p[3:7] == u32(4)
                                             and parse_packet("c2s", p)["spell_id"] == LIGHTNING)
                             or (pid == 0x6C and parse_packet("c2s", p).get("serial") in self.mob_pos)):
            self.attacks_on.append((time.time(), p))
        if pid == 0x02:
            seq, d = p[2], p[1] & 7
            if d != self.facing:
                self.facing = d
                self.send(bytes([0x22, seq, 0x01]))
                return
            nx, ny = self.pos[0] + DD[d][0], self.pos[1] + DD[d][1]
            if (nx, ny) == EXIT_TILE or (self.inside and (nx, ny) == INSIDE):
                # the exit teleporter denies the step, then moves you; live the arrival tile too
                (self.exit_from if (nx, ny) == EXIT_TILE else self.arrival_steps).append(tuple(self.pos))
                self.exits.append(time.time())
                self.send(b"\x21" + bytes([seq]) + u32(self.pos[0]) + u32(self.pos[1]) + bytes([self.facing]) + u32(0))
                self.swingers.clear()
                self.teleport(*OUTSIDE)
                return
            if self.inside and any(self.alive[s] and self.mob_pos[s] == (nx, ny) for s in self.alive):
                self.send(b"\x21" + bytes([seq]) + u32(self.pos[0]) + u32(self.pos[1]) + bytes([self.facing]) + u32(0))
                return
            if self.crawl and self.inside and (nx, ny) not in CRAWL_FLOOR:     # a wall
                self.walls_hit.append((tuple(self.pos), d))
                self.send(b"\x21" + bytes([seq]) + u32(self.pos[0]) + u32(self.pos[1]) + bytes([self.facing]) + u32(0))
                return
            self.pos = [nx, ny]
            self.send(bytes([0x22, seq, 0x01]))
            self.stepped()
            if self.crawl:
                self.trace.append((time.time(), nx, ny))
                self.update_view()
            if (nx, ny) == (ENTRY[0], ENTRY[1] - 1):    # the entrance: confirm, then the teleport
                self.entries.append(time.time())

                def go_in():
                    self.teleport(*INSIDE)
                    self.came_in()
                self.later(0.1, go_in)
        elif pid == 0x72:
            self.warmode = bool(p[1])
            self.send(warmode_pkt(self.warmode))
        elif pid == 0x05:
            serial = int.from_bytes(p[1:5], "big")
            self.engaged = serial
            self.attack_pos.append(tuple(self.pos))
            if self.recall and serial == D and self.alive[D]:
                self.swingers[D] = 3
            elif self.crawl and self.alive.get(serial) and self.inside:
                self.come(serial)            # mongbats fly in and stay on us; the troll walks up
                self.swingers[serial] = 15 if serial == T else 3
            elif serial == A and A not in self.swingers and self.alive[A]:
                self.mob_pos[A] = (self.fight[0], self.fight[1] - 1)    # flies to us and hits hard once
                self.send(mob_move(A, *self.mob_pos[A]))
                self.hurt_self(A, 30)
                self.swingers[A] = 3
        elif pid == 0xFF and p[3:7] == u32(4):
            f = parse_packet("c2s", p)
            self.casts.append(f["spell_id"])
            self.cast_log.append((f["spell_id"], time.time(), self.inside, self.potions))
            self.disarm()                    # live: 0x1D + 0x25 into the pack right after the request
            if f["spell_id"] == LIGHTNING:
                self.light_t.append(time.time())
                if not self.reagent_refusals:            # the server's count differs from ours
                    self.reagent_refusals.append(time.time())
                    self.send(cliloc(combat.CLILOC_NO_REAGENTS))
                    return
            self.mana -= combat.spell_mana(f["spell_id"])
            self.send(mana_pkt(self.mana, self.mana_max))
            self.cid += 1
            ctype = 2 if f["spell_id"] in (GREATER_HEAL, HEAL) else 1
            self.cursors[self.cid] = (f["spell_id"], ctype)
            cid = self.cid
            self.later(0.3, lambda: self.send(cursor(cid, ctype)))
        elif pid == 0x6C:
            f = parse_packet("c2s", p)
            spell, ctype = self.cursors.pop(f["cursor_id"], (None, None))
            if spell is None:
                return
            esc = actions.target_cancel(f["cursor_id"], 0, ctype)
            if p == esc or f.get("serial") in (0, None):
                self.cancels.append((p, esc))
                return
            if f.get("serial") == SELF:
                want = actions.target_object(f["cursor_id"], SELF, self.pos[0], self.pos[1], 0, BODY, ctype)
            elif f.get("serial") in self.mob_pos:
                x, y = self.mob_pos[f["serial"]]
                want = actions.target_object(f["cursor_id"], f["serial"], x, y, 0, MONGBAT, ctype)
            else:
                want = None
            self.targets.append((spell, p, want, time.time(), self.hits))
            if spell in (GREATER_HEAL, HEAL) and f.get("serial") == SELF:
                self.hits = min(self.hits_max, self.hits + (40 if spell == GREATER_HEAL else 7))
                self.send(hits_pkt(SELF, self.hits, self.hits_max))
            elif spell == LIGHTNING and f.get("serial") in self.alive:
                self.hurt_mob(f["serial"], 40)
        elif pid == 0x06:
            serial = int.from_bytes(p[1:5], "big")
            self.dclicks.append(serial)
            if serial == BACKPACK:
                self.send(open_container(BACKPACK, 0x3C))
            elif serial == UTOME and self.recall:
                self.tome_opens += 1
                self.gump_n += 1
                self.send(gump(self.gump_n, escape_mod.RUNETOME_GUMP, TOME_LAYOUT, TOME_LINES))
            elif serial == POT_BAG:
                self.send(open_container(POT_BAG, 0x3D))
            elif serial == POTION:
                self.drink()
            elif serial in self.corpse_items:
                self.send(open_container(serial, 0x09))
                g, n = self.corpse_items[serial]
                self.send(contained(g, combat.GOLD_GRAPHIC, n, serial))
                if serial == CORPSE[A] and not self.staff and self.loot_hit_t is None:
                    # live 2026-10-03: hits fell below --heal-at while looting, and the runner
                    # left without a drink; here a hit to 70 (below 75 %, above 60 %) as it loots
                    self.loot_hit_t = time.time()
                    self.hits = min(self.hits, 70)
                    self.send(hits_pkt(SELF, self.hits, self.hits_max))
        elif pid == 0xB1 and self.recall:
            f = parse_packet("c2s", p)
            if f["serial"] == self.gump_n:
                self.recall_press(f["button_id"])
        elif pid == 0x09 and self.recall and int.from_bytes(p[1:5], "big") == BANKER:
            self.labels_sent += 1                        # the click label, as the server says it
            self.send(says(BANKER, "Jon", "Jon the banker", kind=6))
        elif pid == 0xAD:
            text = parse_packet("c2s", p).get("text")
            self.said.append(text)
            if self.recall and text == "bank":
                if self.inside or cheb(self.pos, BANK_POS) > 12:
                    self.far_bank_speech += 1
                    return
                self.bank_opens.append(time.time())
                self.send(equip(BANKBOX, 0x0E7C, 0x1D))
                self.send(b"\x24" + u32(BANKBOX) + bytes.fromhex("0000004a007d"))
                self.send(contained(BANK_PILE, combat.GOLD_GRAPHIC, self.bank_gold, BANKBOX))
        elif pid == 0x07:
            self.lifted = parse_packet("c2s", p)
            if self.lifted["serial"] == STAFF:
                self.staff_lift = p
                self.send(delete(STAFF))
        elif pid == 0x13:
            f = parse_packet("c2s", p)
            self.rearms.append((time.time(), self.staff_lift, p))
            if f["serial"] == STAFF and self.staff_lift is not None and f["layer"] == 2:
                self.staff_worn, self.staff_lift = True, None
                self.send(equip(STAFF, STAFF_GRAPHIC, 2))
        elif pid == 0x08:
            f = parse_packet("c2s", p)
            lf, self.lifted = self.lifted, None
            if self.recall and f["container"] == BANKBOX:
                if lf is None or lf["serial"] != PACK_GOLD or not self.bank_opens or self.inside:
                    self.drops_refused += 1
                    return
                self.bank_drops.append((time.time(), self.pack_gold))
                self.bank_gold += self.pack_gold                  # RunUO merges the pile
                self.pack_gold = 0
                self.send(delete(PACK_GOLD))
                self.send(contained(BANK_PILE, combat.GOLD_GRAPHIC, self.bank_gold, BANKBOX))
                return
            corpse = next((c for c, (g, _) in self.corpse_items.items() if lf and g == lf["serial"]), None)
            if corpse is None or f["serial"] != lf["serial"] or f["container"] != BACKPACK:
                self.drops_refused += 1
                return
            g, n = self.corpse_items.pop(corpse)
            self.looted[corpse] = n
            self.pack_gold += n                                   # RunUO merges the pile
            self.send(delete(g))
            self.send(contained(PACK_GOLD, combat.GOLD_GRAPHIC, self.pack_gold, BACKPACK))
            if corpse == CORPSE[A]:
                self.loot_a_t = time.time()
                if self.recall:
                    self.later(1.0, self.red_appears)
                else:
                    self.later(2.0, self.spawn_pair)

    async def ticker(self):
        """Mobs swing at us (0x2F + damage) about every 0.8 s; we melee the engaged mob
        when adjacent in war mode (10 a hit)."""
        n = 0
        while True:
            await asyncio.sleep(0.4)
            n += 1
            if self.writer is None:
                continue
            if self.inside and n % 2 == 0:
                for s, dmg in list(self.swingers.items()):
                    if self.crawl and s != T:
                        self.come(s)                    # a mongbat keeps up with us
                    if self.crawl and s == T and cheb(self.pos, self.mob_pos[T]) > 1:
                        continue                        # the troll only hits what stands next to it
                    self.hurt_self(s, dmg)
            if self.crawl and self.m2_t is None and self.reentered and M1 in self.kill_t and self.inside:
                self.m2_t = time.time()                 # M2 comes once we are back in and M1 is dead
                self.alive[M2] = True
                self.update_view()
            if self.inside and n % 3 == 0 and self.warmode and self.engaged and self.alive.get(self.engaged) \
                    and cheb(self.pos, self.mob_pos[self.engaged]) <= 1:
                self.hurt_mob(self.engaged, self.melee)
            if self.still_after and not self.penalty and time.time() - self.last_step_t >= self.still_after:
                self.apply_penalty("still")
            await self.writer.drain()

    async def handle(self, reader, writer):
        await reader.readexactly(5)
        self.writer = writer
        writer.write(PRELUDE)
        self.send(login_pkt(*self.pos))
        for token in (5, 6, 7, 8):
            self.send(seed_pkt(token))
        self.send(self_at(*self.pos))
        self.send(equip(BACKPACK, 0x0E75, 0x15))
        if self.staff:
            self.send(equip(STAFF, STAFF_GRAPHIC, 2))
        self.send(contained(PACK_GOLD, combat.GOLD_GRAPHIC, self.pack_gold, BACKPACK))
        self.send(contained(POT_BAG, 0x0E76, 1, BACKPACK))
        self.send(contained(POTION, 0x0F0C, self.potions, POT_BAG))
        self.send(contained(REG_BAG, 0x0E76, 1, BACKPACK))
        for i, g in enumerate(REGS):
            self.send(contained(REG_BAG + 1 + i, g, 20, REG_BAG))
        self.send(hits_pkt(SELF, self.hits, self.hits_max))
        self.send(mana_pkt(self.mana, self.mana_max))
        if self.recall:                                # in town: the banker by the rune, the tome in the pack
            self.send(human_pkt(BANKER, *BANK_POS, 7))
            self.send(contained(UTOME, 0x71AF, 1, BACKPACK))
        elif self.crawl:
            self.update_view()
        else:
            for s in (A, S):
                self.show_mob(s)
            self.send(swing(S, SELF))                      # S was on us...
            self.alive[S] = False                          # ...and someone else killed it: 0xDEAD only
            self.send(ground_item(CORPSE[S], combat.CORPSE_GRAPHIC, MONGBAT, *self.mob_pos[S]))
            self.send(corpse_flags(CORPSE[S], S))
        self.apply_penalty("login")                    # live: on at every login
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


def state_req(req):
    import socket
    with socket.create_connection(("127.0.0.1", STATE_PORT), timeout=5) as s:
        s.sendall((json.dumps(req) + "\n").encode())
        return json.loads(s.makefile("rb").readline())


def refs(p):
    """The entity serial a combat/loot C2S packet is about, else None."""
    pid = p[0]
    if pid in (0x05, 0x06, 0x07, 0x09):
        return int.from_bytes(p[1:5], "big")
    if pid == 0x34:
        return int.from_bytes(p[6:10], "big")
    if pid == 0x6C:
        return parse_packet("c2s", p).get("serial")
    return None


def check_staff(world, text, rc):
    """The arcane-staff run (World(staff=True)): Shackleworth's prismatic staff, Arcane /
    Magery below 80 (the sim sends no skills), so a cast puts it in the pack (live
    2026-10-03: 0x1D + 0x25 right after the cast request)."""
    print("== arcane staff in hand ==")
    check("staff run: exited 0 after 2 kills, outside", rc == 0 and "hunt complete: 2 kill(s)" in text
          and not world.inside, f"rc {rc}")
    check("no attack spell with the staff in hand (melee: its Arcane Buildup is the damage)",
          not world.light_t and "no attack spell" in text, str(world.light_t))
    inside_heals = [(t, p) for s, t, inside, p in world.cast_log if s in (HEAL, GREATER_HEAL) and inside and p]
    check("inside, no heal spell while a heal potion is in the pack (potions first)", not inside_heals,
          str(inside_heals))
    outside = [t for s, t, inside, p in world.cast_log if not inside]
    check("outside, heal spells while resting: the cast put the staff in the pack", outside and world.disarms
          and any(d >= outside[0] for d in world.disarms), f"casts out {outside} disarms {world.disarms}")
    want = (actions.lift(STAFF, 1), actions.equip_request(STAFF, 2, SELF))
    rearm = [t for t, lift, eq in world.rearms if (lift, eq) == want]
    check("re-equipped after the rest, before going back in: the stock lift + drag pause + 0x13 on layer 2 "
          "(combat.equip_packets, as `ctl act equip`)",
          world.entries and rearm and world.disarms and any(world.disarms[-1] < t < world.entries[0] for t in rearm)
          and all((lift, eq) == want for t, lift, eq in world.rearms),
          f"rearms {[(t, lift.hex(), eq.hex()) for t, lift, eq in world.rearms]} entries {world.entries}")
    check("the staff is worn at the end", world.staff_worn)


def check_fight(world, text, rc, rows, js):
    """The fight-spot run (World(fight=FIGHT), --fight-spot): the mobs come at FIGHT, 6
    tiles SE of the exit spot; the arrival tile INSIDE, on the straight route, is an exit
    teleporter as live, known to the memory store's `teleporters` table."""
    print("== fight spot ==")
    check("fight-spot run: exited 0 after 2 kills, outside", rc == 0 and "hunt complete: 2 kill(s)" in text
          and not world.inside, f"rc {rc} at {world.pos}")
    check("every attack sent standing on the fight spot (walked there on each visit)",
          world.attack_pos and all(p == FIGHT for p in world.attack_pos), str(world.attack_pos))
    threat = [j for j in js if j["kind"] == "threat"]
    check("the two-attacker rule fired at the fight spot; both leaves walked back to the spot, then the exit step",
          len(threat) == 1 and threat[0]["data"]["why"].startswith("2 attackers")
          and world.exit_from == [SPOT, SPOT] and len(world.entries) == 1,
          f"exits from {world.exit_from} entries {len(world.entries)} {str(threat)[:200]}")
    check("never stepped onto the arrival tile (a known teleporter: the Mover routes around it)",
          not world.arrival_steps, str(world.arrival_steps))
    check("both corpses looted at the fight spot", world.looted == {CORPSE[A]: 21, CORPSE[D]: 23}, str(world.looted))
    ok = len(rows) == 2 and all(r["spot"] == list(SPOT) and r["fight_spot"] == list(FIGHT) and r["route_steps"] >= 6
                                and r["leave_at"] == round(min(0.60 + 0.004 * r["route_steps"], 0.70), 3)
                                for r in rows)
    check("episode rows: the spot, the fight spot, the route back (>= 6 steps) and --leave-at + 0.004 per step",
          ok, str([{k: r.get(k) for k in ("spot", "fight_spot", "route_steps", "leave_at")} for r in rows]))


def check_penalty(world, text, rows, name, fight=False):
    """The Stationary Penalty in every run (World.penalty_*): on at login as live. The
    fight-spot run also gets it once more when we first hurt A, and after 14 s without a
    step (live 300 s) while the runner repositions after at most 6 (--reposition-s 6)."""
    print(f"== stationary penalty ({name}) ==")
    log = world.penalty_log
    applies = [(t, why) for t, kind, why, inside in log if kind == "apply"]
    removes = [t for t, kind, why, inside in log if kind == "remove"]
    attacks = [t for p, t in zip(world.c2s, world.c2s_t) if p[0] == 0x05]
    check("on at login; walked off (the 5th step removed it) before the first attack",
          applies and applies[0][1] == "login" and removes and attacks and removes[0] < attacks[0],
          f"log {log} first attack {attacks[:1]}")
    check("no attack (0x05, a Lightning cast, a target on a mob) sent while it was on",
          not world.attacks_on, str([(t, p.hex()) for t, p in world.attacks_on][:3]))
    check("no teleporter stepped on: the exit tile only from the spot when leaving (2), never the arrival tile",
          not world.arrival_steps and world.exit_from == [SPOT, SPOT], f"exits from {world.exit_from} "
          f"arrival steps {world.arrival_steps}")
    took = [int(n) for n in re.findall(r"Stationary Penalty cleared after (\d+) step", text)]
    check("one clear walked by the runner (at the spot: the login one; at the fight spot the walk there "
          "clears that, the mid-fight one): the 5 steps + 1, out and back; the rows count it",
          len(took) == 1 and took[0] >= 6 and sum(r.get("stationary_clears") or 0 for r in rows) == 1,
          f"took {took} rows {[r.get('stationary_clears') for r in rows]}")
    if fight:
        mid = [t for t, why in applies if why == "mid-fight"]
        after = [r for r in removes if mid and r > mid[0]]
        nxt = [t for p, t in zip(world.c2s, world.c2s_t) if mid and t > mid[0] and (
            p[0] == 0x05 or (p[0] == 0x6C and refs(p) in world.mob_pos)
            or (p[0] == 0xFF and p[3:7] == u32(4) and parse_packet("c2s", p)["spell_id"] == LIGHTNING))]
        check("mid-fight (we first hurt A): walked off at once, removed before our next attack",
              mid and after and nxt and after[0] < nxt[0], f"applied {mid} removed {after} next attack {nxt[:1]}")
        still = [(t, why, inside) for t, kind, why, inside in log if kind == "apply" and why == "still" and inside]
        n = sum(r.get("repositions") or 0 for r in rows)
        check("standing still inside, it repositioned before the penalty (never applied inside); rows count it",
              not still and n >= 1, f"still applies inside {still} repositions {n}")


AREAS = {"hall": HALL, "north corridor": NORTH, "R1": R1, "west corridor": WEST, "R2": R2}


def area_of(p):
    return next((name for name, tiles in AREAS.items() if tuple(p) in tiles), None)


def seed_floor(store):
    """The crawl run's floor in the store's walk memory (as if walked before): every step
    between floor tiles confirmed, every step into a wall denied (the runner plans with
    --no-map, so the crawl's floor comes from walk memory)."""
    t, rows = time.time() - 3600, []
    for x, y in CRAWL_FLOOR:
        for d, (dx, dy) in enumerate(DD):
            n = (x + dx, y + dy)
            if n in (EXIT_TILE, INSIDE):
                continue
            rows.append((0, x, y, 0, d, 1 if n in CRAWL_FLOOR else 0, t))
    memory._upsert_walk(store.con.cursor(), rows)
    store.con.commit()


def check_crawl(world, text, rc, rows, js, evs):
    """The crawl run (World(crawl=True), --crawl): patrol through the rooms, zone 2 (R2)
    only once opened, the troll fled from deep in R2 and avoided afterwards, an area left
    as depleted, the survival leave walked back to the exit spot."""
    print("== crawl ==")
    check("crawl run: exited 0 after 2 kills, outside", rc == 0 and "hunt complete: 2 kill(s)" in text
          and not world.inside, f"rc {rc} at {world.pos}")
    seen = {area_of((x, y)) for t, x, y in world.trace} - {None}
    wps = set(re.findall(r"patrol to \((\d+), (\d+)\): route", text))
    check("patrolled waypoint to waypoint (>= 4) through the hall, both corridors and both rooms",
          seen == set(AREAS) and len(wps) >= 4, f"areas {sorted(seen)} waypoints {sorted(wps)}")
    fought = {area_of(p) for p in world.attack_pos}
    check("fought where it met them (attacks in >= 2 areas), not on one spot", len(fought) >= 2, str(fought))
    opened = [e for e in evs if e["kind"] == "crawl_zone" and e["data"].get("open") and e["data"]["zone"] == 2
              and e["data"].get("floor") == 1]
    deep = [t for t, x, y in world.trace if (x, y) in R2]
    check("room R2 (zone 2 of dungeon level 1) entered only after the crawl opened zone 2 (zone 1 known, risk ok)",
          opened and deep and deep[0] > opened[0]["t"], f"opened {[e['t'] for e in opened]} first in R2 {deep[:1]}")
    at_t = [t for p, t in zip(world.c2s, world.c2s_t) if refs(p) == T]
    check("the troll fought in the first visit only", at_t and world.exits and all(t < world.exits[0] for t in at_t),
          f"troll packets {at_t[:3]}.. exits {world.exits}")
    fled = [e for e in evs if e["kind"] == "fight" and "troll" in (e["data"].get("name") or "")]
    check("its fight recorded as fled (job event), and the crawl learned to avoid it",
          [e["data"]["outcome"] for e in fled] == ["fled"] and "crawl: avoiding troll" in text, str(fled)[:300])
    back = [(x, y) for t, x, y in world.trace if world.reentered and t > world.reentered]
    closest = min((cheb(p, T_HOME) for p in back), default=None)
    check("back inside, the troll was never in reach (routes and areas kept away from it)",
          back and closest is not None and closest > 2 and not any(
              t > world.reentered for t in at_t), f"closest {closest}")
    lines = text.splitlines()
    dep = next((i for i, ln in enumerate(lines) if "depleted (" in ln), None)
    m = re.search(r"area \((\d+), (\d+)\) depleted", lines[dep]) if dep is not None else None
    nxt = next((re.search(r"patrol to \((\d+), (\d+)\): route", ln) for ln in lines[dep + 1:]
                if "patrol to" in ln and ": route" in ln), None) if dep is not None else None
    check("an area where a kill came, then nothing for --crawl-depleted-s, left as depleted for another one",
          m and nxt and nxt.groups() != m.groups() and any(r.get("crawl", {}).get("depleted") for r in rows),
          f"{lines[dep] if dep is not None else None} next {nxt and nxt.groups()}")
    leaves = [e for e in evs if e["kind"] == "leave"]
    threat = [j for j in js if j["kind"] == "threat"]
    first = leaves[0] if leaves else None
    check("the survival leave from deep in R2: >= 20 route steps out, --leave-at raised, a threat juncture, the walk "
          "back to the exit spot, then the exit step (never the arrival tile)",
          first and area_of((first["x"], first["y"])) == "R2" and first["data"]["route_steps"] >= 20
          and len(threat) == 1 and world.exit_from == [SPOT, SPOT] and not world.arrival_steps
          and rows and rows[0]["route_steps"] >= 20 and rows[0]["leave_at"] > 0.6,
          f"leave {first and (first['x'], first['y'], first['data'].get('route_steps'))} exits {world.exit_from} "
          f"rows {[(r.get('route_steps'), r.get('leave_at')) for r in rows]}")
    kills = [e["data"] for e in evs if e["kind"] == "fight" and e["data"]["outcome"] == "kill"]
    check("M1 and M2 killed and looted (20 + 22 gold); their fights recorded with time and zone",
          world.looted == {CORPSE[M1]: 20, CORPSE[M2]: 22}
          and sorted(k["serial"] for k in kills) == [f"0x{M1:08X}", f"0x{M2:08X}"]
          and all(k["fight_s"] > 0 and k.get("zone") == 1 for k in kills), f"{world.looted} {kills}")
    c = [r.get("crawl") or {} for r in rows]
    check("episode rows carry the crawl block: dungeon level 1, troll avoided, zone time and the survival leave in zone 2",
          len(rows) == 2 and "troll" in c[0].get("avoided", {}) and c[0].get("floor") == 1 and c[0]["zones"].get("2", {}).get("leaves") == 1
          and sum(v.get("s", 0) for r in c for v in r.get("zones", {}).values()) > 0, str(c)[:400])


PK_WAIT = 2.0             # the recall run's --pk-wait


def check_recall(world, text, rc, rows, js, evs):
    """The recall run (World(recall=True), --enter-recall / --leave-recall / --bank-gold;
    the scenario above the constants): in by the tome's Urukton row, out at once from a
    red in view and from "Bastet is attacking you!", the refused recall walked to the
    arrival, the gold banked at home, back in after --pk-wait, the staff back in hand and
    the Stationary Penalty walked off after every recall before any attack."""
    print("== recall in, recall out ==")
    c2s, ts = world.c2s, world.c2s_t
    check("recall run: exited 0 after 2 kills, at home", rc == 0 and "hunt complete: 2 kill(s)" in text
          and tuple(world.pos) == HOME, f"rc {rc} at {world.pos}")
    presses = [(r["button"], r["ok"]) for r in world.recalls]
    check("tome presses: in by row 'Urukton Bluffs' (101), home by the default (100); one home refused",
          presses == [(101, True), (100, False), (100, True), (101, True), (100, True), (101, True), (100, True)],
          str(world.recalls))
    refused = [r for r in world.recalls if not r["ok"]]
    home_ok = [r for r in world.recalls if r["button"] == 100 and r["ok"]]
    check("the refused recall was cast at the fight spot (> 8 tiles from the gate); every recall home that "
          "landed went out within 1 tile of the arrival (the recall spot)",
          refused and cheb(refused[0]["from"], GATE) > 8
          and all(cheb(r["from"], URUK) <= 1 for r in home_ok), str(world.recalls))
    attack = [t for p, t in zip(c2s, ts) if p[0] == 0x05 or (p[0] == 0x6C and refs(p) in world.mob_pos)]
    homes = [t for t, d in world.arrivals if d == HOME]
    ins = [t for t, d in world.arrivals if d == URUK]
    dclick_tome = [t for p, t in zip(c2s, ts) if p[0] == 0x06 and refs(p) == UTOME]
    red = world.red_t
    first = next((t for t in dclick_tome if red and t > red), None)
    check("red in view: the tome double-clicked within 1.5 s; no attack after it until home",
          red and first and first - red < 1.5 and homes
          and not [t for t in attack if red < t < homes[0]], f"red {red} tome {first} home {homes[:1]}")
    note = world.notice_t
    steps = [t for p, t in zip(c2s, ts) if p[0] == 0x02]
    moved = next((t for t in steps + dclick_tome if note and t > note), None)
    home2 = next((t for t in homes if note and t > note), None)
    check("'Bastet is attacking you!' (no one in view): moving within 1 s (to the recall spot, refused near "
          "there before), home by recall, no attack in between",
          note and moved and moved - note < 1.0 and home2
          and not [t for t in attack if note < t < home2], f"notice {note} moved {moved} home {home2}")
    check("back in only after --pk-wait at home each time",
          len(ins) == 3 and len(homes) == 3 and all(ins[i + 1] - homes[i] >= PK_WAIT - 0.2 for i in range(2)),
          f"in {ins} home {homes}")
    arm = [t for t, lift, eq in world.rearms if lift is not None]
    ok = all(any(i < t < nxt for t in arm) for i in ins
             for nxt in [next((a for a in attack if a > i), None)] if nxt is not None)
    check("every recall with the staff in hand knocked it into the pack; after each arrival in it was "
          "re-equipped before the first attack", len(world.disarms) >= len(ins) and ok and world.staff_worn,
          f"disarms {len(world.disarms)} rearms {arm} arrivals {ins}")
    applied = [t for t, kind, why, inside in world.penalty_log if kind == "apply" and why == "recall" and inside]
    removed = [t for t, kind, why, inside in world.penalty_log if kind == "remove" and inside]
    check("the Stationary Penalty after each recall in was walked off before the first attack; no attack while on",
          len(applied) == 3 and not world.attacks_on
          and all(any(a < r < (next((x for x in attack if x > a), 1e18)) for r in removed) for a in applied),
          f"applied {applied} removed {removed} attacks_on {len(world.attacks_on)}")
    check("A and D looted; the gold banked twice by saying 'bank' next to the banker found by a click: "
          f"100 + 21, then 23; the box holds {BANK_START} + 144",
          world.looted == {CORPSE[A]: 21, CORPSE[D]: 23} and [a for _, a in world.bank_drops] == [121, 23]
          and world.bank_gold == BANK_START + 144 and len(world.bank_opens) == 2 and world.far_bank_speech == 0
          and world.labels_sent >= 1 and world.said == ["bank", "bank"] and world.drops_refused == 0,
          f"looted {world.looted} drops {world.bank_drops} box {world.bank_gold} said {world.said}")
    banks = [e["data"] for e in evs if e["kind"] == "bank"]
    check("job events `bank`: amounts 121, 23; the box's gold after each",
          [(b["amount"], b["box_gold"]) for b in banks] == [(121, BANK_START + 121), (23, BANK_START + 144)],
          str(banks))
    legs = [(e["data"]["leg"], e["data"]["ok"], e["data"].get("failure")) for e in evs if e["kind"] == "travel"]
    check("job events `travel`: in, home refused ('restricted', cliloc 501802), home, in, home, in, home",
          legs == [("in", True, None), ("home", False, "restricted"), ("home", True, None), ("in", True, None),
                   ("home", True, None), ("in", True, None), ("home", True, None)], str(legs))
    threat = [j for j in js if j["kind"] == "threat"]
    check("two urgent `threat` junctures: the red in view, the server's notice",
          [j["severity"] for j in threat] == ["urgent", "urgent"]
          and "red player Bastet in view at 15 tiles" in threat[0]["data"]["why"]
          and "Bastet is attacking" in threat[1]["data"]["why"], str([j["summary"] for j in threat]))
    leaves = [e["data"] for e in evs if e["kind"] == "leave"]
    check("leave events carry the recall: the first refused at the fight spot then landed, the others walked first",
          len(leaves) == 3 and leaves[0]["recall"]["ok"] and leaves[0]["recall"]["refused"]["failure"] == "restricted"
          and leaves[1]["recall"]["walked_first"] and leaves[2]["recall"]["walked_first"],
          str([lv.get("recall") for lv in leaves]))
    check("three visit rows: kills 1, 0, 1; spot = the arrival, fight spot as given",
          [r["kills"] for r in rows] == [1, 0, 1] and all(r["spot"] == list(URUK) and r["fight_spot"] == list(U_FIGHT)
                                                         for r in rows),
          str([{k: r.get(k) for k in ("kills", "spot", "fight_spot", "ended")} for r in rows]))


async def main(staff=False, fight=None, crawl=False, recall=False):
    os.makedirs(LOGDIR, exist_ok=True)
    for f in os.listdir(LOGDIR):
        os.remove(os.path.join(LOGDIR, f))
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "harness.db")
    store = memory.Memory(db)
    # live: the NPD arrival tile is itself an exit teleporter, learned by walking onto it
    store.teleporter_record(0, INSIDE[0], INSIDE[1], 0, 1912, 2556, -20)
    if crawl:
        seed_floor(store)
    store.close()

    if recall:
        world = World(staff=True, fight=U_FIGHT, recall=True)
    else:
        world = World(staff=staff, fight=fight, still_after=14 if fight else None, midfight=bool(fight), crawl=crawl)
    server = await asyncio.start_server(world.handle, "127.0.0.1", UPSTREAM_PORT)
    ticker = asyncio.create_task(world.ticker())
    proxy = subprocess.Popen(
        [PY, f"{ROOT}/harness/proxy.py", "--listen-port", str(PROXY_PORT),
         "--upstream-host", "127.0.0.1", "--upstream-port", str(UPSTREAM_PORT),
         "--control-port", str(CONTROL_PORT), "--state-port", str(STATE_PORT), "--logdir", LOGDIR,
         "--memory-db", db],
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        await asyncio.sleep(1.0)
        reader, writer = await asyncio.open_connection("127.0.0.1", PROXY_PORT)
        writer.write(bytes.fromhex("ef0000000c"))
        await writer.drain()

        async def drain_client():
            while await reader.read(65536):
                pass
        drainer = asyncio.create_task(drain_client())
        await asyncio.sleep(1.0)
        before = state_req({"op": "state", "since": 1 << 62})["world"]
        runner_out = os.path.join(LOGDIR, "runner.out")
        with open(runner_out, "wb") as fh:
            runner = await asyncio.create_subprocess_exec(
                PY, f"{ROOT}/harness/loop_hunt.py", "--kills", "2", "--timeout", "240" if crawl or recall else "150",
                *(["--leave-at", "0.4"] if staff else []),
                *(["--fight-spot", str(fight[0]), str(fight[1]), "--reposition-s", "6"] if fight else []),
                *(["--crawl", "--pull-range", "8", "--target-name", "", "--crawl-band", "22", "--crawl-learn-s", "8",
                   "--crawl-max-dmg", "200", "--crawl-depleted-s", "4", "--crawl-dwell", "2"] if crawl else []),
                *(["--enter-recall", f"0x{UTOME:08X}", "--enter-rune", "Urukton Bluffs",
                   "--leave-recall", f"0x{UTOME:08X}", "--fight-spot", str(U_FIGHT[0]), str(U_FIGHT[1]),
                   "--bank-gold", "1", "--pk-wait", str(PK_WAIT)] if recall else []),
                "--gheal-min-missing", str(GHEAL_MIN),
                "--control-port", str(CONTROL_PORT), "--state-port", str(STATE_PORT), "--memory", db,
                "--entry", str(ENTRY[0]), str(ENTRY[1]), "0",
                "--human", "normal", "--seed", "7", "--human-fast", "0.2", "--quiet", "--no-map",
                "--triage-url", "",
                stdout=fh, stderr=asyncio.subprocess.STDOUT)
            try:
                await asyncio.wait_for(runner.wait(), timeout=300 if crawl or recall else 200)
            except TimeoutError:
                runner.kill()
                await runner.wait()
        text = open(runner_out, encoding="utf-8", errors="replace").read()
        print("---- runner output ----\n" + text + "-----------------------")
        after = state_req({"op": "state", "since": 1 << 62})["world"]
        writer.close()
        drainer.cancel()
        ticker.cancel()

        await asyncio.sleep(1.0)
        store = memory.Memory(db)
        rows = store.episodes("hunt")
        js = store.junctures()
        evs = store.job_events("hunt")
        log = [json.loads(ln) for f in os.listdir(LOGDIR) if f.endswith(".jsonl")
               for ln in open(os.path.join(LOGDIR, f), encoding="utf-8")]
        srcs = {e.get("src") for e in log if e.get("dir") == "c2s"}
        c2s = world.c2s
        if recall:
            check_recall(world, text, runner.returncode, rows, js, evs)
            return
        check_penalty(world, text, rows, "crawl" if crawl else "fight spot" if fight else "staff" if staff
                      else "default", fight=bool(fight))
        if staff:
            check_staff(world, text, runner.returncode)
            return
        if fight:
            check_fight(world, text, runner.returncode, rows, js)
            return
        if crawl:
            check_crawl(world, text, runner.returncode, rows, js, evs)
            return

        check("runner exited 0 after 2 kills, outside",
              runner.returncode == 0 and "hunt complete: 2 kill(s)" in text and not world.inside,
              f"rc {runner.returncode} at {world.pos}")
        check("S (died to someone else: 0xDEAD only) left the live world model before the run",
              f"0x{S:08X}" not in before["mobiles"] and f"0x{S:08X}" not in (before.get("swings") or {}),
              str(sorted(before["mobiles"])))
        check("never a packet at the dead mongbat S, nor at its corpse",
              not [p.hex() for p in c2s if refs(p) in (S, CORPSE[S])])
        i = next((k for k, p in enumerate(c2s) if p[0] == 0x05), None)
        check("first attack: war mode on, then the stock 0x34 + 0x05 on A (combat.attack_packets), back to back",
              i is not None and c2s[i - 1] == actions.status_request(A) and c2s[i] == actions.attack(A)
              and actions.war_mode(True) in c2s[:i - 1]
              and world.c2s_t[i] - world.c2s_t[i - 1] < 0.05, str([p.hex() for p in c2s[max(0, (i or 0) - 3):(i or 0) + 1]]))
        att_a = [k for k, p in enumerate(c2s) if p == actions.attack(A)]
        check("A re-attacked once when it came adjacent, without 0x34 (status request still outstanding)",
              len(att_a) == 2 and c2s[att_a[1] - 1] != actions.status_request(A), str(att_a))
        casts = [p for p in c2s if p[0] == 0xFF and p[3:7] == u32(4)]
        spells = (actions.cast_spell(LIGHTNING), actions.cast_spell(GREATER_HEAL), actions.cast_spell(HEAL))
        check("casts are the stock 0xFF sub 4 for Lightning, Heal and Greater Heal",
              casts and all(p in spells for p in casts) and LIGHTNING in world.casts, str(world.casts))
        light = [(p, w) for s, p, w, t, h in world.targets if s == LIGHTNING]
        heals = [(s, p, w, t, h) for s, p, w, t, h in world.targets if s in (GREATER_HEAL, HEAL)]
        check("every Lightning 0x6C byte-equal to target_object on the mob's current tile (harmful cursor)",
              light and all(p == w for p, w in light), str([(p.hex(), w and w.hex()) for p, w in light][:3]))
        check("every heal spell's 0x6C on self byte-equal (beneficial cursor)",
              heals and all(p == w for s, p, w, t, h in heals), str([(p.hex(), w and w.hex()) for s, p, w, t, h in heals]))
        ref_t = world.reagent_refusals[0] if world.reagent_refusals else None
        check("Lightning answered 'more reagents needed' (502630): not cast again that visit, cast again after "
              "re-entering",
              ref_t is not None and world.exits and world.reentered
              and not [t for t in world.light_t if ref_t < t < world.exits[0]]
              and [t for t in world.light_t if t > world.reentered],
              f"refused {world.reagent_refusals} lightning {world.light_t} exits {world.exits} in {world.reentered}")
        chosen = [(m.group(1), int(m.group(2))) for m in re.finditer(r"\b(greater heal|heal): (\d+) -> ", text)]
        check("each heal spell by the missing hits when chosen: Greater Heal from --gheal-min-missing, else Heal",
              chosen and all((name == "greater heal") == (100 - h >= GHEAL_MIN) for name, h in chosen),
              str(chosen))
        check("any cursor the runner dropped (the leave rule fired while aiming) got the stock Esc 0x6C",
              all(p == esc for p, esc in world.cancels), str([(p.hex(), esc.hex()) for p, esc in world.cancels]))
        first_cast = next((t for s, p, w, t, h in heals), None)
        check("in the fight, the first heal after A's 30-point hit is a potion (+25), not a spell",
              world.drinks and world.exits and world.drinks[0] < world.exits[0]
              and (first_cast is None or world.drinks[0] < first_cast), f"drinks {world.drinks} heals {heals[:1]}")
        check("its bag was opened (stock dclick) before the potion's double-click",
              POT_BAG in world.dclicks and world.dclicks.index(POT_BAG) < world.dclicks.index(POTION),
              str([hex(s) for s in world.dclicks]))
        lt_, la = world.loot_hit_t, world.loot_a_t
        check("hit below --heal-at while looting A: a heal (potion or spell) before the gold is taken",
              lt_ is not None and la is not None
              and (any(lt_ < t < la for t in world.drinks) or any(lt_ < t < la for s, p, w, t, h in heals)),
              f"hit {lt_} gold {la} drinks {world.drinks} heals {[t for s, p, w, t, h in heals]}")
        check("no potion double-clicked again within 10 s of a drink (the runner's own clock)",
              all(b - a >= 10.0 for a, b in zip(world.drinks, world.drinks[1:]))
              and not [t for t in world.refused if any(0 <= t - d < 10.0 for d in world.drinks)],
              f"drinks {world.drinks} refused {world.refused}")
        out_pots = [t for t in world.drinks + world.refused if world.exits[0] < t < world.entries[0]] \
            if world.exits and world.entries else None
        check("no potions while resting outside (spells only)", out_pots == [], str(out_pots))
        ref = world.refused[0] if world.refused else None
        check("after re-entering, the potion the client's drink made cool down was refused (500235) and a heal "
              "spell went out within 3 s instead",
              ref is not None and world.reentered is not None and ref > world.reentered
              and any(0 <= t - ref < 3.0 for s, p, w, t, h in heals), f"refused {world.refused}")
        check("A and D died; both corpses opened and their gold looted into the pack (0x07+0x08 per GrabItem)",
              world.looted == {CORPSE[A]: 21, CORPSE[D]: 23} and world.drops_refused == 0
              and all(any(c2s[k] == want[0] and c2s[k + 1] == want[1] for k in range(len(c2s) - 1))
                      for want in (combat.grab_packets(GOLD[A][0], 21, BACKPACK),
                                   combat.grab_packets(GOLD[D][0], 23, BACKPACK))),
              f"{world.looted} refused {world.drops_refused}")
        check("the backpack was opened (stock dclick) before the first corpse",
              BACKPACK in world.dclicks and CORPSE[A] in world.dclicks
              and world.dclicks.index(BACKPACK) < world.dclicks.index(CORPSE[A]),
              str([hex(s) for s in world.dclicks]))
        off_t = [t for p, t in zip(c2s, world.c2s_t) if p == actions.war_mode(False)]
        check("war mode off when nothing was near (after A's loot, before D and E came)",
              world.loot_a_t is not None and world.spawn_t is not None
              and any(world.loot_a_t < t < world.spawn_t for t in off_t), str(off_t))
        threat = [j for j in js if j["kind"] == "threat"]
        check("two attackers below 80 %: one `threat` juncture (attention) naming both, then out through the exit",
              len(threat) == 1 and threat[0]["severity"] == "attention"
              and {a["serial"] for a in threat[0]["data"]["attackers"]} == {f"0x{D:08X}", f"0x{E:08X}"}
              and len(world.exits) == 2, f"{threat} exits {len(world.exits)}")
        check("the multi-attacker rule (not --leave-at 0.60) made it leave",
              threat and threat[0]["data"]["why"].startswith("2 attackers"), str(threat[:1])[:200])
        check("rested outside with a heal spell and went back in through the entrance (one entry)",
              len(world.entries) == 1 and world.reentered is not None
              and any(world.exits[0] < t < world.entries[0] for s, p, w, t, h in heals),
              f"entries {len(world.entries)}")
        check("E (out of range since we left) was never targeted after re-entering",
              world.reentered is not None
              and not [p.hex() for p, t in zip(c2s, world.c2s_t) if t > world.reentered and refs(p) == E])
        check("E is in last_seen (why range), not in mobiles",
              f"0x{E:08X}" not in after["mobiles"]
              and (after.get("last_seen") or {}).get(f"0x{E:08X}", {}).get("why") == "range",
              str((after.get("last_seen") or {}).get(f"0x{E:08X}")))
        check("no death, no low_supplies; junctures: the one threat only",
              {j["kind"] for j in js} == {"threat"}, str([j["kind"] for j in js]))
        kills = [e for e in evs if e["kind"] == "kill"]
        loots = [e for e in evs if e["kind"] == "loot"]
        check("job events: 2 kills (A, D), 2 loots with the gold, xp = the corpse's gold, the mob's name; 1 leave per exit",
              [e["data"]["serial"] for e in kills] == [f"0x{A:08X}", f"0x{D:08X}"]
              and [e["data"]["gold"] for e in loots] == [21, 23] and [e["data"]["xp"] for e in loots] == [21, 23]
              and [e["data"]["mob"] for e in loots] == [f"0x{A:08X}", f"0x{D:08X}"]
              and all("mongbat" in e["data"]["name"] for e in loots)
              and len([e for e in evs if e["kind"] == "leave"]) == 2, str([(e["kind"], e["data"]) for e in evs])[:400])
        check("two episode rows (visits): 1 kill each, gold and xp 21 / 23, hits lost counted, ends recorded",
              len(rows) == 2 and [r["kills"] for r in rows] == [1, 1] and [r["gold"] for r in rows] == [21, 23]
              and [r["xp"] for r in rows] == [21, 23]
              and rows[0]["hits_lost"] >= 30 and "attackers" in rows[0]["ended"] and rows[1]["ended"] == "done",
              str(rows)[:500])
        check("default (no --fight-spot): the rows' fight spot is the spot, no route margin (--leave-at as given)",
              rows and all(r["fight_spot"] == list(SPOT) and r["route_steps"] == 0 and r["leave_at"] == 0.6
                           for r in rows), str([{k: r.get(k) for k in ("fight_spot", "route_steps", "leave_at")}
                                                for r in rows]))
        check("nothing said in game", not [p for p in c2s if p[0] in (0xAD, 0x03)])
        check("every C2S packet came from the client or the agent (none from the proxy)",
              srcs <= {"client", "agent"}, str(srcs))
        intents = [e["intent"] for e in log if e.get("ev") == "agent_intent" and e.get("intent")]
        kinds = {i.get("kind") for i in intents}
        check("intents: attack, cast, heal, loot, leave, enter, done (loop hunt)",
              {"attack", "cast", "heal", "loot", "leave", "enter", "done"} <= kinds
              and intents[-1]["kind"] == "done" and intents[-1].get("loop") == "hunt", str(sorted(kinds)))
    finally:
        proxy.terminate()
        proxy.wait()
        server.close()


# The loot-rights run (in process, no proxy): HuntLoop.loot over a WorldRuntime fed with the
# packets above. Four of our kills lie beside the spot, each corpse holding gold (live the
# contents come with the corpse, before any open): BLUE's 0xDEAD says notoriety 1 (another
# player did the most damage), REFUSE's open is answered with the server's refusal text,
# REJECT opens but its lift gets 27 05 + the gold back in the corpse + a 0x1D (capture
# 20261003_125556), GOOD is looted as usual.
REFUSAL = "Players cannot commit aggressive actions in that location."
BLUE_C, REFUSE_C, REJECT_C, GOOD_C = 0x4FE00021, 0x4FE00022, 0x4FE00023, 0x4FE00024
RIGHTS = {BLUE_C: (0x002C6001, 0x4FE10021, 30, 1), REFUSE_C: (0x002C6002, 0x4FE10022, 31, 3),
          REJECT_C: (0x002C6003, 0x4FE10023, 32, 3), GOOD_C: (0x002C6004, 0x4FE10024, 25, 3)}


class RightsLink:
    """The runner's Link over an in-process WorldRuntime (the proxy's state shape): act()
    feeds our C2S packet, then the server's answer, into it."""

    def __init__(self):
        from world.runtime import WorldRuntime
        self.rt = WorldRuntime()
        self.events, self.event_t, self.last, self.sent = [], [], None, []
        self.gold, self.lifted = 100, None

    def feed(self, *pkts):
        for p in pkts:
            self.rt.feed_packet("s2c", p)

    def act(self, p):
        self.sent.append(p)
        self.rt.feed_packet("c2s", p)
        s = int.from_bytes(p[1:5], "big") if len(p) >= 5 else None
        if p[0] == 0x06 and s == BACKPACK:
            self.feed(open_container(BACKPACK, 0x3C))
        elif p[0] == 0x06 and s in RIGHTS:
            self.feed(system_text(REFUSAL) if s in (BLUE_C, REFUSE_C) else open_container(s, 0x09))
        elif p[0] == 0x07:
            self.lifted = s
        elif p[0] == 0x08:
            corpse = next(c for c, (_, g, _, _) in RIGHTS.items() if g == self.lifted)
            _, g, n, _ = RIGHTS[corpse]
            if corpse == REJECT_C:
                self.feed(system_text(REFUSAL), b"\x27\x05", contained(g, combat.GOLD_GRAPHIC, n, corpse), delete(g))
            else:
                self.gold += n
                self.feed(delete(g), contained(PACK_GOLD, combat.GOLD_GRAPHIC, self.gold, BACKPACK))

    def state(self):
        evs = json.loads(json.dumps(self.rt.drain_events()))
        self.events += evs
        self.event_t += [time.time()] * len(evs)
        s = self.rt.state
        self.last = {"movement": {"pos": [s.self.x, s.self.y, s.self.z], "self_serial": s.self.serial,
                                  "stalled": False, "rejects_in_row": 0, "inflight": 0},
                     "world": json.loads(json.dumps(s.snapshot()))}
        return self.last

    movement = state

    def intent(self, *a, **k):
        pass


def rights():
    print("\n== loot rights (in process): blue corpse skipped, refused open, lift reject ==")
    import loop_hunt
    from agent_link import Link
    for name in ("wait", "pos", "open_containers"):
        setattr(RightsLink, name, getattr(Link, name))
    db = os.path.join(tempfile.mkdtemp(), "harness.db")
    store = memory.Memory(db)
    link = RightsLink()
    link.feed(login_pkt(), equip(BACKPACK, 0x0E75, 0x15), contained(PACK_GOLD, combat.GOLD_GRAPHIC, 100, BACKPACK))
    for corpse, (mob, g, n, noto) in RIGHTS.items():
        link.feed(mob_pkt(mob, SPOT[0], SPOT[1] - 1), ground_item(corpse, combat.CORPSE_GRAPHIC, MONGBAT, SPOT[0], SPOT[1] - 1),
                  display_death(mob, corpse), delete(mob), corpse_flags(corpse, mob, noto=noto),
                  contained(g, combat.GOLD_GRAPHIC, n, corpse))
    args = loop_hunt.arg_parser().parse_args(["--human", "off", "--quiet", "--triage-url", "", "--no-map",
                                              "--memory", db])
    loop = loop_hunt.HuntLoop(link, store, args)
    loop.state()
    for corpse, (mob, _, _, _) in RIGHTS.items():          # four kills of ours (on_kill)
        loop.count("kills")
        loop.corpses.append({"mob": mob, "name": "a mongbat", "x": SPOT[0], "y": SPOT[1] - 1, "t": time.monotonic()})
    sent = {}
    while loop.corpses:
        c, n0 = loop.corpses[0], len(link.sent)
        loop.loot(c)
        sent[c["corpse"]] = link.sent[n0:]
    loots = {int(e["data"]["corpse"], 16): e["data"] for e in store.job_events("hunt") if e["kind"] == "loot"}
    store.close()

    def to(corpse):
        return [(p[0], int.from_bytes(p[1:5], "big")) for p in sent[corpse] if p[0] in (0x06, 0x07, 0x08)]
    check("blue corpse: no packet at all (no walk, no open, no lift); refused 'blue', no gold, no xp",
          sent[BLUE_C] == [] and loots[BLUE_C].get("refused") == "blue" and loots[BLUE_C]["xp"] == 0,
          f"{sent[BLUE_C]} {loots.get(BLUE_C)}")
    check("refused open: the one 0x06 at it and no lift; the refusal text recorded",
          [x for x in to(REFUSE_C) if x != (0x06, BACKPACK)] == [(0x06, REFUSE_C)]
          and loots[REFUSE_C].get("refused") == REFUSAL and loots[REFUSE_C]["items"] == []
          and loots[REFUSE_C]["xp"] == 0, f"{to(REFUSE_C)} {loots.get(REFUSE_C)}")
    check("lift rejected (27 05, back in the corpse, then 0x1D): not taken, no gold",
          [k for k, _ in to(REJECT_C)][-2:] == [0x07, 0x08] and loots[REJECT_C]["items"] == []
          and loots[REJECT_C]["lift_rejects"] == 1 and loots[REJECT_C]["gold"] == 0, str(loots.get(REJECT_C)))
    check("the good corpse: its gold taken and counted",
          [i["serial"] for i in loots[GOOD_C]["items"]] == [f"0x{RIGHTS[GOOD_C][1]:08X}"] and loots[GOOD_C]["gold"] == 25,
          str(loots.get(GOOD_C)))
    t = loop.totals
    check("totals: the two refused kills taken back, xp only from the opened corpses (32 + 25), gold 25",
          (t["kills"], t["lost_kills"], t["xp"], t["gold"]) == (2, 2, 57, 25), str(t))
    check("every corpse handled once (never retried)", loop.looted == set(RIGHTS) and not loop.corpses, str(loop.looted))



if __name__ == "__main__":
    only = sys.argv[1:]               # e.g. `crawl`: just that run (iterating); none: all six
    if not only or "rights" in only:
        rights()
    if not only or "default" in only:
        asyncio.run(main())
    if not only or "staff" in only:
        asyncio.run(main(staff=True))
    if not only or "fight" in only:
        asyncio.run(main(fight=FIGHT))
    if not only or "crawl" in only:
        asyncio.run(main(crawl=True))
    if not only or "recall" in only:
        asyncio.run(main(recall=True))
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILURES: {FAILURES}"))
    sys.exit(0 if not FAILURES else 1)

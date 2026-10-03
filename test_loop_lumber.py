"""Offline end-to-end test of the lumber loop runner (harness/loop_lumber.py).

A simulated Shelter server behind the real proxy, with the packet shapes and
texts of the demonstration capture (logs/session_20260929_204225):

- hatchet dclick → cliloc 1010018 + location cursor; tree target → a decoy
  "Captcha" gump (no buttons) on every attempt and a real captcha (gump id 1,
  entry 2, Guide button 1 + a submit button whose id isn't the demo's) once per
  trip: trip 1's is a captured solver-readable layout (the runner answers it
  itself), trip 2's is unreadable (pause + beep fallback), then fail/success results; a second
  tree answers "not enough wood" (depleted)
- log stack target → "You shape the logs into boards." (1:1)
- "bank" within 12 tiles of the banker → the bank box (layer 0x1D) opens (0x24); any step
  closes it again (RunUO); lift + drop into the open box, merging stacks like RunUO
- a closed town door between the trees and the bank that opens on the stock open-door
  request and swings shut once the agent is past it
- a moongate on each tile west of that door, so every route to the bank and back steps on
  one: like Shelter's player-cast gates (session 20261001_191355) it opens the renounce-Young
  prompt before the step's confirm and never closes it; the agent must close it (button 0)

The "human" answers the unreadable (fallback) captcha through the client connection. Two trips run.
The banker comes into view within 18 tiles and leaves it beyond 24 (the world model prunes him).

Two more runs on the same simulator (LUMBER_LOOP.md §13), each with its own proxy:
- skirmish: the hatchet in a bag in the pack; 'a great hart' in war mode 4 tiles from the tree
  fighting a player (0x2F both ways) is no threat (passive body); a creature that swings at the agent makes
  it escape and harvest the next tree out of reach; the same creature then hunts it down there
  (escape, kept coming: stop, the logs converted first)
- break: the agent gate (pre-written budget file) announces a break mid-harvest; the trip ends
  at the bank with the carried and new logs banked as boards, exit 0
Plus a unit check of hatchet() (worn, else the shallowest in the pack's bags).

Run: python test_loop_lumber.py   (~2-3 min; private ports; safe while the live proxy runs)
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
PY = r"C:/Users/chris/AppData/Local/Programs/Python/Python313/python.exe"
sys.path.insert(0, f"{ROOT}/harness")

import actions  # noqa: E402
import memory  # noqa: E402
import nav  # noqa: E402
from uo.packets import packet_length, C2S_OVERRIDES  # noqa: E402
from uo.s2c import encode_packet  # noqa: E402
from world.parsers import parse_packet  # noqa: E402
import viz_feed  # noqa: E402

PROXY_PORT, UPSTREAM_PORT, CONTROL_PORT, STATE_PORT = 12670, 12671, 12672, 12673
LOGDIR = f"{ROOT}/logs_test_loop"
C2S_KEY, S2C_KEY = 0x0F, 0x5A
PRELUDE = bytes([0xFF, 0x00, 0x0D] + [0] * 7 + [0x0C, S2C_KEY, C2S_KEY])
SELF, BACKPACK, HATCHET = 0x00094375, 0x44ADA059, 0x44ADB57A
BANKBOX, BANKER = 0x40000B0B, 0x000001EA
START = (100, 200)
GOOD_TREE = {"x": 111, "y": 200, "z": 0, "graphic": "0x0CE0", "stand": [110, 200]}
DRY_TREE = {"x": 105, "y": 194, "z": 0, "graphic": "0x0CE0", "stand": [105, 195]}
BANK_POS = (127, 200)                                    # where the banker actually stands: past the town door
BANK_KNOWN = (107, 200)     # knowledge from an older demo: 20 tiles off (NPCs move; speech range 12)
DOOR = (122, 200)                                        # a closed town door
WALLS = {(122, y) for y in range(180, 236)} - {DOOR}     # long enough that going round costs more than the door
GATES = {(121, y) for y in (199, 200, 201)}              # every route through the door crosses one
RENOUNCE_ID = 0xE2544541                                 # the renounce-Young prompt (20261001_191355)
RENOUNCE_LAYOUT = ("{ resizepic 28 23 11571 401 501 }{ button 22 24 2094 2095 1 0 1 }"
                   "{ text 64 45 2655 0 18 0 1 0 0 0 }{ button 60 460 247 248 1 0 2 }"
                   "{ button 300 460 241 242 1 0 3 }")
LOGS_PER_SUCCESS = 3
STOLEN = 2                                               # a pickpocket's take, once (the loop must carry on)
PASSERBY = 0x0000ABCD                                    # a player who walks up and says hello mid-harvest
GREETING = "hail! good trees here?"
HOLD_S = 3.0                                             # the test overseer's all-clear comes this long after
HIDDEN = 0x0000BEEF                                      # speaks during the hold, never on screen (hidden GM?)
GM_EXTRA_S = 2.0                                         # the GM suspicion is acked this long after the all-clear
GOOD_VISIT = 6                                           # attempts before the good tree runs dry
DD = nav.DIR_DELTA
FAILURES = []
# skirmish scenario (LUMBER_LOOP.md §13: monsters fighting others, escapes, convert on abort)
BAG = 0x44ADC0DE                                         # the hatchet sits in this bag inside the backpack
FAR_TREE = {"x": 119, "y": 212, "z": 0, "graphic": "0x0CE0", "stand": [119, 211]}
FIGHTER, OTHER, ATTACKER = 0x0000F161, 0x0000A0A0, 0x0000BA75
FIGHTER_POS, OTHER_POS = (113, 204), (114, 204)          # 4 tiles from the good tree's stand: in flee range (8)
ATTACKER_POS = (107, 200)                                 # 3 tiles west of the good tree's stand
# break scenario
INITIAL_LOGS = 5                                          # logs carried from an earlier trip
BREAK_AFTER_S = 4.0                                       # agent-active seconds left before the break is due
# library scenario (docs/research/WORLD_LOCATIONS.md): a Witcher-style spot reached by recalling from a
# public library tome, banked after recalling home with our runebook's default rune
TOME, RUNEBOOK = 0x546ACD06, 0x44ADB00C
LIB_START, TOME_POS = (132, 212), (133, 212)              # the library is in town, by the bank: no door between
RUNE_POS = (40, 250)                                      # where the tome's rune "286" puts us
LIB_TREE = {"x": 40, "y": 253, "z": 0, "graphic": "0x0CE0", "stand": [40, 252]}
HOME_RUNE_POS = (125, 205)                                # our runebook's default rune: by the bank, past the door
with open(f"{ROOT}/harness/testdata/escape_gumps.json", encoding="utf-8") as _f:
    _G = json.load(_f)
TOME_GUMP, BOOK_GUMP = _G["runetome_main_witcher_276"], _G["runebook_charges"]   # captured layouts
LOCKOUT_S = 2                                             # the travel lockout the simulated server reports


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


def login_pkt():
    body = u32(SELF) + bytes(4) + u32(0x190) + u32(START[0]) + u32(START[1]) + u32(0) + bytes([0x80])
    return b"\x1b" + body + bytes(42 - len(body))


def seed_pkt(token):
    return bytes.fromhex("bf001d0001") + u32(token) + bytes(20)


def self_at(x, y):          # 0x20 V10 for the player (teleport anchor)
    return b"\x20" + u32(SELF) + u32(0x190) + b"\x01\x83\xea\x20" + u32(x) + u32(y) + b"\x00\x00\x00" + u32(0)


def mobile_pkt(serial, x, y):
    return b"\x20" + u32(serial) + u32(0x191) + b"\x07\x03\xf1\x02" + u32(x) + u32(y) + b"\x00\x00\x03" + u32(0)


def creature_pkt(serial, body, x, y, noto=3, flags=0x40):
    """0x20 MobileUpdate for a creature (Outlands layout, world/layouts.py): war mode by default."""
    return (b"\x20" + u32(serial) + u32(body) + bytes([noto]) + u16(0) + bytes([flags])
            + u32(x) + u32(y) + b"\x00\x00\x00" + u32(0))


def swing(attacker, defender):
    return b"\x2f\x00" + u32(attacker) + u32(defender)


def equip(item, graphic, layer):
    return b"\x2e" + u32(item) + u32(graphic) + u32(0) + bytes([layer]) + u32(SELF) + u16(0)


def contained(serial, graphic, amount, container, x=50, y=60):
    return b"\x25" + u32(serial) + u32(graphic) + b"\x00" + u16(amount) + u16(x) + u16(y) + b"\x00" \
        + u32(container) + u16(0) + u32(0)


def ground_item(serial, graphic, x, y, z):
    return b"\xf3\x00\x01\x00" + u32(serial) + u32(graphic) + b"\x00" + u16(1) + b"\x00\x00" \
        + u32(x) + u32(y) + u32(z) + b"\x00" + u16(0) + u32(0) + u16(0)


def delete(serial):
    return b"\x1d" + u32(serial)


def sys_text(text):
    return var(0x1C, u32(0xFFFFFFFF) + b"\xff\xff\x00" + u16(0x3B2) + u16(3)
               + b"System".ljust(30, b"\x00") + text.encode() + b"\x00")


def player_update(serial, x, y):
    """0x20 MobileUpdate (Outlands layout, as captured in 123206): a human with the player
    flag 0x20, notoriety 1 (a blue player)."""
    return (b"\x20" + u32(serial) + u32(0x190) + b"\x01" + u16(0x83EA) + b"\x20" + u32(x) + u32(y)
            + b"\x00\x00\x02" + u32(0))


def player_says(serial, name, text):
    return var(0x1C, u32(serial) + u16(0x190) + b"\x00" + u16(0x3B2) + u16(3)
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
    def __init__(self, scenario="bank"):
        self.scenario = scenario          # "bank" (the main run), "skirmish" or "break"
        self.scripted = scenario == "bank"  # captchas, the passer-by's speech, the pickpocket
        self.pos = list(LIB_START) if scenario == "library" else list(START)
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
        self.pending_attempt = None
        self.good_n = 0
        self.logs_serial, self.logs = None, 0
        self.stolen = 0
        self.board_serial = 0x45000100
        self.pack_boards = {}             # serial -> amount
        self.bank_stack = None            # (serial, amount)
        self.lifted = None
        self.door_open = False
        self.open_door_reqs = 0
        self.bank_open = False
        self.bank_opens = 0
        self.far_bank_speech = 0          # "bank" said out of the banker's range
        self.bank_dclicks = 0             # a dclick can't open a bank box; the stock client never sends one
        self.drops_refused = 0
        self.harvested = 0
        self.dry_attempts = 0
        self.good_left = GOOD_VISIT
        self.doors_opened = 0
        self.containers_opened = []                          # 0x06 on the backpack (and the bag), in order
        self.gate_gumps = {}              # renounce-prompt serial -> buttons the agent/client replied
        self.attacker_pos = None          # skirmish: the creature that goes for the agent
        self.chase = False                # skirmish: the attacker follows the agent step for step
        self.attacker_swings = 0
        self.far_attempts = 0             # skirmish: harvest attempts at the far tree
        self.banker_seen = False          # the banker's 0x20 sent since he last left the client's view
        self.book_gumps, self.tome_gumps = set(), set()   # library scenario: gumps we sent
        self.recalls_out, self.recalls_home, self.tome_far = [], [], 0
        self.tome_seen = False
        self.home_disturbed = 0           # library: the first recall home is disturbed (escape casts again)
        self.lockout_due = False          # library: the first chop after a recall out meets the travel lockout
        self.lockouts = 0

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
        """The banker comes into view within the server's update range (18) and leaves the
        client's beyond its view range (24, ClassicUO MAX_VIEW_RANGE), as the proxy's world
        model prunes it; so he is sent again on the way back."""
        d = self.cheb(BANK_POS)
        if d <= 18 and not self.banker_seen:
            self.banker_seen = True
            self.send(mobile_pkt(BANKER, *BANK_POS))
        elif d > 24:
            self.banker_seen = False
        if self.scenario == "library":                   # the library tome, like any item, the same way
            t = self.cheb(TOME_POS)
            if t <= 18 and not self.tome_seen:
                self.tome_seen = True
                self.send(ground_item(TOME, 0x71AF, *TOME_POS, 0))
            elif t > 24:
                self.tome_seen = False

    def teleport(self, x, y):
        self.pos = [x, y]
        self.send(self_at(x, y))
        self.update_view()

    def recall_to(self, dest, log):
        """Kal Ort Por, then the jump about 2.1 s later (live 2026-10-02/03). Out at the
        library rune: a player chops nearby (crowding) and the travel lockout comes."""
        log.append(dest)
        self.send(sys_text("Kal Ort Por"))
        asyncio.get_running_loop().call_later(2.1, self.teleport, *dest)
        if log is self.recalls_out:
            self.lockout_due = True
            self.later(2.3, [player_update(OTHER, LIB_TREE["x"] + 4, LIB_TREE["y"])])

    # ---- harvest ----
    def harvest(self, x, y):
        if self.lockout_due:
            self.lockout_due = False
            self.lockouts += 1
            self.later(0.3, [sys_text(f"You have recently traveled and must wait {LOCKOUT_S} seconds "
                                      "before you may begin harvesting.")])
            return
        self.decoys.add(self.next_gump())
        self.send(gump(self.gump_serial, 0x50000000 + self.gump_serial, DECOY_LAYOUT,
                       ["Captcha", "Guide", "Type the Value", "Click when complete"]))
        if (x, y) == (DRY_TREE["x"], DRY_TREE["y"]):
            self.dry_attempts += 1
            self.later(0.3, [cliloc(500493)])
            return
        if self.good_left == 0:                          # out of wood; regrown for the next visit
            self.good_left = GOOD_VISIT
            self.later(0.3, [cliloc(500493)])
            return
        self.good_left -= 1
        if (x, y) == (FAR_TREE["x"], FAR_TREE["y"]):
            self.far_attempts += 1
        if self.scripted and self.captcha_shown < 2:
            self.captcha_shown += 1
            self.captcha_open = self.next_gump()
            self.captcha_auto = self.captcha_shown == 1     # trip 1: auto-solved; trip 2: fallback
            lay = REAL_CAPTCHA_LAYOUT if self.captcha_auto else UNREADABLE_CAPTCHA_LAYOUT
            self.send(gump(self.captcha_open, 0x00000001, lay, ["Guide", "Captcha", "", "Type The Value"]))
            self.pending_attempt = True
            return
        self.result()

    def result(self):
        self.good_n += 1
        if self.good_n % 2 == 1:
            self.later(0.3, [cliloc(500495)])
            return
        if self.logs_serial is None:
            self.logs_serial = 0x45000001 + self.good_n
        self.logs += LOGS_PER_SUCCESS
        self.harvested += LOGS_PER_SUCCESS
        self.later(0.3, [contained(self.logs_serial, 0x1BDD, self.logs, BACKPACK),
                         sys_text("You chop some logs and put them in your backpack.")])
        if not self.scripted:
            if self.scenario == "skirmish" and self.good_n == 2:      # a creature goes for the agent
                asyncio.get_running_loop().call_later(0.5, self.attacker_appears, ATTACKER_POS, False)
            elif self.scenario == "skirmish" and self.good_n == 4:    # ... and later hunts it down
                asyncio.get_running_loop().call_later(0.5, self.attacker_appears, None, True)
            elif getattr(self, "chaser", False) and self.good_n == 2:  # a creature hunts us at a far spot
                asyncio.get_running_loop().call_later(0.5, self.attacker_appears, None, True)
            return
        if self.good_n == 4:                 # a pickpocket lifts part of the stack once, unannounced
            self.logs -= STOLEN
            self.stolen += STOLEN
            self.later(0.8, [contained(self.logs_serial, 0x1BDD, self.logs, BACKPACK)])
        if self.good_n == 2:                 # a player walks up and speaks: the job must hold for the overseer
            self.spoke_at = time.time() + 1.0
            self.later(0.5, [player_update(PASSERBY, self.pos[0] + 2, self.pos[1])])
            self.later(1.0, [player_says(PASSERBY, "Vorn", GREETING)])
            # during the hold, someone the client can't see speaks: a possible hidden GM
            self.later(2.0, [player_says(HIDDEN, "Ann", "what are you up to?")])

    def attacker_appears(self, pos, chase):
        """The attacker comes into view at pos (None: next to the agent, then at its heels)."""
        self.attacker_pos = pos or (self.pos[0] + 1, self.pos[1])
        self.chase = chase
        self.send(creature_pkt(ATTACKER, 0x27, *self.attacker_pos))

    def convert(self, serial):
        if serial != self.logs_serial:
            return
        self.board_serial += 1
        self.pack_boards[self.board_serial] = self.logs
        pk = [delete(self.logs_serial), contained(self.board_serial, 0x1BD7, self.logs, BACKPACK),
              sys_text("You shape the logs into boards.")]
        self.logs_serial, self.logs = None, 0
        self.later(0.2, pk)

    # ---- packets ----
    def on_packet(self, p):
        self.c2s.append(p)
        self.c2s_t.append(time.time())
        pid = p[0]
        if pid == 0x02:
            seq, d = p[2], p[1] & 7
            if d != self.facing:
                self.facing = d
                self.send(bytes([0x22, seq, 0x01]))
                return
            nx, ny = self.pos[0] + DD[d][0], self.pos[1] + DD[d][1]
            blocked = (nx, ny) in WALLS or ((nx, ny) == DOOR and not self.door_open) \
                or (nx, ny) == BANK_POS
            if blocked:
                self.send(b"\x21" + bytes([seq]) + u32(self.pos[0]) + u32(self.pos[1])
                          + bytes([self.facing]) + u32(0))
                return
            old, self.pos = tuple(self.pos), [nx, ny]
            if (nx, ny) in GATES:                        # the gate's gump comes before the confirm
                self.gate_gumps[self.next_gump()] = []
                self.send(gump(self.gump_serial, RENOUNCE_ID, RENOUNCE_LAYOUT,
                               ["Young Player Status", "Guide",
                                "Leaving Shelter Island will cause you to renounce your"]))
            self.bank_open = False                       # moving closes the bank box (RunUO)
            if self.door_open and self.cheb(DOOR) > 2:   # the door swings shut behind the agent
                self.door_open = False
            self.send(bytes([0x22, seq, 0x01]))
            self.update_view()
            if self.chase:                               # the attacker keeps at the agent's heels
                self.attacker_pos = old
                self.send(creature_pkt(ATTACKER, 0x27, *old))
        elif pid == 0x12 and p[3] == 0x58:
            self.open_door_reqs += 1
            if self.cheb(DOOR) <= 1:
                self.door_open = True
                self.doors_opened += 1
                self.send(cliloc(500024))
        elif pid == 0x06:
            serial = int.from_bytes(p[1:5], "big")
            if serial == HATCHET:
                self.cid += 1
                self.cursor_for = self.cid
                self.send(cliloc(1010018))
                self.send(cursor(self.cid))
            elif serial == BANKBOX:
                self.bank_dclicks += 1
            elif serial in (BACKPACK, BAG):
                self.containers_opened.append(serial)
                self.send(b"\x24" + u32(serial) + bytes.fromhex("0000003c007d"))   # as captured (204225)
            elif serial == TOME:                         # a locked-down tome opens within 2 tiles only
                if self.cheb(TOME_POS) > 2:
                    self.tome_far += 1
                    return
                self.tome_gumps.add(self.next_gump())
                self.send(gump(self.gump_serial, 0x09F5976B, TOME_GUMP["layout"], TOME_GUMP["lines"]))
            elif serial == RUNEBOOK:
                self.book_gumps.add(self.next_gump())
                self.send(gump(self.gump_serial, 0x5C7DB029, BOOK_GUMP["layout"], BOOK_GUMP["lines"]))
        elif pid == 0x6C:
            f = parse_packet("c2s", p)
            if f["cursor_id"] != self.cursor_for:
                return
            self.cursor_for = None
            if f["target_type"] == 1 and self.cheb((f["x"], f["y"])) <= 2:
                self.harvest(f["x"], f["y"])
            elif f["target_type"] == 0:
                self.convert(f["serial"])
        elif pid == 0xB1:
            f = parse_packet("c2s", p)
            if f["serial"] in self.decoys:
                self.decoy_replies += 1
            elif f["serial"] in self.tome_gumps and f["button_id"] == 110:     # row 10: "286 - Midlands ..."
                self.recall_to(RUNE_POS, self.recalls_out)
            elif f["serial"] in self.book_gumps and f["button_id"] == 8:       # default entry 1, a charge
                if self.home_disturbed == 0:                                   # the first one is disturbed
                    self.home_disturbed += 1
                    self.send(sys_text("Kal Ort Por"))
                    self.later(0.5, [cliloc(500641)])
                    return
                self.recall_to(HOME_RUNE_POS, self.recalls_home)
            elif f["serial"] in self.gate_gumps:
                self.gate_gumps[f["serial"]].append(f["button_id"])
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
                    self.pending_attempt = None
                    self.result()
        elif pid == 0xAD:
            f = parse_packet("c2s", p)
            if f.get("text") != "bank":
                return
            if self.cheb(BANK_POS) > 12:
                self.far_bank_speech += 1
                return
            self.bank_open = True
            self.bank_opens += 1
            self.send(equip(BANKBOX, 0x0E7C, 0x1D))
            self.send(b"\x24" + u32(BANKBOX) + bytes.fromhex("0000004a007d"))
            if self.bank_stack:
                self.send(contained(self.bank_stack[0], 0x1BD7, self.bank_stack[1], BANKBOX))
        elif pid == 0x07:
            self.lifted = parse_packet("c2s", p)
        elif pid == 0x08:
            f = parse_packet("c2s", p)
            lf, self.lifted = self.lifted, None
            if lf is None or lf["serial"] != f["serial"] or f["container"] != BANKBOX \
                    or not self.bank_open or self.cheb(BANK_POS) > 12:
                self.drops_refused += 1
                return
            amount = self.pack_boards.pop(f["serial"], 0)
            if self.bank_stack is None:
                self.bank_stack = (f["serial"], amount)
                self.send(contained(f["serial"], 0x1BD7, amount, BANKBOX))
            else:                                        # RunUO stacks with the existing pile
                self.bank_stack = (self.bank_stack[0], self.bank_stack[1] + amount)
                self.send(delete(f["serial"]))
                self.send(contained(self.bank_stack[0], 0x1BD7, self.bank_stack[1], BANKBOX))

    async def handle(self, reader, writer):
        await reader.readexactly(5)
        self.writer = writer
        writer.write(PRELUDE)
        self.send(login_pkt())
        for token in (5, 6, 7, 8):
            self.send(seed_pkt(token))
        self.send(equip(BACKPACK, 0x0E75, 0x15))
        if self.scenario == "skirmish":                         # the hatchet is in a bag in the pack
            self.send(contained(BAG, 0x0E76, 1, BACKPACK))
            self.send(contained(HATCHET, 0x0F44, 1, BAG))
            self.send(creature_pkt(FIGHTER, 0xEA, *FIGHTER_POS))      # 'a great hart' in war mode ...
            self.send(player_update(OTHER, *OTHER_POS))               # ... fighting a player (knowledge #89)
            asyncio.get_running_loop().create_task(self.combat())
        else:
            self.send(equip(HATCHET, 0x0F44, 0x02))
        if self.scenario == "library":
            self.send(self_at(*LIB_START))
            self.send(contained(RUNEBOOK, 0x22C5, 1, BACKPACK))
            self.send(contained(0x44ADB0FF, 0x0F7A, 10, BACKPACK))   # black pearl: charges spend none
        if self.scenario == "break":                            # logs carried from an earlier trip
            self.logs_serial, self.logs = 0x45000001, INITIAL_LOGS
            self.send(contained(self.logs_serial, 0x1BDD, self.logs, BACKPACK))
        self.update_view()                                      # the banker, once within range
        self.send(ground_item(0x40005CE3, 0x06AD, *DOOR, 0))     # the town door (demo art)
        for i, (gx, gy) in enumerate(sorted(GATES)):
            self.send(ground_item(0x40006000 + i, 0x0F6C, gx, gy, 0))  # blue moongates
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


def write_spot(path, trees=(GOOD_TREE, DRY_TREE), **extra):
    """The simulator's spot 'sim' (lumber_opt spot format, a --spots file): its trees and
    banker; no hostile player actions there, so no recall book is needed (extra fields
    override). The common knowledge is the committed loops/lumber.json."""
    spot = {"id": "sim", "name": "simulated Shelter trees", "facet": 0,
            "area": {"center": list(START), "radius": 30}, "trees": list(trees),
            "banker": {"serial": f"0x{BANKER:08X}", "name": "Len the banker", "pos": [*BANK_KNOWN, 0]},
            "pvp": False, **extra}
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"spots": [spot]}, f)


def write_witcher(path):
    """A Witcher table for the simulated library: one tome at TOME_POS holding rune 286."""
    doc = {"libraries": [{"id": "cambria", "name": "Sim Rune Library", "facet": 0, "stand": list(LIB_START),
                          "moongate": "Sim", "use_range": 2,
                          "tomes": [{"serial": f"0x{TOME:08X}", "first": "276", "last": "301",
                                     "pos": [*TOME_POS, 0]}]}],
           "runes": [{"id": "286", "name": "Midlands Ruins 1 (South)", "x": RUNE_POS[0], "y": RUNE_POS[1],
                      "tome": f"0x{TOME:08X}"}]}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f)


async def main():
    os.makedirs(LOGDIR, exist_ok=True)
    for f in os.listdir(LOGDIR):
        if os.path.isfile(os.path.join(LOGDIR, f)):      # the other scenarios keep subdirectories
            os.remove(os.path.join(LOGDIR, f))
    tmp = tempfile.mkdtemp()
    paths = {"spots": os.path.join(tmp, "spots.json"), "db": os.path.join(tmp, "harness.db")}
    write_spot(paths["spots"])
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
            "--spot", "sim", "--spots", paths["spots"], "--memory", paths["db"],
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
        check("agent gump replies = the auto-solved captcha + the moongate prompts it closed",
              len(b1_agent) - len(b1_gate) == 1, f"{len(b1_agent)} agent replies, {len(b1_gate)} to gates")
        closes = [e for e in log if e.get("ev") == "gump_close_client"]
        check("every moongate prompt the route opened was closed once, with button 0 (the stock right-click), "
              "and the client's copy closed by the proxy",
              len(world.gate_gumps) >= 3 and all(v == [0] for v in world.gate_gumps.values())
              and len(b1_gate) == len(world.gate_gumps) and len(closes) == len(b1_agent),
              f"{world.gate_gumps} client closes {len(closes)}")
        check("each trip opened the bank box by saying 'bank' next to the banker; the run ends at the bank",
              world.bank_opens == 2 and world.cheb(BANK_POS) <= 4 and "waiting at the bank" in text,
              f"{world.bank_opens} opens, at {world.pos}")
        check("walked to the banker's live position: never spoke out of range (knowledge pos is 20 tiles off)",
              world.far_bank_speech == 0, str(world.far_bank_speech))
        check("every harvested log not stolen ended in the bank box as boards; no drop refused",
              world.bank_stack is not None and world.harvested == 2 * 3 * LOGS_PER_SUCCESS
              and world.bank_stack[1] == world.harvested - world.stolen and world.drops_refused == 0,
              f"bank {world.bank_stack}, harvested {world.harvested}, stolen {world.stolen}, "
              f"refused {world.drops_refused}")
        check("nothing left in the backpack", world.logs == 0 and not world.pack_boards)
        check("each trip tried the dry tree once, then moved on",
              world.dry_attempts == 2, str(world.dry_attempts))
        check("harvest memory (store): dry tree depleted, good tree counted",
              dry_node.get("depleted_at") is not None and good_node.get("successes", 0) >= 4
              and good_node.get("yield") == world.harvested, f"{dry_node} {good_node}")
        check("walk memory (store): the proxy recorded the agent's walks",
              len(walked.edges) >= 20, str(walked.stats()))
        check("open-door requests only next to a door, like the client's auto-open (never at plain walls)",
              world.open_door_reqs == world.doors_opened, f"{world.open_door_reqs} requests, "
              f"{world.doors_opened} opened")
        check("the town door opened on each of the 3 crossings (to the bank, back out, to the bank)",
              world.doors_opened >= 3, str(world.doors_opened))
        check("like a player, the agent opened the backpack once before targeting the logs in it; the bank "
              "box is never double-clicked (only 'bank' opens it)",
              world.containers_opened == [BACKPACK] and world.bank_dclicks == 0,
              f"{world.containers_opened} bank dclicks {world.bank_dclicks}")
        check("only speech: 'bank', stock-encoded, once per trip",
              len(speech) == 2 and all(p == actions.say_unicode("bank") for p in speech), str(len(speech)))
        check("two episode rows with logs and banked boards",
              len(rows) == 2 and all(r.get("logs", 0) >= 6 and r.get("stored", 0) >= 6
                                     and set(r["phases_s"]) == {"harvest", "convert", "to_bank", "store"}
                                     for r in rows), str(rows))
        check("trip rows say where, how it ended and with what: spot sim, banked, the worn iron hatchet, "
              "the walk out and the chopping inside the harvest time, nothing carried at the end",
              all(r.get("spot") == "sim" and r.get("outcome") == "banked" and r.get("why") is None
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
        phase = ["to_tree", "chop", "convert", "to_bank", "open_bank", "store", "trip_done"]
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


async def run_scenario(world, tag, port_base, trees, runner_args, budget=None, spot_extra=None):
    """The real proxy in front of `world` on private ports port_base..+3 (logdir
    LOGDIR/<tag>, an optional pre-written agent gate file), then the runner with
    runner_args. Returns (runner output, exit code, memory store, capture rows)."""
    logdir = os.path.join(LOGDIR, tag)
    os.makedirs(logdir, exist_ok=True)
    for f in os.listdir(logdir):
        os.remove(os.path.join(logdir, f))
    if budget is not None:
        with open(os.path.join(logdir, "agent_budget.json"), "w", encoding="utf-8") as f:
            json.dump(budget, f)
    tmp = tempfile.mkdtemp()
    spots, db, witcher = (os.path.join(tmp, n) for n in ("spots.json", "harness.db", "witcher.json"))
    write_spot(spots, trees, **(spot_extra or {}))
    write_witcher(witcher)
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
            "--spot", "sim", "--spots", spots, "--witcher", witcher, "--memory", db, "--timeout", "300",
            "--quiet", "--no-map", "--max-blocked", "80",
            "--triage-url", "", *runner_args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(runner.communicate(), timeout=360)
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
    the agent (escape, then the next tree out of its reach); the same creature hunting
    it down at that tree (escape, kept coming: stop, logs converted first)."""
    print("\n== skirmish: hatchet in a bag, a hart fighting a player, a creature that goes for us ==")
    world = World("skirmish")
    text, code, store, _ = await run_scenario(world, "skirmish", 12680, [GOOD_TREE, FAR_TREE],
                                              ["--trips", "1", "--logs-per-trip", "100", "--human", "off"])
    dclicks = [int.from_bytes(p[1:5], "big") for p in world.c2s if p[0] == 0x06]
    check("hatchet in a bag in the pack: backpack, then the bag opened (once each), before the first hatchet use",
          world.containers_opened == [BACKPACK, BAG] and HATCHET in dclicks
          and dclicks.index(BACKPACK) < dclicks.index(BAG) < dclicks.index(HATCHET), str(dclicks[:6]))
    threat_js = [j for j in store.junctures() if j["kind"] == "threat"]
    acts = [j["data"].get("action") for j in threat_js]
    check("threat junctures (urgent): escape, escape (it came back), abort (it kept coming)",
          acts == ["escape", "escape", "abort"] and all(j["severity"] == "urgent" for j in threat_js),
          str([(j["summary"], j["data"].get("action")) for j in threat_js]))
    hart = [next((t for t in j["data"]["threats"] if t["serial"] == FIGHTER), {}) for j in threat_js]
    check("every threat was the attacker; the hart fighting the player (in flee range, war mode) wasn't a "
          "threat (a passive body in war mode is fighting someone else, threats.py)",
          threat_js and all(j["data"]["threats"][0]["serial"] == ATTACKER for j in threat_js)
          and hart[0].get("action") in ("watch", "ignore") and not hart[0].get("hostile")
          and hart[0].get("distance", 99) <= hart[0].get("flee_radius", 0), str(hart[:1]))
    m = re.search(r"escaped to \((\d+), (\d+)\)", text)
    to = (int(m[1]), int(m[2])) if m else None
    check("first escape: walked away from the attacker to beyond its flee radius (8)",
          to is not None and to[0] > GOOD_TREE["stand"][0]
          and max(abs(to[0] - ATTACKER_POS[0]), abs(to[1] - ATTACKER_POS[1])) > 8, str(to))
    far = store.harvest_node(0, FAR_TREE["x"], FAR_TREE["y"], FAR_TREE["z"]) or {}
    check("resumed at the next tree out of the attacker's reach and harvested there",
          world.far_attempts >= 2 and far.get("successes", 0) >= 1, f"{world.far_attempts} attempts, {far}")
    check("the attacker kept coming after the second escape: the run stopped (exit 1)",
          code == 1 and "kept coming after the escape" in text and world.attacker_swings >= 2,
          f"exit {code}, {world.attacker_swings} swings")
    check("a creature still coming: stop at once, no 10 s log conversion next to it (live 2026-10-03: "
          "85 -> 40 hits while converting); the logs stay logs, no bank trip",
          world.logs == 2 * LOGS_PER_SUCCESS and world.harvested == 2 * LOGS_PER_SUCCESS
          and not world.pack_boards and world.bank_opens == 0
          and "stopping at once, logs not converted" in text
          and "converting the carried logs before stopping" not in text,
          f"logs {world.logs}, boards {world.pack_boards}, harvested {world.harvested}")
    eps = store.episodes("lumber")
    check("the stopped trip still left its episode row: aborted, why, the logs it got and still "
          "carries, the hatchet from the bag",
          len(eps) == 1 and eps[0].get("outcome") == "aborted" and "kept coming" in (eps[0].get("why") or "")
          and eps[0].get("logs") == world.harvested
          and eps[0].get("carried_end") == {"logs": world.harvested, "boards": 0}
          and (eps[0].get("hatchet") or {}).get("worn") is False
          and "harvest" in eps[0]["phases_s"] and "to_bank" not in eps[0]["phases_s"],
          str(eps)[:600])
    store.close()


async def break_due():
    """docs/OVERSEER.md break_due: the agent gate announces a break mid-harvest; the
    trip ends early at the bank (carried and new logs banked as boards), exit 0."""
    print("\n== break due: stop harvesting, convert, bank, exit 0 ==")
    world = World("break")
    budget = {"day": datetime.date.today().isoformat(), "active_today_s": 0.0,
              "since_break_s": 7200.0 - BREAK_AFTER_S, "next_break_after_s": 7200.0,
              "break_until": None, "break_due_at": None, "last_active": None,
              "paused": False, "killed": False}
    text, code, store, _ = await run_scenario(world, "break", 12690, [GOOD_TREE, DRY_TREE],
                                              ["--trips", "2", "--logs-per-trip", "100", "--human", "off"],
                                              budget=budget)
    eps = store.episodes("lumber")
    check("one trip, 'break due: banked', exit 0 (for ctl break), episode row marked break_due",
          code == 0 and "break due: banked" in text and "loop complete" not in text
          and len(eps) == 1 and eps[0].get("break_due") is True
          and set(eps[0]["phases_s"]) == {"harvest", "convert", "to_bank", "store"},
          f"exit {code}, {len(eps)} episodes")
    check("the harvest stopped early (the good tree still had wood)",
          "break due: stopping the harvest" in text and world.good_left > 0, str(world.good_left))
    check("carried and new logs became boards in the bank box; nothing left in the pack",
          world.bank_stack is not None and world.bank_stack[1] == INITIAL_LOGS + world.harvested
          and world.logs == 0 and not world.pack_boards and world.bank_opens == 1,
          f"bank {world.bank_stack}, harvested {world.harvested}")
    check("no threat juncture", not [j for j in store.junctures() if j["kind"] == "threat"])
    store.close()


async def library():
    """docs/research/WORLD_LOCATIONS.md: a spot reached by a library rune and left by our
    own runebook. Each trip: walk to the library tome, recall to rune 286 with one of its
    charges, wait out the travel lockout, chop (a player chops nearby), convert, recall home
    with the runebook's default rune (trip 1's first cast is disturbed), bank; trip 2
    walks from the bank back to the library. The travel legs and supplies are recorded."""
    print("\n== library: recall out from a public tome, recall home with our runebook, bank; twice ==")
    world = World("library")
    spot = {"access": {"method": "witcher", "rune": "286", "library": "cambria"}, "home": {"method": "recall"},
            "area": {"center": [LIB_TREE["x"], LIB_TREE["y"]], "radius": 10}, "pvp": True}
    text, code, store, _ = await run_scenario(world, "library", 12700, [LIB_TREE],
                                              ["--trips", "2", "--logs-per-trip", "100", "--human", "off",
                                               "--regrow-min", "0.05"], spot_extra=spot)
    eps = store.episodes("lumber")
    trav = [e for e in store.job_events("lumber") if e["kind"] == "travel"]
    check("two trips banked, exit 0", code == 0 and "loop complete: 2 trip(s)" in text
          and [e.get("outcome") for e in eps] == ["banked", "banked"], f"exit {code}, {[e.get('outcome') for e in eps]}")
    check("each trip recalled out from the tome's row for rune 286 (gem 110), standing within its 2 tiles",
          world.recalls_out == [RUNE_POS, RUNE_POS] and world.tome_far == 0, f"{world.recalls_out} far {world.tome_far}")
    check("each trip recalled home with the runebook's default rune (a charge), then banked",
          world.recalls_home == [HOME_RUNE_POS, HOME_RUNE_POS] and world.bank_opens == 2,
          f"{world.recalls_home} opens {world.bank_opens}")
    check("everything harvested ended in the bank box", world.harvested > 0 and world.bank_stack is not None
          and world.bank_stack[1] == world.harvested and world.logs == 0, f"{world.bank_stack} {world.harvested}")
    check("the travels are job events (out then home, twice, all landed) and the walk out includes the recall",
          [e["data"]["leg"] for e in trav] == ["out", "home", "out", "home"] and all(e["data"]["ok"] for e in trav)
          and all(e["walk_out_s"] and e["walk_out_s"] > 2 for e in eps),
          f"{[(e['data'].get('to'), e['data'].get('ok')) for e in trav]} {[e.get('walk_out_s') for e in eps]}")
    out = [e["data"] for e in trav if e["data"]["leg"] == "out"]
    home = [e["data"] for e in trav if e["data"]["leg"] == "home"]
    check("each travel event names its trip, spot, book and Witcher rune (not the tome's row) and what it cost",
          [(d["trip"], d["spot"], d["book"], d.get("witcher_rune")) for d in out]
          == [(1, "sim", f"0x{TOME:08X}", "286"), (2, "sim", f"0x{TOME:08X}", "286")]
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
        and e["supplies"] == {"library_charges": 1, "own_charges": 1, "recall_casts": 0, "reagents_used": {}}
        and e["lockout_s"] >= LOCKOUT_S and e["players_seen"] >= 1 and "skill_end" in e and "weight_end" in e
        for e in eps)
    check("the trip rows carry the travel legs and their time, the lockout waited, the supplies (a library "
          "charge, an own charge; the disturbed cast spent none), the player seen and the end snapshot",
          rows_ok and world.lockouts == 2 and eps[0]["travel"][1]["tries"][0][1] == "disturbed",
          str([{k: e.get(k) for k in ("travel", "travel_s", "supplies", "lockout_s", "players_seen", "skill_end")}
               for e in eps])[:900])
    store.close()


async def library_chased():
    """A creature hunts us down at a library-rune spot (live 2026-10-03, witcher_291: the
    runner converted logs for 12 s under attack, 85 -> 40 hits, then exited in the field).
    Now: escape on foot, it keeps coming -> recall home with our own book at once, no
    conversion, an urgent threat juncture (not pk_escape), exit 1 at home."""
    print("\n== library, chased: a creature keeps coming at a far spot: recall home first, no conversion ==")
    world = World("library")
    world.chaser = True
    spot = {"access": {"method": "witcher", "rune": "286", "library": "cambria"}, "home": {"method": "recall"},
            "area": {"center": [LIB_TREE["x"], LIB_TREE["y"]], "radius": 10}, "pvp": True}
    text, code, store, _ = await run_scenario(world, "library_chased", 12720, [LIB_TREE],
                                              ["--trips", "1", "--logs-per-trip", "100", "--human", "off",
                                               "--regrow-min", "0.05"], spot_extra=spot)
    js = [j for j in store.junctures() if j["source"] == "lumber" and j["severity"] == "urgent"]
    check("it kept coming: recalled home with our runebook at once (the escape recall), exit 1",
          code == 1 and "kept coming after the escape" in text and world.recalls_home
          and world.recalls_home[-1] == HOME_RUNE_POS and "escaped by recall" in text,
          f"exit {code}, home {world.recalls_home}\n{text[-600:]}")
    check("no 10 s log conversion before leaving, no bank trip",
          "converting the carried logs before stopping" not in text and not world.pack_boards
          and world.bank_opens == 0, f"boards {world.pack_boards} opens {world.bank_opens}")
    check("an urgent threat juncture says it recalled away (a creature: not a pk_escape)",
          any(j["kind"] == "threat" and "Recalled away" in j["summary"] for j in js)
          and not any(j["kind"] == "pk_escape" for j in js), str([(j["kind"], j["summary"]) for j in js]))
    store.close()


def unit_hatchet():
    """loop_lumber hatchet(): worn first, then the shallowest in the pack; never the bank box."""
    print("\n== hatchet(): worn, else the shallowest in the backpack's bags ==")
    import loop_lumber
    from agent_link import Abort
    inner = 0x44ADC0DF
    base = {BACKPACK: {"graphic": 0x0E75, "layer": 0x15, "container": SELF},
            BAG: {"graphic": 0x0E76, "container": BACKPACK},
            inner: {"graphic": 0x0E76, "container": BAG},
            BANKBOX: {"graphic": 0x0E7C, "layer": 0x1D, "container": SELF}}
    hatchets = {"worn": (0x4001, SELF), "bag": (0x4002, BAG), "inner": (0x4003, inner), "bank": (0x4004, BANKBOX)}
    loop = loop_lumber.LumberLoop.__new__(loop_lumber.LumberLoop)

    def pick(*which):
        items = {**base, **{hatchets[w][0]: {"graphic": 0x0F43, "container": hatchets[w][1]} for w in which}}
        st = {"movement": {"self_serial": SELF},
              "world": {"items": {f"0x{s:08X}": dict(it, container=f"0x{it['container']:08X}")
                                  for s, it in items.items()}}}
        try:
            return loop.hatchet(st)
        except Abort:
            return None
    check("worn beats any in the pack", pick("inner", "bag", "worn", "bank") == hatchets["worn"][0])
    check("the shallowest bag wins", pick("inner", "bag", "bank") == hatchets["bag"][0])
    check("found at any depth", pick("inner", "bank") == hatchets["inner"][0])
    check("one in the bank box doesn't count", pick("bank") is None)



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


if __name__ == "__main__":
    asyncio.run(main())
    asyncio.run(skirmish())
    asyncio.run(break_due())
    asyncio.run(library())
    asyncio.run(library_chased())
    unit_hatchet()
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILURES: {FAILURES}"))
    sys.exit(0 if not FAILURES else 1)

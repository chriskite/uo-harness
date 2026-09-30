"""Offline end-to-end test of the lumber loop runner (harness/loop_lumber.py).

A simulated Shelter server behind the real proxy, with the packet shapes and
texts of the demonstration capture (logs/session_20260929_204225):

- hatchet dclick → cliloc 1010018 + location cursor; tree target → a decoy
  "Captcha" gump (no buttons) on every attempt, the real captcha (gump id 1,
  entry 2, Guide button 1 + a submit button whose id isn't the demo's) on the first one, then fail/success results; a second
  tree answers "not enough wood" (depleted)
- log stack target → "You shape the logs into boards." (1:1)
- "room" near the innkeeper → rental-room gump 0x8EAEFBDB; button 4 teleports
  into the room (secure container + door) / out again (door gump)
- lift + drop into the container, merging stacks like RunUO
- a closed door on the walk back out that opens on the stock open-door request
- a 3 s post-teleport harvest lockout (the real one is 60 s)

The "human" answers the captcha through the client connection. Two trips run.

Run: python test_loop_lumber.py   (~1-2 min; private ports; safe while the live proxy runs)
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = r"C:/Users/chris/uo-harness"
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
BOX, DOOR_ITEM, INNKEEPER = 0x44ADB583, 0x45757DCB, 0x000001E5
START = (100, 200)
GOOD_TREE = {"x": 111, "y": 200, "z": 0, "graphic": "0x0CE0", "stand": [110, 200]}
DRY_TREE = {"x": 105, "y": 194, "z": 0, "graphic": "0x0CE0", "stand": [105, 195]}
INN_POS = (120, 207)                                     # where the innkeeper actually stands
INN_KNOWN = (106, 207)      # knowledge from an older demo: 14 tiles off (NPCs move; vendor range ≤ 12)
ROOM_IN, BOX_POS, DOOR_POS = (39, 65), (39, 66), (39, 69)
EXIT_TO = (125, 200)
DOOR = (122, 200)                                        # a closed town door
WALLS = {(122, y) for y in range(190, 211)} - {DOOR}
MENU_ID = 0x8EAEFBDB
LOCK_S = 25                                              # longer than the walk back out
LOGS_PER_SUCCESS = 3
GOOD_VISIT = 6                                           # attempts before the good tree runs dry
DD = nav.DIR_DELTA
FAILURES = []


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
CAPTCHA_LAYOUT = ("{ resizepic 27 25 11571 391 278 }{ button 21 19 2094 2095 1 0 1 }"
                  "{ tilepic 84 150 572 }{ textentrylimited 163 251 40 20 2655 2 2 3 }"
                  f"{{ button 222 248 247 249 1 0 {CAPTCHA_SUBMIT} }}")
DECOY_LAYOUT = ("{ nomove }{ noclose }{ nodispose }{ noresize }{ page 0 }{ page 1 }"
                "{ croppedtext -324 -203 1 1 0 0 }{ croppedtext -393 -158 1 1 0 1 }")


class World:
    def __init__(self):
        self.pos = list(START)
        self.facing = 0
        self.writer = None
        self.c2s = []
        self.cid = 0x58B00
        self.cursor_for = None
        self.gump_serial = 0x245000
        self.decoys = set()
        self.decoy_replies = 0
        self.captcha_shown = 0
        self.captcha_answers = 0
        self.captcha_open = None
        self.pending_attempt = None
        self.good_n = 0
        self.logs_serial, self.logs = None, 0
        self.board_serial = 0x45000100
        self.pack_boards = {}             # serial -> amount
        self.box_stack = None             # (serial, amount)
        self.lifted = None
        self.door_open = False
        self.open_door_reqs = 0
        self.in_room = False
        self.t_exit = None
        self.lockout_msgs = 0
        self.after_exit = []              # seconds from each room exit to the next harvest target
        self.awaiting_first = False
        self.menu_gumps = {}              # gump serial -> "inn" | "door"
        self.rooms_entered = 0
        self.too_far = 0
        self.rooms_left = 0
        self.harvested = 0
        self.dry_attempts = 0
        self.good_left = GOOD_VISIT
        self.doors_opened = 0

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

    def teleport(self, x, y):
        self.pos = [x, y]
        self.send(self_at(x, y))

    # ---- harvest ----
    def harvest(self, x, y):
        now = time.monotonic()
        if self.awaiting_first:
            self.awaiting_first = False
            self.after_exit.append(now - self.t_exit)
        self.decoys.add(self.next_gump())
        self.send(gump(self.gump_serial, 0x50000000 + self.gump_serial, DECOY_LAYOUT,
                       ["Captcha", "Guide", "Type the Value", "Click when complete"]))
        if self.t_exit is not None and now - self.t_exit < LOCK_S:
            self.lockout_msgs += 1
            left = int(LOCK_S - (now - self.t_exit)) + 1
            self.send(sys_text(f"You have recently traveled and must wait {left} seconds before you may begin harvesting."))
            return
        if (x, y) == (DRY_TREE["x"], DRY_TREE["y"]):
            self.dry_attempts += 1
            self.later(0.3, [cliloc(500493)])
            return
        if self.good_left == 0:                          # out of wood; regrown for the next visit
            self.good_left = GOOD_VISIT
            self.later(0.3, [cliloc(500493)])
            return
        self.good_left -= 1
        if self.captcha_shown == 0:
            self.captcha_shown += 1
            self.captcha_open = self.next_gump()
            self.send(gump(self.captcha_open, 0x00000001, CAPTCHA_LAYOUT, ["Guide", "Captcha", "", "Type The Value"]))
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
        pid = p[0]
        if pid == 0x02:
            seq, d = p[2], p[1] & 7
            if d != self.facing:
                self.facing = d
                self.send(bytes([0x22, seq, 0x01]))
                return
            nx, ny = self.pos[0] + DD[d][0], self.pos[1] + DD[d][1]
            blocked = (nx, ny) in WALLS or ((nx, ny) == DOOR and not self.door_open) \
                or (nx, ny) == INN_POS
            if blocked:
                self.send(b"\x21" + bytes([seq]) + u32(self.pos[0]) + u32(self.pos[1])
                          + bytes([self.facing]) + u32(0))
                return
            self.pos = [nx, ny]
            self.send(bytes([0x22, seq, 0x01]))
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
            elif serial == DOOR_ITEM and self.in_room and self.cheb(DOOR_POS) <= 2:
                g = self.next_gump()
                self.menu_gumps[g] = "door"
                self.send(gump(g, MENU_ID, buttons([1, 3, 7, 4, 5]), ["Guide"]))
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
            elif f["serial"] == self.captcha_open and f["button_id"] == CAPTCHA_SUBMIT:
                self.captcha_answers += 1
                self.captcha_open = None
                self.send(sys_text("Captcha successful."))
                if self.pending_attempt:
                    self.pending_attempt = None
                    self.result()
            elif self.menu_gumps.get(f["serial"]) == "inn" and f["button_id"] == 4 and self.cheb(INN_POS) > 12:
                self.too_far += 1                     # live 2026-09-29: 13 tiles → this, 11 tiles worked
                self.send(sys_text("That vendor is too far away from you."))
            elif self.menu_gumps.get(f["serial"]) == "inn" and f["button_id"] == 4:
                self.in_room = True
                self.rooms_entered += 1
                self.teleport(*ROOM_IN)
                self.send(sys_text("You enter the rental room."))
                self.send(ground_item(BOX, 0x0E76, *BOX_POS, 2))
                self.send(ground_item(DOOR_ITEM, 0x06E5, *DOOR_POS, 1))
                if self.box_stack:
                    self.send(contained(self.box_stack[0], 0x1BD7, self.box_stack[1], BOX))
            elif self.menu_gumps.get(f["serial"]) == "door" and f["button_id"] == 4:
                self.in_room = False
                self.rooms_left += 1
                self.door_open = False                   # doors swing shut again
                self.teleport(*EXIT_TO)
                self.send(sys_text("You exit the rental room."))
                self.t_exit = time.monotonic()
                self.awaiting_first = True
        elif pid == 0xAD:
            f = parse_packet("c2s", p)
            if f.get("text") == "room" and not self.in_room and self.cheb(INN_POS) <= 12:
                g = self.next_gump()
                self.menu_gumps[g] = "inn"
                self.send(gump(g, MENU_ID, buttons([1, 3, 7, 4, 5, 6]), ["Guide"]))
        elif pid == 0x07:
            self.lifted = parse_packet("c2s", p)
        elif pid == 0x08:
            f = parse_packet("c2s", p)
            lf, self.lifted = self.lifted, None
            if lf is None or lf["serial"] != f["serial"] or f["container"] != BOX \
                    or not self.in_room or self.cheb(BOX_POS) > 2:
                return
            amount = self.pack_boards.pop(f["serial"], 0)
            if self.box_stack is None:
                self.box_stack = (f["serial"], amount)
                self.send(contained(f["serial"], 0x1BD7, amount, BOX))
            else:                                        # RunUO stacks with the existing pile
                self.box_stack = (self.box_stack[0], self.box_stack[1] + amount)
                self.send(delete(f["serial"]))
                self.send(contained(self.box_stack[0], 0x1BD7, self.box_stack[1], BOX))

    async def handle(self, reader, writer):
        await reader.readexactly(5)
        self.writer = writer
        writer.write(PRELUDE)
        self.send(login_pkt())
        for token in (5, 6, 7, 8):
            self.send(seed_pkt(token))
        self.send(equip(BACKPACK, 0x0E75, 0x15))
        self.send(equip(HATCHET, 0x0F44, 0x02))
        self.send(mobile_pkt(INNKEEPER, *INN_POS))
        self.send(ground_item(0x40005CE3, 0x06AD, *DOOR, 0))     # the town door (demo art)
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


def knowledge(path):
    with open(f"{ROOT}/harness/data/loops/lumber.json", encoding="utf-8") as f:
        k = json.load(f)
    k["harvest"]["trees"] = [GOOD_TREE, DRY_TREE]
    k["npcs"]["innkeeper"].update(serial=f"0x{INNKEEPER:08X}", pos=[*INN_KNOWN, 0])
    k["room"].update(inside_pos=[*ROOM_IN, 1],
                     door={"serial": f"0x{DOOR_ITEM:08X}", "graphic": "0x06E5", "pos": [*DOOR_POS, 1]},
                     secure_container={"serial": f"0x{BOX:08X}", "graphic": "0x0E76", "pos": [*BOX_POS, 2]})
    k["travel_lockout"]["seconds"] = LOCK_S
    with open(path, "w", encoding="utf-8") as f:
        json.dump(k, f)


async def main():
    os.makedirs(LOGDIR, exist_ok=True)
    for f in os.listdir(LOGDIR):
        os.remove(os.path.join(LOGDIR, f))
    tmp = tempfile.mkdtemp()
    paths = {"lumber": os.path.join(tmp, "lumber.json"), "db": os.path.join(tmp, "harness.db")}
    knowledge(paths["lumber"])

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
            """Answers the real captcha through the client, like the user would."""
            while True:
                await asyncio.sleep(0.2)
                if world.captcha_open is not None:
                    await asyncio.sleep(1.0)
                    pkt = actions.gump_response(world.captcha_open, 0x00000001, CAPTCHA_SUBMIT,
                                                text_entries=[(2, "326")])
                    writer.write(bytes(b ^ C2S_KEY for b in pkt))
                    await writer.drain()
                    await asyncio.sleep(0.5)

        drainer = asyncio.create_task(drain_client())
        solver = asyncio.create_task(human())
        await asyncio.sleep(0.5)
        runner = await asyncio.create_subprocess_exec(
            PY, f"{ROOT}/harness/loop_lumber.py", "--trips", "2", "--logs-per-trip", "100",
            "--regrow-min", "0.05",
            "--control-port", str(CONTROL_PORT), "--state-port", str(STATE_PORT),
            "--loop", paths["lumber"], "--memory", paths["db"],
            "--human", "normal", "--seed", "11", "--human-fast", "0.25", "--timeout", "300", "--quiet",
            "--no-map",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(runner.communicate(), timeout=360)
        text = out.decode(errors="replace")
        print("---- runner output ----\n" + text + "-----------------------")
        bad_intents = [state_req({"op": "intent", "intent": b})
                       for b in ({"text": ""}, {"text": 5}, ["not", "a", "dict"], {"text": "x" * 201})]
        solver.cancel()
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
        check("one real captcha, answered once, by the client (human) only",
              world.captcha_shown == 1 and world.captcha_answers == 1 and len(b1_client) == 1)
        check("the runner recognised the real captcha (submit id not the demo's) and handed it off",
              text.count("CAPTCHA: please solve it in the client") == 1
              and sum(r.get("captchas", 0) for r in rows) == 1, str([r.get("captchas") for r in rows]))
        check("decoy gumps were shown and never answered",
              len(world.decoys) >= 4 and world.decoy_replies == 0, f"{len(world.decoys)} decoys")
        check("agent gump replies = rental-room menu only (2 enters + 2 exits)",
              len(b1_agent) == 4 and world.rooms_entered == 2 and world.rooms_left == 2, str(len(b1_agent)))
        check("walked to the innkeeper's live position: never 'too far' (knowledge pos is 14 tiles off)",
              world.too_far == 0, str(world.too_far))
        check("every harvested log ended in the secure container as boards",
              world.box_stack is not None and world.box_stack[1] == world.harvested == 2 * 3 * LOGS_PER_SUCCESS,
              f"box {world.box_stack}, harvested {world.harvested}")
        check("nothing left in the backpack", world.logs == 0 and not world.pack_boards)
        check("each trip tried the dry tree once, then moved on",
              world.dry_attempts == 2, str(world.dry_attempts))
        check("harvest memory (store): dry tree depleted, good tree counted",
              dry_node.get("depleted_at") is not None and good_node.get("successes", 0) >= 4
              and good_node.get("yield") == world.harvested, f"{dry_node} {good_node}")
        check("walk memory (store): the proxy recorded the agent's walks, incl. the room's facet-less tiles",
              len(walked.edges) >= 20 and (39, 65) in walked.tiles, str(walked.stats()))
        check("open-door requests only when blocked next to a door (never at plain walls)",
              world.open_door_reqs == world.doors_opened, f"{world.open_door_reqs} requests, "
              f"{world.doors_opened} opened")
        check("after leaving the room the agent waited out the harvest lockout",
              len(world.after_exit) == 1 and world.after_exit[0] >= LOCK_S and world.lockout_msgs == 0
              and "travel lockout: waiting" in text, f"{world.after_exit}")
        check("only speech: 'room', stock-encoded",
              speech and all(p == actions.say_unicode("room") for p in speech), str(len(speech)))
        check("two episode rows with logs and stored boards",
              len(rows) == 2 and all(r.get("logs", 0) >= 6 and r.get("stored", 0) >= 6 for r in rows), str(rows))
        check("every C2S packet came from the client or the agent (none from the proxy)",
              srcs <= {"client", "agent"}, str(srcs))
        intents = [e["intent"] for e in log if e.get("ev") == "agent_intent"]
        kinds = [i["kind"] for i in intents if i]
        check("malformed intents rejected by the proxy (and not recorded)",
              all(not r["ok"] for r in bad_intents) and all(i and i.get("text") for i in intents),
              str(bad_intents))
        phase = ["to_tree", "chop", "convert", "to_inn", "enter_room", "to_box", "store", "exit_room",
                 "trip_done"]
        for n in (1, 2):
            seq = [i["kind"] for i in intents if i and i.get("trip") == n]
            check(f"trip {n}: intents follow the loop's phases in order",
                  is_subsequence(phase, seq), str(dedupe(seq)))
        t2 = [i["kind"] for i in intents if i and i.get("trip") == 2]
        check("trip 2 reports the post-exit lockout wait before chopping",
              "lockout" in t2 and t2.index("lockout") < t2.index("chop"), str(dedupe(t2)))
        cap = [k for k, i in enumerate(intents) if i and i["kind"] == "captcha"]
        check("captcha intent shown once, then the previous intent restored",
              len(cap) == 1 and 0 < cap[0] < len(intents) - 1
              and intents[cap[0] + 1]["text"] == intents[cap[0] - 1]["text"], str(cap))
        check("heading/chopping intents carry the tree as target",
              all(i.get("target") for i in intents if i and i["kind"] in ("to_tree", "chop")))
        check("last intent: finished, lumber loop, 2 trips",
              intents and intents[-1]["kind"] == "done" and intents[-1]["loop"] == "lumber"
              and intents[-1]["trips"] == 2, str(intents[-1:]))
        tag = next(f for f in os.listdir(LOGDIR) if f.endswith(".jsonl"))[len("session_"):-len(".jsonl")]
        drv = viz_feed.ReplayDriver(tag, LOGDIR)
        drv.run_to_end()
        got = drv.query(0).get("intent") or {}
        check("a replay of the capture reproduces the final intent (viz replay)",
              {k: got.get(k) for k in ("kind", "text", "trips")}
              == {k: intents[-1].get(k) for k in ("kind", "text", "trips")}, str(got))
    finally:
        proxy.terminate()
        server.close()


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
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILURES: {FAILURES}"))
    sys.exit(0 if not FAILURES else 1)

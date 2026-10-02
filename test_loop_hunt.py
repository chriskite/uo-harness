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

Every attack/cast/target/loot packet is compared byte-wise with the combat.py /
actions.py builders.

Run: python test_loop_hunt.py   (~20 s; private ports; safe while the live proxy runs)
"""
import asyncio
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
import combat  # noqa: E402
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
A, S, D, E = 0x002C1001, 0x002C1E27, 0x002C3FB9, 0x002C3FE9
POS = {A: (5536, 526), S: (5534, 527), D: (5534, 528), E: (5536, 528)}
HITS = {A: 80, S: 80, D: 200, E: 80}       # D lasts past the potion cooldown after re-entering
CORPSE = {A: 0x4FE00001, S: 0x4FE00003, D: 0x4FE00002}
GOLD = {A: (0x4FE10001, 21), D: (0x4FE10002, 23)}
LIGHTNING, GREATER_HEAL = combat.spell_id("lightning"), combat.spell_id("greater heal")
HEAL = combat.spell_id("heal")
POT_BAG, POTION = 0x44ADA100, 0x44ADA101            # a bag in the pack holding 3 heal potions
GHEAL_MIN = 25                                      # --gheal-min-missing (the sim sends no Magery)
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
    body = u32(SELF) + bytes(4) + u32(BODY) + u32(SPOT[0]) + u32(SPOT[1]) + u32(0) + bytes([0x80])
    return b"\x1b" + body + bytes(42 - len(body))


def seed_pkt(token):
    return bytes.fromhex("bf001d0001") + u32(token) + bytes(20)


def self_at(x, y, d=0):
    return b"\x20" + u32(SELF) + u32(BODY) + b"\x01\x83\xea\x20" + u32(x) + u32(y) + b"\x00\x00" \
        + bytes([d]) + u32(0)


def mob_pkt(serial, x, y):        # 0x20 for a grey (3) mongbat
    return b"\x20" + u32(serial) + u32(MONGBAT) + b"\x03\x00\x00\x00" + u32(x) + u32(y) + b"\x00\x00\x04" + u32(0)


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


def corpse_flags(corpse, serial, name="a mongbat corpse"):   # Outlands 0xFF sub 0xDEAD
    body = u32(0xDEAD) + u32(corpse) + u32(serial) + b"\x03" + name.encode() + b"\x00"
    return var(0xFF, body)


def cliloc(number, args=b""):
    return var(0xC1, u32(0xFFFFFFFF) + b"\xff\xff\x00" + u16(0x3B2) + u16(3) + u32(number)
               + b"System".ljust(30, b"\x00") + args + b"\x00\x00")


def cheb(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


class World:
    def __init__(self):
        self.pos = list(SPOT)
        self.facing = 0
        self.writer = None
        self.c2s, self.c2s_t = [], []
        self.hits, self.hits_max = 100, 100
        self.mana, self.mana_max = 100, 100
        self.warmode = False
        self.alive = {A: True, S: True, D: False, E: False}
        self.mob_hits = dict(HITS)
        self.mob_pos = dict(POS)
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
        self.send(mob_pkt(serial, x, y))
        self.send(name_pkt(serial, "a mongbat"))
        self.send(hits_pkt(serial, self.mob_hits[serial], HITS[serial]))

    def kill(self, serial):
        """In-view death as live (214649): corpse item, 0xAF, 0x1D, 0xFF sub 0xDEAD."""
        self.alive[serial] = False
        self.swingers.pop(serial, None)
        x, y = self.mob_pos[serial]
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
        self.mob_hits[serial] = max(0, self.mob_hits[serial] - amount)
        self.send(damage(serial, amount))
        self.send(hits_pkt(serial, self.mob_hits[serial], HITS[serial]))
        if self.mob_hits[serial] == 0:
            self.kill(serial)

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

    def came_in(self):
        first = self.reentered is None
        self.reentered = time.time()
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
        if pid == 0x02:
            seq, d = p[2], p[1] & 7
            if d != self.facing:
                self.facing = d
                self.send(bytes([0x22, seq, 0x01]))
                return
            nx, ny = self.pos[0] + DD[d][0], self.pos[1] + DD[d][1]
            if (nx, ny) == EXIT_TILE:        # the exit teleporter denies the step, then moves you
                self.exits.append(time.time())
                self.send(b"\x21" + bytes([seq]) + u32(self.pos[0]) + u32(self.pos[1]) + bytes([self.facing]) + u32(0))
                self.swingers.clear()
                self.teleport(*OUTSIDE)
                return
            if self.inside and any(self.alive[s] and self.mob_pos[s] == (nx, ny) for s in self.alive):
                self.send(b"\x21" + bytes([seq]) + u32(self.pos[0]) + u32(self.pos[1]) + bytes([self.facing]) + u32(0))
                return
            self.pos = [nx, ny]
            self.send(bytes([0x22, seq, 0x01]))
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
            if serial == A and A not in self.swingers and self.alive[A]:
                self.mob_pos[A] = (SPOT[0], SPOT[1] - 1)           # flies to us and hits hard once
                self.send(mob_move(A, *self.mob_pos[A]))
                self.hurt_self(A, 30)
                self.swingers[A] = 3
        elif pid == 0xFF and p[3:7] == u32(4):
            f = parse_packet("c2s", p)
            self.casts.append(f["spell_id"])
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
            elif serial == POT_BAG:
                self.send(open_container(POT_BAG, 0x3D))
            elif serial == POTION:
                self.drink()
            elif serial in self.corpse_items:
                self.send(open_container(serial, 0x09))
                g, n = self.corpse_items[serial]
                self.send(contained(g, combat.GOLD_GRAPHIC, n, serial))
        elif pid == 0x07:
            self.lifted = parse_packet("c2s", p)
        elif pid == 0x08:
            f = parse_packet("c2s", p)
            lf, self.lifted = self.lifted, None
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
                    self.hurt_self(s, dmg)
            if self.inside and n % 3 == 0 and self.warmode and self.engaged and self.alive.get(self.engaged) \
                    and cheb(self.pos, self.mob_pos[self.engaged]) <= 1:
                self.hurt_mob(self.engaged, 10)
            await self.writer.drain()

    async def handle(self, reader, writer):
        await reader.readexactly(5)
        self.writer = writer
        writer.write(PRELUDE)
        self.send(login_pkt())
        for token in (5, 6, 7, 8):
            self.send(seed_pkt(token))
        self.send(self_at(*SPOT))
        self.send(equip(BACKPACK, 0x0E75, 0x15))
        self.send(contained(PACK_GOLD, combat.GOLD_GRAPHIC, self.pack_gold, BACKPACK))
        self.send(contained(POT_BAG, 0x0E76, 1, BACKPACK))
        self.send(contained(POTION, 0x0F0C, self.potions, POT_BAG))
        self.send(hits_pkt(SELF, self.hits, self.hits_max))
        self.send(mana_pkt(self.mana, self.mana_max))
        for s in (A, S):
            self.show_mob(s)
        self.send(swing(S, SELF))                      # S was on us...
        self.alive[S] = False                          # ...and someone else killed it: 0xDEAD only
        self.send(ground_item(CORPSE[S], combat.CORPSE_GRAPHIC, MONGBAT, *POS[S]))
        self.send(corpse_flags(CORPSE[S], S))
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


async def main():
    os.makedirs(LOGDIR, exist_ok=True)
    for f in os.listdir(LOGDIR):
        os.remove(os.path.join(LOGDIR, f))
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "harness.db")
    memory.Memory(db).close()

    world = World()
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
                PY, f"{ROOT}/harness/loop_hunt.py", "--kills", "2", "--timeout", "150",
                "--gheal-min-missing", str(GHEAL_MIN),
                "--control-port", str(CONTROL_PORT), "--state-port", str(STATE_PORT), "--memory", db,
                "--entry", str(ENTRY[0]), str(ENTRY[1]), "0",
                "--human", "normal", "--seed", "7", "--human-fast", "0.2", "--quiet", "--no-map",
                "--triage-url", "",
                stdout=fh, stderr=asyncio.subprocess.STDOUT)
            try:
                await asyncio.wait_for(runner.wait(), timeout=200)
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
        server.close()


if __name__ == "__main__":
    asyncio.run(main())
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILURES: {FAILURES}"))
    sys.exit(0 if not FAILURES else 1)

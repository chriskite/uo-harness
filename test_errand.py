"""Offline end-to-end test of the bank errand (harness/errand_bank.py).

Real processes: proxy subprocess + errand runner subprocess. A fake game server
simulates a small world speaking the real wire formats:
  - TestWorth (0x00094375) logs in at (100, 200) facing N
  - a wall row at y=196, x=98..101 forces a server deny + replan
  - NPC "Dusty" (nearest, not a banker) and "Len" (the banker) at (100, 188)
  - single click (0x09) -> 0x1C label (real capture layout)
  - walk -> 22 confirm, or 21 deny into walls/occupied tiles; a new direction only turns
  - speech with keyword 2 ("bank") within 12 tiles of Len -> the real bank-box
    reply from session 20260929_161433 (0x2E layer 0x1D, 0x24 gump 0x4A, 0x3C)
Asserts: the errand completes back at the start, the bank opened, the runner
looked at NPCs like the stock client (09 + 34), learned the wall, sent the
client-identical "bank" packet, and every C2S packet came from the client or agent.
"""
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
sys.path.insert(0, f"{ROOT}/harness")

import actions  # noqa: E402
import memory  # noqa: E402
import nav  # noqa: E402
from uo.packets import packet_length, C2S_OVERRIDES  # noqa: E402
from uo.s2c import encode_packet  # noqa: E402
from world.parsers import parse_packet  # noqa: E402

def _free_ports(n):
    socks = [socket.socket() for _ in range(n)]
    for s in socks:
        s.bind(("127.0.0.1", 0))
    ports = [s.getsockname()[1] for s in socks]
    for s in socks:
        s.close()
    return ports


PROXY_PORT, UPSTREAM_PORT, CONTROL_PORT, STATE_PORT = _free_ports(4)
LOGDIR = tempfile.mkdtemp(prefix="logs_test_errand_")
C2S_KEY, S2C_KEY = 0x0F, 0x5A
PRELUDE = bytes([0xFF, 0x00, 0x0D] + [0] * 7 + [0x0C, S2C_KEY, C2S_KEY])
SELF = 0x00094375
START = (100, 200)
LEN, DUSTY = 0x000001EA, 0x000001E2
NPCS = {LEN: ("Len", (100, 188), "Len the banker"), DUSTY: ("Dusty", (103, 203), "Dusty")}
WALLS = {(x, 196) for x in range(98, 102)}
DD = nav.DIR_DELTA
# real bank-box reply to the stock client's "bank" (session 20260929_161433)
BANK_REPLY = [bytes.fromhex(h) for h in (
    "2e44d78ca800000e7c000002001d000943750000", "2444d78ca80000004a007d", "3c00050000")]

FAILURES = []


def check(name, cond, extra=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAILURES.append(name)


def u32(v):
    return (v & 0xFFFFFFFF).to_bytes(4, "big")


def login_pkt():
    body = u32(SELF) + bytes(4) + u32(0x190) + u32(START[0]) + u32(START[1]) + u32(0) + bytes([0x80])
    return b"\x1b" + body + bytes(42 - len(body))


def mobile_pkt(serial, x, y):
    # 0x20 V10 layout (real: 20 000001df 00000191 07 03f1 02 000007b2 00000a2e 0000 03 00000005)
    return b"\x20" + u32(serial) + u32(0x190) + b"\x07\x03\xf1\x02" + u32(x) + u32(y) + b"\x00\x00\x03" + u32(0)


def name_pkt(serial, name):
    return b"\x98\x00\x25" + u32(serial) + name.encode().ljust(30, b"\x00")


def label_pkt(serial, name, text):
    # real layout: 1c <len> serial graphic u16 type=6 hue font name[30] text\0
    body = u32(serial) + b"\x01\x90\x06\x00\x35\x00\x03" + name.encode().ljust(30, b"\x00") + text.encode() + b"\x00"
    return b"\x1c" + (len(body) + 3).to_bytes(2, "big") + body


def seed_pkt(token):
    return bytes.fromhex("bf001d0001") + u32(token) + bytes(20)


class World:
    def __init__(self):
        self.pos = list(START)
        self.facing = 0
        self.writer = None
        self.c2s = []           # plaintext C2S packets received
        self.denies = 0
        self.bank_opened = 0

    def send(self, pkt):
        self.writer.write(encode_packet(pkt, S2C_KEY))

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
            occupied = {pos for _, pos, _ in NPCS.values()}
            if (nx, ny) in WALLS or (nx, ny) in occupied:
                self.denies += 1
                self.send(b"\x21" + bytes([seq]) + u32(self.pos[0]) + u32(self.pos[1])
                          + bytes([self.facing]) + u32(0))
                return
            self.pos = [nx, ny]
            self.send(bytes([0x22, seq, 0x01]))
        elif pid == 0x09:
            serial = int.from_bytes(p[1:5], "big")
            if serial in NPCS:
                name, _, text = NPCS[serial]
                self.send(label_pkt(serial, name, text))
        elif pid == 0xAD:
            f = parse_packet("c2s", p)
            lx, ly = NPCS[LEN][1]
            if 2 in f.get("keywords", []) and max(abs(self.pos[0] - lx), abs(self.pos[1] - ly)) <= 12:
                self.bank_opened += 1
                for r in BANK_REPLY:
                    self.send(r)

    async def handle(self, reader, writer):
        await reader.readexactly(5)
        self.writer = writer
        writer.write(PRELUDE)
        self.send(login_pkt())
        for token in (5, 6, 7, 8):
            self.send(seed_pkt(token))
        for serial, (name, (x, y), _) in NPCS.items():
            self.send(mobile_pkt(serial, x, y))
            self.send(name_pkt(serial, name))
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


async def connect_retry(port, timeout=30.0):
    """Connect once the proxy subprocess listens (replaces a fixed startup sleep)."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while True:
        try:
            return await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            if loop.time() > end:
                raise
            await asyncio.sleep(0.05)


async def main():
    os.makedirs(LOGDIR, exist_ok=True)
    for f in os.listdir(LOGDIR):
        os.remove(os.path.join(LOGDIR, f))
    mem_path = os.path.join(tempfile.mkdtemp(), "harness.db")

    world = World()
    server = await asyncio.start_server(world.handle, "127.0.0.1", UPSTREAM_PORT)
    proxy = subprocess.Popen(
        [PY, f"{ROOT}/harness/proxy.py", "--listen-port", str(PROXY_PORT),
         "--upstream-host", "127.0.0.1", "--upstream-port", str(UPSTREAM_PORT),
         "--control-port", str(CONTROL_PORT), "--state-port", str(STATE_PORT), "--logdir", LOGDIR,
         "--memory-db", mem_path],
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        reader, writer = await connect_retry(PROXY_PORT)
        writer.write(bytes.fromhex("ef0000000c"))
        await writer.drain()

        async def drain_client():
            while await reader.read(65536):
                pass

        drainer = asyncio.create_task(drain_client())
        await asyncio.sleep(0.5)
        runner = await asyncio.create_subprocess_exec(
            PY, f"{ROOT}/harness/errand_bank.py", "--control-port", str(CONTROL_PORT),
            "--state-port", str(STATE_PORT), "--memory", mem_path, "--human", "off", "--no-map",
            "--timeout", "90", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(runner.communicate(), timeout=120)
        text = out.decode(errors="replace")
        print("---- runner output ----\n" + text + "-----------------------")
        writer.close()
        drainer.cancel()

        await asyncio.sleep(1.0)                        # proxy memory writer: batched commits
        mem = memory.Memory(mem_path).walk_memory(0)
        clicks = [p for p in world.c2s if p[0] == 0x09]
        speech = [p for p in world.c2s if p[0] == 0xAD]
        log = [json.loads(l) for f in os.listdir(LOGDIR) if f.endswith(".jsonl")
               for l in open(os.path.join(LOGDIR, f), encoding="utf-8")]
        srcs = {e.get("src") for e in log if e.get("dir") == "c2s"}

        check("runner exited 0", runner.returncode == 0, str(runner.returncode))
        check("errand reported complete", "errand complete" in text)
        check("server ended with the player back at the start", tuple(world.pos) == START, str(world.pos))
        check("bank opened exactly once", world.bank_opened == 1, str(world.bank_opened))
        check("bank speech byte-identical to the stock client's",
              speech == [actions.say_unicode("bank")], str([p.hex() for p in speech]))
        check("looked at nearest NPC first, then found the banker",
              [int.from_bytes(p[1:5], "big") for p in clicks] == [DUSTY, LEN],
              str([p.hex() for p in clicks]))
        check("each click followed by the stock 0x34 status request",
              all(any(q == actions.status_request(int.from_bytes(c[1:5], "big")) for q in world.c2s)
                  for c in clicks))
        check("server denied at least one move into the wall", world.denies >= 1, str(world.denies))
        check("wall learned by the proxy into the memory store (blocked moves)",
              any(nav.step(a, d) in WALLS for a, d in mem.blocked), str(sorted(mem.blocked)))
        check("walked moves learned into the memory store", len(mem.edges) >= 4, str(len(mem.edges)))
        check("every C2S packet came from the client or the agent (none from the proxy)",
              srcs <= {"client", "agent"}, str(srcs))
    finally:
        proxy.terminate()
        server.close()


if __name__ == "__main__":
    asyncio.run(main())
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILURES: {FAILURES}"))
    if FAILURES:
        print(f"logs kept: {LOGDIR}")
    else:
        shutil.rmtree(LOGDIR, ignore_errors=True)
    sys.exit(0 if not FAILURES else 1)

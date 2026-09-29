"""End-to-end test of the proxy MoveAuthority: client and injected walks from
both senders must arrive upstream on ONE monotonic seq ladder, and the first
walk of each movement cycle (login / after client resync) must carry the cycle
token (8 / 1) — stamped by the proxy when the sender left key 0.
"""
import asyncio
import os
import socket
import subprocess
import sys
import time

ROOT = r"C:/Users/chris/uo-harness"
PY = r"C:/Users/chris/AppData/Local/Programs/Python/Python313/python.exe"
PROXY_PORT = 12593
UPSTREAM_PORT = 12594
LOGDIR = f"{ROOT}/logs_test"

KEY = 0x0F
PRELUDE = bytes.fromhex("ff000d000000000000000c3012a3b4c5d6e7")  # 19B, key at [12]=0x12? no —
# build a valid 19-byte prelude with key=KEY at byte 12
PRELUDE = bytes([0xFF, 0x00, 0x0D] + [0] * 7 + [0x0C, 0x30, KEY, 0xA3, 0xB4, 0xC5, 0xD6, 0xE7, 0xF8])
PREAMBLE = bytes.fromhex("ef0000000c")

GOT = bytearray()


def xor(bs, k):
    return bytes(b ^ k for b in bs)


async def fake_upstream():
    async def handle(reader, writer):
        GOT.extend(await reader.readexactly(5))
        writer.write(PRELUDE)
        await writer.drain()
        while True:
            d = await reader.read(65536)
            if not d:
                break
            GOT.extend(d)
        writer.close()
    server = await asyncio.start_server(handle, "127.0.0.1", UPSTREAM_PORT)
    async with server:
        await server.serve_forever()


def walk(dirb, seq, key=0):
    return xor(bytes([0x02, dirb, seq]) + key.to_bytes(4, "big"), KEY)


async def main():
    os.makedirs(LOGDIR, exist_ok=True)
    for f in os.listdir(LOGDIR):
        os.remove(os.path.join(LOGDIR, f))
    proxy = subprocess.Popen(
        [PY, f"{ROOT}/harness/proxy.py", "--listen-port", str(PROXY_PORT),
         "--upstream-host", "127.0.0.1", "--upstream-port", str(UPSTREAM_PORT),
         "--logdir", LOGDIR],
        stdout=None, stderr=None)
    await asyncio.sleep(1.0)
    upstream = asyncio.create_task(fake_upstream())

    reader, writer = await asyncio.open_connection("127.0.0.1", PROXY_PORT)
    writer.write(PREAMBLE)
    await writer.drain()
    await asyncio.sleep(0.5)  # prelude + key

    # client sends walks with wrong/out-of-order seqs
    for seq in (5, 5, 9, 0):
        writer.write(walk(0x80, seq))
        await writer.drain()
        await asyncio.sleep(0.05)

    # injection through the control channel with a bogus seq
    ctl = socket.create_connection(("127.0.0.1", 25941), timeout=10)
    payload = bytes([0x02, 0x80, 99]) + b"\x00\x00\x00\x00"
    ctl.sendall(len(payload).to_bytes(2, "big") + payload)
    n = int.from_bytes(ctl.recv(2), "big")
    ctl.recv(n)

    # client resync -> seq resets to 0, token 1 armed; agent walks (key 0)
    writer.write(xor(b"\x22\x00\x00", KEY))
    await writer.drain()
    await asyncio.sleep(0.2)
    for bogus in (77, 55):
        payload2 = bytes([0x02, 0x80, bogus]) + b"\x00\x00\x00\x00"
        ctl.sendall(len(payload2).to_bytes(2, "big") + payload2)
        n = int.from_bytes(ctl.recv(2), "big")
        ctl.recv(n)
    ctl.close()

    # client resync, then the client opens the cycle with its own token and
    # sends a stale-key continuation: both keys must pass through unchanged
    writer.write(xor(b"\x22\x00\x00", KEY))
    await writer.drain()
    await asyncio.sleep(0.2)
    writer.write(walk(0x80, 3, key=1))
    await writer.drain()
    await asyncio.sleep(0.05)
    writer.write(walk(0x80, 4, key=8))
    await writer.drain()

    await asyncio.sleep(0.5)
    writer.close()
    await asyncio.sleep(0.5)
    proxy.terminate()
    upstream.cancel()

    # parse upstream-received walks (skip 5-byte preamble)
    walks = []  # (seq, key)
    buf = bytearray(GOT[5:])
    i = 0
    while i + 7 <= len(buf):
        if buf[i] == (0x02 ^ KEY):
            plain = xor(buf[i:i + 7], KEY)
            walks.append((plain[2], int.from_bytes(plain[3:7], "big")))
            i += 7
        else:
            i += 1
    seqs = [s for s, _ in walks]
    keys = [k for _, k in walks]
    ok = True
    def check(name, cond, extra=""):
        nonlocal ok
        print(f"  [{'OK' if cond else 'FAIL'}] {name} {extra}")
        ok = ok and bool(cond)

    print(f"upstream walks (seq, key): {walks}")
    check("9 walks relayed", len(walks) == 9, str(len(walks)))
    check("client seqs normalized 0,1,2,3", seqs[:4] == [0, 1, 2, 3], str(seqs[:4]))
    check("login cycle opener stamped with token 8", keys[0] == 8, str(keys[:1]))
    check("login continuations keep key 0", keys[1:4] == [0, 0, 0], str(keys[1:4]))
    check("injected continuation: seq 99 -> 4, key 0", walks[4:5] == [(4, 0)], str(walks[4:5]))
    check("post-resync injection opens cycle: seq 0, token 1", walks[5:6] == [(0, 1)], str(walks[5:6]))
    check("next injection is a continuation: seq 1, key 0", walks[6:7] == [(1, 0)], str(walks[6:7]))
    check("client's own token passes through: seq 0, key 1", walks[7:8] == [(0, 1)], str(walks[7:8]))
    check("client continuation key untouched: seq 1, key 8", walks[8:9] == [(1, 8)], str(walks[8:9]))
    print("\n" + ("ALL PASS" if ok else "FAILURES PRESENT"))
    sys.exit(0 if ok else 1)


asyncio.run(main())

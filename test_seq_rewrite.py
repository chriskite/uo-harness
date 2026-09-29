"""End-to-end test of the proxy MoveAuthority: client and injected walks from
both senders must arrive upstream on ONE monotonic seq ladder, the first walk
of each movement cycle (login / after client resync) must carry the cycle
token (8 / 1), and the server must never see a token the proxy already spent.
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

    ctl = socket.create_connection(("127.0.0.1", 25941), timeout=10)

    def inject(seq, key=0):
        payload = bytes([0x02, 0x80, seq]) + key.to_bytes(4, "big")
        ctl.sendall(len(payload).to_bytes(2, "big") + payload)
        n = int.from_bytes(ctl.recv(2), "big")
        ctl.recv(n)

    async def client(pkt):
        writer.write(pkt)
        await writer.drain()
        await asyncio.sleep(0.1)

    # agent continuation with a bogus seq
    inject(99)
    # the client presents its own copy of the login token the proxy already
    # stamped into walk #0 -> must be zeroed
    await client(walk(0x80, 4, key=8))

    # resync -> seq 0, token 1 armed; agent opens the cycle, then an agent
    # continuation with a bogus non-zero key
    await client(xor(b"\x22\x00\x00", KEY))
    inject(77)
    inject(55, key=5)
    # client's spent copy of token 1 -> zeroed; a second key 1 is a genuine
    # server push -> passed
    await client(walk(0x80, 0, key=1))
    await client(walk(0x80, 1, key=1))
    ctl.close()

    # resync, the client opens the cycle with its own token (passed), then
    # sends a keyed continuation with nothing spent -> presumed push, passed
    await client(xor(b"\x22\x00\x00", KEY))
    await client(walk(0x80, 3, key=1))
    await client(walk(0x80, 4, key=8))

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
    check("12 walks relayed", len(walks) == 12, str(len(walks)))
    check("client seqs normalized 0,1,2,3", seqs[:4] == [0, 1, 2, 3], str(seqs[:4]))
    check("login cycle opener stamped with token 8", keys[0] == 8, str(keys[:1]))
    check("login continuations keep key 0", keys[1:4] == [0, 0, 0], str(keys[1:4]))
    check("injected continuation: seq 99 -> 4, key 0", walks[4:5] == [(4, 0)], str(walks[4:5]))
    check("client's spent login token zeroed: seq 5, key 0", walks[5:6] == [(5, 0)], str(walks[5:6]))
    check("post-resync injection opens cycle: seq 0, token 1", walks[6:7] == [(0, 1)], str(walks[6:7]))
    check("agent continuation key forced to 0: seq 1", walks[7:8] == [(1, 0)], str(walks[7:8]))
    check("client's spent token 1 zeroed: seq 2, key 0", walks[8:9] == [(2, 0)], str(walks[8:9]))
    check("second client key 1 (server push) passed: seq 3", walks[9:10] == [(3, 1)], str(walks[9:10]))
    check("client's own opener token passes: seq 0, key 1", walks[10:11] == [(0, 1)], str(walks[10:11]))
    check("unspent client continuation key passes: seq 1, key 8", walks[11:12] == [(1, 8)], str(walks[11:12]))
    print("\n" + ("ALL PASS" if ok else "FAILURES PRESENT"))
    sys.exit(0 if ok else 1)


asyncio.run(main())

"""Movement tests for the proxy's MoveAuthority (docs/MOVEMENT.md).

1. Unit (fake clock): seq ladder, token stamping, confirm routing, position
   tracking, rejection rewind, agent pacing/gating, client re-anchor packet.
2. Real capture: position tracked from session 20260929_144541's own 0x1B plus
   its six agent walks equals the server's last self report.
3. End-to-end: proxy subprocess between a fake client and a fake server that
   speaks the real S2C wire format (uo/s2c.py): confirm hiding/rewriting, the
   fabricated 0x21 re-anchor reaching the client, and NO proxy-originated
   traffic reaching the server.
"""
import asyncio
import os
import socket
import subprocess
import sys

ROOT = r"C:/Users/chris/uo-harness"
PY = r"C:/Users/chris/AppData/Local/Programs/Python/Python313/python.exe"
sys.path.insert(0, f"{ROOT}/harness")

import proxy as P  # noqa: E402
from uo.s2c import S2CStream, encode_packet, prelude_keys  # noqa: E402

FAILURES = []


def check(name, cond, extra=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAILURES.append(name)


def walk(dirb, seq, key=0):
    return bytearray([0x02, dirb, seq]) + key.to_bytes(4, "big")


def seed_pkt(token):
    return bytes.fromhex("bf001d0001") + token.to_bytes(4, "big") + bytes(20)


def login_pkt(serial, x, y, z, direction):
    body = (serial.to_bytes(4, "big") + bytes(4) + (0x190).to_bytes(4, "big")
            + x.to_bytes(4, "big") + y.to_bytes(4, "big") + z.to_bytes(4, "big", signed=True)
            + bytes([direction]))
    return b"\x1b" + body + bytes(43 - 1 - len(body))


def deny_fields(pkt):
    return (P._u32(pkt, 2), P._u32(pkt, 6), P._i32(pkt, 11), pkt[10])


# ----------------------------------------------------------------- unit tests

def test_unit():
    print("== MoveAuthority (unit, fake clock) ==")
    ma = P.MoveAuthority()
    ma.on_seed(8)
    ma.on_self_position(100, 200, 5, 0x80)

    p = walk(0x80, 5)
    ma.on_c2s_walk(p, "client", 1.0)
    check("seed token stamped into first walk (key 0)", p[2] == 0 and p[3:7] == b"\x00\x00\x00\x08", p.hex())
    p = walk(0x80, 6, key=8)
    ev = ma.on_c2s_walk(p, "client", 1.2)
    check("client's spent copy of the token zeroed", p[2] == 1 and p[3:7] == bytes(4), p.hex())
    check("  ... and logged", ev and ev[0] == "c2s_stale_token_dropped", str(ev))
    p = walk(0x80, 7, key=3)
    ma.on_c2s_walk(p, "client", 1.4)
    check("other client continuation key passes (server push)", p[3:7] == b"\x00\x00\x00\x03", p.hex())

    check("client confirm rewritten to client's own seq", ma.on_confirm(0) == ("rewrite", 5))
    check("unknown confirm forwarded", ma.on_confirm(0) == ("forward", None))
    ma.on_confirm(1)
    ma.on_confirm(2)
    check("confirmed walks N (facing N) move 3 tiles", ma.pos == [100, 197, 5, 0], str(ma.pos))

    p = walk(0x82, 0, key=9)
    ma.on_c2s_walk(p, "agent", 2.0)
    check("agent continuation: next ladder seq, key forced 0", p[2] == 3 and p[3:7] == bytes(4), p.hex())
    check("agent confirm hidden", ma.on_confirm(3) == ("hide", None))
    check("walk in a new direction only turns", ma.pos == [100, 197, 5, 2], str(ma.pos))
    p = walk(0x82, 0)
    ma.on_c2s_walk(p, "agent", 2.3)
    ma.on_confirm(4)
    check("next walk E moves", ma.pos == [101, 197, 5, 2], str(ma.pos))

    # pacing
    check("run step 0.1 s after the last walk is gated",
          (ma.agent_walk_block(2.4, run=True) or "").startswith("walk gated: pacing"))
    check("run step 0.2 s after the last walk allowed", ma.agent_walk_block(2.5, run=True) is None)
    check("walk step 0.3 s after the last walk is gated",
          (ma.agent_walk_block(2.6, run=False) or "").startswith("walk gated: pacing"))

    # re-anchor: client-only 0x21 with the tracked position, once walking is quiet
    check("no re-anchor while walking is fresh", ma.reanchor_packet(2.5) is None)
    pkt = ma.reanchor_packet(2.3 + P.REANCHOR_IDLE_S + 0.01)
    check("re-anchor is a 15-byte 0x21 at the tracked position",
          pkt is not None and len(pkt) == 15 and pkt[0] == 0x21 and deny_fields(pkt) == (101, 197, 5, 2),
          pkt.hex() if pkt else "None")
    check("re-anchor only once", ma.reanchor_packet(10.0) is None)
    p = walk(0x82, 0)
    ma.on_c2s_walk(p, "agent", 11.0)
    check("no re-anchor while a walk is in flight", ma.reanchor_packet(12.0) is None)
    ma.on_confirm(p[2])

    # client resync: honored (seed) vs ignored
    ma.on_c2s_resync(13.0)
    check("agent gated while a client resync awaits its reply",
          (ma.agent_walk_block(13.5, run=True) or "").startswith("walk gated: awaiting"))
    notes = ma.expire(13.0 + P.RESYNC_REPLY_TIMEOUT_S + 0.1)
    check("ignored resync detected", any(n[0] == "resync_ignored" for n in notes), str(notes))
    p = walk(0x82, 0)
    ma.on_c2s_walk(p, "agent", 15.0)
    check("ladder unchanged by an ignored resync", p[2] == 6, p.hex())
    ma.on_confirm(6)
    ma.on_c2s_resync(16.0)
    ma.on_seed(1)
    p = walk(0x82, 0)
    ma.on_c2s_walk(p, "agent", 17.0)
    check("seed resets ladder; next walk carries the new token",
          p[2] == 0 and p[3:7] == b"\x00\x00\x00\x01", p.hex())

    # rejection: no confirm -> ladder rewinds to the rejected seq, client re-anchored
    before = list(ma.pos)
    p2 = walk(0x82, 0)
    ma.on_c2s_walk(p2, "agent", 17.3)  # seq 1, also sent before the timeout
    notes = ma.expire(17.0 + P.CONFIRM_TIMEOUT_S + 0.1)
    check("rejected walk reported", any(n[0] == "walk_rejected" for n in notes), str(notes))
    p = walk(0x82, 0)
    ma.on_c2s_walk(p, "agent", 19.0)
    check("ladder rewound to the rejected walk's seq", p[2] == 0, p.hex())
    check("rejected walks did not move the tracked position", ma.pos == before, f"{ma.pos} vs {before}")
    ma.expire(19.0 + P.CONFIRM_TIMEOUT_S + 0.1)
    p = walk(0x82, 0)
    ma.on_c2s_walk(p, "agent", 21.0)
    ma.expire(21.0 + P.CONFIRM_TIMEOUT_S + 0.1)
    check("3 rejections in a row stall agent walks",
          (ma.agent_walk_block(30.0, run=True) or "").startswith("walk gated: movement stalled"))
    check("stalled state still re-anchors the client", ma.reanchor_packet(30.0) is not None)
    p = walk(0x82, 0)
    ma.on_c2s_walk(p, "client", 31.0)
    ma.on_confirm(p[2])
    check("a confirmed walk clears the stall", ma.agent_walk_block(32.0, run=True) is None)

    # server deny resets the ladder
    ma.on_deny()
    p = walk(0x82, 0)
    ma.on_c2s_walk(p, "client", 40.0)
    check("server deny resets ladder to 0", p[2] == 0, p.hex())


def test_real_capture():
    print("== position tracking vs real capture (session 20260929_144541) ==")
    raw = open(f"{ROOT}/logs/session_20260929_144541.s2c.raw", "rb").read()
    key, _ = prelude_keys(raw[:13])
    pkts = [p for _, p in S2CStream(key).feed(raw[13:])]
    login = next(p for p in pkts if p[0] == 0x1B)
    ma = P.MoveAuthority()
    ma.self_serial = P._u32(login, 1)
    ma.on_self_position(P._u32(login, 13), P._u32(login, 17), P._i32(login, 21), login[25])
    for i in range(6):  # the session's six agent walks: run W (the first only turns)
        w = walk(0x86, 0)
        ma.on_c2s_walk(w, "agent", float(i))
        ma.on_confirm(w[2])
    last = [p for p in pkts if p[0] == 0x77 and P._u32(p, 1) == ma.self_serial][-1]
    server = [P._u32(last, 5), P._u32(last, 9), P._i32(last, 13), last[17] & 7]
    check("tracked position equals the server's last self 0x77", ma.pos == server,
          f"{[hex(v) for v in ma.pos]} vs {[hex(v) for v in server]}")


# ---------------------------------------------------------------- end-to-end

PROXY_PORT, UPSTREAM_PORT, CONTROL_PORT = 12593, 12594, 12598
LOGDIR = f"{ROOT}/logs_test"
C2S_KEY, S2C_KEY = 0x0F, 0x5A
PRELUDE = bytes([0xFF, 0x00, 0x0D] + [0] * 7 + [0x0C, S2C_KEY, C2S_KEY])
SERIAL = 0x00094375


def xor(bs, k):
    return bytes(b ^ k for b in bs)


class FakeServer:
    def __init__(self):
        self.writer = None
        self.walks = []     # (seq, key) as received upstream
        self.other = []     # any non-walk C2S packet bytes
        self.ready = asyncio.Event()

    async def handle(self, reader, writer):
        await reader.readexactly(5)
        self.writer = writer
        writer.write(PRELUDE + encode_packet(login_pkt(SERIAL, 100, 200, 5, 0x80), S2C_KEY)
                     + encode_packet(seed_pkt(8), S2C_KEY))
        await writer.drain()
        self.ready.set()
        buf = bytearray()
        while True:
            d = await reader.read(65536)
            if not d:
                break
            buf += xor(d, C2S_KEY)
            while len(buf) >= 7 and buf[0] == 0x02:
                self.walks.append((buf[2], int.from_bytes(buf[3:7], "big")))
                del buf[:7]
            if buf and buf[0] != 0x02:
                self.other.append(bytes(buf))
                buf.clear()

    async def send(self, pkt):
        self.writer.write(encode_packet(pkt, S2C_KEY))
        await self.writer.drain()


async def e2e():
    print("== proxy end-to-end ==")
    os.makedirs(LOGDIR, exist_ok=True)
    for f in os.listdir(LOGDIR):
        os.remove(os.path.join(LOGDIR, f))
    proxy = subprocess.Popen(
        [PY, f"{ROOT}/harness/proxy.py", "--listen-port", str(PROXY_PORT),
         "--upstream-host", "127.0.0.1", "--upstream-port", str(UPSTREAM_PORT),
         "--control-port", str(CONTROL_PORT), "--logdir", LOGDIR],
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    srv = FakeServer()
    server = await asyncio.start_server(srv.handle, "127.0.0.1", UPSTREAM_PORT)
    try:
        await asyncio.sleep(1.0)
        reader, writer = await asyncio.open_connection("127.0.0.1", PROXY_PORT)
        writer.write(bytes.fromhex("ef0000000c"))
        await writer.drain()
        await asyncio.wait_for(srv.ready.wait(), 5)
        await asyncio.sleep(0.3)

        client_rx = []  # S2C packets as the client sees them
        dec = S2CStream(S2C_KEY)
        got_prelude = bytearray()

        async def client_read():
            while True:
                d = await reader.read(65536)
                if not d:
                    return
                if len(got_prelude) < 13:
                    need = 13 - len(got_prelude)
                    got_prelude.extend(d[:need])
                    d = d[need:]
                client_rx.extend(p for _, p in dec.feed(d))

        rt = asyncio.create_task(client_read())

        async def client_walk(dirb, seq, key=0):
            writer.write(xor(walk(dirb, seq, key), C2S_KEY))
            await writer.drain()
            await asyncio.sleep(0.25)

        ctl = socket.create_connection(("127.0.0.1", CONTROL_PORT), timeout=10)

        def inject(pkt):
            ctl.sendall(len(pkt).to_bytes(2, "big") + pkt)
            n = int.from_bytes(ctl.recv(2), "big")
            return ctl.recv(n).decode()

        # client walks N twice with its own seqs 5, 9 -> ladder 0 (token 8), 1
        await client_walk(0x80, 5)
        await client_walk(0x80, 9)
        await srv.send(b"\x22\x00\x01")
        await srv.send(b"\x22\x01\x01")
        await asyncio.sleep(0.3)
        # agent: E (turn), E (move); confirms hidden; immediate repeat is paced
        r1 = inject(bytes(walk(0x82, 77)))
        r2 = inject(bytes(walk(0x82, 78)))
        await srv.send(b"\x22\x02\x01")
        await asyncio.sleep(0.25)
        r3 = inject(bytes(walk(0x82, 0)))
        await srv.send(b"\x22\x03\x01")
        await asyncio.sleep(0.2)
        confirms_mid = [p[1] for p in client_rx if p[0] == 0x22]
        denies_mid = [p for p in client_rx if p[0] == 0x21]
        await asyncio.sleep(P.REANCHOR_IDLE_S + 0.5)
        denies = [p for p in client_rx if p[0] == 0x21]
        ctl.close()
        writer.close()
        await asyncio.sleep(0.3)
        rt.cancel()

        check("client walks rewritten upstream: (0,8), (1,0)", srv.walks[:2] == [(0, 8), (1, 0)], str(srv.walks))
        check("client received its confirms under its own seqs 5, 9", confirms_mid == [5, 9], str(confirms_mid))
        check("agent walks accepted as ladder seqs 2, 3", r1 == "OK" and r3 == "OK"
              and srv.walks[2:4] == [(2, 0), (3, 0)], f"{r1} {r3} {srv.walks}")
        check("agent walk right after is paced", r2.startswith("ERR walk gated: pacing"), r2)
        check("agent confirms hidden from the client", 2 not in confirms_mid and 3 not in confirms_mid,
              str(confirms_mid))
        check("no re-anchor while walking is fresh", not denies_mid, str(denies_mid))
        check("client got exactly one fabricated 0x21 at the true position (x101 y198 z5 E)",
              len(denies) == 1 and deny_fields(denies[0]) == (101, 198, 5, 2),
              str([deny_fields(d) for d in denies]))
        check("server received no proxy-originated packets (only walks)", srv.other == [], str(srv.other))
        check("client prelude relayed", bytes(got_prelude) == PRELUDE, got_prelude.hex())
    finally:
        proxy.terminate()
        server.close()


def main():
    test_unit()
    test_real_capture()
    asyncio.run(e2e())
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILURES: {FAILURES}"))
    sys.exit(0 if not FAILURES else 1)


if __name__ == "__main__":
    main()

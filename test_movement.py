"""Movement tests for the proxy's MoveAuthority (docs/MOVEMENT.md).

1. Unit (fake clock): seq ladder, token stamping, confirm routing, resync
   honored vs ignored, agent pacing/gating, desync, re-anchor timing.
2. End-to-end: proxy subprocess between a fake client and a fake server that
   speaks the real S2C wire format (uo/s2c.py): confirm hiding/rewriting on the
   client side, walk rewriting upstream, the automatic re-anchor resync.
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
from uo.s2c import S2CStream, encode_packet  # noqa: E402

FAILURES = []


def check(name, cond, extra=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAILURES.append(name)


def walk(dirb, seq, key=0):
    return bytearray([0x02, dirb, seq]) + key.to_bytes(4, "big")


def seed_pkt(token):
    return bytes.fromhex("bf001d0001") + token.to_bytes(4, "big") + bytes(20)


# ----------------------------------------------------------------- unit tests

def test_unit():
    print("== MoveAuthority (unit, fake clock) ==")
    ma = P.MoveAuthority()
    ma.on_seed(8, 0.0)

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

    p = walk(0x80, 0, key=9)
    ev = ma.on_c2s_walk(p, "agent", 2.0)
    check("agent continuation: next ladder seq, key forced 0", p[2] == 3 and p[3:7] == bytes(4), p.hex())
    check("agent confirm hidden", ma.on_confirm(3) == ("hide", None))

    # ignored resync: no seed arrives -> ladder must continue (session 142237)
    ma.on_c2s_resync(3.0)
    check("agent gated while a resync awaits its reply",
          (ma.agent_walk_block(3.5, run=True) or "").startswith("walk gated: awaiting"))
    notes = ma.expire(3.0 + P.RESYNC_REPLY_TIMEOUT_S + 0.1)
    check("ignored resync detected", any(n[0] == "resync_ignored" for n in notes), str(notes))
    p = walk(0x80, 0)
    ma.on_c2s_walk(p, "agent", 5.0)
    check("ladder unchanged by an ignored resync", p[2] == 4, p.hex())
    ma.on_confirm(4)

    # honored resync: seed -> ladder 0, new token
    ma.on_c2s_resync(6.0)
    ma.on_seed(1, 6.05)
    p = walk(0x80, 0)
    ma.on_c2s_walk(p, "agent", 7.0)
    check("seed resets ladder; agent opener carries the new token",
          p[2] == 0 and p[3:7] == b"\x00\x00\x00\x01", p.hex())
    ma.on_confirm(0)

    # pacing
    check("run step 0.1 s after the last walk is gated",
          (ma.agent_walk_block(7.1, run=True) or "").startswith("walk gated: pacing"))
    check("run step 0.2 s after the last walk allowed", ma.agent_walk_block(7.2, run=True) is None)
    check("walk step 0.3 s after the last walk is gated",
          (ma.agent_walk_block(7.3, run=False) or "").startswith("walk gated: pacing"))

    # re-anchor timing after the burst (last agent walk 7.0, last seed 6.05)
    check("no re-anchor while the burst is fresh", not ma.reanchor_due(7.2))
    check("no re-anchor within RESYNC_MIN_S of the last seed", not ma.reanchor_due(10.0))
    check("re-anchor due once idle and spaced", ma.reanchor_due(6.05 + P.RESYNC_MIN_S + 0.01))
    p = walk(0x80, 0)
    ma.on_c2s_walk(p, "agent", 12.0)
    check("no re-anchor while an agent confirm is pending", not ma.reanchor_due(13.0))

    # unconfirmed agent walk -> desync: agent gated, re-anchor forced
    notes = ma.expire(12.0 + P.CONFIRM_TIMEOUT_S + 0.1)
    check("unconfirmed agent walk reported", any(n[0] == "walk_unconfirmed" for n in notes), str(notes))
    check("agent gated on desync",
          (ma.agent_walk_block(14.0, run=True) or "").startswith("walk gated: seq desync"))
    check("desync forces a re-anchor", ma.reanchor_due(14.0))
    ma.on_c2s_resync(14.0)
    ma.on_seed(1, 14.05)
    check("seed clears desync and staleness",
          not ma.desync and not ma.client_stale and ma.agent_walk_block(14.5, run=True) is None)

    # unconfirmed CLIENT walk is logged but does not trigger proxy traffic
    p = walk(0x80, 0, key=1)
    ma.on_c2s_walk(p, "client", 20.0)
    ma.expire(20.0 + P.CONFIRM_TIMEOUT_S + 0.1)
    check("unconfirmed client walk: no desync, no re-anchor", not ma.desync and not ma.reanchor_due(40.0))

    # deny resets the ladder
    p = walk(0x80, 1)
    ma.on_c2s_walk(p, "client", 41.0)
    ma.on_deny()
    p = walk(0x80, 0)
    ma.on_c2s_walk(p, "client", 42.0)
    check("deny resets ladder to 0", p[2] == 0, p.hex())


# ---------------------------------------------------------------- end-to-end

PROXY_PORT, UPSTREAM_PORT, CONTROL_PORT = 12593, 12594, 12598
LOGDIR = f"{ROOT}/logs_test"
C2S_KEY, S2C_KEY = 0x0F, 0x5A
PRELUDE = bytes([0xFF, 0x00, 0x0D] + [0] * 7 + [0x0C, S2C_KEY, C2S_KEY])


def xor(bs, k):
    return bytes(b ^ k for b in bs)


class FakeServer:
    def __init__(self):
        self.writer = None
        self.walks = []     # (seq, key) as received upstream
        self.resyncs = 0
        self.ready = asyncio.Event()

    async def handle(self, reader, writer):
        await reader.readexactly(5)
        self.writer = writer
        writer.write(PRELUDE + encode_packet(seed_pkt(8), S2C_KEY))
        await writer.drain()
        self.ready.set()
        buf = bytearray()
        while True:
            d = await reader.read(65536)
            if not d:
                break
            buf += xor(d, C2S_KEY)
            while buf:
                if buf[0] == 0x02 and len(buf) >= 7:
                    self.walks.append((buf[2], int.from_bytes(buf[3:7], "big")))
                    del buf[:7]
                elif buf[0] == 0x22 and len(buf) >= 3:
                    self.resyncs += 1
                    del buf[:3]
                else:
                    break

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

        async def client_walk(seq, key=0):
            writer.write(xor(walk(0x80, seq, key), C2S_KEY))
            await writer.drain()
            await asyncio.sleep(0.25)

        ctl = socket.create_connection(("127.0.0.1", CONTROL_PORT), timeout=10)

        def inject(pkt):
            ctl.sendall(len(pkt).to_bytes(2, "big") + pkt)
            n = int.from_bytes(ctl.recv(2), "big")
            return ctl.recv(n).decode()

        # client walks with its own seqs 5, 9 -> ladder 0 (token 8), 1
        await client_walk(5)
        await client_walk(9)
        await srv.send(b"\x22\x00\x01")
        await srv.send(b"\x22\x01\x01")
        await asyncio.sleep(0.3)
        # agent walk -> ladder 2; its confirm must not reach the client
        r1 = inject(bytes(walk(0x80, 77)))
        r2 = inject(bytes(walk(0x80, 78)))  # immediately again -> pacing
        await srv.send(b"\x22\x02\x01")
        await asyncio.sleep(0.3)
        confirms_at_client = [p[1] for p in client_rx if p[0] == 0x22]

        # wait for the automatic re-anchor resync (RESYNC_MIN_S after the login seed)
        for _ in range(80):
            if srv.resyncs:
                break
            await asyncio.sleep(0.1)
        resyncs = srv.resyncs
        await srv.send(seed_pkt(1))
        await asyncio.sleep(0.3)
        r3 = inject(bytes(walk(0x80, 0)))
        await asyncio.sleep(0.3)
        ctl.close()
        writer.close()
        await asyncio.sleep(0.3)
        rt.cancel()

        check("client walks rewritten upstream: (0,8), (1,0)", srv.walks[:2] == [(0, 8), (1, 0)], str(srv.walks))
        check("client received its confirms under its own seqs 5, 9",
              confirms_at_client == [5, 9], str(confirms_at_client))
        check("agent walk accepted and sent as ladder seq 2", r1 == "OK" and srv.walks[2:3] == [(2, 0)],
              f"{r1} {srv.walks}")
        check("agent walk right after is paced", r2.startswith("ERR walk gated: pacing"), r2)
        check("agent confirm hidden from the client", 2 not in confirms_at_client, str(confirms_at_client))
        check("proxy re-anchored the client with exactly one resync", resyncs == 1, str(resyncs))
        check("after the seed, agent walk opens with seq 0 + token 1",
              r3 == "OK" and srv.walks[3:4] == [(0, 1)], f"{r3} {srv.walks}")
        check("client prelude relayed", bytes(got_prelude) == PRELUDE, got_prelude.hex())
    finally:
        proxy.terminate()
        server.close()


def main():
    test_unit()
    asyncio.run(e2e())
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILURES: {FAILURES}"))
    sys.exit(0 if not FAILURES else 1)


if __name__ == "__main__":
    main()

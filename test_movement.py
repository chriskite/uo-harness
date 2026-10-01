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
import json
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

    # late confirm (server hitch past the timeout): hidden from the client, the ladder moves
    # past it, and the agent re-sends nothing while that confirm may still come (20260930_123206)
    ma = P.MoveAuthority()
    ma.on_self_position(100, 200, 5, 2)
    ma.on_seed(8)
    p = walk(0x82, 0)
    ma.on_c2s_walk(p, "agent", 50.0)
    t_exp = 50.0 + P.CONFIRM_TIMEOUT_S + 0.1
    ma.expire(t_exp)
    check("agent gated while an expired walk may still be confirmed",
          (ma.agent_walk_block(t_exp + 1.0, run=True) or "").startswith("walk gated: an expired walk"))
    check("late confirm of an agent walk is hidden", ma.on_confirm(0) == ("hide", None))
    check("late confirm moves the tracked position", ma.pos[:2] == [101, 200], str(ma.pos))
    check("late confirm reopens agent walks", ma.agent_walk_block(t_exp + 1.0, run=True) is None)
    p = walk(0x82, 0)
    ma.on_c2s_walk(p, "agent", t_exp + 1.0)
    check("ladder continues past the late-confirmed seq (no seq sent twice)", p[2] == 1, p.hex())
    p = walk(0x82, 7)
    ma.on_c2s_walk(p, "client", t_exp + 1.5)           # seq 2, from the client
    ma.expire(t_exp + 1.5 + P.CONFIRM_TIMEOUT_S + 0.1)  # both expire
    check("late confirms: agent seq 1 and client seq 2 both hidden (the re-anchor reset the "
          "client's walker, so a forwarded confirm would be a bad step)",
          ma.on_confirm(1) == ("hide", None) and ma.on_confirm(2) == ("hide", None) and ma.client_stale)
    p = walk(0x82, 0)
    t = t_exp + 8.0
    ma.on_c2s_walk(p, "agent", t)                        # seq 3, never confirmed
    ma.expire(t + P.CONFIRM_TIMEOUT_S + 0.1)
    ma.expire(t + P.CONFIRM_TIMEOUT_S + P.LATE_CONFIRM_GRACE_S + 0.2)
    check("after the grace period an unconfirmed walk is rejected for good",
          p[2] == 3 and ma.on_confirm(3) == ("forward", None) and not ma.late, p.hex())

    # the stock client never has more than MAX_STEP_COUNT (5) unconfirmed steps
    ma = P.MoveAuthority()
    ma.on_seed(8)
    for i in range(P.MAX_STEPS_IN_FLIGHT):
        ma.on_c2s_walk(walk(0x82, 0), "agent", 60.0 + i * 0.3)
    check("agent gated at 5 unconfirmed walks",
          "unconfirmed" in (ma.agent_walk_block(62.0, run=True) or ""))


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

PROXY_PORT, UPSTREAM_PORT, CONTROL_PORT, STATE_PORT = 12593, 12594, 12598, 12602
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
         "--control-port", str(CONTROL_PORT), "--state-port", str(STATE_PORT), "--logdir", LOGDIR],
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
        st_sock = socket.create_connection(("127.0.0.1", STATE_PORT), timeout=10)
        st_sock.sendall(b'{"op": "state", "since": 0}\n')
        st_buf = b""
        while not st_buf.endswith(b"\n"):
            st_buf += st_sock.recv(65536)
        st_sock.close()
        state = json.loads(st_buf)
        # the agent answers a gump: the server gets the 0xB1, the client gets a close for that gump
        # id (0xBF sub 4, button 0), so it doesn't keep drawing a gump the server already closed
        before = len(client_rx)
        r_gump = inject(bytes.fromhex("b1001700001234" "e0e675b8" "00000002" "00000000" "00000000"))
        await asyncio.sleep(0.3)
        closes = [p for p in client_rx[before:] if p[:5] == bytes.fromhex("bf000d0004")]
        # the agent answers the target cursor the client shows: the client gets a cancel, and
        # its reply for that (spent) cursor never reaches the server; a later cursor still works
        cursor = bytes.fromhex("6c01" "0005d96a" "00") + bytes(20)
        await srv.send(cursor)
        await asyncio.sleep(0.2)
        before = len(client_rx)
        n_other = len(srv.other)
        r_target = inject(bytes.fromhex("6c01" "0005d96a" "00" "00000000" "00000100" "00000200"
                                        "00000000" "00000cd0"))
        await asyncio.sleep(0.3)
        cancels = [p for p in client_rx[before:] if p[0] == 0x6C]
        writer.write(xor(bytes.fromhex("6c00" "0005d96a" "03") + bytes(4) + bytes.fromhex("7fffffff" * 3)
                         + bytes(4), C2S_KEY))
        await writer.drain()
        await asyncio.sleep(0.3)
        after_spent = srv.other[n_other:]
        await srv.send(bytes.fromhex("6c01" "0005d96b" "00") + bytes(20))
        await asyncio.sleep(0.2)
        writer.write(xor(bytes.fromhex("6c01" "0005d96b" "00" "00000000" "00000101" "00000201"
                                       "00000000" "00000cd0"), C2S_KEY))
        await writer.drain()
        await asyncio.sleep(0.3)
        after_fresh = srv.other[n_other:]
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
        check("each confirmed agent walk re-anchors the client right away (turn E, then step E): it follows "
              "the character instead of lagging until walking stops (ANTICHEAT §10 A11)",
              [deny_fields(d) for d in denies_mid] == [(100, 198, 5, 2), (101, 198, 5, 2)],
              str([deny_fields(d) for d in denies_mid]))
        check("no further re-anchor once walking is quiet (the client is already there)",
              len(denies) == len(denies_mid), str([deny_fields(d) for d in denies]))
        check("server received no proxy-originated packets (only walks, the gump reply and target replies)",
              [p for p in srv.other if p[0] not in (0xB1, 0x6C)] == [], str(srv.other))
        check("client prelude relayed", bytes(got_prelude) == PRELUDE, got_prelude.hex())
        mv = state.get("movement", {})
        check("state endpoint: movement pos = tracked true position",
              state.get("ok") and mv.get("pos") == [101, 198, 5, 2], str(mv))
        check("state endpoint: live world model knows self serial",
              state.get("world", {}).get("self", {}).get("serial") in (SERIAL, f"0x{SERIAL:08X}"),
              str(state.get("world", {}).get("self", {}).get("serial")))
        log = [json.loads(l) for f in os.listdir(LOGDIR) if f.endswith(".jsonl")
               for l in open(os.path.join(LOGDIR, f), encoding="utf-8")]
        steps = [(e["from"], e["to"]) for e in log if e.get("ev") == "step"]
        check("jsonl step events for confirmed moves (turn logs none)",
              steps == [([100, 200], [100, 199]), ([100, 199], [100, 198]), ([100, 198], [101, 198])], str(steps))
        envs = state.get("events", [])
        psteps = [(e["data"]["from"], e["data"]["to"]) for e in envs
                  if e.get("origin") == "proxy" and e["data"].get("ev") == "step"
                  and isinstance(e.get("t"), (int, float))]
        check("state events: proxy-origin step envelopes with numeric t, as in the jsonl",
              psteps == steps, str(psteps))
        check("state events: seqs are 0..next-1 in order",
              [e.get("seq") for e in envs] == list(range(state.get("next", -1))), str(state.get("next")))
        agent_c2s = [e["data"]["id"] for e in envs if e["origin"] == "proxy" and e["data"]["ev"] == "c2s"]
        check("state events: one c2s summary per agent packet (none for client packets)",
              agent_c2s == ["0x02", "0x02"], str(agent_c2s))
        check("state events: agent confirms hidden, a re-anchor on each",
              [e["data"]["seq"] for e in envs if e["data"].get("ev") == "s2c_confirm_hidden"] == [2, 3]
              and [(e["data"]["x"], e["data"]["y"], e["data"].get("on")) for e in envs
                   if e["data"].get("ev") == "reanchor_client"]
              == [(100, 198, "confirm"), (101, 198, "confirm")], str([e["data"] for e in envs if e["origin"] == "proxy"]))
        check("agent gump reply: relayed upstream, and the client's copy closed (gump 0xE0E675B8, button 0)",
              r_gump == "OK" and any(p[0] == 0xB1 for p in srv.other)
              and closes == [bytes.fromhex("bf000d0004" "e0e675b8" "00000000")], f"{r_gump} {closes} {srv.other}")
        check("agent target reply: the client gets the server's own cancel shape for its copy",
              r_target == "OK" and cancels == [P.TARGET_CANCEL_S2C], f"{r_target} {[c.hex() for c in cancels]}")
        check("client's reply to the spent cursor is not relayed; its reply to a new cursor is",
              [p[:6] for p in after_spent] == [bytes.fromhex("6c01" "0005d96a")]
              and [p[:6] for p in after_fresh] == [bytes.fromhex("6c01" "0005d96a"), bytes.fromhex("6c01" "0005d96b")],
              f"{[p.hex() for p in after_spent]} / {[p.hex() for p in after_fresh]}")
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

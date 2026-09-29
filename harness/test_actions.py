"""Phase 3 tests: action builders, proxy injection path, replay integration.

Sections:
  1. Unit: every builder in actions.py vs hand-computed / capture-derived
     expected bytes (ground truth: logs/session_20260928_141253.{c2s.raw,jsonl}
     and docs/WORLDMODEL.md §5).
  2. Injection: proxy subprocess + fake upstream; control client injects
     actions; assert the upstream receives exactly the XOR-encrypted bytes
     under the test session key and the session jsonl logs them as c2s.
  3. Replay integration: action packets fed through WorldRuntime on a
     captured session move StateStore self position and queue events.
  4. Edge: injection before key known -> clean rejection; malformed control
     frame -> connection closed, relay unaffected; walk seq wrap 255->0.

Run: python harness/test_actions.py
"""
import asyncio
import collections
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import actions
import replay as replay_mod
from uo.packets import packet_length, C2S_OVERRIDES
from world.runtime import C2S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
C2S_RAW = open(f"{ROOT}/logs/session_20260928_141253.c2s.raw", "rb").read()
S2C_RAW = open(f"{ROOT}/logs/session_20260928_141253.s2c.raw", "rb").read()
SESSION_KEY = S2C_RAW[12]  # prelude byte 12 (0x07 in this capture)
PRELUDE = S2C_RAW[:19]

PROXY_PORT = 12595
UPSTREAM_PORT = 12596
CONTROL_PORT = 12597
LOGDIR = f"{ROOT}/logs_test_actions"

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  [OK] {name}")
    else:
        print(f"  [FAIL] {name} {detail}")
        FAILURES.append(name)


# ---------------------------------------------------------------------------
# 1. builder unit tests
# ---------------------------------------------------------------------------

def test_walk():
    print("== walk ==")
    # hand-computed: 02 dir seq fastwalk-key(0)
    check("walk dir 7 seq 0",
          actions.walk(7) == bytes.fromhex("02070000000000"))
    check("walk run dir 6 seq 0x3f",
          actions.walk(6, run=True, seq=0x3F) == bytes.fromhex("02863f00000000"))
    # ground truth: decode the real capture and match a dir-6-run packet
    plain = bytes(b ^ SESSION_KEY for b in C2S_RAW[5:])
    buf = bytearray(plain)
    found = []
    while buf:
        plen = packet_length(buf, overrides=C2S_OVERRIDES)
        if plen <= 0:
            break
        pkt = bytes(buf[:plen])
        del buf[:plen]
        if pkt[0] == 0x02 and pkt[1] == 0x86:
            found.append(pkt)
    check("capture contains dir-6 run walks", len(found) >= 2,
          f"(got {len(found)})")
    if len(found) >= 2:
        # consecutive walks: our builder with the captured seqs reproduces them
        check("builder reproduces captured walk #1",
              actions.walk(6, run=True, seq=found[0][2]) == found[0],
              found[0].hex())
        check("builder reproduces captured walk #2",
              actions.walk(6, run=True, seq=found[1][2]) == found[1],
              found[1].hex())
    try:
        actions.walk(8)
        check("walk dir 8 rejected", False)
    except ValueError:
        check("walk dir 8 rejected", True)


def test_sequencer():
    print("== WalkSequencer ==")
    s = actions.WalkSequencer()
    check("starts at 0", s.next() == 0 and s.next() == 1)
    w = actions.WalkSequencer(start=255)
    pkt1 = w.walk(2)
    pkt2 = w.walk(2)
    check("seq wrap 255->0", pkt1[2] == 255 and pkt2[2] == 0,
          f"({pkt1[2]}, {pkt2[2]})")
    check("seq consumed once per walk", w.seq == 1)


def test_dclick():
    print("== dclick ==")
    # capture: {"id": "0x06", "hex": "0640005913"}
    check("dclick 0x40005913",
          actions.dclick(0x40005913) == bytes.fromhex("0640005913"))


def test_say_unicode():
    print("== say_unicode ==")
    # capture: ad 0018 00 02b2 0003 "ENU\0" <utf16be "howdy"> 0000
    expect = bytes.fromhex("ad00180002b20003454e55000068006f0077006400790000")
    got = actions.say_unicode("howdy")
    check('say "howdy" matches capture byte-for-byte', got == expect,
          got.hex())
    # second captured sample: "[aspect" (28 bytes)
    expect2 = bytes.fromhex("ad001c0002b20003454e5500005b0061007300700065006300740000")
    check('say "[aspect" matches capture',
          actions.say_unicode("[aspect") == expect2)
    try:
        actions.say_unicode("x", lang=b"EN")
        check("bad lang rejected", False)
    except ValueError:
        check("bad lang rejected", True)


def test_cast_spell():
    print("== cast_spell ==")
    # capture: ff 000a 00000004 00 000f (spell 15)
    check("cast spell 15",
          actions.cast_spell(15) == bytes.fromhex("ff000a0000000400" "000f"),
          actions.cast_spell(15).hex())
    check("cast spell 5",
          actions.cast_spell(5) == bytes.fromhex("ff000a0000000400" "0005"))


def test_item_query():
    print("== item_query ==")
    # capture: ff 000e 00000009 01 0001 40000d54
    check("item query 0x40000D54",
          actions.item_query(0x40000D54)
          == bytes.fromhex("ff000e0000000901000140000d54"))


def test_gump_response():
    print("== gump_response ==")
    # capture: b1 0017 00215ad2 907fc735 00000005 00000000 00000000
    got = actions.gump_response(0x00215AD2, 0x907FC735, 5)
    check("gump response matches capture",
          got == bytes.fromhex("b1001700215ad2907fc735000000050000000000000000"),
          got.hex())


def test_lift_drop():
    print("== lift / drop ==")
    # layouts per upstream OutgoingPackets.cs Send_PickUpRequest/Send_DropRequest
    check("lift",
          actions.lift(0x40000D54, 1) == bytes.fromhex("0740000d540001"))
    check("drop to ground",
          actions.drop(0x40000D54, 100, 200, 5)
          == bytes.fromhex("0840000d54006400c80500ffffffff"),
          actions.drop(0x40000D54, 100, 200, 5).hex())
    check("drop into container with grid slot",
          actions.drop(0x40000D54, 0xFFFF, 0xFFFF, -5, grid=3,
                       container_serial=0x40001111)
          == bytes.fromhex("0840000d54fffffffffb0340001111"))
    try:
        actions.drop(1, 0, 0, 128)
        check("drop z=128 rejected", False)
    except ValueError:
        check("drop z=128 rejected", True)


# ---------------------------------------------------------------------------
# 2. injection path (proxy subprocess + fake upstream)
# ---------------------------------------------------------------------------

GOT_UPSTREAM = bytearray()  # everything the fake server received post-preamble
UPSTREAM_GOT_PRELUDE_ACK = asyncio.Event()


async def fake_upstream():
    async def handle(reader, writer):
        preamble = await reader.readexactly(5)   # client preamble, cleartext
        assert len(preamble) == 5
        writer.write(PRELUDE)                    # server prelude w/ session key
        await writer.drain()
        UPSTREAM_GOT_PRELUDE_ACK.set()
        while True:
            d = await reader.read(65536)
            if not d:
                break
            GOT_UPSTREAM.extend(d)
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", UPSTREAM_PORT)
    async with server:
        await server.serve_forever()


async def ctrl_send(payload, expect_reply_prefix):
    """Open a control connection, send one frame, read the reply."""
    reader, writer = await asyncio.open_connection("127.0.0.1", CONTROL_PORT)
    writer.write(len(payload).to_bytes(2, "big") + payload)
    await writer.drain()
    hdr = await reader.readexactly(2)
    n = int.from_bytes(hdr, "big")
    reply = await reader.readexactly(n)
    return reader, writer, reply


async def injection_test():
    os.makedirs(LOGDIR, exist_ok=True)
    for f in os.listdir(LOGDIR):
        os.remove(os.path.join(LOGDIR, f))

    proxy = subprocess.Popen(
        [PY, f"{ROOT}/harness/proxy.py",
         "--listen-port", str(PROXY_PORT),
         "--upstream-host", "127.0.0.1", "--upstream-port", str(UPSTREAM_PORT),
         "--control-port", str(CONTROL_PORT),
         "--logdir", LOGDIR],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    await asyncio.sleep(1.0)

    server_task = asyncio.create_task(fake_upstream())
    try:
        await asyncio.sleep(0.3)

        # --- edge: inject before any session exists -> clean rejection
        r, w, reply = await ctrl_send(actions.walk(2, seq=0), b"ERR")
        check("inject before session rejected", reply.startswith(b"ERR"),
              reply)
        w.close()

        # --- game client connects; preamble flows, prelude sets the key
        game_reader, game_writer = await asyncio.open_connection(
            "127.0.0.1", PROXY_PORT)
        game_writer.write(b"\xef\x00\x00\x00\x0c")
        await game_writer.drain()
        await asyncio.wait_for(UPSTREAM_GOT_PRELUDE_ACK.wait(), timeout=5)
        await asyncio.sleep(0.3)  # let the proxy tap parse the prelude

        # --- inject one of every action type
        seq = actions.WalkSequencer()
        payloads = [
            seq.walk(6, run=True),
            seq.walk(6, run=True),
            actions.dclick(0x40005913),
            actions.say_unicode("howdy"),
            actions.cast_spell(15),
            actions.item_query(0x40000D54),
            actions.gump_response(0x00215AD2, 0x907FC735, 5),
            actions.lift(0x40000D54, 1),
            actions.drop(0x40000D54, 100, 200, 5),
        ]
        cr, cw = await asyncio.open_connection("127.0.0.1", CONTROL_PORT)
        for p in payloads:
            cw.write(len(p).to_bytes(2, "big") + p)
            await cw.drain()
            hdr = await cr.readexactly(2)
            n = int.from_bytes(hdr, "big")
            reply = await cr.readexactly(n)
            if reply != b"OK":
                check(f"inject {p.hex()} accepted", False, reply)
        check("all 9 injections accepted", True)

        # ordering probe: real client keepalive interleaved after injections
        keepalive_plain = bytes.fromhex("ff000700000003")
        game_writer.write(bytes(b ^ SESSION_KEY for b in keepalive_plain))
        await game_writer.drain()
        await asyncio.sleep(0.5)

        expected = b"".join(
            bytes(b ^ SESSION_KEY for b in p) for p in payloads
        ) + bytes(b ^ SESSION_KEY for b in keepalive_plain)
        check("upstream received exactly XOR(key) of injections + keepalive",
              bytes(GOT_UPSTREAM) == expected,
              f"({len(GOT_UPSTREAM)}/{len(expected)} bytes)")

        # --- edge: malformed control frame -> ERR + connection closed,
        #     relay unaffected
        mr, mw, reply = await ctrl_send(b"\x02\x86\x00\x00", b"ERR")  # truncated 0x02
        check("malformed frame rejected", reply == b"ERR malformed packet",
              reply)
        closed = await mr.read() == b""
        check("malformed frame closes control connection", closed)
        # relay still fine: another keepalive gets through
        game_writer.write(bytes(b ^ SESSION_KEY for b in keepalive_plain))
        await game_writer.drain()
        await asyncio.sleep(0.4)
        check("relay unaffected by malformed control frame",
              bytes(GOT_UPSTREAM).endswith(
                  bytes(b ^ SESSION_KEY for b in keepalive_plain) * 2))

        game_writer.close()
        cw.close()
        await asyncio.sleep(0.5)

        # --- session log: injected packets appear as c2s
        logs = [f for f in os.listdir(LOGDIR) if f.endswith(".jsonl")]
        check("one jsonl session log", len(logs) == 1, str(logs))
        events = [json.loads(l)
                  for l in open(os.path.join(LOGDIR, logs[0]), encoding="utf-8")]
        c2s = [e for e in events if e.get("dir") == "c2s"]
        ids = collections.Counter(e["id"] for e in c2s)
        for pid, n in {"0x02": 2, "0x06": 1, "0xAD": 1, "0xFF": 4,
                       "0xB1": 1, "0x07": 1, "0x08": 1}.items():
            check(f"log c2s {pid} == {n}", ids.get(pid, 0) == n,
                  f"(got {ids.get(pid, 0)})")
        walk_logs = [e["hex"] for e in c2s if e["id"] == "0x02"]
        check("logged walks are the injected plaintext",
              walk_logs == [p.hex() for p in payloads[:2]], str(walk_logs))
        speech = [e for e in c2s if e["id"] == "0xAD"]
        check("logged speech matches capture format",
              speech and speech[0]["hex"]
              == "ad00180002b20003454e55000068006f0077006400790000")
    finally:
        proxy.terminate()
        server_task.cancel()
        out = proxy.stdout.read() if proxy.stdout else "(none)"
        if FAILURES:
            print("--- proxy output ---")
            print(out)


# ---------------------------------------------------------------------------
# 3. replay integration: action packets through the world-model pipeline
# ---------------------------------------------------------------------------

def test_replay_actions():
    print("== replay + action API ==")
    res = replay_mod.replay_session(f"{ROOT}/logs/session_20260928_141253.c2s.raw",
                                    f"{ROOT}/logs/session_20260928_141253.s2c.raw")
    rt = res.runtime
    rt.drain_events()
    s = res.state.self
    x0, y0, pc0 = s.x, s.y, s.position_changes

    seq = actions.WalkSequencer()
    # 2x east (dir 2: +1,+0), 1x west (dir 6: -1,+0) -> net (+1, 0)
    for d in (2, 2, 6):
        rt.feed_packet(C2S, seq.walk(d))
    rt.feed_packet(C2S, actions.cast_spell(15))
    rt.feed_packet(C2S, actions.item_query(0x40000D54))
    rt.feed_packet(C2S, actions.dclick(0x40005913))
    rt.feed_packet(C2S, actions.say_unicode("howdy"))

    check("injected walks moved self (+1, 0)",
          (s.x, s.y) == (x0 + 1, y0), f"({x0},{y0}) -> ({s.x},{s.y})")
    check("position_changes +3", s.position_changes == pc0 + 3,
          f"({pc0} -> {s.position_changes})")
    check("walk seq tracked", s.walk_seq == 2, f"(got {s.walk_seq})")

    events = rt.drain_events()
    kinds = collections.Counter(e["ev"] for e in events)
    check("walk events queued", kinds.get("walk", 0) == 3, str(kinds))
    check("spell_cast event", kinds.get("spell_cast") == 1)
    check("item_query event", kinds.get("item_query") == 1)
    check("dclick event", kinds.get("dclick") == 1)
    check("speech event", kinds.get("speech") == 1)
    sp = [e for e in events if e["ev"] == "speech"]
    check("speech round-trips through parser",
          sp and sp[0]["text"] == "howdy" and sp[0]["hue"] == 0x02B2,
          str(sp[:1]))
    sc = [e for e in events if e["ev"] == "spell_cast"]
    check("spell_cast parsed id 15", sc and sc[0]["spell_id"] == 15,
          str(sc[:1]))
    check("no parse failures from injected packets",
          rt.parse_failures == 0, f"({rt.parse_failures})")


# ---------------------------------------------------------------------------

def main():
    test_walk()
    test_sequencer()
    test_dclick()
    test_say_unicode()
    test_cast_spell()
    test_item_query()
    test_gump_response()
    test_lift_drop()
    print("== proxy injection ==")
    asyncio.run(injection_test())
    test_replay_actions()
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILURES: {FAILURES}"))
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())

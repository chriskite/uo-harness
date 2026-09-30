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
from uo import speech
import replay as replay_mod
from uo.packets import packet_length, C2S_OVERRIDES
from uo.s2c import PRELUDE_LEN, encode_packet
from world.runtime import C2S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
C2S_RAW = open(f"{ROOT}/logs/session_20260928_141253.c2s.raw", "rb").read()
S2C_RAW = open(f"{ROOT}/logs/session_20260928_141253.s2c.raw", "rb").read()
SESSION_KEY = S2C_RAW[12]  # prelude byte 12: C2S key (0x07 in this capture)
# real 13-byte prelude + the login fastwalk seed (token 8) the server sends at
# login; the proxy arms the walk token from it (docs/MOVEMENT.md)
PRELUDE = S2C_RAW[:PRELUDE_LEN] + encode_packet(
    bytes.fromhex("bf001d0001 00000008") + bytes(20), S2C_RAW[11])

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
    check("seq wrap 255->1 (never 0)", pkt1[2] == 255 and pkt2[2] == 1,
          f"({pkt1[2]}, {pkt2[2]})")
    check("seq consumed once per walk", w.seq == 2)


def test_dclick():
    print("== dclick ==")
    # capture: {"id": "0x06", "hex": "0640005913"}
    check("dclick 0x40005913",
          actions.dclick(0x40005913) == bytes.fromhex("0640005913"))
    # capture (session_20260929_161433, stock client clicking NPC 0x1E2):
    # 09 000001e2 / 34 edededed 04 000001e2 / 98 0007 000001e2
    check("single_click 0x1E2", actions.single_click(0x1E2) == bytes.fromhex("09000001e2"))
    check("status_request 0x1E2", actions.status_request(0x1E2) == bytes.fromhex("34edededed04000001e2"))
    check("name_request 0x1E2", actions.name_request(0x1E2) == bytes.fromhex("980007000001e2"))


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
    # "[pouch" as sent by the stock client (session_20260929_161433, src=client)
    expect3 = bytes.fromhex("ad001a0002b20003454e5500005b0070006f0075006300680000")
    check('say "[pouch" matches capture',
          actions.say_unicode("[pouch") == expect3)
    # the stock client sent these UNENCODED, so no speech.mul keyword matches
    for t in ("howdy", "[aspect", "[pouch"):
        kw = speech.get_keywords(t)
        check(f'no keywords for "{t}"', kw == [], str(kw))
    # "hello" is speech.mul entry 59 (exact-match keyword, no '*'). The
    # unencoded "hello" in session_20260928_211622 was a harness injection by
    # the pre-keyword say_unicode, NOT stock output: the Outlands client's
    # IsMatch (@ 0x1401bba40) is the upstream algorithm, so it would encode it.
    # count 1 -> 00 | (1<<4 | 0x03B>>8)=10 | 3b; then utf8 "hello" 00
    check('"hello" keywords == [59]', speech.get_keywords("hello") == [59])
    check('say "hello" is encoded',
          actions.say_unicode("hello") == bytes.fromhex(
              "ad0015c002b20003454e550000103b") + b"hello\x00",
          actions.say_unicode("hello").hex())
    check('"hello there" does not match exact-only entry 59',
          59 not in speech.get_keywords("hello there"))
    try:
        actions.say_unicode("x", lang=b"EN")
        check("bad lang rejected", False)
    except ValueError:
        check("bad lang rejected", True)


def test_speech_keywords():
    print("== speech keywords (speech.mul) ==")
    n = len(speech.load())
    check("speech.mul parsed", n > 0, f"({n} entries)")
    kw = speech.get_keywords("bank")
    print(f"    'bank' -> {[hex(k) for k in kw]}")
    # 0x0002 is the RunUO bank keyword; speech.mul lists "*bank*" twice
    check('"bank" keywords contain 0x0002', 0x0002 in kw, str(kw))
    check('"bank" keywords == [2, 2]', kw == [2, 2], str(kw))
    check("case-insensitive, trims spaces",
          speech.get_keywords("  BaNk ") == kw)
    check("word boundary: 'banker' is not 'bank'",
          0x0002 not in speech.get_keywords("banker"))
    check("punctuation is a boundary: 'bank!'",
          0x0002 in speech.get_keywords("bank!"))

    # encode_keywords, hand-packed per OutgoingPackets.cs:992-1033:
    # byte count>>4; pending = count&15; per id alternately
    #   even slot: ((pending<<4) | (id>>8 & 15)), (id & 0xFF)
    #   odd slot:  (id>>4), pending = id&15
    # trailing (pending<<4) only if the id count is even.
    # 1 id [0xABC]: 00 | (1<<4|0xA)=1a | bc                    -> 00 1a bc
    check("encode 1 id", speech.encode_keywords([0xABC]) == bytes.fromhex("001abc"))
    # 2 ids [0x123,0x456]: 00 | (2<<4|1)=21 | 23 | 0x456>>4=45, pending 6
    #   | pad 6<<4=60                                           -> 00 21 23 45 60
    check("encode 2 ids", speech.encode_keywords([0x123, 0x456])
          == bytes.fromhex("0021234560"))
    # 3 ids [0x123,0x456,0x789]: 00 21 23 45 | (6<<4|7)=67 | 89, no pad
    #   (count 3 -> first nibble byte 0x31)                   -> 00 31 23 45 67 89
    check("encode 3 ids", speech.encode_keywords([0x123, 0x456, 0x789])
          == bytes.fromhex("003123456789"))
    # 17 ids: count 0x011 -> first byte 0x01, pending nibble 1
    check("encode count >= 16 uses the high count byte",
          speech.encode_keywords([0] * 17)[:2] == bytes.fromhex("0110"))

    # encoded "bank" packet, default hue/font/lang
    pkt = actions.say_unicode("bank")
    print(f"    'bank' -> {pkt.hex()}")
    check("bank: length field == len(pkt)",
          int.from_bytes(pkt[1:3], "big") == len(pkt) == 0x16, pkt.hex())
    check("bank: type 0xC0 (encoded)", pkt[3] == 0xC0)
    check("bank: hue/font/lang",
          pkt[4:12] == bytes.fromhex("02b20003") + b"ENU\x00")
    check("bank: keyword bytes 00 20 02 00 20 (count 2, ids 2, 2)",
          pkt[12:17] == bytes.fromhex("0020020020"))
    check("bank: utf8 text + NUL", pkt[17:] == b"bank\x00")
    # stock client capture (logs/session_20260929_161433.c2s.raw)
    check('say "bank" matches stock-client capture byte-for-byte',
          pkt == bytes.fromhex("ad0016c002b20003454e5500002002002062616e6b00"))
    # type/hue parameters flow into the encoded form (type |= 0xC0)
    yell = actions.say_unicode("bank", hue=0x0035, msg_type=0x09)
    check("encoded yell keeps base type bits", yell[3] == 0xC9 and
          yell[4:6] == bytes.fromhex("0035"), yell.hex())


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
    # capture (same session): one empty text entry, id 2
    got = actions.gump_response(0x00215AB3, 0x6B147227, 0, text_entries=[(2, "")])
    check("gump response with empty text entry matches capture",
          got == bytes.fromhex("b1001b00215ab36b147227000000000000000000000001"
                               "00020000"), got.hex())
    # Send_GumpResponse @ 0x1401557a0: n switches u32 each, then per entry
    # id u16, UTF-16 unit count u16, UTF-16BE text; '\n' -> '\x1f'
    got = actions.gump_response(1, 2, 3, switches=[7, 0x10001],
                                text_entries=[(5, "a\nb")])
    check("gump response switches + text entry layout",
          got == bytes.fromhex("b10029" "00000001" "00000002" "00000003"
                               "00000002" "00000007" "00010001"
                               "00000001" "0005" "0003" "0061001f0062"),
          got.hex())
    try:
        actions.gump_response(1, 2, 3, text_entries=[(1, "x" * 0x801)])
        check("gump text entry over 0x800 units rejected", False)
    except ValueError:
        check("gump text entry over 0x800 units rejected", True)
    try:
        actions.gump_response(1, 2, 3, switches=range(0x2000))
        check("gump response over 0x8000 bytes rejected", False)
    except ValueError:
        check("gump response over 0x8000 bytes rejected", True)
    # Every reply carries the gump's entries and checked switches, like the stock client
    # (live 20260930_123206: an entry-less reply to 'Retrieve Items' -> "That is not a valid number.")
    shelf = ("{ text 58 99 2599 3 }{ textentrylimited 147 100 78 20 2655 1 0 5 }"
             "{ checkbox 10 10 210 211 1 7 }{ checkbox 10 30 210 211 0 8 }"
             "{ textentry 147 130 78 20 2655 9 1 }{ button 200 200 247 248 1 0 2 }")
    got = actions.gump_reply(0x25954A, 0xBEC6217A, 2, shelf, ["", "x"])
    check("gump_reply: all entries (current text) and the checked switch, as the client sends",
          got == actions.gump_response(0x25954A, 0xBEC6217A, 2, switches=[7],
                                       text_entries=[(1, ""), (9, "x")]), got.hex())
    got = actions.gump_reply(0x25954A, 0xBEC6217A, 2, shelf, ["", "x"], {1: "20"})
    check("gump_reply: an override replaces only its entry",
          got == actions.gump_response(0x25954A, 0xBEC6217A, 2, switches=[7],
                                       text_entries=[(1, "20"), (9, "x")]), got.hex())


def test_text_entry_response():
    print("== text_entry_response ==")
    # Send_TextEntryDialogResponse @ 0x140159b90: serial, parent, button,
    # ok u8, len(text)+1 u16, ASCII text + NUL
    got = actions.text_entry_response(0x40001234, 1, 2, "Bob")
    check("text entry response layout",
          got == bytes.fromhex("ac0010" "40001234" "01" "02" "01" "0004"
                               "426f6200"), got.hex())
    check("text entry cancel clears ok byte",
          actions.text_entry_response(1, 1, 2, "", ok=False)[9] == 0)
    try:
        actions.text_entry_response(1, 1, 2, "caf\u00e9")
        check("text entry non-ASCII rejected", False)
    except ValueError:
        check("text entry non-ASCII rejected", True)


def test_lift_drop():
    print("== lift / drop ==")
    # Send_PickUpRequest @ 0x14014bfb0: serial u32, amount u16 (table 7)
    check("lift",
          actions.lift(0x40000D54, 1) == bytes.fromhex("0740000d540001"))
    # Send_DropRequest @ 0x14014c700, protocol >= 10 branch: u32 x/y/z,
    # grid u8, container u32 = 22 B = the client's table length for 0x08
    check("drop to ground (22 B V10+ form)",
          actions.drop(0x40000D54, 100, 200, 5)
          == bytes.fromhex("0840000d54" "00000064" "000000c8" "00000005"
                           "00" "ffffffff"),
          actions.drop(0x40000D54, 100, 200, 5).hex())
    check("drop into container with grid slot, negative z",
          actions.drop(0x40000D54, 0xFFFF, 0xFFFF, -5, grid=3,
                       container_serial=0x40001111)
          == bytes.fromhex("0840000d54" "0000ffff" "0000ffff" "fffffffb"
                           "03" "40001111"))
    try:
        actions.drop(1, 0, 0, 128)
        check("drop z=128 rejected", False)
    except ValueError:
        check("drop z=128 rejected", True)
    try:
        actions.lift(1, 0x10000)
        check("lift amount 0x10000 rejected", False)
    except ValueError:
        check("lift amount 0x10000 rejected", True)


def _capture_c2s(stem):
    """All C2S packets of a capture, framed with the proxy's C2S table."""
    c2s = open(f"{ROOT}/logs/{stem}.c2s.raw", "rb").read()
    key = open(f"{ROOT}/logs/{stem}.s2c.raw", "rb").read()[12]
    buf = bytearray(b ^ key for b in c2s[5:])
    pkts = []
    while buf:
        plen = packet_length(buf, overrides=C2S_OVERRIDES)
        if plen <= 0:
            break
        pkts.append(bytes(buf[:plen]))
        del buf[:plen]
    return pkts, len(buf)


def test_targets():
    print("== target_object / target_xyz / target_cancel ==")
    # ground truth: the stock client targeting the player (serial 0x94375,
    # body 0x190) under the server cursor 0x521a5 (beneficial, type 2)
    pkts, left = _capture_c2s("session_20260928_141253")
    check("capture 141253 frames end-to-end under C2S_OVERRIDES", left == 0,
          f"({left} B left)")
    targets = [p for p in pkts if p[0] == 0x6C]
    check("capture holds one 27-byte client target", [len(p) for p in targets] == [27],
          str([p.hex() for p in targets]))
    check("no phantom 0x00 packet after the target",
          not any(p[0] == 0x00 for p in pkts))
    got = actions.target_object(0x000521A5, 0x00094375, 0x793, 0xA1B, 1,
                                0x190, cursor_type=2)
    check("target_object reproduces captured target (141253)",
          bool(targets) and got == targets[0], got.hex())
    # session_20260928_164548: harmful cursor 0x52cb9
    got = actions.target_object(0x00052CB9, 0x00094375, 0x77C, 0xA24, 0,
                                0x190, cursor_type=1)
    check("target_object reproduces captured target (164548)",
          got == bytes.fromhex("6c0000052cb901000943750000077c00000a24"
                               "0000000000000190"), got.hex())
    # Send_TargetXYZ @ 0x14015d120 V10+: type 1, serial 0, u32 x/y/z/graphic
    got = actions.target_xyz(0x00052CB9, 0x77C, 0xA24, -2, graphic=0x0519)
    check("target_xyz layout",
          got == bytes.fromhex("6c01" "00052cb9" "00" "00000000" "0000077c"
                               "00000a24" "fffffffe" "00000519"), got.hex())
    # Send_TargetCancel @ 0x14015e400 V10+: 3 x 7fffffff then 0
    got = actions.target_cancel(0x00052CB9, target_type=1, cursor_type=1)
    check("target_cancel layout",
          got == bytes.fromhex("6c01" "00052cb9" "01" "00000000" "7fffffff"
                               "7fffffff" "7fffffff" "00000000"), got.hex())
    try:
        actions.target_object(1, 2, 0, 0, 0, 0, cursor_type=256)
        check("target cursor_type 256 rejected", False)
    except ValueError:
        check("target cursor_type 256 rejected", True)
    try:
        actions.target_xyz(1, 0, 0, -129)
        check("target_xyz z=-129 rejected", False)
    except ValueError:
        check("target_xyz z=-129 rejected", True)


def test_vendor():
    print("== buy_request / sell_request ==")
    # capture session_20260928_164548: 1 item from vendor 0x1e2
    check("buy_request matches capture",
          actions.buy_request(0x1E2, [(0x450A6EAC, 1)])
          == bytes.fromhex("3b000f000001e2021a450a6eac0001"))
    # Send_BuyRequest @ 0x1401744c0: empty list -> flag 00, no entries
    check("buy_request empty list",
          actions.buy_request(0x1E2, []) == bytes.fromhex("3b0008000001e200"))
    # Send_SellRequest @ 0x1401750a0: count u16, (serial u32, amount u16)*
    got = actions.sell_request(0x1E2, [(0x40000001, 2), (0x40000002, 0x10)])
    check("sell_request layout",
          got == bytes.fromhex("9f0015000001e20002" "400000010002"
                               "400000020010"), got.hex())
    try:
        actions.buy_request(1, [(2, 0x10000)])
        check("buy amount 0x10000 rejected", False)
    except ValueError:
        check("buy amount 0x10000 rejected", True)


def test_misc_actions():
    print("== skills / doors / equip / combat / popups / prompts ==")
    # Send_UseSkill @ 0x140153660: 24 + ASCII "<id> 0" + NUL
    check("use_skill 21 (hiding)",
          actions.use_skill(21) == bytes.fromhex("120009" "24" "32312030" "00"))
    # capture session_20260928_164548
    check("open_door matches capture",
          actions.open_door() == bytes.fromhex("1200055800"))
    # Send_EquipRequest @ 0x14014d570
    check("equip_request layout",
          actions.equip_request(0x40000D54, 0x02, 0x00094375)
          == bytes.fromhex("1340000d540200094375"))
    # Send_ChangeWarMode @ 0x14014dde0: flag, 32, 00, table pad to 5
    check("war_mode on/off",
          actions.war_mode(True) == bytes.fromhex("7201320000")
          and actions.war_mode(False) == bytes.fromhex("7200320000"))
    check("attack", actions.attack(0x1E2) == bytes.fromhex("05000001e2"))
    # captures session_20260928_141253 (standard BF, no 0xFF-dialect variant)
    check("request_popup matches capture",
          actions.request_popup(0x0008AAE0) == bytes.fromhex("bf000900130008aae0"))
    check("popup_selection matches capture",
          actions.popup_selection(0x0008AAE0, 2)
          == bytes.fromhex("bf000b00150008aae00002"))
    # Send_ASCIIPromptResponse @ 0x14015f470: u64 prompt data, u32 !cancel,
    # ASCII + NUL
    check("ascii_prompt_response layout",
          actions.ascii_prompt_response(0x40000001, 7, "hi")
          == bytes.fromhex("9a0012" "40000001" "00000007" "00000001" "686900"))
    check("ascii_prompt_response cancel flag 0",
          actions.ascii_prompt_response(1, 2, "", cancel=True)[11:15] == bytes(4))
    # Send_UnicodePromptResponse @ 0x14015ff50: lang ascii[3] + 00, UTF-16LE
    check("unicode_prompt_response layout",
          actions.unicode_prompt_response(0x40000001, 7, "hi")
          == bytes.fromhex("c20017" "40000001" "00000007" "00000001"
                           "454e5500" "68006900"))
    # Send_SkillsRequest @ 0x14014f420
    check("skills_request",
          actions.skills_request(0x00094375) == bytes.fromhex("34edededed0500094375"))
    try:
        actions.unicode_prompt_response(1, 2, "x", lang="EN")
        check("prompt lang of 2 chars rejected", False)
    except ValueError:
        check("prompt lang of 2 chars rejected", True)
    try:
        actions.equip_request(1, 0x100, 2)
        check("equip layer 0x100 rejected", False)
    except ValueError:
        check("equip layer 0x100 rejected", True)


def test_c2s_framing():
    print("== builders frame under the proxy's C2S table ==")
    built = {
        "lift": actions.lift(1, 1),
        "drop": actions.drop(1, 2, 3, 4),
        "target_object": actions.target_object(1, 2, 3, 4, 5, 6),
        "target_xyz": actions.target_xyz(1, 2, 3, 4),
        "target_cancel": actions.target_cancel(1),
        "gump_response": actions.gump_response(1, 2, 3, [4], [(5, "text")]),
        "text_entry_response": actions.text_entry_response(1, 2, 3, "abc"),
        "buy_request": actions.buy_request(1, [(2, 3), (4, 5)]),
        "sell_request": actions.sell_request(1, [(2, 3)]),
        "use_skill": actions.use_skill(46),
        "open_door": actions.open_door(),
        "equip_request": actions.equip_request(1, 2, 3),
        "war_mode": actions.war_mode(True),
        "attack": actions.attack(1),
        "request_popup": actions.request_popup(1),
        "popup_selection": actions.popup_selection(1, 2),
        "ascii_prompt_response": actions.ascii_prompt_response(1, 2, "abc"),
        "unicode_prompt_response": actions.unicode_prompt_response(1, 2, "abc"),
        "skills_request": actions.skills_request(1),
    }
    for name, pkt in built.items():
        # followed by a keepalive: framing must end exactly at the builder's
        # last byte, as the proxy's frame loop sees it
        n = packet_length(pkt + bytes.fromhex("ff000700000003"),
                          overrides=C2S_OVERRIDES)
        check(f"{name} frames as {len(pkt)} B", n == len(pkt), f"(got {n})")


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
         "--state-port", "12603",
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
        payloads = [
            actions.walk(6, run=True),
            actions.dclick(0x40005913),
            actions.say_unicode("howdy"),
            actions.cast_spell(15),
            actions.item_query(0x40000D54),
            actions.gump_response(0x00215AD2, 0x907FC735, 5),
            actions.lift(0x40000D54, 1),
            actions.drop(0x40000D54, 100, 200, 5),
            actions.target_object(0x00052CB9, 0x00094375, 0x77C, 0xA24, 0,
                                  0x190, cursor_type=1),
        ]
        cr, cw = await asyncio.open_connection("127.0.0.1", CONTROL_PORT)

        async def ctl_inject(p):
            cw.write(len(p).to_bytes(2, "big") + p)
            await cw.drain()
            n = int.from_bytes(await cr.readexactly(2), "big")
            return await cr.readexactly(n)

        for p in payloads:
            reply = await ctl_inject(p)
            if reply != b"OK":
                check(f"inject {p.hex()} accepted", False, reply)
        check(f"all {len(payloads)} injections accepted", True)
        # an immediate second agent walk violates step pacing (0.2 s run) and
        # is refused, never relayed
        reply = await ctl_inject(actions.walk(6, run=True))
        check("immediate second agent walk refused by pacing",
              reply.startswith(b"ERR walk gated: pacing"), reply)

        # ordering probe: real client keepalive interleaved after injections
        keepalive_plain = bytes.fromhex("ff000700000003")
        game_writer.write(bytes(b ^ SESSION_KEY for b in keepalive_plain))
        await game_writer.drain()
        await asyncio.sleep(0.5)
        # the proxy's MoveAuthority stamps the seed token (8) into the first
        # walk of the session; everything else relays byte-for-byte
        relayed = [payloads[0][:3] + (8).to_bytes(4, "big")] + payloads[1:]
        expected = b"".join(
            bytes(b ^ SESSION_KEY for b in p) for p in relayed
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
        for pid, n in {"0x02": 1, "0x06": 1, "0xAD": 1, "0xFF": 4,
                       "0xB1": 1, "0x07": 1, "0x08": 1, "0x6C": 1}.items():
            check(f"log c2s {pid} == {n}", ids.get(pid, 0) == n,
                  f"(got {ids.get(pid, 0)})")
        walk_logs = [e["hex"] for e in c2s if e["id"] == "0x02"]
        check("logged walk is the relayed plaintext",
              walk_logs == [relayed[0].hex()], str(walk_logs))
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
    # 2x east (dir 2) then 1x west (dir 6), each confirmed by the server.
    # Self moves only on confirms; a walk in a new direction only turns:
    # E turn, E move (+1), W turn -> net (+1, 0), 1 position change
    s.direction = 0
    for d in (2, 2, 6):
        pkt = seq.walk(d)
        rt.feed_packet(C2S, pkt)
        rt.feed_packet("s2c", bytes([0x22, pkt[2], 0x01]))
    rt.feed_packet(C2S, actions.cast_spell(15))
    rt.feed_packet(C2S, actions.item_query(0x40000D54))
    rt.feed_packet(C2S, actions.dclick(0x40005913))
    rt.feed_packet(C2S, actions.say_unicode("howdy"))

    check("confirmed walks moved self (+1, 0)",
          (s.x, s.y) == (x0 + 1, y0), f"({x0},{y0}) -> ({s.x},{s.y})")
    check("position_changes +1 (turns don't count)", s.position_changes == pc0 + 1,
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
    test_speech_keywords()
    test_cast_spell()
    test_item_query()
    test_gump_response()
    test_text_entry_response()
    test_lift_drop()
    test_targets()
    test_vendor()
    test_misc_actions()
    test_c2s_framing()
    print("== proxy injection ==")
    asyncio.run(injection_test())
    test_replay_actions()
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILURES: {FAILURES}"))
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())

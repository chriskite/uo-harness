"""Unit tests for the world-model parsers and runtime mechanics.

Each test encodes the contract from docs/WORLDMODEL.md (field offsets, widths,
endianness) plus ground-truth packets lifted from the captured sessions
(logs/session_20260928_141253.* and logs/session_20260928_164548.*).

Hand-crafted buffers use distinctive per-field values (0x01, 0x0203,
0x04050607, ...) so an off-by-one, wrong width, or wrong endianness in the
implementation fails loudly.

Run directly (python harness/test_world_units.py) or via harness/test_world.py.
"""
import os
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import actions
from world.parsers import PacketIncomplete, parse_packet, parse_fixed
from world.runtime import WorldRuntime

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(name)


def eq(name, got, want):
    check(name, got == want, f"(got {got!r}, want {want!r})")


# ---------------------------------------------------------------------------
# Ground-truth packets from the captures (decrypted/deflated hex)
# ---------------------------------------------------------------------------

# session_20260928_164548 13-byte server prelude = 0xFF sub-0 dialect
# handshake (version 12, S2C key 0x12, C2S key 0xE7)
PRELUDE = bytes.fromhex("ff000d000000000000000c12e7")
# session_20260928_164548 first C2S walk (doc §6: dir 0x86 = dir 6 | run 0x80)
WALK = bytes.fromhex("02860000000008")
# session_20260928_141253 C2S unicode speech "howdy"
SPEECH_HOWDY = bytes.fromhex("ad00180002b20003454e55000068006f0077006400790000")
# session_20260928_164548 C2S dialect spell casts (spell ids 15 and 5)
SPELL15 = bytes.fromhex("ff000a0000000400000f")
SPELL5 = bytes.fromhex("ff000a00000004000005")
# session_20260928_164548 C2S dialect item-detail query
ITEMQUERY = bytes.fromhex("ff000e0000000901000140000d54")
# C2S dialect keepalive (~1/s in both sessions)
KEEPALIVE = bytes.fromhex("ff000700000003")
# session_20260928_141253 C2S gump response (button 3, no switches/texts)
GUMPRESP = bytes.fromhex("b1001700215a42c16e0192000000030000000000000000")
# session_20260928_164548 C2S target response (clicked serial = player), the
# Outlands V10+ 27-byte form (u32 x/y/z/graphic)
TARGETRESP = bytes.fromhex("6c0000052cb901000943750000077c00000a240000000000000190")
# session_20260929_110455 S2C 0xC1 cliloc 1046414 "the remains of ~1_NAME~",
# args UTF-16LE "Bresh Fiscuits"
CLILOC_C1 = bytes.fromhex(
    "c1004e454e279820060600590003000ff78e" + "00" * 30 +
    "420072006500730068002000460069007300630075006900740073000000")
# session login: 0x91 head carries the account name as asciiz @3
LOGIN91 = bytes.fromhex("910462") + b"Hackworth\x00" + b"eyJhbGciOiJ9" + b"\x00" * 4
# character select: 0xEDEDEDED pattern, name ascii[30] @5
CHARSELECT = bytes.fromhex("5dedededed") + b"TestWorth\x00" + b"\x00" * 20 + b"\x00" * 38
# Real S2C packets (XOR + per-packet Huffman decode via uo/s2c.py)
# 0x11 type 5 without tithing, 87 B (session_20260928_141253)
REAL_11_87 = (
    "1100570009437554657374576f727468000000000000000000000000000000000000"
    "000000005000500005000050000f0041000f000f00410041000000000009003a023a"
    "01000000050000000000000000000000020008")
# 0xFF sub 8 buff update with empty title -> cliloc, 78 B (session_164548)
BUFF_REAL = bytes.fromhex(
    "ff004e0000000800094375002d75620000000000000001411666660000000039535b"
    "cd000000003951870d00000f7d8841726d6f7220526174696e6720496e6372656173"
    "65000000000100000000")
# 0x78 MobileEquip with one record / with none (session captures)
MOBILE_EQUIP_1 = bytes.fromhex(
    "78001a000001e9400011770000152e1804200000002000000000")
MOBILE_EQUIP_0 = bytes.fromhex("78000b0008a90500000000")
# 0x1C ASCII talk "Vorn" and 0xAE unicode talk "howdy" by TestWorth
TALK_1C = bytes.fromhex(
    "1c0031000076ed01900600590003566f726e00000000000000000000000000000000"
    "00000000000000000000566f726e00")
TALK_AE = bytes.fromhex(
    "ae003c0009437501900002b20003454e550054657374576f72746800000000000000"
    "00000000000000000000000000000068006f0077006400790000")
# 0x98 S2C name response and 0xA9 character list (5 slots x 30 B names)
NAME_98 = bytes.fromhex(
    "9800250009437554657374576f72746800000000000000000000000000000000000000"
    "0000")
CHARLIST_A9 = bytes.fromhex(
    "a900a10554657374576f727468" + "00" * 21 + "00" * 120 + "0000000008ffff")


# ---------------------------------------------------------------------------
# Fixed-layout tables: distinctive values per field
# ---------------------------------------------------------------------------

def test_fixed_s2c():
    print("== fixed S2C layouts ==")
    # 0x0B Damage (7): serial u32be@1, amount u16be@5
    f = parse_fixed(0x0B, bytes.fromhex("0b040506070809"))
    eq("0x0B", f, {"serial": 0x04050607, "amount": 0x0809})
    # 0x22 ConfirmWalk (3): seq u8@1, notoriety u8@2
    f = parse_fixed(0x22, bytes.fromhex("220a03"))
    eq("0x22", f, {"seq": 0x0A, "notoriety": 0x03})
    # 0x21 DenyWalk V10 (15): seq@1, x u32@2, y u32@6, dir u8@10 (&7),
    # z = i8 of byte 11 only; bytes 12-14 discarded
    # 0x21 DenyWalk (15), real reject (MAP.md): z is an i32 at 11
    f = parse_fixed(0x21, bytes.fromhex("21fb00000778000009fd80ffffffec"))
    eq("0x21", f, {"seq": 0xFB, "x": 0x778, "y": 0x9FD, "dir": 0x80 & 7, "z": -20})
    # 0x2D MobileAttributes (17): serial@1 then 6x u16be (max,cur pairs)
    f = parse_fixed(0x2D, bytes.fromhex("2d04050607" "0100" "0200" "0300" "0400"
                                        "0500" "0600"))
    eq("0x2D", f, {"serial": 0x04050607, "hits_max": 0x0100, "hits": 0x0200,
                   "mana_max": 0x0300, "mana": 0x0400,
                   "stam_max": 0x0500, "stam": 0x0600})
    # 0x2F Swing (10): skip@1, attacker u32@2, defender u32@6
    f = parse_fixed(0x2F, bytes.fromhex("2f000405060708090a0b"))
    eq("0x2F", f, {"attacker": 0x04050607, "defender": 0x08090A0B})
    # 0xA1/0xA2/0xA3 (9): serial@1, max u16@5, current u16@7
    for pid in (0xA1, 0xA2, 0xA3):
        f = parse_fixed(pid, bytes([pid]) + bytes.fromhex("0405060708090a0b"))
        eq(f"0x{pid:02X}", f, {"serial": 0x04050607, "max": 0x0809,
                               "current": 0x0A0B})
    # 0x72 Warmode (5): flag u8@1, 3 padding bytes unread
    f = parse_fixed(0x72, bytes.fromhex("7201003200"))
    eq("0x72", f, {"flag": 0x01})
    # 0x20 UpdatePlayer V10 (28): serial u32@1, graphic u32@5, notoriety u8@9,
    # hue u16@10, flags u8@12, x u32@13, y u32@17, zero u16@21, dir u8@23,
    # z u32@24
    f = parse_fixed(0x20, bytes.fromhex(
        "20" "04050607" "08090a0b" "0c" "0d0e" "0f"
        "10111213" "14151617" "0000" "18" "191a1b1c"))
    eq("0x20", f, {"serial": 0x04050607, "graphic": 0x08090A0B,
                   "notoriety": 0x0C, "hue": 0x0D0E, "flags": 0x0F,
                   "x": 0x10111213, "y": 0x14151617, "dir": 0x18,
                   "z": 0x191A1B1C})
    # 0xF3 UpdateItemSA V12 (38) — offsets verified against the decompiled
    # handler (UpdateItemSA @ 0x140199860): marker u16@1, type u8@3,
    # serial u32@4, graphic u32@8, inc u8@12, amount u16@13, v11 u8@15,
    # skip@16, x u32@17, y u32@21, z i32@25, dir u8@29, hue u16@30,
    # flags u32@32, tail u16@36.
    f = parse_fixed(0xF3, bytes.fromhex(
        "f3" "0001" "02" "04050607" "08090a0b" "0c" "0d0e" "0f" "10"
        "11121314" "15161718" "191a1b1c" "1d" "1e1f" "20212223" "2425"))
    eq("0xF3", f, {"data_type": 0x02, "serial": 0x04050607,
                   "graphic": 0x08090A0B, "graphic_inc": 0x0C,
                   "amount": 0x0D0E, "v11": 0x0F,
                   "x": 0x11121314, "y": 0x15161718, "z": 0x191A1B1C,
                   "dir": 0x1D, "hue": 0x1E1F, "flags": 0x20212223,
                   "tail": 0x2425})
    # z is signed: a live item below ground (session 20261002_153718) sends ffffffe7
    f = parse_fixed(0xF3, bytes(25) + bytes.fromhex("ffffffe7") + bytes(9))
    eq("0xF3 negative z", {"z": f["z"]}, {"z": -25})
    # 0x2E EquipItem V12 (20): item u32@1, graphic u32@5, inc u32@9,
    # layer u8@13, parent u32@14, hue u16@18
    f = parse_fixed(0x2E, bytes.fromhex(
        "2e" "04050607" "08090a0b" "0c0d0e0f" "10" "11121314" "1516"))
    eq("0x2E", f, {"item": 0x04050607, "graphic": 0x08090A0B,
                   "inc": 0x0C0D0E0F, "layer": 0x10, "parent": 0x11121314,
                   "hue": 0x1516})
    # 0x1D DeleteObject (5)
    f = parse_fixed(0x1D, bytes.fromhex("1d04050607"))
    eq("0x1D", f, {"serial": 0x04050607})
    # 0x24 OpenContainer (11): serial u32@1, gump id u32@5, 2 unread tail
    f = parse_fixed(0x24, bytes.fromhex("24" "04050607" "08090a0b" "ffff"))
    eq("0x24", f, {"serial": 0x04050607, "gump_id": 0x08090A0B})
    # 0x25 UpdateContainedItem V12 (27): serial u32@1, graphic u32@5, inc u8@9,
    # amount u16@10, x u16@12, y u16@14, grid u8@16, container u32@17,
    # hue u16@21, v12 u32@23
    f = parse_fixed(0x25, bytes.fromhex(
        "25" "04050607" "08090a0b" "0c" "0d0e" "0f10" "1112" "13"
        "14151617" "1819" "1a1b1c1d"))
    eq("0x25", f, {"serial": 0x04050607, "graphic": 0x08090A0B, "inc": 0x0C,
                   "amount": 0x0D0E, "x": 0x0F10, "y": 0x1112, "grid": 0x13,
                   "container": 0x14151617, "hue": 0x1819, "v12": 0x1A1B1C1D})
    # 0x6C TargetCursor S2C (27): type u8@1, cursor id u32@2, cursor type u8@6,
    # 12 unused + 8 Outlands-extension bytes unread
    f = parse_fixed(0x6C, bytes.fromhex(
        "6c" "01" "04050607" "02" "00" * 12 + "00" * 8))
    eq("0x6C", f, {"target_type": 0x01, "cursor_id": 0x04050607,
                   "cursor_type": 0x02})
    # a cursor is up only for cursor_type < 3; type 3 is the server cancelling it
    # (ClassicUO TargetManager; live 2026-09-30 after a moongate Travel: status showed it as up)
    rt = WorldRuntime()
    rt.feed_packet("s2c", bytes.fromhex("6c" "01" "04050607" "02" + "00" * 20))
    up = rt.state.target.active
    rt.feed_packet("s2c", bytes.fromhex("6c" "00" "00000000" "03" + "00" * 20))
    eq("0x6C: type 2 is up, type 3 (cancel) takes it down", (up, rt.state.target.active), (True, False))
    # 0x6E CharacterAnimation (14)
    f = parse_fixed(0x6E, bytes.fromhex(
        "6e" "04050607" "0809" "0a0b" "0c0d" "01" "00" "05"))
    eq("0x6E", f, {"serial": 0x04050607, "action": 0x0809, "frames": 0x0A0B,
                   "repeat": 0x0C0D, "backward": 1, "repeat_flag": 0,
                   "delay": 5})
    # 0x1B LoginConfirm (43), real packet from session_20260929_144541:
    # serial u32@1, unread u32@5, graphic u32@9, x/y u32@13/17, z i32@21,
    # dir u8@25; bytes 26..42 unread
    f = parse_fixed(0x1B, bytes.fromhex(
        "1b" "00094375" "00000000" "00000190" "000007ab" "00000a25"
        "00000000" "80" "00ffffffff" "00000000" "2a00" "1800" "00000000"))
    eq("0x1B real", f, {"serial": 0x00094375, "graphic": 0x190,
                        "x": 0x7AB, "y": 0xA25, "z": 0, "dir": 0x80})
    # 0x20 real self sample (same session) and z as a signed 32-bit int
    f = parse_fixed(0x20, bytes.fromhex(
        "20" "00094375" "00000190" "01" "83ea" "20" "000007ab" "00000a25"
        "0000" "80" "00000000"))
    eq("0x20 real self", f, {"serial": 0x00094375, "graphic": 0x190,
                             "notoriety": 1, "hue": 0x83EA, "flags": 0x20,
                             "x": 0x7AB, "y": 0xA25, "dir": 0x80, "z": 0})
    f = parse_fixed(0x20, bytes.fromhex("20" + "00" * 23 + "fffffffb"))
    eq("0x20 negative z", f["z"], -5)
    # 0x77 MobileMove V10 (18): serial u32@1, x/y u32@5/9, z i32@13, dir u8@17
    f = parse_fixed(0x77, bytes.fromhex(
        "77" "00094375" "000007ab" "00000a25" "fffffffe" "85"))
    eq("0x77", f, {"serial": 0x00094375, "x": 0x7AB, "y": 0xA25, "z": -2,
                   "dir": 0x85})
    # no layout registered -> None
    eq("0x99 no layout", parse_fixed(0x99, b"\x99\x01\x02"), None)


def test_fixed_c2s():
    print("== fixed C2S layouts ==")
    # 0x02 walk (7): dir u8@1, seq u8@2, key u32be@3
    f = parse_fixed(0x02, bytes.fromhex("020a0b04050607"), "c2s")
    eq("C2S 0x02", f, {"dir": 0x0A, "seq": 0x0B, "key": 0x04050607})
    # 0x06 dclick (5) / 0x09 query (5): serial u32@1
    for pid in (0x06, 0x09):
        f = parse_fixed(pid, bytes([pid]) + bytes.fromhex("04050607"), "c2s")
        eq(f"C2S 0x{pid:02X}", f, {"serial": 0x04050607})
    # 0x34 query (10): pattern u32@1, type u8@5, serial u32@6
    f = parse_fixed(0x34, bytes.fromhex("34edededed0500094375"), "c2s")
    eq("C2S 0x34", f, {"pattern": 0xEDEDEDED, "type": 0x05,
                       "serial": 0x00094375})
    # 0x98 query (7): len u16@1, serial u32@3
    f = parse_fixed(0x98, bytes.fromhex("98000700094375"), "c2s")
    eq("C2S 0x98", f, {"serial": 0x00094375})
    # 0x5D PlayCharacter (73): pattern u32@1, name ascii[30]@5
    f = parse_fixed(0x5D, CHARSELECT, "c2s")
    eq("C2S 0x5D", f, {"name": "TestWorth"})
    # 0x6C target response C2S (27): type@1, cursor u32@2, ctype@6,
    # serial u32@7, x u32@11, y u32@15, z i32@19, graphic u32@23
    f = parse_packet("c2s", TARGETRESP)
    eq("C2S 0x6C", f, {"target_type": 0, "cursor_id": 0x00052CB9,
                       "cursor_type": 1, "serial": 0x00094375,
                       "x": 0x077C, "y": 0x0A24, "z": 0, "graphic": 0x0190})
    # lift / drop / equip: the actions builders (decompile-grounded senders)
    # must parse back field for field
    eq("C2S 0x07", parse_packet("c2s", actions.lift(0x04050607, 0x0809)),
       {"serial": 0x04050607, "amount": 0x0809})
    eq("C2S 0x08", parse_packet("c2s", actions.drop(
        0x04050607, 0x0809, 0x0A0B, -3, 0x0C, 0x0D0E0F10)),
       {"serial": 0x04050607, "x": 0x0809, "y": 0x0A0B, "z": -3, "grid": 0x0C,
        "container": 0x0D0E0F10})
    eq("C2S 0x13", parse_packet("c2s", actions.equip_request(0x04050607, 0x02, 0x08090A0B)),
       {"serial": 0x04050607, "layer": 0x02, "container": 0x08090A0B})


# ---------------------------------------------------------------------------
# Procedural parsers
# ---------------------------------------------------------------------------

def test_character_status_11():
    print("== 0x11 CharacterStatus (type-gated) ==")
    name30 = b"Name!" + b"\x00" * 25
    head = (b"\x11" + b"\x00\x00" + bytes.fromhex("04050607") + name30 +
            bytes.fromhex("0100") + bytes.fromhex("0200") + b"\x01")

    def pkt(body):
        out = head + body
        return out[:1] + len(out).to_bytes(2, "big") + out[3:]

    # type 0: packet ends after the type byte (total 43)
    eq("0x11 type0 len", len(pkt(b"\x00")), 43)
    f = parse_packet("s2c", pkt(b"\x00"))
    eq("0x11 type0", f, {"serial": 0x04050607, "name": "Name!",
                         "hits": 0x0100, "hits_max": 0x0200,
                         "renamable": 1, "type": 0})
    # type 5: female, str/dex/int, stam/mana pairs, gold, physres, weight,
    # then type>=5 (weightmax, race), type>=3 (statscap, followers) and
    # type>=4 (resists, luck, damage, tithing) blocks in doc offset order
    body5 = (b"\x05" b"\x00"                              # type, female
             + bytes.fromhex("0064" "0032" "0046")        # str dex int
             + bytes.fromhex("0011" "0012")               # stam, stam_max
             + bytes.fromhex("0013" "0014")               # mana, mana_max
             + bytes.fromhex("00010002")                  # gold
             + bytes.fromhex("0015")                      # physical resist
             + bytes.fromhex("0016")                      # weight
             + bytes.fromhex("0017")                      # weight max (>=5)
             + b"\x03"                                    # race (>=5)
             + bytes.fromhex("00fa")                      # stats cap (>=3)
             + b"\x04\x05"                                # followers (>=3)
             + bytes.fromhex("0021" "0022" "0023" "0024")  # resists (>=4)
             + bytes.fromhex("0025")                      # luck
             + bytes.fromhex("0026" "0027")               # damage min/max
             + bytes.fromhex("00000028"))                 # tithing
    f = parse_packet("s2c", pkt(body5))
    want = {"serial": 0x04050607, "name": "Name!", "hits": 0x0100,
            "hits_max": 0x0200, "renamable": 1, "type": 5, "female": 0,
            "str": 100, "dex": 50, "int": 70, "stam": 0x11, "stam_max": 0x12,
            "mana": 0x13, "mana_max": 0x14, "gold": 0x00010002,
            "physical_resist": 0x15, "weight": 0x16, "weight_max": 0x17,
            "race": 3, "stats_cap": 0xFA, "followers": 4, "followers_max": 5,
            "fire_resist": 0x21, "cold_resist": 0x22, "poison_resist": 0x23,
            "energy_resist": 0x24, "luck": 0x25, "damage_min": 0x26,
            "damage_max": 0x27, "tithing": 0x28}
    eq("0x11 type5", f, want)
    # type 6: trailing block of 15 x u16be
    tail = b"\x06" + body5[1:] + b"".join(
        (0x30 + i).to_bytes(2, "big") for i in range(15))
    f = parse_packet("s2c", pkt(tail))
    eq("0x11 type6 block", [f[f"extra_{i}"] for i in range(15)],
       [0x30 + i for i in range(15)])
    # real type-5 form without the tithing u32 (87 B, session_20260928_141253)
    real87 = bytes.fromhex(REAL_11_87)
    eq("0x11 real87 len", len(real87), 87)
    f = parse_packet("s2c", real87)
    check("0x11 real87 parsed without tithing",
          f["name"] == "TestWorth" and f["str"] == 80 and f["int"] == 65
          and f["weight_max"] == 570 and f["followers_max"] == 5
          and f["damage_max"] == 8 and "tithing" not in f, str(f))
    rt = WorldRuntime()
    rt.state.self.serial = f["serial"]
    rt.feed_packet("s2c", real87)
    st = rt.state.self.to_dict()["stats"]
    eq("0x11 for self: Str/Dex/Int exported in the snapshot's stats",
       (st.get("str"), st.get("dex"), st.get("int"), rt.state.self.to_dict()["weight"]),
       (f["str"], f["dex"], f["int"], f["weight"]))


def test_skills_3a():
    print("== 0x3A UpdateSkills ==")
    # type 0: full list, no caps, id 0 terminates; ids are wire-1 (decremented)
    pkt = bytes.fromhex("3a" "0014" "00" "0005" "0258" "0230" "01"
                        "0007" "00c8" "0064" "00" "0000")
    f = parse_packet("s2c", pkt)
    eq("0x3A type0", f, {"type": 0, "skills": [
        {"id": 4, "value": 600, "base": 560, "lock": 1},
        {"id": 6, "value": 200, "base": 100, "lock": 0}]})
    # type 1: full with caps
    pkt = bytes.fromhex("3a" "000d" "01" "0002" "0258" "0230" "02" "03e8")
    f = parse_packet("s2c", pkt)
    # id NOT decremented for type 1 (only types 0/2 decrement — upstream CUO
    # UpdateSkills PacketHandlers.cs:2004 and doc §1 agree)
    eq("0x3A type1", f, {"type": 1, "skills": [
        {"id": 2, "value": 600, "base": 560, "lock": 2, "cap": 1000}]})
    # type 0xFF: single update, id NOT decremented, no cap
    pkt = bytes.fromhex("3a" "000b" "ff" "0009" "0258" "0230" "01")
    f = parse_packet("s2c", pkt)
    eq("0x3A single", f, {"type": 0xFF, "skills": [
        {"id": 9, "value": 600, "base": 560, "lock": 1}]})
    # type 0xDF: single with cap
    pkt = bytes.fromhex("3a" "000d" "df" "0009" "0258" "0230" "01" "03e8")
    f = parse_packet("s2c", pkt)
    eq("0x3A single cap", f, {"type": 0xDF, "skills": [
        {"id": 9, "value": 600, "base": 560, "lock": 1, "cap": 1000}]})
    # type 0xFE: skill-name table: u16 count, then (i8 haveButton, u8 len, name)
    names = bytes([1, 5]) + b"Melee" + bytes([0, 6]) + b"Taming"
    pkt = bytes.fromhex("3a") + (3 + 1 + 2 + len(names)).to_bytes(2, "big") + \
        bytes([0xFE]) + (2).to_bytes(2, "big") + names
    f = parse_packet("s2c", pkt)
    eq("0x3A names", f, {"type": 0xFE, "names": [
        {"have_button": 1, "name": "Melee"},
        {"have_button": 0, "name": "Taming"}]})
    # type 2 (full list with caps) as real servers send it: records then a
    # trailing u16 0 (session_20260928_141253 492-byte form, shortened)
    pkt = bytes.fromhex("3a" "0016" "02" "0001" "0000" "0000" "00" "03e8"
                        "000b" "0258" "0258" "00" "03e8" "0000")
    f = parse_packet("s2c", pkt)
    eq("0x3A type2 real form", f, {"type": 2, "skills": [
        {"id": 0, "value": 0, "base": 0, "lock": 0, "cap": 1000},
        {"id": 10, "value": 600, "base": 600, "lock": 0, "cap": 1000}]})


def test_world_item_1a():
    print("== 0x1A UpdateItem (bit flags) ==")
    # bare form: no amount, no graphic offset, no dir, no hue, no flags
    pkt = bytes.fromhex("1a" "000e" "04050607" "0809" "0102" "0304" "7f")
    f = parse_packet("s2c", pkt)
    eq("0x1A bare", f, {"serial": 0x04050607, "graphic": 0x0809, "amount": 1,
                        "x": 0x0102, "y": 0x0304, "z": 0x7F})
    # full form: serial bit31 -> amount; graphic bit15 -> offset byte;
    # graphic bit14 -> subtype; x bit15 -> dir; y bit15 -> hue, bit14 -> flags
    pkt = bytes.fromhex(
        "1a" "0015"
        "84050607"      # serial | bit31
        "c809"          # graphic | bit15 | bit14 -> graphic 0x0809, type 2
        "0e"            # graphic offset
        "00ff"          # amount
        "8102"          # x | bit15 -> 0x0102
        "c304"          # y | bit15 | bit14 -> 0x0304
        "06"            # direction
        "fe"            # z (i8 -2)
        "0a0b"          # hue
        "0c"            # flags
    )
    f = parse_packet("s2c", pkt)
    eq("0x1A full", f, {"serial": 0x04050607, "graphic": 0x0809, "subtype": 2,
                        "graphic_offset": 0x0E, "amount": 0xFF,
                        "x": 0x0102, "y": 0x0304, "dir": 0x06, "z": -2,
                        "hue": 0x0A0B, "flags": 0x0C})


def test_container_content_3c():
    print("== 0x3C UpdateContainedItems ==")
    # count 0
    pkt = bytes.fromhex("3c" "0005" "0000")
    eq("0x3C empty", parse_packet("s2c", pkt), {"items": []})
    # two 26-byte V12 records
    rec1 = ("04050607" "08090a0b" "0c" "0d0e" "0f10" "1112" "13"
            "14151617" "1819" "1a1b1c1d")
    rec2 = ("14050607" "18090a0b" "1c" "1d1e" "1f20" "2122" "23"
            "24252627" "2829" "2a2b2c2d")
    body = "0002" + rec1 + rec2
    pkt = bytes.fromhex("3c") + (3 + len(bytes.fromhex(body))).to_bytes(2, "big") \
        + bytes.fromhex(body)
    f = parse_packet("s2c", pkt)
    eq("0x3C items", f, {"items": [
        {"serial": 0x04050607, "graphic": 0x08090A0B, "v11": 0x0C,
         "amount": 0x0D0E, "x": 0x0F10, "y": 0x1112, "grid": 0x13,
         "container": 0x14151617, "hue": 0x1819, "v12": 0x1A1B1C1D},
        {"serial": 0x14050607, "graphic": 0x18090A0B, "v11": 0x1C,
         "amount": 0x1D1E, "x": 0x1F20, "y": 0x2122, "grid": 0x23,
         "container": 0x24252627, "hue": 0x2829, "v12": 0x2A2B2C2D}]})


def test_corpse_equipment_89():
    print("== 0x89 CorpseEquipment ==")
    # corpse serial, then (layer, serial) pairs; layer 0x16 skipped; 0 ends
    pkt = bytes.fromhex("89" "0017" "04050607"
                        "01" "10111213" "16" "20212223" "05" "30313233" "00")
    f = parse_packet("s2c", pkt)
    eq("0x89", f, {"corpse": 0x04050607, "equipment": [
        {"layer": 0x01, "serial": 0x10111213},
        {"layer": 0x05, "serial": 0x30313233}]})


def test_healthbar_16_17():
    print("== 0x16/0x17 NewHealthbarUpdate ==")
    pkt = bytes.fromhex("16" "000f" "04050607" "0002" "0001" "01" "0002" "00")
    f = parse_packet("s2c", pkt)
    eq("0x16", f, {"serial": 0x04050607, "entries": [
        {"type": 1, "enabled": 1}, {"type": 2, "enabled": 0}]})
    pkt = bytes.fromhex("17" "0009" "04050607" "0000")
    eq("0x17 empty", parse_packet("s2c", pkt),
       {"serial": 0x04050607, "entries": []})


def test_gumps_b0_dd():
    print("== 0xB0 OpenGump / 0xDD CompressedGump ==")
    layout = b"{ page 0 }{ button 10 10 1 2 }"
    line1 = "hello".encode("utf-16-be")
    line2 = "world".encode("utf-16-be")
    lines = (2).to_bytes(2, "big") + len(line1).to_bytes(2, "big") + line1 + \
        len(line2).to_bytes(2, "big") + line2
    body = ("04050607" "08090a0b" "00000064" "00000032") + \
        len(layout).to_bytes(2, "big").hex() + layout.hex() + lines.hex()
    pkt = bytes.fromhex("b0") + (3 + len(bytes.fromhex(body))).to_bytes(2, "big") \
        + bytes.fromhex(body)
    f = parse_packet("s2c", pkt)
    eq("0xB0", f, {"serial": 0x04050607, "gump_id": 0x08090A0B,
                   "x": 100, "y": 50, "layout": "{ page 0 }{ button 10 10 1 2 }",
                   "lines": ["hello", "world"]})
    # malformed layout bytes must not raise; decode is best-effort
    bad = bytes.fromhex("b0" "001a" "04050607" "08090a0b" "00000000" "00000000"
                        "0003" "fffe00" "0000")
    f = parse_packet("s2c", bad)
    check("0xB0 garbage layout no crash", isinstance(f["layout"], str))
    # 0xDD (upstream CompressedGump): zlib layout block, u32 line count,
    # then (if count > 0) a zlib block of (u16be char count, UTF-16BE) lines
    comp_layout = zlib.compress(layout)
    raw_lines = (5).to_bytes(2, "big") + "hello".encode("utf-16-be") + \
        (5).to_bytes(2, "big") + "world".encode("utf-16-be")
    comp_lines = zlib.compress(raw_lines)
    head = ("04050607" "08090a0b" "00000064" "00000032") + \
        (4 + len(comp_layout)).to_bytes(4, "big").hex() + \
        len(layout).to_bytes(4, "big").hex() + comp_layout.hex()

    def dd(payload):
        return bytes.fromhex("dd") + \
            (3 + len(bytes.fromhex(payload))).to_bytes(2, "big") + \
            bytes.fromhex(payload)
    pkt = dd(head + "00000002" +
             (4 + len(comp_lines)).to_bytes(4, "big").hex() +
             len(raw_lines).to_bytes(4, "big").hex() + comp_lines.hex() +
             "00000000")
    f = parse_packet("s2c", pkt)
    eq("0xDD", f, {"serial": 0x04050607, "gump_id": 0x08090A0B,
                   "x": 100, "y": 50,
                   "layout": "{ page 0 }{ button 10 10 1 2 }",
                   "lines": ["hello", "world"], "compressed": True})
    f = parse_packet("s2c", dd(head + "00000000"))
    eq("0xDD no lines", f["lines"], [])


def test_dialect_ff():
    print("== 0xFF dialect ==")
    # ground truth: 13-byte sub-0 prelude handshake -> version 12,
    # flag1/flag2 = S2C/C2S XOR keys 0x12/0xE7 (session_20260928_164548)
    f = parse_packet("s2c", PRELUDE)
    eq("FF sub0 prelude", f, {"sub": 0, "version": 12, "flag1": 0x12,
                              "flag2": 0xE7})
    # sub 3 S2C: u64be timestamp
    f = parse_packet("s2c", bytes.fromhex("ff" "000f" "00000003"
                                          "0102030405060708"))
    eq("FF sub3 s2c", f, {"sub": 3, "timestamp": 0x0102030405060708})
    # sub 9 S2C: remove buff = serial u32be + buff id u16be
    f = parse_packet("s2c", bytes.fromhex("ff" "000d" "00000009"
                                          "04050607" "0809"))
    eq("FF sub9 s2c", f, {"sub": 9, "serial": 0x04050607, "buff_id": 0x0809})
    # sub 0x15 S2C: name response: mode u8, count u16, (serial, asciiz)*
    f = parse_packet("s2c", bytes.fromhex(
        "ff" "001d" "00000015" "00" "0002"
        "04050607" "537765617400"        # "Sweat\0"
        "08090a0b" "50616e7400"))        # "Pant\0"
    eq("FF sub15 names", f, {"sub": 0x15, "mode": 0, "entries": [
        {"serial": 0x04050607, "name": "Sweat"},
        {"serial": 0x08090A0B, "name": "Pant"}]})
    # sub 8 S2C: buff update — 12-byte timers (f32 BE seconds, u64 end)
    title = b"BuffTitle\x00"
    desc = b"BuffDesc\x00"
    payload = ("04050607" "0809" "0001" "0002" "0003" "0004"
               "0001"                              # 1 timer
               "3f400000"                          # f32 BE 0.75
               "0102030405060708"                  # u64be end ts
               "0d0e0f1011121314"                  # u64be timestamp
               ) + title.hex() + desc.hex() + "0005" "0006" "3f800000"
    pkt = bytes.fromhex("ff") + (7 + len(bytes.fromhex(payload))).to_bytes(2, "big") \
        + bytes.fromhex("00000008") + bytes.fromhex(payload)
    f = parse_packet("s2c", pkt)
    want = {"sub": 8, "serial": 0x04050607, "icon_id": 0x0809,
            "f1": 1, "f2": 2, "f3": 3, "f4": 4,
            "timers": [{"seconds": 0.75, "end": 0x0102030405060708}],
            "timestamp": 0x0D0E0F1011121314,
            "title": "BuffTitle", "description": "BuffDesc",
            "category": 5, "mode": 6, "scalar": 1.0}
    eq("FF sub8 buff", f, want)
    # real sub 8 with empty title -> cliloc (session_20260928_164548, 78 B)
    f = parse_packet("s2c", BUFF_REAL)
    check("FF sub8 real cliloc form",
          f["serial"] == 0x00094375 and f["icon_id"] == 0x2D
          and abs(f["timers"][0]["seconds"] - 9.4) < 1e-5
          and f["title"] == "" and f["cliloc"] == 1015176
          and f["description"] == "Armor Rating Increase"
          and f["mode"] == 1 and f["scalar"] == 0.0, str(f))
    # unknown sub: reported, not fatal
    f = parse_packet("s2c", bytes.fromhex("ff" "0009" "00000063" "aabb"))
    eq("FF unknown sub", f, {"sub": 0x63})
    # ground truth C2S subs
    eq("FF c2s keepalive", parse_packet("c2s", KEEPALIVE), {"sub": 3})
    eq("FF c2s spell 15", parse_packet("c2s", SPELL15),
       {"sub": 4, "flag": 0, "spell_id": 15})
    eq("FF c2s spell 5", parse_packet("c2s", SPELL5),
       {"sub": 4, "flag": 0, "spell_id": 5})
    eq("FF c2s item query", parse_packet("c2s", ITEMQUERY),
       {"sub": 9, "serial": 0x40000D54})


def test_c2s_procedural():
    print("== C2S procedural ==")
    # ground truth: "howdy" unicode speech
    f = parse_packet("c2s", SPEECH_HOWDY)
    eq("AD howdy", f, {"type": 0, "hue": 0x02B2, "font": 3, "lang": "ENU\x00",
                       "text": "howdy"})
    # ground truth: the stock client's keyword-encoded "bank" (session
    # 20260929_161433): type|0xC0, ids [2, 2] (speech.mul lists *bank* twice), UTF-8
    f = parse_packet("c2s", bytes.fromhex("ad0016c002b20003454e5500002002002062616e6b00"))
    eq("AD bank keyword-encoded", f, {"type": 0, "hue": 0x02B2, "font": 3, "lang": "ENU\x00",
                                      "keywords": [2, 2], "text": "bank"})
    # ground truth: gump response, button 3, no switches/text
    f = parse_packet("c2s", GUMPRESP)
    eq("B1 ground", f, {"serial": 0x00215A42, "gump_id": 0xC16E0192,
                        "button_id": 3, "switches": [], "texts": []})
    # B1 with switches and text entries (text length = UTF-16 units)
    text = "abc".encode("utf-16-be")
    body = ("04050607" "08090a0b" "0000002a"
            "00000002" "00000001" "00000002"
            "00000001" "0009" + (len(text) // 2).to_bytes(2, "big").hex()
            ) + text.hex()
    pkt = bytes.fromhex("b1") + (3 + len(bytes.fromhex(body))).to_bytes(2, "big") \
        + bytes.fromhex(body)
    f = parse_packet("c2s", pkt)
    eq("B1 full", f, {"serial": 0x04050607, "gump_id": 0x08090A0B,
                      "button_id": 0x2A, "switches": [1, 2],
                      "texts": [{"id": 9, "text": "abc"}]})
    # the client's own encoder (actions.gump_response) parses back
    f = parse_packet("c2s", actions.gump_response(1, 2, 3, [4], [(5, "1234"), (6, "")]))
    eq("B1 round trip", (f["switches"], f["texts"]),
       ([4], [{"id": 5, "text": "1234"}, {"id": 6, "text": ""}]))
    # ground truth (session_20260928_141253): text entry with length 0
    f = parse_packet("c2s", bytes.fromhex(
        "b1001b00215ab36b14722700000000000000000000000100020000"))
    eq("B1 zero-length text", f["texts"], [{"id": 2, "text": ""}])
    # ground truth: 0x91 login, account asciiz @3
    f = parse_packet("c2s", LOGIN91)
    eq("91 account", f, {"account": "Hackworth"})
    # ground truth: walk dir/seq/key
    f = parse_packet("c2s", WALK)
    eq("02 walk ground", f, {"dir": 0x86, "seq": 0, "key": 8})


# ---------------------------------------------------------------------------
# Edge / boundary behavior
# ---------------------------------------------------------------------------

def test_truncation():
    print("== truncation ==")
    truncated = [
        ("s2c", bytes.fromhex("0b04050607")),          # 0x0B missing amount byte
        ("s2c", bytes.fromhex("20" "04050607")),       # 0x20 head only
        ("s2c", bytes.fromhex("f3" "0001" "02")),      # 0xF3 head only
        ("s2c", bytes.fromhex("11" "002b" "0405")),    # 0x11 mid-serial
        ("s2c", bytes.fromhex("3a" "0013" "00" "0005" "02")),  # 0x3A mid-record
        ("s2c", bytes.fromhex("78" "001a" "000001e9" "40001177" "0000")),  # 0x78
        ("s2c", bytes.fromhex("1a" "0018" "84050607")),   # 0x1A flags promise more
        ("s2c", bytes.fromhex("3c" "001f" "0002" "0405")),  # 0x3C short record
        ("s2c", bytes.fromhex("89" "0014" "04050607" "01")),  # 0x89 mid-pair
        ("s2c", bytes.fromhex("16" "000e" "04050607" "0002")),  # 0x16 short
        ("s2c", bytes.fromhex("b0" "0030" "04050607")),  # 0xB0 truncated
        ("s2c", bytes.fromhex("dd" "0030" "04050607")),  # 0xDD truncated
        ("s2c", bytes.fromhex("ff" "000f" "00000003" "0102")),  # sub3 short
        ("c2s", bytes.fromhex("ad" "0018" "00" "02b2")),  # speech truncated
        ("c2s", bytes.fromhex("b1" "0028" "04050607")),  # B1 truncated
        ("s2c", bytes.fromhex("c1" "0030" "04050607" "0809")),  # 0xC1 head only
        # count=max with no payload must fail cleanly, not hang or allocate
        ("s2c", bytes.fromhex("3c" "0005" "ffff")),
        ("s2c", bytes.fromhex("16" "0009" "04050607" "ffff")),
    ]
    rt = WorldRuntime()
    for direction, pkt in truncated:
        raised = None
        try:
            parse_packet(direction, pkt)
        except PacketIncomplete:
            raised = "incomplete"
        except Exception as exc:  # noqa: BLE001 - any other exception is a bug
            raised = f"{type(exc).__name__}: {exc}"
        check(f"truncated {pkt[0]:02x} signals incomplete", raised == "incomplete",
              f"(raised {raised!r})")
        before = rt.parse_failures
        rt.feed_packet(direction, pkt)  # must not raise
        eq(f"runtime survives truncated {pkt[0]:02x}", rt.parse_failures,
           before + 1)


def test_runtime_edges():
    print("== runtime edges ==")
    rt = WorldRuntime()
    # unknown packet id: logged, not fatal, no event
    rt.feed_packet("s2c", b"\xfe\x01\x02\x03")
    eq("unknown id counted", rt.unhandled[("s2c", 0xFE)], 1)
    eq("unknown id no event", rt.drain_events(), [])
    # empty drain
    eq("empty drain", rt.drain_events(), [])
    # last-write-wins: same item serial updated twice, second hue wins
    item_v1 = bytes.fromhex("1a" "0011" "04050607" "0809" "0102" "c304" "7f"
                            "0a0b" "0c")
    item_v2 = bytes.fromhex("1a" "0011" "04050607" "0809" "0102" "c304" "7f"
                            "0d0e" "0c")
    rt.feed_packet("s2c", item_v1)
    rt.feed_packet("s2c", item_v2)
    eq("LWW hue", rt.state.items[0x04050607].hue, 0x0D0E)
    rt.drain_events()
    # lazy name merge, name AFTER entity sighting
    rt.feed_packet("s2c", bytes.fromhex(
        "ff" "0013" "00000015" "00" "0001" "04050607" "4c61746500"))  # "Late"
    eq("name after sighting", rt.state.items[0x04050607].name, "Late")
    # lazy name merge, name BEFORE entity sighting
    rt.feed_packet("s2c", bytes.fromhex(
        "ff" "0014" "00000015" "00" "0001" "090a0b0c" "4561726c7900"))  # Early
    item_early = bytes.fromhex("1a" "000e" "090a0b0c" "0809" "0102" "0304" "00")
    rt.feed_packet("s2c", item_early)
    eq("name before sighting", rt.state.items[0x090A0B0C].name, "Early")
    # vitals without a position (0x2D) only update a mobile the client has
    # (ClassicUO World.Get): an unknown serial is ignored
    rt.feed_packet("s2c", bytes.fromhex("2d" "01020304" "0064" "0032"
                                        "000a" "0005" "0014" "000a"))
    eq("0x2D for an unknown mobile creates nothing", 0x01020304 in rt.state.mobiles, False)
    rt.feed_packet("s2c", bytes.fromhex("77" "01020304" "00000010" "00000010" "00000000" "00"))
    # duplicate serial mobile updates (0x2D twice): last wins
    rt.feed_packet("s2c", bytes.fromhex("2d" "01020304" "0064" "0032"
                                        "000a" "0005" "0014" "000a"))
    rt.feed_packet("s2c", bytes.fromhex("2d" "01020304" "0064" "0063"
                                        "000a" "0005" "0014" "000a"))
    eq("LWW mobile hits", rt.state.mobiles[0x01020304].hits, 0x63)


def test_mobile_parsers():
    print("== 0x78 / 0x1C / 0xAE / 0x98 / 0xA9 (real packets) ==")
    eq("0x78 one record", parse_packet("s2c", MOBILE_EQUIP_1),
       {"serial": 0x1E9, "equipment": [
           {"serial": 0x40001177, "graphic": 0x152E, "layer": 0x18,
            "hue": 0x0420, "v12": 0x20}]})
    eq("0x78 no records", parse_packet("s2c", MOBILE_EQUIP_0),
       {"serial": 0x0008A905, "equipment": []})
    eq("0x1C", parse_packet("s2c", TALK_1C),
       {"serial": 0x76ED, "graphic": 0x190, "type": 6, "hue": 0x59,
        "font": 3, "name": "Vorn", "text": "Vorn"})
    eq("0xAE", parse_packet("s2c", TALK_AE),
       {"serial": 0x00094375, "graphic": 0x190, "type": 0, "hue": 0x02B2,
        "font": 3, "lang": "ENU", "name": "TestWorth", "text": "howdy"})
    eq("0x98 s2c", parse_packet("s2c", NAME_98),
       {"serial": 0x00094375, "name": "TestWorth"})
    f = parse_packet("s2c", CHARLIST_A9)
    eq("0xA9 len", len(CHARLIST_A9), 161)
    eq("0xA9", f, {"names": ["TestWorth", "", "", "", ""]})


def test_cliloc():
    print("== 0xC1 / 0xCC cliloc ==")
    eq("0xC1 real", parse_packet("s2c", CLILOC_C1),
       {"serial": 0x454E2798, "graphic": 0x2006, "type": 6, "hue": 0x0059,
        "font": 3, "cliloc": 1046414, "affix_flags": 0, "name": "",
        "affix": "", "args": "Bresh Fiscuits"})
    # 0xCC: affix flags u8 after the number, affix asciiz after the name,
    # args UTF-16BE
    args = "a\tb".encode("utf-16-be")
    body = ("04050607" "0809" "0a" "0b0c" "0d0e" "000f4240" "02"
            + b"Name".ljust(30, b"\x00").hex() + b" x\x00".hex() + args.hex())
    pkt = bytes.fromhex("cc") + (3 + len(bytes.fromhex(body))).to_bytes(2, "big") \
        + bytes.fromhex(body)
    eq("0xCC", parse_packet("s2c", pkt),
       {"serial": 0x04050607, "graphic": 0x0809, "type": 0x0A, "hue": 0x0B0C,
        "font": 0x0D0E, "cliloc": 1000000, "affix_flags": 2, "name": "Name",
        "affix": " x", "args": "a\tb"})
    rt = WorldRuntime()
    rt.feed_packet("s2c", CLILOC_C1)
    ev = rt.drain_events()
    eq("cliloc event", [(e["ev"], e["cliloc"], e["args"]) for e in ev],
       [("cliloc", 1046414, "Bresh Fiscuits")])
    from uo import cliloc
    table = {1: "the remains of ~1_NAME~", 2: "~2_B~ then ~1_A~", 3: "Iron",
             4: "made of ~1_MAT~"}
    eq("translate one arg", cliloc.translate(table, 1, "Bresh"), "the remains of Bresh")
    eq("translate arg order", cliloc.translate(table, 2, "x\ty"), "y then x")
    eq("translate #ref arg", cliloc.translate(table, 4, "#3"), "made of Iron")
    eq("translate missing arg", cliloc.translate(table, 2, "x"), " then x")
    eq("translate unknown number", cliloc.translate(table, 99, "q"), "#99 [q]")
    raw = (b"\x02\x00\x00\x00\x01\x00" + (500000).to_bytes(4, "little") + b"\x00"
           + (3).to_bytes(2, "little") + b"abc")
    eq("parse table", cliloc.parse(raw), {500000: "abc"})


def test_vendor_popup_command():
    print("== 0x74 / 0x3B / 0x12 / 0xBF context menu (real packets) ==")
    # session_20260928_164548: Dusty the cook's buy list and the buy attempt
    eq("0x74", parse_packet("s2c", bytes.fromhex(
        "7400324029604f030000001908536b696c6c657400000000190c526f6c6c696e67"
        "2050696e00000000030743686565736500")),
       {"container": 0x4029604F, "items": [
           {"price": 25, "name": "Skillet"}, {"price": 25, "name": "Rolling Pin"},
           {"price": 3, "name": "Cheese"}]})
    eq("C2S 0x3B", parse_packet("c2s", bytes.fromhex("3b000f000001e2021a450a6eac0001")),
       {"vendor": 0x1E2, "flag": 2,
        "items": [{"layer": 0x1A, "serial": 0x450A6EAC, "amount": 1}]})
    eq("C2S 0x3B builder", parse_packet("c2s", actions.buy_request(0x1E2, [(5, 7), (6, 8)]))["items"],
       [{"layer": 0x1A, "serial": 5, "amount": 7}, {"layer": 0x1A, "serial": 6, "amount": 8}])
    # 0x3C event carries each container's items in packet order (the client's display order,
    # which a 0x74 price list refers to; ctl buy relies on it)
    rt = WorldRuntime()
    rec = lambda s, c: s.to_bytes(4, "big") + bytes.fromhex("00000f0c" "00" "0001" "0000" "0000" "00") \
        + c.to_bytes(4, "big") + bytes.fromhex("0000" "00000000")  # noqa: E731  (26-byte V12 record)
    body = (3).to_bytes(2, "big") + rec(0x403, 0x4000) + rec(0x401, 0x4000) + rec(0x9, 0x5000)
    rt.feed_packet("s2c", bytes([0x3C]) + (3 + len(body)).to_bytes(2, "big") + body)
    ev = [e for e in rt.drain_events() if e["ev"] == "container_content"]
    eq("0x3C: per-container packet order", ev[0]["containers"], [[0x4000, [0x403, 0x401]], [0x5000, [0x9]]])
    eq("C2S 0x12 open door", parse_packet("c2s", bytes.fromhex("1200055800")),
       {"type": 0x58, "text": ""})
    eq("C2S 0x12 use skill", parse_packet("c2s", actions.use_skill(44)),
       {"type": 0x24, "text": "44 0"})
    # session_20260928_141253: context menu (mode 2) on 0x0008AAE0, 12 entries
    f = parse_packet("s2c", bytes.fromhex(
        "bf006c001400020008aae00c002ddeab00000000002dde9700010000002dde9800"
        "0200000010b7a900030000000f9c5b00040000002dde3e00050000002dde450006"
        "0000002dde4800070000002dde4c00080000002dde4e00090000002dde51000a00"
        "00002dde5f000b0000"))
    eq("BF 0x14 head", (f["sub"], f["serial"], f["mode"], len(f["entries"])),
       (0x14, 0x0008AAE0, 2, 12))
    eq("BF 0x14 entries", (f["entries"][0], f["entries"][11]),
       ({"cliloc": 3006123, "index": 0, "flags": 0},
        {"cliloc": 3006047, "index": 11, "flags": 0}))
    eq("BF 0x13", parse_packet("c2s", bytes.fromhex("bf000900130008aae0")),
       {"sub": 0x13, "serial": 0x0008AAE0})
    eq("BF 0x15", parse_packet("c2s", bytes.fromhex("bf000b00150008aae00002")),
       {"sub": 0x15, "serial": 0x0008AAE0, "index": 2})
    rt = WorldRuntime()
    rt.feed_packet("c2s", bytes.fromhex("bf000b00150008aae00002"))
    rt.feed_packet("s2c", bytes.fromhex("bf0006000803"))  # sub 8: map change to facet 3
    rt.feed_packet("s2c", bytes.fromhex("bf0006001900"))  # sub 0x19: not handled
    eq("BF events", [e["ev"] for e in rt.drain_events()], ["popup_select", "map_change"])
    eq("BF sub 8 sets the facet", rt.state.self.map, 3)
    eq("BF other sub counted unhandled", rt.unhandled[("s2c", 0xBF)], 1)


def test_mobile_routing():
    print("== mobile routing (0x1B / 0x20 / 0x77 / 0x78) ==")
    rt = WorldRuntime()
    rt.feed_packet("s2c", bytes.fromhex(
        "1b" "00094375" "00000000" "00000190" "000007ab" "00000a25"
        "00000000" "80" + "00" * 17))
    s = rt.state.self
    eq("0x1B sets self", (s.serial, s.x, s.y, s.position_absolute),
       (0x00094375, 0x7AB, 0xA25, True))
    # 0x20 for another serial (vendor, notoriety 7) is a mobile, not self
    rt.feed_packet("s2c", bytes.fromhex(
        "20" "000001e3" "00000190" "07" "0401" "00" "0000079c" "00000a15"
        "0000" "02" "00000001"))
    m = rt.state.mobiles.get(0x1E3)
    check("0x20 other -> mobile", m is not None and (m.x, m.y, m.z,
          m.direction, m.notoriety) == (0x79C, 0xA15, 1, 2, 7), repr(m))
    eq("0x20 other leaves self", (s.x, s.y), (0x7AB, 0xA25))
    eq("0x20 other no anomaly", dict(rt.anomalies), {})
    # 0x77 self move (running bit masked off) and other-mobile move
    rt.feed_packet("s2c", bytes.fromhex(
        "77" "00094375" "000007ac" "00000a24" "00000000" "81"))
    eq("0x77 self", (s.x, s.y, s.direction), (0x7AC, 0xA24, 1))
    rt.feed_packet("s2c", bytes.fromhex(
        "77" "000001e3" "0000079d" "00000a16" "00000005" "04"))
    eq("0x77 other", (m.x, m.y, m.z, m.direction), (0x79D, 0xA16, 5, 4))
    # 0x78 equipment parented to the mobile
    rt.feed_packet("s2c", MOBILE_EQUIP_1)
    it = rt.state.items[0x40001177]
    eq("0x78 item parent/layer", (it.container, it.layer), (0x1E9, 0x18))
    # 0x98 name lands on the mobile; 0x1B for another serial is an anomaly
    rt.feed_packet("s2c", bytes.fromhex("98" "0025" "000001e3") +
                   b"Jake".ljust(30, b"\x00"))
    eq("0x98 names mobile", m.name, "Jake")
    rt.feed_packet("s2c", bytes.fromhex("1b" "00000001") + b"\x00" * 38)
    eq("0x1B mismatch anomaly", rt.anomalies["login_confirm_mismatch"], 1)
    eq("0x1B mismatch keeps self", s.serial, 0x00094375)
    # self 0x20 with a ghost body: dead + `death`; human body again: `resurrect`
    rt.drain_events()
    self20 = lambda body: bytes.fromhex(  # noqa: E731
        "20" "00094375" + f"{body:08x}" + "01" "83ea" "20" "000007ac" "00000a24" "0000" "01" "00000000")
    rt.feed_packet("s2c", self20(0x190))
    eq("self alive (human body)", (s.body, s.dead, [e["ev"] for e in rt.drain_events()]), (0x190, False, []))
    rt.feed_packet("s2c", self20(0x192))
    ev = [e for e in rt.drain_events() if e["ev"] in ("death", "resurrect")]
    eq("ghost body -> dead + death event", (s.dead, [(e["ev"], e["body"]) for e in ev]), (True, [("death", 0x192)]))
    eq("snapshot says dead", (rt.state.self.to_dict()["dead"], rt.state.self.to_dict()["body"]), (True, 0x192))
    rt.feed_packet("s2c", self20(0x192))
    eq("still a ghost: no second death event", [e["ev"] for e in rt.drain_events() if e["ev"] == "death"], [])
    rt.feed_packet("s2c", self20(0x190))
    eq("human body again -> resurrect", (s.dead, [e["ev"] for e in rt.drain_events()
                                                   if e["ev"] in ("death", "resurrect")]), (False, ["resurrect"]))


def _var(pid, body_hex):
    body = bytes.fromhex(body_hex)
    return bytes([pid]) + (3 + len(body)).to_bytes(2, "big") + body


def _asc(s):
    b = s.encode("ascii")
    return f"{len(b):02x}" + b.hex()


def test_tracking_packets():
    print("== 0x7C menu / 0xBA quest arrow / 0xFF sub 0x1A (hand-built) ==")
    # 0xBA V10 (14): display, x u32, y u32, serial u32
    pkt = bytes.fromhex("ba" "01" "04050607" "08090a0b" "0c0d0e0f")
    eq("0xBA fields", parse_packet("s2c", pkt),
       {"display": 1, "x": 0x04050607, "y": 0x08090A0B, "serial": 0x0C0D0E0F})
    rt = WorldRuntime()
    rt.feed_packet("s2c", pkt)
    rt.feed_packet("s2c", bytes.fromhex("ba" "00" "00000000" "00000000" "0c0d0e0f"))
    eq("0xBA events", rt.drain_events(), [
        {"ev": "quest_arrow", "display": True, "x": 0x04050607, "y": 0x08090A0B,
         "serial": 0x0C0D0E0F},
        {"ev": "quest_arrow", "display": False, "x": 0, "y": 0, "serial": 0x0C0D0E0F}])
    eq("0xBA handled", rt.unhandled[("s2c", 0xBA)], 0)
    check("0xBA short -> incomplete", _raises(bytes.fromhex("ba01040506070809")))

    # 0x7C item menu: peeked u16 (first graphic's high half) nonzero -> u32
    # graphic + hue u16 + name per entry
    item = _var(0x7C, "04050607" "0809" + _asc("Track") + "02" +
                "00010203" "0405" + _asc("Animals") +
                "00010a0b" "0c0d" + _asc("Players"))
    eq("0x7C item menu", parse_packet("s2c", item),
       {"serial": 0x04050607, "menu_id": 0x0809, "title": "Track", "gray": False,
        "entries": [{"graphic": 0x00010203, "hue": 0x0405, "name": "Animals"},
                    {"graphic": 0x00010A0B, "hue": 0x0C0D, "name": "Players"}]})
    # gray menu: peeked u16 zero -> 4 unread bytes + name per entry (the
    # branch a protocol-12 client takes for any graphic < 0x10000)
    gray = _var(0x7C, "04050607" "0809" + _asc("What?") + "02" +
                "00000102" + _asc("Yes") + "00000304" + _asc("No"))
    f = parse_packet("s2c", gray)
    eq("0x7C gray menu", f,
       {"serial": 0x04050607, "menu_id": 0x0809, "title": "What?", "gray": True,
        "entries": [{"unread": 0x0102, "name": "Yes"}, {"unread": 0x0304, "name": "No"}]})
    empty = _var(0x7C, "04050607" "0809" + _asc("") + "00")
    eq("0x7C no entries", parse_packet("s2c", empty)["entries"], [])
    check("0x7C count beyond buffer -> incomplete", _raises(
        _var(0x7C, "04050607" "0809" + _asc("T") + "02" + "00000102" + _asc("Yes"))))
    rt = WorldRuntime()
    rt.feed_packet("s2c", gray)
    eq("0x7C event", rt.drain_events(), [{"ev": "menu", **f}])

    # 0xFF sub 0x1A: mode 0 set / 1 cancel / 2 clear all. The set is a live Tracking Hunting-mode
    # arrow (20261001_214649): target serial, x, y, z — the mobile stood at (1931,2615,0)
    setp = bytes.fromhex("ff00340000001a000000000300000015aac50000078b00000a3700000000"
                         + b"[Hunting] Joel Embiid\x00".hex())
    eq("sub 0x1A set", parse_packet("s2c", setp),
       {"sub": 0x1A, "mode": 0, "arrow_id": 0, "type": 3, "v16": 0,
        "serial": 0x0015AAC5, "x": 1931, "y": 2615, "z": 0, "text": "[Hunting] Joel Embiid"})
    cancel = _var(0xFF, "0000001a" "01" "0102")
    clear = _var(0xFF, "0000001a" "02")
    odd = _var(0xFF, "0000001a" "07")
    rt = WorldRuntime()
    for p in (setp, cancel, clear, odd):
        rt.feed_packet("s2c", p)
    eq("sub 0x1A events", [e["ev"] for e in rt.drain_events()],
       ["quest_arrow_set", "quest_arrow_cancel", "quest_arrow_clear"])
    eq("sub 0x1A unknown mode counted", rt.dialect_unhandled[("s2c", 0x1A)], 1)
    check("sub 0x1A set truncated -> incomplete", _raises(
        _var(0xFF, "0000001a" "00" "0102" "ee" "03" "0405" "00000706")))

    # world.tracking: mode from System lines only, begin/stop only from our own serial, hits keep
    # the mode they were found in (the arrow has no notoriety)
    def say(serial, text):
        return _var(0xAE, f"{serial:08x}" "0190" "00" "03b2" "0003" + b"ENU\x00".hex()
                    + b"x".ljust(30, b"\x00").hex() + (text.encode("utf-16-be") + b"\x00\x00").hex())
    me = 0x0020F127
    rt = WorldRuntime()
    rt.state.self.serial = me
    for p in (say(0xFFFFFFFF, "You will now hunt murderer players."), say(me, "You begin hunting."), setp,
              say(0x00123456, "You stop hunting."),                     # a player saying it changes nothing
              say(0x00123456, "You will now hunt innocent players.")):
        rt.feed_packet("s2c", p)
    tr = rt.state.snapshot()["tracking"]
    eq("tracking: hunting murderers, the hit carries its serial, spot and the mode it was found in",
       (tr["hunting"], tr["mode"], tr["arrow"]["serial"], tr["arrow"]["x"], tr["arrow"]["y"], tr["arrow"]["mode"]),
       (True, "murderer players", "0x0015AAC5", 1931, 2615, "murderer players"))
    rt.feed_packet("s2c", _var(0xFF, "0000001a" "01" "0000"))
    rt.feed_packet("s2c", say(me, "You stop hunting."))
    tr = rt.state.snapshot()["tracking"]
    eq("tracking: the cancel takes the arrow down (the hit stays in hits); our own line stops the hunt",
       (tr["arrow"], len(tr["hits"]), tr["hunting"]), (None, 1, False))


def _raises(pkt):
    try:
        parse_packet("s2c", pkt)
    except PacketIncomplete:
        return True
    return False



def test_event_semantics():
    print("== event semantics ==")
    import json
    rt = WorldRuntime()
    # scripted sequence: dclick -> server opens gump -> client responds
    rt.feed_packet("c2s", bytes.fromhex("06" "00215a42"))
    layout = b"{ page 0 }"
    body = ("00215a42" "c16e0192" "0000000a" "00000014") + \
        len(layout).to_bytes(2, "big").hex() + layout.hex() + "0000"
    gump_open = bytes.fromhex("b0") + \
        (3 + len(bytes.fromhex(body))).to_bytes(2, "big") + bytes.fromhex(body)
    rt.feed_packet("s2c", gump_open)
    rt.feed_packet("c2s", GUMPRESP)
    events = rt.drain_events()
    kinds = [e["ev"] for e in events]
    eq("event order", kinds, ["dclick", "gump_open", "gump_response"])
    eq("the client's response closes the gump", rt.state.gumps[(0x00215A42, 0xC16E0192)].open, False)
    rt.feed_packet("s2c", gump_open)                                    # reopened
    rt.feed_packet("s2c", bytes.fromhex("bf" "000f" "0004" "c16e0192" "00000000"))   # server closes it
    closes = [e for e in rt.drain_events() if e["ev"] == "gump_close"]
    eq("server 0xBF sub 4 closes by gump id", (rt.state.gumps[(0x00215A42, 0xC16E0192)].open,
                                              [(c["gump_id"], c["closed"]) for c in closes]),
       (False, [(0xC16E0192, 1)]))
    rt.feed_packet("c2s", bytes.fromhex("bf" "0008" "0004" "00" "0005"))   # C2S sub 4 = cast spell: not a close
    eq("C2S 0xBF sub 4 (cast spell) is not a gump close",
       [e["ev"] for e in rt.drain_events() if e["ev"] == "gump_close"], [])
    # every event payload is dict-serializable
    try:
        json.dumps(events)
        ok = True
    except TypeError:
        ok = False
    check("events json-serializable", ok)
    # walk requests don't move self; the server's confirm does (a walk in a new
    # direction only turns, a walk in the facing direction moves one tile)
    rt.feed_packet("c2s", WALK)                                  # dir 6, seq 0
    rt.feed_packet("c2s", bytes.fromhex("02860100000000"))       # dir 6, seq 1
    rt.feed_packet("c2s", bytes.fromhex("02860200000000"))       # dir 6, seq 2 (rejected)
    rt.feed_packet("c2s", bytes.fromhex("02860300000000"))       # dir 6, seq 3
    eq("walk request does not move self", (rt.state.self.x, rt.state.self.y), (0, 0))
    rt.feed_packet("s2c", bytes.fromhex("220001"))                # confirm seq 0: turn to W
    eq("confirmed walk in a new direction only turns",
       (rt.state.self.x, rt.state.self.y, rt.state.self.direction), (0, 0, 6))
    rt.feed_packet("s2c", bytes.fromhex("220101"))                # confirm seq 1: move W
    rt.feed_packet("s2c", bytes.fromhex("220301"))                # confirm seq 3: seq 2 never confirmed
    eq("confirmed walks move; the unconfirmed (rejected) one does not",
       (rt.state.self.x, rt.state.self.y), (-2, 0))
    eq("walk run flag", rt.state.self.direction, 6)
    # deny walk snaps to the server position
    rt.feed_packet("s2c", bytes.fromhex("21" "01" "00001000" "00002000"
                                        "03" "fffffffb"))
    eq("deny walk snap", (rt.state.self.x, rt.state.self.y, rt.state.self.z),
       (0x1000, 0x2000, -5))
    # snapshot is json-serializable
    try:
        json.dumps(rt.state.snapshot())
        ok = True
    except TypeError:
        ok = False
    check("snapshot json-serializable", ok)


def test_status_requested():
    print("== the client's outstanding status requests (ClassicUO HitsRequest) ==")
    rt = WorldRuntime()
    rt.feed_packet("c2s", actions.status_request(0x10))                       # 34 edededed 04 <serial>
    rt.feed_packet("c2s", actions.skills_request(0x20))                       # type 5: not a status request
    eq("a type-4 0x34 marks the mob", rt.state.status_requested, {0x10})
    rt.feed_packet("c2s", bytes.fromhex("bf0009000c00000010"))                # SendCloseStatus
    eq("close status (bf 000c) clears it, so the next attack sends 0x34 again", rt.state.status_requested, set())
    rt.feed_packet("c2s", actions.status_request(0x10))
    rt.feed_packet("s2c", bytes.fromhex("1d00000010"))                         # server removes the mob
    eq("a deleted mob is cleared too", rt.state.status_requested, set())


ME = 0x00094375


def _login(x, y):
    return bytes.fromhex("1b" f"{ME:08x}" "00000000" "00000190" f"{x:08x}" f"{y:08x}" "00000000" "00") + b"\x00" * 17


def _mob20(serial, x, y, noto=3):
    return bytes.fromhex("20" f"{serial:08x}" "00000027" f"{noto:02x}" "0000" "00"
                         f"{x:08x}" f"{y:08x}" "0000" "00" "00000000")


def _move77(serial, x, y):
    return bytes.fromhex("77" f"{serial:08x}" f"{x:08x}" f"{y:08x}" "00000000" "00")


def _equip2e(item, parent, layer):
    return bytes.fromhex("2e" f"{item:08x}" "00000e75" "00000000" f"{layer:02x}" f"{parent:08x}" "0000")


def _contained25(item, container):
    return bytes.fromhex("25" f"{item:08x}" "00000eed" "00" "0001" "0010" "0010" "00"
                         f"{container:08x}" "0000" "00000020")


def _ground1a(item, x, y):
    return _var(0x1A, f"{item:08x}" "0e75" f"{x:04x}" f"{y:04x}" "00")


def _dead(corpse, serial, name="a mongbat corpse"):
    return _var(0xFF, "0000dead" f"{corpse:08x}" f"{serial:08x}" "03" + name.encode().hex() + "00")


def test_pruning():
    """The live table holds only what the stock client has (docs/WORLDMODEL.md
    "Pruning"; live 20261001_214649: the ghosts behind ANTICHEAT.md A12)."""
    print("== pruning: view range, death, teleport, facet, last_seen, swings, seen_t ==")
    clock = [100.0]
    rt = WorldRuntime(clock=lambda: clock[0])
    st = rt.state
    A, B, C, G, PACK = 0x00001001, 0x00001002, 0x00001003, 0x40000001, 0x40000010

    def feed(*pkts, t=None, d="s2c"):
        if t is not None:
            clock[0] = t
        for p in pkts:
            rt.feed_packet(d, p)
        return rt.drain_events()

    feed(_login(1000, 1000), bytes.fromhex("c812"))
    eq("0xC8 sets the view range (18 live)", st.view_range, 18)
    feed(_mob20(A, 1010, 1000), _mob20(B, 1018, 1000), _equip2e(0x40000002, B, 0x01),
         _ground1a(G, 1018, 1001), _contained25(0x40000003, G), bytes.fromhex("24" f"{G:08x}" "0000003c" "0000"),
         _equip2e(PACK, ME, 0x15), _contained25(0x40000011, PACK), t=101.0)
    feed(actions.status_request(B), d="c2s")
    eq("exactly 18 tiles away: still there", (B in st.mobiles, G in st.items, st.containers, st.status_requested),
       (True, True, {G}, {B}))
    eq("seen_t = the packet's time", st.mobiles[A].seen_t, 101.0)
    feed(bytes.fromhex("a1" f"{A:08x}" "0064" "0032"), t=103.5)
    eq("seen_t moves with any update (0xA1)", (st.mobiles[A].seen_t, st.snapshot()["mobiles"][f"0x{A:08X}"]["seen_t"]),
       (103.5, 103.5))

    # one step west (the first walk W only turns): B and the ground item G are 19 away now
    feed(bytes.fromhex("0206000000000000"), bytes.fromhex("0206010000000000"), d="c2s")
    feed(bytes.fromhex("220001"), t=104.0)
    eq("a turn doesn't move or prune", (st.self.x, B in st.mobiles), (1000, True))
    ev = feed(bytes.fromhex("220101"), t=105.0)
    eq("confirmed step prunes beyond the view range, with equipment and contents",
       (st.self.x, B in st.mobiles, 0x40000002 in st.items, G in st.items, 0x40000003 in st.items, A in st.mobiles),
       (999, False, False, False, False, True))
    eq("prune event", [(e["serial"], e["why"]) for e in ev if e["ev"] == "prune"], [(B, "range")])
    eq("containers / status_requested follow removals", (st.containers, st.status_requested), (set(), set()))
    ls = st.snapshot()["last_seen"][f"0x{B:08X}"]
    eq("last_seen: as it was, when, where, why", (ls["x"], ls["y"], ls["t"], ls["facet"], ls["why"], ls["seen_t"]),
       (1018, 1000, 105.0, None, "range", 101.0))
    eq("self's backpack and its contents stay", (PACK in st.items, 0x40000011 in st.items), (True, True))
    feed(_mob20(B, 1010, 1001), t=106.0)
    eq("back in view: live again, out of last_seen", (B in st.mobiles, B in st.last_seen), (True, False))

    # swings: latest per attacker; dropped with the attacker
    feed(bytes.fromhex("2f00" f"{A:08x}" f"{ME:08x}"), t=107.0)
    feed(bytes.fromhex("2f00" f"{0x00009999:08x}" f"{ME:08x}"), t=107.0)   # attacker the client doesn't have
    eq("swings: the live attacker only", st.snapshot()["swings"],
       {f"0x{A:08X}": {"defender": f"0x{ME:08X}", "t": 107.0}})

    # death without 0x1D (Outlands 0xFF sub 0xDEAD): gone, one mobile_death per corpse
    ev = feed(_dead(0x4FEDC20D, A), t=108.0)
    eq("0xDEAD removes the dead mobile and its swing", (A in st.mobiles, A in st.swings), (False, False))
    eq("0xDEAD events", [(e["ev"], e.get("why"), e.get("corpse"), e.get("name")) for e in ev],
       [("prune", "dead", None, None), ("mobile_death", None, 0x4FEDC20D, "a mongbat corpse")])
    eq("last_seen why dead", st.last_seen[A]["why"], "dead")
    ev = feed(_dead(0x4FEDC20D, A), t=150.0)
    eq("the same corpse again (re-sent on coming into view): no second mobile_death", ev, [])
    feed(bytes.fromhex("a1" f"{A:08x}" "0064" "0030"), bytes.fromhex("2d" f"{A:08x}" "0064" "0030" + "00" * 8),
         _var(0x16, f"{A:08x}" "0001" "0001" "01"))
    eq("late vitals don't bring a dead mobile back", A in st.mobiles, False)
    # 0xAF DisplayDeath (in view): removed, the following 0xDEAD reports it
    ev = feed(bytes.fromhex("af" f"{B:08x}" "4f000001" "00000000"), bytes.fromhex("1d" f"{B:08x}"),
              _dead(0x4F000001, B), t=151.0)
    eq("0xAF + 0x1D + 0xDEAD: one prune, one delete, one mobile_death",
       [e["ev"] for e in ev], ["prune", "delete", "mobile_death"])

    # a mobile walking away is dropped by the server-paced World.ProcessDeletes (0xFF sub 5)
    feed(_mob20(C, 1005, 1000), t=152.0)
    feed(_move77(C, 1030, 1000), t=153.0)
    eq("out of range but no sub 5 yet: still there", C in st.mobiles, True)
    ev = feed(_var(0xFF, "00000005"), t=154.0)
    eq("0xFF sub 5 prunes it", (C in st.mobiles, [(e["serial"], e["why"]) for e in ev]), (False, [(C, "range")]))

    # houses: the client keeps a multi within view range + its reach (HouseManager.IsHouseInRange);
    # multi 0x154 reaches 4 tiles, and the server sent it at 22 (live 20261002_153718)
    def _house(serial, x, y):
        return bytes.fromhex("f30001" "02" f"{serial:08x}" "00000154" "00" "0001" "00" "00"
                             f"{x:08x}" f"{y:08x}" "00000001" "2b" "0000" "00000000" "0064")
    feed(_house(0x40000030, st.self.x + 22, st.self.y), _house(0x40000031, st.self.x + 23, st.self.y),
         _ground1a(0x40000032, st.self.x + 19, st.self.y), t=154.5)
    feed(_var(0xFF, "00000005"), t=154.6)
    eq("sub 5 keeps a house within view range + its reach, drops one beyond and any other item past 18",
       (0x40000030 in st.items, 0x40000031 in st.items, 0x40000032 in st.items), (True, False, False))

    # teleport: self 0x20 jumps far; everything near the old spot goes
    feed(_mob20(C, 1001, 1001), _ground1a(G, 1002, 1002), t=155.0)
    ev = feed(bytes.fromhex("20" f"{ME:08x}" "00000190" "01" "83ea" "00" f"{5000:08x}" f"{500:08x}" "0000" "00" "00000000"),
              t=156.0)
    eq("teleport prunes the old surroundings", (C in st.mobiles, G in st.items, [e["serial"] for e in ev if e["ev"] == "prune"]),
       (False, False, [C]))

    # facet change: every mobile but self, every item self doesn't carry; the bank box stays
    BANK = 0x40000020
    feed(_mob20(C, 5001, 501), _ground1a(G, 5001, 502), _equip2e(BANK, ME, 0x1D), _contained25(0x40000021, BANK),
         t=157.0)
    ev = feed(bytes.fromhex("bf0006000801"), t=158.0)
    eq("facet change clears mobiles and ground items",
       (C in st.mobiles, G in st.items, st.self.map, [(e["ev"], e.get("serial"), e.get("why")) for e in ev]),
       (False, False, 1, [("prune", C, "facet"), ("map_change", None, None)]))
    eq("…keeps self's equipment, backpack and bank box with contents",
       all(s in st.items for s in (PACK, 0x40000011, BANK, 0x40000021)), True)
    eq("last_seen remembers the facet it was on", (st.last_seen[C]["why"], st.last_seen[C]["facet"]), ("facet", 0))
    ev = feed(bytes.fromhex("bf0006000801"), t=159.0)
    eq("the same facet again clears nothing", [e["ev"] for e in ev], ["map_change"])


TESTS = [test_fixed_s2c, test_fixed_c2s, test_character_status_11,
         test_skills_3a, test_world_item_1a, test_container_content_3c,
         test_corpse_equipment_89, test_healthbar_16_17, test_gumps_b0_dd,
         test_dialect_ff, test_c2s_procedural, test_mobile_parsers, test_cliloc,
         test_vendor_popup_command, test_tracking_packets,
         test_mobile_routing, test_truncation, test_runtime_edges,
         test_event_semantics, test_status_requested, test_pruning]


def main():
    for t in TESTS:
        t()
    print(f"\nunits: {'ALL PASS' if not FAILURES else f'{len(FAILURES)} FAILURES'}")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())

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

# session_20260928_164548 server prelude = 0xFF sub-0 dialect handshake
PRELUDE = bytes.fromhex("ff000d000000000000000c12e7a11b6b2181c2")
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
# session_20260928_164548 C2S target response (clicked serial = player)
TARGETRESP = bytes.fromhex("6c0000052cb901000943750000077c00000a24")
# session login: 0x91 head carries the account name as asciiz @3
LOGIN91 = bytes.fromhex("910462") + b"Hackworth\x00" + b"eyJhbGciOiJ9" + b"\x00" * 4
# character select: 0xEDEDEDED pattern, name ascii[30] @5
CHARSELECT = bytes.fromhex("5dedededed") + b"TestWorth\x00" + b"\x00" * 20 + b"\x00" * 38


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
    f = parse_fixed(0x21, bytes.fromhex("21070304050608090a0b820cffffff"))
    eq("0x21", f, {"seq": 0x07, "x": 0x03040506, "y": 0x08090A0B,
                   "dir": 0x82 & 7, "z": 0x0C})
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
    # skip@16, x u32@17, y u32@21, z u32@25, dir u8@29, hue u16@30,
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
    # 0x6E CharacterAnimation (14)
    f = parse_fixed(0x6E, bytes.fromhex(
        "6e" "04050607" "0809" "0a0b" "0c0d" "01" "00" "05"))
    eq("0x6E", f, {"serial": 0x04050607, "action": 0x0809, "frames": 0x0A0B,
                   "repeat": 0x0C0D, "backward": 1, "repeat_flag": 0,
                   "delay": 5})
    # 0x1B LoginConfirm (43): only the serial u32@1 is documented-safe
    f = parse_fixed(0x1B, bytes.fromhex("1b" "00094375" + "00" * 38))
    eq("0x1B", f, {"serial": 0x00094375})
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
    # 0x6C target response C2S (19): type@1, cursor u32@2, ctype@6,
    # serial u32@7, x u16@11, y u16@13, z u16@15, graphic u16@17
    f = parse_packet("c2s", TARGETRESP)
    eq("C2S 0x6C", f, {"target_type": 0, "cursor_id": 0x00052CB9,
                       "cursor_type": 1, "serial": 0x00094375,
                       "x": 0x0000, "y": 0x077C, "z": 0, "graphic": 0x0A24})


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
    # 0xDD: same content, two zlib blocks (text lines are UTF-16LE per doc §4)
    comp_layout = zlib.compress(layout)
    raw_lines = (2).to_bytes(2, "big") + \
        (5).to_bytes(2, "big") + "hello".encode("utf-16-le") + \
        (5).to_bytes(2, "big") + "world".encode("utf-16-le")
    comp_lines = zlib.compress(raw_lines)
    payload = ("04050607" "08090a0b" "00000064" "00000032") + \
        (4 + len(comp_layout)).to_bytes(4, "big").hex() + \
        len(layout).to_bytes(4, "big").hex() + comp_layout.hex() + \
        (4 + len(comp_lines)).to_bytes(4, "big").hex() + \
        len(raw_lines).to_bytes(4, "big").hex() + comp_lines.hex()
    pkt = bytes.fromhex("dd") + (3 + len(bytes.fromhex(payload))).to_bytes(2, "big") \
        + bytes.fromhex(payload)
    f = parse_packet("s2c", pkt)
    eq("0xDD", f, {"serial": 0x04050607, "gump_id": 0x08090A0B,
                   "x": 100, "y": 50,
                   "layout": "{ page 0 }{ button 10 10 1 2 }",
                   "lines": ["hello", "world"], "compressed": True})


def test_dialect_ff():
    print("== 0xFF dialect ==")
    # ground truth: sub-0 prelude handshake -> version 12, flags 0x12/0xE7
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
    # sub 8 S2C: buff update (doc §5 layout)
    title = b"BuffTitle\x00"
    desc = b"BuffDesc\x00"
    payload = ("04050607" "0809" "0001" "0002" "0003" "0004"
               "0001"                              # 1 timer
               "0000403f"                          # f32le 0.75
               "0102030405060708"                  # u64be end ts
               "090a0b0c"                          # u32be aux
               "0d0e0f1011121314"                  # u64be timestamp
               ) + title.hex() + desc.hex() + "0005" "0006" "0000803f"
    pkt = bytes.fromhex("ff") + (7 + len(bytes.fromhex(payload))).to_bytes(2, "big") \
        + bytes.fromhex("00000008") + bytes.fromhex(payload)
    f = parse_packet("s2c", pkt)
    want = {"sub": 8, "serial": 0x04050607, "icon_id": 0x0809,
            "f1": 1, "f2": 2, "f3": 3, "f4": 4,
            "timers": [{"seconds": 0.75, "end": 0x0102030405060708,
                        "aux": 0x090A0B0C}],
            "timestamp": 0x0D0E0F1011121314,
            "title": "BuffTitle", "description": "BuffDesc",
            "category": 5, "mode": 6, "scalar": 1.0}
    eq("FF sub8 buff", f, want)
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
    # ground truth: gump response, button 3, no switches/text
    f = parse_packet("c2s", GUMPRESP)
    eq("B1 ground", f, {"serial": 0x00215A42, "gump_id": 0xC16E0192,
                        "button_id": 3, "switches": [], "texts": []})
    # B1 with switches and text entries (text length includes its own 2 bytes)
    text = "abc".encode("utf-16-be")
    body = ("04050607" "08090a0b" "0000002a"
            "00000002" "00000001" "00000002"
            "00000001" "0009" + (2 + len(text)).to_bytes(2, "big").hex()
            ) + text.hex()
    pkt = bytes.fromhex("b1") + (3 + len(bytes.fromhex(body))).to_bytes(2, "big") \
        + bytes.fromhex(body)
    f = parse_packet("c2s", pkt)
    eq("B1 full", f, {"serial": 0x04050607, "gump_id": 0x08090A0B,
                      "button_id": 0x2A, "switches": [1, 2],
                      "texts": [{"id": 9, "text": "abc"}]})
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
        ("s2c", bytes.fromhex("3a" "0013" "00" "0005")),  # 0x3A mid-record
        ("s2c", bytes.fromhex("1a" "0018" "84050607")),   # 0x1A flags promise more
        ("s2c", bytes.fromhex("3c" "001f" "0002" "0405")),  # 0x3C short record
        ("s2c", bytes.fromhex("89" "0014" "04050607" "01")),  # 0x89 mid-pair
        ("s2c", bytes.fromhex("16" "000e" "04050607" "0002")),  # 0x16 short
        ("s2c", bytes.fromhex("b0" "0030" "04050607")),  # 0xB0 truncated
        ("s2c", bytes.fromhex("dd" "0030" "04050607")),  # 0xDD truncated
        ("s2c", bytes.fromhex("ff" "000f" "00000003" "0102")),  # sub3 short
        ("c2s", bytes.fromhex("ad" "0018" "00" "02b2")),  # speech truncated
        ("c2s", bytes.fromhex("b1" "0028" "04050607")),  # B1 truncated
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
    # duplicate serial mobile updates (0x2D twice): last wins
    rt.feed_packet("s2c", bytes.fromhex("2d" "01020304" "0064" "0032"
                                        "000a" "0005" "0014" "000a"))
    rt.feed_packet("s2c", bytes.fromhex("2d" "01020304" "0064" "0063"
                                        "000a" "0005" "0014" "000a"))
    eq("LWW mobile hits", rt.state.mobiles[0x01020304].hits, 0x63)


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
    eq("gump state open", rt.state.gumps[(0x00215A42, 0xC16E0192)].open, True)
    # every event payload is dict-serializable
    try:
        json.dumps(events)
        ok = True
    except TypeError:
        ok = False
    check("events json-serializable", ok)
    # walk dead-reckoning: two walks west (dir 6) from (0,0)
    rt.feed_packet("c2s", WALK)                                  # dir 6, seq 0
    rt.feed_packet("c2s", bytes.fromhex("02860100000000"))       # dir 6, seq 1
    eq("walk integration", (rt.state.self.x, rt.state.self.y), (-2, 0))
    eq("walk run flag", rt.state.self.direction, 6)
    # deny walk snaps to the server position
    rt.feed_packet("s2c", bytes.fromhex("21" "01" "00001000" "00002000"
                                        "03" "fb" "000000"))
    eq("deny walk snap", (rt.state.self.x, rt.state.self.y, rt.state.self.z),
       (0x1000, 0x2000, -5))
    # snapshot is json-serializable
    try:
        json.dumps(rt.state.snapshot())
        ok = True
    except TypeError:
        ok = False
    check("snapshot json-serializable", ok)


TESTS = [test_fixed_s2c, test_fixed_c2s, test_character_status_11,
         test_skills_3a, test_world_item_1a, test_container_content_3c,
         test_corpse_equipment_89, test_healthbar_16_17, test_gumps_b0_dd,
         test_dialect_ff, test_c2s_procedural, test_truncation,
         test_runtime_edges, test_event_semantics]


def main():
    for t in TESTS:
        t()
    print(f"\nunits: {'ALL PASS' if not FAILURES else f'{len(FAILURES)} FAILURES'}")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())

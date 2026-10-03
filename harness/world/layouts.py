"""Declarative packet field tables.

Source of truth: docs/WORLDMODEL.md (field offsets are absolute packet
offsets, offset 0 = packet id byte; all multi-byte integers big-endian unless
flagged). Only fields with upstream/decomp confidence are encoded; bytes the
client itself never reads are `skip:N`.

Field entry: (name, type, offset). Types:
  u8, i8, u16be, u16le, u32be, i32be, u32le   integers
  bytes:N                              raw N bytes
  ascii:N                              fixed-width ASCII, NUL-stripped
  utf16:N                              fixed-width UTF-16BE, NUL-stripped
  skip:N                               N unread bytes

Protocol version is 12 (confirmed by the session prelude handshake, doc §5),
so only the V10/V11/V12 layouts are encoded; legacy variants are omitted.

0xF3 note: the doc's 0xF3 table has an offset typo cascade after the serial
field (its fields sum to 36, not the wire-proven 38). The table below follows
the decompiled handler UpdateItemSA @ 0x140199860 read-for-read, which sums
to exactly 38: graphic u32@8, inc u8@12, amount u16@13, v11 u8@15, pad@16,
x u32@17, y u32@21, z i32@25 (signed: live items below ground send ffffffe7 = -25),
dir u8@29, hue u16@30, flags u32@32, tail u16@36.
"""

# (name, type, offset)
LAYOUTS_S2C: dict[int, list[tuple[str, str, int]]] = {
    # 0x0B Damage (7) — old fixed form in use (doc §1)
    0x0B: [("serial", "u32be", 1), ("amount", "u16be", 5)],
    # 0x22 ConfirmWalk (3)
    0x22: [("seq", "u8", 1), ("notoriety", "u8", 2)],
    # 0x21 DenyWalk V10 (15): z is an i32 at 11 (real: `21 fb 00000778 000009fd
    # 80 ffffffec` = z -20; the proxy's re-anchor writes the same form)
    0x21: [("seq", "u8", 1), ("x", "u32be", 2), ("y", "u32be", 6),
           ("dir", "u8", 10), ("z", "i32be", 11)],
    # 0x2D MobileAttributes (17)
    0x2D: [("serial", "u32be", 1), ("hits_max", "u16be", 5),
           ("hits", "u16be", 7), ("mana_max", "u16be", 9),
           ("mana", "u16be", 11), ("stam_max", "u16be", 13),
           ("stam", "u16be", 15)],
    # 0x2F Swing (10)
    0x2F: [("skip", "skip:1", 1), ("attacker", "u32be", 2),
           ("defender", "u32be", 6)],
    # 0x2C DeathScreen / RunUO DeathStatus (2): action 0 = dead (sent first in
    # Mobile.OnDeath), 2 = sent after the ghost body; ClassicUO ignores 1
    0x2C: [("action", "u8", 1)],
    # 0x27 RejectMoveItemRequest (2): the server refused our 0x07 lift (RunUO
    # LiftRejectReason; 5 = "inspecific", as after a refused corpse in 20261003_125556)
    0x27: [("reason", "u8", 1)],
    # 0xA1 UpdateHitpoints / 0xA2 UpdateMana / 0xA3 UpdateStamina (9)
    0xA1: [("serial", "u32be", 1), ("max", "u16be", 5),
           ("current", "u16be", 7)],
    0xA2: [("serial", "u32be", 1), ("max", "u16be", 5),
           ("current", "u16be", 7)],
    0xA3: [("serial", "u32be", 1), ("max", "u16be", 5),
           ("current", "u16be", 7)],
    # 0x72 Warmode (5): classic padding 00 32 00 unread
    0x72: [("flag", "u8", 1), ("skip", "skip:3", 2)],
    # 0x20 UpdatePlayer / MobileUpdate V10 (28) — read widths verified against
    # MobileUpdateV10 @ 0x140188d10 (4,4,1,2,1,4,4,skip2,1,4). Carries ANY
    # mobile, not just the player (handler looks the serial up when it is not
    # self; real captures: self `20 00094375 00000190 01 83ea 20 000007ab
    # 00000a25 0000 80 00000000`, NPC 0x1E3 with notoriety 7). z is read as
    # a 32-bit int (signed here; real values 0..41).
    0x20: [("serial", "u32be", 1), ("graphic", "u32be", 5),
           ("notoriety", "u8", 9), ("hue", "u16be", 10),
           ("flags", "u8", 12), ("x", "u32be", 13), ("y", "u32be", 17),
           ("skip", "skip:2", 21), ("dir", "u8", 23), ("z", "i32be", 24)],
    # 0x77 MobileMove V10 (18) — decomp MobileMoveV10 @ 0x1401901f0 reads
    # u32 serial, u32 x, u32 y, u32 z, u8 dir (&7; bit 0x80 = running). No
    # graphic/hue/flags/notoriety (upstream 0x77 has them). Real self sample:
    # `77 00094375 000007ab 00000a25 00000000 80` (7423 packets, all 18 B).
    0x77: [("serial", "u32be", 1), ("x", "u32be", 5), ("y", "u32be", 9),
           ("z", "i32be", 13), ("dir", "u8", 17)],
    # 0xF3 UpdateItemSA V12 (38) — decomp-verified, see module docstring
    0xF3: [("skip", "skip:2", 1), ("data_type", "u8", 3),
           ("serial", "u32be", 4), ("graphic", "u32be", 8),
           ("graphic_inc", "u8", 12), ("amount", "u16be", 13),
           ("v11", "u8", 15), ("skip2", "skip:1", 16),
           ("x", "u32be", 17), ("y", "u32be", 21), ("z", "i32be", 25),
           ("dir", "u8", 29), ("hue", "u16be", 30),
           ("flags", "u32be", 32), ("tail", "u16be", 36)],
    # 0x2E EquipItem V12 (20)
    0x2E: [("item", "u32be", 1), ("graphic", "u32be", 5),
           ("inc", "u32be", 9), ("layer", "u8", 13),
           ("parent", "u32be", 14), ("hue", "u16be", 18)],
    # 0x1D DeleteObject (5)
    0x1D: [("serial", "u32be", 1)],
    # 0x24 OpenContainer (11): trailing 2 bytes never read (doc §4)
    0x24: [("serial", "u32be", 1), ("gump_id", "u32be", 5),
           ("skip", "skip:2", 9)],
    # 0x25 UpdateContainedItem V12 (27)
    0x25: [("serial", "u32be", 1), ("graphic", "u32be", 5),
           ("inc", "u8", 9), ("amount", "u16be", 10),
           ("x", "u16be", 12), ("y", "u16be", 14), ("grid", "u8", 16),
           ("container", "u32be", 17), ("hue", "u16be", 21),
           ("v12", "u32be", 23)],
    # 0x6C TargetCursor (27): 12 classic zero bytes + 8-byte Outlands tail,
    # both never read by the client (doc §3)
    0x6C: [("target_type", "u8", 1), ("cursor_id", "u32be", 2),
           ("cursor_type", "u8", 6), ("skip", "skip:12", 7),
           ("skip2", "skip:8", 19)],
    # 0x6E CharacterAnimation (14)
    0x6E: [("serial", "u32be", 1), ("action", "u16be", 5),
           ("frames", "u16be", 7), ("repeat", "u16be", 9),
           ("backward", "i8", 11), ("repeat_flag", "i8", 12),
           ("delay", "u8", 13)],
    # 0x1B LoginConfirm (43) — Assistant.PacketHandlers.LoginConfirm
    # @ 0x1400627a0 (V10 branch): serial u32, u32 (unread), graphic u32,
    # x/y/z i32, dir u8. Real: `1b 00094375 00000000 00000190 000007ab
    # 00000a25 00000000 80 …` (bytes 26..42, map-size area upstream, unread).
    0x1B: [("serial", "u32be", 1), ("skip", "skip:4", 5),
           ("graphic", "u32be", 9), ("x", "u32be", 13), ("y", "u32be", 17),
           ("z", "i32be", 21), ("dir", "u8", 25)],
    # 0xBA DisplayQuestArrow V10 (14; the client's own length table,
    # decompiled/xref_packets_table.c:323) — decomp DisplayQuestArrow
    # @ 0x140194820 (decompiled/protocol_handlers.c:10115-10248): display u8,
    # then x/y as u32 at protocol >= 10 (u16 below), then serial u32. Upstream
    # PacketHandlers.cs:4042-4075 has the u16 form. Tracking's on-screen
    # arrow is a quest arrow on UO servers. [INFERENCE] Outlands' Tracking
    # arrow may come here or via the dialect sub 0x1A instead (parsers.py).
    0xBA: [("display", "u8", 1), ("x", "u32be", 2), ("y", "u32be", 6),
           ("serial", "u32be", 10)],
    # 0xAF DisplayDeath (13): mobile serial, corpse serial, running u32 (upstream
    # PacketHandlers.cs:3693-3749; Outlands DisplayDeath @ 0x140193f20 re-keys the
    # mobile to serial | 0x80000000 the same way). Real (20261001_214649):
    # `af 002b8046 4fe08667 00000000`.
    0xAF: [("serial", "u32be", 1), ("corpse", "u32be", 5), ("running", "u32be", 9)],
    # 0xC8 ClientViewRange (2): the client's World.ClientViewRange (decomp
    # @ 0x14018ee00 writes the global World.ProcessDeletes compares against).
    # Real: `c8 12` (18) at login, echoed by the client.
    0xC8: [("range", "u8", 1)],
}

LAYOUTS_C2S: dict[int, list[tuple[str, str, int]]] = {
    # 0x6C target response, Outlands V10+ form (27; Send_TargetObject
    # @ 0x14015bec0, same as actions.target_object): type@1, cursor u32@2,
    # ctype@6, serial u32@7, x u32@11, y u32@15, z i32@19, graphic u32@23.
    # Real (164548): 6c 00 00052cb9 01 00094375 0000077c 00000a24 00000000 00000190
    0x6C: [("target_type", "u8", 1), ("cursor_id", "u32be", 2),
           ("cursor_type", "u8", 6), ("serial", "u32be", 7),
           ("x", "u32be", 11), ("y", "u32be", 15), ("z", "i32be", 19),
           ("graphic", "u32be", 23)],
    # 0x07 pick-up request (7; Send_PickUpRequest @ 0x14014bfb0)
    0x07: [("serial", "u32be", 1), ("amount", "u16be", 5)],
    # 0x08 drop request, V10+ form (22; Send_DropRequest @ 0x14014c700):
    # container 0xFFFFFFFF = ground at x/y/z
    0x08: [("serial", "u32be", 1), ("x", "u32be", 5), ("y", "u32be", 9),
           ("z", "i32be", 13), ("grid", "u8", 17), ("container", "u32be", 18)],
    # 0x13 equip request (10; Send_EquipRequest @ 0x14014d570)
    0x13: [("serial", "u32be", 1), ("layer", "u8", 5), ("container", "u32be", 6)],
    0x02: [("dir", "u8", 1), ("seq", "u8", 2), ("key", "u32be", 3)],
    # 0x06 double click (5)
    0x06: [("serial", "u32be", 1)],
    # 0x09 entity query (5)
    0x09: [("serial", "u32be", 1)],
    # 0x34 entity query (10): 0xEDEDEDED pattern, type byte, serial
    0x34: [("pattern", "u32be", 1), ("type", "u8", 5),
           ("serial", "u32be", 6)],
    # 0x98 name query (7): u16be length, serial
    0x98: [("skip", "skip:2", 1), ("serial", "u32be", 3)],
    # 0x5D PlayCharacter (73): 0xEDEDEDED pattern, name ascii[30]
    0x5D: [("skip", "skip:4", 1), ("name", "ascii:30", 5)],
}

"""Declarative packet field tables.

Source of truth: docs/WORLDMODEL.md (field offsets are absolute packet
offsets, offset 0 = packet id byte; all multi-byte integers big-endian unless
flagged). Only fields with upstream/decomp confidence are encoded; bytes the
client itself never reads are `skip:N`.

Field entry: (name, type, offset). Types:
  u8, i8, u16be, u16le, u32be, u32le   integers
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
x u32@17, y u32@21, z u32@25, dir u8@29, hue u16@30, flags u32@32, tail u16@36.
"""

# (name, type, offset)
LAYOUTS_S2C: dict[int, list[tuple[str, str, int]]] = {
    # 0x0B Damage (7) — old fixed form in use (doc §1)
    0x0B: [("serial", "u32be", 1), ("amount", "u16be", 5)],
    # 0x22 ConfirmWalk (3)
    0x22: [("seq", "u8", 1), ("notoriety", "u8", 2)],
    # 0x21 DenyWalk V10 (15): only the high byte of the z u32 is used (i8)
    0x21: [("seq", "u8", 1), ("x", "u32be", 2), ("y", "u32be", 6),
           ("dir", "u8", 10), ("z", "i8", 11), ("skip", "skip:3", 12)],
    # 0x2D MobileAttributes (17)
    0x2D: [("serial", "u32be", 1), ("hits_max", "u16be", 5),
           ("hits", "u16be", 7), ("mana_max", "u16be", 9),
           ("mana", "u16be", 11), ("stam_max", "u16be", 13),
           ("stam", "u16be", 15)],
    # 0x2F Swing (10)
    0x2F: [("skip", "skip:1", 1), ("attacker", "u32be", 2),
           ("defender", "u32be", 6)],
    # 0xA1 UpdateHitpoints / 0xA2 UpdateMana / 0xA3 UpdateStamina (9)
    0xA1: [("serial", "u32be", 1), ("max", "u16be", 5),
           ("current", "u16be", 7)],
    0xA2: [("serial", "u32be", 1), ("max", "u16be", 5),
           ("current", "u16be", 7)],
    0xA3: [("serial", "u32be", 1), ("max", "u16be", 5),
           ("current", "u16be", 7)],
    # 0x72 Warmode (5): classic padding 00 32 00 unread
    0x72: [("flag", "u8", 1), ("skip", "skip:3", 2)],
    # 0x20 UpdatePlayer V10 (28) — read widths verified against
    # MobileUpdateV10 @ 0x140188d10 (4,4,1,2,1,4,4,skip2,1,4)
    0x20: [("serial", "u32be", 1), ("graphic", "u32be", 5),
           ("notoriety", "u8", 9), ("hue", "u16be", 10),
           ("flags", "u8", 12), ("x", "u32be", 13), ("y", "u32be", 17),
           ("skip", "skip:2", 21), ("dir", "u8", 23), ("z", "u32be", 24)],
    # 0xF3 UpdateItemSA V12 (38) — decomp-verified, see module docstring
    0xF3: [("skip", "skip:2", 1), ("data_type", "u8", 3),
           ("serial", "u32be", 4), ("graphic", "u32be", 8),
           ("graphic_inc", "u8", 12), ("amount", "u16be", 13),
           ("v11", "u8", 15), ("skip2", "skip:1", 16),
           ("x", "u32be", 17), ("y", "u32be", 21), ("z", "u32be", 25),
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
    # 0x1B LoginConfirm (43): WORLDMODEL.md has no field table for this id;
    # only the serial u32be@1 (universal upstream prefix) is encoded.
    0x1B: [("serial", "u32be", 1)],
}

LAYOUTS_C2S: dict[int, list[tuple[str, str, int]]] = {
    # 0x6C target response (19) — standard C2S form (z is 2 bytes here, not
    # the i8 of the S2C form; wire-validated in doc §6: z 0, graphic 0x0A24)
    0x6C: [("target_type", "u8", 1), ("cursor_id", "u32be", 2),
           ("cursor_type", "u8", 6), ("serial", "u32be", 7),
           ("x", "u16be", 11), ("y", "u16be", 13), ("z", "u16be", 15),
           ("graphic", "u16be", 17)],
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

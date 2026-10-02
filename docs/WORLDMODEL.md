# WORLDMODEL.md — Outlands S2C packet field layouts (priority game-state set)

Authoritative field-layout reference extracted from the decompiled client handlers in
`decompiled/protocol_handlers.c`, cross-referenced against the upstream sources
(`ClassicUO-main/src/ClassicUO.Client/Network/PacketHandlers.cs`,
`Razor-master/Razor/Network/Handlers.cs`) and the authoritative length tables
(`outlands_packet_table.json` base tier via `harness/uo/outlands_table.py`).

> **Wire validation (2026-09-29).** Every layout below that the harness parses was
> re-checked against the *correctly* decoded captures (`harness/uo/s2c.py`: prelude
> keys, XOR, per-packet Huffman — docs/CIPHER.md §4). Earlier wire evidence in this
> file came from the broken decode (no XOR, Huffman from byte 19) and has been
> replaced. Real-packet fixtures live in `harness/test_world_units.py`; every S2C
> packet of all 18 captures parses with 0 failures (`harness/test_world_replay.py`).

## Conventions and reading the evidence

- All multi-byte integer fields are **big-endian** on the wire. In the decompiled CUO
  handlers a BE read appears as a bounds-check block followed by a bswap idiom:
  `u >> 0x18 | (u & 0xff0000) >> 8 | ...` (u32) or `u >> 8 | u << 8` (u16).
- CUO handlers receive a cursor object: `*param_1` = packet buffer, `param_1[1]` =
  packet length, `param_1[2]` = read cursor. The cursor starts at **offset 1** for
  fixed-length packets and **offset 3** for variable-length packets (after the
  `u16be` length at bytes 1–2). Offsets in the tables below are absolute packet offsets
  (offset 0 = packet id byte).
- Assistant (Razor) handlers use `UOO.MemoryMappedReader` methods
  (`ReadByte`/`ReadUInt16`/`ReadUInt32`/`ReadInt16`/`ReadInt32`/`ReadSByte`,
  all big-endian sequential) and follow the same start convention. They are cited as
  independent corroboration of the CUO parse.
- **Outlands protocol version gate**: many layouts branch on
  `*(uint *)(*(longlong *)(DAT_143f8bf90 + 8) + 0x68)`, a version dword written by the
  Outlands handshake (0xFF sub 0, see §5). Observed thresholds: `>= 10` ("V10":
  widened graphic/coordinate fields), `> 10` (V11: extra u8 in container records),
  `> 11` (V12: widened/extra u32 tail fields). The gate value is **confirmed = 12** by
  the session prelude handshake (0xFF sub 0, see §5); the matching wire lengths
  (0xF3=38, 0x25=27, 0x2E=20, 0x20=28, 0x21=15) corroborate. The V12 layouts are
  tabulated in full, with legacy variants noted.
- Confidence labels:
  - **upstream** — layout documented by upstream ClassicUO/Razor source *and* the
    decompiled reads match it.
  - **decomp** — read directly from decompiled buffer accesses (offset + width), no
    upstream semantics; meaning inferred from usage.
  - **unknown** — bytes consumed/skipped by length but semantics unresolved.
- `asciiz` = NUL-terminated ASCII (`FUN_1401a5ae0(param_1, sb, -1, 1, 0)`);
  `ascii[N]` = fixed-width ASCII (same call with explicit length).
- Dialect float32 fields are read via `FUN_1407f4200`. This was earlier assumed to be a
  no-swap little-endian read; the real wire values decode only as **big-endian**
  (0xFF sub 8: `40400000` = 3.0 s), so all fields here are BE, floats included.

## Outlands length deltas vs upstream (priority set)

| ID | Upstream len | Outlands len | Delta |
|----|--------------|--------------|-------|
| 0x0B Damage | 7 (pre-AOS form) / 266 modern | 7 | old fixed form in use |
| 0x20 UpdatePlayer | 19 | 28 | +9 (V10 widening) |
| 0x21 DenyWalk | 8 | 15 | +7 (V10 widening) |
| 0x22 ConfirmWalk | 3 | 3 | — |
| 0x24 OpenContainer | 7 / 9 | 11 | +4 / +2 |
| 0x25 UpdateContainedItem | 20 / 21 | 27 | +7 / +6 |
| 0x2D MobileAttributes | 17 | 17 | — |
| 0x2E EquipItem | 15 | 20 | +5 (V10/V12 widening) |
| 0x2F Swing | 10 | 10 | — |
| 0x6C TargetCursor | 19 | 27 | +8 (tail unread by client) |
| 0x6E CharacterAnimation | 14 | 14 | — |
| 0x72 Warmode | 5 | 5 | — |
| 0xA1/A2/A3 stat updates | 9 | 9 | — |
| 0x1D DeleteObject | 5 | 5 | — |
| 0xF3 SAWorldItem | 26 | 38 | +12 (V10/V11/V12) |

Variable-length (not in base table): 0x11, 0x16, 0x17, 0x1A, 0x3A, 0x3B, 0x3C, 0x78,
0x89, 0x98, 0xA9, 0xAE, 0xB0, 0xBF, 0xD6, 0xDD, and the dialect carrier 0xFF (§5).

---

## 1. Self state

### 0x11 CharacterStatus / MobileStatus — S2C, variable length
Handlers: `ClassicUO.Network.PacketHandlers.CharacterStatus` @ 0x140186520 (4880 B);
corroborated by `Assistant.PacketHandlers.MobileStatus` @ 0x140063250 (Razor 0x11 viewer).

| Off | Size | Type | Field | Confidence |
|-----|------|------|-------|------------|
| 1 | 2 | u16be | packet length | upstream |
| 3 | 4 | u32be | serial | upstream |
| 7 | 30 | ascii[30] | name | upstream |
| 37 | 2 | u16be | Hits (current) | upstream |
| 39 | 2 | u16be | HitsMax | upstream |
| 41 | 1 | i8 | IsRenamable (bool) | upstream |
| 42 | 1 | u8 | **type/flag byte** (`bVar10`) — gates everything below | upstream |
| 43 | 1 | i8 | IsFemale (only if type > 0) | upstream |
| 44 | 2 | u16be | Strength (player only) | upstream |
| 46 | 2 | u16be | Dexterity | upstream |
| 48 | 2 | u16be | Intelligence | upstream |
| 50 | 2 | u16be | Stamina | upstream |
| 52 | 2 | u16be | StaminaMax | upstream |
| 54 | 2 | u16be | Mana | upstream |
| 56 | 2 | u16be | ManaMax | upstream |
| 58 | 4 | u32be | Gold | upstream |
| 62 | 2 | u16be | PhysicalResistance | upstream |
| 64 | 2 | u16be | Weight | upstream |
| 66 | 2 | u16be | WeightMax — only if type ≥ 5 (else computed: `7*(Str>>1)+40`) | upstream |
| 68 | 1 | u8 | Race (0 → coerced to 1) — only if type ≥ 5 | upstream |
| 69 | 2 | u16be | StatsCap — only if type ≥ 3 | upstream |
| 71 | 1 | u8 | Followers — only if type ≥ 3 | upstream |
| 72 | 1 | u8 | FollowersMax — only if type ≥ 3 | upstream |
| 73 | 2 | u16be | FireResistance — only if type ≥ 4 | upstream |
| 75 | 2 | u16be | ColdResistance | upstream |
| 77 | 2 | u16be | PoisonResistance | upstream |
| 79 | 2 | u16be | EnergyResistance | upstream |
| 81 | 2 | u16be | Luck | upstream |
| 83 | 2 | u16be | DamageMin | upstream |
| 85 | 2 | u16be | DamageMax | upstream |
| 87 | 4 | u32be | TithingPoints | upstream |
| 91 | 15×2 | u16be | type ≥ 6 block, each read bounds-guarded (`Position+2 > Length → 0`): MaxPhys/Fire/Cold/Poison/EnergyResist, DefenseChanceIncrease, MaxDefenseChanceIncrease, HitChanceIncrease, SwingSpeedIncrease, DamageIncrease, LowerReagentCost, SpellDamageIncrease, FasterCastRecovery, FasterCasting, LowerManaCost | upstream |

Offsets 44+ assume type ≥ 5 (i.e. 44 = 43+1; the absolute offsets shift if type==0).
The decompiled walk matches upstream field-for-field; no Outlands-specific fields.
**Wire (2026-09-29):** the self status arrives as type 5 in two sizes: 91 B (with
TithingPoints) and **87 B — ends after DamageMax, no TithingPoints**
(session_20260928_141253). The client's cursor reads 0 past the end, so the harness
treats TithingPoints (like the type-6 block) as optional. Real self values: hits
80/80, str 80, dex 15, int 65, weight 58/570, followers 0/5, damage 2–8. Other
mobiles arrive as type 0 (43 B, name + hits only).
Razor's `MobileStatus` reads the identical sequence (serial, name[30], hits pair, flag
byte, str/dex/int, stam/mana pairs, gold u32, armor i16, weight, …).

### 0x0B Damage — S2C, fixed 7
Handler: `ClassicUO.Network.PacketHandlers.Damage` @ 0x1401863e0.

| Off | Size | Type | Field | Confidence |
|-----|------|------|-------|------------|
| 1 | 4 | u32be | serial of damaged entity | upstream |
| 5 | 2 | u16be | damage amount (0 → no display) | upstream |

Outlands uses the old fixed 7-byte form; the modern variable form (type byte @1,
len 266) is *not* in use (table pins 0x0B=7 and the handler reads no type byte).

**Self body, dead/alive (2026-09-30).** On Outlands only S2C 0x20 carries a body graphic. 0x77
has none, and 0x78 carries equipment only. For self, the world model keeps it as
`self.stats.graphic` and exposes `self.body` and `self.dead` (the body is a ghost: 0x192,
0x193, 0x25F, 0x260, 0x2B6, 0x2B7, ClassicUO `Mobile.IsDead`, Mobile.cs:132-140;
`world.state.GHOST_BODIES`, also used by threats.py and ledger.py). A self 0x20 that flips the
body to or from a ghost emits `death` or `resurrect` {body, x, y, z}. Live death
(2026-09-30 10:18, New Player Dungeon): no death gump, the ghost stayed where it died, and the
corpse ("the remains of TestWorth", cliloc 1046414) took the gold.

### 0x20 UpdatePlayer ("MobileUpdate") — S2C, fixed 28 (V10+; legacy 19)
Handlers: `ClassicUO.Network.PacketHandlers.MobileUpdate` @ 0x1401888a0 (legacy path,
dispatches to V10 when version > 9) and `MobileUpdateV10` @ 0x140188d10.
Corroborated by `Assistant.PacketHandlers.MobileUpdate` @ 0x140063af0 (same
version-gated two-variant structure).
**Carries any mobile, not only the player.** The V10 handler compares the serial with
the player's and otherwise looks the mobile up (`FUN_140215150`) and updates it; real
captures send 0x20 for NPCs (e.g. serial 0x1E3 "Jake the barkeep", notoriety 7).
(Earlier text here said "only ever carries the player serial" — wrong.)

Real self sample (session_20260929_144541):
`20 00094375 00000190 01 83ea 20 000007ab 00000a25 0000 80 00000000` → body 0x190,
notoriety 1, hue 0x83EA, flags 0x20, x 0x7AB, y 0xA25, dir 0 + running bit 0x80, z 0.
Byte 9 is stored to mobile +0xC1, the same slot 0xDEAD CorpseFlags writes as
notoriety; values seen: 1 (self), 7 (vendors). z values seen: 0..41 (read as a
32-bit int; the harness treats it as signed).

**V10 layout (live, 28 B):**

| Off | Size | Type | Field | Confidence |
|-----|------|------|-------|------------|
| 1 | 4 | u32be | serial | upstream (name), decomp (width) |
| 5 | 4 | u32be | body/graphic — **widened from u16** | decomp |
| 9 | 1 | u8 | notoriety flag (stored to mobile +0xc1; new vs legacy 0x20) | decomp |
| 10 | 2 | u16be | hue | upstream |
| 12 | 1 | u8 | flags (stored +0xc4; bit 0x20 triggers name re-request) | upstream |
| 13 | 4 | u32be | x — **widened from u16** | decomp |
| 17 | 4 | u32be | y — **widened** | decomp |
| 21 | 2 | u16be | zero/unused (skipped, same as upstream zero2) | upstream |
| 23 | 1 | u8 | direction (low 3 bits) + flag bit 0x80 (stored separately at +0x16e; likely "running"/highlight) | upstream (dir), decomp (0x80) |
| 24 | 4 | u32be | z — **widened from i8** | decomp |

**Legacy layout (19 B, version ≤ 9):** serial u32 @1, graphic u16 @5, zero u8 @7,
hue u16 @8, flags u8 @10, x u16 @11, y u16 @13, zero u16 @15, dir u8 @17 (& 7), z i8 @18.
Matches upstream UpdatePlayer exactly.

### 0x22 ConfirmWalk — S2C, fixed 3
Handlers: CUO `ConfirmWalk` @ 0x1401894e0; `Assistant.PacketHandlers.ConfirmWalk`
@ 0x14005f6d0 (reads the same 2 bytes).

| Off | Size | Type | Field | Confidence |
|-----|------|------|-------|------------|
| 1 | 1 | u8 | walk sequence | upstream |
| 2 | 1 | u8 | notoriety (`& 0xbf`; 0 or >7 coerced to 1) | upstream |

Wire: `22 <seq> 01` — on sessions without harness-injected walks every C2S 0x02 gets
exactly one 0x22 (141253: 84/84, 164548: 165/165, 20260929_144541: 33/33).

### 0x1B LoginConfirm — S2C, fixed 43
Handler: `Assistant.PacketHandlers.LoginConfirm` @ 0x1400627a0 (V10 branch; the CUO
handler slot 0x1B is `CreateGameScene`).

| Off | Size | Type | Field | Confidence |
|-----|------|------|-------|------------|
| 1 | 4 | u32be | player serial | upstream + wire |
| 5 | 4 | u32be | unread (0 on the wire) | decomp |
| 9 | 4 | u32be | body/graphic (V10; legacy u16) | decomp + wire |
| 13 | 4 | i32be | x | decomp + wire |
| 17 | 4 | i32be | y | decomp + wire |
| 21 | 4 | i32be | z | decomp + wire |
| 25 | 1 | u8 | direction (+ 0x80 flag) | decomp + wire |
| 26 | 17 | | not read by the Assistant handler (`00 ffffffff 00000000 2a00 1800 00000000` on the wire; upstream has map width/height here) | unknown |

Real (session_20260929_144541): `1b 00094375 00000000 00000190 000007ab 00000a25
00000000 80 …` → serial 0x00094375, body 0x190, login position (0x7AB, 0xA25, 0).

### 0x77 MobileMove — S2C, fixed 18 (V10; upstream 17 with a different layout)
Handler: CUO `MobileMoveV10` @ 0x1401901f0 (reads u32, u32, u32, u32, u8).
Self when the serial is the player's, otherwise a nearby mobile.

| Off | Size | Type | Field | Confidence |
|-----|------|------|-------|------------|
| 1 | 4 | u32be | serial | decomp + wire |
| 5 | 4 | u32be | x | decomp + wire |
| 9 | 4 | u32be | y | decomp + wire |
| 13 | 4 | i32be | z | decomp + wire |
| 17 | 1 | u8 | direction (`& 7`), bit 0x80 = running (stored +0x16e) | decomp + wire |

No graphic/hue/flags/notoriety (upstream 0x77 has them). Real:
`77 00094375 000007ab 00000a25 00000000 80`. The most frequent S2C id (7 423 packets).

### 0x21 DenyWalk — S2C, fixed 15 (V10+; legacy 8)
Handler: CUO `DenyWalk` @ 0x140189200.

**V10 layout (live, 15 B):**

| Off | Size | Type | Field | Confidence |
|-----|------|------|-------|------------|
| 1 | 1 | u8 | walk sequence | upstream |
| 2 | 4 | u32be | x — **widened from u16** | decomp |
| 6 | 4 | u32be | y — **widened** | decomp |
| 10 | 1 | u8 | direction (`& 7` applied) | upstream |
| 11 | 4 | u32be | z field — only the first (high, BE) byte is used as `i8` z; bytes 12–14 discarded | decomp |

**Legacy layout (8 B, version < 10):** seq u8 @1, x u16be @2, y u16be @4, dir u8 @6,
z i8 @7 — matches upstream.

### 0x3A UpdateSkills — S2C, variable length
Handlers: CUO `UpdateSkills` @ 0x14018b660; corroborated by
`Assistant.PacketHandlers.Skills` @ 0x140061cc0.

| Off | Size | Type | Field | Confidence |
|-----|------|------|-------|------------|
| 3 | 1 | u8 | type: 0=full (caps absent), 1/3=full with caps, 2=single no-cap?, 0xFF=single update, 0xDF=single with cap, 0xFE=skill-name table | upstream |
| 4 | … | | **type 0xFE**: u16be count, then count × (i8 haveButton, u8 nameLen, ascii[nameLen]) — rebuilds the skill list (Outlands custom skills use this) | upstream |
| 4 | … | | **other types**, repeating until end: u16be id (0 ends when type==0; id decremented for types 0/2), u16be value (fixed-point ×10), u16be base (×10), u8 lock, [u16be cap — present iff type ∈ {1,2,3,0xDF}] | upstream |

Wire: the login skill list is **type 2** (full list with caps, 54 × 9-byte records)
terminated by a trailing u16 `0000` read as an id at end-of-packet (upstream stops when
the cursor reaches the end after the id read). Single updates are type 0xDF (13 B,
id not decremented).

### 0x2D MobileAttributes — S2C, fixed 17
Handlers: CUO `MobileAttributes` @ 0x14018ae40; corroborated by
`Assistant.PacketHandlers.MobileStatInfo` @ 0x140063070 (serial + 6×u16).

| Off | Size | Type | Field | Confidence |
|-----|------|------|-------|------------|
| 1 | 4 | u32be | serial | upstream |
| 5 | 2 | u16be | HitsMax | upstream |
| 7 | 2 | u16be | Hits | upstream |
| 9 | 2 | u16be | ManaMax | upstream |
| 11 | 2 | u16be | Mana | upstream |
| 13 | 2 | u16be | StaminaMax | upstream |
| 15 | 2 | u16be | Stamina | upstream |

### 0x2F Swing — S2C, fixed 10
Handler: CUO `Swing` @ 0x14018b440.

| Off | Size | Type | Field | Confidence |
|-----|------|------|-------|------------|
| 1 | 1 | u8 | zero (skipped) | upstream |
| 2 | 4 | u32be | attacker serial; remaining field read only when attacker == player | upstream |
| 6 | 4 | u32be | defender serial | upstream |

### 0xA1 UpdateHitpoints / 0xA2 UpdateMana / 0xA3 UpdateStamina — S2C, fixed 9 each
Handlers: CUO @ 0x140193300 / 0x140193420 / 0x140193570; corroborated by Assistant
`HitsUpdate` @ 0x140062e00, `ManaUpdate` @ 0x140062fa0, `StamUpdate` @ 0x140062ed0
(all three: u32 + 2×u16).

| Off | Size | Type | Field | Confidence |
|-----|------|------|-------|------------|
| 1 | 4 | u32be | serial | upstream |
| 5 | 2 | u16be | max (HitsMax / ManaMax / StaminaMax) | upstream |
| 7 | 2 | u16be | current (Hits / Mana / Stamina) | upstream |

### 0x72 Warmode — S2C, fixed 5
Handler: CUO `Warmode` @ 0x14018f780.

| Off | Size | Type | Field | Confidence |
|-----|------|------|-------|------------|
| 1 | 1 | u8 | warmode flag (≠0 → true) | upstream |
| 2 | 3 | — | unread (classic padding 0x00 0x32 0x00) | upstream |

---

## 2. Nearby entities

### 0x1A UpdateItem / WorldItem — S2C, variable length (bit-flag-driven)
Handler: CUO parser `UpdateItemOld` @ 0x140187ae0, feeding the shared builder
`UpdateItem` @ 0x14019b940(serial, graphic, amount, x, y, z, dir, hue, flags, …).
Corroborated by `Assistant.PacketHandlers.WorldItem` @ 0x140064800.

| Off | Size | Type | Field | Confidence |
|-----|------|------|-------|------------|
| 1 | 2 | u16be | packet length | upstream |
| 3 | 4 | u32be | serial; **bit31** = amount field present (serial &= 0x7fffffff) | upstream |
| 7 | 2 | u16be | graphic; **bit15** = graphic-offset byte follows; **bit14** = item subtype flag (graphic &= 0x3fff, type=2) | upstream |
| [9] | 1 | u8 | graphic offset (only if graphic bit15) | upstream |
| — | 2 | u16be | amount (only if serial bit31; else 1) | upstream |
| — | 2 | u16be | x; **bit15** = direction byte present (x &= 0x7fff) | upstream |
| — | 2 | u16be | y; **bit15** = hue present, **bit14** = flags byte present | upstream |
| — | 1 | u8 | direction (only if x bit15) | upstream |
| — | 1 | i8 | z | upstream |
| — | 2 | u16be | hue (only if y bit15) | upstream |
| — | 1 | u8 | flags (only if y bit14) | upstream |

Identical to upstream 0x1A; no Outlands extension.

### 0x1D DeleteObject — S2C, fixed 5
Handler: CUO `DeleteObject` @ 0x1401884e0 (Assistant `RemoveObject` @ 0x140064510).

| Off | Size | Type | Field | Confidence |
|-----|------|------|-------|------------|
| 1 | 4 | u32be | serial to delete | upstream |

### 0x78 MobileEquip (V12 MobileIncoming) — S2C, variable length
Handlers: CUO `MobileDraw` @ 0x1401905e0 delegates to `MobileEquip` @ 0x140190e20 when
the protocol version > 9. **No graphic/position/notoriety fields** (those come via
0x20); only the equipment list.

| Off | Size | Type | Field | Confidence |
|-----|------|------|-------|------------|
| 1 | 2 | u16be | packet length | upstream |
| 3 | 4 | u32be | mobile serial | decomp + wire |
| 7 | 15×n | | records until an item serial of 0: u32be item serial, u32be graphic, u8 layer, u16be hue, u32be V12 tail (version ≥ 12; u8 before) | decomp + wire |
| — | 4 | u32be | 0 terminator | decomp + wire |

All 597 real 0x78 packets consume exactly. Real: `78 001a 000001e9 | 40001177 0000152e
18 0420 00000020 | 00000000`. The V12 tail matches 0x2E bytes 9–12 for the same item
(0x200 backpack, 0x20 worn items).

### 0x1C Talk / 0xAE UnicodeTalk / 0x98 UpdateName / 0xA9 CharacterList — S2C
Standard upstream layouts, confirmed on the wire:
- **0x1C**: serial u32 @3, graphic u16 @7, type u8 @9, hue u16 @10, font u16 @12,
  name ascii[30] @14, NUL-terminated ASCII text @44 (NPC labels "Jake the barkeep",
  system text).
- **0xAE**: same head, lang ascii[4] @14, name ascii[30] @18, NUL-terminated UTF-16BE
  text @48 (`Welcome TestWorth!`, own speech "howdy").
- **0x98** (37 B, variable-length id): serial u32 @3, name ascii[30] @7.
- **0xA9**: slot count u8 @3, then count × ascii[30] names — **no password field**
  (5 × 30 + 4 = 154 of 161 B); 7-byte tail `00 00000008 ffff` unparsed (upstream:
  city count + flags). Real: 5 slots, "TestWorth" + 4 empty.

### 0xF3 UpdateItemSA / SAWorldItem — S2C, fixed 38 (V12)
Handler: CUO `UpdateItemSA` @ 0x140199860; corroborated by
`Assistant.PacketHandlers.SAWorldItem` @ 0x140064b40.
(Upstream also reuses this handler for 0xD3/0xF7-style updates; here tabulated for 0xF3.)

| Off | Size | Type | Field | Confidence |
|-----|------|------|-------|------------|
| 1 | 2 | u16be | 0x0001 marker (skipped) | upstream |
| 3 | 1 | u8 | graphic type (passed through to builder) | upstream |
| 4 | 4 | u32be | serial | upstream |
| 6 | 4 | u32be | graphic — **widened from u16 at V10** (legacy: u16 @6) | decomp |
| 10 | 1 | u8 | graphic increment (skipped by CUO; upstream `graphicInc`) | upstream |
| 11 | 2 | u16be | amount | upstream |
| 13 | 1 | u8 | V11 field (passed to builder as `param_12`; legacy V<11 skips 2 bytes here) | decomp |
| 14 | 1 | u8 | skipped (second byte of the V11 pair) | unknown |
| 15 | 4 | u32be | x — **widened from u16 at V10** | decomp |
| 19 | 4 | u32be | y — **widened** | decomp |
| 23 | 4 | u32be | z — **widened from i8** | decomp |
| 27 | 1 | u8 | direction/light (upstream `dir`) | upstream |
| 28 | 2 | u16be | hue | upstream |
| 30 | 4 | u32be | flags — **widened from u8 at V12** | decomp |
| 34 | 2 | u16be | durability-ish tail (0 → coerced to 100; upstream `unk2`) | upstream (position), decomp (meaning) |

Legacy variants: V<10 total 26 B (= upstream); V10/V11 intermediate sizes. The 38-byte
V12 form matches the authoritative table (0xF3=38).

### 0x11 as nearby-mobile status
For non-player mobiles, 0x11 (§1) carries only serial/name/hits/renamable/type;
Razor's `MobileStatus` viewer is what populates Razor's mobile model for nearby
entities. See §1 table.

### 0x2E EquipItem / EquipmentUpdate — S2C, fixed 20 (V12; upstream 15)
Handler: CUO `EquipItem` @ 0x14018b0b0; corroborated (version-branched) by
`Assistant.PacketHandlers.EquipmentUpdate` @ 0x1400619c0.

| Off | Size | Type | Field | Confidence |
|-----|------|------|-------|------------|
| 1 | 4 | u32be | item serial | upstream |
| 5 | 4 | u32be | graphic — **widened from u16 at V10** (legacy: u16 graphic + i8 increment) | decomp |
| 9 | 4 | u32be | graphic-increment/offset — **V12 widening** (V8–V11: u8 @7; V<8: absent) | decomp |
| 13 | 1 | u8 | layer | upstream |
| 14 | 4 | u32be | parent/container serial | upstream |
| 18 | 2 | u16be | hue | upstream |

### 0x6E CharacterAnimation — S2C, fixed 14
Handler: CUO `CharacterAnimation` @ 0x14018cf50.

| Off | Size | Type | Field | Confidence |
|-----|------|------|-------|------------|
| 1 | 4 | u32be | serial | upstream |
| 5 | 2 | u16be | action | upstream |
| 7 | 2 | u16be | frame count (only high byte used) | upstream |
| 9 | 2 | u16be | repeat count (only high byte used) | upstream |
| 11 | 1 | i8 | backward flag (forward = !value) | upstream |
| 12 | 1 | i8 | repeat flag (bool) | upstream |
| 13 | 1 | u8 | delay | upstream |

### 0x89 CorpseEquipment — S2C, variable length
Handler: CUO `CorpseEquipment` @ 0x140191a00.

| Off | Size | Type | Field | Confidence |
|-----|------|------|-------|------------|
| 1 | 2 | u16be | packet length | upstream |
| 3 | 4 | u32be | corpse serial (must resolve to graphic 0x2006) | upstream |
| 7 | … | | repeating: i8 layer (0 = terminator; 0x16 entries skipped), u32be item serial | upstream |

### 0x16 / 0x17 NewHealthbarUpdate — S2C, variable length
Handler: CUO `NewHealthbarUpdate` @ 0x1401878e0 (both ids);
corroborated by `Assistant.PacketHandlers.NewMobileStatus` @ 0x140063160 (0x17).

| Off | Size | Type | Field | Confidence |
|-----|------|------|-------|------------|
| 1 | 2 | u16be | packet length | upstream |
| 3 | 4 | u32be | serial | upstream |
| 7 | 2 | u16be | entry count | upstream |
| 9 | … | | per entry: u16be type, i8 enabled — type 1 sets "hits-bar poisoned" flag (+0x164), type 2 sets/clears flag bit 0x08 (+0xc4) | upstream (structure), decomp (flag targets) |

---

## 3. Targeting

### 0x6C TargetCursor — S2C, fixed 27 (upstream 19)
Handler: CUO `TargetCursor` @ 0x140185e20.

| Off | Size | Type | Field | Confidence |
|-----|------|------|-------|------------|
| 1 | 1 | u8 | target type (0 object / 1 ground) | upstream |
| 2 | 4 | u32be | cursor id | upstream |
| 6 | 1 | u8 | cursor type (0 neutral / 1 harmful / 2 helpful) | upstream |
| 7 | 12 | — | unused by CUO (upstream: clicked-on serial u32 + x/y u16 + z i8 + graphic u16, all zero in S2C) | upstream |
| 19 | 8 | — | **Outlands extension — never read by the handler** | unknown |

---

## 4. Containers and gumps

### 0x24 OpenContainer / BeginContainerContent — S2C, fixed 11
Handler: CUO `OpenContainer` @ 0x140189d00; Assistant `BeginContainerContent`
@ 0x140061490 reads only the serial.

| Off | Size | Type | Field | Confidence |
|-----|------|------|-------|------------|
| 1 | 4 | u32be | container serial | upstream |
| 5 | 4 | u32be | gump id — **widened from u16 at V10** (legacy u16, 0xffff → −1 sentinel) | decomp |
| 9 | 2 | — | trailing 2 bytes never read by the handler | unknown |

(gumpId 0x30 opens the vendor-shop path; 0xFFFF-ish values take the generic path.)

### 0x25 UpdateContainedItem / ContainerContentUpdate — S2C, fixed 27 (V12)
Handler: CUO `UpdateContainedItem` @ 0x14018a3b0; corroborated by
`Assistant.PacketHandlers.ContainerContentUpdate` @ 0x1400610e0.

| Off | Size | Type | Field | Confidence |
|-----|------|------|-------|------------|
| 1 | 4 | u32be | item serial | upstream |
| 5 | 4 | u32be | graphic — **widened at V10** (legacy: u16 graphic + u8 increment added) | decomp |
| 9 | 1 | u8 | graphic increment — present from V11, value ignored by CUO | upstream |
| 10 | 2 | u16be | amount (< 2 coerced to 1) | upstream |
| 12 | 2 | u16be | x (read, unused by CUO beyond builder) | upstream |
| 14 | 2 | u16be | y (skipped) | upstream |
| 16 | 1 | u8 | grid index (skipped) | upstream |
| 17 | 4 | u32be | container serial (skipped — CUO re-derives container) | upstream |
| 21 | 2 | u16be | hue (skipped) | upstream |
| 23 | 4 | u32be | **V12 tail field — skipped by CUO**; Razor reads it as i32 (used in item-extra data) | unknown |

### 0x3C UpdateContainedItems / ContainerContent — S2C, variable length
Handler: CUO `UpdateContainedItems` @ 0x14018be00; corroborated field-for-field
(including all three version gates) by `Assistant.PacketHandlers.ContainerContent`
@ 0x1400615a0.

| Off | Size | Type | Field | Confidence |
|-----|------|------|-------|------------|
| 1 | 2 | u16be | packet length | upstream |
| 3 | 2 | u16be | item count | upstream |
| 5 | … | | per item (**26 B at V12**): u32be serial, u32be graphic (V10+; legacy u16+u8 inc), u8 V11 flags, u16be amount (0→1), u16be x, u16be y, u8 grid index, u32be container serial, u16be hue, u32be V12 field | upstream + decomp (widened fields) |

First item's container serial triggers `ClearContainerAndRemoveItems` on the container.
Builder: `AddItemToContainer(serial, graphic, amount, x, y, hue, container, grid, v11byte, v12u32)`.

### 0xDD CompressedGump / OpenCompressedGump — S2C, variable length
Handler: CUO `OpenCompressedGump` @ 0x140197520; corroborated by
`Assistant.PacketHandlers.CompressedGump` @ 0x1400671a0 (two zlib blocks via
`GetCompressedReader`, text lines read as UTF-16).

| Off | Size | Type | Field | Confidence |
|-----|------|------|-------|------------|
| 1 | 2 | u16be | packet length | upstream |
| 3 | 4 | u32be | sender serial | upstream |
| 7 | 4 | u32be | gump id | upstream |
| 11 | 4 | u32be | x | upstream |
| 15 | 4 | u32be | y | upstream |
| 19 | 4 | u32be | compressed layout length (includes its own 4 bytes; `len-4` data follows) | upstream |
| 23 | 4 | u32be | decompressed layout length | upstream |
| 27 | … | u8[] | zlib-compressed layout text | upstream |
| — | 4 | u32be | text line count (0 → no text block follows) | upstream + wire |
| — | 4 | u32be | compressed text-lines length (includes the following 4 bytes) | upstream + wire |
| — | 4 | u32be | decompressed text-lines length | upstream + wire |
| — | … | u8[] | zlib block: count × (u16be char count + UTF-16**BE** text) | upstream + wire |

Wire: all 77 real 0xDD packets parse under this layout and end with 4 zero bytes
(e.g. lines "Guide", "Aspect Mastery", "Charges"). An earlier version of this table
omitted the line-count u32 and said UTF-16LE — both wrong.

### 0xB0 OpenGump / SendGump (+ CreateGump builder) — S2C, variable length
Handlers: CUO `OpenGump` @ 0x1401942a0; Assistant `SendGump` @ 0x140065680
(identical prefix). `ClassicUO.Network.PacketHandlers.CreateGump` @ 0x14019bde0
(13 kB) is **not** a packet parser — it is the shared gump-construction routine that
consumes the parsed (serial, gumpId, x, y, layout, lines) tuple from 0xB0/0xDD.

| Off | Size | Type | Field | Confidence |
|-----|------|------|-------|------------|
| 1 | 2 | u16be | packet length | upstream |
| 3 | 4 | u32be | sender serial | upstream |
| 7 | 4 | u32be | gump id | upstream |
| 11 | 4 | u32be | x | upstream |
| 15 | 4 | u32be | y | upstream |
| 19 | 2 | u16be | layout string length | upstream |
| 21 | … | ascii | layout commands | upstream |
| — | 2 | u16be | text-line count, then count × (u16be len + text) | upstream |

### 0x3B CloseVendorInterface — S2C, variable length
Handler: CUO `CloseVendorInterface` @ 0x14018c2d0.

| Off | Size | Type | Field | Confidence |
|-----|------|------|-------|------------|
| 1 | 2 | u16be | packet length | upstream |
| 3 | 4 | u32be | vendor serial (shop gump to close) | upstream |
| 7 | 1+4n | | u8 count, then count × u32be item serials — **not read by CUO** (upstream Razor/CUO format) | upstream |

### 0x7C OpenMenu / 0xBA DisplayQuestArrow — S2C (Tracking-related; added 2026-09-29, parse only)
- **0x7C** mirrors the client read for read: `protocol_handlers.c:20838-21083` (Outlands
  OpenMenu @0x140191200), upstream `PacketHandlers.cs:2956-3057`. Event `menu` {serial,
  menu_id, title, gray, entries}; item entries are {graphic (u32 at protocol ≥ 10), hue, name}.
  Caveat: at protocol 12 the client's peek reads the high half of the u32 graphic, so graphics
  below 0x10000 take the gray branch. The parser does the same as the client.
- **0xBA** is 14 bytes on Outlands (`xref_packets_table.c:323`): display u8, x u32, y u32,
  serial u32 (`protocol_handlers.c:10115-10248`; upstream uses u16 x/y). Event `quest_arrow`
  {display, x, y, serial}.
- Neither appears in any capture. Outlands Tracking doesn't use them: its window is gump
  `0xFE5C638B` and its arrow is `0xFF` sub `0x1A` (session 20261001_214649, §5 below). Answering a
  menu (C2S 0x7D) isn't built.

---

## 5. Outlands custom dialect

> ### Verification (2026-09-29, correctly decoded captures)
>
> 1. **C2S dialect frame**: `ff <len u16be> <subId u32be> <payload>`. `Send_TimeSyncPingReq` writes id `0xff`, len placeholder, then LE `0x3000000` (= wire bytes `00 00 00 03` = subId 3 BE) → sub 3 = TimeSyncReq.
> 2. **The 13-byte server prelude IS the sub-0 handshake**: `ff 00 0d | 00000000 | 0000000c | <s2c_key> <c2s_key>` — protocol version 12 (the client preamble `ef 0000000c` echoes it), flag1 = the S2C XOR key, flag2 = the C2S XOR key (docs/CIPHER.md §4). Everything after byte 12 is XOR+Huffman packet data. (Earlier text said "19-byte prelude"; bytes 13–18 are the first compressed packet.)
> 3. **S2C dialect subs on the wire** (all 18 captures, 37 299 0xFF packets): sub 1 (7 B, 205×), sub 3 (15 B, TimeSync reply, 12 331×), sub 4 (16 B, 4×), sub 5 (7 B, ProcessDeletes, 23 915×), sub 7 (16 B, 28×), sub 8 (buff update, 121×), sub 9 (13 B, remove buff, 16×), sub 0x15 (item names, 44×), sub 0x16 (mobile data, 26 B mostly, 579×), sub 0x1C (50 B, 36×), sub 0x1D (11 B, 18×), sub 0xDEAD (corpse flags, 2×). Subs 0/3/8/9/0x15 parse on every sample.
> 4. **C2S dialect subs (session 20260928_164548):** sub 3 = TimeSyncReq (empty payload, ~1/s); sub 4 = cast spell `ff 000a <sub=4> 00 <spellId u16be>` (observed 0x0005, 0x000f); sub 9 = item/object detail query `ff 000e <sub=9> 01 0001 <serial u32be>` on vendor-item dclick/hover — answered by S2C sub 0x15 (e.g. `44adb584 "bandage : 100"`).
> 5. **Vendor buy**: C2S `3b <len> <vendorSerial u32be> …` (vendor serial matches a nearby NPC); S2C 0x74 carries the price/name list.
> 6. S2C 0xF0 does occur (`f0 000c fe 00000000 07628000`, once per login, 18×) but is not the dialect carrier; payload unparsed.
>
> The former items about a "0x00-family world-state stream" and an unparsable 117-byte
> 0xFF packet were artifacts of the broken decode (docs/WORLDSTATE.md).

### 0xFF OutlandsProtocol / OutlandsServerPacket — dialect carrier, both directions, variable length, u32 sub-id

**Carrier = 0xFF** (`RunUOProtocolExtention` @ 0x140066380, the upstream Razor 0xF0
handler, is dead code in this build):

- S2C: the 13-byte session prelude is one dialect frame
  `ff 00 0d | 00000000 | 0000000c 12 e7` (session 164548) = subId 0 handshake (see sub 0
  below), and `BuildPacketTable(12)` reproduces every wire length (0xF3=38, 0x25=27,
  0x2E=20, 0x20=28, 0x1B=43, 0x77=18 …). **Protocol version = 12 on the live server.**
- C2S: keepalive frames `ff 0007 00000003` (sub 3) plus sub-4/sub-9 frames. Write side
  proven in `NetClientExt.Send_TimeSyncPingReq` @ 0x14017e940: `GetPacketLength(0xff)`
  (−1 → variable), stores `*(u32*)buf = 0x03000000` — LE store of bytes `00 00 00 03` =
  subId 3 BE on the wire.
- CUO registration: slot 0xFF → `OutlandsProtocol` in the extracted 100-slot handler
  table (`cuo_handler_table.json`, docs/WORLDSTATE.md §2).

Frame: `FF <len u16be> <subId u32be> <payload…>`. Sub-id space is **per-direction**
(e.g. sub 9 S2C = RemoveBuff, sub 9 C2S = item-detail query).

CUO S2C dispatcher: `ClassicUO.Network.PacketHandlers.OutlandsProtocol` @ 0x14019f3a0 —
reads subId u32be @3, switches (payload offsets are from payload start = packet off 7):

| SubId | Target | Payload layout |
|-------|--------|----------------|
| 0 | handshake (inline) | u32be **protocol version** (=12 live; → the global V10/V11/V12 gate at settings+0x68), u8 flag1 (+0x71 = the S2C XOR key NetClient uses in `ProcessRecv`; 0x12 in session 164548), u8 flag2 (+0x72 = the C2S XOR key; 0xE7), then `BuildPacketTable(version)`. Sent as the cleartext 13-byte prelude |
| 1 | `NetClientExt.Send_Info` (server polls client info) | none |
| 2 | inline | u16be type, u32be id; type==1 opens a gump (graphic 0x0a… id) |
| 3 | `ServerTime.TimeSyncReceived` @ 0x1401240d0 | u64be timestamp (read BE in the dispatcher, passed by value). C2S twin: sub 3 keepalive, empty payload |
| 4 | `SpellCastManager.OnPacketResponse` @ 0x140306390 | spell-cast result; **sub-parser not in the decompiled selection — layout open** |
| 5 | `World.ProcessDeletes` | none |
| 6 | `ParticleEffect` @ 0x14018d980 | particle effect record |
| 7 | `WorldSaveManager.WorldSave` | none |
| 8 | `OutlandsBuffUpdate` @ 0x14019a600 | see below |
| 9 | `OutlandsRemoveBuff` @ 0x14019ad20 | u32be serial, u16be buff/icon id |
| 10 | `PartyManager.ParseMemberList` | party sub-packet (party parsers not in the decompiled selection) |
| 0x0B | `PartyManager.ParseMemberRemoved` | party sub-packet |
| 0x0C | `PartyManager.ParseMessage` | party sub-packet |
| 0x0D | `PartyManager.ParseInvitation` | party sub-packet |
| 0x0E | `PartyManager.ParsePing` | party sub-packet |
| 0x0F | `HandleNameCheckResponse` @ 0x14019ae30 | name-check result |
| 0x10 | `HandlePlayerNameChanged` @ 0x14019aec0 | rename notification |
| 0x11 | `HandleAddonTarget` @ 0x14019af60 | addon targeting |
| 0x13 | `OutlandsVendorItemData` @ 0x1401a0f20 | see below |
| 0x14 | `OutlandsVendorMobileData` @ 0x1401a0d00 | see below |
| 0x15 | `ItemNameRequestManager.HandleResponse` @ 0x140143900 (CUO); `Assistant…OutlandsItemNameResponse` @ 0x140064f20 | see below |
| 0x16 | `OutlandsMobileData` @ 0x1401a0980 | see below |
| 0x17 | `HandleScreenShake` @ 0x1401a08e0 | u8 intensity/type, u16be duration → `ScreenShake(u8, u16)` |
| 0x18 | `AnimatedItemMovement` @ 0x1401a06a0 | item movement anim |
| 0x1A | `HandleQuestArrow` @ 0x1401a0160 | quest arrow |
| 0x1B | `HandleWorldGumpAnchor` @ 0x14019fdd0 | u8 (must be 0 else ignored), u32be, u32be, i8, u32be, … (anchor record; only partially consumed in decompilation) |
| 0x1C | `HandleLightState` @ 0x14019f920 | light state |
| 0x1D | inline | u32be → global dword DAT_143f7b76c (flag; semantics unknown) |
| 0xDEAD | `OutlandsCorpseFlags` @ 0x14019a440 | see below |
| 0x12, 0x19 | ignored | — |

Assistant dispatcher: `Assistant.PacketHandlers.OutlandsServerPacket` @ 0x140064eb0 —
reads the same u32be subId, handles only 0x13 / 0x14 / 0x15 (vendor + name data for
Razor's own item/mobile model).

#### C2S dialect subs (confirmed, session_20260928_164548)

| SubId | Meaning | Payload | Evidence |
|-------|---------|---------|----------|
| 3 | TimeSyncReq keepalive | empty (frame len 7) | 251× at ~1/s: `ff 0007 00000003`; sender `Send_TimeSyncPingReq` writes sub 3 |
| 4 | Cast spell | u8 flag (0 observed), u16be spellId (frame len 10) | `ff 000a 00000004 00 000f`; Assistant C2S viewer `OutlandsClientPacket` @ 0x1400602f0 reads sub==4 → u8 (must be 0), u16be spellId → spell-name lookup |
| 9 | Item/object detail query | u8 0x01, u16be 0x0001, u32be serial (frame len 14) | `ff 000e 00000009 01 0001 40000d54`; fires on every vendor-item dclick/hover; the serial is item-range (≥0x40000000) |

Also observed at session start: C2S `f0 0004 ff` (one 4-byte 0xF0 frame — plausibly the
legacy Razor/RunUO 0xF0 negotiation carrying the dialect marker 0xFF; single sample,
labeled **decomp-candidate**). And C2S `bf …` extended commands subs 0x0C / 0x13 / 0x15
carrying entity serials (`bf 0009 000c <serial u32be>` etc.) fire alongside 0x09/0x34/
0x98 queries — CUO's standard-channel entity-info requests.

#### Sub 0x13 OutlandsVendorItemData (vendor item flags/prices)
Both parsers agree. Payload:

| Off | Size | Type | Field | Confidence |
|-----|------|------|-------|------------|
| 0 | 2 | u16be | item count | decomp |
| 2 | … | | per item: u32be serial, u8 flags, **[u32be extra — only if flags & 1]** (CUO stores extra at item +0xe8, flags at +0xf7, marks +0xf8=1) | decomp |

#### Sub 0x14 OutlandsVendorMobileData (vendor mobile data)
CUO: serial → mobile; stores flags/value on the mobile.

| Off | Size | Type | Field | Confidence |
|-----|------|------|-------|------------|
| 0 | 4 | u32be | mobile serial | decomp |
| 4 | 1 | i8 | bool flag (+0x171) | decomp |
| 5 | 1 | u8 | field (+0x172) | decomp |
| 6 | 1 | u8 | field (+0x173) | decomp |
| 7 | 8 | u64be | value (+0x110; gold/price-scale candidate) | decomp |
| 15 | 2 | u16be | value (+0x15c) | decomp |

(Assistant's version reads the u64 as two i32s — same bytes.)

#### Sub 0x15 OutlandsItemNameResponse (item-name query results)
`Assistant.PacketHandlers.OutlandsItemNameResponse` @ 0x140064f20 (CUO twin:
`ItemNameRequestManager.HandleResponse` @ 0x140143900). Answer path for the client's
entity-info queries — the legacy `09`/`34`/`98` id+serial requests and the dialect C2S
sub-9 detail query (`ff 000e 00000009 01 0001 <serial>`) all fired together on vendor
hovers in session_20260928_164548.

| Off | Size | Type | Field | Confidence |
|-----|------|------|-------|------------|
| 0 | 1 | u8 | mode flags; bit0 set → empty names clear the cached name, unset → NUL/`\n`-truncated name stored | decomp |
| 1 | 2 | u16be | entry count | decomp |
| 3 | … | | per entry: u32be serial, asciiz name | decomp |

#### Sub 8 OutlandsBuffUpdate (custom buff gump data)
CUO @ 0x14019a600. Payload:

| Off | Size | Type | Field | Confidence |
|-----|------|------|-------|------------|
| 0 | 4 | u32be | mobile serial (buff target) | decomp |
| 4 | 2 | i16be | buff/icon id candidate | decomp |
| 6 | 2 | u16be | field | decomp |
| 8 | 2 | i16be | field | decomp |
| 10 | 2 | u16be | field | decomp |
| 12 | 2 | i16be | field | decomp |
| 14 | 2 | i16be | timer-list count N | decomp |
| 16 | 12×N | | per timer: f32 seconds, u64be end-timestamp (0 → ∞) | decomp + wire |
| — | 8 | u64be | buff start/end timestamp | decomp + wire |
| — | … | asciiz | title; **if empty**: u32be cliloc id (looked up instead) | decomp + wire |
| — | … | asciiz | description text | decomp + wire |
| — | 2 | i16be | category/type | decomp + wire |
| — | 2 | i16be | mode (== 5 flips a display flag) | decomp + wire |
| — | 4 | f32 | scalar (0 → default constant) | decomp + wire |

**Wire corrections (2026-09-29, 121 real sub-8 packets, all four sizes 144/129/78/57 B
consume exactly):** a timer is **12 bytes** on the wire — the decomp reads a 4-byte
float (`FUN_1407f4200`) and a u64; the 16-byte stride is the in-memory list element,
not a wire u32 "aux" (earlier text). The floats decode as **big-endian** (timer seconds
`40400000` = 3.0, `41166666` = 9.4, `3d75c28f` = 0.06; as little-endian they are
denormals). Real title forms: `"Stationary Penalty"` + description, or empty title +
cliloc 1015176 + `"Armor Rating Increase"`.

#### Sub 9 OutlandsRemoveBuff
u32be mobile serial, u16be buff/icon id (both BE). decomp.

#### Sub 0x16 OutlandsMobileData (custom mobile overlay)
CUO @ 0x1401a0980. Payload:

| Off | Size | Type | Field | Confidence |
|-----|------|------|-------|------------|
| 0 | 4 | u32be | mobile serial | decomp |
| 4 | 4 | u32be | field (+0x144) | decomp |
| 8 | … | asciiz | overlay string 1 (+0xd8) | decomp |
| — | 2 | u16be | value, 0 → 100 (+0x15e/0x160/0x162 triple: cached/current/prev display value with change timestamp) | decomp |
| — | … | asciiz | overlay string 2 (+0xe0) | decomp |
| — | … | asciiz | overlay string 3 (+0xe8) | decomp |
| — | 4 | u32be | field (+0x148) | decomp |
| — | 1 | u8 | flag (+0x175) | decomp |
| — | 1 | u8 | flag (+0x176) | decomp |

#### Sub 0xDEAD OutlandsCorpseFlags
CUO @ 0x14019a440. Payload: u32be corpse serial, u32be flags (+0xdc), u8 notoriety
(+0xc1), asciiz corpse name (+0xa0). decomp.

#### Sub 0x1A HandleQuestArrow (Outlands dialect arrow; added 2026-09-29, live-confirmed 2026-10-01)
`protocol_handlers.c:14307-14582`.
- mode 0: event `quest_arrow_set` {arrow_id, type, v16, serial, x, y, z, text}: u16 arrow id,
  a skipped byte, u8 type, u16 v16, then u32 target serial, u32 x, u32 y, i32 z, asciiz text.
- mode 1: `quest_arrow_cancel` {arrow_id}.
- mode 2: `quest_arrow_clear`.
- Other modes count as dialect_unhandled.

**Live (session 20261001_214649, Tracking in Hunting mode):** this is the Tracking arrow. 5 sets
and 4 cancels, e.g. `ff 0034 0000001a 00 0000 00 03 0000 0015aac5 0000078b 00000a37 00000000
"[Hunting] Joel Embiid"`. The serial is the tracked mobile; for the in-view targets x/y equalled
its world-model position at that moment. The 4th u32 is z: a pack llama 20 tiles away, which the
server never sent us otherwise, came as `0132954e` at (1931,2596) z 21 while we stood at z 10.
type 3 and v16 0 every time. A new target cancels the old arrow (mode 1) and sets the next id
(0, 1, 2…), even when it's the same mobile again. The arrow is a snapshot: nothing moves it
until the next hit. Tracking never used 0xBA or 0x7C.
Details and the Tracking gump: docs/NOTES.md "Tracking"; pinned in `harness/test_world_units.py`.

### 0xD6 EncodedPacket (S2C) / 0xD7 ClientEncodedPacket (C2S)
Razor registers S2C 0xD6 → `EncodedPacket` (mega-cliloc viewer; not in priority set)
and C2S 0xD7 → `Assistant.PacketHandlers.ClientEncodedPacket` @ 0x140066760.

C2S 0xD7 layout as observed by the client:

| Off | Size | Type | Field | Confidence |
|-----|------|------|-------|------------|
| 1 | 2 | u16be | packet length | upstream |
| 3 | 4 | u32be | serial (read, unused) | upstream |
| 7 | 2 | u16be | sub-id | upstream |
| 9 | 1 | u8 | sub-sub-id — handled only when sub-id == 0x19 | decomp |
| 10 | 4 | i32be | value — read only when sub-sub-id == 0; feeds a script/counter text command (< 14) | decomp |

### 0xBF ExtendedPacket (S2C) — sub-id switch
`Assistant.PacketHandlers.ExtendedPacket` @ 0x140065e00 (Razor S2C viewer).
CUO's own 0xBF handler is `ExtendedCommand` @ 0x140194bc0 (7 kB, large sub-table —
not fully extracted; see Open Questions).

Frame: `BF <len u16be> <subId u16be> …`. Sub-ids handled by the Assistant viewer
(matches upstream Razor source, with one Outlands addition):

| SubId | Payload (from packet off 5) | Meaning | Confidence |
|-------|------------------------------|---------|------------|
| 0x04 | u32be gumpId | close gump (removes from player gump list) | upstream |
| 0x05–0x07 | — | read nothing here (upstream 0x06 = party message, parsed in `OnPartyMessage`) | upstream |
| 0x08 | u8 mapIndex | map change | upstream |
| 0x14 | i16be (0x0001), u32be serial, u8 count, count × (u16be clilocIdx, u16be num, u16be flags, [u16be color if flags&2]) | context (popup) menu entries | upstream |
| 0x18 | i32be count, then 2×count × i32be | map patch list | upstream |
| 0x19 | u8 sub: **0** → u32be serial, u8 bool (Outlands-added: sets flag +0x50 on mobile); **2** → u32be serial, u8 pad, u8 stat-locks (bits 4-5 Str, 2-3 Dex, 0-1 Int) | stat locks + custom flag | upstream + decomp |

### OutlandsClientPacket (C2S dialect viewer)
`Assistant.PacketHandlers.OutlandsClientPacket` @ 0x1400602f0 views the client's own
outbound 0xFF dialect frames: reads subId u32be; only sub 4 is handled — u8 flag (must
be 0), u16be spellId, spell-name lookup for Razor's casting display. Matches the
captured C2S sub-4 frames byte-for-byte (see C2S subsection above).

---

## 6. Wire validation summary (2026-09-29)

The former §6 ("the 0x00-family world-state stream") described decode garbage and was
removed; see docs/WORLDSTATE.md for what went wrong. Replacement evidence, from all 18
correctly decoded captures (53 277 S2C packets, 0 length-table mismatches):

- Ids 0x00, 0x3F, 0x40, 0x52, 0x6F, 0x9E, 0xDC never occur. The top S2C ids are 0xFF
  (37 299), 0x77 (7 423), 0x6E (2 571), 0xF3 (903), 0x11 (709), 0x20 (607), 0x78 (597),
  0x22 (533), 0x1C (524). Full id/length census: docs/PROTOCOL.md.
- Layout fixes made against real packets: 0x11 (optional TithingPoints), 0x1B (full
  V10 layout), 0x20 (any mobile, signed z), 0x3A (type-2 end terminator), 0x77 (new),
  0x78 (new), 0xDD (line-count u32, UTF-16BE), 0xFF sub 8 (12-byte timers, BE floats),
  0x1C/0xAE/0x98/0xA9 (new, upstream layouts). Unchanged and confirmed by values:
  0x1D (`1d 000001e8`), 0x22, 0x24, 0x25, 0x2E, 0x3C, 0x6C, 0x6E, 0x72, 0xA1–A3, 0xF3,
  0x89, 0xFF subs 3/9/0x15.
- C2S (decode was always correct): 0x02 movement dir/seq/key (`02 86 00 00000008` —
  dir 0x86 = dir 6 | running 0x80). **Corrected 2026-09-29:** C2S 0x6C target response is
  the Outlands 27-byte form, u32 x/y/z/graphic (`6c 00 00052cb9 01 00094375 0000077c
  00000a24 00000000 00000190`). The earlier "19 B standard" reading came from mis-framed
  captures (docs/NOTES.md). C2S 0xB1 text-entry lengths are UTF-16 unit counts.
- **Added 2026-09-29 (docs/LUMBER_LOOP.md §8):**
  - S2C 0xC1/0xCC cliloc messages (upstream layout, same reads as `DisplayClilocString
    @ 0x140196760`; 11 real 0xC1 across 3 captures)
  - S2C 0x74 buy list and 0xBF sub 0x14 context menu (real packets)
  - C2S 0x3B buy, 0x12 text command, 0xBF subs 0x13/0x15 (real packets)
  - C2S 0x07 lift, 0x08 drop (22 B V10+), 0x13 equip (decompile-grounded senders, same
    layouts as `harness/actions.py`)

  Events: `cliloc`, `buy_list`, `buy`, `command`, `popup`, `popup_request`,
  `popup_select`, `lift`, `drop`, `equip_request`.
- Replay (`harness/replay.py 20260929_144541`) yields self serial 0x00094375, name
  TestWorth, login position (0x7AB, 0xA25, 0) from 0x1B and final position
  (0x7A6, 0xA25, 0) from the last self 0x20/0x77 — from S2C alone.

---

## Open Questions

1. **0x6C TargetCursor +8 tail** (offsets 19–26) — never read by the client; purpose
   unknown (possibly targeting metadata for the CUO fork's target indicators).
2. **0x24 OpenContainer bytes 9–10** — unread; Outlands V10 widened gumpId over bytes
   5–8 leaving 9–10 unexplained (wire: `00 7d`).
3. **0x25/0x3C/0x78/0x2E V12 u32 tail field** — skipped by CUO, read as i32 by Razor's
   container filters; wire values 0x20 (worn/contained items), 0x22, 0x200 (backpack),
   0x220; looks like a flag word, semantics unknown.
4. **0xF3 byte 13–14 pair and byte 29** — V11 1-byte field + one skipped byte; byte 29
   (tabulated as `dir`) is 0x2B on 901 of 903 real packets — meaning unknown.
5. **0xBF CUO ExtendedCommand sub-table** (@ 0x140194bc0, 7 kB) not extracted. S2C subs
   on the wire: 0x01 (fastwalk seeds), 0x08 (map), 0x14 (context menu), 0x19 (stat
   locks); C2S subs 0x0C/0x13/0x15 carry entity serials.
6. **S2C dialect sub 4 payload** (`SpellCastManager.OnPacketResponse` @ 0x140306390),
   the **PartyManager sub-parsers** (0xFF subs 10–0x0E), and the payloads of the
   frequent subs 5 (7 B, no payload), 0x16 (26 B), 0x1C (50 B), 7 and 1 — not parsed.
7. **Sub-0x19/0 (0xBF) mobile flag +0x50** and **0xFF-sub-0x1D global dword** — set but
   consumers not traced.
8. **0x1B bytes 26–42**, **0xA9 7-byte tail**, **S2C 0xF0 `f0 000c fe …`** — unread /
   unparsed; 0x11 StatsCap reads 0 for the test character (real, or a shifted field?).
9. **Assistant readers' exact start offsets** are assumed to follow the
   fixed=1 / variable=3 convention (upstream `MoveToData`); only read sequences, not
   absolute reader positions, were verified for Assistant handlers.

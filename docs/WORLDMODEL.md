# WORLDMODEL.md — Outlands S2C packet field layouts (priority game-state set)

Authoritative field-layout reference extracted from the decompiled client handlers in
`decompiled/protocol_handlers.c`, cross-referenced against the upstream sources
(`ClassicUO-main/src/ClassicUO.Client/Network/PacketHandlers.cs`,
`Razor-master/Razor/Network/Handlers.cs`) and the authoritative length tables
(`outlands_packet_table.json` base tier + `harness/uo/outlands_table.py` EXTRA).

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
- Exception to BE rule: the Outlands custom dialect embeds **little-endian float32**
  fields (read via `FUN_1407f4200`, a `MemoryMarshal.Read<float>`-style helper with no
  byte swap). Flagged per-field.

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

Variable-length (not in base table): 0x11, 0x16, 0x17, 0x1A, 0x3A, 0x3B, 0x3C, 0x89,
0xB0, 0xBF, 0xD6, 0xDD, plus the dialect/world-data ids 0xFF, 0x3F, 0x52 (§5–§6).

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

### 0x20 UpdatePlayer ("MobileUpdate") — S2C, fixed 28 (V10+; legacy 19)
Handlers: `ClassicUO.Network.PacketHandlers.MobileUpdate` @ 0x1401888a0 (legacy path,
dispatches to V10 when version > 9) and `MobileUpdateV10` @ 0x140188d10.
Corroborated by `Assistant.PacketHandlers.MobileUpdate` @ 0x140063af0 (same
version-gated two-variant structure).
Only ever carries the player serial (handler no-ops for other serials).

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
| — | 4 | u32be | compressed text-lines length | upstream |
| — | 4 | u32be | decompressed text-lines length | upstream |
| — | … | u8[] | zlib block: u16be line count, then count × (u16be len + UTF-16LE text) | upstream |

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

---

## 5. Outlands custom dialect

> ### Verification addendum (2026-09-28, post-extraction)
>
> 1. **C2S dialect frame CONFIRMED**: `ff <len u16be> <subId u32be> <payload>`. `Send_TimeSyncPingReq` writes id `0xff`, len placeholder, then LE `0x3000000` (= wire bytes `00 00 00 03` = subId 3 BE) → sub 3 = TimeSyncReq, matching the sub-table.
> 2. **The 19-byte server prelude IS the sub-0 handshake**: `ff 00 0d <subId u32be = 0> <payload>`; payload starts with **protocol version u32be = 0x0000000c (12)** — the client preamble `ef 0000000c` echoes the same version constant. **Protocol version is 12 → the V12 layouts in this document are the active ones.** The remaining prelude bytes carry the session cipher key S (byte 12) and handshake flags; the final 6 bytes (13–18) precede the Huffman stream and are not yet structurally assigned.
> 3. **No 0xF0 packets appear in either captured session** (idle-town play: dialect subs fire on gameplay events — vendors/spells/buffs/party — not on idle). The 0xF0 outer-id assignment below therefore remains **unproven**; given (1) and (2), the S2C dialect more plausibly rides **0xFF** as well. A dialect-traffic capture (open a vendor, cast a spell, take a buff) will settle both the outer id and the sub-payload layouts.
> 4. One 117-byte 0xFF packet observed (live @57015) does not parse under the simple frame — batching or a distinct server-side frame; defer to the dialect-traffic capture.
> 5. **C2S dialect subs CONFIRMED (dialect session 20260928_164548):**
>    - **sub 3** = TimeSyncReq (empty payload) — cadence ~1/s.
>    - **sub 4** = cast spell: `ff 000a <sub=4> <spellId u16be>` (observed 0x0005, 0x000f).
>    - **sub 9** = item/object detail query: `ff 000e <sub=9> 01 00 01 <serial u32be>` — fired on every vendor-item dclick/hover (serials match dclick targets). S2C answer is the 0x00-family record stream below.
> 6. **Vendor buy**: C2S `3b <len> <vendorSerial u32be> …` observed (vendor serial matches nearby-NPC serial).
> 7. **S2C 0x00-family (106 B)**: periodic bursts (~1/s) carrying repeating/overlapping world-data sub-records (`00 1b 85 00 dc`, `00 50 85 00 c7`, `00 83 0072 …` patterns recur across packets) — assessed as the live world-state sync stream for the dialect; sub-record layout still open (handler extraction next).
> 8. One C2S `0x00` 106 B packet contained two back-to-back `ff` dialect packets — possible C2S batching (or a flush-boundary artifact); open.
>
>
### 0xFF OutlandsProtocol / OutlandsServerPacket — dialect carrier, both directions, variable length, u32 sub-id

**Carrier CONFIRMED = 0xFF** (supersedes the v1 0xF0 inference — no 0xF0 S2C packet
appears in any capture; `RunUOProtocolExtention` @ 0x140066380, the upstream Razor 0xF0
handler, is dead code in this build):

- S2C: the 19-byte session prelude is exactly one dialect frame
  `ff 00 0d | 00000000 | 0000000c 12 e7` = subId 0 handshake (see sub 0 below). The
  payload matches `OutlandsProtocol` case 0 field-for-field (u32be version + u8 + u8),
  and `BuildPacketTable(12)` reproduces every observed wire length (0xF3=38, 0x25=27,
  0x2E=20, 0x20=28, 0x21=15 …). **Protocol version = 12 confirmed on the live server.**
- C2S: 251 keepalive frames `ff 0007 00000003` (sub 3) plus sub-4/sub-9 frames in
  session_20260928_164548 (see C2S subsection). Write side proven in
  `NetClientExt.Send_TimeSyncPingReq` @ 0x14017e940: `GetPacketLength(0xff)` (−1 →
  variable), stores `*(u32*)buf = 0x03000000` — LE store of bytes `00 00 00 03` =
  subId 3 BE on the wire.
- The CUO-side *registration* of 0xFF → `OutlandsProtocol` is still inferred (the
  handler-table cctor is not in the decompiled selection), but the content match above
  leaves no practical doubt.

Frame: `FF <len u16be> <subId u32be> <payload…>`. Sub-id space is **per-direction**
(e.g. sub 9 S2C = RemoveBuff, sub 9 C2S = item-detail query).

CUO S2C dispatcher: `ClassicUO.Network.PacketHandlers.OutlandsProtocol` @ 0x14019f3a0 —
reads subId u32be @3, switches (payload offsets are from payload start = packet off 7):

| SubId | Target | Payload layout |
|-------|--------|----------------|
| 0 | handshake (inline) | u32be **protocol version** (=12 live; → the global V10/V11/V12 gate at settings+0x68), u8 flag1 (+0x71; 0x12 observed), u8 flag2 (+0x72; 0xE7 observed — matches the session key logged by the proxy), then `BuildPacketTable(version)` |
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
| 16 | 16×N | | per timer: **f32le** seconds, u64be end-timestamp (0 → ∞), u32be aux | decomp |
| — | 8 | u64be | buff start/end timestamp | decomp |
| — | … | asciiz | title; **if empty**: u32be cliloc id (looked up instead) | decomp |
| — | … | asciiz | description text | decomp |
| — | 2 | i16be | category/type | decomp |
| — | 2 | i16be | mode (== 5 flips a display flag) | decomp |
| — | 4 | f32le | scalar (0 → default constant) | decomp |

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

## 6. The 0x00-family world-state stream (empirical)

Wire evidence from session_20260928_164548 (Huffman-decoded s2c stream, framed with the
authoritative length table; the jsonl side-log truncates packet hex at 64 B and
mis-frames some embedded records — trust the raw-stream framing):

| ID | Length | Observed | Role |
|----|--------|----------|------|
| 0x00 | fixed 106 (EXTRA table, wire-proven) | 18× in ~50 s, ~1/s | periodic world-state sync stream |
| 0x40 | fixed 201 (base table) | 1× | world-data batch (same record stream) |
| 0x3F | variable (absent from table) | 1×, len 13155 | large world-data dump (initial load) |
| 0x52 | variable (absent from table) | 1×, len 30627 | large world-data dump (initial load) |

Evidence that all four carry the **same sub-record stream**: the byte run
`0f 52 40 33 4c 00 31 c7 61 82 00 18` appears verbatim both at the head of the 0x3F
payload and inside a 106-byte 0x00 frame; the 201-byte 0x40 frame's payload similarly
consists of the record motifs below. 0x00 frames chop a continuous record stream —
records recur across frames with overlapping content (incremental updates).

Recurring sub-record motifs (confidence: **unknown**, pattern-observed; the parser is
not in the decompiled selection — see Open Questions):

| Motif (hex) | Len | Notes |
|-------------|-----|-------|
| `72 40 00 00 ac` | 5 | recurs verbatim across frames (also mis-framed as top-level 0x72 by the jsonl logger) |
| `90 00 33 00 dc 00 1b 85 00 dc 00 68 00 00` | 16 | recurs verbatim (jsonl mis-framed it as a 19-B "0x90" packet by swallowing the next record's head) |
| `d4 00 00 83` | 4 | follows the 0x90-motif |
| `31 00 0f 02 32 17 ca 0e 00 62 00 00 1e 00 83` | 15 | recurs verbatim |
| `31 c7 01 1c …` / `31 c7 61 82 00 18` | ~10 | carries mobile-range serials 0x31C7011C / 0x31C76182 |
| `00 50 01 ed 1f 55 …` / `00 50 85 00 c7 28 0a 09 00 23 00` | ~11–12 | record family with varying tails |
| `00 40 00 6e 6e c9 00 0f 06 00` | 10 | recurs verbatim |

Framing notes validated on this session:
- 0x5C is **fixed 2** here (`5c 00` followed by a cleanly-framed 0x52 dump; treating
  0x5C as variable desyncs immediately). This contradicts an earlier pipeline note that
  needed `EXTRA[0x5C] = -1` — the base table's 0x5C=2 is what frames this session.
- Genuine S2C top-level ids in the session: 0x82, 0xF6 (BoatMoving, 119 B), 0x02,
  0x5C, 0x52, 0xBE (AssistVersion, 5 B and 10 B), 0x6A, 0x77 (**18 B** — matches the
  Outlands-extended base-table length, not upstream 15), 0x29, 0x40, 0xDC (9 B),
  0x00, 0x32, 0xCA (6 B), 0x1E, 0x72, 0x01. The jsonl's standalone 0x90/0x31/0x72(17 B)
  entries are mis-frames of 0x00-family record content.
- Validated v1 layouts against capture: 0x1D DeleteObject `1d 07720024` (serial u32be
  ✓, 5 B); C2S 0x02 movement unchanged (dir/seq/key: `02 86 00 00000008` — dir 0x86 =
  dir 6 | running 0x80, seq increments); C2S 0x6C target response stays 19 B standard
  (observed `6c 00 00052cb9 01 00094375 0000 077c 00 0a24` = type 0, cursorID
  0x00052CB9, cursorType 1, clicked serial 0x00094375, x 0, y 0x077C, z 0, graphic
  0x0A24) — the S2C-only extension of 0x6C to 27 B does not affect C2S.

---

## Open Questions

1. **0x00/0x40/0x3F/0x52 world-data record grammar + parser location** — the sub-record
   stream above is empirical only. No handler for id 0x00 (or 0x40/0x3F/0x52) appears
   in the decompiled selection (`protocol_handlers.c` covers PacketHandlers/NetClient/
   NetClientExt only), and no candidate name exists in the method metadata map; the
   parser is presumably a CUO handler outside the selected set. Next step: Ghidra-decompile
   the function registered in the CUO handler table slot for 0x00 (registration cctor),
   or xref-scan for a reader loop over 106-byte buffers.
2. **CUO-side 0xFF registration** — content-matched (sub-0 prelude ↔ `OutlandsProtocol`
   case 0 ↔ `BuildPacketTable(12)` ↔ observed wire lengths) but the handler-table cctor
   is not decompiled; formal proof outstanding. No competing 0xFF handler exists in the
   binary (`RunUOProtocolExtention` is the upstream 0xF0 handler and is unreferenced by
   any capture).
3. **0x6C TargetCursor +8 tail** (offsets 19–26) — never read by the client; purpose
   unknown (possibly targeting metadata for the CUO fork's target indicators).
4. **0x24 OpenContainer bytes 9–10** — unread; Outlands V10 widened gumpId over bytes
   5–8 leaving 9–10 unexplained.
5. **0x25/0x3C V12 u32 tail field** — skipped by CUO, read as i32 by Razor's container
   filters (feeds an item "extra data" slot); semantics unknown (price? quality?).
6. **0xF3 byte 13–14 pair** — V11 introduced a 1-byte field passed to the item builder
   plus one skipped byte; meaning unknown.
7. **0xBF CUO ExtendedCommand sub-table** (@ 0x140194bc0, 7 kB) not extracted; the
   Assistant 0xBF viewer table covers the overlapping cases only. Session shows C2S
   0xBF subs 0x0C/0x13/0x15 carrying entity serials (entity-info queries).
8. **S2C dialect sub 4 payload** (`SpellCastManager.OnPacketResponse` @ 0x140306390)
   and the **PartyManager sub-parsers** (0xFF subs 10–0x0E) — not in the decompiled
   selection.
9. **Sub-0x19/0 (0xBF) mobile flag +0x50** and **0xFF-sub-0x1D global dword** — set but
   consumers not traced.
10. **Assistant readers' exact start offsets** are assumed to follow the
    fixed=1 / variable=3 convention (upstream `MoveToData`); only read sequences, not
    absolute reader positions, were verified for Assistant handlers.

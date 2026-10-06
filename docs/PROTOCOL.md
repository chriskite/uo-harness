# Protocol state — packet map (2026-09-28)

## Authoritative sources (2026-09-28, Ghidra + NativeFormat map)

The NativeAOT reflection metadata was fully parsed (`mrt_parse.py` → `mrt_map.json`, 95,389 invoke entries, 100% name→RVA resolution). All 372 protocol methods (ClassicUO.Network.PacketHandlers, NetClientExt, Assistant.PacketHandlers, PacketsTable) were renamed and decompiled to `decompiled/protocol_handlers.c` (372/372, no failures).

**Packet length table — authoritative** (`harness/uo/outlands_table.py` + `outlands_packet_table.json`):
- Base tier (145 entries) from the client's own static initializer `FUN_1401a13c0` (`Add(dict,id,len)` calls). The 22 Outlands-extended standard packets: `0x08=22, 0x0B=7, 0x1B=43, 0x20=28, 0x21=15, 0x23=42, 0x24=11, 0x25=27, 0x2E=20, 0x54=18, 0x56=15, 0x6C=27, 0x76=22, 0x77=18, 0x90=31, 0x95=11, 0x99=36, 0xB9=5, 0xBA=14, 0xC0=52, 0xEF=5, 0xF3=38` (vs upstream: 19 wider by 1–16 bytes of Outlands fields; `0xEF` narrower, 21→5; `0x0B` and `0xB9` unchanged in length).
- **No wire-derived extras.** The former `0x00=106, 0x6F=76, 0x9E=65, 0xDC=15` entries were derived from the wrong S2C decode (below) and were removed 2026-09-29: none of these ids occurs in any correctly decoded capture.
- Result (2026-09-29, correct decode): all 53 277 S2C packets in the 18 `logs/session_*.s2c.raw` captures have exactly the table length (0 mismatches).

**Correction 2026-09-29 — the old "world-state stream" and "custom S2C dialect" were decode garbage.** Every S2C decode before 2026-09-29 skipped the XOR layer (docs/CIPHER.md §4) and started Huffman at byte 19. The `0x00/0x40/0x3F/0x52` "world-state stream", the "content is not standard UO" finding and the request/response correlation table that used to be here were artifacts of that. `docs/WORLDSTATE.md` is now only a supersession notice (plus the still-valid code-backed handler-table findings).

**`Send_TimeSyncPingReq` SOLVED (decompiled @ 0x14017e940):** emits exactly `ff 00 07 00 00 00 03` — the ~1/s keepalive. **It carries no timestamp**; the anti-speedhack timing channel is purely the *arrival cadence* of this packet server-side. Our relay preserves it; agent action generation does not affect it (the stock client keeps pinging on its own schedule). The `Speedhack/AutoClicking/AutoKeyboard` enum's consumer remains unlocated (likely server-side only). S2C answers it with `0xFF` sub 3 (u64be server time, 15 B).

**Handler surface for layouts**: `ClassicUO.Network.PacketHandlers` (100-slot S2C dispatch table, `cuo_handler_table.json`), `Assistant.PacketHandlers` (76 methods incl. `OutlandsServerPacket` @ 0x64eb0, `OutlandsItemNameResponse` @ 0x64f20) and `ClassicUO.Network.NetClientExt` (104 methods), all decompiled. Verified S2C field layouts: docs/WORLDMODEL.md.

## C2S (client→server) — 100% mapped

Standard UO layouts with the Outlands V10+ widenings, XOR session key (see CIPHER.md). **C2S framing uses the client's own version-12 length table** (the same one as S2C): every `NetClientExt.Send_*` sizes and zero-pads its packet with `PacketsTable.GetPacketLength(id)`, whose tiers (base + V10/V11/V12, `FUN_1401a13c0`, applied by `BuildPacketTable` @ 0x1401a1140) give e.g. `0x08=22`, `0x6C=27`. `harness/uo/packets.py` `C2S_OVERRIDES` = every id where that table differs from upstream, so `packet_length(buf, overrides=C2S_OVERRIDES)` frames all 18 C2S captures end-to-end (19 773 packets, 0 desyncs). Before 2026-09-29 the overrides were only `{0x91: -1}`: the captured 27-byte client target framed as 19 bytes and the remaining `00000001 00000190 ff…` became a 106-byte `0x00` "packet" that swallowed the following keepalives (sessions 141253 and 164548), and the proxy rejected correct 22-byte drops and 27-byte targets.

Senders branch on the protocol version at settings+0x68 (live = 12): `< 10` writes the upstream u16/i8 coordinates, `>= 10` writes u32 x/y/z/graphic. The captured targets are the `>= 10` form.

| ID | Meaning | Notes |
|---|---|---|
| `91 <len:2BE> <name\0> <JWT>` | game login | Outlands custom (length-prefixed, ~1122B) |
| `5D` 73B | character select | standard, `0xEDEDEDED` pattern |
| `09` 5B / `34` 10B / `98` 7B | entity queries (id + serial) | repeated per nearby entity; `34` type 4 = status, type 5 = skills (`Send_SkillsRequest` @ 0x14014f420) |
| `02` 7B | walk | standard |
| `05` 5B | attack | `Send_AttackRequest` @ 0x1401509d0 |
| `06` 5B | dclick | standard |
| `07` 7B | lift | `07 <serial> <amount u16>` (`Send_PickUpRequest` @ 0x14014bfb0) |
| `08` **22B** | drop | `08 <serial> <x u32> <y u32> <z i32> <grid u8> <container u32>` (`Send_DropRequest` @ 0x14014c700 V10+ branch; upstream 15B is the < V10 branch) |
| `12` var | text command | `12 <len> 24 "<skill> 0" 00` use skill (`Send_UseSkill` @ 0x140153660, the only skill sender); `12 0005 58 00` open door (captured) |
| `13` 10B | equip | `13 <serial> <layer u8> <wearer u32>` (`Send_EquipRequest` @ 0x14014d570) |
| `3B` var | vendor buy | `3b <len> <vendor> 02 n×(1a <item u32> <amount u16>)`; captured `3b 000f 000001e2 02 1a 450a6eac 0001` |
| `6C` **27B** | target response | object: `6c 00 <cursor u32> <flags u8> <serial> <x u32> <y u32> <z i32> <graphic u32>`, captured `6c 00 00052cb9 01 00094375 0000077c 00000a24 00000000 00000190`; ground: type `01`, serial 0, graphic 0 for land (`Send_TargetXYZ` @ 0x14015d120); cancel: `6c <type> <cursor> <flags> 00000000 7fffffff×3 00000000` (`Send_TargetCancel` @ 0x14015e400) |
| `72` 5B | war mode | `72 <on> 32 00 00` (`Send_ChangeWarMode` @ 0x14014dde0) |
| `9A` var | ASCII prompt reply | `9a <len> <serial u32> <prompt id u32> <!cancel u32> <ascii> 00` (u64 echoed from the server's 0x9A) |
| `9F` var | vendor sell | `9f <len> <vendor> <n u16> n×(<item u32> <amount u16>)` |
| `AC` var | text entry dialog reply | `ac <len> <serial> <parent u8> <button u8> <ok u8> <len+1 u16> <ascii> 00` |
| `AD` | unicode speech | standard. **Keyword-encoded when speech.mul matches** (client `Send_UnicodeSpeechRequest` @ 0x140151c20, `IsMatch` @ 0x1401bba40 = upstream algorithm): type `\|= 0xC0`, then 12-bit count + 12-bit ids nibble-packed, then UTF-8 text + `00`; else UTF-16BE + `0000`. Capture 20260929_161433 "bank" = `ad 0016 c0 02b2 0003 454e5500 0020020020 62616e6b 00` (ids [2, 2]). NPCs (banker) key off these ids. Port: `harness/uo/speech.py` |
| `B1` var | gump response | `b1 <len> <serial> <gump> <button> <n u32> n×<switch u32> <m u32> m×(<id u16> <chars u16> <utf16be>)`; the client replaces `\n` with `\x1f` and caps an entry at 0x800 units; captured with one empty entry `… 00000001 0002 0000` |
| `BF` var | extended | captured subs (upstream names): `0005` window size, `000b` language, `000c` close status gump, `000f` client type, `0013` context-menu request `bf 0009 0013 <serial>`, `0015` context-menu pick `bf 000b 0015 <serial> <index u16>` (standard BF, no `0xFF`-dialect substitute) |
| `C2` var | unicode prompt reply | `c2 <len> <serial> <prompt id> <!cancel u32> <lang[3]> 00 <utf16le text>` (no terminator) |
| `C8` 2B, `F0` 4B, `32` 2B | misc standard | |
| `D7` var | encoded command | captured `d7 000a 00094375 0032 00` (not built) |
| `ff 00 07 00 00 00 03` | **Outlands keepalive ~1/s** | `Send_TimeSyncPingReq`; relay only, never injected |

Action builders (all rows except login, character select, `D7`, the keepalive and the misc row): `harness/actions.py`; its module docstring lists layout + evidence per builder.

## S2C (server→client) — standard UO content, Outlands-widened layouts

- Wire format (docs/CIPHER.md §4, `harness/uo/s2c.py`): 13-byte cleartext prelude `ff 00 0d | 7x 00 | 0c <s2c_key> <c2s_key>` (itself the `0xFF` sub-0 handshake: protocol version 12 + both XOR keys), then every byte XOR `s2c_key` and UO static Huffman. Each packet is compressed separately and ends with a flush, so one flush segment = one packet — no framing heuristics needed.
- Content is standard UO with the Outlands V10/V12 widenings (u32 graphics/coordinates, extra tails) and the `0xFF` dialect for Outlands features. Offline decode + world model: `harness/replay.py` (`python harness/replay.py <session tag>`).
- Ids observed across all 18 captures (count, length):

| ID | Count | Length | Meaning |
|---|---|---|---|
| `11` | 709 | var 43–91 | CharacterStatus (self type 5 = 87 or 91 B; others type 0 = 43 B) |
| `1B` | 18 | fixed 43 | LoginConfirm — self serial `0x00094375`, graphic, x/y/z, dir |
| `1C` | 524 | var | Talk (ASCII) — NPC name labels, system text |
| `1D` | 226 | fixed 5 | DeleteObject |
| `20` | 607 | fixed 28 | MobileUpdate V10 — self **and** other mobiles |
| `22` | 533 | fixed 3 | ConfirmWalk `22 <seq> <noto>` |
| `24` / `25` / `3C` | 24 / 417 / 25 | 11 / 27 / var | OpenContainer / ContainedItem / ContainerContent (V12) |
| `2E` | 113 | fixed 20 | EquipItem V12 |
| `3A` | 28 | var | UpdateSkills (type 2 full list; type 0xDF single) |
| `54` | 170 | fixed 18 | PlaySound (x/y widened) |
| `55` / `5B` / `BC` / `C8` / `B9` | 18 each / 36 / 36 / 54 | 1 / 4 / 3 / 2 / 5 | LoginComplete / Time / Season / ViewRange / Features |
| `6C` | 2 | fixed 27 | TargetCursor |
| `6E` | 2571 | fixed 14 | CharacterAnimation |
| `72` | 36 | fixed 5 | Warmode |
| `74` | 2 | var 50 | vendor buy list |
| `77` | 7423 | fixed 18 | MobileMove V10 (serial, x/y/z u32, dir) |
| `78` | 597 | var | MobileEquip V12 (serial + equipment records only) |
| `88` | 18 | fixed 66 | OpenPaperdoll |
| `89` | 1 | var | CorpseEquipment |
| `98` | 318 | var 37 | UpdateName (serial + name[30]) |
| `A1`/`A2`/`A3` | 104/23/39 | fixed 9 | hits/mana/stamina |
| `A9` | 18 | var 161 | CharacterList (5 × name[30], no password field) |
| `AE` | 88 | var | UnicodeTalk |
| `BF` | 161 | var | Extended (sub 1 fastwalk seeds, sub 8 map, sub 0x14 context menu, sub 0x19 stat locks) |
| `C0` / `C1` | 12 / 11 | 52 / var | graphical effect / cliloc message |
| `DD` | 77 | var | CompressedGump |
| `F0` | 18 | var 12 | Outlands login-time `f0 000c fe …` frame (unparsed) |
| `F3` | 903 | fixed 38 | UpdateItemSA V12 |
| `FF` | 37 299 | var | Outlands dialect: subs 1, 3 (time sync, 12 331×), 4, 5 (23 915×), 7, 8 (buffs), 9, 0x15 (names), 0x16, 0x1C, 0x1D, 0xDEAD |

Ids the harness parses (0x11, 0x1B, 0x1C, 0x1D, 0x20, 0x22, 0x24, 0x25, 0x2E, 0x3A, 0x3C, 0x6C, 0x6E, 0x72, 0x77, 0x78, 0x89, 0x98, 0xA1–A3, 0xA9, 0xAE, 0xDD, 0xF3, 0xFF subs 0/3/8/9/0x15) parse on every real packet of all 18 captures (0 parse failures, `harness/test_world_replay.py`) with values spot-checked for plausibility; layouts and evidence in docs/WORLDMODEL.md. The others are the upstream ClassicUO id names, spot-checked on one sample each (e.g. `5b 13 2d 2b` = 19:45:43, `c8 12` = range 18, `74` = price + name list, `54` coords 0x7a5/0xa36 widened to u32).

## Methodological notes

- One wrong assumption (no XOR, Huffman from byte 19) produced a self-consistent-looking but false picture that survived for a day: recurring "records", a guessed length table that framed "cleanly" for long runs, and a narrative ("custom dialect") that explained the missing standard content. The check that would have caught it: look for known plaintext (the player serial from C2S, the character name) in the S2C decode. With the correct decode it is everywhere.
- Accept a decode only if it reproduces known plaintext and frames every capture end-to-end under the client's own length table without per-id guesses.

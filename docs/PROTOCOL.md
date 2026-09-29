# Protocol state — packet map (2026-09-28)

## Authoritative sources (2026-09-28, Ghidra + NativeFormat map)

The NativeAOT reflection metadata was fully parsed (`mrt_parse.py` → `mrt_map.json`, 95,389 invoke entries, 100% name→RVA resolution). All 372 protocol methods (ClassicUO.Network.PacketHandlers, NetClientExt, Assistant.PacketHandlers, PacketsTable) were renamed and decompiled to `decompiled/protocol_handlers.c` (372/372, no failures).

**Packet length table — authoritative** (`harness/uo/outlands_table.py` + `outlands_packet_table.json`):
- Base tier (145 entries) from the client's own static initializer `FUN_1401a13c0` (`Add(dict,id,len)` calls). The 22 Outlands-extended standard packets: `0x08=22, 0x0B=7, 0x1B=43, 0x20=28, 0x21=15, 0x23=42, 0x24=11, 0x25=27, 0x2E=20, 0x54=18, 0x56=15, 0x6C=27, 0x76=22, 0x77=18, 0x90=31, 0x95=11, 0x99=36, 0xB9=5, 0xBA=14, 0xC0=52, 0xEF=5, 0xF3=38` (upstream value +6..+16 bytes of Outlands fields).
- **No wire-derived extras.** The former `0x00=106, 0x6F=76, 0x9E=65, 0xDC=15` entries were derived from the wrong S2C decode (below) and were removed 2026-09-29: none of these ids occurs in any correctly decoded capture.
- Result (2026-09-29, correct decode): all 53 277 S2C packets in the 18 `logs/session_*.s2c.raw` captures have exactly the table length (0 mismatches).

**Correction 2026-09-29 — the old "world-state stream" and "custom S2C dialect" were decode garbage.** Every S2C decode before 2026-09-29 skipped the XOR layer (docs/CIPHER.md §4) and started Huffman at byte 19. The `0x00/0x40/0x3F/0x52` "world-state stream", the "content is not standard UO" finding and the request/response correlation table that used to be here were artifacts of that. `docs/WORLDSTATE.md` is now only a supersession notice (plus the still-valid code-backed handler-table findings).

**`Send_TimeSyncPingReq` SOLVED (decompiled @ 0x14017e940):** emits exactly `ff 00 07 00 00 00 03` — the ~1/s keepalive. **It carries no timestamp**; the anti-speedhack timing channel is purely the *arrival cadence* of this packet server-side. Our relay preserves it; agent action generation does not affect it (the stock client keeps pinging on its own schedule). The `Speedhack/AutoClicking/AutoKeyboard` enum's consumer remains unlocated (likely server-side only). S2C answers it with `0xFF` sub 3 (u64be server time, 15 B).

**Handler surface for layouts**: `ClassicUO.Network.PacketHandlers` (100-slot S2C dispatch table, `cuo_handler_table.json`), `Assistant.PacketHandlers` (76 methods incl. `OutlandsServerPacket` @ 0x64eb0, `OutlandsItemNameResponse` @ 0x64f20) and `ClassicUO.Network.NetClientExt` (104 methods), all decompiled. Verified S2C field layouts: docs/WORLDMODEL.md.

## C2S (client→server) — 100% mapped

Standard UO layouts, XOR session key (see CIPHER.md). Frames 242/242 on capture, clean live.

| ID | Meaning | Notes |
|---|---|---|
| `91 <len:2BE> <name\0> <JWT>` | game login | Outlands custom (length-prefixed, ~1122B) |
| `5D` 73B | character select | standard, `0xEDEDEDED` pattern |
| `BF` | general info | standard |
| `09` 5B / `34` 10B / `98` 7B | entity queries (id + serial) | repeated per nearby entity; likely name/info query (cf. `OutlandsItemNameResponse` in client metadata) |
| `02` 7B | walk | standard |
| `06` 5B | dclick | standard |
| `AD` | unicode speech | standard |
| `B1` | gump response | standard |
| `C8` 2B, `F0` 4B, `32` 2B | misc standard | |
| `ff 00 07 00 00 00 03` | **Outlands keepalive ~1/s** | 0xFF namespace; prime `Send_TimeSyncPingReq` suspect |

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

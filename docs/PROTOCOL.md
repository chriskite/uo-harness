# Protocol state — packet map (2026-09-28)

## Authoritative sources (2026-09-28, Ghidra + NativeFormat map)

The NativeAOT reflection metadata was fully parsed (`mrt_parse.py` → `mrt_map.json`, 95,389 invoke entries, 100% name→RVA resolution). All 372 protocol methods (ClassicUO.Network.PacketHandlers, NetClientExt, Assistant.PacketHandlers, PacketsTable) were renamed and decompiled to `decompiled/protocol_handlers.c` (372/372, no failures).

**Packet length table — now authoritative** (`harness/uo/outlands_table.py` + `outlands_packet_table.json`):
- Base tier (145 entries) from the client's own static initializer `FUN_1401a13c0` (`Add(dict,id,len)` calls). The 22 Outlands-extended standard packets: `0x08=22, 0x0B=7, 0x1B=43, 0x20=28, 0x21=15, 0x23=42, 0x24=11, 0x25=27, 0x2E=20, 0x54=18, 0x56=15, 0x6C=27, 0x76=22, 0x77=18, 0x90=31, 0x95=11, 0x99=36, 0xB9=5, 0xBA=14, 0xC0=52, 0xEF=5, 0xF3=38` (upstream value +6..+16 bytes of Outlands fields).
- Tier-dict packets (world-data family) load from deserialized embedded resources; fixed values proven from wire: `0x00=106, 0x6F=76, 0x9E=65`.
- Result: **full live session (102 KB S2C) frames with zero desyncs**; offline proxy test ALL PASS.

**WORLD-STATE STREAM MYSTERY RESOLVED (2026-09-28, see `docs/WORLDSTATE.md`):** the `0x00/0x40/0x3F/0x52` record stream has **no parser in the client at all** — `AnalyzePacket`'s delegate slots for these ids are null; the client frames and *discards* them (full 100-slot dispatch table extracted: `cuo_handler_table.json`). Serial cross-check is negative (no C2S-observed serial appears anywhere in the stream) — it is **not entity state**, and sub-9 item queries are answered via `0xFF` sub `0x15`, not this stream. Assessment: a data channel for Outlands' **sanctioned companion tools** (rules §3.1: ExploreOutlands / Outlands Butler / LootGoblin) or forward-compat. Consequence for the harness: **the agent loses nothing by ignoring it — the client ignores it too.** MessagePack-candidate record framing remains unresolved but is now low-priority. Framing correction folded in: `0xDC=15` (tier-dict overrides the static table's 9).

**`Send_TimeSyncPingReq` SOLVED (decompiled @ 0x14017e940):** emits exactly `ff 00 07 00 00 00 03` — the ~1/s keepalive. **It carries no timestamp**; the anti-speedhack timing channel is purely the *arrival cadence* of this packet server-side. Our relay preserves it; agent action generation does not affect it (the stock client keeps pinging on its own schedule). The `Speedhack/AutoClicking/AutoKeyboard` enum's consumer remains unlocated (likely server-side only).

**Handler surface for layouts**: `Assistant.PacketHandlers` (76 methods incl. `AnalyzePacket` dispatch, `OutlandsServerPacket` @ 0x64eb0, `OutlandsItemNameResponse` @ 0x64f20) and `ClassicUO.Network.NetClientExt` (104 methods) — the custom S2C dialect's parsers, all decompiled and ready for field-layout extraction.

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

## S2C (server→client) — framing solved, layouts are Outlands-custom

- Huffman layer: standard static tree; decoder validated (flush marker −256 appears ~71/1000B across the whole stream = aligned decoding).
- Framing: standard length table works for most ids. Overrides discovered: `{0x6F: 59?, 0xFF: 16, 0x49: 14}` (0x6F may be 76 — repeat-spacing evidence; some overrides may be coincidental alignments, watch desyncs).
- **Content is NOT standard UO layouts** (verified: no ASCII text, no cliloc names, no raw player serial at expected frequency, no `0xEDEDEDED` fillers in 102 KB of live session). This matches the Feb 2026 "world data modernization" — Outlands speaks a custom S2C dialect; their client fork parses it.
- **Repeating sub-record prefixes** found inside multiple packets (nested/TLV-like structure):
  - `a9 20 00 00 6d 00` — ~28-byte records, repeat inside `0xA9`-framed and `0x5A`-framed packets
  - `11 3a 00 00` — 15–20-byte records, repeat inside an `0x11`-framed packet
  - `5a 00 cb 00 6e a0 7b 00 4f 27` — ~36-byte records, repeat inside `0x5A` packets
- Custom S2C ids observed so far: `00`(106B fixed family), `86`(variable, valid embedded len), `FF`(16B), `6F`, `40`, `33`, `F0`, `49`, `5A`, `52`, `83`, `8A`, `A9`, `74`, `E1`, `11`.

## Request/response correlation (live session, timing ≤350 ms)

| C2S | S2C | Assessment |
|---|---|---|
| `06` dclick | `5A` | object/gump open |
| `02` walk | `52` | movement ack (NOT standard 0x22) |
| `AD` speech | `11` | speech echo |
| `98` query | `83` / `8A` / `A9` | entity info responses (name/desc?) |
| `B1` gump response | `40` / `41` / `74` / `E1` | gump results |
| `FF` keepalive | `03` / `00` / `16` | inconsistent — needs more samples |

## Methodological notes

- Desync-greedy length search finds *coincidental* alignments; only accept lengths that (a) satisfy multiple streams, (b) have repeat-spacing evidence, or (c) semantic validation. Repeat-prefix spacing is the strongest passive signal.
- NPC names are cliloc-referenced (no raw text in S2C); own-speech echo arrives as `0x11` but not in plain UTF-16 — content encoding of `0x11` records still open.
- Next step for full layout RE: decompile the client's packet dispatch + `OutlandsServerPacket` handlers in Ghidra (NativeAOT; anchor via the Huffman table refs or metadata names), and/or active behavioral mapping (controlled in-game actions with correlated captures).

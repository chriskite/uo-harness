# WORLDSTATE.md — Outlands "world-state stream" (0x00/0x40/0x3F/0x52): parser hunt + record grammar

Date: 2026-09-28. Binary: `ClassicUO.exe` (Outlands fork, client version 1.0.2.544, NativeAOT).
Captures: `logs/session_20260928_141253.s2c.raw`, `logs/session_20260928_164548.s2c.raw`
(19-byte prelude + Huffman stream; decode via `harness/uo/huffman.py` + `harness/uo/outlands_table.py`).
Analysis scripts: `walk_handlers.py`, `ws_decode.py`, `ws_stream.py`, `ws_frame_test.py`,
`ws_anchors.py`, `ws_period.py`, `ws_msgpack.py`, `extract_resources.py` (workspace root);
Ghidra scripts: `ghidra_scripts/{FindAddCallers,XrefHandlerSingleton,XrefAnalyzePacket,
XrefSingletonField,XrefStaticsBase,DecompileCctor,ResolveStubs,DecompileList,XrefRazorReg,
CctorSlotsAndResource,XrefDelegateType,XrefOne}.java`.

## 1. Carrier packets

| ID | Length | Provenance | Observed | Role |
|----|--------|-----------|----------|------|
| 0x00 | **fixed 106** | wire-proven (varlen framing desyncs by packet 4; fixed-106 frames all 102 KB of session 141253 with zero desync). Absent from the client's static table initializer; reaches the runtime table via the fork's deserialized tier-dict (loader FUN_14118d1a0) | 31× in session 141253 (~1/s), 2×+ in 164548 | periodic world-data burst |
| 0x40 | **fixed 201** | client static table `FUN_1401a13c0` (`Add(dict,0x40,0xC9)`); same value as upstream ClassicUO (UltimaLive legacy entry) | 3× in 141253, 1×+ in 164548 | world-data batch |
| 0x3F | variable (u16be len @1) | absent from static table → varlen | 71 B (141253); **13 155 B** (164548 @off 32360) | large world-data dump |
| 0x52 | variable | absent from static table → varlen | **30 627 B** (164548 @off 130) | large world-data dump (initial load) |

**Framing correction (wire-proven):** 0xDC is **15 bytes**, not 9 as the static table
(`FUN_1401a13c0`) claims. With `EXTRA[0x5C]=2` + `0xDC=15`, session 164548 frames
45 515 / 46 227 bytes including the 13 155-byte 0x3F dump whose declared length lands exactly
(`3f 33 63` = 0x3363 = 13 155); the residual 712 bytes are capture truncation. With the
static-table 0xDC=9 the session desyncs at offset 31 216. The runtime tier-dict therefore
overrides 0xDC (session 141253 contains no 0xDC packets, so the error was invisible there).

## 2. Parser identity — code-backed negative result

**There is no parser for ids 0x00/0x40/0x3F/0x52 in this client build. The packets are
framed by `NetClient` and silently discarded by `PacketHandlers.AnalyzePacket`.**

The complete S2C path (all decompiled and read end-to-end):

```
NetClient.ProcessRecv        @ 0x140145600   (recv loop, Huffman via DecompressBuffer — clean, no tap)
NetClient.ExtractPackets     @ 0x140144f60   (frames via PacketsTable.GetPacketLength)
NetClient.Update (FUN_140144c60)             (second dispatch entry; same tail)
Plugin.ProcessRecvPacket     @ 0x1401a2a20   (Razor bridge: forwards ONLY ids with a
                                              registered Razor viewer/filter — checked first
                                              via HasServerViewer/HasServerFilter)
PacketHandlers.AnalyzePacket @ 0x1401844d0   (handlers[id] delegate; NULL slot → silent return)
```

Evidence chain for "no handler":

1. **The delegate table was fully extracted.** The registration cctor is
   `FUN_140184550 @ 0x140184550` (found via the cctor-guard cell `PTR_FUN_143f7b310`
   referenced by `get_Handlers` @ 0x140184440). It builds the 256-entry
   `OnPacketBufferReader[]` and fills **exactly 100 slots**; each delegate-init stub
   (`FUN_141696cXX…0x1416973e4`) was disassembled to resolve its target
   (`stub_targets.txt` → `cuo_handler_table.json`). Slot→handler map is complete and
   matches upstream ids (0x1B→CreateGameScene, 0xFF→OutlandsProtocol, …).
   **Slots 0x00, 0x3F, 0x40, 0x52 are absent.**
2. **Disassembly-level verification:** no store to slot offsets 0x10 (id 0x00),
   0x208 (0x3F), 0x210 (0x40), 0x2A0 (0x52) exists anywhere in the cctor
   (`decompiled/cctor_slots.txt`).
3. **The delegate EEType (0x1432633f0) has exactly three creation sites**
   (`XrefDelegateType`): the cctor, `LoginScene.Load` @ 0x1402d3f90, and
   `GameScene.OnProtocolSet` @ 0x1402c8ca0. The two scene sites add/remove only the
   login-flow ids 0x53/0x85/0x86/0xA9 (resolved: scene stubs → FUN_1402c8e10,
   FUN_1402c8fe0, FUN_1402d6000, FUN_1402d6520, FUN_1402d6660, FUN_1402d6a60).
   No fourth registration site exists.
4. **Razor (Assistant) layer:** all callers of `Register{Client,Server}To{Server,Client}{Viewer,Filter}`
   (10 functions, `decompiled/razor_reg_callers.c`) register no S2C viewer/filter for
   0x00/0x40/0x3F/0x52. `Assistant.PacketHandlers.OutlandsServerPacket` @ 0x140064eb0
   handles only 0xFF-dialect subs 0x13/0x14/0x15.
5. **UltimaLive is dead code in this fork.** `NetClientExt.Send_UOLive_HashResponse`
   @ 0x14017bc40 has zero code xrefs (vtable entry only); `Ultima.TileMatrix` live-write
   methods (`AddPendingStatic` @ 0x141082550, `RemoveStaticBlock` @ 0x1410823a0) likewise
   have no code callers; `Assistant.World.set_ShardName` @ 0x140057700 (UltimaLive's
   shard-name sink upstream) is unreferenced. The fork kept the upstream 0x3F/0x40 table
   lengths but stripped the UltimaLive handlers. (`UOO.TerrainFile` — the fork's map
   store read by `ClassicUO.Game.Map.Chunk::Load` — has no network-fed writer either.)

This supersedes WORLDMODEL.md Open Question 1's assumption ("parser presumably a CUO
handler outside the selected set"): the dispatch table is now proven complete, and the
parser is not in it. Whatever consumes this stream (server-side telemetry, an external
companion tool, or simply nothing) is outside ClassicUO.exe.

## 3. Record grammar (empirical — pattern-backed, no code backing exists)

Carrier payloads form **one continuous record stream** chopped at carrier boundaries
(records span packets; the same multi-byte runs appear in 0x00 frames, 0x40 frames and the
0x3F/0x52 dumps — e.g. `0f 52 40 33 4c 00 31 c7 61 82 00 18` appears inside a 0x40 frame
@31344 and at the 0x3F dump head in session 164548).

Measured structure (offsets into the concatenated carrier stream of session 164548,
`ws_stream_164548.bin`, 31 034 B):

- **Records are variable-length and heterogeneous.** A recurring record family anchored by
  the 46-byte invariant prefix
  `00 40 00 6e 6e c9 00 0f 06 00 40 9f 00 90 00 33 00 dc 00 1b 85 00 dc 00 68 00 00 d4 00 00 83 31 00 0f 02 32 17 ca 0e 00 62 00 00 1e 00 83`
  recurs 22× with inter-record spacing 86–118 (varies: variable tail). Two occurrences
  (@8199/@9330) share a 104-byte verbatim run before diverging — same record resent with
  small edits (incremental update).
- Multi-record variance map (21 aligned copies): bytes 0–13 invariant; 14–25 two-valued;
  26 invariant; 27+ increasingly variable. I.e. a ~46-byte invariant header/template plus
  a variable tail.
- Second family: 5-byte repeats `00 1b 85 00 dc` (9× consecutive in session 141253
  frame #0; 97× total in the 164548 dump), alternating with `00 50 85 00 c7` (18×) and
  `00 83 00 72` / `72 40 00 00 ac` (12×).
- Byte stats: entropy ≈ 5.6–5.9 bits/byte, ~30 % 0x00 — sparse small-integer structure;
  not compressed (no zlib headers anywhere) and not encrypted (clean length framing).

**MessagePack candidate (inferred, inconclusive):** marker bytes match MessagePack
families — fixint (0x00–0x7F, the dominant bytes), fixarray (0x90–0x9F), fixmap (0x83),
array16 (`dc 00 1b` = array of 27), f32 (0xCA), ext (0xC7). Decoding 0x00 payloads as
MessagePack object sequences terminates exactly at the 105-byte payload end for several
frames (63 objects @frame 1, 30 @off 8779, 67 @off 79789, …) — above-chance but the
decoded values are dominated by bare fixints and the decode desyncs at stream offset 1193
of 3923, so MessagePack remains a **candidate framing, not proven**. If it is MessagePack,
records are schemaless arrays/ints — field semantics need a schema source the binary
does not contain (consistent with §2: no parser).

## 4. Validation on real captures

Framing (proven):
- session 141253: 60 packets, 101 993/102 052 bytes, zero desyncs (residual 59 B = truncation).
- session 164548 with `0x5C=2, 0xDC=15`: 45 515/46 227 bytes (residual 712 B = truncation),
  framing the 30 627 B 0x52 dump, the 13 155 B 0x3F dump, and 0x00/0x40 frames.

Record decode: ≥20 consecutive MessagePack-candidate objects were decoded from multiple
0x00 payloads with exact 105/105-byte termination (e.g. 63 consecutive objects in the
frame at plain offset 106, ending exactly on the payload boundary). Confidence:
**inferred** (candidate framing; exact-fit recurrence across frames is the evidence).

**Serial cross-check — NEGATIVE (proven):** none of the C2S-observed serials appear
anywhere in either carrier stream, in any encoding tested (u32be/u32le/u16be):
player 0x00094375, NPCs 0x000001D3–0x00000236 range (all clicked serials from both
sessions: 0x1DF/0x1F1/0x1F0/0x1E9/0x1EE/0x1EF/0x1FA in 141253; 0x236/0x1E2–0x1E5/0x19D1C/
0xC5C2/0x8AAE0 in 164548), vendor item 0x40000D54. The recurring dwords 0x31C7011C (61×)
and 0x31C76182 (13×) match **no** queried serial → they are not serials; read as
(u16be 0x31C7, u16be 0x011C/0x6182) they are consistent with (graphic, hue/field) pairs
(**inferred**). Conclusion: this stream is not entity/mobile state; the
"sub-9 detail query → answered by 0x00-family" hypothesis from WORLDMODEL.md §5 is
**not supported** — the answer path for those queries remains 0xFF sub 0x15
(`OutlandsItemNameResponse`), which matches queried serials 1:1 in the client code.

## 5. Field confidence summary

| Claim | Confidence |
|-------|-----------|
| Carrier ids/lengths (0x00=106, 0x40=201, 0x3F/0x52 varlen) | proven (wire + client table) |
| 0xDC=15 (not 9) | proven (wire framing of 164548 incl. exact 0x3F length landing) |
| Single continuous record stream across carriers | proven (cross-carrier verbatim runs) |
| No parser in ClassicUO.exe; silent drop in AnalyzePacket | proven (full dispatch-table extraction + EEType xref + disasm slot check) |
| Variable-length heterogeneous records, ~46 B invariant prefix family | inferred (22-sample variance analysis) |
| MessagePack framing of records | inferred, inconclusive (exact-fit decodes + marker families; desync at 1193) |
| Field semantics (graphic/hue/position) | unknown — no code backing exists in the client |
| Stream carries serials/entity state | disproven (exhaustive serial scan, both sessions, 3 encodings) |

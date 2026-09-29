# WORLDSTATE.md — SUPERSEDED (2026-09-29): there is no "world-state stream"

This file used to describe an Outlands "world-state stream" carried by S2C ids
0x00 / 0x40 / 0x3F / 0x52 (a 106-byte 0x00 record family, 13 155-byte 0x3F and
30 627-byte 0x52 "dumps", a record grammar, anchors, periods, a MessagePack
hypothesis). **All of that was decode garbage.** It is kept only as a pointer.

## What went wrong

The S2C wire format is (docs/CIPHER.md §4, `harness/uo/s2c.py`):

```
bytes 0..12   cleartext prelude  ff 00 0d | 7x 00 | 0c <s2c_key> <c2s_key>
bytes 13..    every byte XOR s2c_key (prelude byte 11), then UO static Huffman;
              each packet is compressed separately and ends with a flush,
              so one flush segment == one packet
```

Every analysis behind this file skipped the XOR and started Huffman at byte 19
(or at a heuristic offset). The output was mostly noise that happened to frame
for a while under a guessed length table; the recurring "record motifs" were
artifacts of the same wrong bit alignment repeating. Framing the correctly
decoded captures (all 18 `logs/session_*.s2c.raw`, 53 277 packets) with the
client's own length table gives **0 mismatches, and ids 0x00, 0x3F, 0x40, 0x52
(and the former table extras 0x6F, 0x9E, 0xDC) never occur**. The content is
standard UO: 0x1B LoginConfirm with the player serial 0x00094375, 0xA9
character list "TestWorth", 0x20/0x77/0x78 mobiles, 0x22 ConfirmWalk, 0xBF,
0xFF dialect frames, etc. See docs/WORLDMODEL.md for the verified layouts.

Removed with this supersession: the `ws_*.py` analysis scripts and their
`ws_*.bin` outputs, `map_live_s2c.py`, `beam_lengths.py`,
`discover_s2c_lengths.py`, `s2c_live_packets.txt`, the `EXTRA` entries in
`harness/uo/outlands_table.py`, `S2C_OVERRIDES` in `harness/uo/packets.py`, and
the runtime's `WORLD_DATA_S2C` ignore-list.

## 2. Code-backed findings that remain valid

These were read from the decompiled client, not from captures, and do not
depend on the S2C decode:

- **The S2C handler table is complete**: the `PacketHandlers` registration cctor
  `FUN_140184550 @ 0x140184550` fills exactly 100 of 256 `OnPacketBufferReader`
  slots (`stub_targets.txt` → `cuo_handler_table.json`, `decompiled/cctor_slots.txt`);
  `PacketHandlers.AnalyzePacket @ 0x1401844d0` silently returns on a NULL slot.
  Slots 0x00/0x3F/0x40/0x52 are empty, which is consistent with those ids never
  appearing on the wire. The only other registration sites are `LoginScene.Load`
  @ 0x1402d3f90 and `GameScene.OnProtocolSet` @ 0x1402c8ca0 (login-flow ids
  0x53/0x85/0x86/0xA9).
- **Receive path**: `NetClient.ProcessRecv @ 0x140145600` (applies the XOR with the
  key at NetClient+0x71, then `DecompressBuffer`; see docs/CIPHER.md) →
  `NetClient.ExtractPackets @ 0x140144f60` (framing via `PacketsTable.GetPacketLength`)
  → `Plugin.ProcessRecvPacket @ 0x1401a2a20` (Razor bridge) → `AnalyzePacket`.
- **UltimaLive is dead code in this fork**: `NetClientExt.Send_UOLive_HashResponse`
  @ 0x14017bc40, `Ultima.TileMatrix.AddPendingStatic` @ 0x141082550 and
  `RemoveStaticBlock` @ 0x1410823a0 have no code callers. `UOO.TerrainFile` — the
  fork's map store read by `ClassicUO.Game.Map.Chunk::Load` — has no network-fed
  writer; map data comes from the client's local files.

# Client→server stream cipher — reverse engineering record

Status 2026-09-27: **model fully determined and cross-validated on two live sessions.**
Static keystream recovery (full K) still open; recovered positions suffice for walks/dclicks today.

## Wire format summary (game connection, port 2593)

### Client preamble (cleartext, own TCP segment)
`ef 00 00 00 0c` — constant across sessions. Likely Outlands protocol marker (`0xef`) + version/flags (`0x0c` = 12).

### Server prelude (cleartext, 19 bytes)
`ff 00 0d | 9 × 00 | 0c | tick | S | 4 more bytes`
- bytes 0-2: `ff 00 0d` — looks like variable-length header (id 0xff, len 13), content = 13 bytes.
- byte 12: `0c` — echoes the client preamble constant.
- byte 13: per-session value (0x14 / 0x30 observed) — tick/counter, unknown.
- byte 14: **S — the per-session cipher byte** (0x2f in capture 1, 0x0f in capture 2; delta 0x20 = observed cross-session ciphertext delta).
- bytes 15-18: 4 per-session bytes, purpose unknown.
After byte 18: **standard Huffman-compressed standard UO protocol, no encryption** (verified: decodes and frames cleanly with upstream table/tree).

### Client→server after preamble: per-packet cipher

```
c[i] = p[i] ^ K[i] ^ S
```

- `K[i]` — static keystream, depends only on position within packet. Same across sessions.
- `S` — per-session byte from server prelude (above).
- Length-preserving; applied to every C2S packet after the 5-byte preamble (including the EF-version/1122-byte login blob and all gameplay packets).

## Evidence

1. Walk ladder (controlled session, 20 walks west):
   ```
   0d 8a 0f 0f 0f 0f 07   0d 8a 0e 0f 0f 0f 0f   0d 8a 0d ...   0d 8a 00 0f 0f 0f 0f
   0d 8a 1f ...           0d 89 1e ...           0d 8a 1d ...   0d 8a 1c 0f 0f 0f 0f
   ```
   → plaintext `[02, 86, seq, key key key key]`; position 2 behaves as `c = ~seq` (K[2]^S = 0xFF); seq ladder 0xF0→0xFF, restart 0xE0 after movement reject (building); reject desync visible at step 17 (`0x89` = direction byte w/o run bit).
2. Identical plaintext at different stream offsets → identical ciphertext (heartbeat ×137 in session 1, ×129 in session 2) → keystream resets per packet.
3. Cross-session same-plaintext packets differ by exactly `0x20` at every position (heartbeat, 5-byte fixed packets, size-9 packets) → session constant S, not session keystream.
4. Two independent packet types give the same `K[0]^S`: walk (`0x02`→`0x2d`, K^S1 = 0x2f) and dclick (`0x06`→`0x29`, K^S1 = 0x2f). ✔
5. S1 heartbeat `d0 2f 28 2f 2f 2f 2c` vs S2 `f0 0f 08 0f 0f 0f 0c`: XOR = `20 20 20 20 20 20 20`. ✔

## Recovered keystream (K[i] ^ S) per session

Capture 2 (S = 0x0f, from prelude byte 14):
| pos | K^S | from |
|---|---|---|
| 0 | 0x0f | walk id 0x02 → 0x0d |
| 1 | 0x0c | walk dir 0x86 → 0x8a |
| 2 | 0xff | walk seq ladder (~seq) |
| 3-6 | 0x0f ×4 | walk fastwalk key 00000000 |

Capture 1 (S = 0x2f): K^S at pos 0-2 = `0x2f, 0x2c, 0xdf` (consistent: K^S1 = K^S2 ^ 0x20 ✔)

Candidate absolute K (if S is used unmodified): K[0]=0x00, K[1]=0x03, K[2]=0xf0 (needs binary confirmation).

## Known plaintext packet shapes (plaintext side)

| Plaintext prefix | Packet | Notes |
|---|---|---|
| `02 dir seq k×4` (7 B) | walk | dir = dir \| 0x80 when running |
| `06 serial×4` (5 B) | dclick | S2 ciphertext `09 8f 06 4c 7a` |
| `09 …` | recurring packet family | ciphertext starts `06` (S2) / `26` (S1); sizes 15/24/27/30/33/39/42/45/54; contains constant mid-section (`3b e2e2e2e2 0b` in S2) |
| `ff …` (7 B) | heartbeat ~0.64 s | S1 `d0..`, S2 `f0..` after decrypt attempt: `ff 03 f7 00 00 00 03`? unconfirmed |
| 1122 B | login/auth blob | after preamble; contains EF-version + session ticket presumably |
| `ad …`? | unicode speech candidate | size 21 in S2: `a2 0f 1a cf …` |

## Open items

- Recover full static K (table or PRNG) from the binary — Ghidra target: per-packet position-indexed XOR loop in the send path, session byte S XORed in at login. The keystream-builder should sit near the prelude writer (`ef 0000000c`) / prelude reader (`ff 000d`).
- Confirm S = prelude byte 14 on a third capture; decode the remaining prelude bytes (tick? extra key bytes?).
- Map remaining packet families (0x09-series, heartbeat) and the login blob structure.
- Single-byte-XOR loop found in Ghidra (`FUN_140145600`, XORs received chunks with one byte) — assessed as `.uoo` asset-file obfuscation, NOT the network cipher (brute-force of all 256 single-byte keys fails framing).

## Harness implications

- Relay/proxy: unaffected (works blind).
- World model: unaffected (S2C is standard).
- Action channel: need K over action-packet positions + S per session. S read from wire. K: finish static recovery (or extend known-plaintext coverage per packet type). The login blob never needs forging (passthrough).

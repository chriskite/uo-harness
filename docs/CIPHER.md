# Client→server stream obfuscation — SOLVED 2026-09-27

**Model (final): `c[i] = p[i] ^ S` for the entire client→server stream after the 5-byte preamble.**

A single-byte XOR with a per-session key `S`. No tables, no PRNG, no per-position keystream.
(An earlier draft of this document proposed a per-position keystream `K[i] ^ S`; closer analysis of the
controlled walk capture showed position-2 ciphertext `0f 0e 0d … 00 1f 1e 1d 1c` is simply plaintext
seq `00 01 02 … 0f 10 11 12 13` XOR `0x0f` — one byte explains everything, including the cross-session
`0x20` delta = the two sessions' different key bytes.)

## Wire format (game connection, port 2593)

### 1. Client preamble — cleartext, own TCP segment, 5 bytes
`ef 00 00 00 0c` — constant across sessions. Outlands protocol marker + version/flags (0x0c = 12).

### 2. Client→server stream — XOR-encrypted from byte 5 onward
Every byte XORed with the session key byte `S` (see §3). Plaintext underneath is **100% standard UO
protocol** — decrypted capture frames 242/242 packets with the upstream length table, zero errors.

Login sequence (plaintext):
```
91 <len:2BE> <name\0> <JWT>        game login: len includes everything after the 3 header bytes?
                                   (observed: 91 04 62 "Hackworth"\0 eyJhbGciOi… = 1122 bytes total)
5d <73B>                           character select ("TestWorth", standard 0xEDEDEDED pattern)
bf …                               general info (language "ENU", feature flags…)
09/34/98 …                         standard status/query packets (id + serial)
ff 00 07 00 00 00 03               Outlands keepalive, ~1/s (0xFF = Outlands custom namespace)
02 …                               walk requests
06 …                               dclicks
ad …                               unicode speech
b1 …                               gump responses
```

The JWT (`{"alg":"HS256","typ":"JWT"}`) is issued by `https://login.uooutlands.com` during the HTTPS
auth and forwarded to the game server as the entire credential. Claims observed:
`name` (account), `outlandsid`, `mahid`, `mahleader`, `version` (client version "1.0.2.544"),
`hash` (uuid), `role` ("player"), `userdata` (client IP!), `purpose` ("GameServer"), `jti`, `exp`,
`iss`/`aud` (login.uooutlands.com). **No password on the game wire.**
**Test-shard capture bins may be committed (user decision 2026-09-28, private repo) — production
credentials/artifacts never.**

### 3. Server prelude — cleartext, 13 bytes
`ff 00 0d | 7 × 00 | 0c | K | S`
- bytes 0–2: `ff 00 0d` — 0xFF-namespace variable packet (id 0xff, len 13 incl. header).
- bytes 3–9: seven `00`.
- byte 10: `0c` — echoes the client preamble constant.
- **byte 11: `K` — the server→client XOR key** (earlier notes called it a "tick/counter"; corrected
  2026-09-29, §4).
- **byte 12: `S` — the client→server XOR key** (0x2f → capture 1, 0x0f → capture 2; both decrypt their
  captures perfectly).
- The "6 per-session trailer bytes" that earlier notes put at bytes 13–18 are **not prelude**: they
  are the first compressed bytes of the S2C stream.

### 4. Server→client stream — XOR `K`, then static Huffman, one flush per packet (CORRECTED 2026-09-29)
From byte 13, every byte is XORed with `K` (prelude byte 11), then decoded with the standard UO static
Huffman tree. Each server packet is compressed separately and ends with the flush symbol, zero-padded to
a byte boundary, so **one flush segment is exactly one packet**.
- Client code: `NetClient.ProcessRecv @ 0x140145600` XORs every received byte with the byte at
  `NetClient+0x71` before `DecompressBuffer` (`decompiled/netclient_recv.c:56-69`); upstream ClassicUO
  has no such step.
- Proof: all 18 session captures, 53 277 flush segments → every segment is exactly one packet under
  `harness/uo/outlands_table.py`, with 0 leftover bytes. The content is standard UO: `0x1B` login confirm
  with player serial `0x00094375`, `0xA9` char list "TestWorth", `0x78`/`0x77`/`0x20`, `0x22` walk
  confirms, `0xBF` sub1 fastwalk seeds. Re-encoding each decoded packet with the static code table
  reproduces the wire bytes exactly (0 mismatches), so the zero padding is confirmed.
- **Earlier notes were wrong:** they decoded from byte 19 without the XOR. That produced garbage that
  was misread as a "custom Outlands S2C dialect" (PROTOCOL.md, WORLDSTATE.md). Every S2C finding made
  before 2026-09-29 is suspect unless re-verified with the corrected decode.
- Implementation: `harness/uo/s2c.py` (`prelude_keys`, streaming `S2CStream` yielding
  `(wire_segment, packet)`, `encode_packet`).


## Evidence chain

1. Controlled session (20 walks west via arrow key): 20 consecutive 7-byte packets differing only in
   one counting byte → seq ladder `00..13` after XOR `0x0f`; direction byte `0x85/0x86` (run flag)
   matches steering around the building.
2. Single-byte XOR decrypts the login burst to `91 04 62 "Hackworth"\0 eyJhbGciOi…` — a well-formed
   JWT, verified by base64-decoding header/payload (claims are coherent, signature is 44 bytes of
   base64 → 32-byte HS256 signature).
3. Same single byte decrypts the entire 2977-byte stream to 242 cleanly-framed standard UO packets.
4. Cross-session: identical plaintext packets differ by exactly `0x20` at every position =
   key difference between the two sessions (`0x2f` vs `0x0f`); both keys appear at prelude byte 14
   of their respective server preludes.
5. Keepalive decrypts to `ff 00 07 00 00 00 03` — sensible 0xFF-namespace variable packet.

## S1 framing mistake (corrected)

Early analysis framed S1's first packet as `0xEF` (21 B) using the standard table on ciphertext —
coincidence. Decrypted, both sessions start with the same custom `0x91 <len> <name> <JWT>` login.

## Harness implications

- **Proxy**: tap the S2C prelude → `K` (byte 11) and `S` (byte 12). Decrypt C2S with `S`; decode S2C
  packet by packet with `K`. Because one flush segment is one packet, the proxy can drop or replace a
  single S2C packet (used to hide agent walk confirms, docs/MOVEMENT.md). Timing-sensitive packets
  still forward untouched.
- **Action channel**: to send an action, XOR the plaintext packet with `S`. The client must not be
  relaying our forged packets differently from its own — same scheme, same key, indistinguishable.
- **World model**: S2C is standard UO (with Outlands length widenings); upstream ClassicUO packet
  handlers are the parsing reference.
- The `0xFF` namespace (keepalive `…0003`, server prelude) and the `Speedhack/AutoClicking/AutoKeyboard`
  enum + `Send_TimeSyncPingReq` likely live in the same custom namespace — tag all 0xFF packets in
  logs for later mapping.

"""Offline verification: decode the pktmon capture of a real Test Shard session.

1. Rebuild both TCP directions from tshark's follow,raw dump.
2. Server->client: 13-byte prelude -> XOR keys, then per-packet XOR+Huffman
   (harness/uo/s2c.py: one flush segment == one packet).
3. Client->server: 5-byte preamble, XOR c2s_key, frame with the packet table.
"""
import re, sys
sys.path.insert(0, r"C:/Users/chris/uo-harness/harness")
from uo.packets import frame_take, C2S_OVERRIDES
from uo.outlands_table import outlands_length
from uo.s2c import PRELUDE_LEN, S2CStream, prelude_keys

DUMP = r"C:/Users/chris/uo-harness/stream_dump.txt"
CLIENT_PREAMBLE_LEN = 5

c2s = bytearray()
s2c = bytearray()
for ln in open(DUMP, "r", encoding="ascii", errors="replace"):
    if ln.startswith(("=", "Follow", "Filter", "Node")) or not ln.strip():
        continue
    hexpart = re.sub(r"[^0-9a-fA-F]", "", ln)
    if not hexpart:
        continue
    if ln[0] in ("\t", " "):
        s2c += bytes.fromhex(hexpart)
    else:
        c2s += bytes.fromhex(hexpart)

print(f"C2S raw: {len(c2s)} bytes, S2C raw: {len(s2c)} bytes")

def hexdump(b, n=96):
    for off in range(0, min(n, len(b)), 16):
        chunk = b[off:off+16]
        hexs = " ".join(f"{x:02x}" for x in chunk)
        asc = "".join(chr(x) if 32 <= x < 127 else "." for x in chunk)
        print(f"  {off:04x}  {hexs:<48}  {asc}")

print("\n=== S2C prelude ===")
hexdump(s2c[:PRELUDE_LEN])
s2c_key, c2s_key = prelude_keys(bytes(s2c[:PRELUDE_LEN]))
print(f"  s2c_key=0x{s2c_key:02x} c2s_key=0x{c2s_key:02x}")

print("\n=== C2S framed packets (first 25) ===")
buf = bytearray(b ^ c2s_key for b in c2s[CLIENT_PREAMBLE_LEN:])
total = 0
for i, pkt in enumerate(frame_take(buf, overrides=C2S_OVERRIDES)):
    if i < 25:
        print(f"  [{i:4}] id=0x{pkt[0]:02X} len={len(pkt):5}  {pkt[:24].hex()}")
    total += 1
print(f"  ... total framed: {total}, leftover: {len(buf)} bytes")

print("\n=== S2C packets (first 30) ===")
stream = S2CStream(s2c_key)
pkts = [p for _, p in stream.feed(bytes(s2c[PRELUDE_LEN:]))]
mismatch = 0
for i, pkt in enumerate(pkts):
    if outlands_length(pkt) != len(pkt):
        mismatch += 1
    if i < 30:
        print(f"  [{i:4}] id=0x{pkt[0]:02X} len={len(pkt):5}  {pkt[:20].hex()}")
print(f"  ... total packets: {len(pkts)}, length-table mismatches: {mismatch}, "
      f"incomplete tail: {stream.buffered} wire bytes")

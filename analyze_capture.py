"""Offline verification: decode the pktmon capture of a real Test Shard session.

1. Rebuild both TCP directions from tshark's follow,raw dump.
2. Client->server: frame with the packet table, dump first packets.
3. Server->client: Huffman-decompress, then frame; report integrity.
"""
import re, sys
sys.path.insert(0, r"C:/Users/chris/uo-harness/harness")
from uo.huffman import HuffmanDecoder
from uo.packets import frame_take, packet_length, TABLE

DUMP = r"C:/Users/chris/uo-harness/stream_dump.txt"

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

print("\n=== C2S first bytes ===")
hexdump(c2s)

print("\n=== C2S framed packets (first 25) ===")
buf = bytearray(c2s)
total = 0
for i, pkt in enumerate(frame_take(buf)):
    if i < 25:
        preview = pkt[:24].hex()
        print(f"  [{i:4}] id=0x{pkt[0]:02X} len={len(pkt):5}  {preview}")
    total += 1
print(f"  ... total framed: {total}, leftover: {len(buf)} bytes")

print("\n=== S2C huffman decode ===")
dec = HuffmanDecoder()
plain = dec.decompress(bytes(s2c))
print(f"compressed {len(s2c)} -> decompressed {len(plain)} bytes")
print("first decompressed bytes:")
hexdump(plain)

print("\n=== S2C framed packets (first 30) ===")
buf = bytearray(plain)
ok = True
total = 0
while buf:
    plen = packet_length(buf)
    if plen == 0:
        break
    if plen < 0:
        print(f"  !! DESYNC at offset {len(plain)-len(buf)}: id=0x{buf[0]:02X} declared len implausible")
        ok = False
        break
    pkt = bytes(buf[:plen])
    del buf[:plen]
    if total < 30:
        print(f"  [{total:4}] off={len(plain)-len(buf)-plen:6} id=0x{pkt[0]:02X} len={plen:5}  {pkt[:20].hex()}")
    total += 1
print(f"  ... total framed: {total}, leftover: {len(buf)} bytes, framing_clean={ok}")

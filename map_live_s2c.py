"""Map remaining Outlands custom S2C packets from the LIVE session stream.

Uses the proxy's own raw capture (logs/session_*.s2c.raw = prelude + Huffman stream).
Desync-searches with known overrides baked in, iterates to clean end-to-end framing,
then dumps the full packet log for semantic review.
"""
import collections
import glob
import os
import sys

sys.path.insert(0, r"C:/Users/chris/uo-harness/harness")
from uo.huffman import HuffmanDecoder
from uo.packets import TABLE

LOGS = r"C:/Users/chris/uo-harness/logs"
raw_path = sorted(glob.glob(os.path.join(LOGS, "session_*.s2c.raw")))[-1]
raw = open(raw_path, "rb").read()
print(f"stream: {raw_path} ({len(raw)} bytes)")

plain = HuffmanDecoder().decompress(raw[19:])
N = len(plain)
print(f"decompressed: {N} bytes")

OVERRIDES = {0x6F: 59, 0xFF: 16, 0x49: 14}

def length_at(buf, off, overrides):
    if off >= len(buf):
        return 0
    pid = buf[off]
    fixed = overrides.get(pid, TABLE[pid])
    if fixed > 0:
        return fixed if off + fixed <= len(buf) else 0
    if off + 3 > len(buf):
        return 0
    varlen = (buf[off+1] << 8) | buf[off+2]
    if varlen < 3 or varlen > 0x8000 or off + varlen > len(buf):
        return -1
    return varlen

def clean_run(buf, off, overrides, max_pkts=400):
    n = 0
    while off < len(buf) and n < max_pkts:
        plen = length_at(buf, off, overrides)
        if plen <= 0:
            break
        off += plen
        n += 1
    return n, off

off = 0
while off < N:
    plen = length_at(plain, off, OVERRIDES)
    if plen > 0:
        off += plen
        continue
    pid = plain[off]
    best = None
    for cand in [-1] + list(range(2, 96)):
        trial = dict(OVERRIDES)
        trial[pid] = cand
        n, end = clean_run(plain, off, trial, max_pkts=80)
        if best is None or end > best[1] or (end == best[1] and n > best[0]):
            best = (n, end, cand)
    n, end, cand = best
    if end <= off:
        print(f"!! unrecoverable desync at {off} id=0x{pid:02X}")
        break
    OVERRIDES[pid] = cand
    print(f"desync at {off:6} id=0x{pid:02X} -> override {cand} (clean to {end})")
    off += length_at(plain, off, OVERRIDES) or 1

n, end = clean_run(plain, 0, OVERRIDES, max_pkts=100000)
print(f"\nfinal: {n} packets, consumed {end}/{N}")
print("S2C_OVERRIDES =", {f"0x{k:02X}": v for k, v in sorted(OVERRIDES.items())})

# full packet log for semantic review
out = open(r"C:/Users/chris/uo-harness/s2c_live_packets.txt", "w")
off = 0
i = 0
ids = collections.Counter()
while off < N:
    plen = length_at(plain, off, OVERRIDES)
    if plen <= 0:
        break
    pkt = plain[off:off+plen]
    ids[pkt[0]] += 1
    asc = "".join(chr(x) if 32 <= x < 127 else "." for x in pkt[:72])
    out.write(f"[{i:3}] off={off:6} id=0x{pkt[0]:02X} len={plen:5}  {asc}\n")
    off += plen
    i += 1
out.close()
print("id histogram:", {f"0x{k:02X}": v for k, v in ids.most_common()})

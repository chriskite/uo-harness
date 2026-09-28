"""Discover Outlands custom S2C packet lengths by greedy override search.

At each desync, try candidates (variable-length, fixed 2..64) for the failing
id and keep the one with the longest subsequent clean run. Iterates to the
end of the stream. Pure offline analysis on the captured session.
"""
import sys
sys.path.insert(0, r"C:/Users/chris/uo-harness/harness")
from uo.huffman import HuffmanDecoder
from uo.packets import TABLE

s2c = open(r"C:/Users/chris/uo-harness/s2c_s2.bin", "rb").read()
plain = HuffmanDecoder().decompress(s2c[19:])
N = len(plain)

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

def clean_run(buf, off, overrides, max_pkts=200):
    n = 0
    while off < len(buf) and n < max_pkts:
        plen = length_at(buf, off, overrides)
        if plen <= 0:
            break
        off += plen
        n += 1
    return n, off

overrides = {}
desync_log = []
off = 0
while off < N:
    plen = length_at(plain, off, overrides)
    if plen > 0:
        off += plen
        continue
    pid = plain[off]
    best = None
    for cand in [-1] + list(range(2, 65)):
        trial = dict(overrides)
        trial[pid] = cand
        n, end = clean_run(plain, off, trial, max_pkts=60)
        if best is None or end > best[1] or (end == best[1] and n > best[0]):
            best = (n, end, cand)
    n, end, cand = best
    if end <= off:
        print(f"!! unrecoverable desync at {off} id=0x{pid:02X}; giving up")
        break
    overrides[pid] = cand
    desync_log.append((off, pid, cand, end))
    print(f"desync at {off:6} id=0x{pid:02X} -> override {cand} (clean to {end})")
    off += length_at(plain, off, overrides) or 1

n, end = clean_run(plain, 0, overrides, max_pkts=100000)
print(f"\nfinal: {n} packets, consumed {end}/{N}")
print("S2C_OVERRIDES =", {f"0x{k:02X}": v for k, v in overrides.items()})

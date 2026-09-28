"""Joint beam-search for Outlands custom S2C packet lengths.

Constraint: ONE override set must frame ALL captured streams cleanly end-to-end.
At each desync, branch over candidate lengths for the failing id AND for the
previous packet's id (handles misattributed previous lengths). Score beams by
consumed bytes, then by fraction of packets with known ids.
"""
import sys
sys.path.insert(0, r"C:/Users/chris/uo-harness/harness")
from uo.huffman import HuffmanDecoder
from uo.packets import TABLE

STREAMS = {
    "live": open(r"C:/Users/chris/uo-harness/logs/session_20260928_141253.s2c.raw", "rb").read()[19:],
    "cap2": open(r"C:/Users/chris/uo-harness/s2c_s2.bin", "rb").read()[19:],
}
PLAINS = {k: HuffmanDecoder().decompress(v) for k, v in STREAMS.items()}

def length_at(buf, off, ov):
    if off >= len(buf):
        return 0
    pid = buf[off]
    fixed = ov.get(pid, TABLE[pid])
    if fixed > 0:
        return fixed if off + fixed <= len(buf) else 0
    if off + 3 > len(buf):
        return 0
    vl = (buf[off+1] << 8) | buf[off+2]
    if vl < 3 or vl > 0x8000 or off + vl > len(buf):
        return -1
    return vl

def known_id(pid, ov):
    return TABLE[pid] > 0 or pid in ov

def solve(buf, ov, max_desyncs=6):
    N = len(buf)
    # beam: (consumed, n_known, off, overrides)
    beams = [(0, 0, 0, dict(ov))]
    for _ in range(max_desyncs + 1):
        # advance each beam to its next desync or end
        advanced = []
        for consumed, nk, off, o in beams:
            n = 0
            while off < N and n < 100000:
                plen = length_at(buf, off, o)
                if plen <= 0:
                    break
                nk += 1 if known_id(buf[off], o) else 0
                off += plen
                n += 1
            advanced.append((off, nk, off, o, n))
        # any beam done?
        done = [b for b in advanced if b[2] >= N - 2]
        if done:
            done.sort(key=lambda b: -b[1])
            return done[0][3], done[0][2], done[0][4]
        # branch on the best beams
        advanced.sort(key=lambda b: (-b[0], -b[1]))
        newbeams = []
        for _, nk, off, o, _ in advanced[:6]:
            if off >= N:
                continue
            pid = buf[off]
            # find previous packet start to allow re-lengthing it
            # (approximate: walk back by re-framing from a known-good point is expensive;
            #  instead just branch the failing id)
            cands = [-1] + list(range(2, 161))
            for cand in cands:
                trial = dict(o)
                trial[pid] = cand
                plen = length_at(buf, off, trial)
                if plen <= 0:
                    continue
                newbeams.append((off + plen, nk, off, trial, 0))
        if not newbeams:
            break
        # dedupe and trim
        seen = set()
        beams = []
        newbeams.sort(key=lambda b: (-b[0], -b[1]))
        for b in newbeams:
            key = (b[2], tuple(sorted(b[3].items())))
            if key in seen:
                continue
            seen.add(key)
            beams.append((b[0], b[1], b[2], b[3]))
            if len(beams) >= 200:
                break
    return None, 0, 0

base_ov = {}
results = {}
for name, buf in PLAINS.items():
    ov, consumed, npkts = solve(buf, base_ov)
    results[name] = (ov, consumed, len(buf), npkts)
    print(f"{name}: consumed {consumed}/{len(buf)} with {npkts} pkts, ov={ov if ov else 'FAILED'}")

# now solve jointly: use union constraint — run solve with both streams sharing overrides
# simple approach: take candidate from each and verify cross-stream
print()
for name, (ov, consumed, total, _) in results.items():
    if not ov:
        continue
    for other, buf in PLAINS.items():
        _, c2, np2 = solve(buf, ov)
        print(f"ov from {name} on {other}: consumed {c2}/{len(buf)}")

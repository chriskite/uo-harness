"""Compare 0x00-family framing hypotheses on both sessions."""
import sys
sys.path.insert(0, r"C:/Users/chris/uo-harness/harness")
from uo.huffman import HuffmanDecoder
from uo.outlands_table import OUTLANDS_BASE, EXTRA

def decode(path):
    raw = open(path, "rb").read()
    return HuffmanDecoder().decompress(raw[19:])

def frame_with(plain, overrides):
    pkts = []
    buf = bytearray(plain)
    off = 0
    while buf:
        pid = buf[0]
        fixed = overrides[pid] if pid in overrides else EXTRA.get(pid, OUTLANDS_BASE.get(pid))
        if fixed is not None and fixed > 0:
            plen = fixed if fixed <= len(buf) else 0
        else:
            if len(buf) < 3: break
            plen = (buf[1] << 8) | buf[2]
            if plen < 3 or plen > 0x8000 or plen > len(buf):
                plen = -1
        if plen == 0: break
        if plen < 0:
            return pkts, off, False
        pkts.append((pid, off, plen))
        buf = buf[plen:]
        off += plen
    return pkts, off, True

for sess, extra in (("141253", {}), ("164548", {0x5C: 2})):
    plain = decode(rf"C:/Users/chris/uo-harness/logs/session_20260928_{sess}.s2c.raw")
    print(f"===== session {sess}: {len(plain)} bytes")
    for label, ovr in [
        ("0x00 fixed 106", dict(extra)),
        ("0x00 varlen", {**extra, 0x00: -1}),
        ("0x00 fixed 106, 0x40 varlen", {**extra, 0x40: -1}),
    ]:
        pkts, consumed, clean = frame_with(plain, ovr)
        from collections import Counter
        c = Counter(p[0] for p in pkts)
        print(f"  {label}: framed={len(pkts)} consumed={consumed}/{len(plain)} clean_end={clean} "
              f"ids: {dict(sorted(c.items()))}")

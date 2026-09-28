"""Extract the world-state record stream from a framed session: concatenate carrier payloads in order."""
import sys
sys.path.insert(0, r"C:/Users/chris/uo-harness/harness")
from uo.huffman import HuffmanDecoder
from uo.outlands_table import OUTLANDS_BASE, EXTRA

CARRIERS = (0x00, 0x40, 0x3F, 0x52)

def decode(path):
    raw = open(path, "rb").read()
    return HuffmanDecoder().decompress(raw[19:])

def frame(plain, extra=None):
    ov = dict(extra or {})
    pkts = []
    buf = bytearray(plain)
    off = 0
    while buf:
        pid = buf[0]
        fixed = ov[pid] if pid in ov else EXTRA.get(pid, OUTLANDS_BASE.get(pid))
        if fixed is not None and fixed > 0:
            plen = fixed if fixed <= len(buf) else 0
        else:
            if len(buf) < 3: break
            plen = (buf[1] << 8) | buf[2]
            if plen < 3 or plen > 0x8000 or plen > len(buf): plen = -1
        if plen == 0: break
        if plen < 0:
            return pkts, off, False
        pkts.append((pid, off, plen))
        buf = buf[plen:]
        off += plen
    return pkts, off, True

def carrier_payload(pkt):
    """Strip carrier header: id byte for fixed, id+u16len for varlen."""
    pid = pkt[0]
    if pid in (0x3F, 0x52):  # varlen
        return pkt[3:]
    return pkt[1:]

if __name__ == "__main__":
    sess = sys.argv[1] if len(sys.argv) > 1 else "141253"
    plain = decode(rf"C:/Users/chris/uo-harness/logs/session_20260928_{sess}.s2c.raw")
    pkts, consumed, clean = frame(plain, {0x5C: 2} if sess == "164548" else {})
    print(f"session {sess}: {len(plain)}B, framed {len(pkts)} pkts consumed={consumed} clean={clean}")
    stream = bytearray()
    segs = []
    for pid, off, plen in pkts:
        if pid in CARRIERS:
            pkt = plain[off:off+plen]
            pay = carrier_payload(pkt)
            segs.append((len(stream), pid, off, plen, len(pay)))
            stream += pay
    print(f"record stream: {len(stream)} bytes from {len(segs)} carriers")
    for s0, pid, off, plen, paylen in segs:
        print(f"  stream@{s0:6} <- pkt@fileoff={off:6} id=0x{pid:02x} len={plen} payload={paylen}")
    open(rf"C:/Users/chris/uo-harness/ws_stream_{sess}.bin", "wb").write(bytes(stream))
    print("wrote ws_stream_%s.bin" % sess)

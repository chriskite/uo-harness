"""Decode an Outlands capture session and dump the 0x00-family world-state packets."""
import sys, json, collections
sys.path.insert(0, r"C:/Users/chris/uo-harness/harness")
from uo.huffman import HuffmanDecoder
from uo.outlands_table import outlands_length, EXTRA

def decode_s2c(path, extra_fix=False):
    raw = open(path, "rb").read()
    prelude, stream = raw[:19], raw[19:]
    dec = HuffmanDecoder()
    plain = dec.decompress(stream)
    return prelude, plain

def frame(plain, extra_fix=False):
    """Frame using outlands_length; optionally treat 0x5C as fixed-2."""
    pkts = []
    buf = bytearray(plain)
    while buf:
        plen = outlands_length(buf)
        if plen == 0:
            break
        if plen < 0:
            if extra_fix and buf[0] == 0x5C and len(buf) >= 2:
                plen = 2
            else:
                pkts.append(("DESYNC", bytes(buf[:64])))
                break
        pkts.append((buf[0], bytes(buf[:plen])))
        del buf[:plen]
    return pkts, bytes(buf)

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "141253"
    path = rf"C:/Users/chris/uo-harness/logs/session_20260928_{which}.s2c.raw"
    prelude, plain = decode_s2c(path)
    print(f"prelude={prelude.hex()}  decompressed={len(plain)} bytes")
    pkts, left = frame(plain, extra_fix=(which == "164548"))
    print(f"framed {len(pkts)} packets, leftover {len(left)}")
    hist = collections.Counter(p[0] for p in pkts if p[0] != "DESYNC")
    for pid, n in sorted(hist.items()):
        print(f"  id=0x{pid:02x} count={n}")
    # dump world-state packets
    ws = [p[1] for p in pkts if p[0] in (0x00, 0x40, 0x3F, 0x52)]
    print(f"\nworld-state carriers: {len(ws)}")
    out = open(rf"C:/Users/chris/uo-harness/ws_packets_{which}.bin", "wb")
    for p in ws:
        out.write(len(p).to_bytes(4, "little") + p)
    out.close()
    for i, p in enumerate(ws[:8]):
        print(f"  [{i}] id=0x{p[0]:02x} len={len(p)}: {p[:64].hex()}")

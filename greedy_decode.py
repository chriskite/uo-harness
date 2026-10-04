"""Greedy-decode C2S streams with the per-packet cipher model c[i] = p[i] ^ KS[i].

KS (= K[i]^S) known at positions 0-6 from the walk ladder (capture 2).
Decoding rule: id = c[0]^KS[0]; length from table (fixed, or variable via
c[1]^KS[1], c[2]^KS[2]); keystream position = position within current packet.
Extends the known keystream from known plaintext found in decoded packets
(e.g. account name in the login packet).
"""
import os, sys
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "harness"))
from uo.packets import TABLE

KS = {0: 0x0F, 1: 0x0C, 2: 0xFF, 3: 0x0F, 4: 0x0F, 5: 0x0F, 6: 0x0F}  # capture 2 (S=0x0f)

def dec_byte(c, pos):
    return c ^ KS[pos] if pos in KS else None

def decode_stream(data, label, skip=5):
    print(f"\n===== {label}: greedy decode from offset {skip} ({len(data)-skip} bytes) =====")
    off = skip
    pkts = 0
    while off < len(data):
        pid = dec_byte(data[off], 0)
        if pid is None:
            print(f"  !! no keystream for id byte at stream off {off}")
            break
        fixed = TABLE[pid]
        if fixed > 0:
            plen = fixed
        else:
            b1 = dec_byte(data[off+1], 1) if off+1 < len(data) else None
            b2 = dec_byte(data[off+2], 2) if off+2 < len(data) else None
            if b1 is None or b2 is None:
                print(f"  !! var-len packet 0x{pid:02X} at {off}: len bytes beyond known keystream")
                break
            plen = (b1 << 8) | b2
            if plen < 3 or plen > 0x8000 or off + plen > len(data):
                print(f"  !! var-len packet 0x{pid:02X} at {off}: implausible len {plen}")
                break
        pkt = data[off:off+plen]
        dec = bytes((b ^ KS[i]) if i in KS else 0x2E for i, b in enumerate(pkt))  # '.' for unknown
        known = min(plen, max(KS) + 1)
        asc = "".join(chr(x) if 32 <= x < 127 else "." for x in dec)
        print(f"  [{pkts:3}] off={off:5} id=0x{pid:02X} len={plen:5} ks=0..{known-1}  {dec[:min(plen,40)].hex()}  {asc[:40]}")
        # extend keystream: login packet 0x91 with account at pos 3
        if pid == 0x91 and plen >= 33:
            name = b"Hackworth"
            for i, ch in enumerate(name):
                pos = 3 + i
                ks = pkt[pos] ^ ch
                if pos in KS and KS[pos] != ks:
                    print(f"      !! KS mismatch at pos {pos}: walk={KS[pos]:02x} vs login={ks:02x}")
                KS.setdefault(pos, ks)
            for pos in range(3 + len(name), 3 + 30):  # zero padding
                if pos < plen:
                    KS.setdefault(pos, pkt[pos] ^ 0x00)
            print(f"      -> keystream extended to pos {max(KS)} from account name")
        off += plen
        pkts += 1
    print(f"  decoded {pkts} packets, stopped at {off}/{len(data)}")

c2s = open(os.path.join(ROOT, "c2s_s2.bin"), "rb").read()
decode_stream(c2s, "capture 2 (S=0x0f)")

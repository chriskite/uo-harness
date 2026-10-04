import struct, sys
import os
ROOT = os.path.dirname(os.path.abspath(__file__))

SRC = os.path.join(ROOT, "capture_2593.pcapng")
DST = os.path.join(ROOT, "capture_2593_fixed.pcap")

data = open(SRC, "rb").read()
off = 0
packets = []  # (ts_sec, ts_usec, framedata)
SNAP = b"\xaa\xaa\x03\x00\x00\x00"  # LLC SNAP header, followed by 2-byte ethertype

while off + 12 <= len(data):
    btype, blen = struct.unpack_from("<II", data, off)
    if blen < 12 or off + blen > len(data):
        break
    if btype == 0x00000006:  # Enhanced Packet Block
        iface, tsh, tsl, caplen, pktlen = struct.unpack_from("<IIIII", data, off + 8)
        frame = data[off + 28 : off + 28 + caplen]
        ts = (tsh << 32) | tsl  # assume microseconds
        snap = frame.find(SNAP, 0, 64)
        if snap >= 0:
            eth = b"\x02\x00\x00\x00\x00\x01\x02\x00\x00\x00\x00\x02" + frame[snap + 6 :]
            packets.append((ts // 1_000_000, ts % 1_000_000, eth))
    off += blen

with open(DST, "wb") as f:
    f.write(struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))  # LINKTYPE_ETHERNET
    for ts_s, ts_u, frame in packets:
        f.write(struct.pack("<IIII", ts_s, ts_u, len(frame), len(frame)))
        f.write(frame)
print(f"wrote {len(packets)} packets -> {DST}")

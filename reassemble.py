"""Proper TCP reassembly from the fixed pcap: per-direction, seq-ordered, retransmission-safe."""
import subprocess, sys

PCAP = r"C:/Users/chris/uo-harness/capture_2593_fixed.pcap"
TSHARK = r"C:/Program Files/Wireshark/tshark.exe"

out = subprocess.run(
    [TSHARK, "-r", PCAP, "-Y", "tcp.payload", "-T", "fields",
     "-e", "tcp.srcport", "-e", "tcp.seq", "-e", "tcp.payload"],
    capture_output=True, text=True).stdout

streams = {2593: {}, "client": {}}
for ln in out.splitlines():
    parts = ln.split("\t")
    if len(parts) != 3 or not parts[2]:
        continue
    srcport, seq, payload = int(parts[0]), int(parts[1]), parts[2]
    key = 2593 if srcport == 2593 else "client"
    streams[key][seq] = payload

def reassemble(seqmap):
    data = bytearray()
    next_seq = None
    for seq in sorted(seqmap):
        payload = bytes.fromhex(seqmap[seq])
        if next_seq is None:
            data += payload
            next_seq = seq + len(payload)
        elif seq >= next_seq:
            if seq > next_seq:
                print(f"  !! gap: missing {seq - next_seq} bytes before seq {seq}")
            data += payload
            next_seq = seq + len(payload)
        else:
            overlap = next_seq - seq
            if overlap < len(payload):
                data += payload[overlap:]
                next_seq = seq + len(payload)
    return bytes(data)

c2s = reassemble(streams["client"])
s2c = reassemble(streams[2593])
open(r"C:/Users/chris/uo-harness/c2s.bin", "wb").write(c2s)
open(r"C:/Users/chris/uo-harness/s2c.bin", "wb").write(s2c)
print(f"C2S: {len(c2s)} bytes, S2C: {len(s2c)} bytes")
print("C2S[0:48]:", c2s[:48].hex())
print("S2C[0:48]:", s2c[:48].hex())

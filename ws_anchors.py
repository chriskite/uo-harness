"""Anchor hunting in the record stream: find known serials and motif offsets."""
import sys, re

stream = open(r"C:/Users/chris/uo-harness/ws_stream_141253.bin", "rb").read()
print("stream len", len(stream))

anchors = {
    "player 0x00094375": bytes.fromhex("00094375"),
    "npc 0x000001df": bytes.fromhex("000001df"),
    "npc 0x000001f1": bytes.fromhex("000001f1"),
    "npc 0x000001f0": bytes.fromhex("000001f0"),
    "npc 0x000001e9": bytes.fromhex("000001e9"),
    "npc 0x000001ee": bytes.fromhex("000001ee"),
    "npc 0x000001ef": bytes.fromhex("000001ef"),
    "npc 0x000001fa": bytes.fromhex("000001fa"),
    "motif 001b8500dc": bytes.fromhex("001b8500dc"),
    "motif 005085000c7": bytes.fromhex("00508500c7"),
    "motif 0083 0072": bytes.fromhex("0083007 2".replace(" ","")),
    "motif 7240 0000ac": bytes.fromhex("72400000 ac".replace(" ","")),
    "motif d400008 3": bytes.fromhex("d4000083"),
    "ser 31c7011c": bytes.fromhex("31c7011c"),
    "ser 31c76182": bytes.fromhex("31c76182"),
}
for name, pat in anchors.items():
    offs = [m.start() for m in re.finditer(re.escape(pat), stream)]
    print(f"{name:22} count={len(offs)} offs={offs[:20]}")

# hex dump first 420 bytes (4 carriers)
print()
for off in range(0, 440, 16):
    chunk = stream[off:off+16]
    hexs = " ".join(f"{x:02x}" for x in chunk)
    asc = "".join(chr(x) if 32 <= x < 127 else "." for x in chunk)
    print(f"{off:6}  {hexs:<48}  {asc}")

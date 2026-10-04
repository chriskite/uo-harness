"""Regenerate Python tables from upstream C# source (no manual transcription)."""
import re, sys
import os
ROOT = os.path.dirname(os.path.abspath(__file__))

CUO = os.path.join(ROOT, "ClassicUO-main/src/ClassicUO.Client/Network")
OUT = os.path.join(ROOT, "harness/uo/_generated_tables.py")

# --- Huffman _decTree ---
src = open(f"{CUO}/Huffman.cs").read()
m = re.search(r"_decTree\s*=\s*new int\[\]\s*\{(.*?)\};", src, re.S)
body = re.sub(r"/\*.*?\*/", "", m.group(1))
tree = [int(x) for x in re.findall(r"-?\d+", body)]
assert len(tree) == 512, f"huffman tree: expected 512 entries, got {len(tree)}"
# sanity: every positive value must be a valid node index
for v in tree:
    assert v <= 255 and v >= -256, f"bad entry {v}"

# --- PacketsTable _packetsTable ---
src = open(f"{CUO}/PacketsTable.cs").read()
m = re.search(r"_packetsTable\s*=\s*\{(.*?)\};", src, re.S)
body = re.sub(r"//.*", "", m.group(1))
toks = re.findall(r"0x[0-9A-Fa-f]+|-?\d+", body)
table = [int(t, 16) if t.lower().startswith("0x") else int(t) for t in toks]
assert len(table) == 255, f"packets table: expected 255 entries (0x00-0xFE), got {len(table)}"
table.append(-1)  # 0xFF: upstream treats id >= 0xFF as variable-length
with open(OUT, "w") as f:
    f.write('"""GENERATED from upstream ClassicUO C# source by gen_tables.py. Do not edit by hand."""\n\n')
    f.write("HUFFMAN_TREE = [\n")
    for i in range(0, 512, 16):
        f.write("    " + ", ".join(str(v) for v in tree[i:i+16]) + ",\n")
    f.write("]\n\nPACKET_LENGTHS_BASE = [\n")
    for i in range(0, 256, 8):
        f.write("    " + ", ".join(f"0x{v:04X}" if v > 0 else "-1" for v in table[i:i+8]) + ",  # %02X-%02X\n" % (i, i+7))
    f.write("]\n")
print(f"OK: tree={len(tree)} table={len(table)} -> {OUT}")

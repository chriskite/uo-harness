"""Old (no XOR, Huffman from byte 19) vs corrected S2C decode on the same eight early captures."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load, save  # noqa: E402
from svgchart import bar  # noqa: E402

s = load("04_phantom_decode")["sessions"]
cats = [f'{t["tag"][4:6]}-{t["tag"][6:8]} {t["tag"][9:11]}:{t["tag"][11:13]}' for t in s]
series = {
    "old decoder: packets framed": [t["old_framed"] / 1000 for t in s],
    "old decoder: desync events": [t["old_desync"] / 1000 for t in s],
    "corrected decoder: packets framed": [t["corrected_framed"] / 1000 for t in s],
}
save("04_phantom", bar(cats, series, ylabel="S2C packets (thousands)",
                       xlabel="capture (session start, CDT)", h=300))

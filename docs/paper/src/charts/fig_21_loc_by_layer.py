"""Appendix B: lines of code by layer at the snapshot commit (ee59524), from data/21_loc_by_layer.json."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load, save  # noqa: E402
from svgchart import hbar  # noqa: E402

d = load("21_loc_by_layer")
rows = [(f"{L['name']} ({L['files']})", L["lines"]) for L in d["layers"]]
rows += [(f"{o['name']} ({o['files']})", o["lines"]) for o in d["other"]]
rows.sort(key=lambda r: -r[1])
svg = hbar([r[0] for r in rows], [r[1] for r in rows],
           xlabel="physical lines at ee59524 (files in parentheses)",
           label_w=300, bar_h=18,
           fmt=lambda v: f"{int(v):,}")
save("21_loc_by_layer", svg)
print("wrote figures/21_loc_by_layer.svg")

"""Tool calls by tool across all omp sessions, with error rate in the label.

Writes figures/11_tools.svg from data/11_tools.json.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load, save  # noqa: E402
from svgchart import hbar  # noqa: E402

rows = sorted(load("11_tools")["rows"], key=lambda r: -r["calls"])
labels = []
for r in rows:
    rate = 100 * r["errors"] / r["calls"] if r["calls"] else 0
    labels.append(f'{r["tool"]}  ({rate:.1f}% err)' if r["errors"] else r["tool"])
svg = hbar(labels, [r["calls"] for r in rows], xlabel="tool calls (top-level + subagent sessions)",
           title="omp tool calls by tool", label_w=190, bar_h=18,
           fmt=lambda v: f"{v:,.0f}")
save("11_tools", svg)

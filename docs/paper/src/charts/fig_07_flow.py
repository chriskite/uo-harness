"""Planner data-flow diagram (figures/07_flow.svg): a hand-laid SVG using the page's diagram classes."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import save

ROW_Y = [44, 104, 164, 224, 284, 344]
H = 46


def box(x, y, w, title, sub, cls):
    out = [f'<rect class="d-box {cls}" x="{x}" y="{y}" width="{w}" height="{H}" rx="8"/>',
           f'<text class="d-text" x="{x + w / 2}" y="{y + 19}" text-anchor="middle">{title}</text>']
    if sub:
        out.append(f'<text class="d-small" x="{x + w / 2}" y="{y + 36}" text-anchor="middle">{sub}</text>')
    return "\n".join(out)


IX, IW = 30, 200      # inputs column
PX, PW = 300, 220     # posteriors column
inputs = [("character", "skill · hatchets · weight"),
          ("trip rows", "every trip, aborted ones too"),
          ("home + rune libraries", "landings, own books"),
          ("job events", "pk_seen · recall · thief · death"),
          ("harvest_attempts", "per-tree outcomes"),
          ("prices", "board · hatchets · supplies")]
posts = [("field rate λ", "Gamma, φ, skill-rescaled"),
         ("overhead T", "Normal, structural prior"),
         ("landing", "nearest rune with a route"),
         ("hazards h_D · h_S · h_T", "Gamma, shrunk to pooled"),
         ("regrowth window", "isotonic fit"),
         ("grove capacity", "Beta yielding share")]

parts = ['<svg class="diagram" viewBox="0 0 1000 470" role="img" aria-label="Planner data flow">',
         '<rect class="d-zone" x="16" y="16" width="228" height="390" rx="10"/>',
         '<text class="d-small" x="130" y="32" text-anchor="middle">memory store + live state</text>',
         '<rect class="d-zone" x="286" y="16" width="248" height="390" rx="10"/>',
         '<text class="d-small" x="410" y="32" text-anchor="middle">posteriors (per spot, pooled)</text>']
for (t, s), y in zip(inputs, ROW_Y):
    parts.append(box(IX, y, IW, t, s, "gold"))
for (t, s), y in zip(posts, ROW_Y):
    parts.append(box(PX, y, PW, t, s, "blue"))

TSX, TSY, TSW, TSH = 590, 168, 180, 74
parts.append(f'<rect class="d-box purple" x="{TSX}" y="{TSY}" width="{TSW}" height="{TSH}" rx="8"/>')
parts.append(f'<text class="d-text" x="{TSX + TSW / 2}" y="{TSY + 22}" text-anchor="middle" font-weight="700">Thompson sampling</text>')
parts.append(f'<text class="d-small" x="{TSX + TSW / 2}" y="{TSY + 41}" text-anchor="middle">one draw per eligible spot</text>')
parts.append(f'<text class="d-small" x="{TSX + TSW / 2}" y="{TSY + 58}" text-anchor="middle">value = max over Q of Rate(Q)</text>')
OX, OW = 820, 160
parts.append(box(OX, 150, OW, "spot · Q* · trips", "timeout · hatchet · regrow", "green"))
parts.append(box(OX, 260, OW, "ctl run lumber …", "the runner (one spot)", "green"))


def mid(y):
    return y + H / 2


R_IN, L_P, R_P = IX + IW, PX, PX + PW
edges = [
    (R_IN, mid(ROW_Y[0]), L_P, mid(ROW_Y[0])),          # character -> λ
    (R_IN, mid(ROW_Y[1]) - 8, L_P, mid(ROW_Y[0]) + 8),  # trip rows -> λ
    (R_IN, mid(ROW_Y[1]), L_P, mid(ROW_Y[1])),          # trip rows -> T
    (R_IN, mid(ROW_Y[1]) + 8, L_P, mid(ROW_Y[3]) - 8),  # trip rows -> hazards
    (R_IN, mid(ROW_Y[2]), L_P, mid(ROW_Y[2])),          # home -> landing
    (R_IN, mid(ROW_Y[3]), L_P, mid(ROW_Y[3])),          # job events -> hazards
    (R_IN, mid(ROW_Y[4]), L_P, mid(ROW_Y[4])),          # harvest -> regrowth
]
for x1, y1, x2, y2 in edges:
    parts.append(f'<path class="d-edge" d="M {x1} {y1} L {x2 - 2} {y2}"/>')
# within the posteriors column: landing -> T, regrowth -> capacity
parts.append(f'<path class="d-edge" d="M {PX + PW / 2} {ROW_Y[2]} L {PX + PW / 2} {ROW_Y[1] + H + 2}"/>')
parts.append(f'<path class="d-edge" d="M {PX + PW / 2} {ROW_Y[4] + H} L {PX + PW / 2} {ROW_Y[5] - 2}"/>')
# posteriors -> Thompson
for i, ty in ((0, TSY + 14), (1, TSY + 28), (3, TSY + 46), (5, TSY + 62)):
    parts.append(f'<path class="d-edge blue" d="M {R_P} {mid(ROW_Y[i])} L {TSX - 2} {ty}"/>')
# prices -> Thompson, under the posteriors column
py = ROW_Y[5] + H / 2
parts.append(f'<path class="d-edge" d="M {R_IN} {py + 10} C 260 430, 560 430, {TSX + TSW / 2} {TSY + TSH + 2}"/>')
parts.append(f'<text class="d-elabel" x="430" y="424" text-anchor="middle">gear and supplies in logs</text>')
# Thompson -> output -> runner
parts.append(f'<path class="d-edge" d="M {TSX + TSW} {TSY + 30} L {OX - 2} {150 + H / 2}"/>')
parts.append(f'<path class="d-edge" d="M {OX + OW / 2} {150 + H} L {OX + OW / 2} {258}"/>')
# feedback: runner -> store
parts.append(f'<path class="d-edge dash" d="M {OX + OW / 2} {260 + H} L {OX + OW / 2} 452 L 130 452 L 130 {408}"/>')
parts.append('<text class="d-elabel" x="700" y="456" text-anchor="middle">a new trip row per trip</text>')
parts.append('</svg>')
save("07_flow", "\n".join(parts) + "\n")

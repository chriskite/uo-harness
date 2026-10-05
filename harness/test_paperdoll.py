"""Tests for harness/paperdoll.py.

Deterministic: synthetic gump art, hue tables and tiledata cover layer order,
transparency, full vs partial hues, the female gump fallback and reading the
state. One smoke check renders the real body gump from the install dir
(read-only) when it's present.

Run: python harness/test_paperdoll.py
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import paperdoll  # noqa: E402
import uomap  # noqa: E402

FAILURES = []
RED, GREEN, BLUE, GREY = 31 << 10, 31 << 5, 31, (16 << 10) | (16 << 5) | 16


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


class FakeGumps:
    def __init__(self, imgs):
        self.imgs = imgs

    def __contains__(self, gid):
        return gid in self.imgs

    def get(self, gid):
        return self.imgs.get(gid)


class FakeHues:
    def __init__(self, tables):
        self.tables = tables

    def table(self, hue):
        return self.tables.get(hue & 0x3FFF)


class FakeTD:
    def __init__(self, items):
        self.items = items

    def item(self, g):
        return self.items[g]


def item(gm, gf=0, anim=0, partial=False):
    return SimpleNamespace(gump_male=gm, gump_female=gf, anim_id=anim,
                           flags=uomap.TileFlag.PARTIAL_HUE if partial else 0)


def px(w, h, bgra, x, y):
    o = (y * w + x) * 4
    return tuple(bgra[o:o + 4])


def main():
    print("== composition ==")
    imgs = {12: (2, 1, (GREY, GREY)), 13: (2, 1, (BLUE, BLUE)),
            100: (2, 1, (RED, 0)),            # robe: left pixel, right transparent
            101: (2, 1, (GREEN, GREEN)),      # tunic: both pixels
            102: (2, 1, (GREY, RED)),         # a partial-hue item
            103: (2, 1, (0, GREEN))}          # female-only art
    td = FakeTD({0x1F03: item(100), 0x13D3: item(101), 0x1234: item(102, partial=True),
                 0x2000: item(100, gf=103)})
    table = tuple([BLUE] * 32)
    pd = paperdoll.Paperdoll(FakeGumps(imgs), FakeHues({5: table}), td)
    layers = pd.layers(0x190, 0, [(0x16, 0x1F03, 0), (0x0D, 0x13D3, 0)])
    check("body first, then the tunic (torso) below the robe", [g for g, _, _ in layers] == [12, 101, 100],
          str(layers))
    w, h, out = pd.compose(layers)
    check("robe pixel wins over the tunic; transparent robe pixel shows the tunic",
          px(w, h, out, 0, 0) == (0, 0, 255, 255) and px(w, h, out, 1, 0) == (0, 255, 0, 255))
    w, h, out = pd.compose(pd.layers(0x190, 0, [(0x0D, 0x13D3, 5)]))
    check("a full hue recolours every pixel through the table", px(w, h, out, 0, 0) == (255, 0, 0, 255)
          and px(w, h, out, 1, 0) == (255, 0, 0, 255))
    w, h, out = pd.compose(pd.layers(0x190, 0, [(0x05, 0x1234, 5)]))
    check("a partial hue recolours only grey pixels", px(w, h, out, 0, 0) == (255, 0, 0, 255)
          and px(w, h, out, 1, 0) == (0, 0, 255, 255))
    check("female body uses gump 13 and the female item art",
          [g for g, _, _ in pd.layers(0x191, 0, [(0x16, 0x2000, 0)])] == [13, 103])
    check("male body ignores the female art", [g for g, _, _ in pd.layers(0x190, 0, [(0x16, 0x2000, 0)])] == [12, 100])
    a = pd.png(0x190, 0, [(0x16, 0x1F03, 0)])
    check("renders are cached by body, hue and gear", pd.png(0x190, 0, [(0x16, 0x1F03, 0)]) is a
          and a[:8] == b"\x89PNG\r\n\x1a\n")
    # 8x4 canvas, opaque 2x1 block at x=0..1,y=0 -> centred at x=3..4,y=1 (odd slack rounds down)
    canvas = bytearray(8 * 4 * 4)
    canvas[0:8] = bytes([1, 2, 3, 255, 4, 5, 6, 255])
    moved = paperdoll.Paperdoll.centered(8, 4, bytes(canvas))
    check("content bounding box is centred in the canvas",
          px(8, 4, moved, 3, 1) == (1, 2, 3, 255) and px(8, 4, moved, 4, 1) == (4, 5, 6, 255)
          and px(8, 4, moved, 0, 0) == (0, 0, 0, 0) and len(moved) == len(canvas))
    right = bytearray(8 * 4 * 4)
    right[(3 * 8 + 6) * 4:(3 * 8 + 8) * 4] = bytes([1, 2, 3, 255, 4, 5, 6, 255])
    moved = paperdoll.Paperdoll.centered(8, 4, bytes(right))
    check("content right/below centre moves left/up",
          px(8, 4, moved, 3, 1) == (1, 2, 3, 255) and px(8, 4, moved, 4, 1) == (4, 5, 6, 255))

    print("== from_state ==")
    st = {"world": {"self": {"serial": "0x00094375", "body": 0x190, "stats": {"hue": 0x83EA}},
                    "items": {"0x1": {"graphic": 0x0F44, "layer": 2, "container": "0x00094375"},
                              "0x2": {"graphic": 0x0F0C, "container": "0x40000010"},
                              "0x3": {"graphic": 0x3EA2, "layer": 0x19, "container": "0x00094375"},
                              "0x4": {"graphic": 0x0E7C, "layer": 0x1D, "container": "0x00094375"},
                              "0x5": {"graphic": 0x1517, "layer": 5, "container": "0x00001234"}}}}
    check("worn items only (not the pack, mount, bank box or others' gear)",
          paperdoll.from_state(st) == (0x190, 0x83EA, [(2, 0x0F44, 0)]), str(paperdoll.from_state(st)))
    check("no player -> None", paperdoll.from_state({"world": {"self": {}}}) is None)

    if os.path.exists(paperdoll.GUMPS_PATH):
        print("== real client art (read-only) ==")
        real = paperdoll.Paperdoll()
        w, h, _ = real.gumps.get(12)
        check("male body gump is 260x237", (w, h) == (260, 237), str((w, h)))
        check("hatchet paperdoll gump = 50000 + anim", real.item_gump(0x0F44, False) == 50615)
        data = real.png(0x190, 0x83EA, [(2, 0x0F44, 0), (0x16, 0x1F03, 0)])
        check("a full render is a PNG", data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) > 1000)
    print(f"\npaperdoll: {'ALL PASS' if not FAILURES else f'{len(FAILURES)} FAILURES'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

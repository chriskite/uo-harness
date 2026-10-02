"""The character's paperdoll, drawn from the client's own art (read-only from
the install dir), for the visualizer.

gumps.uoo is read by uoart.UooImages (format in uoart.py and docs/MAP.md).
Verified 2026-09-30 on all 7018 entries (the buffer is always width*height);
body gump 12 and backpack gump 50422 render as the familiar paperdoll art.

hues.mul (ClassicUO HuesLoader): groups of u32 header + 8 x (32 u16 colours,
u16 start, u16 end, 20-byte name). Hue h (1-based) = group (h-1)>>3, entry
(h-1)&7; a pixel's colour is table[red 5 bits of the pixel] (GetColor16); for
items with the tiledata PartialHue flag only grey pixels are hued
(GetPartialHueColor).

Layout: body gump 12 (male) / 13 (female) [0x0E/0x0F elves], then each worn
item's paperdoll gump (artdata male/female gump ids = 50000/60000 + anim id)
in the classic paperdoll layer order (ClassicUO PaperDollInteractable's
_layerOrder before PaperdollOrder replaced it) [INFERENCE: this source tree's
PaperdollOrder table isn't included]. The mount isn't drawn.
"""
import collections
import os
import struct

import uomap
from pngenc import png_bytes
from uoart import GUMPS_PATH, UooImages, rgb555

HUES_PATH = os.path.join(uomap.INSTALL, "hues.mul")
HUE_GROUP = 4 + 8 * (32 * 2 + 2 + 2 + 20)
MAX_HUE = 0x0BB8                     # ClassicUO FixHue: higher hues fall back to 1
BODY_GUMPS = {0x190: 12, 0x192: 12, 0x191: 13, 0x193: 13, 0x25D: 14, 0x25F: 14, 0x25E: 15, 0x260: 15}
FEMALE_BODIES = frozenset([0x191, 0x193, 0x25E, 0x260])
LAYER_BACKPACK, LAYER_MOUNT = 0x15, 0x19
# Cloak, Shirt, Pants, Shoes, Legs, Arms, Torso, Tunic, Ring, Bracelet, Face, Gloves, Skirt,
# Robe, Waist, Necklace, Hair, Beard, Earrings, Helmet, OneHanded, TwoHanded, Talisman
LAYER_ORDER = (0x14, 0x05, 0x04, 0x03, 0x18, 0x13, 0x0D, 0x11, 0x08, 0x0E, 0x0F, 0x07, 0x17,
               0x16, 0x0C, 0x0A, 0x0B, 0x10, 0x12, 0x06, 0x01, 0x02, 0x09)
RENDER_CACHE = 16


class Hues:
    """hues.mul colour tables."""

    def __init__(self, path: str = HUES_PATH):
        with open(path, "rb") as f:
            data = f.read()
        self.tables = []
        for g in range(len(data) // HUE_GROUP):
            base = g * HUE_GROUP + 4
            for e in range(8):
                self.tables.append(struct.unpack_from("<32H", data, base + e * 88))

    def table(self, hue: int):
        """The 32-colour table of a hue as sent by the server, or None (no hue)."""
        h = hue & 0x3FFF
        if h == 0:
            return None
        if h >= MAX_HUE:
            h = 1
        return self.tables[h - 1] if h - 1 < len(self.tables) else None


class Paperdoll:
    def __init__(self, gumps: UooImages | None = None, hues: Hues | None = None, td=None):
        self.gumps = gumps or UooImages(GUMPS_PATH)
        self.hues = hues or Hues()
        self.td = td or uomap.tiledata()
        self._renders = collections.OrderedDict()

    def item_gump(self, graphic: int, female: bool) -> int | None:
        it = self.td.item(graphic)
        for gid in ((it.gump_female, it.gump_male) if female else (it.gump_male,)):
            if gid and gid in self.gumps:
                return gid
        for base in ((60000, 50000) if female else (50000,)):
            if it.anim_id and base + it.anim_id in self.gumps:
                return base + it.anim_id
        return None

    def layers(self, body: int, skin_hue: int, equipment) -> list:
        """[(gump id, hue, partial)] bottom to top. equipment: (layer, graphic, hue)."""
        female = body in FEMALE_BODIES
        out = [(BODY_GUMPS.get(body, 13 if female else 12), skin_hue, False)]
        worn = {layer: (graphic, hue) for layer, graphic, hue in equipment if graphic is not None}
        order = [LAYER_BACKPACK] + [layer for layer in LAYER_ORDER]
        for layer in order:
            if layer not in worn:
                continue
            graphic, hue = worn[layer]
            gid = self.item_gump(graphic, female)
            if gid is not None:
                partial = bool(self.td.item(graphic).flags & uomap.TileFlag.PARTIAL_HUE)
                out.append((gid, hue or 0, partial))
        return out

    def compose(self, layers) -> tuple[int, int, bytes]:
        """(w, h, BGRA) of the layers drawn over each other (canvas = body size)."""
        base = self.gumps.get(layers[0][0])
        if base is None:
            raise ValueError(f"no body gump {layers[0][0]}")
        W, H = base[0], base[1]
        out = bytearray(W * H * 4)
        for gid, hue, partial in layers:
            img = self.gumps.get(gid)
            if img is None:
                continue
            w, h, px = img
            table = self.hues.table(hue)
            colours = {}
            for y in range(min(h, H)):
                row = y * w
                orow = y * W
                for x in range(min(w, W)):
                    c = px[row + x]
                    if not c:
                        continue
                    bgra = colours.get(c)
                    if bgra is None:
                        cc = c
                        if table is not None:
                            r5, g5, b5 = (c >> 10) & 31, (c >> 5) & 31, c & 31
                            if not partial or (r5 == g5 == b5):
                                cc = table[r5]
                        r, g, b = rgb555(cc)
                        bgra = colours[c] = bytes((b, g, r, 255))
                    o = (orow + x) * 4
                    out[o:o + 4] = bgra
        return W, H, bytes(out)

    def png(self, body: int, skin_hue: int, equipment) -> bytes:
        key = (body, skin_hue, tuple(sorted((layer, g, h) for layer, g, h in equipment)))
        hit = self._renders.get(key)
        if hit is not None:
            self._renders.move_to_end(key)
            return hit
        w, h, bgra = self.compose(self.layers(body, skin_hue, equipment))
        data = png_bytes(w, h, bgra, alpha=True)
        self._renders[key] = data
        if len(self._renders) > RENDER_CACHE:
            self._renders.popitem(last=False)
        return data


def from_state(state: dict):
    """(body, skin hue, equipment [(layer, graphic, hue)], names [(layer, name)]) of the
    player in a state-port response (world.self + items worn by self), or None."""
    world = state.get("world") or {}
    me = world.get("self") or {}
    serial = me.get("serial")
    stats = me.get("stats") or {}
    body = me.get("body") or stats.get("graphic")
    if serial is None or body is None:
        return None
    ser = int(serial, 16) if isinstance(serial, str) else serial
    equipment = []
    for it in (world.get("items") or {}).values():
        c = it.get("container")
        c = int(c, 16) if isinstance(c, str) else c
        layer = it.get("layer")
        if c == ser and layer and layer != LAYER_MOUNT and layer < 0x1A and it.get("graphic") is not None:
            equipment.append((layer, it["graphic"], it.get("hue") or 0))
    return body, stats.get("hue") or 0, equipment

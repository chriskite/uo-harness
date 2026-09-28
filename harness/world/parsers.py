"""Packet parsers: one generic table reader plus procedural parsers.

Declarative packets are walked from the field tables in layouts.py. The
procedural parsers hand-code the layouts that are inherently non-tabular:
count-driven (0x3A, 0x3C, 0x89, 0x16/0x17, sub-0x15), bit-flag-driven (0x1A),
type-gated (0x11), layout-text (0xB0, 0xDD), and the 0xFF dialect dispatch.

Every parser is bounds-checked: a short buffer raises PacketIncomplete (the
runtime counts it and moves on — a truncated packet is never fatal). Declared
length prefixes are not trusted for reads; the actual buffer bounds govern.

Text-length conventions (neither appears in the captures; doc gives no
semantics — chosen to match upstream reader idioms, flagged here):
  0xB0 text lines: u16be byte count, UTF-16BE text.
  0xDD text lines: u16be code-unit count, UTF-16LE text (doc §4 says UTF-16LE).
  0xB1 text entries: u16be byte count INCLUDING its own 2 bytes (standard UO),
  UTF-16BE text.
"""
import struct
import zlib

from .layouts import LAYOUTS_S2C, LAYOUTS_C2S


class PacketIncomplete(Exception):
    """The buffer ended before the documented layout was fully read."""


class _Reader:
    """Bounds-checked big-endian cursor over a packet buffer."""

    __slots__ = ("b", "p")

    def __init__(self, buf, pos=0):
        self.b = buf
        self.p = pos

    def take(self, n):
        if n < 0 or self.p + n > len(self.b):
            raise PacketIncomplete(
                f"need {n} bytes at {self.p}, have {len(self.b)}")
        v = self.b[self.p:self.p + n]
        self.p += n
        return v

    def u8(self):
        return self.take(1)[0]

    def i8(self):
        v = self.u8()
        return v - 256 if v > 127 else v

    def u16(self):
        return int.from_bytes(self.take(2), "big")

    def i16(self):
        v = self.u16()
        return v - 65536 if v > 32767 else v

    def u32(self):
        return int.from_bytes(self.take(4), "big")

    def u64(self):
        return int.from_bytes(self.take(8), "big")

    def f32le(self):
        # Outlands dialect embeds little-endian float32 (doc §conventions)
        return struct.unpack("<f", self.take(4))[0]

    def asciiz(self):
        end = self.b.find(b"\x00", self.p)
        if end < 0:
            raise PacketIncomplete("unterminated asciiz")
        s = self.b[self.p:end].decode("ascii", "replace")
        self.p = end + 1
        return s


def _ascii(raw):
    return raw.decode("ascii", "replace").rstrip("\x00")


# ---------------------------------------------------------------------------
# Generic table reader
# ---------------------------------------------------------------------------

def read_layout(layout, pkt):
    """Walk a (name, type, offset) field table against pkt."""
    out = {}
    for name, ftype, off in layout:
        if ftype.startswith("skip:"):
            n = int(ftype[5:])
            if off + n > len(pkt):
                raise PacketIncomplete(f"skip:{n} at {off} overruns buffer")
            continue
        if ftype == "u8":
            out[name] = _Reader(pkt, off).u8()
        elif ftype == "i8":
            out[name] = _Reader(pkt, off).i8()
        elif ftype == "u16be":
            out[name] = _Reader(pkt, off).u16()
        elif ftype == "u16le":
            r = _Reader(pkt, off)
            out[name] = int.from_bytes(r.take(2), "little")
        elif ftype == "u32be":
            out[name] = _Reader(pkt, off).u32()
        elif ftype == "u32le":
            r = _Reader(pkt, off)
            out[name] = int.from_bytes(r.take(4), "little")
        elif ftype.startswith("bytes:"):
            n = int(ftype[6:])
            out[name] = _Reader(pkt, off).take(n)
        elif ftype.startswith("ascii:"):
            n = int(ftype[6:])
            out[name] = _ascii(_Reader(pkt, off).take(n))
        elif ftype.startswith("utf16:"):
            n = int(ftype[6:])
            out[name] = _Reader(pkt, off).take(n).decode(
                "utf-16-be", "replace").rstrip("\x00")
        else:
            raise ValueError(f"unknown field type {ftype!r}")
    return out


# Post-read value adjustments for table-driven packets (doc-mandated masks).
def _post_0x21(fields):
    fields["dir"] &= 7  # doc §1: direction "& 7 applied"
    return fields


_POST = {0x21: _post_0x21}


def parse_fixed(pid, pkt, direction="s2c"):
    """Parse a table-driven packet, or return None if no layout is registered."""
    table = LAYOUTS_S2C if direction == "s2c" else LAYOUTS_C2S
    layout = table.get(pid)
    if layout is None:
        return None
    fields = read_layout(layout, pkt)
    post = _POST.get(pid)
    return post(fields) if post else fields


# ---------------------------------------------------------------------------
# Procedural S2C parsers
# ---------------------------------------------------------------------------

def _p_character_status(pkt):
    """0x11 CharacterStatus — type-gated tail blocks (doc §1)."""
    r = _Reader(pkt)
    r.take(1)
    r.u16()  # declared length, not enforced
    d = {"serial": r.u32(), "name": _ascii(r.take(30)),
         "hits": r.u16(), "hits_max": r.u16(), "renamable": r.i8()}
    t = r.u8()
    d["type"] = t
    if t > 0:
        d["female"] = r.i8()
        d["str"] = r.u16()
        d["dex"] = r.u16()
        d["int"] = r.u16()
        d["stam"] = r.u16()
        d["stam_max"] = r.u16()
        d["mana"] = r.u16()
        d["mana_max"] = r.u16()
        d["gold"] = r.u32()
        d["physical_resist"] = r.u16()
        d["weight"] = r.u16()
        if t >= 5:
            d["weight_max"] = r.u16()
            d["race"] = r.u8()
        if t >= 3:
            d["stats_cap"] = r.u16()
            d["followers"] = r.u8()
            d["followers_max"] = r.u8()
        if t >= 4:
            d["fire_resist"] = r.u16()
            d["cold_resist"] = r.u16()
            d["poison_resist"] = r.u16()
            d["energy_resist"] = r.u16()
            d["luck"] = r.u16()
            d["damage_min"] = r.u16()
            d["damage_max"] = r.u16()
            d["tithing"] = r.u32()
        if t >= 6:
            for i in range(15):
                d[f"extra_{i}"] = r.u16()
    return d


def _p_skills(pkt):
    """0x3A UpdateSkills — type-driven record loop (doc §1)."""
    r = _Reader(pkt)
    r.take(1)
    r.u16()
    t = r.u8()
    d = {"type": t}
    if t == 0xFE:  # skill-name table (Outlands custom skills)
        names = []
        for _ in range(r.u16()):
            names.append({"have_button": r.i8(),
                          "name": r.take(r.u8()).decode("ascii", "replace")})
        d["names"] = names
        return d
    skills = []
    while r.p < len(pkt):
        sid = r.u16()
        if t == 0 and sid == 0:
            break  # full-list terminator
        e = {"id": sid - 1 if t in (0, 2) else sid,
             "value": r.u16(), "base": r.u16(), "lock": r.u8()}
        if t in (1, 2, 3, 0xDF):
            e["cap"] = r.u16()
        skills.append(e)
        if t in (0xFF, 0xDF):
            break  # single-skill update
    d["skills"] = skills
    return d


def _p_world_item(pkt):
    """0x1A UpdateItem — bit-flag-driven optional fields (doc §2)."""
    r = _Reader(pkt)
    r.take(1)
    r.u16()
    serial = r.u32()
    has_amount = bool(serial & 0x80000000)
    serial &= 0x7FFFFFFF
    graphic = r.u16()
    d = {"serial": serial}
    if graphic & 0x8000:
        d["graphic_offset"] = r.u8()
    if graphic & 0x4000:
        d["subtype"] = 2
    d["graphic"] = graphic & 0x3FFF
    d["amount"] = r.u16() if has_amount else 1
    x = r.u16()
    has_dir = bool(x & 0x8000)
    d["x"] = x & 0x7FFF
    y = r.u16()
    has_hue = bool(y & 0x8000)
    has_flags = bool(y & 0x4000)
    d["y"] = y & 0x3FFF
    if has_dir:
        d["dir"] = r.u8()
    d["z"] = r.i8()
    if has_hue:
        d["hue"] = r.u16()
    if has_flags:
        d["flags"] = r.u8()
    return d


def _p_container_content(pkt):
    """0x3C UpdateContainedItems — count x 26-byte V12 records (doc §4)."""
    r = _Reader(pkt)
    r.take(1)
    r.u16()
    items = []
    for _ in range(r.u16()):
        rec = {"serial": r.u32(), "graphic": r.u32(), "v11": r.u8()}
        rec["amount"] = r.u16() or 1
        rec["x"] = r.u16()
        rec["y"] = r.u16()
        rec["grid"] = r.u8()
        rec["container"] = r.u32()
        rec["hue"] = r.u16()
        rec["v12"] = r.u32()
        items.append(rec)
    return {"items": items}


def _p_corpse_equipment(pkt):
    """0x89 CorpseEquipment — (layer, serial) pairs, 0-terminated (doc §2)."""
    r = _Reader(pkt)
    r.take(1)
    r.u16()
    d = {"corpse": r.u32(), "equipment": []}
    while True:
        layer = r.i8()
        if layer == 0:
            break
        serial = r.u32()
        if layer != 0x16:  # invalid-layer entries are read but skipped
            d["equipment"].append({"layer": layer, "serial": serial})
    return d


def _p_healthbar(pkt):
    """0x16/0x17 NewHealthbarUpdate — count x (type, enabled) (doc §2)."""
    r = _Reader(pkt)
    r.take(1)
    r.u16()
    d = {"serial": r.u32(), "entries": []}
    for _ in range(r.u16()):
        d["entries"].append({"type": r.u16(), "enabled": r.i8()})
    return d


def _p_open_gump(pkt):
    """0xB0 OpenGump — raw layout text + text lines, not over-structured."""
    r = _Reader(pkt)
    r.take(1)
    r.u16()
    d = {"serial": r.u32(), "gump_id": r.u32(), "x": r.u32(), "y": r.u32()}
    d["layout"] = r.take(r.u16()).decode("ascii", "replace")
    lines = []
    for _ in range(r.u16()):
        lines.append(r.take(r.u16()).decode("utf-16-be", "replace"))
    d["lines"] = lines
    return d


def _p_compressed_gump(pkt):
    """0xDD CompressedGump — two zlib blocks (doc §4).

    Each compressed length prefix includes its own 4 bytes (same convention
    the doc states for the layout block; assumed symmetric for the text block).
    """
    r = _Reader(pkt)
    r.take(1)
    r.u16()
    d = {"serial": r.u32(), "gump_id": r.u32(), "x": r.u32(), "y": r.u32(),
         "compressed": True}
    clen = r.u32()
    r.u32()  # decompressed length (informational)
    try:
        raw = zlib.decompress(r.take(clen - 4))
    except zlib.error as exc:
        raise PacketIncomplete(f"bad gump layout zlib block: {exc}") from exc
    d["layout"] = raw.decode("ascii", "replace")
    clen2 = r.u32()
    r.u32()
    try:
        raw2 = zlib.decompress(r.take(clen2 - 4))
    except zlib.error as exc:
        raise PacketIncomplete(f"bad gump lines zlib block: {exc}") from exc
    rr = _Reader(raw2)
    lines = []
    for _ in range(rr.u16()):
        lines.append(rr.take(rr.u16() * 2).decode("utf-16-le", "replace"))
    d["lines"] = lines
    return d


# ---------------------------------------------------------------------------
# 0xFF Outlands dialect (both directions; sub-id space is per-direction)
# ---------------------------------------------------------------------------

def _p_buff_update(r, d):
    """0xFF sub 8 S2C OutlandsBuffUpdate (doc §5)."""
    d["serial"] = r.u32()
    d["icon_id"] = r.i16()
    d["f1"] = r.u16()
    d["f2"] = r.i16()
    d["f3"] = r.u16()
    d["f4"] = r.i16()
    timers = []
    for _ in range(r.i16()):
        timers.append({"seconds": r.f32le(), "end": r.u64(), "aux": r.u32()})
    d["timers"] = timers
    d["timestamp"] = r.u64()
    d["title"] = r.asciiz()
    if d["title"] == "":
        d["cliloc"] = r.u32()  # empty title -> cliloc lookup instead
    d["description"] = r.asciiz()
    d["category"] = r.i16()
    d["mode"] = r.i16()
    d["scalar"] = r.f32le()
    return d


def _p_name_response(r, d):
    """0xFF sub 0x15 S2C OutlandsItemNameResponse (doc §5)."""
    d["mode"] = r.u8()
    entries = []
    for _ in range(r.u16()):
        entries.append({"serial": r.u32(), "name": r.asciiz()})
    d["entries"] = entries
    return d


def _p_dialect(direction, pkt):
    """0xFF frame: <len u16be> <subId u32be> <payload> (doc §5)."""
    r = _Reader(pkt)
    r.take(1)
    r.u16()
    sub = r.u32()
    d = {"sub": sub}
    if direction == "s2c":
        if sub == 0:  # session handshake (the 19-byte prelude is one frame)
            d["version"] = r.u32()
            d["flag1"] = r.u8()
            d["flag2"] = r.u8()
        elif sub == 3:  # ServerTime.TimeSyncReceived
            d["timestamp"] = r.u64()
        elif sub == 8:
            _p_buff_update(r, d)
        elif sub == 9:  # OutlandsRemoveBuff
            d["serial"] = r.u32()
            d["buff_id"] = r.u16()
        elif sub == 0x15:
            _p_name_response(r, d)
        # all other subs: sub id reported, payload unparsed (doc §5 table)
    else:
        if sub == 3:
            pass  # TimeSyncReq keepalive, empty payload
        elif sub == 4:  # cast spell: u8 flag (0), u16be spell id
            d["flag"] = r.u8()
            d["spell_id"] = r.u16()
        elif sub == 9:  # item/object detail query: 01 0001 <serial>
            r.u8()
            r.u16()
            d["serial"] = r.u32()
    return d


# ---------------------------------------------------------------------------
# Procedural C2S parsers
# ---------------------------------------------------------------------------

def _p_speech(pkt):
    """0xAD unicode speech: type, hue, font, language, NUL-terminated text."""
    r = _Reader(pkt)
    r.take(1)
    r.u16()
    d = {"type": r.u8(), "hue": r.u16(), "font": r.u16(),
         "lang": r.take(4).decode("ascii", "replace")}
    d["text"] = r.take(len(pkt) - r.p).decode(
        "utf-16-be", "replace").rstrip("\x00")
    return d


def _p_gump_response(pkt):
    """0xB1 gump response: button, switch list, text entry list."""
    r = _Reader(pkt)
    r.take(1)
    r.u16()
    d = {"serial": r.u32(), "gump_id": r.u32(), "button_id": r.u32()}
    d["switches"] = [r.u32() for _ in range(r.u32())]
    texts = []
    for _ in range(r.u32()):
        eid = r.u16()
        ln = r.u16()
        if ln == 0:
            # observed on the wire (session_20260928_141253): empty entry
            texts.append({"id": eid, "text": ""})
            continue
        if ln < 2:
            raise PacketIncomplete(f"gump text entry length {ln} < 2")
        texts.append({"id": eid,
                      "text": r.take(ln - 2).decode("utf-16-be", "replace")})
    d["texts"] = texts
    return d


def _p_login(pkt):
    """0x91 game login: account name asciiz @3 (JWT intentionally not kept)."""
    r = _Reader(pkt)
    r.take(1)
    r.u16()
    return {"account": r.asciiz()}


_PROC_S2C = {
    0x11: _p_character_status,
    0x3A: _p_skills,
    0x1A: _p_world_item,
    0x3C: _p_container_content,
    0x89: _p_corpse_equipment,
    0x16: _p_healthbar,
    0x17: _p_healthbar,
    0xB0: _p_open_gump,
    0xDD: _p_compressed_gump,
}

_PROC_C2S = {
    0xAD: _p_speech,
    0xB1: _p_gump_response,
    0x91: _p_login,
}


def parse_packet(direction, pkt):
    """Parse one framed packet. Returns a field dict, or None if no parser
    is registered for this id/direction. Raises PacketIncomplete on a short
    or corrupt buffer."""
    pid = pkt[0]
    if pid == 0xFF:
        return _p_dialect(direction, pkt)
    proc = (_PROC_S2C if direction == "s2c" else _PROC_C2S).get(pid)
    if proc is not None:
        return proc(pkt)
    return parse_fixed(pid, pkt, direction)

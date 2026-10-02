"""Packet parsers: one generic table reader plus procedural parsers.

Declarative packets are walked from the field tables in layouts.py. The
procedural parsers hand-code the layouts that are inherently non-tabular:
count-driven (0x3A, 0x3C, 0x89, 0x16/0x17, 0xA9, sub-0x15), list-driven
(0x78), bit-flag-driven (0x1A), type-gated (0x11), text (0x1C, 0xAE),
layout-text (0xB0, 0xDD), and the 0xFF dialect dispatch.

Every parser is bounds-checked: a short buffer raises PacketIncomplete (the
runtime counts it and moves on — a truncated packet is never fatal). Declared
length prefixes are not trusted for reads; the actual buffer bounds govern.
Where the client's own reader tolerates a short packet (its cursor returns 0
past the end) and real servers send the short form, the parser mirrors that
(0x11 trailing blocks, 0x3A list end).

Text-length conventions:
  0xB0 text lines: u16be byte count, UTF-16BE text (no capture sample yet).
  0xDD text lines: u16be code-unit count, UTF-16BE text (upstream
  CompressedGump; 77 real packets across the captures decode cleanly).
  0xB1 text entries: u16be UTF-16 unit count, UTF-16BE text (Outlands
  Send_GumpResponse; the old "byte count incl. its own 2 bytes" was wrong).
  0xC1/0xCC cliloc args: to the end of the packet, UTF-16LE / UTF-16BE.
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

    def i32(self):
        return int.from_bytes(self.take(4), "big", signed=True)

    def u64(self):
        return int.from_bytes(self.take(8), "big")

    def f32(self):
        # dialect floats decode big-endian on the wire (0xFF sub 8 timer
        # seconds 40400000 = 3.0, 41166666 = 9.4 in real captures)
        return struct.unpack(">f", self.take(4))[0]

    def remaining(self):
        return len(self.b) - self.p

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
        elif ftype == "i32be":
            out[name] = _Reader(pkt, off).i32()
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
            # Real servers send type-5 packets both with (91 B) and without
            # (87 B, e.g. session_20260928_141253) the tithing u32; the
            # client's cursor reads 0 past the end, so it is optional here.
            if r.remaining() >= 4:
                d["tithing"] = r.u32()
        if t >= 6:
            # each read bounds-guarded in the client (Position+2 > Length -> 0)
            for i in range(15):
                d[f"extra_{i}"] = r.u16() if r.remaining() >= 2 else 0
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
        if r.p >= len(pkt):
            break  # trailing u16 0 terminator (upstream: id read at end)
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
    """0xDD CompressedGump — zlib layout block + optional zlib lines block.

    Upstream CompressedGump, proven on all 77 real 0xDD packets: each
    compressed length prefix counts the following decompressed-length u32
    (so the zlib data is clen-4 bytes); the lines block is preceded by a u32
    line count and omitted when that count is 0; each line is a u16be
    code-unit count + UTF-16BE text. Real packets end with 4 zero bytes.
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
    lines = []
    nlines = r.u32()
    if nlines:
        clen2 = r.u32()
        r.u32()
        try:
            raw2 = zlib.decompress(r.take(clen2 - 4))
        except zlib.error as exc:
            raise PacketIncomplete(f"bad gump lines zlib block: {exc}") from exc
        rr = _Reader(raw2)
        for _ in range(nlines):
            lines.append(rr.take(rr.u16() * 2).decode("utf-16-be", "replace"))
    d["lines"] = lines
    return d


def _p_mobile_equip(pkt):
    """0x78 MobileEquip (V12 form of MobileIncoming, decomp MobileEquip
    @ 0x140190e20): serial u32be @3, then records until a 0 item serial:
    item serial u32be, graphic u32be, layer u8, hue u16be, V12 u32be tail.
    No graphic/position/notoriety fields (those arrive via 0x20). All 597
    real 0x78 packets consume exactly under this grammar."""
    r = _Reader(pkt)
    r.take(1)
    r.u16()
    d = {"serial": r.u32(), "equipment": []}
    while True:
        item = r.u32()
        if item == 0:
            break
        d["equipment"].append({"serial": item, "graphic": r.u32(),
                               "layer": r.u8(), "hue": r.u16(),
                               "v12": r.u32()})
    return d


def _p_talk(pkt):
    """0x1C Talk (ASCII): serial, graphic u16, type, hue, font, name[30],
    NUL-terminated ASCII text — upstream layout, unchanged in Outlands."""
    r = _Reader(pkt)
    r.take(1)
    r.u16()
    d = {"serial": r.u32(), "graphic": r.u16(), "type": r.u8(),
         "hue": r.u16(), "font": r.u16(), "name": _ascii(r.take(30))}
    d["text"] = _ascii(r.take(r.remaining())).split("\x00", 1)[0]
    return d


def _p_unicode_talk(pkt):
    """0xAE UnicodeTalk: serial, graphic u16, type, hue, font, lang[4],
    name[30], NUL-terminated UTF-16BE text — upstream layout."""
    r = _Reader(pkt)
    r.take(1)
    r.u16()
    d = {"serial": r.u32(), "graphic": r.u16(), "type": r.u8(),
         "hue": r.u16(), "font": r.u16(), "lang": _ascii(r.take(4)),
         "name": _ascii(r.take(30))}
    raw = r.take(r.remaining() & ~1)
    d["text"] = raw.decode("utf-16-be", "replace").split("\x00", 1)[0]
    return d


def _p_character_list(pkt):
    """0xA9 CharacterList (Outlands): u8 slot count @3, then count x
    ascii[30] names — no password field (5 slots x 30 B + 4 = 154 of the
    161-byte real packet). The 7-byte tail (`00 00000008 ffff` in every
    capture) is left unparsed."""
    r = _Reader(pkt)
    r.take(1)
    r.u16()
    return {"names": [_ascii(r.take(30)) for _ in range(r.u8())]}


def _p_update_name(pkt):
    """0x98 UpdateName (S2C, 37 B): serial u32be @3, name ascii[30] @7."""
    r = _Reader(pkt)
    r.take(1)
    r.u16()
    return {"serial": r.u32(), "name": _ascii(r.take(30))}


def _p_open_menu(pkt):
    """S2C 0x7C OpenMenu: item/question menu (the classic Tracking category
    menu and other skill menus). Mirrors the client read-for-read: decomp
    PacketHandlers.OpenMenu @ 0x140191200 (decompiled/protocol_handlers.c:
    20838-21083); upstream PacketHandlers.cs:2956-3057.
    serial u32, menu_id u16, title (u8 length + ASCII), count u8, then a PEEK
    at the next u16, which is not consumed (decomp :20922-20941):
      nonzero: item menu. Per entry: graphic (u32 at protocol >= 10, u16
               below; decomp :20999-21033), hue u16, name (u8 length +
               ASCII).
      zero:    gray question menu. Per entry: 4 bytes the client skips (kept
               here as `unread`), name (u8 length + ASCII).
    The harness runs protocol 12 (u32 graphics). There the peek sees the first
    graphic's high half, so a graphic below 0x10000 selects the gray branch,
    exactly as the client would read it. No 0x7C was seen in the 27
    captures."""
    r = _Reader(pkt)
    r.take(3)
    d = {"serial": r.u32(), "menu_id": r.u16()}
    d["title"] = _ascii(r.take(r.u8())).split("\x00", 1)[0]
    count = r.u8()
    d["gray"] = r.remaining() < 2 or int.from_bytes(pkt[r.p:r.p + 2], "big") == 0
    entries = []
    for _ in range(count):
        if d["gray"]:
            e = {"unread": r.u32()}
        else:
            e = {"graphic": r.u32(), "hue": r.u16()}
        e["name"] = _ascii(r.take(r.u8())).split("\x00", 1)[0]
        entries.append(e)
    d["entries"] = entries
    return d


# ---------------------------------------------------------------------------
# 0xFF Outlands dialect (both directions; sub-id space is per-direction)
# ---------------------------------------------------------------------------

def _p_buff_update(r, d):
    """0xFF sub 8 S2C OutlandsBuffUpdate (decomp @ 0x14019a600).

    Timer records are 12 bytes on the wire (f32 seconds + u64 end): the
    decomp's 16-byte stride is the in-memory struct, not the wire. Proven on
    all 121 real sub-8 packets (lengths 144/129/78/57 all consume exactly,
    titles read as text: "Stationary Penalty", or "" + cliloc 1015176).
    """
    d["serial"] = r.u32()
    d["icon_id"] = r.i16()
    d["f1"] = r.u16()
    d["f2"] = r.i16()
    d["f3"] = r.u16()
    d["f4"] = r.i16()
    timers = []
    for _ in range(r.i16()):
        timers.append({"seconds": r.f32(), "end": r.u64()})
    d["timers"] = timers
    d["timestamp"] = r.u64()
    d["title"] = r.asciiz()
    if d["title"] == "":
        d["cliloc"] = r.u32()  # empty title -> cliloc lookup instead
    d["description"] = r.asciiz()
    d["category"] = r.i16()
    d["mode"] = r.i16()
    d["scalar"] = r.f32()
    return d


def _p_name_response(r, d):
    """0xFF sub 0x15 S2C OutlandsItemNameResponse (doc §5)."""
    d["mode"] = r.u8()
    entries = []
    for _ in range(r.u16()):
        entries.append({"serial": r.u32(), "name": r.asciiz()})
    d["entries"] = entries
    return d


def _p_quest_arrow(r, d):
    """0xFF sub 0x1A S2C HandleQuestArrow @ 0x1401a0160 (decompiled/
    protocol_handlers.c:14307-14341): mode u8.
      0 set (HandleQuestArrowSet @ 0x1401a01f0, :14430-14582): arrow_id u16,
        1 byte the client skips, type u8, v16 u16, then four u32 (target
        serial, x, y, z) and an asciiz text. Live 20261001_214649 (Tracking,
        Hunting mode): `ff 0034 0000001a 00 0000 00 03 0000 0015aac5 0000078b
        00000a37 00000000 "[Hunting] Joel Embiid"` = serial 0x0015AAC5 at
        (1931,2615), exactly the mobile's world-model position; type 3, v16 0
        on all samples. The 4th u32 is z: a pack llama 20 tiles away (never
        sent to us otherwise) came with z 21 while we stood at z 10.
      1 cancel (HandleQuestArrowCancel @ 0x1401a04f0, :14349-14387): arrow_id u16.
        The server cancels the old arrow before setting the next (new id).
      2 clear all (@ 0x1401a05a0): no payload."""
    d["mode"] = r.u8()
    if d["mode"] == 0:
        d["arrow_id"] = r.u16()
        r.u8()
        d["type"] = r.u8()
        d["v16"] = r.u16()
        d["serial"], d["x"], d["y"], d["z"] = r.u32(), r.u32(), r.u32(), r.i32()
        rest = r.take(r.remaining())
        d["text"] = rest.split(b"\x00", 1)[0].decode("ascii", "replace")
    elif d["mode"] == 1:
        d["arrow_id"] = r.u16()
    return d


def _p_dialect(direction, pkt):
    """0xFF frame: <len u16be> <subId u32be> <payload> (doc §5)."""
    r = _Reader(pkt)
    r.take(1)
    r.u16()
    sub = r.u32()
    d = {"sub": sub}
    if direction == "s2c":
        if sub == 0:  # session handshake (the 13-byte prelude is one frame;
            # flag1/flag2 = the S2C/C2S XOR keys, see uo/s2c.py)
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
        elif sub == 0x1A:
            _p_quest_arrow(r, d)
        elif sub == 0xDEAD:  # OutlandsCorpseFlags @ 0x14019a440 (docs/WORLDMODEL.md)
            d["corpse"] = r.u32()
            d["serial"] = r.u32()     # the mobile that died
            d["notoriety"] = r.u8()
            d["name"] = r.asciiz()    # corpse name
        # sub 5 (World.ProcessDeletes) has no payload; all other subs: sub id
        # reported, payload unparsed (doc §5 table)
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
    """0xAD unicode speech: type, hue, font, language, then either UTF-16BE
    text (plain) or, when type & 0xC0 (keyword-encoded, as the client sends
    speech containing speech.mul keywords), packed 12-bit keyword ids (count
    first) followed by NUL-terminated UTF-8 text."""
    r = _Reader(pkt)
    r.take(1)
    r.u16()
    d = {"type": r.u8(), "hue": r.u16(), "font": r.u16(),
         "lang": r.take(4).decode("ascii", "replace")}
    if d["type"] & 0xC0 == 0xC0:
        head = r.take(2)
        count = (head[0] << 4) | (head[1] >> 4)
        nbytes = (3 * (count + 1) + 1) // 2  # 12 bits per value incl. the count
        packed = head + r.take(nbytes - 2)
        nib = [n for b in packed for n in (b >> 4, b & 0xF)]
        d["keywords"] = [(nib[3 * i] << 8) | (nib[3 * i + 1] << 4) | nib[3 * i + 2]
                         for i in range(1, count + 1)]
        d["type"] &= ~0xC0 & 0xFF
        d["text"] = r.take(len(pkt) - r.p).split(b"\x00", 1)[0].decode("utf-8", "replace")
    else:
        d["text"] = r.take(len(pkt) - r.p).decode(
            "utf-16-be", "replace").rstrip("\x00")
    return d


def _p_gump_response(pkt):
    """0xB1 gump response: button, switch list, text entry list. Entry text
    length is a UTF-16 unit count (Send_GumpResponse @ 0x1401557a0, same as
    actions.gump_response)."""
    r = _Reader(pkt)
    r.take(1)
    r.u16()
    d = {"serial": r.u32(), "gump_id": r.u32(), "button_id": r.u32()}
    d["switches"] = [r.u32() for _ in range(r.u32())]
    texts = []
    for _ in range(r.u32()):
        eid = r.u16()
        units = r.u16()
        texts.append({"id": eid,
                      "text": r.take(2 * units).decode("utf-16-be", "replace")})
    d["texts"] = texts
    return d


def _p_cliloc(pkt):
    """0xC1 / 0xCC cliloc message (DisplayClilocString @ 0x140196760, same
    reads as upstream): serial u32, graphic u16, type u8, hue u16, font u16,
    cliloc u32, [0xCC: affix flags u8], name ascii[30], [0xCC: affix asciiz],
    then args to the end: UTF-16LE for 0xC1, UTF-16BE for 0xCC ('\\t'
    separated; uo/cliloc.translate renders them)."""
    r = _Reader(pkt)
    pid = r.u8()
    r.u16()
    d = {"serial": r.u32(), "graphic": r.u16(), "type": r.u8(),
         "hue": r.u16(), "font": r.u16(), "cliloc": r.u32()}
    d["affix_flags"] = r.u8() if pid == 0xCC else 0
    d["name"] = _ascii(r.take(30))
    d["affix"] = r.asciiz() if pid == 0xCC else ""
    rest = r.take(r.remaining())
    enc = "utf-16-be" if pid == 0xCC else "utf-16-le"
    d["args"] = rest[:len(rest) & ~1].decode(enc, "replace").split("\x00", 1)[0]
    return d


def _p_buy_list(pkt):
    """S2C 0x74 vendor buy list (upstream BuyList): container serial, count
    u8, then per item price u32 + name (u8 length incl. NUL, ASCII). Real
    (164548): `74 0032 4029604f 03 | 00000019 08 "Skillet\\0" | ...`."""
    r = _Reader(pkt)
    r.take(3)
    d = {"container": r.u32(), "items": []}
    for _ in range(r.u8()):
        price = r.u32()
        d["items"].append({"price": price, "name": _ascii(r.take(r.u8()))})
    return d


def _p_buy_request(pkt):
    """C2S 0x3B buy request (Send_BuyRequest, actions.buy_request): vendor
    u32, flag u8 (2 = list follows, 0 = empty), then (layer u8, serial u32,
    amount u16) records to the end."""
    r = _Reader(pkt)
    r.take(3)
    d = {"vendor": r.u32(), "flag": r.u8(), "items": []}
    while r.remaining() >= 7:
        d["items"].append({"layer": r.u8(), "serial": r.u32(), "amount": r.u16()})
    return d


def _p_text_command(pkt):
    """C2S 0x12 text command: type u8 (0x24 use skill, 0x56 cast, 0x58 open
    door, ...), ASCII argument to the NUL."""
    r = _Reader(pkt)
    r.take(3)
    return {"type": r.u8(),
            "text": r.take(r.remaining()).split(b"\x00", 1)[0].decode("ascii", "replace")}


def _p_extended(pkt):
    """0xBF extended command, sub u16 @3. Parsed subs: S2C 0x14 context menu
    (mode u16, serial u32, count u8, entries; mode 2 = cliloc u32, index u16,
    flags u16 [+ hue u16 if flags & 0x20]; mode 1 = index u16, cliloc u16
    (+3000000), flags u16 [+ hue]), C2S 0x13 menu request (serial), C2S 0x15
    menu selection (serial, index u16). Other subs: {"sub": n} only."""
    r = _Reader(pkt)
    r.take(3)
    sub = r.u16()
    d = {"sub": sub}
    if sub == 0x14:
        mode, d["serial"] = r.u16(), r.u32()
        entries = []
        for _ in range(r.u8()):
            if mode >= 2:
                e = {"cliloc": r.u32(), "index": r.u16(), "flags": r.u16()}
            else:
                idx, num = r.u16(), r.u16()
                e = {"cliloc": num + 3000000, "index": idx, "flags": r.u16()}
            if e["flags"] & 0x20:
                e["hue"] = r.u16()
            entries.append(e)
        d["mode"], d["entries"] = mode, entries
    elif sub == 0x13:
        d["serial"] = r.u32()
    elif sub == 0x08 and r.remaining() >= 1:      # S2C map change (real: bf 0006 0008 00)
        d["map"] = r.u8()
    elif sub == 0x15:
        d["serial"], d["index"] = r.u32(), r.u16()
    elif sub == 0x04 and r.remaining() >= 8:      # S2C close generic gump (C2S sub 4 = 3-byte cast spell)
        d["gump_id"], d["button"] = r.u32(), r.u32()   # ClassicUO PacketHandlers.cs:4154-4156
    elif sub == 0x0C and r.remaining() >= 4:      # C2S close status: `bf 0009 000c <serial>`
        d["serial"] = r.u32()                     # (GameActions.SendCloseStatus)
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
    0x78: _p_mobile_equip,
    0x1C: _p_talk,
    0xAE: _p_unicode_talk,
    0xA9: _p_character_list,
    0x98: _p_update_name,
    0x7C: _p_open_menu,
    0xC1: _p_cliloc,
    0xCC: _p_cliloc,
    0x74: _p_buy_list,
    0xBF: _p_extended,
}

_PROC_C2S = {
    0xAD: _p_speech,
    0xB1: _p_gump_response,
    0x91: _p_login,
    0x3B: _p_buy_request,
    0x12: _p_text_command,
    0xBF: _p_extended,
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

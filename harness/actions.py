"""Phase 3 — typed action API: builders for PLAINTEXT client->server packets.

Every builder returns the exact plaintext bytes a stock Outlands ClassicUO
client would emit (pre-XOR; the proxy applies the session key at injection
time). Only wire-proven layouts are implemented:

  walk          0x02  7B   id, dir(|0x80 if run), seq, fastwalk key u32be
                          ground truth: logs/session_20260928_141253.c2s.raw
                          (e.g. `02 86 3f 00000000` = run dir 6, seq 0x3f);
                          layout per upstream ClassicUO-main
                          src/ClassicUO.Client/Network/OutgoingPackets.cs
                          (dir u8, seq u8, fastwalk key u32be — 0 when the
                          fastwalk prevention toggle is off, as in captures)
  dclick        0x06  5B   id, serial u32be (capture: `06 40005913`)
  say_unicode   0xAD  var  id, len u16be, type u8=0, hue u16be=0x02B2,
                          font u16be=0x0003, lang "ENU\\0", utf16be text,
                          0x0000 terminator
                          ground truth: logs/session_20260928_141253.jsonl
                          `ad 0018 0002 b200 0003 454e5500 <"howdy"> 0000`
  cast_spell    0xFF  10B  dialect sub 4: ff 000a 00000004 00 <spellId u16be>
                          (docs/WORLDMODEL.md §5 C2S table; capture
                          `ff 000a 00000004 00 000f`; client-side viewer
                          Assistant.PacketHandlers.OutlandsClientPacket
                          @ 0x1400602f0: u8 flag must be 0, u16be spellId)
  item_query    0xFF  14B  dialect sub 9: ff 000e 00000009 01 0001 <serial>
                          (capture `ff 000e 00000009 01 0001 40000d54`)
  gump_response 0xB1  var  id, len u16be, serial, gump_id, button_id,
                          switchcount=0, textcount=0 (all u32be)
                          ground truth: `b1 0017 00215ad2 907fc735 00000005
                          00000000 00000000` (session_20260928_141253)
  lift          0x07  7B   id, serial u32be, amount u16be
                          (OutgoingPackets.cs Send_PickUpRequest)
  drop          0x08  15B  id, serial u32be, x u16be, y u16be, z i8,
                          grid u8, container u32be
                          (OutgoingPackets.cs Send_DropRequest — the modern
                          15-byte form with grid slot; matches the client's
                          negotiated C2S length table t[0x08]=0x0F)

Movement sequencing: WalkSequencer owns the 0x02 sequence byte (starts at 0,
+1 per walk, wraps 255->0). KNOWN LIMITATION: if the human client also walks
while the agent drives, the two sequence streams desync and the server
rejects walks (0x21 DenyWalk) until resync. Log-and-continue for now; a
future phase may rewrite seq proxy-side.
"""
import struct

RUN_FLAG = 0x80

# say_unicode defaults, taken from the captured live session
SPEECH_TYPE = 0x00
SPEECH_HUE = 0x02B2
SPEECH_FONT = 0x0003
SPEECH_LANG = b"ENU\x00"


class WalkSequencer:
    """Movement sequence-byte owner for 0x02 walk packets.

    Starts at 0, increments once per walk, wraps 255 -> 0. Share one
    instance across all agent-driven walks for a session.
    """

    def __init__(self, start: int = 0):
        if not 0 <= start <= 0xFF:
            raise ValueError(f"seq start {start} out of u8 range")
        self.seq = start

    def next(self) -> int:
        """Return the current seq and advance (wrapping at 256)."""
        s = self.seq
        self.seq = (self.seq + 1) & 0xFF
        return s

    def walk(self, direction: int, run: bool = False,
             fastwalk_key: int = 0) -> bytes:
        """Build a walk packet consuming the next sequence number."""
        return walk(direction, run=run, seq=self.next(),
                    fastwalk_key=fastwalk_key)


def _check_u8(v, name):
    if not 0 <= v <= 0xFF:
        raise ValueError(f"{name} {v} out of u8 range")


def _check_u16(v, name):
    if not 0 <= v <= 0xFFFF:
        raise ValueError(f"{name} {v} out of u16 range")


def _check_u32(v, name):
    if not 0 <= v <= 0xFFFFFFFF:
        raise ValueError(f"{name} {v} out of u32 range")


def walk(direction: int, run: bool = False, seq: int = 0,
         fastwalk_key: int = 0) -> bytes:
    """0x02 walk request: `02 <dir> <seq> <fastwalk key u32be>`.

    direction 0-7 (0=N .. 7=NW); run sets the 0x80 flag bit on the
    direction byte. fastwalk_key is 0 unless the server's fastwalk
    prevention negotiated a key (all captures show 0).
    """
    if not 0 <= direction <= 7:
        raise ValueError(f"direction {direction} out of range 0-7")
    _check_u8(seq, "seq")
    _check_u32(fastwalk_key, "fastwalk_key")
    d = direction | (RUN_FLAG if run else 0)
    return struct.pack(">BBBI", 0x02, d, seq, fastwalk_key)


def dclick(serial: int) -> bytes:
    """0x06 double click: `06 <serial u32be>`."""
    _check_u32(serial, "serial")
    return struct.pack(">BI", 0x06, serial)


def say_unicode(text: str, hue: int = SPEECH_HUE, font: int = SPEECH_FONT,
                lang: bytes = SPEECH_LANG) -> bytes:
    """0xAD unicode speech (layout per live capture, see module docstring)."""
    _check_u16(hue, "hue")
    _check_u16(font, "font")
    if len(lang) != 4:
        raise ValueError("lang must be exactly 4 bytes (e.g. b'ENU\\x00')")
    body = text.encode("utf-16-be") + b"\x00\x00"
    length = 1 + 2 + 1 + 2 + 2 + 4 + len(body)
    return (struct.pack(">BHBHH", 0xAD, length, SPEECH_TYPE, hue, font)
            + lang + body)


def cast_spell(spell_id: int) -> bytes:
    """0xFF dialect sub 4 cast spell: `ff 000a 00000004 00 <spellId u16be>`."""
    _check_u16(spell_id, "spell_id")
    return struct.pack(">BHI BH", 0xFF, 0x000A, 4, 0, spell_id)


def item_query(serial: int) -> bytes:
    """0xFF dialect sub 9 item/object detail query:
    `ff 000e 00000009 01 0001 <serial u32be>`."""
    _check_u32(serial, "serial")
    return struct.pack(">BHI", 0xFF, 0x000E, 9) + b"\x01\x00\x01" \
        + struct.pack(">I", serial)


def gump_response(serial: int, gump_id: int, button_id: int) -> bytes:
    """0xB1 gump response with no switches/text entries (23 bytes)."""
    for name, v in (("serial", serial), ("gump_id", gump_id),
                    ("button_id", button_id)):
        _check_u32(v, name)
    return struct.pack(">BHIIIII", 0xB1, 23, serial, gump_id, button_id, 0, 0)


def lift(serial: int, amount: int) -> bytes:
    """0x07 pick-up request: `07 <serial u32be> <amount u16be>`."""
    _check_u32(serial, "serial")
    _check_u16(amount, "amount")
    return struct.pack(">BIH", 0x07, serial, amount)


def drop(serial: int, x: int, y: int, z: int, grid: int = 0,
         container_serial: int = 0xFFFFFFFF) -> bytes:
    """0x08 drop request (modern 15B form): `08 <serial> <x u16be>
    <y u16be> <z i8> <grid u8> <container u32be>`.

    container_serial 0xFFFFFFFF = drop to the ground at (x, y, z).
    """
    _check_u32(serial, "serial")
    _check_u16(x, "x")
    _check_u16(y, "y")
    if not -128 <= z <= 127:
        raise ValueError(f"z {z} out of i8 range")
    _check_u8(grid, "grid")
    _check_u32(container_serial, "container_serial")
    return struct.pack(">BIHHbBI", 0x08, serial, x, y, z, grid,
                       container_serial)

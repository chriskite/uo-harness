"""Phase 3 — typed action API: builders for PLAINTEXT client->server packets.

Every builder returns the exact plaintext bytes a stock Outlands ClassicUO
client would emit (pre-XOR; the proxy applies the session key at injection
time). Layouts come from the decompiled Outlands senders
(decompiled/protocol_handlers.c, `NetClientExt.Send_*` @ address), checked
byte-for-byte against captured client packets where one exists. Fixed-length
packets are zero-padded to the client's own length table (uo/outlands_table.py,
protocol version 12), which is also the C2S framing (uo/packets.py
C2S_OVERRIDES). "V10+" = the branch the senders take when the protocol
version at settings+0x68 is >= 10 (live server: 12).

  walk          0x02  7B   id, dir(|0x80 if run), seq, fastwalk key u32be
                          ground truth: logs/session_20260928_141253.c2s.raw
                          (e.g. `02 86 3f 00000000` = run dir 6, seq 0x3f);
                          layout per upstream ClassicUO-main
                          src/ClassicUO.Client/Network/OutgoingPackets.cs
                          (dir u8, seq u8, fastwalk key u32be — on Outlands the
                          cycle token; the proxy's MoveAuthority stamps it)
  dclick        0x06  5B   id, serial u32be (capture: `06 40005913`)
  single_click  0x09  5B   id, serial u32be (capture: `09 000001e2`)
  status_request 0x34 10B  id, 0xEDEDEDED, type u8 (4 = mobile status), serial
                          (capture `34 edededed 04 000001e2`; the stock client
                          sends it right after a 0x09 on the same serial in
                          518/523 clicks across captures)
  name_request  0x98  7B   id, len u16be=7, serial (capture `98 0007 000001e2`;
                          follows a click when the name is not known yet)
  say_unicode   0xAD  var  id, len u16be, type u8=0, hue u16be=0x02B2,
                          font u16be=0x0003, lang "ENU\\0", then either
                          utf16be text + 0x0000 (no speech.mul keyword), or
                          (type|0xC0) packed keyword ids + utf8 text + 0x00
                          (uo/speech.py, ported from ClassicUO)
                          ground truth: logs/session_20260928_141253.jsonl
                          `ad 0018 0002 b200 0003 454e5500 <"howdy"> 0000`;
                          encoded: logs/session_20260929_161433 "bank" =
                          `ad 0016 c0 02b2 0003 454e5500 0020020020 <bank> 00`
  cast_spell    0xFF  10B  dialect sub 4: ff 000a 00000004 00 <spellId u16be>
                          (docs/WORLDMODEL.md §5 C2S table; capture
                          `ff 000a 00000004 00 000f`; client-side viewer
                          Assistant.PacketHandlers.OutlandsClientPacket
                          @ 0x1400602f0: u8 flag must be 0, u16be spellId)
  item_query    0xFF  14B  dialect sub 9: ff 000e 00000009 01 0001 <serial>
                          (capture `ff 000e 00000009 01 0001 40000d54`)
  gump_response 0xB1  var  id, len, serial, gump_id, button_id, n u32,
                          n*switch id u32, m u32, m*(entry id u16, chars u16,
                          utf16be text; '\\n'->'\\x1f', no NUL) (all BE)
                          (Send_GumpResponse @ 0x1401557a0) ground truth:
                          `b1 0017 00215ad2 907fc735 00000005 00000000
                          00000000`; one empty text entry: `b1 001b 00215ab3
                          6b147227 00000000 00000000 00000001 0002 0000`
                          (both session_20260928_141253)
  text_entry_response 0xAC var  id, len, serial u32, parent u8, button u8,
                          ok u8, len(text)+1 u16, ascii text, 00
                          (Send_TextEntryDialogResponse @ 0x140159b90; no
                          capture)
  lift          0x07  7B   id, serial u32be, amount u16be
                          (Send_PickUpRequest @ 0x14014bfb0; table 7, no
                          padding; no capture)
  drop          0x08  22B  id, serial u32, x u32, y u32, z i32, grid u8,
                          container u32 (Send_DropRequest @ 0x14014c700 V10+
                          branch; exactly the table length 22. The 15-byte
                          upstream form is the < V10 branch. No capture)
  target_object 0x6C  27B  id, 00, cursor_id u32, cursor_type u8, serial u32,
                          x u32, y u32, z i32, graphic u32
                          (Send_TargetObject @ 0x14015bec0 V10+) ground truth:
                          `6c 00 00052cb9 01 00094375 0000077c 00000a24
                          00000000 00000190` (session_20260928_164548) and
                          `6c 00 000521a5 02 00094375 00000793 00000a1b
                          00000001 00000190` (session_20260928_141253)
  target_xyz    0x6C  27B  id, 01, cursor_id, cursor_type, 00000000, x u32,
                          y u32, z i32, graphic u32 (0 = land)
                          (Send_TargetXYZ @ 0x14015d120 V10+; no capture)
  target_cancel 0x6C  27B  id, target_type, cursor_id, cursor_type, 00000000,
                          7fffffff x3, 00000000
                          (Send_TargetCancel @ 0x14015e400 V10+; no capture)
  buy_request   0x3B  var  id, len, vendor u32, 02, n*(1a, item u32,
                          amount u16) (Send_BuyRequest @ 0x1401744c0) ground
                          truth: `3b 000f 000001e2 02 1a 450a6eac 0001`
                          (session_20260928_164548)
  sell_request  0x9F  var  id, len, vendor u32, n u16, n*(item u32, amount
                          u16) (Send_SellRequest @ 0x1401750a0; no capture)
  use_skill     0x12  var  id, len, 24, ascii "<id> 0", 00
                          (Send_UseSkill @ 0x140153660, the only skill sender;
                          no 0xFF-dialect variant; no capture)
  open_door     0x12  5B   `12 0005 58 00` (Send_OpenDoor @ 0x140154040;
                          capture session_20260928_164548)
  equip_request 0x13  10B  id, serial u32, layer u8, container u32
                          (Send_EquipRequest @ 0x14014d570; no capture)
  war_mode      0x72  5B   id, on u8, 32, 00, pad 00
                          (Send_ChangeWarMode @ 0x14014dde0; no capture)
  attack        0x05  5B   id, serial u32 (Send_AttackRequest @ 0x1401509d0;
                          no capture)
  request_popup 0xBF  9B   `bf 0009 0013 <serial>` (Send_RequestPopupMenu
                          @ 0x14016c430) ground truth: `bf 0009 0013 0008aae0`
  popup_selection 0xBF 11B `bf 000b 0015 <serial> <index u16>`
                          (Send_PopupMenuSelection @ 0x14016cb70) ground
                          truth: `bf 000b 0015 0008aae0 0002` (both
                          session_20260928_141253; standard BF, no dialect)
  ascii_prompt_response 0x9A var  id, len, serial u32, prompt_id u32,
                          (cancel ? 0 : 1) u32, ascii text, 00
                          (Send_ASCIIPromptResponse @ 0x14015f470; no capture)
  unicode_prompt_response 0xC2 var  id, len, serial, prompt_id, flag u32,
                          lang ascii[3], 00, utf16le text (no NUL)
                          (Send_UnicodePromptResponse @ 0x14015ff50; no
                          capture)
  skills_request 0x34 10B  `34 edededed 05 <serial>` (Send_SkillsRequest
                          @ 0x14014f420; no capture)

Movement sequencing: WalkSequencer owns the 0x02 sequence byte (starts at 0,
+1 per walk, wraps 255->0). KNOWN LIMITATION: if the human client also walks
while the agent drives, the two sequence streams desync and the server
rejects walks (0x21 DenyWalk) until resync. Log-and-continue for now; a
future phase may rewrite seq proxy-side.
"""
import struct

from uo import gumps, speech

RUN_FLAG = 0x80

# say_unicode defaults, taken from the captured live session
SPEECH_TYPE = 0x00
SPEECH_ENCODED = 0xC0  # MessageType.Encoded, OR'd in when keywords match
SPEECH_HUE = 0x02B2
SPEECH_FONT = 0x0003
SPEECH_LANG = b"ENU\x00"


class WalkSequencer:
    """Movement sequence-byte owner for 0x02 walk packets.

    Starts at 0, increments once per walk, wraps 0xFF -> 1 (never 0, per the
    client movement code). Share one instance across all agent-driven walks
    for a session.
    """

    def __init__(self, start: int = 0):
        if not 0 <= start <= 0xFF:
            raise ValueError(f"seq start {start} out of u8 range")
        self.seq = start

    def next(self) -> int:
        """Return the current seq and advance (0xFF -> 1, never 0)."""
        s = self.seq
        self.seq = self.seq + 1 if self.seq < 0xFF else 1
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
    direction byte. Leave seq/fastwalk_key 0 when injecting through the
    proxy: its MoveAuthority assigns the seq and stamps the cycle token
    (docs/MOVEMENT.md).
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
                lang: bytes = SPEECH_LANG, msg_type: int = SPEECH_TYPE) -> bytes:
    """0xAD unicode speech, byte-identical to the stock client's
    Send_UnicodeSpeechRequest: if speech.mul keywords match `text`, the
    encoded form (type|0xC0, keyword ids, UTF-8 + NUL); else UTF-16BE + NUL16.
    """
    _check_u8(msg_type, "msg_type")
    _check_u16(hue, "hue")
    _check_u16(font, "font")
    if len(lang) != 4:
        raise ValueError("lang must be exactly 4 bytes (e.g. b'ENU\\x00')")
    ids = speech.get_keywords(text)
    if ids:
        msg_type |= SPEECH_ENCODED
        body = speech.encode_keywords(ids) + text.encode("utf-8") + b"\x00"
    else:
        body = text.encode("utf-16-be") + b"\x00\x00"
    length = 1 + 2 + 1 + 2 + 2 + 4 + len(body)
    return (struct.pack(">BHBHH", 0xAD, length, msg_type, hue, font)
            + lang + body)


def single_click(serial: int) -> bytes:
    """0x09 single click (LookRequest): `09 <serial u32be>`."""
    _check_u32(serial, "serial")
    return struct.pack(">BI", 0x09, serial)


def status_request(serial: int, status_type: int = 4) -> bytes:
    """0x34 status request: `34 edededed <type u8> <serial u32be>` (4 = mobile status)."""
    _check_u8(status_type, "status_type")
    _check_u32(serial, "serial")
    return struct.pack(">BIBI", 0x34, 0xEDEDEDED, status_type, serial)


def name_request(serial: int) -> bytes:
    """0x98 name request: `98 0007 <serial u32be>`."""
    _check_u32(serial, "serial")
    return struct.pack(">BHI", 0x98, 0x0007, serial)


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


def _check_z(z):
    if not -128 <= z <= 127:
        raise ValueError(f"z {z} out of i8 range")


def _var(pid: int, body: bytes) -> bytes:
    """Variable-length packet: id, u16be total length, body. Capped at 0x8000,
    the most uo.packets.packet_length (and so the proxy) accepts."""
    length = 3 + len(body)
    if length > 0x8000:
        raise ValueError(f"packet 0x{pid:02X} too long ({length} B > 0x8000)")
    return struct.pack(">BH", pid, length) + body


def _ascii(text: str, name: str) -> bytes:
    """ASCII bytes of text (the client writes these fields through an ASCII
    encoding); NUL and non-ASCII rejected rather than silently mangled."""
    try:
        raw = text.encode("ascii")
    except UnicodeEncodeError:
        raise ValueError(f"{name} must be ASCII") from None
    if b"\x00" in raw:
        raise ValueError(f"{name} must not contain NUL")
    return raw


GUMP_TEXT_MAX = 0x800  # Send_GumpResponse caps each entry at 0x800 UTF-16 units


def gump_response(serial: int, gump_id: int, button_id: int,
                  switches=(), text_entries=()) -> bytes:
    """0xB1 gump response (Send_GumpResponse @ 0x1401557a0):
    `b1 <len> <serial> <gump_id> <button_id> <n u32> n*<switch id u32>
    <m u32> m*(<entry id u16> <chars u16> <utf16be text>)` (all BE).

    text_entries: (entry_id, text) pairs. Like the client, '\\n' becomes
    '\\x1f' and the char count is UTF-16 units with no terminator. Text over
    GUMP_TEXT_MAX units is rejected (the client would truncate it)."""
    for name, v in (("serial", serial), ("gump_id", gump_id),
                    ("button_id", button_id)):
        _check_u32(v, name)
    switches = list(switches)
    text_entries = list(text_entries)
    body = bytearray(struct.pack(">IIII", serial, gump_id, button_id,
                                 len(switches)))
    for sw in switches:
        _check_u32(sw, "switch id")
        body += struct.pack(">I", sw)
    body += struct.pack(">I", len(text_entries))
    for entry_id, text in text_entries:
        _check_u16(entry_id, "text entry id")
        raw = text.replace("\n", "\x1f").encode("utf-16-be")
        chars = len(raw) // 2
        if chars > GUMP_TEXT_MAX:
            raise ValueError(f"gump text entry {entry_id} longer than "
                             f"{GUMP_TEXT_MAX} UTF-16 units")
        body += struct.pack(">HH", entry_id, chars) + raw
    return _var(0xB1, bytes(body))


def gump_reply(serial: int, gump_id: int, button_id: int, layout: str, lines=(),
               texts: dict | None = None) -> bytes:
    """0xB1 for a gump as the stock client answers it (ClassicUO
    Gump.OnButtonClick): every text entry of the layout, in layout order, with
    its current text (or `texts[entry id]`), and every switch that starts
    checked. Build every reply with this, never a bare gump_response: a reply
    missing the gump's entries is a shape the client never sends (live
    20260930_123206: "That is not a valid number.")."""
    entries, switches = gumps.reply_fields(layout, lines)
    texts = texts or {}
    return gump_response(serial, gump_id, button_id, switches=switches,
                         text_entries=[(eid, texts.get(eid, v)) for eid, v in entries])


def text_entry_response(serial: int, parent_id: int, button_id: int,
                        text: str, ok: bool = True) -> bytes:
    """0xAC text entry dialog response (Send_TextEntryDialogResponse
    @ 0x140159b90): `ac <len> <serial u32> <parent u8> <button u8> <ok u8>
    <len(text)+1 u16> <ascii text> 00`. serial/parent_id/button_id echo the
    server's 0xAB dialog; ok=False is the cancel button."""
    _check_u32(serial, "serial")
    _check_u8(parent_id, "parent_id")
    _check_u8(button_id, "button_id")
    raw = _ascii(text, "text")
    _check_u16(len(raw) + 1, "text length")
    return _var(0xAC, struct.pack(">IBBBH", serial, parent_id, button_id,
                                  1 if ok else 0, len(raw) + 1)
                + raw + b"\x00")


def lift(serial: int, amount: int) -> bytes:
    """0x07 pick-up request: `07 <serial u32be> <amount u16be>`
    (Send_PickUpRequest @ 0x14014bfb0; table length 7, no padding)."""
    _check_u32(serial, "serial")
    _check_u16(amount, "amount")
    return struct.pack(">BIH", 0x07, serial, amount)


def drop(serial: int, x: int, y: int, z: int, grid: int = 0,
         container_serial: int = 0xFFFFFFFF) -> bytes:
    """0x08 drop request, Outlands V10+ form (22 B): `08 <serial u32>
    <x u32> <y u32> <z i32> <grid u8> <container u32>` (all BE).

    Send_DropRequest @ 0x14014c700 writes u32 x/y/z when the protocol version
    (settings+0x68) is >= 10 (live = 12); the u16/u16/i8 upstream form is
    only its < 10 branch. container_serial 0xFFFFFFFF = drop to the ground
    at (x, y, z)."""
    _check_u32(serial, "serial")
    _check_u32(x, "x")
    _check_u32(y, "y")
    _check_z(z)
    _check_u8(grid, "grid")
    _check_u32(container_serial, "container_serial")
    return struct.pack(">BIIIiBI", 0x08, serial, x, y, z, grid,
                       container_serial)


def target_object(cursor_id: int, serial: int, x: int, y: int, z: int,
                  graphic: int, cursor_type: int = 0) -> bytes:
    """0x6C target response on an entity (Send_TargetObject @ 0x14015bec0,
    V10+ form, 27 B): `6c 00 <cursor_id u32> <cursor_type u8> <serial u32>
    <x u32> <y u32> <z i32> <graphic u32>`.

    cursor_id/cursor_type echo the server's 0x6C TargetCursor (bytes 2-5 and
    6: 0 neutral, 1 harmful, 2 beneficial); x/y/z/graphic are the entity's
    as the client knows them (mobile body, item graphic; items in containers
    use their container-local x/y)."""
    _check_u32(cursor_id, "cursor_id")
    _check_u8(cursor_type, "cursor_type")
    _check_u32(serial, "serial")
    _check_u32(x, "x")
    _check_u32(y, "y")
    _check_z(z)
    _check_u32(graphic, "graphic")
    return struct.pack(">BBIBIIIiI", 0x6C, 0, cursor_id, cursor_type, serial,
                       x, y, z, graphic)


def target_xyz(cursor_id: int, x: int, y: int, z: int, graphic: int = 0,
               cursor_type: int = 0) -> bytes:
    """0x6C target response on a location (Send_TargetXYZ @ 0x14015d120,
    V10+ form, 27 B): `6c 01 <cursor_id u32> <cursor_type u8> 00000000
    <x u32> <y u32> <z i32> <graphic u32>`.

    graphic = 0 for a land tile, the static's graphic for a static (upstream
    TargetManager also adds the tile height to z for surface statics)."""
    _check_u32(cursor_id, "cursor_id")
    _check_u8(cursor_type, "cursor_type")
    _check_u32(x, "x")
    _check_u32(y, "y")
    _check_z(z)
    _check_u32(graphic, "graphic")
    return struct.pack(">BBIBIIIiI", 0x6C, 1, cursor_id, cursor_type, 0,
                       x, y, z, graphic)


def target_cancel(cursor_id: int, target_type: int = 0,
                  cursor_type: int = 0) -> bytes:
    """0x6C target cancel (Send_TargetCancel @ 0x14015e400, V10+ form, 27 B):
    `6c <target_type u8> <cursor_id u32> <cursor_type u8> 00000000
    7fffffff 7fffffff 7fffffff 00000000`. target_type/cursor_id/cursor_type
    echo the server's 0x6C. The V10+ branch stores the constant 0xFFFFFF7F
    little-endian three times, i.e. wire bytes 7f ff ff ff (upstream sends
    ffffffff 00000000 in a 19-byte packet)."""
    _check_u8(target_type, "target_type")
    _check_u32(cursor_id, "cursor_id")
    _check_u8(cursor_type, "cursor_type")
    return struct.pack(">BBIBIIIII", 0x6C, target_type, cursor_id, cursor_type,
                       0, 0x7FFFFFFF, 0x7FFFFFFF, 0x7FFFFFFF, 0)


def buy_request(vendor_serial: int, items) -> bytes:
    """0x3B buy request (Send_BuyRequest @ 0x1401744c0):
    `3b <len> <vendor u32> 02 n*(1a <item serial u32> <amount u16>)`, or
    `3b 0008 <vendor> 00` for an empty list. items: (serial, amount) pairs;
    the item serials are the vendor's for-sale container contents (0x3C)."""
    _check_u32(vendor_serial, "vendor_serial")
    items = list(items)
    body = bytearray(struct.pack(">IB", vendor_serial, 0x02 if items else 0x00))
    for serial, amount in items:
        _check_u32(serial, "item serial")
        _check_u16(amount, "amount")
        body += struct.pack(">BIH", 0x1A, serial, amount)
    return _var(0x3B, bytes(body))


def sell_request(vendor_serial: int, items) -> bytes:
    """0x9F sell request (Send_SellRequest @ 0x1401750a0):
    `9f <len> <vendor u32> <n u16> n*(<item serial u32> <amount u16>)`."""
    _check_u32(vendor_serial, "vendor_serial")
    items = list(items)
    _check_u16(len(items), "item count")
    body = bytearray(struct.pack(">IH", vendor_serial, len(items)))
    for serial, amount in items:
        _check_u32(serial, "item serial")
        _check_u16(amount, "amount")
        body += struct.pack(">IH", serial, amount)
    return _var(0x9F, bytes(body))


def use_skill(skill_id: int) -> bytes:
    """0x12 type 0x24 use skill (Send_UseSkill @ 0x140153660):
    `12 <len> 24 "<skill_id> 0" 00` (ASCII)."""
    _check_u16(skill_id, "skill_id")
    return _var(0x12, b"\x24" + f"{skill_id} 0".encode("ascii") + b"\x00")


def open_door() -> bytes:
    """0x12 type 0x58 open door (Send_OpenDoor @ 0x140154040): `12 0005 58 00`."""
    return _var(0x12, b"\x58\x00")


def equip_request(serial: int, layer: int, container_serial: int) -> bytes:
    """0x13 equip request (Send_EquipRequest @ 0x14014d570, 10 B):
    `13 <serial u32> <layer u8> <container (the wearer) u32>`."""
    _check_u32(serial, "serial")
    _check_u8(layer, "layer")
    _check_u32(container_serial, "container_serial")
    return struct.pack(">BIBI", 0x13, serial, layer, container_serial)


def war_mode(on: bool) -> bytes:
    """0x72 war mode request (Send_ChangeWarMode @ 0x14014dde0, 5 B):
    `72 <on u8> 32 00 00` (the last byte is table padding)."""
    return struct.pack(">BBBBB", 0x72, 1 if on else 0, 0x32, 0, 0)


def attack(serial: int) -> bytes:
    """0x05 attack request (Send_AttackRequest @ 0x1401509d0): `05 <serial>`."""
    _check_u32(serial, "serial")
    return struct.pack(">BI", 0x05, serial)


def request_popup(serial: int) -> bytes:
    """0xBF sub 0x13 context menu request (Send_RequestPopupMenu
    @ 0x14016c430): `bf 0009 0013 <serial u32>`."""
    _check_u32(serial, "serial")
    return _var(0xBF, struct.pack(">HI", 0x13, serial))


def popup_selection(serial: int, entry_index: int) -> bytes:
    """0xBF sub 0x15 context menu selection (Send_PopupMenuSelection
    @ 0x14016cb70): `bf 000b 0015 <serial u32> <entry index u16>`; the index
    is the entry's u16 index from the server's 0xBF sub 0x14 menu."""
    _check_u32(serial, "serial")
    _check_u16(entry_index, "entry_index")
    return _var(0xBF, struct.pack(">HIH", 0x15, serial, entry_index))


def ascii_prompt_response(serial: int, prompt_id: int, text: str,
                          cancel: bool = False) -> bytes:
    """0x9A ASCII prompt response (Send_ASCIIPromptResponse @ 0x14015f470):
    `9a <len> <serial u32> <prompt_id u32> <1, or 0 if cancel, u32>
    <ascii text> 00`. serial/prompt_id are the first 8 bytes after the
    header of the server's 0x9A (the client echoes them as one u64be)."""
    _check_u32(serial, "serial")
    _check_u32(prompt_id, "prompt_id")
    return _var(0x9A, struct.pack(">III", serial, prompt_id, 0 if cancel else 1)
                + _ascii(text, "text") + b"\x00")


def unicode_prompt_response(serial: int, prompt_id: int, text: str,
                            cancel: bool = False, lang: str = "ENU") -> bytes:
    """0xC2 unicode prompt response (Send_UnicodePromptResponse
    @ 0x14015ff50): `c2 <len> <serial u32> <prompt_id u32> <1, or 0 if
    cancel, u32> <lang ascii[3]> 00 <utf16le text>` (no terminator).
    serial/prompt_id echo the server's 0xC2 like ascii_prompt_response."""
    _check_u32(serial, "serial")
    _check_u32(prompt_id, "prompt_id")
    raw_lang = _ascii(lang, "lang")
    if len(raw_lang) != 3:
        raise ValueError("lang must be exactly 3 ASCII chars (e.g. 'ENU')")
    return _var(0xC2, struct.pack(">III", serial, prompt_id, 0 if cancel else 1)
                + raw_lang + b"\x00" + text.encode("utf-16-le"))


def skills_request(serial: int) -> bytes:
    """0x34 type 5 skills request (Send_SkillsRequest @ 0x14014f420):
    `34 edededed 05 <serial u32be>`."""
    return status_request(serial, status_type=5)

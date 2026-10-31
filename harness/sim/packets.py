"""S2C packets the simulated server sends, in the Outlands V10/V12 shapes the world model reads
(harness/world/layouts.py; the same shapes test_loop_hunt.py's simulated NPD sends).

Every builder returns plaintext bytes for `WorldRuntime.feed_packet("s2c", ...)`."""
import struct


def u16(v):
    return (v & 0xFFFF).to_bytes(2, "big")


def u32(v):
    return (v & 0xFFFFFFFF).to_bytes(4, "big")


def i32(v):
    return struct.pack(">i", v)


def var(pid, body):
    return bytes([pid]) + u16(3 + len(body)) + body


def login(serial, body, x, y, z):
    """0x1B LoginConfirm (42 B): serial, body, position."""
    b = u32(serial) + bytes(4) + u32(body) + u32(x) + u32(y) + u32(z & 0xFFFFFFFF) + bytes([0x80])
    return b"\x1b" + b + bytes(42 - len(b))


def mobile(serial, body, x, y, z, noto=3, flags=0, d=0, hue=0):
    """0x20 MobileUpdate V10 (28 B), any mobile: notoriety, flags (0x40 war mode), position."""
    return (b"\x20" + u32(serial) + u32(body) + bytes([noto]) + u16(hue) + bytes([flags])
            + u32(x) + u32(y) + b"\x00\x00" + bytes([d & 7]) + i32(z))


def move(serial, x, y, z, d=0, run=True):
    """0x77 MobileMove V10 (18 B)."""
    return b"\x77" + u32(serial) + u32(x) + u32(y) + i32(z) + bytes([(d & 7) | (0x80 if run else 0)])


def confirm_walk(seq, noto=1):
    return bytes([0x22, seq & 0xFF, noto])


def deny_walk(seq, x, y, z, d):
    return b"\x21" + bytes([seq & 0xFF]) + u32(x) + u32(y) + bytes([d & 7]) + i32(z)


def name(serial, text):
    return b"\x98\x00\x25" + u32(serial) + text.encode().ljust(30, b"\x00")[:30]


def hits(serial, cur, top):
    return b"\xa1" + u32(serial) + u16(top) + u16(cur)


def mana(serial, cur, top):
    return b"\xa2" + u32(serial) + u16(top) + u16(cur)


def stam(serial, cur, top):
    return b"\xa3" + u32(serial) + u16(top) + u16(cur)

def poisoned(serial, level=0):
    """0x17 health-bar status, type 1 (poison), `level` 0 = not poisoned (live 2026-10-26 22:31:09 / 22:31:49 on
    Logan: `17000c00104a6000010001 03`, then `... 00` with the cure)."""
    return b"\x17" + u16(12) + u32(serial) + u16(1) + u16(1) + bytes([level & 0xFF])


def cursor(cid, cursor_type=1, target_type=0):
    """0x6C target cursor: target_type 0 an object, 1 a location ("All Patrol": `6c 01 <id> 00 ...`, live 2026-10-18)."""
    return b"\x6c" + bytes([target_type]) + u32(cid) + bytes([cursor_type]) + bytes(20)


def equip(item, graphic, layer, owner):
    return b"\x2e" + u32(item) + u32(graphic) + u32(0) + bytes([layer]) + u32(owner) + u16(0)


def contained(serial, graphic, amount, container, x=50, y=60, hue=0):
    return (b"\x25" + u32(serial) + u32(graphic) + b"\x00" + u16(amount) + u16(x) + u16(y) + b"\x00"
            + u32(container) + u16(hue) + u32(0))


def ground_item(serial, graphic, amount, x, y, z=0, hue=0):
    return (b"\xf3\x00\x01\x00" + u32(serial) + u32(graphic) + b"\x00" + u16(amount) + b"\x00\x00"
            + u32(x) + u32(y) + i32(z) + b"\x00" + u16(hue) + u32(0) + u16(0))


def open_container(serial, gump):
    return b"\x24" + u32(serial) + u32(gump) + u16(0x7D)


def delete(serial):
    return b"\x1d" + u32(serial)


def display_death(serial, corpse):
    return b"\xaf" + u32(serial) + u32(corpse) + u32(0)


def corpse_flags(corpse, serial, text, noto=3):
    """Outlands 0xFF sub 0xDEAD: the corpse's owner, notoriety and name."""
    return var(0xFF, u32(0xDEAD) + u32(corpse) + u32(serial) + bytes([noto]) + text.encode() + b"\x00")


def system_text(text):
    """0xAE unicode speech from "System" (serial 0xFFFFFFFF), as live."""
    return var(0xAE, u32(0xFFFFFFFF) + b"\xff\xff\x00" + u16(0x3B2) + u16(3) + b"ENU\x00"
               + b"System".ljust(30, b"\x00") + text.encode("utf-16-be") + b"\x00\x00")


def cliloc(number, args=b""):
    return var(0xC1, u32(0xFFFFFFFF) + b"\xff\xff\x00" + u16(0x3B2) + u16(3) + u32(number)
               + b"System".ljust(30, b"\x00") + args + b"\x00\x00")


def says(serial, who, text, kind=0, hue=0x3B2, body=0x190):
    """0x1C ASCII speech from `serial` (overhead "-N" numbers, emotes; kind 6: a click label)."""
    return var(0x1C, u32(serial) + u16(body) + bytes([kind]) + u16(hue) + u16(3)
               + who.encode()[:30].ljust(30, b"\x00") + text.encode() + b"\x00")


def gump(serial, gump_id, layout, lines=()):
    body = u32(serial) + u32(gump_id) + u32(50) + u32(50) + u16(len(layout)) + layout.encode()
    body += u16(len(lines)) + b"".join(u16(len(t.encode("utf-16-be"))) + t.encode("utf-16-be") for t in lines)
    return var(0xB0, body)


def close_gump(gump_id, button=0):
    """0xBF sub 0x04: the server closes a gump of this id (live before each new 'Oxygen' gump: `gump_close` by server)."""
    return var(0xBF, u16(0x04) + u32(gump_id) + u32(button))


def pet_dead(serial, dead=True):
    """0xBF sub 0x19 bonded status: a bonded pet's ghost (live `bf000b 0019 00 <serial> 01`)."""
    return var(0xBF, u16(0x19) + b"\x00" + u32(serial) + (b"\x01" if dead else b"\x00"))


def animation(serial, action, frames=5, repeat=1, backward=False, repeat_flag=False, delay=0):
    """0x6E CharacterAnimation (14 B): a mobile's action (a monster's cast, layouts 0x6E)."""
    return (b"\x6e" + u32(serial) + u16(action) + u16(frames) + u16(repeat)
            + bytes([int(backward), int(repeat_flag), delay & 0xFF]))


def effect(kind, source, target, graphic, x, y, z, tx, ty, tz, speed=10, duration=0, fixed_dir=0, explode=0, hue=0,
           render=0):
    """0xC0 HuedEffect, Outlands V10 form (52 B, layouts 0xC0): kind 0 a moving effect from source to target."""
    return (b"\xc0" + bytes([kind]) + u32(source) + u32(target) + u32(graphic) + u32(x) + u32(y) + i32(z)
            + u32(tx) + u32(ty) + i32(tz) + bytes([speed & 0xFF, duration & 0xFF]) + bytes(2)
            + bytes([fixed_dir & 0xFF, explode & 0xFF]) + u32(hue) + u32(render))


def penalty_buff(self_serial, steps):
    """Outlands 0xFF sub 8 "Stationary Penalty" (capture 20261003_123614 byte for byte)."""
    return var(0xFF, u32(8) + u32(self_serial) + u16(277) + u16(4620) + u16(1) + u16(0) + u16(0) + u16(1)
               + struct.pack(">f", steps) + bytes(8) + bytes(8) + b"Stationary Penalty\x00"
               + b"All damage is reduced to 1. Move {value} more steps to remove this effect\x00"
               + u16(0) + u16(1) + bytes(4))


def penalty_remove(self_serial):
    return var(0xFF, u32(9) + u32(self_serial) + u16(277))


def buff(self_serial, icon, gump, title, text, seconds=0.0, end_ms=0):
    """Outlands 0xFF sub 8, any buff on us (penalty_buff's layout). No timer end unless `end_ms` (the server's ms clock
    a `time_sync` gave): without one the runner reads the buff's presence and our stamina, as it does live before the
    first sync; with one, the end (state.buff_ends_t, Mover.held's HOLD_ICONS)."""
    return var(0xFF, u32(8) + u32(self_serial) + u16(icon) + u16(gump) + u16(1) + u16(0) + u16(0) + u16(1)
               + struct.pack(">f", seconds) + struct.pack(">Q", end_ms) + bytes(8) + title.encode() + b"\x00"
               + text.encode() + b"\x00" + u16(0) + u16(1) + bytes(4))


def time_sync(ms):
    """Outlands 0xFF sub 3 TimeSync: the server's millisecond clock now (buff timer ends are on it)."""
    return var(0xFF, u32(3) + struct.pack(">Q", ms))


def buff_remove(self_serial, icon):
    return var(0xFF, u32(9) + u32(self_serial) + u16(icon))


def warmode(on):
    return bytes([0x72, 1 if on else 0, 0, 0x32, 0])


def seed(token):
    return bytes.fromhex("bf001d0001") + u32(token) + bytes(20)

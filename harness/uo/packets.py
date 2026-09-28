"""UO packet framing.

Packet length table ported from upstream ClassicUO
(src/ClassicUO.Client/Network/PacketsTable.cs) with the latest-version
(CV_7010400+) adjustments baked in, matching what current clients negotiate.

Entry semantics:
  > 0  fixed length in bytes (including the ID byte)
  -1   variable length: uint16 big-endian at bytes 1..2, includes header

frame_take() consumes complete packets from the front of a bytearray.
Unknown-length handling: the table covers all 256 IDs; if framing ever
desyncs (garbage length), callers should log and resync by scanning for a
plausible next ID -- for the proxy we log-and-forward regardless.
"""

from ._generated_tables import PACKET_LENGTHS_BASE as _BASE


def build_table() -> list[int]:
    """Base table with latest client-version adjustments applied.

    Applied (from PacketsTable ctor): CV_500A, CV_5090, CV_6013, CV_6017,
    CV_60142, CV_7000, CV_7090, CV_70180, CV_706400, CV_7010400 branches.
    """
    t = list(_BASE)
    t[0x0B] = 0x07
    t[0x16] = -1
    t[0x31] = -1
    t[0xE1] = -1
    t[0xE3] = -1
    t[0xE6] = 0x05
    t[0xE7] = 0x0C
    t[0xE8] = 0x0D
    t[0xE9] = 0x4B
    t[0xEA] = 0x03
    t[0x08] = 0x0F
    t[0x25] = 0x15
    t[0xEE] = 0x0A
    t[0xEF] = 0x15
    t[0xF1] = 0x09
    t[0xB9] = 0x05
    t[0x24] = 0x09
    t[0x99] = 0x1E
    t[0xBA] = 0x0A
    t[0xF3] = 0x1A
    t[0xF2] = 0x19
    t[0x00] = 0x6A
    t[0xFA] = 0x01
    t[0xFB] = 0x02
    t[0xD5] = 0x09
    t[0xFD] = 0x02
    return t


TABLE = build_table()


def packet_length(buf: bytes | bytearray, table: list[int] = TABLE) -> int:
    """Total length of the packet at buf[0], or 0 if more bytes needed.

    Returns -1 if the declared length is implausible (> 0x8000) — desync signal.
    """
    if not buf:
        return 0
    fixed = table[buf[0]]
    if fixed > 0:
        return fixed if len(buf) >= fixed else 0
    # variable length
    if len(buf) < 3:
        return 0
    varlen = (buf[1] << 8) | buf[2]
    if varlen < 3 or varlen > 0x8000:
        return -1
    return varlen if len(buf) >= varlen else 0


def frame_take(buf: bytearray, table: list[int] = TABLE) -> list[bytes]:
    """Pop all complete packets from the front of buf."""
    out = []
    while buf:
        plen = packet_length(buf, table)
        if plen <= 0:
            break
        out.append(bytes(buf[:plen]))
        del buf[:plen]
    return out

"""Authoritative Outlands S2C packet length table.

Base tier (145 entries) extracted from the client's own static table initializer
(FUN_1401a13c0, decompiled — Add(dict, id, len) calls; later duplicates win).
Everything not listed here is variable-length (uint16 BE at bytes 1-2).

Verified 2026-09-29 against the correctly decoded captures (uo/s2c.py: XOR
s2c_key + per-packet Huffman, one flush segment == one packet): all 53,277
S2C packets across the 18 logs/session_*.s2c.raw captures have exactly the
length this table assigns.

There are no wire-derived extras any more. The former EXTRA entries
(0x00=106, 0x6F=76, 0x9E=65, 0xDC=15) came from framing the old garbage
decode (Huffman started at byte 19 without the XOR layer); none of those ids
occurs in any correctly decoded capture, so they were removed.

Source of truth for updates: docs/PROTOCOL.md + decompiled/xref_add_fn.c.
"""
import json
import os

_base_path = os.path.join(os.path.dirname(__file__), "..", "..", "outlands_packet_table.json")
with open(_base_path, encoding="utf-8") as _f:
    _raw = json.load(_f)["base"]

OUTLANDS_BASE: dict[int, int] = {int(k, 16): v for k, v in _raw.items()}


def outlands_length(buf, off=0):
    """Packet length at buf[off] under the authoritative table, or 0/-1 as packet_length()."""
    if off >= len(buf):
        return 0
    fixed = OUTLANDS_BASE.get(buf[off])
    if fixed is not None and fixed > 0:
        return fixed if off + fixed <= len(buf) else 0
    if off + 3 > len(buf):
        return 0
    varlen = (buf[off + 1] << 8) | buf[off + 2]
    if varlen < 3 or varlen > 0x8000 or off + varlen > len(buf):
        return -1
    return varlen

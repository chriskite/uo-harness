"""Authoritative Outlands packet length table.

Base tier (145 entries) extracted from the client's own static table initializer
(FUN_1401a13c0, decompiled — Add(dict, id, len) calls; later duplicates win).
EXTRA entries resolved from wire captures (repeat-spacing / clean-framing evidence).
Everything not listed here is variable-length (uint16 BE at bytes 1-2).

Source of truth for updates: docs/PROTOCOL.md + decompiled/xref_add_fn.c.
"""
import json
import os

_base_path = os.path.join(os.path.dirname(__file__), "..", "..", "outlands_packet_table.json")
with open(_base_path, encoding="utf-8") as _f:
    _raw = json.load(_f)["base"]

OUTLANDS_BASE: dict[int, int] = {int(k, 16): v for k, v in _raw.items()}

# Wire-proven fixed lengths absent from the client-side dict (tier-dict packets).
EXTRA: dict[int, int] = {
    0x00: 106,  # recurring world-data record family (clean-run proven on two streams)
    0x6F: 76,   # repeat-spacing evidence (270->346) + clean continuation
    0x9E: 65,   # continuation-validated (lands on 0x00-family + valid var-len packets)
}


def outlands_length(buf, off=0):
    """Packet length at buf[off] under the authoritative table, or 0/-1 as packet_length()."""
    if off >= len(buf):
        return 0
    pid = buf[off]
    fixed = EXTRA.get(pid, OUTLANDS_BASE.get(pid))
    if fixed is not None and fixed > 0:
        return fixed if off + fixed <= len(buf) else 0
    if off + 3 > len(buf):
        return 0
    varlen = (buf[off + 1] << 8) | buf[off + 2]
    if varlen < 3 or varlen > 0x8000 or off + varlen > len(buf):
        return -1
    return varlen

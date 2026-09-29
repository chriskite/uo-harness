"""Outlands server->client wire format: prelude, XOR layer, per-packet Huffman.

Wire layout (proven 2026-09-29 on all 18 session captures, docs/CIPHER.md):

  bytes 0..12  cleartext prelude packet `ff 00 0d | 7x 00 | 0c <s2c_key> <c2s_key>`
  bytes 13..   every byte XOR s2c_key (prelude byte 11), then UO static Huffman

The client applies the XOR in `NetClient.ProcessRecv @ 0x140145600` (key at
NetClient+0x71) before `DecompressBuffer`. Every server packet is compressed
separately and terminated by the flush symbol (-256) padded with zero bits to a
byte boundary, so ONE FLUSH SEGMENT == ONE PACKET: its wire bytes can be dropped
or replaced (`encode_packet`) without touching neighbouring packets.
"""
from ._generated_tables import HUFFMAN_TREE as _TREE

PRELUDE_LEN = 13
FLUSH = -256


def prelude_keys(prelude: bytes) -> tuple[int, int]:
    """(s2c_key, c2s_key) from the 13-byte cleartext prelude."""
    if len(prelude) < PRELUDE_LEN or prelude[0] != 0xFF or prelude[10] != 0x0C:
        raise ValueError(f"unexpected S2C prelude {prelude[:PRELUDE_LEN].hex()}")
    return prelude[11], prelude[12]


def _build_codes() -> dict[int, tuple[int, int]]:
    """Leaf symbol -> (code bits as int, bit length), from the decode tree
    (node n: child for bit 1 at _TREE[2n], for bit 0 at _TREE[2n+1];
    child <= 0 is a leaf holding -symbol)."""
    codes: dict[int, tuple[int, int]] = {}
    stack = [(0, 0, 0)]
    while stack:
        node, bits, n = stack.pop()
        for bit, child in ((1, _TREE[node * 2]), (0, _TREE[node * 2 + 1])):
            b, m = (bits << 1) | bit, n + 1
            if child <= 0:
                codes[FLUSH if child == FLUSH else -child] = (b, m)
            else:
                stack.append((child, b, m))
    return codes


_CODES = _build_codes()


def encode_packet(packet: bytes, key: int) -> bytes:
    """Wire bytes for one packet: Huffman codes + flush, zero-padded, XOR key."""
    acc = nbits = 0
    out = bytearray()
    for sym in (*packet, FLUSH):
        code, n = _CODES[sym]
        acc = (acc << n) | code
        nbits += n
        while nbits >= 8:
            nbits -= 8
            out.append(((acc >> nbits) & 0xFF) ^ key)
        acc &= (1 << nbits) - 1
    if nbits:
        out.append(((acc << (8 - nbits)) & 0xFF) ^ key)
    return bytes(out)


class S2CStream:
    """Streaming post-prelude decoder that yields whole packets with their wire bytes."""

    __slots__ = ("key", "_pending", "_pos", "_out", "_bitnum", "_value", "_mask", "_treepos")

    def __init__(self, key: int):
        self.key = key
        self._pending = bytearray()  # wire bytes of the current, incomplete segment
        self._pos = 0                # next unread index in _pending
        self._out = bytearray()      # decoded bytes of the current segment
        self._bitnum = 8
        self._value = 0
        self._mask = 0
        self._treepos = 0

    @property
    def buffered(self) -> int:
        """Wire bytes held back because their segment is not complete yet."""
        return len(self._pending)

    def feed(self, wire: bytes) -> list[tuple[bytes, bytes]]:
        """Consume post-prelude wire bytes; return completed (wire_segment, packet)
        pairs in order. An incomplete trailing segment is retained."""
        pend = self._pending
        pend += wire
        t = _TREE
        key = self.key
        out = self._out
        bitnum, value, mask, tp, pos = self._bitnum, self._value, self._mask, self._treepos, self._pos
        n = len(pend)
        done: list[tuple[bytes, bytes]] = []
        while True:
            if bitnum >= 8:
                if pos >= n:
                    break
                value = pend[pos] ^ key
                pos += 1
                bitnum = 0
                mask = 0x80
            tp = t[tp * 2] if value & mask else t[tp * 2 + 1]
            mask >>= 1
            bitnum += 1
            if tp <= 0:
                if tp == FLUSH:
                    done.append((bytes(pend[:pos]), bytes(out)))
                    del pend[:pos]
                    n -= pos
                    pos = 0
                    out.clear()
                    bitnum = 8
                else:
                    out.append(-tp)
                tp = 0
        self._bitnum, self._value, self._mask, self._treepos, self._pos = bitnum, value, mask, tp, pos
        return done

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


_XOR = [bytes(b ^ k for b in range(256)) for k in range(256)]   # key -> bytes.translate table
_STEP: list = [None] * (256 * 256)   # (tree node << 8) | byte -> _step result, filled on first use


def _step(node: int, byte: int) -> tuple[bytes, bool, int]:
    """Walk one de-XORed wire byte down the decode tree from `node`, MSB first:
    (symbols completed, whether the flush symbol ended the segment, node after).
    The bits after a flush are zero padding and ignored; the next segment starts
    on the next byte at the root. Cached in _STEP."""
    t = _TREE
    syms = bytearray()
    at = node
    mask = 0x80
    while mask:
        at = t[at * 2] if byte & mask else t[at * 2 + 1]
        mask >>= 1
        if at <= 0:
            if at == FLUSH:
                res = (bytes(syms), True, 0)
                break
            syms.append(-at)
            at = 0
    else:
        res = (bytes(syms), False, at)
    _STEP[(node << 8) | byte] = res
    return res


class S2CStream:
    """Streaming post-prelude decoder that yields whole packets with their wire bytes."""

    __slots__ = ("key", "_pending", "_pos", "_out", "_treepos")

    def __init__(self, key: int):
        self.key = key
        self._pending = bytearray()  # wire bytes of the current, incomplete segment
        self._pos = 0                # next unread index in _pending
        self._out = bytearray()      # decoded bytes of the current segment
        self._treepos = 0            # decode tree node between bytes (0: root)

    @property
    def buffered(self) -> int:
        """Wire bytes held back because their segment is not complete yet."""
        return len(self._pending)

    def feed(self, wire: bytes) -> list[tuple[bytes, bytes]]:
        """Consume post-prelude wire bytes; return completed (wire_segment, packet)
        pairs in order. An incomplete trailing segment is retained. Decodes a byte
        at a time through _STEP (a segment always ends on a byte boundary)."""
        pend = self._pending
        pend += wire
        step = _STEP
        out = self._out
        tp, pos = self._treepos, self._pos
        start = 0                    # first wire byte of the current segment
        done: list[tuple[bytes, bytes]] = []
        for b in pend[pos:].translate(_XOR[self.key]):
            pos += 1
            syms, flushed, tp = step[(tp << 8) | b] or _step(tp, b)
            if syms:
                out += syms
            if flushed:
                done.append((bytes(pend[start:pos]), bytes(out)))
                out.clear()
                start = pos
        if start:
            del pend[:start]
            pos -= start
        self._treepos, self._pos = tp, pos
        return done

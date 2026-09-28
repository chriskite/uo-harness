"""UO server->client Huffman decompression.

Ported from upstream ClassicUO (src/ClassicUO.Client/Network/Huffman.cs).
Static tree; stream is MSB-first; leaf values are stored as -(byte).
Symbol -256 = flush marker: discard rest of current byte, resume at next byte boundary.
Decoder is stateful: feed compressed chunks incrementally, get decompressed bytes back.
"""
from ._generated_tables import HUFFMAN_TREE as _TREE


class HuffmanDecoder:
    """Streaming decompressor for the server->client direction."""

    __slots__ = ("_bitnum", "_value", "_mask", "_treepos")

    def __init__(self):
        self.reset()

    def reset(self):
        self._bitnum = 8
        self._value = 0
        self._mask = 0
        self._treepos = 0

    def decompress(self, src: bytes) -> bytes:
        out = bytearray()
        i = 0
        n = len(src)
        t = _TREE
        while True:
            if self._bitnum >= 8:
                if i >= n:
                    break
                self._value = src[i]
                i += 1
                self._bitnum = 0
                self._mask = 0x80
            if self._value & self._mask:
                self._treepos = t[self._treepos * 2]
            else:
                self._treepos = t[self._treepos * 2 + 1]
            self._mask >>= 1
            self._bitnum += 1
            if self._treepos <= 0:
                if self._treepos == -256:
                    # flush marker: skip to next byte boundary
                    self._bitnum = 8
                    self._treepos = 0
                    continue
                out.append(-self._treepos)
                self._treepos = 0
        return bytes(out)

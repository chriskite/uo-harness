"""Test MessagePack hypothesis on the world-state stream."""
import struct, sys

class MP:
    def __init__(self, buf, pos=0, end=None):
        self.b = buf; self.p = pos; self.end = len(buf) if end is None else end
    def read(self, n):
        if self.p + n > self.end: raise EOFError
        v = self.b[self.p:self.p+n]; self.p += n; return v
    def u8(self): return self.read(1)[0]
    def decode(self, depth=0):
        if depth > 64: raise ValueError("depth")
        c = self.u8()
        if c <= 0x7f: return c
        if c >= 0xe0: return c - 0x100
        if 0x80 <= c <= 0x8f:  # fixmap
            n = c & 0xf
            return {"__map__": [(self.decode(depth+1), self.decode(depth+1)) for _ in range(n)]}
        if 0x90 <= c <= 0x9f:  # fixarray
            return [self.decode(depth+1) for _ in range(c & 0xf)]
        if 0xa0 <= c <= 0xbf:  # fixstr
            return self.read(c & 0x1f)
        if c == 0xc0: return None
        if c == 0xc2: return False
        if c == 0xc3: return True
        if c == 0xc4: return self.read(self.u8())
        if c == 0xc5: return self.read(struct.unpack(">H", self.read(2))[0])
        if c == 0xc6: return self.read(struct.unpack(">I", self.read(4))[0])
        if c == 0xca: return struct.unpack(">f", self.read(4))[0]
        if c == 0xcb: return struct.unpack(">d", self.read(8))[0]
        if c == 0xcc: return self.u8()
        if c == 0xcd: return struct.unpack(">H", self.read(2))[0]
        if c == 0xce: return struct.unpack(">I", self.read(4))[0]
        if c == 0xcf: return struct.unpack(">Q", self.read(8))[0]
        if c == 0xd0: return struct.unpack("b", self.read(1))[0]
        if c == 0xd1: return struct.unpack(">h", self.read(2))[0]
        if c == 0xd2: return struct.unpack(">i", self.read(4))[0]
        if c == 0xd3: return struct.unpack(">q", self.read(8))[0]
        if c == 0xd4: t = self.u8(); return ("ext1", t, self.read(1))
        if c == 0xd5: t = self.u8(); return ("ext2", t, self.read(2))
        if c == 0xd6: t = self.u8(); return ("ext4", t, self.read(4))
        if c == 0xd7: t = self.u8(); return ("ext8", t, self.read(8))
        if c == 0xd8: t = self.u8(); return ("ext16", t, self.read(16))
        if c == 0xd9: return self.read(self.u8())
        if c == 0xda: return self.read(struct.unpack(">H", self.read(2))[0])
        if c == 0xdb: return self.read(struct.unpack(">I", self.read(4))[0])
        if c == 0xdc: return [self.decode(depth+1) for _ in range(struct.unpack(">H", self.read(2))[0])]
        if c == 0xdd:
            n = struct.unpack(">I", self.read(4))[0]
            return [self.decode(depth+1) for _ in range(n)]
        if c == 0xde:
            n = struct.unpack(">H", self.read(2))[0]
            return {"__map__": [(self.decode(depth+1), self.decode(depth+1)) for _ in range(n)]}
        if c == 0xdf:
            n = struct.unpack(">I", self.read(4))[0]
            return {"__map__": [(self.decode(depth+1), self.decode(depth+1)) for _ in range(n)]}
        if c == 0xc7:
            n = self.u8(); t = self.u8(); return ("ext8", t, self.read(n))
        if c == 0xc8:
            n = struct.unpack(">H", self.read(2))[0]; t = self.u8(); return ("ext16", t, self.read(n))
        if c == 0xc9:
            n = struct.unpack(">I", self.read(4))[0]; t = self.u8(); return ("ext32", t, self.read(n))
        raise ValueError(f"bad marker 0x{c:02x} at {self.p-1}")

if __name__ == "__main__":
    for sess in ("164548", "141253"):
        stream = open(rf"C:/Users/chris/uo-harness/ws_stream_{sess}.bin", "rb").read()
        print(f"===== {sess} ({len(stream)}B)")
        mp = MP(stream)
        n = 0
        try:
            while mp.p < len(stream) and n < 40:
                start = mp.p
                obj = mp.decode()
                consumed = mp.p - start
                s = repr(obj)
                print(f"  @{start:6} len={consumed:4} {s[:150]}")
                n += 1
        except (EOFError, ValueError) as e:
            print(f"  STOP at {mp.p}: {e}")

"""Walk the frozen NativeAOT statics for PacketHandlers.Handler and dump the delegate array."""
import struct, json, sys
import os
ROOT = os.path.dirname(os.path.abspath(__file__))

EXE = os.path.join(ROOT, "ClassicUO.exe")
data = open(EXE, "rb").read()

# --- minimal PE parsing ---
peoff = struct.unpack_from("<I", data, 0x3C)[0]
assert data[peoff:peoff+4] == b"PE\0\0"
nsec = struct.unpack_from("<H", data, peoff+6)[0]
optsize = struct.unpack_from("<H", data, peoff+20)[0]
opt = peoff+24
image_base = struct.unpack_from("<Q", data, opt+24)[0]
sections = []
soff = opt + optsize
for i in range(nsec):
    s = data[soff+i*40: soff+(i+1)*40]
    name = s[:8].rstrip(b"\0").decode()
    vsize, vaddr, rawsize, rawoff = struct.unpack_from("<IIII", s, 8)
    sections.append((name, vaddr, vsize, rawoff, rawsize))
    print(f"section {name:8s} va=0x{vaddr:08x} vsize=0x{vsize:08x} raw=0x{rawoff:08x}")

def read_va(va, n):
    rva = va - image_base
    for name, vaddr, vsize, rawoff, rawsize in sections:
        if vaddr <= rva < vaddr + max(vsize, rawsize):
            off = rawoff + (rva - vaddr)
            return data[off:off+n]
    raise ValueError(f"VA 0x{va:x} not mapped")

def u64(va): return struct.unpack("<Q", read_va(va, 8))[0]
def u32(va): return struct.unpack("<I", read_va(va, 4))[0]

# --- method map for naming ---
mmap = json.load(open(os.path.join(ROOT, "mrt_map.json")))
byrva = {int(k): v for k, v in mmap["byRva"].items()}
def name_of(va):
    rva = va - image_base
    e = byrva.get(rva)
    if e: return f"{e['type']}::{e['name']}"
    return ""

STATICS_HOLDER = 0x143f8bf98
singleton = u64(STATICS_HOLDER + 0x18)
print(f"\nHandler singleton @ 0x{singleton:x}  EEType=0x{u64(singleton):x} {name_of(u64(singleton))}")
arr = u64(singleton + 8)
print(f"handlers array @ 0x{arr:x}  EEType=0x{u64(arr):x}  len={u64(arr+8)}")

out = []
for i in range(256):
    dlg = u64(arr + 0x10 + i*8)
    if dlg == 0:
        continue
    # delegate layout: +8 first param, +0x10 func ptr?, +0x18?, +0x20 code ptr (per AnalyzePacket)
    ee = u64(dlg)
    p8 = u64(dlg+8)
    p10 = u64(dlg+0x10)
    p18 = u64(dlg+0x18)
    p20 = u64(dlg+0x20)
    nm = name_of(p20) or name_of(p10) or name_of(p18) or name_of(p8)
    out.append((i, dlg, p8, p10, p18, p20, nm))
    print(f"[0x{i:02x}] dlg=0x{dlg:x} ee=0x{ee:x} +8=0x{p8:x} +10=0x{p10:x} {name_of(p10)} +18=0x{p18:x} +20=0x{p20:x} {name_of(p20)}")

json.dump(out, open(os.path.join(ROOT, "handler_array_dump.json"), "w"), indent=1)
print(f"\n{len(out)} non-null handlers")

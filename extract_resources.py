"""Extract NativeAOT embedded resources (RTR sections 324 ResourceIndex / 325 ResourceData)."""
import struct, sys, importlib.util

spec = importlib.util.spec_from_file_location("mrt_parse", r"C:/Users/chris/uo-harness/mrt_parse.py")
mp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mp)

img = mp.PEImage(r"C:/Users/chris/uo-harness/ClassicUO.exe")
hdrs = mp.find_rtr_headers(img)
print(f"{len(hdrs)} RTR headers")
h = hdrs[0]
secs = {sid: (size, rva) for sid, size, rva in h["sections"]}
for sid in sorted(secs):
    size, rva = secs[sid]
    if sid in (324, 325):
        print(f"section {sid}: rva=0x{rva:x} size=0x{size:x}")

def read_rva(rva, n):
    off = img.rva_to_off(rva)
    return img.data[off:off+n]

if 324 in secs and 325 in secs:
    isize, irva = secs[324]
    dsize, drva = secs[325]
    idx = read_rva(irva, isize)
    blob = read_rva(drva, dsize)
    print("index bytes:", idx[:64].hex())
    # ResourceIndex: NativeFormat hashtable of name -> (dataOffset, dataLength)
    # dump raw for manual inspection
    open(r"C:/Users/chris/uo-harness/resource_index.bin", "wb").write(idx)
    open(r"C:/Users/chris/uo-harness/resource_data.bin", "wb").write(blob)
    # scan data for printable names
    import re
    for m in re.finditer(rb"[ -~]{6,}", blob):
        s = m.group().decode()
        print(f"  data@{m.start():8x} {s[:100]}")

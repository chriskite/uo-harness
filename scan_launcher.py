import re, sys

def extract_strings(path, minlen=5):
    data = open(path, "rb").read()
    n = len(data)
    res = []
    i = 0
    while i < n:
        if 32 <= data[i] < 127:
            j = i
            while j < n and 32 <= data[j] < 127:
                j += 1
            if j - i >= minlen:
                res.append((i, data[i:j].decode("ascii")))
            i = j
        else:
            i += 1
    return res

# --- launcher analysis ---
LAUNCHER = r"C:/Program Files (x86)/Ultima Online Outlands/Outlands.exe"
import struct
d = open(LAUNCHER, "rb").read()
pe = struct.unpack_from("<I", d, 0x3C)[0]
magic = struct.unpack_from("<H", d, pe+24)[0]
dd = pe + 24 + (96 if magic == 0x10B else 112)
com_size = struct.unpack_from("<I", d, dd + 14*8 + 4)[0]
bsjb = d.find(b"BSJB")
print(f"launcher: size={len(d)} CLRhdr={'yes' if com_size else 'no'} BSJB={'@'+hex(bsjb) if bsjb>=0 else 'no'}")
ss = extract_strings(LAUNCHER)
with open(r"C:/Users/chris/uo-harness/launcher_strings.txt", "w") as f:
    for off, s in ss:
        f.write(f"{off:08x} A {s}\n")
kw = re.compile(r"uooutlands\.com|/api/|https?://|anticheat|anti-cheat|cheat|speedhack|inject|hash|md5|sha|integrity|verify|detect|heartbeat|telemetry|screenshot|process|module", re.I)
seen = set()
with open(r"C:/Users/chris/uo-harness/launcher_hits.txt", "w") as f:
    for off, s in ss:
        if kw.search(s) and s not in seen and len(s) < 250:
            seen.add(s)
            f.write(f"{off:08x}  {s}\n")
print(f"launcher strings={len(ss)} hits={len(seen)}")

# --- Outlands API model surface from client strings ---
lines = open(r"C:/Users/chris/uo-harness/strings.txt", encoding="utf-8", errors="replace").read().splitlines()
models = set()
for ln in lines:
    parts = ln.split(" ", 2)
    if len(parts) < 3:
        continue
    s = parts[2]
    for m in re.finditer(r"[A-Za-z][A-Za-z0-9_]+", s):
        t = m.group(0)
        if t.endswith(("Model", "JsonContext", "Response", "Request")) and not t.startswith(("System", "get_", "set_", "Create_", "m_")):
            models.add(t)
out = sorted(models)
open(r"C:/Users/chris/uo-harness/api_surface.txt", "w").write("\n".join(out))
print(f"api model-ish names: {len(out)}")

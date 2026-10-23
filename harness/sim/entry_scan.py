"""Where players appear and disappear on Maus 3 in the live captures: the evidence for the simulator's ways onto the
level (docs/SIM.md "Red players", docs/NOTES.md "Red players met live": Maus 3's second way in at (5243, 612)).

python harness/sim/entry_scan.py <tag> ...  -> logs/entry_scan/<tag>.json
While self is on Maus 3 (map 0, x 5150-5299, y 600-760): every mobile (not self, not Logan's pets) that becomes present in
the client model with a tile (it was not present before the packet) is an appearance, with the time since it was last
present, our tile now and 1.5 s ago, and its next 15 s of tiles. Every S2C 0x1D of a present mobile is a disappearance
(last tile, our tile, a 0xAF for it in the 0.2 s before). Per-mobile speech/animations/attack lines for the reveal check;
ground items (0x1A/0xF3, no container) inside the Maus 3 box.
"""
import collections, json, os, sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "harness"))
from replay import timed_packets                      # noqa: E402
from world.runtime import WorldRuntime, S2C           # noqa: E402
from world import parsers                             # noqa: E402

OUT = os.path.join(REPO, "logs", "entry_scan")
PETS = {0x000DDEFE, 0x0026EF56}
BOX = (5150, 600, 5299, 760)
ITEM_BOX = (5165, 605, 5295, 745)


def on_l3(s):
    return s.map in (0, None) and BOX[0] <= s.x <= BOX[2] and BOX[1] <= s.y <= BOX[3]


def run(tag):
    pk = timed_packets(os.path.join(REPO, "logs", f"session_{tag}"))
    now = [None]
    rt = WorldRuntime(clock=lambda: now[0])
    st = rt.state
    info = {}                  # serial -> {graphic, flags, noto, name}
    last_present = {}
    apps, gones, open_paths = [], [], []
    selfpos = collections.deque(maxlen=200)
    l3_since = None
    deaths = collections.deque(maxlen=50)
    acts = collections.defaultdict(list)   # serial -> [(t, kind, detail)]
    items = {}
    l3_time = 0.0
    prev_t = None
    for t, d, src, p in pk:
        now[0] = t
        pid = p[0]
        me = st.self
        if prev_t is not None and me.serial is not None and on_l3(me):
            l3_time += min(t - prev_t, 300.0)
        prev_t = t
        s = None
        present_before = False
        if d == S2C and pid in (0x20, 0x77, 0x78, 0x1D) and len(p) >= (7 if pid == 0x78 else 5):
            s = int.from_bytes(p[3:7] if pid == 0x78 else p[1:5], "big")
            m0 = st.mobiles.get(s)
            present_before = m0 is not None and m0.x is not None
            if pid == 0x1D and present_before and me.serial is not None and on_l3(me) and s not in PETS:
                gones.append({"t": t, "serial": s, "x": m0.x, "y": m0.y, "z": m0.z, "me": [me.x, me.y],
                              "died": any(s == ds and t - dt_ <= 0.3 for dt_, ds in deaths)})
        if d == S2C and pid == 0xAF:
            deaths.append((t, int.from_bytes(p[1:5], "big")))
        if d == S2C and pid in (0x1A, 0xF3):
            try:
                f = parsers.parse_packet(S2C, p)
                if (ITEM_BOX[0] <= f["x"] <= ITEM_BOX[2] and ITEM_BOX[1] <= f["y"] <= ITEM_BOX[3]
                        and st.self.map in (0, None) and f["serial"] not in items):
                    items[f["serial"]] = [round(t, 2), f["graphic"], f["x"], f["y"], f["z"], f.get("hue"),
                                          f.get("data_type")]
            except Exception:
                pass
        rt.feed_packet(d, p)
        me = st.self
        if me.serial is None:
            continue
        selfpos.append((t, me.x, me.y))
        if on_l3(me):
            if l3_since is None:
                l3_since = t
        else:
            l3_since = None
        for e in rt.drain_events():
            k = e["ev"]
            if k == "speech_heard" and e["serial"] not in (me.serial, 0xFFFFFFFF):
                acts[e["serial"]].append((round(t, 3), "speech", e["type"], e["text"]))
            elif k == "speech_heard" and e["serial"] == me.serial and str(e["text"]).endswith(" is attacking you!"):
                acts["attack_lines"].append((round(t, 3), e["text"]))
            elif k == "animation":
                acts[e["serial"]].append((round(t, 3), "anim", e["action"]))
            elif k == "swing":
                acts[e["attacker"]].append((round(t, 3), "swing", e["defender"]))
        if s is None or s == me.serial or s in PETS:
            continue
        m = st.mobiles.get(s)
        if m is not None:
            inf = info.setdefault(s, {})
            for k in ("graphic", "flags", "notoriety", "name", "hue"):
                v = getattr(m, k)
                if v is not None:
                    inf[k] = v
            if s in st.labels:
                inf["label"] = st.labels[s]
        present = m is not None and m.x is not None
        if present and not present_before and on_l3(me):
            past = [q for q in selfpos if q[0] <= t - 1.5]
            p15 = past[-1] if past else selfpos[0]
            rec = {"t": t, "serial": s, "x": m.x, "y": m.y, "z": m.z, "flags": m.flags, "noto": m.notoriety,
                   "graphic": m.graphic, "pid": f"0x{pid:02X}", "me": [me.x, me.y], "me_1_5s": [p15[1], p15[2]],
                   "gap": (t - last_present[s]) if s in last_present else None,
                   "l3_for": t - l3_since if l3_since else None,
                   "ghost_self": me.stats.get("graphic") in (0x192, 0x193, 0x25F, 0x260, 0x2B6, 0x2B7, 0x3DB),
                   "path": [[round(t, 2), m.x, m.y, m.z]]}
            apps.append(rec)
            open_paths.append(rec)
        if present:
            last_present[s] = t
            for rec in open_paths:
                if rec["serial"] == s and t - rec["t"] <= 15 and rec["path"][-1][1:3] != [m.x, m.y]:
                    rec["path"].append([round(t, 2), m.x, m.y, m.z])
        open_paths = [r for r in open_paths if t - r["t"] <= 15]
    keep = {s for s in [a["serial"] for a in apps] + [g["serial"] for g in gones]}
    out = {"tag": tag, "self_serial": st.self.serial, "l3_s": l3_time, "apps": apps, "gones": gones,
           "info": {str(k): v for k, v in info.items() if k in keep},
           "acts": {str(k): v for k, v in acts.items() if k in keep or k == "attack_lines"},
           "items": {str(k): v for k, v in items.items()}}
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, f"{tag}.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    return len(apps), len(gones), len(items)


if __name__ == "__main__":
    for tag in sys.argv[1:]:
        try:
            print(tag, run(tag), flush=True)
        except Exception as ex:
            print(tag, "ERROR", repr(ex), flush=True)

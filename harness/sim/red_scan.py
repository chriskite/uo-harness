"""Red players in the live captures: replay each capture and extract every red-player (notoriety 6) encounter, the
evidence behind the simulator's reds (docs/SIM.md "Red players", docs/NOTES.md "Red players met live").

python harness/sim/red_scan.py <tag> [<tag> ...]   -> logs/red_scan/<tag>.json
All captures: ls logs/session_*.jsonl | sed 's/.*session_//;s/\\.jsonl//' | xargs -P 10 -n 4 python harness/sim/red_scan.py
Per capture: self serial/name, a self track (a row on every self position/hits/stamina/body change), every red
player's packets (0x20/0x77/0x78/0x2E/0xA1/0x2D/0x6E/0x0B/0x1D) with Logan's tile at the time, and, within windows
round the red presence and self deaths (-120 s/+900 s), speech/cliloc/damage/swings/effects/buffs/deaths/corpse
items, and 1 Hz snapshots of the mobiles near us or within 4 tiles of a red while one is in view.
Red player = notoriety 6 from 0x20 and (flags & 0x20 or a human body), not a ghost (threats.identify's rules).
"""
import json, os, sys, time, collections

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "harness"))
from replay import timed_packets                      # noqa: E402
from world.runtime import WorldRuntime, S2C           # noqa: E402
from world import parsers                             # noqa: E402
from uo import cliloc                                 # noqa: E402
import threats                                        # noqa: E402

OUT = os.path.join(REPO, "logs", "red_scan")
CL = cliloc.load()
SERIAL_IDS = {0x20, 0x77, 0x78, 0xA1, 0x2D, 0x6E, 0x1D, 0xAF, 0x0B, 0x11, 0x16, 0x17}
PRE_S, POST_S = 120.0, 900.0


def is_player(m):
    if m.graphic in threats.GHOST_BODIES:
        return False
    return bool((m.flags or 0) & 0x20) or m.graphic in threats.HUMAN_BODIES


def run(tag):
    base = os.path.join(REPO, "logs", f"session_{tag}")
    pk = timed_packets(base)
    now = [None]
    rt = WorldRuntime(clock=lambda: now[0])
    st = rt.state
    reds, redpk, evs, track, snaps = {}, [], [], [], []
    last_self, last_snap = None, -1.0
    corpse_of_self = set()
    for t, d, src, p in pk:
        now[0] = t
        pid = p[0]
        pre = None
        if d == S2C and pid == 0x1D and len(p) >= 5:
            s = int.from_bytes(p[1:5], "big")
            if s in reds:
                pre = ("red_delete", s)
            else:
                it = st.items.get(s)
                if it is not None and it.container is not None:
                    pre = ("item_delete", s, it.container, it.graphic, it.amount, it.layer)
        rt.feed_packet(d, p)
        me = st.self
        if d == S2C and pid in SERIAL_IDS and len(p) >= 7:
            s = int.from_bytes(p[1:5], "big") if pid != 0x78 else int.from_bytes(p[3:7], "big")
            m = st.mobiles.get(s)
            if m is not None and m.notoriety == 6 and is_player(m) and s != me.serial:
                info = reds.get(s)
                if info is None:
                    info = reds[s] = {"serial": s, "first_t": t, "names": [], "equip": {}, "guild": None,
                                      "faction": None, "labels": [], "bodies": [], "hues": []}
                if m.name and m.name not in info["names"]:
                    info["names"].append(m.name)
                lab = st.labels.get(s)
                if lab and lab not in info["labels"]:
                    info["labels"].append(lab)
                if m.graphic not in info["bodies"]:
                    info["bodies"].append(m.graphic)
                info["guild"] = m.guild or info["guild"]
                info["faction"] = m.faction or info["faction"]
                extra = None
                if pid == 0x78:
                    f = parsers.parse_packet(S2C, p)
                    extra = [[e["layer"], e["graphic"], e["hue"], e["serial"]] for e in f["equipment"]]
                    for e in f["equipment"]:
                        info["equip"][str(e["layer"])] = [e["graphic"], e["hue"], t]
                elif pid == 0x6E:
                    f = parsers.parse_packet(S2C, p)
                    extra = [f["action"], f["frames"], f["delay"]]
                elif pid == 0x0B:
                    extra = int.from_bytes(p[5:7], "big")
                redpk.append([round(t, 3), s, f"0x{pid:02X}", m.x, m.y, m.z, m.flags, m.notoriety, m.hits,
                              m.hits_max, me.x, me.y, extra])
        if d == S2C and pid == 0x2E and len(p) >= 18:
            f = parsers.parse_packet(S2C, p)
            if f["parent"] in reds:
                reds[f["parent"]]["equip"][str(f["layer"])] = [f["graphic"], f["hue"], t]
                redpk.append([round(t, 3), f["parent"], "0x2E", None, None, None, None, None, None, None,
                              me.x, me.y, [f["layer"], f["graphic"], f["hue"], f["item"]]])
            elif f["parent"] == me.serial:
                evs.append([t, "self_equip", f["layer"], f["graphic"], f["hue"], f["item"]])
        if pre is not None:
            if pre[0] == "red_delete":
                redpk.append([round(t, 3), pre[1], "0x1D", None, None, None, None, None, None, None, me.x, me.y, None])
            else:
                evs.append([t, "item_delete", *pre[1:]])
        if d == S2C and pid == 0xAF:
            f = parsers.parse_packet(S2C, p)
            evs.append([t, "display_death", f["serial"], f["corpse"]])
            if f["serial"] == me.serial:
                corpse_of_self.add(f["corpse"])
        if d == "c2s" and pid == 0xAD:          # speech we sent (src: agent or client = the user)
            try:
                f = parsers.parse_packet("c2s", p)
                evs.append([t, "say", src, f.get("text")])
            except Exception:
                pass
        for e in rt.drain_events():
            k = e["ev"]
            if k == "speech_heard":
                evs.append([t, "speech", e["serial"], e["name"], e["type"], e["hue"], e["text"]])
            elif k == "cliloc":
                try:
                    txt = cliloc.translate(CL, e["cliloc"], e["args"] or "")
                except Exception:
                    txt = None
                evs.append([t, "cliloc", e["serial"], e["name"], e["cliloc"], txt, e["affix"]])
            elif k == "damage":
                evs.append([t, "damage", e["serial"], e["amount"]])
            elif k == "swing":
                evs.append([t, "swing", e["attacker"], e["defender"]])
            elif k == "effect":
                evs.append([t, "effect", e["type"], e["source"], e["target"], e["graphic"], e["x"], e["y"],
                            e["tx"], e["ty"], e["hue"]])
            elif k == "animation":
                if e["serial"] in reds:
                    evs.append([t, "anim", e["serial"], e["action"]])
            elif k in ("death", "resurrect"):
                evs.append([t, k, e.get("x"), e.get("y")])
            elif k == "mobile_death":
                evs.append([t, "mobile_death", e["serial"], e["corpse"], e["name"], e["notoriety"]])
                if e["serial"] == me.serial:
                    corpse_of_self.add(e["corpse"])
            elif k in ("buff_update", "buff_remove"):
                if e["serial"] == me.serial:
                    evs.append([t, k, e.get("icon_id", e.get("buff_id")), e.get("title")])
            elif k == "item_seen":
                c = e.get("container")
                if c in corpse_of_self:
                    evs.append([t, "corpse_item", c, e["serial"], e.get("graphic"), e.get("amount")])
            elif k == "container_open":
                evs.append([t, "container_open", e["serial"]])
            elif k == "sound":
                evs.append([t, "sound", e["sound"], e["x"], e["y"]])
        cur = (me.x, me.y, me.z, me.map, me.hits, me.hits_max, me.stam, me.stam_max, me.stats.get("graphic"),
               me.serial)
        if cur != last_self and me.serial is not None:
            track.append([round(t, 3), *cur[:9]])
            last_self = cur
        if t - last_snap >= 1.0 and me.serial is not None:
            inview = [s for s in reds if s in st.mobiles and st.mobiles[s].x is not None]
            if inview:
                last_snap = t
                near = []
                for s, m in st.mobiles.items():
                    if m.x is None:
                        continue
                    dme = max(abs(m.x - me.x), abs(m.y - me.y))
                    dred = min(max(abs(m.x - st.mobiles[r].x), abs(m.y - st.mobiles[r].y)) for r in inview)
                    if dme <= 20 or dred <= 4:
                        near.append([s, m.name, m.graphic, m.notoriety, m.flags, m.x, m.y, m.hits, m.hits_max,
                                     m.pet, dme, dred])
                snaps.append([round(t, 3), me.x, me.y, me.hits, me.stam, near])
    wins = []
    by = collections.defaultdict(list)
    for r in redpk:
        by[r[1]].append(r[0])
    for ts_ in by.values():
        wins.append((min(ts_) - PRE_S, max(ts_) + POST_S))
    for e in evs:
        if e[1] == "death" or (e[1] == "speech" and isinstance(e[6], str) and e[6].endswith(" is attacking you!")):
            wins.append((e[0] - PRE_S, e[0] + POST_S))
    wins.sort()
    merged = []
    for a, b in wins:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])

    def inw(t):
        return any(a <= t <= b for a, b in merged)
    keep = [e for e in evs if inw(e[0]) or e[1] in ("death", "resurrect")]
    out = {"tag": tag, "self_serial": st.self.serial, "self_name": st.self.name,
           "t0": pk[0][0] if pk else None, "t1": pk[-1][0] if pk else None,
           "reds": {str(k): v for k, v in reds.items()}, "redpk": redpk, "events": keep, "windows": merged,
           "track": track, "snaps": snaps, "corpses_self": sorted(corpse_of_self)}
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, f"{tag}.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    return len(pk), len(reds), len(redpk), len(keep)


if __name__ == "__main__":
    for tag in sys.argv[1:]:
        t0 = time.time()
        try:
            print(tag, run(tag), f"{time.time() - t0:.0f}s", flush=True)
        except Exception as ex:
            print(tag, "ERROR", repr(ex), flush=True)

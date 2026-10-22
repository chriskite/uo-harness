"""Shoves and stamina in the live captures, for the simulator's walking rules (docs/SIM.md "Shoving and stamina").

    python harness/sim/shove_scan.py [--logs DIR] [--since YYYYMMDD] [--out DIR]

Each capture is replayed through `WorldRuntime` in its row order. Two outputs:
- `shoves.jsonl`: every step of ours (a walk request in the direction we face) into a tile another mobile stood on in
  the model, with our stamina then, the server's answer (0x22 / 0x21), our stamina over the next 3 s; and every shove
  cliloc (1019042 "You shove something out of the way.", 1019043 "... something invisible ...").
- `stamina.jsonl`: every change of our stamina on the Mausoleum's fight level (stamina max 25: Logan), with the steps
  and seconds since the change before and whether a shove cliloc came within 0.5 s.
The model's mobile positions lag the server's (a monster that moved off a tile we step onto reads as a shove that went
through without the cliloc), so the shove cliloc is the evidence of a real shove.
"""
import argparse
import concurrent.futures
import glob
import json
import os
import sys

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS)

DELTA = ((0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1))
SHOVE_CLILOCS = (1019040, 1019041, 1019042, 1019043)
LEVEL = (5150, 600, 5300, 760)          # the fight level (Maus 3)


def scan(job):
    import replay
    from world.runtime import WorldRuntime
    logs, tag = job
    try:
        packets = replay.timed_packets(os.path.join(logs, f"session_{tag}"))
    except Exception as e:                       # noqa: BLE001 (a capture that doesn't replay is skipped, named)
        return [{"tag": tag, "error": str(e)[:200]}], []
    now = [None]
    rt = WorldRuntime(clock=lambda: now[0])
    st = rt.state
    shoves, stamina, pending, open_recs = [], [], {}, []
    last = last_t = shove_t = None
    steps = 0
    for t, d, _src, pkt in packets:
        now[0] = t
        rt.feed_packet(d, pkt)
        s = st.self
        for e in rt.drain_events():
            if e["ev"] == "cliloc" and e.get("cliloc") in SHOVE_CLILOCS:
                shove_t = t
                shoves.append({"tag": tag, "t": round(t, 2), "shove_cliloc": e["cliloc"],
                               "stam": s.stam if s else None, "pos": [s.x, s.y] if s else None})
            elif e["ev"] == "walk_confirm" and e.get("moved"):
                steps += 1
            if e["ev"] == "walk" and s is not None and s.direction is not None and e["x"] is not None \
                    and (s.direction & 7) == (e["dir"] & 7):
                dx, dy = DELTA[e["dir"] & 7]
                to = (e["x"] + dx, e["y"] + dy)
                occ = [m for k, m in st.mobiles.items() if k != s.serial and m.x is not None and (m.x, m.y) == to]
                if occ:
                    pending[e["seq"]] = {"tag": tag, "t": round(t, 2), "to": list(to), "run": e["run"],
                                         "stam": s.stam, "stam_max": s.stam_max,
                                         "blockers": [{"serial": f"0x{m.serial:08X}", "name": m.name,
                                                       "noto": m.notoriety} for m in occ]}
            elif e["ev"] in ("walk_confirm", "walk_deny") and e.get("seq") in pending:
                r = pending.pop(e["seq"])
                r.update(answer="confirm" if e["ev"] == "walk_confirm" else "deny", after=[])
                open_recs.append(r)
        for r in list(open_recs):
            if t - r["t"] > 3.0:
                open_recs.remove(r)
                shoves.append(r)
            elif s is not None and (not r["after"] or r["after"][-1][1] != s.stam):
                r["after"].append([round(t - r["t"], 2), s.stam])
        if s is None or s.stam is None or s.x is None:
            continue
        if last is not None and s.stam != last and s.stam_max == 25 \
                and LEVEL[0] <= s.x <= LEVEL[2] and LEVEL[1] <= s.y <= LEVEL[3]:
            stamina.append({"tag": tag, "t": round(t, 2), "from": last, "to": s.stam, "dt": round(t - last_t, 2),
                            "steps": steps, "shove": shove_t is not None and t - shove_t < 0.5})
        if s.stam != last:
            last, last_t, steps = s.stam, t, 0
    return shoves + open_recs, stamina


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--logs", default=os.path.join(os.path.dirname(HARNESS), "logs"))
    ap.add_argument("--since", default="20261013", help="captures from this day on (YYYYMMDD)")
    ap.add_argument("--out", default=".", help="directory for shoves.jsonl and stamina.jsonl")
    a = ap.parse_args(argv)
    tags = sorted(os.path.basename(p)[len("session_"):-len(".s2c.raw")]
                  for p in glob.glob(os.path.join(a.logs, "session_*.s2c.raw"))
                  if os.path.basename(p)[8:16] >= a.since)
    os.makedirs(a.out, exist_ok=True)
    with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, (os.cpu_count() or 4) - 2)) as ex, \
            open(os.path.join(a.out, "shoves.jsonl"), "w", encoding="utf-8") as fs, \
            open(os.path.join(a.out, "stamina.jsonl"), "w", encoding="utf-8") as fm:
        for shoves, stamina in ex.map(scan, [(a.logs, t) for t in tags]):
            fs.writelines(json.dumps(r) + "\n" for r in shoves)
            fm.writelines(json.dumps(r) + "\n" for r in stamina)
    print(f"{len(tags)} captures -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

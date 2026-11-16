"""How soon the runner's first bard skill goes at a monster in a live hunt: from the monster's first fight with us to our
first Discordance, Peacemaking or Provocation at it (docs/NOTES.md "Shadowspire Cathedral: the bard skills' delay").

    python harness/sim/bard_lat_scan.py logs/session_<tag> logs/tasks/<task>.log [--rows]

The capture (logs/session_<tag>, without extension) is replayed through `WorldRuntime` (`replay.timed_packets`) over
the task's time span (its header line to its last line). Per monster the runner killed (the task log's "killed ..."
lines): `aggro`, the earlier of its war flag going on (0x20 / 0x78 / 0x77 / 0xD3 flags 0x40) and the first '-N' number
over it; `target`, the runner's first "target ..." line for it at or after that (1 s resolution); `bard`, our first
target answer after a bard skill's use (C2S 0x12 type 0x24, skill 15 Discordance, 9 Peacemaking, 22 Provocation: its
two answers); `dead`, its death. The simulator's counterpart is run.py's `bard_latency` (World.bard_lat).
"""
import argparse
import datetime as dt
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import replay  # noqa: E402
from world.runtime import WorldRuntime  # noqa: E402

BARD = {"15": "discord", "9": "peace", "22": "provoke"}
NUMBER = re.compile(r"^-(\d+)$")
WAR = 0x40
HEAD = re.compile(r"^== task .*\((\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d)\)$")
STAMP = re.compile(r"^(\d\d:\d\d:\d\d) ")


def _q(xs, p):
    return round(xs[min(len(xs) - 1, int(p * len(xs)))], 1) if xs else None


def task_lines(path):
    """[(epoch, text)] of a task log's timed lines, and its day."""
    day, out = None, []
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if (h := HEAD.match(line)) is not None:
            day = dt.date.fromisoformat(h.group(1))
            continue
        if day is not None and (m := STAMP.match(line)) is not None:
            t = dt.datetime.combine(day, dt.time.fromisoformat(m.group(1))).timestamp()
            if out and t < out[-1][0] - 43200:          # past midnight
                day += dt.timedelta(days=1)
                t += 86400
            out.append((t, line[9:]))
    return out


def scan(base, lines):
    """{serial: first war/number time}, {serial: (first bard answer time, skill)}, {serial: death time} over the span."""
    t0, t1 = lines[0][0] - 1, lines[-1][0] + 1
    now = [None]
    rt = WorldRuntime(clock=lambda: now[0])
    st = rt.state
    aggro, bard, dead = {}, {}, {}
    pending = None
    for t, d, src, p in replay.timed_packets(base):
        now[0] = t
        rt.feed_packet(d, p)
        evs = rt.drain_events()
        if t < t0 or t > t1:
            continue
        if d == "c2s" and p[0] == 0x12 and len(p) > 4 and p[3] == 0x24:
            sid = p[4:].split(b"\x00")[0].split(b" ")[0].decode(errors="ignore")
            pending = [BARD[sid], 2 if sid == "22" else 1] if sid in BARD else None
        for e in evs:
            if e["ev"] == "target_response" and d == "c2s" and e.get("serial") and pending:
                bard.setdefault(e["serial"], (t, pending[0]))
                pending[1] -= 1
                if pending[1] <= 0:
                    pending = None
            elif e["ev"] == "speech_heard" and NUMBER.match(e.get("text") or ""):
                aggro.setdefault(e["serial"], t)
            elif e["ev"] == "mobile_death":
                dead.setdefault(e["serial"], t)
        if d != "c2s" and p[0] in (0x20, 0x77, 0x78, 0xD3):
            s = int.from_bytes(p[3:7] if p[0] in (0x78, 0xD3) else p[1:5], "big")
            m = st.mobiles.get(s)
            if m is not None and (m.flags or 0) & WAR:
                aggro.setdefault(s, t)
    return aggro, bard, dead


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("capture", help="logs/session_<tag> (no extension)")
    ap.add_argument("task_log")
    ap.add_argument("--rows", action="store_true", help="a line per kill too")
    a = ap.parse_args(argv)
    lines = task_lines(a.task_log)
    aggro, bard, dead = scan(a.capture, lines)
    targets = {}
    rows = []
    for t, text in lines:
        if (m := re.match(r"target an? .+? 0x([0-9A-F]{8})", text)) is not None:
            targets.setdefault(int(m.group(1), 16), []).append(t)
        elif (m := re.match(r"killed an? (.+?) 0x([0-9A-F]{8})", text)) is not None:
            s = int(m.group(2), 16)
            if (g := aggro.get(s)) is None:
                continue
            tg = next((x for x in targets.get(s, []) if x >= g - 1), None)
            b, kind = bard.get(s, (None, None))
            rows.append({"kind": m.group(1), "serial": f"0x{s:08X}",
                         "at": dt.datetime.fromtimestamp(g).strftime("%H:%M:%S"),
                         "target_s": None if tg is None else round(tg - g, 1),
                         "bard_s": None if b is None else round(b - g, 1), "skill": kind,
                         "dead_s": round(dead.get(s, t) - g, 1)})
    if a.rows:
        for r in rows:
            print(json.dumps(r))
    lat = sorted(r["bard_s"] for r in rows if r["bard_s"] is not None and r["bard_s"] >= 0)
    never = sorted(r["dead_s"] for r in rows if r["bard_s"] is None)
    n = len(rows) or 1
    print(json.dumps({"fought": len(rows), "before": sum(1 for r in rows if r["bard_s"] is not None and r["bard_s"] < 0),
                      "in_fight": len(lat), "never": len(never),
                      "lat_s": {"p50": _q(lat, .5), "p75": _q(lat, .75), "p90": _q(lat, .9)},
                      "within_5s": round(sum(1 for x in lat if x <= 5) / n, 3),
                      "within_10s": round(sum(1 for x in lat if x <= 10) / n, 3),
                      "never_fight_s_p50": _q(never, .5),
                      "target_s_p50": _q(sorted(r["target_s"] for r in rows if r["target_s"] is not None), .5)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

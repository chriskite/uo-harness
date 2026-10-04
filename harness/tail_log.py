"""Human-readable live tail of the newest proxy session log.

Run in a terminal:  python tail_log.py [--all]
Follows the newest session_*.jsonl and prints packets in readable form.
--all includes keepalives (ff) which are hidden by default.
"""
import glob
import json
import os
import sys
import time

LOGDIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
SHOW_FF = "--all" in sys.argv

NAMES = {
    "0x02": "walk", "0x06": "dclick", "0x09": "query?", "0x34": "query",
    "0x98": "query?", "0x22": "RESYNC", "0xAD": "speech", "0xB1": "gump-resp",
    "0x91": "login", "0x5D": "charselect", "0xBF": "geninfo", "0xFF": "keepalive",
    "0x3B": "vendor-buy", "0x12": "textcmd",
}


def latest_log():
    files = glob.glob(os.path.join(LOGDIR, "session_*.jsonl"))
    return max(files, key=os.path.getmtime) if files else None


def fmt(e):
    t = time.strftime("%H:%M:%S", time.localtime(e["t"]))
    ms = int((e["t"] % 1) * 1000)
    ev = e.get("ev")
    if ev:
        return f"{t}.{ms:03d}  [event] {ev} {e.get('hex','') or e.get('session_key','') or e.get('note','')}"
    d = e.get("dir", "?")
    pid = e.get("id", "??")
    if pid == "0xFF" and d == "c2s" and not SHOW_FF:
        return None
    name = NAMES.get(pid, "")
    extra = ""
    hx = e.get("hex", "")
    if pid == "0x02" and len(hx) >= 14:
        direction = int(hx[2:4], 16) & 7
        extra = f" dir={direction} seq={int(hx[4:6],16)} key=0x{int(hx[6:14],16):08x}"
    src = e.get("src") or "client"
    src = "" if src == "client" else src.upper()
    return f"{t}.{ms:03d}  {d:4s} {src:5s} {pid} {name:11s} len={e.get('len','?'):<4} {extra} {hx[:32]}"


def main():
    path = None
    pos = 0
    print("tail_log: following newest session log (Ctrl+C to quit)")
    while True:
        cur = latest_log()
        if cur != path:
            path, pos = cur, 0
            print(f"--- now following {os.path.basename(path)}")
        try:
            with open(path, encoding="utf-8") as f:
                f.seek(pos)
                lines = f.readlines()
                pos = f.tell()
        except OSError:
            lines = []
        for ln in lines:
            try:
                out = fmt(json.loads(ln))
            except json.JSONDecodeError:
                continue
            if out:
                print(out, flush=True)
        time.sleep(0.25)


if __name__ == "__main__":
    main()

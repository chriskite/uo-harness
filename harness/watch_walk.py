"""Watch-and-mirror: on each client walk seen in the proxy log, inject a
mirror step (same direction, seq+1, same key) — the 'inject like the client
would' test. Prints CLIENT vs INJECT lines side by side.

Run: python watch_walk.py [--delay 0.2]
"""
import glob
import json
import os
import socket
import sys
import time

LOGDIR = r"C:/Users/chris/uo-harness/logs"
DELAY = float(sys.argv[sys.argv.index("--delay") + 1]) if "--delay" in sys.argv else 0.2


def latest_log():
    files = glob.glob(os.path.join(LOGDIR, "session_*.jsonl"))
    return max(files, key=os.path.getmtime) if files else None


def send(sock, pkt):
    sock.sendall(len(pkt).to_bytes(2, "big") + pkt)
    n = int.from_bytes(sock.recv(2), "big")
    return sock.recv(n).decode()


def main():
    sock = socket.create_connection(("127.0.0.1", 25941), timeout=10)
    print(f"watch_walk: mirroring client walks with +{DELAY}s delay (Ctrl+C to quit)")
    path, pos = None, 0
    while True:
        cur = latest_log()
        if cur != path:
            path, pos = cur, 0
            print(f"--- following {os.path.basename(path)}")
        try:
            with open(path, encoding="utf-8") as f:
                f.seek(pos)
                lines = f.readlines()
                pos = f.tell()
        except OSError:
            lines = []
        for ln in lines:
            try:
                e = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if e.get("dir") == "c2s" and e.get("id") == "0x02":
                hx = e["hex"]
                pkt = bytes.fromhex(hx)
                # mirror: same dir, seq+1 (mod 256, skip 0), same key
                new_seq = (pkt[2] + 1) & 0xFF or 1
                mirror = bytes([pkt[0], pkt[1], new_seq]) + pkt[3:]
                print(f"CLIENT {hx}", flush=True)
                time.sleep(DELAY)
                resp = send(sock, mirror)
                print(f"INJECT {mirror.hex()} -> {resp}", flush=True)
        time.sleep(0.15)


if __name__ == "__main__":
    main()

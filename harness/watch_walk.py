"""Watch-and-mirror: on each CLIENT-ORIGINATED walk seen in the proxy log,
inject a mirror step (same direction, seq+1, same key). Its own injections
are tracked and skipped so it cannot mirror itself (recursion bug fix).

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
STEPS = int(sys.argv[sys.argv.index("--steps") + 1]) if "--steps" in sys.argv else 2


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
    injected_hexes = set()  # every packet we injected — skip when they echo
    while True:
        cur = latest_log()
        if cur != path:
            path, pos = cur, os.path.getsize(cur)  # tail from end; no backlog replay
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
                if hx in injected_hexes:
                    continue  # our own injection echoing back — never mirror it
                pkt = bytes.fromhex(hx)
                print(f"CLIENT {hx}", flush=True)
                time.sleep(DELAY)
                seq = pkt[2]
                for _ in range(STEPS):
                    seq = (seq + 1) & 0xFF or 1
                    # continuation key 0: the token from the client's walk is
                    # single-use, already spent — reusing it gets rejected
                    mirror = bytes([pkt[0], pkt[1], seq]) + b"\x00\x00\x00\x00"
                    resp = send(sock, mirror)
                    injected_hexes.add(mirror.hex())
                    print(f"INJECT {mirror.hex()} -> {resp}", flush=True)
                    time.sleep(DELAY)
        time.sleep(0.15)


if __name__ == "__main__":
    main()

"""Terminal-driven walk control: arrow keys inject walks through the proxy.

Run:  python walk_cli.py
Focus a terminal, then:
  arrow keys  — walk in that direction (up=N, right=E, down=S, left=W)
  space       — toggle run/walk mode (default: run)
  8           — send a cycle-start walk with token 8 (login token)
  1           — send a cycle-start walk with token 1 (re-arm token)
  q           — quit

Seq is managed by the proxy's SeqAuthority — this tool always sends seq 0 and
the proxy assigns the true ladder position. Keys 8/1 are for opening a fresh
movement cycle (first walk after login/resync) when no client walk has opened
one yet.
"""
import msvcrt
import socket
import sys
import time

sys.path.insert(0, r"C:/Users/chris/uo-harness/harness")
from actions import walk

HOST, PORT = "127.0.0.1", 25941
DIRS = {"H": 0, "M": 2, "P": 4, "K": 6}  # scan codes: up, right, down, left
DIR_NAMES = {0: "N", 1: "NE", 2: "E", 3: "SE", 4: "S", 5: "SW", 6: "W", 7: "NW"}


def send(sock, pkt):
    sock.sendall(len(pkt).to_bytes(2, "big") + pkt)
    n = int.from_bytes(sock.recv(2), "big")
    return sock.recv(n).decode()


def main():
    try:
        sock = socket.create_connection((HOST, PORT), timeout=5)
    except OSError as e:
        print(f"cannot reach proxy control at {HOST}:{PORT} ({e})")
        sys.exit(1)
    run_mode = True
    print("walk_cli: arrows=walk, space=run/walk toggle, 8/1=token walk, q=quit")
    try:
        while True:
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):
                code = msvcrt.getwch()
                d = DIRS.get(code)
                if d is None:
                    continue
                resp = send(sock, walk(d, run=run_mode, seq=0, fastwalk_key=0))
                print(f"  walk {DIR_NAMES[d]} ({'run' if run_mode else 'walk'}) -> {resp}")
            elif ch == " ":
                run_mode = not run_mode
                print(f"  mode: {'run' if run_mode else 'walk'}")
            elif ch == "8":
                resp = send(sock, walk(0, run=run_mode, seq=0, fastwalk_key=8))
                print(f"  token-8 walk N -> {resp}")
            elif ch == "1":
                resp = send(sock, walk(0, run=run_mode, seq=0, fastwalk_key=1))
                print(f"  token-1 walk N -> {resp}")
            elif ch in ("q", "Q", "\x03"):
                break
    finally:
        sock.close()


if __name__ == "__main__":
    main()

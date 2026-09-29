"""Terminal-driven walk control: arrow keys inject walks through the proxy.

Run:  python walk_cli.py
Focus a terminal, then:
  arrow keys  — walk in that direction (up=N, right=E, down=S, left=W)
  space       — toggle run/walk mode (default: run)
  q           — quit

Seq and fastwalk key are owned by the proxy's MoveAuthority — this tool always
sends seq 0 / key 0. The proxy hides the server's confirms of these walks from
the client (so its walker never freezes), paces steps (0.2 s run / 0.4 s walk;
faster presses answer `ERR walk gated: pacing ...`). ~0.5 s after walking stops,
the proxy hands the client a fabricated 0x21 with the true position, so the
character on screen jumps there. Nothing extra goes to the server (docs/MOVEMENT.md).
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
    print("walk_cli: arrows=walk, space=run/walk toggle, q=quit")
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
            elif ch in ("q", "Q", "\x03"):
                break
    finally:
        sock.close()


if __name__ == "__main__":
    main()

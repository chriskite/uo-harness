"""Drive action injections into the live proxy for Phase 3 live validation.

Usage: python inject.py <action> [args...]
  say <text>            — unicode speech
  walk <dir> <n> [run]  — n steps in direction (0-7) at the proxy's pace
                          (gated sends — pacing/resync reply — are retried)
  cast <spellid>        — dialect sub-4 cast
  dclick <serial>       — hex or decimal serial
  query <serial>        — item detail query (sub 9)
"""
import socket
import sys
import time

sys.path.insert(0, r"C:/Users/chris/uo-harness/harness")
from actions import walk, dclick, say_unicode, cast_spell, item_query

HOST, PORT = "127.0.0.1", 25941


def send_packet(sock, pkt):
    sock.sendall(len(pkt).to_bytes(2, "big") + pkt)
    hdr = sock.recv(2)
    n = int.from_bytes(hdr, "big")
    resp = b""
    while len(resp) < n:
        resp += sock.recv(n - len(resp))
    return resp.decode()


def _serial(s: str) -> int:
    return int(s, 16) if s.lower().startswith("0x") else int(s)


def main():
    sock = socket.create_connection((HOST, PORT), timeout=10)
    cmd = sys.argv[1]
    if cmd == "say":
        print(send_packet(sock, say_unicode(sys.argv[2])))
    elif cmd == "walk":
        d = int(sys.argv[2]); n = int(sys.argv[3]); run = len(sys.argv) > 4
        for _ in range(n):
            deadline = time.monotonic() + 8.0  # > RESYNC_SPACING_S (5 s) + resync latency
            resp = send_packet(sock, walk(d, run=run))
            while resp.startswith("ERR walk gated") and time.monotonic() < deadline:
                time.sleep(0.1)
                resp = send_packet(sock, walk(d, run=run))
            print(resp)
            if resp != "OK":
                break  # no client resync arrived: the last step likely didn't execute
    elif cmd == "cast":
        print(send_packet(sock, cast_spell(int(sys.argv[2]))))
    elif cmd == "dclick":
        print(send_packet(sock, dclick(_serial(sys.argv[2]))))
    elif cmd == "query":
        print(send_packet(sock, item_query(_serial(sys.argv[2]))))
    else:
        print("unknown action", file=sys.stderr)
        sys.exit(2)
    sock.close()


if __name__ == "__main__":
    main()

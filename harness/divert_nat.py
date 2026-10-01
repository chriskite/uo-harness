"""WinDivert NAT: redirects game traffic to the harness proxy.

The client connects to a literal game-server IP from the HTTPS login response, and that IP
varies between logins (74.91.115.123 Test Shard; 35.71.142.123 and 52.223.17.219 on
2026-09-30). So the NAT diverts every outbound TCP connection to port 2593, whatever the IP,
and remembers each connection's original server per client source port. Full source+dest NAT
through loopback (both legs must look like genuine loopback traffic, otherwise Windows
martian-drops the rewritten packets):

  forward: client:P -> S:2593            =>  src+dst 127.0.0.1  (to proxy), remember P -> S
  return:  proxy(127.0.0.1:2593) -> :P   =>  src S:2593, dst <client-ip>

The proxy asks which S to dial on the lookup port (127.0.0.1:25943): it sends "<P>\\n" and
gets back "<S>\\n", or "\\n" if P is unknown.

The proxy's upstream leg binds to source ports 25940-25960 and is excluded (loop prevention).
Must run elevated (WinDivert driver load). Logs to stdout.
"""
import socketserver
import threading

import pydivert

SERVER_PORT = 2593
PROXY_IP = "127.0.0.1"
PROXY_PORT = 2593
LOOKUP_PORT = 25943

FILTER = (
    # forward: any non-loopback connection to the game port except the proxy's own upstream
    # leg (excluded as a port range 25940-25960 so the proxy can retry across ports)
    f"(outbound and not loopback and tcp.DstPort=={SERVER_PORT}"
    f" and (tcp.SrcPort<25940 or tcp.SrcPort>25960))"
    f" or "
    # return: proxy's client-facing packets (loopback; no direction flag — loopback
    # direction semantics vary, and only the proxy sources from 127.0.0.1:2593)
    f"(ip.SrcAddr=={PROXY_IP} and tcp.SrcPort=={PROXY_PORT})"
)

# client source port -> (client ip, original server ip). Ports get reused, so a new
# connection simply overwrites the entry; dict get/set are atomic under the GIL.
conns: dict[int, tuple[str, str]] = {}


class Lookup(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            entry = conns.get(int(self.rfile.readline().strip()))
        except ValueError:
            entry = None
        self.wfile.write(f"{entry[1] if entry else ''}\n".encode())


def main():
    lookup = socketserver.ThreadingTCPServer((PROXY_IP, LOOKUP_PORT), Lookup)
    lookup.daemon_threads = True
    threading.Thread(target=lookup.serve_forever, daemon=True).start()
    print(f"[divert] filter: {FILTER}", flush=True)
    print(f"[divert] lookup on {PROXY_IP}:{LOOKUP_PORT}", flush=True)
    n = 0
    with pydivert.WinDivert(FILTER) as w:
        print("[divert] driver loaded, NAT active", flush=True)
        for pkt in w:
            try:
                if pkt.tcp.dst_port == SERVER_PORT and not pkt.is_loopback:
                    port = pkt.tcp.src_port
                    entry = (pkt.ipv4.src_addr, pkt.ipv4.dst_addr)
                    if conns.get(port) != entry:
                        conns[port] = entry
                        print(f"[divert] client :{port} -> {entry[1]}:{SERVER_PORT}", flush=True)
                    pkt.ipv4.src_addr = PROXY_IP
                    pkt.ipv4.dst_addr = PROXY_IP
                    w.send(pkt)
                    n += 1
                elif (entry := conns.get(pkt.tcp.dst_port)) is not None:
                    pkt.ipv4.src_addr = entry[1]
                    pkt.ipv4.dst_addr = entry[0]
                    w.send(pkt)
                    n += 1
                else:
                    w.send(pkt)
                if n <= 10 or n % 500 == 0:
                    print(f"[divert] {n} packets rewritten (loopback={pkt.is_loopback})", flush=True)
            except Exception as e:
                print(f"[divert] packet error: {e}", flush=True)

if __name__ == "__main__":
    main()

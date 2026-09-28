"""WinDivert NAT: redirects game traffic to the harness proxy.

The client connects to the literal game-server IP (from the HTTPS login response).
Full source+dest NAT through loopback (both legs must look like genuine loopback
traffic, otherwise Windows martian-drops the rewritten packets):

  forward: client -> 74.91.115.123:2593  =>  src+dst 127.0.0.1  (to proxy)
  return:  proxy(127.0.0.1:2593) -> client =>  src 74.91.115.123:2593, dst <client-ip>

The proxy's upstream leg binds to source port 25940 and is excluded (loop prevention).
Must run elevated (WinDivert driver load). Logs to stdout.
"""
import pydivert

SERVER_IP = "74.91.115.123"
SERVER_PORT = 2593
PROXY_IP = "127.0.0.1"
PROXY_PORT = 2593
UPSTREAM_BIND_PORT = 25940

FILTER = (
    # forward: anything to the game server except the proxy's own upstream leg
    # (excluded as a port range 25940-25960 so the proxy can retry across ports)
    f"(outbound and ip.DstAddr=={SERVER_IP} and tcp.DstPort=={SERVER_PORT} "
    f" and (tcp.SrcPort<25940 or tcp.SrcPort>25960))"
    f" or "
    # return: proxy's client-facing packets (loopback; no direction flag — loopback
    # direction semantics vary, and only the proxy sources from 127.0.0.1:2593)
    f"(ip.SrcAddr=={PROXY_IP} and tcp.SrcPort=={PROXY_PORT})"
)

def main():
    print(f"[divert] filter: {FILTER}", flush=True)
    client_ip = None
    n = 0
    with pydivert.WinDivert(FILTER) as w:
        print("[divert] driver loaded, NAT active", flush=True)
        for pkt in w:
            try:
                if pkt.ipv4.dst_addr == SERVER_IP:
                    client_ip = pkt.ipv4.src_addr
                    pkt.ipv4.src_addr = PROXY_IP
                    pkt.ipv4.dst_addr = PROXY_IP
                    w.send(pkt)
                    n += 1
                elif pkt.ipv4.src_addr == PROXY_IP and client_ip is not None:
                    pkt.ipv4.src_addr = SERVER_IP
                    pkt.ipv4.dst_addr = client_ip
                    w.send(pkt)
                    n += 1
                else:
                    w.send(pkt)
                if n <= 10 or n % 500 == 0:
                    print(f"[divert] {n} packets rewritten (client={client_ip}, loopback={pkt.is_loopback})", flush=True)
            except Exception as e:
                print(f"[divert] packet error: {e}", flush=True)

if __name__ == "__main__":
    main()

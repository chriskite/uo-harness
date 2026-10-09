"""Proxy with several characters at once (harness/proxy.py InjectionHub.pick).

A private proxy subprocess, a fake game server playing one character per
connection, and two fake clients connected back to back. Checks what a consumer
relies on:
  * each client gets its own session log, never reusing an existing tag
    (`<stamp>_2`, `<stamp>_3` in the same second)
  * state port: `sessions` lists both; `state`/`intent` without `char` are
    refused naming both; `char` by serial or by name picks one; an unknown
    selector is refused
  * control port: `@char <sel>` binds the connection; an injected packet reaches
    only the selected character's server
  * when one client leaves, requests without `char` work again

Run: python harness/test_multisession.py   (offline, a few seconds)
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from uo.s2c import encode_packet  # noqa: E402

PY = sys.executable
C2S_KEY, S2C_KEY = 0x0F, 0x5A
PRELUDE = bytes([0xFF, 0x00, 0x0D] + [0] * 7 + [0x0C, S2C_KEY, C2S_KEY])
SERIALS = (0x00000101, 0x00000202)
NAMES = ("Alpha", "Bravo")
FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def _free_ports(n):
    socks = [socket.socket() for _ in range(n)]
    for s in socks:
        s.bind(("127.0.0.1", 0))
    ports = [s.getsockname()[1] for s in socks]
    for s in socks:
        s.close()
    return ports


def xor(bs, k):
    return bytes(b ^ k for b in bs)


def login_pkt(serial, x, y, z, direction):
    body = (serial.to_bytes(4, "big") + bytes(4) + (0x190).to_bytes(4, "big")
            + x.to_bytes(4, "big") + y.to_bytes(4, "big") + z.to_bytes(4, "big", signed=True)
            + bytes([direction]))
    return b"\x1b" + body + bytes(42 - len(body))


def seed_pkt(token):
    return bytes.fromhex("bf001d0001") + token.to_bytes(4, "big") + bytes(20)


def char_select_pkt(name):
    """C2S 0x5D PlayCharacter (73 bytes): pattern, name ascii[30], the rest zero."""
    return b"\x5d" + b"\xed" * 4 + name.encode().ljust(30, b"\0") + bytes(73 - 35)


class Upstream:
    """Fake game server: the n-th connection plays character SERIALS[n] (prelude +
    login confirm + seed), then records the decrypted C2S bytes it receives in rx[n]."""

    def __init__(self, port):
        self.rx = [b"" for _ in SERIALS]
        self.ls = socket.create_server(("127.0.0.1", port))
        self.ls.settimeout(15)
        self.stop = threading.Event()
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        for i, serial in enumerate(SERIALS):
            try:
                conn, _ = self.ls.accept()
            except OSError:
                return
            threading.Thread(target=self._run, args=(conn, i, serial), daemon=True).start()
        self.ls.close()

    def _run(self, conn, i, serial):
        conn.settimeout(0.2)
        buf = b""
        while len(buf) < 5 and not self.stop.is_set():
            try:
                buf += conn.recv(5 - len(buf))
            except socket.timeout:
                continue
        conn.sendall(PRELUDE + encode_packet(login_pkt(serial, 100, 200, 0, 0), S2C_KEY)
                     + encode_packet(seed_pkt(8), S2C_KEY))
        while not self.stop.is_set():
            try:
                d = conn.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            if not d:
                break
            self.rx[i] += xor(d, C2S_KEY)
        conn.close()


def state_port(port, req):
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall((json.dumps(req) + "\n").encode())
        return json.loads(s.makefile("rb").readline())


def recv_exact(s, n):
    b = b""
    while len(b) < n:
        d = s.recv(n - len(b))
        if not d:
            raise ConnectionError("closed")
        b += d
    return b


def control_send(s, payload):
    s.sendall(len(payload).to_bytes(2, "big") + payload)
    return recv_exact(s, int.from_bytes(recv_exact(s, 2), "big")).decode()


def connect(port, timeout=30):
    end = time.monotonic() + timeout
    while True:
        try:
            return socket.create_connection(("127.0.0.1", port), timeout=5)
        except ConnectionRefusedError:
            if time.monotonic() > end:
                raise
            time.sleep(0.05)


def wait_for(fn, timeout=6.0):
    end = time.monotonic() + timeout
    v = fn()
    while not v and time.monotonic() < end:
        time.sleep(0.1)
        v = fn()
    return v


def main():
    listen, control, state, up_port = _free_ports(4)
    logdir = tempfile.mkdtemp(prefix="logs_test_multisession_")
    # older captures hold every plain stamp of the next seconds: each client must get a suffix
    now = time.time()
    taken = {time.strftime("%Y%m%d_%H%M%S", time.localtime(now + d)) for d in range(-1, 30)}
    for stamp in taken:
        open(os.path.join(logdir, f"session_{stamp}.jsonl"), "w").close()
    up = Upstream(up_port)
    proxy = subprocess.Popen(
        [PY, "-u", os.path.join(HERE, "proxy.py"), "--listen-port", str(listen),
         "--upstream-host", "127.0.0.1", "--upstream-port", str(up_port),
         "--control-port", str(control), "--state-port", str(state), "--logdir", logdir, "--no-map-z"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        clients = []
        for name in NAMES:          # back to back: usually within the same second
            c = connect(listen)
            c.sendall(bytes.fromhex("ef0000000c"))
            clients.append(c)
        for c, name in zip(clients, NAMES):   # C2S decodes once the server's prelude gave the key
            c.recv(65536)
            c.sendall(xor(char_select_pkt(name), C2S_KEY))
        wait_for(lambda: all(up.rx))

        print("session logs")
        tags = sorted(f[len("session_"):-len(".jsonl")] for f in os.listdir(logdir)
                      if f.endswith(".jsonl") and f[len("session_"):-len(".jsonl")] not in taken)
        split = [t.rsplit("_", 1) for t in tags]
        check("one new log per client", len(tags) == 2, str(tags))
        check("an existing stamp is never reused: <stamp>_<n>",
              all(s[0] in taken and s[1].isdigit() and int(s[1]) >= 2 for s in split), str(tags))
        check("two clients in the same second get distinct suffixes",
              len(split) == 2 and (split[0][0] != split[1][0] or {split[0][1], split[1][1]} == {"2", "3"}),
              str(tags))

        print("state port")

        def both_known():
            r = state_port(state, {"op": "sessions"})
            got = {(s["serial"], s["name"]) for s in r.get("sessions", [])}
            return r if got == {("0x00000101", "Alpha"), ("0x00000202", "Bravo")} else None
        r = wait_for(both_known)
        check("sessions op lists both characters", bool(r), str(state_port(state, {"op": "sessions"})))
        check("sessions carry their log tags", bool(r) and sorted(s["tag"] for s in r["sessions"]) == tags, str(r))
        r = state_port(state, {"op": "state"})
        check("state without char refused, naming both",
              r["ok"] is False and r["error"] == "several sessions (Alpha, Bravo); pass char", str(r))
        r = state_port(state, {"op": "state", "char": "0x00000202"})
        check("char by serial", r["ok"] is True and r["movement"]["self_serial"] == 0x202, str(r.get("movement")))
        r = state_port(state, {"op": "state", "char": "alpha"})
        check("char by name, any case", r["ok"] is True and r["movement"]["self_serial"] == 0x101,
              str(r.get("movement")))
        r = state_port(state, {"op": "state", "char": "0x999"})
        check("unknown selector refused",
              r["ok"] is False and r["error"] == "no session for character '0x999'", str(r))
        r = state_port(state, {"op": "intent", "intent": {"text": "x"}})
        check("intent without char refused too",
              r["ok"] is False and r["error"].startswith("several sessions ("), str(r))
        r = state_port(state, {"op": "intent", "intent": {"text": "x"}, "char": "Bravo"})
        check("intent with char accepted", r["ok"] is True, str(r))

        print("control port")
        ctl = connect(control)
        click = bytes([0x09]) + (0x40001234).to_bytes(4, "big")
        reply = control_send(ctl, click)
        check("unbound frame refused while several are online", reply.startswith("ERR several sessions ("), reply)
        reply = control_send(ctl, b"@char Nobody")
        check("@char unknown -> ERR", reply == "ERR no session for character 'Nobody'", reply)
        reply = control_send(ctl, b"@char 0x00000101")
        check("@char by serial -> OK <name>", reply == "OK Alpha", reply)
        before = list(up.rx)
        reply = control_send(ctl, click)
        check("bound injection accepted", reply == "OK", reply)
        wait_for(lambda: up.rx[0].endswith(click))
        time.sleep(0.3)
        check("only the selected character's server got the click",
              up.rx[0][len(before[0]):] == click and up.rx[1][len(before[1]):] == b"",
              f"{up.rx[0][len(before[0]):].hex()} / {up.rx[1][len(before[1]):].hex()}")
        ctl.close()

        print("one leaves")
        clients[1].close()
        r = wait_for(lambda: len(state_port(state, {"op": "sessions"})["sessions"]) == 1
                     and state_port(state, {"op": "state"}))
        check("default selection works again with one session",
              bool(r) and r["ok"] is True and r["movement"]["self_serial"] == 0x101, str(r))
        clients[0].close()
        r = wait_for(lambda: state_port(state, {"op": "sessions"})["sessions"] == [])
        check("no sessions: the sessions op still answers", r is True, str(r))
        r = state_port(state, {"op": "state"})
        check("no sessions: state says no active session", r.get("error") == "no active session", str(r))
    finally:
        up.stop.set()
        proxy.terminate()
        proxy.communicate(timeout=5)
        shutil.rmtree(logdir, ignore_errors=True)
    print(f"\nmultisession: {'ALL PASS' if not FAILURES else f'{len(FAILURES)} FAILURES'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

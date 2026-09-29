"""Backend tests for the visualizer (docs/VISUALIZER.md §7): viz_feed + viz_server.

  python harness/test_viz.py

- replay parity on session 20260929_163420 (pinned to the commit that
  recorded the bank errand, since the live proxy may still append to the file):
  exact order, errand outcome, proxy events, /api/state == SessionTap.state()
- order fallback: 20260928_141253 (pre-fix S2C rows) replays as "approx"
- SSE framing, monotonic seqs, verbatim envelopes, Last-Event-ID resume
  without duplicates, and state coalescing
- live: viz_server --live against a proxy subprocess (fake upstream, private
  ports), started before the proxy (proxy down -> connected=false); asserts
  the viz never connects to the proxy's control port and adds no C2S packets
"""
import collections
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable
sys.path.insert(0, HERE)

import viz_feed  # noqa: E402
import viz_server  # noqa: E402
from uo.s2c import encode_packet  # noqa: E402

TAG = "20260929_163420"
TAG_COMMIT = "5f8c228"      # "Phase 3 DONE: unattended bank run ... session_20260929_163420"
OLD_TAG = "20260928_141253"
SERVER_PORT = 12630
PROXY_PORT, UPSTREAM_PORT, CONTROL_PORT, STATE_PORT, VIZ_PORT = 12620, 12621, 12622, 12623, 12624
C2S_KEY, S2C_KEY = 0x0F, 0x5A
PRELUDE = bytes([0xFF, 0x00, 0x0D] + [0] * 7 + [0x0C, S2C_KEY, C2S_KEY])
SELF = 0x00094375

FAILURES = []


def check(name, cond, extra=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAILURES.append(name)


def pinned_capture() -> str:
    """logdir holding the pinned 163420 capture."""
    d = tempfile.mkdtemp(prefix="viz_test_")
    for ext in ("jsonl", "c2s.raw", "s2c.raw"):
        data = subprocess.run(["git", "show", f"{TAG_COMMIT}:logs/session_{TAG}.{ext}"], cwd=ROOT,
                              capture_output=True, check=True).stdout
        with open(os.path.join(d, f"session_{TAG}.{ext}"), "wb") as f:
            f.write(data)
    return d


def get(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def serve(feed, port):
    srv = viz_server.VizServer(("127.0.0.1", port), feed, os.path.join(ROOT, "viz", "dist"),
                               os.path.join(ROOT, "harness", "data", "walkmem.json"))
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    return srv


class SSE:
    """Minimal SSE reader: frames as dicts {id, event, data}."""

    def __init__(self, url, last_id=None):
        self.sock = socket.create_connection(("127.0.0.1", int(url.split(":")[2].split("/")[0])), timeout=5)
        path = "/" + url.split("/", 3)[3]
        hdr = f"GET {path} HTTP/1.1\r\nHost: x\r\nAccept: text/event-stream\r\n"
        if last_id is not None:
            hdr += f"Last-Event-ID: {last_id}\r\n"
        self.sock.sendall((hdr + "\r\n").encode())
        self.buf = b""
        while b"\r\n\r\n" not in self.buf:
            self.buf += self.sock.recv(65536)
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        self.headers = head.decode()

    def frames(self, until, timeout=5.0):
        """Read frames until until(frames) is true or timeout."""
        out = []
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            while b"\n\n" in self.buf:
                raw, self.buf = self.buf.split(b"\n\n", 1)
                f = {}
                for line in raw.decode().split("\n"):
                    if line.startswith(":") or not line:
                        continue
                    k, _, v = line.partition(": ")
                    f[k] = v
                if "event" in f:
                    out.append(f)
            if until(out):
                return out
            self.sock.settimeout(max(0.01, end - time.monotonic()))
            try:
                d = self.sock.recv(65536)
            except socket.timeout:
                break
            if not d:
                break
            self.buf += d
        return out

    def close(self):
        self.sock.close()


def has_state(fr):
    return any(f["event"] == "state" for f in fr)


# --------------------------------------------------------------------- replay

def test_replay_parity(logdir):
    print(f"== replay parity: {TAG} (pinned at {TAG_COMMIT}) ==")
    d = viz_feed.ReplayDriver(TAG, logdir)
    d.run_to_end()
    st = d.tap.state()
    check("order is exact (every jsonl row matched its packet)", d.order == "exact" and d.order_note is None,
          f"{d.order} {d.order_note}")
    check("timeline fully applied", d.position == len(d.items), f"{d.position}/{len(d.items)}")
    pos = st["movement"]["pos"]
    check("final movement.pos == [1963, 2597, 0, facing]", pos is not None and pos[:3] == [1963, 2597, 0], str(pos))
    items = st["world"]["items"]
    me = st["world"]["self"]["serial"]
    bank = [int(k, 16) for k, v in items.items() if v.get("layer") == 0x1D and v.get("container") == me]
    opens = [e["data"]["serial"] for e in st["events"]
             if e["origin"] == "world" and e["data"]["ev"] == "container_open"]
    check("container_open on the bank box (self item, layer 0x1D)", len(bank) == 1 and bank[0] in opens,
          f"bank={[hex(b) for b in bank]} opens={[hex(o) for o in opens]}")
    item_ts = {it[0] for it in d.items}
    check("world event t = the time its packet was processed (a jsonl row t), not drain time",
          all(e["t"] in item_ts for e in st["events"] if e["origin"] == "world"))
    pev = collections.Counter(e["data"]["ev"] for e in st["events"] if e["origin"] == "proxy")
    check("proxy events: 36 step, 2 reanchor_client", pev["step"] == 36 and pev["reanchor_client"] == 2, str(dict(pev)))
    c2s = collections.Counter((e["data"]["src"], e["data"]["id"]) for e in st["events"]
                              if e["origin"] == "proxy" and e["data"]["ev"] == "c2s")
    check("c2s agent events: 54 walks + 1 speech, nothing else",
          c2s == {("agent", "0x02"): 54, ("agent", "0xAD"): 1}, str(dict(c2s)))
    check("54 agent confirms hidden", pev["s2c_confirm_hidden"] == 54, str(pev["s2c_confirm_hidden"]))
    check("recorded timer decisions all reproduced", d.timer_divergences == 0, str(d.timer_divergences))
    check("snapshot labels keep the banker's click label (no event-ring dependency)",
          st["world"]["labels"].get("0x000001EA") == "Len the banker", str(st["world"].get("labels")))
    tc = {(s, p): n for s, p, n in st["traffic"]["c2s"]}
    check("cumulative traffic: 54 agent walks + 1 agent speech, 54 hidden confirms, 2 re-anchors",
          tc == {("agent", "0x02"): 54, ("agent", "0xAD"): 1}
          and st["traffic"]["proxy_events"].get("s2c_confirm_hidden") == 54
          and st["traffic"]["proxy_events"].get("reanchor_client") == 2, str(st["traffic"]))
    check("envelopes: seq 0..next-1, numeric t, known origin",
          [e["seq"] for e in st["events"]] == list(range(st["next"]))
          and all(isinstance(e["t"], float) and e["origin"] in ("world", "proxy") for e in st["events"]))

    srv = serve(d, SERVER_PORT)
    try:
        d.pump()
        got = get(f"http://127.0.0.1:{SERVER_PORT}/api/state")
        want = json.loads(json.dumps({"ok": True, **d.tap.state(), "viz": d.viz()}))
        check("GET /api/state == offline SessionTap.state() + viz block", got == want,
              "" if got == want else str(sorted(k for k in set(got) | set(want) if got.get(k) != want.get(k))))
        check("viz block: replay, session tag, exact, playback at the end",
              got["viz"] == {"mode": "replay", "session": TAG, "order": "exact", "connected": True,
                             "playback": {"playing": False, "rate": 1.0, "position": len(d.items),
                                          "total": len(d.items)}}, str(got["viz"]))
        h = get(f"http://127.0.0.1:{SERVER_PORT}/api/health")
        check("health: mode/order/diagnostics", h["mode"] == "replay" and h["order"] == "exact"
              and h["diagnostics"]["packet_counts"][0][2] > 0, str({k: h[k] for k in ("mode", "order", "ring")}))
        wm = urllib.request.urlopen(f"http://127.0.0.1:{SERVER_PORT}/api/walkmem").read()
        with open(os.path.join(ROOT, "harness", "data", "walkmem.json"), "rb") as f:
            check("/api/walkmem serves the file verbatim", wm == f.read())
        req = urllib.request.Request(f"http://127.0.0.1:{SERVER_PORT}/api/playback", method="POST",
                                     data=b'{"action": "rate", "rate": 0}')
        try:
            urllib.request.urlopen(req)
            code = 200
        except urllib.error.HTTPError as e:
            code = e.code
        check("POST /api/playback rejects rate 0", code == 400, str(code))
    finally:
        srv.shutdown()
        srv.server_close()


def test_order_fallback():
    print(f"== order fallback: {OLD_TAG} ==")
    d = viz_feed.ReplayDriver(OLD_TAG, os.path.join(ROOT, "logs"))
    check("pre-fix capture replays in approximate order", d.order == "approx", f"{d.order} {d.order_note}")
    check("fallback reason recorded", bool(d.order_note), str(d.order_note))
    d.run_to_end()
    st = d.tap.state()
    check("approx replay still builds the world (self known)", st["world"].get("self", {}).get("serial") is not None)
    check("approx viz block says approx", d.viz()["order"] == "approx")


def test_sse(logdir):
    print("== SSE framing, resume, coalescing ==")
    d = viz_feed.ReplayDriver(TAG, logdir)
    srv = serve(d, SERVER_PORT + 1)
    url = f"http://127.0.0.1:{SERVER_PORT + 1}/api/events"
    try:
        for _ in range(300):
            d.step()
        d.pump()
        s = SSE(url)
        check("SSE content type", "text/event-stream" in s.headers.lower(), s.headers.splitlines()[0])
        fr = s.frames(has_state)
        s.close()
        evs = [f for f in fr if f["event"] == "world_event"]
        ids = [int(f["id"]) for f in evs]
        check("fresh connect: whole ring then one state", ids == list(range(d.cursor)) and fr[-1]["event"] == "state"
              and sum(f["event"] == "state" for f in fr) == 1, f"{len(ids)} events, next {d.cursor}")
        check("frames carry id == envelope seq, data verbatim",
              all(json.loads(f["data"]) == json.loads(json.dumps(d.tap.events[int(f["id"])])) for f in evs))
        state = json.loads(fr[-1]["data"])
        check("state frame = response without events, with viz", "events" not in state and state["ok"]
              and state["viz"]["playback"]["position"] == 300 and "movement" in state and "world" in state)
        last = ids[-1]

        for _ in range(400):
            d.step()
        d.pump()
        s = SSE(url, last_id=last)
        fr2 = s.frames(has_state)
        s.close()
        ids2 = [int(f["id"]) for f in fr2 if f["event"] == "world_event"]
        check("Last-Event-ID resume: exactly the missed suffix, no duplicates",
              ids2 == list(range(last + 1, d.cursor)) and ids2, f"{ids2[:3]}..{ids2[-3:]} next {d.cursor}")
        check("resume + first stream = gapless seqs", ids + ids2 == list(range(d.cursor)))
        s = SSE(url + f"?since={d.cursor - 5}")
        fr3 = s.frames(has_state)
        s.close()
        check("?since=N resume", [int(f["id"]) for f in fr3 if f["event"] == "world_event"]
              == list(range(d.cursor - 5, d.cursor)))

        # coalescing: pump thread running; a 500-packet burst -> one state frame
        d.run()
        s = SSE(url)
        s.frames(has_state)            # initial ring + state
        time.sleep(0.6)                # settle: unchanged state is not re-sent
        quiet = s.frames(lambda f: False, timeout=0.6)
        before = d.cursor
        with d.sim:
            for _ in range(500):
                d.step()
        fr4 = s.frames(lambda f: False, timeout=1.2)
        s.close()
        ids4 = [int(f["id"]) for f in fr4 if f["event"] == "world_event"]
        check("idle: no frames while nothing changes", quiet == [], str(quiet[:2]))
        check("burst coalesced into one state frame", sum(f["event"] == "state" for f in fr4) == 1,
              str([f["event"] for f in fr4 if f["event"] == "state"]))
        check("burst events streamed in order", ids4 == list(range(before, d.cursor)) and ids4,
              f"{len(ids4)} of {d.cursor - before}")
    finally:
        d.stop()
        srv.shutdown()
        srv.server_close()


# ----------------------------------------------------------------------- live

def xor(bs, k):
    return bytes(b ^ k for b in bs)


def login_pkt(serial, x, y, z, direction):
    body = (serial.to_bytes(4, "big") + bytes(4) + (0x190).to_bytes(4, "big")
            + x.to_bytes(4, "big") + y.to_bytes(4, "big") + z.to_bytes(4, "big", signed=True)
            + bytes([direction]))
    return b"\x1b" + body + bytes(42 - len(body))


def seed_pkt(token):
    return bytes.fromhex("bf001d0001") + token.to_bytes(4, "big") + bytes(20)


def fake_upstream(ready: threading.Event, stop: threading.Event):
    """Fake game server: prelude + login + seed, confirms every 7-byte walk."""
    ls = socket.create_server(("127.0.0.1", UPSTREAM_PORT))
    ls.settimeout(10)
    ready.set()
    try:
        conn, _ = ls.accept()
    except socket.timeout:
        return
    conn.settimeout(0.2)
    buf = b""
    while len(buf) < 5:
        buf += conn.recv(5 - len(buf))
    conn.sendall(PRELUDE + encode_packet(login_pkt(SELF, 100, 200, 5, 0x80), S2C_KEY)
                 + encode_packet(seed_pkt(8), S2C_KEY))
    buf = b""
    while not stop.is_set():
        try:
            d = conn.recv(65536)
        except socket.timeout:
            continue
        except OSError:
            break
        if not d:
            break
        buf += xor(d, C2S_KEY)
        while len(buf) >= 7 and buf[0] == 0x02:
            conn.sendall(encode_packet(bytes([0x22, buf[2], 0x01]), S2C_KEY))
            buf = buf[7:]
    conn.close()
    ls.close()


def test_live():
    print("== live: viz_server --live against a proxy subprocess ==")
    logdir = tempfile.mkdtemp(prefix="viz_live_")
    viz = subprocess.Popen([PY, "-u", os.path.join(HERE, "viz_server.py"), "--live", "--state-port", str(STATE_PORT),
                            "--port", str(VIZ_PORT)], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    proxy = None
    stop = threading.Event()
    base = f"http://127.0.0.1:{VIZ_PORT}"
    try:
        for _ in range(50):
            try:
                st = get(base + "/api/state")
                break
            except OSError:
                time.sleep(0.1)
        time.sleep(0.5)
        st = get(base + "/api/state")
        check("proxy down: served, connected=false", st["viz"]["connected"] is False and st["ok"] is False
              and st["viz"]["mode"] == "live", str(st.get("viz")))

        ready = threading.Event()
        threading.Thread(target=fake_upstream, args=(ready, stop), daemon=True).start()
        ready.wait(5)
        proxy = subprocess.Popen(
            [PY, "-u", os.path.join(HERE, "proxy.py"), "--listen-port", str(PROXY_PORT),
             "--upstream-host", "127.0.0.1", "--upstream-port", str(UPSTREAM_PORT),
             "--control-port", str(CONTROL_PORT), "--state-port", str(STATE_PORT), "--logdir", logdir],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        time.sleep(1.0)
        client = socket.create_connection(("127.0.0.1", PROXY_PORT), timeout=5)
        client.sendall(bytes.fromhex("ef0000000c"))
        time.sleep(0.3)
        client.sendall(xor(bytes([0x02, 0x80, 5, 0, 0, 0, 0]), C2S_KEY))   # client walks N
        st = None
        end = time.monotonic() + 6
        while time.monotonic() < end:
            st = get(base + "/api/state")
            if st.get("ok") and st["movement"]["pos"] == [100, 199, 5, 0]:
                break
            time.sleep(0.2)
        check("poller reconnected: connected=true, ok", st["viz"]["connected"] is True and st["ok"] is True,
              str(st.get("viz")))
        check("live movement read through the viz", st["movement"]["pos"] == [100, 199, 5, 0]
              and st["movement"]["self_serial"] == SELF, str(st.get("movement")))
        steps = [e["data"] for e in st["events"] if e["origin"] == "proxy" and e["data"]["ev"] == "step"]
        check("live proxy step envelope", steps == [{"ev": "step", "from": [100, 200], "to": [100, 199], "z": 5}],
              str(steps))
        check("live world events wrapped (client walk seen)",
              any(e["origin"] == "world" and e["data"]["ev"] == "walk" for e in st["events"]))
        s = SSE(base + "/api/events")
        fr = s.frames(has_state)
        s.close()
        check("live SSE: gapless ring + state", has_state(fr)
              and [int(f["id"]) for f in fr if f["event"] == "world_event"] == list(range(st["next"])))
        client.close()
        time.sleep(0.5)
    finally:
        stop.set()
        viz.terminate()
        viz.wait(5)
        out = ""
        if proxy is not None:
            proxy.terminate()
            out, _ = proxy.communicate(timeout=5)
    log = [json.loads(l) for f in os.listdir(logdir) if f.endswith(".jsonl")
           for l in open(os.path.join(logdir, f), encoding="utf-8")]
    srcs = {e.get("src") for e in log if e.get("dir") == "c2s"}
    check("proxy jsonl: every C2S packet from the client (viz injected nothing)", srcs == {"client"}, str(srcs))
    check("proxy saw no control-port connection (viz is read-only)", "[proxy] control" not in out,
          out.strip()[-300:])
    shutil.rmtree(logdir, ignore_errors=True)


def main():
    logdir = pinned_capture()
    try:
        test_replay_parity(logdir)
        test_order_fallback()
        test_sse(logdir)
        test_live()
    finally:
        shutil.rmtree(logdir, ignore_errors=True)
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILURES: {FAILURES}"))
    sys.exit(0 if not FAILURES else 1)


if __name__ == "__main__":
    main()

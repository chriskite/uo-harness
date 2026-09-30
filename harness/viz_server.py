"""Visualizer backend (docs/VISUALIZER.md §2): REST + SSE on localhost.

  python harness/viz_server.py --live [--state-port 25942] [--port 8080]
  python harness/viz_server.py --replay 20260929_163420 [--rate 1] [--paused] [--port 8080]

Routes:
  GET  /api/state     latest state-port response (+ `viz` block); `events` = the ring (≤2000)
  GET  /api/events    SSE: `event: world_event` (id = envelope seq) + `event: state`
                      (response without events, ≤4 Hz, only when changed). Resume with
                      the Last-Event-ID header (seq > id) or ?since=N (seq >= N).
  GET  /api/walkmem   harness/data/walkmem.json verbatim (re-read when it changes)
  GET  /api/health    mode, session, order, poll lag, connection, diagnostics
  POST /api/playback  replay only: {"action": "play"|"pause"|"step"|"rate", "rate": R}
  GET  /api/gate      live only: the proxy's agent gate ({"op": "gate"}), verbatim
  POST /api/gate      live only: {"action": "pause"|"resume"|"kill"} -> {"op": "gate",
                      "action": ...}; the proxy's JSON, 200 if ok else 409. Any other
                      action (rearm included: CLI-only, harness/agent_gate.py) -> 400;
                      replay -> 409; state port unreachable -> 502
  GET  /api/facet     facet picture metadata ({"available": false, "error"} without one)
  GET  /api/facet/<cx>/<cy>.png  256x256-tile chunk of the 1 px/tile facet picture
                      (harness/facet.py; read-only from the install dir)
  GET  /, /assets/*   the built frontend (viz/dist)

Live mode only ever opens the proxy's state port (JSON lines). It never connects
to the control port and never injects or sends anything toward the game server
(ANTICHEAT §8). Its one write is the agent gate: pause/resume/kill flip the
proxy's gate, which only decides whether agent injections on the control port
are rejected. Everything else is observation.
"""
import argparse
import json
import os
import socket
import struct
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import viz_feed  # noqa: E402
import facet as facet_mod  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SSE_KEEPALIVE_S = 15.0
GATE_ACTIONS = ("pause", "resume", "kill")   # "rearm" is CLI-only by policy (harness/agent_gate.py)
GATE_TIMEOUT_S = 3.0
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".json": "application/json", ".map": "application/json",
                 ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
                 ".txt": "text/plain; charset=utf-8"}


class WalkMemFile:
    """The walk-memory file, served verbatim; re-read when mtime/size change."""

    def __init__(self, path: str):
        self.path = path
        self.lock = threading.Lock()
        self.key = None
        self.body = None

    def get(self) -> bytes | None:
        try:
            st = os.stat(self.path)
        except OSError:
            return None
        key = (st.st_mtime_ns, st.st_size)
        with self.lock:
            if key != self.key:
                with open(self.path, "rb") as f:
                    self.body = f.read()
                self.key = key
            return self.body


class Handler(BaseHTTPRequestHandler):
    server_version = "uo-viz/1"
    protocol_version = "HTTP/1.1"

    # -- helpers
    def log_message(self, fmt, *args):  # quiet; errors still go through log_error
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj).encode())

    # -- routes
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urlsplit(self.path)
        feed = self.server.feed
        if url.path == "/api/state":
            self._send(200, feed.state_body().encode())
        elif url.path == "/api/events":
            self._sse(url)
        elif url.path == "/api/walkmem":
            body = self.server.walkmem.get()
            if body is None:
                self._json(404, {"error": f"no walk memory file at {self.server.walkmem.path}"})
            else:
                self._send(200, body)
        elif url.path == "/api/health":
            self._json(200, feed.health())
        elif url.path == "/api/gate":
            self._gate(None)
        elif url.path == "/api/facet":
            fp = self.server.facet
            self._json(200, fp.meta() if fp else {"available": False, "error": self.server.facet_error})
        elif url.path.startswith("/api/facet/") and url.path.endswith(".png"):
            self._facet_chunk(url.path[len("/api/facet/"):-len(".png")])
        elif url.path.startswith("/api/"):
            self._json(404, {"error": f"unknown route {url.path}"})
        else:
            self._static(url.path)

    def _facet_chunk(self, spec: str):
        fp = self.server.facet
        try:
            cx, cy = (int(v) for v in spec.split("/"))
        except ValueError:
            self._json(400, {"error": f"bad chunk {spec!r}"})
            return
        png = fp.chunk_png(cx, cy) if fp else None
        if png is None:
            self._json(404, {"error": "no facet picture" if fp is None else f"chunk {cx},{cy} out of range"})
            return
        self._send(200, png, "image/png")

    def do_POST(self):
        url = urlsplit(self.path)
        if url.path not in ("/api/playback", "/api/gate"):
            self._json(404, {"error": f"unknown route {url.path}"})
            return
        n = int(self.headers.get("Content-Length") or 0)
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "bad json"})
            return
        if not isinstance(req, dict):
            self._json(400, {"error": "body must be a JSON object"})
            return
        if url.path == "/api/gate":
            self._gate(req.get("action"))
        else:
            self._playback(req)

    def _playback(self, req: dict):
        feed = self.server.feed
        if not isinstance(feed, viz_feed.ReplayDriver):
            self._json(409, {"error": "playback control is replay-only"})
            return
        action = req.get("action")
        try:
            if action == "play":
                feed.play()
            elif action == "pause":
                feed.pause()
            elif action == "step":
                feed.step()
            elif action == "rate":
                feed.set_rate(float(req.get("rate")))
            else:
                self._json(400, {"error": f"unknown action {action!r}"})
                return
        except (TypeError, ValueError) as e:
            self._json(400, {"error": str(e)})
            return
        self._json(200, {"ok": True, **feed.viz()})

    def _gate(self, action):
        """GET (action None) or POST /api/gate: one gate op on a short-lived state-port
        connection, so the feed's connection and since cursor are untouched."""
        if self.command == "POST" and action not in GATE_ACTIONS:
            self._json(400, {"ok": False, "error": f"unknown gate action {action!r}"
                             + (" (rearm is CLI-only: python harness/agent_gate.py rearm)"
                                if action == "rearm" else "")})
            return
        feed = self.server.feed
        if not isinstance(feed, viz_feed.StatePortPoller):
            self._json(409, {"ok": False, "error": "no gate in replay"})
            return
        try:
            resp = gate_request(feed.host, feed.port, action)
        except (OSError, ValueError) as e:
            self._json(502, {"ok": False, "error": f"state port unreachable: {type(e).__name__}: {e}"})
            return
        if action is not None:
            feed.wake()   # push the new gate in the next state frame
        self._json(200 if resp.get("ok") else 409, resp)

    def _sse(self, url):
        feed = self.server.feed
        since = None
        last_id = self.headers.get("Last-Event-ID")
        qs = parse_qs(url.query)
        try:
            if last_id not in (None, ""):
                since = int(last_id) + 1
            elif "since" in qs:
                since = int(qs["since"][0])
        except ValueError:
            since = None
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        q = feed.subscribe(since)
        try:
            self.wfile.write(b"retry: 1000\n\n")
            self.wfile.flush()
            while not q.closed and not self.server.stopping:
                frames = q.drain(SSE_KEEPALIVE_S)
                self.wfile.write(("".join(frames) if frames else ": keepalive\n\n").encode())
                self.wfile.flush()
        except (ConnectionError, OSError):
            pass
        finally:
            feed.unsubscribe(q)

    def _static(self, path: str):
        dist = os.path.realpath(self.server.dist)
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        full = os.path.realpath(os.path.join(dist, rel))
        try:
            inside = os.path.commonpath([full, dist]) == dist
        except ValueError:  # different drive
            inside = False
        if not inside or not os.path.isfile(full):
            if not os.path.isdir(dist):
                self._send(404, f"frontend not built: {dist} missing (cd viz && bun run build)\n".encode(),
                           "text/plain; charset=utf-8")
            else:
                self._send(404, b"not found\n", "text/plain; charset=utf-8")
            return
        with open(full, "rb") as f:
            body = f.read()
        self._send(200, body, CONTENT_TYPES.get(os.path.splitext(full)[1].lower(), "application/octet-stream"))


def gate_request(host: str, port: int, action: str | None = None) -> dict:
    """`{"op": "gate"[, "action": A]}` on a fresh state-port connection."""
    req = {"op": "gate"} if action is None else {"op": "gate", "action": action}
    with socket.create_connection((host, port), timeout=GATE_TIMEOUT_S) as s:
        s.sendall((json.dumps(req) + "\n").encode())
        with s.makefile("rb") as f:
            line = f.readline()
    if not line:
        raise ConnectionResetError("state port closed the connection")
    resp = json.loads(line)
    if not isinstance(resp, dict):
        raise ValueError("state port reply is not a JSON object")
    return resp


class VizServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, addr, feed, dist: str, walkmem: str, facet_path: str | None = None):
        super().__init__(addr, Handler)
        self.feed = feed
        self.dist = dist
        self.walkmem = WalkMemFile(walkmem)
        self.facet = None
        self.facet_error = "disabled (--no-facet)" if facet_path is None else None
        if facet_path is not None:
            try:
                self.facet = facet_mod.FacetPicture(facet_path)
            except (OSError, ValueError, struct.error) as e:
                self.facet_error = f"{facet_path}: {e}"
        self.stopping = False

    def shutdown(self):
        self.stopping = True
        for q in list(self.feed.subscribers):
            q.closed = True
            q.notify()
        super().shutdown()


def build_feed(args):
    if args.replay:
        feed = viz_feed.ReplayDriver(args.replay, args.logdir)
        feed.set_rate(args.rate)
        if not args.paused:
            feed.play()
    else:
        feed = viz_feed.StatePortPoller(args.state_host, args.state_port)
    feed.run()
    return feed


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true", help="poll the running proxy's state port")
    mode.add_argument("--replay", metavar="TAG", help="replay logs/session_TAG.* offline")
    p.add_argument("--state-host", default="127.0.0.1")
    p.add_argument("--state-port", type=int, default=25942)
    p.add_argument("--logdir", default=os.path.join(ROOT, "logs"))
    p.add_argument("--rate", type=float, default=1.0, help="replay speed (1 = real cadence)")
    p.add_argument("--paused", action="store_true", help="replay: start paused at the first packet")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--dist", default=os.path.join(ROOT, "viz", "dist"))
    p.add_argument("--walkmem", default=os.path.join(ROOT, "harness", "data", "walkmem.json"))
    p.add_argument("--facet", default=facet_mod.DEFAULT_PATH,
                   help="facet picture drawn under the map (read-only; install dir facet00.mul)")
    p.add_argument("--no-facet", action="store_true", help="don't load a facet picture")
    args = p.parse_args()

    feed = build_feed(args)
    srv = VizServer((args.host, args.port), feed, os.path.abspath(args.dist), args.walkmem,
                    None if args.no_facet else args.facet)
    what = (f"replay {args.replay} ({feed.order} order, {len(feed.items)} items)" if args.replay
            else f"live state port {args.state_host}:{args.state_port}")
    print(f"[viz] {what}; http://{args.host}:{args.port}/", flush=True)
    print(f"[viz] facet: {f'{srv.facet.width}x{srv.facet.height} from {srv.facet.path}' if srv.facet else srv.facet_error}",
          flush=True)
    try:
        srv.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        feed.stop()
        srv.server_close()


if __name__ == "__main__":
    main()

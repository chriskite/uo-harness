"""Visualizer backend (docs/VISUALIZER.md §2): REST + SSE on localhost.

  python harness/viz_server.py --live [--state-port 25942] [--port 8080]
  python harness/viz_server.py --replay 20260929_163420 [--rate 1] [--paused] [--port 8080]

Routes:
  GET  /api/state     latest state-port response (+ `viz` block); `events` = the ring (≤2000)
  GET  /api/events    SSE: `event: world_events` (a JSON array of envelopes, id = the last
                      seq) + `event: state` (response without events, ≤4 Hz, only when
                      changed); a slow reader gets the newest state, never a backlog of old
                      ones (viz_feed.Subscriber). Resume with the Last-Event-ID header
                      (seq > id) or ?since=N (seq >= N).
  GET  /api/walkmem   walk memory (facet 0) from the harness memory store, in the
                      nav.WalkMemory JSON format (docs/MEMORY.md)
  GET  /api/paperdoll.png  the player's paperdoll from the current state (body, skin hue,
                      worn items), drawn from the client's gump art (harness/paperdoll.py);
                      404 JSON when the state has no player or the install data is missing
  GET  /api/art/<graphic>.png  an item's art from the client's art.uoo (decimal or 0x hex
                      graphic), cropped to its opaque pixels (harness/uoart.py); 404 JSON
                      for an unknown/empty graphic or missing install data
  GET  /api/multi/<id>  a house's footprint from the client's multi.mul (decimal or 0x hex
                      multi id = the graphic of a data_type 2 ground item): {"id", "source",
                      "tiles": [[dx, dy, "wall"|"floor"]]}; 404 JSON for an unknown id
  GET  /api/live.jpg?zoom=1-3&w=  one JPEG of the character cropped from the game window,
                      w = served width 320-1600 (default 640; height follows 4:3)
  GET  /api/live.mjpeg?zoom=&fps=&w=  the same as a continuous stream (multipart/x-mixed-replace),
                      harness/liveview.py; passive window capture, only while someone watches;
                      503 JSON when there's no game window (or --no-live)
  GET  /api/skillnames  skill names by id from the client's skills.mul (uomap.skill_names; the
                      viz's fallback when the server sent no name list this session)
  GET  /api/cliloc?n=N[,N...]  {"texts": {N: text}} from the client's Cliloc.enu (uo/cliloc.py,
                      read-only; at most CLILOC_MAX numbers, unknown ones left out): the
                      names of buffs the server sent as a cliloc with an empty title
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
  GET  /api/jobs?job=lumber|hunt[&since=T][&tz=M]  job analytics from the memory store
                      (harness/jobs.py; tz = minutes east of UTC for the per-day split,
                      default the server's local offset), cached 2 s
  GET  /api/overseer?after_chat=N&after_juncture=M  {chat, junctures, open, open_ids,
                      heartbeat}: rows with id above the cursors (the newest 200 when 0),
                      the open-juncture count and ids, the overseer's last heartbeat
  POST /api/chat      {"text": T} (1..2000 chars after trimming) -> Memory.chat_post(
                      "user", T) -> {"ok": true, "id": N}; 400 otherwise
  GET  /api/captcha   {"mode": "human"|"auto", "store": bool}: who answers the harvest
                      captcha (Memory.captcha_mode; "human" when unset or no store)
  POST /api/captcha   {"mode": "human"|"auto"} -> Memory.set_captcha_mode -> {"ok": true,
                      "mode": M}; 400 otherwise. Runners read it at every captcha
  GET  /, /assets/*   the built frontend (viz/dist)

Live mode only ever opens the proxy's state port (JSON lines). It never connects
to the control port and never injects or sends anything toward the game server
(ANTICHEAT §8). Its one write toward the proxy is the agent gate: pause/resume/kill
flip the proxy's gate, which only decides whether agent injections on the control
port are rejected. Its writes to the memory store are a user chat row (POST
/api/chat) for the overseer AI to read and the captcha mode (POST /api/captcha).
Everything else is observation.
"""
import argparse
import json
import os
import socket
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import viz_feed  # noqa: E402
import facet as facet_mod  # noqa: E402
import jobs as jobs_mod  # noqa: E402
import memory as memory_mod  # noqa: E402
from uo import cliloc  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SSE_KEEPALIVE_S = 15.0
# Kernel send buffer of an SSE connection: a slow reader's backlog then waits in its
# Subscriber mailbox, where a newer state replaces an older one, not in the socket.
SSE_SNDBUF = 256 * 1024
GATE_ACTIONS = ("pause", "resume", "kill")   # "rearm" is CLI-only by policy (harness/agent_gate.py)
GATE_TIMEOUT_S = 3.0
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".json": "application/json", ".map": "application/json",
                 ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
                 ".txt": "text/plain; charset=utf-8"}
MAX_BODY = 64 * 1024
CHAT_MAX_CHARS = 2000
CLILOC_MAX = 200   # numbers per /api/cliloc request
STOREY_Z = 20       # a house piece this high above the house's tile is on an upper floor


class WalkMemDB:
    """Walk memory projection from the harness memory store (read-only use),
    rebuilt at most every CACHE_S seconds."""

    CACHE_S = 2.0

    def __init__(self, path: str):
        self.path = path
        self.lock = threading.Lock()
        self.at = 0.0
        self.body = None

    def get(self) -> bytes | None:
        if not os.path.exists(self.path):
            return None
        with self.lock:
            now = time.monotonic()
            if self.body is None or now - self.at >= self.CACHE_S:
                mem = memory_mod.Memory(self.path)
                try:
                    self.body = mem.walkmem_json(0)
                finally:
                    mem.close()
                self.at = now
            return self.body


class OverseerDB:
    """Overseer chat + junctures and job analytics from the harness memory store
    (docs/OVERSEER.md, harness/jobs.py). One Memory connection, opened lazily and
    shared by the HTTP threads under a lock: WAL readers see every commit, and the
    schema bookkeeping write happens once, not per poll. GETs never create the
    store; POST /api/chat does."""

    JOBS_CACHE_S = 2.0
    PAGE = 200

    def __init__(self, path: str):
        self.path = path
        self.lock = threading.Lock()
        self.mem = None
        self.jobs_cache = {}

    def _open(self, create: bool):
        if self.mem is None and (create or os.path.exists(self.path)):
            self.mem = memory_mod.Memory(self.path)
        return self.mem

    def close(self):
        with self.lock:
            if self.mem is not None:
                self.mem.close()
                self.mem = None

    def jobs(self, job: str, since: float, utc_offset_s: int) -> bytes:
        key = (job, since, utc_offset_s)
        with self.lock:
            now = time.monotonic()
            hit = self.jobs_cache.get(key)
            if hit is not None and now - hit[0] < self.JOBS_CACHE_S:
                return hit[1]
            mem = self._open(False)
            out = jobs_mod.analytics(mem, job, since, utc_offset_s=utc_offset_s,
                                     plan_now=time.time() if job == "lumber" else None)
            out["store"] = mem is not None
            body = json.dumps(out).encode()
            if len(self.jobs_cache) > 16:
                self.jobs_cache.clear()
            self.jobs_cache[key] = (now, body)
            return body

    def overseer(self, after_chat: int, after_juncture: int) -> dict:
        """Rows with id above the cursors, oldest first; a 0 cursor starts at the
        newest PAGE rows. `open`/`open_ids`: junctures not yet acked (any age)."""
        with self.lock:
            mem = self._open(False)
            if mem is None:
                return {"chat": [], "junctures": [], "open": 0, "open_ids": [], "heartbeat": None,
                        "now": time.time(), "store": False}
            c = mem.con
            if after_chat <= 0:
                after_chat = max(0, (c.execute("SELECT MAX(id) FROM chat").fetchone()[0] or 0) - self.PAGE)
            if after_juncture <= 0:
                after_juncture = max(0, (c.execute("SELECT MAX(id) FROM junctures").fetchone()[0] or 0) - self.PAGE)
            open_ids = [i for (i,) in c.execute("SELECT id FROM junctures WHERE acked_t IS NULL ORDER BY id")]
            hb = c.execute("SELECT value FROM meta WHERE key = 'overseer_heartbeat'").fetchone()
            try:
                heartbeat = float(hb[0]) if hb and hb[0] is not None else None
            except ValueError:
                heartbeat = None
            return {"chat": mem.chat(after_chat, limit=self.PAGE),
                    "junctures": mem.junctures(after_juncture, limit=self.PAGE),
                    "open": len(open_ids), "open_ids": open_ids, "heartbeat": heartbeat,
                    "now": time.time(), "store": True}

    def post_chat(self, text: str) -> int:
        with self.lock:
            return self._open(True).chat_post("user", text)

    def captcha_mode(self) -> dict:
        with self.lock:
            mem = self._open(False)
            return {"mode": mem.captcha_mode() if mem else "human", "store": mem is not None}

    def set_captcha_mode(self, mode: str):
        with self.lock:
            self._open(True).set_captcha_mode(mode)


class Handler(BaseHTTPRequestHandler):
    server_version = "uo-viz/1"
    protocol_version = "HTTP/1.1"

    def _paperdoll(self):
        import paperdoll
        try:
            spec = paperdoll.from_state(json.loads(self.server.feed.state_body()))
        except ValueError:
            spec = None
        if spec is None:
            self._json(404, {"error": "no player in the current state"})
            return
        srv = self.server
        try:
            with srv.paperdoll_lock:
                if srv.paperdoll is None:
                    srv.paperdoll = paperdoll.Paperdoll()
                data = srv.paperdoll.png(*spec)
        except (OSError, ValueError) as e:
            self._json(404, {"error": f"paperdoll unavailable: {e}"})
            return
        self._send(200, data, "image/png")

    def _item_art(self, spec: str):
        import uoart
        try:
            graphic = int(spec, 0)
        except ValueError:
            self._json(400, {"error": f"bad graphic {spec!r}"})
            return
        srv = self.server
        try:
            with srv.item_art_lock:
                if srv.item_art is None:
                    srv.item_art = uoart.ItemArt()
                data = srv.item_art.png(graphic)
        except (OSError, ValueError) as e:
            self._json(404, {"error": f"item art unavailable: {e}"})
            return
        if data is None:
            self._json(404, {"error": f"no art for graphic {graphic:#x}"})
            return
        self._send(200, data, "image/png")

    # -- live view (harness/liveview.py): the character, cropped from the game window
    def _live_args(self, q):
        import liveview
        def num(key, default, lo, hi):
            try:
                return max(lo, min(hi, int(q.get(key, [default])[0])))
            except ValueError:
                return default
        return (num("zoom", 2, 1, 3), num("fps", 5, 1, 10),
                num("w", liveview.OUT_WIDTH, liveview.MIN_OUT_WIDTH, liveview.MAX_OUT_WIDTH))

    def _live_source(self):
        srv = self.server
        with srv.live_lock:
            if srv.live is None:
                if srv.live_factory is None:
                    return None, "live view disabled"
                try:
                    srv.live = srv.live_factory()
                except Exception as e:  # noqa: BLE001  (missing windows-capture, non-Windows host)
                    srv.live_factory = None
                    return None, f"live view unavailable: {e}"
            return srv.live, None

    def _live_frame(self, q):
        import liveview
        zoom, _fps, width = self._live_args(q)
        src, err = self._live_source()
        img, info = (None, err) if src is None else src.latest()
        if img is None:
            self._json(503, {"error": info})
            return
        self._send(200, liveview.render_jpeg(img, zoom, width), "image/jpeg")

    def _live_stream(self, q):
        """multipart/x-mixed-replace JPEG stream (an <img> plays it); ends when the
        viewer disconnects or the server stops."""
        import liveview
        zoom, fps, width = self._live_args(q)
        src, err = self._live_source()
        img, info = (None, err) if src is None else src.latest()
        if img is None:
            self._json(503, {"error": info})
            return
        boundary = "uoframe"
        self.send_response(200)
        self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={boundary}")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        if self.command == "HEAD":
            return
        period = 1.0 / fps
        try:
            while not self.server.stopping:
                t0 = time.monotonic()
                img, _age = src.latest()
                if img is not None:
                    data = liveview.render_jpeg(img, zoom, width)
                    self.wfile.write(f"--{boundary}\r\nContent-Type: image/jpeg\r\n"
                                     f"Content-Length: {len(data)}\r\n\r\n".encode() + data + b"\r\n")
                    self.wfile.flush()
                time.sleep(max(0.0, period - (time.monotonic() - t0)))
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass

    def _skillnames(self):
        try:
            import uomap
            names = uomap.skill_names()
        except OSError as e:
            self._json(404, {"error": f"skills.mul unavailable: {e}"})
            return
        self._json(200, {"names": names, "source": "client skills.mul"})

    def _cliloc(self, q):
        try:
            numbers = {int(s) for v in q.get("n", []) for s in v.split(",") if s.strip()}
        except ValueError:
            self._json(400, {"error": "n must be comma-separated cliloc numbers"})
            return
        if len(numbers) > CLILOC_MAX:
            self._json(400, {"error": f"at most {CLILOC_MAX} numbers"})
            return
        try:
            table = cliloc.load()
        except OSError as e:
            self._json(404, {"error": f"Cliloc.enu unavailable: {e}"})
            return
        self._json(200, {"texts": {str(n): table[n] for n in sorted(numbers) if n in table}})

    def _multi(self, spec: str):
        try:
            multi_id = int(spec, 0)
        except ValueError:
            self._json(400, {"error": f"bad multi id {spec!r}"})
            return
        try:
            body = multi_footprint(multi_id)
        except OSError as e:
            self._json(404, {"error": f"multi.mul unavailable: {e}"})
            return
        if body is None:
            self._json(404, {"error": f"no multi {multi_id:#x} in multi.mul"})
            return
        self._json(200, body)

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
                self._json(404, {"error": f"no harness memory store at {self.server.walkmem.path}"})
            else:
                self._send(200, body)
        elif url.path == "/api/paperdoll.png":
            self._paperdoll()
        elif url.path.startswith("/api/art/") and url.path.endswith(".png"):
            self._item_art(url.path[len("/api/art/"):-len(".png")])
        elif url.path.startswith("/api/multi/"):
            self._multi(url.path[len("/api/multi/"):])
        elif url.path == "/api/live.jpg":
            self._live_frame(parse_qs(url.query))
        elif url.path == "/api/live.mjpeg":
            self._live_stream(parse_qs(url.query))
        elif url.path == "/api/skillnames":
            self._skillnames()
        elif url.path == "/api/cliloc":
            self._cliloc(parse_qs(url.query))
        elif url.path == "/api/health":
            self._json(200, feed.health())
        elif url.path == "/api/gate":
            self._gate(None)
        elif url.path == "/api/facet":
            fp = self.server.facet
            self._json(200, fp.meta() if fp else {"available": False, "error": self.server.facet_error})
        elif url.path.startswith("/api/facet/") and url.path.endswith(".png"):
            self._facet_chunk(url.path[len("/api/facet/"):-len(".png")])
        elif url.path == "/api/jobs":
            self._jobs(parse_qs(url.query))
        elif url.path == "/api/overseer":
            self._overseer(parse_qs(url.query))
        elif url.path == "/api/captcha":
            self._json(200, self.server.overseer.captcha_mode())
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

    def _jobs(self, qs: dict):
        job = qs.get("job", ["lumber"])[0]
        try:
            since = float(qs.get("since", ["0"])[0])
            tz = qs.get("tz", [None])[0]
            utc_offset_s = time.localtime().tm_gmtoff if tz is None else int(tz) * 60
        except ValueError:
            self._json(400, {"error": "since must be a number, tz whole minutes east of UTC"})
            return
        if not (0 < len(job) <= 64) or abs(utc_offset_s) > 24 * 3600:
            self._json(400, {"error": "job must be 1..64 chars, |tz| <= 1440"})
            return
        self._send(200, self.server.overseer.jobs(job, since, utc_offset_s))

    def _overseer(self, qs: dict):
        try:
            after_chat = int(qs.get("after_chat", ["0"])[0])
            after_juncture = int(qs.get("after_juncture", ["0"])[0])
        except ValueError:
            self._json(400, {"error": "after_chat / after_juncture must be integers"})
            return
        self._json(200, self.server.overseer.overseer(after_chat, after_juncture))

    def _chat(self, req: dict):
        text = req.get("text")
        if not isinstance(text, str) or not (0 < len(text.strip()) <= CHAT_MAX_CHARS):
            self._json(400, {"ok": False, "error": f"text must be a string of 1..{CHAT_MAX_CHARS} characters"})
            return
        self._json(200, {"ok": True, "id": self.server.overseer.post_chat(text.strip())})

    def _captcha(self, req: dict):
        mode = req.get("mode")
        if mode not in memory_mod.Memory.CAPTCHA_MODES:
            self._json(400, {"ok": False, "error": f"mode must be one of {memory_mod.Memory.CAPTCHA_MODES}"})
            return
        self.server.overseer.set_captcha_mode(mode)
        self._json(200, {"ok": True, "mode": mode})

    def do_POST(self):
        url = urlsplit(self.path)
        if url.path not in ("/api/playback", "/api/gate", "/api/chat", "/api/captcha"):
            self._json(404, {"error": f"unknown route {url.path}"})
            return
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            self.close_connection = True
            self._json(413, {"error": f"body over {MAX_BODY} bytes"})
            return
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            self._json(400, {"error": "bad json"})
            return
        if not isinstance(req, dict):
            self._json(400, {"error": "body must be a JSON object"})
            return
        if url.path == "/api/gate":
            self._gate(req.get("action"))
        elif url.path == "/api/chat":
            self._chat(req)
        elif url.path == "/api/captcha":
            self._captcha(req)
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
        try:
            self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, SSE_SNDBUF)
        except OSError:
            pass
        sub = feed.subscribe(since)
        try:
            self.wfile.write(b"retry: 1000\n\n")
            self.wfile.flush()
            while not sub.closed and not self.server.stopping:
                self.wfile.write((sub.take(SSE_KEEPALIVE_S) or ": keepalive\n\n").encode())
                self.wfile.flush()
        except (ConnectionError, OSError):
            pass
        finally:
            feed.unsubscribe(sub)

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


def multi_footprint(multi_id: int, root: str | None = None) -> dict | None:
    """GET /api/multi/<id>: the tiles a house's pieces cover (uomap.multi_components),
    as [dx, dy, kind] offsets from the house's own tile. kind is "wall" when an
    impassable piece stands on the ground storey there (dz < STOREY_Z), else "floor":
    foundation, steps, and upper floors. None for an id multi.mul doesn't have."""
    import uomap
    root = root or uomap.INSTALL
    parts = uomap.multi_components(multi_id, root)
    if not parts:
        return None
    td = uomap.tiledata(root)
    tiles = {}
    for dx, dy, dz, g in parts:
        it = td.item(g)
        wall = it is not None and bool(it.flags & uomap.IMPASSABLE) and dz < STOREY_Z
        tiles[(dx, dy)] = tiles.get((dx, dy), False) or wall
    return {"id": multi_id, "source": "client multi.mul",
            "tiles": [[dx, dy, "wall" if w else "floor"] for (dx, dy), w in sorted(tiles.items())]}


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

    def __init__(self, addr, feed, dist: str, memory_db: str, facet_path: str | None = None,
                 live_factory=None):
        super().__init__(addr, Handler)
        self.feed = feed
        self.dist = dist
        self.walkmem = WalkMemDB(memory_db)
        self.paperdoll = None            # paperdoll.Paperdoll, created on first use
        self.paperdoll_lock = threading.Lock()
        self.item_art = None             # uoart.ItemArt, created on first use
        self.item_art_lock = threading.Lock()
        self.live = None                 # liveview.LiveCapture, created on first view
        self.live_lock = threading.Lock()
        self.live_factory = live_factory
        self.overseer = OverseerDB(memory_db)
        self.facet = None
        self.facet_error = "disabled (--no-facet)" if facet_path is None else None
        if facet_path is not None:
            try:
                self.facet = facet_mod.FacetPicture(facet_path)
            except (OSError, ValueError, struct.error) as e:
                self.facet_error = f"{facet_path}: {e}"
        self.stopping = False

    def handle_error(self, request, client_address):
        # A browser or phone dropping a keep-alive connection is routine on a LAN; its traceback
        # (one per dropped socket) buried every real error in the log. Everything else still prints.
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        super().handle_error(request, client_address)

    def shutdown(self):
        self.stopping = True
        for sub in list(self.feed.subscribers):
            sub.close()
        super().shutdown()

    def server_close(self):
        super().server_close()
        self.overseer.close()
        with self.live_lock:
            if self.live is not None:
                self.live.stop_locked()


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
    p.add_argument("--memory-db", default=memory_mod.DEFAULT_DB,
                   help="harness memory store: walk layer, overseer chat/junctures, job analytics (docs/MEMORY.md)")
    p.add_argument("--facet", default=facet_mod.DEFAULT_PATH,
                   help="facet picture drawn under the map (read-only; install dir facet00.mul)")
    p.add_argument("--no-facet", action="store_true", help="don't load a facet picture")
    p.add_argument("--no-live", action="store_true", help="disable the live game-window view")
    args = p.parse_args()

    feed = build_feed(args)
    live_factory = None
    if not args.no_live:
        def live_factory():
            import liveview
            return liveview.LiveCapture()
    srv = VizServer((args.host, args.port), feed, os.path.abspath(args.dist), args.memory_db,
                    None if args.no_facet else args.facet, live_factory=live_factory)
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

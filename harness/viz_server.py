"""Visualizer backend (docs/VISUALIZER.md §2): REST + SSE on localhost.

  python harness/viz_server.py --live [--state-port 25942] [--port 8080]
  python harness/viz_server.py --replay 20260929_163420 [--rate 1] [--paused] [--port 8080]

Routes (`?char=` = a character selector, harness/charsel.py: 0xSERIAL, digits, or a name;
live only, replay ignores it; without one the proxy serves its only session or errors):
  GET  /api/state     latest state-port response (+ `viz` block); `events` = the ring (≤2000);
                      ?char= that character's (a feed per selector, made on first use)
  GET  /api/sessions  live: {"ok": true, "sessions": [{tag, serial: "0x%08X"|null, name}]},
                      the proxy's logged-in characters ({"op": "sessions"}); replay: [];
                      502 when the state port is unreachable
  GET  /api/events    SSE (?char= as /api/state): `event: world_events` (a JSON array of envelopes, id = the last
                      seq) + `event: state` (response without events, ≤4 Hz, only when
                      changed); a slow reader gets the newest state, never a backlog of old
                      ones (viz_feed.Subscriber). Resume with the Last-Event-ID header
                      (seq > id) or ?since=N (seq >= N).
  GET  /api/walkmem   walk memory (facet 0) from the harness memory store, in the
                      nav.WalkMemory JSON format (docs/MEMORY.md)
  GET  /api/paperdoll.png  (?char=) the player's paperdoll from the current state (body, skin hue,
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
  GET  /api/health    (?char=) mode, session, order, poll lag, connection, diagnostics
  POST /api/playback  replay only: {"action": "play"|"pause"|"step"|"rate", "rate": R}
  GET  /api/gate      live only: the proxy's agent gate ({"op": "gate"}), verbatim
  POST /api/gate      live only: {"action": "pause"|"resume"|"kill"} -> {"op": "gate",
                      "action": ...}; the proxy's JSON, 200 if ok else 409. Any other
                      action (rearm included: CLI-only, harness/agent_gate.py) -> 400;
                      replay -> 409; state port unreachable -> 502
  GET  /api/facet     facet picture metadata ({"available": false, "error"} without one)
  GET  /api/facet/<cx>/<cy>.png  256x256-tile chunk of the 1 px/tile facet picture
                      (harness/facet.py; read-only from the install dir)
  GET  /api/jobs?job=lumber|hunt[&since=T][&until=T][&tz=M]  job analytics from the memory
                      store over [since, until) epoch s (harness/jobs.py; tz = minutes east of
                      UTC for the per-day split, default the server's local offset), cached 2 s
  GET  /api/jobs/plan  {plan, store}: the lumber optimizer's plan (jobs.lumber_plan, all
                      history), recomputed once a minute; the Jobs page loads it on its own
  GET  /api/overseer?after_chat=N&after_juncture=M[&char=0xS]  {chat, junctures, open, open_ids,
                      heartbeat}: rows with id above the cursors (the newest 200 when 0),
                      the open-juncture count and ids, the overseer's last heartbeat; char
                      (a serial only, else 400): that character's rows + unscoped ones and
                      its overseer's heartbeat; without: every character's, newest heartbeat
  POST /api/chat      {"text": T[, "char": "0xS"]} (1..2000 chars after trimming) -> Memory.chat_post(
                      "user", T, char_serial=S) -> {"ok": true, "id": N}; 400 otherwise
  GET  /api/captcha   {"mode": "human"|"auto", "store": bool}: who answers the harvest
                      captcha (Memory.captcha_mode; "human" when unset or no store)
  POST /api/captcha   {"mode": "human"|"auto"} -> Memory.set_captcha_mode -> {"ok": true,
                      "mode": M}; 400 otherwise. Runners read it at every captcha
  GET  /api/nystul    {conversations: [{id, title, t_updated, messages, running}], available,
                      model, thinking}: Nystul the Wizard, the AI assistant (harness/nystul.py)
  GET  /api/nystul/<id>  {conversation, messages}; a running answer's text and lookup steps
                      are partial and grow between polls; 404 unknown, 400 non-integer id
  POST /api/nystul/ask  {"conversation": id|null, "text": T[, "char": C]} -> {"ok": true, "conversation",
                      "message"}; 400 bad text, 404 unknown conversation, 409 busy, 503 no omp
  POST /api/nystul/cancel  {"conversation": id} -> {"ok": true}; 404 when nothing runs
  POST /api/nystul/approve  {"proposal": id} -> the operator approves a memory-store change
                      Nystul proposed: applied (harness/nystul_memory.py) and shown in the
                      Seer's chat as a system memory row -> {"ok": true, "result": {id, action}};
                      404 unknown, 409 not pending, 400 the store refused it, 503 no store
  GET  /, /assets/*   the built frontend (viz/dist)

Live mode only ever opens the proxy's state port (JSON lines). It never connects
to the control port and never injects or sends anything toward the game server
(ANTICHEAT §8). Its one write toward the proxy is the agent gate: pause/resume/kill
flip the proxy's gate, which only decides whether agent injections on the control
port are rejected. Its writes to the memory store are a user chat row (POST
/api/chat) for the overseer AI to read and the captcha mode (POST /api/captcha).
Nystul runs headless `omp` subprocesses whose only tools are read-only lookups
(harness/nystul_tools.py); it writes its own harness/data/nystul.db, and the memory
store's knowledge only when the operator approves one of its proposals (POST
/api/nystul/approve), with a `system` memory row in the chat.
Everything else is observation.
"""
import argparse
import json
import math
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
import charsel  # noqa: E402
import facet as facet_mod  # noqa: E402
import jobs as jobs_mod  # noqa: E402
import memory as memory_mod  # noqa: E402
import nystul as nystul_mod  # noqa: E402
import nystul_memory  # noqa: E402
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
HEARTBEAT_KEY = "overseer_heartbeat"   # ctl.HEARTBEAT_KEY; per character charsel.meta_key(HEARTBEAT_KEY, s)
SERIAL_ONLY = "char must be a serial (0x...)"


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
    store; POST /api/chat does. The lumber plan has its own connection and lock, so
    its seconds of Monte Carlo never hold up the chat, junctures or job analytics."""

    JOBS_CACHE_S = 2.0
    PLAN_CACHE_S = 60          # the plan's draws are seeded by the minute (jobs.lumber_plan)
    PAGE = 200

    def __init__(self, path: str):
        self.path = path
        self.lock = threading.Lock()
        self.mem = None
        self.jobs_cache = {}
        self.plan_lock = threading.Lock()
        self.plan_mem = None
        self.plan_cache = None     # (minute, body): ~3 s to compute on the 2026-10-05 store

    def _open(self, create: bool):
        if self.mem is None and (create or os.path.exists(self.path)):
            self.mem = memory_mod.Memory(self.path)
        return self.mem

    def close(self):
        with self.lock:
            if self.mem is not None:
                self.mem.close()
                self.mem = None
        with self.plan_lock:
            if self.plan_mem is not None:
                self.plan_mem.close()
                self.plan_mem = None

    def jobs(self, job: str, since: float, until: float | None, utc_offset_s: int) -> bytes:
        key = (job, since, until, utc_offset_s)
        with self.lock:
            now = time.monotonic()
            hit = self.jobs_cache.get(key)
            if hit is not None and now - hit[0] < self.JOBS_CACHE_S:
                return hit[1]
            mem = self._open(False)
            out = jobs_mod.analytics(mem, job, since, until, utc_offset_s=utc_offset_s)
            out["store"] = mem is not None
            body = json.dumps(out).encode()
            if len(self.jobs_cache) > 16:
                self.jobs_cache.clear()
            self.jobs_cache[key] = (now, body)
            return body

    def plan(self) -> bytes:
        """{plan, store}: jobs.lumber_plan at the server's clock, recomputed once per
        wall-clock minute (its seed); plan null without a store. Concurrent callers wait
        for one computation."""
        with self.plan_lock:
            t = time.time()
            minute = int(t // self.PLAN_CACHE_S)
            if self.plan_cache is not None and self.plan_cache[0] == minute:
                return self.plan_cache[1]
            if self.plan_mem is None and os.path.exists(self.path):
                self.plan_mem = memory_mod.Memory(self.path)
            mem = self.plan_mem
            body = json.dumps({"plan": jobs_mod.lumber_plan(mem, t) if mem is not None else None,
                               "store": mem is not None}).encode()
            if mem is not None:
                self.plan_cache = (minute, body)
            return body

    def overseer(self, after_chat: int, after_juncture: int, char_serial: int | None = None) -> dict:
        """Rows with id above the cursors, oldest first; a 0 cursor starts at the
        newest PAGE rows. `open`/`open_ids`: junctures not yet acked (any age).
        `char_serial`: that character's rows plus the unscoped ones (Memory.scope_sql),
        and its heartbeat; None: every character, the newest heartbeat of any."""
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
            scope, params = mem.scope_sql(char_serial)
            open_ids = [i for (i,) in c.execute(
                f"SELECT id FROM junctures WHERE acked_t IS NULL{scope} ORDER BY id", params)]
            if char_serial is None:
                hb = c.execute("SELECT MAX(CAST(value AS REAL)) FROM meta WHERE key = ? OR key LIKE ?",
                               (HEARTBEAT_KEY, HEARTBEAT_KEY + ":%")).fetchone()
            else:
                hb = c.execute("SELECT MAX(CAST(value AS REAL)) FROM meta WHERE key IN (?, ?)",
                               (HEARTBEAT_KEY, charsel.meta_key(HEARTBEAT_KEY, char_serial))).fetchone()
            heartbeat = float(hb[0]) if hb and hb[0] is not None else None
            return {"chat": mem.chat(after_chat, limit=self.PAGE, char_serial=char_serial),
                    "junctures": mem.junctures(after_juncture, limit=self.PAGE, char_serial=char_serial),
                    "open": len(open_ids), "open_ids": open_ids, "heartbeat": heartbeat,
                    "now": time.time(), "store": True}

    def post_chat(self, text: str, char_serial: int | None = None) -> int:
        with self.lock:
            return self._open(True).chat_post("user", text, char_serial=char_serial)

    def captcha_mode(self) -> dict:
        with self.lock:
            mem = self._open(False)
            return {"mode": mem.captcha_mode() if mem else "human", "store": mem is not None}

    def set_captcha_mode(self, mode: str):
        with self.lock:
            self._open(True).set_captcha_mode(mode)

    def knowledge_entry(self, kid: int) -> dict | None:
        """A knowledge entry as Nystul's Approve card shows it (None: no such entry or store)."""
        import knowledge
        with self.lock:
            mem = self._open(False)
            return None if mem is None else nystul_memory.compact(knowledge.Knowledge(mem.con).get(kid))

    def apply_proposal(self, p: dict) -> dict:
        """Apply a Nystul proposal the operator approved (nystul_memory.apply) and show it in the
        Seer's chat as a `system` memory row: visible in the Seer panel, but it doesn't wake
        `ctl wait` (user rows only) or touch the Seer's heartbeat."""
        import knowledge
        with self.lock:
            mem = self._open(False)
            if mem is None:
                raise LookupError(f"no harness memory store at {self.path}")
            result, text, data = nystul_memory.apply(knowledge.Knowledge(mem.con), p)
            mem.chat_post("system", text[:500], "memory", data={"cmd": "nystul", "op": p["op"], **data})
            return result

    def grove(self, spot_id: str | None, facet: int, x: int | None, y: int | None) -> dict:
        """lumber_opt.grove_view of spot `spot_id`, else of the spot whose area holds
        (x, y) on `facet`; spot null when neither names one (or no store / no map files)."""
        import lumber_opt
        with self.lock:
            mem = self._open(False)
            if mem is None:
                return {"spot": None, "store": False}
            spots = lumber_opt.load_spots(mem)
            spot = spots.get(spot_id) if spot_id else (
                lumber_opt.spot_at(spots, facet, x, y) if x is not None and y is not None else None)
            if spot is None or not spot.get("area"):
                return {"spot": None, "store": True}
            view = lumber_opt.grove_view(mem, spot, time.time())
            if view is None:
                return {"spot": None, "store": True, "error": f"no map files for facet {spot.get('facet') or 0}"}
            return {**view, "store": True}


class Handler(BaseHTTPRequestHandler):
    server_version = "uo-viz/1"
    protocol_version = "HTTP/1.1"

    def _feed(self, url):
        """The feed `?char=` names (VizServer.feed_for); the default feed without one."""
        return self.server.feed_for((parse_qs(url.query).get("char") or [None])[0])

    def _paperdoll(self, url):
        import paperdoll
        try:
            spec = paperdoll.from_state(json.loads(self._feed(url).state_body()))
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
        if url.path == "/api/state":
            self._send(200, self._feed(url).state_body().encode())
        elif url.path == "/api/events":
            self._sse(url)
        elif url.path == "/api/walkmem":
            body = self.server.walkmem.get()
            if body is None:
                self._json(404, {"error": f"no harness memory store at {self.server.walkmem.path}"})
            else:
                self._send(200, body)
        elif url.path == "/api/paperdoll.png":
            self._paperdoll(url)
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
            self._json(200, self._feed(url).health())
        elif url.path == "/api/gate":
            self._gate(None)
        elif url.path == "/api/sessions":
            self._sessions()
        elif url.path == "/api/facet":
            fp = self.server.facet
            self._json(200, fp.meta() if fp else {"available": False, "error": self.server.facet_error})
        elif url.path.startswith("/api/facet/") and url.path.endswith(".png"):
            self._facet_chunk(url.path[len("/api/facet/"):-len(".png")])
        elif url.path == "/api/jobs":
            self._jobs(parse_qs(url.query))
        elif url.path == "/api/jobs/plan":
            self._send(200, self.server.overseer.plan())
        elif url.path == "/api/lumber/grove":
            self._grove(parse_qs(url.query))
        elif url.path == "/api/overseer":
            self._overseer(parse_qs(url.query))
        elif url.path == "/api/captcha":
            self._json(200, self.server.overseer.captcha_mode())
        elif url.path == "/api/nystul":
            self._json(200, self.server.nystul.list())
        elif url.path.startswith("/api/nystul/"):
            self._nystul_get(url.path[len("/api/nystul/"):])
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
            until = qs.get("until", [None])[0]
            until = None if until in (None, "") else float(until)
            tz = qs.get("tz", [None])[0]
            utc_offset_s = time.localtime().tm_gmtoff if tz is None else int(tz) * 60
        except ValueError:
            self._json(400, {"error": "since/until must be numbers, tz whole minutes east of UTC"})
            return
        if not (0 < len(job) <= 64) or abs(utc_offset_s) > 24 * 3600:
            self._json(400, {"error": "job must be 1..64 chars, |tz| <= 1440"})
            return
        if not math.isfinite(since) or (until is not None and not (math.isfinite(until) and until > since)):
            self._json(400, {"error": "since/until must be finite, until after since"})
            return
        self._send(200, self.server.overseer.jobs(job, since, until, utc_offset_s))

    def _grove(self, qs: dict):
        spot = qs.get("spot", [None])[0] or None
        try:
            facet = int(qs.get("facet", ["0"])[0])
            x, y = (None if qs.get(k, [""])[0] == "" else int(qs[k][0]) for k in ("x", "y"))
        except ValueError:
            self._json(400, {"error": "facet/x/y must be integers"})
            return
        if spot is None and (x is None or y is None):
            self._json(400, {"error": "give spot=<id> or x and y (and facet)"})
            return
        self._json(200, self.server.overseer.grove(spot, facet, x, y))

    def _char_serial(self, raw) -> tuple[bool, int | None]:
        """(ok, serial) of a `char` the overseer routes take: absent/empty → (True, None),
        a serial selector → (True, it); anything else answers 400 and gives (False, None)."""
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return True, None
        try:
            p = charsel.parse(raw) if isinstance(raw, str) else None
        except ValueError:
            p = None
        if not isinstance(p, int):
            self._json(400, {"ok": False, "error": SERIAL_ONLY})
            return False, None
        return True, p

    def _overseer(self, qs: dict):
        try:
            after_chat = int(qs.get("after_chat", ["0"])[0])
            after_juncture = int(qs.get("after_juncture", ["0"])[0])
        except ValueError:
            self._json(400, {"error": "after_chat / after_juncture must be integers"})
            return
        ok, char_serial = self._char_serial(qs.get("char", [None])[0])
        if ok:
            self._json(200, self.server.overseer.overseer(after_chat, after_juncture, char_serial))

    def _chat(self, req: dict):
        text = req.get("text")
        if not isinstance(text, str) or not (0 < len(text.strip()) <= CHAT_MAX_CHARS):
            self._json(400, {"ok": False, "error": f"text must be a string of 1..{CHAT_MAX_CHARS} characters"})
            return
        ok, char_serial = self._char_serial(req.get("char"))
        if ok:
            self._json(200, {"ok": True, "id": self.server.overseer.post_chat(text.strip(), char_serial)})

    def _captcha(self, req: dict):
        mode = req.get("mode")
        if mode not in memory_mod.Memory.CAPTCHA_MODES:
            self._json(400, {"ok": False, "error": f"mode must be one of {memory_mod.Memory.CAPTCHA_MODES}"})
            return
        self.server.overseer.set_captcha_mode(mode)
        self._json(200, {"ok": True, "mode": mode})

    def _nystul_get(self, spec: str):
        try:
            conv = int(spec)
        except ValueError:
            self._json(400, {"error": "conversation id must be an integer"})
            return
        out = self.server.nystul.get(conv)
        if out is None:
            self._json(404, {"error": "no such conversation"})
        else:
            self._json(200, out)

    def _nystul_ask(self, req: dict):
        conv, text, char = req.get("conversation"), req.get("text"), req.get("char")
        if conv is not None and (isinstance(conv, bool) or not isinstance(conv, int)):
            self._json(400, {"ok": False, "error": "conversation must be an integer or null"})
            return
        if not isinstance(text, str):
            self._json(400, {"ok": False, "error": "text must be a string"})
            return
        if char is not None and not isinstance(char, str):
            self._json(400, {"ok": False, "error": "char must be a string or null"})
            return
        try:
            out = self.server.nystul.ask(conv, text, (char or "").strip() or None)
        except nystul_mod.NystulError as e:
            self._json(e.code, {"ok": False, "error": str(e)})
            return
        self._json(200, {"ok": True, **out})

    def _nystul_approve(self, req: dict):
        pid = req.get("proposal")
        if isinstance(pid, bool) or not isinstance(pid, int):
            self._json(400, {"ok": False, "error": "proposal must be an integer"})
            return
        try:
            result = self.server.nystul.approve(pid)
        except nystul_mod.NystulError as e:
            self._json(e.code, {"ok": False, "error": str(e)})
            return
        self._json(200, {"ok": True, "result": result})

    def _nystul_cancel(self, req: dict):
        conv = req.get("conversation")
        if isinstance(conv, bool) or not isinstance(conv, int):
            self._json(400, {"ok": False, "error": "conversation must be an integer"})
        elif self.server.nystul.cancel(conv):
            self._json(200, {"ok": True})
        else:
            self._json(404, {"ok": False, "error": "nothing running"})

    def do_POST(self):
        url = urlsplit(self.path)
        if url.path not in ("/api/playback", "/api/gate", "/api/chat", "/api/captcha", "/api/nystul/ask",
                            "/api/nystul/cancel", "/api/nystul/approve"):
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
        elif url.path == "/api/nystul/ask":
            self._nystul_ask(req)
        elif url.path == "/api/nystul/cancel":
            self._nystul_cancel(req)
        elif url.path == "/api/nystul/approve":
            self._nystul_approve(req)
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
            resp = state_request(feed.host, feed.port,
                                 {"op": "gate"} if action is None else {"op": "gate", "action": action})
        except (OSError, ValueError) as e:
            self._json(502, {"ok": False, "error": f"state port unreachable: {type(e).__name__}: {e}"})
            return
        if action is not None:
            for f in self.server.feeds():
                f.wake()   # push the new gate in the next state frame
        self._json(200 if resp.get("ok") else 409, resp)

    def _sessions(self):
        """GET /api/sessions: the proxy's logged-in characters ({"op": "sessions"}) on a
        short-lived state-port connection; replay has none."""
        feed = self.server.feed
        if not isinstance(feed, viz_feed.StatePortPoller):
            self._json(200, {"ok": True, "sessions": []})
            return
        try:
            resp = state_request(feed.host, feed.port, {"op": "sessions"})
        except (OSError, ValueError) as e:
            self._json(502, {"ok": False, "error": f"proxy state port unreachable: {type(e).__name__}: {e}"})
            return
        if not resp.get("ok"):
            self._json(502, {"ok": False, "error": f"proxy: {resp.get('error')}"})
            return
        self._json(200, {"ok": True, "sessions": resp.get("sessions") or []})

    def _sse(self, url):
        feed = self._feed(url)
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


def state_request(host: str, port: int, req: dict) -> dict:
    """One request on a fresh state-port connection (the feed's connection and since
    cursor stay untouched)."""
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
                 live_factory=None, *, state_port: int = 25942, nystul_db: str = nystul_mod.DEFAULT_DB,
                 nystul_model: str = "sonnet", nystul_thinking: str = "medium"):
        super().__init__(addr, Handler)
        self.feed = feed
        self.char_feeds: dict = {}       # charsel.parse(char) -> StatePortPoller for ?char=
        self.char_feeds_lock = threading.Lock()
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
        # Nystul's tools read the store, this server's read routes and ctl's read-only subset
        host = self.server_address[0]
        host = "127.0.0.1" if host in ("", "0.0.0.0", "::") else host
        self.nystul = nystul_mod.Nystul(
            nystul_db, model=nystul_model, thinking=nystul_thinking,
            env={"NYSTUL_MEMORY_DB": os.path.abspath(memory_db),
                 "NYSTUL_VIZ": f"http://{host}:{self.server_address[1]}",
                 "NYSTUL_STATE_PORT": str(state_port)},
            context=lambda: {k: feed.health().get(k) for k in ("mode", "session")},
            lookup=self.overseer.knowledge_entry, apply=self.overseer.apply_proposal)

    def handle_error(self, request, client_address):
        # A browser or phone dropping a keep-alive connection is routine on a LAN; its traceback
        # (one per dropped socket) buried every real error in the log. Everything else still prints.
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        super().handle_error(request, client_address)

    def feed_for(self, char: str | None):
        """The feed of character `char` (a charsel selector): `self.feed` without one, in
        replay (which has one character), or for a selector that doesn't parse; else a
        StatePortPoller asking the proxy for that character, created and pumped once on
        first use, kept until the server closes."""
        feed = self.feed
        if not char or not isinstance(feed, viz_feed.StatePortPoller):
            return feed
        try:
            key = charsel.parse(char)
        except ValueError:
            return feed
        with self.char_feeds_lock:
            f = self.char_feeds.get(key)
            if f is None:
                f = viz_feed.StatePortPoller(feed.host, feed.port, char=char.strip())
                f.pump()   # the first answer (state or the proxy's error) is there for this request
                f.run()
                self.char_feeds[key] = f
            return f

    def feeds(self) -> list:
        with self.char_feeds_lock:
            return [self.feed, *self.char_feeds.values()]

    def shutdown(self):
        self.stopping = True
        for f in self.feeds():
            for sub in list(f.subscribers):
                sub.close()
        super().shutdown()

    def server_close(self):
        super().server_close()
        with self.char_feeds_lock:
            for f in self.char_feeds.values():
                f.stop()
        self.overseer.close()
        self.nystul.close()
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
    p.add_argument("--nystul-db", default=nystul_mod.DEFAULT_DB, help="Nystul the Wizard's conversations")
    p.add_argument("--nystul-model", default="sonnet", help="omp model for Nystul the Wizard")
    p.add_argument("--nystul-thinking", default="medium", help="omp thinking level for Nystul the Wizard")
    args = p.parse_args()

    feed = build_feed(args)
    live_factory = None
    if not args.no_live:
        def live_factory():
            import liveview
            return liveview.LiveCapture()
    srv = VizServer((args.host, args.port), feed, os.path.abspath(args.dist), args.memory_db,
                    None if args.no_facet else args.facet, live_factory=live_factory, state_port=args.state_port,
                    nystul_db=args.nystul_db, nystul_model=args.nystul_model, nystul_thinking=args.nystul_thinking)
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

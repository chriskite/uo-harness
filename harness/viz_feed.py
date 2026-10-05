"""Data feeds for the visualizer (docs/VISUALIZER.md §2).

Both feeds produce state-port responses (`SessionTap.state()` shape, event
envelopes `{"seq", "t", "origin", "data"}`) and merge them into a `Feed`:
a ring of the last RING envelopes plus the latest response without events
(pre-serialized, swapped atomically), fanned out to one `Subscriber` mailbox
per SSE connection. A pump thread refreshes it at most PUMP_HZ times a second,
and a new state goes out only when the serialized state changed.

A mailbox never queues states: it holds the newest one only, plus the
envelopes not yet written (bounded by SUB_EVENTS_MAX; a connection that falls
that far behind loses superseded entity churn and the oldest CHURN_EVS
first). Each write sends all of it as one `world_events` frame and one
`state` frame, so a slow reader skips intermediate states instead of falling
behind (docs/NOTES.md "Viz lag": a 1.8 MB state at ~3/s queued 20000 frames
deep put a LAN reader minutes behind the game).

- StatePortPoller(host, port): LIVE. Polls the proxy's JSON-lines state port
  with the `since` cursor. Read-only: it never opens the control port.
  Tolerates the proxy being down (viz.connected=false, reconnects).
- ReplayDriver(tag, logdir): REPLAY. Runs the proxy's own SessionTap offline
  over a capture (logs/session_<tag>.{jsonl,c2s.raw,s2c.raw}), so its state is
  what the live state port served.
  * order "exact" (captures by the fixed proxy): the jsonl rows map 1:1 onto
    the framed raw C2S packets and the uo.s2c.S2CStream S2C packets. The jsonl
    row order is the proxy's processing order; each row's `t` becomes the
    simulated clock and its `src` the sender. Every row is checked against its
    packet (id, len, hex). Movement timers: the live proxy ticks MoveAuthority
    on a drifting 0.1 s timer and on every state-port poll, at unrecorded
    moments, and a re-anchor due within a 30 ms window depends on that phase
    (163420: a fixed 0.1 s grid adds a third re-anchor). Every timer decision
    that changed state IS recorded (walk_rejected, resync_ignored,
    reanchor_client rows), so the driver ticks the tap exactly then (plus
    before each agent packet, as InjectionHub.inject does); rejections and
    re-anchors happen as they did live. A recorded decision the tap does not
    reproduce is counted in `timer_divergences`.
  * order "approx" (older captures: pre-fix S2C rows, empty c2s.raw, or any
    row mismatch): canonical order, prelude + all C2S + all S2C (as
    replay.py), no timers. The mismatch is printed and kept in `order_note`.
  Known limit of exact replay: raw C2S is post-rewrite, so a client walk's
  original seq/key is gone; client-side `c2s_token_*` and
  `s2c_confirm_rewritten` events are not reproduced (agent ones are).
"""
import collections
import json
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pathfind  # noqa: E402
import proxy  # noqa: E402
from uo.packets import packet_length, C2S_OVERRIDES  # noqa: E402
from uo.s2c import PRELUDE_LEN, S2CStream, prelude_keys  # noqa: E402

RING = 2000            # envelopes kept for /api/state and Last-Event-ID resume
PUMP_HZ = 4            # state refreshes / SSE state pushes per second, at most
SUB_EVENTS_MAX = RING  # unsent envelopes one SSE connection may hold before compaction
# Envelopes about one serial whose current truth the state frame carries: of these, a
# compaction keeps only the newest per serial.
ENTITY_EVS = frozenset({"item_seen", "prune", "delete", "query", "item_query", "animation"})
# High-volume events a lagging connection loses first (oldest first): entity churn, walk
# bookkeeping, per-packet summaries and keepalives, all of which the state frame (movement,
# world, traffic) carries. Speech, cliloc, gumps, targets, containers, intents, deaths and
# the rest are never dropped while these remain.
CHURN_EVS = ENTITY_EVS | frozenset({
    "names", "sound", "keepalive", "walk", "walk_confirm", "walk_deny", "step", "c2s",
    "s2c_confirm_hidden", "s2c_confirm_rewritten", "reanchor_client", "c2s_token_stamped",
    "c2s_token_passthrough", "c2s_resync_seen"})
POLL_TIMEOUT_S = 5.0
RETRY_S = 1.0
TIMER_EVENTS = ("walk_rejected", "resync_ignored", "reanchor_client")  # jsonl rows of timer decisions
IDLE_SKIP_S = 2.0      # replay playback: longer quiet gaps (sim time) are shortened to this
APPROX_DT = 0.01       # approx order: synthetic spacing between packets (s)


# ------------------------------------------------------------------- feed base

class Feed:
    """Ring + latest state + SSE fanout. Subclasses implement query() and viz()."""

    mode = None

    def __init__(self):
        self.lock = threading.Lock()
        self.ring = collections.deque(maxlen=RING)
        self.cursor = 0            # next seq to request
        self.state_json = None     # latest response without events, + viz block
        self.last_ok = None        # wall time of the last successful pump
        self.last_error = None
        self.subscribers = set()   # Subscriber mailboxes, one per SSE connection
        self.sse_dropped = 0       # envelopes compacted away for lagging connections
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None

    # -- subclass API
    def query(self, since: int) -> dict:
        """One state-port response with events seq >= since. Raises OSError when unreachable."""
        raise NotImplementedError

    def viz(self) -> dict:
        raise NotImplementedError

    # -- pump
    def pump(self):
        """Fetch new events + state and publish them."""
        try:
            resp = self.query(self.cursor)
            if resp.get("ok") and resp.get("next", 0) < self.cursor:
                # the proxy's game session restarted: seqs start again at 0
                with self.lock:
                    self.ring.clear()
                    self.cursor = 0
                resp = self.query(0)
            self.last_ok = time.time()
            self.last_error = None if resp.get("ok") else resp.get("error")
        except OSError as e:
            self.last_error = f"{type(e).__name__}: {e}"
            resp = {"ok": False, "error": f"state port unreachable: {self.last_error}"}
        self._publish(resp)

    def _publish(self, resp: dict):
        events = resp.pop("events", None) or []
        with self.lock:
            fresh = [e for e in events if e["seq"] >= self.cursor]
            self.ring.extend(fresh)
            if resp.get("ok"):
                self.cursor = resp["next"]
            if "viz" not in resp:  # a replay query carries its own, consistent with the state
                resp["viz"] = self.viz()
            sj = json.dumps(resp)
            batch = [pending(e) for e in fresh]
            state = None
            if sj != self.state_json:
                self.state_json = state = sj
            if batch or state is not None:
                for sub in self.subscribers:
                    self.sse_dropped += sub.put(batch, state)

    def run(self):
        """Start the pump thread (≤ PUMP_HZ)."""
        self._thread = threading.Thread(target=self._pump_loop, name=f"{self.mode}-pump", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def wake(self):
        """Pump soon (still rate limited)."""
        self._wake.set()

    def _pump_loop(self):
        period = 1.0 / PUMP_HZ
        while not self._stop.is_set():
            start = time.monotonic()
            self.pump()
            wait = RETRY_S if self.last_error else period
            self._wake.wait(wait)
            self._wake.clear()
            rest = period - (time.monotonic() - start)
            if rest > 0:
                time.sleep(rest)

    # -- readers
    def state_body(self) -> str:
        """JSON of GET /api/state: latest state + the ring as `events`."""
        with self.lock:
            sj = self.state_json
            ev = json.dumps(list(self.ring))
        if sj is None:
            return json.dumps({"ok": False, "error": "no data yet", "events": [], "viz": self.viz()})
        return '{"events": ' + ev + ", " + sj[1:]

    def subscribe(self, since: int | None):
        """Register an SSE subscriber. Returns its mailbox, pre-loaded with the
        ring suffix seq >= since (whole ring if since is None or beyond `next`)
        and the current state, atomically with publishing (no gaps/dups)."""
        sub = Subscriber()
        with self.lock:
            if since is None or since > self.cursor:
                since = 0
            sub.put([pending(e) for e in self.ring if e["seq"] >= since], self.state_json)
            self.subscribers.add(sub)
        return sub

    def unsubscribe(self, sub):
        with self.lock:
            self.subscribers.discard(sub)

    def health(self) -> dict:
        with self.lock:
            first = self.ring[0]["seq"] if self.ring else None
            diag = json.loads(self.state_json).get("diagnostics") if self.state_json else None
            subs = len(self.subscribers)
        return {"ok": True, **self.viz(),
                "poll_lag": None if self.last_ok is None else round(time.time() - self.last_ok, 3),
                "last_error": self.last_error, "ring": {"first": first, "next": self.cursor},
                "sse_clients": subs, "sse_dropped": self.sse_dropped, "diagnostics": diag}


def pending(env: dict) -> tuple:
    """(seq, ev, serial, JSON) of an envelope: serialized once for every mailbox."""
    d = env.get("data") or {}
    return env["seq"], d.get("ev"), d.get("serial"), json.dumps(env)


def compact(events: list) -> list:
    """A lagging connection's unsent envelopes cut to SUB_EVENTS_MAX // 2, order
    kept: entity churn superseded by a newer ENTITY_EVS envelope about the same
    serial goes first, then the oldest CHURN_EVS, and only if the rest is still
    over SUB_EVENTS_MAX, the oldest of what remains."""
    target = SUB_EVENTS_MAX // 2
    seen, kept = set(), []
    for p in reversed(events):
        if p[1] in ENTITY_EVS and p[2] is not None:
            if p[2] in seen:
                continue
            seen.add(p[2])
        kept.append(p)
    kept.reverse()
    extra = len(kept) - target
    if extra > 0:
        out = []
        for p in kept:
            if extra > 0 and p[1] in CHURN_EVS:
                extra -= 1
                continue
            out.append(p)
        kept = out
    return kept[-target:] if len(kept) > SUB_EVENTS_MAX else kept


class Subscriber:
    """One SSE connection's unsent output: envelopes in order (compacted beyond
    SUB_EVENTS_MAX) and only the newest state, which replaces an unsent older one."""

    def __init__(self):
        self._cv = threading.Condition()
        self._events = []          # pending() tuples, oldest first
        self._state = None         # newest unsent state JSON
        self.closed = False

    def __len__(self):
        return len(self._events) + (self._state is not None)

    def put(self, events: list, state: str | None = None) -> int:
        """Queue envelopes and/or a newer state; returns how many envelopes compaction dropped."""
        with self._cv:
            self._events.extend(events)
            dropped = 0
            if len(self._events) > SUB_EVENTS_MAX:
                kept = compact(self._events)
                dropped = len(self._events) - len(kept)
                self._events = kept
            if state is not None:
                self._state = state
            self._cv.notify()
            return dropped

    def close(self):
        with self._cv:
            self.closed = True
            self._cv.notify()

    def take(self, timeout: float) -> str:
        """SSE text of everything unsent ("" when nothing came within timeout): one
        `world_events` frame (a JSON array of envelopes, id = the last seq), then
        the newest `state`."""
        with self._cv:
            if not self._events and self._state is None and not self.closed:
                self._cv.wait(timeout)
            events, state = self._events, self._state
            self._events, self._state = [], None
        out = ""
        if events:
            out = f"id: {events[-1][0]}\nevent: world_events\ndata: [{','.join(p[3] for p in events)}]\n\n"
        if state is not None:
            out += f"event: state\ndata: {state}\n\n"
        return out


# ------------------------------------------------------------------------ live

class StatePortPoller(Feed):
    """LIVE feed: the proxy's state port (JSON lines), polled with the since cursor."""

    mode = "live"

    def __init__(self, host: str = "127.0.0.1", port: int = 25942):
        super().__init__()
        self.host, self.port = host, port
        self.sock = None
        self.rfile = None
        self.connected = False

    def viz(self) -> dict:
        return {"mode": "live", "session": None, "order": None, "playback": None,
                "connected": self.connected}

    def _close(self):
        for f in (self.rfile, self.sock):
            try:
                if f is not None:
                    f.close()
            except OSError:
                pass
        self.sock = self.rfile = None
        self.connected = False

    def query(self, since: int) -> dict:
        try:
            if self.sock is None:
                self.sock = socket.create_connection((self.host, self.port), timeout=POLL_TIMEOUT_S)
                self.rfile = self.sock.makefile("rb")
            self.sock.sendall((json.dumps({"op": "state", "since": since}) + "\n").encode())
            line = self.rfile.readline()
            if not line:
                raise ConnectionResetError("state port closed the connection")
            resp = json.loads(line)
        except (OSError, ValueError) as e:
            self._close()
            raise OSError(str(e)) from e
        self.connected = True
        evs = resp.get("events")
        if evs and "seq" not in evs[0]:
            # a proxy started before the §1.4 envelope: bare world events at index since + i
            t = round(time.time(), 3)
            resp["events"] = [{"seq": since + i, "t": t, "origin": "world", "data": e}
                              for i, e in enumerate(evs)]
        return resp

    def stop(self):
        super().stop()
        self._close()


# ---------------------------------------------------------------------- replay

class _Null:
    """Stand-in for the proxy's jsonl/raw capture files."""

    def write(self, data):
        return len(data)

    def flush(self):
        pass

    def close(self):
        pass


def _frame_c2s(plain: bytes) -> list[bytes]:
    """Framed plaintext C2S packets (the proxy's framing; stops at an
    incomplete or implausible packet)."""
    pkts, i = [], 0
    while i < len(plain):
        n = packet_length(plain[i:], overrides=C2S_OVERRIDES)
        if n <= 0:
            break
        pkts.append(plain[i:i + n])
        i += n
    return pkts


def _row_matches(row: dict, pkt: bytes) -> bool:
    return (row.get("id") == f"0x{pkt[0]:02X}" and row.get("len") == len(pkt)
            and row.get("hex") == proxy.hexd(pkt))


class ReplayMismatch(ValueError):
    pass


class ReplayDriver(Feed):
    """REPLAY feed: an offline proxy SessionTap driven by a capture.

    Timeline items: (t, kind, a, b) with kind "c2s_wire" (client bytes through
    the tap's framing), "c2s" (a=src, b=plaintext packet), "s2c" (a=wire),
    "timer" (a=the recorded timer decision's ev name) or "intent" (a=the
    agent intent the runner reported on the state port, exact order only).
    """

    mode = "replay"

    def __init__(self, tag: str, logdir: str = "logs"):
        super().__init__()
        self.tag = tag
        base = os.path.join(logdir, f"session_{tag}")
        s2c_raw = open(base + ".s2c.raw", "rb").read()
        c2s_raw = open(base + ".c2s.raw", "rb").read() if os.path.exists(base + ".c2s.raw") else b""
        rows = []
        if os.path.exists(base + ".jsonl"):
            with open(base + ".jsonl", encoding="utf-8") as f:
                for line in f:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        break  # capture still being written: partial last line
        if len(s2c_raw) < PRELUDE_LEN:
            raise ValueError(f"{base}.s2c.raw: no S2C prelude")
        s2c_key, self.c2s_key = prelude_keys(s2c_raw[:PRELUDE_LEN])
        self.s2c_pkts = S2CStream(s2c_key).feed(s2c_raw[PRELUDE_LEN:])  # [(wire, pkt)]
        self.prelude = s2c_raw[:PRELUDE_LEN]
        self.preamble = c2s_raw[:proxy.CLIENT_PREAMBLE_LEN]
        self.c2s_pkts = _frame_c2s(bytes(b ^ self.c2s_key for b in c2s_raw[proxy.CLIENT_PREAMBLE_LEN:]))
        self.order_note = None
        try:
            if not self.c2s_pkts:
                raise ReplayMismatch("c2s.raw is empty")
            self.items, self.t_end = self._exact(rows)
            self.order = "exact"
        except ReplayMismatch as e:
            self.order_note = f"exact interleave unavailable ({e}); replaying in canonical order"
            print(f"[viz_feed] {tag}: {self.order_note}", file=sys.stderr)
            self.items, self.t_end = self._approx(rows)
            self.order = "approx"
        self.tap = proxy.SessionTap(_Null(), _Null(), _Null(), walkers=pathfind.Walkers())
        # a capture whose re-anchors all came after quiet (made before the per-step re-anchor,
        # ANTICHEAT.md §10 A11) replays with that behaviour, so its recorded decisions reproduce
        reanchors = [r for r in rows if r.get("ev") == "reanchor_client"]
        self.tap.reanchor_on_confirm = not reanchors or any(r.get("on") == "confirm" for r in reanchors)
        self.t0 = self.items[0][0] if self.items else 0.0
        self.now = self.t0
        self.tap.wall = self.tap.mono = lambda: self.now
        self.timer_divergences = 0      # recorded timer decisions the tap did not reproduce
        self.position = 0
        self.playing = False
        self.rate = 1.0
        self.sim = threading.RLock()    # guards tap + playback position
        self._play_thread = None

    # -- timeline construction
    def _exact(self, rows):
        items, ci, si = [], 0, 0
        t_end = None
        truncated = None
        for n, row in enumerate(rows):
            t = row.get("t")
            if t is None:
                continue
            ev, d = row.get("ev"), row.get("dir")
            if ev == "c2s_preamble":
                if row.get("hex") != self.preamble.hex():
                    raise ReplayMismatch(f"row {n}: client preamble differs from c2s.raw")
                items.append((t, "c2s_wire", self.preamble, None))
            elif ev == "s2c_prelude":
                if row.get("hex") != self.prelude.hex():
                    raise ReplayMismatch(f"row {n}: prelude differs from s2c.raw")
                items.append((t, "s2c", self.prelude, None))
            elif d == "c2s":
                if ci >= len(self.c2s_pkts):
                    truncated = f"row {n}: c2s rows outrun c2s.raw"
                    break
                pkt = self.c2s_pkts[ci]
                if not _row_matches(row, pkt):
                    raise ReplayMismatch(f"row {n}: c2s packet {ci} is {pkt[:1].hex()}/{len(pkt)}, "
                                         f"row says {row.get('id')}/{row.get('len')}")
                items.append((t, "c2s", row.get("src", "client"), pkt))
                ci += 1
            elif d == "s2c" and row.get("src") != "proxy":  # proxy rows: the tap re-fabricates them
                if si >= len(self.s2c_pkts):
                    truncated = f"row {n}: s2c rows outrun s2c.raw"
                    break
                wire, pkt = self.s2c_pkts[si]
                if not _row_matches(row, pkt):
                    raise ReplayMismatch(f"row {n}: s2c packet {si} is {pkt[:1].hex()}/{len(pkt)}, "
                                         f"row says {row.get('id')}/{row.get('len')}")
                items.append((t, "s2c", wire, None))
                si += 1
            elif ev in TIMER_EVENTS and row.get("on") != "confirm":   # on-confirm re-anchors come from the s2c row
                items.append((t, "timer", ev, None))
            elif ev == "agent_intent":
                items.append((t, "intent", row.get("intent"), None))
            t_end = t
        if not any(it[1] == "s2c" and it[2] is self.prelude for it in items):
            raise ReplayMismatch("no s2c_prelude row")
        left = (len(self.c2s_pkts) - ci, len(self.s2c_pkts) - si)
        if truncated or any(left):
            self.order_note = (f"capture tail not replayed ({truncated or 'rows end first'}; "
                               f"unmatched packets c2s={left[0]} s2c={left[1]})")
        return items, t_end

    def _approx(self, rows):
        t0 = next((r["t"] for r in rows if "t" in r), 0.0)
        c2s_rows = [r for r in rows if r.get("dir") == "c2s"]
        srcs = ([r.get("src", "client") for r in c2s_rows]
                if len(c2s_rows) == len(self.c2s_pkts)
                and all(r.get("id") == f"0x{p[0]:02X}" and r.get("len") == len(p)
                        for r, p in zip(c2s_rows, self.c2s_pkts))
                else ["client"] * len(self.c2s_pkts))
        seq = [("s2c", self.prelude, None)]
        if self.preamble:
            seq.append(("c2s_wire", self.preamble, None))
        seq += [("c2s", s, p) for s, p in zip(srcs, self.c2s_pkts)]
        seq += [("s2c", w, None) for w, _ in self.s2c_pkts]
        items = [(round(t0 + i * APPROX_DT, 3), k, a, b) for i, (k, a, b) in enumerate(seq)]
        return items, items[-1][0]

    # -- simulation
    def _apply(self, item):
        t, kind, a, b = item
        self.now = max(self.now, t)
        tap = self.tap
        if kind == "c2s_wire":
            tap.tap_c2s(a)
        elif kind == "s2c":
            tap.tap_s2c(a)
        elif kind == "intent":
            tap.set_intent(a)
        elif kind == "timer":  # proxy._movement_timer: tick, then re-anchor when due
            mark = tap.events_base + len(tap.events)
            tap.tick(self.now)
            if a == "reanchor_client":
                ok = bool(tap.reanchor_client(self.now))
            else:
                ok = any(e["data"]["ev"] == a for e in tap.events[max(mark - tap.events_base, 0):])
            if not ok:
                self.timer_divergences += 1
        elif a == "client":
            tap.tap_c2s(bytes(x ^ tap.key for x in b))
        else:  # injected: InjectionHub.inject ticks first, then processes it
            tap.tick(self.now)
            tap.inject_c2s(b, a, self.now)

    def _advance(self, limit: float | None, max_items: int) -> int:
        """Apply items with t <= limit (all if None), at most max_items; returns count."""
        n = 0
        total = len(self.items)
        while self.position < total and n < max_items:
            item = self.items[self.position]
            if limit is not None and item[0] > limit:
                break
            self._apply(item)
            self.position += 1
            n += 1
        if self.position >= total:
            self.now = max(self.now, self.t_end)
            self.playing = False
        return n

    def step(self) -> bool:
        """Apply exactly one timeline item (pauses playback). False at the end."""
        with self.sim:
            self.playing = False
            if self.position >= len(self.items):
                return False
            self._advance(None, 1)
        self.wake()
        return True

    def run_to_end(self):
        """Apply everything now (max rate)."""
        with self.sim:
            self.playing = False
            while self._advance(None, 10000):
                pass
        self.wake()

    def play(self):
        with self.sim:
            if self.position < len(self.items):
                self.playing = True
        self._ensure_player()
        self.wake()

    def pause(self):
        with self.sim:
            self.playing = False
        self.wake()

    def set_rate(self, rate: float):
        if not rate > 0:
            raise ValueError("rate must be > 0")
        with self.sim:
            self.rate = float(rate)
        self.wake()

    def _ensure_player(self):
        if self._play_thread is None:
            self._play_thread = threading.Thread(target=self._play_loop, name="replay-play", daemon=True)
            self._play_thread.start()

    def _play_loop(self):
        last = time.monotonic()
        while not self._stop.is_set():
            time.sleep(0.02)
            wall = time.monotonic()
            dt, last = wall - last, wall
            with self.sim:
                if not self.playing:
                    continue
                target = self.now + dt * self.rate
                if self.position < len(self.items):
                    gap = self.items[self.position][0] - self.now
                    if gap > IDLE_SKIP_S:
                        target = max(target, self.items[self.position][0] - IDLE_SKIP_S)
                self._advance(target, 2000)
                if self.position < len(self.items):
                    self.now = max(self.now, min(target, self.items[self.position][0]))

    # -- feed API
    def query(self, since: int) -> dict:
        with self.sim:
            return {"ok": True, **self.tap.state(since), "viz": self.viz()}

    def viz(self) -> dict:
        return {"mode": "replay", "session": self.tag, "order": self.order,
                "playback": {"playing": self.playing, "rate": self.rate,
                             "position": self.position, "total": len(self.items)},
                "connected": True}

    def health(self) -> dict:
        h = super().health()
        h["order_note"] = self.order_note
        h["sim_time"] = self.now
        h["timer_divergences"] = self.timer_divergences
        return h

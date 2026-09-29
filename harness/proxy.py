"""UO Outlands agent-harness proxy.

TCP relay between the stock Outlands ClassicUO client and the game server:

  client preamble (5B, cleartext)    -> logged, relayed
  server prelude (13B, cleartext)    -> byte 11 = S2C XOR key, byte 12 = C2S XOR key
  C2S (XOR c2s_key)                  -> decrypted, framed, walks rewritten, logged
  S2C (XOR s2c_key + per-packet      -> split into packets (uo/s2c.py), logged;
       Huffman, one flush = 1 packet)   movement packets filtered/rewritten

Agent injection: a localhost control listener (default 127.0.0.1:25941) accepts
length-prefixed (u16be) PLAINTEXT action packets (built by harness/actions.py).
Each accepted packet is processed exactly like client traffic (walk rewrite,
logging, XOR) and written into the client->server relay. Every frame gets a
u16be-prefixed reply: `OK`, or `ERR <reason>` (no session / key unknown /
malformed packet / agent walk gated). Malformed frames also close the control
connection; the game relay is never affected.

Movement (docs/MOVEMENT.md): the MoveAuthority owns seq + fastwalk key of every
walk from both senders and follows the server's own movement packets (0xBF
sub1 seeds, 0x22 confirms, 0x21 denies). ConfirmWalks for agent walks are
hidden from the client (it would treat them as bad steps and freeze its
walker); confirms for client walks are mapped back to the client's own seq.
Once walking is quiet, the proxy re-anchors the client with a fabricated S2C
0x21 at the player's server-true position. It never sends a packet of its own to the server.

Usage:
  python proxy.py [--listen-host 127.0.0.1] [--listen-port 2593]
                  [--upstream-host play.uooutlands.com] [--upstream-port 2593]
                  [--control-host 127.0.0.1] [--control-port 25941]
                  [--logdir logs]
"""
import argparse
import asyncio
import collections
import json
import os
import time

from uo.packets import packet_length, C2S_OVERRIDES
from uo.s2c import PRELUDE_LEN, S2CStream, encode_packet, prelude_keys
from world.runtime import WorldRuntime, C2S, S2C

CONTROL_MAX_FRAME = 4096
CLIENT_PREAMBLE_LEN = 5
RESYNC = b"\x22\x00\x00"
EVENT_CAP = 5000  # event envelopes kept for state-port readers
DIAG_TOP = 20     # packet_counts entries in the state response's diagnostics

# Movement timing (docs/MOVEMENT.md, sessions 20260929_142237/_143051/_144541):
RESYNC_REPLY_TIMEOUT_S = 1.5  # client resync: no 0xBF sub1 seed by then -> the server ignored it
REANCHOR_IDLE_S = 0.5         # no walk in flight and quiet this long -> re-anchor the client
CONFIRM_TIMEOUT_S = 1.5       # walk unconfirmed this long -> the server rejected it
STALL_REJECTS = 3             # this many rejections in a row -> stop agent walks
RUN_STEP_S = 0.2              # minimum agent step spacing, on-foot run / walk
WALK_STEP_S = 0.4             # (the server has a Speedhack violation category)

# UO direction -> (dx, dy): 0=N 1=NE 2=E 3=SE 4=S 5=SW 6=W 7=NW
DIR_DELTA = ((0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1))


def hexd(b, limit=64):
    return b[:limit].hex()


def _u32(b: bytes, off: int) -> int:
    return int.from_bytes(b[off:off + 4], "big")


def _i32(b: bytes, off: int) -> int:
    return int.from_bytes(b[off:off + 4], "big", signed=True)


class MoveAuthority:
    """Owns the session's movement state: seq ladder, fastwalk key, walk
    confirmation routing, the player's server-true position, agent pacing and
    client re-anchoring. Sends nothing to the server on its own.

    - seq: every C2S walk (client + agent) is rewritten onto one ladder
      (last+1, wrap 0xFF -> 1). A server 0xBF sub1 seed (login / honored
      client resync) resets it to 0, and so does a server 0x21 deny. A rejected walk
      (no confirm within CONFIRM_TIMEOUT_S) leaves the server's expectation
      unchanged (session 142237), so the ladder rewinds to that walk's seq.
    - key: the first walk after a seed must carry the seed's token (single
      use). A walk with key 0 in that position gets it stamped in. The client
      then still holds its own copy (`stale_token`), which is zeroed once when
      the client presents it, so the server never sees a spent token.
    - confirms: the server confirms every accepted walk (S2C `22 <seq> ..`).
      The client treats a confirm for a step it never sent as a bad step
      (frozen walker), so agent confirms are hidden, and client confirms are
      rewritten to the client's own seq.
    - position: anchored by the server's self 0x1B/0x20/0x77/0x21, advanced by
      each confirmed walk (a walk in a new direction only turns).
    - re-anchor: hidden confirms mean the client doesn't see agent movement.
      Once walking is quiet, the proxy hands the CLIENT a fabricated 0x21
      DenyWalk with the true position and facing. The client's DenyWalk
      handler resets its walker and places the player there. Nothing reaches
      the server. z is the last server-reported z (confirms carry none).
    """

    __slots__ = ("next_seq", "armed_token", "stale_token", "inflight", "resync_sent_at",
                 "last_walk_at", "client_stale", "rejects_in_row", "self_serial", "pos")

    def __init__(self):
        self.next_seq = 0
        self.armed_token = None      # token the next walk must carry (latest seed/push)
        self.stale_token = None      # client's copy of a token the proxy already spent
        self.inflight = {}           # ladder seq -> (src, sender's own seq, direction, sent_at)
        self.resync_sent_at = None   # a client resync awaits the server's seed
        self.last_walk_at = None
        self.client_stale = False    # client display may differ from the server position
        self.rejects_in_row = 0
        self.self_serial = None
        self.pos = None              # [x, y, z, facing 0-7], server truth

    # ---- C2S ----
    def on_c2s_walk(self, pkt: bytearray, src: str, now: float) -> tuple[str, str] | None:
        """Rewrite a plaintext 7-byte walk in place (seq + key); record it in flight.

        Returns (log_event, note) when the key decision is worth logging.
        """
        s = self.next_seq
        self.next_seq = s + 1 if s < 0xFF else 1
        self.inflight[s] = (src, pkt[2], pkt[1] & 7, now)
        pkt[2] = s
        self.last_walk_at = now
        if src != "client":
            self.client_stale = True
        sent = int.from_bytes(pkt[3:7], "big")
        tok = self.armed_token
        if tok is not None:  # first walk after a seed/push
            self.armed_token = None
            if src == "client" and sent != 0:
                if sent != tok:
                    return ("c2s_token_mismatch",
                            f"client walk carries key {sent}, armed token was {tok}")
                return None
            pkt[3:7] = tok.to_bytes(4, "big")
            self.stale_token = tok
            return ("c2s_token_stamped", f"walk stamped with token {tok}")
        if sent == 0:
            return None
        if src != "client":
            pkt[3:7] = b"\x00\x00\x00\x00"
            return ("c2s_agent_key_cleared", f"agent key {sent} forced to 0 (no token armed)")
        if sent == self.stale_token:
            pkt[3:7] = b"\x00\x00\x00\x00"
            self.stale_token = None
            return ("c2s_stale_token_dropped",
                    f"client re-presented spent token {sent}; forced to 0")
        return ("c2s_token_passthrough", f"client key {sent} passed (no token armed)")

    def on_c2s_resync(self, now: float):
        self.resync_sent_at = now

    # ---- S2C ----
    def on_self_position(self, x: int, y: int, z: int, direction: int):
        """Server-reported player position (0x1B/0x20/0x77/0x21 about self)."""
        self.pos = [x, y, z, direction & 7]

    def on_seed(self, token: int | None):
        """0xBF sub1: the server reset the walker (login / honored resync)."""
        self.next_seq = 0
        self.armed_token = token
        self.stale_token = None
        self.inflight.clear()
        self.resync_sent_at = None
        self.client_stale = False  # the seed arrives with the server's own re-anchor
        self.rejects_in_row = 0

    def on_push(self, token: int):
        """0xBF sub2: one more token; the next walk must carry it."""
        if self.armed_token is None:
            self.armed_token = token

    def on_confirm(self, seq: int) -> tuple[str, int | None]:
        """Route a server ConfirmWalk ("forward"|"hide"|"rewrite", client seq)
        and advance the tracked position."""
        entry = self.inflight.pop(seq, None)
        if entry is None:
            return ("forward", None)
        src, sent_seq, direction, _ = entry
        self.rejects_in_row = 0
        if self.pos is not None:
            if self.pos[3] != direction:
                self.pos[3] = direction           # turn only
            else:
                dx, dy = DIR_DELTA[direction]
                self.pos[0] += dx
                self.pos[1] += dy
        if src != "client":
            return ("hide", None)
        if sent_seq != seq:
            return ("rewrite", sent_seq)
        return ("forward", None)

    def on_deny(self):
        """Server 0x21: rejected walk; both walkers reset (the client also
        repositions itself from the deny)."""
        self.next_seq = 0
        self.inflight.clear()
        self.client_stale = False

    # ---- timers / gates ----
    def expire(self, now: float) -> list[tuple[str, str]]:
        """Time out client-resync replies and walk confirms; returns log notes."""
        notes = []
        if self.resync_sent_at is not None and now - self.resync_sent_at > RESYNC_REPLY_TIMEOUT_S:
            self.resync_sent_at = None
            notes.append(("resync_ignored", "no fastwalk seed in reply; server state unchanged"))
        late = [(t, s, src) for s, (src, _, _, t) in self.inflight.items() if now - t > CONFIRM_TIMEOUT_S]
        if late:
            _, s, src = min(late)
            # the server did not advance past the rejected walk, so later walks
            # in flight were sent with seqs it will reject too
            dropped = len(self.inflight)
            self.inflight.clear()
            self.next_seq = s
            self.rejects_in_row += 1
            self.client_stale = True  # also clears the client's stuck pending steps
            notes.append(("walk_rejected",
                          f"{src} walk seq {s} got no confirm; ladder rewound to {s} "
                          f"({dropped} in flight dropped, {self.rejects_in_row} in a row)"))
        return notes

    def agent_walk_block(self, now: float, run: bool) -> str | None:
        """Reason an agent walk must be refused right now, or None if allowed."""
        if self.resync_sent_at is not None:
            return "walk gated: awaiting the server's reply to a client resync"
        if self.rejects_in_row >= STALL_REJECTS:
            return f"walk gated: movement stalled ({self.rejects_in_row} walks in a row rejected)"
        step = RUN_STEP_S if run else WALK_STEP_S
        if self.last_walk_at is not None and now - self.last_walk_at < step:
            return f"walk gated: pacing ({step:.1f}s between steps)"
        return None

    def reanchor_packet(self, now: float) -> bytes | None:
        """A fabricated S2C 0x21 placing the client at the server-true position,
        when due (client stale, position known, no walk or resync in flight,
        walking quiet for REANCHOR_IDLE_S), else None. Marks the client fresh."""
        if not self.client_stale or self.pos is None or self.inflight or self.resync_sent_at is not None:
            return None
        if self.last_walk_at is not None and now - self.last_walk_at < REANCHOR_IDLE_S:
            return None
        self.client_stale = False
        x, y, z, facing = self.pos
        return (b"\x21\x00" + (x & 0xFFFFFFFF).to_bytes(4, "big") + (y & 0xFFFFFFFF).to_bytes(4, "big")
                + bytes([facing]) + z.to_bytes(4, "big", signed=True))


class InjectionHub:
    """Tracks the one active game session for control-channel injection.

    Single-client assumption (documented): the last attached session wins.
    """

    def __init__(self):
        self.session = None  # (SessionTap, upstream StreamWriter)

    def attach(self, tap, upstream_writer):
        self.session = (tap, upstream_writer)

    def detach(self, tap):
        if self.session is not None and self.session[0] is tap:
            self.session = None

    async def inject(self, payload: bytes) -> str | None:
        """Process payload like client traffic and relay it upstream.

        Returns None on success, or a rejection reason string.
        """
        if self.session is None:
            return "no active session"
        tap, writer = self.session
        if tap.key is None or not tap.c2s_preamble_done:
            return "session key not known yet"
        now = time.monotonic()
        tap.tick(now)
        if payload[0] == 0x02 and len(payload) == 7:
            block = tap.moveauth.agent_walk_block(now, run=bool(payload[1] & 0x80))
            if block is not None:
                return block
        writer.write(tap.inject_c2s(payload, "agent", now))
        await writer.drain()
        return None


async def handle_control(reader, writer, hub):
    """One control connection: u16be-length-prefixed plaintext action packets."""
    peer = writer.get_extra_info("peername")

    def reply(msg: bytes):
        writer.write(len(msg).to_bytes(2, "big") + msg)

    try:
        while True:
            hdr = await reader.readexactly(2)
            n = int.from_bytes(hdr, "big")
            if n == 0 or n > CONTROL_MAX_FRAME:
                reply(b"ERR bad frame length")
                break
            payload = await reader.readexactly(n)
            plen = packet_length(payload, overrides=C2S_OVERRIDES)
            if plen <= 0 or plen != n:
                reply(b"ERR malformed packet")
                break
            err = await hub.inject(payload)
            if err is not None:
                reply(f"ERR {err}".encode())
                continue  # clean rejection; connection stays usable
            reply(b"OK")
    except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass
        print(f"[proxy] control {peer} closed")


class SessionTap:
    """Per-connection protocol processor for both directions.

    Also runs the world model live: every C2S packet (after rewrite, as the
    server sees it) and every S2C packet (as the server sent it, before
    client-side filtering) is fed to a WorldRuntime. Its events, plus the
    proxy's own movement decisions and agent-sent packet summaries, are kept
    in one event log of envelopes `{"seq", "t", "origin": "world"|"proxy",
    "data"}` for state-port readers (`state()`; docs/VISUALIZER.md §1.4).

    `wall`/`mono` are the clocks (time.time / time.monotonic); an offline
    driver (viz_feed.ReplayDriver) substitutes a simulated clock.
    """

    def __init__(self, logf, raw_c2s, raw_s2c):
        self.logf = logf
        self.raw_c2s = raw_c2s
        self.raw_s2c = raw_s2c
        self.wall = time.time
        self.mono = time.monotonic
        self.key = None                  # C2S XOR key (prelude byte 12)
        self.c2s_buf = bytearray()       # client preamble accumulator
        self.c2s_preamble_done = False
        self.c2s_wire = bytearray()      # encrypted bytes of an incomplete C2S packet
        self.s2c_head = bytearray()      # S2C prelude accumulator
        self.s2c = None                  # S2CStream once the prelude is parsed
        self.s2c_passthrough = False     # unexpected prelude: relay S2C undecoded
        self.moveauth = MoveAuthority()
        self.world = WorldRuntime()
        self.events = []                 # envelopes; seq = events_base + list index
        self.events_base = 0
        self.world_errors = 0
        # cumulative since session start, so late readers don't depend on the event ring
        self.traffic_events = collections.Counter()   # proxy event name -> count
        self.traffic_c2s = collections.Counter()      # (src, "0xNN") -> count, src != client

    def _log(self, **kw):
        kw["t"] = round(self.wall(), 3)
        self.logf.write(json.dumps(kw) + "\n")
        self.logf.flush()

    def _append(self, origin: str, items, t: float):
        """Append event data dicts to the event log as envelopes."""
        for data in items:
            self.events.append({"seq": self.events_base + len(self.events), "t": t,
                                "origin": origin, "data": data})
        over = len(self.events) - EVENT_CAP
        if over > 0:
            del self.events[:over]
            self.events_base += over

    def _proxy_event(self, ev: str, **fields):
        """A proxy decision for state-port readers (the jsonl log is separate)."""
        self.traffic_events[ev] += 1
        if ev == "c2s":
            self.traffic_c2s[(fields["src"], fields["id"])] += 1
        self._append("proxy", ({"ev": ev, **fields},), round(self.wall(), 3))

    def tick(self, now: float):
        for ev, note in self.moveauth.expire(now):
            self._log(ev=ev, note=note)
            self._proxy_event(ev, note=note)

    # ---- live world model ----
    def _world(self, direction: str, pkt: bytes):
        """Feed the world model; never lets a model bug touch the relay."""
        t = round(self.wall(), 3)
        try:
            self.world.feed_packet(direction, pkt)
        except Exception as e:  # noqa: BLE001 - relay must survive any model bug
            self.world_errors += 1
            if self.world_errors <= 20:
                self._log(ev="world_error", note=f"{direction} 0x{pkt[0]:02X}: {type(e).__name__}: {e}")
        new = self.world.drain_events()
        if new:
            self._append("world", new, t)

    def diagnostics(self) -> dict:
        """World-model coverage counters (meta-information, not world state)."""
        w = self.world
        return {
            "packet_counts": [[d, f"0x{pid:02X}", n] for (d, pid), n in w.packet_counts.most_common(DIAG_TOP)],
            "unhandled": [[d, f"0x{pid:02X}", n] for (d, pid), n in w.unhandled.most_common()],
            "parse_failures": w.parse_failures,
            "anomalies": dict(w.anomalies),
            "world_errors": self.world_errors,
        }

    def state(self, since: int = 0, snapshot: bool = True) -> dict:
        """Response for state-port readers: movement truth, world model (unless
        snapshot=False), event envelopes with seq >= since, diagnostics."""
        ma = self.moveauth
        start = max(since - self.events_base, 0)
        out = {
            "movement": {
                "pos": ma.pos, "self_serial": ma.self_serial,
                "inflight": len(ma.inflight), "next_seq": ma.next_seq,
                "resync_pending": ma.resync_sent_at is not None,
                "rejects_in_row": ma.rejects_in_row,
                "stalled": ma.rejects_in_row >= STALL_REJECTS,
                "client_stale": ma.client_stale,
            },
        }
        if snapshot:
            out["world"] = self.world.state.snapshot()
        out.update({
            "events": self.events[start:],
            "next": self.events_base + len(self.events),
            "world_errors": self.world_errors,
            "diagnostics": self.diagnostics(),
            "traffic": {
                "proxy_events": dict(self.traffic_events),
                "c2s": [[src, pid, n] for (src, pid), n in sorted(self.traffic_c2s.items())],
            },
        })
        return out

    # ---- client -> server ----
    def _c2s_packet(self, pkt: bytearray, src: str, now: float) -> bytes:
        """Rewrite/log one plaintext C2S packet; return its encrypted wire bytes."""
        if pkt[0] == 0x02 and len(pkt) == 7:
            note = self.moveauth.on_c2s_walk(pkt, src, now)
            if note is not None:
                self._log(ev=note[0], src=src, note=note[1])
                self._proxy_event(note[0], src=src, note=note[1])
        elif bytes(pkt) == RESYNC:
            self.moveauth.on_c2s_resync(now)
            self._log(ev="c2s_resync_seen", src=src)
            self._proxy_event("c2s_resync_seen", src=src)
        self._log(dir="c2s", src=src, id=f"0x{pkt[0]:02X}", len=len(pkt), hex=hexd(bytes(pkt)))
        if src != "client":
            self._proxy_event("c2s", src=src, id=f"0x{pkt[0]:02X}")
        self._world(C2S, bytes(pkt))
        enc = bytes(b ^ self.key for b in pkt)
        self.raw_c2s.write(enc)
        return enc

    def inject_c2s(self, payload: bytes, src: str, now: float | None = None) -> bytes:
        """Process one complete plaintext packet from a non-client sender.

        Independent of the client's partial-packet buffer: only complete client
        packets are ever forwarded, so an injection never splits one.
        """
        out = self._c2s_packet(bytearray(payload), src, self.mono() if now is None else now)
        self.raw_c2s.flush()
        return out

    def tap_c2s(self, data: bytes) -> bytes:
        """Frame + normalize client traffic; returns the bytes to forward upstream.
        Raw capture is flushed every call so a killed proxy loses nothing."""
        out = self._tap_c2s(data)
        self.raw_c2s.flush()
        return out

    def _tap_c2s(self, data: bytes) -> bytes:
        out = bytearray()
        if not self.c2s_preamble_done:
            need = CLIENT_PREAMBLE_LEN - len(self.c2s_buf)
            head, data = data[:need], data[need:]
            self.c2s_buf += head
            out += head
            self.raw_c2s.write(head)
            if len(self.c2s_buf) < CLIENT_PREAMBLE_LEN:
                return bytes(out)
            self._log(ev="c2s_preamble", hex=self.c2s_buf.hex())
            self.c2s_preamble_done = True
            self.c2s_buf.clear()
        self.c2s_wire += data
        if self.key is None:
            if self.c2s_wire:
                self._log(ev="c2s_prekey", note=f"{len(self.c2s_wire)}B buffered until session key arrives")
            return bytes(out)
        plain = bytes(b ^ self.key for b in self.c2s_wire)
        now = self.mono()
        i = 0
        while i < len(plain):
            plen = packet_length(plain[i:], overrides=C2S_OVERRIDES)
            if plen == 0:
                break  # incomplete packet; wait for more bytes
            if plen < 0:
                self._log(ev="c2s_desync", at=plain[i],
                          note="implausible length; forwarding 1 byte anyway")
                out.append(self.c2s_wire[i])
                self.raw_c2s.write(self.c2s_wire[i:i + 1])
                i += 1
                continue
            out += self._c2s_packet(bytearray(plain[i:i + plen]), "client", now)
            i += plen
        del self.c2s_wire[:i]
        return bytes(out)

    # ---- server -> client ----
    def tap_s2c(self, data: bytes) -> bytes:
        """Decode server traffic packet by packet; returns the bytes to forward
        to the client (an incomplete trailing packet is held until complete)."""
        self.raw_s2c.write(data)
        self.raw_s2c.flush()
        if self.s2c_passthrough:
            return data
        out = bytearray()
        if self.s2c is None:
            self.s2c_head += data
            if len(self.s2c_head) < PRELUDE_LEN:
                return b""
            head = bytes(self.s2c_head)
            self.s2c_head.clear()
            try:
                s2c_key, c2s_key = prelude_keys(head)
            except ValueError:
                self._log(ev="s2c_prelude_unexpected", hex=head[:PRELUDE_LEN].hex(),
                          note="relaying S2C undecoded")
                self.s2c_passthrough = True
                return head
            self.key = c2s_key
            self.s2c = S2CStream(s2c_key)
            self._log(ev="s2c_prelude", hex=head[:PRELUDE_LEN].hex(),
                      s2c_key=f"0x{s2c_key:02X}", c2s_key=f"0x{c2s_key:02X}")
            out += head[:PRELUDE_LEN]
            self._world(S2C, head[:PRELUDE_LEN])
            data = head[PRELUDE_LEN:]
        now = self.mono()
        for wire, pkt in self.s2c.feed(data):
            out += self._s2c_packet(wire, pkt, now)
        return bytes(out)

    def _s2c_packet(self, wire: bytes, pkt: bytes, now: float) -> bytes:
        """Log one S2C packet, update movement state; return the wire bytes to forward."""
        pid = pkt[0]
        self._log(dir="s2c", id=f"0x{pid:02X}", len=len(pkt), hex=hexd(pkt))
        self._world(S2C, pkt)
        ma = self.moveauth
        if pid == 0x22 and len(pkt) == 3:
            before = tuple(ma.pos) if ma.pos is not None else None
            action, client_seq = ma.on_confirm(pkt[1])
            if before is not None and (ma.pos[0], ma.pos[1]) != before[:2]:
                self._log(ev="step", **{"from": list(before[:2])}, to=ma.pos[:2], z=ma.pos[2])
                self._proxy_event("step", **{"from": list(before[:2])}, to=ma.pos[:2], z=ma.pos[2])
            if action == "hide":
                self._log(ev="s2c_confirm_hidden", note=f"agent walk seq {pkt[1]} confirmed")
                self._proxy_event("s2c_confirm_hidden", seq=pkt[1])
                return b""
            if action == "rewrite":
                self._log(ev="s2c_confirm_rewritten", note=f"seq {pkt[1]} -> client seq {client_seq}")
                self._proxy_event("s2c_confirm_rewritten", seq=pkt[1], client_seq=client_seq)
                return encode_packet(bytes([0x22, client_seq, pkt[2]]), self.s2c.key)
        elif pid == 0x21 and len(pkt) == 15:
            entry = ma.inflight.get(pkt[1])
            if entry is not None and ma.pos is not None:
                self._log(ev="blocked", **{"from": ma.pos[:2]}, dir=entry[2], src=entry[0])
                self._proxy_event("blocked", **{"from": ma.pos[:2]}, dir=entry[2], src=entry[0])
            ma.on_deny()
            ma.on_self_position(_u32(pkt, 2), _u32(pkt, 6), _i32(pkt, 11), pkt[10])
            self._log(ev="s2c_deny", note="walk denied; ladder reset to 0")
        elif pid == 0x1B and len(pkt) >= 26:
            ma.self_serial = _u32(pkt, 1)
            ma.on_self_position(_u32(pkt, 13), _u32(pkt, 17), _i32(pkt, 21), pkt[25])
        elif pid == 0x20 and len(pkt) == 28 and _u32(pkt, 1) == ma.self_serial:
            ma.on_self_position(_u32(pkt, 13), _u32(pkt, 17), _i32(pkt, 24), pkt[23])
        elif pid == 0x77 and len(pkt) == 18 and _u32(pkt, 1) == ma.self_serial:
            ma.on_self_position(_u32(pkt, 5), _u32(pkt, 9), _i32(pkt, 13), pkt[17])
        elif pid == 0xBF and len(pkt) >= 9:
            sub = int.from_bytes(pkt[3:5], "big")
            if sub == 1:
                keys = [int.from_bytes(pkt[5 + 4 * i:9 + 4 * i], "big")
                        for i in range(min(5, (len(pkt) - 5) // 4))]
                token = next((k for k in keys if k), None)
                ma.on_seed(token)
                self._log(ev="s2c_fastwalk_seed", note=f"walker reset; token {token}")
            elif sub == 2:
                token = int.from_bytes(pkt[5:9], "big")
                ma.on_push(token)
                self._log(ev="s2c_fastwalk_push", note=f"token {token}")
        return wire

    def reanchor_client(self, now: float) -> bytes:
        """Wire bytes of a fabricated S2C 0x21 re-anchoring the client, or b"".
        Client-only: logged as src=proxy, never written to raw_s2c (server truth)."""
        if self.s2c is None:
            return b""
        pkt = self.moveauth.reanchor_packet(now)
        if pkt is None:
            return b""
        x, y, z, facing = self.moveauth.pos
        self._log(ev="reanchor_client", note=f"fabricated 0x21 to client: x={x} y={y} z={z} dir={facing}")
        self._proxy_event("reanchor_client", x=x, y=y, z=z, dir=facing)
        self._log(dir="s2c", src="proxy", id="0x21", len=len(pkt), hex=hexd(pkt))
        return encode_packet(pkt, self.s2c.key)


async def _relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, tap):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            out = tap(data)
            if out:
                writer.write(out)
                await writer.drain()
    except (ConnectionResetError, asyncio.IncompleteReadError, BrokenPipeError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def _movement_timer(tap: SessionTap, client_writer: asyncio.StreamWriter):
    """Expire movement timeouts; re-anchor the client (fabricated S2C 0x21) when due.

    Writes land between whole S2C packets: tap_s2c only ever emits complete ones.
    """
    while True:
        await asyncio.sleep(0.1)
        now = time.monotonic()
        tap.tick(now)
        pkt = tap.reanchor_client(now)
        if pkt:
            client_writer.write(pkt)
            await client_writer.drain()


async def handle_client(client_reader, client_writer, args):
    peer = client_writer.get_extra_info("peername")
    upstream_reader = upstream_writer = None
    last_err = None
    for port in range(args.upstream_bind_port, args.upstream_bind_port + 21):
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(
                args.upstream_host, args.upstream_port,
                local_addr=(args.upstream_bind, port) if args.upstream_bind else None)
            break
        except OSError as e:
            last_err = e
            continue
    if upstream_writer is None:
        client_writer.close()
        print(f"[proxy] upstream connect failed for {peer}: {last_err}")
        return

    os.makedirs(args.logdir, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    logf = open(os.path.join(args.logdir, f"session_{stamp}.jsonl"), "a", encoding="utf-8")
    tap = SessionTap(
        logf,
        open(os.path.join(args.logdir, f"session_{stamp}.c2s.raw"), "ab"),
        open(os.path.join(args.logdir, f"session_{stamp}.s2c.raw"), "ab"),
    )
    tap._log(ev="open", peer=str(peer), upstream=f"{args.upstream_host}:{args.upstream_port}")
    print(f"[proxy] {peer} connected -> {args.upstream_host}:{args.upstream_port} (log session_{stamp}.jsonl)")

    timer = asyncio.create_task(_movement_timer(tap, client_writer))
    try:
        args.hub.attach(tap, upstream_writer)
        await asyncio.gather(
            _relay(client_reader, upstream_writer, tap.tap_c2s),
            _relay(upstream_reader, client_writer, tap.tap_s2c),
        )
    finally:
        timer.cancel()
        args.hub.detach(tap)
        tap._log(ev="close")
        logf.close()
        tap.raw_c2s.close()
        tap.raw_s2c.close()
        print(f"[proxy] {peer} closed")


async def handle_state(reader, writer, hub):
    """One state connection: JSON-lines request/response (localhost only).

    Request  `{"op": "state", "since": N, "snapshot": true}`
             -> SessionTap.state(N, snapshot) + {"ok": true}
             (`snapshot: false` omits `world`: movement + new events only)
    No session -> `{"ok": false, "error": "no active session"}`.
    """
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                resp = {"ok": False, "error": "bad json"}
            else:
                if req.get("op") != "state":
                    resp = {"ok": False, "error": f"unknown op {req.get('op')!r}"}
                elif hub.session is None:
                    resp = {"ok": False, "error": "no active session"}
                else:
                    tap = hub.session[0]
                    tap.tick(time.monotonic())
                    resp = {"ok": True, **tap.state(int(req.get("since", 0)),
                                                    snapshot=bool(req.get("snapshot", True)))}
            writer.write((json.dumps(resp) + "\n").encode())
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def amain(args):
    args.hub = InjectionHub()
    control = await asyncio.start_server(
        lambda r, w: handle_control(r, w, args.hub),
        args.control_host, args.control_port)
    state = await asyncio.start_server(
        lambda r, w: handle_state(r, w, args.hub),
        args.control_host, args.state_port)
    server = await asyncio.start_server(
        lambda r, w: handle_client(r, w, args), args.listen_host, args.listen_port)
    print(f"[proxy] listening on {args.listen_host}:{args.listen_port}, "
          f"upstream {args.upstream_host}:{args.upstream_port}, "
          f"control {args.control_host}:{args.control_port}, state {args.control_host}:{args.state_port}")
    async with server, control, state:
        await server.serve_forever()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--listen-host", default="127.0.0.1")
    p.add_argument("--listen-port", type=int, default=2593)
    p.add_argument("--upstream-host", default="play.uooutlands.com")
    p.add_argument("--upstream-port", type=int, default=2593)
    p.add_argument("--upstream-bind", default="",
                   help="local IP to bind the upstream connection to (NAT loop prevention)")
    p.add_argument("--upstream-bind-port", type=int, default=0,
                   help="local port for the upstream connection (NAT loop prevention)")
    p.add_argument("--control-host", default="127.0.0.1")
    p.add_argument("--control-port", type=int, default=25941,
                   help="localhost control listener for agent action injection")
    p.add_argument("--state-port", type=int, default=25942,
                   help="localhost JSON-lines state endpoint (movement + world model)")
    p.add_argument("--logdir", default="logs")
    args = p.parse_args()
    try:
        asyncio.run(amain(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

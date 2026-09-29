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
After an agent burst the proxy re-anchors the client with one resync.

Usage:
  python proxy.py [--listen-host 127.0.0.1] [--listen-port 2593]
                  [--upstream-host play.uooutlands.com] [--upstream-port 2593]
                  [--control-host 127.0.0.1] [--control-port 25941]
                  [--logdir logs]
"""
import argparse
import asyncio
import json
import os
import time

from uo.packets import packet_length, C2S_OVERRIDES
from uo.s2c import PRELUDE_LEN, S2CStream, encode_packet, prelude_keys

CONTROL_MAX_FRAME = 4096
CLIENT_PREAMBLE_LEN = 5
RESYNC = b"\x22\x00\x00"

# Movement timing (docs/MOVEMENT.md, sessions 20260929_142237/_143051/_144541):
RESYNC_REPLY_TIMEOUT_S = 1.5  # no 0xBF sub1 seed by then -> the server ignored the resync
RESYNC_MIN_S = 5.6            # server ignores resyncs closer together: 2.64 s ignored, 5.56 s honored
REANCHOR_IDLE_S = 0.5         # agent quiet this long -> burst over, re-anchor the client
CONFIRM_TIMEOUT_S = 1.5       # agent walk unconfirmed this long -> ladder desync
RUN_STEP_S = 0.2              # minimum agent step spacing, on-foot run / walk
WALK_STEP_S = 0.4             # (the server has a Speedhack violation category)


def hexd(b, limit=64):
    return b[:limit].hex()


class MoveAuthority:
    """Owns the session's movement state: seq ladder, fastwalk key, walk
    confirmation routing, agent pacing and client re-anchoring.

    - seq: every C2S walk (client + agent) is rewritten onto one ladder
      (last+1, wrap 0xFF -> 1). A server 0xBF sub1 seed (sent at login and in
      answer to an honored resync) resets it to 0; so does a 0x21 deny. A
      resync the server ignores (no seed within RESYNC_REPLY_TIMEOUT_S) leaves
      the server's expectation - and the ladder - unchanged.
    - key: the first walk after a seed must carry the seed's token (single
      use). A walk with key 0 in that position gets it stamped in; the client
      then still holds its own copy (`stale_token`), which is zeroed once when
      the client presents it later, so the server never sees a spent token
      re-presented. Other client continuation keys pass (genuine server pushes,
      0xBF sub2, also arm the token).
    - confirms: the server confirms every accepted walk (S2C `22 <seq> ..`).
      The client treats a confirm for a step it never sent as a bad step
      (WalkingFailed + latched resync -> frozen walker), so agent confirms are
      hidden, and client confirms are rewritten to the client's own seq.
    - re-anchor: hidden confirms mean the client doesn't see agent movement.
      Once an agent burst is over (or an agent walk went unconfirmed), the
      proxy sends one resync, spaced >= RESYNC_MIN_S from the previous one; the
      server answers with a seed + player 0x20, re-anchoring the client.
    """

    __slots__ = ("next_seq", "armed_token", "stale_token", "inflight",
                 "resync_sent_at", "last_resync_at", "last_seed_at",
                 "last_walk_at", "last_agent_walk_at", "client_stale", "desync")

    def __init__(self):
        self.next_seq = 0
        self.armed_token = None      # token the next walk must carry (from the latest seed/push)
        self.stale_token = None      # client's copy of a token the proxy already spent
        self.inflight = {}           # ladder seq -> (src, sender's own seq, sent_at)
        self.resync_sent_at = None   # a resync awaits the server's seed
        self.last_resync_at = None   # any resync sent (spacing)
        self.last_seed_at = None
        self.last_walk_at = None
        self.last_agent_walk_at = None
        self.client_stale = False    # agent moved the character since the last re-anchor
        self.desync = False          # an agent walk went unconfirmed

    # ---- C2S ----
    def on_c2s_walk(self, pkt: bytearray, src: str, now: float) -> tuple[str, str] | None:
        """Rewrite a plaintext 7-byte walk in place (seq + key); record it in flight.

        Returns (log_event, note) when the key decision is worth logging.
        """
        s = self.next_seq
        self.next_seq = s + 1 if s < 0xFF else 1
        self.inflight[s] = (src, pkt[2], now)
        pkt[2] = s
        self.last_walk_at = now
        if src != "client":
            self.last_agent_walk_at = now
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
        self.last_resync_at = now

    # ---- S2C ----
    def on_seed(self, token: int | None, now: float):
        """0xBF sub1: server reset the walker (login / honored resync)."""
        self.next_seq = 0
        self.armed_token = token
        self.stale_token = None
        self.inflight.clear()
        self.resync_sent_at = None
        self.last_seed_at = now
        self.client_stale = False  # the seed arrives with the player 0x20 re-anchor
        self.desync = False

    def on_push(self, token: int):
        """0xBF sub2: one more token; the next walk must carry it."""
        if self.armed_token is None:
            self.armed_token = token

    def on_confirm(self, seq: int) -> tuple[str, int | None]:
        """Route a server ConfirmWalk: ("forward"|"hide"|"rewrite", client seq)."""
        entry = self.inflight.pop(seq, None)
        if entry is None:
            return ("forward", None)
        src, sent_seq, _ = entry
        if src != "client":
            return ("hide", None)
        if sent_seq != seq:
            return ("rewrite", sent_seq)
        return ("forward", None)

    def on_deny(self):
        """0x21: server rejected a walk; stock semantics reset both walkers
        (the client also repositions itself from the deny)."""
        self.next_seq = 0
        self.inflight.clear()
        self.client_stale = False
        self.desync = False

    # ---- timers / gates ----
    def expire(self, now: float) -> list[tuple[str, str]]:
        """Time out resync replies and walk confirms; returns log notes."""
        notes = []
        if self.resync_sent_at is not None and now - self.resync_sent_at > RESYNC_REPLY_TIMEOUT_S:
            self.resync_sent_at = None
            notes.append(("resync_ignored", "no fastwalk seed in reply; server state unchanged"))
        for s, (src, _, t) in list(self.inflight.items()):
            if now - t > CONFIRM_TIMEOUT_S:
                del self.inflight[s]
                if src != "client":
                    self.desync = True
                notes.append(("walk_unconfirmed", f"{src} walk seq {s} got no confirm"))
        return notes

    def agent_walk_block(self, now: float, run: bool) -> str | None:
        """Reason an agent walk must be refused right now, or None if allowed."""
        if self.resync_sent_at is not None:
            return "walk gated: awaiting the server's reply to a resync"
        if self.desync:
            return "walk gated: seq desync after an unconfirmed walk; re-anchor pending"
        step = RUN_STEP_S if run else WALK_STEP_S
        if self.last_walk_at is not None and now - self.last_walk_at < step:
            return f"walk gated: pacing ({step:.1f}s between steps)"
        return None

    def reanchor_due(self, now: float) -> bool:
        """True when the proxy should send a resync to re-anchor the client."""
        if self.resync_sent_at is not None or not (self.client_stale or self.desync):
            return False
        if not self.desync:
            if self.last_agent_walk_at is not None and now - self.last_agent_walk_at < REANCHOR_IDLE_S:
                return False
            if any(src != "client" for src, _, _ in self.inflight.values()):
                return False  # let pending agent confirms land first
        last = max((t for t in (self.last_seed_at, self.last_resync_at) if t is not None), default=None)
        return last is None or now - last >= RESYNC_MIN_S


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
    """Per-connection protocol processor for both directions."""

    def __init__(self, logf, raw_c2s, raw_s2c):
        self.logf = logf
        self.raw_c2s = raw_c2s
        self.raw_s2c = raw_s2c
        self.key = None                  # C2S XOR key (prelude byte 12)
        self.c2s_buf = bytearray()       # client preamble accumulator
        self.c2s_preamble_done = False
        self.c2s_wire = bytearray()      # encrypted bytes of an incomplete C2S packet
        self.s2c_head = bytearray()      # S2C prelude accumulator
        self.s2c = None                  # S2CStream once the prelude is parsed
        self.s2c_passthrough = False     # unexpected prelude: relay S2C undecoded
        self.moveauth = MoveAuthority()

    def _log(self, **kw):
        kw["t"] = round(time.time(), 3)
        self.logf.write(json.dumps(kw) + "\n")
        self.logf.flush()

    def tick(self, now: float):
        for ev, note in self.moveauth.expire(now):
            self._log(ev=ev, note=note)

    # ---- client -> server ----
    def _c2s_packet(self, pkt: bytearray, src: str, now: float) -> bytes:
        """Rewrite/log one plaintext C2S packet; return its encrypted wire bytes."""
        if pkt[0] == 0x02 and len(pkt) == 7:
            note = self.moveauth.on_c2s_walk(pkt, src, now)
            if note is not None:
                self._log(ev=note[0], src=src, note=note[1])
        elif bytes(pkt) == RESYNC:
            self.moveauth.on_c2s_resync(now)
            self._log(ev="c2s_resync_seen", src=src)
        self._log(dir="c2s", src=src, id=f"0x{pkt[0]:02X}", len=len(pkt), hex=hexd(bytes(pkt)))
        enc = bytes(b ^ self.key for b in pkt)
        self.raw_c2s.write(enc)
        return enc

    def inject_c2s(self, payload: bytes, src: str, now: float | None = None) -> bytes:
        """Process one complete plaintext packet from a non-client sender.

        Independent of the client's partial-packet buffer: only complete client
        packets are ever forwarded, so an injection never splits one.
        """
        return self._c2s_packet(bytearray(payload), src, time.monotonic() if now is None else now)

    def tap_c2s(self, data: bytes) -> bytes:
        """Frame + normalize client traffic; returns the bytes to forward upstream."""
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
        now = time.monotonic()
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
            data = head[PRELUDE_LEN:]
        now = time.monotonic()
        for wire, pkt in self.s2c.feed(data):
            out += self._s2c_packet(wire, pkt, now)
        return bytes(out)

    def _s2c_packet(self, wire: bytes, pkt: bytes, now: float) -> bytes:
        """Log one S2C packet, update movement state; return the wire bytes to forward."""
        pid = pkt[0]
        self._log(dir="s2c", id=f"0x{pid:02X}", len=len(pkt), hex=hexd(pkt))
        ma = self.moveauth
        if pid == 0x22 and len(pkt) == 3:
            action, client_seq = ma.on_confirm(pkt[1])
            if action == "hide":
                self._log(ev="s2c_confirm_hidden", note=f"agent walk seq {pkt[1]} confirmed")
                return b""
            if action == "rewrite":
                self._log(ev="s2c_confirm_rewritten", note=f"seq {pkt[1]} -> client seq {client_seq}")
                return encode_packet(bytes([0x22, client_seq, pkt[2]]), self.s2c.key)
        elif pid == 0x21:
            ma.on_deny()
            self._log(ev="s2c_deny", note="walk denied; ladder reset to 0")
        elif pid == 0xBF and len(pkt) >= 9:
            sub = int.from_bytes(pkt[3:5], "big")
            if sub == 1:
                keys = [int.from_bytes(pkt[5 + 4 * i:9 + 4 * i], "big")
                        for i in range(min(5, (len(pkt) - 5) // 4))]
                token = next((k for k in keys if k), None)
                ma.on_seed(token, now)
                self._log(ev="s2c_fastwalk_seed", note=f"walker reset; token {token}")
            elif sub == 2:
                token = int.from_bytes(pkt[5:9], "big")
                ma.on_push(token)
                self._log(ev="s2c_fastwalk_push", note=f"token {token}")
        return wire


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


async def _movement_timer(tap: SessionTap, upstream_writer: asyncio.StreamWriter):
    """Expire movement timeouts and send the client re-anchor resync when due."""
    while True:
        await asyncio.sleep(0.1)
        now = time.monotonic()
        tap.tick(now)
        if tap.key is not None and tap.c2s_preamble_done and tap.moveauth.reanchor_due(now):
            ma = tap.moveauth
            why = "unconfirmed agent walk" if ma.desync else "agent burst over"
            tap._log(ev="reanchor_resync", note=f"{why}; resyncing to re-anchor the client")
            upstream_writer.write(tap.inject_c2s(RESYNC, "proxy", now))
            await upstream_writer.drain()


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

    timer = asyncio.create_task(_movement_timer(tap, upstream_writer))
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


async def amain(args):
    args.hub = InjectionHub()
    control = await asyncio.start_server(
        lambda r, w: handle_control(r, w, args.hub),
        args.control_host, args.control_port)
    server = await asyncio.start_server(
        lambda r, w: handle_client(r, w, args), args.listen_host, args.listen_port)
    print(f"[proxy] listening on {args.listen_host}:{args.listen_port}, "
          f"upstream {args.upstream_host}:{args.upstream_port}, "
          f"control {args.control_host}:{args.control_port}")
    async with server, control:
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
    p.add_argument("--logdir", default="logs")
    args = p.parse_args()
    try:
        asyncio.run(amain(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

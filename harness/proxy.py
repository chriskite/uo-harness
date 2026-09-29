"""UO Outlands agent-harness proxy — Phase 1 + Phase 3 injection.

Byte-exact TCP relay between the stock Outlands ClassicUO client and the game
server, with a passive protocol tap:

  client preamble (5B, cleartext)  -> logged
  server prelude (19B, cleartext)  -> parsed, session XOR key extracted (byte 14)
  C2S (XOR S)                      -> passively decrypted, framed, logged
  S2C (Huffman)                    -> decompressed, framed, logged

Phase 3 adds an agent injection path: a localhost control listener (default
127.0.0.1:25941) accepts length-prefixed (u16be) PLAINTEXT action packets
(built by harness/actions.py). Each accepted packet is XORed with the current
session key, tapped (so it appears in the session log as a c2s packet exactly
like client-originated traffic), and written into the client->server relay
in order. The control protocol answers every frame with a u16be-prefixed
reply: `OK` on accept, `ERR <reason>` on reject (no session / key unknown /
malformed packet / agent walk gated). Malformed frames also close the control
connection; the game relay is never affected.

Walks (0x02) from both senders are rewritten by the MoveAuthority (seq ladder
and cycle token). Agent walks are gated to one per movement cycle (see
MoveAuthority.agent_walk_block). Relay first, tap second, injection third.

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
import sys
import time

from uo.packets import packet_length, C2S_OVERRIDES
from uo.outlands_table import outlands_length
from uo.huffman import HuffmanDecoder

CONTROL_MAX_FRAME = 4096


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
        """XOR payload with the session key, tap it as c2s, relay it upstream.

        Returns None on success, or a rejection reason string.
        """
        if self.session is None:
            return "no active session"
        tap, writer = self.session
        if tap.key is None or not tap.c2s_preamble_done:
            return "session key not known yet"
        if payload[0] == 0x02 and len(payload) == 7:
            block = tap.moveauth.agent_walk_block(time.monotonic())
            if block is not None:
                return block
        enc = bytes(b ^ tap.key for b in payload)
        out = tap.tap_c2s(enc, src="agent")  # taps + walk-rewrites exactly like client traffic
        writer.write(out)
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


CLIENT_PREAMBLE_LEN = 5
SERVER_PRELUDE_LEN = 19

SUPPRESS = object()  # tap return sentinel: drop this frame from the relay


def hexd(b, limit=64):
    return b[:limit].hex()


LOGIN_TOKEN = 8   # cycle token armed at login (validated live, docs/MOVEMENT.md)
REARM_TOKEN = 1   # cycle token armed by each client movement resync (22 0000)
# Delay after a client resync before an agent walk may open the new cycle: the
# server's resync response (walker reset: clears the client's WalkingFailed and
# ResendPacketResync latch) must land first. 0.65 s after the resync matches the
# ~0.7 s one-step-per-cycle cadence validated live (session_20260929_113311).
AGENT_SETTLE_S = 0.65


class MoveAuthority:
    """Owns the session's movement acceptance state: seq ladder + key field,
    and gates agent walks.

    The proxy rewrites every C2S walk (0x02) from BOTH the client and agent
    injections, so the server sees one coherent walker regardless of sender:

    - seq: one monotonic ladder. Server expects last+1, wrap 0xFF -> 1;
      login and client resync (C2S 22 0000) reset it to 0. Sender seqs are
      always overwritten.
    - key: the first walk of a movement cycle must carry the cycle token
      (LOGIN_TOKEN after login, REARM_TOKEN after a resync); it is single-use.
      * Cycle opener from the agent, or from the client with key 0: the armed
        token is stamped in. The client still holds (or will receive) its own
        copy of that token -> remembered as `stale_token`.
      * Cycle opener from the client with its own key: passed through (it is
        server-issued truth; a value != armed token is logged as a mismatch).
      * Client continuations: a key equal to `stale_token` is the client's
        copy of a token the proxy already spent -> zeroed once, so the server
        never sees a consumed token re-presented. Any other key is a genuine
        mid-cycle server push (seen in session_20260928_223537) -> passed.
      Login/resync clear `stale_token` (the server's seed overwrites the
      client's token stack).
    - agent gate: the server confirms every accepted walk (S2C 0x22); a
      confirm for a step the client didn't send trips the client's bad-step
      path (WalkingFailed, latched single resync) and freezes its walking
      until a server walker reset (docs/MOVEMENT.md "client lockout
      mechanism"). The client resync that follows each agent walk earns that
      reset, so agent walks are allowed ONLY as cycle openers, and after a
      resync only once AGENT_SETTLE_S has passed. Agent continuations never
      reach the rewrite.
    """

    __slots__ = ("next_seq", "armed_token", "stale_token", "armed_at")

    def __init__(self):
        self.on_login()

    def on_login(self):
        self.next_seq = 0
        self.armed_token = LOGIN_TOKEN
        self.stale_token = None
        self.armed_at = None  # login seed resets the client walker; no settle

    def on_resync(self):
        self.next_seq = 0
        self.armed_token = REARM_TOKEN
        self.stale_token = None
        self.armed_at = time.monotonic()

    def agent_walk_block(self, now: float) -> str | None:
        """Reason an agent walk must be refused right now, or None if allowed."""
        if self.armed_token is None:
            return "walk gated: movement cycle already open; wait for the client resync"
        if self.armed_at is not None and now - self.armed_at < AGENT_SETTLE_S:
            return "walk gated: client resync still settling"
        return None

    def rewrite(self, pkt: bytearray, src: str) -> tuple[str, str] | None:
        """Rewrite a plaintext 7-byte walk in place (seq + key).

        Returns (log_event, note) when the key decision is worth logging.
        """
        s = self.next_seq
        self.next_seq = s + 1 if s < 0xFF else 1
        pkt[2] = s
        sent = int.from_bytes(pkt[3:7], "big")
        tok = self.armed_token
        if tok is not None:  # cycle opener
            self.armed_token = None
            if src == "client" and sent != 0:
                if sent != tok:
                    return ("c2s_token_mismatch",
                            f"client cycle opener carries key {sent}, armed token was {tok}")
                return None
            pkt[3:7] = tok.to_bytes(4, "big")
            self.stale_token = tok
            return ("c2s_token_stamped", f"cycle opener stamped with token {tok}")
        if sent == 0:
            return None
        if sent == self.stale_token:
            pkt[3:7] = b"\x00\x00\x00\x00"
            self.stale_token = None
            return ("c2s_stale_token_dropped",
                    f"client re-presented spent token {sent}; forced to 0")
        return ("c2s_token_passthrough",
                f"client continuation key {sent} passed (presumed server push)")


class SessionTap:
    """Passive per-connection protocol analyzer."""

    def __init__(self, logf, raw_c2s, raw_s2c):
        self.logf = logf
        self.raw_c2s = raw_c2s
        self.raw_s2c = raw_s2c
        self.key = None
        self.huff = HuffmanDecoder()
        # stream state
        self.c2s_buf = bytearray()       # encrypted, post-preamble
        self.c2s_plain = bytearray()     # decrypted, unframed
        self.s2c_buf = bytearray()       # compressed, post-prelude
        self.s2c_plain = bytearray()     # decompressed, unframed
        self.c2s_preamble_done = False
        self.s2c_prelude_done = False
        self.c2s_wire = bytearray()      # wire bytes pending packet-aligned rewrite
        self.moveauth = MoveAuthority()  # seq ladder + cycle token (proxy-owned)

    def _log(self, **kw):
        kw["t"] = round(time.time(), 3)
        self.logf.write(json.dumps(kw) + "\n")
        self.logf.flush()

    # ---- client -> server ----
    def tap_c2s(self, data: bytes, src: str = "client"):
        """Tap + normalize C2S traffic; returns the bytes to forward upstream.

        Every walk packet (0x02) is rewritten by the MoveAuthority (seq ladder +
        cycle-token stamping). Everything else passes through unchanged.
        raw_c2s records the rewritten (server-truth) wire. `src` ("client" or
        "agent") is logged on each framed packet.
        """
        out = bytearray()
        if not self.c2s_preamble_done:
            need = CLIENT_PREAMBLE_LEN - len(self.c2s_buf)
            head, data = data[:need], data[need:]
            self.c2s_buf += head
            out += head
            if len(self.c2s_buf) < CLIENT_PREAMBLE_LEN:
                self.raw_c2s.write(bytes(out))
                return bytes(out)
            self._log(ev="c2s_preamble", hex=self.c2s_buf.hex())
            self.c2s_preamble_done = True
            self.moveauth.on_login()
            self.c2s_buf.clear()
        self.c2s_wire += data
        if self.key is None:
            # Forward only what's already queued in `out` (e.g. the client preamble);
            # keep post-preamble data buffered in c2s_wire until the key arrives.
            if out:
                self._log(ev="c2s_prekey", note=f"{len(self.c2s_wire)}B buffered until session key arrives")
                self.raw_c2s.write(bytes(out))
            return bytes(out)
        # decrypt the buffered stream, frame on plaintext, rewrite walk seqs
        plain = bytes(b ^ self.key for b in self.c2s_wire)
        out = bytearray()
        i = 0
        while i < len(plain):
            plen = packet_length(plain[i:], overrides=C2S_OVERRIDES)
            if plen == 0:
                break  # incomplete packet; wait for more bytes
            if plen < 0:
                self._log(ev="c2s_desync", at=plain[i],
                          note="implausible length; forwarding 1 byte anyway")
                out.append(self.c2s_wire[i])
                i += 1
                continue
            pkt = bytearray(plain[i:i + plen])
            if pkt[0] == 0x02 and plen == 7:
                note = self.moveauth.rewrite(pkt, src)
                if note is not None:
                    self._log(ev=note[0], src=src, note=note[1])
            elif bytes(pkt) == b"\x22\x00\x00":
                self.moveauth.on_resync()
                self._log(ev="c2s_resync_seen",
                          note=f"client movement resync; seq reset to 0, token {REARM_TOKEN} armed")
            self._log(dir="c2s", src=src, id=f"0x{pkt[0]:02X}", len=plen, hex=hexd(bytes(pkt)))
            out += bytes(b ^ self.key for b in pkt)
            i += plen
        self.c2s_wire = self.c2s_wire[i:]
        self.raw_c2s.write(bytes(out))
        return bytes(out)

    def _drain_c2s(self):  # retained for API compat; framing now happens inline in tap_c2s
        pass

    # ---- server -> client ----

    def tap_s2c(self, data: bytes):
        self.raw_s2c.write(data)
        if not self.s2c_prelude_done:
            self.s2c_buf += data
            # stage 1: as soon as the 13-byte ff handshake packet is in, take the
            # session key (byte 12) so injections/C2S decrypt work immediately
            if self.key is None and len(self.s2c_buf) >= 13:
                head = bytes(self.s2c_buf)
                if head[0] == 0xFF and head[10] == 0x0C:
                    self.key = head[12]
                    self._log(ev="s2c_key", session_key=f"0x{self.key:02X}", tick=head[11])
                    if self.c2s_plain:
                        self._drain_c2s()
                else:
                    self._log(ev="s2c_prelude_unexpected", hex=head[:13].hex())
                    self.s2c_prelude_done = True
                    self.s2c_buf.clear()
                    data = head
                    self.s2c_plain += self.huff.decompress(data)
                    self._drain_s2c()
                    return
            # stage 2: decide the huffman start once enough data exists to
            # validate against (13B ff-packet + variable trailer + compressed data)
            if self.key is None or len(self.s2c_buf) < 48:
                return
            head = bytes(self.s2c_buf)
            self.s2c_prelude_done = True
            self.s2c_buf.clear()
            start = self._find_huffman_start(head)
            self._log(ev="s2c_prelude", hex=head[:start].hex(),
                      session_key=f"0x{self.key:02X}", tick=head[11],
                      trailer=head[13:start].hex(), huffman_start=start)
            data = head[start:]
            if not data:
                return
        self.s2c_plain += self.huff.decompress(data)
        self._drain_s2c()


    @staticmethod
    def _find_huffman_start(head: bytes) -> int:
        """Prelude = 13B ff-packet + variable trailer; find the huffman start
        by trial-decoding each candidate and keeping the longest clean run."""
        best_start, best_score = SERVER_PRELUDE_LEN, 0
        for start in range(SERVER_PRELUDE_LEN, min(len(head) - 8, 33)):
            plain = HuffmanDecoder().decompress(head[start:])
            off = 0
            while True:
                plen = outlands_length(plain, off)
                if plen <= 0:
                    break
                off += plen
            if off > best_score:
                best_start, best_score = start, off
        return best_start

    def _drain_s2c(self):
        buf = getattr(self, "_s2c_framing", None)
        if buf is None:
            buf = self._s2c_framing = bytearray()
        buf += self.s2c_plain
        self.s2c_plain.clear()
        while buf:
            plen = outlands_length(buf)
            if plen == 0:
                break
            if plen < 0:
                self._log(ev="s2c_desync", at=buf[0], note="implausible length; dropping 1 byte")
                del buf[0]
                continue
            pkt = bytes(buf[:plen])
            del buf[:plen]
            self._log(dir="s2c", id=f"0x{pkt[0]:02X}", len=plen, hex=hexd(pkt))


async def _relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, tap):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            suppress = tap(data)
            if suppress is SUPPRESS:
                continue
            writer.write(suppress if suppress is not None else data)
            await writer.drain()
    except (ConnectionResetError, asyncio.IncompleteReadError, BrokenPipeError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


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

    try:
        args.hub.attach(tap, upstream_writer)
        await asyncio.gather(
            _relay(client_reader, upstream_writer, tap.tap_c2s),
            _relay(upstream_reader, client_writer, tap.tap_s2c),
        )
    finally:
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

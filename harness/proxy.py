"""UO Outlands agent-harness proxy — Phase 1.

Byte-exact TCP relay between the stock Outlands ClassicUO client and the game
server, with a passive protocol tap:

  client preamble (5B, cleartext)  -> logged
  server prelude (19B, cleartext)  -> parsed, session XOR key extracted (byte 14)
  C2S (XOR S)                      -> passively decrypted, framed, logged
  S2C (Huffman)                    -> decompressed, framed, logged

Nothing is modified, buffered-to-delay, or injected. Relay first, tap second.

Usage:
  python proxy.py [--listen-host 127.0.0.1] [--listen-port 2593]
                  [--upstream-host play.uooutlands.com] [--upstream-port 2593]
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


CLIENT_PREAMBLE_LEN = 5
SERVER_PRELUDE_LEN = 19


def hexd(b, limit=64):
    return b[:limit].hex()


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

    def _log(self, **kw):
        kw["t"] = round(time.time(), 3)
        self.logf.write(json.dumps(kw) + "\n")
        self.logf.flush()

    # ---- client -> server ----
    def tap_c2s(self, data: bytes):
        self.raw_c2s.write(data)
        if not self.c2s_preamble_done:
            need = CLIENT_PREAMBLE_LEN - len(self.c2s_buf)
            head, data = data[:need], data[need:]
            self.c2s_buf += head
            if len(self.c2s_buf) < CLIENT_PREAMBLE_LEN:
                return
            self._log(ev="c2s_preamble", hex=self.c2s_buf.hex())
            self.c2s_preamble_done = True
            self.c2s_buf.clear()
            if not data:
                return
        self.c2s_plain += data  # decrypt later if key unknown yet
        if self.key is not None:
            self._drain_c2s()
        # if key unknown, bytes accumulate; drained when prelude arrives

    def _drain_c2s(self):
        # decrypt everything pending, frame, log
        plain = bytes(b ^ self.key for b in self.c2s_plain)
        self.c2s_plain.clear()
        buf = getattr(self, "_c2s_framing", None)
        if buf is None:
            buf = self._c2s_framing = bytearray()
        buf += plain
        while buf:
            plen = packet_length(buf, overrides=C2S_OVERRIDES)
            if plen == 0:
                break
            if plen < 0:
                self._log(ev="c2s_desync", at=buf[0], note="implausible length; dropping 1 byte")
                del buf[0]
                continue
            pkt = bytes(buf[:plen])
            del buf[:plen]
            self._log(dir="c2s", id=f"0x{pkt[0]:02X}", len=plen, hex=hexd(pkt))

    # ---- server -> client ----
    def tap_s2c(self, data: bytes):
        self.raw_s2c.write(data)
        if not self.s2c_prelude_done:
            need = SERVER_PRELUDE_LEN - len(self.s2c_buf)
            head, data = data[:need], data[need:]
            self.s2c_buf += head
            if len(self.s2c_buf) < SERVER_PRELUDE_LEN:
                return
            pre = bytes(self.s2c_buf)
            self.s2c_prelude_done = True
            self.s2c_buf.clear()
            if pre[0] == 0xFF and pre[10] == 0x0C:
                self.key = pre[12]
                self._log(ev="s2c_prelude", hex=pre.hex(), session_key=f"0x{self.key:02X}",
                          tick=pre[11], tail=pre[13:].hex())
                if self.c2s_plain:
                    self._drain_c2s()
            else:
                self._log(ev="s2c_prelude_unexpected", hex=pre.hex())
            if not data:
                return
        self.s2c_plain += self.huff.decompress(data)
        self._drain_s2c()

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
            tap(data)
            writer.write(data)
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
        await asyncio.gather(
            _relay(client_reader, upstream_writer, tap.tap_c2s),
            _relay(upstream_reader, client_writer, tap.tap_s2c),
        )
    finally:
        tap._log(ev="close")
        logf.close()
        tap.raw_c2s.close()
        tap.raw_s2c.close()
        print(f"[proxy] {peer} closed")


async def amain(args):
    server = await asyncio.start_server(
        lambda r, w: handle_client(r, w, args), args.listen_host, args.listen_port)
    print(f"[proxy] listening on {args.listen_host}:{args.listen_port}, "
          f"upstream {args.upstream_host}:{args.upstream_port}")
    async with server:
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
    p.add_argument("--logdir", default="logs")
    args = p.parse_args()
    try:
        asyncio.run(amain(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

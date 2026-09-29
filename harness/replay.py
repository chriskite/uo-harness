"""Offline session replay through the full world-model pipeline.

Replays a raw proxy capture (logs/session_*.{c2s,s2c}.raw):

  S2C: 13-byte cleartext prelude `ff 00 0d | 7x 00 | 0c <s2c_key> <c2s_key>`
       (itself the 0xFF sub-0 dialect handshake frame), then every byte XOR
       s2c_key and UO static Huffman, one flush segment per packet
       (harness/uo/s2c.py, docs/CIPHER.md)
  C2S: 5-byte cleartext preamble (skipped) -> single-byte XOR with c2s_key
       (prelude byte 12) -> packets.py framing with C2S_OVERRIDES

Feed order: the raw captures carry no timestamps, so interleaving is not
recoverable. Order is: prelude frame, all C2S, all S2C. C2S-first is the
semantically safe choice — the login burst establishes identity (account,
character name, self serial) before S2C packets confirm/update it; the
server's absolute positions (0x1B/0x20/0x77) then overwrite C2S dead
reckoning, so the final self position is the server's.

S2C needs no framing heuristics: each Huffman flush segment is exactly one
packet. `stats["s2c_length_mismatch"]` counts segments whose length disagrees
with outlands_length (0 on every capture so far) as a table-regression canary.

replay_session() returns the final StateStore, the drained event list, and
the runtime (with counters) — the assertion surface for the replay tests.
"""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from uo.packets import packet_length, C2S_OVERRIDES
from uo.outlands_table import outlands_length
from uo.s2c import PRELUDE_LEN, S2CStream, prelude_keys

from world.runtime import WorldRuntime, C2S, S2C

CLIENT_PREAMBLE_LEN = 5

ReplayResult = collections.namedtuple("ReplayResult",
                                      ["state", "events", "runtime"])


def _frame_c2s(buf, stats):
    """Consume buf into C2S packets; drop-byte resync on implausible lengths."""
    pkts = []
    while buf:
        plen = packet_length(buf, overrides=C2S_OVERRIDES)
        if plen == 0:
            stats["c2s_leftover"] = len(buf)
            break
        if plen < 0:
            stats["c2s_desyncs"] += 1
            del buf[0]
            continue
        pkts.append(bytes(buf[:plen]))
        del buf[:plen]
    return pkts


def s2c_packets(s2c_raw):
    """(s2c_key, c2s_key, [packet, ...]) from a raw S2C capture."""
    s2c_key, c2s_key = prelude_keys(s2c_raw[:PRELUDE_LEN])
    stream = S2CStream(s2c_key)
    pkts = [p for _, p in stream.feed(s2c_raw[PRELUDE_LEN:])]
    return s2c_key, c2s_key, pkts


def replay_session(c2s_path, s2c_path):
    """Replay a capture pair; c2s_path=None replays the server side only
    (proves state derivable from S2C alone)."""
    c2s_raw = open(c2s_path, "rb").read() if c2s_path else b""
    s2c_raw = open(s2c_path, "rb").read()

    rt = WorldRuntime()
    stats = {"c2s_desyncs": 0, "c2s_leftover": 0, "s2c_length_mismatch": 0}

    s2c_key, c2s_key, s2c_pkts = s2c_packets(s2c_raw)
    stats["s2c_key"] = s2c_key
    stats["c2s_key"] = c2s_key
    # the prelude IS a 0xFF sub-0 dialect frame (docs/WORLDMODEL.md §5)
    rt.feed_packet(S2C, s2c_raw[:PRELUDE_LEN])

    # C2S: skip the 5-byte preamble, XOR with the c2s key, frame
    c2s_plain = bytes(b ^ c2s_key for b in c2s_raw[CLIENT_PREAMBLE_LEN:])
    for pkt in _frame_c2s(bytearray(c2s_plain), stats):
        rt.feed_packet(C2S, pkt)

    # S2C: one flush segment == one packet
    for pkt in s2c_pkts:
        if outlands_length(pkt) != len(pkt):
            stats["s2c_length_mismatch"] += 1
        rt.feed_packet(S2C, pkt)

    events = rt.drain_events()
    rt.replay_stats = stats
    return ReplayResult(rt.state, events, rt)


if __name__ == "__main__":
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tag = sys.argv[1] if len(sys.argv) > 1 else "20260928_141253"
    result = replay_session(f"{root}/logs/session_{tag}.c2s.raw",
                            f"{root}/logs/session_{tag}.s2c.raw")
    import json
    print(json.dumps(result.state.snapshot(), indent=1))
    rt = result.runtime
    print(f"events: {len(result.events)}  stats: {rt.replay_stats}")
    print(f"parse_failures: {rt.parse_failures}  anomalies: {dict(rt.anomalies)}")
    print("unhandled:", {f"{d}:0x{p:02x}": n
                         for (d, p), n in sorted(rt.unhandled.items())})

"""Offline session replay through the full world-model pipeline.

Replays a raw proxy capture (logs/session_*.{c2s,s2c}.raw):

  S2C: 19-byte cleartext prelude (itself one 0xFF sub-0 dialect frame,
       carrying the session XOR key at byte 12) -> static Huffman from
       byte 19 -> outlands_table framing
  C2S: 5-byte cleartext preamble (skipped) -> single-byte XOR with the
       session key -> packets.py framing with C2S_OVERRIDES

Feed order: the raw captures carry no timestamps, so interleaving is not
recoverable. Order is: prelude frame, all C2S, all S2C. C2S-first is the
semantically safe choice — the login burst establishes identity (account,
character name, self serial) before S2C packets confirm/update it.

S2C framing desyncs (implausible declared length) are recovered by dropping
one byte, same as the live proxy tap. They occur inside the 0x00-family
world-data stream whose grammar is an open question (docs/WORLDMODEL.md §6);
recovery can surface record content under covered ids — the runtime's
identity guards keep that out of self state (see world/runtime.py).

replay_session() returns the final StateStore, the drained event list, and
the runtime (with counters) — the assertion surface for the replay tests.
"""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from uo.packets import packet_length, C2S_OVERRIDES
from uo.outlands_table import outlands_length
from uo.huffman import HuffmanDecoder

from world.runtime import WorldRuntime, C2S, S2C

CLIENT_PREAMBLE_LEN = 5
SERVER_PRELUDE_LEN = 19

ReplayResult = collections.namedtuple("ReplayResult",
                                      ["state", "events", "runtime"])


def _frame(buf, length_fn, stats):
    """Consume buf into packets; drop-byte resync on implausible lengths."""
    pkts = []
    while buf:
        plen = length_fn(buf)
        if plen == 0:
            stats["leftover"] = len(buf)
            break
        if plen < 0:
            stats["desyncs"] += 1
            del buf[0]
            continue
        pkts.append(bytes(buf[:plen]))
        del buf[:plen]
    return pkts


def replay_session(c2s_path, s2c_path):
    c2s_raw = open(c2s_path, "rb").read()
    s2c_raw = open(s2c_path, "rb").read()

    rt = WorldRuntime()
    stats = {"desyncs": 0, "leftover": 0}

    prelude = s2c_raw[:SERVER_PRELUDE_LEN]
    session_key = prelude[12]
    stats["session_key"] = session_key
    # the prelude IS a 0xFF sub-0 dialect frame (docs/WORLDMODEL.md §5)
    rt.feed_packet(S2C, prelude)

    # C2S: skip the 5-byte preamble, XOR with the session key, frame
    c2s_plain = bytes(b ^ session_key for b in c2s_raw[CLIENT_PREAMBLE_LEN:])
    for pkt in _frame(bytearray(c2s_plain),
                      lambda b: packet_length(b, overrides=C2S_OVERRIDES),
                      stats):
        rt.feed_packet(C2S, pkt)

    # S2C: Huffman-decompress from byte 19, frame with the Outlands table
    s2c_plain = HuffmanDecoder().decompress(s2c_raw[SERVER_PRELUDE_LEN:])
    for pkt in _frame(bytearray(s2c_plain), outlands_length, stats):
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
    print(f"events: {len(result.events)}  stats: {result.runtime.replay_stats}")

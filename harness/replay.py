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
reckoning, so the final self position is the server's. The runtime's clock
gives None (no time), so `seen_t`/`last_seen` times are absent.

Timed order (timed_packets / replay_timed): captures by the fixed proxy have a
jsonl row per packet in the proxy's processing order (viz_feed.py "exact");
each row's `t` and `src` are paired with the raw packet, so the replay sees
the session as the live proxy's WorldRuntime did, clock included.

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

    rt = WorldRuntime(clock=lambda: None)
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


def timed_packets(base):
    """[(t, direction, src, packet)] for capture `base` (logs/session_<tag>
    without extension): the raw packets in the jsonl's row order, each with
    its row's time and sender ("client"/"agent" for C2S, "server" for S2C).
    Rows of packets the proxy fabricated for the client (src "proxy") are
    skipped like the live WorldRuntime never saw them. Raises ValueError if a
    row doesn't match its raw packet (id, length, hex prefix)."""
    import json
    s2c_raw = open(base + ".s2c.raw", "rb").read()
    c2s_raw = open(base + ".c2s.raw", "rb").read()
    _, c2s_key, s2c_pkts = s2c_packets(s2c_raw)
    stats = {"c2s_desyncs": 0, "c2s_leftover": 0}
    c2s_pkts = _frame_c2s(bytearray(b ^ c2s_key for b in c2s_raw[CLIENT_PREAMBLE_LEN:]), stats)
    out, ci, si = [], 0, 0
    with open(base + ".jsonl", encoding="utf-8") as f:
        for n, line in enumerate(f):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                break    # capture still being written
            d = row.get("dir")
            if row.get("ev") == "s2c_prelude":
                out.append((row["t"], S2C, "server", s2c_raw[:PRELUDE_LEN]))
                continue
            if d == "c2s":
                if ci >= len(c2s_pkts):
                    break
                pkt, src = c2s_pkts[ci], row.get("src", "client")
                ci += 1
            elif d == "s2c" and row.get("src") != "proxy":
                if si >= len(s2c_pkts):
                    break
                pkt, src = s2c_pkts[si], "server"
                si += 1
            else:
                continue
            if row.get("id") != f"0x{pkt[0]:02X}" or row.get("len") != len(pkt) \
                    or not pkt.hex().startswith(row.get("hex", "")):
                raise ValueError(f"{base}.jsonl row {n}: {d} row {row.get('id')}/{row.get('len')} "
                                 f"doesn't match raw packet {pkt[:1].hex()}/{len(pkt)}")
            out.append((row["t"], d, src, pkt))
    return out


def replay_timed(base, packets=None):
    """Replay a capture in timed order (timed_packets) with the row clock."""
    now = [None]
    rt = WorldRuntime(clock=lambda: now[0])
    for t, d, _src, pkt in packets if packets is not None else timed_packets(base):
        now[0] = t
        rt.feed_packet(d, pkt)
    return ReplayResult(rt.state, rt.drain_events(), rt)


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

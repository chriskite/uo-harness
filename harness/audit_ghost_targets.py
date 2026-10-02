"""Audit a capture for C2S packets aimed at serials the client no longer had.

  python harness/audit_ghost_targets.py logs/session_20261001_214649 [--json] [--list client|agent|all]

Replays the capture through WorldRuntime in the live proxy's order with each
jsonl row's time (replay.timed_packets), and before feeding every C2S packet
that names an entity checks the serial against the model at that moment:

  05 attack, 06 double click, 09 single click, 6C target (object, serial != 0),
  BF sub 0x13 context-menu request

A serial is "live" when it is self, a mobile in `mobiles` or an item in
`items`. With the pruned world model (docs/WORLDMODEL.md "Pruning") that is
what the stock client still has, so:

- agent packets at a non-live serial are shapes a stock client can't make
  (ANTICHEAT.md A12): it can't click or target what it has removed;
- client packets at a non-live serial mean the model dropped something the
  client still had (a pruning bug) unless the server deleted the serial
  (0x1D) in the same burst: the client queues its 09/34 queries for a new
  mobile and sends them even when the next packet deletes it (live
  20261001_214649: 94 such clicks, all within 0.1 s of the 0x1D).

For each flagged packet the report gives why the serial left the model
(`last_seen.why`: range, facet, dead, delete), how long before, and how long
ago it was learned dead after leaving (`dead_s`); "never seen" if it never
was live.
"""
import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import replay  # noqa: E402
from world.runtime import WorldRuntime, C2S  # noqa: E402

KINDS = {0x05: "attack", 0x06: "dclick", 0x09: "click", 0x6C: "target", 0xBF: "menu"}


def target_serial(pkt: bytes):
    """(kind, serial) for an entity-naming C2S packet, else None."""
    pid = pkt[0]
    if pid in (0x05, 0x06, 0x09) and len(pkt) >= 5:
        # 0x06 sets the high bit for "open my paperdoll" (ClassicUO GameActions.OpenPaperdoll)
        return KINDS[pid], int.from_bytes(pkt[1:5], "big") & 0x7FFFFFFF
    if pid == 0x6C and len(pkt) >= 11 and pkt[1] == 0:        # object target
        serial = int.from_bytes(pkt[7:11], "big")
        return ("target", serial) if serial else None
    if pid == 0xBF and len(pkt) >= 9 and int.from_bytes(pkt[3:5], "big") == 0x13:
        return "menu", int.from_bytes(pkt[5:9], "big")
    return None


def audit(base: str) -> dict:
    packets = replay.timed_packets(base)
    now = [None]
    rt = WorldRuntime(clock=lambda: now[0])
    st = rt.state
    totals = collections.Counter()       # (src, kind) -> packets naming a serial
    flagged = []
    for t, d, src, pkt in packets:
        now[0] = t
        hit = target_serial(pkt) if d == C2S else None
        if hit is not None:
            kind, serial = hit
            totals[(src, kind)] += 1
            if not (serial == st.self.serial or serial in st.mobiles or serial in st.items):
                gone = st.last_seen.get(serial) or {}

                def ago(k):
                    return round(t - gone[k], 1) if gone.get(k) is not None else None
                flagged.append({"t": t, "src": src, "kind": kind, "serial": f"0x{serial:08X}",
                                "why": gone.get("why", "never seen"), "gone_s": ago("t"),
                                "dead_s": ago("dead_t"), "name": gone.get("name")})
        rt.feed_packet(d, pkt)
        rt.drain_events()
    by = collections.Counter((f["src"], f["kind"]) for f in flagged)
    whys = collections.Counter((f["src"], f["why"]) for f in flagged)
    return {
        "capture": base,
        "packets": len(packets),
        "t0": packets[0][0] if packets else None,
        "checked": {f"{s} {k}": n for (s, k), n in sorted(totals.items())},
        "flagged": {f"{s} {k}": n for (s, k), n in sorted(by.items())},
        "flagged_why": {f"{s} {w}": n for (s, w), n in sorted(whys.items())},
        "flagged_serials": dict(collections.Counter(f"{f['src']} {f['serial']}" for f in flagged)),
        "rows": flagged,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("capture", help="logs/session_<tag> (with or without .jsonl)")
    ap.add_argument("--json", action="store_true", help="print the whole report as JSON")
    ap.add_argument("--list", choices=("client", "agent", "all"), default="all",
                    help="whose flagged packets to list (default all)")
    a = ap.parse_args(argv)
    base = a.capture[:-len(".jsonl")] if a.capture.endswith(".jsonl") else a.capture
    if not os.path.exists(base + ".jsonl"):
        ap.error(f"{base}.jsonl not found")
    try:
        rep = audit(base)
    except ValueError as e:     # an older capture whose rows don't map 1:1 onto the raw packets
        print(f"no timed replay for this capture: {e}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(rep, indent=1))
        return 0
    print(f"{rep['capture']}: {rep['packets']} packets replayed")
    print("checked:", rep["checked"])
    print("flagged (serial not in the model when sent):", rep["flagged"] or "none")
    print("flagged by why the serial left:", rep["flagged_why"] or "none")
    t0 = rep["t0"] or 0
    for f in rep["rows"]:
        if a.list in ("all", f["src"]):
            gone = "" if f["gone_s"] is None else f", left {f['gone_s']} s before"
            dead = "" if f["dead_s"] is None else f", known dead {f['dead_s']} s before"
            print(f"  +{f['t'] - t0:8.2f}s {f['src']:6} {f['kind']:6} {f['serial']} "
                  f"{f['name'] or '':12} {f['why']}{gone}{dead}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

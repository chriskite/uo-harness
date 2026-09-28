"""Phase 2 world-model runtime: live game state from the decoded UO stream.

Consumes framed packets in both directions (see harness/replay.py for the
offline pipeline) and maintains a last-write-wins snapshot plus a discrete
event queue for the future agent layer.

- layouts.py  declarative field tables (docs/WORLDMODEL.md is the source of truth)
- parsers.py  generic table reader + procedural parsers
- state.py    StateStore: self, mobiles, items, gumps, census; lazy name merge
- runtime.py  WorldRuntime: feed_packet dispatch, event queue, unhandled counts
"""

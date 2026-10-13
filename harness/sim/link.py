"""SimLink: the agent_link.Link API over the simulated server, in process (docs/SIM.md "Link").

The runner talks to it exactly as to the proxy: `act(pkt)` / `send(pkt)` (walks get their sequence
number here, as the proxy's MoveAuthority stamps them), `state()` / `movement()` (the movement truth,
the client's world model snapshot, new world events), `wait`, `pos`, `open_containers`, `intent`.
The client side is the real `world.runtime.WorldRuntime`, fed with our C2S packets and the
simulated server's S2C packets, so the runner sees what it would see live.

Time: every call advances the virtual clock a little (READ_COST_S per state read, SEND_COST_S per
packet: the live round trip), and the clock's advance runs the world (World.run_until).
"""
from __future__ import annotations

import agent_link
from world.runtime import WorldRuntime

READ_COST_S = 0.02          # a state read (live 20-60 ms with a busy screen)
MOVE_COST_S = 0.005         # a movement-only read
SEND_COST_S = 0.002


class SimLink:
    def __init__(self, world, clock):
        self.world, self.clock = world, clock
        clock.on_advance = world.run_until
        self.rt = WorldRuntime(clock=clock.time)
        self.events, self.event_t = [], []
        self.last = None
        self.char = None
        self.seq = 0
        self.intents = []           # (time, kind, text): what the runner said it was doing
        self.sent = []              # (time, packet) every C2S packet
        world.login_packets()
        self._pump()

    # ------------------------------------------------------------ plumbing
    def _pump(self):
        for p in self.world.drain():
            self.rt.feed_packet("s2c", p)
        evs = self.rt.drain_events()
        if evs:
            t = self.clock.time()
            self.events.extend(evs)
            self.event_t.extend([t] * len(evs))

    def send(self, pkt: bytes) -> str:
        """One C2S packet: into the client's world model and to the server, at once."""
        if pkt[0] == 0x02 and len(pkt) == 7:
            self.seq = self.seq % 255 + 1
            pkt = pkt[:2] + bytes([self.seq]) + pkt[3:]
        self.sent.append((self.clock.time(), pkt))
        self.rt.feed_packet("c2s", pkt)
        self.world.c2s(pkt)
        self._pump()
        self.clock.advance(SEND_COST_S)
        self._pump()
        return "OK"

    def act(self, pkt: bytes):
        self.send(pkt)

    def _movement(self) -> dict:
        pl = self.world.player
        return {"pos": [pl.x, pl.y, pl.z, pl.d], "self_serial": pl.serial, "inflight": 0, "next_seq": self.seq,
                "resync_pending": False, "rejects_in_row": 0, "z_misses": 0, "stalled": False,
                "client_stale": False, "mounted": False}

    def state(self) -> dict:
        self.clock.advance(READ_COST_S)
        self._pump()
        self.last = {"movement": self._movement(), "world": self.rt.state.snapshot(), "events": []}
        return self.last

    def movement(self) -> dict:
        self.clock.advance(MOVE_COST_S)
        self._pump()
        return {"movement": self._movement(), "events": []}

    def intent(self, text, kind=None, target=None, **extra):
        if text is not None:
            self.intents.append((self.clock.time(), kind, text))

    # the proxy-independent helpers, as Link has them
    wait = agent_link.Link.wait
    pos = agent_link.Link.pos
    open_containers = agent_link.Link.open_containers

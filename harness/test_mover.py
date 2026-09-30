"""Deterministic tests for agent_link.Mover human behaviour on a fake grid world.

The fake Link answers walk packets like the server (confirm or deny, turn
first when the facing changes) and exposes the state-port shape Mover reads.
Human profiles are overridden so the behaviour under test always fires.

Run: python harness/test_mover.py   (no network, ~10 s: the 'off' profile still paces steps)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nav  # noqa: E402
from agent_link import Mover  # noqa: E402
from humanize import Human, PROFILES  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}{'' if cond else ' ' + detail}")
    if not cond:
        FAILURES.append(name)


class FakeLink:
    """A grid server: walls deny, doors deny until opened (0x12 0x58 next to them)."""

    def __init__(self, start, facing, walls=(), doors=()):
        self.here = list(start)
        self.facing = facing
        self.walls = set(walls)
        self.doors = {tuple(d): False for d in doors}
        self.denies = []
        self.events = []

    def send(self, pkt):
        if pkt[0] == 0x02:
            d = pkt[1] & 7
            if d != self.facing:
                self.facing = d
                return "OK"
            nxt = nav.step(tuple(self.here), d)
            if nxt in self.walls or self.doors.get(nxt) is False:
                self.denies.append(nxt)
                return "OK"
            self.here = list(nxt)
        elif pkt[0] == 0x12 and pkt[3] == 0x58:
            for door in self.doors:
                if nav.chebyshev(door, tuple(self.here)) <= 1:
                    self.doors[door] = True
        return "OK"

    def act(self, pkt):
        self.send(pkt)

    def state(self):
        items = {f"0x{0x40000000 + i:08X}": {"graphic": 0x06AD, "x": d[0], "y": d[1]}
                 for i, d in enumerate(self.doors)}
        return {"movement": {"pos": [*self.here, 0, self.facing], "inflight": 0, "self_serial": 1,
                             "stalled": False, "rejects_in_row": 0},
                "world": {"mobiles": {}, "items": items, "self": {}}}

    def wait(self, pred, timeout, poll=0.1):
        st = self.state()
        return st if pred(st) else None

    def pos(self, st=None):
        return [*self.here, 0, self.facing]


def test_bump():
    print("== missed turn: run into the known obstacle, then turn ==")
    # East along y=0 to x=3, then north: (4, 0) is a wall the walker already
    # knows (walk memory holds the denied move (3, 0) -> east), and the known
    # wall north of (2, 0) forbids cutting the corner diagonally.
    link = FakeLink((0, 0), facing=2, walls={(4, 1), (4, 0), (4, -1)})
    mem = nav.WalkMemory()
    for x in range(3):
        mem.add_step((x, 0), (x + 1, 0))
    for y in range(0, -5, -1):
        mem.add_step((3, y), (3, y - 1))
    mem.add_blocked((3, 0), 2)
    mem.add_blocked((2, 0), 0)
    mv = Mover(link, mem, Human(Human.with_overrides("off", bump_p=1.0), seed=1))
    mv.walk_to(lambda: (3, -5), 0, "t")
    check("arrived", tuple(link.here) == (3, -5), str(link.here))
    check("ran into the known wall exactly once", link.denies == [(4, 0)], str(link.denies))
    check("bump counted as a bump, not as a blocked move",
          mv.bumps == 1 and mv.blocked_count == 0, f"bumps {mv.bumps} blocked {mv.blocked_count}")


def test_no_bump_when_off():
    print("== profile off: no bumps ==")
    link = FakeLink((0, 0), facing=2, walls={(4, 1), (4, 0), (4, -1)})
    mem = nav.WalkMemory()
    mem.add_blocked((3, 0), 2)
    mv = Mover(link, mem, Human("off"))
    mv.walk_to(lambda: (3, -5), 0, "t")
    check("arrived", tuple(link.here) == (3, -5), str(link.here))
    check("no denies", link.denies == [], str(link.denies))


def test_door():
    print("== closed door on the route: one open-door request, then through ==")
    link = FakeLink((0, 0), facing=2, walls={(2, y) for y in range(-3, 4)} - {(2, 0)},
                    doors=[(2, 0)])
    mv = Mover(link, nav.WalkMemory(), Human("off"), doors=True)
    mv.walk_to(lambda: (4, 0), 0, "t")
    check("arrived through the door", tuple(link.here) == (4, 0), str(link.here))
    check("one open-door request", mv.doors_opened == 1, str(mv.doors_opened))


if __name__ == "__main__":
    assert PROFILES["off"].bump_p == 0.0
    test_bump()
    test_no_bump_when_off()
    test_door()
    print(f"\nmover: {'ALL PASS' if not FAILURES else f'{len(FAILURES)} FAILURES'}")
    sys.exit(0 if not FAILURES else 1)

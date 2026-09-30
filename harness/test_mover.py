"""Deterministic tests for agent_link.Mover human behaviour on a fake grid world.

The fake Link answers walk packets like the server (confirm or deny, turn
first when the facing changes) and exposes the state-port shape Mover reads.
Human profiles are overridden so the behaviour under test always fires.

Run: python harness/test_mover.py   (no network, ~10 s: the 'off' profile still paces steps)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agent_link  # noqa: E402
import nav  # noqa: E402
from agent_link import Abort, Mover  # noqa: E402
from humanize import Human, PROFILES  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}{'' if cond else ' ' + detail}")
    if not cond:
        FAILURES.append(name)


class FakeLink:
    """A grid server: walls deny, doors deny until opened (0x12 0x58 next to them)."""

    def __init__(self, start, facing, walls=(), doors=(), mobiles=(), can_shove=True, z=0, z_walk=None):
        self.here = list(start)
        self.facing = facing
        self.walls = set(walls)
        self.doors = {tuple(d): False for d in doors}
        self.mobiles = [list(m) for m in mobiles]     # [x, y, polls left before it walks off]
        self.polls = 0
        self.can_shove = can_shove                    # stamina sufficient for the server's shove rule
        self.shoved = []
        self.denies = []
        self.events = []
        self.z = z
        self.z_walk = z_walk  # like the proxy's map z (MoveAuthority.z_fn): z after each confirmed step

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
            if any((m[0], m[1]) == nxt and m[2] > 0 for m in self.mobiles):
                if not self.can_shove:
                    self.denies.append(nxt)
                    return "OK"
                self.shoved.append(nxt)
            if self.z_walk is not None:
                self.z = self.z_walk.can_walk(self.here[0], self.here[1], self.z, d)[2]
            self.here = list(nxt)
        elif pkt[0] == 0x12 and pkt[3] == 0x58:
            for door in self.doors:
                if nav.chebyshev(door, tuple(self.here)) <= 1:
                    self.doors[door] = True
        return "OK"

    def act(self, pkt):
        self.send(pkt)

    def state(self):
        self.polls += 1
        for m in self.mobiles:
            m[2] -= 1
        items = {f"0x{0x40000000 + i:08X}": {"graphic": 0x06AD, "x": d[0], "y": d[1]}
                 for i, d in enumerate(self.doors)}
        mobiles = {f"0x{0x100 + i:08X}": {"x": m[0], "y": m[1]} for i, m in enumerate(self.mobiles) if m[2] > 0}
        return {"movement": {"pos": [*self.here, self.z, self.facing], "inflight": 0, "self_serial": 1,
                             "stalled": False, "rejects_in_row": 0},
                "world": {"mobiles": mobiles, "items": items,
                          "self": {"stam": 50 if self.can_shove else 3, "stam_max": 50}}}

    def wait(self, pred, timeout, poll=0.1):
        st = self.state()
        return st if pred(st) else None

    def pos(self, st=None):
        return [*self.here, self.z, self.facing]


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
    mv = Mover(link, mem, Human(Human.with_overrides("off", bump_p=1.0), seed=1), use_map=False)
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
    mv = Mover(link, mem, Human("off"), use_map=False)
    mv.walk_to(lambda: (3, -5), 0, "t")
    check("arrived", tuple(link.here) == (3, -5), str(link.here))
    check("no denies", link.denies == [], str(link.denies))


def test_door():
    print("== closed door on the route: one open-door request, then through ==")
    link = FakeLink((0, 0), facing=2, walls={(2, y) for y in range(-3, 4)} - {(2, 0)},
                    doors=[(2, 0)])
    mv = Mover(link, nav.WalkMemory(), Human("off"), doors=True, use_map=False)
    mv.walk_to(lambda: (4, 0), 0, "t")
    check("arrived through the door", tuple(link.here) == (4, 0), str(link.here))
    check("one open-door request", mv.doors_opened == 1, str(mv.doors_opened))


class GridWalk:
    """Stand-in for pathfind.Walk (Mover's map planner): only `open` tiles are walkable."""

    def __init__(self, open_tiles):
        self.open = set(open_tiles)
        self.dynamic = None

    def clear(self):
        pass

    def can_walk(self, x, y, z, d):
        nxt = nav.step((x, y), d)
        return (nxt[0], nxt[1], 0) if nxt in self.open else None


def map_mover(link, open_tiles):
    mv = Mover(link, nav.WalkMemory(), Human("off"), use_map=True)
    mv.walkers.put(0, GridWalk(open_tiles))
    return mv


HALLWAY = {(x, 0) for x in range(0, 7)}        # one tile wide, (0,0) .. (6,0)
OPEN = {(x, y) for x in range(-1, 6) for y in range(-2, 3)}


def walk_msg(mv, goal):
    try:
        mv.walk_to(lambda: goal, 0, "t")
        return "arrived"
    except Abort as e:
        return str(e)


def test_shove_through():
    print("== NPC standing in the only hallway: shove through it (enough stamina) ==")
    link = FakeLink((0, 0), facing=2, mobiles=[(3, 0, 10 ** 9)])
    msg = walk_msg(map_mover(link, HALLWAY), (6, 0))
    check("arrived", msg == "arrived" and tuple(link.here) == (6, 0), msg)
    check("shoved through the NPC's tile", link.shoved == [(3, 0)], str(link.shoved))


def test_go_around_when_cheap():
    print("== NPC on the straight line in the open: walk around, don't shove ==")
    link = FakeLink((0, 0), facing=2, mobiles=[(2, 0, 10 ** 9)])
    msg = walk_msg(map_mover(link, OPEN), (4, 0))
    check("arrived without shoving", msg == "arrived" and link.shoved == [], f"{msg} {link.shoved}")


def test_shove_denied():
    print("== shove denied (low stamina): not a wall; wait, then go once the NPC moves ==")
    link = FakeLink((0, 0), facing=2, mobiles=[(3, 0, 12)], can_shove=False)
    mv = map_mover(link, HALLWAY)
    msg = walk_msg(mv, (6, 0))
    check("arrived after the NPC walked off", msg == "arrived" and tuple(link.here) == (6, 0), msg)
    check("the shove was denied once and not learned as a wall",
          link.denies == [(3, 0)] and not mv.denied, f"{link.denies} {mv.denied}")
    link = FakeLink((0, 0), facing=2, mobiles=[(3, 0, 10 ** 9)], can_shove=False)
    saved, agent_link.MOBILE_WAIT_S = agent_link.MOBILE_WAIT_S, 0.0
    try:
        msg = walk_msg(map_mover(link, HALLWAY), (6, 0))
    finally:
        agent_link.MOBILE_WAIT_S = saved
    check("NPC never moves: aborts as 'cut by mobiles', not 'no route'",
          "cut by mobiles" in msg and "no route" not in msg, msg)
    link = FakeLink((0, 0), facing=2, mobiles=[(3, 0, 10 ** 9)])
    msg = walk_msg(map_mover(link, HALLWAY), (8, 0))
    check("a goal walls cut off is still 'no route' (mobiles or not)", "no route" in msg, msg)


class LayeredWalk:
    """Stand-in for pathfind.Walk with heights: `levels` {(x, y): {z, ...}};
    a step reaches a level on the next tile within 13 z (stairs), like the client."""

    def __init__(self, levels):
        self.levels = levels
        self.dynamic = None

    def clear(self):
        pass

    def can_walk(self, x, y, z, d):
        nx, ny = nav.step((x, y), d)
        zs = [nz for nz in self.levels.get((nx, ny), ()) if abs(nz - z) <= 13]
        return (nx, ny, min(zs, key=lambda nz: abs(nz - z))) if zs else None


def test_height_goal():
    print("== tree above a cave: chop from the tree's level, not from the cave below it ==")
    # cave z -20 along y=0 (x 0..6), stairs at (0,1) z -8, surface z 5 along y=2 (x 0..7).
    # The tree (6,1) z 5 is adjacent to cave tiles (5,0),(6,0) — directly below its level.
    levels = {(x, 0): {-20} for x in range(7)}
    levels[(0, 1)] = {-8}
    levels.update({(x, 2): {5} for x in range(8)})
    walls = {(x, y) for x in range(-1, 9) for y in range(-1, 4)} - set(levels)
    layered = LayeredWalk(levels)
    link = FakeLink((3, 0), facing=2, walls=walls, z=-20, z_walk=layered)
    mv = Mover(link, nav.WalkMemory(), Human("off"), use_map=True)
    mv.walkers.put(0, layered)
    mv.walk_to(lambda: (6, 1), 1, "t", z_ok=agent_link.reach_z(5, 20))
    check("ended beside the tree on the surface, not in the cave below it",
          tuple(link.here) in {(5, 2), (6, 2), (7, 2)} and link.z == 5, f"{link.here} z {link.z}")


if __name__ == "__main__":
    assert PROFILES["off"].bump_p == 0.0
    test_bump()
    test_no_bump_when_off()
    test_door()
    test_shove_through()
    test_go_around_when_cheap()
    test_shove_denied()
    test_height_goal()
    print(f"\nmover: {'ALL PASS' if not FAILURES else f'{len(FAILURES)} FAILURES'}")
    sys.exit(0 if not FAILURES else 1)

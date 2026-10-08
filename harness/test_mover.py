"""Deterministic tests for agent_link.Mover human behaviour on a fake grid world.

The fake Link answers walk packets like the server (confirm or deny, turn
first when the facing changes) and exposes the state-port shape Mover reads.
Human profiles are overridden so the behaviour under test always fires.

Run: python harness/test_mover.py   (no network, ~1 s: the 'off' profile still paces steps, on a
virtual clock that skips each sleep instead of waiting it out)
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agent_link  # noqa: E402
import nav  # noqa: E402
from agent_link import Abort, Mover  # noqa: E402
from humanize import Human, PROFILES  # noqa: E402

FAILURES = []


class VirtualClock:
    """Sleeping skips ahead instead of blocking: while installed, time.sleep(s) returns at once and
    adds s to an offset that time.monotonic() and time.time() include, so every pacing gap, poll
    deadline and time window (step cadence, STATE_REUSE_S, SHOVE_RETRY_S, MOBILE_WAIT_S ...) sees
    the same elapsed time as a real run, compute time included. Patches the time module itself, so
    it covers humanize, agent_link, travel_guard, threats, memory and this file alike; the code under
    test runs on this one thread (no background threads sleep while it is installed)."""

    def __init__(self):
        self.skipped = 0.0
        self.saved = None

    def sleep(self, seconds):
        if seconds < 0:
            raise ValueError("sleep length must be non-negative")   # as time.sleep
        self.skipped += seconds

    def __enter__(self):
        self.saved = (time.sleep, time.monotonic, time.time)
        _, mono, wall = self.saved
        time.sleep = self.sleep
        time.monotonic = lambda: mono() + self.skipped
        time.time = lambda: wall() + self.skipped
        return self

    def __exit__(self, *exc):
        time.sleep, time.monotonic, time.time = self.saved
        return False


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}{'' if cond else ' ' + detail}")
    if not cond:
        FAILURES.append(name)


class FakeLink:
    """A grid server: walls deny, doors deny until opened (0x12 0x58 next to them)."""

    def __init__(self, start, facing, walls=(), doors=(), mobiles=(), can_shove=True, z=0, z_walk=None,
                 teleports=None, deny_teleports=None, gates=None):
        # tile -> (gump serial, gump id, layout) a moongate there opens when stepped onto (sent
        # before the step's confirm, never closed by the server; captures 20260930_100200 ...)
        self.gates = dict(gates or {})
        self.gumps = {}                               # serial -> gump dict (state-port shape)
        self.replies = []                             # every 0xB1 the agent sent
        self.teleports = dict(teleports or {})   # tile -> where stepping onto it puts you
        # tile -> destination for teleporters that deny the step and move you a moment later
        # (the New Player Dungeon exit, live 2026-09-30): the jump shows from the second state poll
        self.deny_teleports = dict(deny_teleports or {})
        self.pending_jump, self.jump_armed = None, False
        self.here = list(start)
        self.facing = facing
        self.walls = set(walls)
        self.doors = {tuple(d): False for d in doors}
        self.mobiles = [list(m) for m in mobiles]     # [x, y, polls left before it walks off]
        self.polls = 0
        self.can_shove = can_shove                    # stamina sufficient for the server's shove rule
        self.shoved = []
        self.denies = []
        self.door_reqs = []                           # where we stood when each open-door request came
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
            if nxt in self.deny_teleports:
                self.pending_jump, self.jump_armed = self.deny_teleports[nxt], False
                return "OK"
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
            self.here = list(self.teleports.get(nxt, nxt))
            if tuple(self.here) in self.gates:
                serial, gid, layout = self.gates[tuple(self.here)]
                serial += len(self.gumps)              # a fresh gump each time it's stepped on
                self.gumps[serial] = {"serial": f"0x{serial:08X}", "gump_id": f"0x{gid:08X}",
                                      "layout": layout, "lines": ["Young Player Status"], "open": True}
                self.events.append({"ev": "gump_open", "serial": serial, "gump_id": gid,
                                    "layout": layout, "lines": ["Young Player Status"]})
        elif pkt[0] == 0xB1:
            self.replies.append(pkt)
            g = self.gumps.get(int.from_bytes(pkt[3:7], "big"))
            if g is not None:
                g["open"] = False
        elif pkt[0] == 0x12 and pkt[3] == 0x58:
            self.door_reqs.append((tuple(self.here), self.facing))
            for door in self.doors:
                if nav.chebyshev(door, tuple(self.here)) <= 1:
                    self.doors[door] = True
        return "OK"

    def act(self, pkt):
        self.send(pkt)

    def state(self):
        self.polls += 1
        if self.pending_jump is not None:
            if self.jump_armed:
                self.here, self.pending_jump = list(self.pending_jump), None
            else:
                self.jump_armed = True
        for m in self.mobiles:
            m[2] -= 1
        items = {f"0x{0x40000000 + i:08X}": {"graphic": 0x06AD, "x": d[0], "y": d[1]}
                 for i, d in enumerate(self.doors)}
        items.update({f"0x{0x40001000 + i:08X}": {"graphic": 0x0F6C, "x": g[0], "y": g[1], "z": 0}
                      for i, g in enumerate(self.gates)})
        mobiles = {f"0x{0x100 + i:08X}": {"x": m[0], "y": m[1]} for i, m in enumerate(self.mobiles) if m[2] > 0}
        return {"movement": {"pos": [*self.here, self.z, self.facing], "inflight": 0, "self_serial": 1,
                             "stalled": False, "rejects_in_row": 0},
                "world": {"mobiles": mobiles, "items": items, "gumps": list(self.gumps.values()),
                          "self": {"stam": 50 if self.can_shove else 3, "stam_max": 50}}}

    def wait(self, pred, timeout, poll=0.1, full=True):
        st = self.state()
        return st if pred(st) else None

    def pos(self, st=None):
        return [*self.here, self.z, self.facing]


def test_no_walk_into_known_wall():
    print("== a turn with a known wall straight ahead: turn, never step into the wall ==")
    # The stock client checks each step and sends nothing into a wall it knows
    # (PlayerMobile.Walk -> Pathfinder.CanWalk), so neither may the agent.
    link = FakeLink((0, 0), facing=2, walls={(4, 1), (4, 0), (4, -1)})
    mem = nav.WalkMemory()
    mem.add_blocked((3, 0), 2)
    mv = Mover(link, mem, Human("off"), use_map=False)
    mv.walk_to(lambda: (3, -5), 0, "t")
    check("arrived", tuple(link.here) == (3, -5), str(link.here))
    check("no denies", link.denies == [], str(link.denies))


def test_door():
    print("== closed door on the route: opened ahead, like the client's auto-open, then through ==")
    link = FakeLink((0, 0), facing=2, walls={(2, y) for y in range(-3, 4)} - {(2, 0)},
                    doors=[(2, 0)])
    mv = Mover(link, nav.WalkMemory(), Human("off"), doors=True, use_map=False)
    mv.walk_to(lambda: (4, 0), 0, "t")
    check("arrived through the door", tuple(link.here) == (4, 0), str(link.here))
    check("one open-door request, sent facing the door from the next tile (TryOpenDoors)",
          mv.doors_opened == 1 and link.door_reqs == [((1, 0), 2)], str(link.door_reqs))
    check("never walked into the closed door (walls it didn't know yet may deny)",
          (2, 0) not in link.denies, str(link.denies))
    link = FakeLink((1, 1), facing=4, walls={(2, y) for y in range(-3, 4)} - {(2, 0)},
                    doors=[(2, 0)])
    mv = Mover(link, nav.WalkMemory(), Human("off"), doors=True, use_map=False)
    mv.walk_to(lambda: (3, 0), 0, "t")
    check("a door reached by turning toward it is opened right after the turn",
          link.door_reqs == [((1, 1), 1)] and link.denies == [] and tuple(link.here) == (3, 0),
          f"{link.door_reqs} {link.denies} {link.here}")


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


def test_object_arrives_after_plan():
    print("== an object lands on the route after the plan: no step into it, replan around ==")
    # live 20260930_182751: a barrel arrived 1 s after the plan and the Mover walked into it.
    # A hallway with a long detour loop below it, so the first plan goes straight through (3, 0).
    grid = GridWalk(HALLWAY | {(1, 1), (1, 2), (2, 3), (3, 3), (4, 3), (5, 2), (5, 1)})
    link = FakeLink((0, 0), facing=2)
    mv = Mover(link, nav.WalkMemory(), Human("off"), use_map=True)
    mv.walkers.put(0, grid)
    orig = link.state

    def state():
        if tuple(link.here) == (1, 0) and (3, 0) in grid.open:   # the barrel shows up
            grid.open.discard((3, 0))
            link.walls.add((3, 0))
        return orig()
    link.state = state
    msg = walk_msg(mv, (6, 0))
    check("arrived around the object", msg == "arrived" and tuple(link.here) == (6, 0), msg)
    check("never sent a step into the new object (no deny), one step refused before sending",
          link.denies == [] and mv.refused_steps == 1, f"denies {link.denies} refused {mv.refused_steps}")


def test_max_route():
    print("== max_route: a planned route longer than the bound is refused before any step ==")
    # live 2026-10-05 (witcher_58): the way to a tree 28 tiles off was cut and the planner sent Outland Dan on a
    # 255-step detour into a fen daemon and a brackish water. Here the hallway is walled at (3, 0); the only way
    # round is the loop below (8 steps with the diagonals, instead of 6).
    loop = {(1, 1), (1, 2), (2, 3), (3, 3), (4, 3), (5, 2), (5, 1)}
    link = FakeLink((0, 0), facing=2)
    mv = map_mover(link, (HALLWAY - {(3, 0)}) | loop)
    try:
        mv.walk_to(lambda: (6, 0), 0, "t", max_route=7)
        msg = "arrived"
    except Abort as e:
        msg = str(e)
    check("refused as a detour, nothing walked", "detour" in msg and tuple(link.here) == (0, 0) and mv.steps == 0,
          f"{msg} {link.here} steps {mv.steps}")
    mv = map_mover(link, (HALLWAY - {(3, 0)}) | loop)
    mv.walk_to(lambda: (6, 0), 0, "t", max_route=8)
    check("within the bound: walked round", tuple(link.here) == (6, 0), str(link.here))


def test_danger_replan_on_route_only():
    print("== a new danger zone replans the walk only when it touches the rest of the route ==")
    # live 2026-10-05 (witcher_23): an air dragon wandering 20 tiles off moved its zone every few steps and the
    # walk to a tree replanned 123 times in 2 min, swinging between two routes
    for zone, want in ((((2, 6), 1), 0), (((4, 0), 0), 1)):     # off the route; on it (the goal tile)
        link = FakeLink((0, 0), facing=2)
        mv = map_mover(link, OPEN)
        plans = []
        real_plan, real_fresh = mv.plan, mv.fresh_state

        def plan(st, goal, mobiles=True, max_steps=None, real_plan=real_plan):
            plans.append(1)
            return real_plan(st, goal, mobiles, max_steps)

        def fresh(zone=zone, mv=mv, real_fresh=real_fresh):
            st = real_fresh()
            if tuple(link.here) != (0, 0) and not mv.danger:     # the zone appears after the first step
                mv.danger["seen"] = zone
                mv.replan_requested = True
            return st
        mv.plan, mv.fresh_state = plan, fresh
        mv.walk_to(lambda: (4, 0), 0, "t")
        check(f"zone {zone}: {'a replan' if want else 'no replan'}, arrived",
              len(plans) - 1 == want and tuple(link.here) == (4, 0), f"{len(plans) - 1} replans, at {link.here}")


def test_danger_replan_when_near():
    print("== a zone that moves onto the route far ahead: the walk goes on and replans once it gets near ==")
    # live 2026-10-05 (witcher_46): wolves moving far ahead replanned the walk on every step, swinging it between
    # two routes
    from agent_link import DANGER_LOOKAHEAD
    band = {(x, y) for x in range(0, 30) for y in range(-2, 3)}
    link = FakeLink((0, 0), facing=2)
    mv = map_mover(link, band)
    plans = []
    real_plan, real_fresh = mv.plan, mv.fresh_state

    def plan(st, goal, mobiles=True, max_steps=None):
        plans.append(tuple(link.here))
        return real_plan(st, goal, mobiles, max_steps)

    def fresh():
        st = real_fresh()
        if tuple(link.here) != (0, 0) and not mv.danger:      # after the first step, a zone across the band at 24
            mv.danger[("seen", 1)] = ((24, 0), 2)
            mv.replan_requested = True
        return st
    mv.plan, mv.fresh_state = plan, fresh
    mv.walk_to(lambda: (28, 0), 0, "t")
    check("one replan, once the zone's edge (x 22) was within DANGER_LOOKAHEAD; arrived",
          len(plans) == 2 and 22 - plans[1][0] <= DANGER_LOOKAHEAD and plans[1][0] > 2
          and tuple(link.here) == (28, 0), f"plans from {plans}, at {link.here}")


def test_no_pause_in_danger():
    print("== walk pauses: none while a creature's zone is set ==")
    # live 2026-10-05 (witcher_23): an 8.9 s walk pause with an air dragon closing in 10 tiles off, then its breath
    for danger, want in (({}, True), ({("seen", 7): ((20, 20), 13)}, False)):
        link = FakeLink((0, 0), facing=2)
        mv = map_mover(link, OPEN)
        paused = []
        mv.human.after_step = lambda: paused.append(1) or 0.0
        mv.danger = dict(danger)
        mv.walk_to(lambda: (4, 0), 0, "t")
        check(f"danger {danger}: {'pauses offered' if want else 'no pause'}", bool(paused) == want,
              f"{len(paused)} pauses")


def test_boxed_in():
    print("== creatures on every side: a walk replanning round them without getting nearer gives up ==")
    # live 2026-10-05 (witcher_137): 178 danger replans in 4.5 min, the walk swinging between two routes
    from agent_link import DANGER_STALL_REPLANS
    link = FakeLink((0, 0), facing=2)
    mv = map_mover(link, OPEN)
    mv.danger = {("seen", 1): ((0, 0), 30)}
    plans, flip = [], [1]

    def plan(st, goal, mobiles=True, max_steps=None):
        cur = tuple(link.here)
        flip[0] = -flip[0]                                  # east, then back west: never nearer
        step = (cur[0] + flip[0], cur[1])
        plans.append(cur)
        return [cur, step, *[(step[0] + flip[0] * k, 0) for k in range(1, 20)]], mv.walk_map(st)
    real_fresh = mv.fresh_state

    def fresh():
        st = real_fresh()
        mv.replan_requested = True                          # a creature moved: every step asks for a replan
        return st
    mv.plan, mv.fresh_state = plan, fresh
    try:
        mv.walk_to(lambda: (40, 0), 0, "t")
        msg = "arrived"
    except Abort as e:
        msg = str(e)
    check("boxed in after DANGER_STALL_REPLANS replans without a shorter route",
          "boxed in" in msg and len(plans) == DANGER_STALL_REPLANS + 1, f"{msg} plans {len(plans)}")


def test_danger_rims():
    print("== a zone that can't be avoided: the route keeps to its rim, not through the creature ==")
    # user 2026-10-05: "I saw paths Dan could take ... that didn't run straight into mobs"
    band = {(x, y) for x in range(0, 21) for y in range(-3, 4)}
    link = FakeLink((0, 0), facing=2)
    mv = map_mover(link, band)
    mv.danger = {("seen", 1): ((10, 0), 5)}            # covers the band's whole width from x 5 to 15
    path, _ = mv.plan(link.state(), nav.within((20, 0), 0))
    mid = [t for t in path if t[0] == 10]
    check("through the zone at its edge (|y| 3 at the creature's x), not along y 0",
          path is not None and mid and all(abs(y) == 3 for _, y in mid), str(path))


def test_danger_sticky():
    print("== after a danger replan the route keeps to the side it was on ==")
    # live 2026-10-05 (witcher_36): a wandering wisp swung the walk between two sides every second or two
    band = {(x, y) for x in range(0, 21) for y in range(-8, 6)}
    link = FakeLink((0, 0), facing=2)
    mv = map_mover(link, band)
    mv.danger = {("seen", 1): ((10, -3), 3)}           # north round it: y <= -7; south: y >= 1; both 20 steps
    sides = {}
    for side, ys in (("north", range(-8, -6)), ("south", range(1, 6))):
        mv.sticky = {(x, y) for x in range(0, 21) for y in ys} | {(0, 0), (20, 0)}
        path, _ = mv.plan(link.state(), nav.within((20, 0), 0))
        sides[side] = [y for x, y in path if x == 10]
    mv.sticky = set()
    check("the route stays on the side it was on",
          all(y <= -7 for y in sides["north"]) and all(y >= 1 for y in sides["south"]), str(sides))


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


def test_teleporter():
    print("== invisible teleporter on the route: survive it, remember it, route around it next time ==")
    import tempfile
    import memory
    store = memory.Memory(os.path.join(tempfile.mkdtemp(), "harness.db"))
    far = (5536, 530)                              # live 2026-09-30: (1912,2557) -> the NPD
    link = FakeLink((0, 0), facing=2, teleports={(3, 0): far})
    # a corridor along y=0 whose short way crosses (3, 0); a longer detour via y=1..2 exists
    open_tiles = {(x, 0) for x in range(-1, 8)} | {(2, 1), (2, 2), (3, 2), (4, 2), (4, 1)} | {far}
    mv = Mover(link, store, Human("off"), use_map=True)
    mv.walkers.put(0, GridWalk(open_tiles))
    msg = walk_msg(mv, (6, 0))
    check("the teleport doesn't crash the walk; it ends in 'no route' from the far side",
          "no route" in msg and tuple(link.here) == far and mv.teleports == 1, f"{msg} at {link.here}")
    check("remembered in the memory store: source tile -> destination",
          store.teleporters(0) == {(3, 0): (None, far[0], far[1], 0)}, str(store.teleporters(0)))
    link2 = FakeLink((0, 0), facing=2, teleports={(3, 0): far})
    mv2 = Mover(link2, store, Human("off"), use_map=True)            # the next goto: a fresh Mover
    mv2.walkers.put(0, GridWalk(open_tiles))
    msg = walk_msg(mv2, (6, 0))
    check("the next walk routes around the teleporter tile and arrives",
          msg == "arrived" and tuple(link2.here) == (6, 0) and mv2.teleports == 0, f"{msg} at {link2.here}")
    link3 = FakeLink((0, 0), facing=2, teleports={(3, 0): far})
    mv3 = Mover(link3, store, Human("off"), use_map=True)
    mv3.walkers.put(0, GridWalk(open_tiles))
    walk_msg(mv3, (3, 0))
    check("walking onto the teleporter on purpose (it is the goal) is allowed", tuple(link3.here) == far,
          str(link3.here))

    print("== a teleporter that denies the step, then moves you (NPD exit) ==")
    store2 = memory.Memory(os.path.join(tempfile.mkdtemp(), "harness.db"))
    home = (1911, 2556)
    link4 = FakeLink((0, 0), facing=2, deny_teleports={(3, 0): home})
    mv4 = Mover(link4, store2, Human("off"), use_map=True)
    mv4.walkers.put(0, GridWalk(open_tiles | {home}))
    msg = walk_msg(mv4, (6, 0))
    check("the jump after the deny counts as a teleport (not a wall at the source tile)",
          tuple(link4.here) == home and mv4.teleports == 1 and not mv4.denied, f"{msg} {link4.here} {mv4.denied}")
    check("and the teleporter is remembered", store2.teleporters(0) == {(3, 0): (None, home[0], home[1], 0)},
          str(store2.teleporters(0)))


RENOUNCE = 0xE2544541     # the renounce-Young prompt a Shelter moongate opens (20261001_191355)
RENOUNCE_LAYOUT = ("{ resizepic 28 23 11571 401 501 }{ button 22 24 2094 2095 1 0 1 }"
                   "{ text 64 45 2655 2 18 0 1 0 0 0 }{ button 60 460 247 248 1 0 2 }"
                   "{ button 300 460 241 242 1 0 3 }")


def test_moongate_gumps():
    print("== moongates on the route: close the gump of a gate we only pass over, keep the one we go to ==")
    import actions
    link = FakeLink((0, 0), facing=2, gates={(3, 0): (0x02B3BFD5, RENOUNCE, RENOUNCE_LAYOUT)})
    mv = map_mover(link, HALLWAY)
    msg = walk_msg(mv, (6, 0))
    g = next(iter(link.gumps.values()))
    check("walked on over the gate and arrived", msg == "arrived" and tuple(link.here) == (6, 0), msg)
    check("its gump was closed once, with the stock right-click reply (button 0)",
          link.replies == [actions.gump_reply(0x02B3BFD5, RENOUNCE, 0, RENOUNCE_LAYOUT, g["lines"])]
          and not g["open"] and mv.gate_gumps_closed == 1, f"{[r.hex() for r in link.replies]}")

    link = FakeLink((0, 0), facing=2, gates={(3, 0): (0x02B3BFD5, RENOUNCE, RENOUNCE_LAYOUT)})
    mv = map_mover(link, HALLWAY)
    mv.walk_to(lambda: (3, 0), 0, "t", gate=(3, 0))
    check("a walk that means to use the gate leaves its gump open and sends nothing",
          tuple(link.here) == (3, 0) and link.replies == [] and next(iter(link.gumps.values()))["open"],
          f"{link.here} {link.replies}")

    link = FakeLink((0, 0), facing=2, gates={(3, 0): (0x02B3BFD5, RENOUNCE, "{ noclose }" + RENOUNCE_LAYOUT)})
    walk_msg(map_mover(link, HALLWAY), (6, 0))
    check("a noclose gump is never answered (a stock client can't close it)", link.replies == [],
          f"{[r.hex() for r in link.replies]}")


def main():
    test_no_walk_into_known_wall()
    test_door()
    test_shove_through()
    test_go_around_when_cheap()
    test_object_arrives_after_plan()
    test_max_route()
    test_danger_replan_on_route_only()
    test_danger_replan_when_near()
    test_no_pause_in_danger()
    test_boxed_in()
    test_danger_rims()
    test_danger_sticky()
    test_shove_denied()
    test_height_goal()
    test_teleporter()
    test_moongate_gumps()
    print(f"\nmover: {'ALL PASS' if not FAILURES else f'{len(FAILURES)} FAILURES'}")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    with VirtualClock():
        rc = main()
    sys.exit(rc)

"""The overseer's walk guard (harness/travel_guard.py) on test_mover's fake grid world:
routes bend around hostile creatures, a goal inside one's reach or a hostile player
stops the walk, a ghost may still walk to a healer, and sightings are remembered so
the next walk avoids the area before anything is in view.

Run: python harness/test_travel_guard.py   (no network, ~20 s: the 'off' profile still paces steps)
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import threats  # noqa: E402
import travel_guard  # noqa: E402
from agent_link import Abort, Mover, cheb  # noqa: E402
from humanize import Human  # noqa: E402
from memory import Memory  # noqa: E402
from test_mover import FakeLink  # noqa: E402

FAILURES = []
HARPY = {"graphic": 0x1E, "notoriety": 3, "flags": 0x40, "name": "a harpy"}      # war mode: aggressive
RED = {"graphic": 0x190, "notoriety": 6, "flags": 0x20, "name": "Bastet"}
GHOST = 0x192
# live 2026-10-03 on Shelter Island (session 20261003_111419): a sheep in war mode while a
# player killed it, and a tamer's bonded phoenix in war mode next to its owner
WAR_SHEEP = {"graphic": 0xCF, "notoriety": 3, "flags": 0x40, "name": "a sheep"}
PET = {"graphic": 832, "notoriety": 1, "flags": 0x40, "name": "a phoenix", "pet": "bonded"}


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}{'' if cond else ' ' + str(detail)}")
    if not cond:
        FAILURES.append(name)


class World(FakeLink):
    """FakeLink plus labelled mobiles (threats.assess input), our body and a walk trace."""

    def __init__(self, start, others=(), body=0x190):
        super().__init__(start, facing=2)
        self.others = {0x2000 + i: dict(m) for i, m in enumerate(others)}
        self.body = body
        self.trace = [tuple(start)]

    def send(self, pkt):
        r = super().send(pkt)
        if tuple(self.here) != self.trace[-1]:
            self.trace.append(tuple(self.here))
        return r

    def state(self):
        st = super().state()
        st["world"]["mobiles"].update({f"0x{s:08X}": m for s, m in self.others.items()})
        st["world"]["self"].update(hits=100, hits_max=100, stats={"graphic": self.body})
        return st


def walk(world, goal, memory=None, guarded=True, params=None):
    import nav
    mv = Mover(world, memory if memory is not None else nav.WalkMemory(), Human("off"), use_map=False)
    if guarded:
        mv.guard = travel_guard.TravelGuard(mv, memory, goal=lambda: goal, log=lambda *_: None, params=params)
    try:
        mv.walk_to(lambda: goal, 0, "t", max_moves=200)
        return "arrived", mv
    except Abort as e:
        return str(e), mv


def closest(world, pos):
    return min(cheb(t, pos) for t in world.trace)


def test_routes_around_a_harpy():
    print("== a hostile creature on the straight route: go around it, out of its reach ==")
    w = World((0, 0), [dict(HARPY, x=12, y=0)])
    msg, mv = walk(w, (24, 0), guarded=False)
    check("unguarded (the old goto): right through its reach", msg == "arrived" and closest(w, (12, 0)) < 8,
          closest(w, (12, 0)))
    w = World((0, 0), [dict(HARPY, x=12, y=0)])
    msg, mv = walk(w, (24, 0))
    check("guarded: arrived, never within its zone (radius 11)",
          msg == "arrived" and closest(w, (12, 0)) >= 10, (msg, closest(w, (12, 0))))


def test_not_a_threat_to_us():
    print("== a war-mode sheep and a war-mode pet on the route: no zone, straight on; a learned body still is ==")
    learned = threats.Params(aggressive_bodies=frozenset({832}))     # phoenix body seen hostile before
    mem = Memory(os.path.join(tempfile.mkdtemp(), "h.db"))
    w = World((0, 0), [dict(WAR_SHEEP, x=8, y=0), dict(PET, x=16, y=0)])
    msg, mv = walk(w, (24, 0), memory=mem, params=learned)
    check("arrived on the straight line, no danger zone, no sighting recorded",
          msg == "arrived" and not mv.danger and len(w.trace) == 25 and not travel_guard.sightings(mem),
          (msg, mv.danger, len(w.trace), travel_guard.sightings(mem)))
    mem.close()
    w = World((0, 0), [dict(PET, x=12, y=0, flags=0, pet=None)])
    msg, mv = walk(w, (24, 0), params=learned)
    check("the same body untamed, out of war mode (learned aggressive): a zone, routed around",
          msg == "arrived" and len(mv.danger) == 1 and closest(w, (12, 0)) >= 10, (msg, mv.danger, closest(w, (12, 0))))


def test_goal_in_reach_or_hostile_player():
    print("== stop instead: the goal is in a creature's reach, a red is in view; a ghost may walk on ==")
    msg, _ = walk(World((0, 0), [dict(HARPY, x=12, y=0)]), (14, 0))
    check("goal inside the harpy's reach: stopped", "inside the reach of a harpy" in msg, msg)
    w = World((0, 0), [dict(RED, x=20, y=5)])
    msg, _ = walk(w, (24, 0))
    check("a red in view: stopped at once", "hostile player red Bastet" in msg and len(w.trace) <= 2, msg)
    w = World((0, 0), [dict(HARPY, x=12, y=0)], body=GHOST)
    msg, _ = walk(w, (24, 0))
    check("walking as a ghost (to a healer): creatures don't stop or divert it", msg == "arrived", msg)


def test_remembered():
    print("== sightings are remembered: the next walk avoids the area with nothing in view ==")
    mem = Memory(os.path.join(tempfile.mkdtemp(), "h.db"))
    w = World((0, 0), [dict(HARPY, x=12, y=0)])
    msg, _ = walk(w, (24, 0), memory=mem)
    seen = travel_guard.sightings(mem)
    check("one monster_seen job event, with where and what", msg == "arrived" and len(seen) == 1
          and (seen[0]["x"], seen[0]["y"]) == (12, 0) and seen[0]["body"] == 0x1E, seen)
    check("its body counts as aggressive from now on (even out of war mode)",
          0x1E in travel_guard.learned_params(mem).aggressive_bodies)
    w = World((0, 0), [])
    msg, _ = walk(w, (24, 0), memory=mem)
    check("nothing in view: the route still bends around the remembered spot",
          msg == "arrived" and closest(w, (12, 0)) > travel_guard.REMEMBER_R, (msg, closest(w, (12, 0))))
    mem.close()


if __name__ == "__main__":
    test_routes_around_a_harpy()
    test_goal_in_reach_or_hostile_player()
    test_not_a_threat_to_us()
    test_remembered()
    print(f"\ntravel guard: {'ALL PASS' if not FAILURES else f'{len(FAILURES)} FAILURES'}")
    sys.exit(0 if not FAILURES else 1)

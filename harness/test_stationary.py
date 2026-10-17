"""stationary.Stationary: the still-clock and the reposition walk.

Run:  python harness/test_stationary.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stationary  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


class FakeLink:
    def __init__(self, mover):
        self.mover = mover

    def state(self):
        return {"world": {"self": {"map": 0}, "buffs": {}}}


class FakeMover:
    """Steps and teleports counted like agent_link.Mover; walk_to moves along a scripted world."""

    def __init__(self):
        self.steps = self.teleports = 0
        self.sent_at = None
        self.link = FakeLink(self)
        self.pos = (5199, 737)
        self.walks, self.backs = [], []
        self.teleporter = {(5199, 736): (5522, 636)}       # unknown to the out-tile pick

    def walk_to(self, target, radius, label):
        goal = tuple(target())
        self.walks.append((label, goal))
        if goal in self.teleporter:
            self.steps, self.teleports, self.sent_at = self.steps + 1, self.teleports + 1, time.monotonic()
            self.pos = self.teleporter[goal]
            raise stationary.Abort(f"{label}: no route from {self.pos}")
        if self.pos[0] - goal[0] > 100:
            raise stationary.Abort(f"{label}: no route from {self.pos}")
        self.steps, self.sent_at, self.pos = self.steps + 1, time.monotonic(), goal

    def step_back(self, before, facet, label):
        self.backs.append((before, facet, label))
        self.pos = tuple(before)
        return True


class FakeHuman:
    class rng:
        @staticmethod
        def uniform(a, b):
            return a

    @staticmethod
    def choice(xs):
        return xs[0]


def test_steps_before_the_first_look():
    # live 20261017 14:27:22: the hunt's first look came after a 210 s walk-in: "no step for 211 s"
    m = FakeMover()
    still = stationary.Stationary(m, FakeHuman(), reposition_s=200)
    still.last_step_t -= 210                      # constructed 210 s ago
    m.steps, m.sent_at = 140, time.monotonic() - 1   # the walk-in's steps, the last one 1 s ago
    s = still.still_s()
    check("steps before the first look reset the clock", s < 5, f"still {s:.0f} s")
    check("so no reposition is due", still.due({"world": {"self": {}, "buffs": {}}}, True) is None)


def test_teleporters_dont_count():
    m = FakeMover()
    still = stationary.Stationary(m, FakeHuman(), reposition_s=200)
    still.last_step_t -= 210
    m.steps = m.teleports = 1
    check("a teleport alone is no step", still.still_s() >= 210)


def test_walk_off_steps_back_through_an_unknown_teleporter():
    # live 20261017 14:27:23: the out tile (5199, 736) was the up-stairs; "back: no route" on level 2, aborted
    m = FakeMover()
    still = stationary.Stationary(m, FakeHuman())
    still.out_tile = lambda st, home, k: (5199, 736)
    try:
        still.walk_off((5199, 737), 2, "reposition")
        err = None
    except stationary.Abort as e:
        err = str(e)
    check("no abort", err is None, err)
    check("stepped back once, from home", m.backs == [((5199, 737), 0, "reposition: out")], m.backs)
    check("then walked back home", m.walks[-1] == ("reposition: back", (5199, 737)) and m.pos == (5199, 737),
          (m.walks, m.pos))


def test_walk_off_without_a_teleport_no_step_back():
    m = FakeMover()
    still = stationary.Stationary(m, FakeHuman())
    still.out_tile = lambda st, home, k: (5200, 739)
    n = still.walk_off((5199, 737), 2, "reposition")
    check("no step back", m.backs == [], m.backs)
    check("out and back counted", n == 2, n)


if __name__ == "__main__":
    test_steps_before_the_first_look()
    test_teleporters_dont_count()
    test_walk_off_steps_back_through_an_unknown_teleporter()
    test_walk_off_without_a_teleport_no_step_back()
    print("ALL PASS" if not FAILS else f"FAILED: {FAILS}")
    sys.exit(1 if FAILS else 0)

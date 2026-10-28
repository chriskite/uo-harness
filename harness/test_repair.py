"""Offline proof of harness/repair.py against a fake bench that answers as the live ones did (capture
20261027_220057: "Repaired 7 items." 2.1 s after the double-click with the hammer on the way, "... out of charges
...", "... in need of repairs." at once).

Run: python harness/test_repair.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import actions  # noqa: E402
import repair  # noqa: E402

FAILURES = []
BENCH = {"serial": "0x5B3BF0CF", "name": "a repair bench", "pos": [3576, 3251, 6]}
SERIAL = 0x5B3BF0CF


def check(name, cond, extra=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAILURES.append(name)


class NoHuman:
    def wait(self, kind):
        pass


class House:
    """The bench by the landing; each double-click it heeds queues `answer` (events, one batch per poll)."""

    def __init__(self, answer, pos=(3575, 3251, 6), ignore_first=False):
        self.sent, self.pending, self.answer, self.ignore = [], [], answer, ignore_first
        self.state = {"world": {"items": {f"0x{SERIAL:08X}": {"graphic": 0x72AA, "x": 3576, "y": 3251, "z": 6}}},
                      "movement": {"pos": list(pos)}}

    def send(self, pkt):
        self.sent.append(pkt)
        if pkt == actions.dclick(SERIAL):
            if self.ignore:
                self.ignore = False
                return
            self.pending += [[e] for e in self.answer]

    def poll(self):
        return self.state, (self.pending.pop(0) if self.pending else [])


def line(text):
    return {"ev": "speech_heard", "serial": 0xFFFFFFFF, "text": text}


HAMMER = {"ev": "sound", "sound": 42, "x": 3576, "y": 3251, "z": 6}


def test_lines():
    print("== the bench's three answers ==")
    house = House([HAMMER, line("Gravel? No."), HAMMER, HAMMER, line("Repaired 7 items.")])
    out = repair.run(house, NoHuman(), BENCH, timeout=2.0)
    check("a repair: 7 items, after the hammer and an unrelated line, one double-click",
          out["ok"] and out["repaired"] == 7 and out["line"] == "Repaired 7 items."
          and house.sent == [actions.dclick(SERIAL)], str(out))
    out = repair.run(House([line("Repaired 1 item.")]), NoHuman(), BENCH, timeout=2.0)
    check("one item (singular)", out["ok"] and out["repaired"] == 1, str(out))
    out = repair.run(House([line("You do not have any items equipped or in your backpack in need of repairs.")]),
                     NoHuman(), BENCH, timeout=2.0)
    check("nothing to repair: ok, 0 repaired", out["ok"] and out["repaired"] == 0 and not out["empty"], str(out))
    out = repair.run(House([line("That repair bench is out of charges (use any repair kit on the bench to add "
                                 "additional charges).")]), NoHuman(), BENCH, timeout=2.0)
    check("out of charges: not ok, empty", not out["ok"] and out["empty"], str(out))


def test_failures():
    print("== out of reach, an ignored click, no answer ==")
    far = House([line("Repaired 1 item.")], pos=(3572, 3251, 6))
    try:
        repair.run(far, NoHuman(), BENCH, timeout=1.0)
        check("4 tiles off: refused", False)
    except repair.RepairError as e:
        check("4 tiles off: refused before anything is sent", far.sent == [] and "within 2 tiles" in str(e), str(e))
    house = House([HAMMER, line("Repaired 2 items.")], ignore_first=True)
    out = repair.run(house, NoHuman(), BENCH, timeout=3.0)
    check("the first click ignored (nothing back): clicked once more, then repaired",
          out["repaired"] == 2 and house.sent == [actions.dclick(SERIAL)] * 2, str(out))
    slow = House([HAMMER, HAMMER, HAMMER])
    try:
        repair.run(slow, NoHuman(), BENCH, timeout=1.0)
        check("no line: refused", False)
    except repair.RepairError as e:
        check("the hammer but no line: no second click, RepairError at the timeout",
              slow.sent == [actions.dclick(SERIAL)] and "no answer" in str(e), str(e))


if __name__ == "__main__":
    test_lines()
    test_failures()
    print(f"\n{len(FAILURES)} failure(s)" + (f": {FAILURES}" if FAILURES else ""))
    sys.exit(1 if FAILURES else 0)

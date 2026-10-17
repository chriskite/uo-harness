"""HuntLoop.attackers: who is on us when nothing swings at us (docs/HUNT_LOOP.md "Self care").

Run:  python harness/test_attackers.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import loop_hunt  # noqa: E402
import threats  # noqa: E402

FAILS = []
ME = 0x104A60


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


class FakeLink:
    @staticmethod
    def pos(st):
        return st["world"]["self"]["pos"]


def hunt(hurt_ago=1.0):
    h = object.__new__(loop_hunt.HuntLoop)
    h.link, h.dead = FakeLink(), set()
    h.hurt_t = {ME: time.monotonic() - hurt_ago} if hurt_ago is not None else {}
    return h


def state(name, dist, war=True):
    mob = {"x": 5250 + dist, "y": 653, "graphic": 720, "notoriety": 3, "name": name,
           "flags": threats.FLAG_WARMODE if war else 0}
    return {"movement": {"self_serial": ME},
            "world": {"self": {"pos": (5250, 653, 0)}, "swings": {}, "labels": {}, "mobiles": {"0x0001A477": mob}}}


def test_ranged():
    # live 20261017 15:43:14: a corpse purger's barrage from 10 tiles (-23, -28, a disease); no swing at us
    a = hunt().attackers(state("a corpse purger", 10))
    check("a corpse purger in war mode 10 tiles off while we take damage is on us",
          [x["how"] for x in a] == ["a ranged monster in war mode in reach while we take damage"], a)
    check("...up to 13 tiles (a calamity cast from 13)", len(hunt().attackers(state("a calamity", 13))) == 1)
    check("...not 14", hunt().attackers(state("a corpse purger", 14)) == [])
    check("...not when we aren't taking damage", hunt(hurt_ago=None).attackers(state("a corpse purger", 10)) == [])
    check("...not out of war mode", hunt().attackers(state("a corpse purger", 10, war=False)) == [])


def test_caster_and_melee():
    check("a caster (banshee) at 10 still counts", len(hunt().attackers(state("a banshee", 10))) == 1)
    check("...a caster at 11 doesn't", hunt().attackers(state("a banshee", 11)) == [])
    check("a melee monster 5 tiles off in war mode isn't on us", hunt().attackers(state("a shade wolf", 5)) == [])


if __name__ == "__main__":
    test_ranged()
    test_caster_and_melee()
    print("ALL PASS" if not FAILS else f"FAILED: {FAILS}")
    sys.exit(1 if FAILS else 0)

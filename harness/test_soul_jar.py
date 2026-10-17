"""The eerie-corpse alarm (soul_jar.EerieSeen in the proxy, alerts.eerie; docs/NOTES.md "Field of Souls").
The hunt runner's soul jar fill is in test_sim.py (test_soul_jar).

Run: python harness/test_soul_jar.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import alerts  # noqa: E402
import proxy  # noqa: E402
import soul_jar  # noqa: E402
import viz_feed  # noqa: E402
from sim import packets as P  # noqa: E402

FAILURES = []
CORPSE, OTHER, JAR, PACK = 0x407F9312, 0x407F9313, 0x40897AE1, 0x445E9F92


def check(name, cond, extra=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAILURES.append(name)


def tap(seen):
    t = proxy.SessionTap(viz_feed._Null(), viz_feed._Null(), viz_feed._Null())
    t.eerie = seen
    return t


def sightings(t):
    return [e["data"] for e in t.events if e["data"].get("ev") == "eerie_corpse"]


def test_alarm():
    print("== the proxy sounds each eerie corpse once, whoever sees it ==")
    sounds = []
    orig = alerts.eerie
    alerts.eerie = lambda sound=True: sounds.append(1)
    try:
        seen = soul_jar.EerieSeen()
        a, b = tap(seen), tap(seen)
        corpse = P.ground_item(CORPSE, soul_jar.EERIE_CORPSE_GRAPHIC, 1, 5243, 667, 0)
        a._world(proxy.S2C, corpse)
        check("first sight: the wail and an eerie_corpse event with its tile",
              len(sounds) == 1 and [(s["serial"], s["x"], s["y"]) for s in sightings(a)] == [("0x407F9312", 5243, 667)],
              str(sightings(a)))
        a._world(proxy.S2C, P.delete(CORPSE))                 # out of range: the client drops it...
        a._world(proxy.S2C, corpse)                           # ...and the server shows it again on the way back
        b._world(proxy.S2C, corpse)                           # our other character comes by
        check("seen again, or by another session: no second wail", len(sounds) == 1 and len(sightings(b)) == 0,
              f"{len(sounds)} sounds")
        a._world(proxy.S2C, P.ground_item(OTHER, soul_jar.EERIE_CORPSE_GRAPHIC, 1, 5250, 670, 0))
        check("another eerie corpse: its own wail", len(sounds) == 2)
        a._world(proxy.S2C, P.ground_item(OTHER + 10, 0x6C6F, 1, 5243, 668, 0))        # its "eerie whispers"
        a._world(proxy.S2C, P.contained(JAR, soul_jar.EERIE_CORPSE_GRAPHIC, 1, PACK))   # the graphic, not on the ground
        check("its pieces and the graphic inside a container: nothing", len(sounds) == 2)
        replay = tap(None)                                    # viz_feed / replays: no EerieSeen
        replay._world(proxy.S2C, P.ground_item(CORPSE + 100, soul_jar.EERIE_CORPSE_GRAPHIC, 1, 1, 1, 0))
        check("a tap without an EerieSeen (a replay) never sounds", len(sounds) == 2 and not sightings(replay))
    finally:
        alerts.eerie = orig


def main():
    os.environ[alerts.QUIET_ENV] = "1"
    test_alarm()
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILED: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

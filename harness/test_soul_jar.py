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
CORPSE, OTHER, DECOR, JAR, PACK = 0x41194AF0, 0x407F9312, 0x40B8C1A3, 0x40897AE1, 0x445E9F92
WHISPERS = soul_jar.WHISPERS_GRAPHIC


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
        # live 20261017_172358 17:26:17.9: the corpse 0x41194AF0, art 0xB202, its eerie whispers at serial +7
        corpse = [P.ground_item(CORPSE, 0xB202, 1, 1778, 1503, 60), P.ground_item(CORPSE + 1, 0x122D, 1, 1778, 1503, 60),
                  P.ground_item(CORPSE + 7, WHISPERS, 1, 1779, 1503, 60)]
        a._world(proxy.S2C, corpse[0])
        check("the corpse's art alone: nothing yet (decor uses it too)", not sounds)
        for pkt in corpse[1:]:
            a._world(proxy.S2C, pkt)
        check("its whispers a few serials on: the wail and an eerie_corpse event with the corpse's tile",
              len(sounds) == 1 and [(s["serial"], s["x"], s["y"]) for s in sightings(a)] == [("0x41194AF0", 1778, 1503)],
              str(sightings(a)))
        for pkt in corpse:                                    # out of range and back...
            a._world(proxy.S2C, P.delete(int.from_bytes(pkt[4:8], "big")))
        for pkt in corpse:
            a._world(proxy.S2C, pkt)
            b._world(proxy.S2C, pkt)                          # ...and our other character comes by
        check("seen again, or by another session: no second wail", len(sounds) == 1 and not sightings(b),
              f"{len(sounds)} sounds")
        a._world(proxy.S2C, P.ground_item(OTHER + 6, WHISPERS, 1, 5244, 667, 0))      # whispers first this time
        a._world(proxy.S2C, P.ground_item(OTHER, 0xB1F3, 1, 5243, 667, 0))
        check("another one, its pieces in the other order: its own wail", len(sounds) == 2)
        a._world(proxy.S2C, P.ground_item(DECOR, 0xB214, 1, 1500, 1600, 0))           # a decor body
        a._world(proxy.S2C, P.ground_item(DECOR + 40, WHISPERS, 1, 1500, 1601, 0))    # a spell's whispers, unrelated
        a._world(proxy.S2C, P.contained(JAR, 0xB1C5, 1, PACK))
        a._world(proxy.S2C, P.contained(JAR + 3, WHISPERS, 1, PACK))
        check("decor art, unrelated whispers, the pair inside a container: nothing", len(sounds) == 2)
        replay = tap(None)                                    # viz_feed / replays: no EerieSeen
        for pkt in corpse:
            replay._world(proxy.S2C, pkt)
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

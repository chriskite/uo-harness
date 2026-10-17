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


def test_spirit():
    print("== our name-match spirit: one chime when it comes into view, in this field session only ==")
    sounds = []
    orig = alerts.spirit
    alerts.spirit = lambda sound=True: sounds.append(1)
    named = "If you put the spirit of {} to rest, you will receive a 33.0% bonus to all further points earned this session."
    remon, karin, ava = 0x00042527, 0x00021EAD, 0x00031F00

    def show(t, serial, name, x=7161, y=639):           # 0x78, then the label the stock client's 0x09 brings
        t._world(proxy.S2C, P.mobile(serial, 400, x, y, -45))
        t._world(proxy.S2C, P.says(serial, name, name, kind=6, hue=946, body=400))

    def seen(t):
        return [(e["data"]["name"], e["data"]["serial"], e["data"]["x"]) for e in t.events
                if e["data"].get("ev") == "spirit_seen"]
    try:
        clock = [1000.0]
        a = tap(None)
        a.spirit, a.wall = soul_jar.SpiritWatch(), lambda: clock[0]
        show(a, remon, "Remon Stace")
        check("before the server names our spirit: nothing", not sounds)
        a._world(proxy.S2C, P.delete(remon))
        # live 20261017_172358 17:27:09, 3 s after entering; the spirit in view at 17:28:32
        a._world(proxy.S2C, P.system_text(named.format("Remon Stace")))
        show(a, karin, "Karin Winckel")                   # another player's spirit
        check("named; another spirit in view: nothing", not sounds)
        show(a, remon, "Remon Stace")
        check("ours comes into view: the chime and spirit_seen with its tile",
              len(sounds) == 1 and seen(a) == [("Remon Stace", "0x00042527", 7161)], str(seen(a)))
        a._world(proxy.S2C, P.delete(remon))
        show(a, remon, "Remon Stace")
        check("out of view and back: no second chime", len(sounds) == 1)
        a._world(proxy.S2C, P.system_text(soul_jar.EJECTED_TEXT))
        clock[0] += 120
        a._world(proxy.S2C, P.system_text(named.format("Ava Estney")))   # the next jar's session
        show(a, ava, "Ava Estney", 6305, 707)
        check("the next session's spirit: its own chime", len(sounds) == 2)
        a._world(proxy.S2C, P.system_text(named.format("Milo Fabre")))
        clock[0] += soul_jar.FIELD_S + 1
        show(a, 0x00031F01, "Milo Fabre")
        check("named over 16 minutes ago (the session is over): nothing", len(sounds) == 2)
        b = tap(None)
        b.spirit = soul_jar.SpiritWatch()
        show(b, remon, "Remon Stace")
        b._world(proxy.S2C, P.system_text(named.format("Remon Stace")))
        check("already in view when the server names it: the chime then", len(sounds) == 3)
        replay = tap(None)                                    # viz_feed / replays: no SpiritWatch
        replay._world(proxy.S2C, P.system_text(named.format("Remon Stace")))
        show(replay, remon, "Remon Stace")
        check("a tap without a SpiritWatch (a replay) never sounds", len(sounds) == 3)
    finally:
        alerts.spirit = orig


def main():
    os.environ[alerts.QUIET_ENV] = "1"
    test_alarm()
    test_spirit()
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILED: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

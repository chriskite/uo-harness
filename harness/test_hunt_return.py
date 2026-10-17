"""hunt_return: the carried value and V* (docs/HUNT_LOOP.md "Taking the loot home").

Run: python harness/test_hunt_return.py"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import combat  # noqa: E402
import hunt_return  # noqa: E402

FAILURES = []
ME, PACK, BAG = 0x104A60, 0x445E9F92, 0x46E9ABA2
PRICES = {f"loot:{k}": {"price_gp": v} for k, v in {
    "research materials": 25000, "blood aspect core": 14000, "aspect core": 10000, "chromatic core": 35000,
    "arcane scroll": 1000, "gold ore map": 12000, "citrine": 20, "amber": 40, "skill scroll": 5000}.items()}


def check(name, cond, extra=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAILURES.append(name)


def test_price():
    print("== prices by the client's name, a kind's row for the rest ==")
    cases = [({"graphic": 0x5F72, "name": "research materials : 2"}, 25000),
             ({"graphic": 0x0F91, "hue": 2087, "name": "blood aspect core"}, 14000),
             ({"graphic": 0x0F91, "hue": 1150, "name": "void aspect core"}, 10000),        # unseen kind: 10k (user)
             ({"graphic": 0x0FB9}, 35000),                                                   # tiledata 'chroma core'
             ({"graphic": 0x0EF3, "hue": 2609}, 1000),                                       # a pink blank scroll
             ({"graphic": 0x1120A, "name": "gold ore map"}, 12000),
             ({"graphic": 0x0F25}, 40),                                                      # 'piece of amber'
             ({"graphic": 0x227A, "hue": 2291, "name": "Alchemy skill mastery scroll"}, 5000),
             ({"graphic": 0x1120A, "name": "iron ore map"}, None)]
    for it, want in cases:
        got = hunt_return.price(it, PRICES)
        check(f"{it.get('name') or hex(it['graphic'])}: {want}", got == want, str(got))


def test_carried():
    print("== the carried value: the pack's top level, gold and priced loot; our bags aside ==")
    items = {f"0x{PACK:08X}": {"graphic": 0x0E75, "layer": 0x15, "container": f"0x{ME:08X}"},
             f"0x{BAG:08X}": {"graphic": 0x0E76, "container": f"0x{PACK:08X}"},
             "0x40000001": {"graphic": combat.GOLD_GRAPHIC, "amount": 12011, "container": f"0x{PACK:08X}"},
             "0x40000002": {"graphic": 0x0F15, "amount": 6, "name": "citrine : 6", "container": f"0x{PACK:08X}"},
             "0x40000003": {"graphic": 0x5F72, "amount": 1, "name": "research materials", "container": f"0x{PACK:08X}"},
             "0x40000004": {"graphic": 0x4E2E, "amount": 1, "container": f"0x{PACK:08X}"},                # a shawm
             "0x40000005": {"graphic": 0x0F0C, "amount": 30, "container": f"0x{BAG:08X}"},                # our potions
             "0x40000006": {"graphic": combat.GOLD_GRAPHIC, "amount": 500, "container": f"0x{BAG:08X}"}}
    c = hunt_return.carried({"world": {"items": items}, "movement": {"self_serial": ME}}, PRICES)
    check("gold 12011 + 6 citrine 120 + research materials 25000; the shawm unpriced; the bag's potions and gold not "
          "counted", c == {"gold": 12011, "items_gp": 25120, "total": 37131, "unpriced": ["shawm"]}, str(c))


def test_model():
    print("== V* = r sqrt(2T/h): live numbers, the prior without them, other places not counted ==")
    visits = [{"spot": [5199, 737], "t_start": 0.0, "t_end": 60.0 * 70, "gold": 93_000},
              {"spot": [1912, 2557], "t_start": 0.0, "t_end": 60.0 * 600, "gold": 1}]          # another place
    deaths = [{"x": 5183, "y": 682, "facet": 0}, {"x": 5241, "y": 651, "facet": 0}, {"x": 5200, "y": 700, "facet": 0},
              {"x": 1500, "y": 1600, "facet": 0}]
    m = hunt_return.model(visits, deaths, (5199, 737), 0)
    r, h = 93_000 / 70, (hunt_return.PRIOR_DEATHS + 3) / (hunt_return.PRIOR_MIN + 70)
    want = int(r * math.sqrt(2 * hunt_return.TRIP_MIN / h))
    check(f"70 min, 93k gold, 3 deaths there: V* {want}", m["visits"] == 1 and m["deaths"] == 3 and m["v_star"] == want,
          str(m))
    p = hunt_return.model([], [], (5199, 737), 0)
    check("no data: the priors (1000 gold/min, a death an hour)", p["v_star"] == int(
        hunt_return.PRIOR_GOLD_MIN * math.sqrt(2 * hunt_return.TRIP_MIN * hunt_return.PRIOR_MIN)), str(p))


def main():
    test_price()
    test_carried()
    test_model()
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILED: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

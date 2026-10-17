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


def death(t, x=5199, y=700, cause="mob"):
    return {"kind": "death", "t": t, "x": x, "y": y, "facet": 0, "data": {"cause": cause}}


def leave(t, why, out_s=None, x=5199, y=700):
    return {"kind": "leave", "t": t, "x": x, "y": y, "facet": 0,
            "data": {"why": why, **({"out_s": out_s} if out_s is not None else {})}}


def test_model():
    print("== V* = r sqrt(2T/h): h = encounters x P(death | encounter) + monster deaths; T measured ==")
    P = hunt_return
    visits = [{"spot": [5199, 737], "t_start": 0.0, "t_end": 60.0 * 70, "gold": 93_000},
              {"spot": [1912, 2557], "t_start": 0.0, "t_end": 60.0 * 600, "gold": 1}]          # another place
    events = [death(1000.0, cause="unknown"), leave(1000.4, "red player Tugarin zmey in view at 18 tiles"),
              death(2000.0, cause="pk"),                                     # a PK death with no leave: an encounter
              death(3000.0, cause="mob"),
              leave(4000.0, "red player Chaos Agent in view at 17 tiles"),   # escapes
              leave(5000.0, "hostile player Hallik (grey) at 3 tiles"),
              leave(6000.0, "Bob is attacking us (server notice)"),
              leave(7000.0, "hits 41/100 below 50%"),                         # not a player
              death(8000.0, x=1500, y=1600)]                                  # another place
    m = P.model(visits, events, (5199, 737), 0)
    enc, pk = 5, 2
    h_pk = (P.ENC_PRIOR + enc) / (P.ENC_PRIOR_MIN + 70) * (P.P_PK_PRIOR[0] + pk) / (sum(P.P_PK_PRIOR) + enc)
    h_mob = (P.MOB_PRIOR + 1) / (P.ENC_PRIOR_MIN + 70)
    t_min = P.OUT_PRIOR_MIN + P.DROPOFF_PRIOR_MIN + P.IN_PRIOR_MIN
    want = int(93_000 / 70 * math.sqrt(2 * t_min / (h_pk + h_mob)))
    check("4 player leaves + 1 PK death unseen = 5 encounters; the death 0.4 s before a red's leave is a PK death",
          (m["encounters"], m["pk_deaths"], m["deaths"]) == (enc, pk, 3), str(m))
    check(f"V* {want} from the split hazard", m["v_star"] == want, str(m))
    more = P.model(visits, events + [leave(9000.0 + i, "red player X in view at 18 tiles") for i in range(5)],
                   (5199, 737), 0)
    check("five more escapes: P(death | encounter) falls", more["p_pk"] < m["p_pk"], f"{more['p_pk']} vs {m['p_pk']}")
    p = P.model([], [], (5199, 737), 0)
    h0 = P.ENC_PRIOR / P.ENC_PRIOR_MIN * P.P_PK_PRIOR[0] / sum(P.P_PK_PRIOR) + P.MOB_PRIOR / P.ENC_PRIOR_MIN
    check("no data: the priors", p["v_star"] == int(P.PRIOR_GOLD_MIN * math.sqrt(2 * t_min / h0)), str(p))


def test_trip():
    print("== T: the way out, the drop-off and the way in, each shrunk to its prior ==")
    P = hunt_return
    visits = [{"spot": [5199, 737], "t_start": 0.0, "t_end": 600.0, "gold": 1000, "in_s": 132.0},
              {"spot": [5199, 737], "t_start": 700.0, "t_end": 1300.0, "gold": 1000, "in_s": 210.0},
              {"spot": [1912, 2557], "t_start": 0.0, "t_end": 600.0, "gold": 1, "in_s": 6000.0}]   # elsewhere
    events = [leave(600.0, "red player X in view at 17 tiles", out_s=9.0),
              leave(1300.0, "taking the loot home: 30000 gp carried", out_s=81.0),
              {"kind": "dropoff", "t": 640.0, "x": 403, "y": 923, "facet": 3, "data": {"ok": True, "elapsed_s": 29.0}}]
    m = P.model(visits, events, (5199, 737), 0)
    want = {"out": (P.OUT_PRIOR_MIN + 9 / 60 + 81 / 60) / 3, "dropoff": (P.DROPOFF_PRIOR_MIN + 29 / 60) / 2,
            "in": (P.IN_PRIOR_MIN + 132 / 60 + 210 / 60) / 3}
    check("each part's mean with its prior as one more sample; the drop-off at home counts",
          all(abs(m["trip"][k] - want[k]) < 0.01 for k in want) and m["trip_samples"] == {"out": 2, "dropoff": 1, "in": 2},
          str(m["trip"]))
    check("T is their sum", abs(m["trip_min"] - sum(want.values())) < 0.02, str(m["trip_min"]))


def main():
    test_price()
    test_carried()
    test_model()
    test_trip()
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILED: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

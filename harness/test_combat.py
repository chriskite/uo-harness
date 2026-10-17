"""combat.worth_looting against the items live corpses held (Logan Wolf's Mausoleum runs; capture 20261017_110506).

Run: python harness/test_combat.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import combat  # noqa: E402

FAILURES = []


def check(name, cond, extra=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAILURES.append(name)


def test_worth_looting():
    print("== loot: reagents, spell scrolls and worn gear stay; Outlands' hued valuables on those graphics come out ==")
    taken = {"an arcane scroll (a pink blank scroll, 11:11:05)": {"graphic": 0x0EF3, "hue": 2609},
             "a blood aspect core (the wyrm's heart graphic, hued, 11:26:05)": {"graphic": 0x0F91, "hue": 2087},
             "a skill scroll (a hued chivalry scroll graphic, 'scroll of calling', 11:26:31)":
                 {"graphic": 0x227A, "hue": 2291, "name": "scroll of calling"},
             "gold": {"graphic": combat.GOLD_GRAPHIC, "amount": 600},
             "a sapphire": {"graphic": 0x0F19, "hue": 0}}
    left = {"an explosion scroll": {"graphic": 0x1F57, "hue": 0},
            "a plain blank scroll": {"graphic": 0x0EF3, "hue": 0},
            "nightshade": {"graphic": 0x0F88, "hue": 0},
            "a spellbook (hand layer)": {"graphic": 0x0EFA, "hue": 2606},
            "an unidentified cestus (Outlands weapon art: Wearable, layer 0)": {"graphic": 0x79A5, "hue": 0},
            "a bone shield (Outlands armour art)": {"graphic": 0x799A, "hue": 0}}
    taken["a shawm (an instrument: maybe supremely melodious)"] = {"graphic": 0x4E2E, "hue": 0}
    for name, it in taken.items():
        check(f"taken: {name}", combat.worth_looting(it))
    for name, it in left.items():
        check(f"left: {name}", not combat.worth_looting(it))


def main():
    test_worth_looting()
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILED: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

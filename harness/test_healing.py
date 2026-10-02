"""Tests for harness/healing.py: what to heal with (potion first, else Heal or
Greater Heal by the missing hits), on synthetic world snapshots (no network).

Run: python harness/test_healing.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import healing  # noqa: E402

FAILURES = []
ME = 0x0020F127
PACK, BAG, BANK = 0x4B6AA305, 0x4B6BB8B3, 0x4C40B866
POT_SMALL, POT_BIG, POT_BANK = 0x4B6BB8B5, 0x4B6BB8C0, 0x4B6BB8C1


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(name)


def h(s):
    return f"0x{s:08X}"


def world(hits, mana=78, magery=600, potions=((POT_SMALL, BAG, 2), (POT_BIG, PACK, 8)), hits_max=98):
    items = {h(PACK): {"graphic": 0x0E75, "layer": 0x15, "container": h(ME)},
             h(BAG): {"graphic": 0x0E76, "container": h(PACK)},
             h(BANK): {"graphic": 0x0E7C, "layer": 0x1D, "container": h(ME)},
             h(POT_BANK): {"graphic": healing.HEAL_POTION_GRAPHIC, "amount": 1, "container": h(BANK)}}
    for serial, where, n in potions:
        items[h(serial)] = {"graphic": healing.HEAL_POTION_GRAPHIC, "amount": n, "container": h(where)}
    return {"self": {"hits": hits, "hits_max": hits_max, "mana": mana,
                     "skills": {str(healing.MAGERY_SKILL_ID): {"value": magery}}},
            "items": items}


def pick(c):
    return (c.kind, c.potion if c.kind == "potion" else c.spell)


def main():
    print("== potion first ==")
    c = healing.choose(world(50), ME, potion_ready=True)
    check("a potion when one can be drunk: the smallest stack, found in a bag in the pack",
          pick(c) == ("potion", h(POT_SMALL)), str(c))
    c = healing.choose(world(97), ME, potion_ready=True)
    check("a potion even for 1 missing hit (the user's rule: a pot whenever possible)",
          pick(c) == ("potion", h(POT_SMALL)), str(c))
    c = healing.choose(world(98), ME, potion_ready=True)
    check("nothing at full health", c.kind is None and c.missing == 0, str(c))
    c = healing.choose(world(50, potions=()), ME, potion_ready=True)
    check("a potion in the bank box doesn't count: a spell instead",
          c.kind == "spell" and "no heal potion" in c.why, str(c))

    print("== spells by missing hits (Magery 60: Heal 6-7, Greater Heal 24-30) ==")
    check("Greater Heal break-even at Magery 60 is 19 (Heal avg 6.6 x 11 / 4 = 18.15)",
          healing.gheal_break_even(60.0) == 19, str(healing.gheal_break_even(60.0)))
    c = healing.choose(world(98 - 18), ME, potion_ready=False)
    check("18 missing, potion cooling down: Heal", pick(c) == ("spell", healing.HEAL) and "cooling" in c.why, str(c))
    c = healing.choose(world(98 - 19), ME, potion_ready=False)
    check("19 missing: Greater Heal", pick(c) == ("spell", healing.GREATER_HEAL), str(c))
    check("break-even scales with Magery (100: Heal avg 11 -> 31)", healing.gheal_break_even(100.0) == 31,
          str(healing.gheal_break_even(100.0)))
    c = healing.choose(world(98 - 10), ME, potion_ready=False, gheal_min_missing=10)
    check("--gheal-min-missing overrides the break-even", pick(c) == ("spell", healing.GREATER_HEAL), str(c))

    print("== mana ==")
    c = healing.choose(world(40, mana=10), ME, potion_ready=False)
    check("Greater Heal wanted but 10 mana: Heal instead", pick(c) == ("spell", healing.HEAL) and "short" in c.why,
          str(c))
    c = healing.choose(world(90, mana=3), ME, potion_ready=False)
    check("3 mana and no potion ready: no heal possible", c.kind is None and c.missing == 8, str(c))
    c = healing.choose(world(40, mana=0), ME, potion_ready=True)
    check("no mana but a potion ready: the potion", c.kind == "potion", str(c))

    print("== potion clock ==")
    clock = healing.PotionClock()
    check("never drunk: ready", clock.ready(100.0))
    clock.started(100.0)
    check("9.9 s after a drink: not ready", not clock.ready(109.9))
    check("10 s after: ready", clock.ready(110.0))

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILURES")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

"""Tests for harness/healing.py: what to heal with (potion first, else Heal or
Greater Heal by the missing hits and what can be paid for: mana, reagents or a
spellstone), and the flight aid (FleeAid: pouch, cure, heal, refresh while running
or between recall casts), on synthetic world snapshots (no network).

Run: python harness/test_healing.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import combat  # noqa: E402
import healing  # noqa: E402

FAILURES = []
ME = 0x0020F127
PACK, BAG, BANK = 0x4B6AA305, 0x4B6BB8B3, 0x4C40B866
POT_SMALL, POT_BIG, POT_BANK = 0x4B6BB8B5, 0x4B6BB8C0, 0x4B6BB8C1
REG_BAG, STONE = 0x4B6BB900, 0x4B6BB9FF
ALL_REGS = tuple(combat.REAGENTS)
HEAL_REGS = combat.SPELL_REAGENTS[healing.HEAL]                  # garlic, ginseng, spiders' silk


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(name)


def h(s):
    return f"0x{s:08X}"


def world(hits, mana=78, magery=600, potions=((POT_SMALL, BAG, 2), (POT_BIG, PACK, 8)), hits_max=98,
          regs=ALL_REGS, stone=None):
    items = {h(PACK): {"graphic": 0x0E75, "layer": 0x15, "container": h(ME)},
             h(BAG): {"graphic": 0x0E76, "container": h(PACK)},
             h(REG_BAG): {"graphic": 0x0E76, "container": h(BAG)},
             h(BANK): {"graphic": 0x0E7C, "layer": 0x1D, "container": h(ME)},
             h(POT_BANK): {"graphic": healing.HEAL_POTION_GRAPHIC, "amount": 1, "container": h(BANK)}}
    for serial, where, n in potions:
        items[h(serial)] = {"graphic": healing.HEAL_POTION_GRAPHIC, "amount": n, "container": h(where)}
    for i, g in enumerate(regs):
        items[h(REG_BAG + 1 + i)] = {"graphic": g, "amount": 3, "container": h(REG_BAG)}
    if stone is not None:
        items[h(STONE)] = {"graphic": 0x3F1F, "container": h(PACK), "name": stone}
    return {"self": {"hits": hits, "hits_max": hits_max, "mana": mana,
                     "skills": {str(healing.MAGERY_SKILL_ID): {"value": magery}}},
            "items": items}


def pick(c):
    return (c.kind, c.potion if c.kind == "potion" else c.spell)


CURE, HEALP, REFRESH, POUCH, POUCH_SPENT, BAG_CURE = (0x4B6BC001, 0x4B6BC002, 0x4B6BC003, 0x4B6BC004,
                                                     0x4B6BC005, 0x4B6BC006)


def flight(hits=100, stam=25, flags=0x20, poisoned=None, cure=True, heal=True, refresh=True, pouch=True,
           cure_in_bag=False):
    """Outland Dan mid-flight (100 hits, 25 stamina, ctl status 2026-10-06): potions and pouches in the pack."""
    items = {h(PACK): {"graphic": 0x0E75, "layer": 0x15, "container": h(ME)},
             h(BAG): {"graphic": 0x0E76, "container": h(PACK)},
             h(POUCH_SPENT): {"graphic": 0x0E79, "hue": 0, "container": h(PACK)}}       # gone off: no use
    for on, serial, g in ((cure, CURE, healing.CURE_POTION_GRAPHIC), (heal, HEALP, healing.HEAL_POTION_GRAPHIC),
                          (refresh, REFRESH, healing.REFRESH_POTION_GRAPHIC)):
        if on:
            items[h(serial)] = {"graphic": g, "amount": 5, "container": h(PACK)}
    if cure_in_bag:
        items[h(BAG_CURE)] = {"graphic": healing.CURE_POTION_GRAPHIC, "amount": 5, "container": h(BAG)}
    if pouch:
        items[h(POUCH)] = {"graphic": 0x0E79, "hue": 38, "container": h(PACK)}
    return {"self": {"hits": hits, "hits_max": 100, "stam": stam, "stam_max": 25, "poisoned": poisoned,
                     "stats": {"flags": flags}}, "items": items}


def kind(aid):
    return None if aid is None else (aid.kind, aid.serial)


def flight_aid():
    print("== flight aid (FleeAid): one use at a time, by priority ==")
    t = 1000.0
    a = healing.FleeAid()
    w = flight(hits=55, stam=10, flags=0x21, poisoned=True)
    check("paralyzed, poisoned, hurt, tired: the live pouch first (not the spent one)",
          kind(a.choose(w, ME, t, standing=False)) == ("pouch", POUCH))
    check("ready_at before any use: at once", a.ready_at() == 0.0)
    a.used(a.choose(w, ME, t, standing=False), t)
    check("within USE_GAP_S of a use: nothing", a.choose(w, ME, t + 0.5, standing=False) is None)
    check("ready_at: the use + USE_GAP_S", a.ready_at() == t + healing.USE_GAP_S)
    c = a.choose(w, ME, t + 0.6, standing=False)
    check("still flagged frozen 0.6 s after the pouch (its retry gap): the cure goes next", kind(c) == ("cure", CURE),
          str(c))
    a.used(c, t + 0.6)
    c = a.choose(w, ME, t + 1.2, standing=False)
    check("1.2 s: the pouch again (its 1 s retry gap is over, still frozen)", kind(c) == ("pouch", POUCH), str(c))
    w = flight(hits=55, stam=10, poisoned=True)
    a = healing.FleeAid()
    a.used(healing.Aid("cure", CURE, healing.CURE_POTION_GRAPHIC, "poisoned"), t)
    c = a.choose(w, ME, t + 0.6, standing=False)
    check("still poisoned within the cure's retry gap: no heal (it does nothing while poisoned), refresh instead",
          kind(c) == ("refresh", REFRESH), str(c))
    check("poisoned, cure retry gap over: cure again", kind(a.choose(w, ME, t + 1.6, standing=False)) == ("cure", CURE))
    a = healing.FleeAid()
    c = a.choose(flight(hits=55, stam=10, poisoned=False), ME, t, standing=False)
    check("cured, 45 missing, running: heal before refresh", kind(c) == ("heal", HEALP), str(c))
    a.used(c, t)
    c = a.choose(flight(hits=55, stam=10, poisoned=False), ME, t + 0.6, standing=False)
    check("heal potion cooling down (PotionClock): refresh", kind(c) == ("refresh", REFRESH), str(c))
    a.used(c, t + 0.6)
    check("refresh within its 3 s retry gap and the heal clock running: nothing",
          a.choose(flight(hits=55, stam=10), ME, t + 2.0, standing=False) is None)
    check("10 s after the heal: heal again", kind(a.choose(flight(hits=55), ME, t + 10.0, standing=False))
          == ("heal", HEALP))

    print("== flight aid thresholds: running vs standing ==")
    a = healing.FleeAid()
    check("running, 25 missing: heal", kind(a.choose(flight(hits=75), ME, t, standing=False)) == ("heal", HEALP))
    check("running, 24 missing: nothing", a.choose(flight(hits=76), ME, t, standing=False) is None)
    check("standing, 51 hits: nothing (a drink holds the book's click back)",
          a.choose(flight(hits=51), ME, t, standing=True) is None)
    check("standing, 50 hits: heal", kind(a.choose(flight(hits=50), ME, t, standing=True)) == ("heal", HEALP))
    check("standing, stamina 5/25: no refresh (running only)",
          a.choose(flight(stam=5), ME, t, standing=True) is None)
    check("running, stamina 12/25: refresh", kind(a.choose(flight(stam=12), ME, t, standing=False)) == ("refresh", REFRESH))
    check("running, stamina 13/25: nothing", a.choose(flight(stam=13), ME, t, standing=False) is None)
    check("standing, poisoned: cure", kind(a.choose(flight(poisoned=True), ME, t, standing=True)) == ("cure", CURE))

    print("== flight aid: only what lies in the backpack itself ==")
    check("frozen, no live pouch: the cure when poisoned",
          kind(a.choose(flight(flags=0x21, poisoned=True, pouch=False), ME, t, standing=False)) == ("cure", CURE))
    check("poisoned, the cure potions only in a bag: nothing (no bag opened mid-flight)",
          a.choose(flight(poisoned=True, cure=False, cure_in_bag=True), ME, t, standing=True) is None)
    check("hurt, no heal potion: nothing", a.choose(flight(hits=40, heal=False), ME, t, standing=True) is None)
    check("poisoned unknown (None): not poisoned, heal when hurt",
          kind(a.choose(flight(hits=40, poisoned=None), ME, t, standing=True)) == ("heal", HEALP))
    check("self flag 0x20 only: not frozen", a.choose(flight(flags=0x20), ME, t, standing=False) is None)
    c = a.choose(flight(), ME, t, standing=True, frozen_hint=True)
    check("frozen_hint (a cast refused 'while frozen') with flags 0x20: the pouch", kind(c) == ("pouch", POUCH)
          and "frozen" in c.why, str(c))
    check("no backpack: nothing", a.choose({"self": {"hits": 10, "hits_max": 100}, "items": {}}, ME, t,
                                           standing=True) is None)


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
    check("Greater Heal wanted but 10 mana: Heal instead", pick(c) == ("spell", healing.HEAL), str(c))
    c = healing.choose(world(90, mana=3), ME, potion_ready=False)
    check("3 mana and no potion ready: no heal possible", c.kind is None and c.missing == 8, str(c))
    c = healing.choose(world(40, mana=0), ME, potion_ready=True)
    check("no mana but a potion ready: the potion", c.kind == "potion", str(c))

    print("== reagents / spellstone ==")
    c = healing.choose(world(40, regs=()), ME, potion_ready=False)
    check("no reagents, no spellstone: no heal possible (not a cast that the server refuses)",
          c.kind is None and c.missing == 58 and "no garlic" in c.why, str(c))
    c = healing.choose(world(40, regs=HEAL_REGS), ME, potion_ready=False)
    check("Greater Heal wanted, no mandrake root: Heal (its reagents are there)",
          pick(c) == ("spell", healing.HEAL) and "mandrake" in c.why, str(c))
    c = healing.choose(world(40, regs=(), stone="arielle's bauble"), ME, potion_ready=False)
    check("a spellstone in the pack replaces reagents: Greater Heal", pick(c) == ("spell", healing.GREATER_HEAL), str(c))
    c = healing.choose(world(40, regs=(), stone="a spellstone"), ME, potion_ready=False)
    check("an item named '...spellstone...' counts too", c.kind == "spell", str(c))
    w = world(40, regs=())
    w["items"][h(STONE)] = {"graphic": 0x023B, "container": h(BAG)}       # never clicked: no name
    if combat._tile_name(0x023B):
        c = healing.choose(w, ME, potion_ready=False)
        check("an unclicked arielle's bauble (graphic 0x023B) counts by its tiledata name",
              pick(c) == ("spell", healing.GREATER_HEAL), str(c))
    else:
        print("  SKIP unclicked bauble: no Outlands tiledata on this machine")
    c = healing.choose(world(40, regs=()), ME, potion_ready=True)
    check("no reagents but a potion ready: the potion", c.kind == "potion", str(c))
    c = healing.choose(world(40), ME, potion_ready=False, blocked={healing.GREATER_HEAL})
    check("Greater Heal refused by the server for reagents this visit: Heal", pick(c) == ("spell", healing.HEAL), str(c))
    c = healing.choose(world(40), ME, potion_ready=False, blocked={healing.GREATER_HEAL, healing.HEAL})
    check("both refused: no heal possible", c.kind is None, str(c))

    print("== combat.can_cast (the hunt runner's attack-spell check) ==")
    light = combat.spell_id("lightning")
    check("Lightning with mandrake root + sulfurous ash and 11 mana", combat.can_cast(world(98), ME, light, 11))
    check("Lightning without sulfurous ash: no",
          not combat.can_cast(world(98, regs=HEAL_REGS + (0x0F86,)), ME, light, 50))
    check("Lightning with a spellstone and no reagents", combat.can_cast(world(98, regs=(), stone="arielle's bauble"),
                                                                         ME, light, 50))
    check("Lightning with 10 mana: no", not combat.can_cast(world(98), ME, light, 10))
    check("a reagent in the bank box doesn't count",
          combat.missing_reagents({"items": {**world(98, regs=())["items"],
                                             h(0x4B6BBA00): {"graphic": 0x0F8C, "container": h(BANK)},
                                             h(0x4B6BBA01): {"graphic": 0x0F86, "container": h(BANK)}}},
                                  ME, light) == ["mandrake root", "sulfurous ash"])

    print("== potion clock ==")
    clock = healing.PotionClock()
    check("never drunk: ready", clock.ready(100.0))
    clock.started(100.0)
    check("9.9 s after a drink: not ready", not clock.ready(109.9))
    check("10 s after: ready", clock.ready(110.0))

    flight_aid()

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILURES")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

"""Self-healing choice shared by `ctl act heal` and the hunt runner.

Order (user decision 2026-10-02): a heal potion whenever one can be drunk;
otherwise Magery, Heal or Greater Heal by how much health is missing.

- Potions: any heal potion (graphic 0x0F0C; Lesser Heal, Heal and Greater Heal
  share it, the clicked name tells them apart) in the backpack at any bag
  depth, smallest stack first like `ctl act use`. "Can be drunk" = not at full
  health and not within the potion cooldown: one healing potion about every 10 s
  (cliloc 1049415; wiki Alchemy: 10 s). The server's own refusal, cliloc 500235
  "You must wait a few seconds before using another healing potion.", restarts
  the clock (a potion drunk in the client counts too). No free hand is needed on
  Outlands (wiki Alchemy, PvM bonuses).
- Spells (wiki Magery, PvM): Heal (10..12) x Magery/100 for 4 mana, Greater Heal
  (40..50) x Magery/100 for 11 mana. Live 2026-10-01: Greater Heal +27 at Magery 60
  (in range). Greater Heal is chosen when the missing hits reach the mana
  break-even, where it restores more hits per mana than Heal:
  missing >= heal_avg x 11 / 4 (19 at Magery 60). Below that, Heal. If the chosen
  spell can't be paid for (mana, or reagents: combat.can_cast, a spellstone
  replaces them; or the caller's `blocked`, spells the server refused for
  reagents), the other one if it can; else no heal (Choice kind None).
- Live potion evidence (session 20261001_214649): two Lesser Heal potions drunk
  in a fight healed +34 and +24, each answered with "+N" overhead and cliloc
  1008158 "some damage has been healed : " with the amount as its argument.
"""
import math
from dataclasses import dataclass

import combat

HEAL_POTION_GRAPHIC = 0x0F0C
POTION_COOLDOWN_S = 10.0
CLILOC_POTION_WAIT = 500235          # "You must wait a few seconds before using another healing potion."
CLILOC_FULL_HEALTH = 1049547         # "You are already at full health."
CLILOC_HEALED = 1008158              # "some damage has been healed : " + amount
MAGERY_SKILL_ID = 25
HEAL = combat.spell_id("heal")
GREATER_HEAL = combat.spell_id("greater heal")
# (min, max) hits healed per 100 Magery (wiki Magery, PvM)
SPELL_HEAL_PER_100 = {HEAL: (10, 12), GREATER_HEAL: (40, 50)}


def magery(me: dict) -> float:
    """Magery from the world model's self skills (tenths), 0 when unknown."""
    sk = (me.get("skills") or {}).get(str(MAGERY_SKILL_ID)) or {}
    return (sk.get("value") or 0) / 10


def spell_heal_avg(sid: int, mag: float) -> float:
    lo, hi = SPELL_HEAL_PER_100[sid]
    return (lo + hi) / 2 * mag / 100


def gheal_break_even(mag: float) -> int:
    """Missing hits from which Greater Heal restores more hits per mana than Heal."""
    return math.ceil(spell_heal_avg(HEAL, mag) * combat.spell_mana(GREATER_HEAL) / combat.spell_mana(HEAL))


def heal_potions(world: dict, me: int) -> list[tuple[str, dict]]:
    """(key, item) of the heal potions in the backpack at any depth, smallest stack first."""
    items = world["items"]
    pack = combat.backpack(items, me)
    if pack is None:
        return []
    found = [(k, it) for k, it in combat.pack_items(items, pack) if it.get("graphic") == HEAL_POTION_GRAPHIC]
    return sorted(found, key=lambda kv: (kv[1].get("amount") or 1, kv[0]))


class PotionClock:
    """When the next healing potion can be drunk: POTION_COOLDOWN_S after the
    last drink, or after the server's 'wait' refusal (cliloc 500235)."""

    def __init__(self, last: float | None = None):
        self.last = last

    def ready(self, now: float) -> bool:
        return self.last is None or now - self.last >= POTION_COOLDOWN_S

    def started(self, now: float):
        self.last = now


@dataclass
class Choice:
    kind: str | None          # "potion", "spell" or None (nothing to do / nothing possible)
    why: str
    missing: int
    potion: str | None = None  # serial key of the potion stack
    spell: int | None = None   # Magery spell id


def choose(world: dict, me: int, potion_ready: bool, gheal_min_missing: int | None = None,
           blocked=()) -> Choice:
    """What to heal with now. `gheal_min_missing` overrides the mana break-even;
    `blocked`: spell ids not to cast (the server answered 'more reagents needed')."""
    s = world["self"]
    hits, top = s.get("hits"), s.get("hits_max")
    if hits is None or not top:
        return Choice(None, "hits unknown", 0)
    missing = top - hits
    if missing <= 0:
        return Choice(None, "at full health", 0)
    pots = heal_potions(world, me)
    if pots and potion_ready:
        k, it = pots[0]
        return Choice("potion", f"{missing} missing: heal potion", missing, potion=k)
    mag = magery(s)
    threshold = gheal_min_missing if gheal_min_missing is not None else gheal_break_even(mag)
    want, other = (GREATER_HEAL, HEAL) if missing >= threshold else (HEAL, GREATER_HEAL)
    mana = s.get("mana") or 0
    no_pot = "no heal potion" if not pots else "potion cooling down"
    why_not = {}
    for sid in (want, other):
        if sid in blocked:
            why_not[sid] = "the server wants more reagents"
        elif mana < combat.spell_mana(sid):
            why_not[sid] = f"mana {mana}"
        elif lack := combat.missing_reagents(world, me, sid):
            why_not[sid] = "no " + ", ".join(lack)
        else:
            name = combat.MAGERY_SPELLS[sid - 1]
            pick = "" if sid == want else f" ({combat.MAGERY_SPELLS[want - 1]}: {why_not[want]})"
            return Choice("spell", f"{missing} missing, Greater Heal from {threshold}; {no_pot}: {name}{pick}",
                          missing, spell=sid)
    return Choice(None, f"{missing} missing; {no_pot}; Heal: {why_not[HEAL]}; "
                        f"Greater Heal: {why_not[GREATER_HEAL]}", missing)

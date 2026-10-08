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

Flight aid (FleeAid, user request 2026-10-06 after the Razor 'PK Getaway' script): what to use from
the backpack while running from a threat, between recall casts, and (since 2026-10-07) between chops
while working. See FleeAid's docstring.
"""
import math
from dataclasses import dataclass

import combat
import pouch

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


# ------------------------------------------------------------ flight aid
CURE_POTION_GRAPHIC = 0x0F07          # "Orange Potion" (Outland Dan's pack, ctl status 2026-10-06 22:50)
REFRESH_POTION_GRAPHIC = 0x0F0B       # "Red Potion"
FLAG_FROZEN = 0x01                     # self 0x20 flags [INFERENCE: ClassicUO Flags.Frozen 0x01; RunUO
#                                        Mobile.GetPacketFlags sets it when Paralyzed or Frozen]; not seen on Dan yet
# Item use (C2S 0x06) has a server action delay [INFERENCE: RunUO PacketHandlers.UseReq refuses a use within
# Mobile.ActionDelay 0.5 s of the last, cliloc 500119 "You must wait to perform another action"]; live, a book
# double-click 0.3 s after another action was ignored (escape._open). One use at a time, this far apart:
USE_GAP_S = 0.55
# a use that was refused or didn't take (the pouch didn't unfreeze us, still poisoned) is retried after this
RETRY_S = {"pouch": 1.0, "cure": 1.5, "refresh": 3.0}   # heal: PotionClock (one every POTION_COOLDOWN_S)
RUN_HEAL_MISSING = 0.25                # running: drink a heal potion with this share of hits_max missing
STAND_HEAL_HITS = 0.50                 # standing (before / between recall casts): only at or below this share
WORK_HEAL_MISSING = 1                  # working (between chops): any hit missing, like the user's Razor heal
#                                        script (`if hp < maxhits` -> `potion "heal"`; user 2026-10-07)
RUN_REFRESH_STAM = 0.50                # running: a refresh potion at or below this share of stam_max


@dataclass
class Aid:
    kind: str           # "pouch", "cure", "heal" or "refresh"
    serial: int         # the item to double-click
    graphic: int
    why: str


def in_pack(world: dict, me: int, graphic: int, hue: int | None = None) -> list[tuple[int, dict]]:
    """(serial, item) of the items of `graphic` (and `hue`) in the backpack at any bag depth, smallest stack
    first, then by serial. Razor's `findtype … backpack` and `potion` do the same (Razor CE
    Item.FindItemsById(recurse: true), PlayerData.UseItem): they search the client's known pack tree and
    double-click the item by serial (one 0x06), no bag opened (user 2026-10-07)."""
    items = world.get("items") or {}
    pack = combat.backpack(items, me)
    if pack is None:
        return []
    found = [(combat._serial(k), it) for k, it in combat.pack_items(items, pack)
             if it.get("graphic") == graphic and (hue is None or it.get("hue") == hue)]
    return sorted(found, key=lambda kv: (kv[1].get("amount") or 1, kv[0]))


class FleeAid:
    """What to use while running from a threat, between recall casts (user request 2026-10-06, after
    the Razor 'PK Getaway' script) or between chops (`working`, user 2026-10-07, after their Razor heal
    script), one item use at a time, in this order:
      1. paralyzed (self flags & FLAG_FROZEN, or `frozen_hint`: the server refused a cast "while frozen")
         and a live trapped pouch (hue 38) in the pack: the pouch. Its explosion's damage breaks
         paralysis; we can't walk or cast while frozen.
      2. poisoned (world.self.poisoned, the server's 0x16/0x17 about us) and a cure potion: cure.
         [INFERENCE: pre-AOS poison ticks disturb casting] and a heal potion does nothing while poisoned
         [INFERENCE: RunUO BaseHealPotion.Drink, cliloc 1005000].
      3. not poisoned, the heal potion clock ready (PotionClock) and hurt: a heal potion. Running
         (`standing` and `working` False) from RUN_HEAL_MISSING missing; standing (before or between
         recall casts, where a drink holds the book's double-click back USE_GAP_S) only at or below
         STAND_HEAL_HITS; working from WORK_HEAL_MISSING missing.
      4. running only: stamina at or below RUN_REFRESH_STAM and a refresh potion: refresh.
    Items anywhere in the backpack count, bags included (in_pack, as Razor's findtype). No two uses
    within USE_GAP_S (the server's action delay); a kind used RETRY_S ago is skipped (the next kind may
    go). No spells: casting stops a run on Outlands and the cast slot belongs to the recall."""

    def __init__(self):
        self.clock = PotionClock()
        self.last: float | None = None              # monotonic time of the last use
        self.last_kind: dict[str, float] = {}

    def ready_at(self) -> float:
        """Monotonic time from which the next item use (e.g. the runebook's double-click) is taken."""
        return 0.0 if self.last is None else self.last + USE_GAP_S

    def used(self, aid: Aid, now: float):
        self.last = now
        self.last_kind[aid.kind] = now
        if aid.kind == "heal":
            self.clock.started(now)

    def _due(self, kind: str, now: float) -> bool:
        t = self.last_kind.get(kind)
        return t is None or now - t >= RETRY_S[kind]

    def choose(self, world: dict, me: int, now: float, standing: bool, frozen_hint: bool = False,
               working: bool = False) -> Aid | None:
        if now < self.ready_at():
            return None
        s = world.get("self") or {}
        frozen = bool((s.get("stats") or {}).get("flags", 0) & FLAG_FROZEN) or frozen_hint
        if frozen and self._due("pouch", now):
            if live := in_pack(world, me, pouch.POUCH_GRAPHIC, pouch.TRAPPED_HUE):
                why = "paralyzed" if not frozen_hint else "the server says we're frozen"
                return Aid("pouch", live[0][0], pouch.POUCH_GRAPHIC, why)
        poisoned = bool(s.get("poisoned"))
        if poisoned and self._due("cure", now):
            if pots := in_pack(world, me, CURE_POTION_GRAPHIC):
                return Aid("cure", pots[0][0], CURE_POTION_GRAPHIC, "poisoned")
        hits, hmax = s.get("hits"), s.get("hits_max")
        if not poisoned and hits is not None and hmax and self.clock.ready(now):
            if working:
                hurt = hmax - hits >= WORK_HEAL_MISSING
            elif standing:
                hurt = hits <= hmax * STAND_HEAL_HITS
            else:
                hurt = hmax - hits >= hmax * RUN_HEAL_MISSING
            if hurt and (pots := in_pack(world, me, HEAL_POTION_GRAPHIC)):
                return Aid("heal", pots[0][0], HEAL_POTION_GRAPHIC, f"{hmax - hits} hits missing")
        stam, smax = s.get("stam"), s.get("stam_max")
        if not standing and not working and stam is not None and smax and stam <= smax * RUN_REFRESH_STAM \
                and self._due("refresh", now):
            if pots := in_pack(world, me, REFRESH_POTION_GRAPHIC):
                return Aid("refresh", pots[0][0], REFRESH_POTION_GRAPHIC, f"stamina {stam}/{smax}")
        return None

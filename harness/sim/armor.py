"""The player's armor in the simulator: the Outlands rule (wiki rows in the memory store; docs/SIM.md "Player armor").

- A physical hit loses a uniform random share of AR x 0.333 % to AR x 0.666 % of the suit's *total* AR (Armor & Weapons
  > Armor Values, knowledge #11677). Physical: melee from monsters and players, breath, some boss effects. Secondary
  damage (bleed, disease) takes no reduction (Damage Resolution, #11670).
- A suit's AR: its material's full-suit base (#11692) x (1 + quality + ingot/leather material + aspect), the bonuses
  added (Crafted Items #13810: "Exceptional quality bonuses stack additively with material bonuses"; ingots #13811,
  leathers #13813/#13814; the armor aspect's "Armor Rating Bonus: 20% + 5% per Tier", Command #13891), weighted by the
  locations worn (Armor Location Penalty, #11684), plus flat extras (a buff, a non-suit helm).

Live check (0x11 physical_resist, Logan Wolf): exceptional verite plate with Command T14 = 50 x 2.65 = 132.5 -> 133,
T13 -> 130, the aspect lost -> 87.5 -> 88; 148 with the +15 Armor Rating Increase buff.
"""

import random

SUIT_BASE = {"leather": 25, "studded": 30, "bone": 35, "ringmail": 40, "chainmail": 45, "platemail": 50}
LOCATION = {"chest": 0.35, "legs": 0.22, "arms": 0.15, "helmet": 0.14, "gloves": 0.07, "gorget": 0.07}
QUALITY = {"regular": 0.0, "exceptional": 0.40, "defense": 0.20, "guarding": 0.40, "hardening": 0.60,
           "fortification": 0.80, "invulnerability": 1.00}
# ingots and their leather equivalents (Armor & Weapons > Colored Materials, #11663)
MATERIAL = {"iron": 0.0, "leather": 0.0, "dull copper": 0.05, "dullhide": 0.05, "shadow iron": 0.10, "shadowhide": 0.10,
            "copper": 0.15, "copperhide": 0.15, "bronze": 0.20, "bronzehide": 0.20, "gold": 0.25, "goldenhide": 0.25,
            "agapite": 0.30, "rosehide": 0.30, "verite": 0.35, "verehide": 0.35, "valorite": 0.40, "valehide": 0.40,
            "avarite": 0.45, "avarhide": 0.45}


def aspect_bonus(tier) -> float:
    """An armor aspect's AR share at `tier` (None: no aspect active)."""
    return 0.0 if tier is None else 0.20 + 0.05 * tier


def follower_resist(aspect, tier) -> float:
    """The share of all damage our followers don't take from the Command armor aspect at `tier`: "Follower Damage
    Resistance: 5% + 1.5% per player Command Tier Level" (#13891); 0 under another aspect or none."""
    return 0.05 + 0.015 * tier if aspect == "command" and tier is not None else 0.0


def suit_ar(suit: dict) -> int:
    """AR of `suit`: {base: SUIT_BASE key, quality, material, aspect_tier (None: off), pieces (locations worn; default
    all six), extra (flat AR on top)}. Rounded half up like the server's 132.5 -> 133 and 87.5 -> 88."""
    base = SUIT_BASE[suit["base"]]
    pieces = suit.get("pieces") or list(LOCATION)
    share = sum(LOCATION[p] for p in pieces)
    x = 1 + QUALITY[suit.get("quality", "regular")] + MATERIAL[suit.get("material", "iron")] \
        + aspect_bonus(suit.get("aspect_tier"))
    return int(base * share * x + suit.get("extra", 0) + 0.5)


def reduction_range(ar: float) -> tuple[float, float]:
    """The share of a physical hit armor removes: uniform between these (capped at all of it)."""
    ar = max(0.0, ar)
    return min(1.0, ar / 300.0), min(1.0, ar / 150.0)


def taken(ar: float, rng: random.Random) -> float:
    """One hit's kept share at `ar`."""
    lo, hi = reduction_range(ar)
    return 1.0 - rng.uniform(lo, hi)


def mean_taken(ar: float) -> float:
    lo, hi = reduction_range(ar)
    return 1.0 - (lo + hi) / 2


def predict(melee, ar: float, n: int = 20000, seed: int = 1) -> dict:
    """p10 / median / p90 of a hit of uniform `melee` [lo, hi] after the armor at `ar` (the numbers floored at 1)."""
    rng = random.Random(seed)
    v = sorted(max(1, round(rng.uniform(melee[0], melee[1]) * taken(ar, rng))) for _ in range(n))
    return {"p10": v[n // 10], "median": v[n // 2], "p90": v[9 * n // 10]}

"""Load a simulator scenario (harness/data/sim/<name>.json) and build the World's creature specs
from the wiki stats (<name>_kb.json) and the capture's observations (<name>_observed.json)
(docs/SIM.md "Scenario").

Per creature type, in this order of evidence:
- hits, difficulty, gold value, AI, speed category, spell damage: the wiki (knowledge ids in _kb);
- melee damage per hit: the capture's attributed hits (`swing.damage_per_swing`, else
  `damage_to_pets`) when n >= MIN_N, as (p10, p90, median); else the wiki melee x `kb_melee_to_pet`;
  that is what a pet takes. On the player: `melee_player`, the wiki melee x `player_melee_k`, before his
  armor (World._melee_on; docs/SIM.md "Player armor");
- swing period: the capture's median when n >= MIN_N and >= MIN_SWING_S (shorter periods there are
  poison/bleed ticks, the calibration notes), else `defaults.swing_s`;
- chase step: the wiki speed category -> `defaults.speed_step_s`;
- spells (AI mage/meleemage, or ranged): the wiki spell damage x `ranged_kb_factor` every
  `ranged_every_s` within `ranged_range` [INFERENCE: none attributable on the trip]; each cast stands
  `cast_s` (its 0x6E animation `cast_action` first), flies `flight_s` (the 0xC0 `effect`, or none) and is
  lost when the target is out of range or sight at its release: `ranged_observed.<type>` from
  sim/cast_scan.py, else `defaults.ranged_cast_s` / `ranged_flight_s` / `ranged_cast_action` / `ranged_effect`;
- gold: the wiki gold value x 0.9-1.15 (the trip's lifted medians sat within that);
- specials: the scenario's `specials` table;
- `melee_observed` / `ranged_observed`: the scenario's own measured overrides (each with its note),
  over all of the above; `aggro_observed.<type>.range`: the type's measured aggro range (else
  `defaults.aggro_range`);
- armor and magic resist: the wiki's (what the pets' hits meet, sim/pets.py); `ref` in a `melee_observed` entry
  (`melee_ref`), a `ranged_observed` one or a special: the pet its numbers were measured on (else
  `defaults.pet_ref`).

`player_armor(sc)` resolves the player's AR, aspect and tier from `player.armor`; `load` puts the character's Mastery
Chain in (sim/chain.apply) and builds Logan's pets last (sim/pets.select: the scenario's `tamer` block). The barding
numbers come from the effective barding skill (`bard_eff`: skill + instrument quality + the chain's Effective Barding;
docs/SIM.md "Barding from the effective skill"): `bard_chance`, `bard_secs`, `song_share`.
"""
from __future__ import annotations

import json
import math
import os

from sim import armor, chain, pets

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), "data", "sim")
MIN_N = 3
MIN_SWING_S = 1.5


def _stat(d: dict | None):
    if not d or (d.get("n") or 0) < MIN_N or d.get("p10") is None:
        return None
    return [d["p10"], d["p90"], d["median"]]


def creature_spec(key: str, kb: dict, obs: dict | None, sc: dict) -> dict:
    df = sc["defaults"]
    obs = obs or {}
    sw = obs.get("swing") or {}
    melee = _stat(sw.get("damage_per_swing")) or _stat(obs.get("damage_to_pets"))
    src = {"melee": "capture" if melee else "wiki x kb_melee_to_pet"}
    if melee is None and kb.get("melee_damage"):
        lo, hi = kb["melee_damage"]
        f = df["kb_melee_to_pet"]
        melee = [lo * f, hi * f, (lo + hi) / 2 * f]
    period = (sw.get("period_s") or {})
    swing = period["median"] if (period.get("n") or 0) >= MIN_N and period["median"] >= MIN_SWING_S else df["swing_s"]
    src["swing_s"] = "capture" if swing != df["swing_s"] else "default"
    mo = sc.get("melee_observed", {}).get(key)
    if mo is not None:
        melee, swing = mo.get("melee", melee), mo.get("swing_s", swing)
        src["melee"] = "melee_observed"
    ai = kb.get("ai") or "melee"
    ranged = None
    if ai in ("mage", "meleemage") and kb.get("spell_damage"):
        lo, hi = kb["spell_damage"]
        f = df["ranged_kb_factor"]
        ranged = {"range": df["ranged_range"], "damage": [lo * f, hi * f], "every_s": df["ranged_every_s"], "keep": 1}
    elif ai == "ranged" and melee:
        ranged = {"range": df["ranged_range"] - 2, "damage": melee[:2], "every_s": swing, "keep": 4}
    if (seen := sc.get("ranged_observed", {}).get(key)) is not None:
        ranged = {**(ranged or {"range": df["ranged_range"], "keep": 1}),
                  **{k: v for k, v in seen.items() if k not in ("note", "cast_note")}}
    if ranged is not None:
        ranged.setdefault("cast_s", df["ranged_cast_s"])
        ranged.setdefault("flight_s", df["ranged_flight_s"])
        ranged.setdefault("cast_action", df["ranged_cast_action"])
        ranged.setdefault("effect", df["ranged_effect"])
    gold = kb.get("gold_value") or 0
    gold_seen = (sc.get("gold_observed", {}).get(key) or {}).get("gold")   # measured live over the kb's (wiki's) value
    noto = 3
    graphics = (obs.get("graphics") or [kb.get("graphic")])
    return {"key": key, "name": kb.get("name") or f"a {key}", "body": graphics[0] if graphics and graphics[0] else 400,
            "notoriety": noto, "hits": kb.get("hits") or 1000, "difficulty": kb.get("difficulty"),
            "melee": melee if ai != "mage" else None, "swing_s": swing, "hit_chance": df["hit_chance"],
            "armor": kb.get("armor") or 0, "magic_resist": kb.get("magic_resist") or 0,
            "melee_ref": (mo or {}).get("ref"),
            "melee_player": [kb["melee_damage"][0] * df["player_melee_k"], kb["melee_damage"][1] * df["player_melee_k"]]
            if ai != "mage" and kb.get("melee_damage") else None,
            "step_s": df["speed_step_s"].get(kb.get("speed") or "Medium", df["speed_step_s"]["Medium"]),
            "aggro_range": (sc.get("aggro_observed", {}).get(key) or {}).get("range", df["aggro_range"]),
            "ranged": ranged, "gold": gold_seen if gold_seen is not None else [round(gold * 0.9), round(gold * 1.15)],
            "specials": sc.get("specials", {}).get(key, []), "source": src}


def _read(name: str, data_dir: str) -> dict:
    with open(os.path.join(data_dir, f"{name}.json"), encoding="utf-8") as f:
        sc = json.load(f)
    inh = sc.get("inherit")
    if inh:
        # the character's blocks another scenario holds (Logan's pets, pack, bard, vet, potions, engine defaults):
        # a key the scenario lacks is the base's; a dict both have is the base's updated by the scenario's
        base = _read(inh["from"], data_dir)
        for k in inh["keys"]:
            if k not in base:
                raise ValueError(f"scenario {name}: inherit key {k!r} not in {inh['from']}")
            if k not in sc:
                sc[k] = base[k]
            elif isinstance(sc[k], dict) and isinstance(base[k], dict):
                sc[k] = {**base[k], **sc[k]}
    return sc


def load(name: str = "mausoleum", data_dir: str = DATA) -> dict:
    sc = _read(name, data_dir)
    with open(os.path.join(data_dir, sc["evidence"]["kb"]), encoding="utf-8") as f:
        kb = json.load(f)
    obs_path = os.path.join(data_dir, sc["evidence"]["observed"])
    obs = {}
    if os.path.exists(obs_path):
        with open(obs_path, encoding="utf-8") as f:
            obs = json.load(f)
    # the spawns' types and the ones a special brings in (a nest crab's broodling)
    keys = sorted({s["key"] for s in sc["spawns"]} | {sp["key"] for sps in (sc.get("specials") or {}).values()
                                                       if isinstance(sps, list) for sp in sps if sp.get("key")})
    missing = [k for k in keys if k not in kb["creatures"]]
    if missing:
        raise ValueError(f"scenario {name}: no wiki stats for {missing}")
    sc["creatures"] = {k: creature_spec(k, kb["creatures"][k], (obs.get("creatures") or {}).get(k), sc) for k in keys}
    for sp in sc["spawns"]:
        sp.setdefault("respawn_s", sc["defaults"]["respawn_s"])
    play(sc, sc["bard"]["instrument"])
    sc["bard"]["d"] = bard_difficulty(sc["bard"], data_dir)
    if "tamer" in sc:
        chain.apply(sc)                  # the character's own chain; run.py re-applies for --links
        pets.select(sc)                  # Logan's pets (sim/pets.py); run.py re-selects for --team / --bestiary
    return sc


def play(sc: dict, name: str) -> None:
    """Logan plays an instrument of quality `name` (a key of `bard.instrument_bonus`): its bonus is part of his effective
    barding skill (bard_eff). load does it for the scenario's `bard.instrument`, run.py --instrument for another.
    ValueError for a quality not there."""
    have = sc["bard"]["instrument_bonus"]
    if name not in have:
        raise ValueError(f"no instrument quality {name!r} (one of: {', '.join(have)})")
    sc["bard"]["instrument"] = name


def bard_difficulty(b: dict, data_dir: str = DATA) -> dict:
    """{skill: {type: d}, "median": {skill: d}} for World._bard: the live fit (`b["difficulty"]`, sim/bard_scan.py, the
    runner's hunt logs), then the scenario's own fit of the user's hand play (`b["capture_d"]` {skill: {type: d}}), then
    the Guide's chances for the types neither has (`b["guide"]` {type: [chance, E]}: d = E - 100 x chance)."""
    with open(os.path.join(data_dir, b["difficulty"]), encoding="utf-8") as f:
        fit = json.load(f)["kinds"]
    out = {"median": {k: v["median_d"] for k, v in fit.items()}}
    for kind, v in fit.items():
        d = {ty: e["d"] for ty, e in v["types"].items()}
        for ty, cd in ((b.get("capture_d") or {}).get(kind) or {}).items():
            d.setdefault(ty, cd)
        for ty, (p, e) in (b.get("guide") or {}).items():
            d.setdefault(ty, e - 100.0 * p)
        out[kind] = d
    return out


def player_armor(sc: dict) -> dict:
    """The player's armor from `player.armor` (docs/SIM.md "Player armor"): {ar, aspect, tier}. `ar`: the block's
    own `ar` when set (run.py --armor), else the suit's (armor.suit_ar); aspect and tier: the suit's (the Command
    tier sets the pets' follower damage and resistance, sim/pets.build_pet; run.py --aspect-tier)."""
    a = sc["player"]["armor"]
    suit = a["suit"]
    aspect, tier = suit.get("aspect"), suit.get("aspect_tier")
    ar = a["ar"] if a.get("ar") is not None else armor.suit_ar(suit)
    return {"ar": ar, "aspect": aspect, "tier": tier}


def bard_eff(sc: dict) -> float:
    """Logan's effective barding skill: his skill (tamer barding) + the instrument's quality + the Mastery Chain's
    Effective Barding links (docs/SIM.md "Barding from the effective skill")."""
    b = sc["bard"]
    return b["skill"] + b["instrument_bonus"][b["instrument"]] + sc["chain"]["totals"]["effective_barding"]


def bard_chance(sc: dict, kind: str, key: str, e: float, partner: str | None = None) -> float:
    """A bard skill's chance at a creature of type `key` with effective skill `e` (bard_eff + any barded bonus):
    max((e - d) / 100, min_chance_factor x e / 100), at most 1 (knowledge #8530/#5239); d from bard_difficulty, else
    the skill's median. A Provocation of `key` onto `partner` with `bard.provoke_d`: d is the mean of the two creatures'
    Discordance d plus its `offset` (bard.provoke_d note: the live answers fit that, not a per-type provoke d)."""
    b = sc["bard"]
    pd = b.get("provoke_d")
    if kind == "provoke" and pd:
        ds = [b["d"]["discord"].get(k, b["d"]["median"]["discord"]) for k in (key, partner or key)]
        d = sum(ds) / len(ds) + pd["offset"]
    else:
        d = b["d"][kind].get(key, b["d"]["median"][kind])
    return min(1.0, max((e - d) / 100.0, b["min_chance_factor"] * e / 100.0))


def bard_secs(sc: dict, kind: str, e: float) -> int:
    """A success's seconds at effective skill `e`: ceil(seconds_per_100 x e / 100 x (1 + the chain's Barding Effect
    Durations)); live 2,957 of 2,979 answers (bard.seconds_note)."""
    base = sc["bard"]["seconds_per_100"][kind]
    return math.ceil(base * e / 100.0 * (1 + sc["chain"]["totals"]["barding_durations"]) - 1e-9)


def song_share(sc: dict) -> float:
    """A barding song's bonus share: song_per_100 x bard_eff / 100 (live 15.0-18.4 %, bard.song_note)."""
    return sc["bard"]["song_per_100"] * bard_eff(sc) / 100.0

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
  `defaults.aggro_range`).

`fit_bard(observed, kb)` re-derives the effective barding skill from the capture's outcomes;
`player_armor(sc)` resolves the player's AR and the pets' damage share from `player.armor`.
"""
from __future__ import annotations

import json
import math
import os

from sim import armor

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
    if (mo := sc.get("melee_observed", {}).get(key)) is not None:
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
    noto = 3
    graphics = (obs.get("graphics") or [kb.get("graphic")])
    return {"key": key, "name": kb.get("name") or f"a {key}", "body": graphics[0] if graphics and graphics[0] else 400,
            "notoriety": noto, "hits": kb.get("hits") or 1000, "difficulty": kb.get("difficulty"),
            "melee": melee if ai != "mage" else None, "swing_s": swing, "hit_chance": df["hit_chance"],
            "melee_player": [kb["melee_damage"][0] * df["player_melee_k"], kb["melee_damage"][1] * df["player_melee_k"]]
            if ai != "mage" and kb.get("melee_damage") else None,
            "step_s": df["speed_step_s"].get(kb.get("speed") or "Medium", df["speed_step_s"]["Medium"]),
            "aggro_range": (sc.get("aggro_observed", {}).get(key) or {}).get("range", df["aggro_range"]),
            "ranged": ranged, "gold": [round(gold * 0.9), round(gold * 1.15)],
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
    sc["bard"]["chance"] = None          # per target: bard_chance()
    return sc


def player_armor(sc: dict) -> dict:
    """The player's armor from `player.armor` (docs/SIM.md "Player armor"): {ar, aspect, tier, pet_taken_x}.
    `ar`: the block's own `ar` when set (run.py --armor), else the suit's (armor.suit_ar). `pet_taken_x`: the share
    of damage the pets take against the numbers they were measured with, at the Command tier `pets_tier`
    (armor.follower_resist); 1.0 unless the suit's aspect or tier differs from it (run.py --aspect-tier)."""
    a = sc["player"]["armor"]
    suit = a["suit"]
    aspect, tier = suit.get("aspect"), suit.get("aspect_tier")
    ar = a["ar"] if a.get("ar") is not None else armor.suit_ar(suit)
    x = (1 - armor.follower_resist(aspect, tier)) / (1 - armor.follower_resist("command", a["pets_tier"]))
    return {"ar": ar, "aspect": aspect, "tier": tier, "pet_taken_x": x}


def bard_chance(sc: dict, difficulty: float | None) -> float:
    """Success chance of a bard skill against `difficulty` (knowledge #8530/#5239)."""
    b = sc["bard"]
    s = b["effective_skill"]
    d = difficulty if difficulty is not None else 100.0
    return max(s / 100.0 - d / 100.0, b["min_chance_factor"] * s / 100.0)


def fit_bard(observed: dict, kb: dict, skills=("Discordance", "Peacemaking", "Provocation")) -> dict:
    """The effective barding skill that best explains the capture's ok/fail answers per target type
    (maximum likelihood over 100-250, step 1)."""
    rows = []
    for skill in skills:
        for typ, outc in (observed.get("bard", {}).get(skill, {}).get("by_type") or {}).items():
            d = (kb["creatures"].get(typ) or {}).get("difficulty")
            if d is None:
                continue
            rows.append((d, outc.get("ok", 0), outc.get("fail", 0)))
    best = None
    for s in range(100, 251):
        ll = 0.0
        for d, ok, fail in rows:
            p = min(0.99, max(0.01, max(s / 100 - d / 100, 0.33 * s / 100)))
            ll += ok * math.log(p) + fail * math.log(1 - p)
        if best is None or ll > best[1]:
            best = (s, ll)
    n = sum(ok + fail for _, ok, fail in rows)
    return {"effective_skill": best[0], "log_likelihood": round(best[1], 2), "n": n}

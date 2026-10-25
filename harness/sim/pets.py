"""Logan Wolf's pets in the simulator: their stats, attacks, abilities and the perks of his Taming Bestiary and
Healer's Codex, as they work live (docs/SIM.md "Pets, teams and perks").

Data: harness/data/sim/tamer/<character>.json (the tamer's skills, species, pets, teams, perk numbers, the live
anchors) and <character>_observed.json (sim/tamer_scan.py: the codex tiers, the bestiary loadouts, each pet's Guide
values). `load_tamer` merges the two.

A pet's spec (`build_pet`), from its Guide's *base* values at its level (the wiki's level table, patch #4658):
- hits x (1 + 0.10 a level to 10, + 0.05 a level past it); damage x (1 + 0.05 / 0.025); attack speed + 2.5 % /
  1.25 %; armor + 2.5 / 1; magic resist + 5 / 2;
- damage x a static pool: 1 + the Command aspect's follower damage (5 % + 1.5 % a tier) + the Mastery Chain's
  `follower_damage` + Veterinary's 10 % x skill/100 + the underdog bonus 40 % x (min(Taming, Lore) - min taming) / 70
  (the searing imp's Details page) + the species' Zoology + attack speed over the 50 % cap (#4661);
- damage resistance: the Command aspect's 5 % + 1.5 % a tier (armor.follower_resist) + the underdog's 20 % x the
  same share + Zoology; with the perks' and the codex's at most 75 % (#4653);
- attack period: the Guide's attack speed / (1 + level + Command speed bonus, at most 0.5) x `k_speed`, the pet's
  live period over that formula at Command tier 14 (anchors.speed; a pet without an anchor: the others' median);
- damage x `k_dmg`, one factor for every pet: the gravebug's live median melee number over the model's
  (anchors.damage); a pet with `anchors.damage_override` (AlaFlambe, its reason there) has its own (`k_override`).

Its numbers on a monster (`melee_numbers`, `spell_numbers`, `ability_numbers`): uniform in [lo, hi] x (1 + the
perks' damage bonus) x Lethality x the monster's mitigation: physical x armor.taken(AR - Breaker) (#11677, #11669),
spells x (1 - U(12.5 %, 37.5 %) x MR / 100) (patch #5221, Breaker #4223); half_resist halves the AR/MR; an
ignore-armor effect against nothing left to ignore gives +15 % (#11687). Bonuses add within a pool, the pools
multiply [INFERENCE]. Its damage taken (`taken_factor`): the monsters' numbers stand for a reference pet
(`sc["pet_ref"]`: the scenario's defaults.pet_ref, or a creature entry's own `ref`); a pet takes them x its own
mitigation over the reference's (armor for physical, magic resist for spells) x (1 - its DR) / (1 - the reference's).
"""
from __future__ import annotations

import copy
import functools
import json
import os
import random
import statistics
from dataclasses import dataclass, replace

from sim import armor

HERE = os.path.dirname(os.path.abspath(__file__))
TAMER_DIR = os.path.join(os.path.dirname(HERE), "data", "sim", "tamer")
TABS = ("attack", "tank", "utility")
CROSS = "[Cross] "
TIER_POINTS = (0, 1, 3, 5)          # points a perk at rank/tier 0-3 costs (the gumps)
MAX_TAB_POINTS = 20
MAX_CROSS_POINTS = 5
VET_BASE_RANGE = 2                  # Veterinary supplies heal the followers within 2 tiles (wiki #11331)
VET_SINGLE, VET_SHARED = 0.75, 0.50  # of a bandage's heal: one patient, more (wiki #11331)
VET_ROLL = (80, 120)                # a bandage heals Vet/100 x U(80, 120) x (1 + VET_LORE x Lore/100) (wiki #11327)
VET_LORE = 0.2
DR_CAP = 0.75                       # damage resistance at most (#4653)
SPEED_CAP = 0.5                     # attack speed bonus at most; the rest becomes damage (#4661)
BREAKER_AR_CAP = 75                 # armor ignored at most (#11669)
BREAKER_MR_CAP = 150                # magic resist ignored at most (#4223)
IGNORE_ZERO_BONUS = 0.15            # an ignore effect with nothing left to ignore (#11687)
SPELL_RESIST = (0.125, 0.375)       # a spell loses U(lo, hi) x MR/100 (patch #5221)
HURT_S = 10.0                       # Cornered, Aggression: hurt by a creature within this
AFTER_RES_S = 15.0                  # Adrenaline
HEARTY_ABOVE = 0.75
PELT_FAR = 4
NEAR_TARGET_R = 2                   # Hunting Party; Feed's and Carrion's melee heals; Trample's reach
TRAMPLE_TARGETS = 2
GIANT_KILLER_BELOW = 0.66
DOMINANCE_HITS = 5
DOMINANCE_RESET_S = 15.0
SWOOP_S = 10.0
TRIAGE_LOW = 0.33
ANCHOR_N = 20000


@dataclass
class HitCtx:
    """What a pet's perks look at when it hits or is hit (World._pet_ctx builds it)."""
    hurt_10s: bool = False          # a creature hurt it within HURT_S
    enemies_10s: int = 0            # distinct creatures that did (at most 3)
    hp_frac: float = 1.0
    dist: int = 1                   # to its target
    tamer_within_2: bool = False    # it and Logan both within NEAR_TARGET_R of its target
    target_bleeding: bool = False
    target_poisoned: bool = False
    target_diseased: bool = False
    afflicted: bool = False         # it bleeds, is poisoned or diseased
    poisoned: bool = False
    after_res: bool = False         # resurrected within AFTER_RES_S
    tanks_alive: int = 1            # live tank-class pets of the team
    aggroed_on_it: bool = False     # its target is on it
    channel: bool = False           # a vet channel runs with it in the supplies' reach (Triage)
    medic: bool = False             # Combat Medic's window after a finish
    immunized: bool = False         # Immunize's window after a finish
    shield: bool = False            # its Earth Shield is up
    swoop: bool = False             # within SWOOP_S of its cooldown ability, not yet spent
    surge: bool = False             # a Spellsurge is waiting for its next cast
    dom_hits: int = 0               # its hits on this target in the Dominance window
    now: float = 0.0


REF_TANK = HitCtx(hurt_10s=True, enemies_10s=1, hp_frac=0.8, dist=1, aggroed_on_it=True)
REF_CASTER = replace(REF_TANK, hurt_10s=False, enemies_10s=0, hp_frac=1.0, dist=6, aggroed_on_it=False)


# ------------------------------------------------------------ data
@functools.lru_cache(maxsize=None)
def load_tamer(character: str = "logan_wolf") -> dict:
    """The tamer's data and its scan merged ({..., codex, bestiary, guide, character}); cached: never mutate it."""
    with open(os.path.join(TAMER_DIR, f"{character}.json"), encoding="utf-8") as f:
        t = json.load(f)
    with open(os.path.join(TAMER_DIR, f"{character}_observed.json"), encoding="utf-8") as f:
        obs = json.load(f)
    t.update(codex=obs["codex"], bestiary=obs["bestiary"], guide=obs["guide"], character=character)
    return t


def _usable(tabs: dict) -> bool:
    return any(v and v.get("decoded") and v.get("points") for v in tabs.values())


def options(character: str = "logan_wolf") -> dict:
    """The team presets and the loadouts with perks captured (and "none"), for run.py and the viz Sim page."""
    t = load_tamer(character)
    return {"teams": [{"name": n, "pets": list(v["pets"]), "bestiary": v["bestiary"]} for n, v in t["teams"].items()],
            "bestiary": [n for n, tabs in t["bestiary"]["loadouts"].items() if _usable(tabs)] + ["none"]}


def _pet(name: str, t: dict) -> dict:
    return next(p for p in t["pets"] if p["name"] == name)


def _species(name: str, t: dict) -> dict:
    return t["species"][_pet(name, t)["species"]]


def parse_team(arg: str, t: dict) -> tuple[list[str], str | None]:
    """(pet names, the preset's name or None) for a preset name or pet names comma-separated (case-insensitive)."""
    presets = {k.lower(): k for k in t["teams"]}
    if arg.strip().lower() in presets:
        preset = presets[arg.strip().lower()]
        names = list(t["teams"][preset]["pets"])
    else:
        preset, names = None, []
        for part in arg.split(","):
            pet = next((p for p in t["pets"] if p["name"].lower() == part.strip().lower()), None)
            if pet is None:
                raise ValueError(f"unknown pet {part.strip()!r} (pets: {', '.join(p['name'] for p in t['pets'])})")
            if pet["name"] in names:
                raise ValueError(f"pet {pet['name']!r} twice")
            names.append(pet["name"])
    slots = sum(_species(n, t)["slots"] for n in names)
    if slots > t["tamer"]["max_slots"]:
        raise ValueError(f"team takes {slots} control slots, max {t['tamer']['max_slots']}")
    return names, preset


def check_tab(tab: str, perks: dict, t: dict) -> None:
    """ValueError unless `perks` ({"Perk": rank, "[Cross] Perk": rank}) can stand in the bestiary's `tab`."""
    table = t["perks"]["bestiary"]
    pts = cross = 0
    for key, r in perks.items():
        is_cross = key.startswith(CROSS)
        base = key[len(CROSS):] if is_cross else key
        if base not in table:
            raise ValueError(f"bestiary {tab}: unknown perk {key!r}")
        if isinstance(r, bool) or not isinstance(r, int) or not 1 <= r <= 3:
            raise ValueError(f"bestiary {tab}: rank must be 1-3 ({key!r}: {r})")
        home = table[base]["tab"]
        if is_cross and home == tab:
            raise ValueError(f"bestiary {tab}: [Cross] {base!r} is a {tab} perk")
        if not is_cross and home != tab:
            raise ValueError(f"bestiary {tab}: {base!r} is a {home} perk (another tab's perk is '[Cross] {base}')")
        pts += TIER_POINTS[r]
        cross += TIER_POINTS[r] if is_cross else 0
    if pts > MAX_TAB_POINTS:
        raise ValueError(f"bestiary {tab}: {pts} points, max {MAX_TAB_POINTS}")
    if cross > MAX_CROSS_POINTS:
        raise ValueError(f"bestiary {tab}: {cross} cross points, max {MAX_CROSS_POINTS}")


def parse_bestiary(arg: str, t: dict) -> tuple[str, dict]:
    """(label, {tab: perks or None}) for a scanned loadout's name (case-insensitive), "none", or a .json file of
    {"attack": {..}, "tank": {..}, "utility": {..}} in the scan's perk keys. None: a tab never captured whole."""
    a = arg.strip()
    if a.lower() == "none":
        return "none", {tab: {} for tab in TABS}
    if a.lower().endswith(".json"):
        with open(a, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or set(data) - set(TABS):
            raise ValueError(f"bestiary file {a}: an object of tabs {', '.join(TABS)}")
        loadout = {tab: dict(data.get(tab) or {}) for tab in TABS}
        for tab in TABS:
            check_tab(tab, loadout[tab], t)
        return a, loadout
    usable = options(t["character"])["bestiary"]
    names = {k.lower(): k for k in t["bestiary"]["loadouts"]}
    name = names.get(a.lower())
    if name is None:
        raise ValueError(f"unknown loadout {a!r} (loadouts: {', '.join(usable)})")
    if name not in usable:
        raise ValueError(f"loadout {name!r} has no tab captured with its perks (loadouts: {', '.join(usable)})")
    loadout = {}
    for tab in TABS:
        v = t["bestiary"]["loadouts"][name].get(tab)
        loadout[tab] = dict(v.get("perks") or {}) if v and v.get("decoded") else None
        if loadout[tab]:
            check_tab(tab, loadout[tab], t)
    return name, loadout


def codex_numbers(tiers: dict, t: dict) -> dict:
    """The Healer's Codex at `tiers` ({perk: tier}) in numbers: heal and res/cure chance bonuses, the supplies' range,
    Overheal, Triage, Combat Medic, Immunize."""
    table = t["perks"]["codex"]

    def v(perk, key):
        tier = tiers.get(perk, 0)
        return table[perk][key][tier - 1] if tier else 0

    return {"heal": v("Specialist", "heal") + v("Without Borders", "heal"),
            "chance": v("Specialist", "chance") + v("Without Borders", "chance"),
            "range": VET_BASE_RANGE + v("Without Borders", "range"), "overheal": v("Overheal", "heal"),
            "triage": v("Triage", "dr"), "combat_medic": v("Combat Medic", "dr"), "immunize": v("Immunize", "tick"),
            "window_s": table["Combat Medic"]["seconds"]}


def vet_heal(skill: dict, codex: dict, patients: int, rng: random.Random) -> float:
    """One patient's heal at a supplies channel's finish (`skill` {veterinary, lore}; `codex`: codex_numbers): a
    bandage's (wiki #11327) x 75 % for one patient, 50 % for more (#11331), x (1 + Specialist + Without Borders)."""
    share = VET_SINGLE if patients <= 1 else VET_SHARED
    return (skill["veterinary"] / 100 * rng.uniform(*VET_ROLL) * (1 + VET_LORE * skill["lore"] / 100) * share
            * (1 + codex["heal"]))


# ------------------------------------------------------------ building a pet
def _level(level: int) -> dict:
    lo, hi = min(level, 10), max(level - 10, 0)
    return {"hits": 1 + 0.10 * lo + 0.05 * hi, "damage": 1 + 0.05 * lo + 0.025 * hi,
            "speed": 0.025 * lo + 0.0125 * hi, "armor": 2.5 * lo + 1.0 * hi, "mr": 5.0 * lo + 2.0 * hi}


def _command(aspect, tier) -> dict:
    on = aspect == "command" and tier is not None
    return {"damage": 0.05 + 0.015 * tier if on else 0.0, "speed": 0.025 + 0.0075 * tier if on else 0.0,
            "dr": armor.follower_resist(aspect, tier)}


def _period(g: dict, aspect, tier) -> float:
    return g["attack_s"] / (1 + min(SPEED_CAP, _level(g["level"])["speed"] + _command(aspect, tier)["speed"]))


@functools.lru_cache(maxsize=None)
def k_speeds(character: str = "logan_wolf") -> dict:
    """Per pet with a live period: that period over the formula's at the anchors' Command tier."""
    t = load_tamer(character)
    tier = t["anchors"]["speed_tier"]
    return {n: a["period_s"] / _period(t["guide"][n], "command", tier) for n, a in t["anchors"]["speed"].items()}


def k_speed(name: str, t: dict) -> float:
    ks = k_speeds(t["character"])
    return ks[name] if name in ks else statistics.median(ks.values())


@functools.lru_cache(maxsize=None)
def k_dmg(character: str = "logan_wolf") -> float:
    """The live median of the anchor pet's melee numbers over the model's (its melee hits, k_dmg 1, on a target of
    the anchor's armor in REF_TANK, ANCHOR_N draws of random.Random(1))."""
    t = load_tamer(character)
    a = t["anchors"]["damage"]
    _, loadout = parse_bestiary(a["bestiary"], t)
    spec = build_pet(a["pet"], t, loadout, "command", a["tier"], t["teams"][a["team"]]["pets"], k=1.0)
    rng = random.Random(1)
    tgt = {"armor": a["target_ar"], "magic_resist": a["target_mr"]}
    v = [n for _ in range(ANCHOR_N) for n, lab in melee_numbers(spec, tgt, REF_TANK, rng)[0] if lab == "melee"]
    return a["observed_median"] / statistics.median(v)


@functools.lru_cache(maxsize=None)
def k_override(name: str, character: str = "logan_wolf") -> float | None:
    """A pet's own damage factor (anchors.damage_override, each with its reason) instead of k_dmg: its model mean per
    number (k 1; a caster's spells and Spellburn with Spellsurge carried from cast to cast in REF_CASTER, a melee
    pet's hits in REF_TANK) on the anchor's target equal to `mean_per_number`; None without one."""
    t = load_tamer(character)
    a = (t["anchors"].get("damage_override") or {}).get(name)
    if a is None:
        return None
    _, loadout = parse_bestiary(a["bestiary"], t)
    spec = build_pet(name, t, loadout, "command", a["tier"], t["teams"][a["team"]]["pets"], k=1.0)
    rng = random.Random(1)
    tgt = {"armor": a["target_ar"], "magic_resist": a["target_mr"]}
    v, surge = [], False
    for _ in range(a["n"]):
        if spec["attack"] == "spell":
            nums, eff = spell_numbers(spec, tgt, replace(REF_CASTER, surge=surge), rng)
            surge = any(e == "surge" for e, _ in eff)
        else:
            nums, _ = melee_numbers(spec, tgt, REF_TANK, rng)
        v += [n for n, _ in nums]
    return a["mean_per_number"] / statistics.fmean(v)


def build_pet(name: str, t: dict, loadout: dict, aspect, tier, team: list, k: float | None = None) -> dict:
    """The World's spec of pet `name` with the bestiary `loadout` ({tab: perks or None}), Logan's armor aspect and
    tier, in `team` (pet names: Swarm and Menagerie count it). `k`: the damage factor (default its k_override, else
    k_dmg)."""
    pet = _pet(name, t)
    sp = t["species"][pet["species"]]
    g = t["guide"][name]
    lv, cmd, tm = _level(g["level"]), _command(aspect, tier), t["tamer"]
    under = max(0.0, min(1.0, (min(tm["taming"], tm["lore"]) - sp["min_taming"]) / 70.0))
    speed = lv["speed"] + cmd["speed"]
    dmg_static = (1 + cmd["damage"] + tm["follower_damage"] + 0.10 * tm["veterinary"] / 100 + 0.4 * under
                  + sp["zoology"]["damage"] + max(0.0, speed - SPEED_CAP))
    dr_static = cmd["dr"] + 0.2 * under + sp["zoology"]["dr"]
    if k is None:
        k = k_override(name, t["character"])
    f = lv["damage"] * dmg_static * (k_dmg(t["character"]) if k is None else k)
    lo, hi = g["damage"][0] * f, g["damage"][1] * f
    period = _period(g, aspect, tier) * k_speed(name, t)
    perks = dict(loadout.get(sp["class"]) or {})
    table = t["perks"]["bestiary"]
    ranks = {(p[len(CROSS):] if p.startswith(CROSS) else p): r for p, r in perks.items()}
    pv = {p: {key: vals[r - 1] for key, vals in table[p].items() if isinstance(vals, list)} for p, r in ranks.items()}
    abilities = copy.deepcopy(sp["abilities"])
    names = {a["name"] for a in abilities}
    team_species = {_pet(n, t)["species"] for n in team}
    heal_bonus = (pv.get("Menagerie", {}).get("heal_per", 0) * len(team_species)
                  + pv.get("Swarm", {}).get("heal_per", 0) * len(team) + pv.get("Mending", {}).get("heal", 0))
    shield = next((a for a in abilities if a["name"] == "Earth Shield"), {})
    codex = codex_numbers(t["codex"]["current"], t)
    spec = {"serial": pet["serial"], "key": pet["species"], "name": name, "body": sp["body"], "species": pet["species"],
            "class": sp["class"], "slots": sp["slots"], "level": g["level"], "attack": sp["attack"],
            "hits": round(g["hits_max"] * lv["hits"]), "ar": g["armor"] + lv["armor"], "mr": g["magic_resist"] + lv["mr"],
            "damage": [lo, hi], "damage_max": hi, "dmg_static": dmg_static, "dr_static": dr_static, "period": period,
            "abilities": abilities, "perks": perks, "ranks": ranks, "pv": pv,
            "has": {"bleed": "Bleed" in names, "disease": "Disease" in names, "soak": "Soak" in names,
                    "spellburn": "Spellburn" in names,
                    "cooldown": any(a["type"] == "cooldown" for a in abilities),
                    "passive": any(a["type"] == "passive" for a in abilities)},
            "team_pets": len(team), "team_types": len(team_species), "heal_bonus": heal_bonus,
            "shield_armor": shield.get("armor", 0), "shield_bonus": shield.get("melee_bonus", 0.0),
            "codex": {k_: codex[k_] for k_ in ("triage", "combat_medic", "immunize")},
            "threat_x": t["class_threat_x"][sp["class"]], "step_s": t["step_s"], "follow": t["follow"],
            "leash": t["leash"], "specials": []}
    if sp["attack"] == "melee":
        spec.update(melee={"lo": lo, "hi": hi}, swing_s=period, hit_chance=t["hit_chance"])
    else:
        sa = t["anchors"]["speed"].get(name) or {}
        spec["ranged"] = {"range": t["spell_range"], "every_s": period, "cast_s": [0, 0, 0], "flight_s": [0, 0, 0],
                          "cast_action": sa["action"] if sa.get("action") is not None else 4, "effect": None}
    return spec


def ref_key(ref) -> str:
    """`sc["pet_ref"]`'s key of a creature entry's `ref` ({pet, bestiary}; None or "default": the scenario's)."""
    return "default" if not ref or ref == "default" else f"{ref['pet']}|{ref['bestiary']}"


def ref_values(ref: dict, t: dict, tier) -> dict:
    """{ar, mr, dr} of the reference pet `ref` with its loadout at Command `tier`, its DR in REF_TANK."""
    _, loadout = parse_bestiary(ref["bestiary"], t)
    spec = build_pet(ref["pet"], t, loadout, "command", tier, [ref["pet"]])
    return {"ar": spec["ar"], "mr": spec["mr"], "dr": dr(spec, REF_TANK, True)}


def _refs(sc: dict) -> dict:
    out = {"default": sc["defaults"]["pet_ref"]}
    for c in (sc.get("creatures") or {}).values():
        for r in (c.get("melee_ref"), (c.get("ranged") or {}).get("ref"), *(s.get("ref") for s in c.get("specials", []))):
            if r:
                out[ref_key(r)] = r
    return out


def select(sc: dict, team: str | None = None, bestiary: str | None = None) -> None:
    """Build the scenario's pets: `team` (a preset or pet names; default the scenario's `tamer.team`) with the bestiary
    loadout `bestiary` (default: the team preset's own, else the scenario team's). Writes sc["pets"], sc["party"],
    sc["vet"]["range"] / ["codex"] / ["skill"] and sc["pet_ref"]. Run after the armor is set (--aspect-tier)."""
    from sim.scenario import player_armor
    t = load_tamer(sc["tamer"]["character"])
    names, preset = parse_team(team or sc["tamer"]["team"], t)
    if bestiary:
        label, loadout = parse_bestiary(bestiary, t)
    else:
        src = preset or next((k for k in t["teams"] if k.lower() == sc["tamer"]["team"].strip().lower()), None)
        label, loadout = parse_bestiary(t["teams"][src]["bestiary"], t) if src else parse_bestiary("none", t)
    warnings = []
    for tab in TABS:
        if loadout[tab] is None:
            who = [n for n in names if _species(n, t)["class"] == tab]
            if who:
                warnings.append(f"{label} {tab} tab not captured: no perks for {', '.join(who)}")
            loadout[tab] = {}
    arm = player_armor(sc)
    sc["pets"] = [build_pet(n, t, loadout, arm["aspect"], arm["tier"], names) for n in names]
    sc["party"] = {"team": names, "preset": preset, "bestiary": label, "loadout": loadout,
                   "codex": dict(t["codex"]["current"]), "warnings": warnings}
    sc["vet"]["codex"] = dict(t["codex"]["current"])
    sc["vet"]["range"] = codex_numbers(sc["vet"]["codex"], t)["range"]
    sc["vet"]["skill"] = {k: t["tamer"][k] for k in ("veterinary", "lore")}
    tier = sc["player"]["armor"]["pets_tier"]
    sc["pet_ref"] = {k: ref_values(r, t, tier) for k, r in _refs(sc).items()}


# ------------------------------------------------------------ per hit (World and the anchor)
def _dmg_perk(name: str, v: dict, s: dict, c: HitCtx) -> float:
    has = s["has"]
    if name in ("Ambush", "Feral", "Heavy Breathing", "Debilitate", "Deep Lungs", "Frostbite", "Opportunity",
                "Punishment"):
        return v["dmg"]
    if name in ("Cornered",):
        return v["dmg_cond"] if c.hurt_10s else v["dmg"]
    if name == "Feed":
        return (v["dmg_cond"] if c.target_bleeding else 0.0) if has["bleed"] else v["dmg"]
    if name == "Hunting Party":
        return v["dmg_cond"] if c.tamer_within_2 else v["dmg"]
    if name == "Immolate":
        return 0.0 if has["spellburn"] else v["dmg"]
    if name == "Menagerie":
        return v["dmg_per"] * s["team_types"]
    if name in ("Mutation",):
        return 0.0 if has["cooldown"] else v["dmg"]
    if name == "Pelt":
        return v["dmg_cond"] if c.dist >= PELT_FAR else v["dmg"]
    if name == "Primal":
        return 0.0 if has["passive"] else v["dmg"]
    if name == "Swarm":
        return v["dmg_per"] * s["team_pets"]
    if name == "Beast Sting":
        return v["dmg_cond"] if c.target_poisoned else v["dmg"]
    if name == "Carrion":
        return (v["dmg_cond"] if c.target_diseased else 0.0) if has["disease"] else v["dmg"]
    if name in ("Contagion", "Toxic"):
        return 0.0 if has["disease"] else v["dmg"]
    if name == "Metabolism":
        return v["dmg_cond"] if c.hp_frac >= 1.0 else 0.0
    if name == "Scour":
        return 0.0 if has["soak"] else v["dmg"]
    if name == "Swoop":
        return (v["swoop"] if c.swoop else 0.0) if has["cooldown"] else v["dmg"]
    if name == "Adrenaline":
        return v["dmg_cond"] if c.after_res else v["dmg"]
    if name == "Aggression":
        return v["dmg_per"] * min(3, c.enemies_10s) if c.enemies_10s else v["dmg"]
    if name == "Behemoth":
        return v["dmg_cond"] if s["slots"] >= 3 else v["dmg"]
    if name == "Hearty":
        return v["dmg_cond"] if c.hp_frac > HEARTY_ABOVE else v["dmg"]
    if name == "Mending":
        return v["dmg_cond"] if c.hp_frac >= 1.0 and not c.poisoned else 0.0
    if name == "Trample":
        return v["dmg"] if s["attack"] == "spell" else 0.0
    return 0.0          # Giant Killer, Lethality, Breaker, Dominance, Alpha, Bully, Critical Care, ...: not damage


def damage_bonus(spec: dict, ctx: HitCtx) -> float:
    """The perks' damage bonus on this number (one additive pool)."""
    return sum(_dmg_perk(n, v, spec, ctx) for n, v in spec["pv"].items())


def lethality(spec: dict, rng: random.Random) -> float:
    """Lethality's multiplier on one number: x2 on its chance, else 1 + its bonus; 1 without it."""
    v = spec["pv"].get("Lethality")
    if v is None:
        return 1.0
    return 2.0 if rng.random() < v["chance"] else 1.0 + v["dmg"]


def _dr_perk(name: str, v: dict, s: dict, c: HitCtx, aggroed: bool) -> float:
    if name == "Adrenaline":
        return v["dr_cond"] if c.after_res else v["dr"]
    if name == "Aggression":
        return v["dr_per"] * min(3, c.enemies_10s)
    if name == "Behemoth":
        return v["dr_cond"] if s["slots"] >= 3 else v["dr"]
    if name in ("Bully", "Stalwart", "Survival"):
        return v["dr"]
    if name == "Deflection":
        return v["dr_cond"] if aggroed else v["dr"]
    if name == "Guardians":
        return v["dr_cond"] if c.tanks_alive >= 2 else v["dr"]
    if name == "Tolerance":
        return 0.0 if c.afflicted else v["dr"]
    return 0.0


def dr(spec: dict, ctx: HitCtx, attacker_aggroed: bool = False) -> float:
    """Its damage resistance now: the static pool, the perks', the codex's Triage (doubled below 33 % hits) during a
    channel and Combat Medic after one; at most DR_CAP."""
    d = spec["dr_static"] + sum(_dr_perk(n, v, spec, ctx, attacker_aggroed) for n, v in spec["pv"].items())
    cx = spec["codex"]
    if ctx.channel and cx["triage"]:
        d += cx["triage"] * (2 if ctx.hp_frac < TRIAGE_LOW else 1)
    if ctx.medic:
        d += cx["combat_medic"]
    return min(DR_CAP, d)


def hit_chance(spec: dict, ctx: HitCtx, tgt_on_it: bool) -> float:
    """A melee pet's chance to hit: its base + Giant Killer (doubled below 0.66) + Dominance (its first 5 hits on the
    target in a window) + Alpha (doubled when the target is on it); at most 1."""
    h, pv = spec.get("hit_chance", 0.95), spec["pv"]
    if "Giant Killer" in pv:
        b = pv["Giant Killer"]["hit"]
        h += 2 * b if h + b < GIANT_KILLER_BELOW else b
    if "Dominance" in pv and ctx.dom_hits < DOMINANCE_HITS:
        h += pv["Dominance"]["hit"]
    if "Alpha" in pv:
        h += pv["Alpha"]["hit"] * (2 if tgt_on_it else 1)
    return min(1.0, h)


def passive_chance(spec: dict, base: float) -> float:
    v = spec["pv"].get("Primal")
    return base * (1 + v["passive"]) if v is not None and spec["has"]["passive"] else base


def cooldown(spec: dict, base_s: float) -> float:
    v = spec["pv"].get("Mutation")
    return max(1.0, base_s - v["cooldown_s"]) if v is not None and spec["has"]["cooldown"] else base_s


def heal_mult(spec: dict) -> float:
    return 1.0 + spec.get("heal_bonus", 0.0)


def physical_mitigation(spec: dict, tgt_spec: dict, rng: random.Random, half: bool = False) -> float:
    """The share of a pet's physical number the monster keeps: its armor (less Breaker's, halved by half_resist)."""
    ar = tgt_spec.get("armor") or 0
    brk = spec["pv"].get("Breaker")
    if brk is not None:
        ar = max(0.0, ar - min(brk["ar"], BREAKER_AR_CAP))
    if half:
        ar /= 2
    if (brk is not None or half) and ar <= 0:
        return 1 + IGNORE_ZERO_BONUS
    return armor.taken(ar, rng)


def spell_mitigation(spec: dict, tgt_spec: dict, rng: random.Random, half: bool = False) -> float:
    """The share of a pet's spell number the monster keeps: its magic resist (less Breaker's, halved by half_resist)."""
    mr = tgt_spec.get("magic_resist") or 0
    brk = spec["pv"].get("Breaker")
    if brk is not None:
        mr = max(0.0, mr - min(brk["mr"], BREAKER_MR_CAP))
    if half:
        mr /= 2
    if (brk is not None or half) and mr <= 0:
        return 1 + IGNORE_ZERO_BONUS
    return max(0.0, 1 - rng.uniform(*SPELL_RESIST) * mr / 100)


def _passives(spec: dict, ctx: HitCtx, rng: random.Random) -> list:
    """The passive abilities that proc on this attack."""
    return [a for a in spec["abilities"] if a["type"] == "passive" and rng.random() < passive_chance(spec, a["chance"])]


def melee_numbers(spec: dict, tgt_spec: dict, ctx: HitCtx, rng: random.Random) -> tuple[list, list]:
    """One melee hit: ([(amount, label)], [(effect, params)]). Effects: disease {tick, ticks, every_s, double},
    bleed {tick, every_s, seconds}, soak {cripple, seconds}, heal {share of max hits}, trample {amount}."""
    pv = spec["pv"]
    bonus = damage_bonus(spec, ctx) + (spec["shield_bonus"] if ctx.shield else 0.0)
    lo, hi = spec["damage"]
    nums = [(rng.uniform(lo, hi) * (1 + bonus) * lethality(spec, rng) * physical_mitigation(spec, tgt_spec, rng),
             "melee")]
    eff, dm = [], spec["damage_max"]
    for a in _passives(spec, ctx, rng):
        if a["name"] == "Disease":
            tick = dm * a["mult"] * (1 + pv.get("Contagion", {}).get("tick", 0.0))
            double = "Toxic" in pv and rng.random() < pv["Toxic"]["double_first"]
            eff.append(("disease", {"tick": tick, "ticks": int(round(a["seconds"] / a["every_s"])),
                                    "every_s": a["every_s"], "double": double}))
        elif a["name"] == "Bleed":
            n = int(round(a["seconds"] / a["every_s"]))
            eff.append(("bleed", {"tick": dm * a["mult"] / n, "every_s": a["every_s"], "seconds": a["seconds"]}))
        elif a["name"] == "Soak":
            eff.append(("soak", {"cripple": a["cripple_per_slot"] * spec["slots"]
                                 * (1 + pv.get("Scour", {}).get("cripple", 0.0)), "seconds": a["seconds"]}))
    heal = pv.get("Metabolism", {}).get("heal", 0.0)
    if spec["has"]["bleed"] and ctx.target_bleeding and ctx.dist <= NEAR_TARGET_R:
        heal += pv.get("Feed", {}).get("heal", 0.0)
    if spec["has"]["disease"] and ctx.target_diseased and ctx.dist <= NEAR_TARGET_R:
        heal += pv.get("Carrion", {}).get("heal", 0.0)
    if heal:
        eff.append(("heal", {"share": heal}))
    if "Trample" in pv:
        eff.append(("trample", {"amount": dm * pv["Trample"]["trample"]}))
    return nums, eff


def spell_numbers(spec: dict, tgt_spec: dict, ctx: HitCtx, rng: random.Random) -> tuple[list, list]:
    """One cast landing: ([(amount, label)], [(effect, params)]): the spell (x the Spellsurge waiting, if any), a
    Spellburn proc's own number (damage_max x its mult x (1 + bonus + Immolate), half the target's resist).
    Effects: surge {window_s} (the next cast's bonus)."""
    pv = spec["pv"]
    bonus = damage_bonus(spec, ctx)
    lo, hi = spec["damage"]
    surge = next((a for a in spec["abilities"] if a["name"] == "Spellsurge"), None)
    sx = 1 + surge["bonus"] if surge is not None and ctx.surge else 1.0
    nums = [(rng.uniform(lo, hi) * (1 + bonus) * lethality(spec, rng) * sx * spell_mitigation(spec, tgt_spec, rng),
             "spell")]
    eff = []
    for a in _passives(spec, ctx, rng):
        if a["name"] == "Spellburn":
            b = bonus + pv.get("Immolate", {}).get("spellburn", 0.0)
            nums.append((spec["damage_max"] * a["mult"] * (1 + b) * lethality(spec, rng)
                         * spell_mitigation(spec, tgt_spec, rng, half=a.get("half_resist", False)), "spellburn"))
        elif a["name"] == "Spellsurge":
            eff.append(("surge", {"window_s": a["window_s"]}))
    return nums, eff


def ability_numbers(spec: dict, ab: dict, tgt_spec: dict, ctx: HitCtx, rng: random.Random) -> list:
    """A damaging cooldown ability's numbers on its target: Epic Barrage `hits` spells of damage_max x mult (half
    resist), Earth Breath one physical number of damage_max x mult; each x (1 + bonus) x Lethality."""
    bonus = damage_bonus(spec, ctx)
    base = spec["damage_max"] * ab["mult"] * (1 + bonus)
    half = ab.get("half_resist", False)
    if ab.get("damage") == "physical":
        return [(base * lethality(spec, rng) * physical_mitigation(spec, tgt_spec, rng, half), "melee")
                for _ in range(ab.get("hits", 1))]
    return [(base * lethality(spec, rng) * spell_mitigation(spec, tgt_spec, rng, half), "spell")
            for _ in range(ab.get("hits", 1))]


def taken_factor(spec: dict, ctx: HitCtx, dtype: str, rng: random.Random, ref: dict, attacker_aggroed: bool) -> float:
    """The factor on a monster's number (measured on the reference pet `ref` {ar, mr, dr}) a pet takes: physical its
    armor over the reference's (Earth Shield's armor while up), spell its magic resist over the reference's, true
    neither; each x (1 - its DR) / (1 - the reference's). Ticks (bleed, disease, poison): Tolerance's chance of none,
    Immunize's cut in its window. none (oxygen): 1."""
    if dtype == "none":
        return 1.0
    if dtype == "tick":
        tol = spec["pv"].get("Tolerance")
        if tol is not None and rng.random() < tol["skip"]:
            return 0.0
        return 1.0 - spec["codex"]["immunize"] if ctx.immunized else 1.0
    x = (1 - dr(spec, ctx, attacker_aggroed)) / (1 - ref["dr"])
    if dtype == "physical":
        ar = spec["ar"] + (spec["shield_armor"] if ctx.shield else 0)
        return armor.taken(ar, rng) / armor.mean_taken(ref["ar"]) * x
    if dtype == "spell":
        return max(0.0, 1 - rng.uniform(*SPELL_RESIST) * spec["mr"] / 100) \
            / (1 - sum(SPELL_RESIST) / 2 * ref["mr"] / 100) * x
    return x

"""Self-optimizing lumber job (docs/LUMBER_LOOP.md §6): where to chop, how much
to carry home, and which hatchet to use, learned from the memory store.

The objective is banked logs per hour of agent time, net of expected PK losses
(logs/hour is the proxy for gold/hour until colored-wood prices are known; user
decision 2026-10-02). The overseer asks `ctl lumber plan` before each lumber run
and gets one runner command back.

Model (one spot = a tree area plus its bank, `load_spots`):
  - field rate λ: logs per hour in the field (walking between trees, chopping,
    escapes and holds included), per spot. Gamma posterior over recency-weighted
    trips (half-life HALF_LIFE_DAYS), quasi-Poisson with dispersion φ (logs come
    in lumps of ~8 per success). Prior (empirical Bayes): the spread of the
    measured spots' rates, at least PRIOR_CV_MIN, so an unvisited spot is
    explored as "a spot like the others" but not trusted.
  - character: each trip's chopping time is rescaled to today's success chance
    p = Σ_wood tree-colour chance × min(1, (skill − offset)/divisor × (1 + tool
    bonus)) (wiki formulas in woods.json; a colour above our skill counts as
    regular wood [INFERENCE]; Terran 2026-10-02 measured 44/63 = 0.70 at 69.1
    skill, the formula says 0.69), so a spot measured at lower skill is
    credited with what it would yield now. Walking time doesn't scale.
  - overhead T: walk out + convert + walk to the bank + store, per trip. Normal
    posterior; prior from the bank-to-area distance.
  - hazards per field hour, three kinds competing while we chop (trip_terms):
    death h_D (PK or creature; the trip banks nothing, the carried logs and
    every unblessed item we carry are lost, RECOVERY_H), sent home h_S (a
    threat ended the trip early: recall escape, guard flight, a creature stop;
    the carried logs come home) and theft h_T (a thief takes a fraction f of
    what we carry; the trip goes on). Each is a Gamma posterior per spot shrunk
    to a pooled rate; h_D's prior is the spot's hostile-player sightings per
    field hour × P(death | sighting) plus the pooled creature-death rate.
  - trip size Q*: maximises the renewal-reward rate E[banked logs per trip
    cycle] / E[cycle time], minus supplies and the expected gear loss in logs,
    over Q_MIN…Q_MAX and what we can still carry (LUMBER_LOOP §6: Q* ≈
    λ·sqrt(2T/h) for a small death hazard alone).
  - choice: Thompson sampling. One posterior draw per eligible spot, the best
    draw wins; a spot other than the one we stand at pays travel_min out of a
    run (a stint, or one trip when that's longer). That keeps exploring the
    uncertain spots while the evidence favours the good ones, and varies the
    routine (ANTICHEAT.md §8.3).
  - hatchet: per owned or buyable hatchet, the net logs/hour at the chosen spot
    minus wear (one use per success), in logs at the ordinary board price. Every
    carried hatchet is lost on death whichever is used; buying one adds its
    price to that. Unknown prices give a break-even price instead.
  - regrowth: from harvest_attempts, a depleted tree tried again later; the
    gap where an isotonic fit of P(regrown | gap) reaches REGROW_P.

Pure functions over plain lists (`plan`, `regrowth`, `trip_obs`, ...) carry the
logic and are what test_lumber_opt.py checks; `plan_from_store` gathers the
inputs from the memory store and a state-port snapshot.
"""
import copy
import json
import math
import os
import random
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "harness", "data")
SEEDS = os.path.join(DATA, "lumber_spots.json")
HATCHETS_FILE = os.path.join(DATA, "hatchets.json")
WOODS_FILE = os.path.join(DATA, "woods.json")

HATCHET_GRAPHICS = (0x0F43, 0x0F44)
LAYER_MOUNT = 0x19
LUMBERJACKING_ID = 44             # skills.mul order; looked up by name when the names are known

HALF_LIFE_DAYS = 14.0             # a trip's weight halves every two weeks: competition, PKs and patches change
PRIOR_CV_MIN = 0.35               # a new spot's rate prior is at least this uncertain (spots differ a lot)
PRIOR_MIN_FIELD_H = 0.5           # weighted field hours before a spot's rate shapes the prior
DEFAULT_RATE = 1200.0             # logs per field hour before any trip (live 2026-10-02 at 69 skill: 1200-2000)
DEFAULT_CHOP_S = 9.5              # s per attempt for trips that didn't time their chops (LUMBER_LOOP §12)
DEFAULT_CHOP_SHARE = 0.7          # share of field time spent chopping when no trip timed it
DISPERSION_MIN = 8.0              # Var(logs) ≥ ~8 × mean: logs come ~7.6 per success
LOGS_PER_SUCCESS = 7.6            # until harvest_attempts say otherwise (live mean 7.6)
LOGS_PER_TREE = 19.0              # logs a tree gives before 'depleted' (harvest_attempts 2026-10-03: 470 cycles,
                                  # mean 19.3; per spot 17.7-20.2) until harvest memory has enough cycles
LOGS_PER_TREE_MIN_CYCLES = 20
YIELD_PRIOR_TILES = 10.0          # tree tiles of pseudo-data behind a spot's yielding share (pooled mean)
SEC_PER_TILE = 0.45               # overhead prior: walking pace incl. detours [INFERENCE: mounted 0.1-0.2 s/step, on foot 0.4]
OVERHEAD_FIXED_S = 40.0           # convert + open the bank + store + human pauses (live 10-20 s + pauses)
OVERHEAD_SD_MIN_S = 30.0
HAZARD_PRIOR_H = 2.0              # field hours of pseudo-data behind a spot's hazard_prior (sightings)
DEATH_PRIOR = (1.0, 3.0)          # Beta prior of P(death | hostile player sighted) [INFERENCE]
CREATURE_DEATH_PRIOR = (0.01, 20.0)  # pooled creature deaths per field hour, pseudo field hours [INFERENCE]
DEATH_SHRINK_H = 10.0             # field hours a spot's death-rate prior counts for (deaths are rare)
HOME_PRIOR = (0.5, 2.0)           # pooled trips sent home per field hour, pseudo field hours [INFERENCE]
HOME_SHRINK_H = 2.0               # field hours a spot's sent-home prior counts for
THEFT_PRIOR = (0.02, 20.0)        # pooled thefts per field hour, pseudo field hours [INFERENCE]
THEFT_SHRINK_H = 20.0             # thefts are rare: a spot's rate leans on the pooled one
THEFT_FRACTION_PRIOR = (1.0, 1.0)  # Beta prior of the share of the carried logs one theft takes (mean 0.5) [INFERENCE]
CAUSE_LINK_S = 300.0              # a sighting this soon before a death makes it a PK death
RECOVERY_H = 20 / 60              # ghost walk, resurrection, re-equip, back to work [INFERENCE: Terran death 2026-10-02]
DEATH_LINK_S = 1800.0             # a death this soon after a lumber trip counts for that trip's spot
COOLDOWN_S = 1800.0               # keep away from a spot this long after a death or a player threat there
STINT_MIN = 60.0                  # minutes one runner start should last (when trips are shorter)
Q_MIN, Q_MAX = 200, 10000         # logs per trip (user decision 2026-10-03)
Q_GRID = tuple(sorted({int(round(Q_MIN * (Q_MAX / Q_MIN) ** (i / 40) / 25.0)) * 25 for i in range(41)}))
LOG_WEIGHT = 0.025                # stones per log or board (docs/NOTES.md)
REGROW_P = 0.6                    # revisit a depleted tree once P(regrown) reaches this
REGROW_DEFAULT_MIN = 45.0         # until enough depleted-then-retried trees are seen
REGROW_MIN_PAIRS = 20
DRAWS = 2000                      # Monte Carlo draws for P(best)
CURRENT_SPOT_MARGIN = 40          # tiles beyond a spot's radius that still count as standing at it
NEAR_BANK = 30
HUB_RADIUS = 60                   # tiles from a rune library: its rune spots are at hand
LOCKOUT_S = 60.0                  # harvest lockout after any travel (TRAVEL_DEATH §2)
RECALL_TRIP_S = 4.0               # open the book, press, the 2 s cast, arrival (live 2.1 s + the book)
HOME_RUNE_TILES = 10              # walk from the home rune to the banker [INFERENCE: mark it by the bank]
FAIL_EXPOSURE_H = 0.25            # a trip the place itself spoiled counts at least this many field hours
UNWORKABLE_TRIPS = 2              # that many such trips in a row: the spot is out ...
UNWORKABLE_DAYS = 7.0             # ... for this long, then gets one more try
TRAVEL_GAP_S = 3600.0             # a gap this short between trips at two spots is the travel between them
# Abort reasons that say the place can't be worked (not the character, the server or a player):
# no tree we can reach, harvesting answered by something the runner doesn't know (a town region)
PLACE_FAILURES = ("no harvestable tree", "without a known outcome")


# ------------------------------------------------------------------ small helpers
def _num(v, default=None):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def cheb(a, b) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _serial(v) -> int:
    return int(v, 16) if isinstance(v, str) else int(v)


# ------------------------------------------------------------------ spots
def load_seeds(path: str = SEEDS) -> dict:
    """{id: spot} from the committed seed file."""
    return {s["id"]: s for s in load_json(path)["spots"]}


def load_spots(memory, seeds_path: str = SEEDS) -> dict:
    """Seed spots merged with the store's lumber_spots rows: a row with the id of
    a seed overrides its status (and any fields it carries); other rows are
    spots of their own. Every spot gets status/source/reason."""
    out = {}
    for sid, s in load_seeds(seeds_path).items():
        out[sid] = {**s, "status": "active", "source": "seed", "reason": None}
    for row in (memory.lumber_spot_rows() if memory is not None else []):
        base = out.get(row["id"], {"id": row["id"]})
        out[row["id"]] = {**base, **row["data"], "id": row["id"], "status": row["status"],
                          "source": row["source"] if row["id"] not in out else base["source"],
                          "reason": row["reason"]}
    return out


def check_spot(spot: dict):
    """A spot the runner can work: an area and a banker position."""
    area, banker = spot.get("area") or {}, spot.get("banker") or {}
    if len(area.get("center") or ()) != 2 or _num(area.get("radius")) is None:
        raise ValueError(f"spot {spot.get('id')}: area needs center [x, y] and radius")
    if len(banker.get("pos") or ()) != 3:
        raise ValueError(f"spot {spot.get('id')}: banker needs pos [x, y, z]")


def spot_knowledge(know: dict, spot: dict) -> dict:
    """The runner's knowledge for one spot: loops/lumber.json's common facts
    (texts, captcha, conversion) with the spot's venue fields over them."""
    check_spot(spot)
    k = copy.deepcopy(know)
    k.pop("venue", None)                         # the demo's venue; the spot says where we are
    k["spot"] = {key: spot.get(key) for key in ("id", "name", "facet", "pvp", "requires_young", "access", "home")}
    k["pvp"] = bool(spot.get("pvp", True))
    k["facet"] = int(spot.get("facet") or 0)
    k.setdefault("npcs", {})["banker"] = dict(spot["banker"])
    k["harvest"]["trees"] = list(spot.get("trees") or [])
    k["harvest"]["area"] = dict(spot["area"])
    return k


def current_spot(spots: dict, pos, facet) -> str | None:
    """The spot we stand at or by: inside its area (+ CURRENT_SPOT_MARGIN) or
    within NEAR_BANK tiles of its bank; the nearest such one. A Witcher spot's
    bank is the shared home, so standing there (or within HUB_RADIUS of a rune
    library) gives "hub:<library id>": every spot reached from that library is
    at hand, its travel being the trip overhead from home."""
    if not pos:
        return None
    best, home_hub = None, None
    for sid, s in spots.items():
        if int(s.get("facet") or 0) != int(facet or 0) or not s.get("area"):
            continue
        d_area = cheb(pos, s["area"]["center"])
        # A Witcher spot's bank is the home every such spot shares, not the field: standing
        # there isn't being at the spot (live 2026-10-03 at the Cambria bank, "here" was an
        # arbitrary Witcher candidate). Its travel is the overhead from that bank.
        home_only = (s.get("access") or {}).get("method") == "witcher"
        d_bank = cheb(pos, s["banker"]["pos"]) if s.get("banker") and not home_only else 10 ** 6
        if home_only and s.get("banker") and cheb(pos, s["banker"]["pos"]) <= NEAR_BANK:
            home_hub = hub_of(s)
        if d_area <= s["area"]["radius"] + CURRENT_SPOT_MARGIN or d_bank <= NEAR_BANK:
            d = min(d_area, d_bank)
            if best is None or d < best[0]:
                best = (d, sid)
    if best:
        return best[1]
    if home_hub:
        return home_hub
    import places
    for lib in places.witcher()["libraries"]:
        if lib["facet"] == int(facet or 0) and cheb(pos, lib["stand"]) <= HUB_RADIUS:
            return f"hub:{lib['id']}"
    return None


def hub_of(spot: dict) -> str | None:
    """"hub:<library>" for a spot reached by a library rune, else None."""
    access = spot.get("access") or {}
    return f"hub:{access.get('library', 'cambria')}" if access.get("method") == "witcher" else None


# ------------------------------------------------------------------ hatchets and the character
def load_hatchets(path: str = HATCHETS_FILE) -> dict:
    return load_json(path)


def parse_hatchet_spec(spec: str | None):
    """'copper' / 'copper+exceptional' -> (material, quality or None)."""
    if not spec:
        return None
    material, _, quality = spec.strip().lower().partition("+")
    return material.strip(), (quality.strip() or None)


def hatchet_kind(item: dict, table: dict) -> dict:
    """Material (by hue), quality (by the clicked name, when known) and what
    they give: tool bonus, total uses. An unknown hue is material 'unknown'
    with no bonus."""
    hue = item.get("hue") or 0
    mat = next((m for m in table["materials"] if m["hue"] == hue), None)
    name = (item.get("name") or "").lower()
    quality = next((q for q in table["qualities"] if q["name"] in name), None)
    bonus = (mat["tool_bonus"] if mat else 0.0) + (quality["tool_bonus"] if quality else 0.0)
    uses = table["base_uses"] + (mat["extra_uses"] if mat else 0) + (quality["extra_uses"] if quality else 0)
    return {"material": mat["name"] if mat else "unknown", "quality": quality["name"] if quality else None,
            "hue": hue, "tool_bonus": round(bonus, 3), "uses": uses,
            "newbied": "newbied" in name or "blessed" in name}


def matches(kind: dict, want) -> bool:
    """kind (hatchet_kind) is the wanted (material, quality) from parse_hatchet_spec."""
    return want is None or (kind["material"] == want[0] and (want[1] is None or kind["quality"] == want[1]))


_WOODS = None


def success_p(skill, bonus=0.0):
    """Chance that an attempt yields logs (wiki Lumberjacking, woods.json): a
    tree is wood w with base_chance_pct, and chopping w succeeds with
    min(1, (skill − success_offset) / success_divisor × (1 + bonus)). Colours
    above our skill count as regular wood (the wiki is garbled there; mining
    falls back to iron) [INFERENCE]. None without a skill."""
    global _WOODS
    if _num(skill) is None:
        return None
    if _WOODS is None:
        _WOODS = load_json(WOODS_FILE)["woods"]
    mult = 1.0 + (bonus or 0.0)

    def s(w):
        return max(0.0, min(1.0, (skill - w["success_offset"]) / w["success_divisor"] * mult))
    regular = next(w for w in _WOODS if w["name"] == "ordinary")
    p = 0.0
    for w in _WOODS:
        p += w["base_chance_pct"] / 100.0 * s(w if skill >= w["min_skill"] else regular)
    return min(1.0, p)


def skill_value(me: dict, name: str = "Lumberjacking"):
    """A skill's value from the world model's self (tenths on the wire), or None."""
    skills = me.get("skills") or {}
    names = me.get("skill_names") or []
    sid = names.index(name) if name in names else LUMBERJACKING_ID
    sk = skills.get(str(sid)) or skills.get(sid)
    v = _num((sk or {}).get("value"))
    return None if v is None else v / 10.0


def character(world: dict, self_serial, table: dict) -> dict:
    """What the optimizer needs to know about us now (state-port world): skill,
    mounted, buff titles, every hatchet worn or in the backpack (any bag depth)
    with its kind, the reagents in the pack ({name: count}), weight and
    weight_max (stones; the status packet's max already holds Camping's bonus
    [INFERENCE]) and Young status (the "(young)" name label)."""
    import combat
    me = world.get("self") or {}
    items = world.get("items") or {}
    pack = combat.backpack(items, self_serial)
    packed = {k for k, _ in combat.pack_items(items, pack)} if pack is not None else set()
    hatchets = []
    for key, it in items.items():
        if it.get("graphic") not in HATCHET_GRAPHICS or it.get("container") is None:
            continue
        worn = _serial(it["container"]) == self_serial
        if worn or key in packed:
            hatchets.append({"serial": key, "worn": worn, "name": it.get("name"), **hatchet_kind(it, table)})
    hatchets.sort(key=lambda h: (not h["worn"], -h["tool_bonus"]))
    buffs = (world.get("buffs") or {}).get(me.get("serial") or "", {}) or {}
    regs, _ = combat.reagents(world, self_serial) if pack is not None else ({}, False)
    label = (world.get("labels") or {}).get(f"0x{self_serial:08X}", "") or ""
    return {"serial": me.get("serial"), "name": me.get("name"), "skill": skill_value(me),
            "mounted": any(it.get("layer") == LAYER_MOUNT and it.get("container") is not None
                           and _serial(it["container"]) == self_serial for it in items.values()),
            "buffs": sorted({b.get("title") or str(b.get("cliloc") or icon) for icon, b in buffs.items()}),
            "hatchets": hatchets, "reagents": {combat.REAGENTS[g]: n for g, n in regs.items()},
            "weight": _num(me.get("weight")), "weight_max": _num((me.get("stats") or {}).get("weight_max")),
            "young": "(young)" in label.lower()}


# ------------------------------------------------------------------ evidence
def supply_gp(supplies: dict | None, prices: dict) -> tuple[float, int]:
    """(gp, unpriced) of what one trip used (its row's `supplies`): reagents at
    `reagent:<name>` (spaces as _), our own book's charges at `recall_charge` (what
    recharging costs), the public library tome's charges free. Unpriced units count
    nothing and are reported, never guessed."""
    gp, unpriced = 0.0, 0
    s = supplies or {}
    items = [(f"reagent:{k.replace(' ', '_')}", n) for k, n in (s.get("reagents_used") or {}).items()]
    items.append(("recall_charge", _num(s.get("own_charges"), 0)))
    for item, n in items:
        if not n:
            continue
        if item in prices:
            gp += n * prices[item]["price_gp"]
        else:
            unpriced += n
    return gp, unpriced


def trip_obs(ep: dict, prices: dict | None = None) -> dict | None:
    """One trip row (episodes, loop lumber) as the model's observation, or None
    without times. Rows written before 2026-10-02 lack walk_out_s/chop_s/skill:
    the walk out is taken to equal the walk to the bank, chops cost
    DEFAULT_CHOP_S each, and their chopping isn't rescaled. Field time excludes
    the travel lockout waited out at the first tree (`lockout_s`, since
    2026-10-03; it's overhead); a row whose walk out never ended (walk_out_s
    null: no chop, e.g. the recall failed) has no field time. `sent_home`: a
    threat ended the trip early without killing us (a "threat: …" abort: recall
    escape, guard flight, a creature or damage stop; or the row's `creature`
    says it recalled); plan() adds trips with a recall/guard_flight event."""
    t0, t1 = _num(ep.get("t_start")), _num(ep.get("t_end"))
    if t0 is None or t1 is None or t1 < t0:
        return None
    ph = {k: v for k, v in (ep.get("phases_s") or {}).items() if _num(v) is not None}
    outcome = ep.get("outcome") or "banked"
    banked = outcome == "banked" and "to_bank" in ph
    harvest = ph.get("harvest")
    if harvest is None:
        harvest = (t1 - t0) - sum(v for k, v in ph.items() if k != "harvest")
    walk_out = _num(ep.get("walk_out_s"))
    if walk_out is None:
        walk_out = harvest if "walk_out_s" in ep else (ph.get("to_bank", 0.0) if banked else 0.0)
    lockout = _num(ep.get("lockout_s"), 0.0)
    field_s = max(0.0, harvest - walk_out - lockout)
    chop_s = _num(ep.get("chop_s"))
    if chop_s is None:
        chop_s = _num(ep.get("attempts"), 0) * DEFAULT_CHOP_S
    chop_s = min(chop_s, field_s)
    overhead = (walk_out + lockout + ph.get("convert", 0.0) + ph.get("to_bank", 0.0) + ph.get("store", 0.0)
                if banked else None)
    hatchet = ep.get("hatchet") or {}
    logs = _num(ep.get("logs"), 0)
    why = ep.get("why") or ""
    creature = ep.get("creature") if isinstance(ep.get("creature"), dict) else {}
    sent_home = outcome == "aborted" and (why.startswith("threat:") or "escaped by recall" in why
                                          or "fled into the guards" in why or bool(creature.get("recalled")))
    carried = ep.get("carried_end") if isinstance(ep.get("carried_end"), dict) else {}
    gp, unpriced = supply_gp(ep["supplies"], prices or {}) if ep.get("supplies") is not None else (None, 0)
    return {"spot": ep.get("spot") or ep.get("venue"), "t0": t0, "t1": t1, "outcome": outcome,
            "why": ep.get("why"), "dry": bool(ep.get("dry")), "logs": logs,
            "field_s": field_s, "chop_s": chop_s, "overhead_s": overhead,
            "place_fail": outcome == "aborted" and logs == 0 and any(p in why for p in PLACE_FAILURES),
            "sent_home": sent_home and not why.startswith("died"),
            "carried_end": sum(_num(v, 0) for v in carried.values()) if carried else None,
            "hatchet": hatchet or None,
            "p": success_p(ep.get("skill"), hatchet.get("tool_bonus", 0.0)),
            "supply_gp": gp, "supply_unpriced": unpriced}


def adjusted_field_h(trip: dict, p_now) -> float:
    """Field hours the trip's logs would take today: its chopping time scaled by
    p_then / p_now (fewer attempts per log at a higher success chance), its
    walking unchanged. A trip the place spoiled (place_fail: no tree we could
    reach, harvesting refused) counts at least FAIL_EXPOSURE_H with its 0 logs,
    so a spot that can't be worked loses its optimistic prior instead of
    looking untried forever."""
    f, c = trip["field_s"], trip["chop_s"]
    if trip["p"] and p_now:
        f = f - c + c * trip["p"] / p_now
    h = max(f, 0.1 * trip["field_s"]) / 3600.0
    return max(h, FAIL_EXPOSURE_H) if trip.get("place_fail") else h


def weight(t: float, now: float) -> float:
    return 0.5 ** (max(0.0, now - t) / 86400.0 / HALF_LIFE_DAYS)


def attribute_deaths(trips: list, deaths: list, spots: dict) -> list:
    """[(death, spot id, trip or None)] for deaths (dicts t, x, y): during a lumber trip or
    within DEATH_LINK_S after one ended (the Terran PK killed us 11 s after the
    runner stopped), at most CURRENT_SPOT_MARGIN + radius from that spot's
    area; else, with no such trip (rows of aborted trips only exist since
    2026-10-02), inside a spot's area. Deaths elsewhere (hunting) don't count."""
    out = []
    for d in deaths:
        pos = (d["x"], d["y"]) if _num(d.get("x")) is not None else None
        cands = [tr for tr in trips if tr["t0"] <= d["t"] <= tr["t1"] + DEATH_LINK_S]
        if cands:
            tr = max(cands, key=lambda tr: tr["t0"])
            sid = tr["spot"]
            s = spots.get(sid)
            if s is None or (pos is not None and s.get("area") and cheb(pos, s["area"]["center"])
                             > s["area"]["radius"] + CURRENT_SPOT_MARGIN):
                continue
            out.append((d, sid, tr))
        elif pos is not None:
            inside = [(cheb(pos, s["area"]["center"]), sid) for sid, s in spots.items()
                      if s.get("area") and cheb(pos, s["area"]["center"]) <= s["area"]["radius"]]
            if inside:
                out.append((d, min(inside)[1], None))
    return out


def death_cause(d: dict, sightings: list, events: list) -> str:
    """"pk" or "creature" for a death: the runner's own `death` job event within a
    minute says (its cause "pk"; "mob"/"unknown" count as creature); else a
    hostile-player sighting in the CAUSE_LINK_S before it makes it a PK."""
    own = [e for e in events if e["kind"] == "death" and abs(e["t"] - d["t"]) <= 60]
    if own:
        return "pk" if (own[0]["data"] or {}).get("cause") == "pk" else "creature"
    return "pk" if any(d["t"] - CAUSE_LINK_S <= t <= d["t"] for t in sightings) else "creature"


def theft_obs(events: list, trips: list) -> list:
    """[(t, trip or None, share)] per theft: `theft` job events, plus
    `theft_suspected` junctures with no such event within 10 s (the runner posts
    both). share = the wood it took / what we carried then: the event's
    `carried` (logs + boards) when recorded, else a lower bound from the trip
    row (carried at its end + taken) [INFERENCE], else 1; a theft of no wood: 0."""
    thefts = [e for e in events if e["kind"] == "theft"]
    thefts += [e for e in events if e["kind"] == "theft_suspected"
               and not any(abs(x["t"] - e["t"]) <= 10 for x in thefts if x["kind"] == "theft")]
    out = []
    for e in sorted(thefts, key=lambda e: e["t"]):
        data = e["data"] or {}
        lost = data.get("items") if e["kind"] == "theft" else data.get("unexplained_losses")
        wood = sum(_num(it.get("amount"), 1) for it in (lost if isinstance(lost, list) else [])
                   if isinstance(it, dict) and (it.get("wood") or it.get("class") in ("log", "board")))
        tr = next((tr for tr in trips if tr["t0"] <= e["t"] <= tr["t1"]), None)
        c = data.get("carried")
        carried = sum(_num(v, 0) for v in c.values()) if isinstance(c, dict) else _num(c)
        if carried is None and tr is not None and tr.get("carried_end") is not None:
            carried = tr["carried_end"] + wood
        share = 0.0 if not wood else min(1.0, wood / carried) if carried else 1.0
        out.append((e["t"], tr, share))
    return out


def regrowth(rows) -> dict:
    """When a depleted tree has wood again, from harvest_attempts rows
    (t, facet, x, y, z, outcome) in time order: every depleted attempt followed
    by another attempt on the same tree is a (gap, regrown) pair (a fail or a
    success means wood again). P(regrown | gap) is fitted isotonic
    (pool-adjacent-violators); the estimate is the smallest gap where it
    reaches REGROW_P, rounded up to 5 min. REGROW_DEFAULT_MIN with too few
    pairs or when it never does."""
    last, pairs = {}, []
    for t, facet, x, y, z, outcome, *_ in rows:
        k = (facet, x, y, z)
        prev = last.get(k)
        if prev is not None and prev[1] == "depleted" and outcome in ("success", "fail", "depleted"):
            pairs.append(((t - prev[0]) / 60.0, outcome != "depleted"))
        last[k] = (t, outcome)
    pairs.sort()
    out = {"pairs": len(pairs), "regrown": sum(1 for _, r in pairs if r), "minutes": REGROW_DEFAULT_MIN,
           "fitted": False}
    if len(pairs) < REGROW_MIN_PAIRS:
        return out
    blocks = []                                  # [sum, n, first gap]
    for gap, r in pairs:
        blocks.append([float(r), 1, gap])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            s, n, _ = blocks.pop()
            blocks[-1][0] += s
            blocks[-1][1] += n
    curve = []                                   # display: equal neighbours merged
    for s, n, gap in blocks:
        if curve and abs(curve[-1][0] / curve[-1][1] - s / n) < 1e-9:
            curve[-1][0] += s
            curve[-1][1] += n
        else:
            curve.append([s, n, gap])
    out["curve"] = [{"from_min": round(g, 1), "p": round(s / n, 2), "n": n} for s, n, g in curve]
    hit = next((b for b in blocks if b[0] / b[1] >= REGROW_P), None)
    if hit is not None:
        out["minutes"] = float(5 * math.ceil(hit[2] / 5.0))
        out["fitted"] = True
    return out


def tree_yield(attempts, tiles_by_spot: dict, now: float, regrow_min: float) -> dict:
    """What harvest memory says about each spot's trees, the input of its capacity
    (capacity_model). attempts: harvest_attempts rows (t, facet, x, y, z, outcome,
    amount) in time order; tiles_by_spot: spot_tree_tiles(). Returns
    {logs_per_tree, cycles, spots: {id: {trees, tried, yielded, out}}}:
      - logs_per_tree: mean logs of a tree's completed cycles (its successes up to
        'depleted'; cycles with logs only), pooled over every tree; LOGS_PER_TREE
        until LOGS_PER_TREE_MIN_CYCLES cycles;
      - trees: the spot's tree tiles less those found not to be trees;
      - tried / yielded: of those, the ones attempted / that ever gave logs;
      - out: the ones the runner skips now, depleted or unreachable within the
        regrowth window (Memory.harvest_available)."""
    cur, cycles, tiles = {}, [], {}
    for t, facet, x, y, _z, outcome, amount in attempts:
        k = (facet, x, y)
        d = tiles.setdefault(k, {"yielded": False, "not_tree": False, "out_t": None})
        if outcome == "success":
            cur[k] = cur.get(k, 0) + (amount or 0)
            d["yielded"] = True
        elif outcome == "depleted":
            got = cur.pop(k, 0)
            if got > 0:
                cycles.append(got)
            d["out_t"] = t
        elif outcome == "unreachable":
            d["out_t"] = t
        elif outcome == "not_tree":
            d["not_tree"] = True
    window = regrow_min * 60.0
    out = {}
    for sid, (facet, spot_tiles) in tiles_by_spot.items():
        seen = [d for d in (tiles.get((facet, x, y)) for x, y in spot_tiles) if d is not None and not d["not_tree"]]
        bad = sum(1 for x, y in spot_tiles if (tiles.get((facet, x, y)) or {}).get("not_tree"))
        out[sid] = {"trees": len(spot_tiles) - bad, "tried": len(seen),
                    "yielded": sum(1 for d in seen if d["yielded"]),
                    "out": sum(1 for d in seen if d["out_t"] is not None and now - d["out_t"] < window)}
    return {"logs_per_tree": sum(cycles) / len(cycles) if len(cycles) >= LOGS_PER_TREE_MIN_CYCLES else LOGS_PER_TREE,
            "cycles": len(cycles), "spots": out}


def capacity_model(info: dict | None, share0: float) -> dict | None:
    """A spot's capacity parameters from its tree_yield entry (None: unknown, no
    cap): avail = the tree tiles the runner would try now, (a, b) = the Beta
    posterior of the share of tree tiles that give logs (prior: the pooled share
    share0, worth YIELD_PRIOR_TILES tiles). A sparse grove holds few logs per
    trip, and a grove where many mapped trees give nothing (other players'
    chopping, statics that aren't trees) holds fewer than its count says."""
    if info is None:
        return None
    return {"trees": info["trees"], "avail": max(0, info["trees"] - info["out"]),
            "a": YIELD_PRIOR_TILES * share0 + info["yielded"],
            "b": YIELD_PRIOR_TILES * (1.0 - share0) + info["tried"] - info["yielded"]}


def capacity_logs(cap: dict | None, logs_per_tree: float, rng=None):
    """Logs the spot's trees hold for the next trip (avail × yielding share × logs
    per tree): the posterior mean, or one draw with rng; None without a model."""
    if cap is None:
        return None
    share = rng.betavariate(cap["a"], cap["b"]) if rng is not None else cap["a"] / (cap["a"] + cap["b"])
    return cap["avail"] * share * logs_per_tree


# ------------------------------------------------------------------ the model
def overhead_prior_s(spot: dict) -> float:
    """Walk out + back between the bank and the area's edge, plus the bank work.
    A spot reached by a library rune: bank -> library walk, two recalls (out and
    home), the 60 s harvest lockout after the recall out, the walk from the rune's
    landing into the grove (its discovered route, else the straight distance to
    the area's inner half) and a short walk from the home rune to the banker."""
    if not spot.get("banker") or not spot.get("area"):
        return OVERHEAD_FIXED_S + 120.0
    if hub_of(spot):
        import places
        access = spot["access"]
        lib = places.library(access.get("library", "cambria"))
        into = spot.get("route_tiles")
        if into is None:
            try:
                r = places.witcher_rune(access["rune"])
                into = max(0, cheb((r["x"], r["y"]), spot["area"]["center"]) - spot["area"]["radius"] // 2)
            except (KeyError, TypeError):
                into = 0
        walk = cheb(spot["banker"]["pos"], lib["stand"]) + HOME_RUNE_TILES + into
        return OVERHEAD_FIXED_S + LOCKOUT_S + 2 * RECALL_TRIP_S + walk * SEC_PER_TILE
    d = max(0, cheb(spot["banker"]["pos"], spot["area"]["center"]) - spot["area"]["radius"] // 2)
    return OVERHEAD_FIXED_S + 2 * d * SEC_PER_TILE


def dispersion(trips_by_spot: dict, p_now) -> float:
    """Quasi-Poisson φ: Pearson χ² / dof of per-trip logs around each spot's own
    rate, at least DISPERSION_MIN."""
    chi, n, k = 0.0, 0, 0
    for trips in trips_by_spot.values():
        use = [(tr["logs"], adjusted_field_h(tr, p_now)) for tr in trips if tr["field_s"] >= 180]
        if len(use) < 2:
            continue
        lam = sum(lg for lg, _ in use) / sum(h for _, h in use)
        if lam <= 0:
            continue
        k += 1
        for lg, hrs in use:
            n += 1
            chi += (lg - lam * hrs) ** 2 / (lam * hrs)
    if n - k < 2:
        return DISPERSION_MIN
    return max(DISPERSION_MIN, chi / (n - k))


def _e1(a: float, x: float) -> float:
    """∫₀ˣ e^(−a·t) dt."""
    return x if a * x < 1e-12 else -math.expm1(-a * x) / a


def _e2(a: float, x: float) -> float:
    """∫₀ˣ t·e^(−a·t) dt."""
    ax = a * x
    if ax < 1e-4:
        return x * x * (0.5 - ax / 3.0 + ax * ax / 8.0)
    return (-math.expm1(-ax) - ax * math.exp(-ax)) / (a * a)


def trip_terms(q, lam, t_h, hz, recovery_h=RECOVERY_H) -> dict:
    """One trip cycle of q logs as a renewal-reward cycle (LUMBER_LOOP §6). Field
    rate lam (logs/h): chopping q logs takes tf = q/lam field hours, the load
    grows by lam per hour. hz = (h_D, h_S, h_T, f) per field hour, competing:
      - death (h_D): the trip ends and banks nothing; the cycle costs the field
        time so far + recovery_h;
      - sent home (h_S): the trip ends at τ and what we carry comes home;
      - theft (h_T): a thief takes the share f of what we carry; the trip goes
        on (the runner counts logs gained, so it still ends at q gained).
    With k = h_T·f the expected load at t is lam·(1 − e^(−k·t))/k, so with
    h = h_D + h_S:
      banked = e^(−h·tf)·C(tf) + h_S·∫₀^tf e^(−h·t)·C(t) dt
      time   = t_h + ∫₀^tf e^(−h·t) dt + recovery_h·P(death)
      P(death) = h_D·∫₀^tf e^(−h·t) dt   (≤ 1 for any q)
    Returns banked, time_h, p_death, p_home and the expected logs lost to death
    (lost_death) and to thieves (lost_theft)."""
    hd, hs, ht, f = hz
    tf = q / lam
    h, k = hd + hs, ht * f
    stay = _e1(h, tf)                                   # E[field time] = E[min(τ, tf)]
    if k * tf < 1e-9:
        load_tf, load_int = q, lam * _e2(h, tf)         # ∫ e^(−ht)·lam·t dt
    else:
        load_tf, load_int = lam * _e1(k, tf), lam * (stay - _e1(h + k, tf)) / k
    banked = math.exp(-h * tf) * load_tf + hs * load_int
    p_death = hd * stay
    lost_death = hd * load_int
    return {"banked": banked, "time_h": t_h + stay + recovery_h * p_death, "p_death": p_death,
            "p_home": hs * stay, "lost_death": lost_death, "lost_theft": lam * stay - banked - lost_death}


def net_rate(q, lam, t_h, hz, gear_logs, cost_logs=0.0, recovery_h=RECOVERY_H) -> float:
    """Banked logs per hour (renewal-reward: E[banked] / E[cycle time], trip_terms)
    net of the supplies one trip uses (cost_logs: recall charges, reagents) and the
    gear a death loses (gear_logs × P(death)), all in logs at the board price."""
    tt = trip_terms(q, lam, t_h, hz, recovery_h)
    return (tt["banked"] - cost_logs - gear_logs * tt["p_death"]) / tt["time_h"]


def best_q(lam, t_h, hz, gear_logs, q_cap=Q_MAX, cost_logs=0.0, refine=True) -> int:
    """Q* in Q_MIN…min(Q_MAX, q_cap): the best of Q_GRID (log-spaced, ~10 % apart)
    and the cap, then (refine) a golden-section search between its neighbours,
    rounded to 10 logs. Without refine (the Thompson draws) every other grid
    point (~20 % apart: the rate is flat near Q*). Below Q_MIN only when we
    can't carry more (q_cap)."""
    hi = min(Q_MAX, q_cap)
    if hi <= Q_MIN:
        return max(1, int(hi))
    grid = [q for q in (Q_GRID if refine else Q_GRID[::2]) if q < hi] + [int(hi)]
    vals = [net_rate(q, lam, t_h, hz, gear_logs, cost_logs) for q in grid]
    i = max(range(len(grid)), key=vals.__getitem__)
    if not refine or i == len(grid) - 1:
        return grid[i]
    a, b = float(grid[max(0, i - 1)]), float(grid[i + 1])
    g = (math.sqrt(5) - 1) / 2
    for _ in range(30):
        if b - a < 10:
            break
        c, d = b - g * (b - a), a + g * (b - a)
        if net_rate(c, lam, t_h, hz, gear_logs, cost_logs) >= net_rate(d, lam, t_h, hz, gear_logs, cost_logs):
            b = d
        else:
            a = c
    q = int(round((a + b) / 20.0)) * 10
    return max(Q_MIN, min(int(hi), q))


def rate_prior(by_spot: dict, p_now, now) -> tuple:
    """(alpha0, beta0) of a Gamma prior on a spot's field rate: mean and spread
    of the measured spots' rates (each with ≥ PRIOR_MIN_FIELD_H weighted field
    hours), the spread at least PRIOR_CV_MIN of the mean; DEFAULT_RATE before
    any spot is measured."""
    means = []
    for trips in by_spot.values():
        hrs = sum(weight(tr["t1"], now) * adjusted_field_h(tr, p_now) for tr in trips)
        if hrs >= PRIOR_MIN_FIELD_H:
            means.append(sum(weight(tr["t1"], now) * tr["logs"] for tr in trips) / hrs)
    mu = sum(means) / len(means) if means else DEFAULT_RATE
    var = sum((m - mu) ** 2 for m in means) / (len(means) - 1) if len(means) >= 2 else 0.0
    tau2 = max(var, (PRIOR_CV_MIN * mu) ** 2)
    return mu * mu / tau2, mu / tau2


def spot_model(spot, trips, hev, pooled, prior, phi, p_now, now) -> dict:
    """Posterior parameters of one spot from its trips (trip_obs dicts), its
    hazard evidence hev (sightings {trip t0: n}, deaths [t], thefts [t], trips
    sent home {t0}), the pooled hazard rates (plan) and the rate prior
    (rate_prior). Hazards are Gamma posteriors over recency-weighted field
    hours, each shrunk to its pooled rate: h_D's prior mean = sightings per
    field hour × P(death | sighting) + the creature-death rate, worth
    DEATH_SHRINK_H field hours; h_S's = the pooled rate, HOME_SHRINK_H; h_T's =
    the pooled rate, THEFT_SHRINK_H."""
    w = [weight(tr["t1"], now) for tr in trips]
    logs = sum(wi * tr["logs"] for wi, tr in zip(w, trips))
    hrs = sum(wi * adjusted_field_h(tr, p_now) for wi, tr in zip(w, trips))
    exposure = sum(wi * tr["field_s"] / 3600.0 for wi, tr in zip(w, trips))
    alpha = prior[0] + logs / phi
    beta = prior[1] + hrs / phi
    t0 = overhead_prior_s(spot)
    obs = [(wi, tr["overhead_s"]) for wi, tr in zip(w, trips) if tr["overhead_s"] is not None]
    n = 1.0 + sum(wi for wi, _ in obs)
    mean = (t0 + sum(wi * o for wi, o in obs)) / n
    var = sum(wi * (o - mean) ** 2 for wi, o in obs) / n if obs else (0.5 * t0) ** 2
    sd = max(OVERHEAD_SD_MIN_S, math.sqrt(var)) / math.sqrt(n)
    pvp = bool(spot.get("pvp", True))
    sightings = hev["sight"]
    seen = sum(wi * sightings.get(tr["t0"], 0) for wi, tr in zip(w, trips))
    hp = _num(spot.get("hazard_prior"), 0.5) if pvp else 0.0
    sight_a, sight_b = hp * HAZARD_PRIOR_H + seen, HAZARD_PRIOR_H + exposure
    home = sum(wi for wi, tr in zip(w, trips) if tr["t0"] in hev["home"])
    died = sum(weight(t, now) for t in hev["deaths"])
    stolen = sum(weight(t, now) for t in hev["thefts"])
    chop = sum(tr["chop_s"] for tr in trips if tr["field_s"] > 0)
    field = sum(tr["field_s"] for tr in trips if tr["field_s"] > 0)
    sup = [(wi, tr["supply_gp"]) for wi, tr in zip(w, trips) if tr.get("supply_gp") is not None]
    m = {"alpha": alpha, "beta": beta, "rate": alpha / beta,
         "overhead_s": mean, "overhead_sd_s": sd,
         "pvp": pvp, "sight_a": sight_a, "sight_b": sight_b,
         "died_w": died, "exposure": exposure,
         "home_a": HOME_SHRINK_H * pooled["home"] + home, "home_b": HOME_SHRINK_H + exposure,
         "theft_a": THEFT_SHRINK_H * pooled["theft"] + stolen, "theft_b": THEFT_SHRINK_H + exposure,
         "chop_share": chop / field if field > 600 else DEFAULT_CHOP_SHARE,
         "supply_gp": sum(wi * g for wi, g in sup) / sum(wi for wi, _ in sup) if sup else 0.0,
         "supply_unpriced": sum(tr.get("supply_unpriced", 0) for tr in trips),
         "trips": len(trips), "field_h": round(sum(tr["field_s"] for tr in trips) / 3600.0, 2),
         "logs": sum(tr["logs"] for tr in trips), "weight": round(sum(w), 2),
         "sightings": sum(sightings.get(tr["t0"], 0) for tr in trips),
         "sent_home": sum(1 for tr in trips if tr["t0"] in hev["home"]), "thefts": len(hev["thefts"]),
         "place_fails": sum(1 for tr in trips if tr["place_fail"])}
    m["hz"] = hazards_mean(m, pooled)
    return m


def _death_rate(m, sight, p_pk, creature):
    """(shape, rate) of a spot's death-rate Gamma for a sighting rate and P(death | sighting)."""
    m0 = (sight * p_pk if m["pvp"] else 0.0) + creature
    return DEATH_SHRINK_H * m0 + m["died_w"], DEATH_SHRINK_H + m["exposure"]


def hazards_mean(m, pooled) -> tuple:
    """(h_D, h_S, h_T, f): the posterior means."""
    a, b = _death_rate(m, m["sight_a"] / m["sight_b"], pooled["p_pk"], pooled["creature"])
    return a / b, m["home_a"] / m["home_b"], m["theft_a"] / m["theft_b"], pooled["f"]


def _gamma(rng, a, b):
    return rng.gammavariate(a, 1.0 / b) if a > 0 else 0.0


def _draw(m, pooled, rng):
    """One posterior draw (lam, t_h, hz) of a spot."""
    lam = rng.gammavariate(m["alpha"], 1.0 / m["beta"])
    t_h = max(10.0, rng.gauss(m["overhead_s"], m["overhead_sd_s"])) / 3600.0
    sight = _gamma(rng, m["sight_a"], m["sight_b"]) if m["pvp"] else 0.0
    hd = _gamma(rng, *_death_rate(m, sight, rng.betavariate(*pooled["death"]), pooled["creature"]))
    return lam, t_h, (hd, _gamma(rng, m["home_a"], m["home_b"]), _gamma(rng, m["theft_a"], m["theft_b"]),
                      pooled["f"])


def _value(lam, t_h, hz, gear_logs, stint_h, travel_h, cost_logs=0.0, q_cap=Q_MAX, refine=False, cap_logs=None):
    """(net logs/hour of a run there, Q*, capacity-bound): the travel to the spot
    is paid out of the run, which lasts a stint or one trip when that's longer.
    cap_logs (what the spot's trees hold, capacity_logs) caps Q*. When it binds,
    the trip ends dry and the spot is out until its trees regrow, so the run is
    that one trip: its overhead and the travel buy only those logs."""
    q = best_q(lam, t_h, hz, gear_logs, q_cap if cap_logs is None else min(q_cap, cap_logs), cost_logs,
               refine=refine)
    tt = trip_terms(q, lam, t_h, hz)
    v = (tt["banked"] - cost_logs - gear_logs * tt["p_death"]) / tt["time_h"]
    bound = cap_logs is not None and cap_logs < min(q_cap, Q_MAX) and q >= int(cap_logs)
    run_h = tt["time_h"] if bound else max(stint_h, tt["time_h"])
    return v * run_h / (run_h + travel_h), q, bound


def eligibility(spot, trips, threats, now, young, regrow_min) -> str | None:
    """Why the spot can't be picked now, or None."""
    if spot.get("status") != "active":
        return f"status {spot.get('status')}" + (f": {spot['reason']}" if spot.get("reason") else "")
    if spot.get("requires_young") and not young:
        return "Young characters only"
    try:
        check_spot(spot)
    except ValueError as e:
        return str(e)
    recent = [t for t in threats if now - t < COOLDOWN_S]
    if recent:
        return f"player threat or death here {int((now - max(recent)) / 60)} min ago (cooldown {int(COOLDOWN_S / 60)} min)"
    dry = [tr["t1"] for tr in trips if tr["dry"]]
    last = sorted(trips, key=lambda tr: tr["t0"])[-UNWORKABLE_TRIPS:]
    if len(last) == UNWORKABLE_TRIPS and all(tr["place_fail"] for tr in last) \
            and now - last[-1]["t1"] < UNWORKABLE_DAYS * 86400:
        return (f"unworkable: the last {UNWORKABLE_TRIPS} trips got nothing ({last[-1]['why']}); "
                f"another try after {UNWORKABLE_DAYS:g} days")
    if dry and now - max(dry) < regrow_min * 60:
        return f"ran dry {int((now - max(dry)) / 60)} min ago (trees regrow in ~{int(regrow_min)} min)"
    return None


def learned_travel(trips: list, spots: dict) -> dict:
    """{spot id: [minutes]}: the gap between the last trip at one spot and the
    first at another, when shorter than TRAVEL_GAP_S (moving between them; a
    longer gap was a break or a session end)."""
    out = {}
    seq = sorted(trips, key=lambda tr: tr["t0"])
    for a, b in zip(seq, seq[1:]):
        if a["spot"] != b["spot"] and b["spot"] in spots and 0 < b["t0"] - a["t1"] < TRAVEL_GAP_S:
            out.setdefault(b["spot"], []).append((b["t0"] - a["t1"]) / 60.0)
    return out


def travel_h(spot: dict, here: str | None, learned: dict) -> float:
    """Hours to get to `spot` from `here` (current_spot): 0 there or at its
    library hub; else its travel_min prior averaged with the learned moves
    (learned_travel), one pseudo-observation for the prior."""
    if spot["id"] == here or (here is not None and hub_of(spot) == here):
        return 0.0
    samples = learned.get(spot["id"], [])
    prior = _num(spot.get("travel_min"), 10)
    return (prior + sum(samples)) / (1 + len(samples)) / 60.0


def hatchet_price(material: str, quality, table: dict, prices: dict):
    """What replacing a hatchet costs: the prices table's `hatchet:<material>[:<quality>]`,
    else the NPC price of a plain one (hatchets.json), else None (unknown)."""
    key = f"hatchet:{material}" + (f":{quality}" if quality else "")
    if key in prices:
        return prices[key]["price_gp"]
    mat = next((m for m in table["materials"] if m["name"] == material), {})
    return mat.get("npc_price_gp") if quality is None else None


def hatchet_options(char: dict, table: dict, prices: dict) -> list:
    """Hatchets we own (worn or packed) plus the ones we could buy at a known
    price; price_gp = what losing or replacing it costs (None: unknown)."""
    out, seen = [], set()
    for h in char.get("hatchets") or []:
        key = (h["material"], h["quality"])
        if key in seen:
            continue
        seen.add(key)
        out.append({**{k: h[k] for k in ("material", "quality", "tool_bonus", "uses", "newbied")},
                    "owned": True, "worn": h["worn"], "price_gp": hatchet_price(*key, table, prices)})
    for m in table["materials"]:
        for q in [None] + [x["name"] for x in table["qualities"]]:
            if (m["name"], q) in seen:
                continue
            p = hatchet_price(m["name"], q, table, prices)
            if p is None:
                continue
            qd = next((x for x in table["qualities"] if x["name"] == q), {"tool_bonus": 0.0, "extra_uses": 0})
            out.append({"material": m["name"], "quality": q, "tool_bonus": round(m["tool_bonus"] + qd["tool_bonus"], 3),
                        "uses": table["base_uses"] + m["extra_uses"] + qd["extra_uses"], "newbied": False,
                        "owned": False, "worn": False, "price_gp": p})
    return out


def gear_at_risk(char: dict, table: dict, prices: dict, young: bool) -> dict:
    """What a death loses besides the logs: every unblessed item we carry at its
    full replacement price: each hatchet worn or packed (a newbied/blessed one,
    by its name, isn't lost) and the reagents in the pack (`reagent:<name>`).
    Blessed items (the rune tome) stay. A Young character loses nothing.
    Unpriced items count 0 and are listed in `unpriced`."""
    out = {"gp": 0.0, "young": bool(young), "items": [], "unpriced": []}
    if young:
        return out
    for h in char.get("hatchets") or []:
        if h.get("newbied"):
            continue
        name = f"hatchet:{h['material']}" + (f":{h['quality']}" if h.get("quality") else "")
        p = hatchet_price(h["material"], h.get("quality"), table, prices)
        if p is None:
            out["unpriced"].append(name)
        else:
            out["gp"] += p
            out["items"].append({"item": name, "n": 1, "gp": p})
    for reg, n in sorted((char.get("reagents") or {}).items()):
        key = f"reagent:{reg.replace(' ', '_')}"
        if key in prices:
            out["gp"] += n * prices[key]["price_gp"]
            out["items"].append({"item": key, "n": n, "gp": round(n * prices[key]["price_gp"], 1)})
        elif n:
            out["unpriced"].append(key)
    out["gp"] = round(out["gp"], 1)
    return out


def hatchet_value(opt, m, gear_gp, skill, p_ref, logs_per_success, gp_per_log, stint_h, q_cap=Q_MAX,
                  price=None) -> float:
    """Net logs/hour (wear in logs at gp_per_log) with hatchet opt at spot model m
    (posterior means). A death loses gear_gp (gear_at_risk: every hatchet we
    carry, whichever is used) plus, for one we'd buy, its price. price
    overrides opt's (break-even)."""
    price = opt["price_gp"] if price is None else price
    p = success_p(skill, opt["tool_bonus"])
    lam = m["rate"]
    if p and p_ref:
        c = m["chop_share"]
        lam = 1.0 / (c / lam * p_ref / p + (1.0 - c) / lam)
    gear = gear_gp + (0.0 if opt["owned"] or opt["newbied"] else (price or 0.0))
    v, _, _ = _value(lam, m["overhead_s"] / 3600.0, m["hz"], gear / gp_per_log, stint_h, 0.0,
                     m.get("supply_gp", 0.0) / gp_per_log, q_cap, refine=True, cap_logs=m.get("cap_logs"))
    wear = v / logs_per_success * (price or 0.0) / opt["uses"] / gp_per_log
    return v - wear


def breakeven_price(opt, m, gear_gp, skill, p_ref, lps, gpl, stint_h, q_cap, target) -> float | None:
    """The highest price at which opt still nets `target` logs/hour (None: it
    doesn't even at price 0)."""
    def f(price):
        return hatchet_value(opt, m, gear_gp, skill, p_ref, lps, gpl, stint_h, q_cap, price=price) - target
    if f(0.0) <= 0:
        return None
    lo, hi = 0.0, 1.0
    while f(hi) > 0 and hi < 1e8:
        hi *= 4
    for _ in range(50):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if f(mid) > 0 else (lo, mid)
    return round(lo)


def plan(spots: dict, episodes: list, sightings: list, deaths: list, regrow: dict, char: dict | None,
         table: dict, prices: dict, now: float, rng: random.Random, young: bool = False,
         stint_min: float = STINT_MIN, here: str | None = None, logs_per_success: float = LOGS_PER_SUCCESS,
         gp_per_log: float = 9.5, draws: int = DRAWS, events: list = (), trees: dict | None = None) -> dict:
    """The pure planner. episodes: lumber trip rows; sightings: [t] of pk_seen
    job events; deaths: [{t, x, y}] (world `death` events); regrow: regrowth();
    char: character() or None; here: the spot we stand at (current_spot);
    prices: Memory.prices() (hatchets, reagents, the board price, supplies:
    supply_gp); events: lumber job events and theft_suspected junctures
    ({t, kind, data}: death causes, recall/guard_flight escapes, thefts);
    trees: tree_yield() (each spot's trees, what they give, which are out now);
    a spot without an entry has no capacity limit."""
    char = char or {}
    young = bool(young or char.get("young"))
    trips = [tr for tr in (trip_obs(e, prices) for e in episodes) if tr is not None and tr["spot"] in spots]
    by_spot = {sid: [tr for tr in trips if tr["spot"] == sid] for sid in spots}
    hatchets = char.get("hatchets") or []
    worn = hatchets[0] if hatchets else None
    skill = char.get("skill")
    if skill is None:
        known = [e for e in episodes if _num(e.get("skill")) is not None]
        skill = known[-1]["skill"] if known else None
    p_now = success_p(skill, worn["tool_bonus"] if worn else 0.0)

    # hazard evidence: sightings, deaths (and their cause), trips sent home, thefts
    hev = {sid: {"sight": {}, "home": set(), "deaths": [], "thefts": []} for sid in spots}
    sight_by_trip = {}
    for t in sightings:
        tr = next((tr for tr in trips if tr["t0"] <= t <= tr["t1"]), None)
        if tr is not None:
            sight_by_trip[tr["t0"]] = sight_by_trip.get(tr["t0"], 0) + 1
            hev[tr["spot"]]["sight"][tr["t0"]] = sight_by_trip[tr["t0"]]
    linked = attribute_deaths(trips, deaths, spots)
    died_trips = {tr["t0"] for _, _, tr in linked if tr is not None}
    escapes = [e["t"] for e in events if e["kind"] in ("recall", "guard_flight")]
    for tr in trips:
        if tr["t0"] not in died_trips and (tr["sent_home"] or any(tr["t0"] <= t <= tr["t1"] + 5 for t in escapes)):
            hev[tr["spot"]]["home"].add(tr["t0"])
    threats = {sid: [] for sid in spots}
    pk_w = creature_w = 0.0
    for d, sid, _ in linked:
        threats[sid].append(d["t"])
        hev[sid]["deaths"].append(d["t"])
        if death_cause(d, sightings, events) == "pk":
            pk_w += weight(d["t"], now)
        else:
            creature_w += weight(d["t"], now)
    for tr in trips:
        if tr["outcome"] == "aborted" and sight_by_trip.get(tr["t0"]):
            threats[tr["spot"]].append(tr["t1"])
    thefts = [(t, tr, share) for t, tr, share in theft_obs(events, trips) if tr is not None]
    for t, tr, _ in thefts:
        hev[tr["spot"]]["thefts"].append(t)
    seen_w = sum(weight(tr["t1"], now) * sight_by_trip.get(tr["t0"], 0) for tr in trips)
    expo = sum(weight(tr["t1"], now) * tr["field_s"] / 3600.0 for tr in trips)
    home_w = sum(weight(tr["t1"], now) for tr in trips if tr["t0"] in hev[tr["spot"]]["home"])
    theft_w = sum(weight(t, now) for t, _, _ in thefts)
    death = (DEATH_PRIOR[0] + pk_w, DEATH_PRIOR[1] + max(0.0, seen_w - pk_w))
    pooled = {"death": death, "p_pk": death[0] / (death[0] + death[1]),
              "creature": (CREATURE_DEATH_PRIOR[0] * CREATURE_DEATH_PRIOR[1] + creature_w)
              / (CREATURE_DEATH_PRIOR[1] + expo),
              "home": (HOME_PRIOR[0] * HOME_PRIOR[1] + home_w) / (HOME_PRIOR[1] + expo),
              "theft": (THEFT_PRIOR[0] * THEFT_PRIOR[1] + theft_w) / (THEFT_PRIOR[1] + expo),
              "f": (THEFT_FRACTION_PRIOR[0] + sum(s for _, _, s in thefts))
              / (sum(THEFT_FRACTION_PRIOR) + len(thefts))}

    prior = rate_prior(by_spot, p_now, now)
    phi = dispersion(by_spot, p_now)
    stint_h = stint_min / 60.0
    gear_char, gear_src = char, "proxy"
    if not hatchets:
        last = next((tr["hatchet"] for tr in sorted(trips, key=lambda tr: -tr["t0"])
                     if (tr["hatchet"] or {}).get("material")), None)
        gear_char, gear_src = ({"hatchets": [last]}, "last trip row") if last else ({}, None)
    gear = {**gear_at_risk(gear_char, table, prices, young), "source": gear_src}
    gear_logs = gear["gp"] / gp_per_log
    q_cap = Q_MAX
    if _num(char.get("weight_max")) and _num(char.get("weight")) is not None:
        q_cap = max(0, int((char["weight_max"] - char["weight"]) / LOG_WEIGHT))
    trees = trees or {"logs_per_tree": LOGS_PER_TREE, "spots": {}}
    lpt = trees["logs_per_tree"]
    infos = list(trees["spots"].values())
    share0 = (sum(i["yielded"] for i in infos) + 1.0) / (sum(i["tried"] for i in infos) + 2.0)

    learned = learned_travel(trips, spots)
    models, rows = {}, []
    for sid, s in spots.items():
        m = spot_model(s, by_spot[sid], hev[sid], pooled, prior, phi, p_now, now)
        why = eligibility(s, by_spot[sid], threats[sid], now, young, regrow["minutes"])
        trav = travel_h(s, here, learned)
        m["cost_logs"] = m["supply_gp"] / gp_per_log
        t_h = m["overhead_s"] / 3600.0
        m["cap"] = capacity_model(trees["spots"].get(sid), share0)
        m["cap_logs"] = capacity_logs(m["cap"], lpt)
        v, q, bound = _value(m["rate"], t_h, m["hz"], gear_logs, stint_h, trav, m["cost_logs"], q_cap, refine=True,
                             cap_logs=m["cap_logs"])
        tt = trip_terms(q, m["rate"], t_h, m["hz"])
        lo = gamma_quantile(m["alpha"], m["beta"], 0.1)
        hi = gamma_quantile(m["alpha"], m["beta"], 0.9)
        m.update(travel_h=trav, value=v, q=q, bound=bound, why=why, terms=tt)
        models[sid] = m
        hd, hs, ht, _ = m["hz"]
        last = max((tr["t1"] for tr in by_spot[sid]), default=None)
        rows.append({"id": sid, "name": s.get("name"), "status": s.get("status"), "eligible": why is None,
                     "why_not": why, "trips": m["trips"], "field_h": m["field_h"], "logs": m["logs"],
                     "rate_logs_h": round(m["rate"]), "rate_80": [round(lo), round(hi)],
                     "overhead_s": round(m["overhead_s"]), "sightings": m["sightings"],
                     "sightings_per_h": round(m["sight_a"] / m["sight_b"], 2),
                     "deaths": len(hev[sid]["deaths"]), "deaths_per_h": round(hd, 3),
                     "sent_home": m["sent_home"], "sent_home_per_h": round(hs, 2),
                     "thefts": m["thefts"], "thefts_per_h": round(ht, 3),
                     "p_death_trip": round(tt["p_death"], 3),
                     "loss_logs_trip": round(tt["lost_death"] + tt["lost_theft"] + gear_logs * tt["p_death"], 1),
                     "logs_per_trip": q, "net_logs_h": round(v),
                     "trees": None if m["cap"] is None else m["cap"]["trees"],
                     "trees_out": None if m["cap"] is None else m["cap"]["trees"] - m["cap"]["avail"],
                     "yield_share": None if m["cap"] is None else round(m["cap"]["a"] / (m["cap"]["a"] + m["cap"]["b"]), 2),
                     "grove_logs": None if m["cap_logs"] is None else round(m["cap_logs"]),
                     "grove_bound": bound,
                     "supply_gp_trip": round(m["supply_gp"], 1), "supply_unpriced": m["supply_unpriced"],
                     "place_fails": m["place_fails"],
                     "here": sid == here or (here is not None and hub_of(s) == here),
                     "travel_min": round(trav * 60), "travel_samples": len(learned.get(sid, [])),
                     "access": (s.get("access") or {}).get("method", "walk"),
                     "rune": (s.get("access") or {}).get("rune"),
                     "last_trip_h_ago": None if last is None else round((now - last) / 3600.0, 1)})

    eligible = [sid for sid in spots if models[sid]["why"] is None]
    out = {"ok": True, "now": now, "skill": skill, "success_p": None if p_now is None else round(p_now, 3),
           "here": here, "regrow": regrow, "dispersion": round(phi, 1), "young": young,
           "death_given_sighting": round(pooled["p_pk"], 3),
           "creature_deaths_per_h": round(pooled["creature"], 4),
           "sent_home_pooled_per_h": round(pooled["home"], 3), "thefts_pooled_per_h": round(pooled["theft"], 4),
           "theft_fraction": round(pooled["f"], 2), "theft_events": len(thefts),
           "gear_at_risk": gear, "capacity_logs": None if q_cap == Q_MAX else q_cap,
           "logs_per_tree": round(lpt, 1), "yield_share_pooled": round(share0, 2),
           "prior_rate_logs_h": round(prior[0] / prior[1]), "prior_cv": round(1 / math.sqrt(prior[0]), 2),
           "spots": sorted(rows, key=lambda r: (not r["eligible"], -r["net_logs_h"])), "pick": None}
    if not eligible:
        out["ok"] = False
        out["error"] = "no eligible lumber spot (see spots[].why_not)"
        return out

    def value(sid):
        mm = models[sid]
        return _value(*_draw(mm, pooled, rng), gear_logs, stint_h, mm["travel_h"], mm["cost_logs"], q_cap,
                      cap_logs=capacity_logs(mm["cap"], lpt, rng))[0]

    wins = {sid: 0 for sid in eligible}
    for _ in range(draws):
        vals = {sid: value(sid) for sid in eligible}
        wins[max(vals, key=vals.get)] += 1
    for r in out["spots"]:
        r["p_best"] = round(wins.get(r["id"], 0) / draws, 3) if r["eligible"] else 0.0
    sample = {sid: value(sid) for sid in eligible}
    pick = max(sample, key=sample.get)
    greedy = max(eligible, key=lambda sid: models[sid]["value"])
    m = models[pick]
    q = m["q"]
    trip_s = (q / m["rate"] + m["overhead_s"] / 3600.0) * 3600.0     # a full trip, nothing ending it early
    n = max(1, round(stint_min * 60.0 / trip_s))
    run_q = q
    if m["bound"]:
        # the grove holds less than Q*: one trip, told the uncapped Q* so it chops until
        # the trees run out rather than stopping at our estimate of what they hold
        n = 1
        run_q = best_q(m["rate"], m["overhead_s"] / 3600.0, m["hz"], gear_logs, q_cap, m["cost_logs"])
    timeout = int(max(1800, 2 * n * (run_q / m["rate"] * 3600.0 + m["overhead_s"]) + 600))

    hat = hatchet_choice(char, table, prices, m, gear["gp"], skill, p_now, logs_per_success, gp_per_log,
                         stint_h, q_cap)
    args = ["--spot", pick, "--trips", str(n), "--logs-per-trip", str(run_q),
            "--regrow-min", f"{regrow['minutes']:g}", "--timeout", str(timeout)]
    if hat.get("use"):
        args += ["--hatchet", hat["use"]]
    tt = m["terms"]
    out["pick"] = {"spot": pick, "mode": "exploit" if pick == greedy else "explore",
                   "greedy": greedy, "p_best": round(wins[pick] / draws, 3),
                   "travel": None if models[pick]["travel_h"] == 0 else spots[pick].get("travel"),
                   "logs_per_trip": q, "trips": n, "timeout_s": timeout,
                   "grove_logs": None if m["cap_logs"] is None else round(m["cap_logs"]),
                   "grove_bound": m["bound"],
                   "expected_trip_min": round(trip_s / 60.0, 1), "expected_net_logs_h": round(m["value"]),
                   "expected_banked_trip": round(tt["banked"]), "p_death_trip": round(tt["p_death"], 3),
                   "p_sent_home_trip": round(tt["p_home"], 3),
                   "args": args, "command": "ctl run lumber " + " ".join(args)}
    out["hatchets"] = hat
    return out


def hatchet_choice(char, table, prices, m, gear_gp, skill, p_now, lps, gpl, stint_h, q_cap=Q_MAX) -> dict:
    """Rank hatchet options at spot model m; `use` = the material[+quality] to
    pass the runner when an owned one wins and isn't what it would pick anyway
    (the worn one), `buy` = a better one we don't own at a known price. Every
    hatchet we carry is lost on death whichever we use (gear_gp), so owned ones
    differ by tool bonus and wear; one we'd buy also adds its price to the loss."""
    opts = hatchet_options(char, table, prices)
    if not opts or skill is None:
        return {"options": [], "use": None, "buy": None,
                "note": "no skill or hatchet known (proxy down?)" if skill is None else "no hatchet known"}
    rows = []
    for o in opts:
        known = o["price_gp"] is not None
        v = hatchet_value(o, m, gear_gp, skill, p_now, lps, gpl, stint_h, q_cap)
        rows.append({**o, "net_logs_h": round(v, 1), "priced": known})
    priced = [r for r in rows if r["priced"]]
    best = max(priced, key=lambda r: r["net_logs_h"]) if priced else None
    target = best["net_logs_h"] if best else 0.0
    for r in rows:
        r["breakeven_gp"] = None if r is best else breakeven_price(r, m, gear_gp, skill, p_now, lps, gpl,
                                                                    stint_h, q_cap, target)
    spec = None
    if best and best["owned"] and not best["worn"]:
        spec = best["material"] + (f"+{best['quality']}" if best["quality"] else "")
    buy = best if best and not best["owned"] else None
    return {"options": sorted(rows, key=lambda r: -r["net_logs_h"]), "use": spec,
            "buy": None if buy is None else {"material": buy["material"], "quality": buy["quality"],
                                             "price_gp": buy["price_gp"]},
            "gp_per_log": gpl, "logs_per_success": round(lps, 2)}


def gamma_quantile(alpha, beta, q) -> float:
    """Gamma(alpha, rate beta) quantile (Wilson-Hilferty)."""
    if alpha <= 0:
        return 0.0
    z = {0.1: -1.2816, 0.9: 1.2816}[q]
    x = alpha * (1 - 1 / (9 * alpha) + z * math.sqrt(1 / (9 * alpha))) ** 3
    return max(0.0, x / beta)


# ------------------------------------------------------------------ the store
def logs_per_success(memory, n: int = 300) -> float:
    rows = memory.con.execute("SELECT amount FROM harvest_attempts WHERE outcome='success' AND amount > 0 "
                              "ORDER BY t DESC LIMIT ?", (n,)).fetchall()
    return sum(r[0] for r in rows) / len(rows) if len(rows) >= 20 else LOGS_PER_SUCCESS


def gp_per_log(prices: dict) -> float:
    if "board:ordinary" in prices:
        return prices["board:ordinary"]["price_gp"]
    woods = load_json(WOODS_FILE)["woods"]
    return next(w["value_gp"] for w in woods if w["name"] == "ordinary")


def store_inputs(memory) -> dict:
    """Everything `plan` reads from the memory store."""
    deaths = []
    for t, data in memory.con.execute("SELECT t, data FROM events WHERE ev='death' ORDER BY t"):
        d = json.loads(data)
        deaths.append({"t": t, "x": d.get("x"), "y": d.get("y")})
    attempts = memory.con.execute("SELECT t, facet, x, y, z, outcome, amount FROM harvest_attempts "
                                  "ORDER BY t").fetchall()
    events = memory.job_events("lumber")
    events += [{"t": t, "kind": "theft_suspected", "data": json.loads(data) if data else {}}
               for t, data in memory.con.execute("SELECT t, data FROM junctures WHERE kind='theft_suspected' "
                                                 "AND source='lumber' ORDER BY t")]
    # a tracked red beyond the react range, or one already counted this run, isn't a sighting
    # (loop_lumber.track_sighting `counted`; LUMBER_LOOP.md §13 "Tracking reds")
    return {"episodes": memory.episodes("lumber"),
            "sightings": [e["t"] for e in events if e["kind"] == "pk_seen" and e["data"].get("counted", True)],
            "deaths": deaths, "regrow": regrowth(attempts), "attempts": attempts, "prices": memory.prices(),
            "logs_per_success": logs_per_success(memory),
            "events": [e for e in events if e["kind"] in ("death", "recall", "guard_flight", "theft",
                                                          "theft_suspected")]}

_MAPS, _TILES = {}, {}


def spot_tree_tiles(spots: dict) -> dict:
    """{spot id: (facet, {(x, y)})}: the tree tiles the runner tries at each spot,
    its seed trees plus the map's tree statics in its area square (as
    loop_lumber.candidate_trees). Spots on a facet whose map files can't be read
    are left out (no capacity limit). Map tiles are cached per area."""
    import uomap
    out = {}
    for sid, s in spots.items():
        area = s.get("area")
        if not area:
            continue
        facet = int(s.get("facet") or 0)
        (cx, cy), r = area["center"], area["radius"]
        key = (facet, cx, cy, r)
        if key not in _TILES:
            if facet not in _MAPS:
                try:
                    _MAPS[facet] = uomap.UoMap(facet)
                except (OSError, ValueError):
                    _MAPS[facet] = None
            um = _MAPS[facet]
            _TILES[key] = None if um is None else frozenset(
                (x, y) for x, y, _z, _g in um.find_trees(cx - r, cy - r, cx + r, cy + r))
        if _TILES[key] is not None:
            out[sid] = (facet, _TILES[key] | {(t["x"], t["y"]) for t in s.get("trees") or []})
    return out



def plan_from_store(memory, world: dict | None, self_serial, pos, facet, young=False,
                    stint_min=STINT_MIN, seed=None, seeds_path=SEEDS, now=None) -> dict:
    spots = load_spots(memory, seeds_path)
    table = load_hatchets()
    inp = store_inputs(memory)
    char = character(world, self_serial, table) if world is not None and self_serial is not None else None
    out = plan(spots, inp["episodes"], inp["sightings"], inp["deaths"], inp["regrow"], char, table,
               inp["prices"], time.time() if now is None else now, random.Random(seed), young=young,
               stint_min=stint_min, here=current_spot(spots, pos, facet),
               logs_per_success=inp["logs_per_success"], gp_per_log=gp_per_log(inp["prices"]),
               events=inp["events"],
               trees=tree_yield(inp["attempts"], spot_tree_tiles(spots), time.time() if now is None else now,
                                inp["regrow"]["minutes"]))
    out["character"] = None if char is None else {k: char[k] for k in ("name", "serial", "skill", "mounted", "buffs",
                                                                       "weight", "weight_max", "young")}
    return out


# ------------------------------------------------------------------ discovering spots
SLUG = re.compile(r"[^a-z0-9]+")
YOUNG_TOWNS = ("Shelter Island",)  # no hostile player actions; bank and harvesting for Young only (wiki)
TOWN_RADIUS = 50                   # tiles from a township marker: likely inside the town region (no harvesting)
RUNE_SEARCH = 200                  # tiles from a Witcher rune a grove's window centre may lie (user 2026-10-03)
MAX_RUNE_ROUTE = 300               # tiles: the longest walk from a rune's landing into its grove
WINDOW_GRID = 7                    # window centres lie on this global lattice (one grove, one score)
ROUTE_TRIES = 3                    # windows per rune whose route check may fail before the rune is given up


def floor_z(walk, x, y) -> int:
    """Where a banker at a bank marker stands: the lowest floor tile at or above
    the land, else the land. Markers carry no z; bank floors sit at or above the
    land (Shelter 20 on land 0, Terran 35 on 35, Horseshoe Bay 35 on 18), and
    Cambria's has a basement at -20 that isn't it. The runner's banker check
    allows a storey either way (agent_link.same_floor)."""
    objs = walk.objects(x, y)
    land = next((o[0] for o in objs if o[4][0] == "flat"), objs[0][0] if objs else 0)
    return next((o[0] for o in objs if o[4][0] == "item" and o[0] >= land), land)


def bank_list(path=None) -> list:
    """[(town, (x, y), facet)] of the client's bank markers, lawless towns included."""
    import xml.etree.ElementTree as ET
    import guards
    try:
        root = ET.parse(path or guards.MARKERS).getroot()
    except (OSError, ET.ParseError):
        return []
    out = []
    for mk in root.iter("Marker"):
        if mk.get("Icon") != "BANK":
            continue
        try:
            out.append(((mk.get("Name") or "bank").removesuffix(" Bank"), (int(mk.get("X")), int(mk.get("Y"))),
                        int(mk.get("Facet") or 0)))
        except (TypeError, ValueError):
            continue
    return out


def discover(trees_fn, bank_z_fn, banks, guard_points, spots, ring=(30, 110), radius=14, min_trees=25,
             per_bank=3, route_fn=None) -> list:
    """Candidate spots around banks: square windows (side 2·radius+1) on a
    radius-step grid whose centres lie ring[0]..ring[1] tiles from a bank,
    ranked by tree count, minus windows near a known guard point (town:
    harvesting is blocked there) or overlapping a known spot, at most
    per_bank per bank and none overlapping each other. trees_fn(x0, y0, x1, y1)
    -> [(x, y, z, graphic)] (uomap.UoMap.find_trees); bank_z_fn(x, y) -> the banker's z (floor_z).
    route_fn(start, center, radius) -> route length in tiles or None (make_route_fn):
    windows the bank has no walking route to are dropped."""
    import guards
    taken = [(tuple(s["area"]["center"]), s["area"]["radius"]) for s in spots.values() if s.get("area")]
    out = []
    for town, (bx, by), facet in banks:
        r0, r1 = ring
        trees = trees_fn(bx - r1 - radius, by - r1 - radius, bx + r1 + radius, by + r1 + radius)
        grid = {}
        for x, y, _z, _g in trees:
            grid.setdefault((x // radius, y // radius), []).append((x, y))
        cands = []
        for cx in range(bx - r1, bx + r1 + 1, radius):
            for cy in range(by - r1, by + r1 + 1, radius):
                d = cheb((cx, cy), (bx, by))
                if not r0 <= d <= r1:
                    continue
                if any(cheb((cx, cy), g) <= radius + 4 for g in guard_points):
                    continue
                if any(cheb((cx, cy), c) <= r + radius for c, r in taken):
                    continue
                n = 0
                for gx in range((cx - radius) // radius, (cx + radius) // radius + 1):
                    for gy in range((cy - radius) // radius, (cy + radius) // radius + 1):
                        n += sum(1 for x, y in grid.get((gx, gy), ()) if cheb((x, y), (cx, cy)) <= radius)
                if n >= min_trees:
                    cands.append((n, d, cx, cy))
        cands.sort(key=lambda c: (-c[0], c[1]))
        kept = 0
        for n, d, cx, cy in cands:
            if kept >= per_bank:
                break
            if any(cheb((cx, cy), c) <= r + radius for c, r in taken):
                continue
            route = route_fn((bx, by), (cx, cy), radius) if route_fn is not None else None
            if route_fn is not None and route is None:
                continue
            sid = f"auto_{SLUG.sub('_', town.lower()).strip('_')}_{cx}_{cy}"
            out.append({"id": sid, "name": f"{town}: {n} trees at ({cx},{cy}), {d} tiles from the bank",
                        "facet": facet, "area": {"center": [cx, cy], "radius": radius,
                                                 "note": f"discovered from the map: {n} tree statics"},
                        "trees": [], "pvp": town not in YOUNG_TOWNS, "requires_young": town in YOUNG_TOWNS,
                        "banker": {"serial": "0x00000000", "name": f"{town} bank (marker)",
                                   "pos": [bx, by, bank_z_fn(bx, by)]},
                        "hazard_prior": 0.0 if town in YOUNG_TOWNS else (1.0 if town in guards.LAWLESS_TOWNS else 0.5),
                        "travel": f"{town} moongate, then walk to the bank", "travel_min": 10,
                        "tree_count": n, "bank_distance": d, "route_tiles": route})
            taken.append(((cx, cy), radius))
            kept += 1
    return out


def grove_windows(pts, x, y, search: int, radius: int):
    """Tree counts of every radius-window (side 2·radius+1) centred on the global
    WINDOW_GRID lattice within `search` tiles (Chebyshev) of (x, y), as flat numpy
    arrays (counts, cx, cy). pts: int array of tree (x, y) rows. The lattice is
    global, so runes near one grove score its windows alike. A summed-area table
    over the search box makes each count O(1)."""
    import numpy as np
    x0, y0 = x - search - radius, y - search - radius
    side = 2 * (search + radius) + 1
    m = (pts[:, 0] >= x0) & (pts[:, 0] < x0 + side) & (pts[:, 1] >= y0) & (pts[:, 1] < y0 + side)
    grid = np.zeros((side + 1, side + 1), dtype=np.int32)
    np.add.at(grid, (pts[m, 1] - y0 + 1, pts[m, 0] - x0 + 1), 1)
    sat = grid.cumsum(0).cumsum(1)
    g = WINDOW_GRID
    xs = np.arange(-((search - x) // g) * g, x + search + 1, g)
    ys = np.arange(-((search - y) // g) * g, y + search + 1, g)
    cx, cy = (a.ravel() for a in np.meshgrid(xs, ys))
    lx, hx = cx - radius - x0, cx + radius + 1 - x0
    ly, hy = cy - radius - y0, cy + radius + 1 - y0
    return sat[hy, hx] - sat[ly, hx] - sat[hy, lx] + sat[ly, lx], cx, cy


def _lattice(c, reach: int) -> set:
    """WINDOW_GRID lattice keys whose centre lies within `reach` tiles of c."""
    g = WINDOW_GRID
    return {(kx, ky) for kx in range(-((reach - c[0]) // g), (c[0] + reach) // g + 1)
            for ky in range(-((reach - c[1]) // g), (c[1] + reach) // g + 1)}


def discover_witcher(trees_fn, runes, spots, home_bank, *, library: str = "cambria", radius=14,
                     search=RUNE_SEARCH, min_trees=25, max_route=MAX_RUNE_ROUTE, route_fn=None,
                     include_dangerous=False, towns=(), guard_points=()) -> tuple:
    """Candidate spots reached by Witcher runes (places.witcher()["runes"]), at most
    one per rune. Every window with at least min_trees trees whose centre lies within
    `search` tiles of an eligible rune (grove_windows) is ranked globally, most
    trees first, then nearest to its rune; down that list a window is taken for its
    rune unless the rune already has one, it overlaps a known spot or a window taken
    before, it lies in or by a town or a learned guard point, or the walking route
    from the rune's tile into it (route_fn) is missing or longer than max_route
    (after ROUTE_TRIES such failures the rune is given up). So a grove two runes
    reach goes to the nearer one, and a rune whose best grove is taken gets its
    next best. Runes named after monster places (places.danger_hint: "Brigand
    Camp", "Orc Fort", ...) are left out unless include_dangerous, and so are runes
    that already have a spot (`witcher_<id>`). The way out is the library tome's
    recall, the way home our book's default rune (home_bank: {name, pos, serial} of
    the bank next to it). Returns (candidates, {reason: count} of the runes left out)."""
    import numpy as np
    import places
    skipped = {}

    def skip(why):
        skipped[why] = skipped.get(why, 0) + 1
    eligible = []
    for r in runes:
        if r.get("x") is None:
            skip("no coordinates")
        elif (danger := places.danger_hint(r["name"])) and not include_dangerous:
            skip("monster name")
        elif f"witcher_{r['id']}" in spots:
            skip("already a spot")
        else:
            eligible.append((r, danger))
    if not eligible:
        return [], skipped
    reach = search + radius
    xs, ys = [r["x"] for r, _ in eligible], [r["y"] for r, _ in eligible]
    pts = np.array([(tx, ty) for tx, ty, _z, _g in trees_fn(min(xs) - reach, min(ys) - reach,
                                                            max(xs) + reach, max(ys) + reach)],
                   dtype=np.int64).reshape(-1, 2)
    options = []                                     # (-trees, tiles from the rune, rune index, cx, cy)
    for i, (r, _) in enumerate(eligible):
        n, cx, cy = grove_windows(pts, r["x"], r["y"], search, radius)
        k = n >= min_trees
        d = np.maximum(np.abs(cx[k] - r["x"]), np.abs(cy[k] - r["y"]))
        options.extend(zip((-n[k]).tolist(), d.tolist(), [i] * int(k.sum()), cx[k].tolist(), cy[k].tolist()))
    options.sort()
    taken = set()
    for s in spots.values():
        if s.get("area"):
            taken |= _lattice(s["area"]["center"], s["area"]["radius"] + radius)
    townish = set()
    for t in towns:
        townish |= _lattice(t, TOWN_RADIUS)
    for gp in guard_points:
        townish |= _lattice(gp, radius + 4)
    out, done, failed, hits, has_option = [], set(), {}, {}, set()
    for neg_n, d, i, cx, cy in options:
        has_option.add(i)
        if i in done or failed.get(i, 0) >= ROUTE_TRIES:
            continue
        key = (cx // WINDOW_GRID, cy // WINDOW_GRID)
        if key in taken or key in townish:
            hits.setdefault(i, set()).add("taken" if key in taken else "town")
            continue
        r, danger = eligible[i]
        route = route_fn((r["x"], r["y"]), (cx, cy), radius) if route_fn is not None else None
        if route_fn is not None and (route is None or route > max_route):
            failed[i] = failed.get(i, 0) + 1
            continue
        n = -neg_n
        out.append({"id": f"witcher_{r['id']}", "name": f"Witcher {r['id']}: {r['name']} ({n} trees, {d} tiles off)",
                    "facet": 0, "area": {"center": [cx, cy], "radius": radius,
                                         "note": f"{n} tree statics {d} tiles from Witcher rune {r['id']} "
                                                 f"({r['x']},{r['y']})"},
                    "trees": [], "pvp": True,
                    "access": {"method": "witcher", "rune": r["id"], "library": library},
                    "home": {"method": "recall"},
                    "banker": dict(home_bank), "hazard_prior": 0.5, "danger_hint": danger,
                    "travel": f"the {library} rune library (Witcher rune {r['id']})", "travel_min": 10,
                    "tree_count": n, "rune_distance": d, "route_tiles": route})
        done.add(i)
        taken |= _lattice((cx, cy), 2 * radius)
    for i in range(len(eligible)):
        if i in done:
            continue
        if i not in has_option:
            skip("few trees")
        elif failed.get(i):
            skip("no short route from the rune")
        elif "taken" in hits.get(i, ()):
            skip("its groves are taken (a spot or a nearer/denser candidate)")
        else:
            skip("in or by a town (no harvesting there)")
    return out, skipped


def make_route_fn(walk):
    """route_fn for discover*: the planned walking route (pathfind.plan on the
    map) from a start tile into a window, as its length in tiles, or None. It
    plans with the runner's search budget (pathfind.plan's default), so a route
    found here is one the runner's Mover can plan too."""
    import nav
    import pathfind

    def route(start, center, radius):
        objs = walk.objects(start[0], start[1])
        z = next((o[0] + o[1] for o in objs if o[4][0] in ("flat", "item")), 0) if objs else 0
        path = pathfind.plan(walk, (start[0], start[1], z), nav.within(tuple(center), max(1, radius // 2)))
        return None if path is None else len(path) - 1
    return route

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
  - hazard: hostile-player sightings per field hour (Gamma, prior = the spot's
    hazard_prior) × P(death | sighting) (Beta, pooled over spots). A death
    loses the carried logs (Q/2 on average), the gear at risk and RECOVERY_H.
  - trip size Q*: maximises (Q − deaths·(Q/2 + G)) / (Q/λ + T + deaths·R) over
    Q_GRID (LUMBER_LOOP §6: Q* ≈ λ·sqrt(2T/h) when G = R = 0).
  - choice: Thompson sampling. One posterior draw per eligible spot, the best
    draw wins; a spot other than the one we stand at pays travel_min out of a
    stint. That keeps exploring the uncertain spots while the evidence favours
    the good ones, and varies the routine (ANTICHEAT.md §8.3).
  - hatchet: per owned or buyable hatchet, the net logs/hour at the chosen spot
    minus wear (one use per success) and the expected loss on death, in logs at
    the ordinary board price. Unknown prices give a break-even price instead.
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
SEC_PER_TILE = 0.45               # overhead prior: walking pace incl. detours [INFERENCE: mounted 0.1-0.2 s/step, on foot 0.4]
OVERHEAD_FIXED_S = 40.0           # convert + open the bank + store + human pauses (live 10-20 s + pauses)
OVERHEAD_SD_MIN_S = 30.0
HAZARD_PRIOR_H = 2.0              # field hours of pseudo-data behind a spot's hazard_prior
DEATH_PRIOR = (1.0, 3.0)          # Beta prior of P(death | hostile player sighted) [INFERENCE]
RECOVERY_H = 20 / 60              # ghost walk, resurrection, re-equip, back to work [INFERENCE: Terran death 2026-10-02]
DEATH_KIT_GP = 0.0                # unblessed kit lost on death besides the hatchet: unpriced so far
DEATH_LINK_S = 1800.0             # a death this soon after a lumber trip counts for that trip's spot
COOLDOWN_S = 1800.0               # keep away from a spot this long after a death or a player threat there
STINT_MIN = 60.0                  # minutes one runner start should last
Q_GRID = (25, 50, 75, 100, 150, 200, 300, 400, 500, 750, 1000, 1500, 2000, 3000)
REGROW_P = 0.6                    # revisit a depleted tree once P(regrown) reaches this
REGROW_DEFAULT_MIN = 45.0         # until enough depleted-then-retried trees are seen
REGROW_MIN_PAIRS = 20
DRAWS = 2000                      # Monte Carlo draws for P(best)
CURRENT_SPOT_MARGIN = 40          # tiles beyond a spot's radius that still count as standing at it
NEAR_BANK = 30


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
    k["spot"] = {key: spot.get(key) for key in ("id", "name", "facet", "pvp", "requires_young")}
    k["pvp"] = bool(spot.get("pvp", True))
    k["facet"] = int(spot.get("facet") or 0)
    k.setdefault("npcs", {})["banker"] = dict(spot["banker"])
    k["harvest"]["trees"] = list(spot.get("trees") or [])
    k["harvest"]["area"] = dict(spot["area"])
    return k


def current_spot(spots: dict, pos, facet) -> str | None:
    """The spot we stand at or by: inside its area (+ CURRENT_SPOT_MARGIN) or
    within NEAR_BANK tiles of its bank; the nearest such one."""
    if not pos:
        return None
    best = None
    for sid, s in spots.items():
        if int(s.get("facet") or 0) != int(facet or 0) or not s.get("area"):
            continue
        d_area = cheb(pos, s["area"]["center"])
        d_bank = cheb(pos, s["banker"]["pos"]) if s.get("banker") else 10 ** 6
        if d_area <= s["area"]["radius"] + CURRENT_SPOT_MARGIN or d_bank <= NEAR_BANK:
            d = min(d_area, d_bank)
            if best is None or d < best[0]:
                best = (d, sid)
    return best[1] if best else None


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
    mounted, buff titles, and every hatchet worn or in the backpack (any bag
    depth) with its kind."""
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
    return {"serial": me.get("serial"), "name": me.get("name"), "skill": skill_value(me),
            "mounted": any(it.get("layer") == LAYER_MOUNT and it.get("container") is not None
                           and _serial(it["container"]) == self_serial for it in items.values()),
            "buffs": sorted({b.get("title") or str(b.get("cliloc") or icon) for icon, b in buffs.items()}),
            "hatchets": hatchets}


# ------------------------------------------------------------------ evidence
def trip_obs(ep: dict) -> dict | None:
    """One trip row (episodes, loop lumber) as the model's observation, or None
    without times. Rows written before 2026-10-02 lack walk_out_s/chop_s/skill:
    the walk out is taken to equal the walk to the bank, chops cost
    DEFAULT_CHOP_S each, and their chopping isn't rescaled."""
    t0, t1 = _num(ep.get("t_start")), _num(ep.get("t_end"))
    if t0 is None or t1 is None or t1 < t0:
        return None
    ph = {k: v for k, v in (ep.get("phases_s") or {}).items() if _num(v) is not None}
    outcome = ep.get("outcome") or "banked"
    banked = outcome == "banked" and "to_bank" in ph
    walk_out = _num(ep.get("walk_out_s"))
    if walk_out is None:
        walk_out = ph.get("to_bank", 0.0) if banked else 0.0
    harvest = ph.get("harvest")
    if harvest is None:
        harvest = (t1 - t0) - sum(v for k, v in ph.items() if k != "harvest")
    field_s = max(0.0, harvest - walk_out)
    chop_s = _num(ep.get("chop_s"))
    if chop_s is None:
        chop_s = _num(ep.get("attempts"), 0) * DEFAULT_CHOP_S
    chop_s = min(chop_s, field_s)
    overhead = walk_out + ph.get("convert", 0.0) + ph.get("to_bank", 0.0) + ph.get("store", 0.0) if banked else None
    hatchet = ep.get("hatchet") or {}
    return {"spot": ep.get("spot") or ep.get("venue"), "t0": t0, "t1": t1, "outcome": outcome,
            "why": ep.get("why"), "dry": bool(ep.get("dry")), "logs": _num(ep.get("logs"), 0),
            "field_s": field_s, "chop_s": chop_s, "overhead_s": overhead,
            "p": success_p(ep.get("skill"), hatchet.get("tool_bonus", 0.0))}


def adjusted_field_h(trip: dict, p_now) -> float:
    """Field hours the trip's logs would take today: its chopping time scaled by
    p_then / p_now (fewer attempts per log at a higher success chance), its
    walking unchanged."""
    f, c = trip["field_s"], trip["chop_s"]
    if trip["p"] and p_now:
        f = f - c + c * trip["p"] / p_now
    return max(f, 0.1 * trip["field_s"]) / 3600.0


def weight(t: float, now: float) -> float:
    return 0.5 ** (max(0.0, now - t) / 86400.0 / HALF_LIFE_DAYS)


def attribute_deaths(trips: list, deaths: list, spots: dict) -> list:
    """[(death, spot id)] for deaths (dicts t, x, y): during a lumber trip or
    within DEATH_LINK_S after one ended (the Terran PK killed us 11 s after the
    runner stopped), at most CURRENT_SPOT_MARGIN + radius from that spot's
    area; else, with no such trip (rows of aborted trips only exist since
    2026-10-02), inside a spot's area. Deaths elsewhere (hunting) don't count."""
    out = []
    for d in deaths:
        pos = (d["x"], d["y"]) if _num(d.get("x")) is not None else None
        cands = [tr for tr in trips if tr["t0"] <= d["t"] <= tr["t1"] + DEATH_LINK_S]
        if cands:
            sid = max(cands, key=lambda tr: tr["t0"])["spot"]
            s = spots.get(sid)
            if s is None or (pos is not None and s.get("area") and cheb(pos, s["area"]["center"])
                             > s["area"]["radius"] + CURRENT_SPOT_MARGIN):
                continue
            out.append((d, sid))
        elif pos is not None:
            inside = [(cheb(pos, s["area"]["center"]), sid) for sid, s in spots.items()
                      if s.get("area") and cheb(pos, s["area"]["center"]) <= s["area"]["radius"]]
            if inside:
                out.append((d, min(inside)[1]))
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
    for t, facet, x, y, z, outcome in rows:
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


# ------------------------------------------------------------------ the model
def overhead_prior_s(spot: dict) -> float:
    """Walk out + back between the bank and the area's edge, plus the bank work."""
    if not spot.get("banker") or not spot.get("area"):
        return OVERHEAD_FIXED_S + 120.0
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


def net_rate(q, lam, t_h, haz, gear_logs, recovery_h=RECOVERY_H) -> float:
    """Banked logs per hour for trips of q logs: field rate lam (logs/h),
    overhead t_h (h per trip), deaths per field hour haz, each losing the
    carried logs (q/2 on average), gear_logs and recovery_h."""
    tf = q / lam
    deaths = haz * tf
    return (q - deaths * (q / 2.0 + gear_logs)) / (tf + t_h + deaths * recovery_h)


def best_q(lam, t_h, haz, gear_logs, q_max) -> int:
    grid = [q for q in Q_GRID if q <= q_max] or [Q_GRID[0]]
    return max(grid, key=lambda q: net_rate(q, lam, t_h, haz, gear_logs))


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


def spot_model(spot, trips, sightings, prior, phi, p_now, now) -> dict:
    """Posterior parameters of one spot from its trips (trip_obs dicts), the
    hostile-player sightings during them ({trip t0: n}) and the rate prior
    (rate_prior)."""
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
    seen = sum(wi * sightings.get(tr["t0"], 0) for wi, tr in zip(w, trips))
    hp = _num(spot.get("hazard_prior"), 0.5) if pvp else 0.0
    chop = sum(tr["chop_s"] for tr in trips if tr["field_s"] > 0)
    field = sum(tr["field_s"] for tr in trips if tr["field_s"] > 0)
    return {"alpha": alpha, "beta": beta, "rate": alpha / beta,
            "overhead_s": mean, "overhead_sd_s": sd,
            "pvp": pvp, "sight_a": hp * HAZARD_PRIOR_H + seen, "sight_b": HAZARD_PRIOR_H + exposure,
            "chop_share": chop / field if field > 600 else DEFAULT_CHOP_SHARE,
            "trips": len(trips), "field_h": round(sum(tr["field_s"] for tr in trips) / 3600.0, 2),
            "logs": sum(tr["logs"] for tr in trips), "weight": round(sum(w), 2),
            "sightings": sum(sightings.get(tr["t0"], 0) for tr in trips)}


def _draw(m, death, rng):
    lam = rng.gammavariate(m["alpha"], 1.0 / m["beta"])
    t_h = max(10.0, rng.gauss(m["overhead_s"], m["overhead_sd_s"])) / 3600.0
    haz = 0.0
    if m["pvp"] and m["sight_a"] > 0:
        haz = rng.gammavariate(m["sight_a"], 1.0 / m["sight_b"]) * rng.betavariate(*death)
    return lam, t_h, haz


def _value(lam, t_h, haz, gear_logs, stint_h, travel_h):
    q = best_q(lam, t_h, haz, gear_logs, max(Q_GRID[0], lam * stint_h))
    v = net_rate(q, lam, t_h, haz, gear_logs)
    return v * stint_h / (stint_h + travel_h), q


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
    if dry and now - max(dry) < regrow_min * 60:
        return f"ran dry {int((now - max(dry)) / 60)} min ago (trees regrow in ~{int(regrow_min)} min)"
    return None


def hatchet_options(char: dict, table: dict, prices: dict) -> list:
    """Hatchets we own (worn or packed) plus the ones we could buy at a known
    price; price_gp = what losing or replacing it costs (None: unknown)."""
    def price(material, quality):
        key = f"hatchet:{material}" + (f":{quality}" if quality else "")
        if key in prices:
            return prices[key]["price_gp"]
        mat = next((m for m in table["materials"] if m["name"] == material), {})
        return mat.get("npc_price_gp") if quality is None else None
    out, seen = [], set()
    for h in char.get("hatchets") or []:
        key = (h["material"], h["quality"])
        if key in seen:
            continue
        seen.add(key)
        out.append({**{k: h[k] for k in ("material", "quality", "tool_bonus", "uses", "newbied")},
                    "owned": True, "worn": h["worn"], "price_gp": price(*key)})
    for m in table["materials"]:
        for q in [None] + [x["name"] for x in table["qualities"]]:
            if (m["name"], q) in seen:
                continue
            p = price(m["name"], q)
            if p is None:
                continue
            qd = next((x for x in table["qualities"] if x["name"] == q), {"tool_bonus": 0.0, "extra_uses": 0})
            out.append({"material": m["name"], "quality": q, "tool_bonus": round(m["tool_bonus"] + qd["tool_bonus"], 3),
                        "uses": table["base_uses"] + m["extra_uses"] + qd["extra_uses"], "newbied": False,
                        "owned": False, "worn": False, "price_gp": p})
    return out


def hatchet_value(opt, m, death_p, skill, p_ref, logs_per_success, gp_per_log, stint_h, price=None) -> float:
    """Net logs/hour (wear and loss on death in logs at gp_per_log) with hatchet
    opt at spot model m (posterior means). price overrides opt's (break-even)."""
    price = opt["price_gp"] if price is None else price
    p = success_p(skill, opt["tool_bonus"])
    lam = m["rate"]
    if p and p_ref:
        c = m["chop_share"]
        lam = 1.0 / (c / lam * p_ref / p + (1.0 - c) / lam)
    haz = (m["sight_a"] / m["sight_b"]) * death_p if m["pvp"] else 0.0
    gear = 0.0 if opt["newbied"] or not price else price / 2.0 / gp_per_log
    v, _ = _value(lam, m["overhead_s"] / 3600.0, haz, gear + DEATH_KIT_GP / gp_per_log, stint_h, 0.0)
    wear = v / logs_per_success * (price or 0.0) / opt["uses"] / gp_per_log
    return v - wear


def breakeven_price(opt, m, death_p, skill, p_ref, lps, gpl, stint_h, target) -> float | None:
    """The highest price at which opt still nets `target` logs/hour (None: it
    doesn't even at price 0)."""
    def f(price):
        return hatchet_value(opt, m, death_p, skill, p_ref, lps, gpl, stint_h, price=price) - target
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
         gp_per_log: float = 9.5, draws: int = DRAWS) -> dict:
    """The pure planner. episodes: lumber trip rows; sightings: [t] of pk_seen
    job events; deaths: [{t, x, y}] (world `death` events); regrow: regrowth();
    char: character() or None; here: the spot we stand at (current_spot)."""
    char = char or {}
    trips = [tr for tr in (trip_obs(e) for e in episodes) if tr is not None and tr["spot"] in spots]
    by_spot = {sid: [tr for tr in trips if tr["spot"] == sid] for sid in spots}
    hatchets = char.get("hatchets") or []
    worn = hatchets[0] if hatchets else None
    skill = char.get("skill")
    if skill is None:
        known = [e for e in episodes if _num(e.get("skill")) is not None]
        skill = known[-1]["skill"] if known else None
    p_now = success_p(skill, worn["tool_bonus"] if worn else 0.0)

    sight_by_trip = {}
    for t in sightings:
        tr = next((tr for tr in trips if tr["t0"] <= t <= tr["t1"]), None)
        if tr is not None:
            sight_by_trip[tr["t0"]] = sight_by_trip.get(tr["t0"], 0) + 1
    linked = attribute_deaths(trips, deaths, spots)
    threats = {sid: [] for sid in spots}
    for d, sid in linked:
        threats[sid].append(d["t"])
    for tr in trips:
        if tr["outcome"] == "aborted" and sight_by_trip.get(tr["t0"]):
            threats[tr["spot"]].append(tr["t1"])
    seen_w = sum(weight(tr["t1"], now) * sight_by_trip.get(tr["t0"], 0) for tr in trips)
    died_w = sum(weight(d["t"], now) for d, _ in linked)
    death = (DEATH_PRIOR[0] + died_w, DEATH_PRIOR[1] + max(0.0, seen_w - died_w))
    death_p = death[0] / (death[0] + death[1])

    prior = rate_prior(by_spot, p_now, now)
    phi = dispersion(by_spot, p_now)
    stint_h = stint_min / 60.0
    gear_gp = 0.0
    if worn and not worn["newbied"]:
        opts = hatchet_options({"hatchets": [worn]}, table, prices)
        gear_gp = (opts[0]["price_gp"] or 0.0) / 2.0 if opts else 0.0
    gear_logs = (gear_gp + DEATH_KIT_GP) / gp_per_log

    models, rows = {}, []
    for sid, s in spots.items():
        m = spot_model(s, by_spot[sid], sight_by_trip, prior, phi, p_now, now)
        why = eligibility(s, by_spot[sid], threats[sid], now, young, regrow["minutes"])
        travel_h = 0.0 if sid == here else _num(s.get("travel_min"), 10) / 60.0
        haz = (m["sight_a"] / m["sight_b"]) * death_p if m["pvp"] else 0.0
        v, q = _value(m["rate"], m["overhead_s"] / 3600.0, haz, gear_logs, stint_h, travel_h)
        lo = gamma_quantile(m["alpha"], m["beta"], 0.1)
        hi = gamma_quantile(m["alpha"], m["beta"], 0.9)
        m.update(travel_h=travel_h, haz=haz, value=v, q=q, why=why)
        models[sid] = m
        last = max((tr["t1"] for tr in by_spot[sid]), default=None)
        rows.append({"id": sid, "name": s.get("name"), "status": s.get("status"), "eligible": why is None,
                     "why_not": why, "trips": m["trips"], "field_h": m["field_h"], "logs": m["logs"],
                     "rate_logs_h": round(m["rate"]), "rate_80": [round(lo), round(hi)],
                     "overhead_s": round(m["overhead_s"]), "sightings": m["sightings"],
                     "sightings_per_h": round(m["sight_a"] / m["sight_b"], 2),
                     "deaths": sum(1 for _, x in linked if x == sid),
                     "deaths_per_h": round(haz, 3), "logs_per_trip": q, "net_logs_h": round(v),
                     "here": sid == here, "travel_min": round(travel_h * 60),
                     "last_trip_h_ago": None if last is None else round((now - last) / 3600.0, 1)})

    eligible = [sid for sid in spots if models[sid]["why"] is None]
    out = {"ok": True, "now": now, "skill": skill, "success_p": None if p_now is None else round(p_now, 3),
           "here": here, "regrow": regrow, "dispersion": round(phi, 1),
           "death_given_sighting": round(death_p, 3), "prior_rate_logs_h": round(prior[0] / prior[1]),
           "prior_cv": round(1 / math.sqrt(prior[0]), 2),
           "spots": sorted(rows, key=lambda r: (not r["eligible"], -r["net_logs_h"])), "pick": None}
    if not eligible:
        out["ok"] = False
        out["error"] = "no eligible lumber spot (see spots[].why_not)"
        return out

    wins = {sid: 0 for sid in eligible}
    for _ in range(draws):
        vals = {sid: _value(*_draw(models[sid], death, rng), gear_logs, stint_h, models[sid]["travel_h"])[0]
                for sid in eligible}
        wins[max(vals, key=vals.get)] += 1
    for r in out["spots"]:
        r["p_best"] = round(wins.get(r["id"], 0) / draws, 3) if r["eligible"] else 0.0
    sample = {sid: _value(*_draw(models[sid], death, rng), gear_logs, stint_h, models[sid]["travel_h"])[0]
              for sid in eligible}
    pick = max(sample, key=sample.get)
    greedy = max(eligible, key=lambda sid: models[sid]["value"])
    m = models[pick]
    q = m["q"]
    trip_s = (q / m["rate"] + m["overhead_s"] / 3600.0) * 3600.0
    n = max(1, round(stint_min * 60.0 / trip_s))
    timeout = int(max(1800, 2 * n * trip_s + 600))

    hat = hatchet_choice(char, table, prices, m, death_p, skill, p_now, logs_per_success, gp_per_log, stint_h)
    args = ["--spot", pick, "--trips", str(n), "--logs-per-trip", str(q),
            "--regrow-min", f"{regrow['minutes']:g}", "--timeout", str(timeout)]
    if hat.get("use"):
        args += ["--hatchet", hat["use"]]
    out["pick"] = {"spot": pick, "mode": "exploit" if pick == greedy else "explore",
                   "greedy": greedy, "p_best": round(wins[pick] / draws, 3),
                   "travel": None if pick == here else spots[pick].get("travel"),
                   "logs_per_trip": q, "trips": n, "timeout_s": timeout,
                   "expected_trip_min": round(trip_s / 60.0, 1), "expected_net_logs_h": round(m["value"]),
                   "args": args, "command": "ctl run lumber " + " ".join(args)}
    out["hatchets"] = hat
    return out


def hatchet_choice(char, table, prices, m, death_p, skill, p_now, lps, gpl, stint_h) -> dict:
    """Rank hatchet options at spot model m; `use` = the material[+quality] to
    pass the runner when an owned one wins and isn't what it would pick anyway
    (the worn one), `buy` = a better one we don't own at a known price."""
    opts = hatchet_options(char, table, prices)
    if not opts or skill is None:
        return {"options": [], "use": None, "buy": None,
                "note": "no skill or hatchet known (proxy down?)" if skill is None else "no hatchet known"}
    rows = []
    for o in opts:
        known = o["price_gp"] is not None
        v = hatchet_value(o, m, death_p, skill, p_now, lps, gpl, stint_h)
        rows.append({**o, "net_logs_h": round(v, 1), "priced": known})
    priced = [r for r in rows if r["priced"]]
    best = max(priced, key=lambda r: r["net_logs_h"]) if priced else None
    target = best["net_logs_h"] if best else 0.0
    for r in rows:
        r["breakeven_gp"] = None if r is best else breakeven_price(r, m, death_p, skill, p_now, lps, gpl,
                                                                    stint_h, target)
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
    attempts = memory.con.execute("SELECT t, facet, x, y, z, outcome FROM harvest_attempts ORDER BY t").fetchall()
    return {"episodes": memory.episodes("lumber"),
            "sightings": [e["t"] for e in memory.job_events("lumber") if e["kind"] == "pk_seen"],
            "deaths": deaths, "regrow": regrowth(attempts), "prices": memory.prices(),
            "logs_per_success": logs_per_success(memory)}


def plan_from_store(memory, world: dict | None, self_serial, pos, facet, young=False,
                    stint_min=STINT_MIN, seed=None, seeds_path=SEEDS, now=None) -> dict:
    spots = load_spots(memory, seeds_path)
    table = load_hatchets()
    inp = store_inputs(memory)
    char = character(world, self_serial, table) if world is not None and self_serial is not None else None
    out = plan(spots, inp["episodes"], inp["sightings"], inp["deaths"], inp["regrow"], char, table,
               inp["prices"], time.time() if now is None else now, random.Random(seed), young=young,
               stint_min=stint_min, here=current_spot(spots, pos, facet),
               logs_per_success=inp["logs_per_success"], gp_per_log=gp_per_log(inp["prices"]))
    out["character"] = None if char is None else {k: char[k] for k in ("name", "serial", "skill", "mounted", "buffs")}
    return out


# ------------------------------------------------------------------ discovering spots
SLUG = re.compile(r"[^a-z0-9]+")
YOUNG_TOWNS = ("Shelter Island",)  # no hostile player actions; bank and harvesting for Young only (wiki)


def floor_z(walk, x, y) -> int:
    """Where a banker at a bank marker stands: the lowest floor tile on it,
    else the land. Markers carry no z, and bank floors sit above the land
    (Shelter 20 on land 0, Terran 35-36); the runner's banker check allows a
    storey either way (agent_link.same_floor)."""
    objs = walk.objects(x, y)
    return next((o[0] for o in objs if o[4][0] == "item"), objs[0][0] if objs else 0)


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
             per_bank=3) -> list:
    """Candidate spots around banks: square windows (side 2·radius+1) on a
    radius-step grid whose centres lie ring[0]..ring[1] tiles from a bank,
    ranked by tree count, minus windows near a known guard point (town:
    harvesting is blocked there) or overlapping a known spot, at most
    per_bank per bank and none overlapping each other. trees_fn(x0, y0, x1, y1)
    -> [(x, y, z, graphic)] (uomap.UoMap.find_trees); bank_z_fn(x, y) -> the banker's z (floor_z)."""
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
            sid = f"auto_{SLUG.sub('_', town.lower()).strip('_')}_{cx}_{cy}"
            out.append({"id": sid, "name": f"{town}: {n} trees at ({cx},{cy}), {d} tiles from the bank",
                        "facet": facet, "area": {"center": [cx, cy], "radius": radius,
                                                 "note": f"discovered from the map: {n} tree statics"},
                        "trees": [], "pvp": town not in YOUNG_TOWNS, "requires_young": town in YOUNG_TOWNS,
                        "banker": {"serial": "0x00000000", "name": f"{town} bank (marker)",
                                   "pos": [bx, by, bank_z_fn(bx, by)]},
                        "hazard_prior": 0.0 if town in YOUNG_TOWNS else (1.0 if town in guards.LAWLESS_TOWNS else 0.5),
                        "travel": f"{town} moongate, then walk to the bank", "travel_min": 10,
                        "tree_count": n, "bank_distance": d})
            taken.append(((cx, cy), radius))
            kept += 1
    return out

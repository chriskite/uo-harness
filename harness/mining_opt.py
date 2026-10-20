"""Mining spot planner: which mining spot the next run goes to (`ctl mining plan`).

A spot is a natural forge with ore around it (harness/data/mining_spots.json, written by
`python harness/ore_nodes.py spots`; status overrides and runner rows in the store's mining_spots
table). The runner (loop_mining.py) digs there, smelts at the forge and drops the ingots off at home.

Model (deliberately small: the lumber planner's machinery without its trip-size and gear terms):
  - field rate λ: ore per field hour per spot, Gamma posterior over recency-weighted trips
    (lumber_opt.weight). Prior: the pooled rate of the measured trips (at least 3 with field
    time), else PRIOR_ORE_H (the live capture 20261019_200350: 189 ore in 8.7 field minutes), with
    coefficient of variation PRIOR_CV.
  - hazards per field hour: sent home (a threat recalled us or we fled into the guards; a restart
    costs RESTART_H) and death (half a stint of carried ingots lost plus DEATH_H recovery). Each is
    a Gamma posterior per spot around the pooled mining rate.
  - score: λ · max(0, 1 − (h_S·RESTART_H + h_D·(stint/2 + DEATH_H))) ore per hour.
  - choice: Thompson sampling (one posterior draw per eligible spot; the best draw wins), or the
    greedy posterior-mean pick with `exploit` (the runner's hop between spots).
Eligible: active, not in a lumber_opt no-go area, a landing known, no threat there within
lumber_opt.COOLDOWN_S and not run dry within REGROW_MIN.
"""
import copy
import math
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lumber_opt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SEEDS = os.path.join(HERE, "data", "mining_spots.json")
PRIOR_ORE_H = 1300.0     # ore per field hour before any trip (capture 20261019_200350: 189 ore in 8.7 field min)
PRIOR_CV = 0.5           # the prior rate's coefficient of variation
REGROW_MIN = 60          # minutes a spot that ran dry waits (nodes respawn in 30-60 min, docs/NOTES.md)
RESTART_H = 0.1          # hours a threat that sends us home costs (recall, room, the next run's way out)
DEATH_H = 0.5            # hours a death costs besides the carried ingots (recovery, re-equipping)
DRAWS = 2000             # Thompson draws for P(best)
STINT_MIN = 60           # field minutes one run should dig (the runner's --field-min)
# Storing first between two spots costs a stop in the rental room and a new run: the house steward and back out,
# the stockpile's and the shelf's Restock and Resupply (live 2026-10-20 11:47:34-49: 7 s to the room, 8 s storing;
# ~10 s out, lumber_opt ROOM_EXIT_S), then the overseer's plan and the next run's start [INFERENCE: ~30 s]
STORE_FIRST_S = 60.0


# ------------------------------------------------------------------ spots
def load_spots(memory, seeds_path: str = SEEDS) -> dict:
    """Seed spots merged with the store's mining_spots rows: a row with the id of a seed
    overrides its status (and any fields it carries); other rows are spots of their own. A
    seed keeps its own status (ore_nodes.py writes 'candidate' for the unreviewed ones)."""
    out = {}
    for sid, s in lumber_opt.load_seeds(seeds_path).items():
        out[sid] = {**s, "status": s.get("status", "active"), "source": "seed", "reason": None}
    for row in (memory.mining_spot_rows() if memory is not None else []):
        base = out.get(row["id"], {"id": row["id"]})
        out[row["id"]] = {**base, **row["data"], "id": row["id"], "status": row["status"],
                          "source": row["source"] if row["id"] not in out else base["source"],
                          "reason": row["reason"]}
    return out


def check_spot(spot: dict):
    """A spot the runner can work: an area (lumber_opt.check_spot) and a forge {x, y, z}."""
    lumber_opt.check_spot(spot)
    forge = spot.get("forge") or {}
    if not all(isinstance(forge.get(k), int) and not isinstance(forge.get(k), bool) for k in ("x", "y", "z")):
        raise ValueError(f"spot {spot.get('id')}: no forge")


def spot_knowledge(base: dict, spot: dict) -> dict:
    """The runner's knowledge for one spot: loops/mining.json's common facts with the spot's
    area, stands (offline tests; live runs find them on the map), forge and PvP flag."""
    check_spot(spot)
    k = copy.deepcopy(base)
    k["spot"] = {key: spot.get(key) for key in ("id", "name", "facet", "pvp")}
    k["pvp"] = bool(spot.get("pvp", True))
    k["facet"] = int(spot.get("facet") or 0)
    k["harvest"]["area"] = dict(spot["area"])
    k["harvest"]["stands"] = list(spot.get("stands") or [])
    k["forge"] = dict(spot["forge"])
    return k


# ------------------------------------------------------------------ evidence
def trip_obs(row: dict) -> dict | None:
    """One mining trip row (episodes, loop mining) as the model's observation, or None without
    times. Field time is the harvest phase less the walk out and the lockout waited at the spot."""
    t0, t1 = row.get("t_start"), row.get("t_end")
    if t0 is None or t1 is None:
        return None
    harvest = (row.get("phases_s") or {}).get("harvest") or 0.0
    field_h = max(0.0, harvest - (row.get("walk_out_s") or 0.0) - (row.get("lockout_s") or 0.0)) / 3600.0
    why = row.get("why") or ""
    died = why.startswith("died")
    sent_home = not died and (why.startswith("threat:") or "escaped by recall" in why or "fled into the guards" in why
                              or bool((row.get("creature") or {}).get("recalled")))
    return {"spot": row.get("spot"), "t0": t0, "t1": t1, "ore": row.get("ore", 0) or 0, "field_h": field_h,
            "died": died, "sent_home": sent_home, "dry": bool(row.get("dry"))}


def _gamma_draw(rng, shape: float, rate: float) -> float:
    return rng.gammavariate(shape, 1.0 / rate)


def _score(lam: float, h_s: float, h_d: float, death_cost_h: float) -> float:
    """Ore per hour net of the hazards' costs (module doc)."""
    return lam * max(0.0, 1.0 - (h_s * RESTART_H + h_d * death_cost_h))


# ------------------------------------------------------------------ the plan
def plan(spots: dict, episodes: list, now: float, rng: random.Random, landings: dict, draws: int = DRAWS,
         exploit: bool = False, stint_min: float = STINT_MIN, exclude=()) -> dict:
    """The next mining run (module doc). `landings`: {spot id: lumber_opt.landing_for row or None}."""
    obs = [o for o in (trip_obs(e) for e in episodes) if o is not None]
    measured = [o for o in obs if o["field_h"] > 0.05]
    mean = (sum(o["ore"] for o in measured) / sum(o["field_h"] for o in measured)
            if len(measured) >= 3 else PRIOR_ORE_H)
    mean = max(mean, 1.0)
    a0 = 1.0 / PRIOR_CV ** 2
    b0 = a0 / mean
    total_h = sum(o["field_h"] for o in obs)
    pooled = {k: (sum(1 for o in obs if o[k]) + 0.5) / (total_h + 1.0) for k in ("died", "sent_home")}
    death_cost_h = stint_min / 120.0 + DEATH_H
    models, rows = {}, []
    for sid, s in spots.items():
        mine = sorted((o for o in obs if o["spot"] == sid), key=lambda o: o["t1"])
        ws = [lumber_opt.weight(o["t1"], now) for o in mine]
        alpha = a0 + sum(w * o["ore"] for w, o in zip(ws, mine))
        beta = b0 + sum(w * o["field_h"] for w, o in zip(ws, mine))
        haz = {k: (1.0 + sum(w for w, o in zip(ws, mine) if o[k]),
                   1.0 / pooled[k] + sum(w * o["field_h"] for w, o in zip(ws, mine))) for k in ("died", "sent_home")}
        last = mine[-1] if mine else None
        landing = (landings or {}).get(sid)
        why, ready_in = None, None
        if sid in exclude:
            why = "current spot"
        elif s.get("status") != "active":
            why = s.get("status") or "no status"
        elif (ng := lumber_opt.no_go(int(s.get("facet") or 0), tuple((s.get("area") or {}).get("center") or (0, 0)))):
            why = ng
        elif landing is None:
            why = "no landing"
        elif last is not None and (last["sent_home"] or last["died"]) and now - last["t1"] < lumber_opt.COOLDOWN_S:
            why = f"threat {round((now - last['t1']) / 60)} min ago"
            ready_in = lumber_opt.COOLDOWN_S - (now - last["t1"])
        elif last is not None and last["dry"] and now - last["t1"] < REGROW_MIN * 60:
            why = f"dry {round((now - last['t1']) / 60)} min ago"
            ready_in = REGROW_MIN * 60 - (now - last["t1"])
        mean_score = _score(alpha / beta, haz["sent_home"][0] / haz["sent_home"][1], haz["died"][0] / haz["died"][1],
                            death_cost_h)
        models[sid] = {"alpha": alpha, "beta": beta, "haz": haz, "score": mean_score}
        rows.append({"id": sid, "name": s.get("name"), "status": s.get("status"), "eligible": why is None,
                     "why_not": why, "trips": len(mine), "field_h": round(sum(o["field_h"] for o in mine), 2),
                     "ore": sum(o["ore"] for o in mine), "rate_ore_h": round(alpha / beta),
                     "rate_80": [round(lumber_opt.gamma_quantile(alpha, beta, 0.1)),
                                 round(lumber_opt.gamma_quantile(alpha, beta, 0.9))],
                     "deaths": sum(1 for o in mine if o["died"]), "sent_home": sum(1 for o in mine if o["sent_home"]),
                     "score_ore_h": round(mean_score), "p_best": None,
                     "landing": None if landing is None else {k: landing.get(k)
                                                              for k in ("name", "dist", "source", "library")},
                     "last_trip_h_ago": None if last is None else round((now - last["t1"]) / 3600.0, 1),
                     "nodes": s.get("nodes"),
                     "ready_in_min": None if ready_in is None else math.ceil(ready_in / 60)})
    rows.sort(key=lambda r: (not r["eligible"], -r["score_ore_h"]))
    eligible = [r["id"] for r in rows if r["eligible"]]
    out = {"ok": True, "now": now, "prior_ore_h": round(mean), "spots": rows}
    if not eligible:
        waits = sorted((r["ready_in_min"], r["id"]) for r in rows if r["ready_in_min"] is not None)
        err = "no eligible mining spot (see spots[].why_not)"
        if waits:
            err += f"; the first one ready: {waits[0][1]} in {waits[0][0]} min"
        return {"ok": False, "error": err, "spots": rows, "retry_in_min": waits[0][0] if waits else None}

    def draw(sid):
        m = models[sid]
        return _score(_gamma_draw(rng, m["alpha"], m["beta"]), _gamma_draw(rng, *m["haz"]["sent_home"]),
                      _gamma_draw(rng, *m["haz"]["died"]), death_cost_h)
    wins = dict.fromkeys(eligible, 0)
    for _ in range(draws):
        wins[max(eligible, key=draw)] += 1
    for r in rows:
        if r["eligible"]:
            r["p_best"] = round(wins[r["id"]] / draws, 3)
    greedy = max(eligible, key=lambda sid: models[sid]["score"])

    def run(sid):
        args = ["--spot", sid, "--trips", "1", "--field-min", f"{stint_min:g}",
                "--timeout", str(int(stint_min * 60 * 1.5 + 900))]
        landing = next(r["landing"] for r in rows if r["id"] == sid)
        died = models[sid]["haz"]["died"]
        return {"spot": sid, "p_best": round(wins[sid] / draws, 3), "landing": landing,
                "expected_ore_h": round(models[sid]["score"]), "death_h": round(died[0] / died[1], 4),
                "field_min": stint_min, "args": args, "command": "ctl run mining " + " ".join(args)}
    if exploit:
        pick, chosen_by = greedy, "greedy (--exploit)"
    else:
        pick, chosen_by = max(eligible, key=draw), "thompson"
    out["pick"] = {**run(pick), "mode": "exploit" if pick == greedy else "explore", "chosen_by": chosen_by}
    out["greedy"] = run(greedy)
    return out


def hop_choice(run: dict, carried_ore: float, leg_h: float) -> tuple[bool, dict]:
    """Home after a leg that ended early with a load: carry it on to `run`'s spot (plan's pick) or store it
    first in the rental room, as the lumber runner decides for its logs (user 2026-10-20: "the same way we store
    boards ... when we head home and we have enough ingots that it's too risky to carry them back into the
    field, we store them"; lumber_opt.hop_choice). Both ways the next leg is the same; they differ by the load
    at risk and the room's time. A hop risks what the room would store (`carried_ore`: the ingots / 2, in ore;
    raw ore stays carried either way: the stockpile leaves it, live 2026-10-19 22:44:39) to a death on the way in
    (LOCKOUT_S) and in the leg (`leg_h`, the trip's field time left) at the spot's death hazard (posterior mean
    `death_h`); storing first costs STORE_FIRST_S at its rate (`expected_ore_h`). Returns (hop?, the terms)."""
    p = -math.expm1(-run["death_h"] * (max(0.0, leg_h) + lumber_opt.LOCKOUT_S / 3600.0))
    risk = carried_ore * p
    cost = run["expected_ore_h"] * STORE_FIRST_S / 3600.0
    return risk <= cost, {"carried_ore": round(carried_ore), "p_death": round(p, 4), "risk_ore": round(risk, 1),
                          "store_cost_ore": round(cost, 1)}


def plan_from_store(memory, world: dict | None, self_serial, pos, facet, stint_min: float = STINT_MIN, seed=None,
                    seeds_path: str = SEEDS, now: float | None = None, route_check: bool = True,
                    exploit: bool = False, exclude=(), home: dict | None = None, who: str | None = None) -> dict:
    """plan() over the memory store and a state-port snapshot (world None: no proxy). The
    character is the proxy's (world self name), else `who` (ctl's --char), else the newest mining
    trip row's; its home from harness/data/homes.json (home.for_character; `home` overrides: the
    runner's own), its own books from the store, each spot's landing from lumber_opt.spot_landings."""
    import home as homes
    import places
    now = time.time() if now is None else now
    spots = load_spots(memory, seeds_path)
    episodes = memory.episodes("mining")
    char = (lumber_opt.character(world, self_serial, lumber_opt.load_hatchets())
            if world is not None and self_serial is not None else None)
    who = (char or {}).get("name") or who or next(
        (e["character"]["name"] for e in reversed(episodes)
         if isinstance(e.get("character"), dict) and e["character"].get("name")), None)
    home = homes.for_character(who) if home is None else home
    if home is None:
        return {"ok": False, "error": f"no home in harness/data/homes.json for {who}"}
    landings = lumber_opt.spot_landings(memory, spots, home, places.known_books(memory, who), route_check)
    out = plan(spots, episodes, now, random.Random(seed), landings, exploit=exploit, stint_min=stint_min,
               exclude=exclude)
    out["character"] = None if char is None else {"name": char["name"], "serial": char["serial"]}
    return out

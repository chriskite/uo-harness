"""Self-optimizing lumber job (docs/LUMBER_LOOP.md §6): where to chop, how much
to carry home, and which hatchet to use, learned from the memory store.

The objective is stored logs per hour of agent time (what reaches the house's
stockpile or chest at home), net of expected PK losses (logs/hour is the proxy for
gold/hour until colored-wood prices are known; user decision 2026-10-02). The
overseer asks `ctl lumber plan` before each lumber run and gets one runner
command back.

Model (one spot = a tree area, `load_spots`; every trip starts and ends at the
character's home, harness/home.py, user decision 2026-10-04):
  - field rate λ: logs per hour in the field (walking between trees, chopping,
    escapes and holds included), per spot. Gamma posterior over recency-weighted
    trips (half-life HALF_LIFE_DAYS), quasi-Poisson with dispersion φ (logs come
    in lumps of ~8 per success). Prior (empirical Bayes): the measured spots' mean
    rate and their between-spot spread (random-effects moment fit, at least
    PRIOR_CV_MIN), so an unvisited spot is explored as "a spot like the others".
  - character: each trip's chopping time is rescaled to today's success chance
    p = Σ_wood tree-colour chance × min(1, (skill − offset)/divisor × (1 + tool
    bonus)) (wiki formulas in woods.json; a colour above our skill counts as
    regular wood [INFERENCE]; Terran 2026-10-02 measured 44/63 = 0.70 at 69.1
    skill, the formula says 0.69), and all its field time to today's logs per
    success (the Harvest aspect's procs double some successes and grow with the
    aspect: OPTIMIZER.md F1), so a spot measured earlier is credited with what it
    would yield now.
  - overhead T: (for a library rune: the recall into the library's house + the
    walk to its tome) + recall out + walk into the grove + lockout + recall home +
    convert + store, per trip. Normal posterior; prior from the spot's landing
    (landing_for: the rune landing nearest the grove, from our libraries and own books).
  - hazards per exposed hour (the walk in from the landing, the lockout at the grove
    and the field time), competing (trip_terms): death h_D (PK or creature; the trip
    stores nothing, the carried logs and every unblessed item we carry are lost, and
    the measured recovery time), sent home h_S (a player threat or a creature threat
    ended the trip early: recall escape, guard flight, a creature stop; the carried
    logs come home; two hazards with their own pooled rates) and theft h_T (a thief
    takes a fraction f of what we carry; the trip goes on). Each is a Gamma posterior
    per spot shrunk to a pooled rate; h_D's prior is the spot's hostile-player
    sightings per hour × P(death | sighting) plus the pooled creature-death rate.
    Death and sent-home rates are scaled by a shrunk time-of-day factor (UTC blocks).
  - trip size Q*: maximises the renewal-reward rate E[stored logs per trip
    cycle] / E[cycle time], minus supplies and the expected gear loss in logs,
    over Q_MIN…Q_MAX and what we can still carry (LUMBER_LOOP §6: Q* ≈
    λ·sqrt(2T/h) for a small death hazard alone).
  - grove capacity: the trees available × the share that yields (Beta, from tiles
    tried before Smart Harvest and from each dry trip since) × logs per tree
    (successes per tree × today's logs per success).
  - choice: Thompson sampling. One posterior draw per eligible spot, the best
    draw wins (no spot pays travel beyond its overhead: all start at home).
    That keeps exploring the uncertain spots while the evidence favours the good
    ones, and varies the routine (ANTICHEAT.md §8.3).
  - hatchet: per owned or buyable hatchet, the net logs/hour at the chosen spot
    minus wear (one use per success), in logs at the ordinary board price. Every
    carried hatchet is lost on death whichever is used; buying one adds its
    price to that. Unknown prices give a break-even price instead.
  - regrowth: (gap, regrown) pairs per tree (before Smart Harvest) and per stand
    (since); the gap where an isotonic fit of P(regrown | gap) reaches REGROW_P.
  - audit: prequential calibration per day (`ctl lumber audit`).

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
SKILL_IDS = {"Lumberjacking": 44, "Mining": 45}   # skills.mul order; looked up by name when the names are known

HALF_LIFE_DAYS = 14.0             # a trip's weight halves every two weeks: competition, PKs and patches change
PRIOR_CV_MIN = 0.15               # a new spot's rate prior is at least this uncertain: the between-spot spread
                                  # measured 2026-10-11 (OPTIMIZER.md F4) is ~0.06-0.18, above it the fit decides
PRIOR_MIN_FIELD_H = 0.5           # weighted field hours before a spot's rate shapes the prior
# Character drift (OPTIMIZER.md "under-predicted logs per hour"): one shared level g(day) of the log field
# rate on top of each spot's own, fitted from spots chopped on several days, smooth between days
DRIFT_TRIP_SD = 0.10              # log-rate sd of one field hour within a spot (measured 2026-10-11: 0.10)
DRIFT_BEND_SD = 0.02              # how much the level's daily change may change a day (a steady trend is free)
DRIFT_MIN_FIELD_S = 600.0         # trips shorter than this don't inform the level
DRIFT_PROJECT_DAYS = 1.0          # today's level is carried along the last daily change at most this far
# Today's logs per success, projected from the recent trend (the newest-N mean lags a growing aspect)
LPS_TREND_H = 72.0                # hours of successes the trend is fitted over
LPS_TREND_MIN = 300               # successes needed for a trend (else the newest LPS_RECENT mean)
LPS_PROJECT_MAX = 0.10            # the projection moves at most this far from that mean
DEFAULT_RATE = 1200.0             # logs per field hour before any trip (live 2026-10-02 at 69 skill: 1200-2000)
DEFAULT_CHOP_S = 9.5              # s per attempt for trips that didn't time their chops (LUMBER_LOOP §12)
DEFAULT_CHOP_SHARE = 0.7          # share of field time spent chopping when no trip timed it
DISPERSION_MIN = 8.0              # Var(logs) ≥ ~8 × mean: logs come ~7.6 per success
LOGS_PER_SUCCESS = 7.6            # until harvest_attempts say otherwise (live mean 7.6 at 69 skill, 9.1 on 2026-10-11)
LPS_RECENT = 1000                 # newest successes behind today's logs per success (Harvest-aspect procs drift it)
LOGS_PER_TREE = 19.0              # logs a tree gives before 'depleted' (harvest_attempts 2026-10-03: 470 cycles,
                                  # mean 19.3; per spot 17.7-20.2) until harvest memory has enough cycles
SUCCESSES_PER_TREE = 2.5          # the same in successes (19.3 logs / 7.6 per success): a node holds successes, an
                                  # aspect proc doubles one's logs (k334, k155), so logs per tree = this × logs/success
LOGS_PER_TREE_MIN_CYCLES = 20
DRY_OBS_TILES = 30.0              # tree tiles of evidence one dry trip is worth for its spot's yielding share
                                  # (the trees of one grove are emptied by the same players: not independent)
YIELD_PRIOR_TILES = 10.0          # tree tiles of pseudo-data behind a spot's yielding share (pooled mean)
SEC_PER_TILE = 0.45               # overhead prior: walking pace incl. detours [INFERENCE: mounted 0.1-0.2 s/step, on foot 0.4]
OVERHEAD_FIXED_S = 40.0           # the steps to the landing tile at home + convert + store + human pauses (live 10-20 s + pauses)
NO_LANDING_TILES = 60             # landing -> grove walk when no landing is known (display only) [INFERENCE]
OVERHEAD_SD_MIN_S = 30.0
HAZARD_PRIOR_H = 2.0              # field hours of pseudo-data behind a spot's hazard_prior (sightings)
# hostile sightings per field hour assumed at a spot where a faction waypost marker was seen (the
# runner's `faction_zone`; witcher_66, 2026-10-06: a faction group by "FACTION WP 17" killed Dan)
# [INFERENCE: 4x the default hazard_prior; faction players in view now count as sightings]
FACTION_ZONE_PRIOR = 2.0
DEATH_PRIOR = (1.0, 3.0)          # Beta prior of P(death | hostile player sighted) [INFERENCE]
CREATURE_DEATH_PRIOR = (0.01, 20.0)  # pooled creature deaths per field hour, pseudo field hours [INFERENCE]
DEATH_SHRINK_H = 10.0             # field hours a spot's death-rate prior counts for (deaths are rare)
DEATH_IN_TRIP_S = 60.0            # a death this soon after a trip's end is the trip's wherever it fell (escapes,
                                  # the home landing after an escape recall: OPTIMIZER.md F3)
HOME_PLAYER_PRIOR = (0.3, 2.0)    # pooled trips a player threat sent home per field hour, pseudo field hours [INFERENCE]
HOME_MONSTER_PRIOR = (0.2, 2.0)   # ... a creature or damage threat
HOME_SHRINK_H = 2.0               # field hours a spot's sent-home priors count for
TOD_BLOCK_H = 6                   # time-of-day blocks (UTC hours) the pooled hazards are scaled by
TOD_PRIOR_EVENTS = 5.0            # pseudo events at the overall rate behind each block's factor
THEFT_PRIOR = (0.02, 20.0)        # pooled thefts per field hour, pseudo field hours [INFERENCE]
THEFT_SHRINK_H = 20.0             # thefts are rare: a spot's rate leans on the pooled one
THEFT_FRACTION_PRIOR = (1.0, 1.0)  # Beta prior of the share of the carried logs one theft takes (mean 0.5) [INFERENCE]
CAUSE_LINK_S = 300.0              # a sighting this soon before a death makes it a PK death
RECOVERY_H = 20 / 60              # prior: ghost walk, resurrection, re-equip, back to work [INFERENCE: Terran death 2026-10-02]
RECOVERY_PRIOR_N = 2.0            # deaths' worth of that prior; measured: death -> the next lumber trip's start
RECOVERY_CAP_H = 3.0              # longer gaps are the end of the session, not the recovery
SUIT_ITEM = "armor:harvest_suit"  # the Harvest-aspect armor worn on every lumber trip (user 2026-10-11: ~1000 gp,
                                  # lost on death); its price comes from the prices table
DEATH_LINK_S = 1800.0             # a death this soon after a lumber trip counts for that trip's spot
COOLDOWN_S = 1800.0               # keep away from a spot this long after a death or a player threat there
# ... and this long after a thief made us leave it (a `thief` job event that recalled or stopped: our trapped
# pouch went off, or a suspect closed in again after the keep-away step; docs/research/THREATS.md §7 T3)
THIEF_COOLDOWN_S = 1200.0
STINT_MIN = 60.0                  # minutes one runner start should last (when trips are shorter)
Q_MIN, Q_MAX = 200, 10000         # logs per trip (user decision 2026-10-03)
Q_GRID = tuple(sorted({int(round(Q_MIN * (Q_MAX / Q_MIN) ** (i / 40) / 25.0)) * 25 for i in range(41)}))
LOG_WEIGHT = 0.025                # stones per log or board (docs/NOTES.md)
REGROW_P = 0.6                    # revisit a depleted tree once P(regrown) reaches this
REGROW_DEFAULT_MIN = 45.0         # until enough depleted-then-retried trees are seen
REGROW_MIN_PAIRS = 20
DRAWS = 2000                      # Monte Carlo draws for P(best)
CURRENT_SPOT_MARGIN = 40          # tiles beyond a spot's radius where a death still counts for it
LOCKOUT_S = 60.0                  # harvest lockout after any travel (TRAVEL_DEATH §2)
RECALL_TRIP_S = 4.0               # open the book, press, the 2 s cast, arrival (live 2.1 s + the book)
FAIL_EXPOSURE_H = 0.25            # a trip the place itself spoiled counts at least this many field hours
UNWORKABLE_TRIPS = 2              # that many such trips in a row: the spot is out ...
UNWORKABLE_DAYS = 7.0             # ... for this long, then gets one more try
HOP_BACK_S = RECALL_TRIP_S + OVERHEAD_FIXED_S   # a hop's last way home: the recall, convert, store
HOP_MIN_GAIN_LOGS = 0.0          # a library hop must be worth more than going home by this (docs/LUMBER_LOOP.md §6)
# Areas no spot is planned or discovered in, whatever a store says (user decisions; committed so every machine's
# store and every future `lumber discover` honour them): (facet, centre, Chebyshev radius, why)
NO_GO_AREAS = (
    (0, (3654, 2957), 200, "Old Papua: unsurvivable for Dan at his aspect and armor (user 2026-10-08, after a death "
                           "to a mounted gloomwood hunter at witcher_336)"),
)
# Abort reasons that say the place can't be worked (not the character, the server or a player):
# no tree we can reach, harvesting answered by something the runner doesn't know (a town region)
PLACE_FAILURES = ("no harvestable tree", "without a known outcome")
ROUTES_META = "lumber_landing_routes"   # memory meta: {landing>grove key: route tiles or null} (landing_routes)
BAD_LANDINGS_META = "lumber_bad_landings"  # memory meta: {facet:x,y: {name, landed, t}} (mark_bad_landing)


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


def merged_alias(spots: dict) -> dict:
    """{spot id: forest spot id} for the spots `ctl lumber forests` merged into a forest
    (their `merged_into`, lumber_forest.py): their trips and thefts count for that forest."""
    return {sid: s["merged_into"] for sid, s in spots.items()
            if s.get("merged_into") in spots and s["merged_into"] != sid}


def check_spot(spot: dict):
    """A spot the runner can work: an area (center [x, y] and radius; a forest spot also
    `cells`, its CELL squares as [cx, cy] pairs, inside that square)."""
    area = spot.get("area") or {}
    if len(area.get("center") or ()) != 2 or _num(area.get("radius")) is None:
        raise ValueError(f"spot {spot.get('id')}: area needs center [x, y] and radius")
    cells = area.get("cells")
    if cells is not None and (not isinstance(cells, list) or not cells
                              or any(not isinstance(c, (list, tuple)) or len(c) != 2 for c in cells)):
        raise ValueError(f"spot {spot.get('id')}: area cells must be a non-empty list of [cx, cy] pairs")


# A forest spot's area (lumber_forest.py, docs/LUMBER_LOOP.md §6 "Forest spots") is a set of
# CELL x CELL squares (one map block each); its center and radius are the square that holds
# them. A spot without `cells` is that square itself.
CELL = 8


def area_cells(area: dict) -> frozenset | None:
    """A forest area's cells as {(cx, cy)}, None for a plain square."""
    cells = area.get("cells")
    return None if not cells else frozenset((int(c[0]), int(c[1])) for c in cells)


def in_area(area: dict, x: int, y: int, cells: frozenset | None = None) -> bool:
    """(x, y) lies in the area: one of its cells, else its square. cells: area_cells(area)
    computed once by a caller testing many tiles."""
    cells = area_cells(area) if cells is None else cells
    if cells is None:
        return cheb((x, y), area["center"]) <= area["radius"]
    return (x // CELL, y // CELL) in cells


def area_dist(area: dict, p, cells: frozenset | None = None) -> int:
    """Chebyshev tiles from p to the area (0 inside it)."""
    cells = area_cells(area) if cells is None else cells
    x, y = int(p[0]), int(p[1])
    if cells is None:
        return max(0, cheb((x, y), area["center"]) - int(area["radius"]))
    return min(max(0, cx * CELL - x, x - cx * CELL - CELL + 1, cy * CELL - y, y - cy * CELL - CELL + 1)
               for cx, cy in cells)


def landing_dist(area: dict, p) -> int:
    """What a landing row's `dist` measures (landing_for): tiles from a square area's
    centre, from a forest's edge (its centre may lie outside it)."""
    return area_dist(area, p) if area.get("cells") else cheb(p, area["center"])


def spot_knowledge(know: dict, spot: dict) -> dict:
    """The runner's knowledge for one spot: loops/lumber.json's common facts
    (texts, captcha, conversion) with the spot's area, trees and PvP flag over them.
    No banker: trips start and end at home (harness/home.py)."""
    check_spot(spot)
    k = copy.deepcopy(know)
    k.pop("venue", None)                         # the demo's venue; the spot says where we are
    k.get("npcs", {}).pop("banker", None)        # the demo's banker: no trip goes to a bank
    k["spot"] = {key: spot.get(key) for key in ("id", "name", "facet", "pvp")}
    k["pvp"] = bool(spot.get("pvp", True))
    k["facet"] = int(spot.get("facet") or 0)
    k["harvest"]["trees"] = list(spot.get("trees") or [])
    k["harvest"]["area"] = dict(spot["area"])
    return k


# ------------------------------------------------------------------ the way out: landings
def landing_for(spot: dict, home: dict | None, books=(), route_ok=None, bad=()) -> dict | None:
    """The landing a trip to `spot` recalls to (user decision 2026-10-04: always as
    close to the grove as we can): the first places.landings row nearest the area
    (a square's centre, a forest's nearest edge: `dist` is landing_dist), from the rune
    library at home (home.libraries: reached by our own book's library_rune, a recall into the
    library's house, then a walk to its tome; none without a library_rune: unreachable) and the
    character's own books (places.known_books), the 'Bad Places'
    tomes' landings left out, and those in `bad` (landing_key: a recall to them landed elsewhere,
    bad_landings), for which route_ok(row, spot) holds (a walking route from the
    landing into the grove; None: no check). None when no landing qualifies."""
    import home as homes
    import places
    area = spot["area"]
    (cx, cy) = area["center"]
    facet = int(spot.get("facet") or 0)
    libs = homes.libraries(home) if homes.library_rune(home) else []
    rows = places.landings(cx, cy, facet, libraries=libs, books=books)
    cells = area_cells(area)
    if cells is not None:                            # stable: a tie keeps landings' own-book-first order
        rows = sorted(({**row, "dist": area_dist(area, (row["x"], row["y"]), cells)} for row in rows),
                      key=lambda row: row["dist"])
    for row in rows:
        if landing_key(row, facet) in bad:
            continue
        if route_ok is None or route_ok(row, spot):
            return row
    return None


def landing_key(row: dict, facet: int) -> str:
    """A landing's key in BAD_LANDINGS_META: its facet and the rune's tile."""
    return f"{facet}:{row['x']},{row['y']}"


def bad_landings(memory) -> dict:
    """{landing_key: {name, landed, t}}: runes whose recall landed away from their tile
    (loop_lumber.go_out, beyond LANDING_SLACK; live 2026-10-05 the DTF rune "Jonny's House"
    (1817,1865) landed at (1809,1871): a house that puts recalls outside [INFERENCE])."""
    import task_wrap
    try:
        got = json.loads(task_wrap.meta_get(memory, BAD_LANDINGS_META) or "{}")
    except ValueError:
        return {}
    return got if isinstance(got, dict) else {}


def mark_bad_landing(memory, row: dict, facet: int, landed, why: str | None = None):
    """Remember that the recall to `row` landed at `landed`, not by its tile, or couldn't be made
    at all (`why`, landed None: e.g. "blocked"): plans and runs pass it over."""
    import task_wrap
    entry = {"name": row.get("name"), "landed": list(landed) if landed else None, "why": why,
             "t": round(time.time(), 1)}
    task_wrap.meta_update_json(memory, BAD_LANDINGS_META,
                               lambda cur: ({**(cur or {}), landing_key(row, facet): entry}, None), {})


def landing_view(row: dict | None) -> dict | None:
    """A landing as the plan shows it (and the runner's `out` travel leg records it)."""
    if row is None:
        return None
    return {k: row.get(k) for k in ("source", "library", "tome", "book", "name", "x", "y", "dist",
                                    "route_tiles", "route_checked")}


def route_key(row: dict, spot: dict) -> str:
    """The landing-route cache key: facet, the landing tile, the grove's area (a forest's
    cells by count and checksum)."""
    area = spot["area"]
    (cx, cy), r = area["center"], area["radius"]
    key = f"{int(spot.get('facet') or 0)}:{row['x']},{row['y']}>{cx},{cy},{r}"
    if area.get("cells"):
        import zlib
        cells = sorted((int(c[0]), int(c[1])) for c in area["cells"])
        key += f"/{len(cells)}:{zlib.crc32(json.dumps(cells).encode()):08x}"
    return key


def landing_routes(memory) -> dict:
    """{route_key: route tiles or None (no route)} cached in the memory store's meta."""
    import task_wrap
    try:
        got = json.loads(task_wrap.meta_get(memory, ROUTES_META) or "{}")
    except ValueError:
        return {}
    return got if isinstance(got, dict) else {}


def save_landing_routes(memory, new: dict):
    """Merge newly planned routes into the cache (another process may have added some)."""
    import task_wrap
    if new:
        task_wrap.meta_update_json(memory, ROUTES_META, lambda cur: ({**(cur or {}), **new}, None), {})


def make_route_ok(route_fn, routes: dict, new: dict, tries: int = None, max_route: int = None):
    """route_ok for landing_for: the landing has a walking route into the grove no longer
    than max_route (MAX_RUNE_ROUTE) tiles. Answers come from `routes` (the cache); an
    unknown one is planned with route_fn (make_route_fn: the runner's planning budget)
    and added to `routes` and `new`, at most `tries` (ROUTE_TRIES) plans per spot a call
    (later landings of that spot fail unplanned: the next plan tries them). route_fn None:
    cached answers only, an unknown route counts as there. A landing farther from the
    area's edge than max_route fails without planning."""
    tries = ROUTE_TRIES if tries is None else tries
    max_route = MAX_RUNE_ROUTE if max_route is None else max_route
    planned = {}

    def ok(row, spot):
        key = route_key(row, spot)
        if key not in routes:
            if area_dist(spot["area"], (row["x"], row["y"])) > max_route:
                return False
            if route_fn is None:
                return True
            if planned.get(spot["id"], 0) >= tries:
                return False
            planned[spot["id"]] = planned.get(spot["id"], 0) + 1
            routes[key] = new[key] = route_fn((row["x"], row["y"]), spot["area"])
        n = routes[key]
        return n is not None and n <= max_route
    return ok


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
    sid = names.index(name) if name in names else SKILL_IDS.get(name)
    if sid is None:
        return None
    sk = skills.get(str(sid)) or skills.get(sid)
    v = _num((sk or {}).get("value"))
    return None if v is None else v / 10.0


def buff_name(icon, b: dict) -> str:
    """A buff as status.buffs (ctl _buffs) names it: its title, else its cliloc
    rendered from the client's Cliloc.enu (Magic Reflection and Tracking Hunting
    come with an empty title and cliloc 1044416 / 1110004), else the number."""
    if b.get("title"):
        return b["title"]
    if not b.get("cliloc"):
        return str(icon)
    from uo import cliloc
    try:
        return cliloc.translate(cliloc.load(), int(b["cliloc"]))
    except OSError:
        return str(b["cliloc"])


def character(world: dict, self_serial, table: dict) -> dict:
    """What the optimizer needs to know about us now (state-port world): skill,
    mounted, buff names (buff_name), every hatchet worn or in the backpack (any bag
    depth) with its kind, the reagents in the pack ({name: count}), the trapped
    pouches (live, hue 38, and spent ones: harness/pouch.py), weight and
    weight_max (stones; the status packet's max already holds Camping's bonus
    [INFERENCE]) and Young status (the "(young)" name label)."""
    import pouch
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
            "buffs": sorted({buff_name(icon, b) for icon, b in buffs.items()}),
            "hatchets": hatchets, "reagents": {combat.REAGENTS[g]: n for g, n in regs.items()},
            "pouches": None if pack is None else {
                "live": len(pouch.live(world, pack)),
                "spent": sum(1 for p in pouch.pack_pouches(world, pack).values() if not p["live"])},
            "weight": _num(me.get("weight")), "weight_max": _num((me.get("stats") or {}).get("weight_max")),
            "young": "(young)" in label.lower()}


# ------------------------------------------------------------------ evidence
def supply_gp(supplies: dict | None, prices: dict) -> tuple[float, int]:
    """(gp, unpriced) of what one trip used (its row's `supplies`): reagents at
    `reagent:<name>` (spaces as _), our own book's charges at `recall_charge` (what
    recharging costs), trapped pouches gone off at `trapped_pouch` (Errol sells them at 25
    gp: `ctl lumber price trapped_pouch 25`), the public library tome's charges free.
    Unpriced units count nothing and are reported, never guessed."""
    gp, unpriced = 0.0, 0
    s = supplies or {}
    items = [(f"reagent:{k.replace(' ', '_')}", n) for k, n in (s.get("reagents_used") or {}).items()]
    items.append(("recall_charge", _num(s.get("own_charges"), 0)))
    items.append(("trapped_pouch", _num(s.get("trapped_pouches"), 0)))
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
    without times. A trip that ended at home (outcome "stored", since 2026-10-04: the
    boards went into the chest at home) has an overhead: the walk out (for a library rune
    the recall into its house and the walk to the tome, the recall, the walk into the grove),
    the lockout, the way home (`to_room`, the phase's old name: the recall home and the walk to
    the landing tile), convert and store. Legacy rows of the
    bank era (outcome "banked", or none: the oldest rows) keep their form (walk out,
    lockout, convert, to_bank, store) as history. Rows written before 2026-10-02 lack
    walk_out_s/chop_s/skill: the walk out is taken to equal the walk to the bank, chops
    cost DEFAULT_CHOP_S each, and their chopping isn't rescaled. Field time excludes
    the travel lockout waited out at the first tree (`lockout_s`, since 2026-10-03;
    it's overhead); a row whose walk out never ended (walk_out_s null: no chop, e.g.
    the recall failed) has no field time. `sent_home`: a threat ended the trip early
    without killing us (a "threat: …" abort: recall escape, guard flight, a creature
    or damage stop; or the row's `creature` says it recalled); plan() adds trips with
    a recall/guard_flight event. A leg that ended in a library hop (outcome "hopped",
    docs/LUMBER_LOOP.md §6 "Library hops") is a trip of its spot like an aborted one: its
    logs and field time count, a threat that ended it counts as sent home, and it has no
    overhead of its own (the next leg's walk out holds the hop, its `to_room` the way home); a
    stored leg's `hop_home` phase (the recall home before the hop question) is overhead.
    Exposure to the hazards (OPTIMIZER.md F3) is the field time plus the lockout waited at the grove
    plus the walk from the landing into it (`walk_in_s`: the walk out less its `out` travel leg, which
    holds the recall into the library's house, the walk to the tome and the casts). `lps`: the trip's own logs per success (the
    Harvest aspect's procs drift it, F1); `home_kind`: "monster" when a creature (or its recall) sent us
    home, else "player"."""
    t0, t1 = _num(ep.get("t_start")), _num(ep.get("t_end"))
    if t0 is None or t1 is None or t1 < t0:
        return None
    ph = {k: v for k, v in (ep.get("phases_s") or {}).items() if _num(v) is not None}
    outcome = ep.get("outcome") or "banked"      # rows without one are the oldest, bank-era trips
    stored = outcome == "stored" and "to_room" in ph
    banked = outcome == "banked" and "to_bank" in ph
    harvest = ph.get("harvest")
    if harvest is None:
        harvest = (t1 - t0) - sum(v for k, v in ph.items() if k != "harvest")
    walk_out = _num(ep.get("walk_out_s"))
    if walk_out is None:
        walk_out = harvest if "walk_out_s" in ep else (ph.get("to_bank", 0.0) if banked else 0.0)
    lockout = _num(ep.get("lockout_s"), 0.0)
    field_s = max(0.0, harvest - walk_out - lockout)
    walk_in = 0.0
    out_leg = next((lg for lg in (ep.get("travel") if isinstance(ep.get("travel"), list) else [])
                    if isinstance(lg, dict) and lg.get("leg") == "out" and lg.get("ok")), None)
    if _num(ep.get("walk_out_s")) is not None and out_leg is not None:
        walk_in = max(0.0, walk_out - _num(out_leg.get("s"), 0.0))
    chop_s = _num(ep.get("chop_s"))
    if chop_s is None:
        chop_s = _num(ep.get("attempts"), 0) * DEFAULT_CHOP_S
    chop_s = min(chop_s, field_s)
    back = "to_room" if stored else "to_bank" if banked else None
    overhead = (walk_out + lockout + ph.get("hop_home", 0.0) + ph.get(back, 0.0) + ph.get("convert", 0.0)
                + ph.get("store", 0.0) if back else None)    # hop_home: the recall home before a hop question
    hatchet = ep.get("hatchet") or {}
    logs = _num(ep.get("logs"), 0)
    why = ep.get("why") or ""
    creature = ep.get("creature") if isinstance(ep.get("creature"), dict) else {}
    cut = outcome in ("aborted", "hopped")
    sent_home = cut and (why.startswith("threat:") or "escaped by recall" in why
                         or "fled into the guards" in why or bool(creature.get("recalled")))
    monster = why.startswith("threat: monster") or bool(creature.get("recalled"))
    successes = _num(ep.get("successes"))
    carried = ep.get("carried_end") if isinstance(ep.get("carried_end"), dict) else {}
    gp, unpriced = supply_gp(ep["supplies"], prices or {}) if ep.get("supplies") is not None else (None, 0)
    return {"spot": ep.get("spot") or ep.get("venue"), "t0": t0, "t1": t1, "outcome": outcome,
            "why": ep.get("why"), "dry": bool(ep.get("dry")), "logs": logs,
            "field_s": field_s, "chop_s": chop_s, "overhead_s": overhead,
            "walk_in_s": walk_in, "lockout_s": lockout,
            "exposure_s": field_s + (walk_in + lockout if _num(ep.get("walk_out_s")) is not None else 0.0),
            "successes": successes, "lps": logs / successes if successes and logs else None,
            "home_kind": "monster" if monster else "player",
            "place_fail": cut and logs == 0 and any(p in why for p in PLACE_FAILURES),
            "sent_home": sent_home and not why.startswith("died"),
            "carried_end": sum(_num(v, 0) for v in carried.values()) if carried else None,
            "hatchet": hatchet or None,
            "p": success_p(ep.get("skill"), hatchet.get("tool_bonus", 0.0)),
            "supply_gp": gp, "supply_unpriced": unpriced}


def adjusted_field_h(trip: dict, p_now, lps_now=None) -> float:
    """Field hours the trip's logs would take today: its chopping time scaled by
    p_then / p_now (fewer attempts per log at a higher success chance), its
    walking unchanged, then all of it by lps_then / lps_now (each success gives
    more logs as the Harvest aspect grows: OPTIMIZER.md F1; a trip without
    successes isn't rescaled) and by the character's drift since then (`drift`,
    drift_levels: what the runner, skill and gear gained beyond those). A trip the
    place spoiled (place_fail: no tree we could reach, harvesting refused) counts at
    least FAIL_EXPOSURE_H with its 0 logs, so a spot that can't be worked loses its
    optimistic prior instead of looking untried forever."""
    f, c = trip["field_s"], trip["chop_s"]
    if trip["p"] and p_now:
        f = f - c + c * trip["p"] / p_now
    f = max(f, 0.1 * trip["field_s"])
    if trip.get("lps") and lps_now:
        f *= trip["lps"] / lps_now
    f *= trip.get("drift", 1.0)
    h = f / 3600.0
    return max(h, FAIL_EXPOSURE_H) if trip.get("place_fail") else h


def drift_levels(trips: list, p_now, lps_now, now: float) -> dict:
    """The character's drift (OPTIMIZER.md, "under-predicted logs per hour"): log field rate
    = spot effect + g(day) for the trips of spots chopped on at least two UTC days (≥
    DRIFT_MIN_FIELD_S, logs), at their adjusted_field_h without drift (success chance and
    logs per success already rescaled). g is smooth over the days between the first and the
    last such trip: its day-to-day change may itself change by DRIFT_BEND_SD a day (a steady
    trend costs nothing, a bend does; a day without data is interpolated), each trip a
    measurement of sd DRIFT_TRIP_SD per field hour; weighted least squares, g = 0 on the last
    day (held from there on, and carried along its last daily change up to now:
    DRIFT_PROJECT_DAYS at most). -> {g: {day: g}, now: g(now), first, last, trips, spots};
    drift_factor reads it."""
    use = [tr for tr in trips if tr["field_s"] >= DRIFT_MIN_FIELD_S and tr["logs"] > 0 and not tr.get("place_fail")]
    days_of = {}
    for tr in use:
        days_of.setdefault(tr["spot"], set()).add(int(tr["t0"] // 86400))
    use = [tr for tr in use if len(days_of[tr["spot"]]) >= 2]
    out = {"g": {}, "now": 0.0, "first": None, "last": None, "trips": len(use),
           "spots": len({tr["spot"] for tr in use})}
    if not use:
        return out
    import numpy as np
    first = min(int(tr["t0"] // 86400) for tr in use)
    last = max(int(tr["t0"] // 86400) for tr in use)
    nd = last - first                                   # g columns for first..last-1 (g[last] = 0)
    sids = sorted({tr["spot"] for tr in use})
    col = {sid: nd + i for i, sid in enumerate(sids)}
    rows, ys, ws = [], [], []
    for tr in use:
        r = np.zeros(nd + len(sids))
        d = int(tr["t0"] // 86400) - first
        if d < nd:
            r[d] = 1.0
        r[col[tr["spot"]]] = 1.0
        h = adjusted_field_h({**tr, "drift": 1.0}, p_now, lps_now)
        rows.append(r)
        ys.append(math.log(tr["logs"] / h))
        ws.append(tr["field_s"] / 3600.0 / DRIFT_TRIP_SD ** 2)
    for d in range(2, nd + 1):                          # g[d] - 2 g[d-1] + g[d-2] ~ N(0, DRIFT_BEND_SD²)
        r = np.zeros(nd + len(sids))
        if d < nd:
            r[d] = 1.0
        r[d - 1] = -2.0
        r[d - 2] = 1.0
        rows.append(r)
        ys.append(0.0)
        ws.append(1.0 / DRIFT_BEND_SD ** 2)
    a, y, w = np.array(rows), np.array(ys), np.sqrt(np.array(ws))
    sol, *_ = np.linalg.lstsq(a * w[:, None], y * w, rcond=None)
    g = {first + d: float(sol[d]) if d < nd else 0.0 for d in range(nd + 1)}
    # today's level: the last day's, carried along its last daily change up to DRIFT_PROJECT_DAYS
    # past that day's middle (a plan made the next day otherwise trails the trend by a day)
    ahead = min(max(0.0, now / 86400.0 - (last + 0.5)), DRIFT_PROJECT_DAYS)
    out.update(first=first, last=last, g=g, now=(g[last] - g.get(last - 1, 0.0)) * ahead)
    return out


def drift_factor(drift: dict, t: float) -> float:
    """The hours factor exp(g(day of t) − g(now)) for a trip started at t: below 1 for a day
    when the character got less per hour than now (its hours shrink to today's). Days before
    the fit's first take its first level, after its last the last."""
    if not drift["g"]:
        return 1.0
    d = min(max(int(t // 86400), drift["first"]), drift["last"])
    return math.exp(drift["g"][d] - drift["now"])


def weight(t: float, now: float) -> float:
    return 0.5 ** (max(0.0, now - t) / 86400.0 / HALF_LIFE_DAYS)


def attribute_deaths(trips: list, deaths: list, spots: dict) -> list:
    """[(death, spot id, trip or None)] for deaths (dicts t, x, y): during a lumber trip
    (up to DEATH_IN_TRIP_S after its end) wherever it fell: on an escape run, on the way in
    from the landing or at the home landing an escape recall reached (OPTIMIZER.md F3: 3 of
    12 deaths fell 47-2 243 tiles from their spot); after the trip within DEATH_LINK_S
    (the Terran PK killed us 11 s after the runner stopped) at most CURRENT_SPOT_MARGIN
    from that spot's area; else, with no such trip (rows of aborted trips only exist since
    2026-10-02), inside a spot's area. Deaths elsewhere (hunting) don't count."""
    out = []
    for d in deaths:
        pos = (d["x"], d["y"]) if _num(d.get("x")) is not None else None
        cands = [tr for tr in trips if tr["t0"] <= d["t"] <= tr["t1"] + DEATH_LINK_S]
        if cands:
            tr = max(cands, key=lambda tr: tr["t0"])
            sid = tr["spot"]
            s = spots.get(sid)
            during = d["t"] <= tr["t1"] + DEATH_IN_TRIP_S
            if s is None or (not during and pos is not None and s.get("area")
                             and area_dist(s["area"], pos) > CURRENT_SPOT_MARGIN):
                continue
            out.append((d, sid, tr))
        elif pos is not None:
            inside = [(cheb(pos, s["area"]["center"]), sid) for sid, s in spots.items()
                      if s.get("area") and in_area(s["area"], *pos)]
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


def regrowth(rows, stands=()) -> dict:
    """When a depleted tree has wood again. Two kinds of (gap, regrown) pairs:
      - per tree, from harvest_attempts rows (t, facet, x, y, z, outcome) in time order
        (before Smart Harvest, 2026-10-04): a depleted attempt followed by another attempt
        on the same tree (a fail or a success means wood again);
      - per stand, from the runner's `stand` job events ({t, facet, data}; since Smart
        Harvest, OPTIMIZER.md F2): a stand that ended "nothing nearby" followed by another
        stand on the same tile, regrown when that one gave logs. That is what the runner
        acts on (a stand's trees within reach, any of which may regrow).
    P(regrown | gap) is fitted isotonic (pool-adjacent-violators); the estimate is the
    smallest gap where it reaches REGROW_P, rounded up to 5 min. REGROW_DEFAULT_MIN with
    too few pairs or when it never does. The runner skips a dry stand for the window in
    force, so gaps shorter than it are seldom seen: the estimate moves down only as far
    as the shortest gaps observed. `nothing_near` marks on trees make no pairs (the server
    never says which tree regrew)."""
    last, pairs = {}, []
    for t, facet, x, y, z, outcome, *_ in rows:
        k = (facet, x, y, z)
        prev = last.get(k)
        if prev is not None and prev[1] == "depleted" and outcome in ("success", "fail", "depleted"):
            pairs.append(((t - prev[0]) / 60.0, outcome != "depleted"))
        last[k] = (t, outcome)
    n_tree = len(pairs)
    last = {}
    for e in stands:
        d = e.get("data") or {}
        st = d.get("stand")
        if not isinstance(st, list) or len(st) < 2:
            continue
        k = (e.get("facet"), st[0], st[1])
        start = e["t"] - (_num(d.get("s"), 0.0))
        logs = _num(d.get("logs"), 0)
        prev = last.get(k)
        if prev is not None and (logs > 0 or d.get("end") == "nothing_near"):
            pairs.append(((start - prev) / 60.0, logs > 0))
        if d.get("end") == "nothing_near":
            last[k] = e["t"]
        elif logs > 0:
            last.pop(k, None)
    pairs.sort()
    out = {"pairs": len(pairs), "tree_pairs": n_tree, "stand_pairs": len(pairs) - n_tree,
           "regrown": sum(1 for _, r in pairs if r), "minutes": REGROW_DEFAULT_MIN, "fitted": False}
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


def tree_yield(attempts, tiles_by_spot: dict, now: float, regrow_min: float, lps_now=None, at=None) -> dict:
    """What harvest memory says about each spot's trees, the input of its capacity
    (capacity_model). attempts: harvest_attempts rows (t, facet, x, y, z, outcome,
    amount) in time order; tiles_by_spot: spot_tree_tiles(); lps_now: today's logs per
    success; at: {spot id: [t, ...]} times to count the available trees at (its dry
    trips' starts). Returns {logs_per_tree, successes_per_tree, cycles, spots: {id:
    {trees, tried, yielded, out, avail_at}}}:
      - successes_per_tree: mean successes of a tree's completed cycles (its successes up
        to 'depleted'; cycles with logs only), pooled over every tree; SUCCESSES_PER_TREE
        until LOGS_PER_TREE_MIN_CYCLES cycles. A node holds successes; the logs of one
        grow with the Harvest aspect (OPTIMIZER.md F2), so logs_per_tree = it × lps_now
        (without lps_now: the cycles' mean logs, LOGS_PER_TREE until enough);
      - trees: the spot's tree tiles less those found not to be trees;
      - tried / yielded: of those, the ones attempted / that ever gave logs (per-tree
        attempts, from before Smart Harvest: since 2026-10-04 the runner records its
        attempts on the tile it stands on; dry trips carry the share since, capacity_model);
      - out: the ones the runner skips now, depleted, unreachable or marked
        `nothing_near` (in reach of a stand where the server said nothing nearby has
        wood) within the regrowth window (Memory.harvest_available). A mark alone
        isn't an attempt: it counts as out, not as tried;
      - avail_at: {t: trees less those out within the window before t} for `at`."""
    import bisect
    cur, cycles, tiles = {}, [], {}
    for t, facet, x, y, _z, outcome, amount in attempts:
        k = (facet, x, y)
        d = tiles.setdefault(k, {"tried": False, "yielded": False, "not_tree": False, "out_t": None, "outs": []})
        if outcome not in ("nothing_near", "no_harvest"):
            d["tried"] = True
        if outcome == "success":
            c = cur.setdefault(k, [0, 0])
            c[0] += amount or 0
            c[1] += 1
            d["yielded"] = True
        elif outcome == "depleted":
            got = cur.pop(k, None)
            if got and got[0] > 0:
                cycles.append(got)
            d["out_t"] = t
            d["outs"].append(t)
        elif outcome in ("unreachable", "nothing_near"):
            d["out_t"] = t
            d["outs"].append(t)
        elif outcome in ("not_tree", "no_harvest"):      # no_harvest: in a no-harvest area, never again
            d["not_tree"] = True
    window = regrow_min * 60.0
    at = at or {}
    out = {}
    for sid, (facet, spot_tiles) in tiles_by_spot.items():
        seen = [d for d in (tiles.get((facet, x, y)) for x, y in spot_tiles) if d is not None and not d["not_tree"]]
        bad = sum(1 for x, y in spot_tiles if (tiles.get((facet, x, y)) or {}).get("not_tree"))
        trees = len(spot_tiles) - bad
        out[sid] = {"trees": trees, "tried": sum(1 for d in seen if d["tried"]),
                    "yielded": sum(1 for d in seen if d["yielded"]),
                    "out": sum(1 for d in seen if d["out_t"] is not None and now - d["out_t"] < window),
                    "avail_at": {t: trees - sum(1 for d in seen if bisect.bisect_left(d["outs"], t)
                                                > bisect.bisect_left(d["outs"], t - window))
                                 for t in at.get(sid, ())}}
    enough = len(cycles) >= LOGS_PER_TREE_MIN_CYCLES
    spt = sum(n for _, n in cycles) / len(cycles) if enough else SUCCESSES_PER_TREE
    lpt = spt * lps_now if lps_now else (sum(g for g, _ in cycles) / len(cycles) if enough else LOGS_PER_TREE)
    return {"logs_per_tree": lpt, "successes_per_tree": spt, "cycles": len(cycles), "spots": out}


def capacity_model(info: dict | None, share0: float, dry=()) -> dict | None:
    """A spot's capacity parameters from its tree_yield entry (None: unknown, no
    cap): avail = the tree tiles the runner would try now, (a, b) = the Beta
    posterior of the share of tree tiles that give logs (prior: the pooled share
    share0, worth YIELD_PRIOR_TILES tiles), from the tiles tried before Smart Harvest
    and from the spot's dry trips (dry_obs: each a share = its successes / (successes
    per tree × the trees available when it started), worth DRY_OBS_TILES tiles at most,
    recency-weighted; OPTIMIZER.md F2). A sparse grove holds few logs per trip, and a
    grove where many mapped trees give nothing (other players' chopping, statics that
    aren't trees) holds fewer than its count says."""
    if info is None:
        return None
    a = YIELD_PRIOR_TILES * share0 + info["yielded"]
    b = YIELD_PRIOR_TILES * (1.0 - share0) + info["tried"] - info["yielded"]
    for n, share in dry:
        a += n * share
        b += n * (1.0 - share)
    return {"trees": info["trees"], "avail": max(0, info["trees"] - info["out"]), "a": a, "b": b,
            "dry_trips": len(dry)}


def dry_obs(spot: dict, trips: list, info: dict | None, spt: float, lps_now, now: float) -> list:
    """[(tiles, share)] of the spot's dry trips (the runner ran out of trees with wood) since
    its area last changed (its forest's built_t): tiles = min(available trees, DRY_OBS_TILES)
    × the trip's recency weight, share = its successes (else logs / lps_now) over successes
    per tree × the trees available when it started (info["avail_at"], tree_yield's `at`)."""
    if info is None:
        return []
    since = _num((spot.get("forest") or {}).get("built_t"), 0.0)
    out = []
    for tr in trips:
        avail = (info.get("avail_at") or {}).get(tr["t0"])
        if not tr["dry"] or tr["t0"] < since or not avail:
            continue
        succ = tr.get("successes")
        if succ is None:
            succ = tr["logs"] / lps_now if lps_now else tr["logs"] / LOGS_PER_SUCCESS
        out.append((min(avail, DRY_OBS_TILES) * weight(tr["t1"], now), min(1.0, succ / (spt * avail))))
    return out


def dry_times(spots: dict, episodes: list) -> dict:
    """{spot id: [t0, ...]} of the dry trips (a merged spot's are its forest's): the times
    tree_yield counts the available trees at."""
    alias = merged_alias(spots)
    out = {}
    for e in episodes:
        if e.get("dry") and _num(e.get("t_start")) is not None:
            sid = alias.get(e.get("spot"), e.get("spot"))
            if sid in spots:
                out.setdefault(sid, []).append(e["t_start"])
    return out


def capacity_logs(cap: dict | None, logs_per_tree: float, rng=None):
    """Logs the spot's trees hold for the next trip (avail × yielding share × logs
    per tree): the posterior mean, or one draw with rng; None without a model."""
    if cap is None:
        return None
    share = rng.betavariate(cap["a"], cap["b"]) if rng is not None else cap["a"] / (cap["a"] + cap["b"])
    return cap["avail"] * share * logs_per_tree


# ------------------------------------------------------------------ the model
def library_walk(landing: dict | None, home: dict | None) -> int:
    """Tiles walked on a trip out to reach the landing's rune: 0 for an own book's rune
    (recalled at home where we stand), else from where our own book's library_rune lands
    (home.library_rune: the recall into the library's house) to the stand of the library
    holding it (DTF: 4134,1429 -> 4152,1429, 18 tiles). A library rune without a
    library_rune is out of reach (landing_for never picks one): ValueError."""
    if landing is None or landing.get("source") != "library" or not home:
        return 0
    import home as homes
    import places
    rune = homes.library_rune(home)
    if not rune:
        raise ValueError(f"library rune {landing.get('name')!r}: the home has no library_rune to reach "
                         f"library {landing.get('library')!r}")
    return cheb(rune["landing"], places.library(landing["library"])["stand"])


def overhead_prior_s(spot: dict, landing: dict | None = None, home: dict | None = None) -> float:
    """Prior seconds of one trip's overhead, every trip starting and ending at home
    (docs/LUMBER_LOOP.md §6): for a library rune the recall into the library's house
    (home.library_rune, one more RECALL_TRIP_S) and the walk from its landing to the
    tome (library_walk), the recall out, the
    walk from the landing into the grove (its planned route when known, else the
    straight distance to a square's inner half, a forest's edge plus half a cell), the harvest lockout after the recall
    (LOCKOUT_S), the recall home on our book's home rune, the steps to the landing tile,
    convert and store (OVERHEAD_FIXED_S). Without a landing (no home
    known) the walk in is the spot's discovered route_tiles, else NO_LANDING_TILES."""
    into = _into_tiles(spot, landing)
    walk = library_walk(landing, home)
    recalls = 3 if home and landing is not None and landing.get("source") == "library" else 2
    return (walk + into) * SEC_PER_TILE + recalls * RECALL_TRIP_S + LOCKOUT_S + OVERHEAD_FIXED_S


def dispersion(trips_by_spot: dict, p_now, lps_now=None) -> float:
    """Quasi-Poisson φ: Pearson χ² / dof of per-trip logs around each spot's own
    rate, at least DISPERSION_MIN."""
    chi, n, k = 0.0, 0, 0
    for trips in trips_by_spot.values():
        use = [(tr["logs"], adjusted_field_h(tr, p_now, lps_now)) for tr in trips if tr["field_s"] >= 180]
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


def trip_terms(q, lam, t_h, hz, recovery_h=RECOVERY_H, load0=0.0, t_in=0.0) -> dict:
    """One trip cycle of q logs as a renewal-reward cycle (LUMBER_LOOP §6). Field
    rate lam (logs/h): chopping q logs takes tf = q/lam field hours, the load
    grows by lam per hour. hz = (h_D, h_S, h_T, f) per exposed hour, competing:
      - death (h_D): the trip ends and stores nothing; the cycle costs the field
        time so far + recovery_h;
      - sent home (h_S): the trip ends at τ and what we carry comes home;
      - theft (h_T): a thief takes the share f of what we carry; the trip goes
        on (the runner counts logs gained, so it still ends at q gained).
    With k = h_T·f the expected load at t is load0·e^(−k·t) + lam·(1 − e^(−k·t))/k
    (load0: logs carried in from an earlier spot of the same trip, a library hop),
    so with h = h_D + h_S:
      stored = e^(−h·tf)·C(tf) + h_S·∫₀^tf e^(−h·t)·C(t) dt
      time   = t_h + ∫₀^tf e^(−h·t) dt + recovery_h·P(death)
      P(death) = h_D·∫₀^tf e^(−h·t) dt   (≤ 1 for any q)
    t_in (hours, part of t_h): the walk from the landing and the lockout at the grove,
    exposed to death and to being sent home before the first chop (OPTIMIZER.md F3); the
    field phase happens with probability s = e^(−h·t_in), a death on the way loses load0
    and costs the recovery, a threat on the way brings load0 home.
    Returns stored (the logs that reach the chest at home), time_h, p_death, p_home
    and the expected logs lost to death (lost_death) and to thieves (lost_theft)."""
    hd, hs, ht, f = hz
    tf = q / lam
    h, k = hd + hs, ht * f
    stay = _e1(h, tf)                                   # E[field time] = E[min(τ, tf)]
    if k * tf < 1e-9:
        load_tf, load_int = q, lam * _e2(h, tf)         # ∫ e^(−ht)·lam·t dt
    else:
        load_tf, load_int = lam * _e1(k, tf), lam * (stay - _e1(h + k, tf)) / k
    if load0:                                           # the carried-in load: kept, stolen at k, lost at death
        load_tf += load0 * math.exp(-k * tf)
        load_int += load0 * _e1(h + k, tf)
    s = math.exp(-h * t_in)                             # reached the grove with nothing happening
    gone = 1.0 - s
    p_in_death = hd / h * gone if h > 0 else 0.0
    p_in_home = hs / h * gone if h > 0 else 0.0
    stored = s * (math.exp(-h * tf) * load_tf + hs * load_int) + p_in_home * load0
    p_death = p_in_death + s * hd * stay
    lost_death = p_in_death * load0 + s * hd * load_int
    return {"stored": stored, "time_h": t_h + s * stay + recovery_h * p_death, "p_death": p_death,
            "p_home": p_in_home + s * hs * stay, "lost_death": lost_death,
            "lost_theft": load0 + s * lam * stay - stored - lost_death}


def net_rate(q, lam, t_h, hz, gear_logs, cost_logs=0.0, recovery_h=RECOVERY_H, t_in=0.0) -> float:
    """Stored logs per hour (renewal-reward: E[stored] / E[cycle time], trip_terms)
    net of the supplies one trip uses (cost_logs: recall charges, reagents) and the
    gear a death loses (gear_logs × P(death)), all in logs at the board price."""
    tt = trip_terms(q, lam, t_h, hz, recovery_h, t_in=t_in)
    return (tt["stored"] - cost_logs - gear_logs * tt["p_death"]) / tt["time_h"]


def best_q(lam, t_h, hz, gear_logs, q_cap=Q_MAX, cost_logs=0.0, refine=True, recovery_h=RECOVERY_H,
           t_in=0.0) -> int:
    """Q* in Q_MIN…min(Q_MAX, q_cap): the best of Q_GRID (log-spaced, ~10 % apart)
    and the cap, then (refine) a golden-section search between its neighbours,
    rounded to 10 logs. Without refine (the Thompson draws) every other grid
    point (~20 % apart: the rate is flat near Q*). Below Q_MIN only when we
    can't carry more (q_cap)."""
    hi = min(Q_MAX, q_cap)
    if hi <= Q_MIN:
        return max(1, int(hi))

    def rate(q):
        return net_rate(q, lam, t_h, hz, gear_logs, cost_logs, recovery_h, t_in)
    grid = [q for q in (Q_GRID if refine else Q_GRID[::2]) if q < hi] + [int(hi)]
    vals = [rate(q) for q in grid]
    i = max(range(len(grid)), key=vals.__getitem__)
    if not refine or i == len(grid) - 1:
        return grid[i]
    a, b = float(grid[max(0, i - 1)]), float(grid[i + 1])
    g = (math.sqrt(5) - 1) / 2
    for _ in range(30):
        if b - a < 10:
            break
        c, d = b - g * (b - a), a + g * (b - a)
        if rate(c) >= rate(d):
            b = d
        else:
            a = c
    q = int(round((a + b) / 20.0)) * 10
    return max(Q_MIN, min(int(hi), q))


def rate_prior(by_spot: dict, p_now, now, phi=DISPERSION_MIN, lps_now=None) -> tuple:
    """(alpha0, beta0) of a Gamma prior on a spot's field rate: the mean of the measured
    spots' rates (each with ≥ PRIOR_MIN_FIELD_H weighted field hours) and their spread
    between spots, a random-effects moment fit (OPTIMIZER.md F4): the variance of the
    spot means less their mean sampling variance (quasi-Poisson: φ·rate / hours), at least
    (PRIOR_CV_MIN × mean)²; DEFAULT_RATE before any spot is measured."""
    means, samp = [], []
    for trips in by_spot.values():
        hrs = sum(weight(tr["t1"], now) * adjusted_field_h(tr, p_now, lps_now) for tr in trips)
        if hrs >= PRIOR_MIN_FIELD_H:
            r = sum(weight(tr["t1"], now) * tr["logs"] for tr in trips) / hrs
            means.append(r)
            samp.append(phi * r / hrs)
    mu = sum(means) / len(means) if means else DEFAULT_RATE
    var = sum((m - mu) ** 2 for m in means) / (len(means) - 1) if len(means) >= 2 else 0.0
    between = var - (sum(samp) / len(samp) if samp else 0.0)
    tau2 = max(between, (PRIOR_CV_MIN * mu) ** 2)
    return mu * mu / tau2, mu / tau2


def _into_tiles(spot: dict, landing: dict | None) -> float:
    """Tiles from the landing into the grove: its planned route when known, else the straight
    distance to a square's inner half, a forest's edge plus half a cell; without a landing the
    spot's discovered route_tiles, else NO_LANDING_TILES."""
    area = spot.get("area") or {}
    if landing is None or len(area.get("center") or ()) != 2:
        return _num(spot.get("route_tiles"), NO_LANDING_TILES)
    into = landing.get("route_tiles")
    if into is None and area.get("cells"):
        into = area_dist(area, (landing["x"], landing["y"])) + CELL // 2
    elif into is None:
        into = max(0, cheb((landing["x"], landing["y"]), area["center"]) - int(area.get("radius") or 0) // 2)
    return into


def spot_model(spot, trips, hev, pooled, prior, phi, p_now, now, landing=None, home=None, lps_now=None) -> dict:
    """Posterior parameters of one spot from its trips (trip_obs dicts), its
    hazard evidence hev (sightings {trip t0: n}, deaths [t], thefts [t], trips
    sent home {t0: "player"|"monster"}), the pooled hazard rates (plan) and the rate prior
    (rate_prior). Hazards are Gamma posteriors over recency-weighted exposed hours (field,
    lockout and the walk in from the landing: trip_obs `exposure_s`), each shrunk to its
    pooled rate: h_D's prior mean = sightings per hour × P(death | sighting) + the
    creature-death rate, worth DEATH_SHRINK_H hours; the two sent-home hazards (player and
    creature threats, OPTIMIZER.md F4) their pooled rates, HOME_SHRINK_H each; h_T's = the
    pooled rate, THEFT_SHRINK_H. The overhead's prior: overhead_prior_s with the spot's
    landing (landing_for) from home; the exposed time before the first chop (`pre_s`): the
    walk in at SEC_PER_TILE plus LOCKOUT_S, then the trips' walk_in_s + lockout_s."""
    w = [weight(tr["t1"], now) for tr in trips]
    logs = sum(wi * tr["logs"] for wi, tr in zip(w, trips))
    hrs = sum(wi * adjusted_field_h(tr, p_now, lps_now) for wi, tr in zip(w, trips))
    exposure = sum(wi * tr.get("exposure_s", tr["field_s"]) / 3600.0 for wi, tr in zip(w, trips))
    alpha = prior[0] + logs / phi
    beta = prior[1] + hrs / phi
    t0 = overhead_prior_s(spot, landing, home)
    obs = [(wi, tr["overhead_s"]) for wi, tr in zip(w, trips) if tr["overhead_s"] is not None]
    n = 1.0 + sum(wi for wi, _ in obs)
    mean = (t0 + sum(wi * o for wi, o in obs)) / n
    var = sum(wi * (o - mean) ** 2 for wi, o in obs) / n if obs else (0.5 * t0) ** 2
    sd = max(OVERHEAD_SD_MIN_S, math.sqrt(var)) / math.sqrt(n)
    pre = [(wi, tr["walk_in_s"] + tr["lockout_s"]) for wi, tr in zip(w, trips)
           if tr.get("exposure_s", 0.0) > tr["field_s"]]
    pre_s = ((_into_tiles(spot, landing) * SEC_PER_TILE + LOCKOUT_S + sum(wi * x for wi, x in pre))
             / (1.0 + sum(wi for wi, _ in pre)))
    pvp = bool(spot.get("pvp", True))
    sightings = hev["sight"]
    seen = sum(wi * sightings.get(tr["t0"], 0) for wi, tr in zip(w, trips))
    hp = _num(spot.get("hazard_prior"), 0.5) if pvp else 0.0
    if pvp and spot.get("faction_zone"):
        hp = max(hp, FACTION_ZONE_PRIOR)
    sight_a, sight_b = hp * HAZARD_PRIOR_H + seen, HAZARD_PRIOR_H + exposure
    home_p = sum(wi for wi, tr in zip(w, trips) if hev["home"].get(tr["t0"]) == "player")
    home_m = sum(wi for wi, tr in zip(w, trips) if hev["home"].get(tr["t0"]) == "monster")
    died = sum(weight(t, now) for t in hev["deaths"])
    stolen = sum(weight(t, now) for t in hev["thefts"])
    chop = sum(tr["chop_s"] for tr in trips if tr["field_s"] > 0)
    field = sum(tr["field_s"] for tr in trips if tr["field_s"] > 0)
    sup = [(wi, tr["supply_gp"]) for wi, tr in zip(w, trips) if tr.get("supply_gp") is not None]
    m = {"alpha": alpha, "beta": beta, "rate": alpha / beta,
         "overhead_s": mean, "overhead_sd_s": sd, "pre_s": min(pre_s, mean),
         "pvp": pvp, "sight_a": sight_a, "sight_b": sight_b,
         "died_w": died, "exposure": exposure,
         "home_p_a": HOME_SHRINK_H * pooled["home_p"] + home_p, "home_m_a": HOME_SHRINK_H * pooled["home_m"] + home_m,
         "home_b": HOME_SHRINK_H + exposure,
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


def hazards_base(m, pooled) -> dict:
    """The posterior-mean hazards per exposed hour before the time-of-day factor:
    {death, home_p, home_m, theft}."""
    a, b = _death_rate(m, m["sight_a"] / m["sight_b"], pooled["p_pk"], pooled["creature"])
    return {"death": a / b, "home_p": m["home_p_a"] / m["home_b"], "home_m": m["home_m_a"] / m["home_b"],
            "theft": m["theft_a"] / m["theft_b"]}


def hazards_mean(m, pooled) -> tuple:
    """(h_D, h_S, h_T, f): the posterior means, death and the two sent-home hazards
    scaled to the plan's time of day (pooled["tod"])."""
    hb, tod = hazards_base(m, pooled), pooled["tod"]
    return (hb["death"] * tod["death"], hb["home_p"] * tod["home_p"] + hb["home_m"] * tod["home_m"],
            hb["theft"], pooled["f"])


def _gamma(rng, a, b):
    return rng.gammavariate(a, 1.0 / b) if a > 0 else 0.0


def _draw(m, pooled, rng):
    """One posterior draw (lam, t_h, hz) of a spot."""
    tod = pooled["tod"]
    lam = rng.gammavariate(m["alpha"], 1.0 / m["beta"])
    t_h = max(10.0, rng.gauss(m["overhead_s"], m["overhead_sd_s"])) / 3600.0
    sight = _gamma(rng, m["sight_a"], m["sight_b"]) if m["pvp"] else 0.0
    hd = _gamma(rng, *_death_rate(m, sight, rng.betavariate(*pooled["death"]), pooled["creature"])) * tod["death"]
    hs = (_gamma(rng, m["home_p_a"], m["home_b"]) * tod["home_p"]
          + _gamma(rng, m["home_m_a"], m["home_b"]) * tod["home_m"])
    return lam, t_h, (hd, hs, _gamma(rng, m["theft_a"], m["theft_b"]), pooled["f"])


def _value(lam, t_h, hz, gear_logs, cost_logs=0.0, q_cap=Q_MAX, refine=False, cap_logs=None,
           recovery_h=RECOVERY_H, t_in=0.0):
    """(net logs/hour at a spot, Q*, capacity-bound). Every trip starts and ends at
    home, so no spot pays travel beyond its trip overhead t_h. cap_logs (what the
    spot's trees hold, capacity_logs) caps Q*; when it binds, the trip ends dry and
    the spot is out until its trees regrow. t_in: the exposed hours before the first
    chop (trip_terms)."""
    q = best_q(lam, t_h, hz, gear_logs, q_cap if cap_logs is None else min(q_cap, cap_logs), cost_logs,
               refine=refine, recovery_h=recovery_h, t_in=t_in)
    tt = trip_terms(q, lam, t_h, hz, recovery_h, t_in=t_in)
    v = (tt["stored"] - cost_logs - gear_logs * tt["p_death"]) / tt["time_h"]
    bound = cap_logs is not None and cap_logs < min(q_cap, Q_MAX) and q >= int(cap_logs)
    return v, q, bound


def no_go(facet, p) -> str | None:
    """Why (x, y) on `facet` lies in a NO_GO_AREAS area, or None."""
    return next((why for f, c, r, why in NO_GO_AREAS if f == (facet or 0) and cheb(p, c) <= r), None)


def eligibility(spot, trips, threats, now, regrow_min, thieves=()) -> str | None:
    """Why the spot can't be picked now, or None. thieves: [t] of `thief` job events there
    that made us leave (THIEF_COOLDOWN_S)."""
    if spot.get("status") != "active":
        return f"status {spot.get('status')}" + (f": {spot['reason']}" if spot.get("reason") else "")
    if (why := no_go(spot.get("facet"), (spot.get("area") or {}).get("center") or (0, 0))) is not None:
        return f"no-go area: {why}"
    try:
        check_spot(spot)
    except ValueError as e:
        return str(e)
    recent = [t for t in threats if now - t < COOLDOWN_S]
    if recent:
        return f"player threat or death here {int((now - max(recent)) / 60)} min ago (cooldown {int(COOLDOWN_S / 60)} min)"
    robbed = [t for t in thieves if now - t < THIEF_COOLDOWN_S]
    if robbed:
        return (f"a thief made us leave {int((now - max(robbed)) / 60)} min ago "
                f"(cooldown {int(THIEF_COOLDOWN_S / 60)} min)")
    dry = [tr["t1"] for tr in trips if tr["dry"]]
    last = sorted(trips, key=lambda tr: tr["t0"])[-UNWORKABLE_TRIPS:]
    if len(last) == UNWORKABLE_TRIPS and all(tr["place_fail"] for tr in last) \
            and now - last[-1]["t1"] < UNWORKABLE_DAYS * 86400:
        return (f"unworkable: the last {UNWORKABLE_TRIPS} trips got nothing ({last[-1]['why']}); "
                f"another try after {UNWORKABLE_DAYS:g} days")
    if dry and now - max(dry) < regrow_min * 60:
        return f"ran dry {int((now - max(dry)) / 60)} min ago (trees regrow in ~{int(regrow_min)} min)"
    return None


def hatchet_price(material: str, quality, table: dict, prices: dict, fallback: bool = False):
    """What replacing a hatchet costs: the prices table's `hatchet:<material>[:<quality>]`,
    else the NPC price of a plain one (hatchets.json), else None (unknown). fallback: a
    quality one without a price of its own costs at least its material's plain price (what
    losing one we own is worth; never what one we'd buy costs)."""
    key = f"hatchet:{material}" + (f":{quality}" if quality else "")
    if key in prices:
        return prices[key]["price_gp"]
    if quality is not None and fallback:
        return hatchet_price(material, None, table, prices)
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
                    "owned": True, "worn": h["worn"], "price_gp": hatchet_price(*key, table, prices, fallback=True)})
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
    by its name, isn't lost; a quality one without its own price at its material's),
    the reagents in the pack (`reagent:<name>`) and the Harvest-aspect armor suit worn on
    every lumber trip (SUIT_ITEM; user 2026-10-11). Blessed items (the rune tome) stay. A
    Young character loses nothing. Unpriced items count 0 and are listed in `unpriced`
    (the plan's `needs_prices`)."""
    out = {"gp": 0.0, "young": bool(young), "items": [], "unpriced": []}
    if young:
        return out
    if SUIT_ITEM in prices:
        out["gp"] += prices[SUIT_ITEM]["price_gp"]
        out["items"].append({"item": SUIT_ITEM, "n": 1, "gp": prices[SUIT_ITEM]["price_gp"]})
    else:
        out["unpriced"].append(SUIT_ITEM)
    for h in char.get("hatchets") or []:
        if h.get("newbied"):
            continue
        name = f"hatchet:{h['material']}" + (f":{h['quality']}" if h.get("quality") else "")
        p = hatchet_price(h["material"], h.get("quality"), table, prices, fallback=True)
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


def hatchet_value(opt, m, gear_gp, skill, p_ref, logs_per_success, gp_per_log, q_cap=Q_MAX,
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
    v, _, _ = _value(lam, m["overhead_s"] / 3600.0, m["hz"], gear / gp_per_log,
                     m.get("supply_gp", 0.0) / gp_per_log, q_cap, refine=True, cap_logs=m.get("cap_logs"),
                     recovery_h=m.get("recovery_h", RECOVERY_H), t_in=m.get("pre_s", 0.0) / 3600.0)
    wear = v / logs_per_success * (price or 0.0) / opt["uses"] / gp_per_log
    return v - wear


def breakeven_price(opt, m, gear_gp, skill, p_ref, lps, gpl, q_cap, target) -> float | None:
    """The highest price at which opt still nets `target` logs/hour (None: it
    doesn't even at price 0)."""
    def f(price):
        return hatchet_value(opt, m, gear_gp, skill, p_ref, lps, gpl, q_cap, price=price) - target
    if f(0.0) <= 0:
        return None
    lo, hi = 0.0, 1.0
    while f(hi) > 0 and hi < 1e8:
        hi *= 4
    for _ in range(50):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if f(mid) > 0 else (lo, mid)
    return round(lo)


def tod_block(t: float) -> int:
    """The UTC time-of-day block (TOD_BLOCK_H hours) of epoch t."""
    return int((t % 86400.0) // (TOD_BLOCK_H * 3600.0))


def tod_factors(trips: list, hev: dict, linked: list, now: float) -> dict:
    """Time-of-day factors of the pooled hazards (OPTIMIZER.md F3/F4): per UTC block, the
    recency-weighted events per exposed hour there over the overall rate, shrunk to 1 by
    TOD_PRIOR_EVENTS pseudo events: (k_b + C) / (r·E_b + C). Kinds: death (linked deaths,
    by when they fell), home_p / home_m (trips a player or a creature threat sent home, by
    their start). -> {block: now's block, block_h, factors: {kind: [factor per block]}}."""
    nb = 24 // TOD_BLOCK_H
    expo = [0.0] * nb
    k = {kind: [0.0] * nb for kind in ("death", "home_p", "home_m")}
    for tr in trips:
        w, b = weight(tr["t1"], now), tod_block(tr["t0"])
        expo[b] += w * tr["exposure_s"] / 3600.0
        kind = hev[tr["spot"]]["home"].get(tr["t0"])
        if kind:
            k["home_p" if kind == "player" else "home_m"][b] += w
    for d, _, _ in linked:
        k["death"][tod_block(d["t"])] += weight(d["t"], now)
    total = sum(expo)
    factors = {}
    for kind, kb in k.items():
        r = sum(kb) / total if total > 0 else 0.0
        factors[kind] = [round((kb[b] + TOD_PRIOR_EVENTS) / (r * expo[b] + TOD_PRIOR_EVENTS), 3) if r > 0 else 1.0
                         for b in range(nb)]
    return {"block": tod_block(now), "block_h": TOD_BLOCK_H, "factors": factors}


def recovery_h(linked: list, trips: list) -> dict:
    """Hours a death costs before work goes on (OPTIMIZER.md F3): from each linked death to
    the next lumber trip's start (gaps beyond RECOVERY_CAP_H are a session's end, left out),
    their mean shrunk to RECOVERY_H by RECOVERY_PRIOR_N deaths. -> {h, n, gaps_min}."""
    starts = sorted(tr["t0"] for tr in trips)
    gaps = []
    for d, _, _ in linked:
        nxt = next((t for t in starts if t > d["t"]), None)
        if nxt is not None and (nxt - d["t"]) / 3600.0 <= RECOVERY_CAP_H:
            gaps.append((nxt - d["t"]) / 3600.0)
    h = (RECOVERY_H * RECOVERY_PRIOR_N + sum(gaps)) / (RECOVERY_PRIOR_N + len(gaps))
    return {"h": h, "n": len(gaps), "gaps_min": [round(g * 60) for g in gaps]}


def plan(spots: dict, episodes: list, sightings: list, deaths: list, regrow: dict, char: dict | None,
         table: dict, prices: dict, now: float, rng: random.Random, stint_min: float = STINT_MIN,
         home: dict | None = None, landings: dict | None = None, who: str | None = None, at_home=None,
         logs_per_success: float = LOGS_PER_SUCCESS, gp_per_log: float = 9.5, draws: int = DRAWS,
         events: list = (), trees: dict | None = None, exploit: bool = False,
         carried: int | None = None, hop_from: str | None = None) -> dict:
    """The pure planner. episodes: lumber trip rows; sightings: [t] of pk_seen
    job events; deaths: [{t, x, y}] (world `death` events); regrow: regrowth();
    char: character() or None (Young by its name label: a death loses nothing);
    home: the character's home (home.for_character; None: ok false, no pick, the
    spots still ranked), who: its name, at_home: whether it is there now (shown);
    landings: {spot id: landing_for row or None} (an active spot without one can't be
    picked); prices: Memory.prices() (hatchets, reagents, the board price, supplies:
    supply_gp); events: lumber job events and theft_suspected junctures
    ({t, kind, data}: death causes, recall/guard_flight escapes, thefts);
    trees: tree_yield() (each spot's trees, what they give, which are out now);
    a spot without an entry has no capacity limit. exploit: the pick is the greedy spot
    (highest posterior-mean net logs/h) instead of the Thompson draw's; `greedy` is that
    spot's own run either way. carried (a runner in the middle of a trip, at home after a
    leg that ended early with that many logs): `hop` says whether to go out again to another
    spot from the home rune library instead of storing first (hop_choice)."""
    char = char or {}
    young = bool(char.get("young"))
    alias = merged_alias(spots)
    trips = []
    for tr in (trip_obs(e, prices) for e in episodes):  # a merged spot's trips are its forest's
        if tr is not None and alias.get(tr["spot"], tr["spot"]) in spots:
            trips.append({**tr, "spot": alias.get(tr["spot"], tr["spot"])})
    hatchets = char.get("hatchets") or []
    worn = hatchets[0] if hatchets else None
    skill = char.get("skill")
    if skill is None:
        known = [e for e in episodes if _num(e.get("skill")) is not None]
        skill = known[-1]["skill"] if known else None
    p_now = success_p(skill, worn["tool_bonus"] if worn else 0.0)
    lps_now = logs_per_success
    # the character's drift beyond skill and logs per success: each trip's hours to today's level
    drift = drift_levels(trips, p_now, lps_now, now)
    trips = [{**tr, "drift": drift_factor(drift, tr["t0"])} for tr in trips]
    by_spot = {sid: [tr for tr in trips if tr["spot"] == sid] for sid in spots}

    # hazard evidence: sightings, deaths (and their cause), trips sent home (by a player or a
    # creature threat), thefts
    hev = {sid: {"sight": {}, "home": {}, "deaths": [], "thefts": []} for sid in spots}
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
            hev[tr["spot"]]["home"][tr["t0"]] = tr["home_kind"]
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
        if tr["outcome"] in ("aborted", "hopped") and sight_by_trip.get(tr["t0"]):
            threats[tr["spot"]].append(tr["t1"])
    thefts = [(t, tr, share) for t, tr, share in theft_obs(events, trips) if tr is not None]
    for t, tr, _ in thefts:
        hev[tr["spot"]]["thefts"].append(t)
    seen_w = sum(weight(tr["t1"], now) * sight_by_trip.get(tr["t0"], 0) for tr in trips)
    expo = sum(weight(tr["t1"], now) * tr["exposure_s"] / 3600.0 for tr in trips)
    home_w = {kind: sum(weight(tr["t1"], now) for tr in trips if hev[tr["spot"]]["home"].get(tr["t0"]) == kind)
              for kind in ("player", "monster")}
    theft_w = sum(weight(t, now) for t, _, _ in thefts)
    death = (DEATH_PRIOR[0] + pk_w, DEATH_PRIOR[1] + max(0.0, seen_w - pk_w))
    tod = tod_factors(trips, hev, linked, now)
    pooled = {"death": death, "p_pk": death[0] / (death[0] + death[1]),
              "creature": (CREATURE_DEATH_PRIOR[0] * CREATURE_DEATH_PRIOR[1] + creature_w)
              / (CREATURE_DEATH_PRIOR[1] + expo),
              "home_p": (HOME_PLAYER_PRIOR[0] * HOME_PLAYER_PRIOR[1] + home_w["player"]) / (HOME_PLAYER_PRIOR[1] + expo),
              "home_m": (HOME_MONSTER_PRIOR[0] * HOME_MONSTER_PRIOR[1] + home_w["monster"])
              / (HOME_MONSTER_PRIOR[1] + expo),
              "theft": (THEFT_PRIOR[0] * THEFT_PRIOR[1] + theft_w) / (THEFT_PRIOR[1] + expo),
              "f": (THEFT_FRACTION_PRIOR[0] + sum(s for _, _, s in thefts))
              / (sum(THEFT_FRACTION_PRIOR) + len(thefts)),
              "tod": {kind: f[tod["block"]] for kind, f in tod["factors"].items()}}
    recovery = recovery_h(linked, trips)

    phi = dispersion(by_spot, p_now, lps_now)
    prior = rate_prior(by_spot, p_now, now, phi, lps_now)
    landings = landings or {}
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
    spt = trees.get("successes_per_tree", SUCCESSES_PER_TREE)
    dry = {sid: dry_obs(spots[sid], by_spot[sid], trees["spots"].get(sid), spt, lps_now, now) for sid in spots}
    infos = list(trees["spots"].values())
    share0 = ((sum(i["yielded"] for i in infos) + sum(n * s for d in dry.values() for n, s in d) + 1.0)
              / (sum(i["tried"] for i in infos) + sum(n for d in dry.values() for n, _ in d) + 2.0))

    models, rows = {}, []
    thieves = {sid: [e["t"] for e in events if e["kind"] == "thief"
                     and alias.get((e.get("data") or {}).get("spot"), (e.get("data") or {}).get("spot")) == sid
                     and (e.get("data") or {}).get("action") != "keep_away"] for sid in spots}
    for sid, s in spots.items():
        landing = landings.get(sid)
        m = spot_model(s, by_spot[sid], hev[sid], pooled, prior, phi, p_now, now, landing, home, lps_now)
        m["recovery_h"] = recovery["h"]
        t_in = m["pre_s"] / 3600.0
        why = eligibility(s, by_spot[sid], threats[sid], now, regrow["minutes"], thieves[sid])
        if why is None and home is not None and landing is None:
            why = ("no landing rune near the grove with a walking route into it "
                   "(the home rune library, our own books)")
        m["cost_logs"] = m["supply_gp"] / gp_per_log
        t_h = m["overhead_s"] / 3600.0
        m["cap"] = capacity_model(trees["spots"].get(sid), share0, dry[sid])
        m["cap_logs"] = capacity_logs(m["cap"], lpt)
        v, q, bound = _value(m["rate"], t_h, m["hz"], gear_logs, m["cost_logs"], q_cap, refine=True,
                             cap_logs=m["cap_logs"], recovery_h=recovery["h"], t_in=t_in)
        tt = trip_terms(q, m["rate"], t_h, m["hz"], recovery["h"], t_in=t_in)
        lo = gamma_quantile(m["alpha"], m["beta"], 0.1)
        hi = gamma_quantile(m["alpha"], m["beta"], 0.9)
        m.update(value=v, q=q, bound=bound, why=why, terms=tt)
        models[sid] = m
        hd, hs, ht, _ = m["hz"]
        hb = hazards_base(m, pooled)
        last = max((tr["t1"] for tr in by_spot[sid]), default=None)
        rows.append({"id": sid, "name": s.get("name"), "status": s.get("status"), "eligible": why is None,
                     "why_not": why, "trips": m["trips"], "field_h": m["field_h"], "logs": m["logs"],
                     "rate_logs_h": round(m["rate"]), "rate_80": [round(lo), round(hi)],
                     "overhead_s": round(m["overhead_s"]), "sightings": m["sightings"],
                     "sightings_per_h": round(m["sight_a"] / m["sight_b"], 2),
                     "faction_zone": (s.get("faction_zone") or {}).get("label"),
                     "deaths": len(hev[sid]["deaths"]), "deaths_per_h": round(hd, 3),
                     "sent_home": m["sent_home"], "sent_home_per_h": round(hs, 2),
                     "hz_base": {k: round(v_, 4) for k, v_ in hb.items()}, "pre_s": round(m["pre_s"]),
                     "thefts": m["thefts"], "thefts_per_h": round(ht, 3),
                     "p_death_trip": round(tt["p_death"], 3),
                     "loss_logs_trip": round(tt["lost_death"] + tt["lost_theft"] + gear_logs * tt["p_death"], 1),
                     "logs_per_trip": q, "net_logs_h": round(v),
                     "trees": None if m["cap"] is None else m["cap"]["trees"],
                     "trees_out": None if m["cap"] is None else m["cap"]["trees"] - m["cap"]["avail"],
                     "yield_share": None if m["cap"] is None else round(m["cap"]["a"] / (m["cap"]["a"] + m["cap"]["b"]), 2),
                     "dry_trips": None if m["cap"] is None else m["cap"]["dry_trips"],
                     "grove_logs": None if m["cap_logs"] is None else round(m["cap_logs"]),
                     "grove_bound": bound,
                     "supply_gp_trip": round(m["supply_gp"], 1), "supply_unpriced": m["supply_unpriced"],
                     "place_fails": m["place_fails"],
                     "landing": landing_view(landing),
                     "last_trip_h_ago": None if last is None else round((now - last) / 3600.0, 1)})

    eligible = [sid for sid in spots if models[sid]["why"] is None]
    out = {"ok": True, "now": now, "skill": skill, "success_p": None if p_now is None else round(p_now, 3),
           "home": None if home is None else {"character": who, "library": home.get("library"),
                                              "landing": home.get("landing"), "at_home": at_home},
           "regrow": regrow, "dispersion": round(phi, 1), "young": young,
           "death_given_sighting": round(pooled["p_pk"], 3),
           "creature_deaths_per_h": round(pooled["creature"], 4),
           "sent_home_pooled_per_h": {"player": round(pooled["home_p"], 3), "monster": round(pooled["home_m"], 3)},
           "thefts_pooled_per_h": round(pooled["theft"], 4),
           "theft_fraction": round(pooled["f"], 2), "theft_events": len(thefts),
           "time_of_day": tod, "recovery": {**recovery, "h": round(recovery["h"], 3)},
           "gear_at_risk": gear, "capacity_logs": None if q_cap == Q_MAX else q_cap,
           "needs_prices": [{"item": it, "how": f"ctl lumber price {it} <gp>"} for it in gear["unpriced"]],
           "logs_per_success": round(lps_now, 2), "logs_per_tree": round(lpt, 1),
           "drift": {"trips": drift["trips"], "spots": drift["spots"],
                     "level_by_day": {time.strftime("%Y-%m-%d", time.gmtime(d * 86400)): round(math.exp(g - drift["now"]), 3)
                                      for d, g in drift["g"].items()}},
           "successes_per_tree": round(spt, 2), "yield_share_pooled": round(share0, 2),
           "prior_rate_logs_h": round(prior[0] / prior[1]), "prior_cv": round(1 / math.sqrt(prior[0]), 2),
           "spots": sorted(rows, key=lambda r: (not r["eligible"], -r["net_logs_h"])), "pick": None}
    out["pouches"] = pouch_plan(char)
    if home is None:
        out["ok"] = False
        out["error"] = f"no home in harness/data/homes.json for {who or 'an unknown character (no proxy, no trip row)'}"
        return out
    if not eligible:
        out["ok"] = False
        out["error"] = "no eligible lumber spot (see spots[].why_not)"
        return out

    def value(sid):
        mm = models[sid]
        return _value(*_draw(mm, pooled, rng), gear_logs, mm["cost_logs"], q_cap,
                      cap_logs=capacity_logs(mm["cap"], lpt, rng), recovery_h=recovery["h"],
                      t_in=mm["pre_s"] / 3600.0)[0]

    wins = {sid: 0 for sid in eligible}
    for _ in range(draws):
        vals = {sid: value(sid) for sid in eligible}
        wins[max(vals, key=vals.get)] += 1
    for r in out["spots"]:
        r["p_best"] = round(wins.get(r["id"], 0) / draws, 3) if r["eligible"] else 0.0
    sample = {sid: value(sid) for sid in eligible}
    greedy = max(eligible, key=lambda sid: models[sid]["value"])
    pick = greedy if exploit else max(sample, key=sample.get)

    def run_for(sid) -> tuple[dict, dict]:
        """(hatchet_choice, the run view) of spot sid with its own trip arguments: its Q*, the
        trips filling the stint, the timeout and the hatchet for that spot's model."""
        m = models[sid]
        q = m["q"]
        trip_s = (q / m["rate"] + m["overhead_s"] / 3600.0) * 3600.0     # a full trip, nothing ending it early
        n = max(1, round(stint_min * 60.0 / trip_s))
        run_q = q
        if m["bound"]:
            # the grove holds less than Q*: one trip, told the uncapped Q* so it chops until
            # the trees run out rather than stopping at our estimate of what they hold
            n = 1
            run_q = best_q(m["rate"], m["overhead_s"] / 3600.0, m["hz"], gear_logs, q_cap, m["cost_logs"],
                           recovery_h=recovery["h"], t_in=m["pre_s"] / 3600.0)
        timeout = int(max(1800, 2 * n * (run_q / m["rate"] * 3600.0 + m["overhead_s"]) + 600))
        hat = hatchet_choice(char, table, prices, m, gear["gp"], skill, p_now, logs_per_success, gp_per_log, q_cap)
        args = ["--spot", sid, "--trips", str(n), "--logs-per-trip", str(run_q),
                "--regrow-min", f"{regrow['minutes']:g}", "--timeout", str(timeout)]
        if hat.get("use"):
            args += ["--hatchet", hat["use"]]
        tt = m["terms"]
        return hat, {"spot": sid, "p_best": round(wins[sid] / draws, 3),
                     "landing": landing_view(landings.get(sid)),
                     "logs_per_trip": q, "trips": n, "timeout_s": timeout,
                     "grove_logs": None if m["cap_logs"] is None else round(m["cap_logs"]),
                     "grove_bound": m["bound"],
                     "expected_trip_min": round(trip_s / 60.0, 1), "expected_net_logs_h": round(m["value"]),
                     "expected_stored_trip": round(tt["stored"]), "p_death_trip": round(tt["p_death"], 3),
                     "p_sent_home_trip": round(tt["p_home"], 3),
                     "args": args, "command": "ctl run lumber " + " ".join(args)}

    hat, run = run_for(pick)
    out["pick"] = {"spot": pick, "mode": "exploit" if pick == greedy else "explore",
                   "chosen_by": "greedy (--exploit)" if exploit else "thompson", "greedy": greedy, **run}
    # the greedy spot's own run (its quota, timeout, regrowth window and hatchet), ready to start when
    # the overseer has cause to exploit (the pick itself when it is the greedy one)
    out["greedy"] = run if greedy == pick else run_for(greedy)[1]
    out["hatchets"] = hat
    if carried is not None:
        others = [sid for sid in eligible if sid != hop_from]
        target = None if not others else (max(others, key=lambda s: models[s]["value"]) if exploit
                                          else max(others, key=sample.get))
        out["hop"], out["hop_why"] = hop_choice(models, target, gear_logs, q_cap, carried, regrow, landings,
                                                "exploit" if exploit else "thompson")
    return out


def leg_value(q, lam, t_h, hz, gear_logs, cost_logs, load, g, recovery_h=RECOVERY_H) -> tuple:
    """(logs, trip_terms) of a leg that chops q logs at a spot with `load` carried in, its
    overhead t_h (hours), against the long-run rate g (logs/h): what reaches the chest, net of
    supplies and the gear a death loses, minus g × the time it takes (the overhead, the leg, a
    death's recovery). Renewal-reward: of two ways to go on, the one with more reward − g·time
    wins (docs/LUMBER_LOOP.md §6 "Library hops"). The way out's exposure is hop_choice's."""
    tt = trip_terms(q, lam, t_h, hz, recovery_h, load0=load)
    return tt["stored"] - cost_logs - gear_logs * tt["p_death"] - g * tt["time_h"], tt


def hop_out_s(m) -> float:
    """A hop's way out to a spot: its trip overhead without convert, store and the last recall
    home (HOP_BACK_S has those), at least the lockout."""
    return max(LOCKOUT_S, m["overhead_s"] - OVERHEAD_FIXED_S - RECALL_TRIP_S)


def hop_choice(models, target, gear_logs, q_cap, carried, regrow, landings, mode) -> tuple:
    """(hop or None, why not) for a runner at home after a leg that ended early (dry, every tree
    guarded, a threat that recalled it) with `carried` logs (user decision 2026-10-07). The next
    spot is the one the plan would send a fresh trip to (`target`: its Thompson pick, else the
    greedy spot, the spot just left excluded); the question is only whether to carry the load
    along from the rune library or to store it first:
      - store first: carried − g·HOP_BACK_S now, then a fresh trip to the spot (its full overhead);
      - hop: one leg there with the load at risk (its death and theft hazards), the hop's way out
        (hop_out_s) and HOP_BACK_S at its end; it saves storing first.
    Each side at its best q by reward − g·time (leg_value), g = the target's own posterior-mean
    net rate (the rate a fresh trip there earns, so the fresh side is worth ~0 at its Q* and the
    hop leg's q stays near it). The heavier the load, the more a leg risks and the less it should
    chop, until storing first wins: that is the "less than the planner would risk" rule as one
    comparison. The way out (hop_out_s: the landing and the walk into the grove) is exposed too (user
    2026-10-08, after a death 28 s after a hop's landing lost 1028 carried logs): a death there (the
    spot's death hazard over that time) loses what is carried and the gear and costs the recovery, on
    both sides, so the difference is the carried load at risk (`carried_lost_logs`: expected)."""
    if target is None:
        return None, "no other eligible spot"
    room = min(Q_MAX, q_cap)
    if room < 1:
        return None, "no room in the pack for more logs"
    m = models[target]
    g = max(0.0, m["value"])
    out_h, back_h = hop_out_s(m) / 3600.0, HOP_BACK_S / 3600.0
    rec = m.get("recovery_h", RECOVERY_H)

    def best(t_h, load, hi):
        hi = max(1, int(hi))
        grid = [q for q in Q_GRID if q < hi] + [hi]
        return max(((q, *leg_value(q, m["rate"], t_h, m["hz"], gear_logs, m["cost_logs"], load, g, rec))
                    for q in grid), key=lambda x: x[1])
    cap = m["cap_logs"]
    p_out = -math.expm1(-m["hz"][0] * out_h)          # a death on the way out, before the first chop

    def exposed(val):
        return (1.0 - p_out) * val - p_out * (gear_logs + g * (out_h + rec))
    q, v, tt = best(out_h + back_h, carried, room if cap is None else min(room, cap))
    v = exposed(v)
    qf, vf, _ = best(m["overhead_s"] / 3600.0, 0, min(Q_MAX, q_cap + carried) if cap is None
                     else min(Q_MAX, q_cap + carried, cap))
    store_v = carried - g * back_h + exposed(vf)
    if v - store_v <= HOP_MIN_GAIN_LOGS:
        return None, (f"storing the {carried} logs first is worth more ({round(store_v)} vs {round(v)} logs "
                      f"for a hop to {target})")
    quota = q
    if cap is not None and q >= int(cap) and room > q:
        # the grove holds less than the best leg: chop until its trees really run out, told the leg's
        # uncapped Q* (as plan's run_for; reward − g·time at the cap-bound g would grow without end)
        quota = max(q, best_q(m["rate"], out_h + back_h, m["hz"], gear_logs, room, m["cost_logs"], recovery_h=rec))
    timeout = int(max(1800, 2 * (quota / m["rate"] * 3600.0 + hop_out_s(m) + HOP_BACK_S) + 600))
    return {"spot": target, "mode": mode, "logs_per_trip": quota, "carried": carried,
            "gain_logs": round(v - store_v, 1), "hop_value_logs": round(v, 1), "store_value_logs": round(store_v, 1),
            "fresh_logs_per_trip": qf, "rate_logs_h": round(g), "expected_stored": round(tt["stored"]),
            "p_death_leg": round(tt["p_death"], 3), "p_death_way_out": round(p_out, 4),
            "carried_lost_logs": round(carried * (p_out + (1.0 - p_out) * tt["p_death"]), 1),
            "hop_out_s": round(hop_out_s(m)), "timeout_s": timeout,
            "regrow_min": regrow["minutes"], "landing": landing_view((landings or {}).get(target))}, None


def hatchet_choice(char, table, prices, m, gear_gp, skill, p_now, lps, gpl, q_cap=Q_MAX) -> dict:
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
        v = hatchet_value(o, m, gear_gp, skill, p_now, lps, gpl, q_cap)
        rows.append({**o, "net_logs_h": round(v, 1), "priced": known})
    priced = [r for r in rows if r["priced"]]
    best = max(priced, key=lambda r: r["net_logs_h"]) if priced else None
    target = best["net_logs_h"] if best else 0.0
    for r in rows:
        r["breakeven_gp"] = None if r is best else breakeven_price(r, m, gear_gp, skill, p_now, lps, gpl,
                                                                    q_cap, target)
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
def lps_estimate(succ: list, now: float) -> float:
    """Today's logs per success from successes [(t, amount)] in time order, all before now
    (Harvest-aspect procs double some, and their share grows with the aspect: OPTIMIZER.md
    F1). The mean of the newest LPS_RECENT trails a growing aspect by hours, so it is
    projected to now along a least-squares line over the last LPS_TREND_H hours (at least
    LPS_TREND_MIN successes), moved at most LPS_PROJECT_MAX from that mean; a flat or
    falling trend leaves it be. LOGS_PER_SUCCESS with fewer than 20 successes."""
    recent = succ[-LPS_RECENT:]
    if len(recent) < 20:
        return LOGS_PER_SUCCESS
    mean = sum(a for _, a in recent) / len(recent)
    win = [(t, a) for t, a in succ if t >= now - LPS_TREND_H * 3600.0]
    if len(win) < LPS_TREND_MIN:
        return mean
    tm = sum(t for t, _ in win) / len(win)
    am = sum(a for _, a in win) / len(win)
    sxx = sum((t - tm) ** 2 for t, _ in win)
    if sxx <= 0:
        return mean
    slope = sum((t - tm) * (a - am) for t, a in win) / sxx
    if slope <= 0:
        return mean
    return max(mean, min(am + slope * (now - tm), mean * (1.0 + LPS_PROJECT_MAX)))


def logs_per_success(memory, now: float | None = None) -> float:
    """lps_estimate over the store's successes (the newest few thousand)."""
    now = time.time() if now is None else now
    rows = memory.con.execute("SELECT t, amount FROM harvest_attempts WHERE outcome='success' AND amount > 0 "
                              "AND t < ? ORDER BY t DESC LIMIT 20000", (now,)).fetchall()
    return lps_estimate(rows[::-1], now)


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
    stands = [e for e in events if e["kind"] == "stand"]
    return {"episodes": memory.episodes("lumber"),
            "sightings": [e["t"] for e in events if e["kind"] == "pk_seen" and e["data"].get("counted", True)],
            "deaths": deaths, "regrow": regrowth(attempts, stands), "attempts": attempts, "stands": stands,
            "prices": memory.prices(), "logs_per_success": logs_per_success(memory),
            "events": [e for e in events if e["kind"] in ("death", "recall", "guard_flight", "theft",
                                                          "theft_suspected", "thief")]}


_MAPS, _WALKS, _TILES = {}, {}, {}


def _umap(facet: int):
    """The facet's uomap.UoMap (cached), or None when its files can't be read."""
    import uomap
    if facet not in _MAPS:
        try:
            _MAPS[facet] = uomap.UoMap(facet)
        except (OSError, ValueError):
            _MAPS[facet] = None
    return _MAPS[facet]


def _walk(facet: int):
    """pathfind.Walk over the facet's map (cached), or None when its files can't be read."""
    import pathfind
    if facet not in _WALKS:
        um = _umap(facet)
        _WALKS[facet] = None if um is None else pathfind.Walk(um)
    return _WALKS[facet]


def spot_tree_tiles(spots: dict) -> dict:
    """{spot id: (facet, {(x, y)})}: the tree tiles the runner tries at each spot,
    its seed trees plus the map's tree statics in its area (as
    loop_lumber.candidate_trees). Spots on a facet whose map files can't be read
    are left out (no capacity limit). Map tiles are cached per area."""
    out = {}
    for sid, s in spots.items():
        area = s.get("area")
        if not area:
            continue
        facet = int(s.get("facet") or 0)
        cells = area_cells(area)
        key = (facet, tuple(area["center"]), area["radius"], cells)
        if key not in _TILES:
            um = _umap(facet)
            _TILES[key] = None if um is None else frozenset((t["x"], t["y"]) for t in area_trees(um, area))
        if _TILES[key] is not None:
            out[sid] = (facet, _TILES[key] | {(t["x"], t["y"]) for t in s.get("trees") or []})
    return out


def area_trees(um, area: dict, seeds=()) -> list:
    """The trees the runner works at a spot (loop_lumber.candidate_trees before harvest
    memory and ordering): the seed trees, then the map's tree statics in the area (its
    square, a forest's cells), one per tile (the first one wins).
    -> [{x, y, z, graphic: "0x....", seed}]"""
    (cx, cy), r = area["center"], area["radius"]
    cells = area_cells(area)
    found = [{"x": x, "y": y, "z": z, "graphic": f"0x{g:04X}"} for x, y, z, g in um.find_trees(cx - r, cy - r, cx + r, cy + r)
             if cells is None or (x // CELL, y // CELL) in cells]
    seen, out = set(), []
    for t, seed in [(t, True) for t in seeds] + [(t, False) for t in found]:
        if (t["x"], t["y"]) not in seen:
            seen.add((t["x"], t["y"]))
            out.append({**t, "seed": seed})
    return out


def spot_at(spots: dict, facet: int, x: int, y: int):
    """The spot whose area holds (x, y) on `facet` (the nearest centre when several do;
    spots merged into a forest left out), else None."""
    best = None
    for s in spots.values():
        area = s.get("area")
        if not area or int(s.get("facet") or 0) != facet or s.get("merged_into"):
            continue
        d = cheb((x, y), area["center"])
        if in_area(area, x, y) and (best is None or d < best[0]):
            best = (d, s)
    return None if best is None else best[1]


GROVE_MARGIN = 10                 # tiles around a spot's area the grove view also shows (trees the runner doesn't try)


def grove_view(memory, spot: dict, now: float, margin: int = GROVE_MARGIN) -> dict:
    """A spot's trees as the runner sees them, for the visualizer's map: its area (a
    forest's with its `cells`), and every tree-named static within `margin` tiles of the area with
      - kind "tree" (uomap.find_trees: a tree the runner tries when inside the area) or
        "excluded" (why: passable / unchoppable / potted / stump), one per tile, trees first;
      - inside: within the area (the runner's candidates);
      - state for trees, from harvest memory as Memory.harvest_available reads it over the
        regrowth window of the plan (regrowth() over harvest_attempts, as `ctl lumber plan`
        passes it): "ready", "not_tree" (the server said so), "depleted" (depleted or a
        stand's nothing-nearby within the window) or "unreachable" (within the window), with
        `since` and `until` (when the window ends) for the last two.
    None when the facet's map files can't be read."""
    um = _umap(int(spot.get("facet") or 0))
    if um is None:
        return None
    facet = int(spot.get("facet") or 0)
    area = spot["area"]
    (cx, cy), r = area["center"], area["radius"]
    cells = area_cells(area)
    attempts = memory.con.execute("SELECT t, facet, x, y, z, outcome FROM harvest_attempts ORDER BY t").fetchall()
    regrow = regrowth(attempts, [e for e in memory.job_events("lumber") if e["kind"] == "stand"])
    window = regrow["minutes"] * 60.0
    R = r + margin
    nodes = {(x, y, z): (dep, unr, bool(nt)) for x, y, z, dep, unr, nt in memory.con.execute(
        "SELECT x, y, z, depleted_at, unreachable_at, not_tree FROM harvest_nodes WHERE facet=? "
        "AND x BETWEEN ? AND ? AND y BETWEEN ? AND ?", (facet, cx - R, cx + R, cy - R, cy + R))}
    td = um.tiledata
    trees = [t for t in area_trees(um, {"center": [cx, cy], "radius": R}, spot.get("trees") or [])
             if cells is None or area_dist(area, (t["x"], t["y"]), cells) <= margin]
    out, seen = [], set()
    for t in trees:
        seen.add((t["x"], t["y"]))
        dep, unr, not_tree = nodes.get((t["x"], t["y"], t["z"]), (None, None, False))
        row = {**t, "kind": "tree", "inside": in_area(area, t["x"], t["y"], cells), "state": "ready"}
        g = t.get("graphic")
        it = td.item(int(g, 16) if isinstance(g, str) else g) if g is not None else None
        row["name"] = it.name if it else None
        if not_tree:
            row["state"] = "not_tree"
        else:
            for state, since in (("depleted", dep), ("unreachable", unr)):
                if since is not None and now - since < window:
                    row.update(state=state, since=round(since, 1), until=round(since + window, 1))
                    break
        out.append(row)
    for x, y, z, g, why in um.tree_statics(cx - R, cy - R, cx + R, cy + R):
        if why is None or (x, y) in seen or (cells is not None and area_dist(area, (x, y), cells) > margin):
            continue
        seen.add((x, y))
        it = td.item(g)
        out.append({"x": x, "y": y, "z": z, "graphic": f"0x{g:04X}", "seed": False, "kind": "excluded", "why": why,
                    "inside": in_area(area, x, y, cells), "name": it.name if it else None})
    inside = [t for t in out if t["kind"] == "tree" and t["inside"]]
    counts = {"trees": len(inside), "excluded": sum(1 for t in out if t["kind"] == "excluded" and t["inside"]),
              "outside": sum(1 for t in out if t["kind"] == "tree" and not t["inside"])}
    for state in ("ready", "depleted", "unreachable", "not_tree"):
        counts[state] = sum(1 for t in inside if t["state"] == state)
    view_area = {"center": [cx, cy], "radius": r} | ({"cells": sorted([int(c[0]), int(c[1])] for c in cells),
                                                      "cell": CELL} if cells is not None else {})
    return {"spot": {k: spot.get(k) for k in ("id", "name", "facet", "status", "pvp")} | {"area": view_area},
            "regrow_min": regrow["minutes"], "regrow_fitted": regrow["fitted"], "margin": margin, "now": now,
            "counts": counts, "trees": out}


def spot_landings(memory, spots: dict, home: dict, books=(), route_check: bool = True) -> dict:
    """{spot id: landing_for row or None} with `route_tiles` (the planned walk into the
    grove, None: not planned) and `route_checked`. Active spots get the route check: cached
    routes (memory meta ROUTES_META) answer at once, unknown ones are planned on the map
    (make_route_fn, the runner's planning budget), at most ROUTE_TRIES per spot a plan,
    and cached. route_check False (the dashboard) or no map: cached answers only, an
    unplanned landing taken unchecked. Other spots: the nearest landing, unchecked."""
    routes, new, oks, out = landing_routes(memory), {}, {}, {}
    bad = bad_landings(memory)
    for sid, s in spots.items():
        try:
            check_spot(s)
        except ValueError:
            out[sid] = None
            continue
        route_ok = None
        if s.get("status") == "active":
            facet = int(s.get("facet") or 0)
            if facet not in oks:
                walk = _walk(facet) if route_check else None
                oks[facet] = make_route_ok(None if walk is None else make_route_fn(walk), routes, new)
            route_ok = oks[facet]
        row = landing_for(s, home, books, route_ok, bad)
        if row is not None:
            key = route_key(row, s)
            row = {**row, "route_tiles": routes.get(key), "route_checked": key in routes}
        out[sid] = row
    save_landing_routes(memory, new)
    return out


def pouch_plan(char: dict | None) -> dict:
    """The trapped pouches the run carries (docs/PLAN.md "Keep thieves off the logs"): each
    trip keeps its logs in one and uses it up (the runner sets it off at home to
    convert), so carry pouch.CARRY; `buy` = how many to buy at a provisioner first. The runner
    starts no trip without a live one (`low_supplies`). Unknown without the proxy."""
    import pouch
    have = (char or {}).get("pouches")
    out = {"carry": pouch.CARRY, "per_trip": 1, "live": None, "spent": None, "buy": None,
           "how": "ctl act buy <provisioner serial> trapped pouch --amount N (Errol the provisioner: 25 gp)"}
    if have:
        out.update(live=have["live"], spent=have["spent"], buy=max(0, pouch.CARRY - have["live"]))
    return out


def plan_from_store(memory, world: dict | None, self_serial, pos, facet, stint_min=STINT_MIN, seed=None,
                    seeds_path=SEEDS, now=None, route_check: bool = True, exploit: bool = False,
                    carried: int | None = None, hop_from: str | None = None, home: dict | None = None) -> dict:
    """plan() over the memory store and a state-port snapshot (world None: no proxy).
    The character is the proxy's (world self name), else the newest trip row's; its
    home comes from harness/data/homes.json (home.for_character; `home` overrides: the
    runner's own), its own books from the store (places.known_books), each spot's landing
    from spot_landings. carried/hop_from: the runner's library hop question (plan, hop_choice)."""
    import home as homes
    import places
    now = time.time() if now is None else now
    spots = load_spots(memory, seeds_path)
    table = load_hatchets()
    inp = store_inputs(memory)
    char = character(world, self_serial, table) if world is not None and self_serial is not None else None
    who = (char or {}).get("name") or next(
        (e["character"]["name"] for e in reversed(inp["episodes"])
         if isinstance(e.get("character"), dict) and e["character"].get("name")), None)
    home = homes.for_character(who) if home is None else home
    landings = None
    if home is not None:
        landings = spot_landings(memory, spots, home, places.known_books(memory, who), route_check)
    out = plan(spots, inp["episodes"], inp["sightings"], inp["deaths"], inp["regrow"], char, table,
               inp["prices"], now, random.Random(seed), stint_min=stint_min, home=home, landings=landings,
               who=who, at_home=None if home is None or facet is None else homes.at_home(pos, facet, home),
               logs_per_success=inp["logs_per_success"], gp_per_log=gp_per_log(inp["prices"]),
               events=inp["events"], exploit=exploit, carried=carried, hop_from=hop_from,
               trees=tree_yield(inp["attempts"], spot_tree_tiles(spots), now, inp["regrow"]["minutes"],
                                inp["logs_per_success"], dry_times(spots, inp["episodes"])))
    out["character"] = None if char is None else {k: char[k] for k in ("name", "serial", "skill", "mounted", "buffs",
                                                                       "weight", "weight_max", "young", "pouches")}
    return out


def audit(memory, days: int = 7, now: float | None = None, seeds_path: str = SEEDS) -> dict:
    """Prequential calibration of the plan (OPTIMIZER.md §1 and recommendation 6): for each of
    the last `days` UTC days with trips, `plan` on the store as it was at 00:00 UTC that day
    (trips ended, events, harvest memory, prices before it; the spots as they are now), scored
    on that day's trips:
      - logs: actual / predicted, predicted = each trip's field hours × its spot's posterior
        rate (today's character as of the cut);
      - sent home: predicted Σ (h_player + h_creature, each × its time-of-day factor) ×
        exposed hours, against the trips a threat ended;
      - deaths: predicted Σ h_D × its time-of-day factor × exposed hours, against the deaths
        attribute_deaths links to the day's trips;
      - pick/greedy: median posterior-mean value of the plans' picks over their greedy spot
        (the `lumber plan` records in chat that day), what exploring cost by the model's own view;
      - lps: logs per success at the cut and realized that day.
    Plus the totals and P(deaths ≥ actual) under the predicted Poisson mean."""
    now = time.time() if now is None else now
    spots = load_spots(memory, seeds_path)
    alias = merged_alias(spots)
    table = load_hatchets()
    inp = store_inputs(memory)
    tiles = spot_tree_tiles(spots)
    gpl = gp_per_log(inp["prices"])
    trips = []
    for tr in (trip_obs(e, inp["prices"]) for e in inp["episodes"]):
        if tr is not None and alias.get(tr["spot"], tr["spot"]) in spots:
            trips.append({**tr, "spot": alias.get(tr["spot"], tr["spot"])})
    succ = [(t, a) for t, _f, _x, _y, _z, o, a in inp["attempts"] if o == "success" and a]
    escapes = [e["t"] for e in inp["events"] if e["kind"] in ("recall", "guard_flight")]
    plans = []
    for t, data in memory.con.execute("SELECT t, data FROM chat WHERE kind='action' AND data LIKE '%lumber plan%'"):
        d = json.loads(data) if data else {}
        p, g = d.get("pick") or {}, d.get("greedy") or {}
        if d.get("cmd") == "lumber plan" and p.get("expected_net_logs_h") is not None and g.get("expected_net_logs_h"):
            plans.append((t, p["expected_net_logs_h"] / g["expected_net_logs_h"]))
    day0 = math.floor(now / 86400.0) * 86400.0
    rows = []
    for k in range(days - 1, -1, -1):
        cut = day0 - k * 86400.0
        day = [tr for tr in trips if cut <= tr["t0"] < cut + 86400.0]
        if not day:
            continue
        lps = lps_estimate([s for s in succ if s[0] < cut], cut)
        eps = [e for e in inp["episodes"] if (_num(e.get("t_end")) or 0) < cut]
        att = [a for a in inp["attempts"] if a[0] < cut]
        rg = regrowth(att, [e for e in inp["stands"] if e["t"] < cut])
        p = plan(spots, eps, [t for t in inp["sightings"] if t < cut], [d for d in inp["deaths"] if d["t"] < cut],
                 rg, None, table, {k_: v for k_, v in inp["prices"].items() if v["t"] < cut}, cut, random.Random(0),
                 events=[e for e in inp["events"] if e["t"] < cut], draws=1, gp_per_log=gpl, logs_per_success=lps,
                 trees=tree_yield(att, tiles, cut, rg["minutes"], lps, dry_times(spots, eps)))
        by = {r["id"]: r for r in p["spots"]}
        f = p["time_of_day"]["factors"]
        fh = pred_logs = act_logs = pred_home = pred_death = 0.0
        act_home = 0
        for tr in day:
            r = by[tr["spot"]]
            b, eh = tod_block(tr["t0"]), tr["exposure_s"] / 3600.0
            hb = r["hz_base"]
            fh += tr["field_s"] / 3600.0
            pred_logs += r["rate_logs_h"] * tr["field_s"] / 3600.0
            act_logs += tr["logs"]
            pred_home += (hb["home_p"] * f["home_p"][b] + hb["home_m"] * f["home_m"][b]) * eh
            pred_death += hb["death"] * f["death"][b] * eh
            act_home += 1 if tr["sent_home"] or any(tr["t0"] <= t <= tr["t1"] + 5 for t in escapes) else 0
        died = attribute_deaths(day, [d for d in inp["deaths"] if cut <= d["t"] < cut + 86400.0], spots)
        ratios = sorted(x for t, x in plans if cut <= t < cut + 86400.0)
        day_lps = [tr["lps"] for tr in day if tr["lps"]]
        rows.append({"day": time.strftime("%Y-%m-%d", time.gmtime(cut)), "trips": len(day), "field_h": round(fh, 1),
                     "logs_actual_over_predicted": round(act_logs / pred_logs, 3) if pred_logs else None,
                     "sent_home": {"predicted": round(pred_home, 1), "actual": act_home},
                     "deaths": {"predicted": round(pred_death, 2), "actual": len(died)},
                     "pick_over_greedy": round(ratios[len(ratios) // 2], 2) if ratios else None, "plans": len(ratios),
                     "lps": {"at_cut": round(lps, 2),
                             "day": round(sum(day_lps) / len(day_lps), 2) if day_lps else None},
                     "prior_rate_logs_h": p["prior_rate_logs_h"], "dispersion": p["dispersion"],
                     "regrow_min": rg["minutes"]})
    tot_pred = sum(r["deaths"]["predicted"] for r in rows)
    tot_act = sum(r["deaths"]["actual"] for r in rows)
    tail = 1.0 - sum(math.exp(-tot_pred) * tot_pred ** i / math.factorial(i) for i in range(tot_act)) \
        if tot_pred > 0 else None
    return {"ok": True, "days": rows,
            "total": {"field_h": round(sum(r["field_h"] for r in rows), 1),
                      "sent_home": {"predicted": round(sum(r["sent_home"]["predicted"] for r in rows), 1),
                                    "actual": sum(r["sent_home"]["actual"] for r in rows)},
                      "deaths": {"predicted": round(tot_pred, 2), "actual": tot_act,
                                 "p_at_least_actual": None if tail is None else round(tail, 3)}}}


# ------------------------------------------------------------------ discovering spots
SLUG = re.compile(r"[^a-z0-9]+")
TOWN_RADIUS = 50                   # tiles from a township marker: likely inside the town region (no harvesting)
RUNE_SEARCH = 200                  # tiles from a Witcher rune a grove's window centre may lie (user 2026-10-03)
MAX_RUNE_ROUTE = 300               # tiles: the longest walk from a rune's landing into its grove
WINDOW_GRID = 7                    # window centres lie on this global lattice (one grove, one score)
ROUTE_TRIES = 3                    # route plans that may fail per rune (discover) or per spot (a plan's landings)


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


def discover_witcher(trees_fn, runes, spots, *, library: str = "cambria", radius=14,
                     search=RUNE_SEARCH, min_trees=25, max_route=MAX_RUNE_ROUTE, route_fn=None,
                     towns=(), guard_points=()) -> tuple:
    """Candidate spots reached by Witcher runes (places.witcher()["runes"]), at most
    one per rune. Every window with at least min_trees trees whose centre lies within
    `search` tiles of an eligible rune (grove_windows) is ranked globally, most
    trees first, then nearest to its rune; down that list a window is taken for its
    rune unless the rune already has one, it overlaps a known spot or a window taken
    before, it lies in or by a town or a learned guard point, or the walking route
    from the rune's tile into it (route_fn) is missing or longer than max_route
    (after ROUTE_TRIES such failures the rune is given up). So a grove two runes
    reach goes to the nearer one, and a rune whose best grove is taken gets its
    next best. Runes that already have a spot (`witcher_<id>`) are left out (a rune's
    name says nothing about danger: user 2026-10-19). A candidate is an area like any spot:
    trips reach it from home by the landing nearest it (landing_for), which need not be
    this rune; route_tiles is the walk from this rune's landing. Returns (candidates,
    {reason: count} of the runes left out)."""
    import numpy as np
    skipped = {}

    def skip(why):
        skipped[why] = skipped.get(why, 0) + 1
    eligible = []
    for r in runes:
        if r.get("x") is None:
            skip("no coordinates")
        elif f"witcher_{r['id']}" in spots:
            skip("already a spot")
        else:
            eligible.append(r)
    if not eligible:
        return [], skipped
    reach = search + radius
    xs, ys = [r["x"] for r in eligible], [r["y"] for r in eligible]
    pts = np.array([(tx, ty) for tx, ty, _z, _g in trees_fn(min(xs) - reach, min(ys) - reach,
                                                            max(xs) + reach, max(ys) + reach)],
                   dtype=np.int64).reshape(-1, 2)
    options = []                                     # (-trees, tiles from the rune, rune index, cx, cy)
    for i, r in enumerate(eligible):
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
        if key in taken or key in townish or no_go(0, (cx, cy)) is not None:
            hits.setdefault(i, set()).add("taken" if key in taken else "town" if key in townish else "no-go")
            continue
        r = eligible[i]
        route = route_fn((r["x"], r["y"]), {"center": [cx, cy], "radius": radius}) if route_fn is not None else None
        if route_fn is not None and (route is None or route > max_route):
            failed[i] = failed.get(i, 0) + 1
            continue
        n = -neg_n
        out.append({"id": f"witcher_{r['id']}", "name": f"Witcher {r['id']}: {r['name']} ({n} trees, {d} tiles off)",
                    "facet": 0, "area": {"center": [cx, cy], "radius": radius,
                                         "note": f"{n} tree statics {d} tiles from Witcher rune {r['id']} "
                                                 f"({r['x']},{r['y']}, {library} library)"},
                    "trees": [], "pvp": True, "hazard_prior": 0.5,
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
        elif "town" in hits.get(i, ()):
            skip("in or by a town (no harvesting there)")
        else:
            skip("in a no-go area (NO_GO_AREAS)")
    return out, skipped


def make_route_fn(walk):
    """route_fn for discover_witcher and the planner's landing check (make_route_ok):
    the planned walking route (pathfind.plan on the map) from a start tile into an area
    (a square's inner half, any cell of a forest), as its length in tiles, or None. It
    plans with the runner's search budget (pathfind.plan's default), so a route found
    here is one the runner's Mover can plan too."""
    import nav
    import pathfind

    def route(start, area):
        objs = walk.objects(start[0], start[1])
        z = next((o[0] + o[1] for o in objs if o[4][0] in ("flat", "item")), 0) if objs else 0
        cells = area_cells(area)
        goal = nav.within(tuple(area["center"]), max(1, int(area["radius"]) // 2)) if cells is None \
            else nav.in_cells(cells, CELL)
        path = pathfind.plan(walk, (start[0], start[1], z), goal)
        return None if path is None else len(path) - 1
    return route

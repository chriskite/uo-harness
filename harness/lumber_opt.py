"""Self-optimizing lumber job (docs/LUMBER_LOOP.md §6): where to chop, how much
to carry home, and which hatchet to use, learned from the memory store.

The objective is stored logs per hour of agent time (what reaches the rental
room's chest at home), net of expected PK losses (logs/hour is the proxy for
gold/hour until colored-wood prices are known; user decision 2026-10-02). The
overseer asks `ctl lumber plan` before each lumber run and gets one runner
command back.

Model (one spot = a tree area, `load_spots`; every trip starts and ends at the
character's home, harness/home.py, user decision 2026-10-04):
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
  - overhead T: room exit + walk to the landing's rune + recall out + walk into
    the grove + lockout + recall home + into the room + convert + store, per
    trip. Normal posterior; prior from the spot's landing (landing_for: the rune
    landing nearest the grove, from our libraries and own books).
  - hazards per field hour, three kinds competing while we chop (trip_terms):
    death h_D (PK or creature; the trip stores nothing, the carried logs and
    every unblessed item we carry are lost, RECOVERY_H), sent home h_S (a
    threat ended the trip early: recall escape, guard flight, a creature stop;
    the carried logs come home) and theft h_T (a thief takes a fraction f of
    what we carry; the trip goes on). Each is a Gamma posterior per spot shrunk
    to a pooled rate; h_D's prior is the spot's hostile-player sightings per
    field hour × P(death | sighting) plus the pooled creature-death rate.
  - trip size Q*: maximises the renewal-reward rate E[stored logs per trip
    cycle] / E[cycle time], minus supplies and the expected gear loss in logs,
    over Q_MIN…Q_MAX and what we can still carry (LUMBER_LOOP §6: Q* ≈
    λ·sqrt(2T/h) for a small death hazard alone).
  - choice: Thompson sampling. One posterior draw per eligible spot, the best
    draw wins (no spot pays travel beyond its overhead: all start at home).
    That keeps exploring the uncertain spots while the evidence favours the good
    ones, and varies the routine (ANTICHEAT.md §8.3).
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
OVERHEAD_FIXED_S = 40.0           # convert in the room + store in the chest + human pauses (live 10-20 s + pauses)
ROOM_EXIT_S = 10.0                # the room door's menu, "Exit to House Steward", the teleport [INFERENCE]
ROOM_ENTER_S = 15.0               # landing -> house steward, Visit Other Rooms, the owner's row [INFERENCE]
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
HOME_PRIOR = (0.5, 2.0)           # pooled trips sent home per field hour, pseudo field hours [INFERENCE]
HOME_SHRINK_H = 2.0               # field hours a spot's sent-home prior counts for
THEFT_PRIOR = (0.02, 20.0)        # pooled thefts per field hour, pseudo field hours [INFERENCE]
THEFT_SHRINK_H = 20.0             # thefts are rare: a spot's rate leans on the pooled one
THEFT_FRACTION_PRIOR = (1.0, 1.0)  # Beta prior of the share of the carried logs one theft takes (mean 0.5) [INFERENCE]
CAUSE_LINK_S = 300.0              # a sighting this soon before a death makes it a PK death
RECOVERY_H = 20 / 60              # ghost walk, resurrection, re-equip, back to work [INFERENCE: Terran death 2026-10-02]
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
    library at home (home.libraries: trips start at home and
    reach its tomes on foot) and the character's own books (places.known_books), dangerous
    landings left out, and those in `bad` (landing_key: a recall to them landed elsewhere,
    bad_landings), for which route_ok(row, spot) holds (a walking route from the
    landing into the grove; None: no check). None when no landing qualifies."""
    import home as homes
    import places
    area = spot["area"]
    (cx, cy) = area["center"]
    facet = int(spot.get("facet") or 0)
    rows = places.landings(cx, cy, facet, libraries=homes.libraries(home), books=books)
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
    sid = names.index(name) if name in names else LUMBERJACKING_ID
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
    boards went into the rental room's chest) has an overhead: the walk out (room exit,
    library, recall, walk into the grove), the lockout, the way into the room
    (`to_room`: recall home, the house steward), convert and store. Legacy rows of the
    bank era (outcome "banked", or none: the oldest rows) keep their form (walk out,
    lockout, convert, to_bank, store) as history. Rows written before 2026-10-02 lack
    walk_out_s/chop_s/skill: the walk out is taken to equal the walk to the bank, chops
    cost DEFAULT_CHOP_S each, and their chopping isn't rescaled. Field time excludes
    the travel lockout waited out at the first tree (`lockout_s`, since 2026-10-03;
    it's overhead); a row whose walk out never ended (walk_out_s null: no chop, e.g.
    the recall failed) has no field time. `sent_home`: a threat ended the trip early
    without killing us (a "threat: …" abort: recall escape, guard flight, a creature
    or damage stop; or the row's `creature` says it recalled); plan() adds trips with
    a recall/guard_flight event."""
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
    chop_s = _num(ep.get("chop_s"))
    if chop_s is None:
        chop_s = _num(ep.get("attempts"), 0) * DEFAULT_CHOP_S
    chop_s = min(chop_s, field_s)
    back = "to_room" if stored else "to_bank" if banked else None
    overhead = (walk_out + lockout + ph.get(back, 0.0) + ph.get("convert", 0.0) + ph.get("store", 0.0)
                if back else None)
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
    runner stopped), at most CURRENT_SPOT_MARGIN from that spot's
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
            if s is None or (pos is not None and s.get("area")
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


def regrowth(rows) -> dict:
    """When a depleted tree has wood again, from harvest_attempts rows
    (t, facet, x, y, z, outcome) in time order: every depleted attempt followed
    by another attempt on the same tree is a (gap, regrown) pair (a fail or a
    success means wood again). P(regrown | gap) is fitted isotonic
    (pool-adjacent-violators); the estimate is the smallest gap where it
    reaches REGROW_P, rounded up to 5 min. REGROW_DEFAULT_MIN with too few
    pairs or when it never does. Smart Harvest's `nothing_near` marks make no
    pairs: the server never says which tree regrew, so marks alone would only
    ever add 'not regrown' (since 2026-10-04 the fit rests on per-tree data)."""
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
      - tried / yielded: of those, the ones attempted / that ever gave logs (per-tree
        attempts, from before Smart Harvest: since 2026-10-04 the runner records its
        attempts on the tile it stands on, which isn't a tree tile, so new data adds
        no tried/yielded tiles and leaves the share at what it was);
      - out: the ones the runner skips now, depleted, unreachable or marked
        `nothing_near` (in reach of a stand where the server said nothing nearby has
        wood) within the regrowth window (Memory.harvest_available). A mark alone
        isn't an attempt: it counts as out, not as tried."""
    cur, cycles, tiles = {}, [], {}
    for t, facet, x, y, _z, outcome, amount in attempts:
        k = (facet, x, y)
        d = tiles.setdefault(k, {"tried": False, "yielded": False, "not_tree": False, "out_t": None})
        if outcome != "nothing_near":
            d["tried"] = True
        if outcome == "success":
            cur[k] = cur.get(k, 0) + (amount or 0)
            d["yielded"] = True
        elif outcome == "depleted":
            got = cur.pop(k, 0)
            if got > 0:
                cycles.append(got)
            d["out_t"] = t
        elif outcome in ("unreachable", "nothing_near"):
            d["out_t"] = t
        elif outcome == "not_tree":
            d["not_tree"] = True
    window = regrow_min * 60.0
    out = {}
    for sid, (facet, spot_tiles) in tiles_by_spot.items():
        seen = [d for d in (tiles.get((facet, x, y)) for x, y in spot_tiles) if d is not None and not d["not_tree"]]
        bad = sum(1 for x, y in spot_tiles if (tiles.get((facet, x, y)) or {}).get("not_tree"))
        out[sid] = {"trees": len(spot_tiles) - bad, "tried": sum(1 for d in seen if d["tried"]),
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
def library_walk(landing: dict | None, home: dict | None) -> int:
    """Tiles walked at home to reach the landing's rune on a trip out: 0 for an own
    book's rune (recalled where we stand), else from the home landing to the stand of
    the library holding it (home.libraries: the home library only; DTF: 4134,1429 ->
    4152,1429, 18 tiles)."""
    if landing is None or landing.get("source") != "library" or not home:
        return 0
    import places
    return cheb(home["landing"], places.library(landing["library"])["stand"])


def overhead_prior_s(spot: dict, landing: dict | None = None, home: dict | None = None) -> float:
    """Prior seconds of one trip's overhead, every trip starting and ending at home
    (docs/LUMBER_LOOP.md §6): out of the rental room (ROOM_EXIT_S), the walk to the
    landing's rune (library_walk), the recall out, the
    walk from the landing into the grove (its planned route when known, else the
    straight distance to a square's inner half, a forest's edge plus half a cell), the harvest lockout after the recall
    (LOCKOUT_S), the recall home on our book's default rune, into the room
    (ROOM_ENTER_S), convert and store (OVERHEAD_FIXED_S). Without a landing (no home
    known) the walk in is the spot's discovered route_tiles, else NO_LANDING_TILES."""
    area = spot.get("area") or {}
    if landing is None or len(area.get("center") or ()) != 2:
        into = _num(spot.get("route_tiles"), NO_LANDING_TILES)
    else:
        into = landing.get("route_tiles")
        if into is None and area.get("cells"):
            into = area_dist(area, (landing["x"], landing["y"])) + CELL // 2
        elif into is None:
            into = max(0, cheb((landing["x"], landing["y"]), area["center"]) - int(area.get("radius") or 0) // 2)
    walk = library_walk(landing, home)
    return (ROOM_EXIT_S + (walk + into) * SEC_PER_TILE + 2 * RECALL_TRIP_S + LOCKOUT_S
            + ROOM_ENTER_S + OVERHEAD_FIXED_S)


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
      - death (h_D): the trip ends and stores nothing; the cycle costs the field
        time so far + recovery_h;
      - sent home (h_S): the trip ends at τ and what we carry comes home;
      - theft (h_T): a thief takes the share f of what we carry; the trip goes
        on (the runner counts logs gained, so it still ends at q gained).
    With k = h_T·f the expected load at t is lam·(1 − e^(−k·t))/k, so with
    h = h_D + h_S:
      stored = e^(−h·tf)·C(tf) + h_S·∫₀^tf e^(−h·t)·C(t) dt
      time   = t_h + ∫₀^tf e^(−h·t) dt + recovery_h·P(death)
      P(death) = h_D·∫₀^tf e^(−h·t) dt   (≤ 1 for any q)
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
    stored = math.exp(-h * tf) * load_tf + hs * load_int
    p_death = hd * stay
    lost_death = hd * load_int
    return {"stored": stored, "time_h": t_h + stay + recovery_h * p_death, "p_death": p_death,
            "p_home": hs * stay, "lost_death": lost_death, "lost_theft": lam * stay - stored - lost_death}


def net_rate(q, lam, t_h, hz, gear_logs, cost_logs=0.0, recovery_h=RECOVERY_H) -> float:
    """Stored logs per hour (renewal-reward: E[stored] / E[cycle time], trip_terms)
    net of the supplies one trip uses (cost_logs: recall charges, reagents) and the
    gear a death loses (gear_logs × P(death)), all in logs at the board price."""
    tt = trip_terms(q, lam, t_h, hz, recovery_h)
    return (tt["stored"] - cost_logs - gear_logs * tt["p_death"]) / tt["time_h"]


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


def spot_model(spot, trips, hev, pooled, prior, phi, p_now, now, landing=None, home=None) -> dict:
    """Posterior parameters of one spot from its trips (trip_obs dicts), its
    hazard evidence hev (sightings {trip t0: n}, deaths [t], thefts [t], trips
    sent home {t0}), the pooled hazard rates (plan) and the rate prior
    (rate_prior). Hazards are Gamma posteriors over recency-weighted field
    hours, each shrunk to its pooled rate: h_D's prior mean = sightings per
    field hour × P(death | sighting) + the creature-death rate, worth
    DEATH_SHRINK_H field hours; h_S's = the pooled rate, HOME_SHRINK_H; h_T's =
    the pooled rate, THEFT_SHRINK_H. The overhead's prior: overhead_prior_s with the
    spot's landing (landing_for) from home."""
    w = [weight(tr["t1"], now) for tr in trips]
    logs = sum(wi * tr["logs"] for wi, tr in zip(w, trips))
    hrs = sum(wi * adjusted_field_h(tr, p_now) for wi, tr in zip(w, trips))
    exposure = sum(wi * tr["field_s"] / 3600.0 for wi, tr in zip(w, trips))
    alpha = prior[0] + logs / phi
    beta = prior[1] + hrs / phi
    t0 = overhead_prior_s(spot, landing, home)
    obs = [(wi, tr["overhead_s"]) for wi, tr in zip(w, trips) if tr["overhead_s"] is not None]
    n = 1.0 + sum(wi for wi, _ in obs)
    mean = (t0 + sum(wi * o for wi, o in obs)) / n
    var = sum(wi * (o - mean) ** 2 for wi, o in obs) / n if obs else (0.5 * t0) ** 2
    sd = max(OVERHEAD_SD_MIN_S, math.sqrt(var)) / math.sqrt(n)
    pvp = bool(spot.get("pvp", True))
    sightings = hev["sight"]
    seen = sum(wi * sightings.get(tr["t0"], 0) for wi, tr in zip(w, trips))
    hp = _num(spot.get("hazard_prior"), 0.5) if pvp else 0.0
    if pvp and spot.get("faction_zone"):
        hp = max(hp, FACTION_ZONE_PRIOR)
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


def _value(lam, t_h, hz, gear_logs, cost_logs=0.0, q_cap=Q_MAX, refine=False, cap_logs=None):
    """(net logs/hour at a spot, Q*, capacity-bound). Every trip starts and ends at
    home, so no spot pays travel beyond its trip overhead t_h. cap_logs (what the
    spot's trees hold, capacity_logs) caps Q*; when it binds, the trip ends dry and
    the spot is out until its trees regrow."""
    q = best_q(lam, t_h, hz, gear_logs, q_cap if cap_logs is None else min(q_cap, cap_logs), cost_logs,
               refine=refine)
    tt = trip_terms(q, lam, t_h, hz)
    v = (tt["stored"] - cost_logs - gear_logs * tt["p_death"]) / tt["time_h"]
    bound = cap_logs is not None and cap_logs < min(q_cap, Q_MAX) and q >= int(cap_logs)
    return v, q, bound


def eligibility(spot, trips, threats, now, regrow_min, thieves=()) -> str | None:
    """Why the spot can't be picked now, or None. thieves: [t] of `thief` job events there
    that made us leave (THIEF_COOLDOWN_S)."""
    if spot.get("status") != "active":
        return f"status {spot.get('status')}" + (f": {spot['reason']}" if spot.get("reason") else "")
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
                     m.get("supply_gp", 0.0) / gp_per_log, q_cap, refine=True, cap_logs=m.get("cap_logs"))
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


def plan(spots: dict, episodes: list, sightings: list, deaths: list, regrow: dict, char: dict | None,
         table: dict, prices: dict, now: float, rng: random.Random, stint_min: float = STINT_MIN,
         home: dict | None = None, landings: dict | None = None, who: str | None = None, at_home=None,
         logs_per_success: float = LOGS_PER_SUCCESS, gp_per_log: float = 9.5, draws: int = DRAWS,
         events: list = (), trees: dict | None = None, exploit: bool = False) -> dict:
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
    spot's own run either way."""
    char = char or {}
    young = bool(char.get("young"))
    alias = merged_alias(spots)
    trips = []
    for tr in (trip_obs(e, prices) for e in episodes):  # a merged spot's trips are its forest's
        if tr is not None and alias.get(tr["spot"], tr["spot"]) in spots:
            trips.append({**tr, "spot": alias.get(tr["spot"], tr["spot"])})
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
    infos = list(trees["spots"].values())
    share0 = (sum(i["yielded"] for i in infos) + 1.0) / (sum(i["tried"] for i in infos) + 2.0)

    models, rows = {}, []
    thieves = {sid: [e["t"] for e in events if e["kind"] == "thief"
                     and alias.get((e.get("data") or {}).get("spot"), (e.get("data") or {}).get("spot")) == sid
                     and (e.get("data") or {}).get("action") != "keep_away"] for sid in spots}
    for sid, s in spots.items():
        landing = landings.get(sid)
        m = spot_model(s, by_spot[sid], hev[sid], pooled, prior, phi, p_now, now, landing, home)
        why = eligibility(s, by_spot[sid], threats[sid], now, regrow["minutes"], thieves[sid])
        if why is None and home is not None and landing is None:
            why = ("no landing rune near the grove with a walking route into it "
                   "(the home rune library, our own books)")
        m["cost_logs"] = m["supply_gp"] / gp_per_log
        t_h = m["overhead_s"] / 3600.0
        m["cap"] = capacity_model(trees["spots"].get(sid), share0)
        m["cap_logs"] = capacity_logs(m["cap"], lpt)
        v, q, bound = _value(m["rate"], t_h, m["hz"], gear_logs, m["cost_logs"], q_cap, refine=True,
                             cap_logs=m["cap_logs"])
        tt = trip_terms(q, m["rate"], t_h, m["hz"])
        lo = gamma_quantile(m["alpha"], m["beta"], 0.1)
        hi = gamma_quantile(m["alpha"], m["beta"], 0.9)
        m.update(value=v, q=q, bound=bound, why=why, terms=tt)
        models[sid] = m
        hd, hs, ht, _ = m["hz"]
        last = max((tr["t1"] for tr in by_spot[sid]), default=None)
        rows.append({"id": sid, "name": s.get("name"), "status": s.get("status"), "eligible": why is None,
                     "why_not": why, "trips": m["trips"], "field_h": m["field_h"], "logs": m["logs"],
                     "rate_logs_h": round(m["rate"]), "rate_80": [round(lo), round(hi)],
                     "overhead_s": round(m["overhead_s"]), "sightings": m["sightings"],
                     "sightings_per_h": round(m["sight_a"] / m["sight_b"], 2),
                     "faction_zone": (s.get("faction_zone") or {}).get("label"),
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
                     "landing": landing_view(landing),
                     "last_trip_h_ago": None if last is None else round((now - last) / 3600.0, 1)})

    eligible = [sid for sid in spots if models[sid]["why"] is None]
    out = {"ok": True, "now": now, "skill": skill, "success_p": None if p_now is None else round(p_now, 3),
           "home": None if home is None else {"character": who, "library": home.get("library"),
                                              "landing": home.get("landing"), "at_home": at_home},
           "regrow": regrow, "dispersion": round(phi, 1), "young": young,
           "death_given_sighting": round(pooled["p_pk"], 3),
           "creature_deaths_per_h": round(pooled["creature"], 4),
           "sent_home_pooled_per_h": round(pooled["home"], 3), "thefts_pooled_per_h": round(pooled["theft"], 4),
           "theft_fraction": round(pooled["f"], 2), "theft_events": len(thefts),
           "gear_at_risk": gear, "capacity_logs": None if q_cap == Q_MAX else q_cap,
           "logs_per_tree": round(lpt, 1), "yield_share_pooled": round(share0, 2),
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
                      cap_logs=capacity_logs(mm["cap"], lpt, rng))[0]

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
            run_q = best_q(m["rate"], m["overhead_s"] / 3600.0, m["hz"], gear_logs, q_cap, m["cost_logs"])
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
    return out


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
    regrow = regrowth(attempts)
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
    trip keeps its logs in one and uses it up (the runner sets it off in the rental room to
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
                    seeds_path=SEEDS, now=None, route_check: bool = True, exploit: bool = False) -> dict:
    """plan() over the memory store and a state-port snapshot (world None: no proxy).
    The character is the proxy's (world self name), else the newest trip row's; its
    home comes from harness/data/homes.json (home.for_character), its own books from
    the store (places.known_books), each spot's landing from spot_landings."""
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
    home = homes.for_character(who)
    landings = None
    if home is not None:
        landings = spot_landings(memory, spots, home, places.known_books(memory, who), route_check)
    out = plan(spots, inp["episodes"], inp["sightings"], inp["deaths"], inp["regrow"], char, table,
               inp["prices"], now, random.Random(seed), stint_min=stint_min, home=home, landings=landings,
               who=who, at_home=None if home is None or facet is None else homes.at_home(pos, facet, home),
               logs_per_success=inp["logs_per_success"], gp_per_log=gp_per_log(inp["prices"]),
               events=inp["events"], exploit=exploit,
               trees=tree_yield(inp["attempts"], spot_tree_tiles(spots), now, inp["regrow"]["minutes"]))
    out["character"] = None if char is None else {k: char[k] for k in ("name", "serial", "skill", "mounted", "buffs",
                                                                       "weight", "weight_max", "young", "pouches")}
    return out


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
    that already have a spot (`witcher_<id>`). A candidate is an area like any spot:
    trips reach it from home by the landing nearest it (landing_for), which need not be
    this rune; route_tiles is the walk from this rune's landing. Returns (candidates,
    {reason: count} of the runes left out)."""
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
        route = route_fn((r["x"], r["y"]), {"center": [cx, cy], "radius": radius}) if route_fn is not None else None
        if route_fn is not None and (route is None or route > max_route):
            failed[i] = failed.get(i, 0) + 1
            continue
        n = -neg_n
        out.append({"id": f"witcher_{r['id']}", "name": f"Witcher {r['id']}: {r['name']} ({n} trees, {d} tiles off)",
                    "facet": 0, "area": {"center": [cx, cy], "radius": radius,
                                         "note": f"{n} tree statics {d} tiles from Witcher rune {r['id']} "
                                                 f"({r['x']},{r['y']}, {library} library)"},
                    "trees": [], "pvp": True, "hazard_prior": 0.5, "danger_hint": danger,
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

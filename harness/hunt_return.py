"""When a hunt takes its loot home (user 2026-10-17, after two deaths to reds with a full pack: "modify the runner to
leave and drop off loot once the value of our loot exceeds the risk/reward of being killed by reds or mobs, like the
lumber runner does").

The lumber runner's return trigger (docs/LUMBER_LOOP.md §6 "Return trigger"), with gold for boards: the carried value
grows at `r` gold a minute on the level, a death (reds or monsters, hazard `h` a minute) loses all of it, and a round
trip home and back costs `T` minutes. The cost per gold banked is c(V) = rT/V + hV/(2r), least at

    V* = r * sqrt(2 T / h)

so a visit leaves once the carried value reaches V* (docs/HUNT_LOOP.md "Taking the loot home").

- `r`: gold a minute from this character's visit rows at this place (episodes, gold / minutes on the level), else
  PRIOR_GOLD_MIN. Gold only: the items' share is lumpy (a research materials, 25k, now and then), so the rule leaves a
  little early rather than late.
- `h`: the death rate a minute on the level, two parts over the same visits (since `since`, the live server's first run;
  the test shard had no player killers), as the lumber runner splits it (lumber_opt `_death_rate`; user 2026-10-17:
  "count red escapes as part of the risk"):
  - player killers: encounters a minute x P(death | encounter). An encounter is a leave for a player (`leave` job
    events whose why names one: PLAYER_WHY) or a PK death without one. Gamma-Poisson (ENC_PRIOR + encounters) /
    (ENC_PRIOR_MIN + minutes); P(death) Beta(P_PK_PRIOR) with the PK deaths against the escapes. So an escape raises
    how often reds come and lowers how often they kill, and sightings carry information long before deaths do.
  - monsters: (MOB_PRIOR + monster deaths) / (ENC_PRIOR_MIN + minutes).
  A death is a PK death when its cause says so or a player leave lies within PK_LINK_S of it (the 12:06 and 13:40
  deaths of 2026-10-17 logged the leave for the red a moment after the death, the death's cause "unknown").
- `T`: the trip, measured: the way out (a `leave` event's `out_s`, the leave firing to home), the drop-off
  (`dropoff` events' `elapsed_s`) and the way in (visit rows' `in_s`, the recall in to the arrival), each the mean
  of its samples shrunk to a prior worth one sample (OUT_PRIOR_MIN, DROPOFF_PRIOR_MIN, IN_PRIOR_MIN). Waiting out
  --pk-wait and resting to heal are not the return's cost and are not in it.

The carried value is the pack's gold plus every item priced in the store's prices under `loot:<name>` (the name the
client shows, lower case, without its " : N" count or an article), with a generic row for a kind (`loot:aspect core`
for any "... aspect core", `loot:skill scroll` for any skill scroll). Unpriced items count 0 and are listed."""

from __future__ import annotations

import math
import re

import combat

ENC_PRIOR = 1.0              # one player encounter ...
ENC_PRIOR_MIN = 60.0         # ... an hour on the level [INFERENCE: a prior, overtaken by the visits]
P_PK_PRIOR = (1.0, 3.0)      # Beta(deaths, escapes): a red who finds us kills one time in four [INFERENCE]
MOB_PRIOR = 0.5              # monster deaths in ENC_PRIOR_MIN [INFERENCE]
PK_LINK_S = 300.0            # a player leave this close to a death makes it a PK death (lumber_opt.CAUSE_LINK_S)
PLAYER_WHY = re.compile(r"\bplayer\b|is attacking us \(server notice\)")   # loop_hunt.player_threat's whys
PRIOR_GOLD_MIN = 1000.0       # gold a minute without visit rows (live 2026-10-15..17: 1329 over 66 min)
# the trip's priors, each worth one sample, from the live 2026-10-17 logs: a red leave by rope and gate home in 9 s
# (a planned one walks to the gate, up to 194 steps), the steward and the drop-off 29 s, the recall in and the
# walk-in 3.5 and 2.2 min
OUT_PRIOR_MIN, DROPOFF_PRIOR_MIN, IN_PRIOR_MIN = 1.0, 0.5, 3.0
V_MIN, V_MAX = 5_000, 250_000
TILE_CONTAINER = 0x00200000   # tiledata flag (UO TileFlag.Container)
PLACE_R = 120                 # visit rows and deaths within this many tiles of the spot count as this place
_COUNT = re.compile(r"\s*:\s*\d+$")
_ARTICLE = re.compile(r"^(a|an|the)\s+")
ALIASES = {"piece of amber": "amber"}   # tiledata's name -> the client's (prices are keyed by the client's)
LIVE_SINCE = 1792116429.0     # 2026-10-15 21:07 local: the first live-server run (capture 20261015_210709); before, the test shard
GENERIC = (("aspect core", "aspect core"), ("skill mastery scroll", "skill scroll"), ("skill scroll", "skill scroll"),
           ("aspect distillation", "aspect distillation"), ("distillation", "aspect distillation"),
           ("phylactery", "phylactery"))


def loot_name(it: dict) -> str:
    """The item's name as prices are keyed: lower case, without ' : N' or a leading article."""
    return _ARTICLE.sub("", _COUNT.sub("", combat.item_name(it)))


def price(it: dict, prices: dict) -> float | None:
    """Gold for one of item `it` (prices: Memory.prices()), or None when it has no price."""
    name = loot_name(it)
    g, hue = it.get("graphic"), it.get("hue")
    if g in combat.BLANK_SCROLL_GRAPHICS and hue:
        name = "arcane scroll"                      # a hued blank scroll (combat.worth_looting)
    elif g == 0x0FB9 and name in ("chroma core", "core"):
        name = "chromatic core"                     # tiledata 'chroma core'; the client 'a chromatic core'
    name = ALIASES.get(name, name)
    row = prices.get(f"loot:{name}")
    if row is None:
        row = next((prices.get(f"loot:{key}") for tail, key in GENERIC if name.endswith(tail)), None)
    return None if row is None else float(row["price_gp"])


def carried(state: dict, prices: dict) -> dict:
    """{gold, items_gp, total, unpriced [names]} of what lies in the backpack's top level, where loot lands (our own
    bags of supplies, tools and books aside)."""
    items = state["world"]["items"]
    pack = combat.backpack(items, state["movement"].get("self_serial"))
    gold = items_gp = 0.0
    unpriced = set()
    for _, it in items.items():
        if pack is None or it.get("container") is None or combat._serial(it["container"]) != pack:
            continue
        n = it.get("amount") or 1
        if it.get("graphic") == combat.GOLD_GRAPHIC:
            gold += n
            continue
        p = price(it, prices)
        if p is None:
            if not _is_container(it.get("graphic")):
                unpriced.add(loot_name(it))
            continue
        items_gp += p * n
    return {"gold": int(gold), "items_gp": int(items_gp), "total": int(gold + items_gp), "unpriced": sorted(unpriced)}


def _is_container(graphic) -> bool:
    """A bag, box or book bind (tiledata's Container flag): our own, never loot to price."""
    import uomap
    t = uomap.tiledata().item(graphic or 0)
    return bool(t is not None and t.flags & TILE_CONTAINER)


def _mean(prior: float, samples: list[float]) -> float:
    """The samples' mean shrunk to `prior`, worth one sample."""
    return (prior + sum(samples)) / (1 + len(samples))


def model(visits: list[dict], events: list[dict], spot, facet=None) -> dict:
    """{r, h, h_pk, h_mob, trip_min, trip, v_star, minutes, deaths, pk_deaths, encounters, visits}: the rule's inputs
    from visit rows (episodes of loop hunt) and hunt job events (death, leave, dropoff), the place's within PLACE_R of
    `spot` (a drop-off is at home: all count)."""
    def near(x, y, f=None) -> bool:
        return x is not None and y is not None and max(abs(x - spot[0]), abs(y - spot[1])) <= PLACE_R \
            and (facet is None or f is None or f == facet)
    minutes = gold = 0.0
    n, ins = 0, []
    for v in visits:
        s = v.get("spot") or [None, None]
        if not near(s[0], s[1]) or v.get("t_end") is None or v.get("t_start") is None:
            continue
        minutes += max(0.0, v["t_end"] - v["t_start"]) / 60.0
        gold += v.get("gold") or 0
        n += 1
        if v.get("in_s") is not None:
            ins.append(v["in_s"] / 60.0)
    here = [e for e in events if near(e.get("x"), e.get("y"), e.get("facet"))]
    deaths = [e for e in here if e.get("kind") == "death"]
    leaves = [e for e in here if e.get("kind") == "leave"]
    met = [e["t"] for e in leaves if PLAYER_WHY.search(str((e.get("data") or {}).get("why") or ""))]
    pk = [d for d in deaths if (d.get("data") or {}).get("cause") == "pk" or any(abs(d["t"] - t) <= PK_LINK_S for t in met)]
    unseen = sum(1 for d in pk if not any(abs(d["t"] - t) <= PK_LINK_S for t in met))   # a PK death with no leave
    enc = len(met) + unseen
    p_pk = (P_PK_PRIOR[0] + len(pk)) / (P_PK_PRIOR[0] + P_PK_PRIOR[1] + enc)
    h_pk = (ENC_PRIOR + enc) / (ENC_PRIOR_MIN + minutes) * p_pk
    h_mob = (MOB_PRIOR + len(deaths) - len(pk)) / (ENC_PRIOR_MIN + minutes)
    h = h_pk + h_mob
    outs = [e["data"]["out_s"] / 60.0 for e in leaves if (e.get("data") or {}).get("out_s") is not None]
    drops = [e["data"]["elapsed_s"] / 60.0 for e in events
             if e.get("kind") == "dropoff" and (e.get("data") or {}).get("elapsed_s") is not None]
    trip = {"out": _mean(OUT_PRIOR_MIN, outs), "dropoff": _mean(DROPOFF_PRIOR_MIN, drops), "in": _mean(IN_PRIOR_MIN, ins)}
    t_min = sum(trip.values())
    r = gold / minutes if minutes >= 5 else PRIOR_GOLD_MIN
    v = r * math.sqrt(2 * t_min / h)
    return {"r": round(r, 1), "h": round(h, 5), "h_pk": round(h_pk, 5), "h_mob": round(h_mob, 5), "p_pk": round(p_pk, 3),
            "trip_min": round(t_min, 2), "trip": {k: round(x, 2) for k, x in trip.items()},
            "trip_samples": {"out": len(outs), "dropoff": len(drops), "in": len(ins)},
            "v_star": int(min(V_MAX, max(V_MIN, v))), "minutes": round(minutes, 1), "deaths": len(deaths),
            "pk_deaths": len(pk), "encounters": enc, "visits": n}

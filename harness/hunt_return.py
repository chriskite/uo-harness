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
- `h`: Gamma-Poisson: (PRIOR_DEATHS + deaths) / (PRIOR_MIN + minutes) over the same visits and the deaths there
  since `since` (the live server's first run; the test shard had no player killers).
- `T`: TRIP_MIN (the way out, home, the drop-off and the way back in; [INFERENCE] until visit gaps are measured).

The carried value is the pack's gold plus every item priced in the store's prices under `loot:<name>` (the name the
client shows, lower case, without its " : N" count or an article), with a generic row for a kind (`loot:aspect core`
for any "... aspect core", `loot:skill scroll` for any skill scroll). Unpriced items count 0 and are listed."""

from __future__ import annotations

import math
import re

import combat

PRIOR_DEATHS = 1.0            # one death ...
PRIOR_MIN = 60.0              # ... an hour on the level [INFERENCE: a prior, overtaken by the visits]
PRIOR_GOLD_MIN = 1000.0       # gold a minute without visit rows (live 2026-10-15..17: 1329 over 66 min)
TRIP_MIN = 6.0                # out, home, the drop-off, back in [INFERENCE]
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


def model(visits: list[dict], deaths: list[dict], spot, facet=None) -> dict:
    """{r, h, trip_min, v_star, minutes, deaths, visits}: the rule's inputs from visit rows (episodes of loop hunt) and
    death job events near `spot`."""
    def near(x, y, f=None) -> bool:
        return x is not None and y is not None and max(abs(x - spot[0]), abs(y - spot[1])) <= PLACE_R \
            and (facet is None or f is None or f == facet)
    minutes = gold = 0.0
    n = 0
    for v in visits:
        s = v.get("spot") or [None, None]
        if not near(s[0], s[1]) or v.get("t_end") is None or v.get("t_start") is None:
            continue
        minutes += max(0.0, v["t_end"] - v["t_start"]) / 60.0
        gold += v.get("gold") or 0
        n += 1
    d = sum(1 for e in deaths if near(e.get("x"), e.get("y"), e.get("facet")))
    r = gold / minutes if minutes >= 5 else PRIOR_GOLD_MIN
    h = (PRIOR_DEATHS + d) / (PRIOR_MIN + minutes)
    v = r * math.sqrt(2 * TRIP_MIN / h)
    return {"r": round(r, 1), "h": round(h, 5), "trip_min": TRIP_MIN, "v_star": int(min(V_MAX, max(V_MIN, v))),
            "minutes": round(minutes, 1), "deaths": d, "visits": n}

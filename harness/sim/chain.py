"""Logan's Mastery Chain in the simulator: which links it holds, as an input (docs/SIM.md "Mastery Chain links").

The link types the simulator models and each one's value per link by material are in
harness/data/sim/mastery_links.json (the wiki's table, each type's `effect`: where it applies). A chain is
{type: {"n": links, "total": the summed value}}; the World and the pets read its totals (`apply` puts them in
sc["chain"]). Logan's own chain is his tamer file's `chain` (the gump's totals).

A spec (run.py --links) lists links, comma-separated:
- `current`: start from Logan's chain (else from none); a type named after it replaces that type's links;
- `none`: no links;
- `TYPE=N[:MATERIAL][+N:MATERIAL...]`: N links of TYPE, bronze unless a material (bronze, silver, gold, corrupted)
  is given. TYPE is a key of mastery_links.json or its wiki name, any case, spaces or underscores.
E.g. `effective_barding=10, follower_damage=3:gold` or `current, effective_barding=10:silver`.
"""
from __future__ import annotations

import functools
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
TABLE = os.path.join(os.path.dirname(HERE), "data", "sim", "mastery_links.json")


@functools.lru_cache(maxsize=None)
def table() -> dict:
    """mastery_links.json. Cached: never mutate it."""
    with open(TABLE, encoding="utf-8") as f:
        return json.load(f)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.strip().lower()).strip("_")


def type_key(name: str) -> str:
    """The table's key for `name` (a key or a wiki name); ValueError for an unknown or unmodelled link type."""
    tb = table()
    k = _norm(name)
    for key, v in tb["types"].items():
        if k in (key, _norm(v["wiki"])):
            return key
    for wiki, why in tb["unmodelled"].items():
        if k == _norm(wiki):
            raise ValueError(f"{name!r}: not modelled in the simulator ({why})")
    raise ValueError(f"unknown link type {name!r} (modelled: {', '.join(tb['types'])})")


def current(character: str = "logan_wolf") -> dict:
    """The character's chain as his tamer file has it ({type: {n, total}})."""
    from sim import pets
    return {k: dict(v) for k, v in pets.load_tamer(character)["chain"]["links"].items()}


def parse(spec: str, character: str = "logan_wolf") -> dict:
    """The chain `spec` names (module docstring); ValueError for a bad item or past the game's limits."""
    tb = table()
    mats = tb["materials"]
    items = [s.strip() for s in (spec or "").split(",") if s.strip()]
    if not items:
        raise ValueError("an empty --links (say 'none' for no links)")
    out = {}
    named = {}
    for it in items:
        low = it.lower()
        if low == "current":
            out = {**current(character), **out}
            continue
        if low == "none":
            continue
        typ, eq, val = it.partition("=")
        if not eq or not val.strip():
            raise ValueError(f"{it!r}: TYPE=N[:MATERIAL] (or 'current', 'none')")
        key = type_key(typ)
        n, total = 0, 0.0
        for part in val.split("+"):
            cnt, _, mat = part.strip().partition(":")
            mat = (mat.strip().lower() or "bronze")
            if not cnt.strip().isdigit():
                raise ValueError(f"{it!r}: {cnt.strip()!r} is not a number of links")
            if mat not in mats:
                raise ValueError(f"{it!r}: material {mat!r} (one of {', '.join(mats)})")
            n += int(cnt)
            total += int(cnt) * tb["types"][key]["values"][mats.index(mat)]
        named[key] = {"n": n, "total": round(total, 6)}
    out.update(named)
    out = {k: v for k, v in out.items() if v["n"] > 0}
    for k, v in out.items():
        if v["n"] > tb["max_same_type"]:
            raise ValueError(f"{k}: {v['n']} links, the game allows {tb['max_same_type']} of a type "
                             f"(15 with a redline upgrade)")
    if sum(v["n"] for v in out.values()) > tb["max_links"]:
        raise ValueError(f"{sum(v['n'] for v in out.values())} links, a chain holds {tb['max_links']}")
    return out


def totals(links: dict) -> dict:
    """{type: summed value} of a chain; every modelled type present (0 when absent)."""
    return {k: (links.get(k) or {}).get("total", 0.0) for k in table()["types"]}


def describe(links: dict) -> str:
    """One line: each type's links and total, as the gump shows them."""
    tb = table()["types"]
    if not links:
        return "no links"
    return ", ".join(f"{k} {v['n']} ({'+%.2f' % v['total'] if tb[k]['unit'] == 'points' else '+%.2f%%' % (100 * v['total'])})"
                     for k, v in sorted(links.items()))


def apply(sc: dict, spec: str | None = None) -> None:
    """Put a chain in the scenario: `spec`'s (parse), else the character's own. Writes sc["chain"]
    ({links, totals, spec, limit}). Run before pets.select (the pets' numbers) and the World."""
    character = sc["tamer"]["character"]
    from sim import pets
    limit = pets.load_tamer(character)["chain"]["limit"]
    links = parse(spec, character) if spec else current(character)
    sc["chain"] = {"links": links, "totals": totals(links), "spec": spec or "current", "limit": limit,
                   "over_limit": max(0, sum(v["n"] for v in links.values()) - limit)}

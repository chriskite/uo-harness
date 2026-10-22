"""A character's home (harness/data/homes.json, docs/LUMBER_LOOP.md §3): Logan Wolf's house since 2026-10-21.

Trips start and end at home: the character's own book's home rune (`rune`, by name; its default rune when that
one lands at home) lands on the home `landing` tile inside the house, and everything a trip ends with is within
reach of that tile: the boards and ingots go into the house's Resource Stockpile (`stockpile`, when the home has
one) or its secure `chest`, the ore into its `forge`, the loot into the drop-off's items (`dropoff`), the shelf's
Restock and Resupply at the storage shelf there. The way out is the landing rune nearest the grove, from the home
rune library (`library`, the DTF guild house's: reached by our own book's `library_rune`, a recall into the guild
house, then a walk to the tome) or straight from the character's own books (places.landings)."""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
HOMES = os.path.join(HERE, "data", "homes.json")
HOME_R = 6                         # tiles from the landing that count as home: the house (live 2026-10-21: its walls
                                   # within 4 of the landing, the door 4, the house sign 5)


def load(path: str | None = None) -> dict:
    """{character name: home} from the homes file."""
    with open(path or HOMES, encoding="utf-8") as f:
        return json.load(f)["homes"]


def for_character(name: str | None, path: str | None = None) -> dict | None:
    """The home of the character called `name` (case-blind), else None."""
    if not name:
        return None
    want = name.strip().lower()
    return next((h for n, h in load(path).items() if n.strip().lower() == want), None)


def at_home(pos, facet: int | None, home: dict) -> bool:
    """Within HOME_R tiles of the landing on its facet: in the house."""
    if pos is None or pos[0] is None or facet is None or facet != home["facet"]:
        return False
    lx, ly = home["landing"][:2]
    return max(abs(pos[0] - lx), abs(pos[1] - ly)) <= HOME_R


def libraries(home: dict | None) -> list[str]:
    """The rune libraries a trip can recall out from: the home library only, the one our own book's
    `library_rune` recalls into (the DTF guild house's). Any other (the public Cambria one) is out of reach."""
    return [home["library"]] if home and home.get("library") else []


def library_rune(home: dict | None) -> dict | None:
    """{name, landing}: the rune of our own book that recalls into the home library's house (Outland Dan's
    'DTF Loot Chest' -> 4134,1429), the first leg of a trip out by a library tome; None without one."""
    return (home or {}).get("library_rune")


def chest_serial(home: dict) -> int:
    return int(home["chest"]["serial"], 16)

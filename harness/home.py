"""A character's home for harvest trips (harness/data/homes.json, docs/LUMBER_LOOP.md §3).

Trips start and end at home: the character's own book's default rune lands at the
home `landing`, the rental room is entered from there, and the boards go into the
room's Resource Stockpile (`stockpile`, when the home has one) or its secure `chest`.
The way out is the landing rune nearest the grove, from the home rune library or the
character's own books (places.landings)."""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
HOMES = os.path.join(HERE, "data", "homes.json")
NEAR_LANDING = 60                  # tiles from the landing that count as home (the guild house)


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


def in_room(facet: int | None, home: dict) -> bool:
    return facet is not None and facet == home["room"]["facet"]


def at_home(pos, facet: int | None, home: dict) -> bool:
    """In the rental room, or within NEAR_LANDING tiles of the landing on its facet."""
    if in_room(facet, home):
        return True
    if pos is None or facet != home["facet"]:
        return False
    lx, ly = home["landing"][:2]
    return max(abs(pos[0] - lx), abs(pos[1] - ly)) <= NEAR_LANDING


def libraries(home: dict | None) -> list[str]:
    """The rune libraries a trip can recall out from: the home library only. A trip
    starts at home and reaches a library's tomes on foot, so a library elsewhere
    (the public Cambria one, ~2500 tiles from the DTF house) is out of reach."""
    return [home["library"]] if home and home.get("library") else []


def chest_serial(home: dict) -> int:
    return int(home["chest"]["serial"], 16)

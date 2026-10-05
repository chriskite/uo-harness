"""Named places to travel to (docs/research/WORLD_LOCATIONS.md).

- Rune libraries: harness/data/rune_libraries.json (committed): locked-down rune
  tomes a character recalls from while standing within the library's use range
  of the tome: the public Cambria Rune Library (the Witcher set) and the DTF guild
  house (a Witcher set plus towns, docks, dungeons, POIs...). Per tome its rows:
  the name the tome shows and the tile the rune lands on. escape.recall(io, tome,
  rune=<row name>) recalls with one.
- The Witcher rune system: harness/data/witcher_runes.json (committed): rune id
  -> name and the treasure-map dig tile. A library row named "N" or "N - Place"
  is Witcher rune N.
- The World Atlas packs the client ships in ClassicUO/Data/Client/*.xml
  (read-only install data, like guards.bank_markers): points of interest,
  healer caravans, shrines, townships, moongates, dungeons.
- The character's own runebooks and rune tomes (escape.read_book, remembered in
  the memory store's meta `own_books`) and landings(): every known rune that lands
  near a tile, libraries and own books together (the lumber loop's way out).
"""
import json
import os
import re
import time
import xml.etree.ElementTree as ET

import uomap

HERE = os.path.dirname(os.path.abspath(__file__))
WITCHER = os.path.join(HERE, "data", "witcher_runes.json")
LIBRARIES = os.path.join(HERE, "data", "rune_libraries.json")
CLIENT_DATA = os.path.join(uomap.INSTALL, "ClassicUO", "Data", "Client")
ATLAS = {"poi": "POI.xml", "caravan": "Healer_Caravans.xml", "shrine": "Shrines.xml",
         "town": "Townships.xml", "moongate": "Moongates.xml", "dungeon": "Dungeons.xml"}
# Place-name words that mean monsters live there (POI.xml and Witcher rune names, e.g. "Brigand
# Camp 1", "Orc Fort 2", "Ratman Hovel", "Necromancers Swamp") [INFERENCE from the names]
DANGER_WORDS = ("brigan", "orc", "lizardm", "savage", "daemon", "dragon", "necromancer", "ratman",
                "cemetery", "crypt", "graveyard", "ogre", "terathan", "harpy", "lair", "troll", "ettin",
                "cult", "undead", "spider", "arachnid", "minotaur", "kraul", "barbaric", "tribal", "smuggler",
                "bandit", "pirate", "haunted", "volcano", "tomb", "pits", "dungeon", "cavernam")
_WORD = re.compile(r"[a-z]+")
_WITCHER_ROW = re.compile(r"^\s*(\d+[a-z]?)\s*(?:-\s.*)?$", re.I)
AT_LIBRARY = 20                  # tiles from a library's stand: "standing in it"

_cache = {}
_paths = {"witcher": WITCHER, "libraries": LIBRARIES}


def use(witcher: str | None = None, libraries: str | None = None):
    """Read the tables from these files instead (offline tests: a simulated library)."""
    for key, path in (("witcher", witcher), ("libraries", libraries)):
        if path is not None:
            _paths[key] = path
            _cache.clear()


def _load(key: str) -> dict:
    if key not in _cache:
        with open(_paths[key], encoding="utf-8") as f:
            _cache[key] = json.load(f)
    return _cache[key]


def witcher() -> dict:
    """The Witcher table (cached)."""
    return _load("witcher")


def _witcher_by_id() -> dict:
    if "witcher_ids" not in _cache:
        _cache["witcher_ids"] = {r["id"]: r for r in witcher()["runes"]}
    return _cache["witcher_ids"]


def witcher_rune(rune_id: str) -> dict:
    """{id, name, x, y} of a Witcher rune, x/y its dig tile (KeyError if unknown)."""
    r = _witcher_by_id().get(str(rune_id).strip().lower())
    if r is None:
        raise KeyError(f"no Witcher rune {rune_id!r}")
    return r


def libraries() -> list:
    """Every rune library (cached)."""
    return _load("libraries")["libraries"]


def library(lib_id: str = "cambria") -> dict:
    lib = next((lb for lb in libraries() if lb["id"] == lib_id), None)
    if lib is None:
        raise KeyError(f"no rune library {lib_id!r} (known: {', '.join(lb['id'] for lb in libraries())})")
    return lib


def witcher_id(row_name: str) -> str | None:
    """The Witcher rune id a library row stands for ("286" or "286 - Midlands Ruins 1
    (South)" -> "286"), when the table has it; else None."""
    m = _WITCHER_ROW.match(row_name or "")
    if not m:
        return None
    rid = m.group(1).lower()
    return rid if rid in _witcher_by_id() else None


def runes(lib_id: str | None = None) -> list:
    """Every library row as {library, tome, tome_title, tome_pos, name, x, y, witcher}
    (x/y: where the rune lands; witcher: its Witcher id or None), of one library or all."""
    out = []
    for lib in libraries() if lib_id is None else [library(lib_id)]:
        for t in lib["tomes"]:
            for r in t["rows"]:
                out.append({"library": lib["id"], "tome": t["serial"], "tome_title": t.get("title"),
                            "tome_pos": t["pos"], "name": r["name"], "x": r.get("x"), "y": r.get("y"),
                            "witcher": witcher_id(r["name"])})
    return out


def library_rune(lib_id: str, query: str, tome: str | None = None) -> dict:
    """The row of library `lib_id` that `query` names: a Witcher id ("286"), else the
    row's name (case-insensitive), else the one row whose name holds every word of
    it. `tome` (a serial, or its title or words of it) picks among same-named rows
    in different tomes (the DTF library has "Cambria" as a town and as a dock).
    KeyError when nothing or several rows match."""
    rows = runes(lib_id)
    if tome:
        t = tome.strip().lower()
        rows = [r for r in rows if r["tome"].lower() == t or (r["tome_title"] or "").strip().lower() == t] \
            or [r for r in rows if all(w in (r["tome_title"] or "").lower() for w in t.split())]
    q = str(query).strip().lower()
    hit = [r for r in rows if r["witcher"] == q] or [r for r in rows if r["name"].strip().lower() == q] \
        or [r for r in rows if q.split() and all(w in r["name"].lower() for w in q.split())]
    if len(hit) == 1:
        return hit[0]
    where = library(lib_id)["name"] + (f", tome {tome!r}" if tome else "")
    if not hit:
        raise KeyError(f"no rune {query!r} in the {where}")
    raise KeyError(f"{len(hit)} runes in the {where} match {query!r} (pick one with its tome): "
                   + "; ".join(f"{r['name']} [{r['tome_title']}]" for r in hit[:8]))


def find_runes(words, lib_id: str | None = None) -> list:
    """Library rows whose name holds every word (case-insensitive substrings)."""
    ws = [w.lower() for w in (words.split() if isinstance(words, str) else words) if w]
    return [r for r in runes(lib_id) if ws and all(w in r["name"].lower() for w in ws)]


def library_at(pos, facet=0, radius: int = AT_LIBRARY) -> dict | None:
    """The library whose stand is within `radius` tiles of pos on this facet, nearest first."""
    if not pos:
        return None
    near = [(max(abs(lb["stand"][0] - pos[0]), abs(lb["stand"][1] - pos[1])), lb) for lb in libraries()
            if lb["facet"] == int(facet or 0)]
    near = [(d, lb) for d, lb in near if d <= radius]
    return min(near, key=lambda dl: dl[0])[1] if near else None


def witcher_runes_in(lib_id: str) -> list:
    """The Witcher runes library `lib_id` holds, as {id, name, x, y, tome}: the
    table's name, the tile the library's rune lands on (the dig tile when unread)."""
    out = []
    for r in runes(lib_id):
        if r["witcher"] is None:
            continue
        w = witcher_rune(r["witcher"])
        x, y = (r["x"], r["y"]) if r["x"] is not None else (w["x"], w["y"])
        out.append({"id": w["id"], "name": w["name"], "x": x, "y": y, "tome": r["tome"]})
    return out


def tome_at_hand(world: dict, pos, tome: str, lib_id: str = "cambria") -> int | None:
    """The tome serial `tome` ("0x…") when the world model has it within the
    library's use range of pos, else None."""
    lib = library(lib_id)
    it = (world.get("items") or {}).get(tome)
    if it is None or it.get("x") is None or pos is None:
        return None
    if max(abs(it["x"] - pos[0]), abs(it["y"] - pos[1])) > lib["use_range"]:
        return None
    return int(tome, 16)


def save_library(lib: dict, path: str | None = None):
    """Add or replace library `lib` (by id) in the libraries file."""
    path = path or _paths["libraries"]
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    at = next((i for i, lb in enumerate(data["libraries"]) if lb["id"] == lib["id"]), None)
    if at is None:
        data["libraries"].append(lib)
    else:
        data["libraries"][at] = lib
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write("\n")
    if path == _paths["libraries"]:
        _cache.clear()


def atlas(kinds=tuple(ATLAS), root: str = CLIENT_DATA) -> list:
    """[{kind, name, x, y, facet}] from the client's Atlas marker packs; a missing
    or unreadable file contributes nothing."""
    out = []
    for kind in kinds:
        try:
            tree = ET.parse(os.path.join(root, ATLAS[kind])).getroot()
        except (OSError, ET.ParseError):
            continue
        for m in tree.iter("Marker"):
            try:
                out.append({"kind": kind, "name": m.get("Name") or "", "x": int(m.get("X")),
                            "y": int(m.get("Y")), "facet": int(m.get("Facet") or 0)})
            except (TypeError, ValueError):
                continue
    return out


def danger_hint(name: str) -> list:
    """DANGER_WORDS in a place name ([] = nothing known)."""
    words = _WORD.findall((name or "").lower())
    return sorted({d for d in DANGER_WORDS for w in words if w.startswith(d)})


# ------------------------------------------------------------ own books and landings
OWN_BOOKS_KEY = "own_books"      # meta key: {character: {book serial: escape.read_book dict + t}}


def _own_books(mem) -> dict:
    import task_wrap
    return json.loads(task_wrap.meta_get(mem, OWN_BOOKS_KEY) or "{}")


def remember_book(mem, character: str, book: dict):
    """Store what escape.read_book read of `character`'s runebook or rune tome
    (memory-store meta `own_books`, replacing that book's earlier reading)."""
    import task_wrap
    books = _own_books(mem)
    books.setdefault(character, {})[book["serial"]] = {**book, "t": round(time.time(), 1)}
    task_wrap.meta_set(mem, OWN_BOOKS_KEY, json.dumps(books))


def known_books(mem, character: str | None) -> list:
    """The books remembered for `character` (case-blind; None: every character's),
    newest reading first."""
    want = None if character is None else character.strip().lower()
    out = [b for name, books in _own_books(mem).items() if want is None or name.strip().lower() == want
           for b in books.values()]
    return sorted(out, key=lambda b: -(b.get("t") or 0))


def _danger(name: str, title: str | None) -> list:
    return danger_hint(name) + (["bad places"] if "bad places" in (title or "").lower() else [])


def landings(x: int, y: int, facet: int = 0, *, libraries=("cambria",), books=(),
             include_dangerous: bool = False, limit: int | None = None) -> list:
    """Every rune that lands somewhere known on `facet`, nearest (x, y) first
    (Chebyshev `dist`; a tie goes to the own book): the rows of the rune libraries
    `libraries` (ids) and the runes of the own `books` (known_books dicts; a rune
    without a facet counts as facet 0 in a tome, which carries none; a runebook rune whose map hue
    isn't known is left out). Rows {source "library"|"book", library,
    tome, tome_title, tome_pos, witcher, book, kind, book_title, entry (the book's index), name, x, y, dist,
    danger}; library fields are None on book rows and the reverse. `danger`: the
    danger_hint words of the name, plus "bad places" when the tome or book is titled
    so (the DTF 'Bad Places' tome); such rows are left out unless include_dangerous."""
    rows = []
    for lib_id in libraries:
        if library(lib_id)["facet"] != facet:
            continue
        for r in runes(lib_id):
            if r["x"] is not None:
                rows.append({"source": "library", **r, "book": None, "kind": None, "book_title": None,
                             "entry": None, "danger": _danger(r["name"], r["tome_title"])})
    for b in books:
        for r in b.get("runes") or []:
            rf = r.get("facet")
            if rf is None and b.get("kind") == "runetome":
                rf = 0
            if r.get("x") is None or rf != facet:
                continue
            rows.append({"source": "book", "library": None, "tome": None, "tome_title": None, "tome_pos": None,
                         "witcher": None, "book": b["serial"], "kind": b.get("kind"), "book_title": b.get("title"),
                         "entry": r.get("i"), "name": r["name"], "x": r["x"], "y": r["y"],
                         "danger": _danger(r["name"], b.get("title"))})
    for r in rows:
        r["dist"] = max(abs(r["x"] - x), abs(r["y"] - y))
    rows = sorted((r for r in rows if include_dangerous or not r["danger"]),
                  key=lambda r: (r["dist"], r["source"] != "book"))
    return rows if limit is None else rows[:limit]

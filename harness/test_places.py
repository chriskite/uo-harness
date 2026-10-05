"""Tests for harness/places.py's own books and landings(): every known rune that
lands near a tile, from the rune libraries (harness/data/rune_libraries.json) and
the character's own books (memory-store meta `own_books`).

Run: python harness/test_places.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import escape  # noqa: E402
import places  # noqa: E402
from memory import Memory  # noqa: E402

FAILURES = []
with open(os.path.join(HERE, "testdata", "escape_gumps.json"), encoding="utf-8") as f:
    G = json.load(f)


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(name)


def dan_book():
    """Outland Dan's runebook as escape.read_book returns it (the live gump fixture)."""
    g = G["runebook_dan_dtf"]
    info = escape.parse_runebook(g["layout"], g["lines"])
    runes = escape.runebook_entries(g["layout"], g["lines"])
    return {"serial": "0x49865F8F", "kind": "runebook", "title": None, "default": info["default"],
            "default_name": next(r["name"] for r in runes if r["i"] == info["default"]),
            "charges": info["charges"], "entries": info["entries"], "runes": runes}


def test_own_books():
    mem = Memory(os.path.join(tempfile.mkdtemp(), "h.db"))
    check("no book remembered yet", places.known_books(mem, "Outland Dan") == [])
    b = dan_book()
    places.remember_book(mem, "Outland Dan", b)
    places.remember_book(mem, "Outland Dan", {**b, "charges": 7})
    places.remember_book(mem, "Hackworth", {**b, "serial": "0x40000001"})
    got = places.known_books(mem, "outland dan")
    check("one entry per book, the newest reading, the name case-blind",
          len(got) == 1 and got[0]["charges"] == 7 and got[0]["runes"] == b["runes"] and got[0]["t"] > 0, got)
    check("None: every character's books", len(places.known_books(mem, None)) == 2)
    check("another character's books stay apart", [x["serial"] for x in places.known_books(mem, "Hackworth")]
          == ["0x40000001"])


def test_landings():
    rows = places.landings(4134, 1429, libraries=("dtf", "cambria"))
    check("landings near the DTF landing: rows from the libraries, sorted by Chebyshev dist",
          rows and all(r["dist"] == max(abs(r["x"] - 4134), abs(r["y"] - 1429)) for r in rows)
          and [r["dist"] for r in rows] == sorted(r["dist"] for r in rows)
          and {r["library"] for r in rows} == {"dtf", "cambria"} and all(r["source"] == "library" for r in rows),
          rows[:3])
    check("library rows carry the tome and no book fields",
          all(r["tome"] and r["tome_pos"] and r["book"] is None and r["kind"] is None for r in rows))
    check("dangerous landings left out: no 'Bad Places' tome, no monster-place name",
          not any(r["danger"] for r in rows) and not any("bad places" in (r["tome_title"] or "").lower() for r in rows))
    every = places.landings(4134, 1429, libraries=("dtf", "cambria"), include_dangerous=True)
    bad = [r for r in every if r["danger"]]
    check("include_dangerous keeps them, flagged",
          len(every) > len(rows) and bad and any("bad places" in r["danger"] for r in bad), len(bad))
    check("limit", len(places.landings(4134, 1429, libraries=("dtf",), limit=3)) == 3)
    check("another facet: none of these libraries", places.landings(4134, 1429, facet=1, libraries=("dtf", "cambria"))
          == [])

    b = dan_book()
    rows = places.landings(4134, 1429, libraries=("dtf", "cambria"), books=[b])
    top = rows[0]
    check("the own book's DTF Loot Chest (dist 0) comes first, as a book row",
          (top["source"], top["name"], top["dist"], top["book"], top["kind"], top["library"], top["tome"])
          == ("book", "DTF Loot Chest", 0, "0x49865F8F", "runebook", None, None), top)
    lib = rows[1]
    tie = places.landings(lib["x"], lib["y"], libraries=(lib["library"],),
                          books=[{**b, "runes": [{"i": 0, "name": "Here", "x": lib["x"], "y": lib["y"], "facet": 0}]}])
    check("a tie in dist goes to the own book", tie[0]["source"] == "book" and tie[1]["dist"] == 0, tie[:2])
    rows = places.landings(1706, 3130, libraries=(), books=[b])
    check("books only: Cambria MG nearest (1706, 3130)", rows[0]["name"] == "Cambria MG" and rows[0]["dist"] == 0,
          rows[:2])
    odd = {**b, "kind": "runetome", "runes": [{"i": 0, "name": "Orc Fort", "x": 1, "y": 1, "facet": 0},
                                             {"i": 1, "name": "Elsewhere", "x": 2, "y": 2, "facet": 1},
                                             {"i": 2, "name": "Unread", "x": None, "y": None, "facet": None},
                                             {"i": 3, "name": "Tome rune", "x": 3, "y": 3, "facet": None}]}
    got = places.landings(0, 0, libraries=(), books=[odd])
    check("tome runes: dangerous names skipped, other facets and unread tiles left out, no facet = facet 0, "
          "the book's index kept", [(r["name"], r["entry"]) for r in got] == [("Tome rune", 3)], got)
    check("a runebook rune whose map hue is unknown (no facet) is left out: its tile may be on another map",
          places.landings(0, 0, libraries=(), books=[{**odd, "kind": "runebook"}]) == [])
    check("a book titled 'Bad Places' is dangerous",
          places.landings(0, 0, libraries=(), books=[{**odd, "title": "Bad Places"}]) == [])


if __name__ == "__main__":
    for t in (test_own_books, test_landings):
        print(t.__name__)
        t()
    print("FAILED: " + ", ".join(FAILURES) if FAILURES else "ALL PASS")
    sys.exit(1 if FAILURES else 0)

"""Tests for harness/escape.py: reading the default rune, the charges and the
Cast Recall button from the runebook and rune tome gumps exactly as the server
sent them (harness/testdata/escape_gumps.json, captured live 2026-10-02), plus
the failure messages and what can cast Recall. No network.

Run: python harness/test_escape.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import escape  # noqa: E402

FAILURES = []
ME, PACK, BAG = 0x00094375, 0x44ADA059, 0x45CE64A1
with open(os.path.join(HERE, "testdata", "escape_gumps.json"), encoding="utf-8") as f:
    G = json.load(f)


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(name)


def h(s):
    return f"0x{s:08X}"


def parse(fn, key):
    g = G[key]
    return fn(g["layout"], g["lines"])


def test_runebook():
    r = parse(escape.parse_runebook, "runebook_default_2nd")
    check("runebook: default is the entry whose set-default button shows art 2360",
          r["default"] == 1 and r["entries"] == 2, r)
    check("runebook: no charges", r["charges"] == 0, r)
    r = parse(escape.parse_runebook, "runebook_charges")
    check("runebook: the count after 'Charges: '", r["charges"] == 2, r)
    r = parse(escape.parse_runebook, "runebook_empty")
    check("runebook: empty book has no entries and no default", r == {"default": None, "charges": 0, "entries": 0}, r)


def test_runetome():
    r = parse(escape.parse_runetome_main, "runetome_main_default_2nd")
    check("runetome: default is the row whose name is in hue 63 (the second)",
          r["default"] == 1 and r["entries"] == 2 and r["charges"] == 0, r)
    r = parse(escape.parse_runetome_main, "runetome_main_default_1st_single")
    check("runetome: single rune, default first", r["default"] == 0 and r["entries"] == 1, r)
    r = parse(escape.parse_runetome_main, "runetome_main_charges")
    check("runetome: recall charges are right of the recall icon, not the gate count",
          r["charges"] == 2, r)
    d = G["runetome_detail_pair"]["layout"]
    check("runetome detail: Cast Recall of the even rune is the left column (10)",
          escape.runetome_cast_button(d, 0) == 10)
    check("runetome detail: Cast Recall of the odd rune is the right column (20)",
          escape.runetome_cast_button(d, 1) == 20)


def test_failures():
    check("cliloc 500641 is a disturbed cast",
          escape.failure({"ev": "cliloc", "cliloc": 500641}, ME) == "disturbed")
    check("the tome's out-of-charges text",
          escape.failure({"ev": "speech_heard", "text": "That rune tome is out of recall charges."}, ME)
          == "no charges")
    check("power words aren't a failure",
          escape.failure({"ev": "speech_heard", "text": "Kal Ort Por"}, ME) is None)


def world(mana_items=(), extra=()):
    items = {h(PACK): {"graphic": 0x0E75, "layer": 0x15, "container": h(ME)},
             h(BAG): {"graphic": 0x0E76, "container": h(PACK)}}
    for i, (g, where, name) in enumerate(mana_items):
        items[h(0x46000000 + i)] = {"graphic": g, "container": h(where), "amount": 5, "name": name}
    for serial, g, where in extra:
        items[h(serial)] = {"graphic": g, "container": h(where)}
    return {"items": items, "self": {}}


def test_can_cast():
    regs = [(g, BAG, "") for g in escape.REAGENTS]
    check("all three reagents in a bag + mana", escape.can_cast_recall(world(regs), ME, 40))
    check("missing mandrake", not escape.can_cast_recall(world(regs[:2]), ME, 40))
    check("too little mana", not escape.can_cast_recall(world(regs), ME, escape.RECALL_MANA - 1))
    check("a spellstone replaces reagents",
          escape.can_cast_recall(world([(0x3F1F, BAG, "arielle's bauble")]), ME, 40))


def test_find_books():
    w = world(extra=[(0x46853D39, 0x22C5, BAG), (0x46861AB8, 0x71AF, PACK), (0x46990000, 0x22C5, 0x40000001)])
    books = escape.find_books(w, ME)
    check("books in the pack at any depth, tomes first; not one outside the pack",
          books == [(0x46861AB8, "runetome"), (0x46853D39, "runebook")], books)


if __name__ == "__main__":
    for t in (test_runebook, test_runetome, test_failures, test_can_cast, test_find_books):
        print(t.__name__)
        t()
    print("FAILED: " + ", ".join(FAILURES) if FAILURES else "ALL PASS")
    sys.exit(1 if FAILURES else 0)

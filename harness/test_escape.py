"""Tests for harness/escape.py: reading the default rune, the charges and the
Cast Recall button from the runebook and rune tome gumps exactly as the server
sent them (harness/testdata/escape_gumps.json, captured live 2026-10-02), plus
the failure messages, what can cast Recall, the disturb-recovery rule against
the live retries, and escape()'s retries against a simulated server replaying
the hits of the 2026-10-03 Nusero death. No network.

Run: python harness/test_escape.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import actions  # noqa: E402
import combat  # noqa: E402
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


def test_sextant():
    check("sextant: DTF Loot Chest 17°8'N 162°21'W is the DTF landing (4134, 1429) within 2 tiles",
          max(abs(a - b) for a, b in zip(escape.sextant_to_tile("17° 8'N", "162° 21'W"), (4134, 1429))) <= 2,
          escape.sextant_to_tile("17° 8'N", "162° 21'W"))
    check("sextant: Prev Bank 8°47'N 19°53'E is Prevalia's bank (1606, 1524)",
          escape.sextant_to_tile("8° 47'N", "19° 53'E") == (1606, 1524), escape.sextant_to_tile("8° 47'N", "19° 53'E"))
    check("sextant: past 180 degrees east wraps to west (tile -> lines)",
          escape.tile_to_sextant(4134, 1429) == ("17° 8'N", "162° 21'W"), escape.tile_to_sextant(4134, 1429))
    bad = [(x, y) for x in range(0, 5120, 97) for y in range(0, 4096, 89)
           if max(abs(a - b) for a, b in zip(escape.sextant_to_tile(*escape.tile_to_sextant(x, y)), (x, y))) > 1]
    check("sextant: tile -> lines -> tile within a tile over the whole map", not bad, bad[:5])
    check("sextant: not a sextant line", escape.sextant_value("Charges: ") is None)


def test_runebook_entries():
    e = parse(escape.runebook_entries, "runebook_dan_dtf")
    names = [r["name"] for r in e]
    check("Dan's runebook: 11 named runes in entry order, 'Empty' slots left out",
          names == ["Prev Bank", "Cambria MG", "Terran MG", "Cambria Bank", "Anchor's Rest", "Ossuary",
                    "Anchor's Rest MG", "Khal Draco", "SSC", "Shelter Stairs", "DTF Loot Chest"]
          and [r["i"] for r in e] == list(range(11)), names)
    by = {r["name"]: r for r in e}
    check("Dan's runebook: DTF Loot Chest lands at the DTF landing (4134, 1429), facet 0 (hue 81)",
          (by["DTF Loot Chest"]["x"], by["DTF Loot Chest"]["y"], by["DTF Loot Chest"]["facet"]) == (4134, 1429, 0),
          by["DTF Loot Chest"])
    check("interned texts: Shelter Stairs shares Khal Draco's longitude line, tied by layout position",
          by["Shelter Stairs"]["x"] == by["Khal Draco"]["x"] and by["Shelter Stairs"]["y"] != by["Khal Draco"]["y"],
          (by["Shelter Stairs"], by["Khal Draco"]))
    check("Dan's runebook: default entry 10, 8 charges",
          parse(escape.parse_runebook, "runebook_dan_dtf") == {"default": 10, "charges": 8, "entries": 11})
    e = parse(escape.runebook_entries, "runebook_charges")
    check("two entries drawn from one interned name, each with its own sextant lines",
          [(r["name"], r["x"], r["y"]) for r in e] == [("Prevalia",) + escape.sextant_to_tile("8° 42'N", "19° 41'E"),
                                                       ("Prevalia",) + escape.sextant_to_tile("10° 27'N", "19° 58'E")], e)
    check("empty runebook: no entries", parse(escape.runebook_entries, "runebook_empty") == [])


class BookServer:
    """Dan's runebook (the live gump): a double-click opens it, a recall button (2+6i
    charge, 5+6i spell) lands on entry i's tile; 0 closes it."""

    BOOK = 0x49865F8F

    def __init__(self):
        self.g = G["runebook_dan_dtf"]
        self.tiles = {r["i"]: [r["x"], r["y"], 0] for r in escape.runebook_entries(self.g["layout"], self.g["lines"])}
        self.pos, self.pressed, self.queue = [1706, 3181, 0], [], []

    def send(self, pkt):
        if pkt[0] == 0x06:
            self.queue.append({"ev": "gump_open", **self.g})
            return
        button = int.from_bytes(pkt[11:15], "big")
        self.pressed.append(button)
        if button >= 2 and (button - 2) % 6 in (0, 3):
            self.pos = self.tiles[(button - 2) // 6]

    def poll(self):
        evs, self.queue = self.queue, []
        items = {h(PACK): {"graphic": 0x0E75, "layer": 0x15, "container": h(ME)},
                 h(self.BOOK): {"graphic": 0x22C5, "container": h(PACK), "name": "Dan's book"}}
        return ({"movement": {"self_serial": ME, "pos": list(self.pos)},
                 "world": {"items": items, "self": {"map": 0, "mana": 60}}}, evs)


class RechargingBookServer(BookServer):
    """The same book moments after a use (live 2026-10-05): a double-click only says "This book needs
    time to recharge." (cliloc 502406). Casting Recall (spell 32) brings a cursor; answering it with the
    book lands on its default rune (entry 10, DTF Loot Chest)."""

    def __init__(self, reagents=True):
        super().__init__()
        self.reagents, self.sent, self.cursor, self.disturb = reagents, [], None, False

    def send(self, pkt):
        self.sent.append(pkt)
        if pkt[0] == 0x06:
            self.queue.append({"ev": "cliloc", "cliloc": 502406, "text": "This book needs time to recharge."})
        elif pkt == actions.cast_spell(escape.RECALL_SPELL) and self.disturb:
            self.queue += [{"ev": "speech_heard", "serial": ME, "type": 10, "text": "Kal Ort Por"},
                           {"ev": "cliloc", "cliloc": 500641, "text": "Your concentration is disturbed, thus ruining thy spell."}]
        elif pkt == actions.cast_spell(escape.RECALL_SPELL):
            self.cursor = {"active": True, "cursor_id": 0x77, "cursor_type": 0, "target_type": 0}
        elif pkt[0] == 0x6C and self.cursor and int.from_bytes(pkt[7:11], "big") == self.BOOK:
            self.cursor = None
            self.pos = self.tiles[10]

    def poll(self):
        st, evs = super().poll()
        if self.reagents:
            for i, g in enumerate((0x0F7A, 0x0F7B, 0x0F86)):        # black pearl, blood moss, mandrake root
                st["world"]["items"][f"0x4000000{i}"] = {"graphic": g, "amount": 10, "container": h(PACK)}
        st["world"]["target"] = self.cursor or {"active": False}
        return st, evs


def test_recharging_book():
    s = RechargingBookServer()
    r = escape.recall(s, s.BOOK)
    check("a recharging book: Recall cast and answered with the book itself, landing on its default rune",
          r["ok"] and r["method"] == "spell_on_book" and r["to"] == s.tiles[10][:2]
          and s.sent[-2:][0] == actions.cast_spell(escape.RECALL_SPELL) and s.sent[-1][0] == 0x6C, (r, s.sent))
    s = RechargingBookServer(reagents=False)
    r = escape.recall(s, s.BOOK)
    check("no reagents for the spell: a 'recharging' failure (no cast), retried after RECHARGE_WAIT_S",
          not r["ok"] and r["failure"] == "recharging" and escape.retry_wait(r) == escape.RECHARGE_WAIT_S
          and actions.cast_spell(escape.RECALL_SPELL) not in s.sent, r)
    s = RechargingBookServer()
    r = escape.recall(s, s.BOOK, rune="SSC")
    check("a named rune can't go by the spell on the book: 'recharging'", not r["ok"] and r["failure"] == "recharging"
          and actions.cast_spell(escape.RECALL_SPELL) not in s.sent, r)
    s = RechargingBookServer()
    lines = []
    r = escape.escape(s, s.BOOK, attempts=2, log=lines.append)
    check("escape() over a recharging book lands by the spell on the book and logs the try (live 2026-10-05 its "
          "log line raised TypeError on the default rune: no name, no index)",
          r["ok"] and r["method"] == "spell_on_book" and lines and "rune the default" in lines[-1], (r, lines))
    s = RechargingBookServer(reagents=False)
    lines = []
    r = escape.escape(s, s.BOOK, attempts=2, budget_s=2.5, log=lines.append)
    check("escape() with the book recharging and no reagents: 'recharging' tries logged until the budget ends",
          not r["ok"] and r["failure"] == "recharging" and len(lines) >= 2, (r, lines))
    s = RechargingBookServer()
    s.disturb = True                      # live 2026-10-06 16:33: a red's Energy Bolt 1.4 s into the Recall
    r = escape.recall(s, s.BOOK)
    check("the spell on the book disturbed before its cursor: a 'disturbed' cast (method spell_on_book, cast_s from "
          "our words) that waits its disturb recovery, not a 'recharging' refusal",
          not r["ok"] and r["failure"] == "disturbed" and r["method"] == "spell_on_book" and r["cast_s"] is not None
          and escape.retry_wait(r) == escape.disturb_recovery(r["cast_s"]) + escape.RECOVERY_MARGIN_S, r)
    lines = []
    r = escape.escape(s, s.BOOK, attempts=1, log=lines.append)
    check("escape() counts it as a cast (attempts 1 spent: it stops)", not r["ok"] and r["attempts"] == 1, (r, lines))


def test_runebook_read_and_recall_by_name():
    s = BookServer()
    b = escape.read_book(s, s.BOOK)
    check("read_book: runebook read in one gump and closed, nothing recalled",
          s.pressed == [0] and b["kind"] == "runebook" and b["serial"] == "0x49865F8F" and len(b["runes"]) == 11,
          (s.pressed, b["kind"], len(b["runes"])))
    check("read_book: default entry and its name, charges, title from the item name",
          (b["default"], b["default_name"], b["charges"], b["title"]) == (10, "DTF Loot Chest", 8, "Dan's book"), b)
    r = escape.recall(s, s.BOOK, rune="ssc")
    check("recall by name in a runebook: 'ssc' is entry 8, its charge button 2+6*8",
          r["ok"] and r["rune"] == 8 and r["name"] == "SSC" and s.pressed[-1] == 50 and r["to"] == s.tiles[8][:2],
          (r, s.pressed))
    r = escape.recall(s, s.BOOK)
    check("recall without a name: the default rune (DTF Loot Chest, button 62)",
          r["ok"] and r["name"] == "DTF Loot Chest" and s.pressed[-1] == 62, (r, s.pressed))
    r = escape.recall(s, s.BOOK, rune="SSC", entry=0)
    check("recall by entry: the index wins over the name (two runes may share one), entry 0's charge button 2",
          r["ok"] and r["rune"] == 0 and r["name"] == "Prev Bank" and s.pressed[-1] == 2 and r["to"] == s.tiles[0][:2],
          (r, s.pressed))
    try:
        escape.recall(s, s.BOOK, rune="Nowhere")
        check("an unknown rune name raises", False)
    except escape.RecallError:
        check("an unknown rune name raises and closes the book", s.pressed[-1] == 0, s.pressed)


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
    rows = parse(escape.runetome_rows, "runetome_main_witcher_276")
    r = parse(escape.parse_runetome_main, "runetome_main_witcher_276")
    check("Witcher library tome: 26 rows read by name, 40 public recall charges",
          len(rows) == 26 and r["entries"] == 26 and r["charges"] == 40, (len(rows), r))
    hit = [i for i, n in rows.items() if escape.rune_matches(n, "286")]
    check("rune '286' is row 10 (gem 110: the button that recalled to it live)",
          hit == [10] and rows[10] == "286 - Midlands Ruins 1 (South)", (hit, rows.get(10)))
    check("a number matches whole: '28' finds no row, '2' neither",
          not any(escape.rune_matches(n, w) for n in rows.values() for w in ("28", "2")))
    check("rune names match whole and case-blind: '22A' finds the DTF row '22a', trailing spaces aside",
          escape.rune_matches("22a", "22A") and escape.rune_matches("Blood ele island ", "blood ele island")
          and not escape.rune_matches("221", "22"))


def test_library_tome_read():
    print("== reading a library tome: title, rows, every rune's tile from the detail pages ==")
    det = parse(escape.parse_runetome_detail, "runetome_detail_dtf_bad_places_24")
    check("detail page: each column's name and tile; a long centred right-hand name still pairs with its tile",
          det == [{"col": 0, "name": "SSC West Entrance", "x": 3353, "y": 762},
                  {"col": 1, "name": "Undermountain NW Entrance (Rear)", "x": 1815, "y": 669}], det)
    check("main page title", parse(escape.runetome_title, "runetome_main_dtf_bad_places") == "Bad Places")
    for n in (26, 5):
        s = TomeServer(n)
        r = escape.read_runetome(s, 0x4AAA0001, wait=lambda: None)
        want = [{"row": i, "name": s.names[i], "x": 1000 + i, "y": 2000 + i} for i in range(n)]
        check(f"{n} runes: every row read with its own tile, ending on a closed gump",
              r["rows"] == want and r["title"] == "Bad Places" and s.pressed[-1] == 0, (r["rows"][:3], s.pressed))
        check(f"{n} runes: pages flipped pair by pair (200, then next {(n + 1) // 2 - 1} times), no recall pressed",
              s.pressed == [200] + [escape.DETAIL_NEXT] * ((n + 1) // 2 - 1) + [0], s.pressed)
    s = TomeServer(4, shuffle=True)
    r = escape.read_runetome(s, 0x4AAA0001, wait=lambda: None)
    check("a detail page naming another rune than the main page's row (order changed meanwhile) leaves the tile unknown",
          [x["x"] for x in r["rows"]] == [None, None, 1002, 1003], r["rows"])


class TomeServer:
    """A locked-down tome of n runes: the main page is the live DTF 'Bad Places' page cut
    to n rows; detail pages are the live 24-25 page with each rune's name and tile
    (1000 + i, 2000 + i), the right column dropped on an odd last page. shuffle: the
    first page names its two runes the other way round."""

    def __init__(self, n, shuffle=False):
        main = G["runetome_main_dtf_bad_places"]
        rows = escape._runetome_row_texts(main["layout"])
        keep = {f"{100 + i}" for i in range(n)} | {f"{200 + i}" for i in range(n)}
        toks = []
        for t in escape._TOKEN.findall(main["layout"]):
            f = t.split()
            if f[0] == "button" and 100 <= int(f[7]) < 300 and f[7] not in keep:
                continue
            if f[0] == "text" and any(li == int(f[4]) for r, (li, _) in rows.items() if r >= n):
                continue
            toks.append("{ " + t.strip() + " }")
        self.main = {"serial": 1, "gump_id": "0x09F5976B", "layout": "".join(toks), "lines": main["lines"]}
        self.names = [main["lines"][rows[i][0]] for i in range(n)]
        self.n, self.shuffle, self.page, self.pressed, self.queue = n, shuffle, None, [], []

    def detail(self, first):
        """Live: the 0-1 page carries the next-pair button (5, art 4007), the last (24-25) only 2."""
        d = G["runetome_detail_dtf_bad_places_24"]
        lines = list(d["lines"])
        a, b = (first + 1, first) if self.shuffle and first == 0 else (first, first + 1)
        lines[0], lines[1] = self.names[a], f"({1000 + a}, {2000 + a})"
        layout = d["layout"]
        if b < self.n:
            lines[10], lines[11] = self.names[b], f"({1000 + b}, {2000 + b})"
        else:
            layout = "".join("{ " + t.strip() + " }" for t in escape._TOKEN.findall(layout)
                             if t.split()[0] not in ("text", "button") or int(t.split()[1]) < 340)
        if first + 2 < self.n:
            layout += "{ button 583 396 4007 4009 1 0 5 }"
        return {"serial": 2, "gump_id": "0x09F5976B", "layout": layout, "lines": lines}

    def send(self, pkt):
        if pkt[0] == 0x06:
            self.queue.append({"ev": "gump_open", **self.main})
            return
        button = int.from_bytes(pkt[11:15], "big")
        self.pressed.append(button)
        if 200 <= button < 300:
            self.page = (button - 200) // 2 * 2
        elif button == escape.DETAIL_NEXT:
            self.page += 2
        else:
            return
        self.queue.append({"ev": "gump_open", **self.detail(self.page)})

    def poll(self):
        evs, self.queue = self.queue, []
        return {"movement": {"self_serial": ME, "pos": [4152, 1429, 6]}, "world": {"items": {}, "self": {}}}, evs


def test_failures():
    check("cliloc 500641 is a disturbed cast",
          escape.failure({"ev": "cliloc", "cliloc": 500641}, ME) == "disturbed")
    check("cliloc 501942 'That location is blocked.' (live 2026-10-05, a DTF tome rune) is blocked: no retry",
          escape.failure({"ev": "cliloc", "cliloc": 501942}, ME) == "blocked"
          and escape.retry_wait({"failure": "blocked"}) is None)
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
    regs = [(g, BAG, "") for g in combat.SPELL_REAGENTS[escape.RECALL]]
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


# Every live retry after a disturbed cast (memory store, 2026-09-29 .. 10-03; docs/research/
# SPELL_INTERRUPTS.md): (cast time, how far the cast got when disturbed, the next try's
# press after the 500641 at the proxy, the server took it). The proxy sees the 500641 one
# way late and the press reaches the server one way late: RTT ~0.055 s (c2s -> own words).
LIVE_RETRIES = [
    (1.25, 0.363, 2.196, True), (1.25, 0.592, 2.415, True), (1.25, 0.727, 0.457, True),
    (1.25, 0.468, 0.517, True), (1.25, 1.002, 0.493, True), (1.25, 1.088, 0.504, True),
    (1.25, 0.959, 0.030, False), (1.25, 0.959, 0.539, True), (1.25, 0.314, 0.465, True),
    (1.25, 1.115, 0.502, True), (1.25, 1.075, 0.454, True), (1.25, 0.609, 2.379, True),
    (1.25, 0.082, 2.245, True), (1.25, 1.147, 0.475, True), (1.25, 1.154, 0.497, True),
    (1.25, 0.651, 0.460, True), (1.25, 1.176, 0.504, True), (1.25, 0.207, 0.486, False),
    (1.25, 0.207, 1.025, True), (1.25, 0.735, 1.525, True), (1.25, 1.203, 0.354, True),
    (1.25, 1.113, 0.457, True), (1.25, 0.134, 0.458, False), (1.25, 0.134, 0.592, False),
    (1.25, 0.134, 1.141, True), (1.25, 0.126, 0.460, False), (1.25, 0.126, 0.997, True),
    (1.25, 0.392, 2.250, True), (2.0, 0.828, 0.167, False), (2.0, 0.828, 0.684, True)]
RTT = 0.055


def test_disturb_recovery_fits_live_retries():
    wrong = [r for r in LIVE_RETRIES
             if (r[2] + RTT >= escape.disturb_recovery(r[1], r[0])) != r[3]]
    check("disturb recovery max(0.2, 1 - sqrt(elapsed/cast)) predicts all 30 live retries "
          "(refused early ones, taken later ones)", not wrong, wrong)
    check("a fixed recovery can't: refused at 0.59 s after an early disturb, taken at 0.35 s after a late one",
          escape.disturb_recovery(0.134, 1.25) > 0.592 + RTT and escape.disturb_recovery(1.203, 1.25) < 0.354)
    check("Nusero: recall disturbed 0.83 s in -> 0.36 s (the instant retry at 0.17 s was refused)",
          abs(escape.disturb_recovery(0.828) - 0.357) < 0.005)


class Server:
    """The Outlands side of a tome recall on a fake clock (escape.time is swapped
    for it): a double-click opens the tome, a charge button starts a 2.0 s cast
    unless the caster hasn't recovered; the first hit inside a cast disturbs it
    (PvP: every hit does) and sets the recovery max(0.2, 1 - sqrt(e / 2)); a cast
    no hit reaches moves us. `hits` are server times after the escape's start."""

    L = 0.03                                    # one-way latency

    def __init__(self, hits=(), death_at=None, recovered_at=0.0, refuse=None, lag=0.0, frozen=False):
        self.lag = lag                          # extra server delay before a cast lands (live 2026-10-06: 5.18 s)
        self.frozen = frozen                    # paralyzed: every cast "You cannot cast a spell while frozen."
        self.dclicks = []                       # client time of every double-click (the book)
        self.t = 0.0
        self.hits = sorted(hits)
        self.death_at = death_at
        self.next_spell = recovered_at
        self.refuse = refuse                    # a cliloc answering every cast, e.g. heat of battle
        self.queue = []                         # (client time, event)
        self.moves = []                         # (client time, pos)
        self.casts = []                         # (start, end, how): every cast the server ran
        self.refused = 0
        self.gump = G["runetome_main_charges"]

    # escape.time
    def monotonic(self):
        return self.t

    def sleep(self, dt):
        self.t += dt

    # escape IO
    def send(self, pkt: bytes):
        s = self.t + self.L
        if pkt[0] == 0x06:
            self.dclicks.append(self.t)
            self.queue.append((s + self.L, {"ev": "gump_open", **self.gump}))
            return
        button = int.from_bytes(pkt[11:15], "big")
        if pkt[0] != 0xB1 or button == 0:
            return
        if self.refuse or self.frozen:
            self.queue.append((s + self.L, {"ev": "cliloc", "cliloc": self.refuse or 502643}))
            return
        if s < self.next_spell:
            self.refused += 1
            self.queue.append((s + self.L, {"ev": "cliloc", "cliloc": 502644}))
            return
        self.queue.append((s + self.L, {"ev": "speech_heard", "type": 10, "serial": ME, "text": "Kal Ort Por"}))
        end = s + 2.0 + self.lag
        hit = next((h for h in self.hits if s <= h < end), None)
        if hit is None:
            self.moves.append((end + self.L, [1500, 1600, 0]))
            self.casts.append((s, end, "arrived"))
            self.next_spell = end + 0.2
        else:
            self.queue.append((hit + self.L, {"ev": "cliloc", "cliloc": 500641}))
            self.casts.append((s, hit, "disturbed"))
            self.next_spell = hit + max(0.2, 1 - ((hit - s) / 2.0) ** 0.5)

    def poll(self):
        evs = [e for t, e in self.queue if t <= self.t]
        self.queue = [(t, e) for t, e in self.queue if t > self.t]
        pos = [500, 2135, 0]
        for t, p in self.moves:
            if t <= self.t:
                pos = p
        dead = self.death_at is not None and self.t >= self.death_at
        tome = h(0x46861AB8)
        items = {h(PACK): {"graphic": 0x0E75, "layer": 0x15, "container": h(ME)},
                 tome: {"graphic": 0x71AF, "container": h(PACK)}}
        return ({"movement": {"self_serial": ME, "pos": pos},
                 "world": {"items": items, "self": {"map": 1, "mana": 60, "dead": dead}}}, evs)


def run_escape(server, **kw):
    real = escape.time
    escape.time = server
    try:
        return escape.escape(server, 0x46861AB8, log=lambda m: None, **kw)
    finally:
        escape.time = real


# Bastet at Nusero, 2026-10-03 18:04, seconds after the runner's first double-click
# (39.351): Weaken landed 40.313, Harm 42.007, then melee 46.500 .. 51.696; dead 53.012.
NUSERO_HITS = [0.962, 2.656, 7.149, 8.458, 9.744, 11.044, 12.345]
NUSERO_DEATH = 13.661


def test_escape_nusero_replay():
    s = Server(NUSERO_HITS, death_at=NUSERO_DEATH)
    r = run_escape(s)
    arrived = [c for c in s.casts if c[2] == "arrived"]
    check("Nusero replay: the escape lands before Bastet's first melee hit (7.15 s)",
          r["ok"] and arrived and arrived[0][1] < 7.149, (r.get("failure"), s.casts))
    check("Nusero replay: no try wasted on 'not yet recovered'", s.refused == 0
          and all(t["failure"] != "not recovered" for t in r["tries"]), r["tries"])
    check("Nusero replay: Weaken and Harm each break one cast, the third lands; attempts = casts",
          [c[2] for c in s.casts] == ["disturbed", "disturbed", "arrived"] and r["attempts"] == 3, s.casts)
    first_retry = s.casts[1][0] - s.casts[0][1]
    check("the recast reaches the server right after the recovery (not early, within 0.15 s)",
          0 <= first_retry - (1 - ((s.casts[0][1] - s.casts[0][0]) / 2) ** 0.5) < 0.15, first_retry)


def test_escape_early_disturb():
    s = Server([0.25, 1.2])                      # disturbed ~0.13 s in: ~0.75 s recovery
    r = run_escape(s)
    check("an early disturb: waits out the long recovery, no refusal, lands", r["ok"] and s.refused == 0,
          (s.casts, r["tries"]))
    check("tries record how far each cast got and the wait before the next",
          r["tries"][0]["failure"] == "disturbed" and 0.0 < r["tries"][0]["cast_s"] < 0.25
          and r["tries"][0]["wait_s"] > 0.7, r["tries"][0])


def test_escape_stops():
    melee = [0.5 + 1.3 * i for i in range(40)]
    s = Server(melee)
    r = run_escape(s, budget_s=10.0)
    check("hit every 1.3 s: no cast lands; it stops at the budget, not after 3 casts",
          not r["ok"] and r["failure"] == "disturbed" and r["attempts"] >= 6 and s.t <= 10.0 + 2.5,
          (r["attempts"], s.t))
    s = Server(melee, death_at=4.0)
    r = run_escape(s)
    check("death ends the escape", not r["ok"] and r["failure"] == "dead" and s.t < 6.0, (r["failure"], s.t))
    s = Server(refuse=1005564)
    r = run_escape(s)
    check("heat of battle: one try, no recast", r["failure"] == "heat of battle" and len(r["tries"]) == 1,
          r["tries"])
    s = Server(recovered_at=0.3)
    r = run_escape(s, attempts=1)
    check("attempts counts casts: a 'not recovered' refusal doesn't use up attempts=1",
          r["ok"] and r["attempts"] == 1 and [t["failure"] for t in r["tries"]] == ["not recovered", None],
          r["tries"])


def test_escape_late_arrival():
    # live 2026-10-06 (lumber-20261006-101348-70b3): a recall out landed 5.18 s after the press, past ARRIVE_WAIT_S;
    # read as "no arrival", the retry from a tome no longer in reach failed and Dan was left at a hot landing
    s = Server(lag=3.2)
    r = run_escape(s)
    check("a recall that lands 5.2 s after the press is an arrival, after one cast",
          r["ok"] and len(s.casts) == 1 and r["attempts"] == 1 and r["press_to_arrival_s"] > escape.ARRIVE_WAIT_S,
          (r.get("failure"), s.casts, r.get("press_to_arrival_s")))


def test_escape_between():
    s = Server(frozen=True)
    calls = []

    def between(st, last):          # the runner's flight aid: a trapped pouch pops and frees us
        calls.append((s.t, last["failure"], (st["world"].get("self") or {}).get("mana")))
        s.frozen = False
        return s.t + 0.55
    r = run_escape(s, between=between)
    check("a 'frozen' refusal reaches `between` (with the state), once; the next try lands",
          r["ok"] and [c[1:] for c in calls] == [("frozen", 60)] and [t["failure"] for t in r["tries"]] == ["frozen", None],
          (calls, r["tries"]))
    check("the next book double-click waits for the time `between` returned (the server's action delay)",
          len(s.dclicks) == 2 and s.dclicks[1] >= calls[0][0] + 0.55, (s.dclicks, calls))
    plain = run_escape(Server([0.25, 1.2]))
    s = Server([0.25, 1.2])
    seen = []
    r = run_escape(s, between=lambda st, last: seen.append(last["failure"]))
    check("`between` returning None changes nothing: the early-disturb escape's tries are the same as without it",
          r["ok"] and r["tries"] == plain["tries"] and seen == [t["failure"] for t in plain["tries"][:-1]],
          (seen, r["tries"], plain["tries"]))


if __name__ == "__main__":
    for t in (test_runebook, test_sextant, test_runebook_entries, test_runebook_read_and_recall_by_name,
              test_recharging_book,
              test_runetome, test_library_tome_read, test_failures, test_can_cast, test_find_books,
              test_disturb_recovery_fits_live_retries, test_escape_nusero_replay, test_escape_early_disturb,
              test_escape_stops, test_escape_late_arrival, test_escape_between):
        print(t.__name__)
        t()
    print("FAILED: " + ", ".join(FAILURES) if FAILURES else "ALL PASS")
    sys.exit(1 if FAILURES else 0)

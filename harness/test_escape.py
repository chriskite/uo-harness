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

    def __init__(self, hits=(), death_at=None, recovered_at=0.0, refuse=None):
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
            self.queue.append((s + self.L, {"ev": "gump_open", **self.gump}))
            return
        button = int.from_bytes(pkt[11:15], "big")
        if pkt[0] != 0xB1 or button == 0:
            return
        if self.refuse:
            self.queue.append((s + self.L, {"ev": "cliloc", "cliloc": self.refuse}))
            return
        if s < self.next_spell:
            self.refused += 1
            self.queue.append((s + self.L, {"ev": "cliloc", "cliloc": 502644}))
            return
        self.queue.append((s + self.L, {"ev": "speech_heard", "type": 10, "serial": ME, "text": "Kal Ort Por"}))
        end = s + 2.0
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


if __name__ == "__main__":
    for t in (test_runebook, test_runetome, test_failures, test_can_cast, test_find_books,
              test_disturb_recovery_fits_live_retries, test_escape_nusero_replay, test_escape_early_disturb,
              test_escape_stops):
        print(t.__name__)
        t()
    print("FAILED: " + ", ".join(FAILURES) if FAILURES else "ALL PASS")
    sys.exit(1 if FAILURES else 0)

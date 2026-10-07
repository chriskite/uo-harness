"""Tests for harness/ledger.py on synthetic state-port responses (no network).

Run: python harness/test_ledger.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ledger import Ledger, classify, load_woods, woods_summary  # noqa: E402

FAILURES = []
ME = 0x00094375
PACK = 0x40000010
BOX = 0x40000099
BAG = 0x40000020
CORPSE = 0x40000500
LOGS, BOARDS = 0x1BDD, 0x1BD7
DULL = 2419
# never the real harness/data/woods.json: tests must not depend on it
NOWOODS = os.path.join(tempfile.gettempdir(), "no_such_dir_x", "woods.json")


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(name)


def eq(name, got, want):
    check(name, got == want, f"(got {got!r}, want {want!r})")


def h(s):
    return f"0x{s:08X}"


def item(graphic, amount=1, container=PACK, hue=0, layer=None):
    d = {"graphic": graphic, "amount": amount, "hue": hue}
    if container is not None:
        d["container"] = h(container)
    if layer is not None:
        d["layer"] = layer
    return d


def state(contents, *, body=0x190, hits=50, extra=None, pack=True):
    items = {h(s): it for s, it in contents.items()}
    if pack:
        items[h(PACK)] = item(0x0E75, container=ME, layer=0x15)
    items.update({h(s): it for s, it in (extra or {}).items()})
    return {"movement": {"pos": [1000, 1000, 0, 0], "self_serial": ME},
            "world": {"self": {"serial": h(ME), "hits": hits, "hits_max": 50,
                               "stats": {"graphic": body}},
                      "items": items, "mobiles": {}, "labels": {}}}


def fixture_woods(tmp):
    path = os.path.join(tmp, "woods.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"source": "test fixture", "woods": [
            {"name": "ordinary", "log_graphics": ["0x1BDD", "0x1BDE"], "log_hue": 0,
             "board_hue": 0, "min_skill": 0.0, "value_gp": 9.5, "notes": ""},
            {"name": "dullwood", "log_graphics": ["0x1BDD"], "log_hue": DULL,
             "board_hue": DULL, "min_skill": 65.0, "value_gp": None, "notes": ""},
            {"log_graphics": ["0x1BDD"], "log_hue": 5},            # no name: skipped
            {"name": "broken", "log_graphics": ["zz"], "log_hue": 7},  # bad graphic: skipped
        ]}, f)
    return path


def test_woods():
    print("== wood classification ==")
    with tempfile.TemporaryDirectory() as tmp:
        w = load_woods(fixture_woods(tmp))
        eq("fixture entries (malformed skipped)", [e["name"] for e in w.entries],
           ["ordinary", "dullwood"])
        eq("ordinary log", classify(item(LOGS), w), ("log", "ordinary"))
        eq("second log graphic", classify(item(0x1BDE), w), ("log", "ordinary"))
        eq("dullwood log by hue", classify(item(LOGS, hue=DULL), w), ("log", "dullwood"))
        eq("dullwood board by hue", classify(item(BOARDS, hue=DULL), w), ("board", "dullwood"))
        eq("unknown hue", classify(item(LOGS, hue=999), w), ("log", "unknown(999)"))
        eq("not wood", classify(item(0x0F43), w), None)
        eq("value lookup", (w.value_gp("ordinary"), w.value_gp("dullwood")), (9.5, None))
        items = {1: item(LOGS, 10), 2: item(LOGS, 3, hue=DULL), 3: item(BOARDS, 20),
                 4: item(BOARDS, 2, hue=DULL), 5: item(0x0F43)}
        eq("woods_summary", woods_summary(items, woods=w), {"ordinary": 30, "dullwood": 5})
        eq("woods_summary boards", woods_summary(items, kind="board", woods=w),
           {"ordinary": 20, "dullwood": 2})
        eq("woods_summary list input", woods_summary(list(items.values()), kind="log", woods=w),
           {"ordinary": 10, "dullwood": 3})
        bad = os.path.join(tmp, "bad.json")
        with open(bad, "w") as f:
            f.write("{not json")
        eq("malformed file -> empty table", load_woods(bad).entries, [])
        eq("malformed file -> unknown(0)", classify(item(LOGS), load_woods(bad)),
           ("log", "unknown(0)"))
    none = load_woods(NOWOODS)
    eq("absent file -> empty table", none.entries, [])
    eq("absent file log", classify(item(LOGS), none), ("log", "unknown(0)"))
    eq("absent file board", classify(item(BOARDS, hue=DULL), none), ("board", f"unknown({DULL})"))
    eq("absent file summary", woods_summary({1: item(LOGS, 4), 2: item(LOGS, 1, hue=DULL)},
                                            woods=none),
       {"unknown(0)": 4, f"unknown({DULL})": 1})


def base():
    return {0x100: item(LOGS, 10), 0x101: item(BOARDS, 20), 0x102: item(0x0F43),
            BAG: item(0x0E76)}


def test_expected_vs_theft():
    print("== expected moves vs unexplained losses ==")
    with tempfile.TemporaryDirectory() as tmp:
        led = Ledger(fixture_woods(tmp))
    d = led.observe(state(base()), now=0)
    eq("first observation is a baseline", (d.first, d.lost, d.gained), (True, [], []))
    # store the boards in the room box: expected, not theft
    c = base()
    c[0x101] = item(BOARDS, 20, container=BOX)
    d = led.observe(state(c), expected={("moved_out", 0x101)}, now=1)
    eq("store -> expected", [(e["serial"], e["cause"], e["amount"]) for e in d.lost],
       [(0x101, "expected", 20)])
    eq("store destination", d.lost[0]["to"], h(BOX))
    eq("store wood tag", (d.lost[0]["class"], d.lost[0]["wood"]), ("board", "ordinary"))
    eq("store -> no theft", (d.unexplained_losses, d.theft_suspected), ([], False))
    # an unexplained stack decrease is theft
    c2 = dict(c)
    del c2[0x101]
    c2[0x100] = item(LOGS, 7)
    d = led.observe(state(c2), now=2)
    eq("stack decrease -> unexplained", [(e["serial"], e["amount"], e["cause"])
                                          for e in d.unexplained_losses],
       [(0x100, 3, "unexplained")])
    eq("theft suspected", d.theft_suspected, True)
    eq("alive during theft", (d.alive, d.death), (True, False))
    # an expectation declared before the server moved the item carries over
    led2 = Ledger(NOWOODS)
    led2.observe(state(base()), now=0)
    d = led2.observe(state(base()), expected=[("moved_out", 0x102)], now=1)
    eq("expectation before the move: nothing yet", d.lost, [])
    c = base()
    del c[0x102]
    d = led2.observe(state(c), now=2)
    eq("pending expectation covers the later move", [e["cause"] for e in d.lost], ["expected"])
    eq("absent woods -> unknown wood tag on gains/losses",
       led2.summary(), {"unknown(0)": 30})
    # expectations expire
    led3 = Ledger(NOWOODS, expect_ttl_s=5)
    led3.observe(state(base()), now=0)
    led3.expect(("moved_out", 0x102), now=0)
    c = base()
    del c[0x102]
    d = led3.observe(state(c), now=10)
    eq("gone with an expired expectation: settling first", (d.lost, led3.settling), ([], {0x102: 10}))
    d = led3.observe(state(c), now=11)
    eq("expired expectation -> unexplained once settled", [e["cause"] for e in d.lost], ["unexplained"])
    # partial consumption with an amount, then the rest by graphic
    led4 = Ledger(NOWOODS)
    led4.observe(state(base()), now=0)
    c = base()
    c[0x100] = item(LOGS, 4)
    d = led4.observe(state(c), expected=[("consumed", 0x100, 6)], now=1)
    eq("consumed 6 of 10", [(e["cause"], e["amount"]) for e in d.lost], [("expected", 6)])
    c = base()
    c[0x100] = item(LOGS, 1)
    d = led4.observe(state(c), now=2)
    eq("consumed budget used up -> further decrease unexplained",
       [(e["cause"], e["amount"]) for e in d.lost], [("unexplained", 3)])
    del c[0x100]
    d = led4.observe(state(c), expected=[("spent", LOGS)], now=3)
    eq("spent by graphic", [(e["cause"], e["amount"]) for e in d.lost], [("expected", 1)])
    # over-covering consumption: amount 2 of a 5-unit loss leaves 3 unexplained
    led5 = Ledger(NOWOODS)
    led5.observe(state(base()), now=0)
    c = base()
    c[0x100] = item(LOGS, 5)
    d = led5.observe(state(c), expected=[("consumed", 0x100, 2)], now=1)
    eq("partly covered loss split", sorted((e["cause"], e["amount"]) for e in d.lost),
       [("expected", 2), ("unexplained", 3)])


def test_merge_nested_gain():
    print("== merges, nested containers, gains ==")
    led = Ledger(NOWOODS)
    start = base()
    start[0x103] = item(BOARDS, 5)
    led.observe(state(start), now=0)
    c = base()
    c[0x101] = item(BOARDS, 25)            # 0x103 merged into 0x101
    d = led.observe(state(c), now=1)
    eq("merge -> not theft", ([e["cause"] for e in d.lost], d.unexplained_losses),
       (["merged"], []))
    c[0x102] = item(0x0F43, container=BAG)  # hatchet moved into a bag inside the pack
    d = led.observe(state(c), now=2)
    eq("move into a nested bag is no loss", (d.lost, d.gained), ([], []))
    c[0x104] = item(LOGS, 8, hue=DULL)      # chopped colored logs
    c[0x100] = item(LOGS, 12)
    d = led.observe(state(c), now=3)
    eq("gains", sorted((e["serial"], e["amount"], e.get("wood")) for e in d.gained),
       [(0x100, 2, "unknown(0)"), (0x104, 8, f"unknown({DULL})")])
    d = led.observe(state(c, pack=False), now=4)
    eq("pack invisible -> no deltas", (d.pack, d.lost, d.gained), (None, [], []))
    d = led.observe(state(c), now=5)
    eq("baseline kept across the gap", (d.lost, d.gained), ([], []))
    # a bag leaving with its contents is one loss; an expectation on the bag covers it
    c[0x105] = item(0x0F0C, 2, container=BAG)
    led.observe(state(c), now=6)
    c[BAG] = item(0x0E76, container=BOX)
    d = led.observe(state(c, extra={0x102: item(0x0F43, container=BAG),
                                    0x105: item(0x0F0C, 2, container=BAG)}),
                    expected=[("moved_out", BAG)], now=7)
    eq("bag with contents -> one expected entry",
       [(e["serial"], e["cause"], e.get("contents")) for e in d.lost], [(BAG, "expected", 2)])
    # equipping the hatchet from the pack is not theft
    c[0x106] = item(0x0F44)
    led.observe(state(c), now=8)
    del c[0x106]
    d = led.observe(state(c, extra={0x106: item(0x0F44, container=ME, layer=1)}), now=9)
    eq("equipped -> not theft", ([e["cause"] for e in d.lost], d.unexplained_losses),
       (["equipped"], []))


def test_death():
    print("== death is not theft ==")
    led = Ledger(NOWOODS)
    led.observe(state(base()), now=0)
    d = led.observe(state({}, body=0x192, hits=0), now=1)
    eq("ghost body -> death", (d.death, d.alive), (True, False))
    check("death reason names the ghost body", "ghost" in (d.death_reason or ""), d.death_reason)
    eq("losses caused by death", sorted({e["cause"] for e in d.lost}), ["death"])
    eq("no theft on death", d.unexplained_losses, [])
    d = led.observe(state({}, body=0x192), now=2)
    eq("still dead, death reported once", (d.alive, d.death), (False, False))
    d = led.observe(state({0x200: item(0x0F43)}, body=0x190), now=3)
    eq("resurrected -> alive", d.alive, True)
    led.observe(state({}, body=0x190), now=4)
    d = led.observe(state({}, body=0x190), now=5)
    eq("theft after resurrection is theft again", [e["cause"] for e in d.lost], ["unexplained"])

    # heuristic: most of the pack vanishes at once while the body is still human
    led = Ledger(NOWOODS)
    led.observe(state(base()), now=0)
    d = led.observe(state({}), now=1)
    eq("mass vanish -> death", (d.death, bool(d.unexplained_losses)), (True, False))
    check("mass vanish reason", "mass vanish" in (d.death_reason or ""), d.death_reason)
    d = led.observe(state({}, body=0x192), now=2)
    eq("ghost body confirms, no second death", (d.death, d.alive), (False, False))

    # heuristic: pack items now inside a corpse
    led = Ledger(NOWOODS)
    led.observe(state(base()), now=0)
    c = base()
    c[0x100] = item(LOGS, 10, container=CORPSE)
    d = led.observe(state(c, extra={CORPSE: item(0x2006, container=None)}), now=1)
    eq("item moved to a corpse -> death", (d.death, [e["cause"] for e in d.lost]),
       (True, ["death"]))

    # one whole item gone out of four is theft, not death: booked once it stayed gone settle_s
    led = Ledger(NOWOODS)
    led.observe(state(base()), now=0)
    c = base()
    del c[0x101]
    d = led.observe(state(c), now=1)
    eq("single item gone: settling, kept in the baseline, nothing booked yet",
       (d.death, d.lost, d.theft_suspected, led.settling, 0x101 in led.items), (False, [], False, {0x101: 1}, True))
    d = led.observe(state(c), now=1.5)
    eq("still gone inside the window: still settling", (d.lost, led.settling), ([], {0x101: 1}))
    d = led.observe(state(c), now=2)
    eq("single item gone -> theft once settled", (d.death, [e["serial"] for e in d.unexplained_losses]),
       (False, [0x101]))
    eq("gone item destination", d.unexplained_losses[0]["to"], "gone")
    eq("booked once: out of the baseline", (led.settling, 0x101 in led.items), ({}, False))
    d = led.observe(state(c), now=3)
    eq("booked theft not repeated", d.lost, [])

    # a heuristic death that no ghost body confirms lapses after death_hold_s
    led = Ledger(NOWOODS, death_hold_s=10)
    led.observe(state(base()), now=0)
    led.observe(state({}), now=1)
    led.observe(state({0x300: item(LOGS, 2), 0x301: item(BOARDS, 1)}), now=2)
    d = led.observe(state({0x300: item(LOGS, 2)}), now=20)
    d = led.observe(state({0x300: item(LOGS, 2)}), now=21)
    eq("heuristic death lapses -> theft detection resumes",
       (d.alive, [e["cause"] for e in d.lost]), (True, ["unexplained"]))


POUCH = 0x40000030


def test_trapped_pouch():
    print("== logs into the trapped pouch: carried, no false theft; a grab from the pouch is theft ==")
    led = Ledger(NOWOODS)
    c = {**base(), POUCH: item(0x0E79, hue=38)}
    led.observe(state(c), now=0)
    led.expect(("moving", 0x100, POUCH), now=0.5)
    lifted = dict(c)
    del lifted[0x100]                        # the lift: the server deletes the stack from our view
    d = led.observe(state(lifted), now=1)
    eq("the stack on the cursor (vanished from the pack): expected, no theft",
       ([(e["serial"], e["cause"]) for e in d.lost], d.unexplained_losses), ([(0x100, "expected")], []))
    c[0x100] = item(LOGS, 10, container=POUCH)
    d = led.observe(state(c), now=2)
    eq("dropped into the pouch: the same stack back, carried (a gain, no loss)",
       ([(e["serial"], e["amount"]) for e in d.gained], d.lost), ([(0x100, 10)], []))
    eq("pouch contents count as carried (summary)", led.summary(kind="log"), {"unknown(0)": 10})
    c[0x103] = item(LOGS, 6)                 # the next chop: a new stack on top
    led.observe(state(c), now=3)
    led.expect(("moving", 0x103, POUCH), now=3.5)
    del c[0x103]                             # dropped and merged into the pouch's stack in one view
    c[0x100] = item(LOGS, 16, container=POUCH)
    d = led.observe(state(c), now=4)
    eq("merged into the pouch's stack: the vanish is expected, the stack grew",
       ([(e["serial"], e["cause"]) for e in d.lost], [(e["serial"], e["amount"]) for e in d.gained],
        d.unexplained_losses), ([(0x103, "expected")], [(0x100, 6)], []))
    led.expect(("moving", 0x101, POUCH), now=5)
    c[0x101] = item(BOARDS, 20, container=POUCH)  # the drop resolved before any view saw it lifted
    led.observe(state(c), now=6)
    eq("a drag seen landing resolves its expectation", [p for p in led.pending if p["kind"] == "moving"], [])
    c2 = dict(c)
    del c2[0x101]                            # ... so a later grab of that stack is not covered
    d = led.observe(state(c2), now=7)
    eq("a stack taken out of the pouch afterwards: settling", (d.lost, led.settling), ([], {0x101: 7}))
    d = led.observe(state(c2), now=8)
    eq("... and theft once settled",
       [(e["serial"], e["cause"], e["class"]) for e in d.unexplained_losses], [(0x101, "unexplained", "board")])
    c2[0x100] = item(LOGS, 13, container=POUCH)
    d = led.observe(state(c2), now=9)
    eq("part of the pouch's log stack taken (a steal of 3) is theft at once",
       [(e["serial"], e["amount"], e["cause"], e["to"]) for e in d.unexplained_losses], [(0x100, 3, "unexplained",
                                                                                         "split")])
    c3 = dict(c2)
    c3[POUCH] = item(0x0E79, hue=0)         # the pouch went off: an item update, nothing lost
    d = led.observe(state(c3), now=10)
    eq("the pouch going off (hue 38 -> 0) is no loss", (d.lost, d.gained), ([], []))


# live 2026-10-06, run lumber-20261006-173734-54c3 (session 20261006_164724): the server's own packets
LIVE_ME, LIVE_PACK, LIVE_HATCHET = 0x0014683F, 0x46F90508, 0x6627697C
LIVE_INTO_PACK = "256627697c00000f44020001006500580046f90508097200000020"  # t 1791327053.696, the recall's unequip
LIVE_REMOVE = "1d6627697c"                                                 # t 1791327066.462, after our 0x06 use
LIVE_EQUIP = "2e6627697c00000f4400000020020014683f0972"                   # t 1791327066.478, worn (layer 2)


def test_hatchet_used_from_pack():
    print("== the packed hatchet used: removed, then worn 16 ms later; a view in between is no theft ==")
    from world.runtime import WorldRuntime
    rt = WorldRuntime(clock=lambda: None)
    rt.state.self.serial = LIVE_ME
    rt.feed_packet("s2c", bytes.fromhex("2e" f"{LIVE_PACK:08x}" "00000e75" "00000000" "15" f"{LIVE_ME:08x}" "0000"))

    def view():
        return {"movement": {"self_serial": LIVE_ME}, "world": rt.state.snapshot()}

    led = Ledger(NOWOODS)
    rt.feed_packet("s2c", bytes.fromhex(LIVE_INTO_PACK))
    led.observe(view(), now=1791327061.8)
    eq("baseline: the hatchet in the pack", sorted(led.items), [LIVE_HATCHET])
    rt.feed_packet("s2c", bytes.fromhex(LIVE_REMOVE))
    d = led.observe(view(), now=1791327066.47)                 # the view between the two packets
    eq("the view between remove and equip: no theft (juncture 421 booked one)", (d.lost, d.theft_suspected),
       ([], False))
    eq("... the hatchet settling in the baseline", sorted(getattr(led, "settling", {})), [LIVE_HATCHET])
    rt.feed_packet("s2c", bytes.fromhex(LIVE_EQUIP))
    d = led.observe(view(), now=1791327066.4827)               # juncture 421's view
    eq("worn 16 ms later: equipped, no theft",
       ([(e["serial"], e["cause"], e["to"]) for e in d.lost], d.theft_suspected),
       ([(LIVE_HATCHET, "equipped", h(LIVE_ME))], False))
    d = led.observe(view(), now=1791327068)
    eq("nothing later either", (d.lost, d.unexplained_losses), ([], []))

    # gone for a moment, then back in the pack: no loss, no gain
    led = Ledger(NOWOODS)
    led.observe(state(base()), now=0)
    c = base()
    del c[0x102]
    led.observe(state(c), now=1)
    d = led.observe(state(base()), now=1.2)
    eq("back in the pack inside the window: no loss, no gain", (d.lost, d.gained, led.settling), ([], [], {}))
    # a bag gone with its contents settles as one; booked with them folded in
    c = base()
    c[0x105] = item(0x0F0C, 2, container=BAG)
    led.observe(state(c), now=2)
    del c[BAG], c[0x105]
    d = led.observe(state(c), now=3)
    eq("a bag gone with its contents: settling, contents kept", (d.lost, sorted(led.settling), 0x105 in led.items),
       ([], [BAG], True))
    d = led.observe(state(c), now=4)
    eq("still gone: one unexplained entry with its contents",
       [(e["serial"], e["cause"], e.get("contents")) for e in d.unexplained_losses], [(BAG, "unexplained", 1)])


TESTS = [test_woods, test_expected_vs_theft, test_merge_nested_gain, test_death, test_trapped_pouch,
         test_hatchet_used_from_pack]


def main():
    for t in TESTS:
        t()
    print(f"\nledger: {'ALL PASS' if not FAILURES else f'{len(FAILURES)} FAILURES'}")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())

"""Tests for harness/pouch.py (trapped pouches and the pop alarm) and nav.beyond. No network.

- unit: the pack's pouches (live = hue 38, any depth), what they hold
- PopWatch on synthetic events: our double-click makes the pop ours (hue, explosion, sound), a
  pop without one is the alarm, an explosion away from us or long after our click is not ours
- replay of session 20261004_113229 (Hackworth pops two trapped pouches himself at 2:31 and
  2:52): no alarm, both pops ours; the same capture with our two double-clicks taken out (a
  thief's pops, as far as we can see them) raises the alarm on both

Run: python harness/test_pouch.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ledger  # noqa: E402
import nav  # noqa: E402
import pouch  # noqa: E402
import replay  # noqa: E402
from world.runtime import WorldRuntime  # noqa: E402

FAILURES = []
TAG = "20261004_113229"
BASE = os.path.join(os.path.dirname(HERE), "logs", f"session_{TAG}")
ME, PACK = 0x0020F127, 0x4B6AA305
POPPED = (0x5C0CDF37, 0x5C0CDF35)       # the two he popped (2:31, 2:52); a third stays trapped


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(name)


def world(items):
    """A state-port world: self, the backpack on layer 0x15, and `items` {serial: (graphic, hue, container)}."""
    w = {"self": {"serial": f"0x{ME:08X}"},
         "items": {f"0x{PACK:08X}": {"graphic": 0x0E75, "layer": 0x15, "container": f"0x{ME:08X}"}}}
    for s, (g, hue, c) in items.items():
        w["items"][f"0x{s:08X}"] = {"graphic": g, "hue": hue, "amount": 1, "container": f"0x{c:08X}"}
    return w


def test_pack_pouches():
    print("== pouches in the pack: live = hue 38, at any depth; what they hold ==")
    bag, p1, p2, p3, logs = 0x100, 0x101, 0x102, 0x103, 0x104
    w = world({bag: (0x0E76, 0, PACK), p1: (0x0E79, 38, bag), p2: (0x0E79, 38, PACK), p3: (0x0E79, 0, PACK),
               logs: (0x1BDD, 0, p1)})
    check("live pouches: shallowest first (the one in the bag last), the popped one left out",
          pouch.live(w, PACK) == [p2, p1], pouch.live(w, PACK))
    check("pack_pouches: hue, depth, live", pouch.pack_pouches(w, PACK)[p3] == {
        "hue": 0, "container": PACK, "depth": 1, "live": False}, pouch.pack_pouches(w, PACK))
    check("holding: only the live pouch with logs inside", pouch.holding(w, PACK, (0x1BDD,)) == [p1])
    check("contents of a pouch", [s for s, _ in pouch.contents(w, p1)] == [logs])


def test_popwatch_synthetic():
    print("== PopWatch: our double-click makes a pop ours; one without it is the alarm ==")
    p = 0x200
    trapped, popped = world({p: (0x0E79, 38, PACK)}), world({p: (0x0E79, 0, PACK)})
    boom = [(10.05, {"ev": "sound", "sound": 0x0307, "x": 100, "y": 100, "z": 0}),
            (10.05, {"ev": "effect", "type": 2, "graphic": 0x36BD, "x": 99, "y": 100})]
    w = pouch.PopWatch()
    check("first look: no pops", w.observe(trapped, PACK, (100, 100), [], 9.0) == [])
    got = w.observe(popped, PACK, (100, 100), [(10.0, {"ev": "dclick", "serial": p})] + boom, 10.1)
    check("our double-click, then the sound, an explosion and the hue 38 -> 0: three pops, all ours",
          [(g["signal"], g["own"]) for g in got] == [("sound", True), ("explosion", True), ("hue", True)]
          and got[-1]["serial"] == p, got)
    w = pouch.PopWatch()
    w.observe(trapped, PACK, (100, 100), [], 9.0)
    got = w.observe(popped, PACK, (100, 100), boom, 10.1)
    check("the same without our double-click: three pops, none ours (the alarm)",
          [(g["signal"], g["own"]) for g in got] == [("sound", False), ("explosion", False), ("hue", False)], got)
    w = pouch.PopWatch()
    w.observe(trapped, PACK, (100, 100), [], 9.0)
    w.own_pop(p, 10.0)                             # the runner says so before sending the click
    got = w.observe(popped, PACK, (100, 100), boom, 10.1)
    check("the runner's own_pop note counts like the dclick event", all(g["own"] for g in got) and got, got)
    late = w.observe(popped, PACK, (100, 100), [(10.0 + pouch.OWN_POP_S + 1, boom[0][1])], 20.0)
    check(f"an explosion sound more than {pouch.OWN_POP_S:g} s after our click is not ours",
          [(g["signal"], g["own"]) for g in late] == [("sound", False)], late)
    far = w.observe(popped, PACK, (110, 100), boom, 21.0)
    check(f"an explosion beyond {pouch.POP_NEAR} tiles of us is no pop of ours", far == [], far)
    w = pouch.PopWatch()
    w.observe(world({p: (0x0E79, 0, PACK)}), PACK, (100, 100), [], 9.0)
    got = w.observe(popped, PACK, (100, 100), [(10.0, {"ev": "dclick", "serial": p})], 10.1)
    check("opening a pouch that already went off (hue 0) is no pop", got == [], got)
    w = pouch.PopWatch()
    w.observe(trapped, PACK, (100, 100), [], 9.0)
    gone = w.observe(world({}), PACK, (100, 100), [], 10.0)
    check("a live pouch leaving the pack is no pop (the ledger sees a loss)", gone == [], gone)
    w = pouch.PopWatch()
    w.observe(trapped, PACK, (100, 100), [], 9.0)
    noise = w.observe(trapped, PACK, (100, 100), boom, 10.1)
    check("someone else's pouch going off next to us (sound and explosion, ours still armed): pops, no thief "
          "(live 2026-10-06 at the busy guild-house landing)",
          len(noise) == 2 and pouch.thief_pops(noise) == [], noise)
    w = pouch.PopWatch()
    w.observe(trapped, PACK, (100, 100), [], 9.0)
    got = w.observe(popped, PACK, (100, 100), boom, 10.1)
    check("ours going off without our click: all three signals are the thief's", len(pouch.thief_pops(got)) == 3, got)


def test_beyond():
    print("== nav.beyond: a goal at least r+1 tiles from every center ==")
    g = nav.beyond([(10, 10)], 3)
    check("inside r: not a goal; at r+1: a goal", (g((13, 10)), g((14, 10)), g((6, 6))) == (False, True, True))
    check("heuristic: steps still needed", (g.heuristic((10, 10)), g.heuristic((12, 11)), g.heuristic((14, 10)))
          == (4, 2, 0))


def capture_pops(drop_dclicks: bool):
    """Replay the capture in its timed order; PopWatch.observe after every packet that
    brings a dclick/effect/sound event or an item update. drop_dclicks: our C2S 0x06 on
    the pouches never happened (a thief's pops)."""
    now = [None]
    rt = WorldRuntime(clock=lambda: now[0])
    w = pouch.PopWatch()
    pops, live_seen = [], set()
    for t, d, _src, pkt in replay.timed_packets(BASE):
        if drop_dclicks and d == "c2s" and pkt[0] == 0x06 and int.from_bytes(pkt[1:5], "big") in POPPED:
            continue
        now[0] = t
        rt.feed_packet(d, pkt)
        evs = rt.drain_events()
        if not any(e["ev"] in ("dclick", "effect", "sound") for e in evs) and pkt[0] not in (0x25, 0x1D, 0x3C):
            continue
        snap = rt.state.snapshot()
        st = {"world": snap}
        pack = ledger.backpack_serial(st)
        me = snap["self"]
        live_seen |= set(pouch.live(snap, pack)) if pack is not None else set()
        pops += w.observe(snap, pack, (me["x"], me["y"]), [(t, e) for e in evs], t)
    return pops, live_seen


def test_capture_113229():
    print(f"== replay {TAG}: Hackworth's own two pops raise no alarm; without his clicks they do ==")
    if not os.path.exists(BASE + ".jsonl"):
        check(f"capture {TAG} present (committed fixture)", False, BASE)
        return
    pops, live_seen = capture_pops(False)
    check("the three bought pouches were live (hue 38) in the pack", set(POPPED) <= live_seen, live_seen)
    hue = [g for g in pops if g["signal"] == "hue"]
    check("both pops seen as the hue going 38 -> 0, on the two pouches he double-clicked",
          sorted(g["serial"] for g in hue) == sorted(POPPED), hue)
    check("each pop: the sound and five explosions around us", sum(g["signal"] == "sound" for g in pops) == 2
          and sum(g["signal"] == "explosion" for g in pops) == 10, [g["signal"] for g in pops])
    check("no alarm: every signal is ours", pops and all(g["own"] for g in pops),
          [g for g in pops if not g["own"]][:3])
    thief, _ = capture_pops(True)
    alarms = [g for g in thief if not g["own"]]
    check("without his double-clicks (synthetic thief): every signal of both pops is the alarm",
          len(alarms) == len(thief) == len(pops)
          and sorted(g["serial"] for g in alarms if g["signal"] == "hue") == sorted(POPPED),
          [(g["signal"], g["own"]) for g in thief][:8])


TESTS = [test_pack_pouches, test_popwatch_synthetic, test_beyond, test_capture_113229]


def main():
    for t in TESTS:
        t()
    print(f"\npouch: {'ALL PASS' if not FAILURES else f'{len(FAILURES)} FAILURES'}")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())

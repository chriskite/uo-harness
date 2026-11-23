"""The Hunting Grounds run and advice (harness/hunting_grounds.py): what the proxy's state `grounds` says
from the server's lines, and the plan's calls at the points that cost Logan points on 2026-11-22.

Run: python harness/test_hunting_grounds.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hunting_grounds as hg  # noqa: E402
import proxy  # noqa: E402
import viz_feed  # noqa: E402
from sim import packets as P  # noqa: E402

FAILURES = []
ME, BUCK_A, BUCK_B, BUCK_C = 0x00104A60, 0x0020C001, 0x0020C002, 0x0020C003
LX, LY = hg.INSTANCES[0][1]          # the east warden
WX, WY = hg.INSTANCES[1][1]          # the west warden


def check(name, cond, extra=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAILURES.append(name)


def grounds_tap():
    t = proxy.SessionTap(viz_feed._Null(), viz_feed._Null(), viz_feed._Null())
    t.grounds = hg.GroundsLog()
    clock = [1000.0]
    t.wall = lambda: clock[0]
    t._world(proxy.S2C, P.login(ME, 400, LX - 2, LY + 1, 0))                # the lodge's entry (live 14:57:52)
    t._world(proxy.S2C, P.says(ME, "Logan Wolf", hg.START_TEXT, hue=946, body=400))   # over our own head
    return t, clock


def status(t):
    return t.state(snapshot=False)["grounds"]


def at(t, x, y):
    t._world(proxy.S2C, P.mobile(ME, 400, x, y, 0))


def sys_line(t, text):
    t._world(proxy.S2C, P.system_text(text))


def me_line(t, text):
    t._world(proxy.S2C, P.says(ME, "Logan Wolf", text, hue=946, body=400))


def buck(t, serial, pts, x, y, mood):
    t._world(proxy.S2C, P.mobile(serial, 234, x, y, 0))
    t._world(proxy.S2C, P.says(serial, f"{pts} point buck", mood, hue=1256, body=234))


def test_run():
    print("== a run: the clock and its resyncs, trophies, the turn-in and the weapon tier, the end ==")
    t, clock = grounds_tap()
    g = status(t)
    check("the start line: the run with 900 s left and the starter bow", g is not None and g["left_s"] == hg.SESSION_S
          and g["weapon"]["name"] == "starter bow" and g["weapon"]["shoot_from"] == [11, 12], str(g and g["weapon"]))
    check("at the lodge: run south to the deep zone, shoot calm bucks of 6 or more",
          (g["plan"]["phase"], g["plan"]["zone"], g["plan"]["min_points"]) == ("out", [269, 330], 6), str(g["plan"]))
    clock[0] += 297
    me_line(t, "You now have 10 minutes left remaining in this realm.")
    check("the 10-minute line resets the end", status(t)["left_s"] == 600.0, str(status(t)["left_s"]))
    clock[0] += 30
    t._world(proxy.S2C, P.gump(0x01A2B3C4, 3914586893, "{ text 0 0 0 0 }", ["Turn in Trophies and Resupply", "0 Pts",
                                                                             "Score", "0", "9m 30s Left"]))
    check("the warden gump's 'Nm Ns Left' does too", status(t)["left_s"] == 570.0, str(status(t)["left_s"]))
    at(t, 8340, 280)
    for p in (8, 12, 12, 10):
        me_line(t, f"{p} point trophy added to carried trophies.")
    g = status(t)
    check("4 held in the deep zone: hunt on for the 5th", g["held"] == [8, 12, 12, 10] and g["plan"]["phase"] == "hunt"
          and g["plan"]["kills"] == 1, str(g["plan"]))
    me_line(t, "10 point trophy added to carried trophies.")
    check("5 held: run back to the warden", status(t)["plan"]["phase"] == "return")
    at(t, LX, LY + 1)
    sys_line(t, "Ammunition resupplied and 5 trophies turned in for 52 points (52 new score)")
    g = status(t)
    check("the turn-in: nothing held, 5 turned in, the score, the Recurve Bow (3) and the Hunting Bow next at 6",
          g["held"] == [] and g["turned_in"] == 5 and g["score"] == 52 and g["weapon"]["name"] == "Recurve Bow"
          and g["weapon"]["next"]["at"] == 6, str(g["weapon"]))
    me_line(t, hg.EXPIRED_TEXT)
    check("the expiry line: no run", status(t) is None)
    e, eclock = grounds_tap()
    at(e, 1600, 1530)                                                        # the exit moongate: back in Prevalia
    eclock[0] += hg.END_GRACE_S + 1
    at(e, 1601, 1530)
    check("left by the moongate: no run", status(e) is None)
    n, nclock = grounds_tap()
    nclock[0] += hg.SESSION_S + hg.END_GRACE_S + 1
    check("no expiry line, the end long past: no run", status(n) is None)
    w, wclock = grounds_tap()
    at(w, WX - 2, WY + 1)                                                     # the west instance's entry (16:35:42)
    g = status(w)
    check("the west instance: its warden and region, the same zone", g["instance"] == "west" and g["lodge"] == [WX, WY]
          and g["region"] == [7906, 72, 8162, 330] and g["plan"]["zone"] == [269, 330], str({k: g[k] for k in ("instance", "lodge", "region")}))
    at(w, 8000, 300)
    check("the run home from the west warden: 211 tiles, 62 s", status(w)["home_s"] == round(0.2 * 211 + hg.RETURN_MARGIN_S, 1),
          str(status(w)["home_s"]))


def test_deadline():
    print("== the run home: Logan's run 2, 5th trophy carved with 36 s left at y 318 (44 points lost) ==")
    t, clock = grounds_tap()
    t._world(proxy.S2C, P.gump(0x01A2B3C4, 3914586893, "{ text 0 0 0 0 }", ["1m 46s Left"]))   # 15:27:18 by the clock
    at(t, 8227, 303)
    for p in (8, 8, 8):
        me_line(t, f"{p} point trophy added to carried trophies.")
    g = status(t)
    check("106 s left, 3 held, 214 tiles out: one more fits", g["plan"]["phase"] == "hunt" and g["plan"]["kills"] == 1
          and g["slack_s"] > 0 and g["plan"]["min_points"] == 2, f"{g['plan']} slack {g['slack_s']}")
    clock[0] += 30                                                            # the live 15:27:48: still out there
    g = status(t)
    check("76 s left: no time for another kill, head back (the live run went on and lost all 3 + 2 more)",
          g["plan"]["phase"] == "return" and 0 < g["slack_s"] < hg.WARN_SLACK_S, f"{g['plan']} slack {g['slack_s']}")
    clock[0] += 20
    check("the run home no longer fits the margin: slack below 0 (the badge's urgent state)", status(t)["slack_s"] < 0)


def test_bucks():
    print("== bucks: shoot the calm ones worth it, wait out the nervous, skip the rest; the wind's penalty ==")
    t, clock = grounds_tap()
    at(t, 8340, 280)
    sys_line(t, "Wind is now blowing to the south.")
    buck(t, BUCK_A, 10, 8340, 292, "(calm)")                                   # due south of us: directly downwind
    buck(t, BUCK_B, 12, 8352, 270, "(nervous)")
    buck(t, BUCK_C, 2, 8330, 285, "(calm)")                                    # southwest: one octant off
    bk = {b["serial"]: (b["verdict"], b["downwind"], b["dist"]) for b in status(t)["bucks"]}
    check("10 calm: shoot (due south with the wind blowing south: +30%); 12 nervous: wait; 2 calm: skip (+15%)",
          bk == {"0x0020C001": ("shoot", 30, 12), "0x0020C002": ("wait", 0, 12), "0x0020C003": ("skip", 15, 10)}, str(bk))
    check("shoot first in the list", status(t)["bucks"][0]["serial"] == "0x0020C001")
    clock[0] += hg.STATE_TTL_S + 1
    check("a label older than 15 s: mood unknown, wait", status(t)["bucks"][0]["mood"] is None
          and {b["verdict"] for b in status(t)["bucks"] if b["points"] >= 6} == {"wait"})
    t._world(proxy.S2C, P.says(BUCK_A, "10 point buck", "*flees*", hue=1256, body=234))
    check("fleeing (3% to hit): skip", next(b for b in status(t)["bucks"] if b["serial"] == "0x0020C001")["verdict"] == "skip")


# the hotbar as live at 15:08:44 (run 1, capture 20261122_140905): five held, two pairs sharing a line; the Recurve
# Bow's art (30995) beside the Shoot button (button 4)
HOTBAR_LINES = ["Score", "20", "4m 15s Left", "Held Trophies", "8 Pt", "12 Pt", "10 Pt", "Ammo", "5/15", "Wind"]
HOTBAR_LAYOUT = (
    "{ resizepic 13 13 11571 149 548 }{ text 52 26 2606 0 18 0 1 0 0 0 }{ text 101 26 2655 1 18 0 1 0 0 0 }"
    "{ tilepic 20 42 6163 }{ text 55 46 2655 2 18 0 1 0 0 0 }{ gumppictiled 25 79 126 3 96 }"
    "{ text 50 70 149 3 18 0 1 0 0 0 }{ tilepic 44 89 63994 }{ text 101 100 2655 4 18 0 1 0 0 0 }"
    "{ tilepic 37 123 63996 }{ text 101 135 2655 5 18 0 1 0 0 0 }{ tilepic 37 158 63996 }"
    "{ text 101 170 2655 5 18 0 1 0 0 0 }{ tilepic 38 194 63995 }{ text 101 205 2655 6 18 0 1 0 0 0 }"
    "{ tilepic 38 229 63995 }{ text 101 240 2655 6 18 0 1 0 0 0 }{ gumppictiled 25 274 126 3 96 }"
    "{ tilepic 52 287 31188 }{ button 95 285 2151 2154 1 0 1 }{ tilepic 55 318 51173 }{ button 95 325 9721 9724 1 0 2 }"
    "{ tilepichue 43 363 63989 2499 }{ button 95 370 2151 2154 1 0 3 }{ tilepic 47 409 30995 }"
    "{ button 95 414 9721 9724 1 0 4 }{ text 45 465 149 7 18 0 1 0 0 0 }{ text 96 465 2655 8 18 0 1 0 0 0 }")


def test_hotbar():
    print("== the hotbar: a run picked up without its start line (a proxy restarted in the Grounds), and kept true ==")
    t = proxy.SessionTap(viz_feed._Null(), viz_feed._Null(), viz_feed._Null())
    t.grounds = hg.GroundsLog()
    t.wall = lambda: 5000.0
    t._world(proxy.S2C, P.login(ME, 400, 8340, 285, 0))
    check("no start line, no hotbar: no run", status(t) is None)
    t._world(proxy.S2C, P.gump(0x01A2B3C4, 4100931131, HOTBAR_LAYOUT, HOTBAR_LINES))
    g = status(t)
    check("the hotbar starts the run: 255 s left, the five held (shared lines counted per entry), score, ammo",
          g is not None and g["left_s"] == 255.0 and g["held"] == [8, 12, 12, 10, 10] and g["score"] == 20
          and g["ammo"] == 5, str(g and {k: g[k] for k in ("left_s", "held", "score", "ammo")}))
    check("the weapon from the Shoot button's art: the Recurve Bow, trophies turned in its threshold (3)",
          g["weapon"]["name"] == "Recurve Bow" and g["turned_in"] == 3, str(g["weapon"]))
    check("five held: run back", g["plan"]["phase"] == "return")
    last = [x if x != "4m 15s Left" else "5s Left" for x in HOTBAR_LINES]
    t.grounds = hg.GroundsLog()
    t._world(proxy.S2C, P.gump(0x01A2B3C4, 4100931131, HOTBAR_LAYOUT, last))
    check("the hotbar sent with the expiry doesn't start a run", status(t) is None)


def test_plan_endgame():
    print("== after a turn-in with R s left: deep while it fits, shallower as time runs out (§4.2 step 8) ==")
    deep = hg.plan(LX, LY, 300, [], 12, (LX, LY))
    mid = hg.plan(LX, LY, 160, [], 12, (LX, LY))
    near = hg.plan(LX, LY, 80, [], 12, (LX, LY))
    none = hg.plan(LX, LY, 40, [], 12, (LX, LY))
    check("300 s: 5 in the deep zone, calm 6+", (deep["zone"], deep["kills"], deep["min_points"]) == ([269, 330], 5, 6), str(deep))
    check("160 s: a short deep trip, any calm buck (time, not slots, is short)", mid["kills"] < 5 and mid["min_points"] == 2
          and mid["zone"][0] >= 249, str(mid))
    check("80 s: by the lodge", near["phase"] == "hunt" and near["zone"][1] <= 149, str(near))
    check("40 s: nothing fits", none["phase"] == "done", str(none))
    check("upgrades shorten the kill: starter 32.7 s, Rifle 24.2 s",
          round(hg.kill_s(0), 1) == 32.7 and round(hg.kill_s(12), 1) == 24.2)


def main():
    test_run()
    test_deadline()
    test_bucks()
    test_hotbar()
    test_plan_endgame()
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILED: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

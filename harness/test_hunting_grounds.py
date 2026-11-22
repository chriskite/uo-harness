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
LX, LY = hg.LODGE


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
          (g["plan"]["phase"], g["plan"]["zone"], g["plan"]["min_points"]) == ("out", [249, 330], 6), str(g["plan"]))
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


def test_plan_endgame():
    print("== after a turn-in with R s left: deep while it fits, shallower as time runs out (§4.2 step 8) ==")
    deep = hg.plan(LX, LY, 300, [], 12)
    mid = hg.plan(LX, LY, 160, [], 12)
    near = hg.plan(LX, LY, 80, [], 12)
    none = hg.plan(LX, LY, 40, [], 12)
    check("300 s: 5 in the deep zone, calm 6+", (deep["zone"], deep["kills"], deep["min_points"]) == ([249, 330], 5, 6), str(deep))
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
    test_plan_endgame()
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILED: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

"""The Hunting Grounds simulator's world (harness/sim/hgworld.py; docs/SIM.md "Hunting Grounds simulator"), driven by
client packets through a SimLink, no runner: the entry, the hotbar as hunting_grounds.GroundsLog reads it, hide, the
shot and its refusals, the carve, the turn-in, the expiry, the deer's moods and density against the calibration.

Run: python harness/test_hg_sim.py"""
import math
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import actions  # noqa: E402
import hunting_grounds as hg  # noqa: E402
import nav  # noqa: E402
import pathfind  # noqa: E402
from sim import clock as simclock  # noqa: E402
from sim import hgworld as H  # noqa: E402
from sim.hgscenario import load  # noqa: E402
from sim.link import SimLink  # noqa: E402

FAILURES = []
WALKERS = pathfind.Walkers()
EPOCH = 1795384800.0


def check(name, cond, extra=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAILURES.append(name)


class Rig:
    """A world, its SimLink and clock, and a GroundsLog fed from the client model's events."""

    def __init__(self, seed=1, permits=3, followers=False):
        self.w = H.HGWorld(load(), seed=seed, permits=permits, walkers=WALKERS, followers=followers)
        self.c = simclock.SimClock(epoch=EPOCH)
        self.l = SimLink(self.w, self.c)
        self.gl = hg.GroundsLog()
        self.fed = 0

    def wait(self, s):
        self.c.advance(s)
        self.l.state()

    def send(self, pkt):
        self.l.send(pkt)
        self.l.state()

    def mark(self):
        return len(self.l.events)

    def heard(self, mark, serial=None):
        return [e["text"] for e in self.l.events[mark:] if e["ev"] == "speech_heard"
                and (serial is None or e.get("serial") == serial)]

    def gumps(self, mark, gid):
        return [e for e in self.l.events[mark:] if e["ev"] == "gump_open" and e["gump_id"] == gid]

    def status(self):
        self.l.state()
        self.gl.feed(self.l.events[self.fed:], self.c.time())
        self.fed = len(self.l.events)
        pl = self.w.player
        mobs = self.l.rt.state.mobiles

        def where(s):
            m = mobs.get(s)
            return (m.x, m.y) if m is not None and m.x is not None else None
        return self.gl.status(self.c.time(), (pl.x, pl.y), where)

    def step(self, d, run=True):
        pl = self.w.player
        if pl.d != d:
            self.send(actions.walk(d, run))
        self.send(actions.walk(d, run))
        self.wait(self.w.sc["player"]["run_s" if run else "walk_s"])

    def walk_to(self, goal, radius=0, run=True):
        pl = self.w.player
        route = pathfind.plan(self.w.walk, (pl.x, pl.y, pl.z), nav.within(goal, radius), max_expand=200000)
        assert route, f"no route to {goal}"
        for x, y, _ in route[1:]:
            self.step(nav.direction((pl.x, pl.y), (x, y)), run)

    def enter(self):
        w = self.w
        self.walk_to((w.official.x, w.official.y), 2)
        m = self.mark()
        self.send(actions.dclick(w.official.serial))
        g = self.gumps(m, w.sc["gumps"]["official"])[-1]
        self.send(actions.gump_reply(g["serial"], g["gump_id"], 2, g["layout"], g["lines"]))
        return g

    def hotbar_button(self, button):
        g = [e for e in self.l.events if e["ev"] == "gump_open" and e["gump_id"] == self.w.sc["gumps"]["hotbar"]][-1]
        self.send(actions.gump_reply(g["serial"], g["gump_id"], button, g["layout"], g["lines"]))

    def field(self):
        """Put the player in a clear stretch of the instance (every tile within 14 walkable), the session kept alive."""
        w = self.w
        x0, y0, x1, y1 = w.instances[w.s["inst"]]["region"]
        for y in range(y0 + 120, y1 - 15, 4):
            for x in range(x0 + 20, x1 - 20, 4):
                if all(open_tile(w, x + dx, y + dy) for dx in range(-14, 15, 2) for dy in range(-14, 15, 2)):
                    w._teleport((x, y, w.instances[w.s["inst"]]["z"]))
                    self.l.state()
                    w.last_step_t = -1e9
                    return x, y
        raise AssertionError("no open field")

    def fresh(self, keep_time=True):
        """Between staged shots: no cooldowns, the clock far from its end, no corpse, nothing held."""
        w = self.w
        w.s["t0"] = w.t
        w.s["warned"].clear()
        w.s["held"] = []
        w.s["ammo"] = w.sc["ammo"]
        w.last_shot_t = -1e9
        w.last_step_t = -1e9
        for c in [c for c, it in w.items.items() if it["kind"] == "corpse"]:
            w._remove_corpse(c)


def open_tile(w, x, y):
    z = H.los.surface_z(w.walk, x, y, 0)
    return sum(w.walk.can_walk(x, y, z, d) is not None for d in range(8)) == 8


def test_enter():
    print("== the entry: the official's menu spends a permit, lands us in an instance, starts the clock ==")
    r = Rig(permits=2)
    w = r.w
    m = r.mark()
    r.send(actions.dclick(w.official.serial))
    check("the official from 6 tiles off: no menu", not r.gumps(m, w.sc["gumps"]["official"]))
    g = r.enter()
    check("his menu as live (gump 515811277, 'Enter Hunting Grounds' on button 2)",
          g["lines"] == w.sc["gump_templates"]["official"]["lines"] and "{ button 138 383 2151 2154 1 0 2 }" in g["layout"])
    pl = w.player
    inst = w.instances[w.s["inst"]] if w.s else None
    check("in the instance at its entry, one permit spent", inst is not None and (pl.x, pl.y) == inst["entry"]
          and w.permits == 1 and r.l.rt.state.items[H.PERMIT].amount == 1, str((pl.x, pl.y, w.permits)))
    m0 = 0
    check("'*You now have 15 minutes in this realm*' over our own head", hg.START_TEXT in r.heard(m0, pl.serial))
    st = r.status()
    check("GroundsLog: a run with 900 s left", st is not None and 895 <= st["left_s"] <= 900, str(st and st["left_s"]))
    check("metrics: one run", w.metrics["runs"] == 1)
    for kw, text in (({"followers": True}, H.TEXT_FOLLOWERS), ({"permits": 0}, H.TEXT_NO_PERMIT)):
        r2 = Rig(**kw)
        m = r2.mark()
        r2.enter()
        check(f"refused ({text!r}): no session, nothing spent", r2.w.s is None and text in r2.heard(m)
              and r2.w.permits == kw.get("permits", 3))


def test_hotbar():
    print("== the hotbar: every 5 s, the live format, read by GroundsLog ==")
    r = Rig()
    r.enter()
    w = r.w
    gid = w.sc["gumps"]["hotbar"]
    first = r.gumps(0, gid)[0]
    check("the first hotbar's lines are live's (hotbar_empty)", first["lines"] == w.sc["gump_templates"]["hotbar_empty"]["lines"],
          str(first["lines"]))
    m = r.mark()
    r.wait(10.05)
    check("re-sent every 5 s", len(r.gumps(m, gid)) == 2)
    st = r.status()
    check("time left, nothing held, score 0, 15 ammo, the starter bow", st["left_s"] == 890.0 and st["held"] == []
          and st["score"] == 0 and st["ammo"] == 15 and st["weapon"]["name"] == "starter bow",
          str({k: st[k] for k in ("left_s", "held", "score", "ammo")}))
    s = w.s
    s.update(held=[10, 12, 12, 12, 8], turned_in=9, score=88, ammo=10)
    s["t0"] = s["next_hotbar"] - (900 - 200)    # the next hotbar says 200 s left
    m = r.mark()
    r.wait(5.0)
    g = r.gumps(m, gid)[-1]
    check("lines as live (hotbar_full): equal trophies share a line", g["lines"] == w.sc["gump_templates"]["hotbar_full"]["lines"],
          str(g["lines"]))
    st = r.status()
    check("GroundsLog: the five held, the score, the ammo, the Great Bow from the Shoot button's art",
          st["held"] == [10, 12, 12, 12, 8] and st["score"] == 88 and st["ammo"] == 10
          and st["weapon"]["name"] == "Great Bow" and st["left_s"] == 200.0, str({k: st[k] for k in ("held", "score", "ammo", "left_s")}))


def test_hide():
    print("== hide and stealth: always hidden, the 5 s cooldown, revealed by a running step ==")
    r = Rig()
    r.enter()
    r.field()
    w = r.w
    m = r.mark()
    r.send(actions.use_skill(21))
    check("the Hiding skill: hidden at once", w.hidden and H.TEXT_HIDE in r.heard(m)
          and r.l.rt.state.self.stats.get("flags") == 0x80)
    r.wait(2.0)
    m = r.mark()
    r.send(actions.use_skill(21))
    check("again 2 s later: refused", H.TEXT_HIDE_WAIT in r.heard(m))
    r.step(4, run=False)
    check("a walking step keeps us hidden (stealth)", w.hidden)
    r.step(4, run=True)
    check("a running step reveals", not w.hidden and r.l.rt.state.self.stats.get("flags") == 0)
    r.wait(3.0)
    m = r.mark()
    r.hotbar_button(2)
    check("the hotbar's Hide 5 s after the last hide", w.hidden and H.TEXT_HIDE in r.heard(m))
    m = r.mark()
    r.hotbar_button(3)
    check("Track: its line", H.TEXT_TRACK in r.heard(m))


def stage(r, dist, mood="calm", points=8, d=4):
    """A deer `dist` tiles off in direction `d` (in view)."""
    w = r.w
    dx, dy = {0: (0, -1), 2: (1, 0), 4: (0, 1), 6: (-1, 0)}[d]
    deer = w.place_deer(w.player.x + dx * dist, w.player.y + dy * dist, points, mood)
    r.l.state()
    return deer


def shots(r, n, mood, turned_in=0):
    """n staged shots at a deer 8 tiles off in `mood`: (kills, grazes, misses) shares."""
    w = r.w
    out = {"kill": 0, "graze": 0, "miss": 0}
    for _ in range(n):
        r.fresh()
        w.hidden = True       # hidden 8 off the deer holds its mood till the shot (0.025/s)
        w.s["turned_in"] = turned_in
        deer = stage(r, 8, mood)
        before = (w.metrics["kills"], w.metrics["grazes"], w.metrics["misses"])
        r.send(actions.dclick(deer.serial))
        r.wait(2.0)
        k, g, mi = (a - b for a, b in zip((w.metrics["kills"], w.metrics["grazes"], w.metrics["misses"]), before))
        out["kill" if k else "graze" if g else "miss"] += 1
        if deer.alive:
            w.remove_deer(deer)
    return {k: v / n for k, v in out.items()}


def test_shot_rates():
    print("== the shot: hit by the deer's mood, graze by the weapon tier (calm 90 %, starter graze 25 %) ==")
    r = Rig(seed=7)
    r.enter()
    r.field()
    r.w.s["next_wind"] = 1e9
    calm = shots(r, 400, "calm")
    check("calm, starter bow: kill ~0.675, graze ~0.225, miss ~0.10", abs(calm["kill"] - 0.675) < 0.07
          and abs(calm["graze"] - 0.225) < 0.06 and abs(calm["miss"] - 0.10) < 0.05, str(calm))
    rifle = shots(r, 200, "calm", turned_in=12)
    check("calm, Ancient Rifle (5 % graze): kill ~0.855", abs(rifle["kill"] - 0.855) < 0.07, str(rifle))
    nerv = shots(r, 200, "nervous")
    check("nervous: kill ~0.225", abs(nerv["kill"] - 0.225) < 0.08, str(nerv))


def test_refusals():
    print("== the shot's refusals, the cursor, the hinder ==")
    r = Rig(seed=3)
    r.enter()
    r.field()
    w = r.w
    w.s["next_wind"] = 1e9
    r.fresh()
    deer = stage(r, 8)
    r.step(2, run=False)
    m = r.mark()
    r.send(actions.dclick(deer.serial))
    check("moved within 2 s: 'You must remain stationary...'", H.TEXT_STATIONARY in r.heard(m) and w.metrics["shots"] == 0)
    w.remove_deer(deer)
    r.fresh()
    far = stage(r, 14, d=0)
    m = r.mark()
    r.send(actions.dclick(far.serial))
    check("14 tiles with the starter bow (12): out of range", H.TEXT_RANGE in r.heard(m))
    w.s["turned_in"] = 3
    w.sc["hit"]["calm"] = 1.0
    w.weapons[1][3] = 0.0
    m = r.mark()
    r.send(actions.dclick(far.serial))
    check("...with the Recurve Bow (14): the shot goes", w.metrics["shots"] == 1 and w.flight is not None)
    r.step(2, run=False)
    check("a step while the shot is in the air: denied", any(e["ev"] == "walk_deny" for e in r.l.events[m:]))
    r.wait(3.0)
    near = stage(r, 6, d=6)
    m2 = r.mark()
    r.send(actions.dclick(near.serial))
    check("a second shot 3 s after the first: refused", H.TEXT_SHOT_WAIT in r.heard(m2))
    said = r.heard(m)
    check("the kill: felled over our head, then unhindered", H.TEXT_FELLED in r.heard(m, w.player.serial)
          and H.HELD_END_TEXT in said and said.index(H.HELD_END_TEXT) > said.index(H.TEXT_FELLED))
    check("its death and corpse in the client model ('an animal corpse')",
          any(e["ev"] == "mobile_death" and e["serial"] == far.serial and e["name"] == H.CORPSE_NAME
              for e in r.l.events[m:]))
    r.fresh()
    w.s["held"] = [2] * 5
    near = stage(r, 6, d=2)
    m = r.mark()
    r.send(actions.dclick(near.serial))
    check("holding 5: refused", H.TEXT_HOLD_FULL in r.heard(m))
    r.fresh()
    m = r.mark()
    r.hotbar_button(4)
    cur = r.l.rt.state.target
    check("the hotbar's Shoot: a cursor and its prompt", H.TEXT_SHOOT_PROMPT in r.heard(m) and cur.active)
    warden = w.instances[w.s["inst"]]["warden"]
    r.send(actions.target_object(cur.cursor_id, warden.serial, warden.x, warden.y, warden.z, 400))
    check("...at the warden: 'You cannot shoot that.'", H.TEXT_NOT_DEER in r.heard(m))
    near = stage(r, 6, d=4)
    r.hotbar_button(4)
    cur = r.l.rt.state.target
    r.send(actions.target_object(cur.cursor_id, near.serial, near.x, near.y, near.z, 234))
    check("...at a deer: the shot", w.metrics["shots"] == 2)
    r.fresh()
    w.s["ammo"] = 0
    d3 = stage(r, 5, d=4)
    m = r.mark()
    r.send(actions.dclick(d3.serial))
    check("no ammo: refused [INFERENCE line]", H.TEXT_NO_AMMO in r.heard(m))


def test_carve():
    print("== the carve: its delay after the kill, the 2-tile reach, the cap, the hide after it, the decay ==")
    r = Rig(seed=4)
    r.enter()
    r.field()
    w = r.w
    w.s["next_wind"] = 1e9
    w.sc["hit"]["calm"] = 1.0
    w.weapons[0][3] = 0.0
    r.fresh()
    m = r.mark()
    r.hotbar_button(1)
    check("Carve with no corpse in reach: refused", H.TEXT_NO_CORPSE in r.heard(m))
    deer = stage(r, 6)
    r.send(actions.dclick(deer.serial))
    r.wait(1.9)
    corpse = next(c for c, it in w.items.items() if it["kind"] == "corpse")
    it = w.items[corpse]
    m = r.mark()
    r.send(actions.dclick(corpse))
    check("from 6 tiles: too far", w.metrics["refusals"].get("too far") == 1 and not r.heard(m))
    r.walk_to((it["x"], it["y"]), 1)
    m = r.mark()
    early = w.t < it["carve_t"]
    r.send(actions.dclick(corpse))
    check("right after the kill: 'You must wait a moment before carving again.'", early and H.TEXT_CARVE_WAIT in r.heard(m))
    r.wait(max(0.0, it["carve_t"] - w.t) + 0.1)
    m = r.mark()
    r.send(actions.use_skill(19))
    check("once allowed (Forensic Eval): the trophy over our head, the corpse gone",
          H.TEXT_TROPHY.format(8) in r.heard(m, w.player.serial) and w.s["held"] == [8]
          and corpse not in r.l.rt.state.items)
    m = r.mark()
    r.wait(1.0)
    r.send(actions.use_skill(21))
    check("hide 1 s after the carve: refused (their shared timer)", H.TEXT_HIDE_WAIT in r.heard(m))
    r.wait(2.1)
    m = r.mark()
    r.send(actions.use_skill(21))
    check("...3 s after: hidden", H.TEXT_HIDE in r.heard(m))
    r.fresh()
    w.s["held"] = [8]
    deer = stage(r, 2)
    r.send(actions.dclick(deer.serial))
    r.wait(2.0)
    w.s["held"] = [2] * 5
    r.wait(4.5)
    m = r.mark()
    r.hotbar_button(1)
    check("holding 5: 'Players can only carry 5 trophies...'", H.TEXT_CARVE_FULL in r.heard(m))
    corpse = next(c for c, it in w.items.items() if it["kind"] == "corpse")
    r.wait(26.0)
    check("the corpse decays 30 s after the kill", corpse not in w.items and corpse not in r.l.rt.state.items)


def test_turn_in():
    print("== the warden: the turn-in's line, score, ammo and weapon tiers ==")
    r = Rig(seed=5)
    r.enter()
    w = r.w
    warden = w.instances[w.s["inst"]]["warden"]
    w.s.update(held=[8, 10, 12], ammo=3)
    m = r.mark()
    r.send(actions.dclick(warden.serial))
    g = r.gumps(m, w.sc["gumps"]["warden"])[-1]
    check("his gump: the held points and the time left", "30 Pts" in g["lines"] and "Turn in Trophies and Resupply" in g["lines"]
          and any(hg.LEFT.match(x) for x in g["lines"]), str(g["lines"][-5:]))
    m = r.mark()
    r.send(actions.gump_reply(g["serial"], g["gump_id"], 1, g["layout"], g["lines"]))
    check("'Ammunition resupplied and 3 trophies turned in for 30 points (30 new score)'",
          "Ammunition resupplied and 3 trophies turned in for 30 points (30 new score)" in r.heard(m))
    check("3 turned in: 'Recurve Bow unlocked!' over our head", "Recurve Bow unlocked!" in r.heard(m, w.player.serial))
    check("ammo back to 15, nothing held", w.s["ammo"] == 15 and w.s["held"] == [])
    r.wait(5.0)
    st = r.status()
    check("GroundsLog: score 30, the Recurve Bow (its art on the hotbar)", st["score"] == 30
          and st["weapon"]["name"] == "Recurve Bow" and st["ammo"] == 15)
    w.s["held"] = [2, 2, 2, 2, 2]
    m = r.mark()
    r.send(actions.dclick(warden.serial))
    g = r.gumps(m, w.sc["gumps"]["warden"])[-1]
    r.send(actions.gump_reply(g["serial"], g["gump_id"], 1, g["layout"], g["lines"]))
    check("8 turned in: the Hunting Bow (6) unlocked", "Hunting Bow unlocked!" in r.heard(m) and w._tier(w.s["turned_in"]) == 2)


def test_expiry():
    print("== the session's end: the clock lines, the expiry, held trophies lost; the exit moongate ==")
    r = Rig(seed=6)
    r.enter()
    w = r.w
    pl = w.player
    m = r.mark()
    r.wait(301.0)
    check("10 minutes left at 300 s, over our head", H.MINUTES_TEXT.format(10) in r.heard(m, pl.serial))
    r.wait(300.0)
    check("5 minutes left at 600 s", H.MINUTES_TEXT.format(5) in r.heard(m, pl.serial))
    check("the wind's line every 30 s", sum(1 for t in r.heard(m) if hg.WIND.match(t)) == 20)
    w.s["held"] = [10, 10]
    r.status()
    r.wait(300.0)
    check("'Your permit for the Hunting Grounds has expired.' over our head", hg.EXPIRED_TEXT in r.heard(m, pl.serial))
    check("out to a Prevalia tile, hidden", w.s is None and [pl.x, pl.y] in w.sc["town"]["eject_to"] and w.hidden)
    check("the 20 held points lost", w.metrics["points_lost_at_expiry"] == 20 and w.metrics["sessions"][0]["end"] == "expired")
    check("GroundsLog: the run is over", r.status() is None)
    r.enter()
    gate = w.instances[w.s["inst"]]["gate"]
    w.s["held"] = [4]
    m = len(r.l.events)
    r.walk_to(gate)
    menus = r.gumps(m, w.sc["gumps"]["exit_gate"])
    check("a step onto the exit moongate opens its menu (live 2026-11-23), the session still on", menus and w.s is not None)
    g = menus[-1]
    m = len(r.l.events)
    r.send(actions.gump_reply(g["serial"] if isinstance(g["serial"], int) else int(g["serial"], 16),
                              w.sc["gumps"]["exit_gate"], 2, g["layout"], g["lines"]))
    again = r.gumps(m, w.sc["gumps"]["exit_gate"])
    check("the first Confirm asks again (live 2026-11-23 22:41:52.6), the session still on",
          w.s is not None and again and H.EXIT_GATE_AGAIN in r.heard(m), str(r.heard(m)))
    g = again[-1]
    r.send(actions.gump_reply(g["serial"] if isinstance(g["serial"], int) else int(g["serial"], 16),
                              w.sc["gumps"]["exit_gate"], 2, g["layout"], g["lines"]))
    check("the second Confirm ends the session, the held points lost", w.s is None
          and w.metrics["sessions"][1]["end"] == "gate" and w.metrics["points_lost_at_gate"] == 4)


def test_moods():
    print("== deer moods: the hazards by our hidden state, the wind, the recovery ==")
    r = Rig(seed=8, permits=1)
    r.enter()
    r.field()
    w = r.w
    w.s["next_wind"] = 1e9
    w.s["wind"] = hg.OCTANTS.index("south")        # a deer north of us is upwind: no penalty
    spooked = {}
    for hidden in (False, True):
        n = 0
        for _ in range(150):
            r.fresh()
            w.hidden = hidden
            deer = stage(r, 8, d=0)
            r.wait(5.0)
            n += deer.mood != "calm"
            if deer.alive:
                w.remove_deer(deer)
        spooked[hidden] = n / 150
    check("a calm deer 8 tiles off for 5 s: visible us spooks most (0.3/s: ~0.78), hidden few (~0.12)",
          spooked[False] > 0.6 and spooked[True] < 0.25, str(spooked))
    w.hidden = True
    up = stage(r, 8, d=0)
    down = stage(r, 8, d=4)
    side = w.place_deer(w.player.x + 6, w.player.y + 6, 8, "calm")
    hu, hd, hs = w.hazards(up), w.hazards(down), w.hazards(side)
    check("downwind (the wind blows from us to it) x1.3, one octant off x1.15",
          math.isclose(hd[0], hu[0] * 1.3) and math.isclose(hs[0], hu[0] * 1.15), str((hu, hd, hs)))
    w.hidden = False
    t = w.t
    up.mood, up.mood_t, up.sensed_t = "fleeing", t - 11, t - 11
    w._mood(up, 25)
    check("fleeing, nobody sensed for 10 s: nervous", up.mood == "nervous")
    up.mood_t = up.sensed_t = t - 11
    w._mood(up, 25)
    check("...10 s more: calm", up.mood == "calm")
    up.mood, up.mood_t, up.sensed_t = "nervous", t - 11, t - 11
    w._mood(up, 15)
    check("a visible player 15 off keeps a nervous deer from calming", up.mood != "calm")
    m = r.mark()
    r.wait(6.5)
    check("each deer in view says its mood label every 4-6 s",
          {e["serial"] for e in r.l.events[m:] if e["ev"] == "speech_heard" and e["text"] in hg.MOODS} >= {down.serial})
    r.send(actions.single_click(down.serial))
    check("a click on a deer: its label 'N point buck'", r.l.rt.state.labels.get(down.serial) == "8 point buck")


def test_density():
    print("== deer density in the deep band against the calibration (deer in view per 1000 region tiles) ==")
    w = H.HGWorld(load(), seed=11, permits=1, walkers=WALKERS)
    rng = random.Random(3)
    ratios = []
    for i in range(len(w.instances)):
        w.populate(i)
        inst = w.instances[i]
        x0, y0, x1, y1 = inst["region"]
        wy = inst["warden"].y
        deer = [(d.x, d.y) for d in inst["deer"]]
        got = []
        for _ in range(300):
            x, y = rng.randint(x0, x1), rng.randint(wy + 200, wy + 239)
            tiles = (min(x1, x + 18) - max(x0, x - 18) + 1) * (min(y1, y + 18) - max(y0, y - 18) + 1)
            got.append(1000.0 * sum(1 for dx, dy in deer if abs(dx - x) <= 18 and abs(dy - y) <= 18) / tiles)
        want = sum(b["per_1000"] * b["samples"] for b in w.sc["deer"]["density_by_band"] if b["dy"][0] >= 200) \
            / sum(b["samples"] for b in w.sc["deer"]["density_by_band"] if b["dy"][0] >= 200)
        ratios.append(sum(got) / len(got) / want)
    check("dy 200-240: within 30 % of the JSON's", all(0.7 <= x <= 1.3 for x in ratios), str([round(x, 2) for x in ratios]))
    pts = [d.points for d in w.instances[0]["deer"] if d.y - w.instances[0]["warden"].y >= 200]
    check("deep bucks average ~8.8 points", 7.5 <= sum(pts) / len(pts) <= 10.0, f"{sum(pts) / len(pts):.2f} (n {len(pts)})")


def test_violations():
    print("== packets a runner must never send: recorded ==")
    r = Rig()
    r.send(bytes([0x72, 1, 0, 0x32, 0]))
    r.send(actions.say_unicode("hello"))
    whats = [v["what"] for v in r.w.metrics["violations"]]
    check("war mode and speech", whats == ["war mode", "speech"], str(whats))


def test_intern():
    print("== the gump text table: each string once, in order of first use ==")
    layout, lines = H.intern("{ text 1 2 3 0 x }{ text 1 2 3 1 x }{ text 1 2 3 2 x }", ["a", "b", "a"])
    check("dedup", lines == ["a", "b"] and layout == "{ text 1 2 3 0 x }{ text 1 2 3 1 x }{ text 1 2 3 0 x }")


def test_runner():
    print("== loop_hg.HGLoop end to end (run_hg.run_once): two permits spent, points scored, nothing lost or forbidden ==")
    from sim import run_hg
    r = run_hg.run_once(seed=7, permits=2, extra_args=[])
    check("both permits spent, the runner done", r["outcome"] == "done" and r["runs"] == 2 and r["permits_left"] == 0,
          f"{r['outcome']} runs {r['runs']} left {r.get('permits_left')} {r.get('error') or ''}"[:300])
    check("points scored in each run (live runs 4-8: 126-150)", all(s >= 80 for s in r["scores"]), str(r["scores"]))
    check("nothing lost at the expiry, no forbidden packet", r["lost_at_expiry"] == 0 and not r["violations"],
          f"lost {r['lost_at_expiry']} violations {r['violations']}")


def test_runner_home():
    print("== the way home once the permits are spent: our own book's home rune (homes.json), tomes first ==")
    import home as home_mod
    import loop_hg
    from sim import run_hg
    house = home_mod.for_character(H.PLAYER_NAME)
    tried = []
    orig = (loop_hg.escape_mod.find_books, loop_hg.escape_mod.escape)

    def escape(io, book, rune=None, **kw):          # the recall itself is escape.py's (proven live by the hunt runs)
        tried.append((book, rune))
        if book == 0x40000001:
            raise loop_hg.escape_mod.RecallError(f"no rune {rune!r} in the rune tome 0x{book:08X}")
        pl = io.link.world.player
        pl.x, pl.y, pl.z = house["landing"]
        return {"ok": True, "failure": None, "attempts": 1}
    loop_hg.escape_mod.find_books = lambda world, me: [(0x40000001, "runetome"), (0x40000002, "runebook")]
    loop_hg.escape_mod.escape = escape
    try:
        r = run_hg.run_once(seed=8, permits=1, extra_args=["--home"])
    finally:
        loop_hg.escape_mod.find_books, loop_hg.escape_mod.escape = orig
    check("one run, then home: the tome without the rune passed over, the runebook's 'Logan's House' taken",
          r["outcome"] == "done" and r["runs"] == 1
          and tried == [(0x40000001, house["rune"]), (0x40000002, house["rune"])],
          f"{r['outcome']} {tried} {r.get('error') or ''}"[:300])


def test_runner_reveal():
    print("== a reveal the runner didn't cause (live 2026-11-25 23:15:36, a shot landing late after a world save): "
          "hidden again before it walks on ==")
    from sim import hgworld, run_hg
    orig = hgworld.HGWorld._walk
    seen = {"hidden_walks": 0, "hides": None, "visible_walks": 0}

    def walk(self, p):
        before = self.metrics["steps"]
        orig(self, p)
        if self.metrics["steps"] == before or p[1] & 0x80:
            return                              # a turn, a denied step or a running step
        if seen["hides"] is None:
            seen["hidden_walks"] += self.hidden
            # the server reveals us mid-stealth on a roam (the runner's own intent), no running step or shot of ours
            if seen["hidden_walks"] >= 40 and self.hidden and self.intents and self.intents[-1][1] == "roam":
                self._reveal()
                seen["hides"] = self.metrics["hides"]
        elif not self.hidden and self.metrics["hides"] == seen["hides"]:
            seen["visible_walks"] += 1          # walking on visible, not yet hidden again
    hgworld.HGWorld._walk = walk
    try:
        r = run_hg.run_once(seed=7, permits=1, extra_args=[])
    finally:
        hgworld.HGWorld._walk = orig
    check("revealed mid-stealth, then hidden again within 2 walking steps",
          r["outcome"] == "done" and seen["hides"] is not None and seen["visible_walks"] <= 2,
          f"{r['outcome']} {seen} {r.get('error') or ''}"[:300])


def main():
    if WALKERS.get(0) is None:
        print("SKIP: no map geometry for facet 0 (install files missing)")
        return 0
    t0 = time.perf_counter()
    test_intern()
    test_enter()
    test_hotbar()
    test_hide()
    test_shot_rates()
    test_refusals()
    test_carve()
    test_turn_in()
    test_expiry()
    test_moods()
    test_density()
    test_violations()
    test_runner()
    test_runner_reveal()
    test_runner_home()
    print(f"\n{len(FAILURES)} failure(s) in {time.perf_counter() - t0:.1f} s")
    for f in FAILURES:
        print("  FAIL:", f)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

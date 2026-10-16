"""Tests of the Mausoleum simulator (harness/sim/, docs/SIM.md).

The world on the real map (walls, sight), its mechanics as the runner meets them through SimLink and
the client's world model, and an end-to-end hunt of the unmodified --pets runner on virtual time.
Skips when the install's map files are absent (another computer).

Run: python harness/test_sim.py
"""
import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import actions  # noqa: E402
import pathfind  # noqa: E402
import tamer  # noqa: E402
from sim import clock as simclock  # noqa: E402
from sim.link import SimLink  # noqa: E402
from sim.scenario import bard_chance, load  # noqa: E402
from sim.world import Mob, World, cheb as sim_cheb  # noqa: E402

FAILURES = []
WALL_FROM, WALL_TO = (5266, 648, 0), (5265, 657, 0)     # live: "Target cannot be seen." (a stone wall at 5265,656)
SEEN_FROM = (5268, 661, 0)                              # live: Discordance at the same corpse purger, answered
FLOOR = (5266, 648)                                     # walkable; south of it is floor, the wall is at 5265,656


def check(name, cond, extra=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAILURES.append(name)


def world_with(spawns, seed=1, start=FLOOR):
    sc = load()
    sc["spawns"] = [{**s, "respawn_s": [9999, 9999]} for s in spawns]
    sc["player"]["start"] = [start[0], start[1], 0]
    return World(sc, seed=seed)


def link_for(world, epoch=1_800_000_000.0):
    clock = simclock.SimClock(epoch=epoch)
    return SimLink(world, clock), clock


def test_clock():
    print("== the virtual clock stands in for the runner's time ==")
    import agent_link
    c = simclock.SimClock(epoch=1_800_000_000.0)
    ticks = []
    c.on_advance = ticks.append
    patched = simclock.install(c)
    try:
        t0 = agent_link.time.monotonic()
        agent_link.time.sleep(2.5)
        check("sleep advances the clock, not the wall", agent_link.time.monotonic() - t0 == 2.5 and ticks == [2.5])
        check("time() is the run's epoch plus elapsed", agent_link.time.time() == 1_800_000_002.5)
        check("the harness modules are patched (agent_link, tamer)", agent_link in patched and tamer in patched)
    finally:
        simclock.uninstall(patched)
    import time
    check("uninstall restores the real module", agent_link.time is time)


def test_geometry():
    print("== walls and sight from the map, as the live server answered ==")
    w = world_with([])
    check("a wall blocks sight where the live server said 'Target cannot be seen.'",
          not w.sight.can_see(WALL_FROM, WALL_TO))
    check("the live server's accepted target is in sight", w.sight.can_see(SEEN_FROM, WALL_TO))
    link, clock = link_for(w)
    pl = w.player
    x, y = pl.x, pl.y
    for d in (4, 4):                             # turn (already facing north: a turn first), then step south
        link.send(actions.walk(d, run=True))
    check("a step on the floor is confirmed and moves us", (pl.x, pl.y) == (x, y + 1)
          and link.state()["movement"]["pos"][:2] == [x, y + 1], str(pl.pos))
    w2 = world_with([], start=(5265, 655))
    link2, _ = link_for(w2)
    for d in (4, 4):
        link2.send(actions.walk(d, run=True))
    check("a step into the wall is denied (0x21) and we stay", (w2.player.x, w2.player.y) == (5265, 655)
          and w2.metrics["denied_steps"] == 1, str(w2.player.pos))


def test_aggro():
    print("== monsters aggro within range and sight; guarding pets take it ==")
    behind = world_with([{"key": "corpse purger", "at": [5265, 657, 0]}])
    link, clock = link_for(behind)
    clock.advance(3.0)
    m = next(m for m in behind.mobs.values() if m.kind == "monster")
    check("a monster 9 tiles off behind a wall doesn't aggro", m.target is None, str(m.target))
    open_ = world_with([{"key": "shade wolf", "at": [5266, 654, 0]}])
    link, clock = link_for(open_)
    link.send(tamer.command("all guard me"))
    clock.advance(4.0)                         # the scenario's wake delay, 1.5-3 s after first sight
    m = next(m for m in open_.mobs.values() if m.kind == "monster")
    pets = {p.serial for p in open_.pets()}
    check("one 6 tiles off in the open aggroes, on a guarding pet", m.target in pets, str(m.target))
    clock.advance(10.0)
    check("the pets fight it ('-N' over it) and it fights back", m.hits < m.hits_max
          and sum(open_.metrics["pet_damage_taken"].values()) > 0, f"{m.hits}/{m.hits_max}")


def discord(link, m):
    """Discordance at `m` through the client: the skill, then the cursor answered with it."""
    import combat
    link.send(actions.use_skill(tamer.SKILL_DISCORD))
    st = link.state()
    link.send(combat.target_mobile(st["world"]["target"], m.serial, st["world"]["mobiles"][f"0x{m.serial:08X}"]))


def test_aggro_rules():
    print("== a monster goes for whichever of us it sees first; our act pulls it only before the pets hit ==")
    results = {}
    for where, ys in (("behind", 646), ("between", 650)):
        w = world_with([{"key": "shade wolf", "at": [5266, 654, 0]}])
        m = next(m for m in w.mobs.values() if m.kind == "monster")
        m.spec = {**m.spec, "aggro_range": 7}                   # we at 6; the pets at 8 (behind) or 4 (between)
        w.sc["defaults"]["wake_s"] = None                       # who it picks, at once: no wake delay here
        for p, x in zip(w.pets(), (5266, 5267)):
            p.x, p.y = x, ys
        link, clock = link_for(w)
        link.send(tamer.command("all guard me"))
        clock.advance(0.6)
        tgt = w.mobs.get(m.target)
        results[where] = tgt.kind if tgt else None
    check("pets on guard behind us, out of its range: it goes for us; the pets between: for a pet",
          results == {"behind": "player", "between": "pet"}, str(results))
    pulled = {}
    for hurt_first in (False, True):
        w = world_with([{"key": "shade wolf", "at": [5266, 651, 0]}], seed=3)
        m = next(m for m in w.mobs.values() if m.kind == "monster")
        gb = next(p for p in w.pets() if p.name == "GraveRobber")
        m.target, m.threat = gb.serial, {gb.serial: 1}
        if hurt_first:
            m.damage_by, m.threat = {gb.serial: 50}, {gb.serial: 200}
        link, clock = link_for(w)
        discord(link, m)
        clock.advance(0.6)
        pulled[hurt_first] = m.target == w.player.serial
    check("Discordance before the pets hurt it turns it on us; after, it stays on the pet",
          pulled == {False: True, True: False}, str(pulled))


def test_specials():
    print("== the specials that reached Logan live: bleeds, the Charge, transfixed copies ==")
    w = world_with([{"key": "bound soul", "at": [5267, 649, 0]}], seed=2)
    m = next(m for m in w.mobs.values() if m.kind == "monster")
    m.spec = {**m.spec, "specials": [{**m.spec["specials"][0], "chance": 1.0}]}
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    gb.hits = gb.hits_max = 100000
    m.target, m.threat = gb.serial, {gb.serial: 1e6}
    link, clock = link_for(w)
    seen = []
    for _ in range(200):                                    # 20 s: the opener, its 5 ticks, the next opener
        clock.advance(0.1)
        seen += [e for e in w.player_log if e not in seen]
    link.state()                                            # the client takes the server's texts
    texts = [e.get("text") for e in link.events]
    t0, opener = next(((e[0], e[2]) for e in seen if e[1] == "bound soul"), (None, None))
    ticks = [e for e in seen if e[1] == "bleed" and t0 is not None and t0 < e[0] <= t0 + 15.2]
    check("a bound soul meleeing the gravebug bleeds us a tile off: the opener, then 8-10 every 3 s x5, "
          "'You are no longer bleeding.'",
          t0 is not None and 23 <= opener <= 32 and len(ticks) == 5 and all(8 <= e[2] <= 10 for e in ticks)
          and all(abs(b[0] - a[0] - 3.0) < 0.15 for a, b in zip(ticks, ticks[1:]))
          and "They begin to draw blood from you!" in texts and "You are no longer bleeding." in texts,
          f"opener {t0} {opener} ticks {[(round(e[0], 1), e[2]) for e in ticks]}")
    w = world_with([{"key": "nightstalker", "at": [5265, 654, 0]}], seed=4)   # 6 tiles, in sight of the gravebug
    m = next(m for m in w.mobs.values() if m.kind == "monster")
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    m.target, m.threat = gb.serial, {gb.serial: 1e6}
    link, clock = link_for(w)
    clock.advance(1.0)
    link.state()
    charged = [o for o in w.team() if cheb_(o, m) <= 1]
    check("the nightstalker charges 6 tiles onto the gravebug and hits each of us within 1 tile of it",
          any(e.get("text") == "*charges*" for e in link.events) and cheb_(gb, m) <= 1
          and all(o.hits < o.hits_max for o in charged),
          f"at {m.pos}, hit {[(o.name, o.hits) for o in charged]}")
    w = world_with([{"key": "vampire countess", "at": [5266, 651, 0]}], seed=5)
    m = next(m for m in w.mobs.values() if m.kind == "monster")
    m.spec = {**m.spec, "specials": [{**sp, "chance": 1.0} if sp["kind"] == "transfix" else sp
                                     for sp in m.spec["specials"]]}
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    m.target, m.threat = gb.serial, {gb.serial: 1e6}
    link, clock = link_for(w)
    clock.advance(3.0)                                  # past the copy's wake (live 0-2.5 s)
    copies = [c for c in w.mobs.values() if c.key == "transfixed copy"]
    seen = link.state()["world"]["mobiles"]
    c = copies[0] if copies else None
    ours = {p.name for p in w.pets()}
    orig = next((p for p in w.pets() if c is not None and p.serial == c.spec.get("copy_of")), None)
    check("transfix: a hostile copy named as one of our pets, on its tile, going for one of us but its original",
          c is not None and c.name in ours and seen.get(f"0x{c.serial:08X}", {}).get("notoriety") == 3
          and c.target in {o.serial for o in w.team()} and c.target != (orig.serial if orig else None),
          str([(c.name, c.noto, c.target) for c in copies]))
    m.spec = {**m.spec, "specials": []}
    at = orig.pos if orig else None
    clock.advance(1.5)
    check("...while its original stands transfixed: no target, not a step",
          orig is not None and orig.target is None and orig.pos == at, f"{orig and (orig.target, orig.pos, at)}")
    clock.advance(9.0)
    check("...and the copy vanishes after 9.9-11 s, leaving no corpse",
          c is not None and c.serial not in w.mobs
          and not any(it.get("owner") == c.serial for it in w.items.values()), "")
    w = world_with([{"key": "shade wolf", "at": [5266, 649, 0]}], seed=6)
    m = next(m for m in w.mobs.values() if m.kind == "monster")
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    gb.hits = gb.hits_max = 100000
    m.hits = m.hits_max = 1e7
    m.target, m.threat = gb.serial, {gb.serial: 1e6}
    link, clock = link_for(w)
    gone = back = None
    for _ in range(300):
        clock.advance(0.1)
        if gone is None and m.hidden_until:
            gone = clock.elapsed
            unseen = f"0x{m.serial:08X}" not in link.state()["world"]["mobiles"]
        if gone is not None and not m.hidden_until:
            back = clock.elapsed
            break
    check("a shade wolf vanishes from the client mid-fight and comes back 3.5-6 s later on the gravebug",
          gone is not None and unseen and back is not None and 3.5 <= back - gone <= 6.2 and m.target == gb.serial,
          f"gone {gone} back {back} target {m.target}")


def cheb_(a, b):
    return max(abs(a.x - b.x), abs(a.y - b.y))


def test_bard_and_vet():
    print("== bard skills and vet supplies answer as the live server did ==")
    w = world_with([{"key": "shade wolf", "at": [5266, 651, 0]}], seed=3)
    link, clock = link_for(w)
    m = next(m for m in w.mobs.values() if m.kind == "monster")
    mark = len(link.events)
    link.send(actions.use_skill(tamer.SKILL_DISCORD))
    cur = link.state()["world"]["target"]
    check("Discordance: the prompt and a target cursor", cur.get("active") and any(
        e.get("text", "").startswith("What do you wish to discord?") for e in link.events[mark:]), str(cur))
    st = link.state()
    mob = st["world"]["mobiles"][f"0x{m.serial:08X}"]
    import combat
    link.send(combat.target_mobile(cur, m.serial, mob))
    answers = [tamer.bard_answer(e) for e in link.events[mark:] if tamer.bard_answer(e)]
    check("the answer parses with tamer.bard_answer (ok or fail, discord)",
          any(a[0] == tamer.SKILL_DISCORD and a[1] in ("ok", "fail") for a in answers), str(answers))
    mark = len(link.events)
    link.send(actions.use_skill(tamer.SKILL_PEACE))
    check("another skill within the 5 s gap: cliloc 500118", any(e.get("cliloc") == tamer.CLILOC_SKILL_WAIT
                                                                 for e in link.events[mark:]))
    sc = w.sc
    check("the fitted barding chance: shade wolf (difficulty 86.7) above 0.6, the minimum at 145+",
          bard_chance(sc, 86.7) > 0.6 and abs(bard_chance(sc, 145.2) - 0.33 * sc["bard"]["effective_skill"] / 100) < 1e-9)
    w = world_with([])
    link, clock = link_for(w)
    gb = next(p for p in w.pets() if p.key == "gravebug")
    gb.hits = gb.hits_max * 0.5
    vet = next(s for s, it in w.items.items() if it.get("kind") == "vet")

    def until(texts, limit=10.0):
        end = clock.elapsed + limit
        while clock.elapsed < end and not any(e.get("text") in texts for e in link.events[mark:]):
            link.state()
    mark = len(link.events)
    link.send(actions.dclick(vet))
    t_begin = clock.elapsed
    until((tamer.TEXT_VET_FINISH, tamer.TEXT_VET_FAR))
    took = clock.elapsed - t_begin
    check("vet supplies: begin, then finish 5.0-5.3 s later, the gravebug healed",
          any(e.get("text") == tamer.TEXT_VET_BEGIN for e in link.events[mark:]) and 4.9 <= took <= 5.5
          and gb.hits > gb.hits_max * 0.5, f"{took:.2f} s, {gb.hits}/{gb.hits_max}")
    for p in w.pets():
        p.mode = "stay"
    gb.hits, gb.x, gb.y = gb.hits_max * 0.5, w.player.x + 4, w.player.y
    w.player.hits = w.player.hits_max
    clock.advance(1.0)
    mark = len(link.events)
    link.send(actions.dclick(vet))
    until((tamer.TEXT_VET_FINISH, tamer.TEXT_VET_FAR))
    check("a pet 4 tiles off at the finish: 'You are too far away ...'",
          any(e.get("text") == tamer.TEXT_VET_FAR for e in link.events[mark:]))
    mark = len(w.metrics["violations"])
    link.send(actions.say_unicode("GraveRobber release"))
    check("a release phrase reaches the sim only as a recorded violation (nothing released)",
          w.metrics["violations"][mark:] and all(p.alive for p in w.pets()))


def test_hunt():
    print("== the unmodified --pets runner hunts the simulated level on virtual time ==")
    from sim import run as simrun
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = simrun.run_once(1, [], minutes=8)
    check("the run ends without a crash", r["outcome"] in ("done", "aborted"), f"{r['outcome']} {r['error']}")
    check("it recalled in and out, killed and looted", r["recalls"] >= 2 and r["kills"] >= 1 and r["gold_looted"] > 0,
          f"recalls {r['recalls']} kills {r['kills']} gold {r['gold_looted']}")
    check("no war mode, attack, spell, menu pick or release", r["violations"] == [], str(r["violations"]))
    check("'All Guard Me' after arriving; vet supplies used", r["guard_commands"] >= 1 and r["vet_uses"] >= 1,
          f"guard {r['guard_commands']} vet {r['vet_uses']}")
    check("much faster than real time", (r["speed_x"] or 0) > 20, f"{r['speed_x']}x")


def test_standoff():
    print("== a charger fighting the gravebug: stand 3 tiles off, unless the gravebug needs the vet ==")
    import tempfile
    import loop_hunt
    import memory
    from sim import run as simrun
    out = {}
    for frac in (0.9, 0.5):
        w = world_with([{"key": "nightstalker", "at": [5268, 648, 0]}], seed=7)
        m = next(m for m in w.mobs.values() if m.kind == "monster")
        m.spec = {**m.spec, "specials": [], "melee": [1, 1]}       # no Charge in the test: only who stands where
        gb = next(p for p in w.pets() if p.name == "GraveRobber")
        gb.hits = gb.hits_max = 100000
        gb.hits = int(gb.hits_max * frac)
        m.target, m.threat = gb.serial, {gb.serial: 1e9}
        link, clock = link_for(w)
        link.send(tamer.command("all guard me"))
        db = os.path.join(tempfile.mkdtemp(prefix="sim_test_"), "harness.db")
        args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, [], 10, at_hunt=True)
                                                 + ["--memory", db, "--seed", "1", "--human", "off"])
        patched = simclock.install(clock)
        store = memory.Memory(db)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                loop = loop_hunt.HuntLoop(link, store, args)
                loop.team.setup(loop.state())
                clock.advance(1.0)
                walked = loop.team.stand_off(loop.state())
        finally:
            store.close()
            simclock.uninstall(patched)
        out[frac] = (walked, cheb_(w.player, m), cheb_(w.player, gb))
    check("gravebug at 90%: walk out to 3+ tiles from the nightstalker and the gravebug; at 50%: stay to heal it",
          out[0.9][0] and min(out[0.9][1:]) >= 3 and out[0.5][0] is False, str(out))


def test_fire():
    print("== a forgotten soul's spell leaves fire under us: step off it, the pets kept in vet reach ==")
    import tempfile
    import loop_hunt
    import memory
    from sim import run as simrun
    w = world_with([{"key": "forgotten soul", "at": [5268, 648, 0]}], seed=7)
    m = next(m for m in w.mobs.values() if m.kind == "monster")
    m.spec = {**m.spec, "melee": None, "ranged": {**m.spec["ranged"], "damage": [1, 1], "every_s": 1.0, "keep": 10}}
    m.target, m.threat = w.player.serial, {w.player.serial: 1e9}
    link, clock = link_for(w)
    db = os.path.join(tempfile.mkdtemp(prefix="sim_test_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, [], 10, at_hunt=True)
                                             + ["--memory", db, "--seed", "1", "--human", "off"])
    patched = simclock.install(clock)
    store = memory.Memory(db)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            loop = loop_hunt.HuntLoop(link, store, args)
            loop.team.setup(loop.state())
            for _ in range(40):
                clock.advance(0.5)
                if w.player.pos[:2] in tamer.Team.fire_tiles(loop.state()):
                    break
            under = w.player.pos[:2] in tamer.Team.fire_tiles(loop.state())
            walked = loop.team.out_of_fire(loop.state())
            st = loop.state()
    finally:
        store.close()
        simclock.uninstall(patched)
    fire = tamer.Team.fire_tiles(st)
    pets = [p for p in w.pets() if p.alive]
    check("fire under us, then a step off it with every pet within --vet-range",
          under and walked and w.player.pos[:2] not in fire
          and all(cheb_(w.player, p) <= args.vet_range for p in pets),
          f"under {under} walked {walked} at {w.player.pos} fire {fire} pets {[cheb_(w.player, p) for p in pets]}")


def test_pet_res():
    print("== a pet dies: 'All Follow Me', the vet supplies resurrect its ghost; no leave meanwhile ==")
    import tempfile
    import loop_hunt
    import memory
    from sim import run as simrun
    w = world_with([], seed=7)
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    link, clock = link_for(w)
    db = os.path.join(tempfile.mkdtemp(prefix="sim_test_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, [], 10, at_hunt=True)
                                             + ["--memory", db, "--seed", "1", "--human", "off"])
    patched = simclock.install(clock)
    store = memory.Memory(db)
    leaves, said = [], []
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            loop = loop_hunt.HuntLoop(link, store, args)
            loop.team.setup(loop.state())
            mark = w.metrics["follow_commands"]
            w.die(gb, None)
            for _ in range(30):
                clock.advance(0.5)
                st = loop.state()
                leaves.append(loop.leave_reason(st))
                loop.team.care(st)
                if not gb.ghost:
                    break
            said = w.metrics["follow_commands"] - mark
    finally:
        store.close()
        simclock.uninstall(patched)
    check("the ghost followed ('All Follow Me'), came back from a vet channel, and no leave was asked meanwhile",
          said >= 1 and not gb.ghost and gb.hits > 0 and not any(leaves),
          f"follow {said} ghost {gb.ghost} hits {gb.hits:.0f} leaves {[l for l in leaves if l][:2]}")


def test_retreat_sight():
    print("== backing off: to a tile no idle monster sees; an apparition: stand off it and its pet ==")
    import tempfile
    import loop_hunt
    import memory
    from sim import run as simrun
    # live 20261015_090607: the apparition on Logan at (5210, 726), a second one idle at (5195, 722)
    w = world_with([{"key": "apparition", "at": [5210, 726, 0]}, {"key": "apparition", "at": [5195, 722, 0]}],
                   seed=7, start=(5211, 725))
    a, b = sorted((m for m in w.mobs.values() if m.kind == "monster"), key=lambda m: -m.x)
    a.spec = {**a.spec, "specials": [], "melee": [1, 1], "step_s": 999.0}    # stays put: only where we stand matters
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    gb.hits = gb.hits_max = 100000
    a.target, a.threat = gb.serial, {gb.serial: 1e9}
    link, clock = link_for(w)
    db = os.path.join(tempfile.mkdtemp(prefix="sim_test_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, [], 10, at_hunt=True)
                                             + ["--memory", db, "--seed", "1", "--human", "off"])
    patched = simclock.install(clock)
    store = memory.Memory(db)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            loop = loop_hunt.HuntLoop(link, store, args)
            loop.team.setup(loop.state())
            for _ in range(20):
                clock.advance(0.5)
            st = loop.state()
            seen = st["world"]["mobiles"][f"0x{a.serial:08X}"]
            to = loop.team.retreat_tile(st, [(seen["x"], seen["y"])])
            v = loop.team.view(st)
            seen_by_b = to is not None and any(m["serial"] == b.serial for m in v.wakes(to, margin=0))
            zone = loop.team.area_zone(st)
            walked = loop.team.stand_off(st)
    finally:
        store.close()
        simclock.uninstall(patched)
    check("the back-off tile is 7+ from the apparition on us and out of the idle one's sight",
          to is not None and max(abs(to[0] - a.x), abs(to[1] - a.y)) >= tamer.RETREAT_R and not seen_by_b,
          f"to {to} seen by B {seen_by_b} A {a.pos} B {b.pos}")
    check("the apparition in war mode is an area hitter: we step AREA_R off it", (a.x, a.y) in zone and walked
          and cheb_(w.player, a) >= tamer.AREA_R["apparition"], f"zone {zone} walked {walked} at {w.player.pos}")


def test_spacing():
    print("== --vet-range 5: a pet tanking: step 3 off it and its monster, every pet within 4 ==")
    import tempfile
    import loop_hunt
    import memory
    from sim import run as simrun
    out = {}
    for vr in (2, 5):
        w = world_with([{"key": "shade wolf", "at": [5212, 726, 0]}], seed=7, start=(5211, 724))
        m = next(m for m in w.mobs.values() if m.kind == "monster")
        m.spec = {**m.spec, "specials": [], "melee": [1, 1], "step_s": 999.0}
        gb = next(p for p in w.pets() if p.name == "GraveRobber")
        gb.hits = gb.hits_max = 100000
        m.target, m.threat = gb.serial, {gb.serial: 1e9}
        link, clock = link_for(w)
        link.send(tamer.command("all guard me"))
        db = os.path.join(tempfile.mkdtemp(prefix="sim_test_"), "harness.db")
        args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, [], 10, at_hunt=True)
                                                 + ["--memory", db, "--seed", "1", "--human", "off", "--vet-range", str(vr)])
        patched = simclock.install(clock)
        store = memory.Memory(db)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                loop = loop_hunt.HuntLoop(link, store, args)
                loop.team.setup(loop.state())
                for _ in range(10):
                    clock.advance(0.5)
                walked = loop.team.spacing(loop.state())
        finally:
            store.close()
            simclock.uninstall(patched)
        out[vr] = (walked, cheb_(w.player, gb), cheb_(w.player, m), max(cheb_(w.player, p) for p in w.pets()))
    check("vet range 2: no spacing; 5: 3+ from the gravebug and the wolf, every pet within 4",
          out[2][0] is False and out[5][0] and min(out[5][1:3]) >= tamer.SPACING_R and out[5][3] <= 4, str(out))


def test_stairs():
    print("== the Mausoleum stairs beside the arrival: routes keep off them; an accidental trip up steps back ==")
    import tempfile
    import loop_hunt
    import memory
    import nav
    from sim import run as simrun
    w = world_with([], seed=7, start=(5201, 739))      # SE of the stairs: the straight way to the landing crosses them
    for m in [w.player, *w.pets()]:
        m.z = 17                                                   # the fight level's arrival floor
    link, clock = link_for(w)
    db = os.path.join(tempfile.mkdtemp(prefix="sim_test_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, [], 10, at_hunt=True)
                                             + ["--memory", db, "--seed", "1", "--human", "off"])
    patched = simclock.install(clock)
    store = memory.Memory(db)
    stairs = {(5199, 738), (5200, 738), (5200, 736)}
    went_up = []
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            loop = loop_hunt.HuntLoop(link, store, args)
            mv = loop.mover
            path, _ = mv.plan(loop.state(), nav.within((5199, 737), 0), mobiles=False)
            known = stairs <= mv.teleporter_tiles(0)
            mv.teleporter_tiles(0).difference_update(stairs)        # as before the seeds: walk over them
            n0 = mv.teleports
            try:
                mv.walk_to(lambda: (5199, 737), 0, "test", max_moves=12)
            except Exception as e:                                 # noqa: BLE001 - the end position is the check
                went_up.append(str(e))
            went_up.append(mv.teleports - n0)
            back = w.player.pos
    finally:
        store.close()
        simclock.uninstall(patched)
    check("the seeded stairs are known and no route crosses them", known and path and not set(path) & stairs,
          f"known {known} path {path}")
    check("a walk over them went up and came straight back down to Maus 3",
          went_up[-1] >= 2 and abs(back[0] - 5199) <= 6 and abs(back[1] - 738) <= 6, f"at {back} {went_up}")


def test_backspawn():
    print("== backspawn: a tile in the open near a stealther's spawn scores worse than one with the pets between ==")
    import tempfile
    import loop_hunt
    import memory
    from sim import run as simrun
    # live 20261015_171928 18:40:10: Logan at (5176, 688), 3 south of the pets, the shade wolves' and the stalker's
    # room behind him; a nightstalker unseen till then hit him from (5176, 690)
    w = world_with([], seed=7, start=(5176, 688))
    for p, xy in zip(w.pets(), ((5176, 685), (5177, 684))):
        p.x, p.y = xy
    link, clock = link_for(w)
    db = os.path.join(tempfile.mkdtemp(prefix="sim_test_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, [], 10, at_hunt=True)
                                             + ["--memory", db, "--seed", "1", "--human", "off"])
    patched = simclock.install(clock)
    store = memory.Memory(db)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            loop = loop_hunt.HuntLoop(link, store, args)
            loop.team.setup(loop.state())
            clock.advance(0.5)
            v = loop.team.view(loop.state())
            open_, behind = v.stand_score((5176, 688)), v.stand_score((5176, 684))
            kinds = set(loop.team.backspawn.values())
    finally:
        store.close()
        simclock.uninstall(patched)
    check("the scenario's stealthers seed spawn spots; south of the pets scores worse than beside them",
          {"nightstalker", "shade wolf"} <= kinds and open_ > behind, f"{kinds} open {open_} behind {behind}")


def hurt_loop(w, hits, extra=()):
    """A HuntLoop at the hunt with a visit started, Logan at `hits`, the pets beside him on guard."""
    import tempfile
    import loop_hunt
    import memory
    from sim import run as simrun
    for p, xy in zip(w.pets(), ((w.player.x - 1, w.player.y), (w.player.x, w.player.y + 1))):
        p.x, p.y = xy
    w.player.hits = hits
    link, clock = link_for(w)
    link.send(tamer.command("all guard me"))
    db = os.path.join(tempfile.mkdtemp(prefix="sim_test_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, [], 10, at_hunt=True)
                                             + ["--memory", db, "--seed", "1", "--human", "off", "--vet-range", "5",
                                                "--heal-in-place", *extra])
    patched = simclock.install(clock)
    store = memory.Memory(db)
    with contextlib.redirect_stdout(io.StringIO()):
        loop = loop_hunt.HuntLoop(link, store, args)
        loop.team.setup(loop.state())
        loop.spot = loop.fight_spot = tuple(w.player.pos[:2])
        loop.start_visit(loop.state())
    return loop, clock, store, patched


def test_area_first():
    print("== an apparition on our tile: a ready potion at once, then out of its reach, mid-channel ==")
    import time
    from agent_link import cheb
    # live 20261015_171928 18:57:09-19.5: on Logan's tile, -59; 4 s on a potion (its satchel opened first) and a
    # vet channel there, then -60 more
    out = {}
    for ready in (True, False):
        w = world_with([{"key": "apparition", "at": [5207, 726, 0]}], seed=7, start=(5207, 726))
        m = next(m for m in w.mobs.values() if m.kind == "monster")
        m.spec = {**m.spec, "step_s": 999.0}
        loop, clock, store, patched = hurt_loop(w, 41)
        gb = next(p for p in w.pets() if p.name == "GraveRobber")
        m.target, m.threat = gb.serial, {gb.serial: 1e9}         # it fights the gravebug beside us
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                clock.advance(0.5)
                loop.team.vet_busy_until = time.monotonic() + 5.0            # a channel running
                if not ready:
                    loop.potions.started(time.monotonic())                   # one drunk just now
                loop.tick()
                first = (loop.visit.get("potions", 0), cheb(w.player.pos, m.pos))
                loop.tick()
                out[ready] = (first, (loop.visit.get("potions", 0), cheb(w.player.pos, m.pos)))
        finally:
            store.close()
            simclock.uninstall(patched)
    r = tamer.AREA_R["apparition"]
    check("a potion ready: drunk on the first tick, 3+ tiles off it on the second; none: off it on the first",
          out[True][0][0] == 1 and out[True][1][1] >= r and out[False][0] == (0, out[False][0][1])
          and out[False][0][1] >= r, str(out))


def test_leave_called_off():
    print("== a hurt leave healed on the way out is called off; one still hurt goes through ==")
    from agent_link import Abort, cheb
    # live 20261015_171928 18:57:20-40: out at 8/100, 98 by the gate, then waited there in a banshee's sight
    out = {}
    for healed in (True, False):
        w = world_with([], seed=7, start=(5207, 726))
        loop, clock, store, patched = hurt_loop(w, 8)
        world_tick, t0 = clock.on_advance, []

        def heal_up(now, world_tick=world_tick, t0=t0, w=w, healed=healed):
            world_tick(now)
            t0.append(now)
            if now - t0[0] > 3:
                w.player.hits = 98 if healed else min(w.player.hits, 40)
        clock.on_advance = heal_up
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                try:
                    loop.tick()
                except Abort:
                    pass                                   # gone: no --enter-recall in the test
            out[healed] = (loop.visit is not None, cheb(w.player.pos, tuple(loop.args.leave_gate)))
        finally:
            store.close()
            simclock.uninstall(patched)
    check("healed: still on the visit, short of the gate; hurt: through it",
          out[True][0] and out[True][1] > 1 and not out[False][0], str(out))


def test_instrument():
    print("== the instrument last played is gone: the prompt answered with one from the pack; none: skills off ==")
    # live 20261015_171928 after the 18:40 death: 54 "What instrument shall you play?" answered with the
    # skill's target ("That is not a musical instrument."), every bard skill and song lost for two runs
    out = {}
    for harp in (True, False):
        w = world_with([{"key": "shade wolf", "at": [5268, 648, 0]}], seed=7)
        m = next(m for m in w.mobs.values() if m.kind == "monster")
        lute = w.played
        del w.items[lute]
        if harp:
            w.items[0x416ACB89] = {"graphic": 0x0EB2, "amount": 1, "container": w.backpack, "kind": "instrument"}
        loop, clock, store, patched = hurt_loop(w, 100)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                clock.advance(0.5)
                mark = len(loop.link.events)
                loop.team.use(tamer.SKILL_DISCORD, m.serial)
                answers = [tamer.bard_answer(e) for e in loop.link.events[mark:] if tamer.bard_answer(e)]
                out[harp] = (w.played == (0x416ACB89 if harp else lute), any(a[1] in ("ok", "fail") for a in answers),
                             loop.team.no_instrument)
        finally:
            store.close()
            simclock.uninstall(patched)
    check("the lap harp picked and the discord answered; with none, the bard skills off",
          out[True] == (True, True, False) and out[False] == (True, False, True), str(out))


def test_heal_in_place():
    print("== --heal-in-place: a pet below --pet-leave-at is no leave while vet supplies last ==")
    import tempfile
    import loop_hunt
    import memory
    from sim import run as simrun
    out = {}
    for supplies in (True, False):
        sc = load()
        if not supplies:
            sc["loadout"]["items"] = [it for it in sc["loadout"]["items"] if it.get("kind") != "vet"]
        w = World(sc, seed=1)
        gb = next(p for p in w.pets() if p.name == "GraveRobber")
        gb.hits = int(gb.hits_max * 0.15)
        link, clock = link_for(w)
        db = os.path.join(tempfile.mkdtemp(prefix="sim_test_"), "harness.db")
        args = loop_hunt.arg_parser().parse_args(simrun.runner_args(sc, ["--pet-leave-at", "0.35"], 10, at_hunt=True)
                                                 + ["--memory", db, "--seed", "1"])
        patched = simclock.install(clock)
        store = memory.Memory(db)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                loop = loop_hunt.HuntLoop(link, store, args)
                loop.team.setup(loop.state())
                out[supplies] = loop.leave_reason(loop.state())
        finally:
            store.close()
            simclock.uninstall(patched)
    check("gravebug at 15 %: with vet supplies no leave (heal it), without them the pet leave",
          args.heal_in_place and out[True] is None and out[False] and "GraveRobber" in out[False][0], str(out))


def test_songs():
    print("== --songs: the runner plays the three barding songs at the ground; they cut the damage we take ==")
    import tempfile
    import loop_hunt
    import memory
    from sim import run as simrun
    sc = load()
    w = World(sc, seed=1)
    link, clock = link_for(w)
    db = os.path.join(tempfile.mkdtemp(prefix="sim_test_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(sc, [], 10, at_hunt=True)
                                             + ["--memory", db, "--seed", "1"])
    patched = simclock.install(clock)
    store = memory.Memory(db)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            loop = loop_hunt.HuntLoop(link, store, args)
            loop.team.setup(loop.state())
            end = clock.elapsed + 60
            while len(w.songs) < 3 and clock.elapsed < end:
                if not loop.team.song(loop.state()):
                    clock.advance(0.5)
            again = loop.team.song(loop.state())
    finally:
        store.close()
        simclock.uninstall(patched)
    starts = sorted(until - sc["bard"]["song_s"] for until in w.songs.values())
    check("all three songs play, each 10 s or more after the last (the song cooldown)",
          args.songs and set(w.songs) == {"discord", "peace", "provoke"}
          and all(b - a >= 10.0 - 1e-6 for a, b in zip(starts, starts[1:])), f"{w.songs} {starts}")
    check("...and none is played again while all run", again is False)
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    m = Mob(10**6, "monster", "shade wolf", "a shade wolf", 23, gb.x + 1, gb.y, gb.z, hits=100, hits_max=100)
    h0 = gb.hits
    w.hurt(gb, 100, m)
    want = round(100 * (1 - sc["bard"]["song_x"]))
    check(f"Song of Discordance: a creature's 100 hits our pet for {want}", h0 - gb.hits == want, f"{h0 - gb.hits}")


def test_pull_budget():
    print("== aggro.py: the pull budget counts what would see us (line of sight); a --never-aggro type in sight is "
          "no-go ==")
    import tempfile
    import loop_hunt
    import memory
    from sim import run as simrun
    out = {}
    for case, spawns, budget in (
            ("wolf in sight", [("nightstalker", (5266, 650)), ("shade wolf", (5266, 659))], 2.5),
            ("wolf behind the wall", [("nightstalker", (5266, 650)), ("shade wolf", (5267, 659))], 2.5),
            ("wolf in sight, budget 3", [("nightstalker", (5266, 650)), ("shade wolf", (5266, 659))], 3.0),
            ("idle wolf alone", [("shade wolf", (5266, 659))], 2.5),
            ("consort in sight", [("vampiric consort", (5266, 660))], 2.5)):
        w = world_with([{"key": k, "at": [x, y, 0]} for k, (x, y) in spawns])
        link, clock = link_for(w)
        db = os.path.join(tempfile.mkdtemp(prefix="sim_test_"), "harness.db")
        args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, ["--pull-budget", str(budget),
                                                                           "--no-hold-first-sight"], 10, at_hunt=True)
                                                 + ["--memory", db, "--seed", "1"]
                                                 + (["--never-aggro", "vampiric consort"] if "consort" in case else []))
        patched = simclock.install(clock)
        store = memory.Memory(db)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                loop = loop_hunt.HuntLoop(link, store, args)
                loop.team.setup(loop.state())
                for m in w.mobs.values():
                    if m.key == "nightstalker":
                        w._warm(m, True)                 # the pets are fighting it
                out[case] = loop.team.pull_check(loop.state())
        finally:
            store.close()
            simclock.uninstall(patched)
    check("fighting a nightstalker (1.8), a shade wolf (0.9) in sight at 11 tiles: hold (budget 2.5)",
          out["wolf in sight"] and "would wake" in out["wolf in sight"], str(out))
    check("...not when the wall hides it, nor with budget 3",
          out["wolf behind the wall"] is None and out["wolf in sight, budget 3"] is None, str(out))
    check("nothing fighting: a pack is pulled whole", out["idle wolf alone"] is None, str(out))
    check("an idle vampiric consort (--never-aggro) in sight at 12 tiles: hold, whatever fights",
          out["consort in sight"] and "vampiric consort" in out["consort in sight"], str(out))


def test_hold_first_sight():
    print("== --hold-first-sight: nothing fighting, an idle monster sees us: hold up to 10 s, then pass it over ==")
    import tempfile
    import loop_hunt
    import memory
    from sim import run as simrun
    w = world_with([{"key": "shade wolf", "at": [5266, 658, 0]}])
    wolf = next(m for m in w.mobs.values() if m.kind == "monster")
    wolf.calm_until = 1e9                               # it stays idle: the expiry is what's tested
    link, clock = link_for(w)
    db = os.path.join(tempfile.mkdtemp(prefix="sim_test_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, [], 10, at_hunt=True)
                                             + ["--memory", db, "--seed", "1"])
    patched = simclock.install(clock)
    store = memory.Memory(db)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            loop = loop_hunt.HuntLoop(link, store, args)
            loop.team.setup(loop.state())
            first = loop.team.hold_reason(loop.state())
            clock.advance(tamer.HOLD_S + 1)
            later = loop.team.hold_reason(loop.state())
    finally:
        store.close()
        simclock.uninstall(patched)
    check("a shade wolf in sight at 10 tiles: hold", first and "shade wolf" in first, str(first))
    check("...and after HOLD_S without it coming, no longer", later is None, str(later))


def test_rope():
    print("== --rope: an Adventurer's Rope jump along the route, the following pets along; 5 s cooldown ==")
    import tempfile
    import loop_hunt
    import memory
    from sim import run as simrun
    w = world_with([])
    link, clock = link_for(w)
    db = os.path.join(tempfile.mkdtemp(prefix="sim_test_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, [], 10, at_hunt=True)
                                             + ["--memory", db, "--seed", "1"])
    rope = next(it for it in w.items.values() if it.get("kind") == "rope")
    n0, start = rope["amount"], w.player.pos
    patched = simclock.install(clock)
    store = memory.Memory(db)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            loop = loop_hunt.HuntLoop(link, store, args)
            loop.team.setup(loop.state())
            first = loop.rope_jump((5266, 659))
            landed = w.player.pos
            again = loop.rope_jump((5266, 659))
    finally:
        store.close()
        simclock.uninstall(patched)
    d = sim_cheb(start, w.player.pos)
    check("a jump of 4-9 tiles toward the goal, one rope used", first and 4 <= d <= 9 and rope["amount"] == n0 - 1,
          f"{start} -> {landed}, ropes {n0} -> {rope['amount']}")
    check("...the following pets beside us", all(sim_cheb(p.pos, w.player.pos) <= 1 for p in w.pets()),
          str([p.pos for p in w.pets()]))
    check("...and no second jump within the cooldown", again is False)


def test_rest_hurt_pet():
    print("== out of vet supplies, the rest waits for a hurt pet instead of going back in ==")
    import tempfile
    import loop_hunt
    import memory
    from sim import run as simrun
    sc = load()
    sc["player"]["start"] = sc["recall"]["rows"][0]["at"]           # home
    sc["loadout"]["items"] = [it for it in sc["loadout"]["items"] if it.get("kind") != "vet"]
    w = World(sc, seed=1)
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    gb.hits = int(gb.hits_max * 0.15)                                # below --pet-leave-at 0.20
    link, clock = link_for(w)
    db = os.path.join(tempfile.mkdtemp(prefix="sim_test_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(sc, ["--rest-timeout", "300"], 10)
                                             + ["--memory", db, "--seed", "1"])
    patched = simclock.install(clock)
    store = memory.Memory(db)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            loop = loop_hunt.HuntLoop(link, store, args)
            loop.team.setup(loop.state())
            t0 = clock.elapsed
            rested = loop.rest()
            took = clock.elapsed - t0
    finally:
        store.close()
        simclock.uninstall(patched)
    check("no supplies, the gravebug at 15%: not rested until --rest-timeout (it regenerates meanwhile)",
          not rested and took >= 300 and gb.hits > gb.hits_max * 0.15, f"{rested} {took:.0f} s {gb.hits}/{gb.hits_max}")


def main():
    if pathfind.Walkers().get(0) is None:
        print("SKIP: no map files (install dir)")
        return 0
    for t in (test_clock, test_geometry, test_aggro, test_aggro_rules, test_bard_and_vet, test_specials, test_hunt,
              test_standoff, test_fire, test_pet_res, test_retreat_sight, test_spacing, test_stairs, test_backspawn,
              test_area_first, test_leave_called_off, test_instrument, test_heal_in_place,
              test_songs,
              test_pull_budget, test_hold_first_sight, test_rope,
              test_rest_hurt_pet):
        t()
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILED: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

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


def world_with(spawns, seed=1, start=FLOOR, others=False):
    sc = load()
    sc["spawns"] = [{**s, "respawn_s": [9999, 9999]} for s in spawns]
    sc["defaults"]["spawn_spread"] = sc["defaults"]["wander_per_s"] = 0   # the tests place each monster on its tile
    sc["player"]["start"] = [start[0], start[1], 0]
    if others:
        sc["others"] = {**sc["others"], "enabled": True, "arrive_per_h": 0.0,   # the test places its own (add_other)
                        "unseen": {}}                                         # and its monsters stay
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


def test_shove():
    print("== mobiles in the way: shoved through at full stamina (-10), refused below it; stamina's step cost and regen ==")
    # user 2026-10-22: "in Outlands I can run through mobs when I have full stamina, and they block me when I don't";
    # the captures (sim/shove_scan.py): "You shove something out of the way." and 25 -> 15 at each shove into a monster,
    # a point every 15 steps, a point back every 2.04 s, a Total Refresh to full
    bats = [{"key": "vampire bat", "at": [5266, 647, 0]}, {"key": "vampire bat", "at": [5266, 646, 0]}]
    w = world_with(bats)
    link, clock = link_for(w)
    pl = w.player
    for p in w.pets():
        p.mode = "stay"
    for m in w.mobs.values():
        if m.kind == "monster":
            m.next_step = 1e9                      # they stand where placed
    seq = iter(range(1, 255))

    def step(d):
        link.send(actions.walk(d, run=True, seq=next(seq)))
    step(0)                                        # facing north already: a step into the first bat at 25/25
    first = (pl.pos[:2], pl.stam, w.metrics["shoves"],
             any(e.get("cliloc") == 1019042 for e in link.events))
    step(0)                                        # the second bat at 15/25
    refused = (pl.pos[:2], pl.stam, w.metrics["denied_steps"])
    clock.advance(2.1)
    regen = pl.stam
    refresh = next(s for s, it in w.items.items() if it.get("kind") == "refresh_potion")
    left = w.items[refresh]["amount"]
    link.send(actions.dclick(refresh))
    drank = (pl.stam, w.items[refresh]["amount"])
    step(0)
    through = (pl.pos[:2], pl.stam)
    check("at 25/25 a step into a bat goes through: the cliloc, stamina 15",
          first == ((5266, 647), 15, 1, True), str(first))
    check("at 15/25 a step into the next bat is refused (0x21) and we stay", refused == ((5266, 647), 15, 1),
          str(refused))
    check("a point back after 2.04 s", regen == 16, str(regen))
    check("a Total Refresh: stamina to full, one potion fewer", drank == (25, left - 1), str(drank))
    check("...and the bat is shoved through again", through == ((5266, 646), 15), str(through))
    w2 = world_with([{"key": "vampire bat", "at": [5266, 647, 0]}])
    link2, clock2 = link_for(w2)
    for p in w2.pets():
        p.mode = "stay"
    for m in w2.mobs.values():
        if m.kind == "monster":
            m.next_step = 1e9
    s2 = iter(range(1, 255))
    for _ in range(15):                            # west and back, a turn before each step
        for d in ((6, 6) if w2.player.x == 5266 else (2, 2)):
            link2.send(actions.walk(d, run=True, seq=next(s2)))
    after15 = (w2.player.pos[:2], w2.player.stam)
    if w2.player.x != 5266:
        for d in (2, 2):
            link2.send(actions.walk(d, run=True, seq=next(s2)))
    for d in (0, 0):
        link2.send(actions.walk(d, run=True, seq=next(s2)))
    check("15 steps on foot cost a point (24/25), and at 24/25 the bat blocks",
          after15[1] == 24 and w2.player.pos[:2] == (5266, 648) and w2.metrics["shoves"] == 0,
          f"{after15} then {w2.player.pos} shoves {w2.metrics['shoves']}")
    gr = next(p for p in w2.pets() if (p.x, p.y) == (5267, 648))
    for d in (2, 2):                               # east, onto GraveRobber's tile, at 24/25
        link2.send(actions.walk(d, run=True, seq=next(s2)))
    check("our own pet doesn't block, below full stamina too (live 244 of 255), and costs nothing",
          w2.player.pos[:2] == gr.pos[:2] and w2.player.stam == 24 and w2.metrics["shoves"] == 0,
          f"{w2.player.pos} {gr.name} {gr.pos} stam {w2.player.stam}")


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
    print("== a monster goes for whichever of us it sees first, a guarding Tank behind us drawing it at "
          "tank_draw; our act pulls it only before the pets hit ==")
    results = {}
    for where, ys, draw in (("behind", 646, 0.0), ("behind, Tank draw", 646, 1.0), ("between", 650, 0.0)):
        w = world_with([{"key": "shade wolf", "at": [5266, 654, 0]}])
        m = next(m for m in w.mobs.values() if m.kind == "monster")
        m.spec = {**m.spec, "aggro_range": 7}                   # we at 6; the pets at 8 (behind) or 4 (between)
        w.sc["defaults"]["wake_s"] = None                       # who it picks, at once: no wake delay here
        w.sc["defaults"]["tank_draw"] = draw
        for p, x in zip(w.pets(), (5266, 5267)):
            p.x, p.y = x, ys
        link, clock = link_for(w)
        link.send(tamer.command("all guard me"))
        clock.advance(0.6)
        tgt = w.mobs.get(m.target)
        results[where] = tgt.key if tgt and tgt.kind == "pet" else tgt.kind if tgt else None
    check("pets behind us, out of its range: us; with the Tank draw: the gravebug (live first contact 61 %); "
          "the pets between: a pet",
          results["behind"] == "player" and results["behind, Tank draw"] == "gravebug"
          and results["between"] in ("gravebug", "phoenix"), str(results))
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
    called = landed = None
    for _ in range(20):
        clock.advance(0.1)
        if called is None and m.charge_at is not None:
            called = clock.elapsed
        if landed is None and any(o.hits < o.hits_max for o in w.team()):
            landed = clock.elapsed
    link.state()
    charged = [o for o in w.team() if cheb_(o, m) <= 1]
    check("the nightstalker calls its Charge, then 0.8-1.0 s later jumps 6 tiles onto the gravebug and hits each "
          "of us within 1 tile of it (live n=25)",
          any(e.get("text") == "*charges*" for e in link.events) and cheb_(gb, m) <= 1
          and called is not None and landed is not None and 0.7 <= landed - called <= 1.15
          and all(o.hits < o.hits_max for o in charged),
          f"called {called} landed {landed} at {m.pos}, hit {[(o.name, o.hits) for o in charged]}")
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
    gb.hits, gb.x, gb.y = gb.hits_max * 0.5, w.player.x, w.player.y + 3
    w.player.hits = w.player.hits_max
    clock.advance(1.0)
    mark = len(link.events)
    link.send(actions.dclick(vet))
    clock.advance(1.0)
    link.send(actions.walk(4, seq=1))                 # a turn south, then a step mid-channel: the gravebug 2 off then
    link.send(actions.walk(4, seq=2))
    until((tamer.TEXT_VET_FINISH, tamer.TEXT_VET_FAR))
    check("a step during the channel: the gravebug 2 tiles off at the finish healed (live 126 of 128 channels with "
          "steps finished)", w.player.pos[:2] == (FLOOR[0], FLOOR[1] + 1) and gb.hits > gb.hits_max * 0.5,
          f"at {w.player.pos[:2]}, {gb.hits}/{gb.hits_max}")
    for p in w.pets():
        p.mode = "stay"
        p.x, p.y = w.player.x + 4, w.player.y       # live: the pets 3-4 tiles off then (a whole pet in range finishes)
    gb.hits = gb.hits_max * 0.5
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
    # survival and leaving are the batch numbers' business (docs/SIM.md), not this plumbing check (it once required a
    # leave or a death within 8 min of seed 1: an incidental outcome of the draws)
    check("it recalled in, killed and looted", r["recalls"] >= 1 and r["kills"] >= 1 and r["gold_looted"] > 0,
          f"recalls {r['recalls']} deaths {r['deaths']} kills {r['kills']} gold {r['gold_looted']}")
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


def test_vet_tile_off_tank():
    print("== --vet-range 5, the gravebug hurt beside a reanimated daemon: vet it from out of the bone burst, not "
          "from beside it ==")
    import tempfile
    import loop_hunt
    import memory
    from sim import run as simrun
    # live hunt-20261021-091743-38b6 09:53:52-56: "to a hurt pet" walked 9 steps to (5241, 674), beside the gravebug and
    # the daemon at (5241, 675); its bone burst took Logan -41 a second later (user: "we seem to go stand directly on
    # our tank")
    out = {}
    for case, start in (("to_pet", (5247, 668)), ("stand_off", (5242, 673))):
        w = world_with([{"key": "reanimated daemon", "at": [5241, 675, 0]}], seed=7, start=start)
        m = next(m for m in w.mobs.values() if m.kind == "monster")
        burst = next(sp for sp in m.spec["specials"] if sp["kind"] == "burst")
        m.spec = {**m.spec, "specials": [], "melee": [1, 1], "step_s": 999.0}
        m.hits = m.hits_max = 10 ** 6
        gb = next(p for p in w.pets() if p.name == "GraveRobber")
        af = next(p for p in w.pets() if p.name == "AlaFlambe")
        gb.hits = int(gb.hits_max * 0.5)
        m.target, m.threat = gb.serial, {gb.serial: 1e9}
        w._warm(m, True)
        link, clock = link_for(w)
        link.send(tamer.command("all guard me"))
        db = os.path.join(tempfile.mkdtemp(prefix="sim_test_"), "harness.db")
        args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, [], 10, at_hunt=True)
                                                 + ["--memory", db, "--seed", "1", "--human", "off", "--vet-range", "5"])
        patched = simclock.install(clock)
        store = memory.Memory(db)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                loop = loop_hunt.HuntLoop(link, store, args)
                loop.team.setup(loop.state())                # the pets beside us, then on the test's tiles
                for p, xy in ((gb, (5240, 674)), (af, (5243, 676))):
                    p.x, p.y = xy
                    p.mode, p.target = "stay", None          # they hold their tiles: the test's geometry
                gb.target = m.serial
                clock.advance(0.5)
                if case == "to_pet":
                    walked = loop.team.to_pet(loop.state())
                else:
                    loop.team.standoff_t = -1e9
                    walked = loop.team.stand_off(loop.state())
        finally:
            store.close()
            simclock.uninstall(patched)
        out[case] = (walked, cheb_(w.player, gb), cheb_(w.player, m), w.player.pos[:2],
                     [l for l in buf.getvalue().splitlines() if "hurt pet:" in l or "heal the tank:" in l])
    r = burst["radius"]
    check(f"to a hurt pet / stepping in to heal the tank: within 5 of the gravebug, 3+ off it, out of the bone burst's "
          f"{r} tiles", all(o[0] and tamer.SPACING_R <= o[1] <= 5 and o[2] > r for o in out.values()), str(out))


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


def test_gate_dash():
    print("== attacked on a planned walk out within GATE_DASH_R of the gate: through it at once, no stand ==")
    import loop_hunt
    from agent_link import cheb
    # live 2026-10-17 19:04:08-18.7: 2 tiles from the gate, an apparition on Logan, the pets guarding: dead there.
    # Here something unseen takes a hit point every 0.5 s, so a stand never ends by itself
    gate = (5203, 703)
    w = world_with([], seed=7, start=(5209, 695))           # 8 tiles off: GATE_DASH_R
    loop, clock, store, patched = hurt_loop(w, 100)
    world_tick, last = clock.on_advance, [None]

    def bleed(now):
        world_tick(now)
        if last[0] is None or now - last[0] >= 0.5:
            last[0] = now
            if w.player.hits > 60:
                w.hurt(w.player, 1, None, kind="bleed")
    clock.on_advance = bleed
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            t0 = loop_hunt.time.monotonic()
            res = loop.leave_by_gate("info")
            took = loop_hunt.time.monotonic() - t0
    finally:
        store.close()
        simclock.uninstall(patched)
    check("through the gate within seconds (not a stand while the pets fight)",
          res.get("ok") and took < 15 and cheb(w.player.pos, gate) > 100,
          f"ok {res.get('ok')} {res.get('error')} in {took:.1f} s at {w.player.pos}")


def test_gate_after_vet():
    print("== hurt at the leave gate: the vet supplies, then the gate refused (500119): clicked again, through ==")
    import loop_hunt
    from agent_link import cheb
    # live 2026-10-19 10:19:32.30-35 (hunt-20261019-100618-80b2): beside the gate at 79/100, the vet supplies at
    # 32.55, the gate's double-click at 32.75 answered 500119 "You must wait to perform another action.", no menu:
    # "ABORTED: recall home failed (moongate: no 'Moongate Destinations' menu at (5204, 702))", Logan left standing
    gate = (5203, 703)
    w = world_with([], seed=7, start=(5209, 697))
    loop, clock, store, patched = hurt_loop(w, 79)            # a potion at the start: none ready at the gate
    world_tick, hit = clock.on_advance, [False]

    def at_gate(now):
        world_tick(now)
        if not hit[0] and cheb(w.player.pos, gate) <= 1:
            hit[0] = True
            w.hurt(w.player, 21, None, kind="bleed")       # 100 -> 79 there, as live
    clock.on_advance = at_gate
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            res = loop.leave_by_gate("info")
    finally:
        store.close()
        simclock.uninstall(patched)
    check("through the gate", res.get("ok") and cheb(w.player.pos, gate) > 100,
          f"ok {res.get('ok')} {res.get('error')} at {w.player.pos} vet uses {w.metrics['vet_uses']}")


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


def test_blink_stepoff():
    print("== an apparition phases beside the tank we stand next to: off it at once, before the landing ==")
    import time
    from agent_link import cheb
    # live 20261017_110506 11:10:32: an apparition on the gravebug phased with Logan 1 from the gravebug, -40 at the
    # landing (user: "when an apparition vanishes we know its going to pop out on the player/pet it was aggro'd on ...
    # right now we just wait there to get hit for like -47"). Here the stand-off has just moved (its 2 s repeat gap).
    w = world_with([{"key": "apparition", "at": [5205, 726, 0]}], seed=7, start=(5207, 726))
    m = next(m for m in w.mobs.values() if m.kind == "monster")
    m.spec = {**m.spec, "step_s": 999.0}
    loop, clock, store, patched = hurt_loop(w, 100)
    gb = next(p for p in w.pets() if p.name == "GraveRobber")         # at (5206, 726): between us and it
    m.target, m.threat = gb.serial, {gb.serial: 1e9}
    m.cd = {"blink": w.t + 0.05}
    loop.team.standoff_t = time.monotonic()
    hurts = []
    orig = w.hurt
    w.hurt = lambda victim, amount, by, kind="melee", song_pets=True: (
        hurts.append((victim.name, kind, round(cheb(victim.pos, gb.pos)))), orig(victim, amount, by, kind, song_pets))
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            for _ in range(40):
                clock.advance(0.1)
                loop.tick()
                if any(k == "burst" for _, k, _ in hurts):
                    break
    finally:
        store.close()
        simclock.uninstall(patched)
    bursts = [(n, d) for n, k, d in hurts if k == "burst"]
    check("it landed on the gravebug with its area hit, and Logan was 3+ off it by then, unhurt",
          bursts and all(n != "Logan Wolf" for n, _ in bursts) and cheb(w.player.pos, gb.pos) >= tamer.AREA_R["apparition"],
          f"bursts {bursts} Logan {cheb(w.player.pos, gb.pos)} from the gravebug")


def test_standoff_sight():
    print("== the stand-off never goes nearer an idle monster that would see us first: it stays ==")
    import aggro
    from agent_link import cheb
    # live 20261015_204514 21:02:02: the only clear tiles in the pets' reach were 3 from an idle reanimated knight;
    # it woke and hit Logan -46. Here every tile is in its first sight (stand_score patched), so only nearness differs.
    w = world_with([{"key": "nightstalker", "at": [5268, 649, 0]}, {"key": "reanimated knight", "at": [5268, 640, 0]}],
                   seed=7, start=(5268, 648))
    ns = next(m for m in w.mobs.values() if m.key == "nightstalker")
    kn = next(m for m in w.mobs.values() if m.key == "reanimated knight")
    loop, clock, store, patched = hurt_loop(w, 100)
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    ns.target, ns.threat = gb.serial, {gb.serial: 1e9}
    w._warm(ns, True)
    knight = {"kind": "reanimated knight", "pos": kn.pos, "weight": 1.2}
    score, first = aggro.View.stand_score, aggro.View.first_on_us
    aggro.View.stand_score = lambda self, t: 12.0
    aggro.View.first_on_us = lambda self, t, margin=1: [knight]
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            clock.advance(0.3)
            here = cheb(w.player.pos, kn.pos)
            walked = loop.team.stand_off(loop.state())
    finally:
        aggro.View.stand_score, aggro.View.first_on_us = score, first
        store.close()
        simclock.uninstall(patched)
    after = cheb(w.player.pos, kn.pos)
    check("no tile farther from it than here: no stand-off, or one no nearer it",
          (not walked and after == here) or (walked and after >= here), f"walked {walked}, {here} -> {after} tiles")


def test_area_peace():
    print("== two monsters on us within 3: Peacemaking on ourselves, the area pacified for 2 s ==")
    # user 2026-10-15: "in a jam you can peace the area around you by activating Peacemaking and targeting yourself";
    # live 20261015_171928 17:22:01 the user's own answered "You play successfully, briefly pacifying ..."
    w = world_with([{"key": "shade wolf", "at": [5268, 649, 0]}, {"key": "shade wolf", "at": [5269, 648, 0]}],
                   seed=7, start=(5268, 648))
    wolves = [m for m in w.mobs.values() if m.kind == "monster"]
    loop, clock, store, patched = hurt_loop(w, 100)
    for m in wolves:
        m.spec = {**m.spec, "difficulty": 20.0}            # a sure check, to test the action itself
        m.target, m.threat = w.player.serial, {w.player.serial: 1e9}
        w._warm(m, True)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            for _ in range(12):
                clock.advance(0.5)
                loop.tick()
                if w.metrics["bard"].get("area_peace_ok"):
                    break
            calmed = [m.calm_until > w.t for m in wolves]
    finally:
        store.close()
        simclock.uninstall(patched)
    check("the area peace went out and pacified both", w.metrics["bard"].get("area_peace_ok") and all(calmed),
          f"{w.metrics['bard']} calmed {calmed}")


def test_idle_pet():
    print("== a pet standing idle beside the other pet's fight gets 'All Kill' and joins it ==")
    # live 20261015_210709 21:19:51-20:43: AlaFlambe out of a transfix stood 45 s out of war mode while GraveRobber
    # fought the vampire countess alone
    w = world_with([{"key": "shade wolf", "at": [5268, 651, 0]}], seed=7, start=(5268, 648))
    m = next(m for m in w.mobs.values() if m.kind == "monster")
    m.hits = m.hits_max = 10 ** 6
    loop, clock, store, patched = hurt_loop(w, 100)
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    af = next(p for p in w.pets() if p.name == "AlaFlambe")
    m.target, m.threat = gb.serial, {gb.serial: 1e9}
    gb.mode, gb.kill, gb.target = "kill", m.serial, m.serial
    af.mode, af.target = "stay", None                 # out of the fight, as the transfix left it
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            mark = w.metrics["kill_commands"]
            for _ in range(40):
                clock.advance(0.5)
                if w.metrics["kill_commands"] == mark:
                    af.mode, af.target = "stay", None   # the sim's guard would join on its own; live it didn't
                    w._warm(af, False)
                loop.tick()
                if w.metrics["kill_commands"] > mark and af.target == m.serial:
                    break
            said = w.metrics["kill_commands"] - mark
    finally:
        store.close()
        simclock.uninstall(patched)
    check("'All Kill' at the gravebug's target, the phoenix on it", said >= 1 and af.target == m.serial,
          f"kill commands {said}, phoenix target {af.target}")


def test_pet_shot():
    print("== the phoenix blind to its target (the pillar row, the gravebug in its one gap): it moves to a shot ==")
    # the simulator 2026-10-20 (seed 20, 285-385 s): AlaFlambe at (5265, 651) on a bound soul at (5265, 649) stood
    # 100 s without an attack, stepping only straight at it; live the phoenix went 10+ s without one in 1.5 % of the
    # gravebug's fighting time (35 visits)
    w = world_with([{"key": "bound soul", "at": [5265, 649, 0]}], seed=7, start=(5265, 655))
    m = next(m for m in w.mobs.values() if m.kind == "monster")
    m.hits = m.hits_max = 10 ** 6
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    af = next(p for p in w.pets() if p.name == "AlaFlambe")
    (gb.x, gb.y), (af.x, af.y) = (5266, 650), (5265, 651)
    m.target, m.threat = gb.serial, {gb.serial: 1e9}
    for p in (gb, af):
        p.mode, p.target = "guard", m.serial
    link, clock = link_for(w)
    check("blind at the start", not w.can_see(af, m))
    clock.advance(10.0)
    check("the phoenix hit it within 10 s", af.serial in m.damage_by, f"phoenix at {af.pos[:2]}")


def test_countess_held_pet():
    print("== a vampire countess transfixes the gravebug: she fights the phoenix or us until it is free again ==")
    # live (41 visits, 2026-10-20): of the countesses' 47 hits on us while a copy of the gravebug was up, 2 on the
    # gravebug, 28 on the phoenix, 17 on Logan; with the gravebug free 107 of 134 on it
    w = world_with([{"key": "vampire countess", "at": [5266, 650, 0]}], seed=5, start=(5266, 653))
    m = next(m for m in w.mobs.values() if m.key == "vampire countess")
    m.hits = m.hits_max = 10 ** 6
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    for p in w.pets():
        p.mode = "stay"
    gb.x, gb.y = 5266, 651
    m.target, m.threat, m.warmode = gb.serial, {gb.serial: 1e6}, True
    sp = next(sp for sp in m.spec["specials"] if sp["kind"] == "transfix")
    w._transfix(gb, {**sp, "pet_damage": None}, m)
    m.spec = {**m.spec, "specials": [s for s in m.spec["specials"] if s["kind"] != "transfix"] + [
        {**sp, "every_s": 10 ** 6}]}           # no second transfix in the test
    m.cd["transfix"] = 10 ** 6
    copy = next(o for o in w.mobs.values() if o.spec.get("copy_of") == gb.serial)
    link, clock = link_for(w)
    clock.advance(2.0)
    during = m.target
    clock.advance(max(0.0, copy.expires - w.t) + 2.0)
    check("the gravebug held: she is on another of us", during not in (None, gb.serial), f"target {during}")
    check("the copy gone: back on the gravebug (its threat)", m.target == gb.serial, f"target {m.target}")


def test_countess_reguard():
    print("== the gravebug out of a countess' transfix with her on us: 'All Guard Me' at once; transfixed ourselves, "
          "the command waits the hold out ==")
    # live hunt-20261021-091743-38b6 09:48:44-09:49:53 (user 2026-10-21): six releases, the countess fight on
    # throughout, no "All Guard Me" from 09:48:53 until her kill
    w = world_with([{"key": "vampire countess", "at": [5267, 649, 0]}], seed=5, start=(5266, 648))
    m = next(m for m in w.mobs.values() if m.key == "vampire countess")
    m.hits = m.hits_max = 10 ** 6
    sp = next(sp for sp in m.spec["specials"] if sp["kind"] == "transfix")
    m.spec = {**m.spec, "specials": [s for s in m.spec["specials"] if s["kind"] != "transfix"]}  # only the test's own
    loop, clock, store, patched = hurt_loop(w, 100)
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    buf = io.StringIO()
    world_tick = clock.on_advance

    def topped_up(now):
        world_tick(now)
        w.player.hits = max(w.player.hits, 90)          # she stays on us, beside the pets: no retreat, no leave
    clock.on_advance = topped_up
    try:
        with contextlib.redirect_stdout(buf):
            w._transfix(gb, {**sp, "pet_damage": None}, m)
            copy = next(o for o in w.mobs.values() if o.spec.get("copy_of") == gb.serial)
            freed = copy.expires
            pl = w.player
            m.target, m.threat, m.warmode = pl.serial, {pl.serial: 1e9}, True    # she turns on us (user's report)
            mark, recent = None, False
            while w.t < freed + 3.0:
                if not recent and w.t >= freed - 1.5:
                    # a guard 1-1.5 s before the release, as live (09:48:53.69 after a kill, the gravebug free at 54.77):
                    # GUARD_REPEAT_S would hold the next one (a lost target's) back 6.5 s
                    loop.team.guard_t, recent = tamer.time.monotonic(), True
                clock.advance(0.5)
                loop.tick()
                if mark is None and w.t >= freed:
                    mark = (w.metrics["guard_commands"], w.t)
                if mark is not None and w.metrics["guard_commands"] > mark[0]:
                    break
            took = w.t - freed
            guarded = mark is not None and w.metrics["guard_commands"] > mark[0]
            mode = gb.mode
            w.transfix_player(11.1)                                              # now Logan himself
            clock.advance(0.2)
            loop.state()                                                         # the runner's read before its care
            g0, t0 = w.metrics["guard_commands"], w.t
            loop.team.guard_t = -1e9
            loop.team.guard()
            waited = w.t - t0
    finally:
        store.close()
        simclock.uninstall(patched)
    log = buf.getvalue()
    check("released with her on us: 'All Guard Me' within 2 s of the copy's vanish, the gravebug on guard",
          guarded and took <= 2.0 and mode == "guard" and "GraveRobber free of the transfix after" in log
          and "on us: 'All Guard Me'" in log,
          f"guarded {guarded} {took:.1f} s after, mode {mode}; "
          f"{[l for l in log.splitlines() if 'transfix' in l or 'Guard' in l]}")
    check("transfixed ourselves: the guard waits the hold out (not silenced) and lands",
          w.metrics["silenced"] == 0 and w.metrics["guard_commands"] == g0 + 1 and waited >= 10.0,
          f"silenced {w.metrics['silenced']}, guards {w.metrics['guard_commands'] - g0}, waited {waited:.1f} s")


def test_loot_lift_gap():
    print("== a corpse of several items: lifts 0.5 s+ apart, a 'must wait' refusal tried again; every item taken ==")
    import combat
    # live hunt-20261021-091743-38b6: 5 items left in corpses, each "You must wait another moment before you may pick
    # up an item from here." 0.04 s after a lift 0.41-0.49 s after the last taken (user: "it just moves on")
    from sim import world as simworld
    out = {}
    for server_gap in (simworld.LIFT_GAP_S, 0.75):      # as live; slower than combat.LIFT_GAP_S: a refusal, a retry
        w = world_with([{"key": "shade wolf", "at": [5266, 649, 0]}], seed=7)
        m = next(m for m in w.mobs.values() if m.kind == "monster")
        loop, clock, store, patched = hurt_loop(w, 100)
        buf, gap0 = io.StringIO(), simworld.LIFT_GAP_S
        simworld.LIFT_GAP_S = server_gap
        try:
            with contextlib.redirect_stdout(buf):
                w.die(m, None)
                corpse = next(s for s, it in w.items.items() if it.get("kind") == "corpse" and it["owner"] == m.serial)
                for amount in (111, 222, 333):
                    w.items[w._item_serial()] = {"graphic": combat.GOLD_GRAPHIC, "amount": amount,
                                                 "container": corpse, "kind": "gold"}
                total = sum(it["amount"] for it in w.items.values() if it.get("container") == corpse)
                g0 = w.metrics["gold_looted"]
                clock.advance(0.5)
                c = {"mob": m.serial, "name": "a shade wolf", "x": m.x, "y": m.y, "t": tamer.time.monotonic(),
                     "corpse": corpse}
                loop.corpses.append(c)
                loop.loot(c)
        finally:
            simworld.LIFT_GAP_S = gap0
            store.close()
            simclock.uninstall(patched)
        out[server_gap] = (sum(1 for it in w.items.values() if it.get("container") == corpse),
                           f"{w.metrics['gold_looted'] - g0}/{total}", w.metrics["lift_waits"],
                           [l for l in buf.getvalue().splitlines() if "lift" in l or "looted" in l])
    live, slow = out[simworld.LIFT_GAP_S], out[0.75]
    check("the server's 0.5 s: all 4 items taken, none refused; a slower server: refused, tried again, all taken",
          live[0] == 0 and live[2] == 0 and slow[0] == 0 and slow[2] > 0
          and any("trying again in" in l for l in slow[3]), str(out))


def test_loot_off_area_hitter():
    print("== a corpse beside a reanimated daemon still fighting the pets: no loot walk into its bone burst; looted "
          "from a clear tile, or once it is dead ==")
    import combat
    # live hunt-20261021-091743-38b6 09:54:10: "to the corpse: route 5 steps from (5247, 675)", "arrived at (5242, 676)",
    # a tile from the daemon at (5241, 675) fighting the pets, 8 s before its death (user 2026-10-21)
    out = {}
    for case, corpse_at, start in (("every tile near it in reach", (5242, 677), (5247, 675)),
                                   ("a clear tile within reach", (5245, 676), (5250, 672))):
        w = world_with([{"key": "reanimated daemon", "at": [5241, 675, 0]},
                        {"key": "shade wolf", "at": [corpse_at[0], corpse_at[1], 0]}], seed=7, start=start)
        d = next(m for m in w.mobs.values() if m.key == "reanimated daemon")
        wolf = next(m for m in w.mobs.values() if m.key == "shade wolf")
        d.spec = {**d.spec, "specials": [], "melee": [1, 1], "step_s": 999.0}
        d.hits = d.hits_max = 10 ** 6
        loop, clock, store, patched = hurt_loop(w, 100)
        gb = next(p for p in w.pets() if p.name == "GraveRobber")
        af = next(p for p in w.pets() if p.name == "AlaFlambe")
        world_tick, near = clock.on_advance, [99]

        def watch(now):
            world_tick(now)
            if d.alive:
                near[0] = min(near[0], sim_cheb(w.player.pos, d.pos))
        clock.on_advance = watch
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                for p, xy in ((gb, (5240, 674)), (af, (5236, 675))):
                    p.x, p.y = xy
                    p.mode, p.target = "stay", None
                d.target, d.threat = gb.serial, {gb.serial: 1e9}
                w._warm(d, True)
                w.die(wolf, None)
                corpse = next(s for s, it in w.items.items() if it.get("kind") == "corpse" and it["owner"] == wolf.serial)
                w.items[w._item_serial()] = {"graphic": combat.GOLD_GRAPHIC, "amount": 500, "container": corpse,
                                             "kind": "gold"}
                clock.advance(1.0)
                c = {"mob": wolf.serial, "name": "a shade wolf", "x": corpse_at[0], "y": corpse_at[1],
                     "t": tamer.time.monotonic(), "corpse": corpse}
                loop.corpses.append(c)
                loop.loot(c)
                first = (c in loop.corpses, sum(1 for it in w.items.values() if it.get("container") == corpse),
                         w.player.pos[:2])
                if c in loop.corpses:                    # waiting: the daemon dies, then the corpse is looted
                    w.die(d, gb)
                    clock.advance(1.5)                   # past LOOT_DEFER_S
                    nxt = loop.loot_next()
                    if nxt is not None:
                        loop.loot(nxt)
        finally:
            store.close()
            simclock.uninstall(patched)
        out[case] = {"first": first, "nearest to the live daemon": near[0], "left": sum(
            1 for it in w.items.values() if it.get("container") == corpse), "queued": c in loop.corpses,
            "log": [l for l in buf.getvalue().splitlines() if "corpse" in l or "looting" in l or "looted" in l]}
    a, b = out["every tile near it in reach"], out["a clear tile within reach"]
    r = tamer.AREA_R["reanimated daemon"]
    check(f"never within {r - 1} of the live daemon; every tile near the corpse in reach: kept queued, then looted after "
          f"its death; a clear tile in reach: looted from there at once",
          a["nearest to the live daemon"] >= r and a["first"][0] and a["left"] == 0 and not a["queued"]
          and b["nearest to the live daemon"] >= r and not b["first"][0] and b["first"][1] == 0, str(out))



def test_on_another_player():
    print("== a monster fighting another player, our pets in war mode at our side: 'All Kill' at it, no 15 s wait ==")
    # live 20261019_095934 10:10:57-10:11:28 (hunt-20261019-100618-80b2): a tormented soul in war mode on Lord Phaze at
    # (5236, 676), Logan at (5242, 683) with both pets beside him in war mode (guarding keeps the flag): the runner read
    # the pets as on it and waited "for it to come on" 15 s, twice. Seen from (5243, 674), 7 tiles east, "All Kill"
    # goes at once (4b06515); from Logan's own (5242, 683) a wall stands between (los.Sight), no tile within bard_step's
    # 2 steps sees it, and 4b06515 still waited: walk round until we see it (walk_to_see)
    for start, within, what in (((5243, 674), 6.0, "in sight 7 tiles off"),
                                ((5242, 683), 12.0, "behind the wall, Logan's live tile")):
        w = world_with([{"key": "tormented soul", "at": [5236, 677, 0]}], seed=7, start=start, others=True)
        m = next(m for m in w.mobs.values() if m.kind == "monster")
        m.hits = m.hits_max = 10 ** 6
        phaze = w.add_other(5236, 676, w.stand_z(5236, 676, w.player.z), name="Lord Phaze", stay=1e6, dps=1.0)
        m.target, m.threat, phaze.target = phaze.serial, {phaze.serial: 1e9}, m.serial
        w._warm(m, True)
        loop, clock, store, patched = hurt_loop(w, 100)
        took = None
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                mark, t0 = w.metrics["kill_commands"], w.t
                for _ in range(40):
                    clock.advance(0.5)
                    for p in w.pets():
                        if p.target is None:
                            w._warm(p, True)    # live: guarding, in war mode at Logan's side between fights
                    loop.tick()
                    if w.metrics["kill_commands"] > mark:
                        took = w.t - t0
                        break
        finally:
            store.close()
            simclock.uninstall(patched)
        check(f"{what}: 'All Kill' at the monster on Lord Phaze within {within:.0f} s",
              took is not None and took <= within and w.metrics["kill_on_others"] >= 1,
              f"after {took} s from {start} (now {w.player.pos[:2]}), kill commands at it {w.metrics['kill_on_others']}")


def daemon_on_elsa(start):
    """Live 2026-10-23 23:29 (hunt-20261023-220741-f118): a reanimated daemon at (5240, 700) fighting LiquidElsa, Logan
    at `start`, the pets beside him held out of it (live they never engaged it)."""
    w = world_with([{"key": "reanimated daemon", "at": [5240, 700, 0]}], seed=7, start=start, others=True)
    m = next(m for m in w.mobs.values() if m.kind == "monster")
    m.hits = m.hits_max = 10 ** 6
    elsa = w.add_other(5239, 701, w.stand_z(5239, 701, w.player.z), name="LiquidElsa", stay=1e6, dps=1.0)
    m.target, m.threat, elsa.target = elsa.serial, {elsa.serial: 1e9}, m.serial
    w._warm(m, True)
    return w, m


def test_kill_unseen_once():
    print("== 'All Kill' answered 'Target cannot be seen.': not again while we and it stand where it was refused ==")
    # live 23:29:47 and 23:29:59 from (5241, 693): the daemon on us behind a wall, refused twice 12 s apart (LOS_BLOCK_S
    # then KILL_REPEAT_S); the user: "keeps trying to 'All Kill' a reanimated demon that is not in line of sight"
    w, m = daemon_on_elsa((5241, 693))
    loop, clock, store, patched = hurt_loop(w, 100)
    loop.attackers = lambda st: [{"serial": f"0x{m.serial:08X}"}]
    sent = {}
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            for phase, xy in (("behind the wall", None), ("a tile that sees it", (5242, 694))):
                if xy is not None:
                    w.player.x, w.player.y = xy
                mark = w.metrics["kill_commands"]
                for _ in range(80):
                    clock.advance(0.5)
                    for p in w.pets():
                        p.mode, p.target = "stay", None
                    loop.team.fight(m.serial)
                sent[phase] = w.metrics["kill_commands"] - mark
    finally:
        store.close()
        simclock.uninstall(patched)
    check("one refused 'All Kill' in 40 s behind the wall; sent again once a step sees it",
          sent["behind the wall"] == 1 and sent["a tile that sees it"] >= 1, str(sent))


def test_patrol_off_passed_over_area_hitter():
    print("== the patrol stays out of a passed-over reanimated daemon's bone burst: no patrol/stand-off ping-pong ==")
    # live 23:29:34-23:30:19: dropped (pinned 15 s), the patrol went on to (5243, 698) 3 tiles from it, the bone burst
    # (-34, -41) and the stand-off back to (5241, 693): six rounds
    import crawl
    import time
    w, m = daemon_on_elsa((5241, 693))
    loop, clock, store, patched = hurt_loop(w, 100)
    inside = standoffs = 0
    try:
        with contextlib.redirect_stdout(io.StringIO()) as out:
            loop.crawl = c = crawl.Crawl(loop, crawl.load_prior(store.con, loop.spot, loop.args.crawl_depth_risk))
            c.setup(loop.state())
            c.unlocked = 99
            c.wp = min(range(len(c.wps)), key=lambda i: sim_cheb(c.wps[i], (5243, 698)))
            c.seek_for = m.serial
            loop.skip[m.serial] = time.monotonic() + 60
            for _ in range(80):
                clock.advance(0.5)
                for p in w.pets():
                    p.mode, p.target = "stay", None
                loop.tick()
                inside += sim_cheb(w.player.pos, m.pos) <= tamer.AREA_R["reanimated daemon"]
            standoffs = out.getvalue().count("stand-off to")
    finally:
        store.close()
        simclock.uninstall(patched)
    check("40 s: never in its reach, no stand-off", inside == 0 and standoffs == 0,
          f"half-seconds in reach {inside}, stand-offs {standoffs}")


def test_others_corpse_not_looted():
    print("== a monster another player did most of the damage to: its corpse is blue, the runner doesn't open it ==")
    # live loot rights (docs/HUNT_LOOP.md "Loot rights"): the most damage takes the corpse; 160 of 164 monsters seen
    # killed by another player on Maus 3 left a blue one (docs/NOTES.md "Other players at the Mausoleum")
    w = world_with([{"key": "shade wolf", "at": [5236, 677, 0]}], seed=7, start=(5243, 674), others=True)
    m = next(m for m in w.mobs.values() if m.kind == "monster")
    m.hits = m.hits_max = 6000
    other = w.add_other(5236, 676, w.stand_z(5236, 676, w.player.z), name="Lord Phaze", stay=1e6, dps=700.0)
    m.target, m.threat, other.target = other.serial, {other.serial: 1e9}, m.serial
    w._warm(m, True)
    loop, clock, store, patched = hurt_loop(w, 100)
    gold0 = w.metrics["gold_looted"]
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            after = 0
            for _ in range(120):
                clock.advance(0.5)
                loop.tick()
                after += m.serial not in w.mobs
                if after > 20 and not loop.corpses:      # 10 s after the death: the runner's loot pass is over
                    break
    finally:
        store.close()
        simclock.uninstall(patched)
    corpse = next((it for it in w.items.values() if it.get("kind") == "corpse" and it["owner"] == m.serial), None)
    hurt_by_pets = sum(v for s, v in m.damage_by.items() if s in {p.serial for p in w.pets()})
    check("the kill is the other player's: a blue corpse", corpse is not None and corpse["others"] and corpse["noto"] == 1,
          f"corpse {corpse and {k: corpse[k] for k in ('noto', 'others')}}, damage {m.damage_by}")
    check("the pets fought it (the runner's kill to credit), the corpse never opened, no gold from it",
          hurt_by_pets > 0 and w.metrics["blue_corpse_opens"] == 0 and w.metrics["gold_looted"] == gold0
          and loop.totals["lost_kills"] == 1,
          f"pets' damage {hurt_by_pets}, opens {w.metrics['blue_corpse_opens']}, gold {w.metrics['gold_looted'] - gold0}, "
          f"lost kills {loop.totals['lost_kills']}")


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
            out[healed] = (loop.visit is not None, cheb(w.player.pos, tuple(loop.args.leave_gate)),
                           sorted({p.mode for p in w.pets()}))
        finally:
            store.close()
            simclock.uninstall(patched)
    check("healed: still on the visit, short of the gate, the pets back on guard; hurt: through it",
          out[True][0] and out[True][1] > 1 and out[True][2] == ["guard"] and not out[False][0], str(out))


def test_leave_from_far():
    print("== a leave from more than GATE_THROUGH_R tiles off the gate walks to it and through ==")
    from agent_link import Abort, cheb
    # live 18:28:22-45 (hunt-20261017-181520-e0ca): the hits leave at (5261, 652), 58 tiles from the gate, read
    # as through it before a step; four recalls in the dungeon, then aborted at 9/100
    w = world_with([], seed=7, start=(5261, 652))
    loop, clock, store, patched = hurt_loop(w, 8)
    world_tick = clock.on_advance

    def stay_hurt(now):
        world_tick(now)
        w.player.hits = min(w.player.hits, 30)          # no heal calls the leave off (test_leave_called_off)
    clock.on_advance = stay_hurt
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                loop.tick()
            except Abort:
                pass                                   # gone: no --enter-recall in the test
    finally:
        store.close()
        simclock.uninstall(patched)
    check("out of the Mausoleum (through the gate), not standing where the leave fired",
          cheb(w.player.pos, (5261, 652)) > 100, str(w.player.pos))


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


def test_bard_sight():
    print("== a bard skill only at a target in sight (the live 'Target cannot be seen.' wall and its seen tile) ==")
    # live 2026-10-17: 44 of 418 bard uses answered "Target cannot be seen."; the 17 replayed (18:12, 18:31, 21:05
    # captures) were all out of our sight by los.Sight, 78 of the 79 answered ones in it
    out = {}
    for start in (WALL_FROM[:2], SEEN_FROM[:2]):
        # the live answers were at a corpse purger; a bound soul stands in (no bard skill at an idle ranged one now)
        w = world_with([{"key": "bound soul", "at": list(WALL_TO)}], seed=7, start=start)
        m = next(m for m in w.mobs.values() if m.kind == "monster")
        m.calm_until = 1e9
        loop, clock, store, patched = hurt_loop(w, 100)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                st = loop.state()
                loop.team.bard(st, m.serial, st["world"]["mobiles"][f"0x{m.serial:08X}"])
            out[start] = loop.team.counts.get("bard", 0)
        finally:
            store.close()
            simclock.uninstall(patched)
    check("behind the stone wall: no skill; from the tile the live server answered: the skill goes out",
          out[WALL_FROM[:2]] == 0 and out[SEEN_FROM[:2]] == 1, str(out))


def test_purger_back_off():
    print("== an idle corpse purger would see us before the pets: back behind them; no skill at it ==")
    # live 2026-10-17 23:14:08-12: "holding here: corpse purger in sight at 10 tiles: letting it come"; it woke at 9 with
    # Logan the nearest of us and its barrage diseased him (-23, -26), the gravebug beside it
    import aggro
    w = world_with([{"key": "corpse purger", "at": [5265, 655, 0]}], seed=7, start=(5256, 654))   # live 23:14:11
    m = next(m for m in w.mobs.values() if m.kind == "monster")
    m.calm_until = 1e9
    loop, clock, store, patched = hurt_loop(w, 100)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            st = loop.state()
            before = [x["kind"] for x in aggro.View(loop, st).first_on_us(loop.pos(st), margin=1)]
            stepped = loop.team.back_off(st)
            clock.advance(2.0)                          # the guarding pets trail us
            st = loop.state()
            after = [x["kind"] for x in aggro.View(loop, st).first_on_us(loop.pos(st), margin=1)]
            skill = loop.team.bard(st, m.serial, st["world"]["mobiles"][f"0x{m.serial:08X}"])
    finally:
        store.close()
        simclock.uninstall(patched)
    check("first in its sight, then stepped back: no longer before the pets; no bard skill at it",
          before == ["corpse purger"] and stepped and after == [] and not skill and loop.team.counts.get("bard", 0) == 0,
          f"before {before} stepped {stepped} after {after} at {w.player.pos} pets {[p.pos for p in w.pets()]}")


def test_purger_no_ping_pong():
    print("== an idle corpse purger backed off from: the crawl doesn't walk back into its sight ==")
    # live 2026-10-18 12:05:09-25: back off, then "toward a target" / "patrol to (5283, 651)" (seeking the purger) on
    # into its sight, "letting it come", back off, four times; then its barrage and a foul disease
    import aggro
    import crawl
    w = world_with([{"key": "corpse purger", "at": [5265, 655, 0]}], seed=7, start=(5256, 654))
    m = next(m for m in w.mobs.values() if m.kind == "monster")
    m.calm_until = 1e9                                  # never wakes by sight: only the pets' attack does
    loop, clock, store, patched = hurt_loop(w, 100)
    seen = ticks = 0
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            loop.crawl = crawl.Crawl(loop, crawl.load_prior(store.con, loop.spot, loop.args.crawl_depth_risk))
            loop.crawl.setup(loop.state())
            t0 = crawl.time.monotonic()
            while crawl.time.monotonic() - t0 < 90 and m.hits > 0 and m.target is None:
                loop.tick()
                st = loop.state()
                v = aggro.View(loop, st)
                ticks += 1
                seen += bool([x for x in v.first_on_us(v.pos, margin=1) if x["shy"]])
    finally:
        store.close()
        simclock.uninstall(patched)
    backs = loop.team.counts.get("back_offs", 0)
    check("at most 2 back-offs and in its first sight on at most 2 looks (it fights the pets or is left alone)",
          backs <= 2 and seen <= 2, f"back_offs {backs}, first sight on {seen} of {ticks} looks, purger on "
          f"{m.target}, hits {m.hits}, at {w.player.pos}")


def test_patrol_pull():
    print("== an idle corpse purger 12 tiles off: 'All Patrol' beside it, the pets wake it, it shoots them ==")
    # live demo 2026-10-18 17:55:57.5: Logan 14 tiles from an idle purger, the spot 3 from it; it woke on GraveRobber
    # and fought the pets, Logan 100/100 throughout
    w = world_with([{"key": "corpse purger", "at": [5265, 655, 0]}], seed=7, start=(5253, 653))
    m = next(m for m in w.mobs.values() if m.kind == "monster")
    loop, clock, store, patched = hurt_loop(w, 100, extra=("--patrol-pull",))
    targets = set()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            t0 = tamer.time.monotonic()
            while tamer.time.monotonic() - t0 < 90 and m.alive:
                loop.tick()
                if m.target is not None:
                    targets.add(m.target)
    finally:
        store.close()
        simclock.uninstall(patched)
    pets = {p.serial for p in w.pets()}
    hit = w.metrics.get("player_damage_by", {}).get("corpse purger", 0) + w.metrics.get("player_damage_by", {}).get(
        "disease", 0)
    check("one patrol, the purger killed on the pets alone (its target never Logan; a shot of its may stray to him in "
          "reach, world.SHOT_AT_PLAYER_P), 'All Guard Me' after",
          loop.team.counts.get("patrols", 0) == 1 and w.metrics["patrol_commands"] == 1 and not m.alive
          and targets and targets <= pets and w.metrics["guard_commands"] >= 2,
          f"patrols {loop.team.counts.get('patrols', 0)} cmds {w.metrics['patrol_commands']} purger alive {m.alive} "
          f"targets {targets} pets {pets} on Logan {hit} guards {w.metrics['guard_commands']}")


def test_vet_channel_not_ours():
    print("== a vet channel begun by his own Razor loop: the runner's try neither goes out nor waits for a reply ==")
    # live 2026-10-18 21:14:29.1 his loop began a channel mid-flight; the runner's tries at 30.8 and 33.8 got "You must
    # wait a few moments before you may use this item again." and each stood out the 1.5 s reply wait
    import healing
    w = world_with([], seed=7, start=(5253, 653))
    loop, clock, store, patched = hurt_loop(w, 60)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            st = loop.state()
            serial, _ = healing.in_pack(st["world"], loop.me(st), tamer.VET_SUPPLIES_GRAPHIC)[0]
            loop.link.send(actions.dclick(serial))           # the client's own use
            clock.advance(0.3)
            uses = w.metrics["vet_uses"]
            t0 = tamer.time.monotonic()
            went = loop.team.vet(loop.state(), us_below=1.0)
            took = tamer.time.monotonic() - t0
    finally:
        store.close()
        simclock.uninstall(patched)
    check("no try (the channel known busy), no wait", not went and took < 0.3 and w.metrics["vet_uses"] == uses,
          f"went {went} took {took:.2f} s uses {w.metrics['vet_uses']} (was {uses})")


def test_restart_inside():
    print("== started on Maus 3 far from --spot with the walk-in arguments: hunts from there, no recall ==")
    # live 2026-10-18 21:29:58 (hunt-20261018-212957-c791): from (5209, 689), 48 tiles from --spot 5199 737,
    # "recall to Maus Near 2 failed: unusable no recall from (5209, 689): inside the Mausoleum"
    import tempfile
    import loop_hunt
    import memory
    from sim import run as simrun
    w = world_with([], seed=7, start=(5209, 689))
    link, clock = link_for(w)
    db = os.path.join(tempfile.mkdtemp(prefix="sim_test_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, ["--spot", "5199", "737"], 10, at_hunt=False)
                                             + ["--memory", db, "--seed", "1", "--human", "off"])
    patched = simclock.install(clock)
    store = memory.Memory(db)
    err, hunted = None, []
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            loop = loop_hunt.HuntLoop(link, store, args)
            loop.hunt = lambda: hunted.append(loop.pos(loop.state())[:2])
            try:
                loop.run()
            except loop_hunt.Abort as e:
                err = str(e)
    finally:
        store.close()
        simclock.uninstall(patched)
    check("no abort, no recall, the hunt begins where it stands with the crawl's floor",
          err is None and hunted and w.metrics["recalls"] == 0 and loop.crawl is not None,
          f"err {err!r} hunted {hunted} recalls {w.metrics['recalls']}")


def test_walk_in_through_neighbour():
    print("== the walk-in put through a teleporter by another step: on, not 'no route' ==")
    # live 2026-10-18 21:05:41-43 (hunt-20261018-210443-1cbe): attacked 3 tiles from the last teleporter (5522, 637),
    # the step off the tank went onto (5522, 636) and out to Maus 3 (5199, 736); the walk on aborted "no route"
    import tempfile
    import loop_hunt
    import memory
    from sim import run as simrun
    w = world_with([], seed=7, start=(5199, 736))
    link, clock = link_for(w)
    db = os.path.join(tempfile.mkdtemp(prefix="sim_test_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, [], 10, at_hunt=False)
                                             + ["--memory", db, "--seed", "1", "--human", "off"])
    patched = simclock.install(clock)
    store = memory.Memory(db)
    store.teleporter_record(0, 5522, 637, 0, 5199, 737, 17)       # learned on earlier walks in
    err = None
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            loop = loop_hunt.HuntLoop(link, store, args)
            loop.team.setup(loop.state())
            try:
                loop.walk_in([tuple(t) for t in args.enter_via][-1:])
            except loop_hunt.Abort as e:
                err = str(e)
    finally:
        store.close()
        simclock.uninstall(patched)
    check("the last teleporter counted as passed: no abort, still on Maus 3", err is None
          and sim_cheb(w.player.pos, (5199, 737)) <= 3, f"err {err!r} at {w.player.pos}")



def test_unseen_target_passed_over():
    print("== an idle target within APPROACH_R that can't see us (a wall between): passed over, not waited for ==")
    import tempfile
    import loop_hunt
    import memory
    from sim import run as simrun
    # live 20261017_181210 18:17:02-17: Logan stopped at (5182, 712) 6 tiles from an idle shade wolf at
    # (5186, 706) that couldn't see him (sight blocked at (5183, 711)), "waiting for a shade wolf to come on"
    w = world_with([{"key": "shade wolf", "at": [5186, 706, 0]}], start=(5182, 712))
    wolf = next(m for m in w.mobs.values() if m.kind == "monster")
    wolf.calm_until = 1e9                               # stays idle
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
            start = w.player.pos
            t0 = loop_hunt.time.monotonic()
            loop.team.fight(wolf.serial)
            skipped = loop.skip.get(wolf.serial, 0) > loop_hunt.time.monotonic()
            took = loop_hunt.time.monotonic() - t0
            picked = loop.pick_target(loop.state())
    finally:
        store.close()
        simclock.uninstall(patched)
    check("passed over at once (skipped, not picked again), Logan where he was",
          skipped and took < 1.0 and picked != wolf.serial and w.player.pos == start,
          f"skipped {skipped} in {took:.1f} s, picked {picked}, at {w.player.pos}")


def test_ledge_target_not_picked():
    print("== a monster on a ledge above, a long way round by the stairs: not a target in reach ==")
    import tempfile
    import loop_hunt
    import memory
    from sim import run as simrun
    # live 2026-10-17 21:10-21:11: a vampire at (5200, 648, 45), Logan at (5200, 647, 25), 11 steps by the stairs;
    # picked, passed over as out of sight, picked again 60 s later, the waypoint held meanwhile
    w = world_with([{"key": "vampire bat", "at": [5200, 648, 45]}], start=(5200, 647))
    w.player.z = 25
    bat = next(m for m in w.mobs.values() if m.kind == "monster")
    bat.calm_until = 1e9                                # stays idle up there
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
            st = loop.state()
            picked = loop.pick_target(st)
            mob = st["world"]["mobiles"].get(f"0x{bat.serial:08X}")
    finally:
        store.close()
        simclock.uninstall(patched)
    check("the bat on the ledge (in view, 1 tile off on the map) is not picked", mob is not None and picked is None,
          f"in view {mob is not None}, picked {picked}, us {w.player.pos}")


def test_crawl_seeks_in_view():
    print("== the crawl patrols toward a monster in view beyond the pull range, not away from it ==")
    import tempfile
    import crawl
    import loop_hunt
    import memory
    from sim import run as simrun
    # live 22:08-22:40: 102 of 159 patrol starts had a monster we'd fight within 18 tiles; 68 walked away from all
    out = []
    for seed in (1, 2, 3):
        w = world_with([{"key": "shade wolf", "at": [5251, 651, 0]}])     # 15 tiles west of FLOOR (old: walked to 19)
        wolf = next(m for m in w.mobs.values() if m.kind == "monster")
        wolf.calm_until = 1e9
        link, clock = link_for(w)
        db = os.path.join(tempfile.mkdtemp(prefix="sim_test_"), "harness.db")
        args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, [], 10, at_hunt=True)
                                                 + ["--memory", db, "--seed", str(seed)])
        patched = simclock.install(clock)
        store = memory.Memory(db)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                loop = loop_hunt.HuntLoop(link, store, args)
                loop.team.setup(loop.state())
                st = loop.state()
                loop.spot = loop.fight_spot = tuple(loop.pos(st)[:2])
                loop.crawl = crawl.Crawl(loop, crawl.load_prior(store.con, loop.spot, loop.args.crawl_depth_risk))
                loop.crawl.setup(loop.state())
                c = loop.crawl
                # its area patrolled a moment ago, as live: the waypoint score (staleness) sends the patrol elsewhere
                c.visited[c.cell_of[c.floor.nearest(wolf.pos[:2])]] = crawl.time.monotonic()
                before = sim_cheb(w.player.pos, wolf.pos)
                loop.crawl.idle(loop.state())
                out.append((before, sim_cheb(w.player.pos, wolf.pos)))
        finally:
            store.close()
            simclock.uninstall(patched)
    check("each of 3 seeds: the first patrol ends nearer the wolf, within the pull range",
          all(a > b and b <= 8 for a, b in out), str(out))


def test_never_aggro_walk_out():
    print("== a --never-aggro monster in sight: an advance stops on a step nearer it, not on one out of it ==")
    import tempfile
    import loop_hunt
    import memory
    from sim import run as simrun
    # live 2026-10-17 21:12-21:16: 3225 "patrol to ...: stopped (a elder vampire would see us)" lines, every patrol
    # stopped before its first step while Logan stood in the elder vampire's sight
    w = world_with([{"key": "elder vampire", "at": [5266, 660, 0]}])
    ev = next(m for m in w.mobs.values() if m.kind == "monster")
    ev.calm_until = 1e9                                 # stays idle
    link, clock = link_for(w)
    db = os.path.join(tempfile.mkdtemp(prefix="sim_test_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, [], 10, at_hunt=True)
                                             + ["--memory", db, "--seed", "1"])
    patched = simclock.install(clock)
    store = memory.Memory(db)
    out = []
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            loop = loop_hunt.HuntLoop(link, store, args)
            loop.team.setup(loop.state())
            for y in (648, 648, 647, 648):              # stand, stand again (a new walk's first look), away, nearer
                w.player.y = y
                clock.advance(0.3)
                out.append(loop.team.pull_check(loop.state()))
    finally:
        store.close()
        simclock.uninstall(patched)
    first, again, away, nearer = out
    check("first sight stops; the same spot again and a step away go on; a step nearer stops",
          first and "would see us" in first and again is None and away is None and nearer and "would see us" in nearer,
          str(out))


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


def test_ledge_attacker():
    print("== on the walk in, a monster on a ledge 25 above hitting a pet: no target, the walk goes on ==")
    # live 2026-10-19 19:34:43-35:50 (hunt-20261019-192245-e3d7): an insane reanimator at (5507, 660, 30) over
    # GraveRobber at (5507, 661, 5); "target an insane reanimator ... dropping ... skipping it for 60 s" four times,
    # the walk-in fight standing its whole 90 s (user: "we're just standing around rather than continuing")
    import loop_hunt
    w = world_with([{"key": "insane reanimator", "at": [5507, 660, 30]}], seed=7, start=(5510, 664))
    m = next(m for m in w.mobs.values() if m.kind == "monster")
    loop, clock, store, patched = hurt_loop(w, 100)
    for p in w.pets():
        p.x, p.y, p.z = 5507, 661, w.stand_z(5507, 661, 5)
    gr = next(p for p in w.pets() if p.name == "GraveRobber")
    m.target, m.threat = gr.serial, {gr.serial: 50}                # on GraveRobber, as live
    w._warm(m, True)
    world_tick, last = clock.on_advance, [None]

    def casts(now):
        world_tick(now)
        if m.alive and (last[0] is None or now - last[0] >= 3.5):    # live: -3 or -4 % on GraveRobber every 3.5-4 s
            last[0] = now
            w.hurt(gr, 3, m, kind="spell")
    clock.on_advance = casts
    picked = []
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            real = loop.pick_target
            loop.pick_target = lambda st, pull=True: picked.append(real(st, pull)) or picked[-1]
            loop.walk_hits, loop.walk_hurt_t = None, -1e9                # as walk_in sets them
            for _ in range(10):                                         # its first hits on GraveRobber seen
                clock.advance(0.5)
                loop.state()
            pet_on = m.serial in loop.team.pet_attackers(loop.state())
            t0 = loop_hunt.time.monotonic()
            loop.walk_in_fight("to the teleporter at 5522,637")
            took = loop_hunt.time.monotonic() - t0
    finally:
        store.close()
        simclock.uninstall(patched)
    check("on GraveRobber from the ledge (as live), yet never picked; the walk-in fight over in seconds, not 90",
          pet_on and m.serial not in picked and took < 20,
          f"z {m.z} vs pets {gr.z}; on a pet {pet_on}; picked {set(picked)}; took {took:.1f} s")


def test_caster_at_12():
    print("== a caster in war mode 12 tiles off with sight of us, mid-vet-channel, a fight on: out of its sight ==")
    # live 2026-10-19 19:54:39-57 (hunt-20261019-192245-e3d7): a blightmare at (5243, 684) cast at Logan standing at
    # (5243, 672) for 18 s (-21..-28, four "spell blights you with a foul disease!"), 100 -> 4, dead at 19:55:01;
    # CASTER_R was 10, and one caster's sight never paid for a step (REPOSITION_GAIN)
    import aggro
    import loop_hunt
    w = world_with([{"key": "shadowguard", "at": [5243, 684, 0]}, {"key": "tormented soul", "at": [5241, 675, 0]}],
                   seed=7, start=(5243, 672))
    caster, soul = (next(m for m in w.mobs.values() if m.key == k) for k in ("shadowguard", "tormented soul"))
    loop, clock, store, patched = hurt_loop(w, 60)
    for p, xy in zip(w.pets(), ((5242, 675), (5244, 674))):
        p.x, p.y = xy
    caster.target, caster.threat = w.player.serial, {w.player.serial: 50}
    soul.target, soul.threat = w.pets()[0].serial, {w.pets()[0].serial: 50}
    w._warm(caster, True)
    caster.next_step = 1e9                     # it stood and cast, live (19:54:40-57 at (5243, 684)/(5243, 685))
    w._warm(soul, True)

    def seen(st):
        v = aggro.View(loop, st)
        return sum(1 for x in v.mobs if x["war"] and x["caster"] and v.sees(x, v.pos, 12))
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            clock.advance(0.5)
            st = loop.state()
            loop.team.vet_busy_until = loop_hunt.time.monotonic() + 5.0    # a channel running, as live
            before = seen(st)
            moved = loop.team.reposition(st)
            after = seen(loop.state())
    finally:
        store.close()
        simclock.uninstall(patched)
    check("in its sight at 12, then a step out of it (mid-channel)", before == 1 and moved and after == 0,
          f"before {before} moved {moved} after {after} at {w.player.pos}")


def test_blightmare_reach():
    print("== the live 18:20:49 tiles: a blightmare awake 12 off casting at us mid-channel: a step out of its reach ==")
    # live 2026-10-21 18:20:49-18:21:05 (hunt-20261021-175514-caa5): Logan at (5242, 673), the blightmare at (5242, 685)
    # cast at him for 16 s (-27 -24 -26 -27 -23, two "spell blights you with a foul disease!" and their -21 ticks) while
    # the pets at (5246, 675) killed a tormented soul at (5245, 674). The reposition refused (5242, 672), 13 off: out of
    # its reach, but still in its sight there, which stand_score counts (to 18) and the no-more-idle-eyes test took as an
    # eye more
    import aggro
    import loop_hunt
    w = world_with([{"key": "blightmare", "at": [5242, 685, 0]}, {"key": "tormented soul", "at": [5245, 674, 0]}],
                   seed=7, start=(5242, 673))
    bm, soul = (next(m for m in w.mobs.values() if m.key == k) for k in ("blightmare", "tormented soul"))
    loop, clock, store, patched = hurt_loop(w, 60)
    for p in w.pets():
        p.x, p.y = 5246, 675
    bm.target, bm.threat = w.player.serial, {w.player.serial: 50}
    soul.target, soul.threat = w.pets()[0].serial, {w.pets()[0].serial: 50}
    w._warm(bm, True)
    bm.next_step = 1e9                         # it stood at (5242, 685) the whole fight, live
    w._warm(soul, True)
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            clock.advance(0.5)
            st = loop.state()
            loop.team.vet_busy_until = loop_hunt.time.monotonic() + 5.0    # a channel on us (live 53.1: us at 61/100)
            before = tamer.Team.casting_at(aggro.View(loop, st), loop.pos(st))
            moved = loop.team.reposition(st)
            st = loop.state()
            after = tamer.Team.casting_at(aggro.View(loop, st), loop.pos(st))
    finally:
        store.close()
        simclock.uninstall(patched)
    said = [line for line in out.getvalue().splitlines() if "can cast at us" in line]
    check("in its reach at 12, then a step out of it, logged", before == 1 and moved and after == 0 and said,
          f"before {before} moved {moved} after {after} at {w.player.pos} ({sim_cheb(w.player.pos, bm.pos)} off) {said}")


def test_cover():
    print("== an awake corpse purger 8 off shooting us, the pets 3 off: care walks out of its shot first ==")
    # simulator 2026-10-21 (seed 1003, the goal batch): in the north-east room an awake purger 8 off shot Logan 100 -> 13
    # in 13 s while care stepped off the tank and vetted, the reposition finding no tile within 4 steps that kept the
    # pets in vet range and woke no more eyes; dead on the walk out (user 2026-10-21: "stay out of their aggro range ...
    # or keep the pets between us and the mob"). Here the room's second purger spawn, Logan 8 off in its sight
    import aggro
    w = world_with([{"key": "corpse purger", "at": [5274, 652, 0]}], seed=7, start=(5266, 651))
    pg = next(m for m in w.mobs.values() if m.key == "corpse purger")
    loop, clock, store, patched = hurt_loop(w, 90)
    for p, xy in zip(w.pets(), ((5263, 651), (5266, 654))):
        p.x, p.y = xy
    pg.target, pg.threat = w.player.serial, {w.player.serial: 50}
    w._warm(pg, True)
    pg.next_step = 1e9                         # it shoots from where it stands
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            clock.advance(0.5)
            st = loop.state()
            v = aggro.View(loop, st)
            before = tamer.Team.shot_at(v, tamer.Team.shy_awake(v), loop.pos(st))
            moved = loop.team.care(st)
            st = loop.state()
            v = aggro.View(loop, st)
            after = tamer.Team.shot_at(v, tamer.Team.shy_awake(v), loop.pos(st))
    finally:
        store.close()
        simclock.uninstall(patched)
    said = [line for line in out.getvalue().splitlines() if "cover:" in line]
    check("in its shot, then out of it, logged", before == 1 and moved and after == 0 and said,
          f"before {before} moved {moved} after {after} at {w.player.pos} ({sim_cheb(w.player.pos, pg.pos)} off) {said}")


def test_to_pet_not_into_shot():
    print("== a pet fighting an awake corpse purger, hurt beyond vet range: no walk into its shot to vet it ==")
    # live 2026-10-22 19:39:36-53 (hunt-20261022-191304-536b): GraveRobber on a patrolled purger at (5228, 737); "to a
    # hurt pet: (5232, 733)" four times, each into its shot, and cover walked Logan out to (5229, 729) each time
    import aggro
    w = world_with([{"key": "corpse purger", "at": [5228, 737, 0]}], seed=7, start=(5229, 729))
    pg = next(m for m in w.mobs.values() if m.key == "corpse purger")
    loop, clock, store, patched = hurt_loop(w, 100)
    gr = next(p for p in w.pets() if p.name == "GraveRobber")
    for p, xy in zip(w.pets(), ((5230, 736), (5226, 729))):
        p.x, p.y = xy
    gr.hits = int(gr.hits_max * 0.7)
    pg.target, pg.threat = gr.serial, {gr.serial: 50}
    w._warm(pg, True)
    pg.next_step = 1e9                         # it shoots from where it stands
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            clock.advance(2.5)                 # its first shot landed (cast_s up to 2.16 s): GR's bar sent hurt
            st = loop.state()
            v = aggro.View(loop, st)
            before = tamer.Team.shot_at(v, tamer.Team.shy_awake(v), loop.pos(st))
            walked = loop.team.to_pet(st)
            st = loop.state()
            v = aggro.View(loop, st)
            after = tamer.Team.shot_at(v, tamer.Team.shy_awake(v), loop.pos(st))
    finally:
        store.close()
        simclock.uninstall(patched)
    check("out of its shot before and after: no walk into it, the reason logged",
          before == 0 and after == 0 and not walked and "staying out of it" in out.getvalue(),
          f"before {before} walked {walked} after {after} at {w.player.pos}")


def test_blightmare_first_sight():
    print("== the live 18:20:44 scene: the runner never takes an idle blightmare's first sight (it doesn't come on) ==")
    # live 2026-10-21 18:20:44-48: "crawl: blightmare ... in view at 13 tiles: patrolling toward it", an approach and a
    # patrol step into its sight at 10 with the pets behind, and it woke on Logan (war mode 18:20:48.5); it never
    # stepped, cast at him from 10-12 and he died at 18:21:15. Simulator (sim/fight1820.py), seeds 1-48: 22 deaths before
    import crawl
    from agent_link import Abort
    out = []
    for seed in (3, 5, 7):
        w = world_with([{"key": "blightmare", "at": [5242, 685, 0]}, {"key": "tormented soul", "at": [5248, 674, 0]},
                        {"key": "tormented soul", "at": [5251, 681, 0]}, {"key": "tormented soul", "at": [5239, 688, 0]}],
                       seed=seed, start=(5244, 672))
        bm = next(m for m in w.mobs.values() if m.key == "blightmare")
        loop, clock, store, patched = hurt_loop(w, 100)
        on_us = [0.0]
        world_tick = clock.on_advance

        def watch(now):
            world_tick(now)
            if bm.alive and bm.target == w.player.serial:
                on_us[0] += 0.1
        clock.on_advance = watch
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                loop.crawl = crawl.Crawl(loop, crawl.load_prior(store.con, loop.spot, loop.args.crawl_depth_risk))
                loop.crawl.setup(loop.state())
                t0 = crawl.time.monotonic()
                while crawl.time.monotonic() - t0 < 60 and w.player.alive and not w.player.ghost:
                    loop.tick()
        except Abort:
            pass                                        # a leave's recall (dead before it, live and here before the fix)
        finally:
            store.close()
            simclock.uninstall(patched)
        out.append((seed, round(on_us[0]), w.metrics.get("player_damage_by", {}).get("blightmare", 0),
                    w.metrics["deaths"]))
    check("3 seeds: the blightmare never on Logan, none of its spells on him",
          all(s == 0 and d == 0 and not dead for _, s, d, dead in out),
          f"(seed, seconds it was on him, its damage, deaths) {out}")


def test_blightmare_awake_patrol():
    print("== the live 18:21:09 tiles: a blightmare awake on us 12 off, the patrol toward it: none into its reach ==")
    # live 2026-10-21 18:21:09-12: after the loot at (5244, 673), 61/100 and two infections, "patrol to (5243, 682)"
    # (the waypoint its seek chose at 18:20:44) went on toward it, 12 -> 10 tiles; "stopped (under attack)", "waiting
    # for a blightmare to come on", -21 -28 -21 in 0.3 s: 6/100, dead on the way out
    import crawl
    w = world_with([{"key": "blightmare", "at": [5242, 685, 0]}], seed=7, start=(5244, 673))
    bm = next(m for m in w.mobs.values() if m.key == "blightmare")
    loop, clock, store, patched = hurt_loop(w, 61)
    for p in w.pets():
        p.x, p.y = 5243, 672
    bm.target, bm.threat = w.player.serial, {w.player.serial: 50}
    w._warm(bm, True)
    bm.next_step = 1e9
    near = [sim_cheb(w.player.pos, bm.pos)]
    world_tick = clock.on_advance

    def watch(now):
        world_tick(now)
        near[0] = min(near[0], sim_cheb(w.player.pos, bm.pos))
    clock.on_advance = watch
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            c = loop.crawl = crawl.Crawl(loop, crawl.load_prior(store.con, loop.spot, loop.args.crawl_depth_risk))
            c.setup(loop.state())
            c.wp = min(range(len(c.wps)), key=lambda i: sim_cheb(c.wps[i], (5243, 682)))
            c.seek_for, c.leg_t = bm.serial, crawl.time.monotonic()
            wp = c.wps[c.wp]
            st = loop.state()
            c.account(st, 0)
            c.idle(st)
    finally:
        store.close()
        simclock.uninstall(patched)
    said = [line for line in out.getvalue().splitlines() if "awake blightmare" in line or "avoid now" in line]
    now = c.wps[c.wp] if c.wp is not None else None
    check("the waypoint by it dropped, never nearer it than 12", near[0] >= 12 and now != wp,
          f"nearest {near[0]}, waypoint {wp} -> {now}; {said}")


def test_guard_after_dropped_kill():
    print("== 'All Kill' at a target later dropped: 'All Guard Me' again (the kill order ended the guarding) ==")
    # live 2026-10-23 (hunt-20261023-220741-f118): 'All Kill' at a reanimated daemon 23:29:07-32, dropped 23:29:34, no
    # 'All Guard Me' after; a blightmare woke on Logan at 23:30:22.5 and cast at him with both pets beside him out of
    # war mode until 2.4 s after the next 'All Kill' (30.6); dead at 36.96 (sim/fight2330.py: 3 deaths in 96 before)
    import loop_hunt
    w = world_with([{"key": "tormented soul", "at": [5272, 652, 0]}], seed=7, start=(5266, 651))
    soul = next(m for m in w.mobs.values() if m.key == "tormented soul")
    loop, clock, store, patched = hurt_loop(w, 100)
    team = loop.team
    team.guard_needed, team.guard_t = False, loop_hunt.time.monotonic() - 90.0   # guarding, said long ago
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            g0 = w.metrics["guard_commands"]
            team.all_kill(soul.serial)
            for _ in range(4):                 # the kill on: no 'All Guard Me' calling them off it
                clock.advance(0.5)
                team.care(loop.state())
            during = w.metrics["guard_commands"] - g0
            loop.skip[soul.serial] = loop_hunt.time.monotonic() + 60.0   # dropped (fight's PIN_S rule)
            for _ in range(6):
                clock.advance(0.5)
                team.care(loop.state())
                if w.metrics["guard_commands"] > g0:
                    break
            after = w.metrics["guard_commands"] - g0
    finally:
        store.close()
        simclock.uninstall(patched)
    modes = sorted({p.mode for p in w.pets()})
    check("none while it is the target, then 'All Guard Me' once dropped, the pets guarding",
          during == 0 and after == 1 and modes == ["guard"],
          f"during {during} after {after} modes {modes} {[ln for ln in out.getvalue().splitlines() if 'guard' in ln.lower()]}")


def test_rope_sight():
    print("== --rope from (5207, 689) toward the gate: no landing out of our sight as a ground target ==")
    # live 2026-10-19 10:29:20.1-21.3 fleeing the red Malito: "[Rope" at (5208, 698) from (5207, 689), "Target cannot be
    # seen." (500237), a denied step and ~1 s hindered with him 2 tiles off; eye to eye it looked clear
    import tempfile
    import loop_hunt
    import memory
    from sim import run as simrun
    w = world_with([], start=(5207, 689))
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
            mark = len(loop.link.events)
            jumped = loop.rope_jump((5203, 703))
            refused = [e.get("cliloc") for e in loop.link.events[mark:] if e.get("ev") == "cliloc"
                       and e.get("cliloc") in loop_hunt.ROPE_REFUSED]
    finally:
        store.close()
        simclock.uninstall(patched)
    check("a jump to a tile we see, or none tried: never 'Target cannot be seen.'", not refused,
          f"jumped {jumped} refused {refused} at {w.player.pos} misses {loop.totals.get('rope_misses', 0)}")


def red_world(seed=1, start=(5243, 726)):
    """The live 10:35 death's ground (2026-10-21): Logan with both pets beside him in Maus 3's east wing, whose only way
    to the leave gate runs north up the corridor (5240-5246, 696-719)."""
    w = world_with([], seed=seed, start=start)
    for p, d in zip(w.pets(), ((-1, 1), (1, 1))):
        p.x, p.y = start[0] + d[0], start[1] + d[1]
    return w


def red_loop(w, seed=1):
    """A --pets runner on `w` (the scenario's --leave-gate and --rope): (loop, clock, store, patched)."""
    import tempfile
    import loop_hunt
    import memory
    from sim import run as simrun
    link, clock = link_for(w)
    db = os.path.join(tempfile.mkdtemp(prefix="sim_test_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, [], 10, at_hunt=True)
                                             + ["--memory", db, "--seed", str(seed)])
    patched = simclock.install(clock)
    store = memory.Memory(db)
    with contextlib.redirect_stdout(io.StringIO()):
        loop = loop_hunt.HuntLoop(link, store, args)
        loop.team.setup(loop.state())
    return loop, clock, store, patched


def test_red_flight():
    print("== two reds down the only way to the gate (a dead end): past them at once; ropes as from casters ==")
    # live 2026-10-21 10:35:05-13 (hunt-20261021-102636-fdda, Maus 3): 'Ticket Tongje' in view at (5243, 708), 17 tiles
    # north of Logan at (5243, 726), running south, 'Di ve nha' behind him; the gate's route ran north up their corridor,
    # the rope landed Logan at (5244, 716) beside Tongje and the walk went on north past both: dead at (5244, 710). Then
    # "away from them first" (2026-10-21) ran ahead of three reds into the wing's south-west end, dead 2026-10-22
    # 20:01:02; user 2026-10-22: "Away first only outside dead ends" (sim/fight2001.py)
    from agent_link import Abort
    w = red_world()
    loop, clock, store, patched = red_loop(w)
    reds = [w.add_red(5243, 708)]
    world_tick = clock.on_advance

    def watch(now):
        world_tick(now)
        if len(reds) == 1 and w.t >= 1.2:                     # the second one 1.2 s later, a tile behind (live)
            reds.append(w.add_red(5243, 707, name="Di ve nha"))
    clock.on_advance = watch
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            try:
                res = loop.leave_by_gate("urgent")
            except Abort as e:
                res = {"ok": False, "error": str(e)}
    finally:
        store.close()
        simclock.uninstall(patched)
    first = next((ln for ln in out.getvalue().splitlines() if "to the moongate: the way passes" in ln), "")
    check("the first decision: a dead end, past them at once, not away into the wing", "(a dead end)" in first, first)
    ropes = [ln for ln in out.getvalue().splitlines() if "rope to" in ln]
    check("...every rope chosen as from ranged reds (the simulator's cast), not only farther off", all(
        "(ranged)" in ln for ln in ropes), str(ropes[:2]))
    print(f"  ({'through the gate' if res.get('ok') else res.get('error')} at {w.t:.1f} s, deaths {w.metrics['deaths']})")


def test_red_out_of_view():
    print("== the leave's red out of view before the flight, back along the gate's way: the wing is a dead end ==")
    # live 2026-10-21 17:37:09.9-22 (hunt-20261021-172808-cc2f, Maus 3): I Make Bodies came into view at (5219, 661, 25),
    # 18 tiles from Logan at (5237, 676), and walked down the ramp out of view by 10.9 (0x1D); the flight's first look
    # came at 11.4 and found no one: the rope to (5242, 674) and "route 82 steps" north up x 5243 to y 654, the gate's
    # way, where he came back along y 654 at 13.3 with Botan behind him (y 653): beside them at (5243, 658), dead at
    # (5233, 655) at 21.9. Their tiles and step times are the capture's.
    from agent_link import Abort
    w = red_world(start=(5237, 676))
    loop, clock, store, patched = red_loop(w)
    imb = [(5220, 660, 0.4), (5221, 659, 0.2), (5222, 658, 0.2)] \
        + [(5222 + i, 658 - min(i, 4), 0.36) for i in range(1, 8)] + [(x, 654, 0.2) for x in range(5230, 5242)] \
        + [(5242, 655, 0.3), (5243, 656, 0.2), (5244, 657, 0.2)]
    bot = [(5220 + i, 660 - i, 0.2) for i in range(7)] + [(x, 653, 0.2) for x in range(5227, 5240)] \
        + [(5240, 654, 0.3), (5241, 655, 0.2)]
    reds = [w.add_red(5219, 661, name="I Make Bodies", route=imb)]
    world_tick = clock.on_advance

    def watch(now):
        world_tick(now)
        if len(reds) == 1 and w.t >= 2.1:                     # Botan 2.1 s after him, on his tile (live)
            reds.append(w.add_red(5219, 661, name="Botan", route=bot))
    clock.on_advance = watch
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            why = loop.leave_reason(loop.state())             # the leave's check, him in view
            clock.advance(1.5)                                 # live 1.5 s to the flight's first look; him out of view
            gone = sim_cheb(w.player.pos, reds[0].pos) > 18
            try:
                loop.leave_by_gate("urgent")
            except Abort:
                pass
    finally:
        store.close()
        simclock.uninstall(patched)
    lines = [ln for ln in out.getvalue().splitlines() if "rope to" in ln or "moongate" in ln or "away from" in ln]
    check("the leave from him, then him out of view before the flight", bool(why) and "I Make Bodies" in why[0] and gone,
          str(why))
    check("...the flight's first decision from where he was: a dead end, past them at once; ropes as from a caster",
          bool(lines) and "(a dead end)" in lines[0]
          and all("(ranged)" in ln for ln in lines if "rope to" in ln), str(lines[:3]))


def test_rope_ranged():
    print("== a caster red on us at the east wing's mouth: the rope anyway, along the way to the gate ==")
    # live 2026-10-22 22:03: Flat Lander (nothing in his hands, Energy Bolts every ~2.2 s) on Logan at the wing's mouth;
    # no rope while a player hit us, dead in 10 s. User 2026-10-22: rope from casters and bows even while they hit us
    import nav
    w = red_world(start=(5239, 654))
    loop, clock, store, patched = red_loop(w)
    # 9 tiles west, in sight: his attack line, then bolts (his live empty hands; this test's caster casts from there)
    red = w.add_red(5230, 651, name="Flat Lander", spec={"open_r": 12, "cast_from_r": 12, "hands": [[0, 0, 1]]})
    red.next_step = 1e9
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            clock.advance(2.0)
            st = loop.state()
            calm, ranged = loop.player_calm(st), loop.ranged_fight(st)
            before = len(loop.mover.plan(st, nav.within((5204, 702), 0), mobiles=False)[0])
            loop.pk_flight = True
            jumped = loop.rope_jump((5204, 702), loop.hostile_spots(loop.state()))
            after = len(loop.mover.plan(loop.state(), nav.within((5204, 702), 0), mobiles=False)[0])
    finally:
        store.close()
        simclock.uninstall(patched)
    check("a player on us, but a caster: the rope went, landing nearer the gate along the way (past him)",
          not calm and ranged and jumped and after < before and "cursor cancelled" not in out.getvalue(),
          f"calm {calm} ranged {ranged} jumped {jumped} route {before} -> {after}")


def test_rope_cancel_attacked():
    print("== a player turns on us between the rope's cursor and our target: the cursor cancelled, no jump ==")
    # live 2026-10-21 10:35:05.99 "[Rope", 06.04 its cursor, 06.07 "Ticket Tongje is attacking you!", 06.54 our target
    # anyway; landed beside him (user: a rope only while no player is hitting us yet)
    w = red_world()
    loop, clock, store, patched = red_loop(w)
    red = w.add_red(5243, 704, spec={"open_r": 12})           # 22 tiles north, out of view, standing
    red.next_step = 1e9
    world_tick, moved = clock.on_advance, []

    def watch(now):
        world_tick(now)
        if w.pending and w.pending.get("kind") == "rope" and not moved:
            moved.append(w.t)
            w._teleport(red, (5243, 715, 0))                    # 11 tiles off in sight: its attack line next tick
    clock.on_advance = watch
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            loop.pk_flight = True
            start = w.player.pos
            jumped = loop.rope_jump((5247, 737))
    finally:
        store.close()
        simclock.uninstall(patched)
    check("its attack line came while the cursor was up", bool(moved) and w.metrics.get("red_attacks") == 1)
    check("...no jump: the cursor cancelled, we stand to run on", not jumped and w.player.pos == start
          and not w.metrics.get("ropes") and "cursor cancelled" in out.getvalue(),
          f"jumped {jumped} {start} -> {w.player.pos}")


def test_rope_combat_cooldown():
    print("== a rope used while a player attacks us: its 2-minute combat cooldown known, a refusal read ==")
    # live 2026-10-22 20:00:48.0 a jump 6.2 s after "Druidx is attacking you!", then at 20:00:58.86 "[Rope" refused:
    # "You must wait another 1m 50s before you may use this again while in combat." (the flight stood for it)
    import loop_hunt
    w = red_world()
    loop, clock, store, patched = red_loop(w)
    red = w.add_red(5243, 715, spec={"open_r": 12})           # 11 tiles north in sight: its attack line, no spells
    red.next_step = 1e9
    world_tick = clock.on_advance

    def watch(now):
        world_tick(now)
        red.next_swing = 1e9
    clock.on_advance = watch
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            clock.advance(1.0)
            loop.state()
            first = loop.rope_jump((5247, 737))
            known = loop.rope_next - loop_hunt.time.monotonic()
            clock.advance(6.0)
            loop.rope_next = 0.0                               # as if it hadn't known: the server's refusal
            second = loop.rope_jump((5224, 737))
            read = loop.rope_next - loop_hunt.time.monotonic()
    finally:
        store.close()
        simclock.uninstall(patched)
    check("the first jump landed and set the combat cooldown", first and known >= 110 and "in combat with a player"
          in out.getvalue(), f"landed {first} next in {known:.0f} s")
    check("...a second '[Rope' refused: its time left read from the server's line", not second and 100 <= read <= 115
          and "its combat cooldown" in out.getvalue(), f"second {second} next in {read:.0f} s")


def test_red_caster_sight():
    print("== a red's spell: range and sight checked when the cast ends; held out of sight, released in sight ==")
    # wiki Magery / ServUO Target.Invoke: the target is checked at targeting; a finished cast is held (precast)
    w = world_with([], start=SEEN_FROM)
    pl = w.player
    bolt = {"opener": {"eb": 1}, "mix": {"eb": 1}, "open_r": 12, "cast_from_r": 12, "summon_share": 0.0}
    red = w.add_red(*WALL_TO[:2], spec=bolt)
    red.next_step = 1e9                                    # it stands: only its casting is under test
    while not red.rs["cast"] and w.t < 5:
        w.run_until(w.t + 0.1)
    words_t = w.t
    w._teleport(pl, WALL_FROM)                             # behind the wall before its cast ends
    w.run_until(words_t + 4.0)
    held = red.rs["held"] is not None and pl.hits == pl.hits_max
    w._teleport(pl, SEEN_FROM)
    w.run_until(w.t + 0.5)
    check("cast out of sight: no damage, the spell held; back in sight: released and landed", held
          and pl.hits < pl.hits_max and red.rs["held"] is None, f"held {held} hits {pl.hits}")


def test_red_hamstring():
    print("== a red's Hamstring: stamina 0 and a walk's pace for 3 s, then the stamina back; immune 30 s after ==")
    w = world_with([], start=FLOOR)
    pl = w.player
    link, clock = link_for(w)
    hit = {"open_r": 2, "hands": [[5119, 1, 1]], "stealth_p": 0.0, "summon_share": 0.0,
           "melee": {"swing_s": [1.0, 1.0], "hit": 1.0, "damage": [1, 1],
                     "hamstring": {"s": 3.0, "immune_s": 30.0, "cd_s": [1, 1]}}}
    red = w.add_red(FLOOR[0] + 1, FLOOR[1], kit="melee", spec=hit)
    red.next_step = 1e9
    clock.advance(0.3)
    hs = (w.hamstrung_until > w.t, pl.stam)
    red.next_swing = 1e9                                   # no more hits: only our steps from here
    clock.advance(0.5)
    seq = iter(range(1, 50))
    link.send(actions.walk(4, run=True, seq=next(seq)))   # a turn south, a step, then one 0.2 s and one 0.4 s later
    link.send(actions.walk(4, run=True, seq=next(seq)))
    at1 = pl.pos[:2]
    clock.advance(0.2)
    link.send(actions.walk(4, run=True, seq=next(seq)))
    fast = pl.pos[:2]
    clock.advance(0.4)
    link.send(actions.walk(4, run=True, seq=next(seq)))
    slow = pl.pos[:2]
    clock.advance(3.0)
    check("hit and hamstrung: stamina 0; a step 0.2 s after the last refused, one 0.4+ s after taken",
          hs == (True, 0) and at1 != FLOOR and fast == at1 and slow != at1, f"{hs} {at1} {fast} {slow}")
    check("...3 s later the stamina it had, and immune: the next hit hamstrings no one",
          pl.stam == pl.stam_max and not w.hamstring_player(3.0, 30.0), f"stam {pl.stam}")


def test_red_breaks_recall():
    print("== a red's hit on us mid-recall: the cast disturbed (500641), the recast refused until it recovers ==")
    # docs/research/SPELL_INTERRUPTS.md: every player hit broke the cast (3 of 3, Bastet); recovery max(0.2, 1-sqrt(e/T))
    w = world_with([], start=FLOOR)
    link, clock = link_for(w)
    w.recall_at, w.recall_start_t = (w.t + 1.9, w.sc["recall"]["rows"][0]), w.t
    clock.advance(0.3)
    red = w.add_red(FLOOR[0] + 1, FLOOR[1], kit="melee",
                    spec={"open_r": 2, "stealth_p": 0.0, "melee": {"swing_s": [9, 9], "hit": 1.0, "damage": [5, 5]}})
    red.next_step = 1e9
    clock.advance(0.2)
    link.state()
    disturbed = w.recall_at is None and any(e.get("cliloc") == 500641 for e in link.events)
    check("the recall disturbed, no arrival, a recovery ahead", disturbed and w.cast_recover_until > w.t
          and w.player.pos[:2] == FLOOR, f"{disturbed} recover {w.cast_recover_until - w.t:.2f} s")


def test_red_follows_teleporter():
    print("== a red on us when we take a teleporter follows us through it; one arriving by a gold gate reaches Maus 3 ==")
    # live 2026-10-22 18:53:14 Botan 3 s behind Logan through (5554, 700); the crimson gate is exit-only, so reds walk in
    w = world_with([], start=(5554, 701))
    pl = w.player
    pl.z = w.stand_z(5554, 701, 20)                        # on the platform the teleporter stands on (the map's z)
    link, clock = link_for(w)
    red = w.add_red(5564, 702, kit="melee", spec={"stealth_p": 0.0, "open_r": 1, "jump": {}})
    clock.advance(0.5)
    seq = iter(range(1, 50))
    for d in (0, 0):                                       # north onto the teleporter tile (5554, 700), as Logan went
        link.send(actions.walk(d, run=True, seq=next(seq)))
        clock.advance(0.25)
    through = w.reds.level(pl.pos) == "l2"
    clock.advance(8.0)
    check("we through to the second level, the red after us", through and w.reds.level(red.pos) == "l2",
          f"us {pl.pos} red {red.pos}")
    sc = load()
    sc["reds"] = {**sc["reds"], "enabled": True, "arrive_per_h": 0.0}
    sc["player"]["start"] = sc["recall"]["rows"][0]["at"]          # home: no one to hunt
    w2 = World(sc, seed=1)
    for m in list(w2.reds.mobs()):
        w2.reds._gone(m)
    w2.reds._group((5626, 689, 0), stay=600.0)
    w2.run_until(240.0)
    on3 = [m for m in w2.reds.mobs() if w2.reds.level(m.pos) == "maus3"]
    check("a group recalled in by a gold gate runs the teleporters down to Maus 3", len(on3) == len(w2.reds.mobs()) > 0,
          str([(m.name, m.pos) for m in w2.reds.mobs()]))


def test_gold_banked():
    print("== gold banked: what we carried at our last landing home; a death loses only what came after ==")
    w = world_with([], start=FLOOR)
    w.metrics["gold_looted"] = 5000
    w._land(w.sc["recall"]["rows"][0])
    w.metrics["gold_looted"] += 3000
    w._land(w.sc["recall"]["rows"][1])                     # back in: nothing banked by that
    check("5000 banked at home, the 3000 since not", w.metrics["gold_banked"] == 5000, str(w.metrics["gold_banked"]))



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


def _log_hurts(w):
    """Wrap w.hurt to record (time, victim, kind, hits lost)."""
    log, orig = [], w.hurt

    def hurt(victim, amount, by, kind="melee", song_pets=True):
        before = victim.hits
        orig(victim, amount, by, kind, song_pets)
        log.append((round(w.t, 2), victim, kind, before - victim.hits))
    w.hurt = hurt
    return log


def _log_overhead(w):
    log, orig = [], w._overhead

    def overhead(m, text, kind=0):
        log.append((round(w.t, 2), m.serial, text))
        orig(m, text, kind)
    w._overhead = overhead
    return log


def _tile_at(w, d):
    """A floor tile whose nearest of us is exactly `d` tiles off, Logan in its sight."""
    pl = w.player
    for dy in range(-d - 2, d + 3):
        for dx in range(-d - 2, d + 3):
            x, y = pl.x + dx, pl.y + dy
            z = w.stand_z(x, y, pl.z)
            if abs(z - pl.z) > 5 or not any(w.walk.can_walk(x, y, z, k) for k in range(8)):
                continue
            near = min(max(abs(o.x - x), abs(o.y - y)) for o in w.team())
            probe = Mob(0, "monster", "probe", "probe", 400, x, y, z)
            if near == d and w.can_see(probe, pl):
                return (x, y, z)
    return None


def test_audit_mechanics():
    print("== the monster mechanics measured on 2026-10-15's captures (docs/SIM.md 'Monster mechanics audit') ==")
    # aggro range 10: one of us seen at 11 tiles for 6 s wakes nothing; at 9 it wakes 1.5-3 s later
    woke = {}
    for d in (11, 9):
        w = world_with([{"key": "shade wolf", "at": [5203, 717, 0]}], seed=11, start=(5207, 726))   # an open room
        m = next(m for m in w.mobs.values() if m.kind == "monster")
        spot = _tile_at(w, d)
        if spot is None:
            woke[d] = "no tile"
            continue
        m.x, m.y, m.z = spot
        m.home = spot
        link, clock = link_for(w)
        when = None
        for _ in range(60):
            clock.advance(0.1)
            if when is None and m.target is not None:
                when = clock.elapsed
        woke[d] = when
    check("aggro 10: seen at 11 tiles, no wake in 6 s; at 9, war 1.5-3 s after (+ the 0.5 s think; live n=211)",
          woke.get(11) is None and isinstance(woke.get(9), float) and 1.4 <= woke[9] <= 4.1, str(woke))
    # the victims: the numbers are the gravebug's; Logan x1.6, the phoenix x1.7 on melee
    w = world_with([])
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    af = next(p for p in w.pets() if p.name == "AlaFlambe")
    check("a monster's hit: the gravebug x1, Logan x1.2 on melee and x1.6 on spells, the phoenix x1.7 on melee only",
          (w._taken_x(gb, "melee"), w._taken_x(w.player, "melee"), w._taken_x(w.player, "spell"),
           w._taken_x(af, "melee"), w._taken_x(af, "spell")) == (1.0, 1.2, 1.6, 1.7, 1.0))
    # apparition: a blink every ~8 s though its target stands beside it, onto the target's tile, the area hit
    # only at the landing; the song cuts Logan's share, not the pets'
    w = world_with([{"key": "apparition", "at": [5268, 649, 0]}], seed=12)
    m = next(m for m in w.mobs.values() if m.kind == "monster")
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    for o in w.team():
        o.hits = o.hits_max = 10 ** 6
    m.hits = m.hits_max = 10 ** 7
    m.target, m.threat = gb.serial, {gb.serial: 1e9}
    w.songs["discord"] = 1e9
    hurts, said = _log_hurts(w), _log_overhead(w)
    link, clock = link_for(w)
    phases, landings = [], []
    hidden = False
    for _ in range(200):
        clock.advance(0.1)
        if m.hidden_until and not hidden:
            phases.append(round(w.t, 2))
        if hidden and not m.hidden_until:
            landings.append((round(w.t, 2), (m.x, m.y) == (gb.x, gb.y)))
        hidden = bool(m.hidden_until)
    bursts = [h for h in hurts if h[2] == "burst"]
    gaps = [round(b - a, 2) for a, b in zip(phases, phases[1:])]
    on_landing = all(any(abs(b[0] - t) < 0.05 for t, _ in landings) for b in bursts)
    pet_burst = [h[3] for h in bursts if h[1].kind == "pet"]
    pl_burst = [h[3] for h in bursts if h[1].kind == "player"]
    check("apparition: '*phases*' 0.6-2.5 s in, then every 7.8-8.7 s beside its target, back on its tile 1.1-1.7 s "
          "later with the area hit there only (live n=48/56)",
          phases and 0.6 <= phases[0] <= 2.7 and len(gaps) >= 1 and all(7.8 <= g <= 8.7 for g in gaps)
          and landings and all(ok for _, ok in landings) and bursts and on_landing
          and sum(1 for t, _, x in said if x == "*phases*") == len(phases),
          f"phases {phases} landings {landings} bursts {[(b[0], b[1].name, b[3]) for b in bursts]}")
    check("...the pets' share 36-38 under the Song of Discordance too, Logan's 55-60 cut by it",
          pet_burst and all(36 <= n <= 38 for n in pet_burst) and pl_burst
          and all(44 <= n <= 50 for n in pl_burst), f"pets {pet_burst} Logan {pl_burst}")
    # banshee: a hit with the ability ready dematerializes it, back 3-12 tiles off 1.4-1.6 s later; 7.5 s cooldown
    w = world_with([{"key": "banshee", "at": [5266, 652, 0]}], seed=13)
    m = next(m for m in w.mobs.values() if m.kind == "monster")
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    for o in w.team():
        o.hits = o.hits_max = 10 ** 6
    m.hits = m.hits_max = 10 ** 7
    m.target, m.threat = gb.serial, {gb.serial: 1e9}
    said = _log_overhead(w)
    link, clock = link_for(w)
    clock.advance(0.2)
    at = m.pos
    w.hurt(m, 50, gb)
    gone = bool(m.hidden_until) and f"0x{m.serial:08X}" not in link.state()["world"]["mobiles"]
    back = None
    for _ in range(25):
        clock.advance(0.1)
        if back is None and not m.hidden_until:
            back = round(clock.elapsed - 0.2, 2)
    moved = max(abs(m.x - at[0]), abs(m.y - at[1]))
    w.hurt(m, 50, gb)
    again_soon = bool(m.hidden_until)
    clock.advance(6.5)
    w.hurt(m, 50, gb)
    again_later = bool(m.hidden_until)
    check("banshee: hit, '*dematerializes*' and gone; back 3-12 tiles off 1.4-1.6 s later; not again within 7.5 s "
          "(live n=63/61)",
          gone and back is not None and 1.3 <= back <= 1.7 and 3 <= moved <= 12 and not again_soon and again_later
          and any(x == "*dematerializes*" for _, _, x in said), f"gone {gone} back {back} moved {moved} "
          f"again soon {again_soon} later {again_later}")
    # vampire: '*attempts to feed*', 5.0-5.9 s later '*feeds*', then 4-5 ticks 0.5 s apart on its target
    w = world_with([{"key": "vampire", "at": [5268, 648, 0]}], seed=14)
    m = next(m for m in w.mobs.values() if m.kind == "monster")
    m.spec = {**m.spec, "specials": [{**sp, "chance": 1.0} for sp in m.spec["specials"]]}
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    for o in w.team():
        o.hits = o.hits_max = 10 ** 6
    m.hits = m.hits_max = 10 ** 7
    m.target, m.threat = gb.serial, {gb.serial: 1e9}
    hurts, said = _log_hurts(w), _log_overhead(w)
    link, clock = link_for(w)
    clock.advance(9.5)
    tried = next((t for t, s, x in said if x == "*attempts to feed*"), None)
    fed = next((t for t, s, x in said if x == "*feeds*"), None)
    ticks = [h for h in hurts if h[2] == "feed"]
    check("vampire: '*attempts to feed*', '*feeds*' 5.0-5.9 s later, 4-5 ticks 0.5 s apart on its target (live n=12)",
          tried is not None and fed is not None and 4.9 <= fed - tried <= 6.0 and 4 <= len(ticks) <= 5
          and all(h[1] is gb for h in ticks) and all(abs(b[0] - a[0] - 0.5) < 0.06 for a, b in zip(ticks, ticks[1:])),
          f"tried {tried} fed {fed} ticks {[(h[0], h[3]) for h in ticks]}")
    # a transfixed copy walks and swings at the monsters' pace; a second transfix takes the other pet
    w = world_with([{"key": "vampire countess", "at": [5266, 651, 0]}], seed=15)
    m = next(m for m in w.mobs.values() if m.kind == "monster")
    sp = next(s for s in m.spec["specials"] if s["kind"] == "transfix")
    w._transfix(w.pets()[0], sp, m)
    m.spec = {**m.spec, "specials": [{**sp, "chance": 1.0}]}
    m.target = w.pets()[0].serial
    link, clock = link_for(w)
    clock.advance(6.0)
    copies = [c for c in w.mobs.values() if c.key == "transfixed copy"]
    check("transfix: copies step every 0.4 s and swing every 3.38 s (live n=101/24); one up, the other pet is next",
          len(copies) == 2 and {c.spec["copy_of"] for c in copies} == {p.serial for p in w.pets()}
          and all(c.spec["step_s"] == 0.4 and c.spec["swing_s"] == 3.38 for c in copies),
          str([(c.name, c.spec["step_s"], c.spec["swing_s"]) for c in copies]))


def test_walk_in():
    print("== --enter-via: the pets on 'All Follow Me' the whole way in; a slow pet stops the walk until it comes ==")
    import tempfile
    import loop_hunt
    import memory
    from sim import run as simrun
    # live 22:20:56: "All Guard Me" at the Maus Near 2 landing set the pets fighting there and Logan walked the
    # 75 steps to the first teleporter alone. A gravebug at a quarter of Logan's pace stands in for a pet held back.
    w = world_with([], start=(5626, 688))
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    gb.spec = {**gb.spec, "step_s": 0.8}
    link, clock = link_for(w)
    db = os.path.join(tempfile.mkdtemp(prefix="sim_test_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, ["--no-songs"], 10)
                                             + ["--memory", db, "--seed", "1"])
    first = tuple(w.sc["runner"]["enter_via"][0])
    patched = simclock.install(clock)
    store = memory.Memory(db)
    lag, err = [], None
    run_until = clock.on_advance

    def advance(now):
        run_until(now)
        lag.append(sim_cheb(gb.pos, w.player.pos))
    clock.on_advance = advance
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            loop = loop_hunt.HuntLoop(link, store, args)
            loop.team.setup(loop.state())
            g0 = w.metrics["guard_commands"]
            try:
                loop.walk_in([first])
            except Exception as e:  # noqa: BLE001
                err = e
    finally:
        clock.on_advance = run_until
        store.close()
        simclock.uninstall(patched)
    check("through the first teleporter, no abort", err is None and sim_cheb(w.player.pos, first) > 3,
          f"{err} at {w.player.pos}")
    check("...no 'All Guard Me' on the way", w.metrics["guard_commands"] == g0, str(w.metrics["guard_commands"] - g0))
    check("...both pets came through with us", all(sim_cheb(p.pos, w.player.pos) <= tamer.FOLLOW_R + 1 for p in w.pets()),
          str([(p.name, p.pos) for p in w.pets()]))
    check("...the slow one never more than a few tiles past the lag limit, the walk waiting for it",
          lag and max(lag) <= loop_hunt.WALK_IN_LAG_R + 3 and "waiting for the pets" in buf.getvalue(),
          f"max {max(lag) if lag else None}")


def test_tele_dash():
    print("== attacked on the walk-in within TELE_DASH_R of the teleporter: on and through it, no stand ==")
    import tempfile
    import loop_hunt
    import memory
    from sim import run as simrun
    # live 2026-10-20 22:57:15-31 (hunt-20261020-223926-1d18): attacked 13 steps from 5522,637 (route 12 from
    # (5513, 647)), the pets guarded and Logan stood dying until the user walked him through to Maus 3. Here
    # something unseen takes a hit point every 0.5 s down to 60, so a stand lasts ~20 s
    w = world_with([], seed=7, start=(5513, 647))
    tele, arrival = (5522, 637), (5199, 737)
    link, clock = link_for(w)
    db = os.path.join(tempfile.mkdtemp(prefix="sim_test_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, ["--no-songs"], 10, at_hunt=False)
                                             + ["--memory", db, "--seed", "1", "--human", "off"])
    patched = simclock.install(clock)
    store = memory.Memory(db)
    world_tick, last, err = clock.on_advance, [None], None

    def bleed(now):
        world_tick(now)
        if (last[0] is None or now - last[0] >= 0.5) and sim_cheb(w.player.pos, arrival) > 20:
            last[0] = now
            if w.player.hits > 60:
                w.hurt(w.player, 1, None, kind="bleed")
    clock.on_advance = bleed
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            loop = loop_hunt.HuntLoop(link, store, args)
            loop.team.setup(loop.state())
            g0 = w.metrics["guard_commands"]
            t0 = loop_hunt.time.monotonic()
            try:
                loop.walk_in([tele])
            except Exception as e:  # noqa: BLE001
                err = e
            took = loop_hunt.time.monotonic() - t0
    finally:
        clock.on_advance = world_tick
        store.close()
        simclock.uninstall(patched)
    out = buf.getvalue()
    check("through within seconds, on Maus 3 (not a stand while the pets guard)",
          err is None and took < 10 and sim_cheb(w.player.pos, arrival) <= 3, f"{err} in {took:.1f} s at {w.player.pos}")
    check("...no 'All Guard Me', 'through it now' logged, not 'attacked on the way in'",
          w.metrics["guard_commands"] == g0 and "steps from it; through it now" in out
          and "attacked on the way in" not in out, f"guards {w.metrics['guard_commands'] - g0}")
    check("...both pets came through with us", all(sim_cheb(p.pos, w.player.pos) <= tamer.FOLLOW_R + 1 for p in w.pets()),
          str([(p.name, p.pos) for p in w.pets()]))


def walk_in_red(start, place, tiles):
    """A --pets runner from `start` on the walk in, a red placed by `place(w)` (None until it places one) as the world
    ticks; walk_in(tiles). -> (exception or None, the runner's log, world, loop)."""
    import tempfile
    import loop_hunt
    import memory
    from sim import run as simrun
    w = world_with([], seed=3, start=start)
    for p, d in zip(w.pets(), ((-1, 1), (1, 1))):
        p.x, p.y = start[0] + d[0], start[1] + d[1]
    link, clock = link_for(w)
    db = os.path.join(tempfile.mkdtemp(prefix="sim_test_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, ["--no-songs"], 10)
                                             + ["--memory", db, "--seed", "1"])
    patched = simclock.install(clock)
    store = memory.Memory(db)
    world_tick, red, err = clock.on_advance, [], None

    def tick(now):
        world_tick(now)
        if not red and (r := place(w)) is not None:
            red.append(r)
    clock.on_advance = tick
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            loop = loop_hunt.HuntLoop(link, store, args)
            loop.team.setup(loop.state())
            try:
                loop.walk_in(tiles)
            except Exception as e:  # noqa: BLE001
                err = e
    finally:
        clock.on_advance = world_tick
        store.close()
        simclock.uninstall(patched)
    return err, buf.getvalue(), w, loop


def test_walk_in_red_first_level():
    print("== a red on the walk in's first level: the flight to a gold moongate and the recall home ==")
    # live 2026-10-22 18:52:33 (hunt-20261022-185159-c0c5): Botan in view at (5610, 677), 18 tiles from Logan at about
    # (5592, 688) on the first level; no check on the walk in, so it walked on (sim/fight1853.py, docs/NOTES.md)
    import loop_hunt
    err, out, w, _ = walk_in_red((5600, 689), lambda w: w.add_red(5610, 677, name="Botan") if w.t >= 1.0 else None,
                                 [tuple(t) for t in w_enter_via()])
    check("WalkInFled: by a gold moongate's recall, home and alive",
          isinstance(err, loop_hunt.WalkInFled) and not w.player.ghost and w.player.pos[0] < 5000
          and "LEAVING the walk in by the gold moongate" in out, f"{err!r} at {w.player.pos}")
    check("...no fight, guard or bard skill against him", "attacked on the way in" not in out
          and "area peace" not in out, "")


def test_walk_in_red_second_level():
    print("== a red through the first teleporter after us: on through the rest at flight pace, the leave gate next ==")
    # live 2026-10-22 18:53:14-48: Botan came through 5554,700 3 s after Logan, "Botan is attacking you!" at 18:53:17;
    # the pets guarded, Logan vetted and played Area Peace, dead at (5535, 713). Simulator before the fix: 12 of 12
    # dead (sim/fight1853.py level2)
    start = (5547, 696)
    err, out, w, loop = walk_in_red(start, lambda w: w.add_red(*start, name="Botan") if w.t >= 3.0 else None,
                                    [tuple(t) for t in w_enter_via()[1:]])
    check("through to Maus 3 alive, the leave gate's flight next (walk_in_red)",
          err is None and not w.player.ghost and sim_cheb(w.player.pos, (5199, 737)) <= 3
          and loop.walk_in_red is not None and "on through the teleporters at flight pace" in out,
          f"{err!r} at {w.player.pos}, walk_in_red {loop.walk_in_red!r}")
    check("...no stand: no 'attacked on the way in', no area peace", "attacked on the way in" not in out
          and "area peace" not in out, "")


MAUS2_STUCK = (5507, 691, -8)    # live 2026-10-24 00:40:32 (hunt-20261024-000844-30b5): Logan left alive here on Maus 2
SIM_HOME = (4134, 1429)         # the scenario tome's first row, the default rune: home


def main_in_sim(start, extra=(), walk_to=None, seed=7):
    """loop_hunt.main() as `ctl run hunt` starts it, with the scenario's arguments (run.runner_args: --pets --crawl
    --leave-recall --leave-gate --gate-dest --enter-recall --enter-rune --enter-via, as docs/OVERSEER.md §5's) and
    `extra`, on the simulator: Logan at `start` (x, y, z), both pets beside him, the store knowing the scenario's
    teleporters (as the live one does from the walks in). `walk_to(orig, mover, *a, **k)` stands in for
    Mover.walk_to (a failure injected). -> (exit code, the runner's log, world, the store's `escape` junctures, its
    `escape` job events)."""
    import tempfile
    import agent_link
    import loop_hunt
    import memory
    from sim import run as simrun
    sc = load()
    sc["spawns"] = []
    sc["player"]["start"] = list(start)
    w = World(sc, seed=seed)
    for p, d in zip(w.pets(), ((-1, 1), (1, 1))):
        w._teleport(p, (start[0] + d[0], start[1] + d[1], start[2]))
    link, clock = link_for(w)
    db = os.path.join(tempfile.mkdtemp(prefix="sim_test_"), "harness.db")
    store = memory.Memory(db)
    for tp in sc["teleporters"]:
        store.teleporter_record(0, tp["from"][0], tp["from"][1], 0, *tp["to"])
    store.close()
    argv = simrun.runner_args(sc, ["--no-songs", *extra], 10) + ["--memory", db, "--seed", "1", "--human", "off"]
    saved = loop_hunt.Link, sys.argv, agent_link.Mover.walk_to
    orig = saved[2]
    loop_hunt.Link = lambda *a, **k: link
    sys.argv = ["loop_hunt.py", *argv]
    if walk_to is not None:
        agent_link.Mover.walk_to = lambda mover, *a, **k: walk_to(orig, mover, *a, **k)
    patched = simclock.install(clock)
    buf, code = io.StringIO(), None
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            try:
                loop_hunt.main()
            except SystemExit as e:
                code = e.code
    finally:
        loop_hunt.Link, sys.argv, agent_link.Mover.walk_to = saved
        simclock.uninstall(patched)
    store = memory.Memory(db)
    js = [j for j in store.junctures(limit=1000) if j["kind"] == "escape"]
    evs = store.job_events("hunt")
    store.close()
    return code, buf.getvalue(), w, js, [e for e in evs if e["kind"] == "escape"]


def no_route_once(label):
    """A Mover.walk_to stand-in whose first walk labelled `label` fails as the live walk in did at 00:40:32 (its door
    walled: "<label>: no route from (5507, 691)"); every other walk, and that one again, goes on."""
    from agent_link import WalkFailed
    failed = []

    def walk_to(orig, mover, *a, **k):
        if (a[2] if len(a) > 2 else k.get("label")) == label and not failed:
            failed.append(1)
            here = mover.link.pos(mover.link.state())
            raise WalkFailed(f"{label}: no route from {tuple(here[:2])}")
        return orig(mover, *a, **k)
    return walk_to


def test_escape_after_walk_in_error():
    print("== the walk in fails on Maus 2 (live 00:40:32, 'no route'): on through the teleporters, the leave gate, "
          "home; not left inside ==")
    # live 2026-10-24 (hunt-20261024-000844-30b5): "ABORTED: to the teleporter at 5522,637: no route from (5507, 691)",
    # Logan alive at (5507, 691, -8), nobody moved him; a calamity attacked at 01:10:32 and killed him at 01:10:48
    code, out, w, js, evs = main_in_sim(MAUS2_STUCK, walk_to=no_route_once("to the teleporter at 5522,637"))
    err = "to the teleporter at 5522,637: no route from (5507, 691)"
    check("home and alive, the run's exit non-zero", code == 1 and not w.player.ghost
          and sim_cheb(w.player.pos, SIM_HOME) <= 5, f"exit {code} at {w.player.pos} ghost {w.player.ghost}")
    check("...the log: the error inside, the escape, the ending naming both",
          f"ERROR inside the dungeon at (5507, 691, -8): {err}; escaping home" in out
          and f"escaped home after: {err}" in out
          and f"ABORTED: {err} (escaped home by the teleporters from 5522,637 and the leave gate)" in out,
          "\n".join(ln for ln in out.splitlines() if "ERROR" in ln or "escape" in ln or "ABORTED" in ln)[-600:])
    check("...an `escape` juncture (attention) and job event", len(js) == 1 and js[0]["severity"] == "attention"
          and len(evs) == 1 and evs[0]["data"]["ok"], f"{[(j['severity'], j['summary']) for j in js]}")
    check("...both pets came home with him", all(sim_cheb(p.pos, w.player.pos) <= 3 for p in w.pets()),
          str([(p.name, p.pos) for p in w.pets()]))


def test_go_home():
    print("== --go-home: from the first level, Maus 2 where Logan was left, and Maus 3: home, exit 0, no hunting ==")
    for where, start, way in (("the first level", (5600, 689, 0), "the gold moongate at"),
                              ("Maus 2", MAUS2_STUCK, "the teleporters from 5522,637"),
                              ("Maus 3", (5243, 726, 0), "the leave gate at 5203,703")):
        code, out, w, js, evs = main_in_sim(start, ["--go-home"])
        check(f"from {where} {start[:2]}: home, exit 0, by {way}", code == 0 and not w.player.ghost
              and sim_cheb(w.player.pos, SIM_HOME) <= 5 and "--go-home: home at" in out
              and bool(evs) and evs[-1]["data"]["way"].startswith(way),
              f"exit {code} at {w.player.pos} way {evs[-1]['data']['way'] if evs else None}: "
              + " | ".join(ln for ln in out.splitlines() if "go-home" in ln or "escape" in ln or "ABORTED" in ln)[-500:])
        check(f"...no kill, no `escape` juncture (the Seer asked for it)", w.metrics.get("kills", 0) == 0 and not js,
              f"kills {w.metrics.get('kills')} junctures {len(js)}")


def test_no_escape_on_death_or_stop():
    print("== no escape after a death or a `ctl stop`: the run ends where it stands ==")
    from agent_link import WalkFailed

    def dies(orig, mover, *a, **k):
        w = mover.link.world
        w.hurt(w.player, 1000, None, kind="bleed")
        raise WalkFailed("no route")

    def stopped(orig, mover, *a, **k):
        raise KeyboardInterrupt
    for name, fake in (("a death", dies), ("ctl stop", stopped)):
        code, out, w, js, evs = main_in_sim(MAUS2_STUCK, walk_to=fake)
        check(f"{name}: exit 1, no escape, no recall", code == 1 and "escaping home" not in out
              and w.metrics["recalls"] == 0 and not js and not evs,
              f"exit {code} recalls {w.metrics['recalls']}: "
              + " | ".join(ln for ln in out.splitlines() if "ABORTED" in ln or "stopped" in ln)[-300:])


def w_enter_via():
    from sim.scenario import load
    return load()["runner"]["enter_via"]


def test_soul_jar():
    print("== Field of Souls: with an empty soul jar, walk to an eerie corpse and fill it there; no jar, no walk ==")
    import json
    import soul_jar
    from sim.scenario import load as load_sc
    corpse_at = (5266, 654)                                   # 6 route steps south of FLOOR
    out = {}
    for jar in (True, False):
        sc = load_sc()
        sc["spawns"] = []
        sc["player"]["start"] = [FLOOR[0], FLOOR[1], 0]
        if jar:
            sc["loadout"]["items"].append({"serial": 0x6FFF0001, "graphic": soul_jar.EMPTY_JAR_GRAPHIC, "amount": 2,
                                           "kind": "soul_jar"})
        w = World(sc, seed=1)
        corpse = w.eerie_corpse(*corpse_at)
        loop, clock, store, patched = hurt_loop(w, w.player.hits_max)
        filled_at = None
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                for _ in range(12):
                    loop.tick()
                    clock.advance(0.5)
                    if w.metrics.get("soul_jars") and filled_at is None:
                        filled_at = sim_cheb(w.player.pos, corpse_at)
                st = loop.state()
                me = loop.me(st)
                out[jar] = {"filled": [s for s, _ in soul_jar.filled_jars(st["world"], me)],
                            "empty": sum(it.get("amount") or 1 for _, it in soul_jar.empty_jars(st["world"], me)),
                            "corpse_seen": f"0x{corpse:08X}" in st["world"]["items"], "at": filled_at,
                            "dist": sim_cheb(w.player.pos, corpse_at),
                            "events": [json.loads(r[0]) for r in store.con.execute(
                                "SELECT data FROM job_events WHERE kind='soul_jar'")]}
                if jar:                                                         # a fight on (the pets engaged),
                    loop.engaged = 0x00300099                                   # back at the fight spot:
                    w.eerie_corpse(*corpse_at)                                  # one 6 tiles off...
                    far = loop.soul_corpse(loop.state(), walk=False)
                    near = w.eerie_corpse(*w.player.pos[:2])                    # ...and one on our tile
                    pick = loop.soul_corpse(loop.state(), walk=False)
                    out["fight"] = (far, pick[0] if pick else None, near, sim_cheb(w.player.pos, corpse_at))
        finally:
            store.close()
            simclock.uninstall(patched)
    got = out[True]
    ev = got["events"][0] if got["events"] else {}
    check("with a jar: one filled jar in the pack, one empty left, the corpse gone, filled within the fill range",
          len(got["filled"]) == 1 and got["empty"] == 1 and not got["corpse_seen"]
          and got["at"] is not None and got["at"] <= soul_jar.FILL_RANGE, str({k: got[k] for k in got if k != "events"}))
    check("...a soul_jar job event: filled, the spirit's name, the corpse's tile",
          len(got["events"]) == 1 and ev.get("filled") is True and ev.get("spirit") == "Karin Winckel"
          and ev.get("at", [])[:2] == list(corpse_at), str(got["events"]))
    check("no jar: no walk to the corpse, it stays", out[False]["dist"] >= 6 and out[False]["corpse_seen"]
          and not out[False]["events"], str(out[False]["dist"]))
    f = out["fight"]
    check("a fight on: only an eerie corpse within reach without a step", f[0] is None and f[1] == f[2] and f[3] > 2,
          str(f))


def test_soul_jar_guards():
    print("== an eerie corpse: no walk while the pets fight; the walk stops short of the idle monsters around it ==")
    # live 2026-10-20 18:22:07-27 (hunt-20261020-181726-433a): the pets fighting a shadowguard beside him, Logan walked
    # 16 steps alone to an eerie corpse at (5179, 680) with an idle vampire countess, a corpsebride and a vampire bat
    # by it: the jar filled at 74/100, then 100 -> 0 in 13 s with the pets 15 tiles behind
    import soul_jar
    from sim.scenario import load as load_sc
    corpse_at = (5179, 680)
    out = {}
    for case in ("pets fighting", "monsters by the corpse"):
        sc = load_sc()
        group = [{"key": "vampire countess", "at": [5177, 682, 25]}, {"key": "corpsebride", "at": [5176, 678, 25]},
                 {"key": "vampire bat", "at": [5175, 682, 25]}]
        wolf = [{"key": "shade wolf", "at": [5199, 683, 25]}]
        sc["spawns"] = [{**s, "respawn_s": [9999, 9999]} for s in (wolf if case == "pets fighting" else group)]
        sc["defaults"]["spawn_spread"] = sc["defaults"]["wander_per_s"] = 0
        sc["player"]["start"] = [5197, 682, 0]
        sc["loadout"]["items"].append({"serial": 0x6FFF0001, "graphic": soul_jar.EMPTY_JAR_GRAPHIC, "amount": 1,
                                       "kind": "soul_jar"})
        w = World(sc, seed=7)
        corpse = w.eerie_corpse(*corpse_at)
        loop, clock, store, patched = hurt_loop(w, w.player.hits_max)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                if case == "pets fighting":
                    m = next(m for m in w.mobs.values() if m.kind == "monster")
                    gr = next(p for p in w.pets() if p.name == "GraveRobber")
                    m.x, m.y = gr.x + 1, gr.y
                    m.target, m.threat = gr.serial, {gr.serial: 50}
                    w._warm(m, True)
                    w.hurt(gr, 5, m)
                    clock.advance(0.5)
                    st = loop.state()
                    out[case] = (loop.patrol_hold(st), loop.soul_corpse(st))
                else:
                    st = loop.state()
                    pick = loop.soul_corpse(st)
                    loop.fill_soul_jar(*pick)
                    st = loop.state()
                    out[case] = (pick[0] == corpse, sim_cheb(w.player.pos, corpse_at), w.metrics.get("soul_jars", 0),
                                 corpse in loop.jar_later, loop.soul_corpse(st))
        finally:
            store.close()
            simclock.uninstall(patched)
    hold, pick = out["pets fighting"]
    check("the pets fighting beside us: no walk to a corpse 18 tiles off", hold is not None and pick is None,
          f"hold {hold!r} pick {pick}")
    picked, dist, filled, later, again = out["monsters by the corpse"]
    check("monsters by the corpse: the walk stops short, no jar filled there, the corpse waits",
          picked and dist > soul_jar.FILL_RANGE + 2 and not filled and later and again is None,
          f"picked {picked} dist {dist} filled {filled} deferred {later} picked again {again}")


def test_soul_jar_defer():
    print("== an eerie corpse whose walk waited for a monster to come: tried after a kill, and at once within reach ==")
    # live 2026-10-20 23:02:03-23:04:10 (hunt-20261020-225836-002b): the walk stopped "vampire bat in sight at 10 tiles:
    # letting it come; the corpse waits 120 s"; the bat died at 23:02:15, and at 23:03:21 Logan looted a corpsebride one
    # tile from the eerie corpse, still not tried; the user filled the jar by hand
    import soul_jar
    from sim.scenario import load as load_sc
    corpse_at = (5266, 654)                                   # 6 route steps south of FLOOR
    sc = load_sc()
    sc["spawns"] = []
    sc["player"]["start"] = [FLOOR[0], FLOOR[1], 0]
    sc["loadout"]["items"].append({"serial": 0x6FFF0001, "graphic": soul_jar.EMPTY_JAR_GRAPHIC, "amount": 1,
                                   "kind": "soul_jar"})
    w = World(sc, seed=1)
    corpse = w.eerie_corpse(*corpse_at)
    loop, clock, store, patched = hurt_loop(w, w.player.hits_max)
    pull_check = loop.team.pull_check
    out = {}

    def defer():
        loop.team.pull_check = lambda s: "vampire bat in sight at 10 tiles: letting it come"
        try:
            if (pick := loop.soul_corpse(loop.state())) is not None:      # still deferred: nothing to walk to
                loop.fill_soul_jar(*pick)
        finally:
            loop.team.pull_check = pull_check
        return loop.soul_corpse(loop.state())
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            out["deferred"] = defer()
            loop.on_kill(loop.state(), 0x003331F4, {"name": "a vampire bat"})
            pick = loop.soul_corpse(loop.state())
            out["after a kill"] = pick[0] if pick else None
            out["deferred again"] = defer()
            w._teleport(w.player, (corpse_at[0] + 1, corpse_at[1], w.player.z))
            clock.advance(0.3)
            pick = loop.soul_corpse(loop.state(), walk=False)
            out["within reach"] = pick[0] if pick else None
    finally:
        store.close()
        simclock.uninstall(patched)
    check("the walk's hold defers the corpse", out["deferred"] is None and out["deferred again"] is None, str(out))
    check("...a kill since: the corpse is tried again", out["after a kill"] == corpse
          and "a kill since: trying it again" in buf.getvalue(), str(out))
    check("...deferred but within FILL_WALK_R (a fight on): tried at once", out["within reach"] == corpse, str(out))



def test_transfixed_walk():
    print("== transfixed on a retreat: the walk waits the hold out, then goes; no 'too many blocked moves' ==")
    # live 2026-10-20 19:23:23.04-34.15 (hunt-20261020-183232-1def): "The creature overpowers your will and transfixes
    # you!" as a retreat began at (5180, 680); 21 denied steps in 7 s, "ABORTED: too many blocked moves (21)", Logan
    # left on Maus 3 (alive). The hold ended 11.1 s after it began ("You are no longer hindered.")
    import loop_hunt
    from agent_link import Abort
    w = world_with([], seed=7, start=(5197, 682))
    loop, clock, store, patched = hurt_loop(w, 100)
    err, took, blocked = None, None, None
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            w.transfix_player(11.1)
            clock.advance(0.2)
            loop.state()
            t0 = loop_hunt.time.monotonic()
            b0 = loop.mover.blocked_count
            try:
                loop.mover.walk_to(lambda: (5190, 682), 0, "retreat", urgent=True, max_moves=21, max_route=21)
            except Abort as e:
                err = str(e)
            took = loop_hunt.time.monotonic() - t0
            blocked = loop.mover.blocked_count - b0
    finally:
        store.close()
        simclock.uninstall(patched)
    check("arrived after the hold (>= 10 s), no Abort, no blocked move counted",
          err is None and took >= 10 and blocked == 0 and sim_cheb(w.player.pos, (5190, 682)) == 0,
          f"err {err!r} took {took:.1f} s blocked {blocked} at {w.player.pos} denied {w.metrics['denied_steps']}")


def test_career_junctures():
    print("== a career's run reports its own junctures, not its earlier runs' (pets left behind counted once) ==")
    import tempfile
    import memory
    from sim import run as simrun
    # batch 252bed2 (144 x 20 min, seed 17): "runs with a pet left behind" 4 = 2 events; seed 70's (career 22, run 1)
    # counted again in seeds 101 and 132 (runs 2 and 3), whose own runs left none
    db = os.path.join(tempfile.mkdtemp(prefix="sim_test_"), "harness.db")
    m = memory.Memory(db)
    m.juncture("hunt", "pet_left_behind", "Pets not with us after the recall (gate): GraveRobber", "urgent", {})
    m.close()
    with contextlib.redirect_stdout(io.StringIO()):
        r = simrun.run_once(1, ["--vet-range", "5"], minutes=0.2, at_hunt=True, db=db)
    left = [j for j in r["junctures"] if j["kind"] == "pet_left_behind"]
    check("the earlier run's pet_left_behind not in this run's junctures", not left and r["outcome"] != "crashed",
          f"{left} {r['outcome']} {r.get('error')}")


def test_walk_failed_carries_on():
    print("== a walk in the hunt fails: carry on from there; after WALK_FAIL_MAX more, the leave home ==")
    # live 2026-10-20 19:23:30 (hunt-20261020-183232-1def): a retreat's "too many blocked moves" ended the run on
    # Maus 3; Logan and both pets died standing there by 19:30 with no runner
    import loop_hunt
    w = world_with([], seed=7, start=(5197, 682))
    loop, clock, store, patched = hurt_loop(w, 100)
    res, buf = None, io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            loop.args.crawl, loop.crawl = False, None
            loop.to_spot = lambda: None
            loop.fight_spot = (5197, 600)              # no route there: every "back to the spot" fails
            try:
                loop.hunt()
                res = "returned"
            except Exception as e:
                res = f"raised {type(e).__name__}: {e}"
    finally:
        store.close()
        simclock.uninstall(patched)
    fails = buf.getvalue().count("a walk failed:")
    check("each failed walk carried on, then the planned leave home: the run ends there, not in the dungeon",
          res == "returned" and fails == loop_hunt.WALK_FAIL_MAX + 1 and "LEAVING by the moongate" in buf.getvalue()
          and sim_cheb(w.player.pos, (5197, 682)) > 100, f"{res}; {fails} failed walks; at {w.player.pos}")

def _banshee_on_us(seed=3):
    """A banshee 6 tiles south of FLOOR in war mode on the player, standing where it is; the pets on Follow."""
    w = world_with([{"key": "banshee", "at": [FLOOR[0], FLOOR[1] + 6, 0]}], seed=seed)
    b = next(m for m in w.mobs.values() if m.key == "banshee")
    pl = w.player
    b.target, b.threat, b.next_step = pl.serial, {pl.serial: 50}, 1e9
    w._warm(b, True)
    casts = []
    orig = w._cast

    def cast(m, tgt, ranged):
        casts.append((round(w.t, 2), m.serial))
        orig(m, tgt, ranged)
    w._cast = cast
    return w, b, pl, casts


def _run_to_cast(w, b, limit=10.0):
    while b.cast is None and w.t < limit:
        w.run_until(w.t + 0.1)
    return b.cast is not None


def _trace_to_tmp(w, label="test"):
    import tempfile
    from sim import trace
    p = os.path.join(tempfile.mkdtemp(prefix="sim_trace_"), f"{label}.trace.jsonl.gz")
    return trace.Tracer(w, p, label=label, seed=0), p


def test_cast_then_flight():
    print("== a monster's spell: its cast animation, cast_s standing, the 0xC0 effect, the hit flight_s later ==")
    w, b, pl, casts = _banshee_on_us()
    hurts = _log_hurts(w)
    link, clock = link_for(w)
    clock.advance(8.0)
    link._pump()
    r = b.spec["ranged"]
    first = next((h[0] for h in hurts if h[1] is pl and h[2] == "spell"), None)
    start = casts[0][0] if casts else None
    anim = [e for e in link.events if e["ev"] == "animation" and e["serial"] == b.serial]
    eff = [e for e in link.events if e["ev"] == "effect" and e["source"] == b.serial and e["target"] == pl.serial]
    check("the first spell hit comes cast_s + flight_s after the cast began",
          first is not None and start is not None and first - start >= r["cast_s"][0] + r["flight_s"][0] - 1e-9,
          f"cast {start} hit {first} (cast_s {r['cast_s']}, flight_s {r['flight_s']})")
    check("...its cast animation and (with an effect) the moving effect from it to us reached the client",
          anim and anim[0]["action"] == r["cast_action"] and (bool(eff) if r.get("effect") else not eff),
          f"{len(anim)} animations, {len(eff)} effects, effect {r.get('effect')}")


def test_cast_lost_out_of_range():
    print("== a cast whose target is out of range at its release is lost; the next one every_s later ==")
    from sim import trace
    w, b, pl, casts = _banshee_on_us()
    hurts = _log_hurts(w)
    tracer, p = _trace_to_tmp(w)
    ok = _run_to_cast(w, b)
    t0 = w.t
    w._teleport(pl, w.sc["recall"]["rows"][0]["at"])
    w.run_until(w.t + 6.0)
    tracer.close({})
    lost = [e for e in trace.read(p) if e["k"] == "e" and e["e"] == "cast_lost" and e["by"] == b.serial]
    spells = [h for h in hurts if h[1] is pl and h[2] == "spell" and h[0] >= t0]
    every = b.spec["ranged"]["every_s"]
    check("no spell hit after the teleport, the cast lost as out of range, the next one >= 0.85 every_s later",
          ok and not spells and lost and lost[0]["why"] == "range"
          and b.next_swing >= lost[0]["t"] + 0.85 * every - 0.01,
          f"cast {ok} at {t0}, hits after {spells}, lost {lost[:1]}, next swing {b.next_swing:.2f}")


def test_cast_not_interrupted():
    print("== damage on a caster doesn't break its cast: the spell still lands ==")
    w, b, pl, casts = _banshee_on_us()
    b.cd["dematerialize"] = 1e9                # its own reply to a hit is another matter (a cancel as 'hidden')
    hurts = _log_hurts(w)
    ok = _run_to_cast(w, b)
    end = b.cast[0] if ok else None
    w.hurt(b, 1, pl)
    still = b.cast is not None
    w.run_until(w.t + 4.0)
    spells = [h for h in hurts if h[1] is pl and h[2] == "spell"]
    check("the cast runs on through the hit and its spell lands on us", ok and still and spells and spells[0][0] >= end,
          f"cast until {end}, still casting after the hit {still}, spell hits {[(h[0], h[3]) for h in spells]}")


def test_trace_roundtrip():
    print("== a trace rebuilds the world's records at its end; a key every 10 s; its hurt events are the world's ==")
    from sim import trace
    w, b, pl, casts = _banshee_on_us()
    b.spec = {**b.spec, "ranged": {**b.spec["ranged"], "damage": [1, 2]}}   # no death in 30 s
    hurts = _log_hurts(w)
    tracer, p = _trace_to_tmp(w)
    w.run_until(30.0)
    last = (tracer.mobs, tracer.w, tracer.items)
    tracer.close({})
    lines = trace.read(p)
    st = trace.state_at(lines, w.t)
    keys = [ln["t"] for ln in lines if ln["k"] == "key"]
    gaps = [round(b2 - a, 2) for a, b2 in zip(keys, keys[1:])]
    evs = [(e["t"], e["v"], e["kind"], e["n"]) for e in lines if e["k"] == "e" and e["e"] == "hurt"]
    world = [(t, v.serial, kind, n) for t, v, kind, n in hurts if n > 0]
    check("state_at(end) is the tracer's last records", (st["mobs"], st["w"], st["items"]) == last,
          f"{len(st['mobs'])} mobiles, {len(st['items'])} items")
    check("...a key line every 10 s", len(keys) >= 4 and all(abs(g - 10.0) <= 0.11 for g in gaps), f"keys at {keys}")
    check("...its hurt events are the world's hurts", evs == world and len(evs) > 0, f"{len(evs)} vs {len(world)}")


def test_trace_no_side_effects():
    print("== a traced run is the untraced one (the tracer draws nothing, sees nothing, sends nothing) ==")
    import tempfile
    from sim import run as simrun
    p = os.path.join(tempfile.mkdtemp(prefix="sim_trace_"), "t.trace.jsonl.gz")
    keys = ("kills", "player_damage_taken", "deaths", "sim_s")
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        a = simrun.run_once(3, [], minutes=3, at_hunt=True)
        b = simrun.run_once(3, [], minutes=3, at_hunt=True, trace=p)
    check("same kills, damage on us, deaths and game time with and without the trace",
          {k: a[k] for k in keys} == {k: b[k] for k in keys} and os.path.getsize(p) > 0,
          f"{ {k: a[k] for k in keys} } vs { {k: b[k] for k in keys} }")



def main():
    if pathfind.Walkers().get(0) is None:
        print("SKIP: no map files (install dir)")
        return 0
    for t in (test_clock, test_geometry, test_shove, test_aggro, test_aggro_rules, test_bard_and_vet, test_specials,
              test_hunt,
              test_standoff, test_fire, test_pet_res, test_retreat_sight, test_spacing, test_vet_tile_off_tank,
              test_stairs, test_backspawn,
              test_area_first, test_leave_called_off, test_leave_from_far, test_gate_dash, test_gate_after_vet,
              test_bard_sight, test_rope_sight, test_red_flight, test_red_out_of_view,
              test_rope_cancel_attacked, test_rope_combat_cooldown, test_rope_ranged, test_red_caster_sight,
              test_red_hamstring, test_red_breaks_recall, test_red_follows_teleporter, test_gold_banked,
              test_ledge_attacker,
              test_caster_at_12, test_blightmare_reach, test_cover, test_to_pet_not_into_shot, test_blightmare_first_sight,
              test_blightmare_awake_patrol, test_guard_after_dropped_kill,
              test_purger_back_off, test_purger_no_ping_pong, test_patrol_pull, test_vet_channel_not_ours,
              test_restart_inside, test_walk_in_through_neighbour,
              test_instrument,
              test_standoff_sight,
              test_idle_pet, test_pet_shot, test_countess_held_pet, test_countess_reguard, test_loot_lift_gap,
              test_loot_off_area_hitter, test_on_another_player, test_kill_unseen_once,
              test_patrol_off_passed_over_area_hitter,
              test_others_corpse_not_looted,
              test_area_peace, test_audit_mechanics, test_blink_stepoff,
              test_heal_in_place,
              test_songs,
              test_pull_budget, test_hold_first_sight, test_unseen_target_passed_over, test_ledge_target_not_picked,
              test_never_aggro_walk_out, test_crawl_seeks_in_view, test_rope,
              test_rest_hurt_pet, test_walk_in, test_tele_dash, test_walk_in_red_first_level,
              test_walk_in_red_second_level, test_escape_after_walk_in_error, test_go_home,
              test_no_escape_on_death_or_stop, test_soul_jar, test_soul_jar_guards, test_soul_jar_defer,
              test_transfixed_walk, test_walk_failed_carries_on, test_career_junctures,
              test_cast_then_flight, test_cast_lost_out_of_range, test_cast_not_interrupted, test_trace_roundtrip,
              test_trace_no_side_effects):
        t()
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILED: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

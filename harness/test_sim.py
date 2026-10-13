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
from sim.world import World  # noqa: E402

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
    clock.advance(3.0)
    m = next(m for m in open_.mobs.values() if m.kind == "monster")
    pets = {p.serial for p in open_.pets()}
    check("one 6 tiles off in the open aggroes, on a guarding pet", m.target in pets, str(m.target))
    clock.advance(10.0)
    check("the pets fight it ('-N' over it) and it fights back", m.hits < m.hits_max
          and sum(open_.metrics["pet_damage_taken"].values()) > 0, f"{m.hits}/{m.hits_max}")


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
    for t in (test_clock, test_geometry, test_aggro, test_bard_and_vet, test_hunt, test_rest_hurt_pet):
        t()
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILED: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

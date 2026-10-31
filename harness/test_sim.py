"""Tests of the Mausoleum simulator (harness/sim/, docs/SIM.md).

The world on the real map (walls, sight), its mechanics as the runner meets them through SimLink and
the client's world model, and an end-to-end hunt of the unmodified --pets runner on virtual time.
Skips when the install's map files are absent (another computer).

Run: python harness/test_sim.py
"""
import collections
import contextlib
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import actions  # noqa: E402
import oxygen  # noqa: E402
import pathfind  # noqa: E402
import tamer  # noqa: E402
from sim import clock as simclock  # noqa: E402
from sim.link import SimLink  # noqa: E402
from sim.scenario import bard_chance, bard_eff, bard_secs, load, play  # noqa: E402
from sim.world import Mob, World, cheb as sim_cheb  # noqa: E402

FAILURES = []
WALL_FROM, WALL_TO = (5266, 648, 0), (5265, 657, 0)     # live: "Target cannot be seen." (a stone wall at 5265,656)
SEEN_FROM = (5268, 661, 0)                              # live: Discordance at the same corpse purger, answered
FLOOR = (5266, 648)                                     # walkable; south of it is floor, the wall is at 5265,656


def check(name, cond, extra=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        FAILURES.append(name)


def world_with(spawns, seed=1, start=FLOOR, others=False, team=None, bestiary=None):
    sc = load()
    if team or bestiary:
        from sim import pets
        pets.select(sc, team, bestiary)
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


def test_shy_wake():
    print("== a shy monster's wake (docs/NOTES.md \"Shooters' and casters' first target\"): no sooner than 1.6 s after one "
          "of us comes into its range and sight; then the nearest of us in its sight, not the first seen; near ties "
          "2:1 to Logan when he is the slightly nearer ==")
    sg = (5266, 654, 0)                                        # a shadowguard (aggro.shy); Logan at (5266, 648): 6 off

    def wake(seed, pets_at, move=None):
        w = world_with([{"key": "shadowguard", "at": list(sg)}], seed=seed)
        m = next(m for m in w.mobs.values() if m.kind == "monster")
        m.spec = {**m.spec, "aggro_range": 7}
        for p, at in zip(w.pets(), pets_at):
            p.x, p.y = at
            p.mode = "stay"
        link, clock = link_for(w)
        for _ in range(80):
            clock.advance(0.1)
            if move and m.seen_onset is not None and w.t - m.seen_onset >= move[0]:
                for p in w.pets():
                    p.x, p.y = move[1]
                move = None
            if m.target is not None:
                break
        return w, m
    far = ((5266, 637), (5267, 637))                           # 17 off: out of its sight's 14
    delays, took = [], collections.Counter()
    for seed in range(1, 41):
        w, m = wake(seed, far)
        r = m.wake_rec
        delays.append(round(r["flip"] - r["on"], 2) if r else None)
        took["logan" if m.target == w.player.serial else "other"] += 1
    d = sorted(x for x in delays if x is not None)
    check("Logan alone in its sight: taken every time, the wake 1.6 s or more after the onset (median ~2.2)",
          len(d) == 40 and took["logan"] == 40 and d[0] >= 1.6 and 1.9 <= d[20] <= 2.6,
          f"min {d[:1]} median {d[20:21]} max {d[-1:]} took {dict(took)}")
    first = collections.Counter()
    for seed in range(1, 21):                                  # seen first, then a pet 3 off by the flip
        w, m = wake(seed, far, move=(0.8, (5266, 651)))
        first["logan" if m.target == w.player.serial else "pet"] += 1
    check("Logan seen first, a pet the nearest at the flip (3 against 6): the pet every time", first["pet"] == 20,
          str(dict(first)))
    tie = {}
    for name, pets_at in (("Logan nearer", ((5261, 650), (5271, 650))),     # the pets 6.40 against Logan's 6.0
                          ("a pet nearer", ((5262, 650), (5271, 650)))):    # a pet 5.66
        c = collections.Counter()
        for seed in range(1, 91):
            w, m = wake(seed, pets_at)
            c["logan" if m.target == w.player.serial else "pet"] += 1
        tie[name] = c["logan"]
    check("near ties of 90: Logan 6.0 against the pets 6.40, Logan about 2 in 3 (TIE_LOGAN_P); against a pet 5.66, "
          "about 1 in 2 (TIE_PET_P)", 48 <= tie["Logan nearer"] <= 72 and 33 <= tie["a pet nearer"] <= 57, str(tie))


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
        p.x, p.y = w.player.x + w.sc["vet"]["range"] + 2, w.player.y   # beyond the supplies' reach (the codex's 5)
    gb.hits = gb.hits_max * 0.5
    w.player.hits = w.player.hits_max
    clock.advance(1.0)
    mark = len(link.events)
    link.send(actions.dclick(vet))
    until((tamer.TEXT_VET_FINISH, tamer.TEXT_VET_FAR))
    check("a pet 2 tiles beyond the supplies' reach at the finish: 'You are too far away ...'",
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


def test_no_spacing_after_fight():
    print("== --vet-range 5, the fight over (the wolf dead): no step off the pets coming back to us ==")
    import tempfile
    import loop_hunt
    import memory
    from sim import run as simrun
    # user 2026-10-24: "stepping off the tank repeatedly when there are no monsters around"; until then spacing went on
    # for FIGHT_LINGER_S 8 s after the last monster, a step every SPACING_REPEAT_S as the guarding pets followed
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
                                             + ["--memory", db, "--seed", "1", "--human", "off", "--vet-range", "5"])
    patched = simclock.install(clock)
    store = memory.Memory(db)
    steps, nearest = 0, 99
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            loop = loop_hunt.HuntLoop(link, store, args)
            loop.team.setup(loop.state())
            for _ in range(10):
                clock.advance(0.5)
            during = loop.team.spacing(loop.state())
            w.die(m, gb)
            for _ in range(12):                       # 6 s: within the old FIGHT_LINGER_S, the pets back beside us
                clock.advance(0.5)
                nearest = min(nearest, min(cheb_(w.player, p) for p in w.pets()))
                steps += bool(loop.team.spacing(loop.state()))
    finally:
        store.close()
        simclock.uninstall(patched)
    check("a step off the tank in the fight; none once it is over, with a pet within 2 of us",
          during and steps == 0 and nearest <= tamer.SPACING_R - 1, f"during {during} steps after {steps} "
                                                                     f"nearest pet {nearest}")


def test_calm_heal():
    print("== no fight on: a pet a few hits short doesn't hold the patrol nor get the vet supplies; one below "
          "CALM_HEAL_AT does ==")
    import tempfile
    import loop_hunt
    import memory
    from sim import run as simrun
    # user 2026-10-24: "waiting to heal before moving on when a pet is just missing a few HP"; until then any pet below
    # --pet-heal-at 0.95 started a channel after the fight and busy_healing held the patrol through it
    w = world_with([], seed=7, start=(5211, 724))
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    link, clock = link_for(w)
    db = os.path.join(tempfile.mkdtemp(prefix="sim_test_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, [], 10, at_hunt=True)
                                             + ["--memory", db, "--seed", "1", "--human", "off", "--vet-range", "5"])
    patched = simclock.install(clock)
    store = memory.Memory(db)
    out = {}
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            loop = loop_hunt.HuntLoop(link, store, args)
            loop.team.setup(loop.state())
            for frac in (0.9, 0.7):
                gb.hits = gb.hits_max
                w.hurt(gb, gb.hits_max * (1 - frac), None)
                clock.advance(1.0)
                st = loop.state()
                out[frac] = (gb.pct(), loop.team.busy_healing(st), loop.team.vet_needed(st, loop.team.pets(st)) is not None,
                             loop.team.vet_needed(st, loop.team.pets(st), calm=True) is not None)
    finally:
        store.close()
        simclock.uninstall(patched)
    a, b = out[0.9], out[0.7]
    check("at ~90 %: no hold, no vet when calm (a fight's threshold still calls for it); at ~70 %: hold and vet",
          a[0] > tamer.CALM_HEAL_AT * 100 and not a[1] and a[2] and not a[3]
          and b[0] < tamer.CALM_HEAL_AT * 100 and b[1] and b[3], str(out))


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
        gb.hits = int(gb.hits_max * 0.4)       # below tamer.TO_PET_CASTER_AT: the daemon is a caster out of our sight
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


def test_boss_room_door():
    print("== Maus 3's mini-boss room door: a walk onto it as a stand tile steps back out and ends, the way out known "
          "or not ==")
    # live 2026-10-24 12:22:36 (hunt-20261024-112241-b911): "keeping off the tank: (5242, 612)", the step onto it put
    # Logan in the mini-boss room at (5242, 587); the walk took the tile as its goal (aimed), so no step back, and he
    # stood in the room under the custodian until the user stepped him off the arrival and back on, out at (5243, 612)
    import tempfile
    import loop_hunt
    import memory
    from sim import run as simrun
    door, room = (5242, 612), (5242, 587)
    for known_out in (True, False):
        w = world_with([], seed=7, start=(5242, 615))
        link, clock = link_for(w)
        db = os.path.join(tempfile.mkdtemp(prefix="sim_test_"), "harness.db")
        args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, [], 10, at_hunt=True)
                                                 + ["--memory", db, "--seed", "1", "--human", "off"])
        patched = simclock.install(clock)
        store = memory.Memory(db)
        err, out = None, io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                loop = loop_hunt.HuntLoop(link, store, args)
                mv = loop.mover
                seeded = {door, room} <= mv.teleporter_tiles(0)
                mv.teleporter_tiles(0).discard(door)                # as before the seed: the door unknown
                mv._tele_to[0].pop(door, None)
                if not known_out:
                    mv.teleporter_tiles(0).discard(room)
                    mv._tele_to[0].pop(room, None)
                n0 = mv.teleports
                try:
                    mv.walk_to(lambda: door, 0, "off the tank", max_moves=12)
                except Exception as e:                             # noqa: BLE001 - the end position is the check
                    err = str(e)
                n = mv.teleports - n0
        finally:
            store.close()
            simclock.uninstall(patched)
        how = "known" if known_out else "unknown (stepped off the arrival and back on)"
        check(f"way out {how}: in and straight back out to Maus 3, the walk ended at the door", seeded and n == 2
              and sim_cheb(w.player.pos, door) <= 2 and err is not None and "is a teleporter" in err,
              f"at {w.player.pos}, {n} teleports, {err!r}; "
              + " | ".join(ln for ln in out.getvalue().splitlines() if "teleport" in ln or "back" in ln)[-500:])


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
    w.hurt = lambda victim, amount, by, kind="melee", song_pets=True, **kw: (
        hurts.append((victim.name, kind, round(cheb(victim.pos, gb.pos)))), orig(victim, amount, by, kind, song_pets, **kw))
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


def test_standoff_ties():
    print("== stand-off tiles that rank alike: the fewest steps, then the lowest tile (x, y, z), not tiles_within's walk "
          "order ==")
    # fbf76ab (2026-10-28) left the tile out of standoff_tile's ranking, so a tie went to tiles_within's walk order (north
    # first) in every stand-off; Tidal Tomb level 2 in the simulator, 768 runs: pet deaths 123 with the lowest tile, 175
    # in the walk order (docs/SIM.md "Tidal Tomb: Logan in the bedrock's spray")
    import aggro
    w = world_with([], seed=7, start=(5268, 648))
    loop, clock, store, patched = hurt_loop(w, 100)
    score, first = aggro.View.stand_score, aggro.View.first_on_us
    aggro.View.stand_score = lambda self, t: 0.0               # no idle eyes anywhere: only the steps and the tile differ
    aggro.View.first_on_us = lambda self, t, margin=1: []
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            clock.advance(0.3)
            st = loop.state()
            x, y = w.player.pos[:2]
            zone = {(x + 1, y): 2}                             # an area hitter a tile east: the tiles west of us clear
            to, stay = loop.team.standoff_tile(st, zone)
            clear = [(k, t) for t, k in tamer.Team.tiles_within(loop.team.view(st), tamer.STANDOFF_MOVES).items()
                     if tamer.ClearOf(zone)(t)]
    finally:
        aggro.View.stand_score, aggro.View.first_on_us = score, first
        store.close()
        simclock.uninstall(patched)
    want, walk_first = min(clear)[1], min(clear, key=lambda c: c[0])[1]
    check("the clear tile the fewest steps off with the lowest (x, y, z), not the walk order's first",
          stay is None and to == want and want != walk_first,
          f"to {to} stay {stay!r}, lowest {want}, walk order's first {walk_first}")


def test_pet_death_readout():
    print("== a pet's death in the run summary (pet_deaths): who hurt it and what healed it in its last 15 s, where it "
          "and Logan stood, the vet supplies' reach, who fought it, what the runner was doing ==")
    w = world_with([{"key": "nightstalker", "at": [5268, 655, 0]}], seed=7, start=(5268, 648))
    ns = next(o for o in w.mobs.values() if o.kind == "monster")
    link, clock = link_for(w)
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    w._place(gb, (5268, 654, 0), d=0)
    ns.target = gb.serial
    link.intent("Veterinary supplies: GraveRobber at 50%", "vet")
    gb.hits = gb.hits_max // 2
    w.heal(gb, 10, "vet")
    for _ in range(50):
        if gb.ghost:
            break
        w.hurt(gb, gb.hits_max, ns)
    d = w.metrics["pet_deaths"][-1] if w.metrics["pet_deaths"] else {}
    check("GraveRobber's death: the nightstalker's damage, the vet heal, Logan 6 off and the reach by the scenario's vet "
          "range, the nightstalker fighting it, the intent",
          len(w.metrics["pet_deaths"]) == 1 and d["pet"] == "GraveRobber" and d["last_15s"].get("nightstalker", 0) > 0
          and d["healed_15s"].get("vet", 0) > 0 and d["dist"] == 6 and d["vet_reach"] == (6 <= w.sc["vet"]["range"])
          and d["fighting"] == ["nightstalker"] and d["doing_15s"] == {"vet": 1}
          and d["doing"] == "Veterinary supplies: GraveRobber at 50%" and len(d["pets"]) == len(w.pets()) - 1,
          str(d))


def test_area_peace():
    print("== two monsters on us within 3: Peacemaking on ourselves, the area pacified for 2 s ==")
    # user 2026-10-15: "in a jam you can peace the area around you by activating Peacemaking and targeting yourself";
    # live 20261015_171928 17:22:01 the user's own answered "You play successfully, briefly pacifying ..."
    w = world_with([{"key": "shade wolf", "at": [5268, 649, 0]}, {"key": "shade wolf", "at": [5269, 648, 0]}],
                   seed=7, start=(5268, 648))
    wolves = [m for m in w.mobs.values() if m.kind == "monster"]
    loop, clock, store, patched = hurt_loop(w, 100)
    for p, x in zip(w.pets(), (5262, 5261)):
        p.x, p.y, p.mode = x, 648, "stay"                  # out of the wolves' way: both come on us (the test's jam)
    for k in w.sc["bard"]["d"]["median"]:
        w.sc["bard"]["d"][k]["shade wolf"] = -100.0             # a sure check, to test the action itself
    for m in wolves:
        m.spec = {**m.spec, "difficulty": 20.0}            # and a rare Barding Break
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


def test_bard_provoke():
    print("== two monsters on the pets, the target discorded: Provocation of the other onto it, both cursors answered ==")
    # the simulator's link delivers the server's second cursor ("Whom do you wish them to attack?") inside the send of
    # the first answer; Team.use took the second cursor's mark after that send, so every provocation ended "no second
    # target" (0 in every batch since the trip compare in docs/SIM.md)
    w = world_with([{"key": "shade wolf", "at": [5268, 651, 0]}, {"key": "shade wolf", "at": [5269, 651, 0]}],
                   seed=7, start=(5268, 648))
    wolves = [m for m in w.mobs.values() if m.kind == "monster"]
    loop, clock, store, patched = hurt_loop(w, 100)
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    gb.hits = gb.hits_max = 10 ** 6
    for m, dx in zip(wolves, (0, 1)):
        m.x, m.y = gb.x + dx, gb.y + 1                      # both beside the gravebug, in our sight
        m.next_step = 1e9
        m.hits = m.hits_max = 10 ** 6
        m.spec = {**m.spec, "difficulty": 20.0}            # a rare Barding Break
        m.target, m.threat = gb.serial, {gb.serial: 1e9}
        w._warm(m, True)
    for k in w.sc["bard"]["d"]["median"]:
        w.sc["bard"]["d"][k]["shade wolf"] = -100.0         # a sure check, to test the action itself
    first, other = wolves
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            clock.advance(0.5)
            for m in wolves:
                w.hurt(gb, 5, m)                            # the gravebug hurt by both: the wolves are in the fight
            clock.advance(0.5)
            st = loop.state()
            loop.team.effects[first.serial] = {"discord": tamer.time.monotonic() + 1e6}   # discord done: provoke next
            fighting = loop.team.others(st, first.serial)
            loop.team.bard(st, first.serial, st["world"]["mobiles"][f"0x{first.serial:08X}"])
            clock.advance(1.0)
    finally:
        store.close()
        simclock.uninstall(patched)
    bard = w.metrics["bard"]
    check("the other wolf is in the fight; the provocation is answered and incites it onto the target",
          fighting == [other.serial] and bard.get("provoke_ok") == 1 and other.provoke
          and other.provoke[0] == first.serial, f"others {fighting} bard {bard} provoke {other.provoke}")


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
                if mark is None and w.t >= freed:
                    mark = (w.metrics["guard_commands"], w.t)    # before the runner's tick: a guard in it counts
                loop.tick()
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


def test_ally_left_alone():
    print("== a monster an ally (green) is fighting in sight: no 'All Kill' at it, the pets never hit it, its corpse "
          "not opened ==")
    # user 2026-10-28: "detect when a player already killing a mob is an ally (member of DTF or an allied guild, a
    # 'green' player) and not attack that mob ... we can loot the corpse, but we shouldn't". The scene of
    # test_on_another_player with Lord Phaze green to us (notoriety 2): before the change, 'All Kill' within 6 s
    w = world_with([{"key": "tormented soul", "at": [5236, 677, 0]}], seed=7, start=(5243, 674), others=True)
    m = next(m for m in w.mobs.values() if m.kind == "monster")
    m.hits = m.hits_max = 3000
    phaze = w.add_other(5236, 676, w.stand_z(5236, 676, w.player.z), name="Lord Phaze", stay=1e6, dps=100.0, noto=2)
    m.target, m.threat, phaze.target = phaze.serial, {phaze.serial: 1e9}, m.serial
    w._warm(m, True)
    loop, clock, store, patched = hurt_loop(w, 100)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            after = 0
            for _ in range(120):
                clock.advance(0.5)
                for p in w.pets():
                    if p.target is None:
                        w._warm(p, True)    # live: guarding, in war mode at Logan's side between fights
                loop.tick()
                after += m.serial not in w.mobs or not m.alive
                if after > 20:
                    break
    finally:
        store.close()
        simclock.uninstall(patched)
    ours = sum(v for s, v in m.damage_by.items() if s in {p.serial for p in w.pets()} | {w.player.serial})
    corpse = next((it for it in w.items.values() if it.get("kind") == "corpse" and it["owner"] == m.serial), None)
    check("Lord Phaze killed it alone: no 'All Kill' at it, no damage of ours, its (grey) corpse never opened",
          not m.alive and w.metrics["kill_on_allies"] == 0 and ours == 0 and corpse is not None and corpse["ally"]
          and w.metrics["ally_corpse_opens"] == 0,
          f"alive {m.alive} kill commands {w.metrics['kill_on_allies']} ours {ours} corpse "
          f"{corpse and corpse['noto']} opens {w.metrics['ally_corpse_opens']}")


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


def test_theirs_passed_over():
    print("== a monster on another player: 'All Kill' while it is near full hits; passed over once they have it below "
          "THEIRS_BELOW (the most damage takes the loot) ==")
    # simulator 2026-10-24: 63 of 63 kills after "is fighting <player>: 'All Kill' at it" left a blue corpse; live 12 % of
    # the credited kills were blue (docs/SIM.md "Other players")
    from sim import packets as P
    out = {}
    for frac in (1.0, 0.5):
        w, m = daemon_on_elsa((5242, 694))
        m.hits = m.hits_max * frac
        loop, clock, store, patched = hurt_loop(w, 100)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                w.send(P.hits(m.serial, m.pct(), 100))
                for _ in range(10):
                    clock.advance(0.5)
                    loop.team.fight(m.serial)
                out[frac] = (w.metrics["kill_on_others"], loop.skip.get(m.serial, 0) > tamer.time.monotonic())
        finally:
            store.close()
            simclock.uninstall(patched)
    check("full hits: 'All Kill' at it; at half: passed over, no 'All Kill'",
          out[1.0][0] >= 1 and not out[1.0][1] and out[0.5] == (0, True), str(out))


def test_theirs_after_kill_sent():
    print("== a monster on another player sent at near full hits: it falls below THEIRS_BELOW as the pets go; they stay "
          "on it ==")
    # live 2026-10-25 16:33:38-43 (hunt-20261025-152542-5ab5): "a nightstalker 0x006A240B is fighting A: 'All Kill' at
    # it" at 86 % (capture 0xA1), "passing over ... A has it at 52 %" 4 s later, the pets called off; 25 of 34 such
    # "All Kill"s dropped that way in two runs, 11 of the 21 corpses seen ours all the same
    from sim import packets as P
    w, m = daemon_on_elsa((5242, 694))
    loop, clock, store, patched = hurt_loop(w, 100)
    try:
        with contextlib.redirect_stdout(io.StringIO()) as out:
            w.send(P.hits(m.serial, m.pct(), 100))
            clock.advance(0.5)
            loop.team.fight(m.serial)
            sent = w.metrics["kill_on_others"]
            m.hits = m.hits_max * 0.5                     # Elsa's (and the pets') blows on the way
            w.send(P.hits(m.serial, m.pct(), 100))
            for _ in range(6):                            # 3 s, inside KILL_REPEAT_S
                clock.advance(0.5)
                loop.team.fight(m.serial)
            held = loop.skip.get(m.serial, 0) <= tamer.time.monotonic() and "passing over" not in out.getvalue()
            on_it = all(p.mode == "kill" and p.target == m.serial for p in w.pets() if p.alive)
    finally:
        store.close()
        simclock.uninstall(patched)
    check("'All Kill' at full hits; at half 3 s later: not passed over, the pets still on it",
          sent >= 1 and held and on_it, f"sent {sent}, held {held}, pets on it {on_it}")


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


def test_walk_past_purger_on_another():
    print("== an awake corpse purger on another player's pets in the corridor: the patrol walks past it; no cover, "
          "no 'GraveRobber is fighting' ==")
    # live 2026-10-24 19:01:21-19:02:07 (hunt-20261024-185409-49f0): Gix's boreal faerie wyrm and colossal blade on a
    # purger at (5267, 653), passed over at 19:01:08; "patrol to (5243, 716)" south past it stopped in its shot
    # ("GraveRobber is fighting"), "cover: the corpse purger shoots us here; to (5267, 648)", the patrol again: nine
    # rounds in 46 s. User: "We can just walk past when one is fighting someone else"
    import crawl
    import threats
    import time
    w = world_with([{"key": "corpse purger", "at": [5267, 653, 0]}], seed=7, start=(5267, 648), others=True)
    m = next(m for m in w.mobs.values() if m.kind == "monster")
    m.hits = m.hits_max = 10 ** 6
    other = w.add_other(5266, 654, w.stand_z(5266, 654, w.player.z), name="Gix of Phyrexia", stay=1e6, dps=1.0)
    m.target, m.threat, other.target = other.serial, {other.serial: 1e9}, m.serial
    w._warm(m, True)
    loop, clock, store, patched = hurt_loop(w, 100)
    south = []
    try:
        with contextlib.redirect_stdout(io.StringIO()) as out:
            loop.crawl = c = crawl.Crawl(loop, crawl.load_prior(store.con, loop.spot, loop.args.crawl_depth_risk))
            c.setup(loop.state())
            c.unlocked = 99
            loop.skip[m.serial] = time.monotonic() + 1e6          # passed over, as live
            clock.advance(1.0)
            # guarding pets keep war mode on between fights live (f64 on GraveRobber all through 19:01:18-50); the
            # simulator drops it without a target, so the state is set as live: beside the fight, in war mode
            st = loop.state()
            gbm = st["world"]["mobiles"][f"0x{next(p for p in w.pets() if p.name == 'GraveRobber').serial:08X}"]
            gbm["x"], gbm["y"] = 5267, 652
            gbm["flags"] = (gbm.get("flags") or 0) | threats.FLAG_WARMODE
            busy = loop.pets_busy(st)
            c.wp = min(range(len(c.wps)), key=lambda i: sim_cheb(c.wps[i], (5243, 716)))
            for _ in range(80):                           # 40 s
                clock.advance(0.5)
                loop.tick()
                south.append(w.player.pos[1])
            covers = out.getvalue().count("cover: the corpse purger shoots us here")
    finally:
        store.close()
        simclock.uninstall(patched)
    check("40 s: no cover, no wait out of its shot, the patrol on south past it", covers == 0
          and "waiting out of it" not in out.getvalue() and max(south) >= 700,
          f"covers {covers}, southmost y {max(south) if south else None}")
    check("...a guarding pet beside it isn't 'fighting' it (it is on Gix's)", busy is None, str(busy))


def test_pets_stuck_off_fight():
    print("== the pets standing locked on a monster on someone else's side, out of their reach: 'All Follow Me', not a "
          "rejoin/patrol ping-pong ==")
    # live 2026-10-24 21:40:53-21:41:31 (hunt-20261024-211218-bab0): 'All Kill' at a reanimated daemon at (5237, 701) on
    # another player's giant swamp beetle, the target dropped, 'All Guard Me'; the three pets stood 34 s in war mode at
    # (5243, 692), 9-10 off it, with no attack of their own (capture replay), and the patrol (stopped "GirthyEarthy at
    # 9"), the reposition out of its reach and "back to GirthyEarthy" went round seven times in 45 s. 'All Guard Me'
    # mid-fight doesn't call them off (docs/NOTES.md); 'All Follow Me' does (the simulator's command rules)
    import crawl
    w = world_with([{"key": "reanimated daemon", "at": [5237, 701, 0]}], seed=7, start=(5243, 691), others=True)
    m = next(m for m in w.mobs.values() if m.kind == "monster")
    m.hits = m.hits_max = 10 ** 6
    other = w.add_other(5236, 702, w.stand_z(5236, 702, w.player.z), name="Gix of Phyrexia", stay=1e6, dps=1.0)
    m.target, m.threat, other.target = other.serial, {other.serial: 1e9}, m.serial
    w._warm(m, True)
    loop, clock, store, patched = hurt_loop(w, 100)
    stuck = {p.serial for p in w.pets()}          # standing locked on it, out of their reach, until a command calls
    pet_step = w._pet                             # them off: in war mode, no step, no attack (the capture replay)

    def locked(p):
        if p.mode == "follow":
            stuck.discard(p.serial)
        if p.serial not in stuck:
            return pet_step(p)
        p.target = None
        w._warm(p, True)
    w._pet = locked
    rejoins = follows = 0
    try:
        with contextlib.redirect_stdout(io.StringIO()) as out:
            loop.crawl = c = crawl.Crawl(loop, crawl.load_prior(store.con, loop.spot, loop.args.crawl_depth_risk))
            c.setup(loop.state())
            c.unlocked = 99
            c.wp = min(range(len(c.wps)), key=lambda i: sim_cheb(c.wps[i], (5231, 722)))
            now = tamer.time.monotonic()                          # the simulator's clock
            loop.skip[m.serial] = now + 1e6                       # passed over, as live
            loop.team.guard_needed, loop.team.guard_t = False, now   # 'All Guard Me' just said
            f0, g0 = w.metrics["follow_commands"], w.metrics["guard_commands"]
            for _ in range(120):                                  # 60 s
                clock.advance(0.5)
                loop.tick()
            follows = w.metrics["follow_commands"] - f0
            guards = w.metrics["guard_commands"] - g0
            rejoins = out.getvalue().count("back to the pets: route")
            # the patrol reaches its waypoint: on its tile, or in sight within crawl.SEEN_R (Crawl.interrupt's stop)
            on = any(s in out.getvalue() for s in ("patrol to (5231, 722): arrived",
                                                   "patrol to (5231, 722): stopped (looked at it from"))
    finally:
        store.close()
        simclock.uninstall(patched)
    far = max(sim_cheb(p.pos, w.player.pos) for p in w.pets())
    modes = sorted({p.mode for p in w.pets()})
    check("60 s: one walk back to the stuck pets, then 'All Follow Me'; they come, the patrol goes on, 'All Guard Me'",
          rejoins <= 1 and follows == 1 and not stuck and on and far <= 8 and guards >= 1 and modes == ["guard"],
          f"walks back {rejoins}, 'All Follow Me' {follows}, still stuck {len(stuck)}, patrol on {on}, pets {far} off, "
          f"'All Guard Me' {guards}, modes {modes}")


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
    keep = tamer.THEIRS_BELOW
    tamer.THEIRS_BELOW = 0.0       # the pets go at it however hurt (test_theirs_passed_over has the pass-over)
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
        tamer.THEIRS_BELOW = keep
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
    print("== the instrument last played is gone: the prompt answered with one from the pack (a lap harp, a psaltery, a "
          "shawm); none: skills off, and no recall in ==")
    # live 20261015_171928 after the 18:40 death: 54 "What instrument shall you play?" answered with the
    # skill's target ("That is not a musical instrument."), every bard skill and song lost for two runs. Live
    # 2026-10-25 17:24:23 Logan's durable psaltery (0x4E2A, not in INSTRUMENT_GRAPHICS then) broke; the visit went on
    # without bard skills, and nothing kept a run from recalling in without one
    import loop_hunt
    out = {}
    for spare in (0x0EB2, 0x4E2A, 0x4E2E, None):      # the shawm: live 2026-10-26 18:41 refused, his only one
        w = world_with([{"key": "shade wolf", "at": [5268, 648, 0]}], seed=7)
        m = next(m for m in w.mobs.values() if m.kind == "monster")
        lute = w.played
        del w.items[lute]
        if spare is not None:
            w.items[0x416ACB89] = {"graphic": spare, "amount": 1, "container": w.backpack, "kind": "instrument"}
        loop, clock, store, patched = hurt_loop(w, 100)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                clock.advance(0.5)
                try:
                    loop.check_instrument()
                    refused = None
                except loop_hunt.Abort as e:
                    refused = str(e)
                mark = len(loop.link.events)
                loop.team.use(tamer.SKILL_DISCORD, m.serial)
                answers = [tamer.bard_answer(e) for e in loop.link.events[mark:] if tamer.bard_answer(e)]
                low = [j for j in store.con.execute("SELECT kind, data FROM junctures").fetchall()
                       if j[0] == "low_supplies" and "instrument" in (j[1] or "")]
                out[spare] = (w.played == (0x416ACB89 if spare else lute), any(a[1] in ("ok", "fail") for a in answers),
                              loop.team.no_instrument, refused is not None and "no instrument" in refused, len(low) > 0)
        finally:
            store.close()
            simclock.uninstall(patched)
    check("the lap harp, the psaltery or the shawm picked and the discord answered, the recall in allowed; with none, "
          "the bard skills off and no recall in (low_supplies)",
          out[0x0EB2] == out[0x4E2A] == out[0x4E2E] == (True, True, False, False, False)
          and out[None] == (True, False, True, True, True),
          str(out))


def test_recall_charges():
    print("== no recall in without a charge for the way in and the way home (no Recall to cast) ==")
    # live 2026-10-25 17:53:38-49 (hunt-20261025-172927-2bc3): the recall home took the tome's last charge, the recall
    # in failed on the spell ("reagents"); at 1 charge the home recall would have failed at Totem with the loot
    import loop_hunt
    out = {}
    for charges in (36, 2, 1):
        w = world_with([{"key": "shade wolf", "at": [5268, 648, 0]}], seed=7)
        w.items[0x444AB1F3]["charges"] = charges
        loop, clock, store, patched = hurt_loop(w, 100)
        loop.args.enter_recall = 0x444AB1F3            # §5's command: in and home by the same tome
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                clock.advance(0.5)
                try:
                    loop.check_charges()
                    refused = None
                except loop_hunt.Abort as e:
                    refused = str(e)
                low = [j for j in store.con.execute("SELECT kind, data FROM junctures").fetchall()
                       if j[0] == "low_supplies" and "recall charges" in (j[1] or "")]
                out[charges] = (refused is not None and "not going in" in refused, len(low))
        finally:
            store.close()
            simclock.uninstall(patched)
    check("36 or 2 charges (in and home, one tome): in; 1: not going in, one low_supplies",
          out == {36: (False, 0), 2: (False, 0), 1: (True, 1)}, str(out))


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


def test_cure_potion():
    print("== poisoned: the runner's tick drinks a cure potion (none while not poisoned) ==")
    # user 2026-10-26: "the runner seems not to use cure potions when Logan is poisoned"; live 22:31:09-49 Logan stayed
    # poisoned 40 s on Tidal Tomb level 1 until the user drank one
    import tempfile
    import loop_hunt
    import memory
    from sim import run as simrun
    sc = load()
    w = World(sc, seed=1)
    link, clock = link_for(w)
    db = os.path.join(tempfile.mkdtemp(prefix="sim_test_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(sc, ["--no-songs"], 10, at_hunt=True)
                                             + ["--memory", db, "--seed", "1"])
    cures = lambda: sum(it["amount"] for it in w.items.values() if it.get("kind") == "cure_potion")  # noqa: E731
    patched = simclock.install(clock)
    store = memory.Memory(db)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            loop = loop_hunt.HuntLoop(link, store, args)
            loop.team.setup(loop.state())
            loop.spot = loop.fight_spot = tuple(w.player.pos[:2])
            loop.start_visit(loop.state())
            before = cures()
            end = clock.elapsed + 8
            while clock.elapsed < end:
                loop.tick()
                clock.advance(0.2)
            unpoisoned = cures()
            w.player.poison_until, w.player.poison_next = w.t + 60, w.t + 2
            clock.advance(0.2)
            poisoned_at = w.t
            while w.player.poison_until > w.t and w.t - poisoned_at < 10:
                loop.tick()
                clock.advance(0.2)
    finally:
        store.close()
        simclock.uninstall(patched)
    check("no cure potion while not poisoned", unpoisoned == before, f"{before} -> {unpoisoned}")
    check("poisoned: a cure potion drunk and the poison gone within 3 s",
          w.player.poison_until <= w.t and w.t - poisoned_at <= 3 and cures() == before - 1
          and "cure potion: cured" in buf.getvalue(),
          f"after {w.t - poisoned_at:.1f} s, potions {before} -> {cures()}; "
          + " | ".join(ln for ln in buf.getvalue().splitlines() if "cure" in ln)[-300:])


def test_songs_tidal_floor():
    print("== songs on Tidal Tomb's floor (statics over the invalid land 0x244, or over valid land under a raised floor): "
          "the floor static named, in sight ==")
    # live 2026-10-26 21:52-21:54 (hunt-20261026-215149-a52b): every song at a tile beside Logan with graphic 0 got
    # 500237 "Target cannot be seen." (the server takes graphic 0 for the land, at z -100 under the floor)
    # live 2026-10-27 01:04:42-01:05:22 (hunt-20261027-233648-7e3b): level 1's ledge, land at z 19 under the stone 0x71E
    # and the floor 0x51E at 24: four songs at graphic 0, z 24, "cannot be seen"
    import tempfile
    import loop_hunt
    import memory
    from sim import run as simrun
    for where, start, z in (("the arrival", TIDAL_L1, None), ("the ledge", (6675, 2022), 24)):
        w = tidal_world(start, z=z)
        link, clock = link_for(w)
        db = os.path.join(tempfile.mkdtemp(prefix="sim_test_"), "harness.db")
        args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, [], 10, at_hunt=True)
                                                 + ["--memory", db, "--seed", "1"])
        patched = simclock.install(clock)
        store = memory.Memory(db)
        buf, sent = io.StringIO(), []
        act = link.act

        def spy(data, act=act, sent=sent):
            if data[:1] == b"\x6c":
                sent.append(data)
            return act(data)
        link.act = spy
        try:
            with contextlib.redirect_stdout(buf):
                loop = loop_hunt.HuntLoop(link, store, args)
                loop.team.setup(loop.state())
                end = clock.elapsed + 60
                while len(w.songs) < 3 and clock.elapsed < end:
                    if not loop.team.song(loop.state()):
                        clock.advance(0.5)
        finally:
            store.close()
            simclock.uninstall(patched)
        graphics = [int.from_bytes(d[23:27], "big") for d in sent if d[1] == 1]
        check(f"all three songs play at {where}, at a floor static (graphic not 0), no 'cannot be seen'",
              set(w.songs) == {"discord", "peace", "provoke"} and graphics and all(graphics)
              and "cannot be seen" not in buf.getvalue(), f"{w.songs} graphics {[hex(g) for g in graphics]} | "
              + " | ".join(ln for ln in buf.getvalue().splitlines() if "Song" in ln)[-500:])


def test_songs():
    print("== --songs: the runner plays the three barding songs at the ground; they cut the damage we take ==")
    import tempfile
    import loop_hunt
    import memory
    from sim import run as simrun
    sc = load()
    # on Maus 3's land floor where a live song played (18:58:11 2026-10-26), nothing spawned: the arrival (5199, 737, 17)
    # is the stairs' top over land at 0, and a song there names the land under the stone, refused as on Tidal Tomb's
    # ledge 2026-10-27
    sc["player"]["start"], sc["spawns"] = [5232, 651, 0], []
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
    gb.hurt_by = {}
    h0 = gb.hits
    w.hurt(gb, 100, m, "wail")
    sung = h0 - gb.hits
    del w.songs["discord"]
    gb.hurt_by = {}
    h0 = gb.hits
    w.hurt(gb, 100, m, "wail")
    plain = h0 - gb.hits
    x = 1 - w.song_x
    check(f"Song of Discordance: a creature's hit on our pet x {x:.2f}", abs(sung - plain * x) <= 1, f"{sung} of {plain}")


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


def test_bard_peek():
    print("== a caster or shy one on a pet, out of our sight by the care: a step into its sight and the skill at once ==")
    # live 2026-10-25 (hunt-20261025-214237-36a7): 21:45:55 a blightmare at (5244, 688) on GraveRobber, Logan at (5240,
    # 698) where cover put him; 21:48:40 a reanimated daemon at (5242, 695) on GraveRobber, Logan at (5240, 705) where the
    # reposition put him. The pets killed both with no bard skill: bard_step counted the target's own sight of us
    # (user: "patrolled the pets correctly to a blightmare but did not try to discord or peace it")
    out = {}
    for key, at, me, pets in (("blightmare", (5244, 688, 0), (5240, 698), ((5244, 689), (5243, 694))),
                              ("reanimated daemon", (5242, 695, 0), (5240, 705), ((5242, 696), (5244, 706)))):
        w = world_with([{"key": key, "at": list(at)}], seed=7, start=me)
        m = next(m for m in w.mobs.values() if m.key == key)
        loop, clock, store, patched = hurt_loop(w, 100)
        gr = next(p for p in w.pets() if p.name == "GraveRobber")
        for p, xy in zip(sorted(w.pets(), key=lambda p: p is not gr), pets):
            p.x, p.y = xy
        m.target, m.threat = gr.serial, {gr.serial: 50}
        w._warm(m, True)
        m.next_step = 1e9
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                loop.link.send(tamer.command("all kill"))
                clock.advance(4.0)                      # the pets on it: hurt within ENGAGED_S
                st = loop.state()
                seen = loop.team.bard_sees(st, m.serial)
                went = loop.team.bard(st, m.serial, st["world"]["mobiles"][f"0x{m.serial:08X}"])
            out[key] = (seen, went, loop.team.counts.get("bard", 0), tuple(w.player.pos[:2]))
        finally:
            store.close()
            simclock.uninstall(patched)
    check("out of its sight at the start, then a step and the skill, for both",
          all(not s and went and n == 1 for s, went, n, _ in out.values()), str(out))


def test_bard_peek_corner():
    print("== four monsters on the pets round a corner, two of them awake purgers: a peek and a skill all the same ==")
    # live 2026-10-25 23:31:10-23:32:02 (hunt-20261025-231940-5e67, capture replay): Logan at (5262, 647) out of both
    # purgers' shot, the fight 4-7 tiles off round the wall, one Discordance in 50 s (user: "lots of mobs are fighting
    # the pets and we are not barding any of them"). The only tile that saw it was 4 steps off, out of vet range of
    # AlaFlambe and in the second purger's sight
    w = world_with([{"key": "corpse purger", "at": [5266, 651, 0]}, {"key": "corpse purger", "at": [5265, 652, 0]},
                    {"key": "bound soul", "at": [5266, 652, 0]}, {"key": "bound soul", "at": [5269, 652, 0]}],
                   seed=7, start=(5262, 647))
    loop, clock, store, patched = hurt_loop(w, 100)
    gr = next(p for p in w.pets() if p.name == "GraveRobber")
    af = next(p for p in w.pets() if p.name == "AlaFlambe")
    gr.x, gr.y, af.x, af.y = 5265, 651, 5268, 652
    mobs = [m for m in w.mobs.values() if m.kind == "monster"]
    for m, pet in zip(mobs, (gr, gr, gr, af)):
        m.target, m.threat = pet.serial, {pet.serial: 50}
        w._warm(m, True)
        m.next_step = 1e9
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            loop.link.send(tamer.command("all kill"))
            clock.advance(4.0)                      # the pets on the first purger: hurt within ENGAGED_S
            st = loop.state()
            seen = loop.team.bard_sees(st, mobs[0].serial)
            went = loop.team.bard(st, mobs[0].serial, st["world"]["mobiles"][f"0x{mobs[0].serial:08X}"])
    finally:
        store.close()
        simclock.uninstall(patched)
    check("out of its sight in the corner, then the peek and the skill", not seen and went
          and loop.team.counts.get("bard", 0) == 1, f"seen {seen} went {went} counts {loop.team.counts} at {w.player.pos}")


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


def test_caster_room_pets_first():
    print("== the master reanimator's room from its doorway: the pets first on the casters, not Logan ==")
    # live 2026-10-26 19:30:06-16 (hunt-20261026-184350-3c20): "holding here: shadowguard in sight at 6 tiles: letting it
    # come" in the doorway (5241, 723); it woke on Logan (ensnared, diseased), the reanimator after, dead at 19:30:23. The
    # user from the same place at 19:45: "All Patrol" into the room from out of sight, both woke on the pets, no damage
    # (aggro.shy since: the casters too)
    w = world_with([{"key": "shadowguard", "at": [5235, 723, 0]}, {"key": "master reanimator", "at": [5230, 722, 0]}],
                   seed=7, start=(5241, 723))
    for m in w.mobs.values():
        m.next_step = 1e9                               # they stand where the capture had them
    loop, clock, store, patched = hurt_loop(w, 100)
    first = {}
    tick = w._tick

    def watch():
        tick()
        for m in w.mobs.values():
            if m.kind == "monster" and m.target is not None and m.serial not in first:
                first[m.serial] = "Logan" if m.target == w.player.serial else "pet"
    w._tick = watch
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            import crawl
            loop.crawl = crawl.Crawl(loop, crawl.load_prior(store.con, loop.spot, loop.args.crawl_depth_risk))
            loop.crawl.setup(loop.state())
            t0 = tamer.time.monotonic()
            while tamer.time.monotonic() - t0 < 40 and len(first) < 2 and w.player.alive:
                loop.tick()
    finally:
        store.close()
        simclock.uninstall(patched)
    by = {k: round(v) for k, v in w.metrics.get("player_damage_by", {}).items() if k in ("shadowguard", "master reanimator")}
    check("both woke on a pet, none on Logan; little of their damage on him", first and "Logan" not in first.values()
          and sum(by.values()) < 30, f"first targets {first} damage on Logan {by} at {w.player.pos}")


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


def test_patrol_after_back_off():
    print("== an idle corpse purger seeing us first at 11: back off within reach of a patrol spot, then 'All Patrol' ==")
    # live 2026-10-25 15:57:40 (hunt-20261025-152542-5ab5): back off from 11 to (5251, 654), 14 off, where no spot is
    # within PATROL_MAX_R of us and PATROL_SPOT_R of it; no patrol, and the crawl walked away (30 of 38 such back-offs
    # 10-22..25 had none)
    import crawl
    w = world_with([{"key": "corpse purger", "at": [5265, 653, 0]}], seed=7, start=(5254, 653))
    m = next(m for m in w.mobs.values() if m.kind == "monster")
    loop, clock, store, patched = hurt_loop(w, 100)
    targets = set()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            loop.crawl = crawl.Crawl(loop, crawl.load_prior(store.con, loop.spot, loop.args.crawl_depth_risk))
            loop.crawl.setup(loop.state())
            t0 = tamer.time.monotonic()
            while tamer.time.monotonic() - t0 < 120 and m.alive:
                loop.tick()
                if m.target is not None:
                    targets.add(m.target)
    finally:
        store.close()
        simclock.uninstall(patched)
    pets = {p.serial for p in w.pets()}
    c = loop.team.counts
    check("backed off, then one patrol; the purger killed on the pets alone",
          c.get("back_offs", 0) >= 1 and c.get("patrols", 0) == 1 and not m.alive and targets and targets <= pets,
          f"back_offs {c.get('back_offs', 0)} patrols {c.get('patrols', 0)} purger alive {m.alive} targets {targets} "
          f"pets {pets} at {w.player.pos}")


def test_patrol_after_pass_over():
    print("== an idle corpse purger passed over ('it can't see us'): still patrolled at ==")
    # live 2026-10-25 15:52:21-29: "passing over a corpse purger ...: it can't see us from 8 tiles; skipping it for 60
    # s", then backed off from at 4 tiles and never patrolled at (HuntLoop.skip stopped patrol_pull; 11 of 11 such
    # back-offs 10-22..25)
    w = world_with([{"key": "corpse purger", "at": [5265, 655, 0]}], seed=7, start=(5253, 653))
    m = next(m for m in w.mobs.values() if m.kind == "monster")
    loop, clock, store, patched = hurt_loop(w, 100)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            loop.skip[m.serial] = tamer.time.monotonic() + 60.0
            t0 = tamer.time.monotonic()
            while tamer.time.monotonic() - t0 < 20 and not loop.team.counts.get("patrols", 0):
                loop.tick()
    finally:
        store.close()
        simclock.uninstall(patched)
    check("one patrol at it within 20 s", loop.team.counts.get("patrols", 0) == 1,
          f"patrols {loop.team.counts.get('patrols', 0)} at {w.player.pos}")


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


def test_to_pet_not_into_caster_reach():
    print("== the live 00:38:30 tiles: the tank at 92 % on an awake master reanimator 12 off: no walk to 4 of it; at "
          "70 % the walk; a potion refused at full health starts no cooldown ==")
    # live 2026-10-25 00:38:27-45 (hunt-20261024-230728-d7cf): the reposition out of its sight to (5243, 733), then "to a
    # hurt pet: (5243, 722), 4 from GirthyEarthy", its and a shadowguard's spells on all of us, 100 -> 0 in 6 s; a vet
    # finish (72 -> 100) just before a potion's click ("You are already at full health.") counted as a drink, and no
    # potion was tried at 65 and 45 hits
    import aggro
    import healing
    from agent_link import serial_of
    out, res = io.StringIO(), {}
    for frac in (0.92, 0.70):     # 70 %: live 2026-10-25 21:48:55-49:06 GraveRobber held at 56-64 % (user: "didn't vet")
        w = world_with([{"key": "master reanimator", "at": [5239, 721, 0]}], seed=7, start=(5243, 733))
        mr = next(m for m in w.mobs.values() if m.key == "master reanimator")
        loop, clock, store, patched = hurt_loop(w, 100)
        gr = next(p for p in w.pets() if p.name == "GraveRobber")
        for p, xy in zip(w.pets(), ((5239, 722), (5241, 725))):
            p.x, p.y = xy
        w.hurt(gr, gr.hits_max * (1 - frac), mr, "spell")     # its bar sent hurt
        mr.target, mr.threat = gr.serial, {gr.serial: 50}
        w._warm(mr, True)
        try:
            with contextlib.redirect_stdout(out):
                clock.advance(0.5)
                st = loop.state()
                v = aggro.View(loop, st)
                awake = [m for m in v.mobs if m["war"] and m["caster"]]
                walked = loop.team.to_pet(st)
                res[frac] = (bool(awake), walked, sim_cheb(w.player.pos, mr.pos))
                if frac == 0.92:
                    last = loop.potions.last
                    w.player.hits = w.player.hits_max
                    potion = serial_of(healing.heal_potions(loop.state()["world"], loop.me(loop.state()))[0][0])
                    drunk = loop.drink(loop.state(), potion)
                    res["potion"] = (drunk, loop.potions.last == last)
        finally:
            store.close()
            simclock.uninstall(patched)
    check("at 92 %: no walk nearer the awake reanimator, the reason logged",
          res[0.92][0] and not res[0.92][1] and res[0.92][2] == 12 and "awake master reanimator" in out.getvalue(),
          f"(awake, walked, from it) {res[0.92]}")
    check(f"at 70 % (below tamer.TO_PET_CASTER_AT {tamer.TO_PET_CASTER_AT:.0%}): the walk to the pet",
          res[0.70][1] and res[0.70][2] < 12,
          f"(awake, walked, from it) {res[0.70]}")
    check("at full health the potion isn't drunk and its clock stays", res["potion"] == (False, True),
          f"(drunk, clock unchanged) {res['potion']}")


def test_blightmare_first_sight():
    print("== the live 18:20:44 scene: the runner never takes an idle blightmare's first sight (it doesn't come on) ==")
    # live 2026-10-21 18:20:44-48: "crawl: blightmare ... in view at 13 tiles: patrolling toward it", an approach and a
    # patrol step into its sight at 10 with the pets behind, and it woke on Logan (war mode 18:20:48.5); it never
    # stepped, cast at him from 10-12 and he died at 18:21:15. Simulator (sim/fight1820.py), seeds 1-48: 22 deaths before
    import crawl
    from agent_link import Abort
    from sim import world as simworld
    out = []
    # its shots at the pets don't stray to Logan here (world.SHOT_AT_PLAYER_P): the runner closes to 11-12 tiles of it
    # while it fights them, before and after 2026-10-25, and whether a 20 % stray lands is the seed's luck, not the rule
    # under test (seed 3 drew one once the pets' rules changed the draws)
    stray, simworld.SHOT_AT_PLAYER_P = simworld.SHOT_AT_PLAYER_P, 0.0
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
    simworld.SHOT_AT_PLAYER_P = stray
    check("3 seeds: the blightmare never on Logan, none of its spells on him",
          all(s == 0 and d == 0 and not dead for _, s, d, dead in out),
          f"(seed, seconds it was on him, its damage, deaths) {out}")


def test_seek_by_kill_value():
    print("== the live 22:02:08 scene: an idle reanimated daemon 13 off and a tormented soul 12 off: the seek goes to "
          "the daemon ==")
    # live 2026-10-25 22:02:08 (hunt-20261025-214237-36a7): "crawl: tormented soul ... in view at 12 tiles: patrolling
    # toward it", passed over there ("it can't see us"), the patrol on north; the daemon, spawned 22:01:42 at (5237, 701),
    # never sought (user: "walked away from a freshly spawned reanimated demon"). The store's estimates then: the daemon
    # 1,491 gp in 34.7 s, the soul 857 gp in 13.1 s; by gold a fighting minute the soul won
    import crawl
    w = world_with([{"key": "reanimated daemon", "at": [5237, 701, 0]}, {"key": "tormented soul", "at": [5247, 676, 0]}],
                   seed=7, start=(5238, 688))
    for m in w.mobs.values():
        m.calm_until = 1e9
    loop, clock, store, patched = hurt_loop(w, 100)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            model = crawl.Model()
            for name, fight_s, gold in (("a reanimated daemon", 34.7, 1491), ("tormented soul", 13.1, 857)):
                for _ in range(200):
                    model.add_fight(name, fight_s, 5, "kill")
                    model.add_gold(name, gold)
            c = loop.crawl = crawl.Crawl(loop, model)
            c.setup(loop.state())
            c.unlocked = 99
            st = loop.state()
            found = c.seek(st, loop.pos(st))
    finally:
        store.close()
        simclock.uninstall(patched)
    check("the daemon (more gold a kill over the time it costs)", found is not None and found[1] == "reanimated daemon",
          str(found))


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


def test_red_walled():
    print("== a red's Wall of Stone across the only way to the gate: out of their sight meanwhile, not standing ==")
    # live 2026-10-30 23:43:31.19-36.23 (hunt-20261030-230618-058b, Maus 3's east wing): Silent Assassin's Wall of Stone
    # at (5254, 652-654) cut the corridor west; "no route from (5258, 654); looking again (1 of 6)" ... "(6 of 6)" in
    # 4.7 s standing 1-3 tiles from three casters, dead (sim/fight2343.py). Here they stand (only the flight is under test)
    import los
    from agent_link import Abort
    w = red_world(start=(5258, 654))
    loop, clock, store, patched = red_loop(w)
    w.add_wall([(5254, 652), (5254, 653), (5254, 654)], life_s=6.0)
    reds = [w.add_red(*xy, name=n, spec={"hands": [[0, 0, 1]]}) for n, xy in
            (("S p a c e B a r", (5257, 653)), ("Silent Assassin", (5256, 653)), ("Vowxin Retp", (5261, 653)))]
    for r in reds:
        r.next_step = 1e9
    world_tick, hidden = clock.on_advance, []

    def watch(now):
        world_tick(now)
        pl = w.player
        if not hidden and not pl.ghost and not any(
                w.sight.point_blocked((r.x, r.y, r.z + los.EYE), (pl.x, pl.y, pl.z + los.EYE)) is None for r in reds):
            hidden.append((w.t, pl.pos))
    clock.on_advance = watch
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            try:
                loop.leave_by_gate("urgent")
            except Abort:
                pass
    finally:
        store.close()
        simclock.uninstall(patched)
    lines = out.getvalue().splitlines()
    cut = [ln for ln in lines if "no route from" in ln]
    check("no route: the wall named as what cuts the way, no count of looks against the flight",
          bool(cut) and "a wall of stone at (5254, " in cut[0] and not any(" of 6)" in ln for ln in cut), str(cut[:2]))
    check("...out of their sight meanwhile (5 steps, a potion on the way), there within 3.5 s, not standing in their "
          "spells", bool(hidden) and hidden[0][0] <= 3.5 and any("out of their sight meanwhile" in ln for ln in cut),
          f"hidden {hidden[:1]} deaths {w.metrics['deaths']}")


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

    def hurt(victim, amount, by, kind="melee", song_pets=True, **kw):
        before = victim.hits
        orig(victim, amount, by, kind, song_pets, **kw)
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
    # the victims: a monster's spell, feed and melee numbers are the reference pet's (each pet's own share:
    # test_pet_builds); Logan x1.6 on spells, x1.2 on a feed tick (his melee: test_armor)
    w = world_with([])
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    af = next(p for p in w.pets() if p.name == "AlaFlambe")
    check("a monster's hit: Logan x1.2 on a feed tick and x1.6 on spells; on a pet the measured number (its share in hurt)",
          (w._taken_x(w.player, "feed"), w._taken_x(w.player, "spell"), w._taken_x(gb, "melee"),
           w._taken_x(af, "melee"), w._taken_x(af, "spell")) == (1.2, 1.6, 1.0, 1.0, 1.0))
    # apparition: a blink every ~8 s though its target stands beside it, onto the target's tile, the area hit
    # only at the landing; the song cuts Logan's share, not the pets'. The 10-15 loadout: the live 37 against 37 was
    # measured with it, and its gravebug is the reference pet (the current scan's tabs move its share)
    w = world_with([{"key": "apparition", "at": [5268, 649, 0]}], seed=12, bestiary="Phoenix/Gravebug 2026-10-15")
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
    pet_burst = {p.name: [h[3] for h in bursts if h[1] is p] for p in w.pets()}
    pl_burst = [h[3] for h in bursts if h[1].kind == "player"]
    check("apparition: '*phases*' 0.6-2.5 s in, then every 7.8-8.7 s beside its target, back on its tile 1.1-1.7 s "
          "later with the area hit there only (live n=48/56)",
          phases and 0.6 <= phases[0] <= 2.7 and len(gaps) >= 1 and all(7.8 <= g <= 8.7 for g in gaps)
          and landings and all(ok for _, ok in landings) and bursts and on_landing
          and sum(1 for t, _, x in said if x == "*phases*") == len(phases),
          f"phases {phases} landings {landings} bursts {[(b[0], b[1].name, b[3]) for b in bursts]}")
    from sim import pets
    ctx = w._pet_ctx(gb, m)
    af = next(p for p in w.pets() if p.name == "AlaFlambe")
    more = (1 - pets.dr(af.spec, w._pet_ctx(af, m))) / (1 - pets.dr(gb.spec, ctx, True))
    check("...the gravebug's share 36-38 (the reference pet's numbers) under the Song of Discordance too, the phoenix's x "
          "its resistance over the gravebug's (live 37 against 37: docs/SIM.md 'Pets, teams and perks'), Logan's 55-60 "
          "cut by it", pet_burst["GraveRobber"] and all(36 <= n <= 38 for n in pet_burst["GraveRobber"])
          and pet_burst["AlaFlambe"] and all(36 * more - 1 <= n <= 38 * more + 1 for n in pet_burst["AlaFlambe"])
          and pl_burst and all(44 <= n <= 50 for n in pl_burst), f"pets {pet_burst} x{more:.3f} Logan {pl_burst}")
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


def test_walk_in_fetch_pet():
    print("== a pet fighting beyond a command's reach on the walk in: go within reach, 'All Follow Me', on through ==")
    # live 2026-10-24 18:26:46-18:27:50 (hunt-20261024-173530-0ca1): 'All Guard Me' on the first level sent GraveRobber
    # 17 tiles off to (5582, 684) after a reanimator apprentice, then a corpse eater; three 'All Follow Me' from
    # (5570, 701) didn't reach him, the walk aborted ("the pets aren't within 3 tiles of us after 20 s") and the escape
    # home left him there
    import tempfile
    import loop_hunt
    import memory
    from sim import run as simrun
    from sim import world as simworld
    start, fight_at, first = (5570, 701), (5582, 684), (5554, 700)
    w = world_with([{"key": "corpse eater", "at": [fight_at[0], fight_at[1] - 1, 0]}], seed=7, start=start)
    m = next(m for m in w.mobs.values() if m.kind == "monster")
    m.hits = m.hits_max = 10 ** 6
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    gb.spec = {**gb.spec, "leash": 30}        # live he fought on 18 from us; the simulator's leash (14) calls him back
    link, clock = link_for(w)
    db = os.path.join(tempfile.mkdtemp(prefix="sim_test_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, ["--no-songs"], 10)
                                             + ["--memory", db, "--seed", "1", "--human", "off"])
    patched = simclock.install(clock)
    store = memory.Memory(db)
    err, buf = None, io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            loop = loop_hunt.HuntLoop(link, store, args)
            loop.team.setup(loop.state())                 # both pets beside us: the roster
            gb.x, gb.y, gb.z = fight_at[0], fight_at[1], w.stand_z(*fight_at, 0)
            m.target, m.threat = gb.serial, {gb.serial: 1e9}
            gb.mode, gb.target = "guard", m.serial
            w._warm(m, True)
            w._warm(gb, True)
            clock.advance(1.0)
            try:
                loop.walk_in([first])
            except Exception as e:  # noqa: BLE001
                err = e
    finally:
        store.close()
        simclock.uninstall(patched)
    out = buf.getvalue()
    check("the setup: GraveRobber beyond a command's reach", sim_cheb(fight_at, start) > simworld.COMMAND_R, "")
    check("through the first teleporter with both pets, no abort", err is None and sim_cheb(w.player.pos, first) > 3
          and all(sim_cheb(p.pos, w.player.pos) <= tamer.FOLLOW_R + 1 for p in w.pets()),
          f"{err} at {w.player.pos}, pets {[(p.name, p.pos) for p in w.pets()]}")
    check("...after going back within reach of him", "beyond a command's reach" in out,
          " | ".join(ln for ln in out.splitlines() if "GraveRobber" in ln or "pets" in ln)[-600:])


def test_wait_pets_attacked():
    print("== waiting for lagging pets on the walk out: attacked meanwhile, the walk's fight, not standing in it ==")
    # simulator 2026-10-26 (tidal_tomb seed 3, the leave at time up): 'to the moongate: waiting for the pets' with both
    # 9 tiles off, an oozing noughtilus on Logan from 100 to dead in 18 s, no potion, until 'the pets aren't within 3
    # tiles after 20 s; going on'
    import tempfile
    import loop_hunt
    import memory
    from sim import run as simrun
    x, y = TIDAL_L1
    w = tidal_world(TIDAL_L1, [{"key": "oozing noughtilus", "at": [x + 1, y - 1, -16]}])
    m = next(m for m in w.mobs.values() if m.kind == "monster")
    m.hits = m.hits_max = 10 ** 6
    link, clock = link_for(w)
    db = os.path.join(tempfile.mkdtemp(prefix="sim_test_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, ["--no-songs"], 10)
                                             + ["--memory", db, "--seed", "1", "--human", "off"])
    patched = simclock.install(clock)
    store = memory.Memory(db)
    lowest, buf = [w.player.hits], io.StringIO()
    run_until = clock.on_advance

    def advance(now):
        run_until(now)
        lowest[0] = min(lowest[0], w.player.hits)
    try:
        with contextlib.redirect_stdout(buf):
            loop = loop_hunt.HuntLoop(link, store, args)
            loop.team.setup(loop.state())                 # both pets beside us: the roster
            for p in w.pets():                            # then 9 tiles off (up the corridor) and stuck there
                w._teleport(p, (x + 2, y - 9, -16))
                w._moved(p)
                p.spec = {**p.spec, "step_s": 1e9}
            m.target, m.threat = w.player.serial, {w.player.serial: 1e9}
            w._warm(m, True)
            clock.advance(1.0)                            # the client sees the pets' new tiles
            apart = [sim_cheb(p.pos, w.player.pos) for p in w.pets()]
            clock.on_advance = advance
            loop.wait_pets("to the moongate", None, required=False)
    finally:
        clock.on_advance = run_until
        store.close()
        simclock.uninstall(patched)
    out = buf.getvalue()
    check("the setup: both pets out of reach at the wait's start", all(d > tamer.FOLLOW_R for d in apart), str(apart))
    check("the wait ends in the walk's fight ('attacked on the way out'), Logan alive above 40",
          "attacked on the way out" in out and w.player.alive and not w.player.ghost and lowest[0] > 40,
          f"lowest {lowest[0]}; " + " | ".join(out.splitlines()[-8:])[-700:])


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


def main_in_sim(start, extra=(), walk_to=None, seed=7, scenario="mausoleum"):
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
    sc = load(scenario)
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


def test_go_home_tidal_level3():
    print("== --go-home from Tidal Tomb's level 3: up the arrival strip (--leave-via), the crimson gate on level 2, "
          "home ==")
    # user 2026-10-25: "We can leave lvl 3 by going back to the crimson gate on lvl 2, as I did" (11:35:19 a step S onto
    # (7161, 2221), 11:36:55 the crimson gate at (6897, 1992)); before --leave-via: "no way out from (7170, 2200)"
    code, out, w, js, evs = main_in_sim((7170, 2200, -50), ["--go-home", "--leave-gate", "6897", "1992",
                                                             "--leave-via", "7161", "2221"], scenario="tidal_tomb")
    way = evs[-1]["data"]["way"] if evs else None
    check("home, exit 0, by the --leave-via teleporter and the gate a level up", code == 0 and not w.player.ghost
          and sim_cheb(w.player.pos, SIM_HOME) <= 5 and bool(way) and way.startswith("the --leave-via teleporters from "
                                                                                     "7161,2221"),
          f"exit {code} at {w.player.pos} way {way}: "
          + " | ".join(ln for ln in out.splitlines() if "go-home" in ln or "escape" in ln or "ABORTED" in ln)[-500:])
    check("...both pets came home with him", all(sim_cheb(p.pos, w.player.pos) <= 3 for p in w.pets()),
          str([(p.name, p.pos) for p in w.pets()]))


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


def test_armor():
    print("== Logan's armor (docs/SIM.md 'Player armor'): a melee hit loses AR/300-AR/150 of the wiki melee x k; the suit's AR "
          "and the Command tier ==")
    from sim import armor
    from sim.scenario import player_armor
    s = {"base": "platemail", "quality": "exceptional", "material": "verite", "aspect": "command"}
    check("Logan's live suits: no aspect 88, Command tier 13 130, tier 14 133, with the +15 buff 148 (0x11 status)",
          [armor.suit_ar({**s, "aspect_tier": t}) for t in (None, 13, 14)] == [88, 130, 133]
          and armor.suit_ar({**s, "aspect_tier": 14, "extra": 15}) == 148)
    w = world_with([{"key": "shade wolf", "at": [FLOOR[0] + 1, FLOOR[1], 0]}])
    spec, k = w.sc["creatures"]["shade wolf"], w.sc["defaults"]["player_melee_k"]
    lo, hi = 40 * k, 50 * k                          # the wiki's shade wolf: 40-50
    check("the scenario's suit: AR 133", w.player.ar == 133, str(w.player.ar))
    hits = {}
    for ar in (0, 86, 133):
        w.player.ar = ar
        hits[ar] = [w._melee_on(w.player, spec) for _ in range(4000)]
    check("no armor: the wiki melee x k as it is", all(lo <= h <= hi for h in hits[0]),
          f"{min(hits[0]):.1f}-{max(hits[0]):.1f}")
    check("AR 133: between x(1 - 133/150) of the least and x(1 - 133/300) of the most",
          all(lo * (1 - 133 / 150) - 1e-9 <= h <= hi * (1 - 133 / 300) + 1e-9 for h in hits[133]),
          f"{min(hits[133]):.1f}-{max(hits[133]):.1f}")
    ratio = (sum(hits[133]) / len(hits[133])) / (sum(hits[86]) / len(hits[86]))
    check("the mean hit at AR 133 over AR 86 is the rule's (1 - 133/200) / (1 - 86/200) = 0.59",
          abs(ratio - 0.588) < 0.03, f"{ratio:.3f}")
    w.player.ar = 133
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    check("a pet's melee doesn't meet Logan's armor: the gravebug-referenced numbers",
          all(spec["melee"][0] <= w._melee_on(gb, spec) <= spec["melee"][1] for _ in range(200)))
    sc = load()
    sc["player"]["armor"]["suit"]["aspect_tier"] = 10
    a10 = player_armor(sc)
    sc["player"]["armor"]["ar"] = 60
    check("Command tier 10 instead of 14: AR 50 x 2.45 = 123; an explicit `ar` (run.py --armor) wins over the suit's",
          a10["ar"] == 123 and player_armor(sc)["ar"] == 60, str(a10))
    from sim import pets
    pets.select(sc)                                  # the pets at the suit's tier 10 (run.py --aspect-tier)
    sc["spawns"] = []
    sc["player"]["start"] = [FLOOR[0], FLOOR[1], 0]
    w10 = World(sc, seed=1)
    gb10 = next(p for p in w10.pets() if p.name == "GraveRobber")
    d14 = {p.name: p.spec["dr_static"] for p in w.pets()}
    check("the pets' follower resistance at tier 10: 6 % less (the Command aspect's 5 % + 1.5 % a tier)",
          all(abs(d14[p.name] - p.spec["dr_static"] - 0.06) < 1e-9 for p in w10.pets()),
          str({p.name: round(d14[p.name] - p.spec["dr_static"], 4) for p in w10.pets()}))
    taken = []
    for ww, g in ((w, gb), (w10, gb10)):
        g.hits = before = g.hits_max
        ww.hurt(g, 100, None, "wail")
        taken.append(before - g.hits)
    check("a 100 wail (no armor) on the gravebug: more at tier 10 than at tier 14", taken[1] > taken[0], str(taken))
    from sim import run as simrun
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        r = simrun.run_once(1, [], minutes=0.2, at_hunt=True, armor={"aspect_tier": "off", "ar": None})
    sc = load()
    sc["player"]["armor"]["suit"]["aspect_tier"] = None          # what --aspect-tier off sets
    pets.select(sc)
    off = {p["name"]: p for p in sc["pets"]}
    cmd = 0.05 + 0.015 * 14                                      # the Command aspect's damage and resistance at 14
    check("--aspect-tier off (out of arcane essence, live 2026-10-25 21:16-23:17): the suit's AR without its bonus, 88; "
          "the pets lose the tier-14 Command damage and resistance (0.26 each) and attack slower",
          r["outcome"] == "done" and r["player_ar"] == 88
          and all(abs(p.spec["dmg_static"] - off[p.name]["dmg_static"] - cmd) < 1e-9
                  and abs(p.spec["dr_static"] - off[p.name]["dr_static"] - cmd) < 1e-9
                  and off[p.name]["period"] > p.spec["period"] for p in w.pets()),
          f"{r['outcome']} {r.get('error')} AR {r.get('player_ar')} "
          + str({n: (round(o['dmg_static'], 3), round(o['dr_static'], 3), o['period']) for n, o in off.items()}))


def test_tamer_scan_rows():
    print("== sim/tamer_scan.py reads the codex's and the bestiary's perk rows: gems = rank, points 0/1/3/5 ==")
    from sim import tamer_scan as ts
    # Healer's Codex page 1, session 150 seq 4485 (2026-10-17 22:06:15), its first three rows verbatim
    layout = ("{ text 70 59 2655 0 18 0 1 0 0 0 }{ text 130 40 2655 1 18 0 1 0 0 0 }{ text 130 62 2550 2 18 0 1 0 0 0 }"
              "{ button 160 64 2118 2117 1 0 100 }{ tooltip 0 3 }{ text 130 84 149 4 18 0 1 0 0 0 }"
              "{ button 300 86 2118 2117 1 0 200 }{ text 70 159 2655 0 18 0 1 0 0 0 }{ text 130 140 2655 5 18 0 1 0 0 0 }"
              "{ text 130 162 2550 2 18 0 1 0 0 0 }{ button 160 164 2118 2117 1 0 101 }{ tooltip 0 6 }"
              "{ text 130 184 149 4 18 0 1 0 0 0 }{ button 300 186 2118 2117 1 0 201 }{ tilepic 43 251 3617 }"
              "{ tilepic 65 251 3617 }{ tilepic 87 251 3617 }{ text 70 276 63 7 18 0 1 0 0 0 }"
              "{ text 130 250 63 8 18 0 1 0 0 0 }{ text 130 272 2550 2 18 0 1 0 0 0 }{ button 160 272 2118 2117 1 0 102 }"
              "{ tooltip 0 9 }")
    lines = ["0 Pts", "Combat Medic", "Info", "Patients receive a (2% / 6% / 10%) bonus to damage resistance over the "
             "next 15 seconds (cannot stack).", "Spend 1 Pts to Raise Tier", "Immunize", "Patients for the next 15 seconds "
             "have a (20% / 60%/  100%) chance for new poisons to apply at 1 level lower. New bleed and disease effects "
             "will be applied at (8% / 24% / 40%) reduced damage (cannot stack).", "5 Pts", "Overheal", "An additional "
             "(8% / 24% / 40%) of healing amounts are applied to patient divided over the next 3 seconds."]
    rows = ts.codex_rows(layout, lines)
    check("codex: Combat Medic tier 0 (0 points), Immunize 0, Overheal tier 3 (3 gems, 5 points)",
          rows == [{"perk": "Combat Medic", "points": 0, "tier": 0}, {"perk": "Immunize", "points": 0, "tier": 0},
                   {"perk": "Overheal", "points": 5, "tier": 3}], str(rows))
    # Taming Bestiary, Nightmare/Fire Attack page 3/11, session 194 seq 360634 (2026-10-25 11:49:24), rows 1-3
    layout = ("{ tilepic 53 38 55046 }{ tilepic 73 38 55046 }{ text 70 71 63 0 18 0 1 0 0 0 }"
              "{ text 130 35 63 1 18 0 1 0 0 0 }{ text 130 57 2550 2 18 0 1 0 0 0 }{ button 160 59 2118 2117 1 0 100 }"
              "{ tooltip 0 3 }{ text 130 79 149 4 18 0 1 0 0 0 }{ button 300 81 2118 2117 1 0 200 }"
              "{ tilepic 63 113 55046 }{ text 70 146 63 5 18 0 1 0 0 0 }{ text 130 110 63 6 18 0 1 0 0 0 }"
              "{ text 130 132 2550 2 18 0 1 0 0 0 }{ button 160 134 2118 2117 1 0 101 }{ tooltip 0 7 }"
              "{ text 130 154 149 4 18 0 1 0 0 0 }{ button 300 156 2118 2117 1 0 201 }{ text 70 204 2655 8 18 0 1 0 0 0 }"
              "{ text 130 185 2655 9 18 0 1 0 0 0 }{ text 130 207 2550 2 18 0 1 0 0 0 }"
              "{ button 160 209 2118 2117 1 0 102 }{ tooltip 0 10 }{ text 130 229 149 11 18 0 1 0 0 0 }"
              "{ button 300 231 2118 2117 1 0 202 }")
    lines = ["3 Pts", "Lethality", "Info", "Follower Damage have a (6% / 18% / 30%) chance to inflict +100% Damage, "
             "otherwise will inflict (4% / 12% / 20%) additional Damage.", "Spend 2 Pts to Raise Tier", "1 Pts",
             "Menagerie", "Follower Damage increased by ((1.5% / 4.5% / 7.5%) * Different Types of Followers Controlled "
             "By Player). Healing Amounts increased by ((1% / 3% / 5%) * Different Types of Followers Controlled By "
             "Player).", "0 Pts", "Mutation", "Reduces cooldown length of Cooldown Abilities by (1.5 / 4.5 / 7.5) "
             "seconds. Follower Damage increased by (5% / 15% / 25%) if follower does not have a Cooldown Ability.",
             "Spend 1 Pts to Raise Tier"]
    rows = ts.bestiary_rows(layout, lines)
    check("bestiary: Lethality rank 2 (2 gems, 3 points), Menagerie 1, Mutation 0",
          [(r["perk"], r["cross"], r["points"], r["rank"]) for r in rows]
          == [("Lethality", False, 3, 2), ("Menagerie", False, 1, 1), ("Mutation", False, 0, 0)], str(rows))
    # Phoenix/Gravebug Tank, session 124 seq 9014: its fourth row, a cross perk
    layout = ("{ tilepic 63 263 55046 }{ text 70 296 1256 0 18 0 1 0 0 0 }{ text 130 260 1256 11 18 0 1 0 0 0 }"
              "{ text 130 282 2550 2 18 0 1 0 0 0 }{ button 160 284 2118 2117 1 0 103 }{ tooltip 0 12 }"
              "{ text 130 304 149 4 18 0 1 0 0 0 }")
    lines = ["1 Pts", "Tolerance", "Info", "", "Spend 2 Pts to Raise Tier", "0 Pts", "Trample", "",
             "Spend 1 Pts to Raise Tier", "[Cross] Ambush", "", "[Cross] Beast Sting", ""]
    rows = ts.bestiary_rows(layout, lines)
    check("bestiary: '[Cross] Beast Sting' rank 1 is the cross perk Beast Sting",
          rows == [{"perk": "Beast Sting", "cross": True, "points": 1, "rank": 1}], str(rows))
    try:
        ts.bestiary_rows(layout.replace("{ tilepic 63 263 55046 }", "{ tilepic 63 263 55046 }{ tilepic 83 263 55046 }"),
                         lines, "test")
        refused = False
    except ValueError:
        refused = True
    check("a row whose gems and points disagree (2 gems, 1 point) is a ValueError", refused)


def test_pet_aggro_setting():
    print("== a pet's Aggro setting (the Guide's Abilities page) decides its draw; a preset pins the one it played with ==")
    from sim import pets
    from sim import tamer_scan as ts
    # AlaFlambe's Abilities page, 2026-10-28 18:25:23 (seq 2385) and after the toggle 18:25:27, top row verbatim
    head = ("{ text 10 0 149 0 18 0 1 0 0 0 }{ text 69 28 149 1 18 0 1 0 0 0 }{ button 47 61 2223 2223 1 0 3 }"
            "{ tilepichue 64 45 39892 HUE }{ button 105 61 2224 2224 1 0 4 }{ text 140 28 149 2 18 0 1 0 0 0 }"
            "{ button 144 61 2223 2223 1 0 5 }{ tilepic 165 57 TILE }{ button 216 61 2224 2224 1 0 6 }")
    lines = ["Guide", "Aggro", "Combat Distance"]
    got = [ts.settings_view(head.replace("HUE", h).replace("TILE", tl), lines)
           for h, tl in (("2104", "3643"), ("2117", "45320"))]
    check("Abilities page: grey 2104 off and 3643 ranged, red 2117 on and 45320 melee",
          got == [{"aggro": False, "combat_distance": "ranged"}, {"aggro": True, "combat_distance": "melee"}], str(got))
    check("a Values page isn't an Abilities page", ts.settings_view("{ text 75 150 149 1 18 0 1 0 0 0 }",
                                                                    ["Guide", "Min Taming"]) is None)
    t = pets.load_tamer()
    none = {tab: {} for tab in pets.TABS}
    on = pets.build_pet("AlaFlambe", t, none, "command", 14, ["AlaFlambe"],
                        settings={"aggro": True, "combat_distance": "melee"})
    off = pets.build_pet("AlaFlambe", t, none, "command", 14, ["AlaFlambe"],
                         settings={"aggro": False, "combat_distance": "ranged"})
    check("the phoenix with aggro on draws as the gravebug (4.0) and casts from beside its target; off 0.1 from 10",
          (on["threat_x"], on["ranged"]["range"], off["threat_x"], off["ranged"]["range"]) == (4.0, 1, 0.1, 10),
          str((on["threat_x"], on["ranged"]["range"], off["threat_x"], off["ranged"]["range"])))
    sc = load()
    pets.select(sc, "phoenix_gravebug")
    check("phoenix_gravebug plays AlaFlambe as it was 10-12..10-25 (aggro off, ranged) whatever its Guide says now",
          [(p["name"], p["threat_x"]) for p in sc["pets"]] == [("GraveRobber", 4.0), ("AlaFlambe", 0.1)]
          and sc["pets"][1]["ranged"]["range"] == 10, str([(p["name"], p["threat_x"]) for p in sc["pets"]]))


def test_party_validation():
    print("== a team and a bestiary loadout are refused with the reason: slots, points, unknown names ==")
    import json
    import tempfile
    from sim import pets
    t = pets.load_tamer()

    def err(fn):
        try:
            fn()
        except ValueError as e:
            return str(e)
        return None

    def tab_file(data):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(data, f)
        f.close()
        return f.name
    got = {"slots": err(lambda: pets.parse_team("AlaFlambe, NightmareA", t)),
           "pet": err(lambda: pets.parse_team("GraveRobber,Fluffy", t)),
           "twice": err(lambda: pets.parse_team("graverobber,GraveRobber", t)),
           "points": err(lambda: pets.parse_bestiary(tab_file(
               {"attack": {"Lethality": 3, "Immolate": 3, "Primal": 3, "Pelt": 3, "Cornered": 1}}), t)),
           "perk": err(lambda: pets.parse_bestiary(tab_file({"tank": {"Bogus": 1}}), t)),
           "own_cross": err(lambda: pets.parse_bestiary(tab_file({"tank": {"[Cross] Hearty": 1}}), t)),
           "cross": err(lambda: pets.parse_bestiary(tab_file({"tank": {"[Cross] Lethality": 3, "[Cross] Pelt": 1}}), t)),
           "loadout": err(lambda: pets.parse_bestiary("Fortres", t))}
    want = {"slots": "team takes 6 control slots, max 5", "pet": "unknown pet 'Fluffy' (pets: GraveRobber, ",
            "twice": "pet 'GraveRobber' twice", "points": "bestiary attack: 21 points, max 20",
            "perk": "bestiary tank: unknown perk 'Bogus'", "own_cross": "bestiary tank: [Cross] 'Hearty' is a tank perk",
            "cross": "bestiary tank: 6 cross points, max 5", "loadout": "unknown loadout 'Fortres' (loadouts: "}
    check("each refusal names its reason", all((got[k] or "").startswith(v) for k, v in want.items()),
          str({k: v for k, v in got.items() if not (v or "").startswith(want[k])}))
    names, preset = pets.parse_team("nightmare_fire", t)
    check("a preset gives its pets, tank first", names == ["NightmareA", "NightmareB", "a searing imp"]
          and preset == "nightmare_fire", str(names))
    sc = load()
    pets.select(sc, "GirthyEarthy, a tidal mantis")
    check("pets named without a preset take the scenario team's loadout; Phoenix/Gravebug's Utility tab empty (the user: "
          "no utility pet in that team): the mantis without perks, no warning",
          sc["party"]["bestiary"] == "Phoenix/Gravebug" and sc["party"]["warnings"] == []
          and next(p for p in sc["pets"] if p["name"] == "a tidal mantis")["perks"] == {}, str(sc["party"]["warnings"]))
    t = pets.load_tamer(sc["tamer"]["character"])            # the cached copy select reads
    tabs = t["bestiary"]["loadouts"]["Phoenix/Gravebug"]
    known, tabs["utility"] = tabs["utility"], None          # as if never captured and not known
    try:
        sc = load()
        pets.select(sc, "GirthyEarthy, a tidal mantis")
    finally:
        tabs["utility"] = known
    check("a tab never captured and not known: no perks for that class and a warning",
          sc["party"]["warnings"] == ["Phoenix/Gravebug utility tab not captured: no perks for a tidal mantis"]
          and next(p for p in sc["pets"] if p["name"] == "a tidal mantis")["perks"] == {}, str(sc["party"]["warnings"]))
    sc = load()
    pets.select(sc, "nightmare_fire", None, ["nightmareb=14"])
    nb = next(p for p in sc["pets"] if p["name"] == "NightmareB")
    lv = {p["name"]: p["level"] for p in sc["pets"]}
    check("--pet-level: NightmareB at 14 (502 hits), the others at their Guide's", nb["hits"] == 502
          and sc["party"]["levels"] == lv and lv["NightmareB"] == 14
          and lv["NightmareA"] == t["guide"]["NightmareA"]["level"], str(sc["party"]["levels"]))
    got = [err(lambda a=a: pets.parse_levels([a], t)) for a in ("Bogus=3", "GraveRobber=16", "GraveRobber=x")]
    check("--pet-level refused: an unknown pet, a level past 15 or not a number",
          got[0].startswith("unknown pet 'Bogus'") and all("a level 1-15" in g for g in got[1:]), str(got))


def test_pet_builds():
    print("== the pets' hits by the wiki level table at the levels of the live hit bars; the phoenix takes the "
          "gravebug-measured numbers through its own armor and resist ==")
    import random
    import statistics
    from sim import pets
    t = pets.load_tamer()
    none = {tab: {} for tab in pets.TABS}
    want = {"GraveRobber": (15, 542), "AlaFlambe": (15, 742), "NightmareA": (14, 499), "NightmareB": (14, 502),
            "a searing imp": (14, 341), "fireminion": (15, 367), "GirthyEarthy": (14, 986), "a tidal mantis": (11, 652)}
    hits = {n: pets.build_pet(n, t, none, "command", 14, [n], level=lv)["hits"] for n, (lv, _) in want.items()}
    check("hits: the Guide's x (1 + 10 % a level to 10, 5 % past it); live bars ~1000, ~480, 331-343, 352-363",
          hits == {n: h for n, (_, h) in want.items()}, str(hits))
    sc = load()
    pets.select(sc, "phoenix_gravebug", "Phoenix/Gravebug 2026-10-15")    # the loadout of the live ratios
    gb, ph = sc["pets"]
    ref, rng = sc["pet_ref"]["default"], random.Random(1)

    def mean(spec, dt):
        return statistics.fmean(pets.taken_factor(spec, pets.REF_TANK, dt, rng, ref, True) for _ in range(20000))
    phys, spell = mean(ph, "physical") / mean(gb, "physical"), mean(ph, "spell") / mean(gb, "spell")
    check("phoenix : gravebug on a monster's melee 1.6-1.85 (live 1.7), on its spells 0.72-0.88 (live 0.79; the "
          "gravebug's Tank-tab resistance puts the model near 0.86)", 1.6 <= phys <= 1.85 and 0.72 <= spell <= 0.88,
          f"{phys:.3f} {spell:.3f}")
    check("the supplies reach 5 (2 + Without Borders tier 3)", sc["vet"]["range"] == 5, str(sc["vet"]["range"]))


def test_anchors_pinned():
    print("== a rescan after a loadout or level change moves the pets, not the live anchors (k_dmg, the phoenix's own "
          "factor, k_speed, the reference pets) ==")
    import copy
    from sim import pets
    from sim.scenario import load as load_sc
    caches = (pets.load_tamer, pets.k_dmg, pets.k_speeds)
    real_load = pets.load_tamer
    before = (pets.k_dmg(), pets.k_override("AlaFlambe"), dict(pets.k_speeds()), load_sc()["pet_ref"]["default"])
    t2 = copy.deepcopy(real_load())
    pg = t2["bestiary"]["loadouts"]["Phoenix/Gravebug"]
    # live 2026-10-25 21:01-21:03: the Attack tab reset and refilled without its cross perks; the Tank tab redone too
    pg["attack"] = {**pg["attack"], "perks": {"Immolate": 2, "Lethality": 1, "Mutation": 2, "Pelt": 2, "Primal": 3}}
    pg["tank"] = {**pg["tank"], "perks": {"Survival": 3, "Hearty": 3, "Guardians": 3}}
    for n in t2["guide"]:
        t2["guide"][n]["level"] = 15                         # every pet at the top level
    for c in caches:
        c.cache_clear()
    pets.load_tamer = lambda character="logan_wolf": t2
    try:
        after = (pets.k_dmg(), pets.k_override("AlaFlambe"), dict(pets.k_speeds()), load_sc()["pet_ref"]["default"])
        sc = load_sc()
        af = next(p for p in sc["pets"] if p["name"] == "AlaFlambe")
        nb = pets.build_pet("NightmareB", t2, {tab: {} for tab in pets.TABS}, "command", 14, ["NightmareB"])
    finally:
        pets.load_tamer = real_load
        for c in caches:
            c.cache_clear()
    check("the anchors and the default reference pet unchanged", after == before, f"{before[:2]} -> {after[:2]}")
    check("...while the phoenix carries tonight's perks (no Swarm, Hunting Party, Breaker) and NightmareB, measured at "
          "1.60 s at level 14, is faster at 15",
          set(af["perks"]) == {"Immolate", "Lethality", "Mutation", "Pelt", "Primal"} and nb["period"] < 1.60 - 0.01,
          f"{sorted(af['perks'])} {nb['period']:.3f}")


def test_pet_mitigation():
    print("== a plain cast meets the target's magic resist; Spellburn and Epic Barrage are physical against half its armor "
          "(#4271, #4259) ==")
    import random
    import statistics
    from sim import pets
    sc = load()
    pets.select(sc, "phoenix_gravebug", "Phoenix/Gravebug 2026-10-15")
    af = sc["pets"][1]
    eb = next(a for a in af["abilities"] if a["name"] == "Epic Barrage")

    def barrage(tgt):
        rng = random.Random(5)
        return statistics.fmean(n for _ in range(3000) for n, _ in pets.ability_numbers(af, eb, tgt, pets.REF_CASTER, rng))

    def cast(tgt):
        rng = random.Random(5)
        return statistics.fmean(pets.spell_numbers(af, tgt, pets.REF_CASTER, rng)[0][0][0] for _ in range(6000))
    bare, resistant, armored = barrage({"armor": 0, "magic_resist": 0}), barrage({"armor": 0, "magic_resist": 200}), \
        barrage({"armor": 120, "magic_resist": 0})
    want = (1 - 60 / 200) / 1.15         # half of 120 at the rule's mean; nothing left to ignore: +15 % (#11687)
    check("Epic Barrage: magic resist 200 changes nothing; armor 120 keeps the share of half of it (0.61 of the bare)",
          abs(resistant / bare - 1) < 1e-9 and abs(armored / bare - want) < 0.02, f"{resistant / bare:.3f} {armored / bare:.3f}")
    r = cast({"armor": 0, "magic_resist": 100}) / cast({"armor": 0, "magic_resist": 200})
    check("a plain cast: magic resist 100 against 200 (Breaker's 10 off) keeps 0.775 / 0.525 of it",
          abs(r - 0.775 / 0.525) < 0.03, f"{r:.3f}")


def test_kdmg_barding():
    print("== k_dmg compares the live median with model hits under the barding live had (Provocation song, discord) ==")
    import copy
    from sim import pets
    real = pets.load_tamer
    t2 = copy.deepcopy(real())
    t2["anchors"]["damage"]["barding"] = {"multipliers": {"1": 1}}
    k = pets.k_dmg()
    for c in (pets.load_tamer, pets.k_dmg):
        c.cache_clear()
    pets.load_tamer = lambda character="logan_wolf": t2
    try:
        k_plain = pets.k_dmg()
    finally:
        pets.load_tamer = real
        for c in (pets.load_tamer, pets.k_dmg):
            c.cache_clear()
    check("without the barding the factor would be 1.2-1.3x higher (107 hits x 1.169, 91 x 1.364)",
          1.2 <= k_plain / k <= 1.3, f"{k:.4f} {k_plain:.4f}")


def test_parity_team():
    print("== parity.py: one team's live visits only, up to --until; another team's death stays with its visit; the kill "
          "time ratio weighs each type by its live kills ==")
    import json
    import random
    import tempfile
    import memory
    from sim import parity
    db = os.path.join(tempfile.mkdtemp(prefix="parity_"), "harness.db")
    store = memory.Memory(db)
    ours, theirs = {"0x000DDEFE": "GraveRobber", "0x0026EF56": "AlaFlambe"}, {"0x000DDEF7": "NightmareB"}
    rows = [(1000, 2000, ours), (2100, 3000, theirs), (3100, 4000, ours)]
    with store.con:
        for a, b, p in rows:
            store.con.execute("INSERT INTO episodes(loop, t_start, t_end, data, char_serial) VALUES('hunt',?,?,?,1)",
                              (a, b, json.dumps({"style": "pets", "t_start": a, "t_end": b, "pets": p,
                                                 "spot": [5230, 700]})))
        for t, kind, d in ((1500, "fight", {"name": "a shade wolf", "fight_s": 5, "outcome": "kill"}),
                           (2500, "death", {"cause": "shade wolf"}),
                           (2600, "fight", {"name": "a bound soul", "fight_s": 9, "outcome": "kill"})):
            store.con.execute("INSERT INTO job_events(t, job, kind, facet, x, y, data, char_serial) "
                              "VALUES(?, 'hunt', ?, 0, 5230, 700, ?, 1)", (t, kind, json.dumps(d)))
    store.close()
    visits, fights = parity.live_visits(db, 0, 1, [0, 0, 0, 0], until=3050, pets={0x000DDEFE, 0x0026EF56},
                                        dungeon=[[5150, 600, 5760, 760]])
    check("ours up to --until: one visit, its fight, no death (the other team's death 500 s after its end)",
          [v["t_start"] for v in visits] == [1000] and not visits[0]["_deaths"]
          and [f["name"] for f in fights] == ["a shade wolf"], f"{[(v['t_start'], v['_deaths']) for v in visits]} {fights}")
    kr = parity.kill_ratio({"a": [10] * 6, "b": [20] * 12}, {"a": [5] * 6, "b": [20] * 12}, random.Random(1))
    check("kill ratio: (0.5^6 x 1^12)^(1/18) = 0.794", abs(kr[0] - 0.5 ** (1 / 3)) < 1e-9 and kr[3] == 2, str(kr))


def test_perk_conditions():
    print("== the bestiary's conditions as the World sees them: Cornered, Pelt, Aggression; the codex's Triage ==")
    from sim import pets
    w = world_with([{"key": "shade wolf", "at": [FLOOR[0] + 3, FLOOR[1], 0]}], team="earth_fire",
                   bestiary="Random 2026-10-24")             # pinned: a later change to Random doesn't move the ranks
    w.t = 100.0
    mon = next(m for m in w.mobs.values() if m.kind == "monster")
    fm = next(p for p in w.pets() if p.name == "fireminion")
    corn, pelt = fm.spec["pv"]["Cornered"], fm.spec["pv"]["Pelt"]
    fm.x, fm.y = mon.x - 2, mon.y
    fm.hurt_by = {mon.serial: w.t - 5}
    hot = pets.damage_bonus(fm.spec, w._pet_ctx(fm, mon))
    fm.hurt_by = {mon.serial: w.t - 15}
    cold = pets.damage_bonus(fm.spec, w._pet_ctx(fm, mon))
    check("Cornered (Random, rank 1): hurt by a creature 5 s ago +7 %, 15 s ago +4 %",
          abs(hot - cold - (corn["dmg_cond"] - corn["dmg"])) < 1e-9 and (corn["dmg_cond"], corn["dmg"]) == (0.07, 0.04),
          f"{hot:.3f} {cold:.3f}")
    fm.x = mon.x - 5
    far = pets.damage_bonus(fm.spec, w._pet_ctx(fm, mon))
    check("Pelt (rank 2): its target 5 tiles off +21 %, 2 tiles off +12 %",
          abs(far - cold - (pelt["dmg_cond"] - pelt["dmg"])) < 1e-9 and (pelt["dmg_cond"], pelt["dmg"]) == (0.21, 0.12),
          f"{far:.3f} {cold:.3f}")
    w = world_with([{"key": "shade wolf", "at": [FLOOR[0] + 3, FLOOR[1], 0]}], bestiary="Phoenix/Gravebug 2026-10-15")
    w.t = 100.0
    mon = next(m for m in w.mobs.values() if m.kind == "monster")
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    agg = gb.spec["pv"]["Aggression"]
    none = (pets.damage_bonus(gb.spec, w._pet_ctx(gb, mon)), pets.dr(gb.spec, w._pet_ctx(gb, mon)))
    gb.hurt_by = {mon.serial: w.t - 2, mon.serial + 1: w.t - 8}
    two = (pets.damage_bonus(gb.spec, w._pet_ctx(gb, mon)), pets.dr(gb.spec, w._pet_ctx(gb, mon)))
    check("Aggression (rank 2): two creatures on it in 10 s: +9 % damage and +3 % resistance each; none: +12 % damage",
          abs(two[0] - none[0] - (2 * agg["dmg_per"] - agg["dmg"])) < 1e-9 and abs(two[1] - none[1] - 2 * agg["dr_per"]) < 1e-9
          and (agg["dmg_per"], agg["dr_per"], agg["dmg"]) == (0.09, 0.03, 0.12), f"{none} {two}")
    gb.hurt_by = {}
    gb.x, gb.y = w.player.x + 1, w.player.y
    base = pets.dr(gb.spec, w._pet_ctx(gb))
    w.vet_end = w.t + 3.0
    ch = pets.dr(gb.spec, w._pet_ctx(gb))
    gb.hits = gb.hits_max * 0.30
    low = pets.dr(gb.spec, w._pet_ctx(gb))
    check("Triage (tier 3): +15 % resistance during a channel within the supplies' reach, +30 % below 33 % hits",
          abs(ch - base - 0.15) < 1e-9 and abs(low - base - 0.30) < 1e-9, f"{base:.3f} {ch:.3f} {low:.3f}")


def test_dreamlull():
    print("== the incubus' Dreamlull: 15 % x Primal of its landed casts hex the target (25 a slot x Scour, its magic resist "
          "off for every caster) and hinder it 1 s a slot (no step, swing or cast); less on a creature of difficulty 100+ ==")
    import random
    from sim import pets
    sc = load()
    pets.select(sc, "phoenix_incubus")
    ph, bus = sc["pets"]
    pv = bus["pv"]
    rng, low = random.Random(3), {"armor": 25, "magic_resist": 25, "difficulty": 50}
    effs = [e for _ in range(20000) for e in pets.spell_numbers(bus, low, pets.REF_CASTER, rng)[1] if e[0] == "dreamlull"]
    want = 0.15 * (1 + pv["Primal"]["passive"])
    p = effs[0][1] if effs else {}
    check(f"a proc on {want:.0%} of the casts (15 % x (1 + Primal's {pv['Primal']['passive']:.0%}))",
          abs(len(effs) / 20000 - want) < 0.012, f"{len(effs)} of 20000")
    check("...each a hex of 25 x (1 + Scour's amount) for 15 s and a 1 s hinder (1 slot)",
          p.get("hex") == 25 * (1 + pv["Scour"]["amount"]) and p.get("seconds") == 15 and p.get("hinder_s") == 1, str(p))
    hard = next((e[1] for _ in range(200) for e in pets.spell_numbers(bus, {**low, "difficulty": 190}, pets.REF_CASTER,
                                                                       rng)[1] if e[0] == "dreamlull"), {})
    check("on difficulty 190 both x (1 - 70 % x 90 / 900) = 0.93 (#5947)",
          abs(hard.get("hinder_s", 0) - 0.93) < 1e-9 and abs(hard.get("hex", 0) - p.get("hex", 0) * 0.93) < 1e-9, str(hard))
    r = random.Random(1)
    keep = (pets.spell_mitigation(ph, {"magic_resist": 50}, r, 50.0), pets.spell_mitigation(ph, {"magic_resist": 250}, r, 200.0),
            pets.spell_mitigation(bus, {"magic_resist": 200}, random.Random(2), 150.0),
            pets.spell_mitigation(bus, {"magic_resist": 200}, random.Random(2)))
    check("a hex takes the resist off every caster's spell: 50 hexed 50 keeps all; at most 150 off with Breaker's (#4223)",
          keep[0] == 1.0 and keep[1] < 1.0 and abs(keep[2] - keep[3]) > 1e-9
          and abs(keep[2] - pets.spell_mitigation(bus, {"magic_resist": 200}, random.Random(2), 100.0)) < 1e-12, str(keep))
    w = world_with([{"key": "shade wolf", "at": [FLOOR[0] + 1, FLOOR[1], 0]}], team="phoenix_incubus")
    mon = next(m for m in w.mobs.values() if m.kind == "monster")
    b = next(m for m in w.pets() if m.name == "Bussin")
    for m in w.pets():
        m.mode, m.target = "stay", None
        m.x, m.y = w.player.x - 15, w.player.y          # out of the fight
    w.run_until(1.0)
    mon.target, mon.next_think, mon.next_swing = w.player.serial, 1e9, w.t
    w._pet_effects(b, mon, [("dreamlull", {"hex": 50.0, "seconds": 15, "hinder_s": 1.0})])
    t0, due, at = w.t, mon.next_swing, mon.pos
    w.run_until(t0 + 0.9)
    held = (mon.next_swing == due, mon.pos == at)
    w.run_until(t0 + 1.2)
    check("hindered 1 s beside Logan with its swing due: no swing in 0.9 s, the swing at its end",
          held == (True, True) and mon.next_swing > due, f"{held} next swing {due:.2f} -> {mon.next_swing:.2f}")
    mon.x, mon.y, mon.path = w.player.x + 4, w.player.y, []
    mon.next_step = w.t
    w._pet_effects(b, mon, [("dreamlull", {"hex": 50.0, "seconds": 15, "hinder_s": 1.0})])
    t0, at = w.t, mon.pos
    w.run_until(t0 + 0.9)
    still = mon.pos == at
    w.run_until(t0 + 1.5)
    check("...4 tiles off on Logan: rooted 1 s, then it closes in", still and mon.pos != at, f"{at} -> {mon.pos}")
    ctx = w._pet_ctx(w.pets()[0], mon)
    check("the hex (50) is on it for the phoenix's casts too", ctx.target_hex == 50.0, str(ctx.target_hex))


def test_opportunity_punishment():
    print("== Opportunity and Punishment by the gump's text: for the incubus (hinder, hex) their conditions, for a "
          "species without them the flat damage ==")
    from sim import pets
    t = pets.load_tamer()
    perks = {"Opportunity": 3, "Punishment": 2}
    ph = pets.build_pet("AlaFlambe", t, {"attack": perks}, "command", 14, ["AlaFlambe"])
    bus = pets.build_pet("Bussin", t, {"utility": perks}, "command", 14, ["Bussin"])
    hot, cold = pets.HitCtx(hinder_10s=True), pets.HitCtx()
    check("the phoenix (no hinder, no hex): +25 % and +15 % flat, hindered or not; no number at a hex's end",
          abs(pets.damage_bonus(ph, cold) - 0.40) < 1e-9 and abs(pets.damage_bonus(ph, hot) - 0.40) < 1e-9
          and pets.punishment(ph) == 0.0, f"{pets.damage_bonus(ph, cold):.3f} {pets.damage_bonus(ph, hot):.3f}")
    check("the incubus: Opportunity +100 % only within 10 s of its hinder on the target, else 0; Punishment no bonus",
          abs(pets.damage_bonus(bus, hot) - 1.00) < 1e-9 and abs(pets.damage_bonus(bus, cold)) < 1e-9,
          f"{pets.damage_bonus(bus, hot):.3f} {pets.damage_bonus(bus, cold):.3f}")
    check("...Punishment: DamageMax x 1.5 when its hex ends", abs(pets.punishment(bus) - bus["damage_max"] * 1.5) < 1e-9,
          f"{pets.punishment(bus):.1f} of max {bus['damage_max']:.1f}")
    w = world_with([{"key": "shade wolf", "at": [FLOOR[0] + 3, FLOOR[1], 0]}], team="phoenix_incubus")
    w.run_until(1.0)
    mon = next(m for m in w.mobs.values() if m.kind == "monster")
    b = next(m for m in w.pets() if m.name == "Bussin")
    for m in w.pets():
        m.mode, m.target = "stay", None
        m.x, m.y = w.player.x - 15, w.player.y          # out of the fight: no cast of its own refreshes the hex
    b.hindered = {mon.serial: w.t - 5}
    near = w._pet_ctx(b, mon).hinder_10s
    b.hindered = {mon.serial: w.t - 11}
    check("the World: a hinder 5 s ago counts, 11 s ago not", near and not w._pet_ctx(b, mon).hinder_10s)
    b.spec = {**b.spec, "pv": {**b.spec["pv"], "Punishment": {"punish": 1.5, "dmg": 0.15}}}
    mon.hits = mon.hits_max = 1e6
    hurts = _log_hurts(w)
    w._pet_effects(b, mon, [("dreamlull", {"hex": 50.0, "seconds": 15, "hinder_s": 1.0})])
    t0 = w.t
    w.run_until(t0 + 15.3)
    pun = [h for h in hurts if h[1] is mon and h[2] == "punishment"]
    check("...its hex ends 15 s on: one Punishment number on the monster", len(pun) == 1 and abs(pun[0][0] - t0 - 15) < 0.2
          and mon.hex == 0.0, f"{[(h[0], h[3]) for h in pun]} hex from {t0:.1f}")


def test_vet_codex():
    print("== the supplies' heal: the wiki's formula x Specialist and Without Borders x the pet's healing perks; "
          "Overheal 40 % more in 3 ticks ==")
    from sim import pets
    w = world_with([], team="nightmare_fire")
    link, clock = link_for(w)
    nb = next(p for p in w.pets() if p.name == "NightmareB")
    for p in w.pets():
        p.mode = "stay"
        p.x, p.y = (w.player.x + 1, w.player.y) if p is nb else (w.player.x + 9, w.player.y)
    nb.hits = round(nb.hits_max * 0.3)                  # room for the heal and its Overheal
    over = _log_overhead(w)
    vet = next(s for s, it in w.items.items() if it.get("kind") == "vet")
    link.send(actions.dclick(vet))
    clock.advance(10.0)
    link._pump()
    plus = [(t, int(x[1:])) for t, s, x in over if s == nb.serial and x.startswith("+")]
    sk = w.sc["vet"]["skill"]
    f = sk["veterinary"] / 100 * (1 + 0.2 * sk["lore"] / 100) * 0.75 * (1 + 0.40 + 0.10) * pets.heal_mult(nb.spec)
    ok = len(plus) >= 4
    first, ticks = (plus[0], plus[1:4]) if ok else ((0, 0), [])
    check("one patient: Vet/100 x U(80, 120) x (1 + 0.2 x Lore/100) x 75 % x (1 + 40 % + 10 %) x its perks "
          f"({80 * f:.0f}-{120 * f:.0f})", ok and 80 * f - 1 <= first[1] <= 120 * f + 1, str(plus))
    check("then Overheal: 3 ticks 1, 2 and 3 s after, 40 % of the heal in all (± 2 %)",
          ok and [round(t - first[0]) for t, _ in ticks] == [1, 2, 3]
          and 0.38 <= sum(n for _, n in ticks) / first[1] <= 0.42, str(plus))


def test_team_run():
    print("== run.py --team: another team hunts (the runner with three pets) ==")
    from sim import run as simrun
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        r = simrun.run_once(1, [], minutes=0.2, at_hunt=True, team="nightmare_fire")
    check("the team, its loadout and its three pets in the summary",
          r["outcome"] == "done" and r["team"] == ["NightmareA", "NightmareB", "a searing imp"]
          and r["bestiary"] == "Nightmare/Fire" and set(r["pets_end"]) == {"NightmareA", "NightmareB", "a searing imp"},
          f"{r['outcome']} {r['error']} {r.get('team')} {r.get('bestiary')} {r.get('pets_end')}")


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


TIDAL_L1 = (6653, 2097)       # Tidal Tomb: the recall arrival on level 1, 2 tiles from the golden moongate
TIDAL_L2 = (6943, 2004)       # ...level 2's arrival (under water: the scenario's `oxygen`)
TIDAL_L3 = (7162, 2221)       # ...level 3's arrival, 11 tiles from its Recovery Dolphin (7164, 2210)


def tidal_world(start, spawns=(), seed=1, z=None, team=None):
    sc = load("tidal_tomb")
    if team:
        from sim import pets
        pets.select(sc, team, None)
    sc["spawns"] = [{**s, "respawn_s": [9999, 9999]} for s in spawns]
    sc["defaults"]["spawn_spread"] = sc["defaults"]["wander_per_s"] = 0
    sc["player"]["start"] = [start[0], start[1], z if z is not None else {TIDAL_L1: -16, TIDAL_L3: -50}.get(start, -38)]
    return World(sc, seed=seed)


def test_scenario_inherit():
    print("== a scenario's `inherit`: the base's blocks it lacks, a dict it has updated by its own, a missing key refused; "
          "the instrument quality played: the child's over the base's, an unknown one refused ==")
    import json
    import tempfile
    d = tempfile.mkdtemp(prefix="sim_inherit_")
    kb = {"creatures": {"rat": {"name": "a rat", "hits": 10, "melee_damage": [1, 2], "ai": "melee"}}}
    base = {"evidence": {"kb": "kb.json", "observed": "none.json"}, "player": {"name": "Logan", "start": [1, 1, 0]},
            "pets": [{"name": "Rex"}], "defaults": {"a": 1, "b": 2}, "spawns": [],
            "bard": {"skill": 149, "instrument": "fine", "instrument_bonus": {"fine": 20, "plain": 0},
                     "difficulty": "bard.json"}}
    child = {"evidence": {"kb": "kb.json", "observed": "none.json"}, "player": {"start": [9, 9, 0]},
             "defaults": {"b": 3}, "spawns": [],
             "inherit": {"from": "base", "keys": ["player", "pets", "defaults", "bard"]}}
    bad = {**child, "inherit": {"from": "base", "keys": ["vet"]}}
    played = {**child, "bard": {"instrument": "plain"}}
    unknown = {**child, "bard": {"instrument": "golden"}}
    fit = {"kinds": {"discord": {"types": {}, "median_d": 50.0}}}
    for name, data in (("kb", kb), ("base", base), ("child", child), ("bad", bad), ("played", played),
                       ("unknown", unknown), ("bard", fit)):
        with open(os.path.join(d, f"{name}.json"), "w", encoding="utf-8") as f:
            json.dump(data, f)
    sc = load("child", d)
    check("the base's player name with the child's start; the base's pets; defaults merged child over base",
          sc["player"] == {"name": "Logan", "start": [9, 9, 0]} and sc["pets"] == [{"name": "Rex"}]
          and sc["defaults"] == {"a": 1, "b": 3} and sc["bard"]["skill"] == 149, str(sc["player"]))
    try:
        load("bad", d)
        refused = False
    except ValueError:
        refused = True
    check("a key the base doesn't have is refused", refused)
    b = load("played", d)["bard"]
    check("the child's instrument over the base's, the base's qualities and skill kept",
          b["instrument"] == "plain" and b["instrument_bonus"] == {"fine": 20, "plain": 0} and b["skill"] == 149, str(b))
    try:
        load("unknown", d)
        refused = False
    except ValueError:
        refused = True
    check("an instrument quality not in instrument_bonus is refused", refused)


def test_chain_links():
    print("== Mastery Chain links as an input (--links): the spec and the game's limits; the effective barding skill "
          "sets the chance, the durations and the songs by the live rules ==")
    import copy
    from sim import chain

    def err(spec):
        try:
            chain.parse(spec)
            return ""
        except ValueError as e:
            return str(e)
    cur = chain.parse("current, effective_barding=10:silver")
    check("'current' keeps Logan's other links; a type named replaces his (10 silver Effective Barding: +37.5)",
          cur["follower_damage"] == chain.current()["follower_damage"]
          and cur["effective_barding"] == {"n": 10, "total": 37.5}, str(cur))
    mixed = chain.parse("Follower Damage=2+4:gold")
    check("a wiki name; materials add per link (2 bronze + 4 gold Follower Damage: +16 %, as Logan's six)",
          abs(mixed["follower_damage"]["total"] - 0.16) < 1e-9 and mixed["follower_damage"]["n"] == 6, str(mixed))
    got = {s: err(s) for s in ("effective_barding=11", "effective_barding=10, follower_damage=10, gold_drop=10, "
                               "healing_received=1", "bogus=1", "damage dealt by player=1", "follower_damage=1:platinum",
                               "follower_damage")}
    check("refused: 11 of a type, 31 links, an unknown type, an unmodelled one (with why), a bad material, no count",
          "allows 10" in got["effective_barding=11"] and "holds 30" in got[next(k for k in got if "gold_drop" in k)]
          and "unknown link type" in got["bogus=1"] and "doesn't fight" in got["damage dealt by player=1"]
          and "material" in got["follower_damage=1:platinum"] and "TYPE=N" in got["follower_damage"], str(got))
    sc = load()
    check("Logan's effective barding: 80 skill + supremely melodious 25 + his six links' 18", bard_eff(sc) == 123,
          str(bard_eff(sc)))
    # the live answers (bard_observed durations): discord 71 s at E 118, 86 s on a provoked one; peace 43 s on a
    # discorded one, 36 s on another; at E 123 discord 74 s, peace on a discorded one 45 s
    want = {("discord", 118): 71, ("discord", 143): 86, ("peace", 143): 43, ("peace", 118): 36, ("provoke", 118): 36,
            ("discord", 123): 74, ("peace", 148): 45, ("discord", 108): 65, ("peace", 133): 40}
    secs = {k: bard_secs(sc, *k) for k in want}
    check("durations as live: ceil(60 or 30 x E / 100)", secs == want, str(secs))
    long = copy.deepcopy(sc)
    chain.apply(long, "current, barding_durations=10:gold")
    check("10 gold Barding Effect Durations links: +45 %", bard_secs(long, "discord", 123) == 108,
          str(bard_secs(long, "discord", 123)))
    more = copy.deepcopy(sc)
    chain.apply(more, "current, effective_barding=10")
    gain = bard_chance(more, "discord", "shade wolf", bard_eff(more)) - bard_chance(sc, "discord", "shade wolf", 123)
    check("4 more Effective Barding links (+12): +12 points of chance at a shade wolf", abs(gain - 0.12) < 1e-9
          and bard_eff(more) == 135, f"{gain:.4f} at E {bard_eff(more)}")
    texts = []
    for quality, shown in (("eminently melodious", "16.9%"), ("supremely melodious", "18.4%")):
        s2 = copy.deepcopy(sc)
        play(s2, quality)
        w = World(s2, seed=1)
        w._sys = texts.append
        w._song("discord")
        texts.append(shown)
    check("the song prints 15 % x E / 100 cut to a tenth: 16.9 % at E 113, 18.4 % at E 123 (live)",
          f"({texts[1]}" in texts[0] and f"({texts[3]}" in texts[2], str(texts))


def test_tidal_oxygen():
    print("== Tidal Tomb: 732 s under water, then -5 every 6 s down to 66 % and no lower (pets 5 %); level 1 refills ==")
    w = tidal_world(TIDAL_L2)
    hurts = _log_hurts(w)
    pl = w.player
    w.run_until(731.0)
    check("no oxygen damage in the first 732 s (live 2026-10-24: 19:52:11 under, 20:04:23 the first)",
          not any(k == "oxygen" for _, _, k, _ in hurts))
    w.run_until(800.0)
    on_us = [(t, n) for t, v, k, n in hurts if k == "oxygen" and v is pl]
    gaps = [round(b[0] - a[0], 2) for a, b in zip(on_us[:7], on_us[1:7])]
    floor = 0.66 * pl.hits_max
    check("from 732 s, 5 every 6 s (no song cuts it) to 66 %, the 7th only down to it; then only what regenerated",
          len(on_us) >= 7 and 731.9 <= on_us[0][0] <= 732.2 and all(n == 5 for _, n in on_us[:6])
          and 1 <= on_us[6][1] <= 5 and all(g == 6.0 for g in gaps) and all(n <= 2 for _, n in on_us[7:])
          and pl.hits >= floor - 1, f"{on_us} hits {pl.hits} of {pl.hits_max}")
    first = {}
    for t, v, k, n in hurts:
        if k == "oxygen" and v.kind == "pet":
            first.setdefault(v.name, (n, v.hits_max))
    check("each pet's first is 5 % of its hits", set(first) == {p.name for p in w.pets()}
          and all(abs(n - 0.05 * mx) <= 1 for n, mx in first.values()), str(first))
    pl.x, pl.y, pl.z = TIDAL_L1[0], TIDAL_L1[1], -16
    w.run_until(805.0)
    n = len(hurts)
    w.run_until(860.0)
    check("on level 1 the oxygen is refilled: no more deprivation hits", w.under_s == 0.0
          and not any(k == "oxygen" for _, _, k, _ in hurts[n:]), f"under_s {w.under_s}")


def test_tidal_dolphin():
    print("== Tidal Tomb level 3: the Recovery Dolphin refills our oxygen within 12 tiles every ~5.25 s, not from 14 ==")
    # live 2026-10-26 22:47:51-22:48:11: refills every ~5.2 s at 10-12 tiles from it; none at 13+ (all three captures)
    for where, d, near in (((7164, 2214), 4, True), ((7172, 2198), 12, True), ((7178, 2196), 14, False)):
        w = tidal_world(TIDAL_L3)
        hurts = _log_hurts(w)
        said, orig = [], w._sys
        w._sys = lambda text: (said.append((round(w.t, 2), text)), orig(text))
        pl = w.player
        pl.x, pl.y, pl.z = where[0], where[1], -50
        w.under_s = 725.0                                   # 7 s from the deprivation
        w.run_until(40.0)
        refills = [t for t, text in said if text == "Your oxygen is refilled."]
        deprived = [t for t, v, k, n in hurts if k == "oxygen"]
        if near:
            check(f"{d} tiles from it: 'Your oxygen is refilled.' within 5.3 s, then every ~5.25 s; no deprivation",
                  bool(refills) and refills[0] <= 5.3 and all(5.1 <= b - a <= 5.4 for a, b in zip(refills, refills[1:]))
                  and not deprived and w.under_s < 5.3, f"refills {refills} deprived {deprived[:3]}")
        else:
            check(f"{d} tiles off: no refill, the deprivation from 7 s on",
                  not refills and bool(deprived) and 6.9 <= deprived[0] <= 7.2, f"refills {refills} deprived {deprived[:3]}")


def test_tidal_ooze_and_brood():
    print("== Tidal Tomb specials: a noughtilus' ooze aura (4 bursts 0.3 s apart within 2), a nest crab's broodlings ==")
    x, y = TIDAL_L1
    w = tidal_world(TIDAL_L1, [{"key": "oozing noughtilus", "at": [x + 2, y, -16]}])
    hurts = _log_hurts(w)
    m = next(m for m in w.mobs.values() if m.kind == "monster")
    m.hits = m.hits_max = 100000
    m.spec = {**m.spec, "melee": None}
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    gb.hits = gb.hits_max = 100000
    m.target, m.threat = gb.serial, {gb.serial: 1e6}
    w.run_until(30.0)
    ooze = [(t, v, n) for t, v, k, n in hurts if k == "ooze"]
    groups = []
    for t in sorted({t for t, _, _ in ooze}):
        if groups and t - groups[-1][-1] <= 0.35:
            groups[-1].append(t)
        else:
            groups.append([t])
    check("in 30 s: 2+ auras of 4 bursts 0.3 s apart, every hit on one of us within 2 tiles",
          len(groups) >= 2 and all(len(g) == 4 for g in groups[:-1])
          and all(abs(b - a - 0.3) < 0.051 for g in groups for a, b in zip(g, g[1:]))
          and all(sim_cheb(v.pos, m.pos) <= 2 for _, v, _ in ooze), f"{[[round(t, 1) for t in g] for g in groups]}")
    gaps = [round(b[0] - a[0], 1) for a, b in zip(groups, groups[1:])]
    check("...7-12 s apart", bool(gaps) and all(6.9 <= g <= 12.2 for g in gaps), str(gaps))
    w = tidal_world(TIDAL_L1, [{"key": "dungeon-nest crab", "at": [x + 2, y, -16]}])
    said = _log_overhead(w)
    crab = next(m for m in w.mobs.values() if m.kind == "monster")
    crab.hits = crab.hits_max = 100000
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    gb.hits = gb.hits_max = 100000
    crab.target, crab.threat = gb.serial, {gb.serial: 1e6}
    most, first = 0, None
    for _ in range(900):                                    # 90 s
        w.run_until(w.t + 0.1)
        brood = [o for o in w.mobs.values() if o.alive and o.spec.get("summoner") == crab.serial]
        most = max(most, len(brood))
        if brood and first is None:
            first, first_d, first_tgt = brood[0], sim_cheb(brood[0].pos, crab.pos), brood[0].target
    digs = [t for t, s, text in said if s == crab.serial and text == "*digs*"]
    emerge = [t for t, s, text in said if first is not None and s == first.serial and text == "*emerges*"]
    check("a pincer crab '*emerges*' beside the nest crab 0.5 s (a tick) after its '*digs*', on its target",
          first is not None and first.key == "pincer crab" and first_d <= 1 and first_tgt == gb.serial
          and bool(digs) and bool(emerge) and 0.45 < emerge[0] - digs[0] < 0.65,
          f"{first and first.pos} digs {digs[:2]} emerges {emerge}")
    check("...at most 2 of its own up at once", 1 <= most <= 2, str(most))


def test_tidal_dig():
    print("== Tidal Tomb: a nest crab's Dig: '*digs*', gone at its broodling's 0.5 s, up on its target's tile 1.52-2.06 s "
          "after the dig with a number on each of us within 1 there ==")
    # live (docs/NOTES.md "Tidal Tomb: dead on level 1 on the way out", six captures): 230 digs, back 1.52-2.06 s later
    # (median 1.76) on its target's tile 0-16 tiles off; 316 of 317 of us within 1 of it hit, 4 of 48 at 2, 0 of 99 at
    # 3-4. Live 2026-10-28 22:53:18.84 '*digs*' beside Logan, 19.35 gone, 20.38 up on his tile: -23, -22, -24
    x, y = TIDAL_L1
    w = tidal_world(TIDAL_L1, [{"key": "dungeon-nest crab", "at": [x + 6, y, -16]}])
    hurts, said = _log_hurts(w), _log_overhead(w)
    crab = next(m for m in w.mobs.values() if m.kind == "monster")
    crab.hits = crab.hits_max = 100000
    crab.spec = {**crab.spec, "melee": None, "step_s": 999.0}     # only the Dig hurts; it stays where it stands
    gb, ala = (next(p for p in w.pets() if p.name == n) for n in ("GraveRobber", "AlaFlambe"))
    w._place(gb, (x + 1, y, w.player.z), d=0)
    w._place(ala, (x - 3, y, w.player.z), d=0)
    for o in w.team():
        o.hits = o.hits_max = 100000
        if o is not w.player:
            o.mode = "stay"
    crab.target, crab.threat = w.player.serial, {w.player.serial: 1e9}
    w._warm(crab, True)
    crab.cd["brood"] = w.t + 0.2
    seen = []
    for _ in range(50):                                     # 5 s
        w.run_until(w.t + 0.1)
        seen.append((round(w.t, 2), crab.serial in w.viewing, crab.pos[:2]))
    dig = next((t for t, s, text in said if s == crab.serial and text == "*digs*"), None)
    gone = next((t for t, vis, _ in seen if dig is not None and t > dig and not vis), None)
    back = next((t for t, vis, _ in seen if gone is not None and t > gone and vis), None)
    up_at = next((p for t, _, p in seen if t == back), None)
    hits = [(round(t, 2), v.name, round(n)) for t, v, k, n in hurts if k == "burst"]
    check("'*digs*', gone (0x1D) 0.5 s later, back 1.52-2.06 s after the dig on Logan's tile 6 off",
          dig is not None and gone is not None and 0.45 <= gone - dig <= 0.6 and back is not None
          and 1.5 <= back - dig <= 2.1 and up_at == w.player.pos[:2], f"dig {dig} gone {gone} back {back} at {up_at}")
    check("...at that instant a number on Logan (18-34) and GraveRobber beside him, none on AlaFlambe 3 off; counted",
          sorted(n for _, n, _ in hits) == ["GraveRobber", "Logan Wolf"] and len({t for t, _, _ in hits}) == 1
          and all(18 <= d for _, n, d in hits if n == "Logan Wolf")
          and w.metrics.get("dig", {}).get("hits_us") == 1 and w.metrics["dig"].get("digs") == 1, f"{hits} "
          f"{w.metrics.get('dig')}")


LEAVE_L1 = (6670, 2039, 19)    # Tidal Tomb level 1: Logan's tile at the stop (live 2026-10-28 22:53:01)
SCAV_L1 = (6679, 2029, 19)     # ...the lowtide scavenger in war mode from 22:53:04.5, up the leave route's first steps


def test_tidal_leave_coming():
    print("== Tidal Tomb level 1 (live 22:53 death): a planned walk out with a lowtide scavenger coming in war mode up "
          "its route stops and the pets guard; beside it at 30 hits: the potion, then out of its large swing's reach "
          "before any vet supplies, no area peace from the walk's guard ==")
    # live 2026-10-28 22:53:01-21.9 (capture 20261028_182147; docs/NOTES.md "Tidal Tomb: dead on level 1 on the way out"):
    # the route ran 3 steps north-east into the scavenger with the pets on follow; beside it the guard's area peace
    # (6 s), then its potion and the vet supplies on Bussin (89 %) before walk_in_fight's order; dead at 21.93
    import loop_hunt
    from agent_link import cheb
    w = tidal_world(LEAVE_L1[:2], [{"key": "lowtide scavenger", "at": list(SCAV_L1)}], z=LEAVE_L1[2])
    sc = next(m for m in w.mobs.values() if m.key == "lowtide scavenger")
    loop, clock, store, patched = hurt_loop(w, 100)
    loop.link.send(tamer.command("all follow me"))           # live: on follow from 22:53:01.7
    sc.target, sc.threat = w.player.serial, {w.player.serial: 1.0}
    w._warm(sc, True)
    hurts, near, tick0 = _log_hurts(w), [], clock.on_advance

    def tick(now):
        tick0(now)
        if sc.alive and not any(v is sc for _, v, _, _ in hurts):
            near.append(cheb(w.player.pos, sc.pos))          # Logan to it until a pet first hurts it
    clock.on_advance = tick
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            res = loop.leave_by_gate("info")
    finally:
        store.close()
        simclock.uninstall(patched)
    out = buf.getvalue()
    check("'to the moongate: lowtide scavenger in war mode N tiles off coming on the way out; the pets guard'; Logan "
          "5+ from it until the pets hurt it (before: 1, beside it); through the gate alive",
          bool(re.search(r"to the moongate: lowtide scavenger in war mode \d+ tiles off coming on the way out; the pets "
                         r"guard", out)) and near and min(near) >= 5 and res.get("ok") and w.player.alive,
          f"nearest {min(near) if near else None} ok {res.get('ok')} {[ln for ln in out.splitlines() if 'moongate' in ln][:2]}")
    x, y, z = LEAVE_L1[0] + 3, LEAVE_L1[1] - 3, LEAVE_L1[2]  # live 22:53:17.5: Logan at (6673, 2036), it north of him
    w = tidal_world((x, y), [{"key": "lowtide scavenger", "at": [x, y - 1, z]}], z=z)
    sc = next(m for m in w.mobs.values() if m.key == "lowtide scavenger")
    sc.hits = sc.hits_max = 10 ** 6
    sc.spec = {**sc.spec, "step_s": 999.0}
    loop, clock, store, patched = hurt_loop(w, 31)
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    w._place(gb, (x, y + 1, z), d=0)
    gb.hits = int(gb.hits_max * 0.89)
    sc.target, sc.threat = w.player.serial, {w.player.serial: 1.0}
    w._warm(sc, True)
    w._face(sc, w.player)
    w.hurt(w.player, 1, sc)
    loop.walking_out = loop.walk_out_fought = True            # as leave_by_gate's planned walk sets them
    loop.walk_hits, loop.walk_hurt_t = None, -1e9
    saved, loop_hunt.WALK_IN_FIGHT_S = loop_hunt.WALK_IN_FIGHT_S, 4.0
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            clock.advance(0.2)
            loop.walk_in_fight("to the moongate", "out")
    finally:
        loop_hunt.WALK_IN_FIGHT_S = saved
        store.close()
        simclock.uninstall(patched)
    lines = [ln.split(" ", 1)[-1] for ln in buf.getvalue().splitlines()
             if any(k in ln for k in ("heal potion", "stand-off to", "vet supplies (", "area peace", "retreating"))]
    first = [next((i for i, ln in enumerate(lines) if ln.startswith(k)), 99) for k in ("heal potion", "stand-off to",
                                                                                         "vet supplies (")]
    check("beside it at 31: 'heal potion' first, then 'stand-off to' out of its reach (AREA_R 2), the vet supplies after; "
          "no 'area peace (walking out past ...)' (before: the guard's area peace, potion and vet supplies first)",
          tamer.AREA_R.get("lowtide scavenger") == 2 and tamer.AREA_R.get("dungeon-nest crab") == 2
          and first[0] < first[1] < first[2] and not any("walking out past" in ln for ln in lines), str(lines[:5]))


TT2_CRAB = (6911, 2060, -100)    # Tidal Tomb level 2, live 2026-10-30 18:29: the giant pincer crab that pinched Logan


def test_tidal_pinch():
    print("== Tidal Tomb level 2: a giant pincer crab's pinch from 2 tiles: its number after the swing's, 'Claws pinch "
          "you in place...', Entangle 6 s (buff 20 with its end: the runner's Mover.held), no step meanwhile: the walk "
          "waits it out ==")
    # live 2026-10-30 18:29:29.14-15 (capture 20261030_175703): from (6911, 2060) on Logan at (6910, 2058), -16 then -20
    # with the text, buff 20 'Unable to Move' 6.0 s; the walk's step at 29.18 denied (docs/NOTES.md "Tidal Tomb: level 2
    # death 2026-10-30 18:29")
    import loop_hunt
    from agent_link import Abort
    w = tidal_world((6910, 2058), [{"key": "giant pincer crab", "at": list(TT2_CRAB)}], z=-100)
    crab = next(m for m in w.mobs.values() if m.kind == "monster")
    crab.hits = crab.hits_max = 10 ** 6
    sure = [{**sp, "chance": 1.0} for sp in crab.spec["specials"]]
    crab.spec = {**crab.spec, "hit_chance": 1.0, "specials": sure, "step_s": 999.0, "melee_player": [5, 5]}
    loop, clock, store, patched = hurt_loop(w, 100)
    for p in w.pets():
        w._place(p, (6900, 2050, -100), d=0)
        p.mode, p.next_step = "stay", 1e9
    hurts = _log_hurts(w)
    crab.target, crab.threat = w.player.serial, {w.player.serial: 1.0}
    w._warm(crab, True)
    err, took, held, ends, blocked = None, None, None, None, None
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            clock.advance(0.3)
            st = loop.state()
            held = loop.mover.held(st)
            buff = (st["world"]["buffs"].get(f"0x{w.player.serial:08X}") or {}).get("20") or {}
            ends = (buff.get("ends_t") or 0) - loop_hunt.time.time()
            crab.spec = {**crab.spec, "specials": [{**sp, "chance": 0.0} for sp in sure]}   # one pinch only
            t0, b0, d0 = loop_hunt.time.monotonic(), loop.mover.blocked_count, w.metrics["denied_steps"]
            try:
                loop.mover.walk_to(lambda: (6906, 2058), 0, "retreat", urgent=True, max_moves=21, max_route=21)
            except Abort as e:
                err = str(e)
            took = loop_hunt.time.monotonic() - t0
            blocked = (loop.mover.blocked_count - b0, w.metrics["denied_steps"] - d0)
    finally:
        store.close()
        simclock.uninstall(patched)
    on_me = [(k, round(n)) for _, v, k, n in hurts if v is w.player]
    check("the crab's swing from 2 tiles, then the pinch's own number; Logan held (Mover.held) with the buff's end "
          "~6 s off; the walk waited the hold out (>= 5.5 s), no step sent or blocked, then arrived",
          [k for k, _ in on_me[:2]] == ["melee", "pinch"] and held and 5.4 <= ends <= 6.0 and err is None
          and took >= 5.5 and blocked == (0, 0) and sim_cheb(w.player.pos, (6906, 2058)) == 0,
          f"hurts {on_me[:3]} held {held} ends in {ends:.2f} s err {err!r} took {took:.1f} s blocked {blocked} at "
          f"{w.player.pos}")


TT2_LEAVE = (6901, 2050, -100)   # Tidal Tomb level 2, live 2026-10-30 18:29:25.9: Logan's tile as the walk to the gate began
TT2_PETS = (6902, 2047, -100)    # ...both pets' tile then (on guard after a biolumen shellpod)
TT2_SPAWNS = ({"key": "giant pincer crab", "at": list(TT2_CRAB)}, {"key": "voidfin", "at": [6904, 2061, -100]},
              {"key": "drowned mariner", "at": [6902, 2068, -100]}, {"key": "soulcatcher", "at": [6913, 2045, -100]})
TT2_GATE = ("--leave-gate", "6913", "2071")   # level 2's crimson gate (the live run's --leave-gate)


def test_tidal_leave_crab():
    print("== Tidal Tomb level 2 (live 2026-10-30 18:29 death): the planned walk to the gate stops short of the idle giant "
          "pincer crab by its route and steps back so a pet is the nearest of us; it wakes on the pet, Logan never "
          "pinched, seeds 1-24 ==")
    # live 18:29:25.9-34.9: "to the moongate: route 23 steps from (6901, 2050)", the pets 1-3 behind on follow; at
    # (6910, 2058) the crab 2 off struck and pinched (6 s held), the drowned mariner (-33, -25) and a voidfin (-43) woke
    # on him, dead 5.8 s into the hold. Before (loop_hunt.LEAD False, today's walk): the crab woke on Logan 22 of 24 times
    # 1-3 tiles off, Logan pinched 7 times, 1,241 on him; after: on AlaFlambe 24 of 24 with Logan 8 off, no pinch, 591
    import loop_hunt
    wakes, pinched, fails, led, dmg = [], [], [], 0, 0
    for seed in range(1, 25):
        w = tidal_world(TT2_LEAVE[:2], TT2_SPAWNS, seed=seed, z=TT2_LEAVE[2], team="phoenix_incubus")
        crab = next(m for m in w.mobs.values() if m.key == "giant pincer crab")
        loop, clock, store, patched = hurt_loop(w, 100, TT2_GATE)
        for p in w.pets():
            w._place(p, TT2_PETS, d=0)
        hurts = _log_hurts(w)
        seen, tick0 = [], clock.on_advance

        def tick(now, crab=crab, w=w, seen=seen, tick0=tick0):
            tick0(now)
            if crab.target is not None and not seen:
                seen.append((crab.target == w.player.serial, sim_cheb(w.player.pos, crab.pos)))
        clock.on_advance = tick
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                res = loop.leave_by_gate("info")
        finally:
            store.close()
            simclock.uninstall(patched)
        wakes.append(seen[0] if seen else None)
        pinched += [seed for _, v, k, _ in hurts if v is w.player and k == "pinch"]
        dmg += sum(n for _, v, _, n in hurts if v is w.player)
        led += "for the pets to lead" in buf.getvalue()
        if not (res.get("ok") and w.player.alive and not w.player.ghost):
            fails.append((seed, res.get("error"), round(w.player.hits)))
    on_pet = sum(1 for s in wakes if s is not None and not s[0])
    check(f"'... with us in front; stopping at ... for the pets to lead' in every seed; the crab woke on a pet in each "
          f"(LEAD {loop_hunt.LEAD}); Logan never pinched, alive and through the gate (before: on Logan 22 of 24, "
          f"pinched 7 times, 1,241 on him)",
          led == 24 and on_pet == 24 and not pinched and not fails,
          f"led {led} on a pet {on_pet} wakes {wakes} pinched in seeds {pinched} on Logan {round(dmg)} fails {fails}")


def test_tidal_large_swing():
    print("== Tidal Tomb: a lowtide scavenger's large swing every ~7.8 s, its hit 0.5 s later on each of us within 1 of "
          "its target, its pincer crabs ==")
    # live 2026-10-26 20:41:52-20:42:28 (capture 20261026_183552): '*takes large swing*' every 7.6-8.0 s, 14-21 on
    # GraveRobber 0.5 s later, a pincer crab '*emerges*' at that instant while fewer than 2 of its own were up. Its reach
    # (docs/NOTES.md "Tidal Tomb: dead on level 1 on the way out", six captures): everyone within 1 of the tile it faces
    # (its target's), 306 of 314 there, 0 of 137 elsewhere within 3; Logan 20-33
    from agent_link import cheb
    x, y = TIDAL_L1
    w = tidal_world(TIDAL_L1, [{"key": "lowtide scavenger", "at": [x + 2, y, -16]}])
    said, rolled, orig = _log_overhead(w), [], w.hurt
    gb = next(p for p in w.pets() if p.name == "GraveRobber")

    def hurt(victim, amount, by, kind="melee", *a, **kw):
        if by is not None and by.key == "lowtide scavenger":
            # its number before GraveRobber's mitigation, and who of us stood within 1 of the gravebug then
            rolled.append((round(w.t, 2), victim, amount,
                           frozenset(o.serial for o in w.team() if cheb(o.pos, gb.pos) <= 1)))
        orig(victim, amount, by, kind, *a, **kw)
    w.hurt = hurt
    sc = next(m for m in w.mobs.values() if m.kind == "monster")
    sc.hits = sc.hits_max = 100000
    sc.spec = {**sc.spec, "melee": None}                      # only the large swing hurts
    for o in w.team():
        o.hits = o.hits_max = 100000
    sc.target, sc.threat = gb.serial, {gb.serial: 1e6}
    most = 0
    for _ in range(600):                                    # 60 s
        w.run_until(w.t + 0.1)
        most = max(most, sum(1 for o in w.mobs.values() if o.alive and o.spec.get("summoner") == sc.serial))
    swings = [t for t, s, text in said if s == sc.serial and text == "*takes large swing*"]
    gaps = [round(b - a, 2) for a, b in zip(swings, swings[1:])]
    check("in 60 s: 6+ large swings, 7.6-8.0 s apart", len(swings) >= 6 and all(7.55 <= g <= 8.05 for g in gaps),
          f"{len(swings)} swings, gaps {gaps}")
    check("...each lands 0.5 s later on GraveRobber, a 14-21 (the gravebug's live numbers) before its mitigation",
          all(any(0.45 < t - s < 0.65 and v is gb and 14 <= n <= 21 for t, v, n, _ in rolled) for s in swings[:-1]),
          f"{[(t, v.name, round(n)) for t, v, n, _ in rolled[:6]]}")
    lands = sorted({t for t, *_ in rolled})
    hit = {t: {v.serial for u, v, _, _ in rolled if u == t} for t in lands}
    near = {t: next(b for u, _, _, b in rolled if u == t) for t in lands}
    logan = [n for _, v, n, _ in rolled if v is w.player]
    check("...and at that instant on each of us within 1 of it, none farther (Logan's own 20-33)",
          all(hit[t] == near[t] for t in lands) and logan and all(20 <= n <= 33 for n in logan),
          f"{[(t, sorted(hit[t]) == sorted(near[t])) for t in lands[:4]]} Logan {[round(n) for n in logan]}")
    crabs = [t for t, s, text in said if text == "*emerges*"]
    check("...pincer crabs '*emerge*' at a swing's hit, at most 2 of its own up at once",
          bool(crabs) and all(any(0.45 < c - s < 0.65 for s in swings) for c in crabs) and 1 <= most <= 2,
          f"emerges {[round(c, 1) for c in crabs]} swings {[round(s, 1) for s in swings]} most {most}")


ZOA_L2 = (6885, 2068, -100)     # Tidal Tomb level 2, live 2026-10-29 12:36:38-55: the combustive zoa Logan died to...
PETS_L2 = (6895, 2062)          # ...the pets it fought, 10 off...
LOGAN_L2 = (6893, 2065)         # ...and Logan, 3 from them, 8 from it and out of its sight (los.Sight)


def _zoa_case(logan, loop_hits=None, extra=()):
    """The live 12:36 layout: a combustive zoa at ZOA_L2 in war mode on the first pet and held in place, the live team
    (AlaFlambe and Bussin) held at PETS_L2, 10 off, as live, Logan at `logan`, everyone but Logan tough; with `loop_hits`
    a HuntLoop there at those hits (hurt_loop, runner arguments `extra`).
    -> (world, zoa, the scorch's hits [(t, victim, amount)], the loop's (loop, clock, store, patched) or None)."""
    w = tidal_world(logan, [{"key": "combustive zoa", "at": list(ZOA_L2)}], z=ZOA_L2[2], team="phoenix_incubus")
    z = next(m for m in w.mobs.values() if m.kind == "monster")
    z.hits = z.hits_max = 10 ** 6
    z.spec = {**z.spec, "step_s": 999.0}                    # it stays where the live one stood
    run = hurt_loop(w, loop_hits, extra) if loop_hits is not None else None
    for p in w.pets():
        w._place(p, (PETS_L2[0], PETS_L2[1], ZOA_L2[2]), d=0)
        p.hits = p.hits_max = 10 ** 6
        p.mode = "stay"
        p.next_step = 1e9                                   # they stood there live (12:36:42-55), casting
    tgt = w.pets()[0]
    z.target, z.threat = tgt.serial, {tgt.serial: 1e9}
    w._warm(z, True)
    rolled, orig = [], w.hurt

    def hurt(victim, amount, by, kind="melee", *a, **kw):
        if kind == "burst" and by is z:
            rolled.append((round(w.t, 2), victim, amount))
        orig(victim, amount, by, kind, *a, **kw)
    w.hurt = hurt
    return w, z, rolled, run


def test_tidal_scorch():
    print("== Tidal Tomb: a combustive zoa's scorch every ~7.4 s on the pet it targets 10 off and on each of us within 3 "
          "of that pet, its sight of us not needed; none at 4+ ==")
    # live 2026-10-29 12:36:47.02 and 55.14 (docs/NOTES.md "Tidal Tomb: level 2 death 2026-10-29 12:36"): Logan 3 from the
    # pets, 8 from it and out of its sight, -44 and -45; six captures: 86 of 97 of us within 3 of its target hit, 0 of 97
    # at 4+, Logan 30-48
    from agent_link import cheb
    w, z, rolled, _ = _zoa_case(LOGAN_L2)
    for o in w.team():
        o.hits = o.hits_max = 10 ** 6
    w.run_until(w.t + 40.0)
    lands = sorted({t for t, _, _ in rolled})
    gaps = [round(b - a, 2) for a, b in zip(lands, lands[1:])]
    logan = [n for _, v, n in rolled if v is w.player]
    check("in 40 s: 4+ scorches 6.6-8.2 s apart, each on both pets and on Logan 3 from them (30-48), 8 from the zoa "
          "and out of its sight",
          len(lands) >= 4 and all(6.6 <= g <= 8.2 for g in gaps) and len(rolled) == 3 * len(lands)
          and len(logan) == len(lands) and all(30 <= n <= 48 for n in logan)
          and cheb(w.player.pos, z.pos) == 8 and not w.sight.can_see(z.pos, w.player.pos),
          f"lands {lands} gaps {gaps} hits {len(rolled)} Logan {[round(n) for n in logan]}")
    w, z, rolled, _ = _zoa_case((LOGAN_L2[0], LOGAN_L2[1] + 1))
    for o in w.team():
        o.hits = o.hits_max = 10 ** 6
    w.run_until(w.t + 40.0)
    check("...Logan 4 from the pets (8 from the zoa, out of its sight): the pets scorched, Logan never",
          len(rolled) >= 8 and not any(v is w.player for _, v, _ in rolled)
          and cheb(w.player.pos, (PETS_L2[0], PETS_L2[1])) == 4,
          str([(t, v.name, round(n)) for t, v, n in rolled][:6]))


def test_scorch_stand_off():
    print("== the live 12:36 layout: a combustive zoa fighting the pets 10 off, Logan 3 from them and out of its sight: "
          "the stand-off takes him 4+ off the pets, no scorch on him ==")
    # live 2026-10-29 12:36:42-55 (hunt-20261029-122604-bc07): "keeping out of the reach of the awake combustive zoa"
    # (its sight of our tile: none), "waiting out of it" 3 from the pets; scorched -44 and -45 (and its lightning), dead.
    # Before tamer.TARGET_AREA no rule kept us off the pets of a hit centred on its target: he stood there and was
    # scorched every ~7.4 s. No Peacemaking here: a peace on it stops the scorch for 45 s and the test with it
    from agent_link import cheb
    w, z, rolled, (loop, clock, store, patched) = _zoa_case(LOGAN_L2, loop_hits=100, extra=("--no-peace",))
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            t0 = tamer.time.monotonic()
            while tamer.time.monotonic() - t0 < 40 and w.player.alive:
                loop.tick()
    finally:
        store.close()
        simclock.uninstall(patched)
    out = buf.getvalue()
    on_us = [(t, round(n)) for t, v, n in rolled if v is w.player]
    lands = {t for t, _, _ in rolled}
    check("in 40 s the pets were scorched 4+ times, Logan at most twice (on the bard step's walks through the reach to "
          "see the zoa and the cover's back out); he ends 4+ from the pets; 'area hit on its target: the awake combustive "
          "zoa ... keeping 4+ off the pets within 12 of it' (before: he stood 2-3 off, scorched every time, 5 of 5)",
          len(lands) >= 4 and len(on_us) <= 2 and w.player.alive
          and cheb(w.player.pos, (PETS_L2[0], PETS_L2[1])) >= 4
          and "area hit on its target: the awake combustive zoa" in out and "keeping 4+ off the pets within 12" in out,
          f"Logan scorched {on_us}, ends {w.player.pos} ({cheb(w.player.pos, (PETS_L2[0], PETS_L2[1]))} from the pets) "
          f"{[ln for ln in out.splitlines() if 'stand-off' in ln or 'area hit' in ln][:3]}")


def test_potion_after_vet():
    print("== a heal potion right after another item use (the vet supplies' click): refused with 500119, clicked again "
          "and drunk; a refusal is no drink for the potion clock ==")
    # live 2026-10-29 12:36:47.30 the vet supplies' click, 47.56 the potion's, "You must wait to perform another action."
    # at 47.61; the runner logged "heal potion: 56 -> 56 hits", its clock counted a drink and no potion went out again
    # before the death at 55.1 (23 hits at 51.8)
    import healing
    import loop_hunt
    from agent_link import serial_of
    w = world_with([], seed=7, start=(5209, 697))
    loop, clock, store, patched = hurt_loop(w, 56)
    buf = io.StringIO()
    out = {}
    try:
        with contextlib.redirect_stdout(buf):
            clock.advance(1.0)
            st = loop.state()
            potion = serial_of(healing.heal_potions(st["world"], loop.me(st))[0][0])
            w.last_use_t = w.t                           # an item use just now
            out["drunk"] = loop.drink(st, potion)
            out["hits"] = w.player.hits
            clock.advance(1.0)
            w.last_use_t = w.t
            saved, loop_hunt.DRINK_TRIES = loop_hunt.DRINK_TRIES, 1
            try:
                loop.potions.last = None
                out["once"] = loop.drink(loop.state(), potion)
                out["ready"] = loop.potions.ready(tamer.time.monotonic())
            finally:
                loop_hunt.DRINK_TRIES = saved
    finally:
        store.close()
        simclock.uninstall(patched)
    log = buf.getvalue()
    check("refused, 'clicking it again in 0.6 s', drunk: 56 -> 80+ (before: 'heal potion: 56 -> 56 hits', nothing drunk)",
          out["drunk"] and out["hits"] >= 80 and "clicking it again in 0.6 s" in log, f"{out} {log.splitlines()[-4:]}")
    check("...refused on its only try: 'heal potion not drunk', the clock still ready (before: counted as drunk, no "
          "potion for its cooldown)",
          out["once"] is False and out["ready"] and "heal potion not drunk: 'You must wait" in log, str(out))


def test_patrol_refused_hold():
    print("== a patrol walk refused before its first step for the same reason again: one 'stopped' line and a hold, "
          "not the walk again at once ==")
    # live 2026-10-29 12:36:42-43 (hunt-20261029-122604-bc07): "patrol to (6861, 2054): stopped (would wake 0.4 more onto
    # 3.0 (budget 3))" 100 times in a second, 329 at 12:35:39-42: no other rule takes that refusal up, so the tick
    # walked again at once
    import crawl
    w = world_with([], seed=7, start=(5253, 653))
    loop, clock, store, patched = hurt_loop(w, 100)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            loop.crawl = c = crawl.Crawl(loop, crawl.load_prior(store.con, loop.spot, loop.args.crawl_depth_risk))
            c.setup(loop.state())
            loop.patrol_stop = lambda st: "would wake 0.4 more onto 3.0 (budget 3)"
            start = tuple(w.player.pos[:2])
            t0 = tamer.time.monotonic()
            for _ in range(20):
                c.idle(loop.state())
            spent = tamer.time.monotonic() - t0
    finally:
        store.close()
        simclock.uninstall(patched)
    lines = [ln for ln in buf.getvalue().splitlines() if ": stopped (would wake 0.4 more" in ln]
    check("20 refused walks: one 'stopped' line, a 0.4 s hold on each repeat, Logan where he stood (before: 20 lines, "
          "no time spent)",
          len(lines) == 1 and spent >= 0.4 * 18 and tuple(w.player.pos[:2]) == start,
          f"{len(lines)} lines, {spent:.1f} s, at {w.player.pos}")


def test_zoa_lightning():
    print("== Tidal Tomb: a zoa's lightning on its target and on everyone of us within 4 of it at once, its sight of them "
          "not needed; none at 5; its own cooldown (~87 s for the combustive zoa) ==")
    # live (docs/NOTES.md "Tidal Tomb: the zoas' lightning, the scorch's susceptibility, the shooters' hits"): 45 casts,
    # Logan struck 22 of 22 times within 4 of its target, 0 of 23 at 5+; 2026-10-29 12:36:51.56 and 54.90 -34 and -18, 8
    # from the combustive zoa and out of its sight, 3 from the pets. Before: not modelled (the giant astral zoa's
    # 'volley' on 3 of us at random within 12 of it)
    import collections
    struck = {}
    for d in (3, 4, 5):
        w, z, rolled, _ = _zoa_case((PETS_L2[0] - 2, PETS_L2[1] + d))
        sp = next(s for s in z.spec["specials"] if s.get("id") == "lightning")
        w._burst(z, sp, w.mobs[z.target])
        struck[d] = {(v.name, round(n)) for _, v, n in rolled}
    names = {d: {n for n, _ in v} for d, v in struck.items()}
    pets = {p.name for p in w.pets()}
    logan = [n for d in (3, 4) for name, n in struck[d] if name == w.player.name]
    check("Logan 3 and 4 from the pets it targets (8-9 from it, out of its sight) struck with both pets (21-34); 5 off, "
          "the pets only",
          names[3] == names[4] == pets | {w.player.name} and names[5] == pets and len(logan) == 2
          and all(21 <= n <= 34 for n in logan), f"{struck}")
    w, z, rolled, _ = _zoa_case(LOGAN_L2)
    for o in w.team():
        o.hits = o.hits_max = 10 ** 6
    fired, orig_burst = collections.defaultdict(list), w._burst
    w._burst = lambda mob, sp, tgt=None: (fired[sp.get("id")].append(round(w.t, 1)), orig_burst(mob, sp, tgt))
    w.run_until(w.t + 200.0)
    gaps = [round(b - a, 1) for a, b in zip(fired["lightning"], fired["lightning"][1:])]
    check("...in 200 s of the fight its lightning 2-3 times, 78-96 s apart, beside its scorch every ~7.4 s",
          2 <= len(fired["lightning"]) <= 3 and all(78 <= g <= 96 for g in gaps) and len(fired["scorch"]) >= 24,
          f"lightning {fired['lightning']} scorches {len(fired['scorch'])}")


def test_scorch_susceptible():
    print("== Tidal Tomb: each scorch makes its victims 8 % more susceptible to the combustive zoas a stack, each "
          "stack's time refreshed by the next, all gone 17 s after the last; a drowned mariner's hits unchanged ==")
    # live (the scorch's note): a pet's scorches in a row x1.10, 1.17, 1.24-1.31, 1.37-1.45, 1.41-1.48, 1.52 the first
    # after 1-6 before; back to the first after a gap of 27-35 s; the mariners' and soulcatchers' numbers on scorched pets
    # 24.4 against 22.4-25.0, 27.0 against 27-28. Before: not modelled (every scorch at the same range)
    import dataclasses
    w, z, rolled, _ = _zoa_case(LOGAN_L2)
    for o in w.team():
        o.hits = o.hits_max = 10 ** 6
    mariner = dataclasses.replace(z, key="drowned mariner", serial=z.serial + 1)

    def taken(by):
        before = w.player.hits
        w.hurt(w.player, 1000, by, "spell")
        return before - w.player.hits
    w.run_until(w.t + 31.0)
    k = len([s for s in w.player.susceptible if s[0] > w.t])
    on_us = len([1 for _, v, _ in rolled if v is w.player])
    ratio = taken(z) / taken(mariner)
    z.cd["scorch"] = 1e9                                   # no more scorches
    w.run_until(w.t + 18.0)
    after = taken(z) / taken(mariner)
    check("after 4+ scorches on Logan (3 from the pets) as many stacks: the zoa's hits x1 + 0.08 a stack, the mariner's "
          "x1; 18 s after the last none, x1",
          on_us >= 4 and k == on_us and abs(ratio - (1 + 0.08 * k)) <= 0.01 and abs(after - 1.0) <= 0.01,
          f"scorches on us {on_us}, stacks {k}, ratio {ratio:.3f}, after {after:.3f}")


def _shooter_case(key, logan, loop_hits=None, extra=()):
    """The 12:36 layout (_zoa_case) with a Tidal Tomb shooter at ZOA_L2 in war mode on the first pet, held in place, its
    shots not diverted to Logan (at_player_p 0) -> (world, shooter, [(t, victim, number rolled)] of its, the loop or
    None)."""
    w = tidal_world(logan, [{"key": key, "at": list(ZOA_L2)}], z=ZOA_L2[2], team="phoenix_incubus")
    m = next(x for x in w.mobs.values() if x.kind == "monster")
    m.hits = m.hits_max = 10 ** 6
    m.spec = {**m.spec, "step_s": 999.0, "ranged": {**m.spec["ranged"], "at_player_p": 0.0}}
    run = hurt_loop(w, loop_hits, extra) if loop_hits is not None else None
    for p in w.pets():
        w._place(p, (PETS_L2[0], PETS_L2[1], ZOA_L2[2]), d=0)
        p.hits = p.hits_max = 10 ** 6
        p.mode = "stay"
        p.next_step = 1e9
    tgt = w.pets()[0]
    m.target, m.threat = tgt.serial, {tgt.serial: 1e9}
    w._warm(m, True)
    hits, orig = [], w.hurt

    def hurt(victim, amount, by, kind="melee", *a, **kw):
        if by is m:
            hits.append((round(w.t, 2), victim, amount))
        orig(victim, amount, by, kind, *a, **kw)
    w.hurt = hurt
    return w, m, hits, run


def test_shooter_hits():
    print("== Tidal Tomb: a drowned mariner's shot every 4.0 s, 55 % of them hitting, and a soulcatcher's spell every "
          "~5.7 s, on their target and on everyone of us within 1 of it; Logan at 2 never; Logan's own numbers ==")
    # live (docs/NOTES.md "Tidal Tomb: the zoas' lightning, the scorch's susceptibility, the shooters' hits"): the mariner
    # 202 shots 4.0 s apart, 112 hitting, 159 of 161 of us within 1 of its target hit, 0 of 132 at 2+, Logan 15-39; the
    # soulcatcher 98 of 106 within 1, 0 of 242 at 2+, Logan 28-37. Before: one target, the mariner every 1.57 s (38-42
    # on Logan), the soulcatcher every 2.73 s (37-48)
    for key, every, lo, hi, share in (("drowned mariner", 4.0, 15, 39, (0.35, 0.75)),
                                       ("soulcatcher", 5.7, 28, 37, (0.75, 1.0))):
        out = {}
        for d in (1, 2):
            w, m, hits, _ = _shooter_case(key, (PETS_L2[0] - d, PETS_L2[1] + d))
            for o in w.team():
                o.hits = o.hits_max = 10 ** 6
            casts = []
            orig_cast = w._cast
            w._cast = lambda mob, tgt, ranged: (casts.append(round(w.t, 2)), orig_cast(mob, tgt, ranged))
            w.run_until(w.t + 120.0)
            out[d] = (casts, hits)
        casts, hits = out[1]
        gaps = [b - a for a, b in zip(casts, casts[1:])]
        lands = sorted({t for t, _, _ in hits})
        logan = [n for _, v, n in hits if v.kind == "player"]
        pet_t = [t for t, v, _ in hits if v.kind == "pet"]
        check(f"{key}: casts every ~{every} s; {share[0]:.0%}-{share[1]:.0%} of them land, each on both pets and on "
              f"Logan 1 from them ({lo}-{hi}); Logan 2 off never",
              len(casts) >= 120 / every * 0.8 and all(every * 0.8 <= g <= every * 1.2 for g in gaps)
              and share[0] <= len(lands) / len(casts) <= share[1] and len(pet_t) == 2 * len(lands)
              and len(logan) == len(lands) and all(lo - 1 <= n <= hi + 1 for n in logan)
              and not any(v.kind == "player" for _, v, _ in out[2][1]) and out[2][1],
              f"casts {len(casts)} lands {len(lands)} Logan {logan[:6]} at 2: "
              f"{sum(1 for _, v, _ in out[2][1] if v.kind == 'player')} of {len(out[2][1])}")


def test_shooter_stand_off():
    print("== a drowned mariner fighting the pets 10 off, Logan 1 from them: the stand-off takes him 2+ off the pets "
          "(tamer.TARGET_AREA from its ranged_observed area_r), out of its hits on them ==")
    # live: 13 of 14 of Logan's mariner hits came with him within 1 of its target, a pet (docs/NOTES.md "Tidal Tomb: the
    # zoas' lightning, the scorch's susceptibility, the shooters' hits"). Before: TARGET_AREA had the zoas only, and the
    # cover out of its sight took him to (6895, 2063), 1 from the pets, for 12 s of its shots
    from agent_link import cheb
    w, m, hits, (loop, clock, store, patched) = _shooter_case("drowned mariner", (PETS_L2[0] - 1, PETS_L2[1] + 1),
                                                              loop_hits=100, extra=("--no-peace",))
    buf, near = io.StringIO(), 0.0
    try:
        with contextlib.redirect_stdout(buf):
            t0 = last = tamer.time.monotonic()
            while tamer.time.monotonic() - t0 < 30 and w.player.alive:
                loop.tick()
                now = tamer.time.monotonic()
                if cheb(w.player.pos, PETS_L2) <= 1:
                    near += now - last
                last = now
    finally:
        store.close()
        simclock.uninstall(patched)
    out = buf.getvalue()
    on_us = [(t, round(n)) for t, v, n in hits if v.kind == "player"]
    check("in 30 s Logan stands within 1 of the pets under 2 s (the first tick's step out) and ends 2+ off; 'area hit on "
          "its target: the awake drowned mariner ... keeping 2+ off the pets within 11 of it' (before: 12 s at 1 from "
          "them, the cover's tile)",
          tamer.TARGET_AREA.get("drowned mariner") == (11, 2) and tamer.TARGET_AREA.get("soulcatcher") == (11, 2)
          and near < 2.0 and cheb(w.player.pos, PETS_L2) >= 2
          and "area hit on its target: the awake drowned mariner" in out and "keeping 2+ off the pets within 11" in out,
          f"within 1 for {near:.1f} s, Logan hit {on_us}, ends {w.player.pos} ({cheb(w.player.pos, PETS_L2)} from the "
          f"pets) {[ln for ln in out.splitlines() if 'stand-off' in ln or 'area hit' in ln or 'cover:' in ln][:3]}")
    check("...the zoas' lightning (one every 22-87 s) widens no zone: the zoas keep their 4 (TARGET_AREA_EVERY_S)",
          tamer.TARGET_AREA.get("combustive zoa") == (12, 4) and tamer.TARGET_AREA.get("astral zoa") == (11, 4)
          and "toxic zoa" not in tamer.TARGET_AREA, str(tamer.TARGET_AREA))


def test_loose_on_us():
    print("== a razorfin beside us attacking us while the pets fight a zoa beside them: it becomes the target and gets "
          "'All Kill' at once ==")
    # simulator level 2 (docs/SIM.md "Tidal Tomb: the zoas' lightning, ..."): 7 of 24 deaths a fin alone on Logan, the
    # pets at full hits 5-10 off on a zoa they fought: the target stayed the zoa (it fought the pets) and nobody went at
    # the fin. Before: pick_target kept the zoa
    import loop_hunt
    w = tidal_world(LOGAN_L2, [{"key": "astral zoa", "at": [PETS_L2[0] + 1, PETS_L2[1], ZOA_L2[2]]},
                               {"key": "razorfin", "at": [LOGAN_L2[0] - 1, LOGAN_L2[1], ZOA_L2[2]]}],
                    z=ZOA_L2[2], team="phoenix_incubus")
    zoa = next(x for x in w.mobs.values() if x.key == "astral zoa")
    fin = next(x for x in w.mobs.values() if x.key == "razorfin")
    loop, clock, store, patched = hurt_loop(w, 100, ("--no-peace",))
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            for p in w.pets():
                w._place(p, (PETS_L2[0], PETS_L2[1], ZOA_L2[2]), d=0)
                p.mode, p.next_step = "stay", 1e9
            for x in (zoa, fin):
                x.hits = x.hits_max = 10 ** 6
                x.spec = {**x.spec, "step_s": 999.0}
                w._warm(x, True)
            zoa.target, fin.target = w.pets()[0].serial, w.player.serial
            clock.advance(0.5)
            now = tamer.time.monotonic()
            loop.hurt_t.update({zoa.serial: now, w.pets()[0].serial: now, w.player.serial: now})
            loop.engaged = zoa.serial
            st = loop.state()
            picked = loop.pick_target(st)
            loop.fight(picked)
            said = [p.kill for p in w.pets()]
    finally:
        store.close()
        simclock.uninstall(patched)
    check("the razorfin on us is the target (not the zoa the pets fight) and the pets are sent at it at once: "
          "'... on us and the pets on ...: it is the target now' (before: the zoa kept)",
          picked == fin.serial and fin.serial in said and "it is the target now" in buf.getvalue()
          and loop_hunt.LOOSE_R == 1,
          f"picked {picked:#x} (fin {fin.serial:#x}, zoa {zoa.serial:#x}), the pets' kill {said}, "
          f"{buf.getvalue().splitlines()[-3:]}")


BEDROCK = (6886, 1957, -126)    # Tidal Tomb level 2's north strip, a live bedrock spawn (20261024_102346 20:53:13)


def test_tidal_bedrock_spray():
    print("== Tidal Tomb: an ocean bedrock's spray down its frontal cone at what it fights every ~7.8 s; beside it no "
          "hit; provoked, only on its provoke target ==")
    # live (docs/NOTES.md "The ocean bedrock's spray"): Logan hit 6 of 6 times in the cone (29-48), 0 of 20 outside;
    # provoked, its waves hit only the grouper it was set on (11 of ours in them untouched)
    def setup(logan, grouper=False):
        spawns = [{"key": "ocean bedrock", "at": list(BEDROCK)}]
        if grouper:
            spawns.append({"key": "ironscale grouper", "at": [BEDROCK[0], BEDROCK[1] - 1, BEDROCK[2]]})
        w = tidal_world(logan, spawns, z=BEDROCK[2])
        rolled, orig = [], w.hurt

        def hurt(victim, amount, by, kind="melee", *a, **kw):
            if kind == "spray":
                rolled.append((round(w.t, 2), victim, amount))
            orig(victim, amount, by, kind, *a, **kw)
        w.hurt = hurt
        mobs = {o.key: o for o in w.mobs.values() if o.kind == "monster"}
        b = mobs["ocean bedrock"]
        gb = next(p for p in w.pets() if p.name == "GraveRobber")
        w._place(gb, (BEDROCK[0], BEDROCK[1] + 1, BEDROCK[2]), d=0)
        for o in [b, w.player, *w.pets(), *mobs.values()]:
            o.hits = o.hits_max = 10 ** 6
        for p in w.pets():
            p.mode = "stay"
        if grouper:
            g = mobs["ironscale grouper"]
            b.provoke, g.provoke = (g.serial, 1e9), (b.serial, 1e9)
            b.target, g.target = g.serial, b.serial
            w._warm(g, True)
        else:
            b.target, b.threat = gb.serial, {gb.serial: 1e9}
        w._warm(b, True)
        w.run_until(w.t + 40.0)
        return w, b, rolled
    w, b, rolled = setup((BEDROCK[0], BEDROCK[1] + 4))
    on_us = [(t, n) for t, v, n in rolled if v is w.player]
    on_gb = [t for t, v, n in rolled if v.name == "GraveRobber"]
    gaps = [round(b_ - a, 2) for a, b_ in zip(on_gb, on_gb[1:])]
    check("in the cone 4 tiles in front: 4+ sprays in 40 s 7.5-8.0 s apart, each on Logan 29-48 0.3 s after the tank",
          len(on_gb) >= 4 and all(7.45 <= g <= 8.05 for g in gaps) and len(on_us) == len(on_gb)
          and all(29 <= n <= 48 for _, n in on_us) and all(abs(t - g - 0.3) < 0.06 for (t, _), g in zip(on_us, on_gb)),
          f"tank {on_gb} gaps {gaps} us {on_us}")
    w, b, rolled = setup((BEDROCK[0] + 2, BEDROCK[1]))
    check("...beside it (2 east, the tank south): the tank sprayed, Logan never",
          any(v.name == "GraveRobber" for _, v, _ in rolled) and not any(v is w.player for _, v, _ in rolled),
          str([(t, v.name, round(n)) for t, v, n in rolled][:6]))
    w, b, rolled = setup((BEDROCK[0], BEDROCK[1] - 4), grouper=True)
    check("...provoked onto the grouper north of it, Logan 4 north in its waves: only the grouper hit",
          len(rolled) >= 4 and all(v.key == "ironscale grouper" for _, v, _ in rolled),
          str([(t, v.name, round(n)) for t, v, n in rolled][:6]))


def test_spray_stand_off():
    print("== the runner keeps out of an awake ocean bedrock's frontal cone (its spray) while the pets fight it ==")
    # user 2026-10-26: "I stand behind the ocean bedrock so it doesn't do its spray attack on me"
    w = tidal_world((BEDROCK[0], BEDROCK[1] + 3), [{"key": "ocean bedrock", "at": list(BEDROCK)}], z=BEDROCK[2])
    b = next(o for o in w.mobs.values() if o.kind == "monster")
    b.hits = b.hits_max = 10 ** 6
    loop, clock, store, patched = hurt_loop(w, 100)
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    w._place(gb, (BEDROCK[0], BEDROCK[1] + 1, BEDROCK[2]), d=0)
    b.target, b.threat = gb.serial, {gb.serial: 1e9}
    w._warm(b, True)
    for p in w.pets():
        p.hits = p.hits_max = 10 ** 6
    sprayed, rolled, orig = [], [], w.hurt

    def hurt(victim, amount, by, kind="melee", *a, **kw):
        if kind == "spray":
            (rolled if victim is w.player else sprayed).append(round(w.t, 1))
        orig(victim, amount, by, kind, *a, **kw)
    w.hurt = hurt
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            t0 = tamer.time.monotonic()
            while tamer.time.monotonic() - t0 < 40 and w.player.alive:
                loop.tick()
            st = loop.state()
            cone = loop.team.spray_cone(st, b.serial, st["world"]["mobiles"][f"0x{b.serial:08X}"], "ocean bedrock",
                                        loop.team.pets(st))
    finally:
        store.close()
        simclock.uninstall(patched)
    check("in 40 s the pets were sprayed, Logan never; he ends out of its cone",
          sprayed and not rolled and tuple(w.player.pos[:2]) not in cone,
          f"pets sprayed at {sprayed[:5]}, Logan at {rolled}, ends {w.player.pos} facing {b.d}")


def _bedrock_case(logan, bedrock, pets, target, spawns=()):
    """Level 2 as live 2026-10-28 (capture 20261028_182147): Logan at `logan`, an ocean bedrock at `bedrock` in war mode
    on `target` ("pet": the first pet, "us": Logan) and held in place, no wave until a test sets its cd, the pets at
    `pets` on stay, everyone tough; `spawns` more monsters (idle)."""
    w = tidal_world(logan[:2], [{"key": "ocean bedrock", "at": list(bedrock)}, *spawns], z=logan[2])
    b = next(o for o in w.mobs.values() if o.key == "ocean bedrock")
    b.hits = b.hits_max = 10 ** 6
    b.spec = {**b.spec, "step_s": 999.0}                    # it stays where the live one stood
    b.cd["spray"] = 1e9
    loop, clock, store, patched = hurt_loop(w, 100)
    for p, xy in zip(w.pets(), pets):
        w._place(p, (xy[0], xy[1], logan[2]), d=0)
        p.hits = p.hits_max = 10 ** 6
        p.mode = "stay"
    tgt = w.pets()[0] if target == "pet" else w.player
    b.target, b.threat = tgt.serial, {tgt.serial: 1e9}
    w._warm(b, True)
    w._face(b, tgt)
    return w, b, loop, clock, store, patched


def _cone_of(loop, b):
    st = loop.state()
    return loop.team.spray_cone(st, b.serial, st["world"]["mobiles"][f"0x{b.serial:08X}"], "ocean bedrock",
                                loop.team.pets(st))


def test_spray_windup_fewest_steps():
    print("== an ocean bedrock winds up with Logan 7 down its cone: out by the fewest steps before the wave, not the "
          "5-step tile by the pets ==")
    # live 2026-10-28 22:18:03-05 (capture 20261028_182147, hunt-20261028-215803-e48f): the bedrock at (6949, 2059) facing
    # west at the pets 3 in front of it, Logan at (6942, 2060) 7 down its cone; "stand-off to (6947, 2056): 5 step(s)",
    # its action 18 at 22:18:04.08 came mid-walk, -42 at 04.97
    w, b, loop, clock, store, patched = _bedrock_case((6942, 2060, -100), (6949, 2059, -126),
                                                      ((6946, 2060), (6946, 2060)), "pet")
    hurts = _log_hurts(w)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            start, cone0 = tuple(w.player.pos[:2]), _cone_of(loop, b)
            b.cd["spray"] = w.t                                # its wind-up now
            while b.spray is None:
                clock.advance(0.05)
            walked = loop.team.stand_off(loop.state())
            clock.advance(2.0)                                 # past the wave's last ring
            cone = _cone_of(loop, b)
    finally:
        store.close()
        simclock.uninstall(patched)
    lines = [ln for ln in buf.getvalue().splitlines() if "spray" in ln or "stand-off" in ln]
    out = next((ln for ln in lines if "ocean bedrock's spray: stepping out of its cone to" in ln), "")
    steps = int(re.search(r", (\d+) step\(s\)", out).group(1)) if out else None
    sprayed = [n for _, v, k, n in hurts if k == "spray" and v is w.player]
    check("from 7 down its cone at the wind-up: 'ocean bedrock's spray: stepping out of its cone to (x, y), N step(s)', "
          "N at most 3, not the live 5; the wave misses Logan; he ends out of the cone",
          start in cone0 and walked and steps is not None and steps <= 3 and not sprayed
          and tuple(w.player.pos[:2]) not in cone, f"{lines[:3]} sprayed {sprayed} at {w.player.pos}")


def test_spray_loot_wait():
    print("== a corpse whose every reachable tile within 2 lies in an awake ocean bedrock's cone: no walk in, kept "
          "queued ==")
    # live 2026-10-28 22:18:08-13 (capture 20261028_182147): the shellpod's corpse at (6944, 2060), the bedrock at
    # (6948, 2060) facing the pets at (6946, 2059-2060); Logan at (6949, 2056), out of the cone, walked "to the corpse" 4
    # steps to (6945, 2059), 3 down it (the tiles south of the corpse are wall), and its wave took -34
    w, b, loop, clock, store, patched = _bedrock_case(
        (6949, 2056, -126), (6948, 2060, -126), ((6946, 2060), (6946, 2059)), "pet",
        [{"key": "biolumen shellpod", "at": [6944, 2060, -107]}])
    pod = next(o for o in w.mobs.values() if o.key == "biolumen shellpod")
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            w.die(pod, w.pets()[0])
            corpse = next(s for s, it in w.items.items() if it.get("kind") == "corpse" and it["owner"] == pod.serial)
            clock.advance(0.5)
            start = tuple(w.player.pos[:2])
            spot, why = loop.team.loot_tile(loop.state(), (6944, 2060))
            c = {"mob": pod.serial, "name": "a biolumen shellpod", "x": 6944, "y": 2060, "t": tamer.time.monotonic(),
                 "corpse": corpse}
            loop.corpses.append(c)
            loop.loot(c)
            cone = _cone_of(loop, b)
    finally:
        store.close()
        simclock.uninstall(patched)
    said = [ln for ln in buf.getvalue().splitlines() if "looting" in ln or "corpse" in ln]
    check("loot_tile: a wait, 'the corpse at (6944, 2060) lies in the ocean bedrock's spray cone, ...'",
          spot is None and bool(why) and why.startswith("the corpse at (6944, 2060) lies in the ocean bedrock's spray "
                                                        "cone"), f"{spot} {why}")
    check("the loot: 'not looting ... yet: the corpse at (6944, 2060) lies in the ocean bedrock's spray cone'; kept "
          "queued, Logan where he stood, out of the cone",
          any("not looting" in ln and "spray cone" in ln for ln in said) and c in loop.corpses
          and tuple(w.player.pos[:2]) == start and start not in cone, f"{said[:2]} at {w.player.pos}")


def test_spray_hold_awake():
    print("== an ocean bedrock wakes 10 tiles off coming at Logan: the hold goes on ('letting it come'), no patrol into "
          "its cone; a walk's route bends round an awake bedrock's cone ==")
    # live 2026-10-28 22:04:44-48 (capture 20261028_182147): "holding here: ocean bedrock in sight at 10 tiles: letting it
    # come"; it woke at 22:04:46 facing Logan, the hold ended, "patrol to (6955, 2055): route 7 steps" into its cone,
    # stopped at the pull range, the stand-off out, the patrol back in; its wave at 46.92, -42 at 48.36
    import nav
    w, b, loop, clock, store, patched = _bedrock_case((6958, 2047, -126), (6953, 2057, -126),
                                                      ((6957, 2047), (6958, 2046)), "us")
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            clock.advance(0.2)
            st = loop.state()
            loop.team.update(st)
            hold = loop.team.hold_reason(st)
            stop = loop.team.pull_check(st)                    # the patrol's and an advance's stop
    finally:
        store.close()
        simclock.uninstall(patched)
    check("hold_reason: 'an awake ocean bedrock at N tiles, out of its spray cone: letting it come'; an advance (the "
          "patrol, an approach) stops on it",
          bool(re.fullmatch(r"an awake ocean bedrock at (9|10) tiles, out of its spray cone: letting it come", hold or ""))
          and stop == hold, f"hold {hold!r} stop {stop!r}")
    # the north strip (test_spray_stand_off's spawn): the bedrock facing the tank south of it, its cone wall to wall from 5
    # south; Logan 5 west and 4 south of it beside the cone, bound for 5 east: the straight way, 10 steps, crosses the cone
    x, y, z = BEDROCK
    w, b, loop, clock, store, patched = _bedrock_case((x - 5, y + 4, z), BEDROCK, ((x, y + 1), (x, y + 1)), "pet")
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            clock.advance(0.2)
            st = loop.state()
            loop.team.update(st)
            cone = set(loop.team.spray_tiles(st))
            avoid = set(loop.mover.avoid.get("spray", ()))
            goal = (x + 5, y + 4)
            path, _ = loop.mover.plan(st, nav.within(goal, 0))
            loop.mover.avoid.clear()
            straight, _ = loop.mover.plan(st, nav.within(goal, 0))
    finally:
        store.close()
        simclock.uninstall(patched)
    inside = [tuple(t[:2]) for t in (path or []) if tuple(t[:2]) in cone]
    crossed = [tuple(t[:2]) for t in (straight or []) if tuple(t[:2]) in cone]
    check("the cone among the Mover's avoid tiles; the route to the far side goes round it, at most its edge tile by the "
          "wall (without them: through it)",
          bool(cone) and avoid == cone and (x - 5, y + 4) not in cone and goal not in cone and path is not None
          and len(inside) <= 1 and len(crossed) >= 4,
          f"{len(cone)} cone tiles, path {path and len(path)} steps, in the cone {inside}; without "
          f"{straight and len(straight)} steps, {len(crossed)} in it")


BUBBLES_AT = (6950, 2010)      # Tidal Tomb level 2: Logan's loot tile when the bubbles hit him (live 2026-10-28 21:13:48-58)


def test_tidal_bubbles():
    print("== Tidal Tomb: a clearwater grouper's volleys of 8 bubbles 7.5-9 s apart; each pops ~1.9 s after it lands, on "
          "each of us within 1 of it, once a volley ==")
    # live (docs/NOTES.md "Tidal Tomb: the clearwater grouper's bubbles"): 282 pops, 1.88-2.04 s after the landing; the
    # 52 hits all within 1 of the bubble, none on one of us hit by a bubble in the 2.5 s before
    x, y = BUBBLES_AT
    w = tidal_world(BUBBLES_AT, [{"key": "clearwater grouper", "at": [x - 1, y + 3, -126]}], z=-126)
    g = next(o for o in w.mobs.values() if o.kind == "monster")
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    w._place(gb, (x - 1, y + 2, -126), d=0)
    for o in [g, w.player, *w.pets()]:
        o.hits = o.hits_max = 10 ** 6
    for p in w.pets():
        p.mode = "stay"
    g.target, g.threat = gb.serial, {gb.serial: 1e9}
    w._warm(g, True)
    lands, pops, hits = [], [], []
    land0, pop0, hurt0 = w._bubble_land, w._bubble_pop, w.hurt

    def land(ms, tile, sp):
        lands.append((w.t, tile[:2]))
        land0(ms, tile, sp)

    def pop(it):
        pops.append((w.t, (it["x"], it["y"])))
        pop0(it)

    def hurt(victim, amount, by, kind="melee", *a, **kw):
        if kind == "bubble":
            hits.append((w.t, victim, victim.pos[:2], amount))
        hurt0(victim, amount, by, kind, *a, **kw)
    w._bubble_land, w._bubble_pop, w.hurt = land, pop, hurt
    w.run_until(w.t + 40.0)
    volleys = []
    for t, _ in lands:
        if volleys and t - volleys[-1][-1] < 3.0:
            volleys[-1].append(t)
        else:
            volleys.append([t])
    gaps = [round(b[0] - a[0], 2) for a, b in zip(volleys, volleys[1:])]
    check("4+ volleys in 40 s of 6-8 bubbles over under 0.8 s, 7.5-9.0 s apart",
          len(volleys) >= 4 and all(6 <= len(v) <= 8 and v[-1] - v[0] < 0.8 for v in volleys)
          and all(7.4 <= gp <= 9.1 for gp in gaps), f"sizes {[len(v) for v in volleys]} gaps {gaps}")
    life = [round(pt - lt, 2) for (lt, lt_tile), (pt, pt_tile) in zip(lands, pops)]
    check("each bubble pops 1.88-2.04 s after it lands", len(pops) >= len(lands) - 8
          and all(1.85 <= s <= 2.1 for s in life), f"{len(lands)} landed, {len(pops)} popped, lives {sorted(life)[:3]}"
          f"..{sorted(life)[-3:]}")
    near = all(any(abs(pt - t) < 1e-6 and max(abs(pos[0] - b[0]), abs(pos[1] - b[1])) <= 1 for pt, b in pops)
               for t, v, pos, n in hits)
    again = [(v.name, round(b - a, 2)) for i, (a, v, _, _) in enumerate(hits) for b, u, _, _ in hits[i + 1:]
             if u is v and b - a < 2.5]
    check("hits on us, each within 1 of a popping bubble, none on one of us twice within 2.5 s",
          hits and near and not again and w.metrics["bubbles"]["thrown"] == len(lands),
          f"{len(hits)} hits ({sorted({v.name for _, v, _, _ in hits})}), near {near}, again {again[:3]}")


MARINER_ROOM = (7418, 1958)   # Tidal Tomb: Marinerbane's room, beside its dolphin (7418, 1957); the boss spawns at (7373, 1957)


def test_tidal_marinerbane():
    print("== Tidal Tomb: Marinerbane, the mini boss: +40,000 hits a damaging player; one move every 12-18.5 s from "
          "5.4-11.5 s in (no cannon crew before 85 s); deep dwellers 1.25-1.31 s after the emote, 4-8 within 12, dying "
          "with it; barrels bursting on each of us within 4; back 300 s after its death ==")
    # live (docs/NOTES.md "Tidal Tomb: Marinerbane's room", capture 20261030_175703): 24 moves 10.3-22.7 s apart, waves of
    # 4-8 at 1-12 tiles 1.25-1.31 s after '*stirs deep dwellers*', 43 of 51 summons alive in the room died in its death
    # tick, barrels 75-91 on the pets at 0-4 tiles and none at 5+, respawn 300.1-300.4 s after each death
    w = tidal_world(MARINER_ROOM, [{"key": "marinerbane", "at": [7373, 1957, 3]}], z=0)
    w.spawns[0]["respawn_s"] = [300, 300]
    boss = next(o for o in w.mobs.values() if o.key == "marinerbane")
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    w._place(gb, (7376, 1957, 2), d=6)
    for o in [w.player, *w.pets()]:
        o.hits = o.hits_max = 10 ** 6
    for p in w.pets():
        p.mode = "stay"
    base = boss.hits_max
    w.hurt(boss, 10, w.player)
    w.hurt(boss, 10, gb)
    other = Mob(0x7FFF0001, "other", "other", "Someone", 400, 7380, 1957, 0)
    w.hurt(boss, 10, other)
    check("100,000 hits; Logan and his pet one player (+40,000), another player +40,000 more",
          base == 100000 and boss.hits_max == 180000 and list(w.metrics["boss_size"].values()) == [2],
          f"{base} -> {boss.hits_max}")
    boss.hits = boss.hits_max = 10 ** 7
    boss.target, boss.threat = gb.serial, {gb.serial: 1e9}
    w._warm(boss, True)
    moves, add0 = [], w._add_summon
    summons = []
    t0 = w.t

    def overhead(m, text, ov0=w._overhead):
        if m is boss and text.startswith("*") and text != "*Increases in Size*":
            moves.append((round(w.t - t0, 2), text))
        ov0(m, text)

    def add(m, sp, tile, with_summoner=False):
        if m is boss:
            summons.append((w.t, sp["key"], max(abs(tile[0] - boss.x), abs(tile[1] - boss.y)), with_summoner))
        add0(m, sp, tile, with_summoner)
    w._overhead, w._add_summon = overhead, add
    w.run_until(w.t + 300.0)
    gaps = [round(b[0] - a[0], 2) for a, b in zip(moves, moves[1:])]
    crews = [t for t, x in moves if x == "*raises a mighty cannon crew*"]
    check("the first move 5.4-11.5 s in, then one every 12-18.5 s; no cannon crew before 85 s",
          moves and 5.3 <= moves[0][0] <= 11.6 and all(11.9 <= g <= 18.6 for g in gaps) and all(t >= 85 for t in crews),
          f"{len(moves)} moves, first {moves[:1]}, gaps {min(gaps, default=None)}-{max(gaps, default=None)}, crews {crews}")
    pool = set(next(mv for sp in boss.spec["specials"] if sp["kind"] == "rotation" for mv in sp["moves"]
                    if mv.get("summon"))["summon"]["pool"])
    waves = [t for t, x in moves if x == "*stirs deep dwellers*"]
    sizes = [[s for s in summons if 1.24 <= s[0] - (t0 + t) <= 1.31 + 0.11] for t in waves]   # due on the next 0.1 s tick
    check("each wave 1.25-1.31 s after '*stirs deep dwellers*': 4-8 of the pool, 1-12 tiles from it, with it",
          waves and all(4 <= len(v) <= 8 for v in sizes) and sum(map(len, sizes)) == len(summons)
          and all(s[1] in pool and 1 <= s[2] <= 12 and s[3] for s in summons),
          f"{len(waves)} waves, sizes {[len(v) for v in sizes]}, keys {sorted({s[1] for s in summons})[:4]}..")
    up = [o for o in w.mobs.values() if o.kind == "monster" and o.spec.get("summoner") == boss.serial]
    boss.hits = 1
    w.hurt(boss, 10, w.player)
    left = [o for o in w.mobs.values() if o.kind == "monster" and o.spec.get("summoner") == boss.serial]
    t_dead = w.t
    w.run_until(w.t + 299.0)
    early = any(o.key == "marinerbane" for o in w.mobs.values())
    w.run_until(w.t + 1.5)
    back = [o for o in w.mobs.values() if o.key == "marinerbane"]
    check("its summons up when it dies die with it; it is back 300 s later",
          up and not left and not early and back and back[0].pos == (7373, 1957, 3),
          f"{len(up)} up, {len(left)} left; back {[o.pos for o in back]} after {round(w.t - t_dead, 1)} s")
    # the barrels, alone: one throw at a time onto one of us, its burst on each of us within 4 of that tile
    w2 = tidal_world(MARINER_ROOM, [{"key": "marinerbane", "at": [7373, 1957, 3]}], z=0, seed=3)
    b2 = next(o for o in w2.mobs.values() if o.key == "marinerbane")
    for o in [w2.player, *w2.pets()]:
        o.hits = o.hits_max = 10 ** 6
    for p in w2.pets():
        p.mode = "stay"
    b2.calm_until = 1e9                  # pacified: no rotation of its own, only the throws below
    for o, (x, y) in ((w2.player, (7381, 1957)), (next(p for p in w2.pets() if p.name == "GraveRobber"), (7383, 1957)),
                      (next(p for p in w2.pets() if p.name != "GraveRobber"), (7387, 1959))):
        w2._place(o, (x, y, w2.stand_z(x, y, 0)), d=6)
    sp = next(mv for s in b2.spec["specials"] if s["kind"] == "rotation" for mv in s["moves"] if mv.get("barrels"))["barrels"]
    hurts = _log_hurts(w2)
    booms, boom0 = [], w2._barrel_boom

    def boom(m, s, tile):
        booms.append((w2.t, tile, {o.serial: max(abs(o.x - tile[0]), abs(o.y - tile[1])) for o in w2.team()}))
        boom0(m, s, tile)
    w2._barrel_boom = boom
    throws = []
    for _ in range(12):
        throws.append(w2.t)
        w2._barrel_throw(b2, sp)
        w2.run_until(w2.t + 1.0)
    flights = [round(t - th, 2) for (t, _, _), th in zip(booms, throws)]
    hit = [(v.serial, n) for _, v, k, n in hurts if k == "barrel"]
    expect = [s for _, _, d in booms for s, dd in d.items() if dd <= 4]
    check("12 barrels, each bursting 0.36-0.61 s after its throw on one of our tiles, a number on each of us within 4 "
          "and on nobody farther", len(booms) == 12 and all(0.35 <= f <= 0.65 for f in flights)
          and sorted(s for s, _ in hit) == sorted(expect) and all(0 in d.values() for _, _, d in booms),
          f"flights {min(flights, default=None)}-{max(flights, default=None)}, {len(hit)} hits, {len(expect)} expected")


def test_bubbles_step_off():
    print("== a clearwater grouper's bubble lands beside us mid-fight, mid-channel: out of its reach before it pops "
          "(stand_off), the pets kept in vet reach; a corpse in its reach looted from a tile out of it ==")
    # live 2026-10-28 21:13:48-58 (capture 20261028_182147, hunt-20261028-211014-9150): Logan looted from (6950, 2010)
    # with bubbles landing at (6950, 2011) and (6949, 2010); -40 and -42 as they popped 1.9 s later. User: "Standing in
    # the clearwater group's exploding bubbles is no good"
    x, y = BUBBLES_AT
    w = tidal_world(BUBBLES_AT, [{"key": "clearwater grouper", "at": [x - 1, y + 3, -126]}], z=-126)
    g = next(o for o in w.mobs.values() if o.kind == "monster")
    g.hits = g.hits_max = 10 ** 6
    g.cd["bubbles"] = 1e9                    # no volley of its own: the one bubble below
    loop, clock, store, patched = hurt_loop(w, 100)
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    for p in w.pets():
        p.hits = p.hits_max = 10 ** 6
    sp = next(s for s in g.spec["specials"] if s["kind"] == "bubbles")
    hurts = _log_hurts(w)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            here = tuple(w.player.pos)
            bubble = (here[0], here[1] + 1)
            reach = {(bubble[0] + dx, bubble[1] + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)}   # a pop's: 1 tile
            w._bubble_land(g.serial, (*bubble, here[2]), sp)
            clock.advance(0.1)
            st = loop.state()
            shown = [it for it in st["world"]["items"].values() if (it.get("x"), it.get("y")) == bubble
                     and it.get("hue") == 0x0B40]
            loot_at, why = loop.team.loot_tile(st, here[:2])   # a corpse on our tile, the grouper idle: only the bubble
            g.target, g.threat = gb.serial, {gb.serial: 1e9}
            w._warm(g, True)                                     # then the fight on the tank
            loop.team.vet_busy_until = tamer.time.monotonic() + 5.0    # mid-channel: out all the same
            walked = loop.team.stand_off(loop.state())
            pos = tuple(w.player.pos[:2])
            clock.advance(2.5)                   # the pop
    finally:
        store.close()
        simclock.uninstall(patched)
    popped = [n for _, v, k, n in hurts if k == "bubble" and v is w.player]
    pets = [p for p in w.pets() if p.alive]
    check("the bubble in our world model, its reach on us; a step out of it before the pop, no hit on us",
          shown and walked and pos not in reach and not popped and all(cheb_(w.player, p) <= 5 for p in pets),
          f"shown {len(shown)} walked {walked} at {pos} popped {popped}")
    check("the log: 'bubbles at (x, y): stepping to (x, y)'",
          f"bubbles at {bubble}: stepping to {pos}" in buf.getvalue(),
          [ln for ln in buf.getvalue().splitlines() if "bubble" in ln][:2])
    check("looting a corpse on our tile in the bubble's reach: from a tile out of it",
          why is None and loot_at is not None and loot_at[:2] not in reach, f"loot_at {loot_at} why {why}")


def test_tidal_backlash():
    print("== Tidal Tomb: a giant astral zoa's spell on its target and those within 3, each a Backlash stack; a caster pet "
          "under n stacks fizzles 1 - 0.7^n of its casts, none without ==")
    x, y = TIDAL_L3[0], TIDAL_L3[1] - 6
    w = tidal_world(TIDAL_L3, [{"key": "giant astral zoa", "at": [x, y, -50]}])
    z = next(o for o in w.mobs.values() if o.kind == "monster")
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    af = next(p for p in w.pets() if p.name == "AlaFlambe")
    w._place(gb, (x, y + 1, -50), d=0)
    w._place(af, (x + 6, y + 1, -50), d=0)
    for o in [z, w.player, *w.pets()]:
        o.hits = o.hits_max = 10 ** 6
    sp = next(s for s in z.spec["specials"] if s.get("id") == "astral")
    w._burst(z, sp, gb)
    stacks = {o.name: len(o.backlash) for o in w.team()}
    check("on the tank and whoever is within 3 of it, not the phoenix 6 off; a 0.3 stack each",
          stacks["GraveRobber"] == 1 and stacks["AlaFlambe"] == 0 and gb.backlash[0][1] == 0.3, str(stacks))
    rates = {}
    for n in (0, 1, 2):
        af.backlash = [[w.t + 30, 0.3]] * n
        rates[n] = sum(w._fizzles(af) for _ in range(4000)) / 4000
    check("fizzles: none without, ~0.30 under one stack, ~0.51 under two",
          rates[0] == 0 and abs(rates[1] - 0.30) < 0.03 and abs(rates[2] - 0.51) < 0.03, str(rates))


def test_tidal_specials5():
    print("== Tidal Tomb: a razorfin's spin stacks a bleed on each of us within 2 (side by side); Festering Wound cuts "
          "heals 5 % a stack, not the gravebug's devour; a swashbuckler ropes from 6 tiles, not from beside its target; "
          "a buccaneer bleeds the pets beside it with no number ==")
    x, y = TIDAL_L1
    # the spin's bleed: two spins on GraveRobber beside it, two stacks of 5 ticks of 9, 3 s apart, side by side
    w = tidal_world(TIDAL_L1, [{"key": "razorfin", "at": [x + 3, y, -16]}])
    fin = next(o for o in w.mobs.values() if o.kind == "monster")
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    gb.hits = gb.hits_max = 10 ** 6
    spin = next(s for s in fin.spec["specials"] if s.get("id") == "spin")
    w._place(gb, (x + 2, y, -16), d=0)
    ticks, orig = [], w.hurt

    def hurt(victim, amount, by, kind="melee", *a, **kw):
        if kind == "bleed" and victim is gb:
            ticks.append((round(w.t, 1), amount))
        orig(victim, amount, by, kind, *a, **kw)
    w.hurt = hurt
    t0 = w.t
    w._burst(fin, spin)
    w.t += 1.0
    w._burst(fin, spin)
    for _ in range(170):
        w._effects(gb)
        w.t += 0.1
    check("two spins, two stacks side by side: 10 ticks of 9, each stack's 3.0 s apart, done by 16 s",
          len(ticks) == 10 and {n for _, n in ticks} == {9} and len(gb.bleed_stacks) == 0
          and abs(ticks[0][0] - t0 - 3.0) < 0.15, str(ticks))
    # Festering Wound: 4 stacks cut a heal to 0.80 of itself, not the devour's
    w = tidal_world(TIDAL_L1)
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    gb.hits_max, gb.hits = 10 ** 6, 10
    gb.festering = [[w.t + 60, 0.05]] * 4
    h0 = gb.hits
    w.heal(gb, 100)
    cut = gb.hits - h0
    w.heal(gb, 100, "devour")
    whole = gb.hits - h0 - cut
    base = 100 * (1 + w.song("peace")) * pet_rules_heal_mult(gb)
    check("4 stacks: a heal at 0.80, the devour whole", abs(cut - round(base * 0.8)) <= 1 and abs(whole - round(base)) <= 1,
          f"cut {cut} whole {whole} base {base:.1f}")
    # the swashbuckler's rope: from 6 tiles it lands beside its target; from beside it, no rope
    out = {}
    for d in (6, 1):
        w = tidal_world(TIDAL_L1, [{"key": "drowned swashbuckler", "at": [x + d + 1, y, -16]}])
        sw = next(o for o in w.mobs.values() if o.kind == "monster")
        gb = next(p for p in w.pets() if p.name == "GraveRobber")
        w._place(gb, (x + 1, y, -16), d=0)
        for o in [sw, *w.pets(), w.player]:
            o.hits = o.hits_max = 10 ** 6
        sw.spec = {**sw.spec, "step_s": 99.0}                    # it doesn't walk in: only a rope closes the gap
        for p in w.pets():
            p.mode = "stay"
        sw.target, sw.threat = gb.serial, {gb.serial: 1e9}
        w._warm(sw, True)
        said = _log_overhead(w)
        w.run_until(w.t + 3.0)
        out[d] = (sum(1 for _, s, txt in said if s == sw.serial and txt == "*adventurers rope*"), sim_cheb(sw.pos, gb.pos))
    check("from 6 tiles: one rope, then beside its target; from beside it: none", out[6] == (1, 1) and out[1][0] == 0,
          str(out))
    # the buccaneer: the pets beside it bleed (18 a tick), no number with it; Logan 6 off doesn't
    w = tidal_world(TIDAL_L1, [{"key": "drowned buccaneer", "at": [x + 2, y, -16]}])
    bc = next(o for o in w.mobs.values() if o.kind == "monster")
    bleed = next(s for s in bc.spec["specials"] if s.get("id") == "bleed")
    pets = w.pets()
    for p, xy in zip(pets, ((x + 1, y), (x + 3, y))):
        w._place(p, (xy[0], xy[1], -16), d=0)
    w._place(w.player, (x + 8, y, -16), d=0)
    nums, orig = [], w.hurt

    def hurt2(victim, amount, by, kind="melee", *a, **kw):
        nums.append((victim.name, kind))
        orig(victim, amount, by, kind, *a, **kw)
    w.hurt = hurt2
    w._burst(bc, bleed)
    check("both pets a bleed stack of 18, Logan none, no number at the cast",
          all(len(p.bleed_stacks) == 1 and p.bleed_stacks[0][2] == 18 for p in pets) and not w.player.bleed_stacks
          and not nums, f"{[(p.name, p.bleed_stacks) for p in pets]} {nums}")
    from errand_bank import gating
    check("the runners' restriction guard: not the razorfin's 'Razor-sharp fins cause you to bleed!', still 'Razor'",
          not gating("Razor-sharp fins cause you to bleed!") and gating("The use of Razor is not allowed here."))


def pet_rules_heal_mult(p):
    from sim import pets as pet_rules
    return pet_rules.heal_mult(p.spec)


def test_tidal_pressure():
    print("== Pressure: each stack lands 30 s after the first, all at once, one number each ==")
    w = tidal_world(TIDAL_L1)
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    gb.hits = gb.hits_max = 10 ** 6
    got, orig = [], w.hurt

    def hurt(victim, amount, by, kind="melee", *a, **kw):
        if kind == "pressure":
            got.append((round(w.t, 1), victim.name, amount))
        orig(victim, amount, by, kind, *a, **kw)
    w.hurt = hurt
    sp = {"damage_pet": [35, 35], "damage_player": [10, 10], "seconds": 30.0}
    t0 = w.t
    w._pressure(gb, sp)
    w.run_until(t0 + 5.0)
    w._pressure(gb, sp)
    w.run_until(t0 + 29.8)
    before = list(got)
    w.run_until(t0 + 30.3)
    check("nothing before 30 s; then 2 x 35 at once", not before and [n for _, _, n in got] == [35, 35]
          and len({t for t, _, _ in got}) == 1, str(got))


def test_avoid_backlash():
    print("== with every attack pet a caster (the phoenix team) an idle giant astral zoa in pull range isn't pulled; "
          "with --no-avoid-backlash it is ==")
    x, y = TIDAL_L3[0], TIDAL_L3[1] - 6
    picked = {}
    for extra in ((), ("--no-avoid-backlash",)):
        w = tidal_world(TIDAL_L3, [{"key": "giant astral zoa", "at": [x, y, -50]}])
        z = next(o for o in w.mobs.values() if o.kind == "monster")
        z.calm_until = 1e9                               # stays idle
        loop, clock, store, patched = hurt_loop(w, 100, extra)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                loop.setup()
                picked[bool(extra)] = loop.pick_target(loop.state())
        finally:
            store.close()
            simclock.uninstall(patched)
    check("not picked by default; picked with --no-avoid-backlash", picked[False] is None and picked[True] is not None,
          str(picked))


def test_tidal_level3_bursts():
    print("== Tidal Tomb level 3: a sea serpent's area hit on us within 3 only; a giant astral zoa's two bursts both fire ==")
    import collections
    x, y = TIDAL_L3[0], TIDAL_L3[1] - 6

    def setup(key):
        w = tidal_world(TIDAL_L3, [{"key": key, "at": [x, y, -50]}])
        m = next(m for m in w.mobs.values() if m.kind == "monster")
        m.hits = m.hits_max = 10 ** 6
        m.next_step = 1e9                                    # it stands where placed
        for o in w.team():
            o.hits = o.hits_max = 10 ** 6
        for p in w.pets():
            p.mode = "stay"
        gb = next(p for p in w.pets() if p.name == "GraveRobber")
        gb.x, gb.y = x + 1, y                                # the tank beside it; Logan and the phoenix 6 off
        m.target, m.threat = gb.serial, {gb.serial: 1e6}
        return w, m

    w, m = setup("tidal sea serpent")
    m.spec = {**m.spec, "melee": None}
    hits, orig_hurt = [], w.hurt

    def hurt(victim, amount, by, kind="melee", song_pets=True, **kw):
        if kind == "burst":
            hits.append((round(w.t, 2), victim.name, sim_cheb(victim.pos, by.pos)))
        orig_hurt(victim, amount, by, kind, song_pets, **kw)
    w.hurt = hurt
    w.run_until(40.0)
    times = sorted({t for t, _, _ in hits})
    check("the serpent's area hit lands on the tank beside it every ~7.7 s, on none of us 6 off",
          len(times) >= 3 and all(6.8 <= b - a <= 8.6 for a, b in zip(times, times[1:]))
          and {n for _, n, _ in hits} == {"GraveRobber"} and all(d <= 3 for _, _, d in hits), f"{times} {hits[:4]}")
    w, m = setup("giant astral zoa")
    fired, struck, cur, orig_burst = collections.Counter(), collections.defaultdict(set), [None], w._burst

    def burst(mob, sp, tgt=None):
        fired.update([sp.get("id")])
        cur[0] = sp.get("id")
        orig_burst(mob, sp, tgt)
        cur[0] = None
    w._burst, orig_hurt = burst, w.hurt

    def hurt_burst(victim, amount, by, kind="melee", song_pets=True, **kw):
        if kind == "burst" and cur[0]:
            struck[cur[0]].add(victim.name)
        orig_hurt(victim, amount, by, kind, song_pets, **kw)
    w.hurt = hurt_burst
    w.run_until(70.0)
    check("...its 'astral' (every ~5.6 s) and 'lightning' (~29 s) bursts each on their own cooldown, the lightning on "
          "its target and everyone within 4 of it (Logan and the pets 6 off spared)",
          fired["astral"] >= 10 and fired["lightning"] >= 2 and struck["lightning"] == {"GraveRobber"},
          f"{dict(fired)} {dict(struck)}")


def test_tidal_recall_rule():
    print("== the recall rule knows both dungeons: Tidal Tomb's gold gates, the Mausoleum's still ==")
    import escape
    check("a recall at the Tidal Tomb arrival, 2 tiles from the golden moongate, is allowed",
          escape.recall_forbidden((*TIDAL_L1, -16), 0) is None)
    why = escape.recall_forbidden((6650, 1990, 39), 0)
    check("...deep in level 1 it is refused", why is not None and "Tidal Tomb" in why, str(why))
    check("...on level 2 too", escape.recall_forbidden((*TIDAL_L2, -38), 0) is not None)
    check("...and on level 3 (the dungeon's box takes it in)", escape.recall_forbidden((*TIDAL_L3, -50), 0) is not None)
    check("...and on the Mausoleum's fight level as before", escape.recall_forbidden((5261, 652, 0), 0) is not None)


def test_area_hitters():
    print("== every area hitter alike: a self-centred area hit of radius r up to 5 keeps us r + 1 off; one centred on its "
          "target or reaching 12 isn't a stand-off from it; one centred on its target with a radius keeps us radius + 1 "
          "off each pet in its reach (TARGET_AREA) ==")
    got = {k: tamer.AREA_R.get(k) for k in ("razorfin", "voidfin", "greater razorfin", "tidal sea serpent",
                                             "drowned buccaneer", "corpsebride", "oozing noughtilus", "reanimated daemon",
                                             "giant astral zoa", "giant combustive zoa", "combustive zoa",
                                             "biolumen shellpod", "ossuarian deathbringer", "phantasm", "hierarch")}
    want = {"razorfin": 3, "voidfin": 3, "greater razorfin": 3, "tidal sea serpent": 4, "drowned buccaneer": 2,
            "corpsebride": 3, "oozing noughtilus": 3, "reanimated daemon": 6, "giant astral zoa": None,
            "giant combustive zoa": None, "combustive zoa": None, "biolumen shellpod": None,
            "ossuarian deathbringer": 5, "phantasm": None, "hierarch": None}
    check("as the rule says", got == want, str({k: v for k, v in got.items() if v != want[k]}))
    want = {"astral zoa": (11, 4), "giant astral zoa": (11, 4), "combustive zoa": (12, 4), "giant combustive zoa": (12, 4),
            "drowned mariner": (11, 2), "soulcatcher": (11, 2)}
    check("...the zoas' spell and scorch round the pet they target, the shooters' hits on everyone within 1 of theirs; "
          "nothing else (not a shellpod's barrage, radius 0; not the zoas' lightning, one every 22-87 s)",
          tamer.TARGET_AREA == want, str(tamer.TARGET_AREA))


def test_shy_by_ai():
    print("== shy (keep out of its first sight, the pets into it): a mage or ranged AI from difficulty 60, the shadowguard; "
          "not a weak caster, not a melee one ==")
    import aggro
    got = {k: aggro.shy(k) for k in ("corpse purger", "blightmare", "shadowguard", "calamity", "drowned mariner",
                                     "soulcatcher", "astral zoa", "familiar", "reanimator zealot", "ocean bedrock",
                                     "vampire countess")}
    want = {"corpse purger": True, "blightmare": True, "shadowguard": True, "calamity": True, "drowned mariner": True,
            "soulcatcher": True, "astral zoa": True, "familiar": False, "reanimator zealot": False,
            "ocean bedrock": False, "vampire countess": False}
    check("as the rule says for both dungeons' types", got == want, str({k: v for k, v in got.items() if v != want[k]}))


def test_walk_in_fought():
    print("== --fight-via: the level 2 leg to level 3 fought through; a drowned mariner by the way woken on the pets, "
          "killed, then on through the teleporter ==")
    import tempfile
    import loop_hunt
    import memory
    from sim import run as simrun
    from sim import world as simworld
    # user 2026-10-26: "in TT we'll always have to fight in 2 in order to get to 3"; simulator the same day: 15 of 16
    # level 3 runs died, most on the walk across level 2 among its drowned mariners and soulcatchers
    # On the shy monsters' measured wake (2026-10-29) it failed: 3 back-offs, then on past the mariner with the pets 2
    # tiles behind, and it woke with Logan 1.0 off it, the pets 1.4 (a near tie, 2:1 Logan). Since then the pass
    # (tamer.Team.pass_by): the walk planned so it wakes on a pet; seeds 1-12 of this layout 12 of 12, before 5 of 12
    w = tidal_world((6834, 2079), [{"key": "drowned mariner", "at": [6852, 2072, -86]}], z=-10)
    m = next(o for o in w.mobs.values() if o.kind == "monster")
    link, clock = link_for(w)
    db = os.path.join(tempfile.mkdtemp(prefix="sim_test_"), "harness.db")
    tele = ["6884", "1929"]
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, ["--no-songs", "--enter-via", *tele,
                                                                       "--fight-via", *tele], 10, at_hunt=False)
                                             + ["--memory", db, "--seed", "1", "--human", "off"])
    pets = {p.serial for p in w.pets()}
    targets = set()
    stray, simworld.SHOT_AT_PLAYER_P = simworld.SHOT_AT_PLAYER_P, 0.0
    import tamer
    passing, tamer.PASS = tamer.PASS, True    # off by default since 2026-10-30; this test is the pass's
    run_until = clock.on_advance

    def watch(now):
        run_until(now)
        if m.alive and m.target is not None:
            targets.add(m.target)
    clock.on_advance = watch
    patched = simclock.install(clock)
    store = memory.Memory(db)
    err = None
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            loop = loop_hunt.HuntLoop(link, store, args)
            loop.team.setup(loop.state())
            try:
                loop.walk_in([(6884, 1929)])
            except loop_hunt.Abort as e:
                err = str(e)
    finally:
        clock.on_advance = run_until
        simworld.SHOT_AT_PLAYER_P = stray
        tamer.PASS = passing
        store.close()
        simclock.uninstall(patched)
    check("through to level 3 alive, the mariner dead, its aggro only ever on the pets",
          err is None and w.player.alive and not w.player.ghost and sim_cheb(w.player.pos, TIDAL_L3) <= 3
          and not m.alive and targets and targets <= pets,
          f"err {err!r} at {w.player.pos} mariner alive {m.alive} targets {targets} pets {pets} | "
          + " | ".join(buf.getvalue().splitlines()[-6:])[-600:])


def _oxygen_loop(w, extra=()):
    """A HuntLoop on world `w` (Tidal Tomb) with its own store, for the oxygen tests: (loop, link, clock, store, patched)."""
    import tempfile
    import loop_hunt
    import memory
    from sim import run as simrun
    link, clock = link_for(w)
    db = os.path.join(tempfile.mkdtemp(prefix="sim_test_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, ["--no-songs", *extra], 10, at_hunt=False)
                                             + ["--memory", db, "--seed", "1", "--human", "off"])
    patched = simclock.install(clock)
    store = memory.Memory(db)
    loop = loop_hunt.HuntLoop(link, store, args)
    loop.team.setup(loop.state())
    return loop, link, clock, store, patched


def test_oxygen_hop():
    print("== oxygen on level 2: the clock read off the 'Oxygen' gump; the west strip's hop skipped as the scenario says; "
          "with its skip gone, past the deprivation less the leg, the fought leg there, up to level 1 with the pets and "
          "back down, no deprivation; not before ==")
    import json
    w = tidal_world((6850, 2071), z=-10)    # 38 route steps from the west strip's teleporter up
    w.under_s = 400.0                       # 400 s under: the gump says so; no leg yet (it goes from 732 - 120 - leg)
    hurts = _log_hurts(w)
    dry = []
    try:
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            loop, link, clock, store, patched = _oxygen_loop(w)
            run_until = clock.on_advance

            def watch(now):
                run_until(now)
                if w.player.x < 6800:
                    dry.append(w.t)
            clock.on_advance = watch
            clock.sleep(7.0)                # one gump at least
            st = loop.state()
            early_el, early_due = loop.oxy.elapsed(clock.monotonic()), loop.oxygen_due(st)
            w.under_s = 640.0
            clock.sleep(7.0)
            st = loop.state()
            el = loop.oxy.elapsed(clock.monotonic())
            skipped = [s["tile"] for s in oxygen.sites(loop.oxy.ox)]   # the arrival stairs are out of reach from here
            for h in loop.oxy.ox["hops"]:
                h.pop("skip", None)
            loop.oxy_plan_t = -1e9
            site = loop.oxygen_due(st)
            if site is not None:
                loop.oxygen_leg(st, site)
            ev = store.con.execute("SELECT data FROM job_events WHERE kind='oxygen'").fetchall()
            store.close()
    finally:
        simclock.uninstall(patched)
    check("the clock follows the gump to within a bar's step (30.1 s) of the world's time under; no leg at 400 s",
          early_el is not None and abs(early_el - 407) <= 33 and early_due is None and el is not None
          and abs(el - 647) <= 33, f"clock {early_el} at 407, {el} at 647; due early {early_due}")
    check("the west strip's hop, marked `skip` in the scenario, is no refill", (6834, 2079) not in skipped, str(skipped))
    outcome = json.loads(ev[-1][0])["outcome"] if ev else None
    pets_near = all(sim_cheb(p.pos, w.player.pos) <= 3 for p in w.pets())
    check("at 640 s: the hop's leg, up on level 1 and back down on level 2 with both pets, refilled, no deprivation",
          site is not None and site["kind"] == "hop" and outcome == "refilled" and dry and w.under_s < 30
          and w._underwater(w.player) and pets_near and not any(k == "oxygen" for _, _, k, _ in hurts),
          f"site {site and site['kind']} outcome {outcome} dry {len(dry)} under {w.under_s:.0f} at {w.player.pos} "
          f"pets {[p.pos for p in w.pets()]} | " + " | ".join(buf.getvalue().splitlines()[-6:])[-600:])


def test_oxygen_stairs():
    print("== oxygen on level 2 as the scenario has it: the fought leg to the arrival stairs (the west strip skipped), up "
          "to level 1 with the pets and back down, refilled, no deprivation ==")
    import json
    w = tidal_world((6942, 2026), z=-117)   # 21 route steps from the stairs up (6943, 2004): N from (x, 2005)
    w.under_s = 640.0
    hurts = _log_hurts(w)
    dry = []
    try:
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            loop, link, clock, store, patched = _oxygen_loop(w)
            run_until = clock.on_advance

            def watch(now):
                run_until(now)
                if w.player.x < 6800:
                    dry.append(w.player.pos)
            clock.on_advance = watch
            clock.sleep(7.0)
            st = loop.state()
            site = loop.oxygen_due(st)
            if site is not None:
                loop.oxygen_leg(st, site)
            ev = store.con.execute("SELECT data FROM job_events WHERE kind='oxygen'").fetchall()
            store.close()
    finally:
        simclock.uninstall(patched)
    outcome = json.loads(ev[-1][0])["outcome"] if ev else None
    pets_near = all(sim_cheb(p.pos, w.player.pos) <= 3 for p in w.pets())
    check("the stairs' leg: up to level 1's row y 1984 and back down to level 2, both pets along, refilled, no deprivation",
          site is not None and site["tile"] == (6943, 2004) and outcome == "refilled" and dry
          and all(p[1] == 1984 or p[1] == 1983 for p in dry) and w.under_s < 30 and w._underwater(w.player)
          and pets_near and not any(k == "oxygen" for _, _, k, _ in hurts),
          f"site {site and site['tile']} outcome {outcome} dry {sorted(set(dry))[:4]} under {w.under_s:.0f} at "
          f"{w.player.pos} pets {[p.pos for p in w.pets()]} | " + " | ".join(buf.getvalue().splitlines()[-6:])[-600:])


SOUL_L2 = (6942, 2023, -110)  # live 2026-10-29 18:13:23 (hunt-20261029-180419-d505): the idle soulcatcher 0x000B2A2F...
BACKED_L2 = (6951, 2037)      # ...and the tile Logan backed off to from its sight 17 of 18 times, the pets beside him


def test_oxygen_leg_past_shooter():
    print("== oxygen on level 2, the live 18:13 layout: the stairs' leg from where Logan backed off, an idle soulcatcher "
          "on its route, 'All Patrol' at a floor over the land refused: three back-offs, then on past it with the pets "
          "on guard, up the stairs well before 732 s ==")
    # live 2026-10-29 18:13:00-18:14:28: "oxygen: to the teleporter up at 6943,2004: 470 s under water", "fought through",
    # then "holding here: a soulcatcher would see us" / "... would see us before the pets at 11 tiles ...; back to (6951,
    # 2037) out of its sight" / the route again, 18 times in 63 s; "'All Patrol' to (6942, 2026): cannot be seen" twice
    # (graphic 0: the land 0x244 at -128 under the stone stairs, out of our eye's sight; the stairs' top in it)
    import json
    import agent_link
    import loop_hunt
    w = tidal_world(BACKED_L2, [{"key": "soulcatcher", "at": list(SOUL_L2)}], z=-126, team="phoenix_incubus")
    pets = w.pets()
    pl = w.player
    answers = {}
    for spot, origin in (((6942, 2026, -117), BACKED_L2), ((6947, 2019, -126), (6956, 2030))):   # live: refused, taken
        w.player.x, w.player.y = origin
        w._cursor({"kind": "patrol"}, cursor_type=0, target_type=1)
        w._target(actions.target_xyz(w.cid, *spot, 0, 0))
        answers[spot[:2]] = pets[0].mode == "patrol"
        for p in pets:
            p.mode, p.patrol = "follow", None
    w.player.x, w.player.y = BACKED_L2
    check("the simulator's ground answer as the server's: 'All Patrol' at graphic 0 on (6942, 2026) from (6951, 2037) "
          "refused (live 18:13:25), at (6947, 2019) from (6956, 2030) taken (live 2026-10-28 22:16:52)",
          answers == {(6942, 2026): False, (6947, 2019): True}, str(answers))
    for p in pets:
        w._place(p, (BACKED_L2[0] - 1, BACKED_L2[1], -126), d=0)
    w.under_s = 470.0
    hurts = _log_hurts(w)
    seen = {"under": 0.0, "up": None}
    walk_to = agent_link.Mover.walk_to

    def planned(self, *a, **k):
        # the live walk's start: 0.35-0.74 s from the intent to its first step (capture 20261029_100942 18:13:26-34,
        # the planning on the real clock); without it the simulator's rounds come under BACK_OFF_S and creep through
        loop_hunt.time.sleep(0.4)
        return walk_to(self, *a, **k)
    try:
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            loop, link, clock, store, patched = _oxygen_loop(w, ("--vet-range", "5", "--crawl-learn-s", "180"))
            agent_link.Mover.walk_to = planned
            run_until = clock.on_advance

            def watch(now):
                run_until(now)
                seen["under"] = max(seen["under"], w.under_s)
                if w.player.x < 6800 and seen["up"] is None:
                    seen["up"] = seen["under"]
                if w.under_s > loop.oxy.limit_s + 30:
                    raise loop_hunt.Abort("test: deprived")
            clock.on_advance = watch
            clock.sleep(7.0)
            st = loop.state()
            site = loop.oxygen_site(st)     # live the leg began at 470 s, 96 route steps off; from here 33: not due yet
            err = None
            try:
                if site is not None:
                    loop.oxygen_leg(st, site)
            except loop_hunt.Abort as e:
                err = str(e)
            ev = store.con.execute("SELECT data FROM job_events WHERE kind='oxygen'").fetchall()
            store.close()
    finally:
        agent_link.Mover.walk_to = walk_to
        simclock.uninstall(patched)
    outcome = json.loads(ev[-1][0])["outcome"] if ev else None
    counts = loop.team.counts
    check("the leg: its patrol refused, then (since 2026-10-29) a step-in that puts the soulcatcher on a pet, or "
          "BACK_OFF_REPEAT back-offs and on past it (tamer.STEP_IN False); up the stairs before the deprivation, "
          "refilled, no oxygen damage, Logan alive",
          err is None and site is not None and site["tile"] == (6943, 2004) and counts.get("patrol_refused", 0) >= 1
          and counts.get("back_offs", 0) <= tamer.BACK_OFF_REPEAT
          and (counts.get("step_in_pet", 0) == 1 if tamer.STEP_IN else counts.get("faced", 0) == 1)
          and outcome == "refilled" and seen["up"] is not None and seen["up"] < loop.oxy.limit_s
          and not any(k == "oxygen" for _, _, k, _ in hurts) and pl.alive and not pl.ghost,
          f"err {err!r} site {site and site['tile']} outcome {outcome} back-offs {counts.get('back_offs')} faced "
          f"{counts.get('faced')} step-ins {counts.get('step_ins')} on a pet {counts.get('step_in_pet')} refused "
          f"{counts.get('patrol_refused')} up at {seen['up']} max under {seen['under']:.0f} "
          f"| " + " | ".join(buf.getvalue().splitlines()[-6:])[-600:])


def _step_in_layout(start, reactive: bool):
    """Level 2's idle soulcatcher at SOUL_L2, Logan at `start` with the pets beside him on guard: the step-in at it
    (Team.step_in, fight's walk up; `reactive`: Logan already in its first sight, the tick's back_off), then 30 s of
    the tick's fighting (combat_step). -> (world, its first target, the team's counts, the log)."""
    w = tidal_world(start, [{"key": "soulcatcher", "at": list(SOUL_L2)}], z=-126, team="phoenix_incubus")
    m = next(o for o in w.mobs.values() if o.kind == "monster")
    loop, clock, store, patched = hurt_loop(w, 100)
    loop.team.guard_needed = False                      # hurt_loop said 'All Guard Me'
    first = {}
    run_until = clock.on_advance

    def watch(now):
        run_until(now)
        if m.target is not None and "who" not in first:
            first["who"] = m.target
    clock.on_advance = watch
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            st = loop.state()
            if reactive or not loop.team.step_in(st, m.serial):
                loop.team.back_off(st)
            for _ in range(150):
                st = loop.state()
                if not loop.combat_step(st, loop.attackers(st), False):
                    clock.advance(0.2)
    finally:
        clock.on_advance = run_until
        store.close()
        simclock.uninstall(patched)
    return w, first.get("who"), loop.team.counts, buf.getvalue()


def test_step_in():
    print("== the step-in (user 2026-10-29: 'All Guard', walk up, then walk away slightly so the pets end up between): "
          "Logan steps into an idle soulcatcher's sight and back, it takes a pet ==")
    w, who, counts, out = _step_in_layout((6945, 2035), reactive=False)
    pets = {p.serial: p.name for p in w.pets()}
    check("12 tiles off, out of reach of 'All Patrol' (refused, live 18:13:25): up into its sight and back behind the "
          "pets; its first target a pet, Logan untouched (before, tamer.STEP_IN False: it stays idle)",
          who in pets and counts.get("step_ins") == 1 and counts.get("step_in_pet") == 1 and w.player.hits == 100
          and "step-in on the soulcatcher" in out,
          f"first target {pets.get(who, who)} counts {dict((k, v) for k, v in counts.items() if 'step' in k or 'back' in k)}"
          f" Logan {w.player.hits} at {w.player.pos} | "
          + " | ".join(x for x in out.splitlines() if "step-in" in x)[-400:])


def test_step_in_fallback():
    print("== the step-in's fallback: in an idle soulcatcher's first sight at its range's edge, the pets a tile behind "
          "out of its range, no step-back tile: the back-off as before ==")
    w, who, counts, out = _step_in_layout((6944, 2033), reactive=True)
    check("no step-back tile ('no step-in ... no step-back tile'), then back behind the pets out of its sight; no "
          "step-in, Logan untouched", "no step-in at the soulcatcher" in out and "no step-back tile" in out
          and counts.get("back_offs", 0) >= 1 and not counts.get("step_ins") and w.player.hits == 100
          and who != w.player.serial,
          f"counts {dict((k, v) for k, v in counts.items() if 'step' in k or 'back' in k)} Logan {w.player.hits} | "
          + " | ".join(x for x in out.splitlines() if "step" in x or "back" in x)[-400:])


def test_oxygen_dolphin():
    print("== oxygen on level 3: the fought leg to within reach of the Recovery Dolphin, its refill, no deprivation ==")
    import json
    w = tidal_world((7173, 2175), z=-50)    # 26 route steps from within 9 of the arrival's dolphin (7164, 2210)
    w.under_s = 650.0
    hurts = _log_hurts(w)
    try:
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            loop, link, clock, store, patched = _oxygen_loop(w)
            clock.sleep(7.0)
            st = loop.state()
            site = loop.oxygen_due(st)
            if site is not None:
                loop.oxygen_leg(st, site)
            ev = store.con.execute("SELECT data FROM job_events WHERE kind='oxygen'").fetchall()
            store.close()
    finally:
        simclock.uninstall(patched)
    outcome = json.loads(ev[-1][0])["outcome"] if ev else None
    check("the dolphin's leg, its refill line, no deprivation",
          site is not None and site["kind"] == "dolphin" and outcome == "refilled"
          and w.metrics.get("dolphin_refills", 0) >= 1 and not any(k == "oxygen" for _, _, k, _ in hurts),
          f"site {site and (site['kind'], site['tile'])} outcome {outcome} refills {w.metrics.get('dolphin_refills')} "
          f"at {w.player.pos} | " + " | ".join(buf.getvalue().splitlines()[-6:])[-600:])


def test_tidal_hunt():
    print("== a Tidal Tomb hunt from home: recall in, kills on level 1, out by the golden moongate to Totem, home ==")
    from sim import run as simrun
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        r = simrun.run_once(3, [], scenario="tidal_tomb", minutes=5)
    check("done, kills, the gold banked at home", r["outcome"] == "done" and r["kills"] > 0 and r["gold_banked"] > 0
          and r["hunt_s"] > 200, f"{r['outcome']} {r['error']} kills {r['kills']} banked {r['gold_banked']} hunt {r['hunt_s']}")


OSS_L3 = (5622, 1085)         # the Ossuary: an open room on level 3 (the hunting level)


def ossuary_world(start, spawns=(), seed=1, z=0):
    sc = load("ossuary")
    sc["spawns"] = [{**s, "respawn_s": [9999, 9999]} for s in spawns]
    sc["defaults"]["spawn_spread"] = sc["defaults"]["wander_per_s"] = 0
    sc["player"]["start"] = [start[0], start[1], z]
    return World(sc, seed=seed)


def _hold_on(w, m, victim):
    """`m` fights `victim` for good: unkillable, no melee or spells of its own, so its specials are all it does."""
    m.hits = m.hits_max = 100000
    m.spec = {**m.spec, "melee": None, "ranged": None}
    victim.hits = victim.hits_max = 100000
    m.target, m.threat = victim.serial, {victim.serial: 1e6}


def test_ossuary_recall_rule():
    print("== the recall rule over a dungeon of two boxes: the Ossuary's levels 1-3 and its level 4 far off, its golden "
          "gates; outside, the tome's 'Ossuary' row ==")
    import escape
    check("a recall at the tome's 'Ossuary' landing outside is allowed", escape.recall_forbidden((1213, 2816, 31), 0) is None)
    check("...beside level 1's golden gates too", escape.recall_forbidden((5926, 1324, 0), 0) is None
          and escape.recall_forbidden((5871, 1311, 5), 0) is None)
    why = escape.recall_forbidden((*OSS_L3, 0), 0)
    check("...on level 3 it is refused", why is not None and "Ossuary" in why, str(why))
    check("...and on level 4 (the second box)", escape.recall_forbidden((4900, 2000, 0), 0) is not None)
    check("...but not between the boxes (Darkmire's 5216, 1953)", escape.recall_forbidden((5216, 1953, 0), 0) is None)


def test_ossuary_delayed_bursts():
    print("== the Ossuary's emote-then-hit specials: a phantasm's lament on each of us within 12 tiles 1.5 s after "
          "'*laments*', none beyond; a hierarch's channelled fire, 6 ticks 0.5 s apart on its target from 1.0 s ==")
    x, y = OSS_L3
    # Logan and the phoenix (following him) 14 tiles east of the phantasm, GraveRobber on it by "All Kill"
    w = ossuary_world((x + 17, y), [{"key": "phantasm", "at": [x + 3, y, 0]}])
    hurts, said = _log_hurts(w), _log_overhead(w)
    m = next(m for m in w.mobs.values() if m.kind == "monster")
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    _hold_on(w, m, gb)
    gb.mode, gb.kill = "kill", m.serial
    m.spec = {**m.spec, "specials": [{**m.spec["specials"][0], "chance": 1.0}], "step_s": 1e9}   # it stays put
    ph = next(p for p in w.pets() if p.name == "AlaFlambe")
    w.run_until(40.0)
    cues = [t for t, s, text in said if s == m.serial and text == "*laments*"]
    hits = [(t, v) for t, v, k, _ in hurts if k == "burst"]
    check("2+ laments ~15 s apart, each followed 1.5 s later by a hit on GraveRobber beside it, none on Logan and the "
          "phoenix 13+ off, none sooner", len(cues) >= 2 and sim_cheb(w.player.pos, m.pos) > 12
          and sim_cheb(ph.pos, m.pos) > 12 and all({v.serial for t, v in hits if abs(t - c - 1.5) < 0.06} == {gb.serial}
                                                   for c in cues)
          and all(any(abs(t - c - 1.5) < 0.06 for c in cues) for t, _ in hits),
          f"cues {cues} hits {[(t, v.name) for t, v in hits]} us {sim_cheb(w.player.pos, m.pos)} "
          f"phoenix {sim_cheb(ph.pos, m.pos)}")
    w = ossuary_world(OSS_L3, [{"key": "hierarch", "at": [x + 3, y, 0]}])
    hurts, said = _log_hurts(w), _log_overhead(w)
    m = next(m for m in w.mobs.values() if m.kind == "monster")
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    _hold_on(w, m, gb)
    w.run_until(12.0)
    cue = next((t for t, s, text in said if s == m.serial and text == "*channels ancient fire*"), None)
    ticks = [t for t, v, k, _ in hurts if k == "burst" and cue is not None and cue < t <= cue + 4.0]
    check("its channel: 6 hits on GraveRobber alone, 1.0 s after the emote and 0.5 s apart",
          cue is not None and len(ticks) == 6 and all(v is gb for t, v, k, _ in hurts if k == "burst")
          and all(abs(t - (cue + 1.0 + 0.5 * i)) < 0.06 for i, t in enumerate(ticks)), f"cue {cue} ticks {ticks}")


def test_ossuary_death_brood():
    print("== an apophite spirit's death: 5 spirit asps beside it on its target, gone 5-10 s later; one kill, no asp's ==")
    x, y = OSS_L3
    w = ossuary_world(OSS_L3, [{"key": "apophite spirit", "at": [x + 3, y, 0]}])
    m = next(m for m in w.mobs.values() if m.kind == "monster")
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    gb.hits = gb.hits_max = 100000
    m.target, m.threat = gb.serial, {gb.serial: 1e6}
    w.run_until(1.0)
    kills = w.metrics["kills"]
    w.hurt(m, m.hits + 1, gb)
    asps = [o for o in w.mobs.values() if o.alive and o.key == "spirit asp"]
    check("5 spirit asps within a tile of its corpse, on GraveRobber",
          len(asps) == 5 and all(sim_cheb(o.pos, m.pos) <= 1 and o.target == gb.serial for o in asps),
          f"{[(o.pos, o.target) for o in asps]}")
    w.run_until(w.t + 4.9)
    early = sum(1 for o in asps if o.serial in w.mobs)
    w.run_until(w.t + 5.3)
    check("...all still up at 4.9 s, all gone by 10.2 s, the spirit the only kill",
          early == 5 and not any(o.serial in w.mobs for o in asps) and w.metrics["kills"] == kills + 1,
          f"up at 4.9 s {early}, kills {w.metrics['kills'] - kills}")


def test_pets_pass_through():
    print("== our pets walk through each other and us, as live: the phoenix down the Ossuary's one-wide ramp past the "
          "gravebug behind Logan ==")
    w = ossuary_world((5889, 1327), z=-13)
    gb = next(p for p in w.pets() if p.name == "GraveRobber")
    ph = next(p for p in w.pets() if p.name == "AlaFlambe")
    w._teleport(gb, (5889, 1326, -10))
    w._teleport(ph, (5890, 1318, 0))
    for p in (gb, ph):
        p.mode = "follow"
    w.run_until(8.0)
    check("the phoenix within 2 of Logan in 8 s (before 2026-10-28 it waited at the top, 9 off)",
          sim_cheb(ph.pos, w.player.pos) <= 2, f"phoenix {ph.pos} gravebug {gb.pos} us {w.player.pos}")


def test_ossuary_hunt():
    print("== an Ossuary hunt from home: recall outside, in by the entrance and levels 1-2, kills on level 3, out by its "
          "crimson moongate to Totem, home ==")
    from sim import run as simrun
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        r = simrun.run_once(3, [], scenario="ossuary", minutes=10)
    check("done, kills on level 3, the gold banked at home", r["outcome"] == "done" and r["kills"] > 0
          and r["gold_banked"] > 0 and r["hunt_s"] > 120,
          f"{r['outcome']} {r['error']} kills {r['kills']} banked {r['gold_banked']} hunt {r['hunt_s']}")


def main():
    if pathfind.Walkers().get(0) is None:
        print("SKIP: no map files (install dir)")
        return 0
    for t in (test_clock, test_geometry, test_shove, test_aggro, test_aggro_rules, test_shy_wake, test_bard_and_vet, test_specials,
              test_hunt,
              test_standoff, test_fire, test_pet_res, test_retreat_sight, test_spacing, test_no_spacing_after_fight,
              test_vet_tile_off_tank, test_calm_heal,
              test_stairs, test_boss_room_door, test_backspawn,
              test_area_first, test_leave_called_off, test_leave_from_far, test_gate_dash, test_gate_after_vet,
              test_bard_sight, test_bard_peek, test_bard_peek_corner, test_bard_provoke, test_rope_sight, test_red_flight,
              test_red_out_of_view, test_red_walled,
              test_rope_cancel_attacked, test_rope_combat_cooldown, test_rope_ranged, test_red_caster_sight,
              test_red_hamstring, test_red_breaks_recall, test_red_follows_teleporter, test_gold_banked,
              test_ledge_attacker,
              test_caster_at_12, test_blightmare_reach, test_cover, test_to_pet_not_into_shot,
              test_to_pet_not_into_caster_reach, test_blightmare_first_sight,
              test_blightmare_awake_patrol, test_seek_by_kill_value, test_guard_after_dropped_kill,
              test_purger_back_off, test_purger_no_ping_pong, test_caster_room_pets_first, test_patrol_pull, test_patrol_after_back_off,
              test_patrol_after_pass_over, test_vet_channel_not_ours,
              test_restart_inside, test_walk_in_through_neighbour,
              test_instrument, test_recall_charges,
              test_standoff_sight, test_standoff_ties, test_pet_death_readout,
              test_idle_pet, test_pet_shot, test_countess_held_pet, test_countess_reguard, test_loot_lift_gap,
              test_loot_off_area_hitter, test_on_another_player, test_ally_left_alone, test_kill_unseen_once,
              test_theirs_passed_over,
              test_theirs_after_kill_sent,
              test_patrol_off_passed_over_area_hitter, test_walk_past_purger_on_another, test_pets_stuck_off_fight,
              test_others_corpse_not_looted,
              test_area_peace, test_audit_mechanics, test_blink_stepoff,
              test_heal_in_place,
              test_songs, test_songs_tidal_floor, test_cure_potion,
              test_pull_budget, test_hold_first_sight, test_unseen_target_passed_over, test_ledge_target_not_picked,
              test_never_aggro_walk_out, test_crawl_seeks_in_view, test_rope,
              test_rest_hurt_pet, test_walk_in, test_walk_in_fetch_pet, test_wait_pets_attacked, test_tele_dash,
              test_walk_in_red_first_level,
              test_walk_in_red_second_level, test_escape_after_walk_in_error, test_go_home, test_go_home_tidal_level3,
              test_no_escape_on_death_or_stop, test_soul_jar, test_soul_jar_guards, test_soul_jar_defer,
              test_transfixed_walk, test_walk_failed_carries_on, test_career_junctures, test_armor,
              test_tamer_scan_rows, test_pet_aggro_setting, test_party_validation, test_pet_builds, test_anchors_pinned,
              test_pet_mitigation,
              test_kdmg_barding, test_parity_team, test_perk_conditions, test_dreamlull, test_opportunity_punishment,
              test_vet_codex,
              test_team_run,
              test_cast_then_flight, test_cast_lost_out_of_range, test_cast_not_interrupted, test_trace_roundtrip,
              test_trace_no_side_effects, test_scenario_inherit, test_chain_links, test_tidal_oxygen, test_tidal_dolphin,
              test_tidal_ooze_and_brood, test_tidal_dig, test_tidal_leave_coming, test_tidal_pinch,
              test_tidal_leave_crab, test_tidal_large_swing,
              test_tidal_scorch, test_scorch_stand_off, test_potion_after_vet, test_patrol_refused_hold,
              test_zoa_lightning, test_scorch_susceptible, test_shooter_hits, test_shooter_stand_off, test_loose_on_us,
              test_tidal_bedrock_spray, test_spray_stand_off,
              test_spray_windup_fewest_steps, test_spray_loot_wait, test_spray_hold_awake,
              test_tidal_bubbles, test_tidal_marinerbane, test_bubbles_step_off, test_tidal_level3_bursts,
              test_tidal_recall_rule, test_tidal_backlash, test_tidal_pressure, test_tidal_specials5, test_avoid_backlash,
              test_area_hitters, test_shy_by_ai,
              test_walk_in_fought, test_oxygen_hop, test_oxygen_stairs, test_oxygen_leg_past_shooter, test_step_in,
              test_step_in_fallback, test_oxygen_dolphin,
              test_tidal_hunt,
              test_ossuary_recall_rule,
              test_ossuary_delayed_bursts,
              test_ossuary_death_brood, test_pets_pass_through, test_ossuary_hunt):
        t()
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILED: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

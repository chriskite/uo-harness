"""Behaviour of the mining pieces without a server: the runner's dig classifier (loop_mining.MineLoop.outcome)
on the live capture's messages (20261019_200350, docs/NOTES.md "Live hand-mining capture"), the spot planner
(mining_opt.plan: eligibility, the pick, the prior) and the store's mining tables (memory.py v7).

Run: python harness/test_mining.py
"""
import json
import os
import random
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mining_opt  # noqa: E402
from loop_mining import MineLoop  # noqa: E402
from memory import Memory  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FAILURES = []
NOW = 2_000_000_000.0


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}{'' if cond else ' ' + str(detail)}")
    if not cond:
        FAILURES.append(name)


def speech(text):
    return {"ev": "speech_heard", "text": text}


def cl(n):
    return {"ev": "cliloc", "cliloc": n}


def test_outcome():
    print("the dig classifier on the capture's messages")
    with open(os.path.join(HERE, "data", "loops", "mining.json"), encoding="utf-8") as f:
        know = json.load(f)
    loop = object.__new__(MineLoop)
    loop.k = know

    def out(events):
        loop.link = SimpleNamespace(events=list(events))
        return loop.outcome(0)
    double = "Harvest double yield/loot triggered."
    check("double yield, then 1007072: two ore", out([speech(double), cl(1007072)]) == ("success", 2))
    check("1007072 alone: one ore", out([cl(1007072)]) == ("success", 1))
    # live 2026-11-08 21:26:30-57 (mining-20261108-212058-e379): six avarite digs read "no recognised outcome", the run
    # aborted on the 4th in a row
    check("1095400 'You dig some avarite ore': one ore", out([cl(1095400)]) == ("success", 1))
    check("503043 'You loosen some rocks': a fail", out([cl(503043)]) == ("fail", 0))
    check("500119 'You must wait': busy", out([cl(500119)]) == ("busy", 0))
    check("the travel lockout with its seconds",
          out([speech("You have recently traveled and must wait 7 seconds before you may begin harvesting.")])
          == ("lockout", 7))
    check("the stationary penalty",
          out([speech("A stationary penalty is in effect (must move 5 tiles to remove).")]) == ("stationary", 0))
    check("nothing in reach",
          out([speech("You do not see any harvestable resources nearby.")]) == ("nothing_near", 0))
    check("a no-harvest area", out([speech("Harvesting is not allowed in this area.")]) == ("no_harvest", 0))
    check("riding", out([cl(501864)]) == ("mounted", 0))
    check("no reply yet: None", out([speech("(Harvest Aspect 120/25000 xp)")]) is None)


def spot(sid, status="active", x=1000):
    return {"id": sid, "name": sid, "status": status, "facet": 0, "area": {"center": [x, 1000], "radius": 40},
            "forge": {"x": x, "y": 1000, "z": 0}, "nodes": 30}


def trip(sid, ore, hours=0.5, ago_min=600, why=None, dry=False, blocked=False):
    t1 = NOW - ago_min * 60
    out = {"spot": sid, "t_start": t1 - hours * 3600 - 300, "t_end": t1, "phases_s": {"harvest": hours * 3600 + 120},
           "walk_out_s": 100, "lockout_s": 20, "ore": ore, "why": why, "dry": dry}
    if blocked:
        out["creature_blocked"] = True
    return out


LANDING = {"name": "a rune", "dist": 10, "source": "library", "library": "dtf", "x": 0, "y": 0}


def test_plan():
    print("the planner: eligibility, the pick, the prior")
    spots = {s["id"]: s for s in (spot("A", x=1000), spot("B", x=1100), spot("dry", x=1200), spot("hot", x=1300),
                                  spot("cand", "candidate", x=1400), spot("far", x=1500))}
    episodes = [trip("A", 1000, ago_min=600 + i) for i in range(3)] + [trip("B", 250, ago_min=700 + i) for i in range(3)]
    episodes += [trip("dry", 100, ago_min=30, dry=True), trip("hot", 50, ago_min=10, why="threat: a red at 9 tiles")]
    landings = {sid: (None if sid == "far" else LANDING) for sid in spots}
    out = mining_opt.plan(spots, episodes, NOW, random.Random(1), landings, exploit=True)
    rows = {r["id"]: r for r in out["spots"]}
    check("a dry trip 30 min ago keeps the spot out", rows["dry"]["why_not"] == "dry 30 min ago", rows["dry"]["why_not"])
    check("a threat 10 min ago keeps the spot out", rows["hot"]["why_not"] == "threat 10 min ago",
          rows["hot"]["why_not"])
    check("a candidate isn't planned", rows["cand"]["why_not"] == "candidate", rows["cand"]["why_not"])
    check("no landing: not planned", rows["far"]["why_not"] == "no landing", rows["far"]["why_not"])
    check("exploit picks the 2000 ore/h spot over the 500 one", out["pick"]["spot"] == "A", out["pick"])
    check("P(best) of the 2000 ore/h spot > 0.9", rows["A"]["p_best"] > 0.9, rows["A"]["p_best"])
    check("its command runs it", out["pick"]["command"].startswith("ctl run mining --spot A --trips 1 --field-min 60"),
          out["pick"]["command"])
    check("ineligible rows sort last", [r["eligible"] for r in out["spots"]] == [True, True, False, False, False, False])
    # live 2026-11-07: forge_1951_1523 ended creature-blocked at 14:37:45 (a sand crab's zone held the ore left), was
    # planned again at 14:52:28 and ended blocked at 0 ore in 37 s
    crab = {s["id"]: s for s in (spot("crab", x=1200), spot("old", x=1300))}
    blocked = mining_opt.plan(crab, [trip("crab", 171, ago_min=15, blocked=True, why="every stand left was within a "
                                          "creature's reach at 171 ore"), trip("old", 171, ago_min=70, blocked=True)],
                              NOW, random.Random(1), {sid: LANDING for sid in crab})
    b = {r["id"]: r for r in blocked["spots"]}
    check("a creature-blocked leg 15 min ago keeps the spot out for the regrowth (45 min left); 70 min ago it's back",
          b["crab"]["why_not"] == "creature-blocked 15 min ago" and b["crab"]["ready_in_min"] == 45
          and b["old"]["eligible"], (b["crab"]["why_not"], b["crab"]["ready_in_min"], b["old"]["why_not"]))
    # live 2026-11-10 18:03:20 (mining-20261110-175224-31a4): forge_2426_413's leg ended with its forge cut off by a
    # cougar and an air elemental (stored, not creature-blocked), was planned again 21 s later and its next leg spent
    # 7 min in 4 escapes before it ended creature-blocked
    cut = trip("crab", 49, ago_min=10)
    cut["forge_unreachable"] = "to the forge: every way passes an escaped creature (23 tiles round (2432, 449))"
    c = {r["id"]: r for r in mining_opt.plan(crab, [cut], NOW, random.Random(1), {sid: LANDING for sid in crab})["spots"]}
    check("a leg ended with its forge cut off 10 min ago keeps the spot out for the regrowth (50 min left)",
          c["crab"]["why_not"] == "forge cut off 10 min ago" and c["crab"]["ready_in_min"] == 50,
          (c["crab"]["why_not"], c["crab"]["ready_in_min"]))
    check("every row carries its hazards: deaths/h, sent home/h, P(death) over one run",
          all({"deaths_per_h", "sent_home_per_h", "p_death_stint"} <= r.keys() for r in out["spots"]), out["spots"])
    check("a spot with a sent-home leg is above the pooled sent-home rate",
          rows["hot"]["sent_home_per_h"] > out["sent_home_pooled_per_h"],
          (rows["hot"]["sent_home_per_h"], out["sent_home_pooled_per_h"]))
    check("the pick carries its field rate and P(death) over one run",
          "rate_ore_h" in out["pick"] and 0 < out["pick"]["p_death_stint"] < 1, out["pick"])
    out = mining_opt.plan(spots, episodes, NOW, random.Random(1), landings, exploit=True, exclude={"A"})
    rows = {r["id"]: r for r in out["spots"]}
    check("exclude drops the current spot", rows["A"]["why_not"] == "current spot" and out["pick"]["spot"] == "B",
          (rows["A"]["why_not"], out["pick"]["spot"]))
    out = mining_opt.plan(spots, [], NOW, random.Random(1), landings)
    rows = {r["id"]: r for r in out["spots"]}
    check("a spot without trips has the prior rate", rows["A"]["rate_ore_h"] == mining_opt.PRIOR_ORE_H,
          rows["A"]["rate_ore_h"])
    out = mining_opt.plan({"cand": spot("cand", "candidate")}, [], NOW, random.Random(1), {"cand": LANDING})
    check("no eligible spot: ok false with an error", out["ok"] is False and "no eligible" in out["error"], out)
    cooled = {s["id"]: s for s in (spot("dry", x=1200), spot("hot", x=1300))}
    out = mining_opt.plan(cooled, [trip("dry", 100, ago_min=30, dry=True), trip("hot", 50, ago_min=10, why="threat: x")],
                          NOW, random.Random(1), {sid: LANDING for sid in cooled})
    rows = {r["id"]: r for r in out["spots"]}
    check("every spot cooling down: when each is ready, and the soonest in the error (the overseer waits that long)",
          out["ok"] is False and rows["hot"]["ready_in_min"] == 20 and rows["dry"]["ready_in_min"] == 30
          and out["retry_in_min"] == 20 and "the first one ready: hot in 20 min" in out["error"], out)
    check("a plan without a pick keeps the prior and the pooled figures",
          {"prior_ore_h", "deaths_pooled_per_h", "regrow_min"} <= out.keys(), out.keys())


def test_dashboard_plan():
    print("the dashboard's plan (jobs.mining_plan): the planner plus the legs' and events' extras")
    import jobs
    mem = Memory(os.path.join(tempfile.mkdtemp(), "h.db"))
    dan = {"serial": "0x00000001", "name": "Outland Dan"}
    sid = "forge_2599_620"
    mem.episode("mining", {**trip(sid, 300, ago_min=120), "outcome": "stored", "skill": 71.0, "skill_end": 71.5,
                           "character": dan})
    mem.episode("mining", {**trip(sid, 100, ago_min=60, why="threat: monster an orc at 8 tiles"), "outcome": "aborted",
                           "skill": 71.5, "skill_end": 72.0, "character": dan})
    mem.job_event("mining", "recall", {"spot": sid, "cause": "creature"}, t=NOW - 3700)
    mem.job_event("mining", "recall", {"spot": sid, "cause": "player"}, t=NOW - 3650)
    mem.job_event("mining", "monster_hit", {"spot": sid}, t=NOW - 3680)
    out = jobs.mining_plan(mem, NOW)
    row = next((r for r in out["spots"] if r["id"] == sid), {})
    check("ok, with the newest leg's skill", out["ok"] is True and out["skill"] == 72.0, (out.get("error"), out["skill"]))
    check("the spot's row: name, reach, the dtf landing, legs and threats",
          row.get("name") and row.get("reach") and (row.get("landing") or {}).get("library") == "dtf"
          and row["trips"] == 2 and row["sent_home"] == 1 and row["pvp"] is True, row)
    check("its events: one creature recall, one PK escape, one creature hit; the last leg aborted",
          (row.get("creature_recalls"), row.get("pk_escapes"), row.get("creature_hits")) == (1, 1, 1)
          and row.get("last_outcome") == "aborted", row)
    check("a pick and its command", out.get("pick", {}).get("command", "").startswith("ctl run mining --spot "),
          out.get("pick"))
    mem.close()
    empty = Memory(os.path.join(tempfile.mkdtemp(), "h.db"))
    out = mining_opt.plan_from_store(empty, None, None, None, None, seed=1, now=NOW, route_check=False)
    check("no home: still ranked (every active spot 'no landing'), ok false, no pick",
          out["ok"] is False and out["error"].startswith("no home in harness/data/homes.json for") and out["spots"]
          and all(r["why_not"] == "no landing" for r in out["spots"] if r["status"] == "active")
          and "pick" not in out, {k: out.get(k) for k in ("ok", "error", "pick")})
    empty.close()


def test_hop_choice():
    print("home after a leg cut short: carry the ingots on or store them first (user 2026-10-20)")
    run = {"spot": "B", "expected_ore_h": 1200, "death_h": 0.1}    # storing first: STORE_FIRST_S of ore time
    light, _ = mining_opt.hop_choice(run, 50, 0.5)          # 100 ingots, half an hour of the trip left
    heavy, terms = mining_opt.hop_choice(run, 600, 0.5)     # 1200 ingots
    check("a light load hops on; a heavy one is stored first (risk above storing first's cost)",
          light and not heavy and terms["risk_ore"] > terms["store_cost_ore"], terms)
    late, _ = mining_opt.hop_choice(run, 600, 0.0)
    deadly, _ = mining_opt.hop_choice({**run, "death_h": 1.0}, 100, 0.5)
    check("the same heavy load hops when no field time is left; a deadly spot stores even 200 ingots",
          late and not deadly)


def test_death_events():
    print("a death event within a leg (or just after it) makes the leg a death, not a threat sent home")
    row = {"spot": "A", "t_start": NOW - 600, "t_end": NOW, "phases_s": {"harvest": 300}, "ore": 50,
           "why": "threat: monster a cursed remnant at 18 tiles; escaped by recall to (1883, 1810) in 5.37 s"}
    near, far = mining_opt.trip_obs(row, [NOW + 0.5]), mining_opt.trip_obs(row, [NOW + 3600])
    check("live 2026-10-20 13:14:57: the death 0.5 s after the row's end counts; one an hour later doesn't",
          near["died"] and not near["sent_home"] and not far["died"] and far["sent_home"], (near, far))


def test_memory():
    print("the store's mining tables")
    m = Memory(os.path.join(tempfile.mkdtemp(), "h.db"))
    m.mine_blocks_mark(0, [(10, 20), (11, 20)], "depleted", t=100.0)
    st = m.mine_blocks_state(0, [(10, 20), (11, 20), (12, 20)])
    check("depleted blocks carry depleted_at; unknown ones have no row",
          st == {(10, 20): {"depleted_at": 100.0, "no_harvest": False},
                 (11, 20): {"depleted_at": 100.0, "no_harvest": False}}, st)
    m.mine_blocks_mark(0, [(10, 20)], "no_harvest", t=150.0)
    m.mine_blocks_mark(0, [(10, 20)], "depleted", t=200.0)
    st = m.mine_blocks_state(0, [(10, 20)])
    check("no_harvest sticks after a later 'depleted'", st[(10, 20)] == {"depleted_at": 200.0, "no_harvest": True}, st)
    try:
        m.mine_blocks_mark(0, [(1, 1)], "success")
        check("an unknown block outcome is refused", False)
    except ValueError:
        check("an unknown block outcome is refused", True)
    m.mining_spot_put("forge_1_2", "disabled", {"forge": {"x": 1, "y": 2, "z": 0}}, "runner", "no smelt")
    rows = m.mining_spot_rows()
    check("mining_spot_put round-trips", len(rows) == 1 and rows[0]["id"] == "forge_1_2"
          and rows[0]["status"] == "disabled" and rows[0]["data"] == {"forge": {"x": 1, "y": 2, "z": 0}}
          and rows[0]["reason"] == "no smelt", rows)
    check("the lumber spots stay empty", m.lumber_spot_rows() == [])
    m.close()


def test_reachable_stands():
    print("candidate stands: only the ones the walk reaches from where we stand; the next by walking distance")
    import loop_mining
    import ore_nodes
    # a landing 30 tiles north of the area (center (50, 50), radius 10), a corridor down into its north half;
    # a wall along y 50 cuts the south half off except by a loop east through x 75, outside the area but within
    # STAND_MARGIN; a pocket at (42, 58) has no way in; (47, 32) beside the corridor isn't walkable at all
    open_ = {(50, y) for y in range(20, 41)} | {(x, y) for x in range(40, 61) for y in range(40, 61) if y != 50}
    open_ -= {(x, y) for x in range(40, 45) for y in range(56, 61)}
    open_ |= {(42, 58)} | {(x, 45) for x in range(60, 76)} | {(75, y) for y in range(45, 56)}
    open_ |= {(x, 55) for x in range(60, 76)}

    class Walk:
        m = SimpleNamespace(tiledata=None)

        def can_walk(self, x, y, z, d):
            nx, ny = x + (0, 1, 1, 1, 0, -1, -1, -1)[d], y + (-1, -1, 0, 1, 1, 1, 0, -1)[d]
            return (nx, ny, 0) if z == 0 and (nx, ny) in open_ else None
    stands = [{"x": 45, "y": 45, "n": 1, "blocks": [[5, 5]]}, {"x": 55, "y": 57, "n": 1, "blocks": [[6, 7]]},
              {"x": 42, "y": 58, "n": 1, "blocks": [[5, 7]]}, {"x": 47, "y": 32, "n": 1, "blocks": [[5, 4]]},
              {"x": 55, "y": 52, "n": 1, "blocks": [[6, 6]]}]
    saved = ore_nodes.area_stands, ore_nodes.exclusions, ore_nodes.Tiles, loop_mining.log
    ore_nodes.area_stands = lambda *a, **k: [dict(s) for s in stands]
    ore_nodes.exclusions = lambda facet, db: ([], [], [])
    ore_nodes.Tiles = lambda td: None
    loop_mining.log = lambda s: None
    try:
        loop = object.__new__(MineLoop)
        loop.k = {"harvest": {"area": {"center": [50, 50], "radius": 10}, "stands": []}, "spot": {"id": "t"}}
        loop.facet, loop._ore_tiles, loop.dry_blocks, loop._steps = 0, None, set(), None
        loop.args = SimpleNamespace(memory=":memory:", regrow_min=60)
        loop.memory = SimpleNamespace(mine_blocks_state=lambda facet, blocks: {})
        loop.mover = SimpleNamespace(walk_map=lambda st: Walk(), danger={})
        loop.link = SimpleNamespace(pos=lambda st: [50, 20, 0, 4], state=lambda: {}, last=None)
        cands = loop.candidate_stands({})
        got = sorted((s["x"], s["y"]) for s in cands)
        # from (55, 49), just north of the wall: (55, 52) is 3 tiles off but ~48 steps round by x 75; (45, 45) 10
        loop.link.pos = lambda st: [55, 49, 0, 4]
        loop.no_route_stands, loop.danger = set(), {}
        loop.human = SimpleNamespace(rng=random.Random(7))
        picks = {(p["x"], p["y"]) for p in (loop.next_stand(cands) for _ in range(20))}
        loop.mover.danger = {("seen", 7): ((45, 45), 3)}   # a creature in view among the near stand's rocks
        zoned = {(p["x"], p["y"]) for p in (loop.next_stand(cands) for _ in range(20))}
        loop.mover = SimpleNamespace(walk_map=lambda st: None, danger={})
        loop._steps = None
        no_map = {(p["x"], p["y"]) for p in (loop.next_stand(cands) for _ in range(20))}
    finally:
        ore_nodes.area_stands, ore_nodes.exclusions, ore_nodes.Tiles, loop_mining.log = saved
    check("the north half and the south half by the loop outside the area; not the pocket, not off the walk",
          got == [(45, 45), (55, 52), (55, 57)], got)
    check("the next stand is the nearest on foot, not the nearest as the crow flies", picks == {(45, 45)}, picks)
    check("without a map: Chebyshev", no_map == {(55, 52)}, no_map)
    check("a creature in view standing by the nearest stand: the stand in its zone is left out",
          zoned and (45, 45) not in zoned, zoned)


def test_field_stands():
    print("an ore field: only its cells' stands; one beyond the per-stand flood is still picked when it's the one left")
    import loop_mining
    import ore_nodes
    # a corridor y 10, x 0-200; the field's cells cover x 0-15 and x 144-159 of it; its square spans them all
    open_ = {(x, 10) for x in range(0, 201)}

    class Walk:
        m = SimpleNamespace(tiledata=None)

        def can_walk(self, x, y, z, d):
            nx, ny = x + (0, 1, 1, 1, 0, -1, -1, -1)[d], y + (-1, -1, 0, 1, 1, 1, 0, -1)[d]
            return (nx, ny, 0) if z == 0 and (nx, ny) in open_ else None
    stands = [{"x": 10, "y": 10, "n": 1, "blocks": [[1, 1]]}, {"x": 150, "y": 10, "n": 1, "blocks": [[18, 1]]},
              {"x": 80, "y": 10, "n": 1, "blocks": [[10, 1]]}]
    area = {"center": [80, 7], "radius": 80, "cells": [[0, 1], [1, 1], [18, 1], [19, 1]]}
    saved = ore_nodes.area_stands, ore_nodes.exclusions, ore_nodes.Tiles, loop_mining.log
    ore_nodes.area_stands = lambda *a, **k: [dict(s) for s in stands]
    ore_nodes.exclusions = lambda facet, db: ([], [], [])
    ore_nodes.Tiles = lambda td: None
    loop_mining.log = lambda s: None
    try:
        loop = object.__new__(MineLoop)
        loop.k = {"harvest": {"area": area, "stands": []}, "spot": {"id": "f"}}
        loop.facet, loop._ore_tiles, loop.dry_blocks, loop._steps = 0, None, set(), None
        loop.args = SimpleNamespace(memory=":memory:", regrow_min=60)
        loop.memory = SimpleNamespace(mine_blocks_state=lambda facet, blocks: {})
        loop.mover = SimpleNamespace(walk_map=lambda st: Walk(), danger={})
        loop.link = SimpleNamespace(pos=lambda st: [5, 10, 0, 4], state=lambda: {}, last=None)
        cands = loop.candidate_stands({})
        got = sorted((s["x"], s["y"]) for s in cands)
        loop.no_route_stands, loop.danger = set(), {}
        loop.human = SimpleNamespace(rng=random.Random(7))
        first = loop.next_stand(cands)
        loop.dry_blocks = {(1, 1)}                  # the near stand dug out
        loop.link.pos = lambda st: [6, 10, 0, 4]    # a step off: a new flood, cut to STAND_FLOOD around us
        far = loop.next_stand(cands)
        box = loop._steps[1]
    finally:
        ore_nodes.area_stands, ore_nodes.exclusions, ore_nodes.Tiles, loop_mining.log = saved
    check("the stands in the field's cells, not the one between them", got == [(10, 10), (150, 10)], got)
    check("the near stand first", first is not None and (first["x"], first["y"]) == (10, 10), first)
    check("the far stand, beyond the cut flood, once the near one is dug out (scored by the whole flood then)",
          box[2] >= 150 and far is not None and (far["x"], far["y"]) == (150, 10), (box, far))


def test_next_stand_away_from_escaped():
    print("after an escape the next stand is one reached by a way out of the escaped creature's zone, scored by that "
          "way; none such: blocked, not dry")
    # live 2026-11-07 13:02:36-56 (mining-20261107-125005-79d2, forge_1383_1146): after each escape from a zombie the
    # stand picked lay past it (west), the walk there met it again: 4 escapes in 16 s, then "came back after 3 escapes".
    # User 2026-11-07: "Pick next stand away from it". A corridor y 20-30, x 30-70; the zombie was at (49, 25), its
    # zone 10 spans the corridor; we stand at (62, 25), east of it
    import loop_mining
    corridor = {(x, y) for x in range(30, 71) for y in range(20, 31)}
    loop_ = {(x, y) for x in range(30, 71) for y in range(0, 3)} | {(x, y) for x in range(68, 71) for y in range(0, 31)} \
        | {(x, y) for x in range(30, 33) for y in range(0, 31)}
    open_ = [corridor]

    class Walk:
        m = SimpleNamespace(tiledata=None)

        def can_walk(self, x, y, z, d):
            nx, ny = x + (0, 1, 1, 1, 0, -1, -1, -1)[d], y + (-1, -1, 0, 1, 1, 1, 0, -1)[d]
            return (nx, ny, 0) if z == 0 and (nx, ny) in open_[0] else None
    west = {"x": 32, "y": 25, "blocks": [(1, 1), (1, 2), (1, 3), (1, 4)]}
    east = {"x": 68, "y": 22, "blocks": [(9, 9)]}
    logs = []
    saved = loop_mining.log
    loop_mining.log = logs.append
    try:
        loop = object.__new__(MineLoop)
        loop.k = {"harvest": {"area": {"center": [50, 20], "radius": 20}, "stands": []}, "spot": {"id": "z"}}
        loop.facet, loop.dry_blocks, loop._block_state, loop._steps, loop._clear = 0, set(), {}, None, None
        loop._past_logged = None
        loop.args = SimpleNamespace(regrow_min=60)
        loop.mover = SimpleNamespace(walk_map=lambda st: Walk(), danger={})
        loop.link = SimpleNamespace(pos=lambda st: [62, 25, 0, 4], state=lambda: {}, last=None)
        loop.human = SimpleNamespace(rng=SimpleNamespace(uniform=lambda a, b: 1.0))
        loop.no_route_stands, loop.danger, loop.escaped_from = set(), {}, {}
        before = loop.next_stand([west, east])
        # live 2026-11-08 12:31-12:35 (mining-20261108-122035-886c, Time Quest Hub): a blue player stepped away from
        # (keep_away: self.danger, not escaped_from) across the corridor; no monster escaped
        loop.danger = {9: ((49, 25), 5)}                                # spans the corridor, y 20-30
        thief = loop.next_stand([west, east])
        loop.danger = {7: ((49, 25), 10), ("was", 7): ((49, 25), 10)}     # escaped, out of view
        loop.escaped_from = {7: 1}
        away = loop.next_stand([west, east])
        cap = dict(loop._clear_route or {})
        loop.dry_blocks = {(9, 9)}                                       # the east stand dug out
        none = loop.next_stand([west, east])
        blocked = loop._blocked_only
        open_[0] = corridor | loop_                                      # a way round to the north
        loop.dry_blocks, loop._steps, loop._clear = set(), None, None   # a new map: no kept floods
        round_ = loop.next_stand([west, east])
        far = loop._steps[2][(32, 25)]
        clear = loop._clear_route[(32, 25)]
    finally:
        loop_mining.log = saved
    check("no escape: the west stand (4 blocks, 30 steps) over the east one (1 block, 6 steps)", before is west, before)
    check("after the escape: the east stand, its walk capped by the clear way's 6 steps", away is east
          and cap.get((68, 22)) == 6 and (32, 25) not in cap, (away, cap.get((68, 22))))
    check("a keep-away player across the way: not a wall, the west stand still", thief is west, thief)
    check("only the stand past it left: none, blocked (not dry), logged once",
          none is None and blocked and sum("passed over" in s for s in logs) == 1, (none, blocked, logs))
    check("a way round its zone: the west stand again, scored by the way round", round_ is west and clear > far + 20,
          (round_, far, clear))


def test_run_past():
    print("weak animals on the way to a stand: run past when we ride and the stand is past their chase")
    import loop_lumber
    import threats
    me, horse = 0x100, 0x200

    def state(mounted=True, cougar=(2620, 729), orc=None):
        mobs = {"0x00000C01": {"x": cougar[0], "y": cougar[1]}}
        if orc:
            mobs["0x00000C02"] = {"x": orc[0], "y": orc[1]}
        items = {f"0x{horse:08X}": {"layer": 0x19, "container": f"0x{me:08X}"}} if mounted else {}
        return {"movement": {"self_serial": me, "pos": [2622, 737, 0, 0]},
                "world": {"mobiles": mobs, "items": items, "self": {"map": 0}}}

    cougar = threats.Threat(serial=0xC01, name="a cougar", body=63, notoriety=3, kind="monster", player=None,
                            hostile=True, distance=8)
    orc = threats.Threat(serial=0xC02, name="an orc", body=17, notoriety=3, kind="monster", player=None,
                         hostile=True, distance=6)
    saved = loop_lumber.log
    loop_lumber.log = lambda s: None
    events = []
    try:
        loop = object.__new__(MineLoop)
        loop.mode, loop.watch = "work", threats.Watch()
        loop.run_past_from, loop.ran_past, loop._later = {}, set(), []
        loop.later = lambda fn: loop._later.append(fn)
        loop.memory = SimpleNamespace(job_event=lambda job, kind, data, **w: events.append((kind, data)))
        loop.JOB = "mining"

        def flee(st, goal, monsters=(cougar,), swung=()):
            loop.walk_goal = goal
            return [t.name for t in loop.run_past(st, list(monsters), set(swung))]
        # live mining-20261101-202640-6e00: the stand 2560,686, a cougar at (2620, 729) 8 tiles from Dan
        far = flee(state(), (2560, 686))
        for fn in loop._later:
            fn()
        near = flee(state(), (2630, 745))
        afoot = flee(state(mounted=False), (2560, 686))
        hit_us = flee(state(), (2560, 686), swung={0xC01})
        digging = flee(state(), None)
        both = flee(state(orc=(2625, 731)), (2560, 686), monsters=(cougar, orc))
        loop.run_past_from = {0xC01: (2570, 690)}      # first seen by the stand, chasing us out to (2620, 729)
        came_from_goal = flee(state(), (2560, 686))
    finally:
        loop_lumber.log = saved
    check("riding to a stand 60 tiles from the cougar: run past it, no escape", far == [], far)
    check("one run_past job event for it", [k for k, _ in events] == ["run_past"]
          and events[0][1]["goal"] == [2560, 686], events)
    check("a stand within ESCAPE_RUN of it: fled from as before", near == ["a cougar"], near)
    check("on foot it keeps pace: fled from", afoot == ["a cougar"], afoot)
    check("one that swung at us: fled from", hit_us == ["a cougar"], hit_us)
    check("no walk under way (digging): fled from", digging == ["a cougar"], digging)
    check("an orc isn't a weak animal: still fled from", both == ["an orc"], both)
    check("a stand near where it was first seen: fled from (its spawn is there)", came_from_goal == ["a cougar"],
          came_from_goal)


def field_loop(here=(1, 1, 0)):
    """A MineLoop whose harvest_trip runs with the travel and the world stubbed out: at `here` by the spot, field
    time left, one candidate stand at (1, 1), no house forge (the leg-end smelt would walk to the field forge again)."""
    loop = object.__new__(MineLoop)
    loop.link = SimpleNamespace(state=lambda: {}, pos=lambda st: here)
    loop.break_due = loop.finish_requested = False
    loop.stats, loop.leg_t0, loop.trip_field_s, loop.harvesting = {}, None, 0.0, True
    loop.last_ore_t, loop.stall_stands, loop.fruitless = None, 0, 0
    loop.home = {}
    for name in ("aspect_ensure", "ward_ensure"):
        setattr(loop, name, lambda why: None)
    loop.guard_route_ready = loop.go_out = lambda: None
    loop.track_ensure = lambda why: None
    loop.state = lambda: {}
    loop.candidate_stands = lambda st: [{"x": 1, "y": 1}]
    loop.time_up = lambda: False
    loop.needs_smelt = lambda st: False
    loop.smeltable_ore = lambda st: 0
    return loop


def test_daily_cap_near():
    print("the gate's daily budget nearly used: the trip ends at home like a break, no digging")
    # live 2026-11-08 22:46:40 (mining-20261108-224015-2660): the budget ran out mid-leg, the gate refused the recall
    # home ("action 0x06 refused: ERR daily agent budget exhausted") and Dan was left at forge_2426_413
    import loop_lumber
    loop = field_loop()
    loop.task_id = None
    loop.check_gate({"gate": {"daily_remaining_s": loop_lumber.CAP_MARGIN_S + 60}})
    check("10+ min of budget left: carry on", loop.ending is None, loop.ending)
    loop.check_gate({"gate": {}})
    check("no budget figure: carry on", loop.ending is None, loop.ending)
    loop.check_gate({"gate": {"daily_remaining_s": loop_lumber.CAP_MARGIN_S - 60}})
    check("within CAP_MARGIN_S: the trip winds down", loop.ending == "daily cap near", loop.ending)
    dug = []
    loop.dig_stand = lambda stand, tally: dug.append(stand)
    got = loop.harvest_trip()
    check("... and the leg digs nothing (home, store, the run ends)", got == 0 and dug == [], (got, dug))
    check("... with no hop after it", loop.ended_early() is None, loop.ended_early())


def test_forge_unreachable():
    print("the field forge out of reach with a full pack: the leg ends and the ore goes home, no abort")
    import loop_mining
    from agent_link import WalkFailed
    saved = loop_mining.log
    loop_mining.log = lambda s: None
    smelts = []
    try:
        loop = field_loop()
        loop.needs_smelt = lambda st: True
        loop.smeltable_ore = lambda st: 40

        def smelt(why, home=False):
            smelts.append(why)
            raise WalkFailed("to the forge: no route from (1418, 1143)")
        loop.smelt = smelt
        loop.next_stand = lambda stands: (_ for _ in ()).throw(AssertionError("dug on with a full pack"))
        got = loop.harvest_trip()
    except Exception as e:                    # noqa: BLE001 - the abort this test is about
        got = e
    finally:
        loop_mining.log = saved
    check("the leg returns (no abort)", got == 0, repr(got))
    check("one walk to the forge, not a second at the leg's end", smelts == ["weight"], smelts)
    check("the trip row says why", "no route" in loop.stats.get("forge_unreachable", ""), loop.stats)


def test_stalled_leg():
    print("stands with no route and no ore for minutes: the leg ends (no loop)")
    import loop_mining
    saved = loop_mining.log, loop_mining.time.monotonic
    clock = [1000.0]
    loop_mining.log = lambda s: None
    loop_mining.time.monotonic = lambda: clock[0]
    tried = []
    try:
        loop = field_loop()
        loop.next_stand = lambda stands: stands[0]

        def dig_stand(stand, tally):          # live 18:34-18:39: each stand "no route" after a ~25 s walk
            tried.append(clock[0])
            clock[0] += 25.0
            loop.stall_stands += 1
            if len(tried) > 40:
                raise AssertionError("still looping")
        loop.dig_stand = dig_stand
        got = loop.harvest_trip()
        early = loop.stalled(now=loop.last_ore_t + loop_mining.STALL_S - 1)
        loop.stall_stands = loop_mining.STALL_STANDS - 1
        few = loop.stalled(now=loop.last_ore_t + 600)
    except Exception as e:                    # noqa: BLE001
        got, early, few = e, None, None
    finally:
        loop_mining.log, loop_mining.time.monotonic = saved
    check("the leg ends, counted as creature-blocked (a hop may follow)",
          got == 0 and loop.stats.get("creature_blocked") is True, repr(got))
    check("after STALL_S without ore and STALL_STANDS stands given up",
          len(tried) == max(loop_mining.STALL_STANDS, int(loop_mining.STALL_S // 25) + 1), tried)
    check("not before STALL_S", early is None, early)
    check("not with fewer stands given up", few is None, few)
    # live 2026-11-04 23:04:12-23:05:17 (mining-20261104-230056-1ee0, user: "The runner is stuck in a loop"): after an
    # escape the way on was cut and 3327 stands failed "no route" in 65 s, each at once
    fast = []
    saved = loop_mining.log, loop_mining.time.monotonic
    loop_mining.log = lambda s: None
    loop_mining.time.monotonic = lambda: clock[0]
    try:
        loop = field_loop()
        loop.next_stand = lambda stands: stands[0]

        def no_route(stand, tally):
            fast.append(clock[0])
            clock[0] += 0.02
            loop.stall_stands += 1
            if len(fast) > 200:
                raise AssertionError("still looping")
        loop.dig_stand = no_route
        got = loop.harvest_trip()
    except Exception as e:                    # noqa: BLE001
        got = e
    finally:
        loop_mining.log, loop_mining.time.monotonic = saved
    check("stands failing at once: the leg ends after STALL_STANDS_MAX of them, well before STALL_S",
          got == 0 and loop.stats.get("creature_blocked") is True and len(fast) == loop_mining.STALL_STANDS_MAX,
          (repr(got), len(fast)))


def test_far_stand_route_cap():
    print("a stand beyond the walking flood: its walk is capped by its steps in the whole area's flood (Chebyshev if "
          "even that doesn't reach it), as a flooded one by its steps")
    # live 2026-11-04 23:17:57-23:19:51 (mining-20261104-231210-dd9f, forge_2551_665): stands 81-83 tiles off had no
    # cap, routes of 424-623 steps round creatures, two walks went round in circles, 2 min without ore. 2026-11-05
    # 13:31:24-30 (mining-20261105-133044-ff72): capped at their Chebyshev distance, 30 such stands of a multi-level
    # ruin failed in 6 s and the leg ended at 0 ore
    import loop_mining
    from agent_link import WalkFailed
    caps, floods = {}, []

    def walk(xy, r, label, max_route=None):
        caps[xy] = max_route
        raise WalkFailed(f"{label}: only a 424-step detour")

    def walk_steps(st, whole=False):
        floods.append(whole)
        return {(2520, 660): 12, (2592, 640): 140}           # the whole area: (2592, 640) 140 steps by the ramps
    loop = object.__new__(MineLoop)
    loop.link = SimpleNamespace(state=lambda: {}, pos=lambda st: (2511, 659, 0))
    loop.state = lambda: {}
    loop.doing = lambda *a: None
    loop.walk_to_work = walk
    loop.walk_steps = walk_steps
    loop.no_route_stands, loop.stall_stands, loop.fruitless = set(), 0, 0
    loop._steps = (None, None, {(2520, 660): 12})          # the 80-tile flood reached (2520, 660) in 12 steps
    loop._clear_route = None
    saved = loop_mining.log
    loop_mining.log = lambda s: None
    try:
        for xy in ((2520, 660), (2592, 640), (2600, 700)):
            loop.dig_stand({"x": xy[0], "y": xy[1], "blocks": []}, {"ore": 0})
    finally:
        loop_mining.log = saved
    check("flooded: 10 + 2 x 12 steps; beyond it: 10 + 2 x its 140 steps in the whole flood; reached by neither: "
          "10 + 2 x its 89 tiles; the whole flood only for the stands beyond",
          caps == {(2520, 660): 34, (2592, 640): 290, (2600, 700): 188} and floods == [True, True]
          and loop.no_route_stands == {(2520, 660), (2592, 640), (2600, 700)}, (caps, floods))


def test_field_forge_route_cap():
    print("with a house forge, the field forge's walk is capped like a stand's (its walking steps); without one it isn't")
    # live 2026-11-04 23:30:58 (mining-20261104-232153-a8e3, forge_2551_665): an 831-step route to the forge from a field
    # stand 112 tiles (181 walking steps on the map) off went round in circles; the leg ended and the ore went home anyway
    from agent_link import WalkFailed
    with open(os.path.join(HERE, "data", "loops", "mining.json"), encoding="utf-8") as f:
        know = json.load(f)
    know["forge"] = {"x": 2551, "y": 665}
    caps = []
    flood = [{(2551, 667): 181, (2552, 663): 183, (2560, 600): 70}]      # the tiles within forge_range 2: 181 the nearest

    def walk_to(center, r, label, max_route=None, **kw):
        caps.append(max_route)
        raise WalkFailed(f"{label}: only an 831-step detour")
    loop = object.__new__(MineLoop)
    loop.k, loop.unsmeltable = know, set()
    loop.link = SimpleNamespace(state=lambda: {}, pos=lambda st: (2569, 553, 0))
    loop.state = lambda: {}
    loop.walk_steps = lambda st, whole=False: flood[0]
    loop.in_pack = lambda st, graphics: [(0x10, {"graphic": 0x19B9, "amount": 280, "hue": 0})] \
        if 0x19B9 in graphics else []
    loop.doing = lambda *a: None
    loop.mover = SimpleNamespace(walk_to=walk_to)
    zones, clear, every = [[]], [{}], [[]]
    loop.escape_zones = lambda st: zones[0]
    loop.danger_zones = lambda escaped_only=False: every[0]
    loop.clear_steps = lambda st, box, z: clear[0]
    loop._steps = (None, None, None)
    for home, steps in (({"forge": {"serial": "0x00000099", "pos": [3574, 3250, 0]}}, flood[0]),
                        ({"forge": {"serial": "0x00000099", "pos": [3574, 3250, 0]}}, None), ({}, flood[0])):
        loop.home, flood[0] = home, steps
        try:
            loop.smelt("weight")
        except WalkFailed:
            pass
    check("house forge: 10 + 2 x its 181 walking steps = 372 (no map: x its 112 tiles = 234); none: no cap",
          caps == [372, 234, None], caps)
    # live 2026-11-08 12:27:49-12:28:17 (mining-20261108-122035-886c, forge_2426_413): each walk to the forge went back
    # past one air dragon, 3 escapes in 21 s
    house = {"forge": {"serial": "0x00000099", "pos": [3574, 3250, 0]}}
    loop.home, flood[0], zones[0] = house, {(2551, 667): 181}, [((2555, 640), 14)]
    caps.clear()
    try:
        loop.smelt("weight")
        why = None
    except WalkFailed as e:
        why = str(e)
    clear[0] = {(2551, 667): 230}                           # a way round its zone, 230 steps
    try:
        loop.smelt("weight")
    except WalkFailed:
        pass
    check("after an escape: no way to the forge out of the creature's zone: the leg's WalkFailed, no walk; "
          "a way round: capped by its 230 steps (470)",
          why is not None and "every way passes an escaped creature" in why and caps == [470], (why, caps))
    # live 2026-11-08 16:18:32-48 (mining-20261108-160947-05be, forge_1524_2698): a cultist's 19-tile zone held the forge
    # and each escape ended inside that zone, so escape_zones left it out; 3 escapes in 16 s
    caps.clear()
    zones[0], every[0], clear[0] = [], [((2545, 660), 19)], {(2551, 667): 30}
    try:
        loop.smelt("weight")
        held = None
    except WalkFailed as e:
        held = str(e)
    check("the forge within an escaped creature's zone we stand in too: the leg's WalkFailed, no walk",
          held is not None and "within an escaped creature's reach" in held and caps == [], (held, caps))


def test_stash_lift_refused():
    print("a stash lift the server refuses is sent again, no drop after a refused lift; refused every time: left loose")
    # live 2026-11-07 18:19:52 (mining-20261107-172756-cdaa): the lift of 452 ingots 0.38 s after the smelt's double-click
    # got cliloc 500119 and 0x27 05; the drop went out anyway and the stack stayed loose in the pack
    import actions
    import loop_lumber
    pouch, ingots = 0x4E530EBD, 0x4E80E520

    def run(refusals):
        sent, held = [], [0]
        link = SimpleNamespace(events=[], last={})

        def act(pkt):
            sent.append(pkt[0])
            if pkt == actions.lift(ingots, 452) and refusals[0] > 0:
                refusals[0] -= 1
                link.events += [{"ev": "cliloc", "cliloc": 500119}, {"ev": "lift_reject", "reason": 5}]
            elif pkt[0] == 0x08:
                held[0] += 452
        link.act, link.state = act, lambda: {}
        loop = object.__new__(MineLoop)
        loop.link, loop.stats = link, {}
        loop.human = SimpleNamespace(wait=lambda kind: None)
        loop.ledger = SimpleNamespace(expect=lambda *a: None)
        loop.state = lambda: {}
        loop.in_pack = lambda st, graphics, top=False: [(ingots, {"amount": 452})]
        loop.log_pouch = lambda st: pouch
        loop.open_for = lambda *a: None
        loop.held = lambda st, p, graphics: held[0]
        loop.wait_for = lambda pred, timeout: {} if pred({}) else None
        return loop.stash(), sent
    saved = loop_lumber.log, loop_lumber.STASH_RETRY_S
    loop_lumber.log, loop_lumber.STASH_RETRY_S = (lambda s: None), 0.0
    try:
        once = run([1])
        always = run([9])
    finally:
        loop_lumber.log, loop_lumber.STASH_RETRY_S = saved
    check("refused once: lifted again, then dropped into the pouch", once == (452, [0x07, 0x07, 0x08]), once)
    check("refused every time: STASH_LIFT_TRIES lifts, no drop, nothing moved",
          always == (0, [0x07] * loop_lumber.STASH_LIFT_TRIES), always)


def test_far_stand_scored_by_steps():
    print("a pick beyond the 80-tile flood is scored again by the whole flood's walking steps")
    # live 2026-11-06 14:47:27 (mining-20261106-143925-85da, forge_2551_665): a stand 81 tiles but ~280 steps off
    # (scored at 81) beat the near ones; 1 min of 411-554-step routes went round in circles
    near, far_s = {"x": 2520, "y": 660, "blocks": [1]}, {"x": 2592, "y": 646, "blocks": [1, 2, 3, 4, 5, 6]}
    box80, whole_box = (2431, 578, 2591, 738), (2397, 509, 2713, 825)
    floods = []
    loop = object.__new__(MineLoop)
    loop.link = SimpleNamespace(state=lambda: {}, pos=lambda st: (2511, 658, 0), last=None)
    loop.mover = SimpleNamespace(danger={})
    loop.danger = {}
    loop.human = SimpleNamespace(rng=SimpleNamespace(uniform=lambda a, b: 1.0))
    loop.no_route_stands = set()
    loop.avail = lambda s: s["blocks"]

    def walk_steps(st, whole=False):
        floods.append(whole)
        loop._steps = (None, whole_box if whole else box80,
                       {(2520, 660): 12, (2592, 646): 280} if whole else {(2520, 660): 12})
        return loop._steps[2]
    loop.walk_steps = walk_steps
    got = loop.next_stand([near, far_s])
    check("the near stand (1 block, 12 steps) over the far one (6 blocks, 81 tiles but 280 steps); one whole flood",
          got is near and floods == [False, True], (got, floods))


def test_last_pickaxe_worn_out():
    print("the last pickaxe wears out mid-leg: a low_supplies juncture (item pickaxe), then the abort")
    # live 2026-11-05 14:19 (mining-20261105-133044-ff72): both pickaxes wore out after ~2200 ore; the run aborted
    # "no pickaxe worn, in the backpack or in a bag in it" with no low_supplies, the juncture the Seer acts on
    from agent_link import Abort
    posted = []
    loop = object.__new__(MineLoop)
    loop.JOB, loop.trip_n = "mining", 1
    loop.self_serial = lambda st: 0x14683F
    loop.pickaxes = lambda st: []
    loop.memory = SimpleNamespace(juncture=lambda job, kind, summary, sev, data: posted.append((kind, data["item"])))
    try:
        loop.pickaxe_in_hand({"world": {"items": {}}})
        out = None
    except Abort as e:
        out = str(e)
    check("low_supplies for the pickaxe, then Abort", posted == [("low_supplies", "pickaxe")]
          and out == "no pickaxe worn, in the backpack or in a bag in it", (posted, out))


def test_teleported_away():
    print("a walk's unplanned teleport far from the spot: the leg ends at once, no stand tried from there")
    import loop_mining
    # live 2026-11-03 14:30:12 (mining-20261103-141742-4bc0): a stand walk at forge_3537_1948 put Dan at (7338, 1220)
    # through an unknown teleporter; 90 stands "no route" for 110 s, then the leg ended "the area ran dry"
    saved = loop_mining.log
    loop_mining.log = lambda s: None
    dug = []
    try:
        far = field_loop(here=(7338, 1220, -55))
        far.next_stand = lambda stands: stands[0]
        far.dig_stand = lambda stand, tally: dug.append(stand)
        got = far.harvest_trip()
        edge = field_loop(here=(1 + loop_mining.AWAY_TILES, 1, 0)).away([{"x": 1, "y": 1}])
    finally:
        loop_mining.log = saved
    check("the leg ends with nothing dug, why in the trip row", got == 0 and dug == []
          and "teleported away" in far.stats.get("away", ""), (got, dug, far.stats))
    check("AWAY_TILES from the nearest stand is still at the spot", edge is None, edge)


def test_gate_out():
    print("a recall home the place refuses: through the moongate in view to Cambria, then the recall again")
    # live 2026-11-03 14:32:06.87 (mining-20261103-141742-4bc0): "Magical travel is not allowed in this location." at the
    # arrival gate (7338, 1220); six recalls read as "no arrival", the run aborted there. By hand the gate's Cambria row
    # and a recall from there took Dan home (14:38)
    import escape
    import loop_lumber
    import moongate
    from agent_link import Abort, Mover
    check("the refusal text is a 'restricted' failure (no retry)",
          escape.failure({"ev": "speech_heard", "text": "Magical travel is not allowed in this location."}, 0)
          == "restricted" and escape.retry_wait({"failure": "restricted"}) is None)

    def run(gate_at):
        calls, events = [], []
        pos = [7338, 1220, -55]
        items = {} if gate_at is None else {"0x40001234": {"graphic": 0x0F6C, "x": gate_at[0], "y": gate_at[1], "z": -55}}
        state = {"world": {"items": items}}
        loop = object.__new__(MineLoop)
        loop.JOB, loop.trip_n, loop.recall_book, loop.home_rune_name = "mining", 1, 0x49865F8F, None
        loop.home = {"landing": [3575, 3251, 0]}
        loop.link = SimpleNamespace(state=lambda: state, pos=lambda st: pos, events=events,
                                    act=lambda pkt: calls.append(("dclick", int.from_bytes(bytes(pkt)[1:5], "big"))))
        loop.mover = object.__new__(Mover)
        loop.mover.use_map = False
        loop.mover.walk_to = lambda goal, r, label, **kw: calls.append(("walk", tuple(goal())))
        loop.human = SimpleNamespace(wait=lambda kind: None)
        loop.at_home = lambda st: tuple(pos[:2]) == (3575, 3251)
        loop.doing = loop.travel_leg = loop.note_book_charge = lambda *a, **k: None
        loop.supplies_now = lambda st: {}
        loop._where = lambda st: {}
        loop.memory = SimpleNamespace(job_event=lambda job, kind, data, **kw: calls.append((kind, data["ok"])))

        def recall(io, book, **kw):
            calls.append(("recall", tuple(pos[:2])))
            if tuple(pos[:2]) == (7338, 1220):
                return {"ok": False, "failure": "restricted"}
            pos[:] = [3575, 3251, 0]
            return {"ok": True, "failure": None}

        def travel(link, human, dest, since=0, log=None, urgent=False):
            calls.append(("travel", dest))
            pos[:] = [1742, 2872, 0]
            return {"ok": True, "from": [7338, 1220], "to": pos[:], "dest": dest}
        saved = escape.escape, escape.LinkIO, moongate.travel, loop_lumber.log
        escape.escape, escape.LinkIO, moongate.travel, loop_lumber.log = recall, (lambda link: None), travel, \
            (lambda s: None)
        try:
            loop.go_home()
            out = "home"
        except Abort as e:
            out = str(e)
        finally:
            escape.escape, escape.LinkIO, moongate.travel, loop_lumber.log = saved
        return calls, out
    calls, out = run((7338, 1220))
    check("on the gate: refused, the gate's double-click, Cambria, the recall from there lands home",
          out == "home" and calls == [("recall", (7338, 1220)), ("dclick", 0x40001234), ("travel", "Cambria"),
                                      ("gate_out", True), ("recall", (1742, 2872))], (out, calls))
    calls, out = run((7340, 1226))
    check("a gate 6 tiles off: walked onto, then Cambria", calls[1:3] == [("walk", (7340, 1226)), ("travel", "Cambria")]
          and out == "home", (out, calls))
    calls, out = run(None)
    check("no gate in view: the run aborts where it is, one recall tried",
          out == "recall home failed: restricted (at (7338, 1220))" and calls == [("recall", (7338, 1220))], (out, calls))


def test_cut_off_level():
    print("a melee creature on a ledge above with no way down: not fled from, not zoned")
    import threats
    import loop_lumber
    import nav

    class Levels:
        """z 0 ground x 0..40; a ledge at z 50 over x 0..40, y -12..-6; `ramp` joins them (or not)."""
        def __init__(self, ramp):
            self.ramp = ramp

        def can_walk(self, x, y, z, d):
            nx, ny = nav.step((x, y), d)
            if not (0 <= nx <= 40):
                return None
            if z == 0 and -5 <= ny <= 10:
                return (nx, ny, 0)
            if z == 50 and -12 <= ny <= -6:
                return (nx, ny, 50)
            if self.ramp and (nx, ny) in {(20, -5), (20, -6)} and abs(ny - y) == 1 and nx == x:
                return (nx, ny, 0 if ny == -5 else 50)
            return None

    def verdict(ramp, reach=1, sz=50, us=(5, 0, 0)):
        loop = object.__new__(MineLoop)
        loop._cut_off = {}
        loop.mover = SimpleNamespace(walk_map=lambda st: Levels(ramp))
        st = {"movement": {"pos": list(us) + [0]},
              "world": {"mobiles": {"0x00000C03": {"x": 5, "y": -7, "z": sz}}}}
        t = threats.Threat(serial=0xC03, name="a skeleton", body=50, notoriety=3, kind="monster", player=None,
                           hostile=True, distance=7, reach=reach)
        return loop.cut_off(st, t)
    saved = loop_lumber.log
    loop_lumber.log = lambda s: None
    try:
        # live 2026-11-02 21:23-21:24 (mining-20261102-211805-d8d8): a skeleton at z 50 on a ledge 7 tiles off Dan at z 0
        ledge = verdict(ramp=False)
        near_ramp = verdict(ramp=True)        # the ramp at x 20: 15 tiles along the ledge, ~25 steps down to us
        ramp_near_us = verdict(ramp=True, us=(20, 0, 0))
        ranged = verdict(ramp=False, reach=10)
        same_level = verdict(ramp=False, sz=10)
    finally:
        loop_lumber.log = saved
    check("no way down: cut off", ledge is True, ledge)
    check("a way down beyond LEVEL_ROUTE_MAX steps: still cut off", near_ramp is True, near_ramp)
    check("a way down within LEVEL_ROUTE_MAX: a threat", ramp_near_us is False, ramp_near_us)
    check("a ranged one shoots across levels: a threat", ranged is False, ranged)
    check("within LEVEL_APART_Z: a threat", same_level is False, same_level)


def test_fall_hit():
    print("hits lost right after a step off a ledge: fall damage, walk on (not 'taking damage, no creature in view')")
    import time
    import loop_lumber
    saved = loop_lumber.log
    loop_lumber.log = lambda s: None
    try:
        def verdict(lost=5, ago=0.3, swung=(), damage_events=0, spelled=()):
            loop = object.__new__(MineLoop)
            later = []
            loop.mover = SimpleNamespace(fell=(time.monotonic() - ago, (1216, 2392), 23))
            loop.spelled, loop.trip_n, loop.k, loop.JOB = list(spelled), 1, {"spot": {"id": "forge_1173_2397"}}, "mining"
            loop.later = later.append
            loop.watch = SimpleNamespace(acknowledge=lambda: None)
            st = {"world": {"self": {"hits": 95, "hits_max": 100, "map": 0}}, "movement": {"pos": [1216, 2392, 23, 2]}}
            a = SimpleNamespace(damage={"lost": lost, "damage_events": damage_events})
            return loop.fall_hit(st, a, set(swung)), later
        # live 2026-11-02 21:51:16 (forge_1173_2397): -5 as a bare 0xA1 0.007 s after the step onto (1216, 2392)
        fall, later = verdict()
        stale, _ = verdict(ago=5.0)
        swing, _ = verdict(swung={0xC01})
        packet, _ = verdict(damage_events=1)
        spell, _ = verdict(spelled=[0xC02])
    finally:
        loop_lumber.log = saved
    check("a hit 0.3 s after the fall: the fall's, dealt with, a `fall` event", fall is True and len(later) == 1, fall)
    check("not when the fall was long ago", stale is False, stale)
    check("not with a swing at us, a damage packet or a spell", (swing, packet, spell) == (False, False, False),
          (swing, packet, spell))


def test_in_guards():
    print("in the guards after a flight: say 'guards', then the recall home (a drill: no alarm)")
    import time
    import loop_lumber
    from loop_lumber import Unsafe
    saved = loop_lumber.log
    loop_lumber.log = lambda s: None

    def run(drill=False, recall=None):
        loop = object.__new__(MineLoop)
        said, events, junctures, homes = [], [], [], []
        st = {"world": {"self": {"map": 0}, "mobiles": {}}, "movement": {"pos": [3840, 2881, 0, 2]}}
        loop.link = SimpleNamespace(state=lambda: st, act=lambda pkt: said.append(pkt), pos=lambda s: s["movement"]["pos"])
        loop.watch = SimpleNamespace(update=lambda s, **k: SimpleNamespace(threats=[]))
        loop.memory = SimpleNamespace(job_event=lambda job, kind, data, **w: events.append(kind),
                                      juncture=lambda job, kind, text, sev, data: junctures.append((kind, text)))
        loop.drill, loop._guard_run, loop.hit_by, loop.JOB = drill, True, [], "mining"
        loop.go_home = lambda: homes.append(True)
        loop.post_threat = lambda *a, **k: "red X"

        def recall_out(*a, **k):
            if recall == "lands":
                raise Unsafe("escaped by recall")
            return "recall failed after 1 cast(s): disturbed"
        loop.recall_out = recall_out
        try:
            loop.in_guards(None, set(), "flee", time.monotonic(), True)
            out = "no raise"
        except Unsafe as e:
            out = str(e)
        return said, events, junctures, homes, out
    try:
        said, events, junctures, homes, out = run(recall="lands")
        guards_said = len(said) == 1 and b"guards" in bytes(said[0]).replace(b"\x00", b"")
        check("a flight: 'guards' said, the guard_flight event, then the recall home (it lands: Unsafe from it)",
              guards_said and events == ["guard_flight"] and out == "escaped by recall", (events, out))
        said, events, junctures, homes, out = run(recall="fails")
        check("the recall fails: an urgent pk_escape juncture there, Unsafe",
              [k for k, _ in junctures] == ["pk_escape"] and "recall home failed" in out, (junctures, out))
        said, events, junctures, homes, out = run(drill=True)
        check("a flee drill: 'guards', a flee_drill event, home by the book, no juncture",
              len(said) == 1 and events == ["flee_drill"] and homes == [True] and not junctures, (events, junctures))
    finally:
        loop_lumber.log = saved


def test_red_flight_zones():
    print("running from a red: no route round creature zones meanwhile, back after")
    loop = object.__new__(MineLoop)
    zone = {("seen", 1): ((10, 10), 13)}
    loop.mover = SimpleNamespace(danger=dict(zone), danger_tiles={(5, 5)}, avoid={"spray": {(6, 6)}})
    loop.red_flight = None
    loop.red_flight_start()
    during = (loop.mover.danger, loop.mover.danger_tiles, loop.mover.avoid)
    loop.aggro_zones({})                      # a state read meanwhile adds no zone (and doesn't look at the state)
    after_read = dict(loop.mover.danger)
    loop.red_flight_end()
    check("set aside while running from a player", during == ({}, set(), {}) and after_read == {}, (during, after_read))
    check("back afterwards", loop.mover.danger == zone and loop.mover.danger_tiles == {(5, 5)}
          and loop.red_flight is None, loop.mover.danger)


def test_smelt_busy():
    print("an ore stack's double-click answered 'You must wait to perform another action.': pressed again, smelted")
    # live 2026-11-03 14:17:07.04 (mining-20261103-140347-e657): the 6 shadow ore's press 0.49 s after the last stack's
    # got 500119; the runner logged "smelt: no answer to 6 ore", left them in the pack and stored the rest
    import loop_mining
    with open(os.path.join(HERE, "data", "loops", "mining.json"), encoding="utf-8") as f:
        know = json.load(f)
    know["spot"] = {"id": "forge_1_2"}
    ore = {0x10: {"graphic": 0x19B9, "amount": 100, "hue": 0}, 0x11: {"graphic": 0x19B9, "amount": 6, "hue": 0x966}}
    ingots = {}
    pressed, events, logged = [], [], []

    def act(pkt):
        serial = int.from_bytes(bytes(pkt)[1:5], "big")
        pressed.append(serial)
        if serial == 0x11 and pressed.count(0x11) == 1:       # the press too soon after the last one's
            events.append(cl(500119))
            return
        it = ore.pop(serial)
        ingots[serial | 0x100] = {"graphic": 0x1BF2, "amount": 2 * it["amount"], "hue": it["hue"]}
        events.append(cl(501988))
    loop = object.__new__(MineLoop)
    loop.k, loop.home, loop.unsmeltable, loop.stats, loop.timing, loop.trip_n = know, \
        {"forge": {"serial": "0x00000099", "pos": [3, 4, 0]}}, set(), {}, {"lockout_s": 0.0}, 1
    loop.link = SimpleNamespace(events=events, act=act, open_containers=lambda *a: None, state=lambda: {"world": {}})
    loop.state = lambda: {"world": {}}
    loop.wait_for = lambda pred, timeout: {"world": {}} if pred({"world": {}}) else None
    loop.item = lambda st, serial: {} if serial == 0x99 or serial in ore else None
    loop.in_pack = lambda st, graphics: [(s, it) for s, it in {**ore, **ingots}.items() if it["graphic"] in graphics]
    loop.doing = lambda *a: None
    loop.mover = SimpleNamespace(walk_to=lambda *a, **kw: None)
    loop.human = SimpleNamespace(wait=lambda kind: None)
    loop.ledger = SimpleNamespace(expect=lambda what: None)
    loop.memory = SimpleNamespace(job_event=lambda job, kind, data, **kw: logged.append((kind, data)))
    loop._where = lambda st: {}
    saved = loop_mining.log, loop_mining.containers_to_open, loop_mining.BUSY_RETRY_S
    loop_mining.log, loop_mining.containers_to_open, loop_mining.BUSY_RETRY_S = (lambda s: None), (lambda w, s: []), 0.0
    try:
        made = loop.smelt("home", home=True)
    finally:
        loop_mining.log, loop_mining.containers_to_open, loop_mining.BUSY_RETRY_S = saved
    check("the busy stack pressed again and smelted: no ore left, 212 ingots", not ore and made == 212
          and pressed == [0x10, 0x11, 0x11] and not loop.unsmeltable, (ore, made, pressed, loop.unsmeltable))
    check("the smelt event counts all 106 ore", [d["ore"] for k, d in logged if k == "smelt"] == [106], logged)


def main():
    for t in (test_outcome, test_plan, test_dashboard_plan, test_hop_choice, test_death_events, test_memory,
              test_reachable_stands, test_field_stands, test_next_stand_away_from_escaped, test_run_past,
              test_forge_unreachable, test_stalled_leg,
              test_cut_off_level, test_fall_hit, test_in_guards, test_red_flight_zones, test_smelt_busy,
              test_teleported_away, test_gate_out, test_far_stand_route_cap, test_field_forge_route_cap,
              test_last_pickaxe_worn_out, test_stash_lift_refused, test_far_stand_scored_by_steps,
              test_daily_cap_near):
        t()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)}: {FAILURES}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

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


def trip(sid, ore, hours=0.5, ago_min=600, why=None, dry=False):
    t1 = NOW - ago_min * 60
    return {"spot": sid, "t_start": t1 - hours * 3600 - 300, "t_end": t1, "phases_s": {"harvest": hours * 3600 + 120},
            "walk_out_s": 100, "lockout_s": 20, "ore": ore, "why": why, "dry": dry}


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
        loop.link = SimpleNamespace(pos=lambda st: [50, 20, 0, 4], state=lambda: {})
        cands = loop.candidate_stands({})
        got = sorted((s["x"], s["y"]) for s in cands)
        # from (55, 49), just north of the wall: (55, 52) is 3 tiles off but ~48 steps round by x 75; (45, 45) 10
        loop.link.pos = lambda st: [55, 49, 0, 4]
        loop.no_route_stands, loop.out_of_reach = set(), lambda x, y: True
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


def main():
    for t in (test_outcome, test_plan, test_dashboard_plan, test_hop_choice, test_death_events, test_memory,
              test_reachable_stands):
        t()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)}: {FAILURES}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

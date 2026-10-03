"""Behaviour of the lumber optimizer (harness/lumber_opt.py, docs/LUMBER_LOOP.md §6):
which spot it picks, how much it carries, which hatchet, and what it learns from
trips, deaths and depleted trees. Synthetic trip rows; no network, no game.

Run: python harness/test_lumber_opt.py
"""
import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lumber_opt as lo  # noqa: E402
from memory import Memory  # noqa: E402

FAILURES = []
NOW = 1_800_000_000.0
TABLE = lo.load_hatchets()


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}{'' if cond else ' ' + str(detail)}")
    if not cond:
        FAILURES.append(name)


def spot(sid, x=1000, y=1000, pvp=True, hazard=0.5, **kw):
    return {"id": sid, "name": sid, "facet": 0, "area": {"center": [x, y], "radius": 20},
            "banker": {"serial": "0x00000000", "name": "b", "pos": [x - 40, y, 0]}, "pvp": pvp,
            "hazard_prior": hazard, "travel_min": 0, "status": "active", **kw}


def trip(sid, t1, logs, field_s, chop_share=0.7, skill=None, outcome="banked", dry=False, why=None,
         walk_out=60.0, convert=10.0, to_bank=60.0, store=5.0):
    phases = {"harvest": walk_out + field_s}
    if outcome == "banked":
        phases.update(convert=convert, to_bank=to_bank, store=store)
    t0 = t1 - sum(phases.values())
    return {"loop": "lumber", "spot": sid, "t_start": t0, "t_end": t1, "outcome": outcome, "why": why, "dry": dry,
            "logs": logs, "walk_out_s": walk_out, "chop_s": field_s * chop_share, "skill": skill,
            "hatchet": {"tool_bonus": 0.0}, "phases_s": phases}


def series(sid, n, rate, field_s=1800.0, skill=None, start=NOW - 86400, gap=3600):
    """n banked trips of field_s seconds at `rate` logs per field hour."""
    return [trip(sid, start + i * gap, round(rate * field_s / 3600.0), field_s, skill=skill) for i in range(n)]


def regrow(minutes=45.0):
    return {"minutes": minutes, "pairs": 0, "fitted": False}


def plan(spots, episodes, char=None, deaths=(), sightings=(), seed=1, **kw):
    return lo.plan({s["id"]: s for s in spots}, episodes, list(sightings), list(deaths), kw.pop("regrow", regrow()),
                   char, TABLE, kw.pop("prices", {}), kw.pop("now", NOW), random.Random(seed), draws=600, **kw)


def row(out, sid):
    return next(r for r in out["spots"] if r["id"] == sid)


def char(skill, *hatchets):
    return {"skill": skill, "hatchets": [{"serial": f"0x{i:08X}", "worn": i == 0, "name": None, **h}
                                         for i, h in enumerate(hatchets)]}


def iron():
    return lo.hatchet_kind({"hue": 0}, TABLE)


def copper():
    return lo.hatchet_kind({"hue": 2413}, TABLE)


def test_explore_exploit():
    print("== a well-measured good spot is exploited, an unvisited one explored, a poor one dropped ==")
    spots = [spot("good", 1000, 1000), spot("poor", 2000, 2000), spot("new", 3000, 3000)]
    eps = series("good", 12, 1800) + series("poor", 12, 600)
    out = plan(spots, eps)
    pb = {s["id"]: row(out, s["id"])["p_best"] for s in spots}
    check("P(best): good > new > poor, and the poor spot is practically never best",
          pb["good"] > pb["new"] > pb["poor"] and pb["poor"] < 0.02, pb)
    picks = [plan(spots, eps, seed=s)["pick"]["spot"] for s in range(60)]
    check("Thompson picks: mostly the good spot, the unvisited one sometimes, the poor one never",
          picks.count("good") > picks.count("new") > 0 and picks.count("poor") == 0,
          {k: picks.count(k) for k in pb})
    out2 = plan(spots, eps + series("new", 12, 500, start=NOW - 40000, gap=1800))
    check("once the new spot proves poor it stops being explored", row(out2, "new")["p_best"] < 0.02,
          row(out2, "new"))


def test_skill_rescaling():
    print("== a spot measured at lower skill is credited with what it yields at today's skill ==")
    spots = [spot("a")]
    eps = series("a", 10, 1000, skill=60.0)
    low = plan(spots, eps, char=char(60.0, iron()))
    high = plan(spots, eps, char=char(90.0, iron()))
    r_low, r_high = row(low, "a")["rate_logs_h"], row(high, "a")["rate_logs_h"]
    gain = lo.success_p(90.0) / lo.success_p(60.0)
    check("higher skill now: higher field rate, but less than the success ratio (walking doesn't speed up)",
          r_high > r_low * 1.15 and r_high < r_low * gain, (r_low, r_high, round(gain, 2)))
    unknown = plan(spots, series("a", 10, 1000))
    check("trips without a recorded skill aren't rescaled",
          abs(row(unknown, "a")["rate_logs_h"] - row(plan(spots, series("a", 10, 1000), char=char(90.0, iron())),
                                                     "a")["rate_logs_h"]) <= 1)


def test_trip_size_and_hazard():
    print("== how much to carry: less where PKs are, everything (up to the stint) where there are none ==")
    lam, t_h = 1500.0, 150 / 3600.0
    safe, risky, deadly = (lo.best_q(lam, t_h, h, 3.0, 5000) for h in (0.0, 0.05, 0.5))
    check("trip size falls as the death rate rises", safe > risky > deadly, (safe, risky, deadly))
    check("no hazard: the largest trip the stint allows", safe == max(q for q in lo.Q_GRID if q <= 5000), safe)
    spots = [spot("calm", hazard=0.05), spot("pk", 2000, 2000, hazard=3.0)]
    eps = series("calm", 8, 1500) + series("pk", 8, 1500, start=NOW - 3 * 86400)   # not interleaved: no "moves"
    out = plan(spots, eps)
    check("same yield, more PK sightings: smaller trips and lower net logs/hour",
          row(out, "pk")["logs_per_trip"] < row(out, "calm")["logs_per_trip"]
          and row(out, "pk")["net_logs_h"] < row(out, "calm")["net_logs_h"],
          (row(out, "pk"), row(out, "calm")))
    eps2 = eps + [trip("calm", NOW - 7200 - i * 4000, 300, 1200) for i in range(6)]
    seen = [e["t_start"] + 300 for e in eps2[-6:]]
    out2 = plan(spots, eps2, sightings=seen)
    check("hostile players seen during trips raise the spot's sighting rate",
          row(out2, "calm")["sightings_per_h"] > row(out, "calm")["sightings_per_h"],
          (row(out2, "calm")["sightings_per_h"], row(out, "calm")["sightings_per_h"]))


def test_eligibility():
    print("== when a spot can't be picked: cooldown after a death or PK, dry, status, Young ==")
    spots = [spot("a"), spot("b", 3000, 3000)]
    aborted = trip("a", NOW - 600, 100, 400, outcome="aborted", why="threat: red X")
    eps = series("a", 5, 1500) + series("b", 5, 1000) + [aborted]
    death = {"t": NOW - 590, "x": 1005, "y": 1002}
    out = plan(spots, eps, deaths=[death])
    check("a death 10 s after a trip there: counted for that spot, which is on cooldown",
          row(out, "a")["deaths"] == 1 and not row(out, "a")["eligible"] and "cooldown" in row(out, "a")["why_not"]
          and out["pick"]["spot"] == "b", row(out, "a"))
    far = plan(spots, eps, deaths=[{"t": NOW - 590, "x": 4000, "y": 100}])
    check("a death far from the spot isn't blamed on it", row(far, "a")["deaths"] == 0, row(far, "a"))
    old = series("a", 5, 1500, start=NOW - 30 * 86400) + series("b", 5, 1000)
    lone = plan(spots, old, deaths=[{"t": NOW - 86400 * 2, "x": 1010, "y": 995},
                                    {"t": NOW - 86400 * 2, "x": 1200, "y": 1200}])
    check("no trip row around a death (aborted runs before 2026-10-02): blamed on the spot whose area it's "
          "in, a death outside every area on none",
          row(lone, "a")["deaths"] == 1 and row(lone, "b")["deaths"] == 0, (row(lone, "a"), row(lone, "b")))
    later = plan(spots, eps, deaths=[death], now=NOW + lo.COOLDOWN_S)
    check("the cooldown ends", row(later, "a")["eligible"], row(later, "a")["why_not"])
    pk = plan(spots, eps, sightings=[aborted["t_start"] + 100])
    check("a trip cut short with a hostile player in sight also puts the spot on cooldown",
          not row(pk, "a")["eligible"], row(pk, "a"))
    dry = series("a", 5, 1500) + series("b", 5, 1000) + [trip("a", NOW - 600, 200, 900, dry=True)]
    check("ran dry 10 min ago: out until the trees regrow",
          not row(plan(spots, dry, regrow=regrow(45)), "a")["eligible"]
          and row(plan(spots, dry, regrow=regrow(45), now=NOW + 3000), "a")["eligible"])
    st = [spot("a"), spot("c", 3000, 3000, status="candidate"), spot("d", 5000, 5000, status="disabled"),
          spot("y", 7000, 7000, requires_young=True)]
    out = plan(st, series("a", 3, 1000))
    check("candidate and disabled spots aren't picked; a Young-only spot only for a Young character",
          not row(out, "c")["eligible"] and not row(out, "d")["eligible"] and not row(out, "y")["eligible"]
          and row(plan(st, [], young=True), "y")["eligible"])
    none = plan([spot("d", status="disabled")], [])
    check("nothing eligible: ok false and no pick", not none["ok"] and none["pick"] is None, none.get("error"))


def test_regrowth():
    print("== regrowth: when a depleted tree has wood again ==")
    rows, t = [], 0.0
    for i in range(40):
        gap = 20 + i * 1.5                                   # 20 .. 78.5 min
        rows += [(t, 0, i, 0, 0, "depleted"), (t + gap * 60, 0, i, 0, 0, "success" if gap >= 50 else "depleted")]
        t += 10000
    rows.sort()
    est = lo.regrowth(rows)
    check("trees regrown from 50 min on: estimate 50 min", est["fitted"] and est["minutes"] == 50.0, est)
    check("too few retries: the default", lo.regrowth(rows[:10])["minutes"] == lo.REGROW_DEFAULT_MIN)


def test_hatchets():
    print("== which hatchet: tool bonus vs wear and the loss on death ==")
    spots = [spot("a", hazard=0.5)]
    eps = series("a", 10, 1200, skill=70.0)
    me = char(70.0, iron(), copper())
    cheap = plan(spots, eps, char=me, prices={"hatchet:copper": {"price_gp": 300}})
    check("a cheap copper hatchet beats the worn iron one: the plan passes --hatchet copper",
          cheap["hatchets"]["use"] == "copper" and "--hatchet copper" in cheap["pick"]["command"],
          cheap["hatchets"]["options"])
    dear = plan(spots, eps, char=me, prices={"hatchet:copper": {"price_gp": 200000}})
    check("a very valuable one isn't worth risking or wearing out: keep the iron hatchet",
          dear["hatchets"]["use"] is None and "--hatchet" not in dear["pick"]["command"])
    unpriced = plan(spots, eps, char=me)
    cop = next(o for o in unpriced["hatchets"]["options"] if o["material"] == "copper")
    check("unknown price: not used, but a break-even price is given",
          unpriced["hatchets"]["use"] is None and cop["breakeven_gp"] and cop["breakeven_gp"] > 25, cop)
    at = plan(spots, eps, char=me, prices={"hatchet:copper": {"price_gp": cop["breakeven_gp"]}})
    vals = {o["material"]: o["net_logs_h"] for o in at["hatchets"]["options"]}
    check("at the break-even price copper and iron net the same", abs(vals["copper"] - vals["iron"]) < 1.0, vals)
    safe = plan([spot("a", pvp=False)], eps, char=me, prices={"hatchet:copper": {"price_gp": 1500}})
    risky = plan([spot("a", hazard=5.0)], eps, char=me, prices={"hatchet:copper": {"price_gp": 1500}})
    check("the same hatchet is worth more where nobody can kill us",
          next(o for o in safe["hatchets"]["options"] if o["material"] == "copper")["breakeven_gp"] is None
          and safe["hatchets"]["use"] == "copper" and risky["hatchets"]["use"] is None,
          (safe["hatchets"]["use"], risky["hatchets"]["use"]))
    check("success chance grows with the tool bonus and skill, capped at 1",
          lo.success_p(70, 0.06) > lo.success_p(70) and lo.success_p(100, 1.0) == 1.0
          and abs(lo.success_p(69.1) - 0.69) < 0.01)


def test_spots_store():
    print("== spots: seeds, the store's overrides and additions, the runner's knowledge ==")
    db = os.path.join(tempfile.mkdtemp(), "h.db")
    mem = Memory(db)
    mem.lumber_spot_put("corpse_creek", "disabled", {}, "overseer", "lawless greys stop every trip")
    mem.lumber_spot_put("auto_x", "candidate", spot("auto_x"), "discover")
    spots = lo.load_spots(mem)
    check("a store row disables a seed and keeps the seed's definition",
          spots["corpse_creek"]["status"] == "disabled" and spots["corpse_creek"]["banker"]["name"].startswith("Zakia")
          and spots["corpse_creek"]["reason"])
    check("a store-only spot appears with its status", spots["auto_x"]["status"] == "candidate")
    know = {"venue": "shelter_island", "harvest": {"trees": [{"x": 1}], "area": None}, "npcs": {"banker": {}},
            "captcha": {"gump_id": "0x1"}}
    k = lo.spot_knowledge(know, spots["terran_wilds"])
    check("the runner's knowledge: the spot's banker, area, no seed trees, pvp; the demo's venue gone",
          k["npcs"]["banker"]["pos"] == [726, 1508, 0] and k["harvest"]["area"]["radius"] == 30
          and k["harvest"]["trees"] == [] and k["pvp"] is True and "venue" not in k and k["captcha"])
    check("Shelter is no-PvP", lo.spot_knowledge(know, spots["shelter_island"])["pvp"] is False)
    check("the spot we stand at: in its area or by its bank",
          lo.current_spot(spots, (880, 1490), 0) == "terran_wilds"
          and lo.current_spot(spots, (730, 1510), 0) == "terran_wilds"
          and lo.current_spot(spots, (100, 100), 0) is None)
    mem.close()


def test_discover():
    print("== discover: tree-dense areas near banks, never in town or on a known spot ==")
    trees = [(x, y, 0, 0x0CE0) for x in range(1060, 1080, 2) for y in range(990, 1010, 2)]     # 100 east
    trees += [(x, y, 0, 0x0CE0) for x in range(940, 950, 3) for y in range(995, 1005, 3)]     # 16 west
    fn = lambda x0, y0, x1, y1: [t for t in trees if x0 <= t[0] <= x1 and y0 <= t[1] <= y1]  # noqa: E731
    banks = [("Town", (1000, 1000), 0)]
    found = lo.discover(fn, lambda x, y: 5, banks, set(), {}, min_trees=25, per_bank=3)
    check("the dense grove east of the bank is proposed; the sparse one isn't",
          len(found) >= 1 and all(c["area"]["center"][0] > 1040 for c in found)
          and found[0]["banker"]["pos"] == [1000, 1000, 5] and found[0]["tree_count"] >= 25, found)
    check("candidates don't overlap each other", all(
        lo.cheb(a["area"]["center"], b["area"]["center"]) > a["area"]["radius"] + b["area"]["radius"]
        for i, a in enumerate(found) for b in found[i + 1:]))
    guarded = lo.discover(fn, lambda x, y: 5, banks, {(1070, 1000)}, {}, min_trees=25)
    check("not next to a known guard point (town: no harvesting)",
          all(lo.cheb(c["area"]["center"], (1070, 1000)) > c["area"]["radius"] + 4 for c in guarded), guarded)
    known = {"s": spot("s", 1070, 1000)}
    check("not on a spot we already have", lo.discover(fn, lambda x, y: 5, banks, set(), known, min_trees=25) == [])


def test_failed_places():
    print("== a spot that yields nothing loses its optimistic prior and is set aside, then retried ==")
    spots = [spot("bad"), spot("new", 3000, 3000)]
    fails = [trip("bad", NOW - 3600 * k, 0, 20, outcome="aborted",
                  why="no harvestable tree available (all depleted, unreachable or ruled out)") for k in (2, 1)]
    out = plan(spots, fails)
    r = row(out, "bad")
    check("two such trips in a row: out, with the reason", not r["eligible"] and "unworkable" in r["why_not"]
          and "no harvestable tree" in r["why_not"], r["why_not"])
    check("its rate is now below an untried spot's (the failures count as field time with 0 logs)",
          r["rate_logs_h"] < row(out, "new")["rate_logs_h"], (r["rate_logs_h"], row(out, "new")["rate_logs_h"]))
    later = plan(spots, fails, now=NOW + 8 * 86400)
    check("a week later it gets one more try", row(later, "bad")["eligible"], row(later, "bad")["why_not"])
    mixed = fails[:1] + [trip("bad", NOW - 5400, 300, 900)] + fails[1:]
    check("a failure, a good trip, a failure: not set aside (not in a row)",
          row(plan(spots, mixed), "bad")["eligible"])
    other = [trip("bad", NOW - 3600 * k, 0, 20, outcome="aborted", why="threat: monster a mongbat") for k in (2, 1)]
    check("trips stopped by a monster aren't the place's fault", row(plan(spots, other), "bad")["eligible"])


def test_travel_and_hub():
    print("== travel: learned from moves between spots; nothing to travel from a rune library ==")
    w = spot("w", 4000, 1000, access={"method": "witcher", "rune": "286", "library": "cambria"},
             home={"method": "recall"}, travel_min=10)
    a, b = spot("a", travel_min=10), spot("b", 2000, 2000, travel_min=10)
    eps = [trip("a", NOW - 7200, 300, 900), trip("b", NOW - 7200 + 20 * 60 + 1035, 300, 900)]   # b lasts 1035 s
    out = plan([a, b, w], eps, here="a")
    check("one move a -> b took 20 min: b's travel is (prior 10 + 20) / 2",
          row(out, "b")["travel_min"] == 15 and row(out, "b")["travel_samples"] == 1, row(out, "b"))
    hub = plan([a, b, w], [], here="hub:cambria")
    check("at the library hub a Witcher spot is at hand (no travel), others aren't",
          row(hub, "w")["travel_min"] == 0 and row(hub, "a")["travel_min"] == 10)
    check("standing at the Cambria library is the hub", lo.current_spot({}, (1706, 3181), 0) == "hub:cambria")
    wb = {**w, "banker": {"pos": [1750, 3003, 0]}}
    check("at a Witcher spot's home bank: the hub, not that spot (its area is ~2,200 tiles away)",
          lo.current_spot({"w": wb, "w2": {**wb, "id": "w2"}}, (1752, 3001), 0) == "hub:cambria")
    check("inside a Witcher spot's area: that spot", lo.current_spot({"w": wb}, (4000, 1000), 0) == "w")
    check("a Witcher spot's overhead prior holds the 60 s lockout and two recalls",
          lo.overhead_prior_s({**w, "banker": {"pos": [1750, 3003, 0]}}) > lo.OVERHEAD_FIXED_S + 60 + 8)


def test_discover_witcher():
    print("== discover from Witcher runes: trees near the rune, a route in, no monster camps or towns ==")
    import places
    runes = [{"id": "1", "name": "Quiet Grove", "x": 1000, "y": 1000, "tome": "0x1"},
             {"id": "2", "name": "Brigand Camp 1", "x": 2000, "y": 1000, "tome": "0x1"},
             {"id": "3", "name": "Town Edge", "x": 3000, "y": 1000, "tome": "0x1"},
             {"id": "4", "name": "Cliff Top", "x": 4000, "y": 1000, "tome": "0x1"},
             {"id": "5", "name": "Sandbar", "x": 5000, "y": 1000, "tome": "0x1"}]
    trees = [(r["x"] + dx, r["y"] + 10 + dy, 0, 0x0CE0) for r in runes[:4] for dx in range(-8, 9, 2)
             for dy in range(-8, 9, 2)]                     # 81 trees just south of runes 1-4, none at 5
    fn = lambda x0, y0, x1, y1: [t for t in trees if x0 <= t[0] <= x1 and y0 <= t[1] <= y1]  # noqa: E731
    route = lambda start, c, r: None if start == (4000, 1000) else 12                     # noqa: E731
    home = {"serial": "0x0", "name": "home bank", "pos": [1, 2, 0]}
    found, skipped = lo.discover_witcher(fn, runes, {}, home, route_fn=route, towns=[(3000, 1030)])
    check("only the quiet grove", [s["id"] for s in found] == ["witcher_1"], [s["id"] for s in found])
    s = found[0] if found else {}
    check("reached by its library rune, home by our book's default rune, banked at the home bank",
          s.get("access") == {"method": "witcher", "rune": "1", "library": "cambria"}
          and s.get("home") == {"method": "recall"} and s.get("banker") == home
          and s.get("tree_count", 0) >= 60 and abs(s["area"]["center"][1] - 1010) <= 7, s)
    check("the others left out for the right reasons",
          skipped == {"monster name": 1, "in or by a town (no harvesting there)": 1,
                      "no short route from the rune": 1, "few trees": 1}, skipped)
    check("monster words in place names", places.danger_hint("Orc Fort 2") == ["orc"]
          and places.danger_hint("Western Ruins Brigands 3") == ["brigand"] and places.danger_hint("Cedar Forest") == [])
    check("the committed table: 360 runes, each in one of the 14 Cambria tomes",
          len(places.witcher()["runes"]) == 360 and len(places.library()["tomes"]) == 14
          and places.witcher_rune("286")["name"] == "Midlands Ruins 1 (South)")


if __name__ == "__main__":
    for fn in (test_explore_exploit, test_skill_rescaling, test_trip_size_and_hazard, test_eligibility,
               test_regrowth, test_hatchets, test_spots_store, test_discover, test_failed_places,
               test_travel_and_hub, test_discover_witcher):
        fn()
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILURES: {FAILURES}"))
    sys.exit(0 if not FAILURES else 1)

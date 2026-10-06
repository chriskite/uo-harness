"""Behaviour of the lumber optimizer (harness/lumber_opt.py, docs/LUMBER_LOOP.md §6):
which spot it picks (and the landing rune a trip recalls to from home), how much it
carries, which hatchet, and what it learns from trips, deaths and depleted trees.
Synthetic trip rows; no network, no game.

Run: python harness/test_lumber_opt.py
"""
import math
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
# Outland Dan's home (harness/data/homes.json, live 2026-10-04; the Resource Stockpile since 2026-10-05)
HOME = {"library": "dtf", "landing": [4134, 1429, 6], "facet": 0,
        "room": {"owner": "logan", "facet": 3, "arrival": [403, 923, 1], "exit": "steward"},
        "chest": {"serial": "0x4AE0DD2C", "name": "paragon chest (drake)", "pos": [404, 922, 2]},
        "stockpile": {"serial": "0x62645C82", "pos": [403, 921, 2]}}


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}{'' if cond else ' ' + str(detail)}")
    if not cond:
        FAILURES.append(name)


def spot(sid, x=1000, y=1000, pvp=True, hazard=0.5, **kw):
    return {"id": sid, "name": sid, "facet": 0, "area": {"center": [x, y], "radius": 20}, "pvp": pvp,
            "hazard_prior": hazard, "status": "active", **kw}


def landing(s, dist=20, **kw):
    """An own book's rune `dist` tiles east of the spot's centre (landing_for's row shape)."""
    (cx, cy) = s["area"]["center"]
    return {"source": "book", "library": None, "tome": None, "tome_title": None, "tome_pos": None,
            "book": "0x49865F8F", "kind": "runebook", "name": f"{s['id']} rune", "x": cx + dist, "y": cy,
            "dist": dist, "danger": [], "route_tiles": None, "route_checked": False, **kw}


def trip(sid, t1, logs, field_s, chop_share=0.7, skill=None, outcome="stored", dry=False, why=None,
         walk_out=60.0, to_room=20.0, convert=10.0, store=5.0, to_bank=60.0):
    """A trip row as the runner writes it: home -> grove -> home ('stored': into the room's chest);
    outcome 'banked' writes a bank-era row (to_bank instead of to_room)."""
    phases = {"harvest": walk_out + field_s}
    if outcome == "stored":
        phases.update(to_room=to_room, convert=convert, store=store)
    elif outcome == "banked":
        phases.update(convert=convert, to_bank=to_bank, store=store)
    t0 = t1 - sum(phases.values())
    return {"loop": "lumber", "spot": sid, "t_start": t0, "t_end": t1, "outcome": outcome, "why": why, "dry": dry,
            "logs": logs, "walk_out_s": walk_out, "chop_s": field_s * chop_share, "skill": skill,
            "hatchet": {"tool_bonus": 0.0}, "phases_s": phases}


def series(sid, n, rate, field_s=1800.0, skill=None, start=NOW - 86400, gap=3600):
    """n stored trips of field_s seconds at `rate` logs per field hour."""
    return [trip(sid, start + i * gap, round(rate * field_s / 3600.0), field_s, skill=skill) for i in range(n)]


def regrow(minutes=45.0):
    return {"minutes": minutes, "pairs": 0, "fitted": False}


def plan(spots, episodes, char=None, deaths=(), sightings=(), seed=1, **kw):
    """lo.plan at HOME with one landing 20 tiles off per spot (kw landings/home override)."""
    kw.setdefault("home", HOME)
    kw.setdefault("landings", {s["id"]: landing(s) for s in spots})
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


def test_greedy_command():
    print("== an explore pick also gives the greedy spot's run, with that spot's own trip arguments; --exploit ==")
    spots = [spot("good", 1000, 1000), spot("poor", 2000, 2000), spot("new", 3000, 3000)]
    eps = series("good", 12, 1800) + series("poor", 12, 600)
    outs = [plan(spots, eps, seed=s) for s in range(60)]
    out = next((o for o in outs if o["pick"]["mode"] == "explore"), None)
    check("some seed explores", out is not None)
    if out is None:
        return
    p, g = out["pick"], out["greedy"]

    def arg(view, name):
        return view["args"][view["args"].index(name) + 1]
    check("the explore pick names the greedy spot, and `greedy` is that spot's run",
          p["spot"] != g["spot"] == p["greedy"] == "good" and g["command"] == "ctl run lumber " + " ".join(g["args"])
          and arg(g, "--spot") == "good" and g["command"].startswith("ctl run lumber --spot good "), g)
    gr, pr = row(out, "good"), row(out, p["spot"])
    check("each run carries its own spot's quota, net rate, timeout and P(best), not the other's",
          int(arg(g, "--logs-per-trip")) == g["logs_per_trip"] == gr["logs_per_trip"]
          and int(arg(p, "--logs-per-trip")) == p["logs_per_trip"] == pr["logs_per_trip"]
          and g["logs_per_trip"] != p["logs_per_trip"] and g["expected_net_logs_h"] == gr["net_logs_h"]
          and int(arg(g, "--timeout")) == g["timeout_s"] and g["timeout_s"] != p["timeout_s"]
          and g["p_best"] == gr["p_best"] and g["landing"] == gr["landing"], (p, g))
    check("the timeout covers the greedy run's own trips (twice their full length)",
          g["timeout_s"] >= 2 * g["trips"] * g["expected_trip_min"] * 60, g)
    ex = plan(spots, eps, seed=outs.index(out), exploit=True)
    check("--exploit: the pick is the greedy spot with the same run, chosen_by greedy",
          ex["pick"]["spot"] == "good" and ex["pick"]["mode"] == "exploit"
          and ex["pick"]["chosen_by"] == "greedy (--exploit)" and ex["pick"]["command"] == g["command"]
          and ex["greedy"]["command"] == g["command"] and p["chosen_by"] == "thompson", ex["pick"])
    same = next(o for o in outs if o["pick"]["mode"] == "exploit")
    check("an exploit pick's `greedy` is the pick's own run", same["greedy"]["command"] == same["pick"]["command"])


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


def hz(death=0.0, home=0.0, theft=0.0, share=0.5):
    return (death, home, theft, share)


def test_trip_size():
    print("== how much to carry: renewal-reward over death, sent-home and theft hazards, 200..10,000 logs ==")
    lam, t_h = 1500.0, 150 / 3600.0
    safe, risky, deadly = (lo.best_q(lam, t_h, hz(h), 3.0) for h in (0.0, 0.05, 0.5))
    check("trip size falls as the death rate rises", safe > risky > deadly, (safe, risky, deadly))
    check("bounds: no hazard -> 10,000 logs (no stint cap), an extreme one -> 200",
          safe == lo.Q_MAX == 10000 and lo.best_q(lam, t_h, hz(50.0), 3.0) == lo.Q_MIN == 200,
          (safe, lo.best_q(lam, t_h, hz(50.0), 3.0)))
    q, h = 1500, 0.4
    dead, home = lo.trip_terms(q, lam, t_h, hz(death=h)), lo.trip_terms(q, lam, t_h, hz(home=h))
    surv = math.exp(-h * q / lam)
    check("a dying trip stores nothing: only the trips that live store, all q of them",
          abs(dead["stored"] - q * surv) < 1e-6 and abs(dead["p_death"] - (1 - surv)) < 1e-9, dead)
    check("a trip sent home at the same rate stores everything it chopped, and loses nothing",
          abs(home["stored"] - lam * (1 - surv) / h) < 1e-6 and home["stored"] > dead["stored"]
          and home["lost_death"] == 0 and dead["lost_death"] > 0 and home["p_death"] == 0, (home, dead))
    check("being sent home alone never shrinks the trip (nothing is lost)",
          lo.best_q(lam, t_h, hz(home=3.0), 3.0) == lo.Q_MAX)
    long = lo.trip_terms(10000, 500.0, t_h, hz(death=1.0))           # 20 field hours at 1 death/h
    rate = lo.net_rate(10000, 500.0, t_h, hz(death=1.0), 3.0)
    check("long trips stay well-defined: P(death) <= 1, nothing stored below 0, the rate no worse than losing "
          "the gear every cycle",
          abs(long["p_death"] - (1 - math.exp(-20))) < 1e-9 and 0 <= long["stored"] < 1
          and -3.0 / long["time_h"] <= rate < 0, (long, rate))
    calm, robbed = lo.best_q(lam, t_h, hz(0.05), 3.0), lo.best_q(lam, t_h, hz(0.05, theft=2.0), 3.0)
    check("thieves lower Q* (a grab takes a share of the load)", robbed < calm, (calm, robbed))
    lost = lo.trip_terms(2000, lam, t_h, hz(theft=1.0, share=0.5))
    check("theft: the trip runs its full length, the share taken is gone",
          abs(lost["time_h"] - (t_h + 2000 / lam)) < 1e-9 and lost["lost_theft"] > 0
          and abs(lost["stored"] + lost["lost_theft"] - 2000) < 1e-6, lost)
    check("gear at risk lowers Q*", lo.best_q(lam, t_h, hz(0.05), 300.0) < calm)

    spots = [spot("calm", hazard=0.05), spot("pk", 2000, 2000, hazard=3.0)]
    eps = series("calm", 8, 1500) + series("pk", 8, 1500, start=NOW - 3 * 86400)   # not interleaved: no "moves"
    out = plan(spots, eps)
    check("same yield, more PK sightings: smaller trips and lower net logs/hour",
          row(out, "pk")["logs_per_trip"] < row(out, "calm")["logs_per_trip"]
          and row(out, "pk")["net_logs_h"] < row(out, "calm")["net_logs_h"]
          and row(out, "pk")["deaths_per_h"] > row(out, "calm")["deaths_per_h"],
          (row(out, "pk"), row(out, "calm")))
    p = out["pick"]
    check("the runner's timeout covers the chosen trips (twice their full length)",
          p["timeout_s"] >= 2 * p["trips"] * p["expected_trip_min"] * 60, p)
    eps2 = eps + [trip("calm", NOW - 7200 - i * 4000, 300, 1200) for i in range(6)]
    seen = [e["t_start"] + 300 for e in eps2[-6:]]
    out2 = plan(spots, eps2, sightings=seen)
    check("hostile players seen during trips raise the spot's sighting and death rates",
          row(out2, "calm")["sightings_per_h"] > row(out, "calm")["sightings_per_h"]
          and row(out2, "calm")["deaths_per_h"] > row(out, "calm")["deaths_per_h"],
          (row(out2, "calm"), row(out, "calm")))


def test_capacity():
    print("== capacity: a grove holds its yielding trees x logs per tree; a sparse one ends trips early ==")
    att, t = [], NOW - 5 * 86400
    for x in range(24):                                   # 24 trees give 8 + 8 + 4 logs, then run out
        for amt in (8, 8, 4):
            att.append((t, 0, x, 0, 0, "success", amt))
            t += 10
        att.append((t, 0, x, 0, 0, "depleted", 0))
        t += 10
    att += [(t, 0, 24, 0, 0, "depleted", 0), (t + 10, 0, 25, 0, 0, "not_tree", 0),
            (NOW - 600, 0, 0, 0, 0, "depleted", 0)]       # 24: chopped out by someone else; 0: out again now
    ty = lo.tree_yield(att, {"a": (0, {(x, 0) for x in range(28)})}, NOW, 45.0)
    check("harvest memory: logs per tree from completed cycles; trees less non-trees; tried, yielded, out now",
          ty["logs_per_tree"] == 20.0 and ty["cycles"] == 24
          and ty["spots"]["a"] == {"trees": 27, "tried": 25, "yielded": 24, "out": 1}, ty)
    check("too few cycles: the default logs per tree",
          lo.tree_yield(att[:8], {}, NOW, 45.0)["logs_per_tree"] == lo.LOGS_PER_TREE)
    marks = att + [(NOW - 300, 0, 26, 0, 0, "nothing_near", 0), (NOW - 300, 0, 1, 0, 0, "nothing_near", 0),
                   (NOW - 200, 0, 26, 1, 0, "success", 7)]   # Smart Harvest: a stand tile's attempt, not a tree
    tm = lo.tree_yield(marks, {"a": (0, {(x, 0) for x in range(28)})}, NOW, 45.0)
    check("Smart Harvest 'nothing nearby' marks: the marked trees are out now, a mark alone isn't tried; "
          "a stand tile's attempts touch no tree tile",
          tm["spots"]["a"] == {"trees": 27, "tried": 25, "yielded": 24, "out": 3} and tm["cycles"] == 24, tm)
    rg = lo.regrowth([r[:6] for r in sorted(marks)])
    check("marks make no regrowth pairs", rg["pairs"] == lo.regrowth([r[:6] for r in sorted(att)])["pairs"], rg)

    def trees(**spots):
        return {"logs_per_tree": 20.0, "spots": {sid: {"trees": n, "tried": tried, "yielded": yielded, "out": out}
                                                 for sid, (n, tried, yielded, out) in spots.items()}}
    sp = [spot("s"), spot("d", 3000, 3000)]
    eps = series("s", 6, 1500) + series("d", 6, 1500, start=NOW - 3 * 86400)
    out = plan(sp, eps, trees=trees(s=(30, 30, 24, 0), d=(300, 300, 240, 0)))
    s, d = row(out, "s"), row(out, "d")
    check("same chopping rate: the sparse grove's trips stop at what its trees hold, so it nets less per hour",
          s["grove_bound"] and abs(s["logs_per_trip"] - s["grove_logs"]) <= 1 and s["logs_per_trip"] < d["logs_per_trip"]
          and s["net_logs_h"] < d["net_logs_h"] and 400 < s["grove_logs"] < 560, (s, d))
    gone = row(plan(sp, eps, trees=trees(s=(30, 30, 24, 20), d=(300, 300, 240, 0))), "s")
    check("trees still regrowing don't count", gone["trees_out"] == 20 and gone["grove_logs"] < s["grove_logs"] / 2,
          gone)
    only = plan(sp[:1], eps[:6], trees=trees(s=(30, 30, 24, 0)))["pick"]
    quota = int(only["args"][only["args"].index("--logs-per-trip") + 1])
    check("a grove-bound pick is one trip, told the uncapped quota (it chops until the trees run out)",
          only["grove_bound"] and only["trips"] == 1 and quota > only["grove_logs"], only)
    fresh = plan([spot("s"), spot("d", 3000, 3000)], [], trees=trees(s=(40, 0, 0, 0), d=(300, 0, 0, 0)))
    check("untried spots: the denser grove wins the Thompson draws more often",
          row(fresh, "d")["p_best"] > row(fresh, "s")["p_best"], (row(fresh, "d"), row(fresh, "s")))


def test_hazard_evidence():
    print("== hazards learned per spot: deaths, trips sent home, thefts; shrunk to the pooled rate ==")
    spots = [spot("a"), spot("b", 3000, 3000)]
    base = series("a", 6, 1500, start=NOW - 5 * 86400) + series("b", 6, 1500, start=NOW - 3 * 86400)
    out = plan(spots, base)
    fled = [trip("a", NOW - 86400 - i * 4000, 200, 600, outcome="aborted",
                 why="threat: red X at 17 tiles (ETA 0.5 s); escaped by recall to (1, 2) in 2.2 s") for i in range(4)]
    sent = plan(spots, base + fled)
    check("trips a threat ended raise that spot's sent-home rate, more than the pooled rate moves the other's",
          row(sent, "a")["sent_home"] == 4
          and row(sent, "a")["sent_home_per_h"] > row(sent, "b")["sent_home_per_h"] > row(out, "b")["sent_home_per_h"],
          (row(sent, "a")["sent_home_per_h"], row(sent, "b")["sent_home_per_h"], row(out, "b")["sent_home_per_h"]))
    stopped = [trip("a", NOW - 86400 - i * 4000, 200, 600, outcome="aborted", why="threat: monster a mongbat")
               for i in range(2)]
    esc = [{"t": tr["t_end"] - 1, "kind": "recall", "data": {}} for tr in stopped]
    check("a recall event inside an aborted trip counts it as sent home",
          row(plan(spots, base + [{**tr, "why": "stopped"} for tr in stopped], events=esc), "a")["sent_home"] == 2)
    killed = trip("a", NOW - 86400, 200, 600, outcome="aborted", why="threat: red X; escaped by recall")
    died = plan(spots, base + [killed], deaths=[{"t": killed["t_end"] + 11, "x": 1000, "y": 1000}])
    check("a death right after the trip: a death there, not a trip sent home; its rate rises more than the other "
          "spot's (which moves only through the pooled creature rate)",
          row(died, "a")["deaths"] == 1 and row(died, "a")["sent_home"] == 0
          and row(died, "a")["deaths_per_h"] - row(out, "a")["deaths_per_h"]
          > 2 * (row(died, "b")["deaths_per_h"] - row(out, "b")["deaths_per_h"]) > 0,
          (row(died, "a"), row(out, "a")))
    check("a death with no player seen before it is a creature death: the pooled creature rate rises, "
          "P(death | sighting) doesn't",
          died["creature_deaths_per_h"] > out["creature_deaths_per_h"]
          and died["death_given_sighting"] == out["death_given_sighting"],
          (died["creature_deaths_per_h"], died["death_given_sighting"]))
    pk = plan(spots, base + [killed], deaths=[{"t": killed["t_end"] + 11, "x": 1000, "y": 1000}],
              sightings=[killed["t_end"] - 3])
    check("a sighting just before: a PK death (P(death | sighting) rises)",
          pk["death_given_sighting"] > out["death_given_sighting"])
    tr = base[2]
    grab = {"t": tr["t_end"] - 100, "kind": "theft",
            "data": {"amount": 300, "items": [{"graphic": 0x1BDD, "amount": 300, "class": "log", "wood": "ordinary"}],
                     "carried": {"logs": 300, "boards": 100}}}    # the runner's shape (loop_lumber check_ledger)
    regs = {"t": base[3]["t_end"] - 100, "kind": "theft",
            "data": {"amount": 10, "items": [{"graphic": 0x0F86, "amount": 10}]}}
    robbed = plan(spots, base, events=[grab, regs])
    check("thefts at a spot raise its theft rate and lower its Q*; the share taken is learned (wood 300/400, "
          "reagents 0)",
          row(robbed, "a")["thefts"] == 2 and row(robbed, "a")["thefts_per_h"] > row(out, "a")["thefts_per_h"]
          and row(robbed, "a")["logs_per_trip"] < row(out, "a")["logs_per_trip"]
          and robbed["theft_fraction"] == round((1 + 0.75 + 0) / 4, 2),
          (row(robbed, "a"), robbed["theft_fraction"]))
    junct = {"t": tr["t_end"] - 100, "kind": "theft_suspected",
             "data": {"unexplained_losses": [{"amount": 300, "class": "log", "wood": "ordinary"}]}}
    check("a theft_suspected juncture next to its theft event counts once; alone it counts",
          row(plan(spots, base, events=[grab, junct]), "a")["thefts"] == 1
          and row(plan(spots, base, events=[junct]), "a")["thefts"] == 1)
    zone = {"label": "FACTION WP 17", "x": 1784, "y": 2150, "t": NOW - 3600}
    marked = plan([spot("a", faction_zone=zone), spot("b", 3000, 3000)], base)
    check("a spot where the runner saw a faction waypost: its expected sightings and deaths rise, the plan names "
          "the waypost (two equal spots otherwise)",
          row(marked, "a")["sightings_per_h"] > row(marked, "b")["sightings_per_h"] == row(out, "b")["sightings_per_h"]
          and row(marked, "a")["deaths_per_h"] > row(out, "a")["deaths_per_h"]
          and row(marked, "a")["faction_zone"] == "FACTION WP 17" and row(marked, "b")["faction_zone"] is None,
          (row(marked, "a")["sightings_per_h"], row(marked, "b")["sightings_per_h"], row(marked, "a")["deaths_per_h"]))


def test_gear_and_capacity():
    print("== a death loses every unblessed item at full price (nothing when Young); Q fits what we can carry ==")
    me = {**char(70.0, iron(), copper(), {**copper(), "newbied": True}), "reagents": {"black pearl": 20, "nightshade": 5}}
    prices = {"hatchet:copper": {"price_gp": 300}, "reagent:black_pearl": {"price_gp": 3.0}}
    g = lo.gear_at_risk(me, TABLE, prices, young=False)
    check("all carried hatchets at full price (iron 25 + copper 300; the newbied one stays), priced reagents, "
          "unpriced ones listed",
          g["gp"] == 25 + 300 + 60 and g["unpriced"] == ["reagent:nightshade"], g)
    check("Young: nothing is lost", lo.gear_at_risk(me, TABLE, prices, young=True)["gp"] == 0)
    spots = [spot("a", hazard=1.0)]
    eps = series("a", 10, 1500, skill=70.0)
    adult = plan(spots, eps, char=me, prices=prices)
    young = plan(spots, eps, char={**me, "young": True}, prices=prices)
    check("the plan carries it: more lost per trip and fewer net logs/hour; Young (the name label) -> nothing at risk",
          adult["gear_at_risk"]["gp"] == 385 and young["gear_at_risk"]["gp"] == 0 and young["young"]
          and row(adult, "a")["loss_logs_trip"] > row(young, "a")["loss_logs_trip"]
          and row(adult, "a")["net_logs_h"] < row(young, "a")["net_logs_h"],
          (row(adult, "a"), row(young, "a")))
    last = eps[:-1] + [{**eps[-1], "hatchet": {**copper(), "tool_bonus": 0.0}}]
    seen = plan(spots, last, prices=prices)["gear_at_risk"]
    check("no live character: the newest trip row's hatchet is what's at risk", seen["gp"] == 300
          and seen["source"] == "last trip row", seen)
    calm = [spot("c", pvp=False)]
    free = plan(calm, series("c", 10, 1500), char={**char(70.0, iron()), "young": True})
    heavy = plan(calm, series("c", 10, 1500), char={**char(70.0, iron()), "young": True,
                                                    "weight": 300, "weight_max": 330})
    check("what we can still carry caps the trip: (330 - 300) / 0.025 = 1,200 logs",
          heavy["capacity_logs"] == 1200 and row(heavy, "c")["logs_per_trip"] == 1200
          and row(free, "c")["logs_per_trip"] > 1200 and heavy["pick"]["logs_per_trip"] == 1200,
          (row(free, "c")["logs_per_trip"], row(heavy, "c")["logs_per_trip"]))
    world = {"self": {"weight": 40, "stats": {"weight_max": 400}}, "labels": {"0x00000001": "Hackworth (young)"},
             "items": {"0x40000001": {"graphic": 0x0E75, "layer": 0x15, "container": "0x00000001"},
                       "0x40000002": {"graphic": 0x0F7A, "amount": 12, "container": "0x40000001"},
                       "0x40000003": {"graphic": 0x0F43, "hue": 0, "container": "0x40000001"}}}
    c = lo.character(world, 1, TABLE)
    check("the character snapshot reads weight, weight_max, the packed hatchet and reagents, the Young label",
          (c["weight"], c["weight_max"], c["reagents"], c["young"], [h["material"] for h in c["hatchets"]])
          == (40, 400, {"black pearl": 12}, True, ["iron"]), c)


def test_eligibility():
    print("== when a spot can't be picked: cooldown after a death or PK, dry, status, no landing ==")
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
          spot("n", 7000, 7000)]
    out = plan(st, series("a", 3, 1000), landings={"a": landing(st[0])})
    check("candidate and disabled spots aren't picked; nor an active one no landing rune reaches",
          not row(out, "c")["eligible"] and not row(out, "d")["eligible"] and not row(out, "n")["eligible"]
          and "no landing rune" in row(out, "n")["why_not"] and out["pick"]["spot"] == "a"
          and out["pick"]["landing"]["name"] == "a rune", (row(out, "n"), out["pick"]))
    thief = {"t": NOW - 300, "kind": "thief", "data": {"spot": "a", "trigger": "pouch_pop", "action": "recall"}}
    step = {"t": NOW - 300, "kind": "thief", "data": {"spot": "a", "trigger": "near", "action": "keep_away"}}
    base = series("a", 5, 1500) + series("b", 5, 1000)
    robbed = plan(spots, base, events=[thief])
    check("a thief made us leave 5 min ago (our pouch went off): the spot is out for THIEF_COOLDOWN_S",
          not row(robbed, "a")["eligible"] and "thief" in row(robbed, "a")["why_not"]
          and row(plan(spots, base, events=[thief], now=NOW - 300 + lo.THIEF_COOLDOWN_S), "a")["eligible"],
          row(robbed, "a")["why_not"])
    check("a keep-away step alone (we chopped on) is no cooldown",
          row(plan(spots, base, events=[step]), "a")["eligible"], row(plan(spots, base, events=[step]), "a"))
    have = plan(spots, base, char={**char(60.0, iron()), "pouches": {"live": 1, "spent": 2}})
    check("the plan says how many trapped pouches to carry and buy (one a trip)",
          have["pouches"]["carry"] == 3 and have["pouches"]["buy"] == 2 and have["pouches"]["live"] == 1
          and plan(spots, base)["pouches"]["buy"] is None, have["pouches"])
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
    print("== which hatchet: tool bonus vs wear; every carried one is lost on death, a bought one adds its price ==")
    spots = [spot("a", hazard=0.5)]
    eps = series("a", 10, 1200, skill=70.0)
    me = char(70.0, iron(), copper())
    cheap = plan(spots, eps, char=me, prices={"hatchet:copper": {"price_gp": 300}})
    check("a cheap copper hatchet beats the worn iron one: the plan passes --hatchet copper",
          cheap["hatchets"]["use"] == "copper" and "--hatchet copper" in cheap["pick"]["command"],
          cheap["hatchets"]["options"])
    dear = plan(spots, eps, char=me, prices={"hatchet:copper": {"price_gp": 200000}})
    check("a very valuable one isn't worth wearing out: keep the iron hatchet",
          dear["hatchets"]["use"] is None and "--hatchet" not in dear["pick"]["command"])
    unpriced = plan(spots, eps, char=me)
    cop = next(o for o in unpriced["hatchets"]["options"] if o["material"] == "copper")
    check("unknown price: not used, but a break-even price is given",
          unpriced["hatchets"]["use"] is None and cop["breakeven_gp"] and cop["breakeven_gp"] > 25, cop)
    at = plan(spots, eps, char=me, prices={"hatchet:copper": {"price_gp": cop["breakeven_gp"]}})
    vals = {o["material"]: o["net_logs_h"] for o in at["hatchets"]["options"]}
    check("at the break-even price copper and iron net the same", abs(vals["copper"] - vals["iron"]) < 1.0, vals)
    check("an owned copper hatchet is at risk whichever is used: the plan's gear at risk holds iron + copper",
          cheap["gear_at_risk"]["gp"] == 25 + 300, cheap["gear_at_risk"])
    only_iron = char(70.0, iron())
    safe = plan([spot("a", pvp=False)], eps, char=only_iron, prices={"hatchet:copper": {"price_gp": 1500}})
    risky = plan([spot("a", hazard=5.0)], eps, char=only_iron, prices={"hatchet:copper": {"price_gp": 1500}})
    check("buying one is worth more where nobody can kill us (its full price is at risk where they can)",
          (safe["hatchets"]["buy"] or {}).get("material") == "copper" and risky["hatchets"]["buy"] is None,
          (safe["hatchets"]["buy"], risky["hatchets"]["buy"]))
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
          spots["corpse_creek"]["status"] == "disabled" and spots["corpse_creek"]["area"]["radius"] == 45
          and spots["corpse_creek"]["reason"])
    check("a store-only spot appears with its status", spots["auto_x"]["status"] == "candidate")
    check("no seed carries a bank, travel or Young field; Shelter Island is no spot (no harvesting for non-Young)",
          "shelter_island" not in spots and not any(k in s for s in lo.load_seeds().values()
                                                    for k in ("banker", "travel", "travel_min", "requires_young",
                                                              "access", "home")))
    know = {"venue": "shelter_island", "harvest": {"trees": [{"x": 1}], "area": None},
            "npcs": {"banker": {"pos": [1, 2, 3]}, "innkeeper": {}}, "captcha": {"gump_id": "0x1"}}
    k = lo.spot_knowledge(know, spots["terran_wilds"])
    check("the runner's knowledge: the spot's area, no seed trees, pvp; no banker, the demo's venue gone",
          "banker" not in k["npcs"] and "innkeeper" in k["npcs"] and k["harvest"]["area"]["radius"] == 30
          and k["harvest"]["trees"] == [] and k["pvp"] is True and "venue" not in k and k["captcha"]
          and k["spot"] == {"id": "terran_wilds", "name": spots["terran_wilds"]["name"], "facet": 0, "pvp": True})
    check("a spot needs only an area", lo.check_spot(spot("z")) is None)
    try:
        lo.check_spot({"id": "q", "area": {"center": [1]}})
        refused = False
    except ValueError as e:
        refused = "area needs center" in str(e)
    check("... and is refused without one", refused)
    mem.close()


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


def test_landings():
    print("== the way out: the landing rune nearest the grove (home library, own books), a route in, no bad places ==")
    import home as homes
    import places
    s = spot("g", 3000, 3000)
    book = {"serial": "0x49865F8F", "kind": "runebook", "title": "Dan's book", "default": 0,
            "runes": [{"i": 0, "name": "DTF Loot Chest", "x": 4134, "y": 1429, "facet": 0},
                      {"i": 1, "name": "Orc Fort 9", "x": 3000, "y": 3000, "facet": 0},
                      {"i": 2, "name": "Grove A", "x": 3001, "y": 3000, "facet": 0},
                      {"i": 3, "name": "Grove B", "x": 3003, "y": 3000, "facet": 0}]}
    bad = {"serial": "0x1", "kind": "runetome", "title": "Bad Places", "runes": [{"i": 0, "name": "Glade", "x": 3000,
                                                                               "y": 3002}]}
    books = [book, bad]
    got = lo.landing_for(s, HOME, books)
    check("the nearest landing wins; a monster-named rune and a 'Bad Places' book are skipped",
          got is not None and got["name"] == "Grove A" and got["source"] == "book" and got["dist"] == 1, got)
    asked = []

    def no_a(row, sp):
        asked.append(row["name"])
        return row["name"] != "Grove A"
    nxt = lo.landing_for(s, HOME, books, no_a)
    check("no walking route from it: the next nearest", nxt["name"] == "Grove B" and asked == ["Grove A", "Grove B"],
          (nxt, asked))
    lib = lo.landing_for(s, HOME, books, lambda row, sp: row["source"] == "library")
    check("no own rune with a route: the home library's nearest (DTF)", lib["source"] == "library"
          and lib["library"] == "dtf" and lib["dist"] > 3, lib)
    check("nothing reachable: None", lo.landing_for(s, HOME, books, lambda row, sp: False) is None)
    bad = {lo.landing_key(got, 0)}
    check("a landing remembered as bad (its recall put us elsewhere): the next nearest",
          lo.landing_for(s, HOME, books, bad=bad)["name"] == "Grove B", bad)
    check("the home in harness/data/homes.json is this one; it recalls out from its own library only",
          homes.for_character("outland dan") == HOME and homes.libraries(HOME) == ["dtf"])

    # overhead prior: every trip from home
    lib_row = {**lib, "x": 3001, "y": 3000, "dist": 1, "route_tiles": None}
    by_book, by_lib = lo.overhead_prior_s(s, got, HOME), lo.overhead_prior_s(s, lib_row, HOME)
    stand = places.library("dtf")["stand"]
    check("a library rune costs the walk from the home landing to the library's stand (DTF: 18 tiles); "
          "an own book's is recalled on the spot",
          lo.library_walk(lib_row, HOME) == lo.cheb(HOME["landing"], stand) == 18
          and lo.library_walk(got, HOME) == 0 and abs(by_lib - by_book - 18 * lo.SEC_PER_TILE) < 1e-9,
          (by_book, by_lib))
    fixed = (lo.ROOM_EXIT_S + 2 * lo.RECALL_TRIP_S + lo.LOCKOUT_S + lo.ROOM_ENTER_S + lo.OVERHEAD_FIXED_S)
    check("the prior: room exit, two recalls, the lockout, into the room, convert and store, plus the walk into "
          "the grove (to the area's inner half, or the planned route when known)",
          abs(by_book - fixed) < 1e-9
          and abs(lo.overhead_prior_s(s, {**got, "route_tiles": 40}, HOME) - fixed - 40 * lo.SEC_PER_TILE) < 1e-9
          and abs(lo.overhead_prior_s(s, {**got, "x": 3100}, HOME) - fixed - 90 * lo.SEC_PER_TILE) < 1e-9,
          by_book)

    # the route check: cached answers, planning budget per spot, too far without planning
    planned = []

    def route_fn(start, center, radius):
        planned.append(start)
        return None if start == (3001, 3000) else 7
    routes = {lo.route_key({"x": 3003, "y": 3000}, s): 9}
    new = {}
    ok = lo.make_route_ok(route_fn, routes, new, tries=1)
    first = lo.landing_for(s, HOME, books, ok)
    check("unknown route planned (no route from Grove A), a cached one answers without planning (Grove B)",
          first["name"] == "Grove B" and planned == [(3001, 3000)]
          and new == {lo.route_key({"x": 3001, "y": 3000}, s): None}, (first, planned, new))
    far = spot("f", 3000, 3400)
    check("past the spot's planning budget, unknown landings fail unplanned; a landing beyond MAX_RUNE_ROUTE of "
          "the area's edge fails without planning",
          not ok({"x": 3010, "y": 3000}, s) and not ok({"x": 3000, "y": 3000 - lo.MAX_RUNE_ROUTE - 21}, far)
          and planned == [(3001, 3000)])
    check("no planner (the dashboard): cached answers hold, an unknown landing is taken unchecked",
          lo.make_route_ok(None, {lo.route_key({"x": 3001, "y": 3000}, s): None}, {})({"x": 3001, "y": 3000}, s)
          is False and lo.make_route_ok(None, {}, {})({"x": 3001, "y": 3000}, s) is True)
    db = os.path.join(tempfile.mkdtemp(), "h.db")
    mem = Memory(db)
    lo.save_landing_routes(mem, new)
    lo.save_landing_routes(mem, {"0:1,1>2,2,3": 5})
    check("routes are cached in the memory store, merged", lo.landing_routes(mem) == {**new, "0:1,1>2,2,3": 5},
          lo.landing_routes(mem))
    lo.mark_bad_landing(mem, {"name": "Jonny's House", "x": 1817, "y": 1865}, 0, (1809, 1871))
    check("a bad landing is kept in the memory store by facet and tile, with where we landed",
          lo.bad_landings(mem).get("0:1817,1865", {}).get("landed") == [1809, 1871], lo.bad_landings(mem))
    mem.close()


def test_home_and_trips():
    print("== trips start and end at home: stored trips teach the overhead; no home, no plan ==")
    tr = trip("a", NOW - 3600, 300, 900, walk_out=100.0, to_room=25.0, convert=12.0, store=6.0)
    tr["lockout_s"] = 30.0
    o = lo.trip_obs(tr)
    check("a stored trip's overhead: walk out + lockout + into the room + convert + store",
          o["outcome"] == "stored" and o["overhead_s"] == 100 + 30 + 25 + 12 + 6 and o["field_s"] == 900 - 30, o)
    old = lo.trip_obs(trip("a", NOW - 7200, 300, 900, outcome="banked", walk_out=80.0, to_bank=50.0))
    check("a bank-era row still counts, with its to_bank form", old["overhead_s"] == 80 + 10 + 50 + 5, old)
    out = plan([spot("a")], [trip("a", NOW - 7200, 300, 900, outcome="banked"), trip("a", NOW - 3600, 300, 900)])
    check("the planner learns from both", row(out, "a")["trips"] == 2 and out["ok"], row(out, "a"))
    check("the pick names its landing and the logs it expects in the chest; no travel",
          out["pick"]["landing"]["name"] == "a rune" and out["pick"]["expected_stored_trip"] > 0
          and "travel" not in out["pick"] and "expected_banked_trip" not in out["pick"]
          and out["home"]["library"] == "dtf", out["pick"])
    lost = plan([spot("a")], series("a", 3, 1000), home=None, landings=None, who="Hackworth")
    check("no home for the character: ok false, a clear error, no pick, the spots still ranked",
          not lost["ok"] and lost["error"] == "no home in harness/data/homes.json for Hackworth"
          and lost["pick"] is None and row(lost, "a")["rate_logs_h"] > 0, lost.get("error"))
    db = os.path.join(tempfile.mkdtemp(), "h.db")
    mem = Memory(db)
    none = lo.plan_from_store(mem, None, None, None, None, seed=1, now=NOW, route_check=False)
    check("no proxy and no trip row naming the character: the same error",
          not none["ok"] and none["error"].startswith("no home in harness/data/homes.json for")
          and none["home"] is None, none.get("error"))
    import jobs
    mem.episode("lumber", {**trip("terran_wilds", NOW - 3600, 300, 900),
                           "character": {"serial": "0x00000001", "name": "Outland Dan"}})
    dash = jobs.lumber_plan(mem, NOW)
    active = [r for r in dash["spots"] if r["status"] == "active"]
    check("the newest trip row names the character: its home, a landing from the DTF library for every seed spot "
          "(the dashboard plans no routes), a pick that names its landing",
          dash["ok"] and dash["home"]["character"] == "Outland Dan" and dash["home"]["at_home"] is None
          and active and all(r["landing"] and r["landing"]["library"] == "dtf" and r["reach"] for r in active)
          and dash["pick"]["landing"] == next(r["landing"] for r in active if r["id"] == dash["pick"]["spot"])
          and lo.landing_routes(mem) == {}, (dash.get("error"), [(r["id"], r["reach"]) for r in active]))
    mem.close()


def test_libraries():
    print("== rune libraries: which library and tome a rune is recalled from, and where it lands ==")
    import places
    d, c = places.library_rune("dtf", "286"), places.library_rune("cambria", "286")
    check("each library's rune 286 lands where its own rune was marked (DTF read off the tome, Cambria = dig tile)",
          (d["x"], d["y"]) == (1768, 2003) and d["name"] == "286" and (c["x"], c["y"]) == (1765, 2007)
          and d["tome"] != c["tome"], (d, c))
    try:
        places.library_rune("dtf", "Cambria")
        ambiguous = False
    except KeyError as e:
        ambiguous = "Towns Shrines & Alliances" in str(e) and "Public Dockmasters" in str(e)
    check("a name two tomes carry is refused with both tomes named", ambiguous)
    check("the tome picks one", places.library_rune("dtf", "cambria", "towns")["tome_title"] == "Towns Shrines & Alliances"
          and places.library_rune("dtf", "Cambria", "Public Dockmasters")["x"] is not None)
    lib = {r["id"]: r for r in places.witcher_runes_in("dtf")}
    check("the DTF Witcher set: all 360 runes, at the tiles its runes land on, with the table's names",
          len(lib) == 360 and (lib["165"]["x"], lib["165"]["y"]) == (4143, 71)
          and lib["165"]["name"] == places.witcher_rune("165")["name"], lib.get("165"))


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
    found, skipped = lo.discover_witcher(fn, runes, {}, route_fn=route, towns=[(3000, 1030)])
    check("only the quiet grove", [s["id"] for s in found] == ["witcher_1"], [s["id"] for s in found])
    s = found[0] if found else {}
    check("an area like any spot (no bank, access or travel: trips reach it from home), with the route in "
          "from its rune",
          not any(k in s for k in ("banker", "access", "home", "travel", "travel_min", "requires_young"))
          and s.get("route_tiles") == 12 and s.get("pvp") is True
          and s.get("tree_count", 0) >= 60 and abs(s["area"]["center"][1] - 1010) <= 7, s)
    check("the others left out for the right reasons",
          skipped == {"monster name": 1, "in or by a town (no harvesting there)": 1,
                      "no short route from the rune": 1, "few trees": 1}, skipped)
    check("monster words in place names", places.danger_hint("Orc Fort 2") == ["orc"]
          and places.danger_hint("Western Ruins Brigands 3") == ["brigan"] and places.danger_hint("Brigan Camp 2") == ["brigan"]
          and places.danger_hint("Cedar Forest") == [])
    check("the committed tables: 360 Witcher runes, each in one of the 14 Cambria tomes",
          len(places.witcher()["runes"]) == 360 and len(places.library()["tomes"]) == 14
          and sum(len(t["rows"]) for t in places.library()["tomes"]) == 360
          and places.witcher_rune("286")["name"] == "Midlands Ruins 1 (South)")
    # Up to 200 tiles out: a grove two runes reach goes to the nearer rune; a rune whose
    # best grove is taken gets its next best; one with nothing else left is reported.
    far = [{"id": "10", "name": "West Field", "x": 6000, "y": 1000, "tome": "0x1"},
           {"id": "11", "name": "East Field", "x": 6260, "y": 1000, "tome": "0x1"},
           {"id": "12", "name": "South Field", "x": 6150, "y": 1200, "tome": "0x1"}]
    big = [(6150 + dx, 1010 + dy, 0, 0x0CE0) for dx in range(-8, 9, 2) for dy in range(-8, 9, 2)]   # 81
    small = [(5900 + dx, 1000 + dy, 0, 0x0CE0) for dx in range(-5, 7, 2) for dy in range(-5, 7, 2)]  # 36
    fn2 = lambda x0, y0, x1, y1: [t for t in big + small if x0 <= t[0] <= x1 and y0 <= t[1] <= y1]  # noqa: E731
    found2, skipped2 = lo.discover_witcher(fn2, far, {}, route_fn=lambda s, c, r: lo.cheb(s, c))
    got = {s["id"]: (s["tree_count"], s["area"]["center"]) for s in found2}
    check("the 81-tree grove 110 tiles from rune 11 (150 from rune 10) goes to rune 11",
          got.get("witcher_11", (0,))[0] == 81 and lo.cheb(got["witcher_11"][1], (6150, 1010)) <= 7, got)
    check("rune 10 gets its next best grove, 100 tiles west (the window nearest the rune that holds all of it)",
          got.get("witcher_10", (0,))[0] == 36 and lo.cheb(got["witcher_10"][1], (5900, 1000)) <= 14, got)
    check("rune 12 reaches only the taken grove",
          "witcher_12" not in got and skipped2 == {"its groves are taken (a spot or a nearer/denser candidate)": 1},
          skipped2)
    s11 = next(s for s in found2 if s["id"] == "witcher_11")
    check("with no landing known yet, its overhead prior walks the route from the rune into the grove",
          abs(lo.overhead_prior_s(s11) - lo.overhead_prior_s({**s11, "route_tiles": 0})
              - s11["route_tiles"] * lo.SEC_PER_TILE) < 1e-6 and s11["route_tiles"] >= 100, s11)
    check("a rune that already has a spot is left alone",
          lo.discover_witcher(fn2, far[1:2], {"witcher_11": spot("witcher_11", 1, 1)})
          == ([], {"already a spot": 1}))


def test_travel_costs():
    print("== travel costs: the lockout is overhead, a trip that never chopped has no field time, supplies cost ==")
    base = trip("w", NOW - 3600, 300, 900)
    locked = {**base, "lockout_s": 60.0}
    a, b = lo.trip_obs(base), lo.trip_obs(locked)
    check("the 60 s lockout waited at the first tree moves from field time to overhead",
          b["field_s"] == a["field_s"] - 60 and b["overhead_s"] == a["overhead_s"] + 60, (a, b))
    never = trip("w", NOW - 3600, 0, 80, outcome="aborted", why="library recall to rune 286 failed: fizzled")
    never["walk_out_s"] = None
    check("walk out never ended (the recall failed): no field time, so no 0-log hour against the spot",
          lo.trip_obs(never)["field_s"] == 0.0 and not lo.trip_obs(never)["place_fail"], lo.trip_obs(never))
    sup = {"library_charges": 1, "own_charges": 1, "recall_casts": 0, "reagents_used": {"black pearl": 2}}
    spots = [spot("s")]
    bare = plan(spots, series("s", 10, 1500))
    eps = [{**e, "supplies": sup} for e in series("s", 10, 1500)]
    free = plan(spots, eps)
    check("no supply prices: supplies cost nothing and are counted unpriced (2 reagents + 1 own charge a trip)",
          row(free, "s")["supply_gp_trip"] == 0 and row(free, "s")["supply_unpriced"] == 30
          and row(free, "s")["net_logs_h"] == row(bare, "s")["net_logs_h"], (row(free, "s"), row(bare, "s")))
    prices = {"reagent:black_pearl": {"price_gp": 50.0}, "recall_charge": {"price_gp": 100.0}}
    paid = plan(spots, eps, prices=prices)
    check("priced: 2 x 50 + 1 x 100 = 200 gp a trip (the library's charges are free), and the spot nets less",
          row(paid, "s")["supply_gp_trip"] == 200 and row(paid, "s")["supply_unpriced"] == 0
          and row(paid, "s")["net_logs_h"] < row(bare, "s")["net_logs_h"] - 10, (row(paid, "s"), row(bare, "s")))


if __name__ == "__main__":
    for fn in (test_explore_exploit, test_greedy_command, test_skill_rescaling, test_trip_size, test_hazard_evidence,
               test_gear_and_capacity, test_eligibility, test_regrowth, test_hatchets, test_spots_store,
               test_failed_places, test_landings, test_home_and_trips, test_libraries, test_discover_witcher,
               test_travel_costs, test_capacity):
        fn()
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILURES: {FAILURES}"))
    sys.exit(0 if not FAILURES else 1)

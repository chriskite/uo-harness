"""Behaviour of the lumber optimizer (harness/lumber_opt.py, docs/LUMBER_LOOP.md §6):
which spot it picks, how much it carries, which hatchet, and what it learns from
trips, deaths and depleted trees. Synthetic trip rows; no network, no game.

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
    check("a dying trip banks nothing: only the trips that live bank, all q of them",
          abs(dead["banked"] - q * surv) < 1e-6 and abs(dead["p_death"] - (1 - surv)) < 1e-9, dead)
    check("a trip sent home at the same rate banks everything it chopped, and loses nothing",
          abs(home["banked"] - lam * (1 - surv) / h) < 1e-6 and home["banked"] > dead["banked"]
          and home["lost_death"] == 0 and dead["lost_death"] > 0 and home["p_death"] == 0, (home, dead))
    check("being sent home alone never shrinks the trip (nothing is lost)",
          lo.best_q(lam, t_h, hz(home=3.0), 3.0) == lo.Q_MAX)
    long = lo.trip_terms(10000, 500.0, t_h, hz(death=1.0))           # 20 field hours at 1 death/h
    rate = lo.net_rate(10000, 500.0, t_h, hz(death=1.0), 3.0)
    check("long trips stay well-defined: P(death) <= 1, nothing banked below 0, the rate no worse than losing "
          "the gear every cycle",
          abs(long["p_death"] - (1 - math.exp(-20))) < 1e-9 and 0 <= long["banked"] < 1
          and -3.0 / long["time_h"] <= rate < 0, (long, rate))
    calm, robbed = lo.best_q(lam, t_h, hz(0.05), 3.0), lo.best_q(lam, t_h, hz(0.05, theft=2.0), 3.0)
    check("thieves lower Q* (a grab takes a share of the load)", robbed < calm, (calm, robbed))
    lost = lo.trip_terms(2000, lam, t_h, hz(theft=1.0, share=0.5))
    check("theft: the trip runs its full length, the share taken is gone",
          abs(lost["time_h"] - (t_h + 2000 / lam)) < 1e-9 and lost["lost_theft"] > 0
          and abs(lost["banked"] + lost["lost_theft"] - 2000) < 1e-6, lost)
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
                     "carried": 400}}
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
    for fn in (test_explore_exploit, test_skill_rescaling, test_trip_size, test_hazard_evidence,
               test_gear_and_capacity, test_eligibility, test_regrowth, test_hatchets, test_spots_store,
               test_discover, test_failed_places, test_travel_and_hub, test_discover_witcher, test_travel_costs):
        fn()
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILURES: {FAILURES}"))
    sys.exit(0 if not FAILURES else 1)

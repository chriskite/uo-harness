"""Maus 3 on 2026-10-31 (hunt-20261031-111828-a80c, capture session_20261031_105918) in the simulator: the death at
11:57:22 and the two near-deaths before it, each as its live layout (docs/NOTES.md "Maus 3 near-deaths 2026-10-31 11:23
and 11:49, dead at 11:57"). The live team (phoenix_incubus: Bussin and AlaFlambe), the live command's runner arguments.

    python harness/sim/fight1031.py <case> <seeds: 1,2,3 or 1-24> [--log] [--harness DIR]

1157  The death. Logan at (5246, 637) where "off the tank" put him (11:57:15.86), the pets at (5245, 638) on guard, a
      forgotten soul idle at (5245, 629), 8 off (live 11:57:15.98), and (added 2026-10-31) a vampiric consort hidden
      at her spawn spot (5243, 633), freshly respawned (World.lurk_now; live killed 11:51:09, back by 11:56:55).
      Live the runner targeted the soul at 8 tiles and "toward a target" walked him 2 steps on to (5245, 635), 6 off it,
      3 ahead of the pets, 2 from the consort's spot; she came out beside him and bit -46 with a bleed (-29 3 s on),
      then -39: dead. Per seed: where Logan stood when the soul woke (its distance, the nearest pet's, Logan nearer it
      than every pet), its first target, the approaches and step-ins, the approach's stops short of the consort's spot,
      the nearest Logan came to her spot, her bites on him, Logan's damage by source, the lowest hits, death.
1149  The retreat. Logan at (5243, 631) at 46/100, a shade wolf beside him at (5244, 631) on him, the pets at
      (5243, 635) on guard, the forgotten soul awake at (5245, 628) casting at him; the potion drunk 3 s before, a vet
      channel on him with 4 s to run (live 11:49:34). Live the retreat went 7 tiles north-west to (5237, 625), 10 from
      the pets, 8 from the soul in its sight, which cast at him there (-23, 26 -> 3/100). Per seed: the retreat's tile,
      its distance from the nearest pet, whether the soul saw him there within its spell reach, the soul's damage on
      him in the 10 s from the retreat, death.
1123  The wails. Logan at (5176, 687) at 89/100 where the stand-off left him (11:23:33.40), the pets at (5176, 688) on
      guard, a corpsebride at (5176, 685) awake on him, its last wail 3 s before (the next due in 5.2 s). Live "off the
      tank" took him to (5178, 691), the bride came to the pets at (5176, 687), 4 off him, and its wail hit him there
      (-21 at 11:23:39.05, as at :30.74 from 4 too: the wail reaches 4 tiles 75 % of the time, the scenario's far_r).
      Per seed: the wails, those that hit Logan and from how far, the seconds he stood within 4 of it while it fought,
      his damage, death.

--harness runs another checkout's runner (a snapshot, with this simulator copied in) for a before/after comparison.
"""
import contextlib
import io
import os
import re
import sys
import tempfile

ARGS = sys.argv[1:]
HARNESS = os.path.abspath(ARGS[ARGS.index("--harness") + 1]) if "--harness" in ARGS \
    else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS)


def seeds(arg):
    if "-" in arg:
        a, b = arg.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(s) for s in arg.split(",")]


CASE = ARGS[0] if ARGS else "1157"
SEEDS = seeds(ARGS[1]) if len(ARGS) > 1 and not ARGS[1].startswith("--") else [1]
SHOW_LOG = "--log" in ARGS

import loop_hunt  # noqa: E402
import memory  # noqa: E402
import tamer  # noqa: E402
from agent_link import Abort  # noqa: E402
from sim import clock as simclock  # noqa: E402
from sim import pets as simpets  # noqa: E402
from sim import run as simrun  # noqa: E402
from sim.link import SimLink  # noqa: E402
from sim.scenario import load  # noqa: E402
from sim.world import World, cheb  # noqa: E402

TEAM = "phoenix_incubus"                     # live: Bussin (incubus) and AlaFlambe (phoenix)
EXTRA = ["--heal-in-place", "--vet-range", "5", "--crawl-learn-s", "180", "--human", "off"]
LAYOUT = {
    "1157": {"start": (5246, 637), "pets": ((5245, 638), (5245, 638)),
             "spawns": [("forgotten soul", (5245, 629)), ("shade wolf", (5242, 642)), ("vampiric consort", (5243, 633))],
             "secs": 40.0, "hits": 100},
    "1149": {"start": (5243, 631), "pets": ((5243, 635), (5243, 635)),
             "spawns": [("forgotten soul", (5245, 628)), ("shade wolf", (5244, 631))], "secs": 12.0, "hits": 46},
    "1123": {"start": (5176, 687), "pets": ((5176, 688), (5176, 688)), "spawns": [("corpsebride", (5176, 685))],
             "secs": 25.0, "hits": 89, "args": ["--no-peace"]},   # live no song calmed it; a calm one doesn't wail
}
RETREAT_TO = re.compile(r"retreating .*; to \((\d+), (\d+)\)")
CONSORT_SPOT = (5243, 633)                   # the vampiric consort's spawn spot (harness/data/sim/mausoleum.json)
SPELL_R = tamer.CASTER_R                     # the soul's spells: 7-8 tiles live (11:49:30, :36), the runner's reach 12


def world(case, seed):
    lay = LAYOUT[case]
    sc = load()
    simpets.select(sc, TEAM, None, None)
    sc["spawns"] = [{"key": k, "at": [x, y, 0], "respawn_s": [99999, 99999]} for k, (x, y) in lay["spawns"]]
    sc["defaults"]["spawn_spread"] = sc["defaults"]["wander_per_s"] = 0     # each where the capture had it
    sc["player"]["start"] = [*lay["start"], 0]
    sc["others"] = {**sc["others"], "enabled": case == "1157", "arrive_per_h": 0.0, "unseen": {}}   # placed below
    return World(sc, seed=seed)


def one(case, seed):
    lay = LAYOUT[case]
    w = world(case, seed)
    clock = simclock.SimClock(epoch=simrun.EPOCH)
    link = SimLink(w, clock)
    for p, xy in zip(w.pets(), lay["pets"]):
        p.x, p.y = xy
    w.player.hits = lay["hits"]
    link.send(tamer.command("all guard me"))
    mob = {m.key: m for m in w.mobs.values()}
    if case == "1157":
        # live: a shade wolf at (5242, 642) in war mode on Lord Scruffy Growler (5242, 643), 5 off Logan, 16 % left
        # (11:57:11 "passing over ... its loot is theirs"); with it fighting within 8 the hold doesn't stop the approach
        wolf = mob["shade wolf"]
        other = w.add_other(5242, 643, w.stand_z(5242, 643, w.player.z), name="Scruffy Growler", stay=1e6, dps=1.0)
        wolf.hits = wolf.hits_max * 0.16
        wolf.target, wolf.threat, other.target = other.serial, {other.serial: 1e9}, wolf.serial
        w._warm(wolf, True)
        w.lurk_now(mob["vampiric consort"])  # hidden at her spot, as her successor live
    pl = w.player
    db = os.path.join(tempfile.mkdtemp(prefix="fight_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, EXTRA + lay.get("args", []), 10, at_hunt=True)
                                             + ["--memory", db, "--seed", str(seed)])
    hits = []                                # (t, source, kind, amount, distance from the source)
    orig_hurt = w.hurt

    def hurt(victim, amount, by, kind="melee", *a, **kw):
        before = victim.hits
        r = orig_hurt(victim, amount, by, kind, *a, **kw)
        if victim is pl and before != victim.hits:
            hits.append((round(w.t, 2), by.key if by is not None else kind, kind, round(before - victim.hits),
                         cheb(by.pos, pl.pos) if by is not None else None))
        return r
    w.hurt = hurt
    wails = [0]
    orig_overhead = w._overhead

    def overhead(m, text, *a, **kw):
        if text == "*wails*":
            wails[0] += 1
        return orig_overhead(m, text, *a, **kw)
    w._overhead = overhead
    wake, low, near_s, retreat, spot_d = {}, [pl.hits], [0.0], {}, [99]
    out = io.StringIO()
    world_tick = clock.on_advance
    last = [None]

    def watch(now):
        world_tick(now)
        low[0] = min(low[0], pl.hits)
        dt = w.t - last[0] if last[0] is not None else 0.0
        last[0] = w.t
        if case == "1157":
            spot_d[0] = min(spot_d[0], cheb(pl.pos, CONSORT_SPOT))
        soul = mob.get("forgotten soul")
        if soul is not None and not wake and soul.alive and soul.target is not None:
            tgt = w.mobs.get(soul.target)
            pets = [p for p in w.pets() if p.alive]
            dp = min(cheb(p.pos, soul.pos) for p in pets) if pets else None
            wake.update({"t": round(w.t, 1), "on": "Logan" if soul.target == pl.serial else tgt.name if tgt else "?",
                         "logan_d": cheb(pl.pos, soul.pos), "pet_d": dp,
                         "ahead": dp is not None and cheb(pl.pos, soul.pos) < dp, "at": tuple(pl.pos[:2])})
        bride = mob.get("corpsebride")
        if bride is not None and bride.alive and bride.target is not None and cheb(pl.pos, bride.pos) <= 4:
            near_s[0] += dt
        if case == "1149" and not retreat and (m := RETREAT_TO.search(out.getvalue())):
            to = (int(m.group(1)), int(m.group(2)))
            soul, pets = mob["forgotten soul"], [p for p in w.pets() if p.alive]
            z = w.stand_z(to[0], to[1], pl.z)
            retreat.update({"t": w.t, "to": to, "pet_d": min(cheb(to, p.pos) for p in pets) if pets else None,
                            "from_pet_d": min(cheb(pl.pos, p.pos) for p in pets) if pets else None,
                            "soul_d": cheb(to, soul.pos), "soul_sees": soul.alive and cheb(to, soul.pos) <= SPELL_R
                            and w.sight.can_see(soul.pos, (to[0], to[1], z if z is not None else pl.z))})
    clock.on_advance = watch
    patched = simclock.install(clock)
    store = memory.Memory(db)
    outcome = "done"
    try:
        with contextlib.redirect_stdout(out):
            loop = loop_hunt.HuntLoop(link, store, args)
            loop.team.setup(loop.state())
            loop.spot = loop.fight_spot = tuple(pl.pos[:2])
            loop.start_visit(loop.state())
            for sid in tamer.SONG_ORDER:                                         # live: the three songs up since 11:18
                loop.team.songs[sid] = loop_hunt.time.monotonic() + 900.0
            w.songs.update({k: w.t + 900.0 for k in ("discord", "peace", "provoke")})
            if case == "1149":
                soul, wolf = mob["forgotten soul"], mob["shade wolf"]
                for m in (soul, wolf):
                    m.target, m.threat = pl.serial, {pl.serial: 50}
                    w._warm(m, True)
                loop.potions.started(loop_hunt.time.monotonic() - 3.0)          # live: drunk 31.13, the retreat 34.19
                loop.team.vet_busy_until = loop_hunt.time.monotonic() + 4.0     # live: the channel 32.53-37.93
            if case == "1123":
                bride = mob["corpsebride"]
                bride.hits = bride.hits_max = 10 ** 6           # live it fought on past the leave (54 % at 11:23:40)
                bride.target, bride.threat = pl.serial, {pl.serial: 50}
                w._warm(bride, True)
                bride.cd["wail"] = w.t + 5.2                    # live: wails at 30.35 and 38.65
            t_end = w.t + lay["secs"]
            try:
                while w.t < t_end and pl.alive and not pl.ghost:
                    loop.tick()
            except Abort as e:
                outcome = f"aborted: {str(e)[:60]}"
    finally:
        clock.on_advance = world_tick
        store.close()
        simclock.uninstall(patched)
    logl = out.getvalue().splitlines()
    by = {}
    for _, src, kind, n, _d in hits:
        by[f"{src}:{kind}"] = by.get(f"{src}:{kind}", 0) + n
    res = {"seed": seed, "dead": w.metrics["deaths"], "low": round(low[0]), "taken": sum(h[3] for h in hits), "by": by,
           "outcome": outcome}
    if case == "1157":
        res.update({"wake": wake, "approaches": sum("toward a target: arrived" in ln for ln in logl),
                    "step_ins": sum("step-in" in ln for ln in logl),
                    "soul_on_logan": sum(n for _, s, _k, n, _d in hits if s == "forgotten soul"),
                    "stops": sum("from the vampiric consort's spawn spot" in ln and "stopping at" in ln for ln in logl),
                    "spot_d": spot_d[0], "bites": sum(1 for _, s, _k, _n, _d in hits if s == "vampiric consort"),
                    "consort_on_logan": sum(n for _, s, k, n, _d in hits if s == "vampiric consort" or k == "bleed")})
    elif case == "1149":
        res.update({"retreat_to": retreat.get("to"), "from_pet_d": retreat.get("from_pet_d"),
                    "pet_d": retreat.get("pet_d"), "soul_d": retreat.get("soul_d"),
                    "soul_sees": bool(retreat.get("soul_sees")),
                    "soul_after": sum(n for t, s, _k, n, _d in hits if s == "forgotten soul" and retreat
                                      and t >= retreat["t"])})
    else:
        res.update({"wails": wails[0],
                    "wail_hits": [(d, n) for _, s, k, n, d in hits if k == "wail"],
                    "near_s": round(near_s[0], 1)})
    res["log"] = logl
    return res


if __name__ == "__main__":
    rows = []
    for s in SEEDS:
        r = one(CASE, s)
        log = r.pop("log")
        rows.append(r)
        print(r)
        if SHOW_LOG:
            print("\n".join("    " + line[:170] for line in log))
    n = len(rows)
    if n > 1:
        dead = sum(r["dead"] for r in rows)
        if CASE == "1157":
            woke = [r["wake"] for r in rows if r["wake"]]
            print(f"all {n}: deaths {dead}, the soul woke in {len(woke)}: on Logan {sum(x['on'] == 'Logan' for x in woke)}, "
                  f"Logan nearer it than every pet {sum(x['ahead'] for x in woke)}, approaches "
                  f"{sum(r['approaches'] for r in rows)}, step-ins {sum(r['step_ins'] for r in rows)}, the soul's damage on "
                  f"Logan {sum(r['soul_on_logan'] for r in rows)}, lowest hits median "
                  f"{sorted(r['low'] for r in rows)[n // 2]}; the consort's spot: the approach stopped short in "
                  f"{sum(r['stops'] > 0 for r in rows)}, Logan within 2 of it in {sum(r['spot_d'] <= 2 for r in rows)} "
                  f"(nearest {min(r['spot_d'] for r in rows)}), bitten in {sum(r['bites'] > 0 for r in rows)}, her "
                  f"damage and bleed on Logan {sum(r['consort_on_logan'] for r in rows)}")
        elif CASE == "1149":
            went = [r for r in rows if r["retreat_to"]]
            print(f"all {n}: deaths {dead}, retreats {len(went)}: to a tile in the soul's sight within {SPELL_R} "
                  f"{sum(r['soul_sees'] for r in went)}, 6+ from every pet {sum((r['pet_d'] or 0) >= 6 for r in went)}, "
                  f"the soul's damage on Logan after it {sum(r['soul_after'] for r in rows)}, lowest hits median "
                  f"{sorted(r['low'] for r in rows)[n // 2]}")
        else:
            wh = [h for r in rows for h in r["wail_hits"]]
            print(f"all {n}: deaths {dead}, wail hits on Logan {len(wh)} ({sum(x[1] for x in wh)} hits; at 3-4 tiles "
                  f"{sum(1 for x in wh if x[0] is not None and x[0] >= 3)}), seconds within 4 of the fighting bride "
                  f"{sum(r['near_s'] for r in rows):.1f}, lowest hits median {sorted(r['low'] for r in rows)[n // 2]}")

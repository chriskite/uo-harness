"""The 11:56 death on Maus 3 on the way to the crimson gate (hunt-20261105-114104-0635, capture
session_20261105_084113, 2026-11-05) in the simulator. Live: a planned leave (the loot home, 11:55:57 at (5241, 697))
fought its way north out of the east wing and west along y 654; on an approach to a corpsebride the red MultiCast (a
spellbook) came into view at (5213, 667) at 11:56:11.23, 18 tiles off; the approach took two more steps west and the
leave rule fired at the fought leg's next round (12.1, 15 tiles): "the rest of the way as a flight", "the way passes
MultiCast ... (a dead end): past them at once". SweDo (a crossbow) came into view at (5211, 669) at 12.96, IIIaJIaBa (a
spellbook, an earth elemental summoned) at (5208, 657) at 14.97, all three on the gate's way (the ramp top west), the
only way to the gate crossing the strip x 5200-5220, y 672-674. Logan roped to (5226, 661), walked south and west along
y 673 between them, SweDo reached (5208, 673) first, and Logan died at (5207, 673) at 11:56:23.81 (docs/NOTES.md "Dead
to two reds on the way to the gate (2026-11-05 11:56)").

Two layouts, the live team (AlaFlambe, Bussin), the reds walking their capture tiles (0x77) up to where each first
stood to cast or shoot, then coming at us (sim.world.add_red): MultiCast a mage (opener Magic Arrow, as live), SweDo
the archer kit with his crossbow, IIIaJIaBa a mage with a summon.
- the flight (default): from the leave rule's moment, 11:56:12.17, Logan at (5229, 654), the pets beside him
  (HuntLoop.leave_by_gate "urgent", the runner's own flight). Per seed: dead or through the gate, the flight's first
  decision, the seconds to the death, the nearest any red came in the first 6 s, the first rope's landing (`rope1`:
  south of y 659, as live, or along the way west/north decides most runs), the damage on Logan (`--log`: by whom).
- --from-leave: the planned leave itself from (5241, 697) (HuntLoop.run, the return rule firing on its first look, as
  sim/fight2333.py), the monsters in view live on the way (a corpsebride, the elder vampire and the elder vampire
  countess on the ledge west, idle), the reds coming into view when Logan first comes within 18 tiles of (5213, 667)
  (live (5231, 655)); its log follows the live one line for line ("target a corpsebride", "holding here: a elder
  vampire countess would see us", "a leave rule fired (red player MultiCast in view at 16 tiles)", two steps nearer
  him first). Per seed also the seconds from the first sight to the flight (`flight_s`) and Logan's steps nearer
  MultiCast meanwhile; the seconds are from the first sight.

    python harness/sim/fight1156.py <seeds: 1,2,3 or 1-24> [--from-leave] [--alt NAME] [--spawns] [--log]
                                    [--trace DIR] [--harness DIR]

--alt tries another flight by monkeypatching the runner here (the repo's runner is unchanged):
  away     no dead-end rule: away from them first (flee_tile), back east, as before 2026-10-22
  first    the flight at MultiCast's first sight (11:56:11.23, Logan at (5231, 655), 18 tiles; live 0.94 s later at 15)
  hold     out of their sight and stand there (cover_tile's tile, fewer of them seeing it), up to FLEE_MAX_S, instead
           of past them at once: the wait for them to pass
  stop     (--from-leave) a planned leave's fought leg stops its walks (the approach, the walk on) for a hostile player
           in view (player_threat), so the leave rule's round comes at once; today only the round checks it, and the
           recall mode's look on every read is off while leaving (HuntLoop.fleeing)
--spawns (the flight): the level's monsters (scenario spawns) kept, none within 6 of the start (live the nearest, a
  corpsebride passed over, was 7-8 off); without it the level is empty, as in the other replays. The northern loop
  round the ramp top (y 624-641: the elder vampire countess, an elder vampire, a vampiric consort, forgotten souls) is
  free only on an empty level.
The simulated pets guard against monsters only (live none fought a red), so 'All Guard Me' is no alternative here.
--harness runs another checkout's runner (a snapshot, with this simulator copied in) for a before/after comparison.
"""
import contextlib
import io
import os
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


SEEDS = seeds(ARGS[0]) if ARGS and not ARGS[0].startswith("--") else [1]
SHOW_LOG = "--log" in ARGS
ALT = ARGS[ARGS.index("--alt") + 1] if "--alt" in ARGS else "today"
SPAWNS = "--spawns" in ARGS
FROM_LEAVE = "--from-leave" in ARGS

import loop_hunt  # noqa: E402
import memory  # noqa: E402
from agent_link import Abort  # noqa: E402
from sim import clock as simclock  # noqa: E402
from sim import pets as simpets  # noqa: E402
from sim import run as simrun  # noqa: E402
from sim import trace  # noqa: E402
from sim.link import SimLink  # noqa: E402
from sim.scenario import load  # noqa: E402
from sim.world import World, cheb  # noqa: E402
TRACE = ARGS[ARGS.index("--trace") + 1] if "--trace" in ARGS else None   # record each run's trace (sim/trace.py) here
STAMP = trace.stamp()

EXTRA = ["--heal-in-place", "--vet-range", "5", "--crawl-learn-s", "180"]
# t 0 = 11:56:12.17 (the leave rule's moment): Logan at (5229, 654), both pets on (5230, 654), MultiCast at (5214, 666)
START, PETS = (5229, 654), ((5230, 654), (5230, 655))
# (x, y, seconds after the one before), the capture's 0x77s up to each one's first stand to cast or shoot
MULTICAST_AT = (5214, 666)
MULTICAST = [(5215, 665, 0.22), (5216, 664, 0.19), (5217, 663, 0.23), (5218, 662, 0.16), (5219, 661, 0.20),
             (5220, 660, 0.20), (5221, 659, 0.20), (5222, 658, 0.21), (5223, 657, 0.19), (5224, 656, 0.21)]
SWEDO_T, SWEDO_AT = 0.79, (5211, 669)
SWEDO = [(5212, 668, 0.19), (5213, 667, 0.21), (5214, 666, 0.19), (5215, 665, 0.22), (5215, 664, 0.29),
         (5215, 663, 0.20), (5215, 662, 0.20), (5216, 661, 0.33), (5217, 660, 0.18)]
OIA_T, OIA_AT = 2.80, (5208, 657)
OIA = [(5209, 656, 0.18), (5210, 656, 0.31), (5211, 657, 0.30), (5212, 658, 0.20), (5213, 659, 0.20),
       (5214, 660, 0.21)]
if ALT == "first" or FROM_LEAVE:
    # t 0 = 11:56:11.23, MultiCast's first sight at (5213, 667), 0.94 s before the live leave rule's moment
    START, PETS = (5231, 655), ((5232, 655), (5232, 656))
    MULTICAST_AT = (5213, 667)
    MULTICAST = [(5214, 666, 0.94)] + MULTICAST
    SWEDO_T, OIA_T = SWEDO_T + 0.94, OIA_T + 0.94
SIGHT_FROM, SIGHT_R = (5213, 667), 18     # --from-leave: the reds come into view when Logan first comes this near
LEAVE_START, LEAVE_PETS = (5241, 697, 0), ((5240, 698), (5242, 698))    # 11:55:57, "LEAVING by the moongate"
# --from-leave: the monsters in view on the way, idle (capture 0x77/0x20), no respawns
LEAVE_SPAWNS = [("corpsebride", (5224, 647, 25)), ("elder vampire", (5215, 639, 15)),
                ("elder vampire countess", (5217, 639, 15))]
WHY = "taking the loot home: 45430 gp carried (the 11:55:57 leave)"


class Home(Exception):
    """Through the gate and home: the run's end here."""


def patch(loop):
    """--alt: the alternative on this HuntLoop instance (the runner's code unchanged)."""
    if ALT == "away":
        loop.dead_end = lambda *a, **k: False
    elif ALT == "hold":
        # FLEE_FAR_R 0: flee_out's "N+ tiles from them here, looking again" (FLEE_HOLD_S) whenever no tile is offered;
        # the only tile offered is cover_tile's when fewer of them see it than here, so the flight goes out of their
        # sight and stands there, up to FLEE_MAX_S, then past them as today
        loop_hunt.FLEE_FAR_R = 0
        loop.dead_end = lambda *a, **k: False

        def flee_tile(st, spots, failed=()):
            found = loop.cover_tile(st, spots, failed)
            return found[0] if found is not None and found[2] < found[1] else None
        loop.flee_tile = flee_tile
    elif ALT == "stop":
        walk_to = loop.mover.walk_to

        def stopping_walk(goal, r, label, *a, stop=None, **kw):
            def stop2(s):
                if loop.fought_end is not None and loop.fleeing and loop.player_threat(s):
                    return "a hostile player in view"
                return stop(s) if stop is not None else None
            return walk_to(goal, r, label, *a, stop=stop2, **kw)
        loop.mover.walk_to = stopping_walk


def one(seed):
    sc = load()
    simpets.select(sc, "phoenix_incubus", None, None)   # the live team: AlaFlambe (the tank) and Bussin
    if FROM_LEAVE:
        sc["spawns"] = [{"key": k, "at": list(at), "respawn_s": [99999, 99999]} for k, at in LEAVE_SPAWNS]
        sc["defaults"]["spawn_spread"] = sc["defaults"]["wander_per_s"] = 0   # each where the capture had it
        sc["player"]["start"] = list(LEAVE_START)
        pets = LEAVE_PETS
    else:
        sc["spawns"] = [s for s in sc["spawns"] if cheb(s["at"][:2], START) > 6] if SPAWNS else []
        sc["player"]["start"] = list(START) + [0]
        pets = PETS
    clock = simclock.SimClock(epoch=simrun.EPOCH)
    w = World(sc, seed=seed)
    for p, xy in zip(w.pets(), pets):
        p.x, p.y = xy
    link = SimLink(w, clock)
    tracer = trace.Tracer(w, trace.path_for(TRACE, STAMP, "fight1156", seed), label="fight1156", seed=seed,
                          epoch=simrun.EPOCH) if TRACE else None
    db = os.path.join(tempfile.mkdtemp(prefix="fight_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(sc, EXTRA, 10, at_hunt=True)
                                             + ["--memory", db, "--seed", str(seed)])
    patched = simclock.install(clock)
    store = memory.Memory(db)
    world_tick, reds, near, t0, died = clock.on_advance, [], [99], [None], [None]
    fled, nearer, last = [None], [0], [None]
    hits, orig_hurt = [], w.hurt

    def hurt(victim, amount, by, kind="melee", *a, **kw):
        before = victim.hits
        r = orig_hurt(victim, amount, by, kind, *a, **kw)
        if victim is w.player and before != victim.hits and t0[0] is not None:
            hits.append((round(w.t - t0[0], 1), by.name if by is not None else kind, round(before - victim.hits)))
        return r
    w.hurt = hurt

    def watch(now):
        world_tick(now)
        if t0[0] is None:
            if FROM_LEAVE and (w.player.ghost or cheb(w.player.pos, SIGHT_FROM) > SIGHT_R):
                return
            t0[0] = w.t
            # MultiCast live: a spellbook; Magic Arrow first (14.30), Telekinesis and its stuck potion (18.21), Energy
            # Bolts (20.85, 22.40)
            reds.append(w.add_red(*MULTICAST_AT, name="MultiCast", route=list(MULTICAST),
                                  spec={"hands": [[0x0EFA, 1, 1]], "opener": {"ma": 1}}))
        el = w.t - t0[0]
        if len(reds) == 1 and el >= SWEDO_T:
            # SweDo live: a crossbow (0x0F50); bolts 17.39 (-21), 20.30, 23.81 (-13), ~3 s apart
            reds.append(w.add_red(*SWEDO_AT, name="SweDo", route=list(SWEDO), kit="archer",
                                  spec={"hands": [[0x0F50, 2, 1, 8]]}))
        if len(reds) == 2 and el >= OIA_T:
            # IIIaJIaBa live: a spellbook, an earth elemental summoned beside him (15.27), Energy Bolt 19.70
            reds.append(w.add_red(*OIA_AT, name="IIIaJIaBa", route=list(OIA),
                                  spec={"hands": [[0x0EFA, 1, 1]], "summon_share": 1.0}))
        if el <= 6.0 and not w.player.ghost and reds:
            near[0] = min(near[0], *(cheb(w.player.pos, r.pos) for r in reds))
        if fled[0] is None and not w.player.ghost:
            # Logan's own steps nearer MultiCast before the flight (live 2: the approach's (5230, 655), (5229, 654))
            d = cheb(w.player.pos, reds[0].pos)
            if last[0] is not None and w.player.pos[:2] != last[0][0] and d < last[0][1]:
                nearer[0] += 1
            last[0] = (w.player.pos[:2], d)
        if w.player.ghost and died[0] is None:
            died[0] = round(el, 1)
    clock.on_advance = watch
    out = io.StringIO()
    outcome = "?"
    try:
        with contextlib.redirect_stdout(tracer.tee(out) if tracer else out):
            loop = loop_hunt.HuntLoop(link, store, args)
            patch(loop)
            flee_out = loop.flee_out

            def flee(*a, **kw):
                if fled[0] is None and t0[0] is not None:
                    fled[0] = round(w.t - t0[0], 2)
                return flee_out(*a, **kw)
            loop.flee_out = flee
            try:
                if FROM_LEAVE:
                    fired = [False]

                    def return_reason(st):
                        if fired[0]:
                            return None
                        fired[0] = True
                        return WHY, "return"
                    loop.return_reason = return_reason

                    def home_landed(res):
                        raise Home()
                    loop.home_landed = home_landed
                    loop.run()
                    outcome = "run ended"
                else:
                    loop.team.setup(loop.state())
                    res = loop.leave_by_gate("urgent")
                    outcome = "through the gate" if res.get("ok") else f"not out: {res.get('error') or res}"[:70]
            except Home:
                outcome = "through the gate"
            except Abort as e:
                outcome = f"aborted: {str(e)[:60]}"
    finally:
        clock.on_advance = world_tick
        store.close()
        simclock.uninstall(patched)
    logl = out.getvalue().splitlines()
    first = next((ln for ln in logl if "to the moongate: the way passes" in ln), "")
    result = {"seed": seed, "dead": w.metrics["deaths"], "outcome": outcome,
              "first": "dead end: past" if "a dead end" in first else ("away first" if "away from them" in first
                                                                        else first[9:60]),
              "death_s": died[0], "nearest_6s": near[0], "ropes": sum("rope to" in ln for ln in logl),
              "flight_s": fled[0], "taken": sum(n for _, _, n in hits), "sim_s": round(clock.elapsed, 1),
              "rope1": next((ln.split("rope to ")[1].split(")")[0] + ")" for ln in logl if "rope to (" in ln), None),
              "log": logl, "hits": hits}
    if FROM_LEAVE:
        result.update(seen=t0[0] is not None, nearer=nearer[0])
    if tracer is not None:
        tracer.close({k: v for k, v in result.items() if k not in ("log", "hits")})
    return result


if __name__ == "__main__":
    rows = []
    for s in SEEDS:
        r = one(s)
        log, hl = r.pop("log"), r.pop("hits")
        rows.append(r)
        print(r, flush=True)
        if SHOW_LOG:
            print("    HITS", hl)
            print("\n".join("    " + line[:170] for line in log))
    if len(rows) > 1:
        n = len(rows)
        ds = sorted(r["death_s"] for r in rows if r["death_s"] is not None)
        extra = ""
        if FROM_LEAVE:
            fs = sorted(r["flight_s"] for r in rows if r.get("flight_s") is not None)
            extra = (f", seen {sum(r['seen'] for r in rows)}, sight to flight median "
                     f"{fs[len(fs) // 2] if fs else '-'} s, steps nearer MultiCast before it "
                     f"{sum(r['nearer'] for r in rows)}")
        print(f"{ALT}{' --from-leave' if FROM_LEAVE else ''}{' --spawns' if SPAWNS else ''}: all {n}: dead "
              f"{sum(bool(r['dead']) for r in rows)}, through the gate "
              f"{sum(r['outcome'] == 'through the gate' for r in rows)}, death at a median "
              f"{ds[len(ds) // 2] if ds else '-'} s, first decisions "
              f"{ {k: sum(r['first'] == k for r in rows) for k in {r['first'] for r in rows}} }{extra}")

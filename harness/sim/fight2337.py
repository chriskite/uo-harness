"""The walk in on Maus 2 to the teleporter at (5522, 637), from the live layout of 2026-11-05 23:37:10.1
(hunt-20261105-223546-b3bd, capture session_20261105_084113; docs/NOTES.md "Dead on Maus 2's walk in (2026-11-05 23:37)").

    python harness/sim/fight2337.py [SEEDS] [--late] [--alt NAME[,NAME]] [--no-save] [--log] [--trace DIR]
                                    [--harness DIR] [--set MODULE.NAME=VALUE] [-- runner args]

Logan at (5507, 668, -10) on 83 hits and 23 stamina, 29 steps into the leg from (5507, 693), the heal potion ready, both
pets a tile and two behind him on follow; "attacked on the way in; the pets guard" (walk_in_fight), then the walk on and
through the teleporter down to Maus 3 (walk_in). Each monster where the capture had it (0x77 / 0x20, its 0xC0s): in war
mode on Logan and standing there throughout, a calamity at (5497, 675) at 93 % and a calamity at (5511, 667) at 70 %;
idle, an insane reanimator on its ledge at (5502, 658, 5) (live in war mode from 23:37:16.96), a calamity at (5509, 650)
and an abomination at (5517, 643) (where each stood when it woke, 24.48 and 28.41), abominations at (5515, 636) and
(5532, 662), an insane reanimator at (5534, 647) and a reanimator zealot at (5529, 676) where first seen (live also a
devourer beetle at (5500, 634), 16-18 off at the end: no simulator type); a third calamity comes into view at
(5500, 676) 6.9 s in (live 23:37:16.97) and stands there. As live: "Your feet stick to a strange substance!" 0.02 s in
(an Entangle for 1.15 s), and a world save of 4.24 s from 0.06 s after Logan first stands on (5507, 664) (live
23:37:12.39-16.63, 2.29 s in; SaveLink: the server frozen, our packets held and answered after it, the proxy's 3 s
rejection of a walk sent in it, the save lines on the wire).
--late: from 23:37:21.11 instead, the first cover's end: Logan at (5506, 663, 5) on 72 hits just after the potion, the
pets on (5507, 669) on guard, AlaFlambe at 90 %; the calamity at (5511, 667) dead, the two by (5497, 675) and (5500, 676)
on AlaFlambe, the insane reanimator in war mode on Logan; no save. Live from there the covers took him 6 -> 12 -> 20
tiles from the pets, who stood at (5507, 669) the whole time; 'All Kill' from 20 tiles got no target cursor; dead at
(5518, 649) 11.2 s on.
SEEDS: "1-24" or "1,5,9" (default 1). A row per seed and a summary: deaths, the seconds to the death, Logan's lowest
hits, his farthest from the nearest pet, the covers and how far their tiles were from the pets, when the first cover
walk arrived (`cover1_s`; live 10.3 s in), the covers that stayed by the pets (`stays`: "staying by them"), through the
teleporter (on Maus 3) or not, the damage on him by source.
--alt tries other choices by monkeypatching the runner here (the repo's runner is unchanged); several with commas:
  nocover   no cover walk on the way in (Team.cover off inside walk_in_fight): Logan stands with the pets
  potion    a heal potion below --heal-at between a cover walk's steps (its Mover guard), as drink_on_the_run
(The cover within --vet-range - 1 of the pets, once `--alt near`, is the runner's own since 2026-11-06:
tamer.COVER_NEAR_PETS; `--set tamer.COVER_NEAR_PETS=False` for the cover before it.)
--no-save: no world save. --harness DIR: run another checkout's harness (a before/after pair on the same script).
"""
import ast
import contextlib
import io
import os
import re
import sys
import tempfile

ARGS = sys.argv[1:]
EXTRA_ARGS = ARGS[ARGS.index("--") + 1:] if "--" in ARGS else []
ARGS = ARGS[:ARGS.index("--")] if "--" in ARGS else ARGS
HARNESS = os.path.abspath(ARGS[ARGS.index("--harness") + 1]) if "--harness" in ARGS \
    else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS)
SETS = [ARGS[i + 1] for i, a in enumerate(ARGS) if a == "--set"]


def seeds(arg):
    if "-" in arg:
        a, b = arg.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(s) for s in arg.split(",")]


POS = [a for i, a in enumerate(ARGS) if not a.startswith("--")
       and (i == 0 or ARGS[i - 1] not in ("--harness", "--set", "--alt", "--trace"))]
SEEDS = seeds(POS[-1]) if POS else [1]
SHOW_LOG = "--log" in ARGS
ALTS = set(ARGS[ARGS.index("--alt") + 1].split(",")) if "--alt" in ARGS else set()
LATE_PHASE = "--late" in ARGS
SAVE = "--no-save" not in ARGS and not LATE_PHASE
TRACE = ARGS[ARGS.index("--trace") + 1] if "--trace" in ARGS else None   # record each run's trace (sim/trace.py) here

import agent_link  # noqa: E402
import loop_hunt  # noqa: E402
import memory  # noqa: E402
import tamer  # noqa: E402
from agent_link import Abort  # noqa: E402
from sim import clock as simclock  # noqa: E402
from sim import pets as simpets  # noqa: E402
from sim import run as simrun  # noqa: E402
from sim import trace  # noqa: E402
from sim.link import SEND_COST_S, SimLink  # noqa: E402
from sim.scenario import load  # noqa: E402
from sim.world import World, cheb  # noqa: E402

for s in SETS:
    name, value = s.split("=", 1)
    mod, attr = name.rsplit(".", 1)
    m = sys.modules.get(mod) or __import__(mod)
    setattr(m, attr, ast.literal_eval(value))

STAMP = trace.stamp()
EXTRA = ["--heal-in-place", "--vet-range", "5", "--crawl-learn-s", "180", "--human", "off"]
TELEPORTER, ARRIVAL = (5522, 637), (5199, 737)
# where the idle monsters stood; the calamity 0x00154807 and the abomination 0x001548B2 where they stood when they woke
# (24.48 and 28.41: they had walked there idle from (5510, 646) and (5513, 643), first seen at 20.24 and 22.75)
IDLE = [("calamity", (5509, 650, 2), None, 1.0, False),                  # 0x00154807, the one on him at the end
        ("abomination", (5517, 643, -10), None, 1.0, False),             # 0x001548B2
        ("abomination", (5515, 636, -10), None, 1.0, False),             # 0x00154F6A
        ("abomination", (5532, 662, -10), None, 1.0, False),             # 0x00154B71
        ("insane reanimator", (5534, 647, -10), None, 1.0, False),       # 0x00154D67
        ("reanimator zealot", (5529, 676, -10), None, 1.0, False)]       # 0x00131B86
if not LATE_PHASE:
    # t 0 = 23:37:10.10: the -17 at 10.08 (the calamity at (5497, 675)), the runner's "attacked on the way in" at 10.1
    START, HITS, STAM, HURT_AGO = (5507, 668, -10), 83, 23, 0.02
    PETS, PET_BARS = {"Bussin": (5507, 669), "AlaFlambe": (5507, 670)}, {}
    # (kind, tile, on whom in war mode or None for idle, its bar, stands there throughout)
    SPAWNS = [("calamity", (5497, 675, -10), "Logan", 0.93, True),            # 0x0015522E
              ("calamity", (5511, 667, -8), "Logan", 0.70, True),             # 0x001546EB, the pets' kill at 21.45
              ("insane reanimator", (5502, 658, 5), None, 1.0, False)] + IDLE  # 0x00154782 on its ledge, war 16.96
    POTION_AGO = None                                  # ready: the last at 23:33:58
    LATE = ("calamity", (5500, 676, -10), 6.87)        # 0x00153AF2 into view at 23:37:16.97, then on AlaFlambe from 18.61
else:
    # t 0 = 23:37:21.11: "+38", "You drink a healing potion." (its double-click 21.05), Logan on the first cover's tile
    START, HITS, STAM, HURT_AGO = (5506, 663, 5), 72, 25, 0.9       # the -27 at 20.22
    PETS, PET_BARS = {"Bussin": (5507, 669), "AlaFlambe": (5507, 669)}, {"AlaFlambe": 0.90}
    SPAWNS = [("calamity", (5497, 675, -10), "AlaFlambe", 0.90, True),        # 0x0015522E, its bolts at her 20.89, 25.15
              ("calamity", (5500, 676, -10), "AlaFlambe", 1.0, True),         # 0x00153AF2, at her from 19.17
              ("insane reanimator", (5502, 658, 5), "Logan", 1.0, False)] + IDLE   # its fireball at Logan 23.06
    POTION_AGO, LATE = 0.06, None
LATE_PARK = (5440, 676, -10)                       # where the late calamity waits out of everyone's view until then
ENTANGLE = (0.02, 1.15, "Your feet stick to a strange substance!")   # live 23:37:10.12-11.27
SAVE_AT, SAVE_S = 2.29, 4.24                       # live 23:37:12.39 "The world is saving" - 16.63 "World save complete"
# ...and as live 0.06 s after Logan first stood on (5507, 664) (12.33, the cover's 4th step; its 5th, a turn NW, went out
# at 12.45 in the save), when that comes within SAVE_SHIFT_S of SAVE_AT
SAVE_TILE, SAVE_AFTER_S, SAVE_SHIFT_S = (5507, 664), 0.06, 1.0
CONFIRM_TIMEOUT_S = 3.0                            # proxy.CONFIRM_TIMEOUT_S: a walk unconfirmed this long is rejected
COVER = re.compile(r"cover: the .* shoots us here; to \((\d+), (\d+)\)")


class SaveLink(SimLink):
    """SimLink with one world save, as live 23:37:12.39-16.63: from `start` (the clock's elapsed seconds) for `length`
    the server is frozen (no tick, nothing sent) and keeps our packets, answering them in order at its end; a walk sent
    in it stays in flight until the proxy's CONFIRM_TIMEOUT_S rejection (live "agent walk seq 31 got no confirm" at
    15.48), and its confirm comes after the save (16.66). The save lines go out on the wire as live, and save_t0 follows
    them as agent_link.Link's does. The world's clock runs `length` behind ours after it."""

    def __init__(self, world, clock, start, length):
        self.save_start, self.save_end = start, start + length
        self.off, self.saving, self.saved, self.held, self.walk_t = 0.0, False, False, [], None
        super().__init__(world, clock)
        clock.on_advance = self.advance

    def advance(self, now):
        if not self.saved and now >= self.save_start:
            if not self.saving:
                self.world.run_until(self.save_start)
                self.saving = True
                self.world._sys("The world is saving, please wait.")
            if now < self.save_end:
                return
            self.saving, self.saved, self.off = False, True, self.save_end - self.save_start
            self.world._sys(f"World save complete. The entire process took {self.save_end - self.save_start:.1f} "
                            f"seconds.")
            held, self.held, self.walk_t = self.held, [], None
            for pkt in held:
                self.world.c2s(pkt)
        self.world.run_until(now - self.off)

    def _pump(self):
        n = len(self.events)
        super()._pump()
        for ev in self.events[n:]:
            kind = agent_link.world_save(ev)
            if kind == "start":
                self.save_t0 = self.clock.monotonic()
            elif kind == "done":
                self.save_t0 = None

    def send(self, pkt: bytes) -> str:
        if not self.saving:
            return super().send(pkt)
        if pkt[0] == 0x02 and len(pkt) == 7:
            self.seq = self.seq % 255 + 1
            pkt = pkt[:2] + bytes([self.seq]) + pkt[3:]
            self.walk_t = self.clock.elapsed
        self.sent.append((self.clock.time(), pkt))
        self.rt.feed_packet("c2s", pkt)
        self.held.append(pkt)
        self._pump()
        self.clock.advance(SEND_COST_S)
        self._pump()
        return "OK"

    def _movement(self) -> dict:
        mv = super()._movement()
        if self.saving and self.walk_t is not None and self.clock.elapsed - self.walk_t < CONFIRM_TIMEOUT_S:
            mv["inflight"] = 1
        return mv


def patch(loop):
    """--alt: the alternatives on this HuntLoop (the runner's code unchanged)."""
    team = loop.team
    if "nocover" in ALTS:
        orig = team.cover
        team.cover = lambda st: False if loop.walk_fighting else orig(st)
    if "potion" in ALTS:
        orig_cover2, orig_drink = team.cover, loop.drink_on_the_run
        in_cover = [False]

        def cover2(st):
            in_cover[0] = True
            try:
                return orig_cover2(st)
            finally:
                in_cover[0] = False

        def drink(st):
            if in_cover[0] and not loop._drinking:
                me = st["world"]["self"]
                if me.get("hits") is not None and me.get("hits_max") and loop.frac(me) < loop.args.heal_at \
                        and loop.potions.ready(loop_hunt.time.monotonic()):
                    loop._drinking = True
                    try:
                        return loop.heal(st, potions=True)
                    finally:
                        loop._drinking = False
            return orig_drink(st)
        team.cover, loop.drink_on_the_run = cover2, drink


class Log(io.StringIO):
    """The runner's stdout; each cover's tile measured against the pets as it is written, and when the first cover walk
    arrived (live 10.3 s in: the Entangle, the save and its wait)."""

    def __init__(self, w, clock):
        super().__init__()
        self.w, self.clock, self.covers, self.arrived = w, clock, [], None

    def write(self, s):
        for x, y in COVER.findall(s):
            pets = [p for p in self.w.pets() if p.alive and not p.ghost]
            self.covers.append(((int(x), int(y)), min((cheb((int(x), int(y)), p.pos) for p in pets), default=None)))
        if self.arrived is None and "cover: arrived" in s:
            self.arrived = round(self.clock.elapsed, 1)
        return super().write(s)


def one(seed):
    sc = load()
    simpets.select(sc, "phoenix_incubus", None, None)     # the live team: AlaFlambe (the tank) and Bussin
    sc["spawns"] = [{"key": k, "at": list(at), "respawn_s": [99999, 99999]} for k, at, *_ in SPAWNS] \
        + ([{"key": LATE[0], "at": list(LATE_PARK), "respawn_s": [99999, 99999]}] if LATE else [])
    sc["defaults"]["spawn_spread"] = sc["defaults"]["wander_per_s"] = 0
    sc["player"]["start"] = list(START)
    w = World(sc, seed=seed)
    clock = simclock.SimClock(epoch=simrun.EPOCH)
    link = SaveLink(w, clock, SAVE_AT, SAVE_S) if SAVE else SimLink(w, clock)
    tracer = trace.Tracer(w, trace.path_for(TRACE, STAMP, "fight2337", seed), label="fight2337", seed=seed,
                          epoch=simrun.EPOCH) if TRACE else None
    pl = w.player
    pl.hits, pl.stam, pl.d = HITS, STAM, 0
    pets = {p.name: p for p in w.pets()}
    for name, xy in PETS.items():
        pets[name].x, pets[name].y = xy
        pets[name].hits = round(pets[name].hits_max * PET_BARS.get(name, 1.0))
    who = {"Logan": pl, **pets}
    mobs = sorted((m for m in w.mobs.values() if m.kind == "monster"), key=lambda m: m.spawn)
    late = mobs[-1] if LATE else None
    for (key, at, on, share, stays), m in zip(SPAWNS, mobs):
        m.hits = round(m.hits_max * share)
        if on is not None:
            m.target, m.threat = who[on].serial, {who[on].serial: 50}
            w._warm(m, True)
        if stays:
            m.spec = {**m.spec, "step_s": 999.0}
            m.next_step = 1e9
    if late is not None:
        late.spec, late.next_step = {**late.spec, "step_s": 999.0}, 1e9
    link.send(tamer.command("all follow me" if not LATE_PHASE else "all guard me"))   # live on follow from the landing
    db = os.path.join(tempfile.mkdtemp(prefix="fight_"), "harness.db")
    args = loop_hunt.arg_parser().parse_args(simrun.runner_args(w.sc, EXTRA + EXTRA_ARGS, 10, at_hunt=False)
                                             + ["--memory", db, "--seed", str(seed)])
    hits = []
    orig_hurt = w.hurt

    def hurt(victim, amount, by, kind="melee", *a, **kw):
        before = victim.hits
        r = orig_hurt(victim, amount, by, kind, *a, **kw)
        if victim is pl and before != victim.hits:
            hits.append((round(clock.elapsed, 2), by.key if by is not None else kind, round(before - victim.hits)))
        return r
    w.hurt = hurt
    low, far, died, events = [pl.hits], [0], [None], {"late": LATE is None, "entangled": LATE_PHASE}
    world_tick = clock.on_advance

    def watch(now):
        if SAVE and not link.saving and not link.saved and not events.get("save_set") \
                and pl.pos[:2] == SAVE_TILE and abs(now - SAVE_AT) <= SAVE_SHIFT_S:
            events["save_set"] = True
            link.save_start, link.save_end = now + SAVE_AFTER_S, now + SAVE_AFTER_S + SAVE_S
        if not events["entangled"] and now >= ENTANGLE[0]:
            events["entangled"] = True
            w._root(pl, ENTANGLE[1], ENTANGLE[2])
        if not events["late"] and now >= LATE[2]:
            events["late"] = True
            late.x, late.y, late.z = LATE[1]
            late.home = LATE[1]
        world_tick(now)
        low[0] = min(low[0], pl.hits)
        if pl.alive and not pl.ghost and cheb(pl.pos, (*ARRIVAL, 0)) > 5:
            near = [cheb(pl.pos, p.pos) for p in w.pets() if p.alive and not p.ghost]
            if near:
                far[0] = max(far[0], min(near))
        if died[0] is None and pl.ghost:
            died[0] = round(now, 1)
    clock.on_advance = watch
    patched = simclock.install(clock)
    store = memory.Memory(db)
    out = Log(w, clock)
    why = None
    try:
        with contextlib.redirect_stdout(tracer.tee(out) if tracer else out):
            loop = loop_hunt.HuntLoop(link, store, args)
            loop.team.setup(loop.state())
            patch(loop)
            now = loop_hunt.time.monotonic()
            if POTION_AGO is not None:
                loop.potions.last = now - POTION_AGO
            try:
                # live the hits fell 100 -> 83 at 10.08 (--late: 61 -> 34 at 20.22, then the potion to 72)
                loop.walking_in, loop.walk_hits, loop.walk_hurt_t = True, 100 if not LATE_PHASE else HITS, now - HURT_AGO
                loop.walk_in_fight(f"to the teleporter at {TELEPORTER[0]},{TELEPORTER[1]}")
                loop.walk_in([TELEPORTER])
            except Abort as e:
                why = str(e)[:80]
    finally:
        clock.on_advance = world_tick
        store.close()
        simclock.uninstall(patched)
    logl = out.getvalue().splitlines()
    by = {}
    for _, src, n in hits:
        by[src] = by.get(src, 0) + n
    res = {"seed": seed, "dead": w.metrics["deaths"], "dead_s": died[0], "low": round(low[0]), "far": far[0],
           "covers": len(out.covers), "cover_far": max((d for _, d in out.covers if d is not None), default=0),
           "cover1_s": out.arrived, "stays": sum("staying by them" in ln for ln in logl),
           "taken": sum(h[2] for h in hits), "by": by, "through": cheb(pl.pos, (*ARRIVAL, 0)) <= 5 and not pl.ghost,
           "why": why, "s": round(clock.elapsed), "log": logl, "hits": hits}
    if tracer is not None:
        tracer.close({k: v for k, v in res.items() if k not in ("log", "hits")})
    return res


if __name__ == "__main__":
    rows = []
    for s in SEEDS:
        r = one(s)
        log, hl = r.pop("log"), r.pop("hits")
        rows.append(r)
        print(r)
        if SHOW_LOG:
            print("    HITS", hl)
            print("\n".join("    " + line[:170] for line in log))
    n = len(rows)
    if n > 1:
        tot = {}
        for r in rows:
            for k, v in r["by"].items():
                tot[k] = tot.get(k, 0) + v
        dead_s = sorted(r["dead_s"] for r in rows if r["dead_s"] is not None)
        c1 = sorted(r["cover1_s"] for r in rows if r["cover1_s"] is not None)
        print(f"all {n}{' --late' if LATE_PHASE else ''}{' --alt ' + ','.join(sorted(ALTS)) if ALTS else ''}"
              f"{'' if SAVE or LATE_PHASE else ' --no-save'}{' ' + ' '.join(SETS) if SETS else ''}"
              f"{' -- ' + ' '.join(EXTRA_ARGS) if EXTRA_ARGS else ''}: deaths {sum(r['dead'] for r in rows)} (median "
              f"{dead_s[len(dead_s) // 2] if dead_s else '-'} s in), through {sum(r['through'] for r in rows)}, lowest "
              f"hits median {sorted(r['low'] for r in rows)[n // 2]}, under 20 {sum(r['low'] < 20 for r in rows)}, "
              f"farthest from the pets median {sorted(r['far'] for r in rows)[n // 2]} (max {max(r['far'] for r in rows)})"
              f", covers {sum(r['covers'] for r in rows)} (a tile 9+ off the pets in {sum(r['cover_far'] >= 9 for r in rows)}"
              f" runs), the first arrived median {c1[len(c1) // 2] if c1 else '-'} s in, stays by the pets "
              f"{sum(r['stays'] for r in rows)}, damage on Logan {sum(r['taken'] for r in rows)} "
              f"{dict(sorted(tot.items(), key=lambda kv: -kv[1]))}")

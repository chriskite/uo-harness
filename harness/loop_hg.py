"""Hunting Grounds runner: spends the character's Hunting Permits one 15-minute run after another (the deer event,
docs/research/HUNTING_GROUNDS.md; user request 2026-11-22: "a runner that can run Hunting Grounds in a loop as long
as I have hunting permits available").

One run:
  1. In Prevalia (an ejected run lands 50-180 tiles from the bank): walk to the Hunting Official, double-click him,
     press "Enter Hunting Grounds" (one permit spent, the 15-minute clock starts).
  2. In the Grounds the plan (hunting_grounds.plan via GroundsLog.status, the same advice the viz shows) says where to
     hunt and which bucks are worth a trophy slot:
     - roam: run (visible) to the zone and through it, cells not swept lately first, until a buck worth shooting
       comes into view;
     - stalk: hide (the Hiding skill always succeeds in the Grounds), stealth-walk (a running step reveals) to the
       weapon's range, stand still 2 s, double-click the buck (the shot);
     - carve: run to the corpse, carve it once the server allows (3-4 s after the kill), hide again 3 s later;
     - return: run to the Game Warden and turn the trophies in when the slots are full or the run home only just
       fits the time left.
  3. The permit expires (900 s) or the run ends at the warden with no time left; the server ejects us to town.
Then the next permit, until the backpack has none, `ctl stop` (now) / `ctl stop --after-trip` (after this run), the
agent gate's break or the daily cap comes up (no run is started that would run into it); then it recalls home
(homes.json: our own book's "Logan's House" rune; --no-home stays in town).

Every action is one the hotbar, a Razor hotkey or a double-click gives a player (ANTICHEAT.md §8): skills by id,
double-clicks on things in view, gump buttons found by their label. Nothing is said. War mode is never used.

Episodes (docs/MEMORY.md): one row per run, loop "hg": score, trophies, points lost at the expiry, shots, kills,
grazes, misses, instance. The simulator (harness/sim/run_hg.py, docs/SIM.md "Hunting Grounds simulator") runs this
class unchanged against a simulated server.

Run:  python harness/loop_hg.py [--min-points N] [--max-runs N] [--roam run|stealth]   (or `ctl run hg -- ...`)
"""
import argparse
import os
import random
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import actions  # noqa: E402
import combat  # noqa: E402
import escape as escape_mod  # noqa: E402
import home as home_mod  # noqa: E402
import hunting_grounds as hg  # noqa: E402
import task_wrap  # noqa: E402
from agent_link import Abort, GatePaused, Link, Mover, WalkFailed, cheb, label_since, log, look_at, serial_of  # noqa: E402
from errand_bank import gating  # noqa: E402
from humanize import PROFILES, Human  # noqa: E402
from memory import DEFAULT_DB, Memory  # noqa: E402
from room import labelled_button  # noqa: E402

SYSTEM = (None, 0, 0xFFFFFFFF)
HUMAN_BODIES = (400, 401)
FELLED = "You have felled a deer! Carve it to claim a trophy."
HIDDEN = "You hide and may now move in stealth."
HIDE_WAIT = "You must wait a moment before hiding again."
CARVE_WAIT = "You must wait a moment before carving again."
CARVE_FULL = "Players can only carry 5 trophies at a time."
SHOT_STILL = "You must remain stationary for a moment before shooting."
SHOT_WAIT = "You must wait a few moments before shooting again."
SHOT_RANGE = "That is out of range of your weapon."
SHOT_FULL = "You are already holding 5 trophies."
SHOT_NOT = "You cannot shoot that."
FIRE_CURSOR = "Select a target to fire at."
UNHINDERED = "You are no longer hindered."
HINDERED_MAX_S = 1.2         # the hold ends 0.07-0.2 s after the kill lands and 0.6-0.8 s after a carve
FOLLOWERS = "Players are not allowed to bring followers into this event."
TROPHY = re.compile(r"^\d+ point trophy added to carried trophies\.$")
SKILL_HIDING = 21
# Measured timings (docs/research/HUNTING_GROUNDS.md §2, harness/data/sim/hunting_grounds.json "timing"), each with
# a small margin over the server's rule so a retry is the exception
STATIONARY_S = 2.3            # the server refuses a shot within 2 s of a step
SHOT_COOLDOWN_S = 7.2         # a shot 6.0 s after the last was refused, 7.7 s went through
SHOT_RESULT_S = 3.0           # target response -> felled: 1.29-1.75 s; no word by then: a miss
CARVE_AFTER_KILL_S = 3.9      # carves were refused up to ~4.2 s after the kill and went through from ~3.2 s
CARVE_RETRY_S = 0.5
CARVE_LINE_S = 3.5           # the corpse vanishes 0.8 s into a carve and the trophy line comes ~2.3 s later
HIDE_AFTER_HIDE_S = 5.05
HIDE_AFTER_CARVE_S = 3.05
HIDE_TRY_S = 6.0              # give up hiding for this stalk after this long (shoot from where we are)
STALK_MAX_S = 30.0            # a buck we can't get a shot at in this long is left alone
NERVOUS_WAIT_S = 6.0          # a nervous buck in range: wait this long for it to settle (calm after 10 s alone)
SKIP_S = 60.0                 # a buck given up on (fled, refused) isn't stalked again for this long
CELL = 16                     # roam memory: the zone in CELL x CELL squares, the least lately swept first
UNREACHABLE_S = 300.0        # a roam waypoint with no route: its cell counts as swept this far into the future
ROAM_MIN, ROAM_MAX = 18, 40   # a roam waypoint this far off (Chebyshev)
ROAM_EDGE = 6                 # waypoints keep this far inside the region
ZONE_SNEAK_R = 25             # hidden this close outside the zone: walk in hidden (roam), don't run
NPC_R = 3                     # double-click an NPC from this close
GUMP_WAIT_S = 3.0
START_WAIT_S = 8.0
BREAK_MARGIN_S = 17 * 60      # a run (15 min) plus the walk in: none is started this close to a break or the cap
FINISH_CHECK_S = 2.0


def text_of(ev) -> str | None:
    return ev.get("text") if ev.get("ev") == "speech_heard" else None


class HGLoop:
    def __init__(self, link, memory: Memory, args):
        self.link = link
        self.memory = memory
        self.args = args
        self.human = Human(args.human, seed=args.seed, log=log)
        self.rng = random.Random(args.seed)
        self.mover = Mover(link, memory, self.human, max_blocked=args.max_blocked, guard=self.guard,
                           use_map=not args.no_map)
        self.mover.no_idle = lambda st: self.g is not None     # in the Grounds every second is points: no idling
        self.grounds = hg.GroundsLog()
        self.g = None                 # GroundsLog.status of the last read (None outside a run)
        self.fed = 0                  # link.events fed to GroundsLog
        self.heard = 0                # link.events checked for restriction text
        self.me_serial = None
        self.task_id = os.environ.get("UO_TASK_ID")
        self.finish = None            # why no further run is started ("stop requested", "break due", ...)
        self._finish_checked = 0.0
        self.started = time.time()
        self.totals = {"runs": 0, "score": 0, "trophies": 0, "lost": 0, "shots": 0, "kills": 0}
        self.row = None               # this run's episode row
        self.hidden = False
        self.hide_ready = 0.0         # monotonic: the next hide the server accepts
        self.shot_ready = 0.0
        self.last_move = 0.0          # monotonic: our last step (a shot needs STATIONARY_S still)
        self.corpse = None            # (corpse serial, kill monotonic, (x, y)) to carve
        self.skip = {}                # buck serial -> monotonic until which it isn't stalked
        self.swept = {}               # roam cell -> monotonic of our last pass
        self.halt = None
        self.roam_failed = False      # the last roam walk found no route: pick a random waypoint next
        self.trophy_t = 0.0           # monotonic: the last trophy line (our carve or the client's)

    # ------------------------------------------------------------------ state
    def update(self, st):
        """Feed the new events to GroundsLog and read the run (self.g) from this state."""
        if self.me_serial is None:
            self.me_serial = st["movement"]["self_serial"]
        new = self.link.events[self.fed:]
        self.fed = len(self.link.events)
        now = time.time()
        for ev in new:
            if ev.get("ev") != "speech_heard":
                continue
            if ev.get("text") == HIDDEN:
                self.hidden = True
            elif TROPHY.match(ev.get("text") or ""):
                # a carve, ours or the client's own: ClassicUO's auto-open corpses (Logan's profile, range 2)
                # double-clicks our kill as we walk up, and in the Grounds that double-click carves. Hide is refused
                # for 3 s after any carve (live 2026-11-23 10:32-10:38: 46 refused hides, one burst per kill)
                self.hide_ready = max(self.hide_ready, time.monotonic() + HIDE_AFTER_CARVE_S)
                self.trophy_t = time.monotonic()
        self.grounds.feed(new, now)
        pos = st["movement"]["pos"]
        me = (pos[0], pos[1]) if pos is not None else None
        mobs = st["world"]["mobiles"]

        def where(serial):
            m = mobs.get(f"0x{serial:08X}")
            return (m["x"], m["y"]) if m and m.get("x") is not None else None
        self.g = self.grounds.status(now, me, where, min_points=self.args.min_points)
        return st

    def read(self):
        return self.update(self.link.state())

    def me(self, st=None) -> tuple:
        return tuple(self.link.pos(st)[:2])

    def mob_at(self, st, serial):
        m = st["world"]["mobiles"].get(f"0x{serial:08X}")
        return (m["x"], m["y"], m.get("z") or 0) if m and m.get("x") is not None else None

    def said(self, mark: int, *texts) -> str | None:
        """The first of `texts` (prefixes) said to us (system or over our head) since link.events[mark]."""
        for ev in self.link.events[mark:]:
            t = text_of(ev)
            if t and ev.get("serial") in SYSTEM + (self.me_serial,) and any(t.startswith(x) for x in texts):
                return t
        return None

    def doing(self, kind: str, text: str, target=None, serial=None):
        g = self.g or {}
        self.link.intent(text[:200], kind, list(target) if target is not None else None, loop="hg",
                         target_serial=serial, trip=self.totals["runs"] or None,
                         tally={"score": self.totals["score"] + int(g.get("score") or 0),
                                "trophies": self.totals["trophies"] + int(g.get("turned_in") or 0),
                                "runs": self.totals["runs"]},
                         started=self.started)

    # ------------------------------------------------------------------ guards
    def guard(self, st):
        """Every state read in a walk: the run's events, the restriction text (ANTICHEAT.md §8.4), death."""
        self.update(st)
        for ev in self.link.events[self.heard:]:
            if ev.get("ev") == "speech_heard" and ev.get("serial") in SYSTEM and gating(ev.get("text") or ""):
                self.halt = "a server restriction message"
                self.memory.juncture("hg", "server_restriction", f"Server restriction: {ev.get('text')!r}"[:300],
                                     "urgent", {"text": ev.get("text")})
                raise Abort(f"server restriction message: {ev.get('text')!r}")
        self.heard = len(self.link.events)
        if st["world"]["self"].get("dead"):
            self.halt = "dead"
            self.memory.juncture("hg", "death", "Died in the Hunting Grounds", "urgent", {})
            raise Abort("dead")
        return False

    def check_finish(self, st):
        """No new run once `ctl stop --after-trip` asked, a break is due, or the break / daily cap is closer than a run."""
        gate = st.get("gate") or {}
        why = None
        left = gate.get("break_starts_in_s")
        if gate.get("break_due_at") is not None or (left is not None and left < BREAK_MARGIN_S):
            why = "break due"
        left = gate.get("daily_remaining_s")
        if left is not None and left < BREAK_MARGIN_S:
            why = "daily cap near"
        if self.task_id and time.monotonic() - self._finish_checked >= FINISH_CHECK_S:
            self._finish_checked = time.monotonic()
            if task_wrap.finish_requested(self.memory, self.task_id):
                why = "stop requested"
        if why and not self.finish:
            log(f"{why}: no further run")
            self.finish = why

    # ------------------------------------------------------------------ town
    def permits(self, st) -> int:
        items = st["world"]["items"]
        pack = combat.backpack(items, st["movement"]["self_serial"])
        if pack is None:
            return 0
        return sum(int(it.get("amount") or 1) for _, it in combat.pack_items(items, pack)
                   if it.get("graphic") == hg.PERMIT_GRAPHIC)

    def find_npc(self, near, words: str, radius: int = 8) -> int:
        """Serial of the NPC by `near` whose click label has `words`: one already labelled, else single-click the human
        bodies there, nearest first (agent_link.find_banker's way)."""
        st = self.read()
        for key, text in (st["world"].get("labels") or {}).items():
            m = st["world"]["mobiles"].get(key)
            if words in (text or "").lower() and m and m.get("x") is not None and cheb(near, (m["x"], m["y"])) <= radius:
                return serial_of(key)
        cands = sorted((cheb(near, (m["x"], m["y"])), serial_of(k), m.get("name"))
                       for k, m in st["world"]["mobiles"].items()
                       if m.get("x") is not None and m.get("graphic") in HUMAN_BODIES
                       and serial_of(k) != self.me_serial and cheb(near, (m["x"], m["y"])) <= radius)
        for _, serial, name in cands[:6]:
            self.human.wait("use")
            mark = len(self.link.events)
            look_at(self.link, serial, known_name=bool(name))
            self.link.wait(lambda s: label_since(self.link, serial, mark) is not None, timeout=1.5)
            if words in (label_since(self.link, serial, mark) or "").lower():
                return serial
        raise Abort(f"no {words} by {near}")

    def press(self, serial: int, gump_id: int, label: str, fallback: int, done) -> bool:
        """Double-click `serial`, wait for its gump, press the button labelled `label`; True once `done(mark)` holds."""
        for attempt in range(3):
            self.human.wait("use")
            mark = len(self.link.events)
            self.link.act(actions.dclick(serial))
            st = self.link.wait(lambda s: any(e.get("ev") == "gump_open" and serial_of(e.get("gump_id")) == gump_id
                                              for e in self.link.events[mark:]), timeout=GUMP_WAIT_S)
            if st is None:
                log(f"no gump from 0x{serial:08X} (try {attempt + 1})")
                continue
            gump = [e for e in self.link.events[mark:] if e.get("ev") == "gump_open"
                    and serial_of(e.get("gump_id")) == gump_id][-1]
            button = labelled_button(gump, label) or fallback
            self.human.wait("menu")
            mark = len(self.link.events)
            self.link.act(actions.gump_reply(serial_of(gump["serial"]), gump_id, button, gump.get("layout") or "",
                                             gump.get("lines") or []))
            if self.link.wait(lambda s: done(mark), timeout=START_WAIT_S) is not None:
                return True
            if (refusal := self.said(mark, FOLLOWERS)) is not None:
                self.memory.juncture("hg", "followers", "Hunting Grounds: stable the followers first", "attention",
                                     {"text": refusal})
                raise Abort("followers out: the Hunting Grounds don't allow them (stable them)")
        return False

    def enter(self) -> bool:
        """From town into a run: walk to the Hunting Official, spend a permit. False when there is none left."""
        st = self.read()
        n = self.permits(st)
        if n == 0:
            return False
        here = self.me(st)
        if cheb(here, hg.OFFICIAL_AT) > 400:
            raise Abort(f"not in Prevalia ({here}): start by the Hunting Official at {hg.OFFICIAL_AT}")
        self.doing("to_official", f"To the Hunting Official ({n} permit{'s' if n != 1 else ''})", hg.OFFICIAL_AT)
        self.mover.walk_to(lambda: hg.OFFICIAL_AT, NPC_R + 2, "to the official")
        official = self.find_npc(hg.OFFICIAL_AT, "hunting official")
        m = self.mob_at(self.link.state(), official)
        if m is not None and cheb(self.me(), m[:2]) > NPC_R:
            self.mover.walk_to(lambda: (self.mob_at(self.link.state(), official) or m)[:2], NPC_R, "to the official")
        self.doing("enter", "Entering the Hunting Grounds")
        ok = self.press(official, hg.OFFICIAL_GUMP, "Enter Hunting Grounds", 2,
                        lambda mark: self.said(mark, "*You now have 15 minutes") is not None
                        or any(e.get("ev") == "gump_open" and serial_of(e.get("gump_id")) == hg.HOTBAR_GUMP
                               for e in self.link.events[mark:]))
        if not ok:
            raise Abort("the Hunting Official didn't let us in")
        self.start_run()
        return True

    # ------------------------------------------------------------------ the run
    def start_run(self):
        self.totals["runs"] += 1
        self.hidden = False
        self.corpse = None
        self.skip.clear()
        self.swept.clear()
        self.shot_ready = self.hide_ready = 0.0
        self.row = {"run": self.totals["runs"], "t_start": time.time(), "instance": None, "score": 0, "trophies": 0,
                    "lost": 0, "shots": 0, "kills": 0, "grazes": 0, "misses": 0, "turn_ins": 0}
        log(f"run {self.totals['runs']}: in")

    def end_run(self, last):
        """The run is over (expired, ejected): its row. Trophies still held were lost."""
        r = self.row
        if r is None:
            return
        last = last or {}
        r.update({"t_end": time.time(), "score": int(last.get("score") or 0),
                  "trophies": int(last.get("turned_in") or 0), "lost": int(last.get("held_points") or 0),
                  "instance": last.get("instance")})
        for k in ("score", "trophies", "lost"):
            self.totals[k] += r[k]
        log(f"run {r['run']} over: {r['score']} points, {r['trophies']} trophies"
            + (f", {r['lost']} points lost at the expiry" if r["lost"] else ""))
        self.memory.episode("hg", r)
        self.row = None

    def play(self):
        """One run, from the entry to its end."""
        last = None
        while True:
            self.read()
            if self.g is None:
                if last is not None:
                    break
                time.sleep(0.2)      # the hotbar or the start line hasn't come yet
                continue
            last = self.g
            if self.row["instance"] is None:
                self.row["instance"] = self.g["instance"]
            p = self.g["plan"]
            if p is None:
                time.sleep(0.2)
                continue
            if self.corpse is not None:
                self.carve()
                continue
            if p["phase"] == "return":
                self.turn_in()
                continue
            if p["phase"] == "done":
                self.doing("wait", "Nothing more fits before the end: waiting it out")
                time.sleep(1.0)
                continue
            buck = self.pick()
            if buck is not None:
                self.stalk(serial_of(buck["serial"]))
                continue
            self.roam()
        self.end_run(last)

    def pick(self):
        """The buck to stalk: worth a trophy slot and not fleeing. Calm ones (the plan's verdict "shoot") first, then
        the rest ("wait": nervous, or no mood heard yet), each by points then distance (the status's order). A buck
        not yet calm is still worth closing in on hidden: the stalk waits for it to settle in range."""
        now = time.monotonic()
        bucks = [b for b in (self.g or {}).get("bucks") or []
                 if b["verdict"] in ("shoot", "wait") and self.skip.get(serial_of(b["serial"]), 0) <= now]
        if not self.args.stalk_unsettled:
            bucks = [b for b in bucks if b["verdict"] == "shoot"]
        return next((b for b in bucks if b["verdict"] == "shoot"), bucks[0] if bucks else None)

    def buck(self, serial):
        return next((b for b in (self.g or {}).get("bucks") or [] if serial_of(b["serial"]) == serial), None)

    # ---- roam
    def cell(self, t) -> tuple:
        return (t[0] // CELL, t[1] // CELL)

    def waypoint(self) -> tuple:
        """Where to run next: into the zone (out / north), else a tile ROAM_MIN-ROAM_MAX off in the zone whose cell we
        swept longest ago."""
        g = self.g
        x0, y0, x1, y1 = g["region"]
        zy0, zy1 = g["plan"]["zone"]
        here = self.me()
        if g["plan"]["phase"] in ("out", "north") and not self.roam_failed:
            y = zy0 + 10 if g["plan"]["phase"] == "out" else zy1 - 10
            return (min(max(here[0], x0 + 30), x1 - 30), min(max(y, zy0), zy1))
        now = time.monotonic()
        best = None
        for _ in range(40):
            t = (self.rng.randint(x0 + ROAM_EDGE, x1 - ROAM_EDGE),
                 self.rng.randint(max(zy0, y0 + ROAM_EDGE), min(zy1, y1 - ROAM_EDGE)))
            d = cheb(here, t)
            if not ROAM_MIN <= d <= ROAM_MAX:
                continue
            score = now - self.swept.get(self.cell(t), -1e6) + self.rng.random()
            if best is None or score > best[0]:
                best = (score, t)
        return best[1] if best else (min(max(here[0], x0 + 30), x1 - 30), (zy0 + zy1) // 2)

    def roam_stop(self, st, rehide: bool = False):
        """A roam walk's stop: the plan changed, a buck worth stalking came into view, or (`rehide`: a visible run in the
        zone) the hide is ready again or any deer came into view (stop and hide before it sees us)."""
        self.update(st)
        if self.g is None:
            return "over"
        p = self.g["plan"]
        if p is None or p["phase"] in ("return", "done") or self.corpse is not None:
            return "plan"
        self.swept[self.cell(self.me(st))] = time.monotonic()
        if self.pick() is not None:
            return "buck"
        if rehide and (time.monotonic() >= self.hide_ready or self.g["bucks"]):
            return "hide"
        return None

    def roam(self):
        """Run to the zone; in it (--roam stealth, the default) sweep it hidden. Not hidden yet: hide when the skill is
        ready; while its cooldown runs, stand still if a deer is in view (a visible player makes calm deer bolt in ~3 s,
        and a run toward them is the worst of it), else run until it is ready. Live 2026-11-23 10:15-10:24 the runner
        ran visible in the zone between stalks: 24 of 44 hides were broken by a running step within a second, and the
        next stalk then waited out the 5 s hide cooldown standing in view while the buck fled."""
        to = self.waypoint()
        y0, y1 = self.g["plan"]["zone"]
        here_y = self.me()[1]
        # just outside the zone (a stalk drifted over its edge, or the zone moved with the clock) and hidden: walk back
        # in hidden rather than break the stealth for a few tiles of running
        near = self.hidden and max(y0 - here_y, here_y - y1) <= ZONE_SNEAK_R
        zone = self.g["plan"]["phase"] == "hunt" or near
        stealth = zone and self.args.roam == "stealth"
        if stealth and not self.hidden:
            if time.monotonic() >= self.hide_ready or self.g["bucks"]:
                self.doing("hide", f"Hiding in the zone (y {y0}-{y1})")
                if not self.hide():
                    log("roam: couldn't hide; running on")
                    stealth = False
            else:
                self.doing("roam", f"Running through the zone until the hide is ready (y {y0}-{y1})", to)
                self.walk(lambda: to, 3, "roam", stop=lambda s: self.roam_stop(s, rehide=True), run=True)
                return
        self.doing("roam", f"{'Stalking through' if stealth else 'Running to'} the zone (y {y0}-{y1})", to)
        if self.walk(lambda: to, 3, "roam", stop=self.roam_stop, run=not stealth) == "failed":
            # no route there (water, a wall): not that cell again for a while, and no fixed waypoint next time
            self.swept[self.cell(to)] = time.monotonic() + UNREACHABLE_S
            self.roam_failed = True
        else:
            self.roam_failed = False

    def walk(self, center_fn, radius, label, stop=None, run=True):
        before = self.mover.steps
        try:
            return self.mover.walk_to(center_fn, radius, label, stop=stop, run=run)
        except WalkFailed as e:
            log(f"{label}: {e}")
            if label == "roam":
                self.swept[self.cell(self.me())] = time.monotonic()
            return "failed"
        finally:
            if self.mover.steps != before:
                self.last_move = time.monotonic()
                if run:
                    self.hidden = False

    # ---- stalk and shoot
    def hide(self) -> bool:
        """Hide (the Hiding skill; always succeeds in the Grounds); False when the server keeps refusing for HIDE_TRY_S."""
        if self.hidden:
            return True
        end = time.monotonic() + HIDE_TRY_S
        while time.monotonic() < end:
            wait = self.hide_ready - time.monotonic()
            if wait > 0:
                time.sleep(min(wait, end - time.monotonic()))
            self.human.wait("hotkey")
            mark = len(self.link.events)
            self.link.act(actions.use_skill(SKILL_HIDING))
            self.link.wait(lambda s: self.said(mark, HIDDEN, HIDE_WAIT) is not None, timeout=1.5)
            said = self.said(mark, HIDDEN, HIDE_WAIT)
            if said == HIDDEN:
                self.hidden = True
                self.hide_ready = time.monotonic() + HIDE_AFTER_HIDE_S
                return True
            self.hide_ready = time.monotonic() + (0.6 if said else 1.0)
        return False

    def stalk(self, serial: int):
        """Hide, close in walking to the weapon's range, stand still, shoot."""
        t0 = time.monotonic()
        b = self.buck(serial)
        if b is None:
            return
        self.doing("stalk", f"Stalking a {b['points']}-point buck ({b['dist']} tiles)", (b["x"], b["y"]), serial)
        self.hide()
        settle_t = None
        while True:
            st = self.read()
            b = self.buck(serial)
            if self.g is None or b is None:
                return
            if b["verdict"] == "skip" or b["mood"] == "fleeing" or self.g["plan"]["phase"] in ("return", "done"):
                self.skip[serial] = time.monotonic() + SKIP_S
                return
            if time.monotonic() - t0 > STALK_MAX_S:
                log(f"stalk: no shot at 0x{serial:08X} in {STALK_MAX_S:.0f} s")
                self.skip[serial] = time.monotonic() + SKIP_S
                return
            # shoot from `shot_margin` inside the range (a buck past it when the shot lands ~1.5 s later is never
            # killed), at most from the view's edge: the Rifle (range 20) shoots anything in view
            want = max(6, hg.shot_r(self.g["weapon"]["range"], self.args.shot_margin))
            if b["dist"] > want:
                self.walk(lambda: self.buck_xy(serial), want, "stalk",
                          stop=lambda s: self.stalk_stop(s, serial, want), run=False)
                continue
            still = time.monotonic() - self.last_move
            if still < STATIONARY_S:
                time.sleep(STATIONARY_S - still)
                continue
            if b["mood"] != "calm":
                settle_t = settle_t or time.monotonic()
                if time.monotonic() - settle_t > NERVOUS_WAIT_S:
                    self.skip[serial] = time.monotonic() + SKIP_S
                    return
                time.sleep(0.5)
                continue
            if time.monotonic() < self.shot_ready:
                time.sleep(self.shot_ready - time.monotonic())
                continue
            if self.shoot(serial, b, st) != "again":
                return

    def buck_xy(self, serial):
        b = self.buck(serial)
        return (b["x"], b["y"]) if b else self.me()

    def stalk_stop(self, st, serial, want):
        self.update(st)
        b = self.buck(serial)
        if self.g is None or b is None or b["mood"] == "fleeing" or b["verdict"] == "skip":
            return "lost"
        if self.g["plan"]["phase"] in ("return", "done"):
            return "plan"
        if b["dist"] <= want:
            return "in range"
        return None

    def shoot(self, serial: int, b, st) -> str:
        """Double-click the buck (the shot; the Shoot cursor answered if the server asks for a target). "again": a
        refusal to try once more from here; else the shot's end ("kill", "graze", "miss", "refused")."""
        self.doing("shoot", f"Shooting a {b['points']}-point buck ({b['dist']} tiles, {b['mood']})", (b["x"], b["y"]),
                   serial)
        self.human.wait("hotkey_aim")
        mark = len(self.link.events)
        self.link.act(actions.dclick(serial))
        sent = time.monotonic()
        answered = False
        outcome = None
        while time.monotonic() - sent < SHOT_RESULT_S:
            st = self.read()
            cur = st["world"].get("target") or {}
            if cur.get("active") and not answered and self.said(mark, FIRE_CURSOR) is not None:
                at = self.mob_at(st, serial)
                if at is None:
                    break
                self.human.wait("hotkey_aim")
                self.link.act(actions.target_object(cur["cursor_id"], serial, at[0], at[1], at[2], hg_body(st, serial),
                                                    cur.get("cursor_type") or 0))
                answered = True
            refusal = self.said(mark, SHOT_STILL, SHOT_WAIT, SHOT_RANGE, SHOT_FULL, SHOT_NOT)
            if refusal is not None:
                log(f"shot refused: {refusal}")
                if refusal.startswith(SHOT_STILL):
                    self.last_move = time.monotonic()
                    return "again"
                if refusal.startswith(SHOT_WAIT):
                    self.shot_ready = time.monotonic() + 1.0
                    return "again"
                if refusal.startswith(SHOT_RANGE):
                    return "again"
                self.skip[serial] = time.monotonic() + SKIP_S
                return "refused"
            if self.said(mark, FELLED) is not None:
                outcome = "kill"
                break
            if any(e.get("ev") == "speech_heard" and e.get("serial") == serial and e.get("text") == "*grazed*"
                   for e in self.link.events[mark:]):
                outcome = "graze"
                break
            time.sleep(0.1)
        self.hidden = False
        self.shot_ready = sent + SHOT_COOLDOWN_S
        self.row["shots"] += 1
        self.totals["shots"] += 1
        if outcome == "kill":
            self.row["kills"] += 1
            self.totals["kills"] += 1
            felled_t = time.monotonic()
            self.unhindered(mark)
            self.link.wait(lambda s: self.corpse_of(serial, mark) is not None, timeout=1.5)
            self.corpse = (self.corpse_of(serial, mark), felled_t, (b["x"], b["y"]), serial)
            log(f"felled a {b['points']}-point buck")
            return "kill"
        self.row["grazes" if outcome == "graze" else "misses"] += 1
        self.skip[serial] = time.monotonic() + SKIP_S
        log(f"{'grazed' if outcome == 'graze' else 'missed'} the {b['points']}-point buck")
        return outcome or "miss"

    def corpse_of(self, deer: int, mark: int):
        for e in self.link.events[mark:]:
            if e.get("ev") == "mobile_death" and e.get("serial") == deer and e.get("corpse") is not None:
                return serial_of(e["corpse"])
        return None

    # ---- carve
    def unhindered(self, mark: int):
        """Wait (at most HINDERED_MAX_S) for the server to let us move again after a shot or a carve: a step sent while
        held is refused, and the Mover counts it as a blocked tile."""
        self.link.wait(lambda s: self.said(mark, UNHINDERED) is not None, timeout=HINDERED_MAX_S)

    def carve(self):
        """Run to the kill, carve it once the server allows (CARVE_AFTER_KILL_S), within the corpse's 30 s."""
        corpse, kill_t, at, deer = self.corpse
        self.corpse = None
        if corpse is None:
            st = self.read()
            corpse = self.corpse_near(st, at)
            if corpse is None:
                log("carve: no corpse found")
                return
        self.doing("carve", "Carving the trophy", at, corpse)
        self.walk(lambda: self.item_xy(corpse) or at, 1, "to the corpse", run=True,
                  stop=lambda s: "gone" if self.item_xy(corpse, s) is None else None)
        end = kill_t + 25.0
        while time.monotonic() < end:
            st = self.read()
            if self.g is None:
                return
            if self.item_xy(corpse, st) is None:
                # gone: a carve started (the corpse goes 0.8 s into it, the trophy line ~2.3 s later; the client's
                # auto-open does the double-click as we walk up, live 2026-11-23 10:32:47.9-50.2) or it decayed.
                # Wait for the trophy before anything else: hide is refused all through a carve
                self.link.wait(lambda s: self.update(s) is not None and self.trophy_t > kill_t, timeout=CARVE_LINE_S)
                return
            if cheb(self.me(st), self.item_xy(corpse, st)) > 2:
                self.walk(lambda: self.item_xy(corpse) or at, 1, "to the corpse", run=True)
                continue
            wait = kill_t + CARVE_AFTER_KILL_S - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self.human.wait("hotkey")
            mark = len(self.link.events)
            self.link.act(actions.dclick(corpse))
            self.link.wait(lambda s: self.said(mark, CARVE_WAIT, CARVE_FULL) is not None
                           or any(TROPHY.match(text_of(e) or "") for e in self.link.events[mark:]), timeout=1.5)
            if any(TROPHY.match(text_of(e) or "") for e in self.link.events[mark:]):
                self.hide_ready = max(self.hide_ready, time.monotonic() + HIDE_AFTER_CARVE_S)
                self.unhindered(mark)
                self.last_move = time.monotonic()
                return
            if self.said(mark, CARVE_FULL) is not None:
                return
            time.sleep(CARVE_RETRY_S)

    def item_xy(self, serial, st=None):
        st = st or self.link.state()
        it = st["world"]["items"].get(f"0x{serial:08X}")
        return (it["x"], it["y"]) if it and it.get("x") is not None else None

    def corpse_near(self, st, at):
        best = None
        for k, it in st["world"]["items"].items():
            if it.get("graphic") == combat.CORPSE_GRAPHIC and it.get("x") is not None and it.get("container") is None:
                d = cheb(at, (it["x"], it["y"]))
                if d <= 3 and (best is None or d < best[0]):
                    best = (d, serial_of(k))
        return best[1] if best else None

    # ---- the warden
    def turn_in(self):
        lodge = tuple(self.g["lodge"])
        held, pts = len(self.g["held"]), self.g["held_points"]
        self.doing("return", f"Back to the warden with {held} troph{'y' if held == 1 else 'ies'} ({pts} points)", lodge)
        self.mover.teleporter_tiles(0).add((lodge[0], lodge[1] + hg.GATE_DY))   # the exit moongate ends the run
        self.walk(lambda: lodge, 2, "to the warden", run=True, stop=lambda s: "over" if self.update(s) and self.g is None
                  else None)
        if self.g is None:
            return
        warden = self.find_npc(lodge, "game warden", radius=4)
        self.doing("turn_in", "Turning the trophies in")
        ok = self.press(warden, hg.WARDEN_GUMP, "Turn in Trophies", 1,
                        lambda mark: self.said(mark, "Ammunition resupplied") is not None)
        if ok:
            self.row["turn_ins"] += 1
            self.read()
        else:
            log("the warden didn't take the trophies")

    # ------------------------------------------------------------------ the loop
    def run(self):
        st = self.link.wait(lambda s: s["movement"]["pos"] is not None and s["movement"]["self_serial"] is not None,
                            timeout=10.0)
        if st is None:
            raise Abort("proxy has no player position yet (log in first)")
        self.update(st)
        while True:
            st = self.read()
            self.guard(st)
            if self.g is not None:          # started inside a run (a restart): play it out
                if self.row is None:
                    self.start_run()
                self.play()
                continue
            self.check_finish(st)
            if self.finish or (self.args.max_runs and self.totals["runs"] >= self.args.max_runs):
                break
            if not self.enter():
                log("no Hunting Permit left")
                self.finish = self.finish or "no permits left"
                break
            self.play()
        t = self.totals
        log(f"hunting grounds done ({self.finish or 'max runs'}): {t['runs']} run(s), {t['score']} points, "
            f"{t['trophies']} trophies, {t['lost']} points lost at expiries, {t['shots']} shots, {t['kills']} kills")
        if self.args.home:
            self.go_home()
        self.doing("done", f"Finished: {t['runs']} run(s), {t['score']} points ({self.finish or 'max runs'})"
                           + (", home" if self.args.home else ""))

    def go_home(self):
        """Recall to the character's home (harness/data/homes.json: Logan Wolf's house) with our own runebook or rune
        tome's home rune (`rune`, "Logan's House"), as the user does after his runs (user 2026-11-23: "return to
        Logan's house after the last hunting permit is consumed, or max runs is reached"). The books in the pack are
        tried in escape.find_books' order (tomes first); one without that rune raises RecallError before any cast."""
        st = self.read()
        me = st["world"].get("self") or {}
        house = home_mod.for_character(me.get("name"))
        if house is None:
            log(f"no home for {me.get('name')!r} in homes.json: staying here")
            return
        if home_mod.at_home(self.link.pos(st), me.get("map") or 0, house):
            log("already home")
            return
        self.doing("home", f"Recalling home ({house['rune']})", house["landing"][:2])
        books = escape_mod.find_books(st["world"], st["movement"]["self_serial"])
        if not books:
            raise Abort("no runebook or rune tome in the backpack to recall home with")
        why = None
        for book, kind in books:
            self.human.wait("use")
            try:
                res = escape_mod.escape(escape_mod.LinkIO(self.link), book, rune=house.get("rune"), log=log, attempts=3)
            except escape_mod.RecallError as e:
                log(f"home: {kind} 0x{book:08X}: {e}")
                why = str(e)
                continue
            st = self.link.state()
            pos = self.link.pos(st)
            facet = (st["world"].get("self") or {}).get("map") or 0
            self.memory.job_event("hg", "travel", {"leg": "home", "book": f"0x{book:08X}", **res}, facet=facet,
                                  x=pos[0], y=pos[1])
            if res.get("ok") and home_mod.at_home(pos, facet, house):
                log(f"home at {tuple(pos[:2])}")
                return
            why = res.get("failure") or f"landed at {tuple(pos[:2])}, not at home"
        raise Abort(f"couldn't recall home: {why}")


def hg_body(st, serial) -> int:
    m = st["world"]["mobiles"].get(f"0x{serial:08X}") or {}
    return m.get("graphic") or 234


def arg_parser():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--shot-margin", type=int, default=hg.SHOT_MARGIN,
                    help="shoot from this many tiles inside the weapon's range, at most from the view's 18 (a buck that "
                         "steps past the range during the shot's flight is grazed or missed, never killed: 0 of 8 live)")
    ap.add_argument("--min-points", type=int, default=None,
                    help="shoot calm bucks worth at least this while trophy slots are the limit (default: the plan's)")
    ap.add_argument("--stalk-unsettled", action=argparse.BooleanOptionalAction, default=True,
                    help="also close in (hidden) on worth-it bucks not yet heard calm, and shoot once they settle")
    ap.add_argument("--roam", choices=("run", "stealth"), default="stealth",
                    help="sweep the zone running (visible) or stealth-walking (hidden, half speed)")
    ap.add_argument("--home", action=argparse.BooleanOptionalAction, default=True,
                    help="recall home (homes.json, our own book's home rune) once the runs are over")
    ap.add_argument("--max-runs", type=int, default=0, help="stop after this many runs (0: until the permits run out)")
    ap.add_argument("--max-blocked", type=int, default=12)
    ap.add_argument("--human", choices=sorted(PROFILES), default="normal")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--no-map", action="store_true", help="plan on walk memory only (tests)")
    ap.add_argument("--control-port", type=int, default=25941)
    ap.add_argument("--state-port", type=int, default=25942)
    ap.add_argument("--char", default=None)
    ap.add_argument("--memory", default=DEFAULT_DB)
    return ap


def stop_intent(loop, text):
    try:
        loop.doing("stopped", text[:200])
    except (OSError, ValueError, Abort):
        pass


def main():
    from loop_hunt import watch_stop
    args = arg_parser().parse_args()
    memory = Memory(args.memory)
    link = Link(args.control_port, args.state_port, char=args.char)
    link.end_on_pause = True
    loop = HGLoop(link, memory, args)
    watch_stop(args.memory)
    code = 0
    try:
        loop.run()
    except Abort as e:
        log(f"ABORTED: {e}")
        code = 1
        stop_intent(loop, f"Stopped: {e}")
    except GatePaused as e:
        log(f"stopped: {e}")
        code = 1
        stop_intent(loop, "Stopped: the agent gate was paused")
    except KeyboardInterrupt:
        log("stopped (ctl stop)")
        code = 1
        stop_intent(loop, "Stopped")
    except BaseException as e:
        stop_intent(loop, f"Crashed: {type(e).__name__}: {e}")
        raise
    finally:
        if loop.row is not None:
            loop.end_run(loop.g)
        memory.close()
    sys.exit(code)


if __name__ == "__main__":
    main()

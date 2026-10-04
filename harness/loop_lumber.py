"""Lumber loop runner (docs/LUMBER_LOOP.md §3, §6, §12, §13).

One trip = harvest trees → convert logs to boards → walk to the banker →
say "bank" → drop the boards into the bank box. The run ends at the bank. No
rental room and no deed creation (user decision 2026-10-01: bank the boards).

Harvest = Smart Harvest (user decision 2026-10-04; docs/PLAN.md "Smart Harvest
for lumber"): double-click the hatchet and answer its cursor with ourselves
(self_target); the server chops a tree within its reach that still has wood. The
tree list only chooses where to stand (next_stand); the runner stays until the
server says "You do not see any harvestable resources nearby.", then moves on.
The reach is unmeasured (SMART_RANGE); every stand is a `stand` job event for
measuring it.

Where: one lumber spot (--spot; harness/lumber_opt.py load_spots: the seed
spots in harness/data/lumber_spots.json plus the memory store's lumber_spots
rows), merged over the common knowledge in harness/data/loops/lumber.json
(mined from the user's demonstration: texts, captcha shape, conversion). The
overseer gets the spot, trip size and hatchet from `ctl lumber plan`.

What the loop learns lives in the harness memory store (harness/memory.py,
docs/MEMORY.md): attempts and yield per stand tile, trees out of wood (marked
when a stand says nothing nearby) or unreachable, every attempt, and one episode
row per trip, aborted trips included (outcome, why, timings, skill, hatchet, what
was still carried), which lumber_opt.py learns from. Walk memory is recorded by
the proxy.

Captcha (ANTICHEAT.md §8.8/§8.13). The real captcha is the
gump with lumber.json's id plus a text entry and the submit button. Who
answers it is the memory store's captcha mode, toggled in the viz header
(user decision 2026-10-01). "human", the default: the runner pauses and beeps
until a solve is observed in the client. "auto": captcha.solve reads the
digits from the gump layout (harness/captcha.py) and the runner answers with
the stock 0xB1; an unreadable layout or rejected answers fall back to the
human wait. The runner never replies to a gump without a reply button (the
decoys).

The runner answers no other gump, except that its Mover closes (button 0) the
gump of a moongate a route only passes over (agent_link.Mover.close_gate_gumps).
The only speech is "bank", and "guards" once inside a guard zone after a flight
with a hostile player within 12 tiles.

Guards: jittered pacing, overall timeout, HP loss, movement stall, the agent
gate (pause/break wait, kill/budget abort), bounded retries everywhere.

Threats (threats.py; LUMBER_LOOP.md §13): a monster close enough to flee from
gets an escape (walk beyond its flee radius, then harvest at the next stand out of
its reach). Damage from a single creature at healthy hits (--creature-recall-at)
gets a run: walk out of its reach (a ranged one's: 12 tiles + margin) and chop
on at a stand outside it; damage again soon after the walk-away
(--creature-rehit-s), low hits, two attackers or no escapes left recall home.
Trees within reach of a known-aggressive creature in view are left for later.
A player/red threat, or a monster that keeps coming stops the run. An abort
while harvesting converts the carried logs first when that is safe, so carried
wood is boards. A break announced by the agent gate (break_due) ends the trip
early: convert, bank, exit 0 for `ctl break`.

Tracking (tracking.py; LUMBER_LOOP.md §13 "Tracking reds"): Hunting murderer
players is kept on for the whole run (at the start, after travel, between chops
whenever the server's lines or the buff say it's off; one try per --track-retry-s).
A murderer hit within --track-react-range tiles at a pvp spot is a red sighting:
recall home like for a red in view. Every murderer hit is a `pk_seen` event
(source 'tracking'); only near ones count as the spot's hazard.

Run:  python harness/loop_lumber.py --spot horseshoe_bay [--trips 1] [--logs-per-trip 15]
"""
import argparse
import json
import math
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import actions  # noqa: E402
from agent_link import (Abort, Link, Mover, bank_opened, cheb, containers_to_open, log, reach_z,  # noqa: E402
                        same_floor, serial_of)
import uomap  # noqa: E402
import nav  # noqa: E402
import escape as escape_mod  # noqa: E402
import guards  # noqa: E402
from humanize import PROFILES, Human  # noqa: E402
from uo.gumps import parse_layout  # noqa: E402
from memory import DEFAULT_DB, Memory  # noqa: E402
import ledger as ledger_mod  # noqa: E402
import threats  # noqa: E402
from speech_guard import SpeechGuard, staff_hints  # noqa: E402
import triage  # noqa: E402
import alerts  # noqa: E402
import lumber_opt  # noqa: E402
import travel_guard  # noqa: E402
import stationary  # noqa: E402
import places  # noqa: E402
import captcha  # noqa: E402
import combat  # noqa: E402
import tracking  # noqa: E402

RECALL_S = 2.0                # Recall cast time (docs/research/TRAVEL_DEATH.md)
NEXT_STAND_PLANS = 6          # nearest trees (straight line) whose stands next_stand() compares
# Smart Harvest's reach (Chebyshev tiles from our tile to a tree it may chop). UNMEASURED: to be
# measured on the first attended Smart Harvest trip (docs/PLAN.md "Smart Harvest for lumber"; the
# `stand` job events carry what the measurement needs). 1 is the only value the evidence proves:
# capture 20261001_214649 at 23:47, at (1905,2616) the one tree within 6 tiles stood at distance 1
# (1906,2617), it chopped and the server turned us to face it (0x77 dir SE). RunUO's by-hand chop
# reach is 2. It decides which trees a stand counts (next_stand) and which ones a "nothing nearby"
# marks out of wood (work_stand); too small only costs a walk to a stand that then says nothing
# nearby, too large would rule out trees that still have wood.
SMART_RANGE = 1
SURVEY_R = 6                  # trees within this many tiles of a stand go into its `stand` event (the measurement)
DIR_NAMES = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
HOME_NEAR = 60               # tiles from the banker: close enough to walk instead of recalling home
# Coloured-wood success, e.g. "You chop some dullwood logs and put them in your backpack."
# (live 2026-10-02, Terran; unmatched it counted as an unknown outcome and aborted the trip)
COLORED_CHOP = re.compile(r"You chop some [a-z]+ logs and put them in your backpack\.$")
SPEECH_POLL_S = 1.0           # while paused for speech: state reads + all-clear checks
THREAT_MARGIN_S = 1.0         # reaction + packet latency on top of the cast
# Waits stay watchful (LUMBER_LOOP.md §13 "Blind waits"; live 2026-10-03, Bastet: the red came into
# view during the chop's 2.1 s aim pause and the recall went out 2.5 s after sight): every human pause
# and every wait for a server result reads the state and runs the threat checks at least this often.
LOOK_EVERY_S = 0.2
BLIND_PAUSES = frozenset({"drag"})   # lift -> drop: nothing may come between (an item on the cursor)
TOOL_CURSOR_WAIT_S = 0.5      # a threat right after the hatchet's dclick: its cursor still comes, then is cancelled

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "harness", "data")
LAYER_BACKPACK = 0x15
HATCHETS = (0x0F43, 0x0F44)
LOGS = tuple(range(0x1BDD, 0x1BE3))
BOARDS = (0x1BD7,)
DROP_AUTO = 0x7FFFFFFF        # client drop-into-container auto-position (demo)
ESCAPE_MARGIN = 2             # an escape ends this many tiles beyond the monster's reach (flee radius or spell range)
ESCAPES_PER_TRIP = 3          # monster escapes per trip (runs from damage included); one more threat stops the run
PACK_DEPTH_MAX = 16           # container nesting bound when looking for the hatchet
FLEE_MAX_MOVES = 400          # a guard flight's step bound (guards.FLEE_MAX_DIST tiles and detours)
FLEE_ARRIVAL_WAIT_S = 1.5     # after a flight arrives: how long its 500112 may still come (data.confirmed)


def h(v) -> int:
    return int(v, 16) if isinstance(v, str) else int(v)


def self_target(st: dict, cur: dict) -> bytes:
    """Smart Harvest's answer to the hatchet's cursor: 0x6C on ourselves with combat.target_self
    (the fields `ctl act target self` sends): our serial, our tile's x/y and z as movement truth
    has them, our body as the graphic. Byte-equal to the stock client's in capture
    20261001_214649 at 23:20 and 23:47 (test_loop_lumber.py unit_capture_smart_harvest). The z is
    movement's, not the world model's self z, which said 10 there while the client sent 0."""
    return combat.target_self(cur, st["movement"]["self_serial"], st["movement"]["pos"],
                              (st["world"].get("self") or {}).get("body"))


def facing_to(a, b) -> int:
    """The direction 0..7 from tile a to tile b as RunUO's Utility.GetDirection quantizes it (a
    straight one within a 3:1 slope, else the diagonal) [INFERENCE for Outlands: RunUO's harvest
    turns the harvester to face the target; live 23:47 the turn pointed at the one tree there]."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    adx, ady = abs(dx), abs(dy)
    if adx >= ady * 3:
        return 2 if dx > 0 else 6
    if ady >= adx * 3:
        return 4 if dy > 0 else 0
    if dx > 0:
        return 3 if dy > 0 else 1
    return 5 if dy > 0 else 7


LEG_KEYS = ("leg", "kind", "method", "book", "witcher_rune", "ok", "attempts", "s", "walk_s", "failure",
            "charges", "mana_used", "reagents_used")


def leg_summary(data: dict) -> dict:
    """A travel leg's job-event data reduced to the trip row's `travel` entry, plus
    `tries`: each cast's method, failure, and elapsed time ("charge", null, 2.2)."""
    out = {k: data[k] for k in LEG_KEYS if data.get(k) is not None}
    out["tries"] = [[t.get("method"), t.get("failure"), t.get("elapsed_s")] for t in data.get("tries") or []]
    return out


def pack_depth(items: dict, container: int, me: int, pack: int) -> int | None:
    """How deep an item whose container is `container` sits: 0 worn (on `me`),
    1 in the backpack, 2 in a bag in it, ... None elsewhere (the bank box,
    the ground, a container the world model doesn't know)."""
    if container == me:
        return 0
    depth = 1
    while container != pack:
        ent = items.get(f"0x{container:08X}")
        if ent is None or ent.get("container") is None or depth > PACK_DEPTH_MAX:
            return None
        container = serial_of(ent["container"])
        depth += 1
    return depth


def in_hand(world: dict, item: int, me: int) -> bool:
    """Whether `item` is worn by `me` (its container is us) in a state-port world."""
    it = (world.get("items") or {}).get(f"0x{item:08X}") or {}
    return it.get("container") is not None and serial_of(it["container"]) == me


def row_hatchet(start: dict | None, worn_chopping: bool | None) -> dict | None:
    """The trip row's `hatchet` (lumber_opt.character's entry at the trip start) with
    `worn` as it was when the last chop's cursor came, when the trip chopped: the server
    equips a packed hatchet on the double-click, and every cast (Magic Reflection at
    the bank, a recall) puts it back in the pack, so at the start it is often packed
    (live 2026-10-03, trip 1: worn False at 21:43:43, in hand from the first chop at
    21:44:39 to the end). `worn_at_start` keeps the start reading."""
    if start is None or worn_chopping is None:
        return start
    return {**start, "worn": worn_chopping, "worn_at_start": start.get("worn")}


def hit_verdict(*, hits, hits_max, recall_at: float, attackers: list, players: list, escapes: int,
                since_run_s: float | None, rehit_s: float, walking: bool = False,
                can_escape: bool = True) -> str | None:
    """Damage taken (LUMBER_LOOP.md §13 "Running from a creature"): why it must send
    us home (monster_stop), or None to run from it (walk out of its reach and chop on
    at a stand outside it; while already walking away: walk on). Home when a hostile
    player is in view, nothing in view could have hit us, two or more creatures
    could have, hits are below recall_at of max, it came within rehit_s of arriving
    from the last walk-away, or no escape is left (a speech hold, ESCAPES_PER_TRIP)."""
    if players:
        return f"taking damage with a hostile player in view ({players[0]})"
    if not attackers:
        return "taking damage, no creature in view that could have hit us"
    if len(attackers) >= 2:
        return f"taking damage, {len(attackers)} creatures attacking"
    if hits is None or not hits_max:
        return "taking damage (hits unknown)"
    if hits < recall_at * hits_max:
        return f"taking damage, hits {hits}/{hits_max} below {recall_at:.0%}"
    if walking:
        return None
    if since_run_s is not None and since_run_s <= rehit_s:
        return f"still taking damage {since_run_s:.1f} s after the walk-away"
    if not can_escape:
        return "taking damage during a speech hold: no escape"
    if escapes >= ESCAPES_PER_TRIP:
        return f"taking damage after {escapes} escapes this trip already"
    return None


class Unsafe(Abort):
    """Stop at once, without converting the carried logs first: a player or red
    threat, death, or a server restriction (captcha)."""


class Escape(Exception):
    """Monster threats to walk away from (LumberLoop.escape), then carry on; `hit`:
    the monster_hit episode when damage sent us (LumberLoop.creature_hit)."""

    def __init__(self, monsters, summary: str, hit: dict | None = None):
        super().__init__(summary)
        self.monsters = monsters      # [threats.Threat]
        self.summary = summary
        self.hit = hit


class InGuards(Exception):
    """The server said we're under the guards' protection (cliloc 500112) during a flight."""


def alert(sound: bool = True):
    """Captcha alert sound (alerts.handoff): the human is to solve it."""
    alerts.handoff(sound)


class LumberLoop:
    def __init__(self, link: Link, memory: Memory, know: dict, args):
        self.link = link
        self.k = know
        self.args = args
        self.deadline = time.monotonic() + args.timeout
        self.start_hits = None
        self.human = Human(args.human, seed=args.seed, fast=args.human_fast, log=log, sleep=self.pause)
        self.mover = Mover(link, memory, self.human, max_blocked=args.max_blocked,
                           guard=self.check_guards, doors=True, use_map=not args.no_map)
        self.still = stationary.Stationary(self.mover, self.human)
        self.memory = memory
        self.stats = {}
        self.trip_n = None
        # a creature is a threat when it's in war mode, murderer-red or known aggressive
        # (threats.Params, plus every body the store has seen hostile: travel_guard.learned_params);
        # a wandering goat isn't
        self.watch = threats.Watch(travel_guard.learned_params(memory))
        self.last_threats = None
        self.seen_hostiles = set()
        self.ledger = ledger_mod.Ledger()
        self._intent = None          # last reported (kind, text, target), restored after a captcha
        self.speech = SpeechGuard()  # a character speaking near us hands control to the overseer
        self.triage = triage.Triage(args.triage_url, log=log)  # Laya verdict per line (shadow + escalate)
        self.mode = "work"           # "work" | "escape" (walking away) | "salvage" (converting before a stop)
        #                              | "flee" (running to the guards)
        self.holding = False         # in a speech hold: the overseer has control
        self.escapes = 0             # monster escapes this trip
        self.danger = {}             # serial -> ((x, y), tiles): monsters escaped from this trip and their reach;
        #                              ("was", serial) -> the same around where it was when we escaped (fixed)
        self.run_arrived = None      # time.time() when the last walk-away ended (--creature-rehit-s)
        self.creature = self.new_creature_tally()   # this trip's creature cost: the episode row's `creature`
        self.avoided = set()         # (tree, creature serial) pairs logged as left alone (next_stand)
        self.swingers = {}           # attacker serial -> time of its latest swing or spell at us since the last escape
        self._swing_scan = 0         # link.events index scanned for swings and spells on us
        self.spelled = []            # (time, caster or None) of spells on us (threats.spell_on_us) not yet dealt with
        self.hit_by = []             # serials creature_hit blamed in this threat check (the junctures' `attackers`)
        self.hatchet_worn = None     # this trip: the hatchet in hand when a chop's cursor came (trip row hatchet.worn)
        self.break_due = False       # the agent gate announced a break (break_due)
        self.recall_book = None      # runebook / rune tome serial: the red escape (prepare_recall)
        self._attack_scan = 0        # link.events index scanned for "... is attacking you!"
        self._flee_mark = 0          # len(link.events) when the guard flight started
        self.facet = know["facet"]   # the spot's facet: tree records and candidates
        self.hatchets = lumber_opt.load_hatchets()
        self.want_hatchet = lumber_opt.parse_hatchet_spec(args.hatchet)   # --hatchet material[+quality]
        self.trip_t0 = None
        self.timing = {}             # this trip's walk_out_s / chop_s / tree_walk_s / lockout_s / stationary_s
        self.travel = []             # this trip's travel legs (travel_leg): the episode row's `travel`
        self.players_seen = {}       # serial -> name of every player in view this trip (crowding, threats)
        self.reagents0 = {}          # reagent counts in the pack at the trip start (supplies used)
        self.trk = tracking.Keeper(tracking.MURDERERS, args.track_retry_s)   # our own hunt, as the server reports it
        self._trk_scan = 0           # link.events index folded into self.trk
        self.afield = False          # at the spot (after go_out, until home): a tracking hit sends us home
        self.sighted = {}            # serial -> in react range when its tracking sighting was last logged
        self.counted = set()         # hostile serials whose sighting counts as the spot's hazard (lumber_opt)
        self.escaped = set()         # serials a tracking hit already recalled us away from
        self._looking = False        # in look(): a pause inside the threat checks sleeps plainly
        self._looked_t = -1e9        # monotonic time of the last threat check (check_threats)
        self._tool_sent = None       # (events mark, monotonic time) of a hatchet dclick whose cursor may still come
        self._cancelled = None       # cursor id of the last target cursor cancelled by drop_cursor
        self._sight = {}             # mobile key -> wall time it came into view (note_sightings: react_s)

    @staticmethod
    def new_creature_tally() -> dict:
        """The trip row's `creature`: escapes (walk-aways, runs from damage included), hits
        lost to creatures, whether a creature sent us home by recall and why it ended the
        trip (null when none did); runs (walk-aways after damage), hits (damage episodes)
        and avoided_trees (trees left alone near a known-aggressive creature)."""
        return {"escapes": 0, "hits_lost": 0, "recalled": False, "why": None,
                "runs": 0, "hits": 0, "avoided_trees": 0}

    def doing(self, kind: str, text: str, target=None):
        """Tell the visualizer what the agent is trying to do (proxy-side only)."""
        self._intent = (kind, text, target)
        self.link.intent(text, kind, target, loop="lumber", trip=self.trip_n, trips=self.args.trips)

    # ------------------------------------------------------------ guards
    def check_guards(self, st: dict):
        relaxed = self.mode in ("salvage", "flee")
        if not relaxed and time.monotonic() > self.deadline:
            raise Abort(f"overall timeout ({self.args.timeout}s)")
        mv = st["movement"]
        if mv["stalled"]:
            raise Abort(f"movement stalled ({mv['rejects_in_row']} walks rejected in a row)")
        self.check_gate(st)
        # The ledger first: a thief's grab and his notoriety change arrive together (live
        # 2026-10-03: 10 mandrake root gone 75 ms after "Caputo Wood" turned grey next to us),
        # and the threat check raises into the escape, so a loss read after it is never booked.
        self.check_ledger(st)
        self.check_threats(st)
        if self.mode == "work":
            self.check_speech(st)
        hits = st["world"]["self"].get("hits")
        if hits is not None:
            if self.start_hits is None:
                self.start_hits = hits
            elif hits < self.start_hits and not relaxed:
                raise Abort(f"hit points dropped ({self.start_hits} -> {hits}); stopping")

    def _where(self, st):
        pos = st["movement"]["pos"] or [None, None]
        return {"facet": st["world"]["self"].get("map"), "x": pos[0], "y": pos[1]}

    def check_gate(self, st):
        """The agent gate's break_due (harness/agent_gate.py, docs/OVERSEER.md):
        stop harvesting and finish this trip at the bank, so the overseer can
        start the break there (`ctl break`)."""
        gate = st.get("gate") or {}
        if gate.get("break_due_at") is not None and not self.break_due:
            self.break_due = True
            left = gate.get("break_starts_in_s")
            log("break due" + (f" (it starts in {left:.0f} s)" if left is not None else "")
                + ": ending the trip at the bank")

    # ------------------------------------------------------------ watchful waits
    def look(self, st=None):
        """The threat side of a state read (the main tick's ledger + check_threats,
        tracking included) for the waits in between: a threat raises from here as it
        would from check_guards. Inside the threat checks (a pause during their own
        walk) it does nothing."""
        if self._looking:
            return
        self._looking = True
        try:
            st = st or self.link.state()
            self.check_ledger(st)
            self.check_threats(st, escape=not self.holding)
        finally:
            self._looking = False

    def pause(self, seconds: float, kind: str):
        """Human.sleep: spend a human pause (`kind`) reading the state and checking for
        threats every LOOK_EVERY_S and once more at its end, right before the action
        it delays (live 2026-10-03: Bastet came into view during the chop's aim pause;
        a plain sleep there sent the recall 2.5 s after sight). A threat ends the pause
        at once by raising. A drag (BLIND_PAUSES) sleeps plainly."""
        if kind in BLIND_PAUSES or self._looking:
            time.sleep(seconds)
            return
        t0 = time.monotonic()
        end = t0 + seconds
        while True:
            now = time.monotonic()
            if now >= end or now - self._looked_t >= LOOK_EVERY_S:
                try:
                    self.look()
                except (Abort, Escape):
                    log(f"the {kind} pause cut short {now - t0:.2f} s into its {seconds:.2f} s")
                    raise
            now = time.monotonic()
            if now >= end:
                return
            time.sleep(min(LOOK_EVERY_S, end - now))

    def wait_for(self, pred, timeout: float):
        """link.wait for a server result with the threat checks on every read (look)."""
        return self.link.wait(lambda s: self.look(s) or pred(s), timeout)

    def drop_cursor(self) -> bool:
        """Before an escape: cancel a target cursor that is up (the chop's, the log
        target's, any other) with the client's Esc (0x6C cancel, as loop_hunt does;
        the proxy clears the client's copy), so the escape's double-click never goes
        out under our own cursor and no target is answered after the threat. A hatchet
        double-click whose cursor hasn't come yet gets up to TOOL_CURSOR_WAIT_S for it.
        True when a cursor was cancelled."""
        if self._tool_sent is not None:
            mark, sent = self._tool_sent
            self._tool_sent = None
            left = sent + TOOL_CURSOR_WAIT_S - time.monotonic()
            if left > 0 and self.cursor(mark) is None and self.real_captcha(mark) is None:
                self.link.wait(lambda s: self.cursor(mark) is not None, left, poll=0.03)
        cur = ((self.link.last or {}).get("world") or {}).get("target") or {}
        if not cur.get("active") or cur.get("cursor_id") is None or cur["cursor_id"] == self._cancelled:
            return False
        self.link.act(actions.target_cancel(cur["cursor_id"], cur.get("target_type") or 0,
                                            cur.get("cursor_type") or 0))
        self._cancelled = cur["cursor_id"]
        log(f"target cursor 0x{cur['cursor_id']:08X} cancelled (Esc) for the escape")
        return True

    def note_sightings(self, st):
        """Keep {mobile key: wall time first in view} for the mobiles in view: on the first
        read that shows one, its `seen_t` (the proxy's time of the latest packet about it,
        so at most one read interval late), else now; dropped when it leaves the view."""
        now, prev = time.time(), self._sight
        self._sight = {k: prev.get(k) or min(now, m.get("seen_t") or now)
                       for k, m in (st["world"].get("mobiles") or {}).items()}

    def sight_t(self, worst, swung: dict) -> float | None:
        """Wall time the threat was first seen: the worst threat or a swinger coming
        into view (note_sightings), or the first swing at us; None when none of them is
        known (a tracking hit beyond the view)."""
        serials = ([worst.serial] if worst is not None else []) + list(swung)
        ts = [self._sight[k] for k in (f"0x{s:08X}" for s in serials) if k in self._sight]
        ts += list(swung.values())
        return min(ts) if ts else None

    # ------------------------------------------------------------ threats
    def swung_at_us(self, st) -> dict:
        """{attacker serial: time} of 0x2F swings at us, and of spells on us whose
        effect names the caster (threats.spell_on_us), since the last escape, within
        the threat window (an escape clears the ones that caused it). Every spell on
        us, caster named or not, also goes to `spelled` until creature_hit deals with it."""
        me, ev, ts = self.self_serial(st), self.link.events, self.link.event_t
        for i in range(self._swing_scan, len(ev)):
            if ev[i].get("ev") == "swing" and ev[i].get("defender") == me:
                self.swingers[ev[i]["attacker"]] = ts[i]
                continue
            landed, caster = threats.spell_on_us(ev[i], me)
            if landed:
                self.spelled.append((ts[i], caster))
                if caster is not None:
                    self.swingers[caster] = ts[i]
        self._swing_scan = len(ev)
        lo = time.time() - self.watch.params.damage_window_s
        self.spelled = [s for s in self.spelled if s[0] >= lo]
        return {s: t for s, t in self.swingers.items() if t >= lo}

    def check_threats(self, st, escape: bool = True):
        """threats.py over every state read. Hostile players are logged once each
        (pk_seen). A flee-level threat posts an urgent `threat` juncture whose
        data.action says what follows:
          - a red in view, a flee-level player, a non-creature swinging at us, or a
            player named in "... is attacking you!": 'recall' when a recall book is
            ready (recall_out: escape to its default rune, then stop), else 'abort'
            at once (Unsafe)
          - damage taken (a hits drop; Outlands names no attacker) or a spell landing
            on us (threats.spell_on_us: its effect on us or the server's line, e.g.
            "Magic reflect removed.", no hits lost): creature_hit runs from one
            creature at healthy hits ('escape' with data.hit), else 'recall' home or
            'abort' (monster_stop)
          - only creatures (in flee range, or swinging at us): 'escape' (Escape:
            walk away and carry on, LumberLoop.escape), at most ESCAPES_PER_TRIP
            times a trip and never during a speech hold (escape=False); else 'abort'.
        While escaping, creatures are what we're walking away from (players still stop
        the run, damage only when creature_hit says so); while converting before a stop, only
        players and death count. With no recall (no book, or it failed) a player
        threat sends us running to the guards (flee_to_guards); during that flight
        only death and the server's 500112 count. Tracking hits are read here too
        (check_tracking): a near murderer hit is a red out of view."""
        self._looked_t = time.monotonic()
        self.hit_by = []
        self.track_observe(st)
        a = self.watch.update(st, recall_s=RECALL_S, margin_s=THREAT_MARGIN_S)
        self.last_threats = a
        self.note_sightings(st)
        if a.dead:
            self.died(st, "ghost body")
        if self.mode == "flee":
            if self.guards_entered(self._flee_mark):
                raise InGuards()
            return
        for t in a.threats:
            if t.hostile and t.player and t.serial not in self.seen_hostiles:
                self.seen_hostiles.add(t.serial)
                data = {**t.to_dict(), "source": "view", "counted": t.serial not in self.counted}
                self.counted.add(t.serial)
                self.memory.job_event("lumber", "pk_seen", data, **self._where(st))
            if t.player and t.distance >= 0:
                self.players_seen[t.serial] = t.name
        by_serial = {t.serial: t for t in a.threats}
        swung = self.swung_at_us(st)
        players = [t for t in a.flee if t.kind != "monster"]
        # A red in view is in reach whatever the ETA says (docs/PLAN.md "Red sighting":
        # Bastet's first hit came 4.6 s after sight, the straight-line ETA said 0.6 s).
        players += [t for t in a.threats if t.player and t.kind == "red" and t.distance >= 0
                    and t not in players]
        players += [t for t in self.attacked_by_players(st, a) if t not in players]
        aggressors = [s for s in swung if s not in by_serial or by_serial[s].kind != "monster"]
        monsters = [t for t in a.flee if t.kind == "monster"]
        monsters += [by_serial[s] for s in swung if s not in aggressors
                     and s not in {t.serial for t in monsters}]
        if players or aggressors:
            worst = players[0] if players else None
            why = self.recall_out(st, a, worst, swung) if self.recall_book is not None else "no recall book"
            if self.k["pvp"]:
                self.flee_to_guards(st, a, worst, swung, why)
            self.threat_stop(st, a, worst, swung, Unsafe, why)
        self.check_tracking(st, a, swung)
        if self.mode == "salvage":
            return
        if a.damage["lost"] > 0 or a.damage["damage_events"] > 0 or self.spelled:
            self.creature_hit(st, a, swung, monsters, escape)
        if not monsters or self.mode == "escape":
            return
        if not escape:
            self.monster_stop(st, a, monsters[0], swung, "speech hold: no escape")
        if self.escapes >= ESCAPES_PER_TRIP:
            self.monster_stop(st, a, monsters[0], swung, f"{self.escapes} escapes this trip already")
        raise Escape(monsters, self.post_threat(st, a, monsters[0], swung, "escape"))

    def creature_hit(self, st, a, swung, monsters, escape: bool):
        """Damage taken (LUMBER_LOOP.md §13 "Running from a creature"). Who hit us is
        inferred (threats.hit_attackers); hit_verdict says whether it sends us home
        (monster_stop: recall when far from home). Otherwise the damage is dealt with
        (Watch.acknowledge) and we run: Escape with the episode, so escape() walks out
        of the attacker's reach (a ranged one's, CREATURE_SPELL_RANGE + margin) and the
        harvest goes on at a stand outside it. While already walking away, we walk on.
        Each episode is a `monster_hit` job event; a sole attacker teaches its body
        (travel_guard.learn_hit: aggressive, and ranged when nothing was adjacent). With
        no candidate within its assumed reach, a creature we walked away from this trip
        that is still in view is taken to outrange it (its distance is learned as reach).
        A spell landing on us (swung_at_us: `spelled`) is a hit like a hits drop, even
        when Magic Reflection took it and no hits were lost; a caster its effect names
        is in `swung`, an unnamed one is found like a ranged hit."""
        spells, self.spelled = len(self.spelled), []
        attackers, ranged = threats.hit_attackers(a, self.watch.params, swung)
        if not attackers:
            attackers = [t for t in a.threats if t.serial in self.danger and t.kind == "monster"
                         and 0 <= t.distance <= self.watch.params.max_range]
        self.hit_by = [t.serial for t in attackers]
        me = st["world"]["self"]
        hits, hmax = me.get("hits"), me.get("hits_max")
        walking = self.mode == "escape"
        since_run = None if self.run_arrived is None else round(time.time() - self.run_arrived, 1)
        players = [t.name or f"0x{t.serial:08X}" for t in a.threats if t.player and t.hostile and t.distance >= 0]
        why = hit_verdict(hits=hits, hits_max=hmax, recall_at=self.args.creature_recall_at, attackers=attackers,
                          players=players, escapes=self.escapes, since_run_s=since_run,
                          rehit_s=self.args.creature_rehit_s, walking=walking, can_escape=escape)
        worst = attackers[0] if attackers else (monsters[0] if monsters else None)
        lost = a.damage["lost"]
        self.creature["hits"] += 1
        self.creature["hits_lost"] += lost
        hit = {"body": worst.body if worst else None, "name": worst.name if worst else None,
               "serial": f"0x{worst.serial:08X}" if worst else None, "distance": worst.distance if worst else None,
               "hits_lost": lost, "trip": self.trip_n, "spot": self.k["spot"]["id"],
               "hits": hits, "hits_max": hmax, "damage_events": a.damage["damage_events"], "spells": spells,
               "attackers": len(attackers), "attacker_serials": [f"0x{t.serial:08X}" for t in attackers],
               "ranged": ranged if attackers else None, "aggression": worst.aggression if worst else None,
               "escapes": self.escapes, "walking": walking, "since_run_s": since_run}
        if worst is not None:
            hit["reach"] = threats.creature_reach(worst.body, self.watch.params, ranged=ranged)
        if why:
            recalled = self.creature["recalled"]
            try:
                self.monster_stop(st, a, worst, swung, why)
            finally:
                hit.update(action="recall" if self.creature["recalled"] and not recalled else "stop", why=why)
                self.memory.job_event("lumber", "monster_hit", hit, **self._where(st))
        hit["action"] = "walk_on" if walking else "run"
        self.memory.job_event("lumber", "monster_hit", hit, **self._where(st))
        if len(attackers) == 1:
            self.watch.params = travel_guard.learn_hit(self.watch.params, worst.body, worst.distance, 1)
        self.watch.acknowledge()
        self.start_hits = hits                    # the drop is dealt with (check_guards' HP guard)
        what = (f"{worst.name or f'0x{worst.serial:08X}'} at {worst.distance} tiles"
                f"{' (ranged)' if ranged else ''}")
        if walking:
            log(f"hit again while walking away (-{lost}, {hits}/{hmax}{', a spell' if spells else ''}) by {what}; "
                f"walking on")
            return
        self.creature["runs"] += 1
        desc = f"{'spell' if spells and not lost else 'hit'} by {what}: -{lost}, {hits}/{hmax}"
        raise Escape(attackers, self.post_threat(st, a, worst, swung, "escape", desc, extra={"hit": hit}), hit=hit)

    def attacked_by_players(self, st, a) -> list:
        """Players named in a "<name> is attacking you!" since the last check: the
        server's notice that a player made us their target (live 2026-10-02, 1.8 s
        before Bastet's first hit; the attacker sends no 0x2F swing)."""
        ev = self.link.events
        names = set()
        for i in range(self._attack_scan, len(ev)):
            text = ev[i].get("text") or ""
            if ev[i].get("ev") == "speech_heard" and text.endswith(" is attacking you!"):
                names.add(text[: -len(" is attacking you!")])
        self._attack_scan = len(ev)
        return [t for t in a.threats if t.player and t.name in names]

    # ------------------------------------------------------------ tracking reds
    def track_observe(self, st):
        """Fold the world events since the last call into our hunt's state (tracking.Keeper)."""
        ev = self.link.events
        self.trk.observe(st["world"], ev[self._trk_scan:], self.self_serial(st), time.monotonic())
        self._trk_scan = len(ev)

    def check_tracking(self, st, a, swung):
        """New Tracking hits (world.tracking.hits since the run started). A hit while
        hunting murderer players is a red within tracking range, maybe beyond the view:
        a `pk_seen` job event (source 'tracking'; per red: the first hit, and the first
        one within --track-react-range). Within that range (Chebyshev to the arrow, else
        the "N spaces" line) at a pvp spot while out at it, the red escape: recall home
        (recall_out, reason 'tracking: <name> N spaces'), else run to the guards, then
        stop. Farther hits are logged only: at high skill Tracking finds reds sitting in
        their houses far away. A red we already recalled away from doesn't trigger again."""
        pos = st["movement"].get("pos")
        for hit in self.trk.new_hits(st["world"], pos[:2] if pos else None):
            if not hit["murderer"]:
                continue
            d, serial = hit["distance"], hit["serial"]
            near = d is not None and d <= self.args.track_react_range
            react = near and self.k["pvp"] and self.afield and serial not in self.escaped
            self.track_sighting(st, hit, near, react)
            if not react:
                continue
            self.escaped.add(serial)
            name = hit["name"] or f"0x{serial:08X}"
            why = f"tracking: {name} {d} spaces"
            p = self.watch.params
            worst = threats.Threat(serial=serial, name=hit["name"], body=None, notoriety=6, kind="red", player=True,
                                   evidence=[f"Tracking hit while hunting {tracking.MURDERERS}"], hostile=True,
                                   distance=d, s_per_tile=p.mounted_s_per_tile, strike_range=p.player_strike_range,
                                   eta_s=round(max(0, d - p.player_strike_range) * p.mounted_s_per_tile, 1),
                                   action="flee", reason=why)
            log(f"TRACKING: red {name} {d} tiles away (arrow at {hit['x']},{hit['y']}); escaping")
            fail = self.recall_out(st, a, worst, swung, why=why) if self.recall_book is not None else "no recall book"
            self.flee_to_guards(st, a, worst, swung, f"{why}; {fail}")
            self.threat_stop(st, a, worst, swung, Unsafe, f"{why}; {fail}")

    def track_sighting(self, st, hit, near: bool, react: bool):
        """A tracked red as a `pk_seen` job event, the in-view sighting's sibling: source
        'tracking', name, serial, arrow x/y/z, distance, in_range, react, and `counted`:
        whether lumber_opt counts it as the spot's hazard (within range and not already
        counted from a sighting of the same serial this run, in view or tracked)."""
        serial = hit["serial"]
        prev = self.sighted.get(serial)
        if prev is not None and (prev or not near):
            return
        self.sighted[serial] = near
        counted = near and serial not in self.counted
        if counted:
            self.counted.add(serial)
        data = {"source": "tracking", "serial": serial, "name": hit["name"], "kind": "red", "player": True,
                "hostile": True, "x": hit["x"], "y": hit["y"], "z": hit["z"], "distance": hit["distance"],
                "spaces": hit["spaces"], "mode": hit["mode"], "in_range": near, "react": react,
                "react_range": self.args.track_react_range, "counted": counted, "trip": self.trip_n}
        self.memory.job_event("lumber", "pk_seen", data, **self._where(st))
        log(f"tracking: red {hit['name'] or f'0x{serial:08X}'} {hit['distance']} tiles away"
            + ("" if near else f" (beyond {self.args.track_react_range}: logged only)"))

    def track_ensure(self, where: str):
        """Keep Hunting murderer players on (--track reds): when the server's lines or
        the buff say it's off or on another mode, the stock sequence (tracking.hunt:
        UseSkill only without the gump, the mode arrows, Begin) at the human's pace,
        between chops, at most one try per --track-retry-s. Another skill's cooldown
        (500118) waits for the next try; no Tracking skill is logged once and recorded,
        and the run carries on without. Each try is a `tracking` job event."""
        if self.args.track == "off" or self.trk.unavailable:
            return
        st = self.link.state()
        self.track_observe(st)
        now = time.monotonic()
        if not self.trk.due(now):
            return
        sk = tracking.skill(st["world"])
        if sk is not None and sk <= 0:
            self.track_unavailable(where, "no Tracking skill (0.0)")
            return
        self.trk.last_try = now
        self.trk.attempts += 1
        was = f"on ({self.trk.mode})" if self.trk.on else "off"
        log(f"tracking ({where}): Hunting is {was}; turning on Hunting {tracking.MURDERERS}")
        resume = self._intent
        self.doing("track", f"Tracking: hunting {tracking.MURDERERS}")
        try:
            res = tracking.hunt(escape_mod.LinkIO(self.link), tracking.MURDERERS, self.human,
                                hunting=self.trk.on, busy_tries=1)
        except tracking.NoTracking as e:
            self.track_unavailable(where, str(e))
            return
        except tracking.TrackError as e:
            res = {"ok": False, "clicks": [], "error": str(e)}
        finally:
            if resume is not None:
                self.doing(*resume)
        self.track_observe(self.link.state())
        if not res["ok"]:
            log(f"tracking: not on ({res.get('error') or 'the server did not confirm'}); "
                f"next try in {self.args.track_retry_s:.0f} s")
        self.memory.job_event("lumber", "tracking", {"where": where, "ok": res["ok"], "clicks": res["clicks"],
                                                     "error": res.get("error"), "skill": sk, "trip": self.trip_n},
                              **self._where(st))

    def track_unavailable(self, where: str, why: str):
        self.trk.unavailable = why
        log(f"tracking unavailable ({why}); lumbering without it")
        st = self.link.state()
        self.memory.job_event("lumber", "tracking", {"where": where, "ok": False, "unavailable": why,
                                                     "skill": tracking.skill(st["world"]), "trip": self.trip_n},
                              **self._where(st))

    def prepare_recall(self, st) -> int | None:
        """The red escape's book (escape.py), read once at the start like a player
        glancing at it: a runebook or rune tome in the pack with a default rune and a
        charge or a castable Recall. Required at spots where players can attack
        (pvp) unless --recall off."""
        if self.args.recall == "off" or not self.k["pvp"]:
            return None
        books = escape_mod.find_books(st["world"], self.self_serial(st))
        why = "no runebook or rune tome in the backpack"
        for book, kind in books:
            try:
                info = escape_mod.check_ready(escape_mod.LinkIO(self.link), book)
            except escape_mod.RecallError as e:
                why = str(e)
                continue
            if info["default"] is None and info["entries"] != 1:
                why = f"{kind} 0x{book:08X} has no default rune"
            elif info["charges"] <= 0 and not info["can_cast"]:
                why = f"{kind} 0x{book:08X}: no charges and Recall can't be cast (mana/reagents)"
            else:
                log(f"red escape ready: {kind} 0x{book:08X}, default rune "
                    f"{(info['default'] or 0) + 1}, {info['charges']} charge(s)")
                return book
        raise Abort(f"no recall escape ({why}); refusing to work where players can attack without one "
                    f"(--recall off to override)")

    def monster_stop(self, st, a, worst, swung, why: str):
        """A creature ends the run: under attack or with monsters closing in there is no
        time to convert logs (live 2026-10-03, witcher_291: 85 -> 40 hits during a 12 s
        conversion, then the run exited in the field and the overseer's recall landed at
        15/100). So: recall home at once when away from home with a book ready, and stop
        without converting (Unsafe). Near home, or without a book, stop where we stand.
        The trip row's `creature` gets the why and whether the recall landed."""
        self.creature["why"] = why
        if self.recall_book is not None and cheb(self.link.pos(st), self.banker_pos()) > HOME_NEAR:
            try:
                why = f"{why}; {self.recall_out(st, a, worst, swung, pk=False, why=why)}"
            except Unsafe:
                self.creature["recalled"] = True
                raise
        self.threat_stop(st, a, worst, swung, Unsafe, why)

    def recall_out(self, st, a, worst, swung, pk: bool = True, why: str | None = None) -> str:
        """Recall to the book's default rune at once (escape.escape: recasts as soon as the
        server takes a cast again, until it lands or escape.ESCAPE_BUDGET_S is spent),
        before any bookkeeping, then stop: the `threat` juncture (action 'recall') and
        an urgent `pk_escape` juncture when it landed (`pk`; a creature escape posts
        an urgent `threat` juncture instead). Returns why it failed; the caller then
        stops the plain way (threat_stop). A target cursor that is up is cancelled
        first (drop_cursor); the `recall` job event's `react_s` is first sight
        (sight_t) -> the escape's first packet (the book's double-click), and
        `cursor_cancelled` whether a cursor had to go first."""
        cancelled = self.drop_cursor()
        sight, pressed = self.sight_t(worst, swung), time.time()
        react = round(pressed - sight, 2) if sight is not None else None
        log(f"recalling out: {react} s after first sight" if react is not None else "recalling out")
        try:
            res = escape_mod.escape(escape_mod.LinkIO(self.link), self.recall_book, log=log)
        except escape_mod.RecallError as e:
            return f"recall not possible: {e}"
        data = {**res, "trip": self.trip_n, "spot": self.k["spot"]["id"], "book": f"0x{self.recall_book:08X}",
                "threat": worst.to_dict() if worst else None, "attackers": self.attacker_list(swung),
                "cause": "player" if pk else "creature", "react_s": react, "cursor_cancelled": cancelled}
        if why:
            data["why"] = why
        self.memory.job_event("lumber", "recall", data, **self._where(st))
        self.travel.append(leg_summary({"leg": "escape", "s": res["elapsed_s"], **data}))
        if not res["ok"]:
            return f"recall failed after {res['attempts']} cast(s): {res['failure']}"
        # What the pack lost on the way out (a thief's grab arrives with his flag change and
        # the run ends here): casts spent their reagents, anything else is suspected theft.
        self.expect_casts(res)
        try:
            self.check_ledger(self.link.state())
        except Abort:
            pass
        summary = self.post_threat(st, a, worst, swung, "recall", why)
        self.memory.juncture("lumber", "pk_escape" if pk else "threat",
                             f"Recalled away from {summary} ({res['kind']} {res['method']}, "
                             f"{res['press_to_arrival_s']} s); stopped", "urgent", data)
        raise Unsafe(f"threat: {summary}" + (f" ({why})" if why else "")
                     + f"; escaped by recall to {tuple(res['to'])} in {res['elapsed_s']} s")

    def guards_entered(self, since: int) -> bool:
        """The server's "You are now under the protection of the town guards." since
        link.events[since]."""
        return any(e.get("ev") == "cliloc" and e.get("cliloc") == guards.ENTER_CLILOC
                   for e in self.link.events[since:])

    def flee_to_guards(self, st, a, worst, swung, why):
        """The recall escape failed (`why`): run to the nearest known guarded place
        (guards.flee_goals: tiles where the server said we were under the guards'
        protection, bank markers), urgent pacing, until we stand on one or the
        server says we're under the guards' protection (cliloc 500112) on the way.
        The notice lags and skips crossings (guards.py), so arriving counts without
        it; `confirmed` says whether it came. There: "guards" once when a hostile
        player is within 12 tiles, an urgent `pk_escape` juncture, then Unsafe.
        Returns (after logging why) only when the flight failed; the caller then
        stops the plain way."""
        self.drop_cursor()
        me = tuple(self.link.pos(st)[:2])
        facet = st["world"]["self"].get("map") or 0
        attacker = None
        if worst is not None:
            m = st["world"]["mobiles"].get(f"0x{worst.serial:08X}")
            if m and m.get("x") is not None:
                attacker = (m["x"], m["y"])
        goals = guards.flee_goals(self.memory.guard_points(facet), guards.bank_markers(), facet, me, attacker)
        if not goals:
            log(f"guard flight: no guarded place known within {guards.FLEE_MAX_DIST} tiles")
            return
        t0 = time.monotonic()
        self.mode = "flee"
        self._flee_mark = len(self.link.events)
        self.doing("flee", f"Running to the guards from {worst.name if worst else 'an attacker'}")
        log(f"guard flight ({why}): {len(goals)} guarded place(s) in range")
        try:
            self.mover.walk_to(None, 0, "to the guards", max_moves=FLEE_MAX_MOVES,
                               goal_fn=nav.any_of(goals), urgent=True)
            confirmed = self.link.wait(lambda s: self.guards_entered(self._flee_mark),
                                       FLEE_ARRIVAL_WAIT_S) is not None
        except InGuards:
            confirmed = True
        except Unsafe:
            raise
        except Abort as e:
            log(f"guard flight failed: {e}")
            return
        finally:
            self.mode = "work"
        self.in_guards(worst, swung, why, t0, confirmed)

    def in_guards(self, worst, swung, why, t0, confirmed: bool):
        """At a guarded place after a flight: call the guards on a hostile player
        within 12 tiles, post `guard_flight` + `pk_escape`, raise Unsafe."""
        st = self.link.state()
        a = self.watch.update(st, recall_s=RECALL_S, margin_s=THREAT_MARGIN_S)
        pos = self.link.pos(st)
        near = [t for t in a.threats if t.player and t.hostile and 0 <= t.distance <= guards.ATTACKER_NEAR]
        if near:
            self.link.act(actions.say_unicode("guards"))
            log(f"called the guards on {near[0].name or f'0x{near[0].serial:08X}'}")
        data = {"method": "guards", "recall_failure": why, "to": list(pos[:2]), "confirmed": confirmed,
                "flight_s": round(time.monotonic() - t0, 2), "called_guards": bool(near),
                "threat": worst.to_dict() if worst else None,
                "attackers": self.attacker_list(swung)}
        self.memory.job_event("lumber", "guard_flight", data, **self._where(st))
        summary = self.post_threat(st, a, worst, swung, "guards")
        self.memory.juncture("lumber", "pk_escape",
                             f"Fled into the guards from {summary} ({data['flight_s']} s); stopped", "urgent", data)
        raise Unsafe(f"threat: {summary}; fled into the guards at {tuple(pos[:2])}")

    def attacker_list(self, swung) -> list:
        """The junctures' and job events' `attackers`: those swinging or casting at us
        (swung) and the ones creature_hit blamed for the damage in this check (hit_by),
        so the list matches its "N creatures attacking" (juncture 222 had [] for 2)."""
        return [f"0x{s:08X}" for s in dict.fromkeys([*swung, *self.hit_by])]

    def post_threat(self, st, a, worst, swung, action, why=None, extra=None) -> str:
        """The urgent `threat` juncture + `flee` job event (`extra` merged into its
        data, e.g. the monster_hit episode as `hit`); returns the summary. A target
        cursor still up is cancelled (drop_cursor): an escape walks off, a stop leaves."""
        self.drop_cursor()
        if worst is not None:
            summary = (f"{worst.kind} {worst.name or f'0x{worst.serial:08X}'} at {worst.distance} tiles "
                       f"(ETA {worst.eta_s:.1f} s)")
        elif swung:
            summary = "attacked by " + ", ".join(f"0x{s:08X}" for s in swung)
        else:
            summary = "taking damage"
        data = {**a.to_dict(), "action": action, "attackers": self.attacker_list(swung), **(extra or {})}
        if why:
            data["why"] = why
        what = "escaping" if action == "escape" else "stopping"
        self.memory.juncture("lumber", "threat", f"Threat: {summary}; {what}" + (f" ({why})" if why else ""),
                             "urgent", data)
        self.memory.job_event("lumber", "flee", data, **self._where(st))
        return summary

    def threat_stop(self, st, a, worst, swung, cls, why=None):
        summary = self.post_threat(st, a, worst, swung, "abort", why)
        raise cls(f"threat: {summary}" + (f" ({why})" if why else "") + "; stopping")

    def check_ledger(self, st):
        """ledger.py over every state read: unexplained pack losses are
        reported as suspected theft (the loop carries on); death stops. The theft
        event carries `carried`: the logs and boards in the pack just before the
        loss (the ledger's previous snapshot), so a theft's share of the load is known."""
        before = self.ledger.items
        d = self.ledger.observe(st)
        if d.death:
            self.died(st, d.death_reason or "pack emptied")
        if d.theft_suspected:
            lost = d.unexplained_losses
            n = sum(e.get("amount") or 1 for e in lost)
            what = ", ".join(sorted({e.get("wood") or f"0x{e['graphic']:04X}" for e in lost}))
            carried = None if before is None else {
                "logs": sum(r["amount"] for r in before.values() if r["graphic"] in LOGS),
                "boards": sum(r["amount"] for r in before.values() if r["graphic"] in BOARDS)}
            self.memory.juncture("lumber", "theft_suspected", f"{n} item(s) left the pack unexplained: {what}",
                                 "attention", d.to_dict())
            self.memory.job_event("lumber", "theft", {"amount": n, "items": lost, "carried": carried},
                                  **self._where(st))
            log(f"pack lost {n} item(s) without a cause ({what}); suspected theft, carrying on")

    def check_speech(self, st):
        """speech_guard.py: a character speaking near us hands control to the
        overseer (user request 2026-10-01, harvest jobs only)."""
        who = self.new_speakers(st)
        if who:
            self.speech_hold(who, st)

    def new_speakers(self, st):
        """New speakers, each with its Laya verdict (triage.py) when the service is on."""
        who = self.speech.scan(st["world"], self.link.events, self.link.event_t)
        for w in who:
            v = self.triage.judge(w, st["world"], names=self.speech.names)
            if v and "error" not in v:
                log(f"laya: {w['label'] or w['name'] or w['serial']}: {w['text']!r} "
                    f"check {v['check']:.2f} direct {v['direct']:.2f} ({v['ms']} ms)")
        return who

    def speech_hold(self, who, st):
        """Send nothing until the overseer gives the all-clear (acks the
        `speech_nearby` juncture) and no `gm_suspected` juncture is open.
        Threats and death still end the job. The overseer may talk to the
        speaker meanwhile (ctl allows `act say` and `single_click` while a task
        holds) or stop the job. A speaker with staff hints (speech_guard.
        staff_hints, including Laya's attendance check) raises `gm_suspected`
        and the staff alarm, which repeats (alerts.STAFF_REPEAT_S) until that
        juncture is acked. At the all-clear, a `speech_clear` job event keeps
        every line heard with its verdict and how the hold ended (the labeled
        data for fine-tuning Laya)."""
        first = who[0]
        name = first["label"] or first["name"] or first["serial"]
        log(f"SPEECH: {name}: {first['text']!r}; pausing for the overseer")
        resume = self._intent
        self.doing("speech", f"Paused: {name} spoke nearby; waiting for the overseer")
        data = {"hold": True, "task": "lumber", "trip": self.trip_n, "speakers": who, **self._where(st)}
        jid = self.memory.juncture("lumber", "speech_nearby",
                                   f"{name} said {first['text']!r} nearby; harvesting paused until the "
                                   f"all-clear (ack)"[:300], "urgent", data)
        self.memory.job_event("lumber", "speech_hold", data, **self._where(st))
        if not self.suspect_staff(who, st):
            alert(not self.args.quiet)
        t0, heard, lines = time.monotonic(), {w["serial"] for w in who}, list(who)
        gms = set()                                  # gm_suspected juncture ids seen open during the hold
        self.holding = True
        try:
            while True:
                gms.update(alerts.open_gm(self.memory))
                self.pause(SPEECH_POLL_S, "speech_hold")   # threats + ledger (look, escape=False) meanwhile
                st = self.link.last                         # the pause's last read
                new = self.new_speakers(st)
                for w in new:
                    log(f"SPEECH (paused): {w['label'] or w['name'] or w['serial']}: {w['text']!r}")
                    heard.add(w["serial"])
                lines += new
                self.suspect_staff(new, st)
                alerts.staff_alarm_due(self.memory, not self.args.quiet)
                j = self.memory.junctures(after_id=jid - 1, limit=1)
                if j and j[0]["acked_t"] is not None and not alerts.open_gm(self.memory):
                    break
        finally:
            self.holding = False
        waited = time.monotonic() - t0
        self.deadline += waited                      # the pause isn't the job's time
        gm = [{"id": g["id"], "source": g["source"], "summary": g["summary"]}
              for g in self.memory.junctures(after_id=min(gms) - 1, limit=max(gms) - min(gms) + 1)
              if g["id"] in gms] if gms else []
        self.memory.job_event("lumber", "speech_clear",
                              {"juncture": jid, "waited_s": round(waited, 1), "gm_suspected": gm, "lines": lines},
                              **self._where(st))
        self.speech.clear(heard)
        self.stats["speech_holds"] = self.stats.get("speech_holds", 0) + 1
        self.stats["speech_wait_s"] = self.stats.get("speech_wait_s", 0.0) + waited
        log(f"all-clear after {waited:.0f} s; resuming")
        self.human.wait("read")
        if resume is not None:
            self.doing(*resume)

    def suspect_staff(self, who, st) -> bool:
        """Raise gm_suspected (urgent, staff alarm) for speakers with staff hints,
        unless one is already open. True when one is open afterwards."""
        hinted = [w for w in who if staff_hints(w)]
        if hinted and not alerts.open_gm(self.memory):
            w = hinted[0]
            name = w["label"] or w["name"] or w["serial"]
            log(f"POSSIBLE STAFF: {name} ({', '.join(staff_hints(w))}); staff alarm")
            alerts.post_gm(self.memory, "lumber", f"{name}: {', '.join(staff_hints(w))}",
                           {"task": "lumber", "trip": self.trip_n, "speakers": hinted, **self._where(st)},
                           not self.args.quiet)
        return bool(alerts.open_gm(self.memory))

    def died(self, st, reason):
        a = self.last_threats
        players = [t for t in (a.threats if a else []) if t.hostile and t.player]
        cause = "pk" if players else ("mob" if a and a.under_attack else "unknown")
        data = {"reason": reason, "cause": cause, "threats": a.to_dict() if a else None}
        self.memory.juncture("lumber", "death", f"Died ({cause}): {reason}", "urgent", data)
        self.memory.job_event("lumber", "death", data, **self._where(st))
        raise Unsafe(f"died ({cause}): {reason}")

    def state(self):
        st = self.link.state()
        self.check_guards(st)
        return st

    # ------------------------------------------------------------ world lookups
    def self_serial(self, st) -> int:
        return st["movement"]["self_serial"]

    def item(self, st, serial):
        return st["world"]["items"].get(f"0x{serial:08X}")

    def backpack(self, st) -> int:
        me = self.self_serial(st)
        for key, it in st["world"]["items"].items():
            if it.get("layer") == LAYER_BACKPACK and it.get("container") is not None \
                    and serial_of(it["container"]) == me:
                return serial_of(key)
        raise Abort("backpack not known to the world model")

    def in_pack(self, st, graphics):
        pack = self.backpack(st)
        return [(serial_of(k), it) for k, it in st["world"]["items"].items()
                if it.get("graphic") in graphics and it.get("container") is not None
                and serial_of(it["container"]) == pack]

    def count(self, st, graphics) -> int:
        return sum(it.get("amount") or 1 for _, it in self.in_pack(st, graphics))

    def hatchet(self, st, want=None) -> int:
        """A worn hatchet, else the shallowest one in the backpack or in a bag in
        it (any depth); with `want` (lumber_opt.parse_hatchet_spec, --hatchet)
        only one of that material and quality. use_hatchet opens the bags on the
        way like a player."""
        me, pack, items = self.self_serial(st), self.backpack(st), st["world"]["items"]
        best = None
        for key, it in items.items():
            if it.get("graphic") in HATCHETS and it.get("container") is not None:
                if want is not None and not lumber_opt.matches(lumber_opt.hatchet_kind(it, self.hatchets), want):
                    continue
                depth = pack_depth(items, serial_of(it["container"]), me, pack)
                if depth is not None and (best is None or depth < best[0]):
                    best = (depth, serial_of(key))
        if best is None:
            what = "hatchet" if want is None else "+".join(w for w in want if w) + " hatchet"
            raise Abort(f"no {what} worn, in the backpack or in a bag in it")
        return best[1]

    def snapshot(self, st) -> dict:
        """Who works this trip and with what (lumber_opt.character): the optimizer
        rescales each trip's chopping to the skill and tool bonus of the day."""
        ch = lumber_opt.character(st["world"], self.self_serial(st), self.hatchets)
        try:
            serial = f"0x{self.hatchet(st, self.want_hatchet):08X}"
        except Abort:
            serial = None
        return {"character": {"serial": ch["serial"], "name": ch["name"]}, "skill": ch["skill"],
                "mounted": ch["mounted"], "buffs": ch["buffs"],
                "hatchet": next((h for h in ch["hatchets"] if h["serial"] == serial), None)}

    # ------------------------------------------------------------ event scans
    def since(self, mark):
        return self.link.events[mark:]

    def heard(self, mark, text=None, cliloc=None):
        for ev in self.since(mark):
            if text is not None and ev.get("ev") == "speech_heard" and ev.get("text") == text:
                return ev
            if cliloc is not None and ev.get("ev") == "cliloc" and ev.get("cliloc") == cliloc:
                return ev
        return None

    def gump(self, mark, gump_id):
        for ev in self.since(mark):
            if ev.get("ev") == "gump_open" and ev.get("gump_id") == gump_id:
                return ev
        return None

    def real_captcha(self, mark):
        """The real captcha: lumber.json's gump id with the text entry and a
        reply button besides Guide (the submit id is random per captcha).
        Decoys (same words, no buttons) never match."""
        cap = self.k["captcha"]
        for i, ev in enumerate(self.since(mark)):
            if ev.get("ev") == "gump_open" and ev.get("gump_id") == h(cap["gump_id"]):
                lay = parse_layout(ev.get("layout", ""))
                if cap["answer_entry_id"] in lay["entries"] \
                        and any(b != cap["guide_button"] for b in lay["buttons"]):
                    return mark + i
        return None

    def cursor(self, mark):
        for ev in self.since(mark):
            if ev.get("ev") == "target":
                return ev
        return None

    # ------------------------------------------------------------ captcha
    def captcha_handoff(self, idx):
        """Who answers is the memory store's captcha mode (Memory.captcha_mode,
        toggled in the viz header), read when the captcha opens and on every
        poll while waiting.

        human (the default): pause + beep until a solve is observed in the client.
        auto: captcha_auto answers from the layout. Unreadable layouts and
        repeated strikes fall back to the human wait; switching to auto during
        the wait hands the newest captcha to the solver."""
        cap = self.k["captcha"]
        self.stats["captchas"] = self.stats.get("captchas", 0) + 1
        t0 = time.monotonic()
        resume = self._intent
        auto_failed = False
        if self.memory.captcha_mode() == "auto":
            if self.captcha_auto(idx):
                self.captcha_done(t0, resume, "auto")
                return
            auto_failed = True
        why = "auto-solve did not answer it" if auto_failed else "captcha mode: human"
        log(f"CAPTCHA up: agent paused, waiting for the solve in the client ({why})")
        paused = "Captcha up — paused until it is solved"
        self.doing("captcha", paused)
        jid = self.memory.juncture("lumber", "captcha", f"Captcha up; agent paused until solved ({why})",
                                   "urgent", {"trip": self.trip_n, "mode": "auto" if auto_failed else "human"})
        alert(not self.args.quiet)
        next_beep = t0 + self.args.captcha_beep_s
        while True:
            self.link.state()
            if self.heard(idx, text=cap["ok_text"]):
                self.memory.juncture_ack(jid)          # solved; nothing left for the overseer
                self.captcha_done(t0, resume, "human")
                return
            if not auto_failed and self.memory.captcha_mode() == "auto":
                log("captcha mode switched to auto while waiting")
                if self.captcha_auto(self.last_real_captcha(idx)):
                    self.memory.juncture_ack(jid)
                    self.captcha_done(t0, resume, "auto")
                    return
                auto_failed = True
                self.doing("captcha", paused)
            now = time.monotonic()
            if now - t0 > self.args.captcha_timeout:
                raise Unsafe(f"captcha not solved within {self.args.captcha_timeout:.0f} s")
            if now >= next_beep:
                alert(not self.args.quiet)
                next_beep = now + self.args.captcha_beep_s
            self.pause(0.5, "captcha_wait")

    def captcha_done(self, t0, resume, how):
        waited = time.monotonic() - t0
        self.stats["captcha_wait_s"] = self.stats.get("captcha_wait_s", 0.0) + waited
        log(f"captcha solved in {waited:.0f} s ({how}); resuming")
        self.human.wait("read")
        if resume is not None:
            self.doing(*resume)

    def last_real_captcha(self, idx):
        """The newest real captcha at or after idx (a wrong answer re-opens one)."""
        while (nxt := self.real_captcha(idx + 1)) is not None:
            idx = nxt
        return idx

    def answered(self, idx):
        """A solve heard or a 0xB1 for the captcha at idx since it opened (the
        human may answer while the solver waits; never answer a gump twice)."""
        ev = self.link.events[idx]
        return self.heard(idx, text=self.k["captcha"]["ok_text"]) is not None or any(
            e.get("ev") == "gump_response" and e.get("serial") == ev.get("serial")
            and e.get("gump_id") == ev.get("gump_id") for e in self.since(idx + 1))

    def captcha_auto(self, idx) -> bool:
        """captcha.solve reads the digits from the gump layout (harness/captcha.py,
        ANTICHEAT.md §8.8) and the runner answers with the stock 0xB1 after a
        human-plausible delay. A wrong answer costs a strike (the server re-opens
        a fresh captcha). True once solved; False when the layout is unreadable,
        the strikes run out or the client answered first (the caller waits for
        the solve)."""
        cap = self.k["captcha"]
        strikes = 0
        while strikes <= self.args.captcha_max_strikes:
            ev = self.link.events[idx]
            digits = captcha.solve(ev.get("layout", ""))
            submit = captcha.submit_button(ev.get("layout", ""), cap["guide_button"])
            if digits is None or submit is None:
                return False
            self.doing("captcha", "Solving the captcha")
            self.human.wait("captcha")
            self.link.state()
            if self.answered(idx):
                log("captcha answered in the client meanwhile; not sending")
                return False
            mark = len(self.link.events)
            self.link.act(actions.gump_reply(ev["serial"], h(cap["gump_id"]), submit,
                                             ev.get("layout", ""), ev.get("lines") or [],
                                             texts={cap["answer_entry_id"]: digits}))
            log(f"captcha answered {digits!r} (auto-solved from the layout)")
            end = time.monotonic() + 12.0
            while time.monotonic() < end:
                self.link.state()
                if self.heard(mark, text=cap["ok_text"]):
                    return True
                nxt = self.real_captcha(mark)
                if nxt is not None:            # rejected: a fresh captcha opened
                    strikes += 1
                    log(f"captcha answer rejected (strike {strikes})")
                    idx = nxt
                    break
                self.pause(0.3, "captcha_wait")
            else:
                raise Unsafe("captcha answer got no server reply within 12 s")
        return False

    # ------------------------------------------------------------ using the hatchet
    def use_hatchet(self):
        """dclick the hatchet; returns the target-cursor event (captchas handled).
        A hatchet in the pack is reached like a player would: the containers on
        the way that the server hasn't opened yet are opened first, outermost
        first (agent_link.containers_to_open, ANTICHEAT.md closed containers).
        Now and then the human hesitates: cancels the cursor (stock Esc packet)
        and uses the hatchet again. When the cursor comes, whether the hatchet is now
        in hand is noted for the trip row (hatchet_worn): the server equips a packed
        hatchet on the double-click, and every spell cast puts it back in the pack
        (LUMBER_LOOP.md §13, session 20261003_213125)."""
        for attempt in range(2):
            self.human.wait("use")
            st = self.state()
            hatchet = self.hatchet(st, self.want_hatchet)
            closed = containers_to_open(st["world"], hatchet)
            if closed:
                self.link.open_containers(closed, self.human)
            mark = len(self.link.events)
            self._tool_sent = (mark, time.monotonic())     # a threat before its cursor comes: drop_cursor waits
            self.link.act(actions.dclick(hatchet))
            self.wait_for(lambda s: self.cursor(mark) or self.real_captcha(mark) is not None, 4.0)
            self._tool_sent = None
            cap = self.real_captcha(mark)
            if cap is not None:
                self.captcha_handoff(cap)
                return None
            cur = self.cursor(mark)
            if cur is not None:
                st = self.link.last or st
                self.hatchet_worn = in_hand(st["world"], hatchet, self.self_serial(st))
            if cur is None or attempt == 1 or not self.human.hesitate():
                return cur
            log("(hesitating: cancelling the cursor)")
            self.human.wait("aim")
            self.link.act(actions.target_cancel(cur["cursor_id"], cur["target_type"], cur["cursor_type"]))
        return None

    # ------------------------------------------------------------ harvesting
    def outcome(self, mark):
        """The server's answer to an attempt since events[mark]: success (logs), fail
        (500495), depleted (500488/500493 not enough wood), nothing_near (Smart Harvest:
        "You do not see any harvestable resources nearby.", with "You cannot produce any
        wood from that." over our head: nothing within its reach has wood, move to the next
        stand), lockout (travel lockout seconds); None while none came."""
        hv, lock = self.k["harvest"], self.k["travel_lockout"]
        for ev in self.since(mark):
            e = ev.get("ev")
            if e == "speech_heard":
                t = ev.get("text") or ""
                if t == hv["success_text"] or COLORED_CHOP.match(t):
                    return ("success", 0)
                if t in hv["nothing_near_texts"]:
                    return ("nothing_near", 0)
                if t.startswith(lock["text_prefix"]):
                    digits = [int(w) for w in t.split() if w.isdigit()]
                    return ("lockout", digits[0] if digits else lock["seconds"])
            elif e == "cliloc":
                n = ev.get("cliloc")
                if n == hv["fail_cliloc"]:
                    return ("fail", 0)
                if n in hv["depleted_clilocs"]:
                    return ("depleted", 0)
        return None

    def attempt(self):
        """One Smart Harvest attempt: use the hatchet and answer its cursor with ourselves
        (self_target); the server chops a tree within its reach that still has wood →
        (outcome, logs gained)."""
        st = self.state()
        before = self.count(st, LOGS)
        cur = self.use_hatchet()
        if cur is None:
            return ("captcha", 0)
        self.human.wait("aim")
        st = self.state()
        mark = len(self.link.events)
        self.link.act(self_target(st, cur))
        end = time.monotonic() + self.args.attempt_timeout
        while time.monotonic() < end:
            self.state()
            cap = self.real_captcha(mark)
            if cap is not None:
                self.captcha_handoff(cap)
                mark = cap + 1               # the server finishes the attempt after the answer
                end = time.monotonic() + self.args.attempt_timeout
                continue
            out = self.outcome(mark)
            if out is not None:
                if out[0] == "success":
                    st = self.wait_for(lambda s: self.count(s, LOGS) > before, 3.0)
                    gained = self.count(st or self.link.state(), LOGS) - before
                    return ("success", max(gained, 0))
                return out
            time.sleep(0.15)
        return ("none", 0)

    def candidate_trees(self, st):
        """Seed trees (the spot's) plus trees found on the map in the spot's
        area, minus what harvest memory rules out (regrowth window --regrow-min),
        nearest first with human noise; at most --max-trees (0 = all). Smart
        Harvest picks the tree itself: the list decides where to stand (next_stand)."""
        seeds = list(self.k["harvest"]["trees"])
        found = []
        area = self.k["harvest"].get("area")
        walk = self.mover.walk_map(st)
        if area and walk is not None:
            cx, cy = area["center"]
            r = area["radius"]
            found = [{"x": x, "y": y, "z": z, "graphic": f"0x{g:04X}"}
                     for x, y, z, g in walk.m.find_trees(cx - r, cy - r, cx + r, cy + r)]
        seen, trees = set(), []
        for t in seeds + found:
            if (t["x"], t["y"]) not in seen:
                seen.add((t["x"], t["y"]))
                trees.append(t)
        now = time.time()
        trees = [t for t in trees if self.memory.harvest_available(
            self.facet, t["x"], t["y"], t["z"], self.args.regrow_min * 60, now)]
        pos = st["movement"]["pos"]
        trees.sort(key=lambda t: cheb(pos, (t["x"], t["y"])) * self.human.rng.uniform(1.0, 1.6))
        return trees[: self.args.max_trees] if self.args.max_trees > 0 else trees

    def harvest_trip(self) -> int:
        """Smart-Harvest at stands by the candidate trees until the quota. A monster
        escape (Escape -> self.escape) leaves the current stand; harvesting resumes
        at the next stand out of the reach of every monster escaped from.
        A break announced by the gate (self.break_due) ends the harvest. Running
        out of trees before the quota marks the trip `dry` (lumber_opt keeps
        the spot out of the plan until the trees regrow)."""
        tally = {"gained": 0, "attempts": 0, "successes": 0, "unknown": 0}
        try:
            if self.break_due:
                log("break due: no harvesting this trip")
                return 0
            self.go_out()
            self.afield = True
            self.track_ensure("at the spot")
            trees = self.candidate_trees(self.state())
            if not trees:
                self.stats["dry"] = True
                raise Abort("no harvestable tree available (all depleted, unreachable or ruled out)")
            while trees and tally["gained"] < self.args.logs_per_trip and not self.break_due:
                tree = self.next_stand(trees)
                if tree is None:
                    self.stats["creature_blocked"] = True
                    log(f"every tree left ({len(trees)}) is within reach of an aggressive creature in view; "
                        f"ending the harvest at {tally['gained']} logs")
                    break
                if not self.out_of_reach(tree["x"], tree["y"]):
                    log(f"tree {tree['x']},{tree['y']}: within reach of a monster we backed away from; skipping")
                    continue
                try:
                    self.work_stand(tree, trees, tally)
                except Escape as e:
                    self.escape(e)
            if self.break_due:
                log(f"break due: stopping the harvest at {tally['gained']} logs; converting and banking")
            elif not trees and tally["gained"] < self.args.logs_per_trip:
                self.stats["dry"] = True
                log(f"the area ran dry at {tally['gained']} logs (every candidate tree out of wood or tried); banking")
            return tally["gained"]
        finally:
            self.stats.update(attempts=tally["attempts"], successes=tally["successes"], logs=tally["gained"])

    def next_stand(self, trees: list) -> dict | None:
        """Remove and return the tree to stand by next (Smart Harvest then picks the tree
        it chops within SMART_RANGE of where we stand): among the NEXT_STAND_PLANS nearest
        by straight line, the one whose planned walk costs least per candidate tree within
        SMART_RANGE of the stand it ends on, with a little human noise. Planned walks, not
        straight lines: on 2026-10-02 (Terran) the start-order list sent the runner on
        80-90 step loops round a ridge between trees on both sides of a road while trees
        3-6 steps away waited. Trees within reach of a known-aggressive creature in view
        (tree_guards) wait in the list while it is around; None when every tree left does."""
        st = self.link.state()
        pos = self.link.pos(st)
        trees.sort(key=lambda t: cheb(pos, (t["x"], t["y"])))
        guards = self.tree_guards(st)
        free = []
        for t in trees:
            g = next((g for g in guards if cheb(g[1], (t["x"], t["y"])) <= g[2]), None)
            if g is None:
                free.append(t)
            elif ((t["x"], t["y"]), g[0].serial) not in self.avoided:
                self.avoided.add(((t["x"], t["y"]), g[0].serial))
                self.creature["avoided_trees"] += 1
                log(f"tree {t['x']},{t['y']}: within {g[2]} tiles of {g[0].name or f'0x{g[0].serial:08X}'} "
                    f"at {g[1]} ({g[0].aggression}); choosing a tree away from it")
        if not free:
            return None
        best, best_cost = 0, None
        for i, t in enumerate(free[:NEXT_STAND_PLANS]):
            path, _ = self.mover.plan(st, nav.within((t["x"], t["y"]), 1, self.tree_z_ok(t)))
            if path is None:
                continue
            stand = tuple(t["stand"]) if "stand" in t else path[-1]
            n = sum(1 for o in trees if cheb(stand, (o["x"], o["y"])) <= SMART_RANGE)
            c = len(path) * self.human.rng.uniform(1.0, 1.15) / max(n, 1)
            if best_cost is None or c < best_cost:
                best, best_cost = i, c
        trees.remove(free[best])
        return free[best]

    def tree_guards(self, st) -> list:
        """[(threat, (x, y), tiles)]: known-aggressive creatures in view (hostile in the
        last assessment: learned bodies, war mode, notoriety 6; never pets or passive
        bodies) with the zone (zone_r) whose trees wait while they are around."""
        a = self.last_threats
        mobs = st["world"]["mobiles"]
        out = []
        for t in (a.threats if a else []):
            m = mobs.get(f"0x{t.serial:08X}") or {}
            if t.kind == "monster" and t.hostile and 0 <= t.distance <= self.watch.params.max_range \
                    and m.get("x") is not None:
                out.append((t, (m["x"], m["y"]), self.zone_r(t)))
        return out

    def zone_r(self, t) -> int:
        """Tiles around a creature that are out of bounds: its reach (a ranged one's at
        least CREATURE_SPELL_RANGE, threats.creature_reach) or its flee radius, whichever
        is more, plus ESCAPE_MARGIN."""
        return max(t.flee_radius, threats.creature_reach(t.body, self.watch.params)) + ESCAPE_MARGIN

    def work_stand(self, tree, trees, tally):
        """Walk next to `tree` (next_stand) and Smart-Harvest there until the server says
        nothing within its reach has wood (or not enough wood), the quota is met, a break
        is due or --max-attempts-per-stand; tally (gained/attempts/successes/unknown) is
        the trip's. Attempts are recorded on the stand tile (the server doesn't say which
        tree it chopped). On "nothing nearby" the trees within SMART_RANGE of the stand
        (this one and those left in the trip's `trees`) are marked `nothing_near` for the
        regrowth window and leave the list. Each stand is a `stand` job event with what
        measuring Smart Harvest's reach and choice needs: the stand tile, the trees within
        SURVEY_R (offset, distance, graphic), attempts, successes, logs, the direction the
        server turned us per chop (faced), how the stay ended and how long it took."""
        label = f"tree {tree['x']},{tree['y']}"
        spot = (tree["x"], tree["y"])
        quota = f"{tally['gained']}/{self.args.logs_per_trip} logs"
        self.doing("to_tree", f"Heading to the trees at {spot[0]},{spot[1]} ({quota})", spot)
        z_ok = self.tree_z_ok(tree)
        walk0 = time.monotonic()
        try:
            if "stand" in tree:
                self.mover.walk_to(lambda: tree["stand"], 0, f"to {label}", z_ok=z_ok)
            else:
                self.mover.walk_to(lambda: (tree["x"], tree["y"]), 1, f"to {label}", z_ok=z_ok)
        except Abort as e:
            if "no route" not in str(e):
                raise
            self.memory.harvest_record(self.facet, tree["x"], tree["y"], tree["z"], h(tree["graphic"]), "unreachable")
            log(f"{label}: unreachable; trying the next stand")
            return
        finally:
            if self.timing.get("walk_out_s") is not None:      # the first walk is the walk out
                self.timing["tree_walk_s"] += time.monotonic() - walk0
        x, y, z = self.link.pos(self.link.state())[:3]
        stand = (x, y)
        near = sorted((t for t in [tree, *trees] if cheb(stand, (t["x"], t["y"])) <= SURVEY_R),
                      key=lambda t: cheb(stand, (t["x"], t["y"])))
        reach = [t for t in near if cheb(stand, (t["x"], t["y"])) <= SMART_RANGE]
        rec = {"trip": self.trip_n, "spot": self.k["spot"]["id"], "stand": [x, y, z], "anchor": list(spot),
               "range": SMART_RANGE,
               "trees": [[t["x"] - x, t["y"] - y, cheb(stand, (t["x"], t["y"])), t["graphic"]] for t in near],
               "attempts": 0, "successes": 0, "logs": 0, "faced": [], "end": None}
        where = f"stand {x},{y}"
        log(f"{where}: {len(reach)} candidate tree(s) within {SMART_RANGE} (reach unmeasured), "
            f"{len(near)} within {SURVEY_R}: {[tuple(t[:3]) for t in rec['trees']]}")
        t_stand = time.monotonic()
        try:
            while rec["attempts"] < self.args.max_attempts_per_stand and tally["gained"] < self.args.logs_per_trip \
                    and not self.break_due:
                self.track_ensure("between chops")
                if self.unstick(stand):
                    continue
                self.doing("chop", f"Chopping by {x},{y} ({tally['gained']}/{self.args.logs_per_trip} logs)", stand)
                if self.timing.get("walk_out_s") is None:
                    self.timing["walk_out_s"] = time.time() - self.trip_t0
                c0, w0 = time.monotonic(), self.stats.get("speech_wait_s", 0.0)
                out, n = self.attempt()
                self.chopped(c0, w0)
                if out != "none":
                    tally["unknown"] = 0                 # the abort counts unknowns in a row
                if out in ("success", "fail"):
                    rec["attempts"] += 1
                    tally["attempts"] += 1
                    self.memory.harvest_record(self.facet, x, y, z, None, out, n)
                    faced = ((self.link.last or {}).get("world", {}).get("self") or {}).get("direction")
                    rec["faced"].append(faced)
                    if out == "success":
                        rec["successes"] += 1
                        rec["logs"] += n
                        tally["successes"] += 1
                        tally["gained"] += n
                    log(f"{where}: {f'+{n} logs' if out == 'success' else 'fail'} "
                        f"({tally['gained']}/{self.args.logs_per_trip}); {self.faced_text(stand, faced, near)}")
                elif out in ("nothing_near", "depleted"):
                    rec["end"] = out
                    if out == "depleted":           # the server's pick ran out; which one it was is unknown
                        log(f"{where}: not enough wood here; next stand")
                        return
                    for t in reach:
                        self.memory.harvest_record(self.facet, t["x"], t["y"], t["z"], h(t["graphic"]), "nothing_near")
                        if t in trees:
                            trees.remove(t)
                    log(f"{where}: nothing nearby has wood after {rec['attempts']} attempt(s), {rec['logs']} logs; "
                        f"{len(reach)} tree(s) within {SMART_RANGE} marked out of wood; next stand")
                    return
                elif out == "lockout":
                    wait = n + self.human.rng.uniform(1.0, 3.0)
                    log(f"travel lockout reported: waiting {wait:.0f} s")
                    self.doing("lockout", f"Waiting out the travel lockout ({wait:.0f} s)", stand)
                    self.pause(wait, "lockout")
                    self.timing["lockout_s"] += wait     # travel's cost, not the field's (lumber_opt.trip_obs)
                    continue
                elif out == "none":
                    tally["unknown"] += 1
                    log(f"{where}: no recognised outcome ({tally['unknown']} in a row)")
                    if tally["unknown"] > 3:
                        raise Abort("harvest attempts keep ending without a known outcome")
                c0, w0 = time.monotonic(), self.stats.get("speech_wait_s", 0.0)
                self.human.wait("between")
                self.human.fidget(self.link, self.link.state(), self.backpack(self.link.state()))
                self.chopped(c0, w0)
            rec["end"] = ("break" if self.break_due else "quota" if tally["gained"] >= self.args.logs_per_trip
                          else "max_attempts")
        except BaseException as e:
            rec["end"] = rec["end"] or f"interrupted: {type(e).__name__}: {e}"[:200]
            raise
        finally:
            rec["s"] = round(time.monotonic() - t_stand, 1)
            self.memory.job_event("lumber", "stand", rec, facet=self.facet, x=x, y=y)

    @staticmethod
    def faced_text(stand, faced, near) -> str:
        """Where the server turned us for a chop, and the trees around the stand in that
        direction (facing_to): the measurement of which tree Smart Harvest picks."""
        if faced is None:
            return "facing unknown"
        hit = [(t["x"], t["y"]) for t in near if (t["x"], t["y"]) != stand
               and facing_to(stand, (t["x"], t["y"])) == faced]
        return f"facing {DIR_NAMES[faced & 7]}: trees that way {hit or 'none known'}"

    def unstick(self, stand) -> bool:
        """Outlands' Stationary Penalty (stationary.py) stops harvesting until we walk
        (patch 2025-01-25, docs/research/THREATS.md T4; wiki Mining). It comes at once
        after a recall (go_out lands next to the spot) and after 301-315 s without a
        step (a long tree, a speech hold or a captcha). Before each chop: when it is
        on, walk it off (its steps + 1, out and back to the stand tile); before it
        comes, reposition 2-4 steps out and back. True when it walked; the trip's
        episode row counts `stationary_clears` / `repositions` and their `stationary_s`."""
        t0 = time.monotonic()
        kind = self.still.handle(self.link.state(), stand, True, self.doing)
        if kind is None:
            return False
        key = "stationary_clears" if kind == "penalty" else "repositions"
        self.stats[key] = self.stats.get(key, 0) + 1
        self.timing["stationary_s"] += time.monotonic() - t0
        return True

    def chopped(self, c0: float, wait0: float):
        """Count the time since c0 as chopping (attempts, captchas, the pause
        between attempts), minus speech holds in it (they scale with time, not
        with attempts; lumber_opt rescales chopping to the success chance)."""
        held = self.stats.get("speech_wait_s", 0.0) - wait0
        self.timing["chop_s"] += max(0.0, time.monotonic() - c0 - held)

    # ------------------------------------------------------------ escaping monsters
    def guarded(self, fn):
        """fn(), run again after each monster escape (ESCAPES_PER_TRIP bounds it)."""
        while True:
            try:
                return fn()
            except Escape as e:
                self.escape(e)

    def out_of_reach(self, x, y) -> bool:
        """(x, y) is beyond the reach (zone_r) of every monster escaped from this
        trip, at its live position when in view and where it was when we escaped."""
        mobs = ((self.link.last or {}).get("world") or {}).get("mobiles") or {}
        for key, (pos, tiles) in list(self.danger.items()):
            m = (mobs.get(f"0x{key:08X}") or {}) if isinstance(key, int) else {}
            if m.get("x") is not None:
                pos = (m["x"], m["y"])
                self.danger[key] = (pos, tiles)
            if cheb(pos, (x, y)) <= tiles:
                return False
        return True

    def escape(self, e: Escape):
        """Walk away from e's monsters to a tile beyond each one's zone (zone_r: its
        flee radius or, for a ranged one, CREATURE_SPELL_RANGE, + ESCAPE_MARGIN;
        escape_tiles), then check that none followed: one still in flee range, or a
        ranged one within its reach, stops the run ('it kept coming'). The zones
        stay for the rest of the trip, around the creature and around where it was."""
        st = self.link.state()
        mobs = st["world"]["mobiles"]
        self.escapes += 1
        self.stats["escapes"] = self.stats.get("escapes", 0) + 1
        self.creature["escapes"] += 1
        for t in e.monsters:
            m = mobs.get(f"0x{t.serial:08X}") or {}
            if m.get("x") is not None:
                zone = ((m["x"], m["y"]), self.zone_r(t))
                self.danger[t.serial] = self.danger[("was", t.serial)] = zone
                self.mover.danger[t.serial] = self.mover.danger[("was", t.serial)] = zone   # routes bend around
                travel_guard.record(self.memory, st, t, (m["x"], m["y"]), job="lumber")
        names = ", ".join(t.name or f"0x{t.serial:08X}" for t in e.monsters)
        goals = self.escape_tiles(st)
        log(f"ESCAPE {self.escapes}/{ESCAPES_PER_TRIP}: {e.summary}; backing away to {goals[0]}")
        self.mode = "escape"
        try:
            for i, goal in enumerate(goals):
                self.doing("escape", f"Backing away from {names}", goal)
                try:
                    self.mover.walk_to(lambda: goal, 1, "escape", max_moves=80)
                    break
                except Abort as x:
                    if "no route" not in str(x) or i == len(goals) - 1:
                        raise
                    log(f"escape: no route to {goal}; trying another way")
        finally:
            self.mode = "work"
        self.swingers.clear()                # the swings that caused this escape are dealt with
        st = self.link.state()
        a = self.watch.update(st, recall_s=RECALL_S, margin_s=THREAT_MARGIN_S)
        self.last_threats = a
        fled = {t.serial for t in e.monsters}
        still = [t for t in a.flee if t.kind == "monster"]
        still += [t for t in a.threats if t.serial in fled and t not in still and 0 <= t.distance
                  and t.reach > self.watch.params.monster_strike_range and t.distance <= t.reach]
        if still:
            self.monster_stop(st, a, still[0], {}, "it kept coming after the escape")
        self.run_arrived = time.time()
        log(f"escaped to {tuple(st['movement']['pos'][:2])}; carrying on")

    def escape_tiles(self, st) -> list:
        """Escape goals, best first: up to two tiles walked before (walk memory)
        out of every escaped-from monster's reach, within 60 degrees of straight
        away from them, nearest first; then the first such tile straight away
        and 45 degrees to either side."""
        pos = tuple(st["movement"]["pos"][:2])
        zones = list(self.danger.values())

        def clear(t):
            return all(cheb(t, z) > r for z, r in zones)
        vx = vy = 0.0
        for (zx, zy), _ in zones:
            n = math.hypot(pos[0] - zx, pos[1] - zy) or 1.0
            vx, vy = vx + (pos[0] - zx) / n, vy + (pos[1] - zy) / n
        if vx == vy == 0.0:
            vx = 1.0
        vn = math.hypot(vx, vy)
        known = []
        for t in self.mover.mem_for(st["world"]["self"].get("map")).tiles:
            d = math.hypot(t[0] - pos[0], t[1] - pos[1])
            if d and cheb(t, pos) <= 40 and clear(t) \
                    and ((t[0] - pos[0]) * vx + (t[1] - pos[1]) * vy) / (d * vn) >= 0.5:
                known.append(t)
        goals = sorted(known, key=lambda t: cheb(t, pos))[:2]
        for deg in (0, 45, -45):
            c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
            ux, uy = (vx * c - vy * s) / vn, (vx * s + vy * c) / vn
            for k in range(1, 80):
                t = (pos[0] + round(ux * k), pos[1] + round(uy * k))
                if clear(t):
                    if t not in goals:
                        goals.append(t)
                    break
        return goals

    # ------------------------------------------------------------ converting
    def convert(self):
        ok_text = self.k["convert"]["ok_text"]
        for _ in range(4):
            st = self.state()
            stacks = self.in_pack(st, LOGS)
            if not stacks:
                return
            serial, it = stacks[0]
            if "woods" not in self.stats:           # this trip's logs by wood type (ledger.py, woods.json)
                self.stats["woods"] = self.ledger.summary(kind="log")
            self.doing("convert", f"Making boards from {it.get('amount') or 1} logs")
            self.open_for((serial, False))              # the logs are targeted in the open backpack
            cur = self.use_hatchet()
            if cur is None:
                continue
            self.human.wait("aim")
            mark = len(self.link.events)
            self.ledger.expect(("consumed", serial))     # the log stack becomes boards: not theft
            self.link.act(actions.target_object(cur["cursor_id"], serial, it.get("x") or 0,
                                                it.get("y") or 0, 0, it["graphic"],
                                                cursor_type=cur["cursor_type"]))
            if self.wait_for(lambda s: self.heard(mark, text=ok_text), 5.0) is None:
                raise Abort(f"log stack 0x{serial:08X} did not convert")
            log(f"converted {it.get('amount') or 1} logs to boards")
            self.human.wait("between")
        raise Abort("logs left after 4 conversions")

    def open_for(self, *needs):
        """Open what the client must show first (agent_link.containers_to_open;
        needs are (serial, itself) pairs), like a player opening the bag."""
        world, todo = self.link.state()["world"], []
        for serial, itself in needs:
            todo += [s for s in containers_to_open(world, serial, itself) if s not in todo]
        if todo:
            self.link.open_containers(todo, self.human)

    def tree_z_ok(self, tree):
        """Stand on the tree's level (not in a cave under it); map planner only."""
        if not self.mover.use_map:
            return None
        it = uomap.tiledata().item(h(tree["graphic"]))
        return reach_z(tree["z"], it.height if it else 0)

    # ------------------------------------------------------------ the bank
    def banker_mobile(self):
        return self.link.state()["world"]["mobiles"].get(self.k["npcs"]["banker"]["serial"]) or {}

    def banker_pos(self):
        """Where the banker stands now (world model), else the demo position."""
        m = self.banker_mobile()
        if m.get("x") is not None:
            return (m["x"], m["y"])
        return tuple(self.k["npcs"]["banker"]["pos"][:2])

    def banker_z(self) -> int:
        z = self.banker_mobile().get("z")
        return z if z is not None else self.k["npcs"]["banker"]["pos"][2]

    # ------------------------------------------------------------ travel (spots reached by recall)
    def go_out(self):
        """Witcher-rune spots (spot access {"method": "witcher", "rune": N}): unless we
        stand in the spot's area already, walk to the rune library, stand by the tome
        that holds rune N and recall to it (escape.recall: one of the tome's public
        charges, else our own spell; docs/research/WORLD_LOCATIONS.md). The 60 s
        harvest lockout after it is waited out by the first chop (outcome 'lockout').
        Every attempt is a `travel` job event (travel_leg), a failed walk or recall too."""
        access = self.k["spot"].get("access") or {}
        if access.get("method") != "witcher":
            return
        st = self.state()
        area = self.k["harvest"]["area"]
        pos = self.link.pos(st)
        if cheb(pos, area["center"]) <= area["radius"] + 10:
            return
        rune = places.witcher_rune(access["rune"])
        lib = places.library(access.get("library", "cambria"))
        leg = {"leg": "out", "witcher_rune": rune["id"], "library": lib["id"], "book": rune["tome"]}
        t0 = time.monotonic()
        # No distance limit (user decision 2026-10-03): the walk goes as far as the map planner
        # routes; "no route" from far away still aborts (bring us closer by moongate first).
        tome = next(t for t in lib["tomes"] if t["serial"] == rune["tome"])
        self.doing("to_library", f"Walking to the {lib['name']}", tuple(tome["pos"][:2]))
        try:
            self.mover.walk_to(lambda: tuple(tome["pos"][:2]), lib["use_range"] - 1, "to the rune library")
            if self.wait_for(lambda s: rune["tome"] in s["world"]["items"], 3.0) is None:
                raise Abort(f"the tome {rune['tome']} for rune {rune['id']} isn't at the {lib['name']} "
                            f"({tome['pos'][:2]})")
        except Abort as e:
            self.travel_leg(leg, t0, failure=f"walk: {e}")
            raise
        leg["walk_s"] = round(time.monotonic() - t0, 1)
        self.doing("recall_out", f"Recalling to rune {rune['id']} ({rune['name']})", (rune["x"], rune["y"]))
        self.human.wait("use")
        before = self.supplies_now(self.link.state())
        try:
            res = escape_mod.escape(escape_mod.LinkIO(self.link), int(rune["tome"], 16), attempts=2, log=log,
                                    rune=rune["id"])
        except escape_mod.RecallError as e:
            self.travel_leg(leg, t0, failure=f"recall: {e}")
            raise Abort(f"library recall to rune {rune['id']} not possible: {e}")
        self.travel_leg(leg, t0, res, before)
        if not res["ok"]:
            raise Abort(f"library recall to rune {rune['id']} failed: {res['failure']}")
        log(f"recalled to rune {rune['id']} ({rune['name']}) at {tuple(res['to'])} ({res['method']})")

    def go_home(self):
        """Spots with home {"method": "recall"}: recall to the default rune of our
        book (the same one the red escape uses), unless the banker is already near;
        open_bank walks the rest. A `travel` job event either way it goes."""
        if (self.k["spot"].get("home") or {}).get("method") != "recall":
            return
        if cheb(self.link.pos(self.link.state()), self.banker_pos()) <= HOME_NEAR:
            return
        if self.recall_book is None:
            raise Abort("this spot goes home by recall, and there is no runebook or rune tome ready")
        self.doing("recall_home", "Recalling home")
        self.human.wait("use")
        leg, t0 = {"leg": "home", "book": f"0x{self.recall_book:08X}"}, time.monotonic()
        before = self.supplies_now(self.link.state())
        try:
            res = escape_mod.escape(escape_mod.LinkIO(self.link), self.recall_book, attempts=3, log=log)
        except escape_mod.RecallError as e:
            self.travel_leg(leg, t0, failure=f"recall: {e}")
            raise Abort(f"recall home not possible: {e}")
        self.travel_leg(leg, t0, res, before)
        if not res["ok"]:
            raise Abort(f"recall home failed: {res['failure']}")
        self.afield = False

    def supplies_now(self, st) -> dict:
        """Mana and reagents in the pack now (travel legs: what a recall cost)."""
        counts, _ = combat.reagents(st["world"], self.self_serial(st))
        return {"mana": (st["world"].get("self") or {}).get("mana"),
                "reagents": {combat.REAGENTS[g]: n for g, n in counts.items()}}

    def travel_leg(self, leg: dict, t0: float, res: dict | None = None, before: dict | None = None,
                   failure: str | None = None):
        """One travel leg (out by a library tome, home by our book): a `travel` job
        event with the trip, the spot, the book, how long it took (`s`, the walk to the
        library included, `walk_s`), escape.escape's result (method, every try, the
        charges the book showed) or why it couldn't be tried, and the mana and reagents
        it cost; its summary goes into the trip row's `travel`."""
        st = self.link.state()
        data = {**leg, "trip": self.trip_n, "spot": self.k["spot"]["id"], "s": round(time.monotonic() - t0, 1)}
        if res is not None:
            self.expect_casts(res)
            data.update({k: v for k, v in res.items() if k not in data})
        else:
            data.update(ok=False, failure=failure, attempts=0, tries=[])
        if before is not None:
            after = self.supplies_now(st)
            if before["mana"] is not None and after["mana"] is not None:
                data["mana_used"] = before["mana"] - after["mana"]
            used = {k: n - after["reagents"].get(k, 0) for k, n in before["reagents"].items()}
            data["reagents_used"] = {k: n for k, n in used.items() if n > 0}
        self.memory.job_event("lumber", "travel", data, **self._where(st))
        self.travel.append(leg_summary(data))

    def expect_casts(self, res: dict):
        """Recall casts by spell spend one of each recall reagent: tell the ledger, so a
        tome without charges doesn't read as theft."""
        casts = sum(1 for t in res.get("tries") or []
                    if t.get("method") == "spell" and t.get("failure") not in escape_mod.NOT_CAST)
        if casts:
            self.ledger.expect(*[("spent", g, casts) for g in combat.SPELL_REAGENTS[escape_mod.RECALL]])

    def open_bank(self) -> int:
        """Walk up to where the banker stands now and say "bank"; the bank box
        serial once the server has opened it (0x24). NPCs move, so the demo
        position is only the fallback (the innkeeper's lesson, LUMBER_LOOP.md §13).
        A spot whose way home is a recall recalls first (go_home)."""
        self.go_home()
        self.track_ensure("home")
        self.doing("to_bank", "Going to the bank: heading to the banker", self.banker_pos())
        self.mover.walk_to(self.banker_pos, self.args.bank_range, "to the banker",
                           z_ok=same_floor(self.banker_z()))
        self.doing("open_bank", "Opening the bank box", self.banker_pos())
        self.human.wait("speak")
        mark = len(self.link.events)
        self.link.act(actions.say_unicode("bank"))
        st = self.wait_for(lambda s: bank_opened(s["world"], self.self_serial(s), self.since(mark)), 5.0)
        if st is None:
            raise Abort("the bank box did not open (no banker in range?)")
        box = bank_opened(st["world"], self.self_serial(st), self.since(mark))
        log(f"bank box opened (0x{box:08X})")
        self.afield = False
        return box

    def deposit(self, box: int) -> int:
        """Drag every board stack from the open backpack into the open bank box,
        right after it opened: no step in between (moving closes a bank box in
        RunUO [INFERENCE for Outlands])."""
        stored = 0
        stacks = self.in_pack(self.state(), BOARDS)
        if stacks:  # the bank gump is open from the speech; the backpack may still need opening
            self.open_for(*[(serial, False) for serial, _ in stacks])
        for serial, it in stacks:
            amount = it.get("amount") or 1
            self.doing("store", f"Banking {amount} boards", self.banker_pos())
            self.human.wait("use")
            self.ledger.expect(("moved_out", serial))    # into the bank box: not theft
            self.link.act(actions.lift(serial, amount))
            self.human.wait("drag")
            self.link.act(actions.drop(serial, DROP_AUTO, DROP_AUTO, 0, 0, box))
            pack = self.backpack(self.link.state())
            moved = self.wait_for(
                lambda s: (self.item(s, serial) is None
                           or serial_of(self.item(s, serial).get("container") or "0") != pack), 4.0)
            if moved is None:
                raise Abort(f"board stack 0x{serial:08X} did not leave the backpack")
            stored += amount
            log(f"banked {amount} boards")
        self.stats["stored"] = self.stats.get("stored", 0) + stored
        return stored

    # ------------------------------------------------------------ trips
    def episode(self, row):
        self.memory.episode("lumber", row)

    def trip(self, n):
        """One trip: harvest -> convert -> walk to the banker and open the bank
        box -> bank the boards. The run ends at the bank. A monster escape in
        any phase is followed by that phase again (the harvest goes on at the
        next stand out of reach); an abort while harvesting converts the carried
        logs first when that's safe (salvage). Every trip leaves an episode row,
        an aborted one too (outcome 'aborted' + why): leaving those out would
        flatter exactly the spots where trips get cut short."""
        self.stats = {}
        self.trip_n = n
        self.escapes, self.danger = 0, {}
        self.mover.danger = {}
        self.run_arrived, self.creature, self.avoided = None, self.new_creature_tally(), set()
        t0, s0, b0 = time.time(), self.mover.steps, self.mover.blocked_count
        self.trip_t0 = t0
        self.timing = {"walk_out_s": None, "chop_s": 0.0, "tree_walk_s": 0.0, "lockout_s": 0.0, "stationary_s": 0.0}
        self.travel, self.players_seen, self.hatchet_worn = [], {}, None
        self.trk.reset()
        phases = {}
        st = self.link.state()
        snap = self.snapshot(st)
        self.reagents0 = self.supplies_now(st)["reagents"]
        outcome, why = "aborted", None

        def timed(name, fn, retry=True):
            t = time.time()
            try:
                return self.guarded(fn) if retry else fn()
            finally:
                phases[name] = round(phases.get(name, 0.0) + time.time() - t, 1)

        try:
            try:
                timed("harvest", self.harvest_trip)
            except Abort as e:
                self.salvage(e)
                raise
            timed("convert", self.convert)

            def bank():
                box = timed("to_bank", self.open_bank, retry=False)
                timed("store", lambda: self.deposit(box), retry=False)
            self.guarded(bank)              # an escape after the box opened: walk back and say bank again
            outcome = "banked"
        except BaseException as e:
            why = str(e) if isinstance(e, Abort) else f"{type(e).__name__}: {e}"
            raise
        finally:
            if self.break_due:
                self.stats["break_due"] = True
            row = {"loop": "lumber", "spot": self.k["spot"]["id"], "trip": n, "outcome": outcome, "why": why,
                   "t_start": round(t0, 1), "t_end": round(time.time(), 1), "phases_s": phases,
                   **{k: None if v is None else round(v, 1) for k, v in self.timing.items()},
                   "steps": self.mover.steps - s0, "blocked": self.mover.blocked_count - b0,
                   "doors_opened": self.mover.doors_opened,
                   "human_session": dict(self.human.stats), **snap,
                   "hatchet": row_hatchet(snap["hatchet"], self.hatchet_worn), "carried_end": self.carried(),
                   **self.trip_end(snap), "tracking": self.trk.tally(), "creature": dict(self.creature),
                   **self.stats}
            self.episode(row)
            log(f"trip {n} {outcome}: {row}")
        self.doing("trip_done", f"Trip {n} done: {self.stats.get('logs', 0)} logs, "
                                f"{self.stats.get('stored', 0)} boards banked")

    def trip_end(self, snap: dict) -> dict:
        """The rest of the trip row (docs/LUMBER_LOOP.md "What the optimizer learns
        from"): Lumberjacking at the end and the gain, the weight carried, the travel
        legs and their time, the supplies used (library and own book charges, recall
        casts, reagents gone from the pack), the players seen (crowding) and the uses
        left on the hatchet as last seen in its label."""
        out = {"travel": self.travel, "travel_s": round(sum(leg.get("s") or 0.0 for leg in self.travel), 1),
               "players_seen": len(self.players_seen),
               "players": sorted({n for n in self.players_seen.values() if n})[:10]}
        tries = [(leg, t) for leg in self.travel for t in leg["tries"]]
        out["supplies"] = {
            # a charge is spent when the recall lands [INFERENCE: RunUO takes it in the spell's effect]
            "library_charges": sum(1 for leg, t in tries if t[0] == "charge" and t[1] is None and leg["leg"] == "out"),
            "own_charges": sum(1 for leg, t in tries if t[0] == "charge" and t[1] is None and leg["leg"] != "out"),
            "recall_casts": sum(1 for _, t in tries if t[0] == "spell")}
        hatchet = (snap.get("hatchet") or {}).get("serial")
        try:
            st = self.link.state()
            me = st["world"].get("self") or {}
            skill = lumber_opt.skill_value(me)
            out["skill_end"] = skill
            if skill is not None and snap.get("skill") is not None:
                out["skill_gain"] = round(skill - snap["skill"], 1)
            out["weight_end"] = me.get("weight")
            now = self.supplies_now(st)["reagents"]
            out["supplies"]["reagents_used"] = {k: n - now.get(k, 0) for k, n in self.reagents0.items()
                                                if n - now.get(k, 0) > 0}
        except (OSError, ValueError, KeyError, Abort):
            pass
        if hatchet is not None:      # the newest "(N uses remaining)" label of it (clicked by anyone)
            out["hatchet_uses_seen"] = self.memory.uses_seen(int(hatchet, 16))
        return out

    def carried(self) -> dict | None:
        """Logs and boards left in the backpack (what a death now would lose)."""
        try:
            st = self.link.state()
            return {"logs": self.count(st, LOGS), "boards": self.count(st, BOARDS)}
        except (OSError, ValueError, KeyError, Abort):
            return None

    def salvage(self, e: Abort):
        """Carried wood is always boards: before a harvest abort ends the run,
        convert the logs in the pack, unless stopping at once is safer (unsafe_stop).
        Only players and death interrupt it (mode 'salvage': no timeout, HP,
        creature or speech checks); a failed conversion is logged, not raised."""
        why = self.unsafe_stop(e)
        if why:
            log(f"stopping at once, logs not converted: {why}")
            return
        try:
            if not self.in_pack(self.link.state(), LOGS):
                return
            log(f"converting the carried logs before stopping ({e})")
            self.mode = "salvage"
            self.convert()
        except Abort as x:
            log(f"could not convert the carried logs: {x}")
        finally:
            self.mode = "work"

    def unsafe_stop(self, e: Abort) -> str | None:
        """Why the run must stop without converting first, or None: a player/red
        threat, death or a captcha restriction (Unsafe), the agent gate closed
        (kill, daily budget), an open gm_suspected juncture, or a speech hold
        (the overseer has control)."""
        if isinstance(e, Unsafe):
            return str(e)
        gate = (self.link.last or {}).get("gate") or {}
        if gate.get("blocked"):
            return f"agent gate closed ({gate.get('reason')})"
        if alerts.open_gm(self.memory):
            return "possible staff nearby (gm_suspected open)"
        if self.holding:
            return "speech hold: the overseer has control"
        return None

    def run(self):
        st = self.link.wait(lambda s: s["movement"]["pos"] is not None
                            and s["movement"]["self_serial"] is not None, 5.0)
        if st is None:
            raise Abort("proxy has no player position yet (log in first)")
        self.guarded(lambda: self.check_guards(self.link.state()))
        self.hatchet(st, self.want_hatchet)
        self.recall_book = self.prepare_recall(st)
        self.guarded(lambda: self.track_ensure("start"))   # its human pauses watch for threats (pause)
        # routes bend around where hostile creatures were seen lately (travel_guard)
        self.mover.danger_tiles = travel_guard.remembered_tiles(self.memory, self.facet, self.link.pos(st)[:2])
        for n in range(1, self.args.trips + 1):
            self.trip(n)
            if self.break_due:
                log(f"break due: banked after trip {n}; stopping for the break (ctl break)")
                self.doing("break_due", "Break due: boards banked; waiting at the bank for the break")
                return
        log(f"loop complete: {self.args.trips} trip(s); waiting at the bank")
        self.doing("done", f"Finished: {self.args.trips} trip(s); waiting at the bank")


def stop_intent(loop, text):
    """Last words for the visualizer; the proxy may already be gone."""
    try:
        loop.doing("stopped", text[:200])
    except (OSError, ValueError, Abort):
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--spot", required=True,
                    help="lumber spot id (harness/data/lumber_spots.json or the store; `ctl lumber spots`)")
    ap.add_argument("--spots", default=lumber_opt.SEEDS, help="seed spot file (tests)")
    ap.add_argument("--witcher", default=places.WITCHER, help="Witcher rune table (tests: a simulated library)")
    ap.add_argument("--trips", type=int, default=1)
    ap.add_argument("--logs-per-trip", type=int, default=15)
    ap.add_argument("--hatchet", default=None,
                    help="use only a hatchet of this material[+quality], e.g. copper or copper+exceptional "
                         "(harness/data/hatchets.json); default: the worn one, else the shallowest in the pack")
    ap.add_argument("--max-attempts-per-stand", type=int, default=60,
                    help="Smart Harvest attempts at one stand before moving on even though the server still "
                         "chops (a stay normally ends at 'nothing nearby'; Stationary Penalty repositions "
                         "happen in between)")
    ap.add_argument("--max-trees", type=int, default=0, help="candidate trees per trip (0 = every one in the area)")
    ap.add_argument("--regrow-min", type=float, default=lumber_opt.REGROW_DEFAULT_MIN,
                    help="skip a tree for this long after it ran dry or a stand by it said nothing nearby "
                         "(`ctl lumber plan` passes the "
                         "estimate from harvest memory)")
    ap.add_argument("--attempt-timeout", type=float, default=10.0)
    ap.add_argument("--human", choices=sorted(PROFILES), default="normal",
                    help="human-texture profile (humanize.py); 'off' for deterministic tests")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--no-map", action="store_true",
                    help="plan on walk memory only (offline tests against simulated worlds)")
    ap.add_argument("--human-fast", type=float, default=1.0,
                    help="scale human delays (offline tests of the normal profile only)")
    ap.add_argument("--captcha-timeout", type=float, default=600.0)
    ap.add_argument("--captcha-max-strikes", type=int, default=2,
                    help="wrong auto-solve answers tolerated before the pause + beep fallback")
    ap.add_argument("--captcha-beep-s", type=float, default=30.0)
    ap.add_argument("--quiet", action="store_true", help="no handoff sound (tests)")
    ap.add_argument("--triage-url", default=triage.DEFAULT_URL,
                    help="laya-serve for speech triage (triage.py); empty = off")
    ap.add_argument("--bank-range", type=int, default=4,
                    help="walk to within this many tiles of the banker's current position")
    ap.add_argument("--recall", choices=("require", "off"), default="require",
                    help="red escape by recall (escape.py): where players can attack (pvp spots) a runebook "
                         "or rune tome with a default rune and a charge or a castable Recall is required to "
                         "start; 'off' runs without it (a red then only stops the run)")
    ap.add_argument("--track", choices=("reds", "off"), default="reds",
                    help="keep Tracking's Hunting mode on murderer players all run (tracking.py); murderer hits "
                         "within --track-react-range at a pvp spot send us home like a red in view")
    ap.add_argument("--track-react-range", type=int, default=80,
                    help="tiles (Chebyshev to the tracking arrow) within which a tracked red triggers the escape; "
                         "farther ones (reds in their houses) are logged only. 80 (user decision 2026-10-03): a "
                         "mounted red covered 55 tiles in ~5.5 s and killed us after the old 40 only logged him")
    ap.add_argument("--track-retry-s", type=float, default=30.0,
                    help="at most one try to turn Hunting back on per this many seconds")
    ap.add_argument("--creature-recall-at", type=float, default=0.6,
                    help="damage from a creature with hits at or above this fraction of max: walk out of its reach "
                         "and chop on; below it (or two attackers, a re-hit soon after the walk-away, no escapes "
                         "left): recall home (LUMBER_LOOP.md §13 'Running from a creature')")
    ap.add_argument("--creature-rehit-s", type=float, default=10.0,
                    help="damage within this many seconds of arriving from a walk-away sends us home")
    ap.add_argument("--timeout", type=float, default=3600.0)
    ap.add_argument("--max-blocked", type=int, default=20)
    ap.add_argument("--control-port", type=int, default=25941)
    ap.add_argument("--state-port", type=int, default=25942)
    ap.add_argument("--loop", default=os.path.join(DATA, "loops", "lumber.json"),
                    help="common lumber knowledge (texts, captcha, conversion); the spot adds the venue")
    ap.add_argument("--memory", default=DEFAULT_DB,
                    help="harness memory (SQLite, docs/MEMORY.md): walk memory, harvest nodes, episodes")
    args = ap.parse_args()

    with open(args.loop, encoding="utf-8") as f:
        know = json.load(f)
    memory = Memory(args.memory)
    places.use(args.witcher)
    spots = lumber_opt.load_spots(memory, args.spots)
    spot = spots.get(args.spot)
    why = None
    if spot is None:
        why = f"unknown spot {args.spot!r}; known: {', '.join(sorted(spots))}"
    elif spot["status"] == "disabled":
        why = f"spot {args.spot} is disabled" + (f": {spot['reason']}" if spot.get("reason") else "")
    else:
        try:
            know = lumber_opt.spot_knowledge(know, spot)
        except ValueError as e:
            why = str(e)
    if why:
        log(f"ABORTED: {why}")
        memory.close()
        sys.exit(1)
    link = Link(args.control_port, args.state_port)
    loop = LumberLoop(link, memory, know, args)
    code = 0
    try:
        loop.run()
    except Abort as e:
        log(f"ABORTED: {e}")
        code = 1
        stop_intent(loop, f"Stopped: {e}")
    except BaseException as e:
        stop_intent(loop, f"Crashed: {type(e).__name__}: {e}")
        raise
    finally:
        memory.close()
    sys.exit(code)


if __name__ == "__main__":
    main()

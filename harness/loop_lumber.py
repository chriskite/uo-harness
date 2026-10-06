"""Lumber loop runner (docs/LUMBER_LOOP.md §3, §6, §12, §13).

One trip (docs/LUMBER_LOOP.md §12.5, user decision 2026-10-04) starts at home, in
the rental room or the guild house (harness/home.py, harness/data/homes.json): out of
the room through its door, recall to the landing nearest the grove (a rune of a rune
library we may use or of our own books, places.landings) → harvest trees (each chop's
logs dragged into a trapped pouch) → recall home with our own book's default rune →
into the rental room through the house steward → set the pouch off ourselves, open
it, convert the logs to boards → store the boards in the room's secure chest. The
run ends in the room. No bank, no deed creation.

Thieves (docs/PLAN.md "Keep thieves off the logs"; harness/pouch.py): the logs ride
in a trapped pouch (hue 38) from the chop to the room, so a thief's snoop sets it
off. A pouch going off without our double-click (the explosion around us, its
sound, or its hue 38 -> 0) recalls home like a red and keeps us off the spot for
lumber_opt.THIEF_COOLDOWN_S. A player within STEAL_GUARD tiles while we chop at a
stand is a suspected thief whatever his notoriety: the runner steps KEEP_AWAY_TILES
away after a player's reaction time (an attention `thief_near` juncture); if he closes in again,
it recalls. A run starts a trip only with a live pouch (else `low_supplies`).

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

The runner answers no other gump, except the rental room menus (harness/room.py:
into the room and out of it, buttons by their labels, never End Rental Contract or
Expand) and that its Mover closes (button 0) the gump of a moongate a route only
passes over (agent_link.Mover.close_gate_gumps). It says nothing, except "guards"
once inside a guard zone after a flight with a hostile player within 12 tiles.

Guards: jittered pacing, overall timeout, HP loss, movement stall, the agent
gate (pause/break wait, kill/budget abort), bounded retries everywhere.

Staff (speech_guard.py; docs/PLAN.md "Staff alarm on an invulnerable player in
view"): a character speaking near us while we harvest holds the job for the
overseer (`speech_nearby`); one with staff hints also raises `gm_suspected` and the
repeating staff alarm. An invulnerable player (notoriety 7 + player flag 0x20)
coming into view is a staff hint without a word: the alarm at once, a
`staff_sighting` job event on its first sighting this run, and the same hold.

Threats (threats.py; LUMBER_LOOP.md §13): a monster close enough to flee from
gets an escape (walk beyond its flee radius, then harvest at the next stand out of
its reach). Damage from a single creature at healthy hits (--creature-recall-at)
gets a run: walk out of its reach (a ranged one's: 12 tiles + margin) and chop
on at a stand outside it; damage again soon after the walk-away
(--creature-rehit-s), low hits, two attackers or no escapes left recall home.
Trees within reach of a known-aggressive creature in view are left for later.
A player/red threat, or a monster that keeps coming stops the run. An abort
while harvesting stashes loose logs in the trapped pouch when that is safe (with
no live pouch it converts them), so carried wood is protected. A break announced
by the agent gate (break_due) ends the trip early: home, convert, store, exit 0
for `ctl break` in the rental room.

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
from agent_link import (Abort, Link, Mover, cheb, containers_to_open, label_since, log, look_at, reach_z,  # noqa: E402
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
from speech_guard import SpeechGuard, staff_hints, what  # noqa: E402
import triage  # noqa: E402
import alerts  # noqa: E402
import lumber_opt  # noqa: E402
import travel_guard  # noqa: E402
import stationary  # noqa: E402
import places  # noqa: E402
import home as home_mod  # noqa: E402
import room as room_mod  # noqa: E402
import mount as mount_mod  # noqa: E402
import captcha  # noqa: E402
import combat  # noqa: E402
import tracking  # noqa: E402
import pouch  # noqa: E402
import aspects  # noqa: E402
import shelf as shelf_mod  # noqa: E402
import stockpile as stockpile_mod  # noqa: E402

RECALL_S = 2.0                # Recall cast time (docs/research/TRAVEL_DEATH.md)
NEXT_STAND_PLANS = 6          # nearest trees (straight line) whose stands next_stand() compares
RETHINK_PLANS = 3             # nearer trees (straight line) whose routes tree_rethink plans per look
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
TREE_RECHECK_S = 5.0          # on the way to a tree, look this often for a nearer clear one (work_stand)
TREE_SWITCH_GAIN = 8          # ... at least this many tiles nearer than the one we walk to
LOCAL_TREES_R = 15            # trees this close to us join the candidates when the spot's are all guarded or
#                               after an escape (local_trees; user, 2026-10-05: chop the trees where we are)
TREE_DROP_COOLDOWN_S = 120.0  # a tree dropped because a zone covered it waits this long (next_stand)
RECENT_ZONE_S = 60.0          # a creature that left the view keeps its zone at its last tile this long (tree_guards)
CONVERT_RETRIES = 3           # hatchet uses without a cursor tolerated while converting (convert)
DIR_NAMES = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
AT_GROVE = 10                 # tiles beyond the area's radius that count as being at the grove already (no travel)
LANDING_SLACK = 3             # tiles from the chosen rune's tile a recall out may land (live: on the tile) before it is wrong
# the stash's lift waits this long after the chop's target (live 2026-10-05 10:09:30: a lift 0.47 s after the
# hatchet's double-click got "You must wait to perform another action." and the logs stayed loose)
STASH_AFTER_S = 0.8
KEEPER_SEARCH = 18            # tiles around us whose human NPCs are clicked to find the house steward
KEEPER_CLICKS = 12
CHEST_REACH = 2               # tiles from the home chest to drop into it [INFERENCE: RunUO's 2-tile item reach]
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
RECALL_ALERT_TRIES = 2        # failed recalls away from a creature before the urgent keep_running juncture
RECALL_RETRY_S = 10.0         # between recall casts while nothing is after us (run_and_recall)
KEEP_RUNNING_LOOK_S = 0.5     # how often run_and_recall looks around while nothing is after us
# Creatures we can see (user, 2026-10-05: "just not move into aggro range of mobs we can see while
# lumbering"): RunUO's monsters perceive within 10 tiles (BaseCreature rangePerception, the usual
# spawn argument) [INFERENCE for Outlands]; trees and routes keep AGGRO_R from every one in view
AGGRO_R = 13
# An escape runs far, not a few steps (user, 2026-10-05: "a few steps isn't going to break aggro"):
# its goal is at least ESCAPE_RUN tiles from what we flee [INFERENCE: past perception and view]
ESCAPE_RUN = 20
# Before a recall away from creatures, run until each attacker is this far: a 2 s cast next to them
# is disturbed, and a caster's spells reach 12 (threats.CREATURE_SPELL_RANGE)
RECALL_GAP = threats.CREATURE_SPELL_RANGE + 2
RECALL_GAP_MAX_MOVES = 60
PACK_DEPTH_MAX = 16           # container nesting bound when looking for the hatchet
FLEE_MAX_MOVES = 400          # a guard flight's step bound (guards.FLEE_MAX_DIST tiles and detours)
FLEE_ARRIVAL_WAIT_S = 1.5     # after a flight arrives: how long its 500112 may still come (data.confirmed)
TREE_DETOUR = 3              # a route to a tree may be this many times its Chebyshev distance ...
TREE_ROUTE_MIN = 30          # ... or this many steps, whichever is more; longer: the next stand (work_stand)
WALK_HITS_MAX = 2            # hits that cost hits while walking away from a creature: this many, home (hit_verdict)
ESCAPE_DETOUR = 2            # an escape route may be this many times its goal's distance ...
ESCAPE_ROUTE_MIN = 12        # ... or this many steps; none of the goals that short: recall home (escape)
# Thieves (docs/PLAN.md "Keep thieves off the logs"; docs/research/THREATS.md §7 T3): any player this
# close while harvesting is a suspected thief (they look blue until the steal; the steal needs 1 tile)
STEAL_GUARD = 2
KEEP_AWAY_TILES = 4           # the keep-away step ends at least this many tiles from every suspect
KEEP_AWAY_MOVES = 30          # its step bound


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


LEG_KEYS = ("leg", "kind", "method", "book", "library", "witcher_rune", "landing", "ok", "attempts", "s", "walk_s",
            "failure", "charges", "mana_used", "reagents_used")


def leg_summary(data: dict) -> dict:
    """A travel leg's job-event data reduced to the trip row's `travel` entry, plus
    `tries`: each cast's method, failure, and elapsed time ("charge", null, 2.2)."""
    out = {k: data[k] for k in LEG_KEYS if data.get(k) is not None}
    out["tries"] = [[t.get("method"), t.get("failure"), t.get("elapsed_s")] for t in data.get("tries") or []]
    return out


def pack_depth(items: dict, container: int, me: int, pack: int) -> int | None:
    """How deep an item whose container is `container` sits: 0 worn (on `me`),
    1 in the backpack, 2 in a bag in it, ... None elsewhere (a chest, the
    ground, a container the world model doesn't know)."""
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
    home, a recall) puts it back in the pack, so at the start it is often packed
    (live 2026-10-03, trip 1: worn False at 21:43:43, in hand from the first chop at
    21:44:39 to the end). `worn_at_start` keeps the start reading."""
    if start is None or worn_chopping is None:
        return start
    return {**start, "worn": worn_chopping, "worn_at_start": start.get("worn")}


def hit_verdict(*, hits, hits_max, recall_at: float, attackers: list, players: list, escapes: int,
                since_run_s: float | None, rehit_s: float, walking: bool = False,
                can_escape: bool = True, walk_hits: int = 0) -> str | None:
    """Damage taken (LUMBER_LOOP.md §13 "Running from a creature"): why it must send
    us home (monster_stop), or None to run from it (walk out of its reach and chop on
    at a stand outside it; while already walking away: walk on). Home when a hostile
    player is in view, nothing in view could have hit us, two or more creatures
    could have, hits are below recall_at of max, the walk-away has already taken
    WALK_HITS_MAX hits that cost hits (`walk_hits`, this one included: it outranges the
    walk; live 2026-10-05 a brackish water's -20, -16 on the way, then -44 during the late
    recall: home at 7/100), the attacker is next to us while we walk away (it caught up:
    live 2026-10-05 a hoarfrost's -38 at 1 tile, "walking on", dead 2 s later), it came within
    rehit_s of arriving from the last walk-away, or no escape is left (a speech hold,
    ESCAPES_PER_TRIP)."""
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
    if walking and walk_hits >= WALK_HITS_MAX:
        return f"hit {walk_hits} times while walking away: it outranges the walk-away"
    if walking and any(0 <= getattr(t, "distance", -1) <= 1 for t in attackers):
        return "it caught up with us while walking away"
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


class KeepAway(Exception):
    """Players within STEAL_GUARD tiles while harvesting, suspected thieves (threats `thief`):
    step out of their reach (LumberLoop.keep_away), then carry on."""

    def __init__(self, suspects):
        super().__init__(", ".join(t.name or f"0x{t.serial:08X}" for t in suspects))
        self.suspects = suspects      # [threats.Threat]


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
        # a wandering goat isn't. A player within STEAL_GUARD tiles is `thief` (acted on while harvesting)
        self.watch = threats.Watch(travel_guard.learned_params(memory, threats.Params(steal_guard=STEAL_GUARD)))
        self.last_threats = None
        self.seen_hostiles = set()
        self.ledger = ledger_mod.Ledger()
        self._intent = None          # last reported (kind, text, target), restored after a captcha
        self.speech = SpeechGuard()  # a character speaking near us hands control to the overseer
        self.pending_staff = []      # invulnerable players come into view, held for at the next speech check
        self.triage = triage.Triage(args.triage_url, log=log)  # Laya verdict per line (shadow + escalate)
        self.mode = "work"           # "work" | "escape" (walking away) | "salvage" (converting before a stop)
        #                              | "flee" (running to the guards)
        self.holding = False         # in a speech hold: the overseer has control
        self.escapes = 0             # monster escapes this trip
        self.danger = {}             # serial -> ((x, y), tiles): monsters escaped from this trip and their reach;
        #                              ("was", serial) -> the same around where it was when we escaped (fixed)
        self.run_arrived = None      # time.time() when the last walk-away ended (--creature-rehit-s)
        self.walk_hits = 0           # hits that cost hits during the current walk-away (hit_verdict walk_hits)
        self.creature = self.new_creature_tally()   # this trip's creature cost: the episode row's `creature`
        self.avoided = set()         # (tree, creature serial) pairs logged as left alone (next_stand)
        self.recent_guards = {}      # creature serial -> (threat, (x, y), zone tiles, monotonic time last seen) (tree_guards)
        self.dropped_trees = {}      # (x, y) -> monotonic time a creature's zone made us drop the walk to it
        self.no_route = set()        # (x, y) of trees no route reached this trip (no_route_tree)
        self.switch_tree = None      # (x, y) tree_rethink switched the walk to: next_stand's next pick
        self.recalled_home = False   # an escape recall landed this trip (recall_out): trip's home_after_recall
        self.swingers = {}           # attacker serial -> time of its latest swing or spell at us since the last escape
        self._swing_scan = 0         # link.events index scanned for swings and spells on us
        self.spelled = []            # (time, caster or None) of spells on us (threats.spell_on_us) not yet dealt with
        self.hit_by = []             # serials creature_hit blamed in this threat check (the junctures' `attackers`)
        self.hatchet_worn = None     # this trip: the hatchet in hand when a chop's cursor came (trip row hatchet.worn)
        self.break_due = False       # the agent gate announced a break (break_due)
        self.recall_book = None      # our book whose default rune lands at home: the way home and the red escape
        self.home_rune = None        # (x, y, facet) that default rune lands on (prepare_recall)
        self.home = None             # this character's home (harness/home.py: landing, room, chest), run()
        self.home_name = None        # the character's name it is keyed by
        self.books = []              # our own runebooks / rune tomes as read at the start (escape.read_book)
        self.out_landing = None      # the landing trips recall to (lumber_opt.landing_for), chosen once a run
        self.pre_stats = {}          # this trip's steps at home before it (resupply, mount), for its trip row
        self.mount_warned = False    # the missing-mount juncture went out this run
        self.pre_trip = None         # (time, mover steps, blocked) when resupply_home began this trip
        self.aspect_hue = aspects.HARVEST_HUE   # worn armor in this hue counts as Harvest-aspected (aspect_ensure)
        self.aspect_warned = set()   # aspect problems already posted as a juncture this run
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
        self.pops = pouch.PopWatch()   # our trapped pouches going off: ours, or a thief's (check_pouches)
        self._pop_scan = 0           # link.events index folded into self.pops
        self._pop_since = time.time()  # pops before this run started are another run's (its own set-off)
        self._stash_due = None       # monotonic: when the last chop's stash may lift (attempt, stash_now)
        self._own_pop_until = 0.0    # monotonic: our own pouch's hit is being acknowledged until then
        self.pouches_used = 0        # this trip: trapped pouches that went off (ours and a thief's)
        self.harvesting = False      # chopping at a stand (work_stand): the keep-away is on
        self.suspects = {}           # serial -> name: players we stepped away from this trip (keep_away)

    @staticmethod
    def new_creature_tally() -> dict:
        """The trip row's `creature`: escapes (walk-aways, runs from damage included), hits
        lost to creatures, whether a creature sent us home by recall and why it ended the
        trip (null when none did); runs (walk-aways after damage), hits (damage episodes),
        avoided_trees (trees left alone near a known-aggressive creature) and recall_fails
        (recalls away from a creature that failed; run_and_recall ran on after each)."""
        return {"escapes": 0, "hits_lost": 0, "recalled": False, "why": None,
                "runs": 0, "hits": 0, "avoided_trees": 0, "recall_fails": 0}

    def doing(self, kind: str, text: str, target=None):
        """Tell the visualizer what the agent is trying to do (proxy-side only)."""
        self._intent = (kind, text, target)
        self.link.intent(text, kind, target, loop="lumber", trip=self.trip_n, trips=self.args.trips)

    # ------------------------------------------------------------ guards
    def check_guards(self, st: dict):
        relaxed = self.mode in ("salvage", "flee", "gap")
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
        self.check_pouches(st)       # before the threat check: our own pouch's hit is no attack
        self.check_threats(st)
        self.pending_staff += self.staff_in_view(st)
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
        stop harvesting and finish this trip in the rental room, so the overseer can
        start the break there (`ctl break`)."""
        gate = st.get("gate") or {}
        if gate.get("break_due_at") is not None and not self.break_due:
            self.break_due = True
            left = gate.get("break_starts_in_s")
            log("break due" + (f" (it starts in {left:.0f} s)" if left is not None else "")
                + ": ending the trip in the rental room")

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
            self.check_pouches(st)
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
          - chopping at a stand, a player of any notoriety within STEAL_GUARD tiles
            (threats `thief`): thief_near (KeepAway: step out of reach; one we already
            stepped away from, closing in again: recall, thief_out)
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
        self.aggro_zones(st)
        if a.dead:
            self.died(st, "ghost body")
        if self.mode == "flee":
            if self.guards_entered(self._flee_mark):
                raise InGuards()
            return
        if self.mode == "gap":                 # running out of reach before a recall (gain_distance)
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
        # a creature that swung at us and has left the view is still a creature, not a player
        # (Watch.monsters); one never seen is read as a player, the safe side
        aggressors = [s for s in swung
                      if (by_serial[s].kind != "monster" if s in by_serial else s not in self.watch.monsters)]
        monsters = [t for t in a.flee if t.kind == "monster"]
        monsters += [by_serial[s] for s in swung if s not in aggressors and s in by_serial
                     and s not in {t.serial for t in monsters}]
        if players or aggressors:
            worst = players[0] if players else None
            if self.on_home_rune(st):
                why = "at home, on the way-home rune's tile: no recall"   # threat_stop stops here
            else:
                why = self.recall_out(st, a, worst, swung) if self.recall_book is not None else "no recall book"
                if self.k["pvp"]:
                    self.flee_to_guards(st, a, worst, swung, why)
            self.threat_stop(st, a, worst, swung, Unsafe, why)
        self.check_tracking(st, a, swung)
        if self.mode == "salvage":
            return
        if self.harvesting and self.mode == "work" and a.thieves:
            self.thief_near(st, a, step=escape)
        if a.damage["lost"] > 0 or a.damage["damage_events"] > 0 or self.spelled:
            self.creature_hit(st, a, swung, monsters, escape)
        if not monsters or self.mode == "escape":
            return
        if not escape:
            self.monster_stop(st, a, monsters[0], swung, "speech hold: no escape")
        if self.escapes >= ESCAPES_PER_TRIP:
            self.monster_stop(st, a, monsters[0], swung, f"{self.escapes} escapes this trip already")
        # one we already ran from is on us again: it hunts us, and another run only brings it along (live
        # 2026-10-05, witcher_23: an air dragon followed two escapes, then breathed twice from 9 tiles)
        back = next((t for t in monsters if t.serial in self.danger), None)
        if back is not None:
            self.monster_stop(st, a, back, swung, f"{back.name or f'0x{back.serial:08X}'} followed us after an escape")
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
        lost = a.damage["lost"]
        if walking and lost > 0:
            self.walk_hits += 1
        since_run = None if self.run_arrived is None else round(time.time() - self.run_arrived, 1)
        players = [t.name or f"0x{t.serial:08X}" for t in a.threats if t.player and t.hostile and t.distance >= 0]
        why = hit_verdict(hits=hits, hits_max=hmax, recall_at=self.args.creature_recall_at, attackers=attackers,
                          players=players, escapes=self.escapes, since_run_s=since_run,
                          rehit_s=self.args.creature_rehit_s, walking=walking, can_escape=escape,
                          walk_hits=self.walk_hits if walking else 0)
        worst = attackers[0] if attackers else (monsters[0] if monsters else None)
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

    def read_books(self, st) -> list[dict]:
        """Our own runebooks and rune tomes, read once at the start like a player leafing
        through them (escape.read_book: title, default rune, charges, every rune's
        landing tile) and remembered in the store for `ctl lumber plan`
        (places.remember_book, keyed by the character). An unreadable one is skipped."""
        books = []
        for serial, kind in escape_mod.find_books(st["world"], self.self_serial(st)):
            try:
                book = escape_mod.read_book(escape_mod.LinkIO(self.link), serial,
                                            wait=lambda: self.human.wait("read"))
            except escape_mod.RecallError as e:
                log(f"{kind} 0x{serial:08X} not read: {e}")
                continue
            places.remember_book(self.memory, self.home_name, book)
            books.append(book)
            log(f"{kind} 0x{serial:08X} {book.get('title') or ''!r}: {len(book['runes'])} rune(s), "
                f"{book['charges']} charge(s), default {book.get('default_name')!r}")
        return books

    @staticmethod
    def default_rune(book: dict) -> dict | None:
        """The rune a book recalls to without a name: its default, else its only rune (escape.recall)."""
        i = book.get("default")
        if i is None and len(book["runes"]) == 1:
            i = book["runes"][0]["i"]
        return next((r for r in book["runes"] if r["i"] == i), None) if i is not None else None

    def prepare_recall(self, st) -> int:
        """The way home, which is also the red escape (escape.py): our own book whose
        default rune lands at home (home.at_home: by the landing, e.g. Outland Dan's
        'DTF Loot Chest' rune), with a charge or a castable Recall. Every trip comes home
        by it, so no run starts without one."""
        me = self.self_serial(st)
        can_cast = escape_mod.can_cast_recall(st["world"], me, (st["world"].get("self") or {}).get("mana"))
        why = "no runebook or rune tome in the backpack"
        for book in self.books:
            serial, rune = int(book["serial"], 16), self.default_rune(book)
            if rune is None:
                why = f"{book['kind']} {book['serial']} has no default rune"
            elif rune.get("x") is None:
                why = f"{book['kind']} {book['serial']}: its default rune {rune['name']!r} shows no tile"
            elif not home_mod.at_home((rune["x"], rune["y"]), rune.get("facet") or 0, self.home):
                why = (f"{book['kind']} {book['serial']}: its default rune {rune['name']!r} lands at "
                       f"{rune['x']},{rune['y']}, not by home ({tuple(self.home['landing'][:2])})")
            elif book["charges"] <= 0 and not can_cast:
                why = f"{book['kind']} {book['serial']}: no charges and Recall can't be cast (mana/reagents)"
            else:
                log(f"way home ready: {book['kind']} {book['serial']}, default rune {rune['name']!r} "
                    f"({rune['x']},{rune['y']}), {book['charges']} charge(s)")
                self.home_rune = (rune["x"], rune["y"], rune.get("facet") or 0)
                return serial
        raise Abort(f"no way home ({why}): a trip needs our own runebook or rune tome whose default rune "
                    f"lands at home")

    def on_home_rune(self, st) -> bool:
        """In the room, or so near the way-home rune's tile that a recall wouldn't move us
        (escape.JUMP_TILES: it would read as no arrival and recast until the budget is spent)."""
        if home_mod.in_room(self.facet_now(st), self.home):
            return True
        if self.home_rune is None or self.facet_now(st) != self.home_rune[2]:
            return False
        return cheb(self.link.pos(st), self.home_rune[:2]) <= escape_mod.JUMP_TILES

    def aspect_ensure(self, where: str):
        """Head out in a suit of Harvest aspect armor (user, 2026-10-04; docs/NOTES.md
        "Aspects"). Passive first: the six armor layers worn in the aspect's hue (the
        server re-sends each piece in it on activation; a piece that left the
        character since, e.g. dropped or stored, came back in its own hue). Only when a
        piece isn't, the Aspect Mastery menu like a player (aspects.activate: armor,
        Harvest, Activate twice; 5 Arcane Essence, nothing when the server says the
        armor already has it, which then teaches the suit's hue). A missing piece, a
        failed activation or essence below the menu's warning level is logged, posted
        once per run (`low_supplies`, attention) and the trip goes on without. The
        trip row's `harvest_aspect` says what was found and done."""
        if self.args.harvest_aspect == "off":
            self.stats["harvest_aspect"] = {"ok": None, "action": "off"}
            return
        st = self.link.state()
        suit = aspects.suit(st["world"], self.self_serial(st), self.aspect_hue)
        rec = {"ok": suit["ok"], "action": "none", "missing": suit["missing"], "plain": suit["plain"]}
        self.stats["harvest_aspect"] = rec
        if suit["ok"]:
            return
        if suit["missing"]:
            rec["action"] = "missing"
            self.aspect_problem(st, "missing", f"not wearing a full armor suit for the Harvest aspect: no "
                                f"{', '.join(suit['missing'])}", suit)
            return
        log(f"harvest aspect ({where}): {', '.join(suit['plain'])} without it; activating")
        resume = self._intent
        self.doing("aspect", "Activating the Harvest aspect on the armor")
        try:
            res = aspects.activate(escape_mod.LinkIO(self.link), "armor", self.human, "harvest")
        except aspects.AspectError as e:
            res = {"ok": False, "already": False, "error": str(e), "texts": []}
        finally:
            if resume is not None:
                self.doing(*resume)
        hues = {p["hue"] for p in suit["pieces"]}
        if res.get("already") and len(hues) == 1:
            self.aspect_hue = hues.pop()     # the suit has the aspect in a hue of its own (Manage Hues)
        rec.update(ok=res["ok"], action="already" if res.get("already") else "activated" if res["ok"] else "failed",
                   charges=res.get("charges"), error=res.get("error"))
        st = self.link.state()
        self.memory.job_event("lumber", "aspect", {"where": where, "trip": self.trip_n, **rec,
                                                   "texts": res.get("texts"), "aspect_hue": self.aspect_hue},
                              **self._where(st))
        if not res["ok"]:
            self.aspect_problem(st, "failed", f"Harvest aspect not activated: {res.get('error')}", suit)
            return
        log(f"harvest aspect: {'already on' if res.get('already') else 'activated'}; "
            f"{res.get('charges')} Arcane Essence charges")
        low = res.get("warn_below") or 50
        if res.get("charges") is not None and res["charges"] < low:
            self.aspect_problem(st, "essence", f"Arcane Essence low: {res['charges']} charges (warning below {low}); "
                                "at 0 every aspect is lost", suit, item="arcane essence",
                                have=res["charges"], need=low)

    def aspect_problem(self, st, key: str, text: str, suit: dict, item: str = "harvest aspect armor",
                       have=None, need=None):
        """Log an aspect problem; the first of its kind this run is also a `low_supplies`
        juncture (attention) for the overseer."""
        log(f"harvest aspect: {text}")
        if key in self.aspect_warned:
            return
        self.aspect_warned.add(key)
        self.memory.juncture("lumber", "low_supplies", text, "attention",
                             {"item": item, "have": have, "need": need, "why": key, "trip": self.trip_n,
                              "missing": suit["missing"], "plain": suit["plain"], "pieces": suit["pieces"],
                              "how": "ctl act aspect activate armor harvest (all six armor pieces worn)"})

    def monster_stop(self, st, a, worst, swung, why: str):
        """A creature ends the run: under attack or with monsters closing in there is no
        time to convert logs (live 2026-10-03, witcher_291: 85 -> 40 hits during a 12 s
        conversion, then the run exited in the field and the overseer's recall landed at
        15/100). So: with a book, unless a recall wouldn't move us (on_home_rune: in the room
        or on the way-home rune's tile), run_and_recall (it returns only when we died); else
        out of the room run out of reach while anything is after us (run_clear). Then stop
        without converting (Unsafe). The trip row's `creature` gets the why and whether the
        recall landed."""
        self.creature["why"] = why
        if self.recall_book is not None and not self.on_home_rune(st):
            self.run_and_recall(st, a, worst, swung, why)
            why = f"{why}; died while running from it"
        elif not home_mod.in_room(self.facet_now(st), self.home):
            self.run_clear(st, a, swung)
        st = self.link.state()
        a = self.watch.update(st, recall_s=RECALL_S, margin_s=THREAT_MARGIN_S)
        self.threat_stop(st, a, worst, swung, Unsafe, why)

    def run_clear(self, st, a, swung):
        """No recall to make: run out of reach (gain_distance) until nothing is after us, we
        die, or there is nowhere left to run."""
        while not a.dead:
            steps = self.mover.steps
            if not self.gain_distance(st, a, swung) or self.mover.steps == steps:
                return
            st = self.link.state()
            a = self.watch.update(st, recall_s=RECALL_S, margin_s=THREAT_MARGIN_S)

    def run_and_recall(self, st, a, worst, swung, why: str):
        """Run out of reach (gain_distance), cast Recall once, and again: until a recall lands
        (recall_out raises Unsafe) or we die (returns). Never standing still with a creature
        after us (user, 2026-10-05: "we're on a horse and can outrun any mob in the overworld. If
        we fail to recall, we should just run away", even on an island with recall failing and a
        demon chasing). With something after us: run, then one cast, then run again. With
        nothing within RECALL_GAP: escape.escape's own recasting (its budget, standing); when
        that gave up, the next try RECALL_RETRY_S later (mana comes back), running whenever
        something comes after us meanwhile. After RECALL_ALERT_TRIES failed recalls an urgent
        `threat` juncture (action 'keep_running') asks the overseer to find out why; it stops
        the task when it must (ctl stop)."""
        fails, gave_up = 0, None
        while not a.dead:
            ran = self.gain_distance(st, a, swung)
            st = self.link.state()
            a = self.watch.update(st, recall_s=RECALL_S, margin_s=THREAT_MARGIN_S)
            if a.dead:
                return
            if not ran and gave_up is not None and time.monotonic() - gave_up < RECALL_RETRY_S:
                time.sleep(KEEP_RUNNING_LOOK_S)          # nothing after us: watch until the next try
                st = self.link.state()
                a = self.watch.update(st, recall_s=RECALL_S, margin_s=THREAT_MARGIN_S)
                continue
            try:
                fail = self.recall_out(st, a, worst, swung, pk=False, why=why, attempts=1 if ran else None)
            except Unsafe:
                self.creature["recalled"] = True
                raise
            if not ran:
                gave_up = time.monotonic()
            fails += 1
            self.creature["recall_fails"] = fails
            log(f"{fail}; running on")
            if fails == RECALL_ALERT_TRIES:
                pos = tuple(self.link.pos(self.link.state())[:2])
                self.memory.juncture("lumber", "threat", f"Recall failing ({fail}) with {self.threat_name(worst)} "
                                     f"after us at {pos}: still running from it", "urgent",
                                     {"action": "keep_running", "why": why, "failure": fail, "fails": fails,
                                      "pos": list(pos), "trip": self.trip_n, "spot": self.k["spot"]["id"],
                                      "threat": worst.to_dict() if worst else None})
            st = self.link.state()
            a = self.watch.update(st, recall_s=RECALL_S, margin_s=THREAT_MARGIN_S)

    @staticmethod
    def threat_name(t) -> str:
        return "a creature" if t is None else (t.name or f"0x{t.serial:08X}")

    def gain_distance(self, st, a, swung) -> bool:
        """Before a recall away from creatures: run (urgent: no pauses, running while
        stamina allows) until each creature after us (hostile ones in view within
        RECALL_GAP, and those swinging or casting at us) is RECALL_GAP tiles off or out of
        view, at most RECALL_GAP_MAX_MOVES steps in all, toward escape_tiles' goals (ESCAPE_RUN
        from where they are), new goals from where they are now each time one is reached and
        they followed (live 2026-10-05, Prevalia Gate: the first goal reached, the ratmen 9 tiles
        behind, the recall came there and cost 34 hits). Only death interrupts it (mode 'gap').
        True when it walked. Live 2026-10-05: a recall cast with a brackish water hitting lost 44
        hits in 2.2 s, and a hoarfrost caught up with a short walk-away and killed Dan."""
        foes = {t.serial for t in a.threats if t.kind == "monster" and t.hostile and 0 <= t.distance < RECALL_GAP}
        foes |= {s for s in swung if (by := next((t for t in a.threats if t.serial == s), None)) is not None
                 and by.kind == "monster"}

        def where(s) -> list:
            ms = s["world"]["mobiles"]
            return [(m["x"], m["y"]) for m in (ms.get(f"0x{f:08X}") or {} for f in foes) if m.get("x") is not None]
        if not where(st):
            return False

        def clear(s):
            pos = self.link.pos(s)[:2]
            return all(cheb(pos, xy) >= RECALL_GAP for xy in where(s)) and "clear"
        log(f"out of reach before the recall: running {RECALL_GAP} tiles from {len(foes)} creature(s)")
        mode, self.mode = self.mode, "gap"
        start = self.mover.steps
        try:
            while not clear(st) and self.mover.steps - start < RECALL_GAP_MAX_MOVES:
                left, before = RECALL_GAP_MAX_MOVES - (self.mover.steps - start), self.mover.steps
                for goal in self.escape_tiles(st, where(st)):
                    try:
                        self.mover.walk_to(lambda: goal, 1, "out of reach", max_moves=left, urgent=True, stop=clear)
                        break
                    except Abort as x:
                        if "no route" not in str(x) and "detour" not in str(x):
                            log(f"out of reach: {str(x).split(': ', 1)[-1]}; recalling from here")
                            return True
                if self.mover.steps == before:          # nowhere to go from here
                    break
                st = self.link.state()
        finally:
            self.mode = mode
        log("out of reach: " + ("clear" if clear(self.link.state()) else
                                f"still within {RECALL_GAP} tiles after {self.mover.steps - start} steps") + "; recalling")
        return True

    def recall_out(self, st, a, worst, swung, pk: bool = True, why: str | None = None,
                   what: str | None = None, attempts: int | None = None) -> str:
        """Recall to the book's default rune at once (escape.escape: recasts as soon as the
        server takes a cast again, until it lands or escape.ESCAPE_BUDGET_S is spent),
        before any bookkeeping, then stop: the `threat` juncture (action 'recall') and
        an urgent `pk_escape` juncture when it landed (`pk`; a creature escape posts
        an urgent `threat` juncture instead). Returns why it failed; the caller then
        stops the plain way (threat_stop). A target cursor that is up is cancelled
        first (drop_cursor); the `recall` job event's `react_s` is first sight
        (sight_t) -> the escape's first packet (the book's double-click), and
        `cursor_cancelled` whether a cursor had to go first. `what` names the threat when no
        mobile does (post_threat), e.g. a trapped pouch going off with nobody in view. `attempts`:
        casts before giving up (escape.escape; None: until its budget is spent)."""
        cancelled = self.drop_cursor()
        sight, pressed = self.sight_t(worst, swung), time.time()
        react = round(pressed - sight, 2) if sight is not None else None
        log(f"recalling out: {react} s after first sight" if react is not None else "recalling out")
        try:
            res = escape_mod.escape(escape_mod.LinkIO(self.link), self.recall_book, log=log, attempts=attempts)
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
        summary = self.post_threat(st, a, worst, swung, "recall", why, what=what)
        self.memory.juncture("lumber", "pk_escape" if pk else "threat",
                             f"Recalled away from {summary} ({res['kind']} {res['method']}, "
                             f"{res['press_to_arrival_s']} s); stopped", "urgent", data)
        self.recalled_home = True        # trip(): home_after_recall, into the room to convert and store
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

    def post_threat(self, st, a, worst, swung, action, why=None, extra=None, what=None) -> str:
        """The urgent `threat` juncture + `flee` job event (`extra` merged into its
        data, e.g. the monster_hit episode as `hit`); returns the summary (`what` when no
        mobile names the threat). A target cursor still up is cancelled (drop_cursor): an
        escape walks off, a stop leaves."""
        self.drop_cursor()
        if worst is not None:
            summary = (f"{worst.kind} {worst.name or f'0x{worst.serial:08X}'} at {worst.distance} tiles "
                       f"(ETA {worst.eta_s:.1f} s)")
        elif swung:
            summary = "attacked by " + ", ".join(f"0x{s:08X}" for s in swung)
        else:
            summary = what or "taking damage"
        data = {**a.to_dict(), "action": action, "attackers": self.attacker_list(swung), **(extra or {})}
        if why:
            data["why"] = why
        what = "escaping" if action == "escape" else "stopping"
        self.memory.juncture("lumber", "threat", f"Threat: {summary}; {what}" + (f" ({why})" if why else ""),
                             "urgent", data)
        self.memory.job_event("lumber", "flee", data, **self._where(st))
        return summary

    def threat_stop(self, st, a, worst, swung, cls, why=None, what=None):
        summary = self.post_threat(st, a, worst, swung, "abort", why, what=what)
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

    # ------------------------------------------------------------ thieves
    def check_pouches(self, st):
        """pouch.PopWatch over every state read (docs/PLAN.md "Keep thieves off the logs").
        Our own pouch going off (we double-clicked it to open it, unpack) costs a hit: that
        drop is acknowledged (threats.Watch.acknowledge, the HP guard's start_hits) on every
        read for pouch.OWN_POP_S after our click or its signals. A pop we didn't cause (the
        explosion around us, its sound, or a hue 38 -> 0 on a pouch we never clicked) is a
        thief at our logs: pouch_alarm. Counts this trip's spent pouches (pouches_used)."""
        ev, ts = self.link.events, self.link.event_t
        new = [(t, e) for t, e in zip(ts[self._pop_scan:], ev[self._pop_scan:])
               if t is None or t >= self._pop_since]
        self._pop_scan = len(ev)
        try:
            pack = self.backpack(st)
        except Abort:
            pack = None
        pos = st["movement"]["pos"]
        pops = self.pops.observe(st["world"], pack, tuple(pos[:2]) if pos else None, new, time.time())
        self.pouches_used += sum(1 for p in pops if p["signal"] == "hue")
        if any(p["own"] for p in pops):
            self._own_pop_until = time.monotonic() + pouch.OWN_POP_S
        if time.monotonic() < self._own_pop_until:
            hits = (st["world"].get("self") or {}).get("hits")
            self.watch.acknowledge(hits=hits)
            if hits is not None and self.start_hits is not None and hits < self.start_hits:
                log(f"our trapped pouch took {self.start_hits - hits} hit(s); not an attack")
                self.start_hits = hits
        alarm = [p for p in pops if not p["own"]]
        if alarm and self.mode != "flee":
            self.pouch_alarm(st, alarm)

    def pouch_alarm(self, st, pops):
        """A trapped pouch in our pack went off without our double-click: a thief snooped
        it (docs/NOTES.md "A trapped pouch popped by its owner": the explosion shows on us).
        Leave like for a red (thief_out), naming the nearest player within STEAL_GUARD tiles
        as the suspect (a snoop needs 1 tile; none in view: a hidden thief)."""
        a = self.last_threats or self.watch.update(st, recall_s=RECALL_S, margin_s=THREAT_MARGIN_S)
        near = sorted((t for t in a.threats if t.player and 0 <= t.distance <= STEAL_GUARD),
                      key=lambda t: t.distance)
        worst = near[0] if near else None
        sig = ", ".join(sorted({p["signal"] for p in pops}))
        who = (f"{worst.name or f'0x{worst.serial:08X}'} at {worst.distance} tiles" if worst
               else f"nobody within {STEAL_GUARD} tiles in view (hidden?)")
        self.thief_out(st, a, worst, "pouch_pop", f"trapped pouch went off without our double-click ({sig}); {who}",
                       {"signals": pops})

    def thief_near(self, st, a, step: bool = True):
        """Players within STEAL_GUARD tiles while harvesting (threats `thief`; THREATS.md §7 T3:
        thieves look blue until the steal, which needs 1 tile). One we already stepped away
        from this trip closing in again: recall (thief_out). Anyone else: KeepAway, which
        harvest_trip answers with keep_away. During a speech hold (`step` False) nothing is
        sent for a newcomer: the overseer has the speaker."""
        back = [t for t in a.thieves if t.serial in self.suspects]
        if back:
            t = back[0]
            self.thief_out(st, a, t, "closed_again", f"suspected thief {t.name or f'0x{t.serial:08X}'} "
                                                     f"closed to {t.distance} tiles again")
        if step:
            raise KeepAway(a.thieves)

    def thief_out(self, st, a, worst, trigger: str, why: str, extra: dict | None = None):
        """Leave a thief at our logs: away from home with a recall book, recall to its default
        rune (recall_out: the threat + pk_escape junctures), else stop where we are (Unsafe).
        Either way a `thief` job event (trigger 'pouch_pop' or 'closed_again', action
        'recall' or 'abort') keeps the spot out of `lumber plan` for THIEF_COOLDOWN_S
        (lumber_opt; THREATS.md §7 T3/T4)."""
        recall = self.afield and self.recall_book is not None
        data = {"trigger": trigger, "action": "recall" if recall else "abort", "why": why,
                "suspect": worst.to_dict() if worst else None, "trip": self.trip_n,
                "spot": self.k["spot"]["id"], **(extra or {})}
        self.memory.job_event("lumber", "thief", data, **self._where(st))
        self.stats["thief"] = trigger
        log(f"THIEF: {why}")
        what = None if worst else "a thief at our trapped pouch"
        if recall:
            why = f"{why}; {self.recall_out(st, a, worst, {}, why=why, what=what)}"
        self.threat_stop(st, a, worst, {}, Unsafe, why, what=what)

    def keep_away(self, e: KeepAway):
        """Step out of the suspects' reach (THREATS.md §7 T3): after a player's reaction time
        (the 'read' pause, which still watches for threats), walk to a tile at least
        KEEP_AWAY_TILES from each of them, then carry on harvesting at a stand out of their
        reach (out_of_reach: they stay in self.danger for the trip, and routes bend around
        them). One step beats the 5 s steal cooldown where a 2 s recall would still leave
        them in reach. An attention `thief_near` juncture and a `thief` job event (action
        'keep_away') name them. Still in steal range after the walk (they followed): recall."""
        st = self.link.state()
        mobs = st["world"]["mobiles"]
        self.drop_cursor()
        here = tuple(self.link.pos(st)[:2])
        centers = []
        for t in e.suspects:
            self.suspects[t.serial] = t.name
            m = mobs.get(f"0x{t.serial:08X}") or {}
            if m.get("x") is not None:
                centers.append((m["x"], m["y"]))
                zone = ((m["x"], m["y"]), KEEP_AWAY_TILES)
                self.danger[t.serial] = zone
                self.mover.danger[("thief", t.serial)] = zone
        names = ", ".join(f"{t.kind} {t.name or f'0x{t.serial:08X}'} at {t.distance} tiles" for t in e.suspects)
        data = {"trigger": "near", "action": "keep_away", "suspects": [t.to_dict() for t in e.suspects],
                "from": list(here), "trip": self.trip_n, "spot": self.k["spot"]["id"]}
        self.memory.job_event("lumber", "thief", data, **self._where(st))
        self.memory.juncture("lumber", "thief_near", f"Suspected thief while harvesting: {names}; stepping away",
                             "attention", data)
        self.stats["keep_aways"] = self.stats.get("keep_aways", 0) + 1
        log(f"KEEP AWAY: {names}; stepping out of reach")
        self.mode = "escape"
        try:
            self.doing("escape", f"Stepping away from {e}", here)
            self.human.wait("read")
            if centers:
                self.mover.walk_to(None, 0, "away from a suspected thief", max_moves=KEEP_AWAY_MOVES,
                                   goal_fn=nav.beyond(centers, KEEP_AWAY_TILES - 1))
        finally:
            self.mode = "work"
        st = self.link.state()
        a = self.watch.update(st, recall_s=RECALL_S, margin_s=THREAT_MARGIN_S)
        self.last_threats = a
        back = [t for t in a.thieves if t.serial in self.suspects]
        if back:
            self.thief_out(st, a, back[0], "closed_again", f"suspected thief {back[0].name or f'0x{back[0].serial:08X}'} "
                                                          f"followed to {back[0].distance} tiles")
        log(f"stepped away to {tuple(self.link.pos(st)[:2])}; carrying on")

    def check_speech(self, st):
        """speech_guard.py: a character speaking near us hands control to the
        overseer (user request 2026-10-01, harvest jobs only)."""
        who = self.new_speakers(st)
        if who:
            self.speech_hold(who, st)

    def new_speakers(self, st):
        """New speakers, each with its Laya verdict (triage.py) when the service is on,
        then the invulnerable players come into view (staff_in_view)."""
        who = self.speech.scan(st["world"], self.link.events, self.link.event_t)
        for w in who:
            v = self.triage.judge(w, st["world"], names=self.speech.names)
            if v and "error" not in v:
                log(f"laya: {w['label'] or w['name'] or w['serial']}: {w['text']!r} "
                    f"check {v['check']:.2f} direct {v['direct']:.2f} ({v['ms']} ms)")
        staff, self.pending_staff = self.pending_staff + self.staff_in_view(st), []
        return who + staff

    def staff_in_view(self, st):
        """Invulnerable players come into view (SpeechGuard.sightings): a `staff_sighting`
        job event on each one's first sighting this run, and gm_suspected with the staff
        alarm at once (suspect_staff: one per open alarm) whatever the mode. They are
        held for like speakers (new_speakers -> speech_hold)."""
        seen = self.speech.sightings(st["world"])
        for w in seen:
            log(f"STAFF IN VIEW: {w['label'] or w['name'] or w['serial']} (body {w['body']}, hue {w['hue']}, "
                f"at {w['x']},{w['y']})")
            if w.pop("first"):
                self.memory.job_event("lumber", "staff_sighting", {**w, "trip": self.trip_n, "mode": self.mode},
                                      **self._where(st))
        if seen:
            self.suspect_staff(seen, st)
        return seen

    def speech_hold(self, who, st):
        """Send nothing until the overseer gives the all-clear (acks the
        `speech_nearby` juncture) and no `gm_suspected` juncture is open.
        Threats and death still end the job. The overseer may talk to the
        speaker meanwhile (ctl allows `act say` and `single_click` while a task
        holds) or stop the job. A speaker with staff hints (speech_guard.
        staff_hints, including Laya's attendance check) raises `gm_suspected`
        and the staff alarm, which repeats (alerts.STAFF_REPEAT_S) until that
        juncture is acked. An invulnerable player come into view (staff_in_view,
        type "sighting", no text) is held for the same way. At the all-clear, a
        `speech_clear` job event keeps every line heard with its verdict and how
        the hold ended (the labeled data for fine-tuning Laya)."""
        first = who[0]
        name = first["label"] or first["name"] or first["serial"]
        log(f"SPEECH: {name} {what(first)}; pausing for the overseer")
        resume = self._intent
        self.doing("speech", f"Paused: {name} " + ("in view" if first["text"] is None else "spoke nearby")
                   + "; waiting for the overseer")
        data = {"hold": True, "task": "lumber", "trip": self.trip_n, "speakers": who, **self._where(st)}
        jid = self.memory.juncture("lumber", "speech_nearby",
                                   f"{name} {what(first)} nearby; harvesting paused until the "
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
                    log(f"SPEECH (paused): {w['label'] or w['name'] or w['serial']} {what(w)}")
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

    def in_pack(self, st, graphics, top: bool = False):
        """[(serial, item)] of `graphics` in the backpack at any depth (logs in the trapped
        pouch are carried), or only lying directly in it (`top`)."""
        pack = self.backpack(st)
        inside = ledger_mod.pack_items(st, pack)
        return [(serial_of(k), it) for k, it in st["world"]["items"].items()
                if it.get("graphic") in graphics and it.get("container") is not None
                and (serial_of(it["container"]) == pack if top else serial_of(k) in inside)]

    def count(self, st, graphics) -> int:
        return sum(it.get("amount") or 1 for _, it in self.in_pack(st, graphics))

    def log_pouch(self, st) -> int | None:
        """The live trapped pouch for the wood: the one already holding some (one pouch a
        trip), else the shallowest; None without one."""
        pack = self.backpack(st)
        held = pouch.holding(st["world"], pack, LOGS + BOARDS)
        live = held or pouch.live(st["world"], pack)
        return live[0] if live else None

    def held(self, st, container, graphics) -> int:
        return sum(it.get("amount") or 1 for _, it in pouch.contents(st["world"], container, graphics))

    def hatchet(self, st, want=None) -> int:
        """The hatchet with the best tool bonus by its material (lumber_opt.hatchet_kind: the hue;
        the shelf hands out GM coloured ones, user 2026-10-05), worn first among equals, else the
        shallowest in the backpack or in a bag in it (any depth); with `want`
        (lumber_opt.parse_hatchet_spec, --hatchet) only one of that material and quality.
        use_hatchet opens the bags on the way like a player."""
        me, pack, items = self.self_serial(st), self.backpack(st), st["world"]["items"]
        best = None
        for key, it in items.items():
            if it.get("graphic") in HATCHETS and it.get("container") is not None:
                kind = lumber_opt.hatchet_kind(it, self.hatchets)
                if want is not None and not lumber_opt.matches(kind, want):
                    continue
                depth = pack_depth(items, serial_of(it["container"]), me, pack)
                rank = (-kind["tool_bonus"], depth)
                if depth is not None and (best is None or rank < best[0]):
                    best = (rank, serial_of(key))
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
    def use_hatchet(self, kind: str = "use", hesitate: bool = True):
        """dclick the hatchet after a `kind` pause; returns the target-cursor event
        (captchas handled). A hatchet in the pack is reached like a player would: the
        containers on the way that the server hasn't opened yet are opened first,
        outermost first (agent_link.containers_to_open, ANTICHEAT.md closed containers).
        Outside the harvest cycle (`hesitate`) the human now and then hesitates: cancels
        the cursor (stock Esc packet) and uses the hatchet again. When the cursor comes,
        whether the hatchet is now in hand is noted for the trip row (hatchet_worn): the
        server equips a packed hatchet on the double-click, and every spell cast puts it
        back in the pack (LUMBER_LOOP.md §13, session 20261003_213125)."""
        for attempt in range(2):
            self.human.wait(kind)
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
            if cur is None or attempt == 1 or not hesitate or not self.human.hesitate():
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
        """One Smart Harvest attempt at a Razor script's pace (humanize SCRIPT_MEDIAN; user
        decision 2026-10-04): use the hatchet ~0.2 s after the last reply and answer its
        cursor with ourselves at once (self_target); the server chops a tree within its
        reach that still has wood → (outcome, logs gained). While the server works (~4.2 s
        live), the last chop's loose logs go into the trapped pouch (stash; STASH_AFTER_S after
        the target: a lift sooner after the hatchet's use is refused), so the drag costs no time
        between chops; a reply quicker than that stashes after it."""
        self._stash_due = None                       # this chop's stash covers anything left loose
        st = self.state()
        before = self.count(st, LOGS)
        cur = self.use_hatchet("chop_use", hesitate=False)
        if cur is None:
            return ("captcha", 0)
        self.human.wait("chop_aim")
        st = self.state()
        mark = len(self.link.events)
        self.link.act(self_target(st, cur))
        stash_at = time.monotonic() + STASH_AFTER_S   # the server's action delay after the hatchet's use
        stashed = False
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
                gained = 0
                if out[0] == "success":
                    st = self.wait_for(lambda s: self.count(s, LOGS) > before, 3.0)
                    gained = max(self.count(st or self.link.state(), LOGS) - before, 0)
                if not stashed:              # a quick reply: stash_due drags after it's recorded,
                    self._stash_due = stash_at   # past the server's action delay
                return ("success", gained) if out[0] == "success" else out
            if not stashed and time.monotonic() >= stash_at:
                stashed = True
                self.stash()
                continue
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

    def add_local_trees(self, trees: list) -> int:
        """Trees on the map within LOCAL_TREES_R of where we stand that aren't candidates yet
        (harvest memory still allows them) join `trees`: after an escape we stand somewhere
        else, and when every tree of the spot waits on a creature, the ones around us may
        not (user, 2026-10-05: "he ran away from the mob and ended up right next to some
        other trees but wasn't chopping them"). Returns how many joined."""
        st = self.state()
        walk = self.mover.walk_map(st)
        if walk is None:
            return 0
        x, y = self.link.pos(st)[:2]
        r = LOCAL_TREES_R
        have = {(t["x"], t["y"]) for t in trees}
        now = time.time()
        new = [{"x": tx, "y": ty, "z": tz, "graphic": f"0x{g:04X}"}
               for tx, ty, tz, g in walk.m.find_trees(x - r, y - r, x + r, y + r)
               if (tx, ty) not in have and (tx, ty) not in self.no_route
               and self.memory.harvest_available(self.facet, tx, ty, tz, self.args.regrow_min * 60, now)]
        if new:
            trees.extend(new)
            log(f"{len(new)} tree(s) within {r} tiles of {(x, y)} join the candidates")
        return len(new)

    def harvest_trip(self) -> int:
        """Smart-Harvest at stands by the candidate trees until the quota. A monster
        escape (Escape -> self.escape) or a suspected thief (KeepAway -> self.keep_away)
        leaves the current stand; harvesting resumes at the next stand out of the reach
        of every monster escaped from and every player stepped away from. Each chop's
        logs go into the trapped pouch (stash).
        A break announced by the gate (self.break_due) ends the harvest. Running
        out of trees before the quota marks the trip `dry` (lumber_opt keeps
        the spot out of the plan until the trees regrow)."""
        tally = {"gained": 0, "attempts": 0, "successes": 0, "unknown": 0}
        try:
            if self.break_due:
                log("break due: no harvesting this trip")
                return 0
            self.aspect_ensure("heading out")
            self.go_out()
            self.afield = True
            self.track_ensure("at the spot")
            trees = self.candidate_trees(self.state())
            if not trees:
                self.stats["dry"] = True
                raise Abort("no harvestable tree available (all depleted, unreachable or ruled out)")
            while trees and tally["gained"] < self.args.logs_per_trip and not self.break_due:
                tree = self.next_stand(trees)
                if tree is None and self.add_local_trees(trees):
                    tree = self.next_stand(trees)
                if tree is None:
                    self.stats["creature_blocked"] = True
                    log(f"every tree left ({len(trees)}) is within reach of an aggressive creature in view; "
                        f"ending the harvest at {tally['gained']} logs")
                    break
                if not self.out_of_reach(tree["x"], tree["y"]):
                    log(f"tree {tree['x']},{tree['y']}: within reach of a monster or a suspected thief we "
                        f"backed away from; skipping")
                    continue
                try:
                    self.work_stand(tree, trees, tally)
                except Escape as e:
                    self.escape(e)
                    self.add_local_trees(trees)
                except KeepAway as e:
                    self.keep_away(e)
            if self.break_due:
                log(f"break due: stopping the harvest at {tally['gained']} logs; going home to convert and store")
            elif not trees and tally["gained"] < self.args.logs_per_trip:
                self.stats["dry"] = True
                log(f"the area ran dry at {tally['gained']} logs (every candidate tree out of wood or tried); "
                    f"going home")
            return tally["gained"]
        finally:
            self.harvesting = False
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
        in_view = {g[0].serial for g in self.tree_guards(st, recent=False)}
        free, later = [], []
        for t in trees:
            g = next((g for g in guards if cheb(g[1], (t["x"], t["y"])) <= g[2]), None)
            if g is None:
                free.append(t)
                continue
            if not any(cheb(h[1], (t["x"], t["y"])) <= h[2] for h in guards if h[0].serial in in_view):
                later.append(t)            # only a creature that has left the view guards it
            if ((t["x"], t["y"]), g[0].serial) not in self.avoided:
                self.avoided.add(((t["x"], t["y"]), g[0].serial))
                self.creature["avoided_trees"] += 1
                log(f"tree {t['x']},{t['y']}: within {g[2]} tiles of {g[0].name or f'0x{g[0].serial:08X}'} "
                    f"at {g[1]} ({g[0].aggression}); choosing a tree away from it")
        now = time.monotonic()
        waiting = lambda t: now - self.dropped_trees.get((t["x"], t["y"]), -1e9) < TREE_DROP_COOLDOWN_S  # noqa: E731
        # trees a zone made us drop a moment ago wait their cooldown whatever else is left (live witcher_48: a
        # fallback to them alternated two covered trees twice a second); then those only a creature now out
        # of view guards
        free = [t for t in free if not waiting(t)] or [t for t in later if not waiting(t)]
        if not free:
            return None
        switch, self.switch_tree = self.switch_tree, None
        best = next((i for i, t in enumerate(free) if (t["x"], t["y"]) == switch), None)
        if best is not None:                       # tree_rethink planned the way there already
            trees.remove(free[best])
            return free[best]
        best, best_cost = None, None
        for i, t in enumerate(free[:NEXT_STAND_PLANS]):
            path, _ = self.mover.plan(st, nav.within((t["x"], t["y"]), 1, self.tree_z_ok(t)),
                                      max_steps=self.tree_route_max(pos, t))
            if path is None:
                self.no_route_tree(t, trees)
                continue
            stand = tuple(t["stand"]) if "stand" in t else path[-1]
            n = sum(1 for o in trees if cheb(stand, (o["x"], o["y"])) <= SMART_RANGE)
            c = len(path) * self.human.rng.uniform(1.0, 1.15) / max(n, 1)
            if best_cost is None or c < best_cost:
                best, best_cost = i, c
        if best is None:                           # no way to any of the nearest: the next ones
            return self.next_stand(trees)
        trees.remove(free[best])
        return free[best]

    def tree_route_max(self, pos, t) -> int:
        """The longest route to tree `t` from `pos` worth walking (work_stand's max_route): longer
        is a detour, and the planner stops looking past it (fast; live 2026-10-05, Norse
        Settlement: nine unbounded 7 s searches while a norse bear rider closed in)."""
        return max(TREE_ROUTE_MIN, TREE_DETOUR * cheb(pos, (t["x"], t["y"])))

    def no_route_tree(self, t, trees: list):
        """No route of acceptable length (tree_route_max) reaches tree `t` from here (a cliff,
        water, a long way round): it leaves the trip's candidates and add_local_trees doesn't
        bring it back."""
        xy = (t["x"], t["y"])
        self.no_route.add(xy)
        if t in trees:
            trees.remove(t)
        log(f"tree {xy[0]},{xy[1]}: no route there short enough; leaving it")

    def tree_guards(self, st, recent: bool = True) -> list:
        """[(threat, (x, y), tiles)]: every creature that may come for us (may_aggro: not a pet, a
        passive body or name; user, 2026-10-05: never move into aggro range of a mob we can see)
        with its zone, aggro_r: its trees wait while it is around, and routes bend around it
        (aggro_zones, in view only). One in view now at its tile; with `recent`, one seen within
        RECENT_ZONE_S that has left the view at its last tile (next_stand takes trees only those
        guard when nothing else is left; live 2026-10-05, witcher_267: two norse bear riders
        patrolling in and out of view had Dan pick trees, then drop them as the zone came back,
        2.5 min of back and forth)."""
        a = self.last_threats
        mobs = st["world"]["mobiles"]
        now = time.monotonic()
        out = []
        for t in (a.threats if a else []):
            m = mobs.get(f"0x{t.serial:08X}") or {}
            if self.may_aggro(t) and 0 <= t.distance <= self.watch.params.max_range and m.get("x") is not None:
                g = (t, (m["x"], m["y"]), self.aggro_r(t))
                out.append(g)
                self.recent_guards[t.serial] = (*g, now)
        if not recent:
            return out
        here = {g[0].serial for g in out}
        for s, (t, xy, r, seen) in list(self.recent_guards.items()):
            if now - seen > RECENT_ZONE_S:
                del self.recent_guards[s]
            elif s not in here:
                out.append((t, xy, r))
        return out

    def may_aggro(self, t) -> bool:
        """A creature that may come for us: a monster that isn't a pet (the "(tame)" / "(bonded)"
        line) or of a passive body or name (threats.Params). Hostile ones (war mode, learned
        bodies, notoriety 6) of course; idle ones too, since they turn when we come near."""
        p = self.watch.params
        return (t.kind == "monster" and (t.hostile or (
            "pet" not in (t.aggression or "") and t.body not in p.passive_bodies
            and (t.name or "").lower() not in p.passive_names)))

    def aggro_r(self, t) -> int:
        """Tiles around a creature we stay out of: zone_r, at least AGGRO_R."""
        return max(self.zone_r(t), AGGRO_R)

    def aggro_zones(self, st):
        """The Mover's ("seen", serial) danger zones: the tree_guards of this state read, so
        every walk bends around the creatures in view; a new or moved one asks for a replan. A
        zone we already stand in shrinks to just inside where we stand: no going round it, but
        no closer either (live 2026-10-05: leaving it out let a walk from the Prevalia Gate
        landing pass 2 tiles from a ratman 6 tiles off; keeping it whole, a creature at our heels
        bent an escape back and forth)."""
        here = tuple(self.link.pos(st)[:2])
        zones = {("seen", t.serial): (xy, min(r, cheb(here, xy) - 1)) for t, xy, r in self.tree_guards(st, recent=False)}
        zones = {k: z for k, z in zones.items() if z[1] >= 0}
        old = {k: v for k, v in self.mover.danger.items() if isinstance(k, tuple) and k[0] == "seen"}
        for k in old.keys() - zones.keys():
            del self.mover.danger[k]
        for k, (xy, r) in zones.items():
            prev = old.get(k)
            self.mover.danger[k] = (xy, r)
            if prev is None or cheb(prev[0], xy) >= travel_guard.MOVED_REPLAN:
                self.mover.replan_requested = True

    def tree_rethink(self, spot, trees: list, z_ok=None):
        """walk_to's `stop` on the way to the tree at `spot`: the walk ends (its reason returned)
        as soon as a creature's zone covers that tree (live 2026-10-05: an air dragon came back
        into view by the tree, and the walk replanned round it and went on to that tree), and,
        looked at every TREE_RECHECK_S with the trees around us as candidates (add_local_trees),
        when a clear candidate's planned route is TREE_SWITCH_GAIN steps shorter than the one left
        to `spot` (user, 2026-10-05: staying away from a creature, Dan kept heading for the same
        tree, 2 min and 123 replans round an air dragon, past trees he could chop). That tree is
        next_stand's next pick (switch_tree). Planned routes, not straight lines: live witcher_98
        a tree 5 tiles off up a cliff no route reaches was "nearer" 13 times in 2 min while
        next_stand, finding no way there, sent us back to the far trees each time."""
        last = [time.monotonic()]

        def stop(st):
            guards = self.tree_guards(st)
            guarded = lambda xy: any(cheb(g[1], xy) <= g[2] for g in guards)   # noqa: E731
            if any(cheb(g[1], spot) <= g[2] for g in self.tree_guards(st, recent=False)):
                return "a creature's zone covers it now"
            now = time.monotonic()
            if now - last[0] < TREE_RECHECK_S:
                return None
            last[0] = now
            self.add_local_trees(trees)
            here = tuple(self.link.pos(st)[:2])
            left = cheb(here, spot)
            near = sorted((t for t in trees
                           if (t["x"], t["y"]) not in self.no_route
                           and cheb(here, (t["x"], t["y"])) + TREE_SWITCH_GAIN <= left
                           and not guarded((t["x"], t["y"])) and self.out_of_reach(t["x"], t["y"])),
                          key=lambda t: cheb(here, (t["x"], t["y"])))[:RETHINK_PLANS]
            if not near:
                return None
            route, _ = self.mover.plan(st, nav.within(spot, 1, z_ok),
                                       max_steps=self.tree_route_max(here, {"x": spot[0], "y": spot[1]}))
            for t in near:
                xy = (t["x"], t["y"])
                path, _ = self.mover.plan(st, nav.within(xy, 1, self.tree_z_ok(t)),
                                          max_steps=self.tree_route_max(here, t))
                if path is None:
                    self.no_route_tree(t, trees)
                elif route is None or len(path) + TREE_SWITCH_GAIN <= len(route):
                    self.switch_tree = xy
                    return f"tree {xy[0]},{xy[1]} is nearer and clear ({len(path) - 1} steps)"
            return None
        return stop

    def boxed_in(self, why: str):
        """A walk replanned round creatures on every side without getting nearer (Mover's
        DANGER_STALL_REPLANS; user, 2026-10-05, witcher_137: "He needs to recall home and mark this
        place unworkable"): the spot is disabled in the store (lumber_opt plans no more trips there;
        `ctl lumber spot set <id> --status active` brings it back), an attention `stuck` juncture
        says so, and monster_stop runs out of reach and recalls home."""
        st = self.link.state()
        a = self.watch.update(st, recall_s=RECALL_S, margin_s=THREAT_MARGIN_S)
        guards = sorted(self.tree_guards(st, recent=False), key=lambda g: cheb(self.link.pos(st), g[1]))
        spot_id = self.k["spot"]["id"]
        reason = (f"boxed in by creatures on all sides ({len(guards)} in view: "
                  f"{', '.join(sorted({g[0].name or f'0x{g[0].serial:08X}' for g in guards})) or 'none named'}); "
                  f"unworkable (runner, {time.strftime('%Y-%m-%d %H:%M')})")
        row = next((r for r in self.memory.lumber_spot_rows() if r["id"] == spot_id), None)
        self.memory.lumber_spot_put(spot_id, "disabled", row["data"] if row else {},
                                    row["source"] if row else "runner", reason)
        pos = list(self.link.pos(st)[:2])
        log(f"{why}: spot {spot_id} disabled ({reason}); recalling home")
        self.memory.juncture("lumber", "stuck", f"Boxed in by creatures at {spot_id} {tuple(pos)}: spot disabled, "
                             f"recalling home", "attention",
                             {"pos": pos, "reason": why, "spot": spot_id, "disabled": True, "trip": self.trip_n,
                              "creatures": [{"serial": f"0x{g[0].serial:08X}", "name": g[0].name, "at": list(g[1]),
                                             "zone": g[2]} for g in guards]})
        self.monster_stop(st, a, guards[0][0] if guards else None, {}, why)

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
        here = tuple(self.link.pos(self.link.state())[:2])
        # live 2026-10-05 (witcher_58, near death): mobiles cut the way 28 tiles to a tree and the
        # planner sent us on a 255-step detour through the wilds into a fen daemon and a brackish water
        max_route = self.tree_route_max(here, tree)
        rethink = self.tree_rethink(spot, trees, z_ok)
        try:
            if "stand" in tree:
                why = self.mover.walk_to(lambda: tree["stand"], 0, f"to {label}", z_ok=z_ok, max_route=max_route,
                                         stop=rethink)
            else:
                why = self.mover.walk_to(lambda: (tree["x"], tree["y"]), 1, f"to {label}", z_ok=z_ok,
                                         max_route=max_route, stop=rethink)
            if why:
                trees.append(tree)               # still a candidate, for later or from elsewhere
                if why.startswith("a creature's zone"):
                    self.dropped_trees[spot] = time.monotonic()
                log(f"{label}: {why}; choosing the next stand from here")
                return
        except Abort as e:
            if "detour" in str(e):
                log(f"{label}: {str(e).split(': ', 1)[-1]}; trying the next stand")
                return
            if "boxed in" in str(e):
                self.boxed_in(str(e).split(": ", 1)[-1])
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
        self.harvesting = True       # the keep-away is on while we stand and chop (a thief comes to us)
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
                    if self._stash_due is not None:      # the reply beat the stash: drag them now
                        c0, w0 = time.monotonic(), self.stats.get("speech_wait_s", 0.0)
                        self.stash_now()
                        self.chopped(c0, w0)
                elif out in ("nothing_near", "depleted"):
                    rec["end"] = out
                    if out == "depleted":           # the server's pick ran out; which one it was is unknown
                        log(f"{where}: not enough wood here; next stand")
                        self.stash_now()
                        return
                    for t in reach:
                        self.memory.harvest_record(self.facet, t["x"], t["y"], t["z"], h(t["graphic"]), "nothing_near")
                        if t in trees:
                            trees.remove(t)
                    log(f"{where}: nothing nearby has wood after {rec['attempts']} attempt(s), {rec['logs']} logs; "
                        f"{len(reach)} tree(s) within {SMART_RANGE} marked out of wood; next stand")
                    self.stash_now()
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
            self.stash_now()                   # the last chop's logs (each chop stashes the one before)
            rec["end"] = ("break" if self.break_due else "quota" if tally["gained"] >= self.args.logs_per_trip
                          else "max_attempts")
        except BaseException as e:
            rec["end"] = rec["end"] or f"interrupted: {type(e).__name__}: {e}"[:200]
            raise
        finally:
            self.harvesting = False
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
        """Walk away from e's monsters to a tile at least ESCAPE_RUN tiles from each of them
        and out of every zone (escape_tiles: theirs, zone_r, and the aggro zones of the other
        creatures in view; user, 2026-10-05: a few steps don't break aggro), then check that
        none followed: one still in flee range, or a ranged one within its reach, stops the run
        ('it kept coming'; monster_stop runs RECALL_GAP away before the recall). The zones
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
        run_from = [(m["x"], m["y"]) for m in (mobs.get(f"0x{t.serial:08X}") or {} for t in e.monsters)
                    if m.get("x") is not None]
        goals = self.escape_tiles(st, run_from)
        if not goals:
            self.monster_stop(st, self.watch.update(st, recall_s=RECALL_S, margin_s=THREAT_MARGIN_S),
                              e.monsters[0], {}, "nowhere clear to run to")
        log(f"ESCAPE {self.escapes}/{ESCAPES_PER_TRIP}: {e.summary}; backing away to {goals[0]}")
        self.mode, self.walk_hits = "escape", 0
        here = tuple(st["movement"]["pos"][:2])
        try:
            for i, goal in enumerate(goals):
                self.doing("escape", f"Backing away from {names}", goal)
                # live 2026-10-05 (witcher_149, a death): a goal 4 tiles away took a 36-step route round a
                # building and through a door; the hoarfrost caught us on it
                max_route = max(ESCAPE_ROUTE_MIN, ESCAPE_DETOUR * cheb(here, goal))
                try:
                    # no pauses while running from a creature (user, 2026-10-05)
                    self.mover.walk_to(lambda: goal, 1, "escape", max_moves=80, max_route=max_route, urgent=True)
                    break
                except Abort as x:
                    if "no route" not in str(x) and "detour" not in str(x):
                        raise
                    if i == len(goals) - 1:
                        st = self.link.state()
                        a = self.watch.update(st, recall_s=RECALL_S, margin_s=THREAT_MARGIN_S)
                        self.monster_stop(st, a, e.monsters[0], {}, "no short way out of its reach")
                    log(f"escape: {str(x).split(': ', 1)[-1]}; trying another way")
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

    def escape_tiles(self, st, run_from=()) -> list:
        """Escape goals, best first: up to two tiles walked before (walk memory)
        at least ESCAPE_RUN tiles from each of `run_from` and out of every zone (the
        runner's escaped-from monsters, the Mover's: aggro zones of the creatures in view,
        thieves), within 60 degrees of straight away from them, nearest first; then the
        first such tile straight away and 45 degrees to either side."""
        pos = tuple(st["movement"]["pos"][:2])
        zones = list(self.danger.values())
        avoid = zones + [z for k, z in self.mover.danger.items() if isinstance(k, tuple) and k[0] == "seen"]

        def clear(t):
            return all(cheb(t, z) > r for z, r in avoid) and all(cheb(t, p) >= ESCAPE_RUN for p in run_from)
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

    # ------------------------------------------------------------ the trapped pouch
    def stash_now(self):
        """stash, past the server's action delay when the last chop's reply came before attempt()
        could stash (_stash_due: a lift sooner after the hatchet's use is refused)."""
        if self._stash_due is not None:
            time.sleep(max(0.0, self._stash_due - time.monotonic()))
            self._stash_due = None
        return self.stash()

    def stash(self, graphics=LOGS) -> int:
        """Drag the wood lying directly in the backpack (a chop's new logs) into the trapped
        pouch (log_pouch; docs/PLAN.md "Keep thieves off the logs"), with the stock lift and
        drop at a player's pace: the drop onto the pouch's icon in the open backpack, as the
        store drops into the chest. The ledger knows the drag (ledger "moving"). A stack
        not seen in the pouch within 3 s stays where it is (the next chop tries again).
        Returns the units moved; none without a live pouch (pouch_ready starts no trip
        without one)."""
        st = self.state()
        loose = self.in_pack(st, graphics, top=True)
        p = self.log_pouch(st) if loose else None
        if p is None:
            return 0
        self.open_for((p, False))
        moved = 0
        for serial, it in loose:
            amount = it.get("amount") or 1
            before = self.held(self.link.last or st, p, graphics)
            self.human.wait("use")
            self.ledger.expect(("moving", serial, p))
            self.link.act(actions.lift(serial, amount))
            self.human.wait("drag")
            self.link.act(actions.drop(serial, DROP_AUTO, DROP_AUTO, 0, 0, p))
            if self.wait_for(lambda s: self.held(s, p, graphics) >= before + amount, 3.0) is None:
                log(f"stash: {amount} of 0x{serial:08X} not seen in the trapped pouch 0x{p:08X}; leaving it")
                continue
            moved += amount
        if moved:
            self.stats["stashed"] = self.stats.get("stashed", 0) + moved
            log(f"stashed {moved} in the trapped pouch 0x{p:08X}")
        return moved

    def unpack(self, graphics):
        """Set off, ourselves, every live trapped pouch holding `graphics`, so that the next
        double-click opens it (a double-click on a live one only sets it off; then it is an
        ordinary pouch, hue 0). Our pop costs a hit and is no alarm (check_pouches: our click
        names it, pouch.PopWatch). Abort when it doesn't go off."""
        st = self.state()
        for p in pouch.holding(st["world"], self.backpack(st), graphics):
            self.open_for((p, False))
            self.human.wait("use")
            self.pops.own_pop(p, time.time())
            self._own_pop_until = time.monotonic() + pouch.OWN_POP_S
            self.link.act(actions.dclick(p))
            if self.wait_for(lambda s: (self.item(s, p) or {}).get("hue") != pouch.TRAPPED_HUE, 4.0) is None:
                raise Abort(f"trapped pouch 0x{p:08X} didn't go off on our double-click")
            log(f"set off our trapped pouch 0x{p:08X} to open it")
            self.human.wait("read")

    def pouch_ready(self, st):
        """A trip carries its logs in a live trapped pouch, and uses one up (unpack, in the
        rental room). Checked after resupply_home topped the loadout up from the shelves: still
        none (the shelves had none), an attention `low_supplies` juncture (item 'trapped pouch')
        and the run stops before the trip; the overseer tells the user, or buys some at a
        provisioner (`ctl act buy <provisioner> trapped pouch --amount N`; Errol, 25 gp)."""
        pp = pouch.pack_pouches(st["world"], self.backpack(st))
        live = [s for s, p in pp.items() if p["live"]]
        if live:
            return
        data = {"item": "trapped pouch", "have": 0, "need": 1, "carry": pouch.CARRY,
                "spent_in_pack": len(pp), "graphic": f"0x{pouch.POUCH_GRAPHIC:04X}", "hue": pouch.TRAPPED_HUE,
                "how": "a provisioner sells Trapped Pouch (25 gp, Errol): ctl act buy <provisioner serial> "
                       f"trapped pouch --amount {pouch.CARRY}"}
        self.memory.juncture("lumber", "low_supplies", "No trapped pouch (hue 38) in the pack for the logs; "
                             "buy some at a provisioner", "attention", data)
        raise Abort("no trapped pouch for the logs (low_supplies): buy Trapped Pouches at a provisioner")

    # ------------------------------------------------------------ converting
    def convert(self):
        """Every log stack in the pack into boards (in the rental room, before storing): the
        trapped pouch holding them is set off first (unpack) and opened on the way
        (open_for), the logs targeted with the hatchet's cursor; the boards land where
        the logs were [INFERENCE: RunUO ScissorHelper drops them into the logs' container]."""
        ok_text = self.k["convert"]["ok_text"]
        self.unpack(LOGS)
        # one conversion per stack (a trip's woods plus what earlier aborted trips left: live
        # 2026-10-05, 4 stacks), and CONVERT_RETRIES for hatchet uses that bring no cursor (one did
        # after the pouch went off, and the 4-try loop left a 9-log stack)
        tries = len(self.in_pack(self.state(), LOGS)) + CONVERT_RETRIES
        for _ in range(tries):
            st = self.state()
            stacks = self.in_pack(st, LOGS)
            if not stacks:
                return
            serial, it = stacks[0]
            if "woods" not in self.stats:           # this trip's logs by wood type (ledger.py, woods.json)
                self.stats["woods"] = self.ledger.summary(kind="log")
            self.doing("convert", f"Making boards from {it.get('amount') or 1} logs")
            self.open_for((serial, False))              # the logs are targeted in their open container
            cur = self.use_hatchet()
            if cur is None:
                log("convert: the hatchet brought no cursor; trying again")
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
        raise Abort(f"logs left after {tries} conversion tries")

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

    # ------------------------------------------------------------ home (harness/home.py)
    @staticmethod
    def facet_now(st) -> int | None:
        return (st["world"].get("self") or {}).get("map")

    def at_home(self, st) -> bool:
        """In the rental room, or within home.NEAR_LANDING tiles of the home landing."""
        return self.home is not None and home_mod.at_home(self.link.pos(st), self.facet_now(st), self.home)

    def find_home(self, st) -> dict:
        """This character's home (harness/home.py, --homes) by its name in the world model."""
        name = (st["world"].get("self") or {}).get("name")
        found = home_mod.for_character(name, self.args.homes)
        if found is None:
            raise Abort(f"no home for {name!r} in {self.args.homes}: every trip starts and ends there (the "
                        f"landing our book's default rune recalls to, the rental room and its chest)")
        self.home_name = name
        log(f"home of {name}: landing {tuple(found['landing'][:2])} (facet {found['facet']}), the rental room "
            f"(owner {found['room'].get('owner') or 'ourselves'}), chest {found['chest']['serial']}")
        return found

    def leave_room(self):
        """Out of the rental room through its door (room.leave: the door's menu, the home's exit,
        "Exit to House Steward" for Outland Dan): a teleport to the landing, so the 60 s harvest
        lockout and the Stationary Penalty follow (the first chop waits them out, unstick)."""
        self.doing("leave_room", "Leaving the rental room")
        try:
            res = room_mod.leave(escape_mod.LinkIO(self.link), self.human, self.home["room"].get("exit") or "steward")
        except room_mod.RoomError as e:
            raise Abort(f"leaving the rental room: {e}")
        if not res["ok"]:
            raise Abort(f"leaving the rental room: {res.get('error')}")
        log(f"left the rental room to {res['exit']} at {res['pos']} (facet {res['facet']})")

    def resupply_home(self):
        """Before each trip, at home (user, 2026-10-05; docs/NOTES.md "Storage shelves"): the
        loadout topped up from the Storage Shelf (shelf.resupply, the same flow as `ctl act
        resupply`). In the rental room its shelf first; when that leaves something out ("No
        resupply: …" or nothing available), out of the room (the trip goes that way anyway) and
        the shelf by the landing (Outland Dan: the DTF guild house's, 2 tiles off). A shelf that
        fails is logged and passed over: pouch_ready still decides whether the trip can go.
        What each shelf gave and lacked goes into the trip row (`resupply`)."""
        if self.args.resupply == "off":
            return
        st = self.state()
        if not self.at_home(st):
            log("resupply: not at home; the run goes on with what we carry")
            return
        done = []
        if home_mod.in_room(self.facet_now(st), self.home):
            res = self.resupply_here("room")
            if res is not None:
                done.append(res)
                if not res.get("error") and not res["missing"] and not res["partial"] and not res["none_available"]:
                    self.pre_stats["resupply"] = done
                    return
            self.leave_room()
        res = self.resupply_here("landing")
        if res is not None:
            done.append(res)
        if done:
            self.pre_stats["resupply"] = done

    def mount_home(self):
        """Ride out (user, 2026-10-05; docs/NOTES.md "Our mount"): at home before each trip, not
        riding, our pet within reach (mount.find_own: the one remembered for this character, else
        the pet whose menu offers "Release") gets the stock double-click. A ghost is revived first
        through the rental room (out and in again when already inside; live 2026-10-05 the horse's
        ghost came back alive that way). The DTF guild house sends a ridden mount to rest when we
        come into it, by a recall or out of the room ("Your mount finds a quiet place to rest
        safely."), and gives it back when we leave it by recall or into the room ("Your mount
        returns.", live): outside the room a remembered mount that isn't here is resting, and the
        trip rides from the landing (`resting`; mount_after_recall checks it came back). A
        remembered mount missing in the room or still a ghost is an attention `low_supplies`
        juncture (item 'mount', once a run) and the trip goes on foot. The trip row's `mount` says
        what happened."""
        if self.args.mount == "off":
            return
        st = self.state()
        if mount_mod.mounted(st) or not self.at_home(st):
            return
        io = escape_mod.LinkIO(self.link)
        known = mount_mod.remembered(self.memory, self.home_name)
        found = mount_mod.find_own(io, self.human, st, known)
        in_room = home_mod.in_room(self.facet_now(st), self.home)
        if found is None and known is not None and not in_room:
            log(f"mount: 0x{known:08X} rests in the guild house; it comes back when we recall out")
            self.pre_stats["mount"] = {"pet": f"0x{known:08X}", "mounted": False, "resting": True}
            return
        if found is not None and found[1].get("dead"):
            pet = found[0]
            mount_mod.remember(self.memory, self.home_name, pet, found[1].get("name"))
            log(f"our mount 0x{pet:08X} is a ghost: into the rental room to revive it")
            if in_room:
                self.leave_room()
            self.to_room()
            st = self.state()
            m = st["world"]["mobiles"].get(f"0x{pet:08X}") or {}
            found = (pet, m) if m.get("x") is not None else None
        rec = {"pet": None if found is None else f"0x{found[0]:08X}", "mounted": False}
        if found is None or found[1].get("dead"):
            rec["why"] = "no pet of ours in reach" if found is None else "still a ghost after the rental room"
            log(f"mount: {rec['why']}; the trip goes on foot")
            if known is not None:
                self.mount_missing(known, rec["why"])
            self.pre_stats["mount"] = rec
            return
        pet, m = found
        mount_mod.remember(self.memory, self.home_name, pet, m.get("name"))
        self.doing("mount", f"Mounting {m.get('name') or 'our pet'}")
        rec["mounted"] = mount_mod.mount(io, self.human, pet)
        log(f"mount: {'riding' if rec['mounted'] else 'no mount after the double-click on'} 0x{pet:08X}")
        self.pre_stats["mount"] = rec

    def mount_missing(self, pet: int, why: str):
        """A remembered mount we can't ride: an attention `low_supplies` juncture (item 'mount'), once a run."""
        if self.mount_warned:
            return
        self.mount_warned = True
        self.memory.juncture("lumber", "low_supplies", f"Our mount 0x{pet:08X} can't be ridden ({why}); the "
                             "trips go on foot", "attention",
                             {"item": "mount", "pet": f"0x{pet:08X}", "why": why,
                              "how": "find it, or revive its ghost (act room enter / a healer); then act mount"})

    def mount_after_recall(self):
        """After the recall out: our remembered mount is under us again. The guild house rests it
        when we come out of the room or recall in, and gives it back on the recall out ("Your mount
        returns.", live 2026-10-05). The trip row's `mount.mounted` says whether we ride; a
        juncture when a mount that rested didn't come back."""
        known = mount_mod.remembered(self.memory, self.home_name)
        if self.args.mount == "off" or known is None:
            return
        end = time.time() + 2.0
        while not mount_mod.mounted(self.state()) and time.time() < end:
            time.sleep(0.1)
        rec = self.stats.setdefault("mount", {"pet": f"0x{known:08X}"})
        rec["mounted"] = mount_mod.mounted(self.state())
        log(f"mount: {'riding' if rec['mounted'] else 'not riding'} after the recall out")
        if not rec["mounted"] and "why" not in rec:
            self.mount_missing(known, "it didn't come back after the recall out")

    def resupply_here(self, where: str, restock: bool = False) -> dict | None:
        """shelf.resupply from the nearest usable storage shelf in view (None: no shelf here). With
        `restock`, first the shelf's Restock with our backpack (the user's routine, 2026-10-05):
        every pack item may leave, so each is declared to the ledger."""
        st = self.state()
        found = shelf_mod.find_shelves(st)
        if not found:
            log(f"resupply: no storage shelf in view ({where})")
            return None
        if restock:
            for s in shelf_mod.carried(st):
                self.ledger.expect(("moved_out", s))    # into our own shelf: not theft
        self.doing("resupply", f"{'Restocking and resupplying' if restock else 'Resupplying'} from the storage "
                               f"shelf ({where})")
        t0 = time.monotonic()
        try:
            res = shelf_mod.resupply(escape_mod.LinkIO(self.link), self.human, walk=self.walk_to_item,
                                     restock=restock)
        except shelf_mod.ShelfError as e:
            log(f"resupply ({where}): {e}")
            return {"where": where, "error": str(e), "s": round(time.monotonic() - t0, 1)}
        got = shelf_mod.summary(res["added"])
        log((f"restocked ({where}): {'; '.join(res['restocked']) or 'no answer'}; " if restock else "")
            + f"resupplied ({where}) from {res['shelf']}: {got}"
            + (f"; the shelf lacks {', '.join(res['missing'])}" if res["missing"] else "")
            + (f"; only part of {', '.join(res['partial'])}" if res["partial"] else "")
            + ("; it had nothing to give" if res["none_available"] else ""))
        return {"where": where, "shelf": res["shelf"], "s": round(time.monotonic() - t0, 1),
                "added": [{"name": x["name"], "amount": x["amount"], "graphic": x["graphic"], "hue": x["hue"]}
                          for x in res["added"]],
                "missing": res["missing"], "partial": res["partial"], "none_available": res["none_available"],
                **({"restocked": res["restocked"], "gone": len(res["gone"])} if restock else {}),
                **({} if res["ok"] else {"error": res.get("error")})}

    def walk_to_item(self, serial: int, rng: int):
        """shelf.resupply's walker: within `rng` of a ground item (guarded Mover)."""
        it = self.link.state()["world"]["items"].get(f"0x{serial:08X}") or {}
        if it.get("x") is None:
            raise Abort(f"the item 0x{serial:08X} isn't in view")
        xy = (it["x"], it["y"])
        self.mover.walk_to(lambda: xy, rng, "to the storage shelf")

    def landing(self) -> dict | None:
        """The landing trips recall to, chosen once a run (lumber_opt.landing_for, as `ctl lumber
        plan` shows it): the rune nearest the grove of our home's rune library (home.libraries)
        and our own books, dangerous ones left out, with a walking route from it into the grove.
        Routes are planned on the map with the Mover's planner (lumber_opt.make_route_fn) and
        cached in the store (lumber_opt.landing_routes); without a map (--no-map) only cached
        answers count and an unknown route counts as there."""
        if self.out_landing is None:
            spot = {**self.k["spot"], "area": self.k["harvest"]["area"]}
            walk = self.mover.walkers.get(self.facet) if self.mover.use_map else None
            routes, new = lumber_opt.landing_routes(self.memory), {}
            ok = lumber_opt.make_route_ok(lumber_opt.make_route_fn(walk) if walk is not None else None, routes, new)
            self.out_landing = lumber_opt.landing_for(spot, self.home, self.books, route_ok=ok,
                                                      bad=lumber_opt.bad_landings(self.memory))
            lumber_opt.save_landing_routes(self.memory, new)
        return self.out_landing

    def go_out(self):
        """Out to the grove (docs/LUMBER_LOOP.md §12.5, user decision 2026-10-04: recall as close
        to it as we can): from the rental room first through its door (leave_room). Unless we
        stand at the grove already (its area + AT_GROVE) or no farther from it than the landing
        (then the harvest walks), recall to the landing (landing): a rune library's row by
        walking to the tome that holds it and recalling from it (escape: one of its charges,
        else our own spell), a rune of our own book where we stand. The 60 s harvest lockout
        after it is waited out by the first chop (outcome 'lockout'). Every attempt is a
        `travel` job event (travel_leg, leg 'out' with the landing), a failed walk or recall too.
        Once out (afield, set the moment the recall lands) it does nothing: harvest_trip runs
        again after an escape (guarded), and an escape at the landing must not send us back to
        the home library (live 2026-10-05 Wintertop: "to the rune library: no route", a death)."""
        if self.afield:
            log("already out at the spot (after an escape): no travel")
            return
        st = self.state()
        if home_mod.in_room(self.facet_now(st), self.home):
            self.leave_room()
            st = self.state()
        area = self.k["harvest"]["area"]
        here = cheb(self.link.pos(st), area["center"]) if self.facet_now(st) == self.facet else None
        if here is not None and here <= area["radius"] + AT_GROVE:
            return
        row = self.landing()
        if row is None:
            raise Abort(f"no landing for spot {self.k['spot']['id']}: no rune of the rune library "
                        f"({', '.join(home_mod.libraries(self.home)) or 'none'}) or of our own books lands near it "
                        f"with a walking route into it")
        if here is not None and here <= row["dist"]:
            log(f"the grove is {here} tiles away, no landing nearer ({row['name']!r}: {row['dist']}); walking")
            return
        library = row["source"] == "library"
        landing = {k: row.get(k) for k in ("source", "library", "tome", "book", "name", "x", "y")}
        leg = {"leg": "out", "landing": landing, "library": row.get("library"),
               "book": row["tome"] if library else row["book"], "witcher_rune": row.get("witcher")}
        where = f"the {row['library']} rune library" if library else f"our {row.get('kind') or 'book'} {row['book']}"
        t0 = time.monotonic()
        if library:
            lib = places.library(row["library"])
            tome_xy = tuple(row["tome_pos"][:2])
            self.doing("to_library", f"Walking to the {lib['name']}", tome_xy)
            try:
                self.mover.walk_to(lambda: tome_xy, lib["use_range"] - 1, "to the rune library")
                if self.wait_for(lambda s: row["tome"] in s["world"]["items"], 3.0) is None:
                    raise Abort(f"the tome {row['tome']} holding {row['name']!r} isn't at the {lib['name']} "
                                f"({tome_xy})")
            except Abort as e:
                self.travel_leg(leg, t0, failure=f"walk: {e}")
                raise
            leg["walk_s"] = round(time.monotonic() - t0, 1)
        self.doing("recall_out", f"Recalling to {row['name']}", (row["x"], row["y"]))
        self.human.wait("use")
        before = self.supplies_now(self.link.state())
        try:
            res = escape_mod.escape(escape_mod.LinkIO(self.link), int(leg["book"], 16), attempts=2, log=log,
                                    rune=row["name"], entry=row.get("entry"))
        except escape_mod.RecallError as e:
            self.travel_leg(leg, t0, failure=f"recall: {e}")
            raise Abort(f"recall to {row['name']!r} from {where} not possible: {e}")
        self.travel_leg(leg, t0, res, before)
        if not res["ok"]:
            if res["failure"] in ("blocked", "unmarked", "restricted"):
                # the rune can't take us there (live 2026-10-05: DTF "Kaern's Manor", "That location is
                # blocked." twice): passed over from now on, like a landing that puts us elsewhere
                lumber_opt.mark_bad_landing(self.memory, row, self.facet, None, why=res["failure"])
            raise Abort(f"recall to {row['name']!r} from {where} failed: {res['failure']}")
        to = tuple(res["to"][:2]) if res.get("to") else None
        if to is None or cheb(to, (row["x"], row["y"])) > LANDING_SLACK:
            why = (f"the recall to {row['name']!r} from {where} landed at {to}, not by its tile "
                   f"({row['x']},{row['y']})")
            if to is not None:                   # passed over from now on (plans, runs): lumber_opt.bad_landings
                lumber_opt.mark_bad_landing(self.memory, row, self.facet, to)
            log(f"{why}: recalling home; no more trips by this landing")
            try:
                self.go_home()
            except Abort as e:
                why += f"; {e}"
            raise Abort(why)
        self.afield = True                       # out: an escape from here on harvests on, not travels again
        self.mount_after_recall()
        log(f"recalled to {row['name']!r} from {where} at {to} ({res['method']}; "
            f"{row['dist']} tiles from the grove's centre)")

    def go_home(self):
        """Home by our own book's default rune (prepare_recall), unless at home already
        (home.at_home: in the room or within NEAR_LANDING of the landing; to_room walks the
        rest). A `travel` job event (leg 'home'); a recall that lands anywhere but home aborts.
        Either way we're no longer out at the spot (afield)."""
        st = self.link.state()
        if self.at_home(st):
            self.afield = False
            return
        self.doing("recall_home", "Recalling home")
        self.human.wait("use")
        leg, t0 = {"leg": "home", "book": f"0x{self.recall_book:08X}"}, time.monotonic()
        before = self.supplies_now(st)
        try:
            res = escape_mod.escape(escape_mod.LinkIO(self.link), self.recall_book, attempts=3, log=log)
        except escape_mod.RecallError as e:
            self.travel_leg(leg, t0, failure=f"recall: {e}")
            raise Abort(f"recall home not possible: {e}")
        self.travel_leg(leg, t0, res, before)
        if not res["ok"]:
            raise Abort(f"recall home failed: {res['failure']}")
        self.afield = False
        st = self.link.state()
        if not self.at_home(st):
            raise Abort(f"the recall home landed at {tuple(self.link.pos(st)[:2])} (facet {self.facet_now(st)}), "
                        f"not by home {tuple(self.home['landing'][:2])}")

    def supplies_now(self, st) -> dict:
        """Mana and reagents in the pack now (travel legs: what a recall cost)."""
        counts, _ = combat.reagents(st["world"], self.self_serial(st))
        return {"mana": (st["world"].get("self") or {}).get("mana"),
                "reagents": {combat.REAGENTS[g]: n for g, n in counts.items()}}

    def travel_leg(self, leg: dict, t0: float, res: dict | None = None, before: dict | None = None,
                   failure: str | None = None):
        """One travel leg (out to the landing, home by our book): a `travel` job
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
        """Recall casts by spell (from the book's gump, or the spell on the book) spend one of each
        recall reagent: tell the ledger, so they don't read as theft (live 2026-10-05: a spell on the
        book's reagents became a theft_suspected juncture)."""
        casts = sum(1 for t in res.get("tries") or []
                    if t.get("method") in escape_mod.SPELL_METHODS and t.get("failure") not in escape_mod.NOT_CAST)
        if casts:
            self.ledger.expect(*[("spent", g, casts) for g in combat.SPELL_REAGENTS[escape_mod.RECALL]])

    def to_room(self):
        """Home (go_home), then into the rental room through the house steward (room.enter:
        his context menu "Room", "Visit Other Rooms" and the owner's row, home.room.owner;
        none: our own room), walking up to him first (walk_to_keeper). Home by distance only
        (go_home walked, the keeper not known in view): to the landing first, where he stands
        by. Converting and storing happen in the room, the safe place."""
        self.go_home()
        self.track_ensure("home")
        st = self.state()
        if home_mod.in_room(self.facet_now(st), self.home):
            return
        landing = tuple(self.home["landing"][:2])
        if room_mod.find_keeper(st) is None and cheb(self.link.pos(st), landing) > room_mod.KEEPER_RANGE:
            self.doing("to_room", "Walking home to the landing", landing)
            self.mover.walk_to(lambda: landing, room_mod.KEEPER_RANGE, "to the home landing")
        serial, label = self.find_keeper()
        self.doing("to_room", f"Going into the rental room via {label}", self.mobile_xy(serial))
        try:
            res = room_mod.enter(escape_mod.LinkIO(self.link), self.human,
                                 (self.home["room"].get("owner") or "").split(), walk=self.walk_to_keeper)
        except room_mod.RoomError as e:
            raise Abort(f"into the rental room: {e}")
        st = self.link.state()
        if not res["ok"] or not home_mod.in_room(self.facet_now(st), self.home):
            raise Abort(f"into the rental room: {res.get('error') or f'on facet {self.facet_now(st)} after it'}")
        log(f"in the rental room ({res['room']}) via {res['via']} at {res['pos']}")

    def mobile_xy(self, serial: int):
        m = self.link.state()["world"]["mobiles"].get(f"0x{serial:08X}") or {}
        return (m["x"], m["y"]) if m.get("x") is not None else None

    def find_keeper(self) -> tuple[int, str]:
        """The house steward (or innkeeper) room.enter goes through: one whose click label is
        known (room.find_keeper), else single-click the invulnerable human NPCs within
        KEEPER_SEARCH tiles, nearest first at a player's pace (agent_link.look_at, the stock
        click), until one's label names a keeper."""
        st = self.state()
        found = room_mod.find_keeper(st)
        if found is not None:
            return found
        me, my = tuple(self.link.pos(st)[:2]), self.self_serial(st)
        cands = sorted((cheb(me, (m["x"], m["y"])), serial_of(key), m.get("name"))
                       for key, m in st["world"]["mobiles"].items()
                       if m.get("x") is not None and m.get("graphic") in threats.HUMAN_BODIES
                       and m.get("notoriety") == 7 and serial_of(key) != my)
        cands = [c for c in cands if c[0] <= KEEPER_SEARCH][:KEEPER_CLICKS]
        log(f"house steward search: {len(cands)} NPC(s) nearby to look at")
        for dist, serial, name in cands:
            self.human.wait("use")
            mark = len(self.link.events)
            look_at(self.link, serial, known_name=bool(name))
            self.wait_for(lambda s: label_since(self.link, serial, mark) is not None, 1.5)
            label = label_since(self.link, serial, mark)
            log(f"  looked at {name or f'0x{serial:08X}'} ({dist} tiles): {label!r}")
            if room_mod.keeper_kind(label):
                return serial, label
        raise Abort(f"no house steward or innkeeper by home ({len(cands)} NPC(s) looked at)")

    def walk_to_keeper(self, serial: int, rng: int):
        """room.enter's walker: up to where the keeper stands now, on his floor (guarded Mover)."""
        last = [self.mobile_xy(serial)]
        z = (self.link.state()["world"]["mobiles"].get(f"0x{serial:08X}") or {}).get("z")

        def where():
            last[0] = self.mobile_xy(serial) or last[0]
            return last[0]
        if last[0] is None:
            raise Abort(f"the house steward 0x{serial:08X} isn't in view")
        self.mover.walk_to(where, rng, "to the house steward", z_ok=same_floor(z) if z is not None else None)

    def store(self) -> int:
        """Every board stack in the pack (the opened pouch included; a live pouch holding boards is
        set off first, unpack) into the room's Resource Stockpile (home.stockpile; user,
        2026-10-05: "It is where we will now drop off all our boards"; stockpile.deposit: one Add
        Items and one target per stack), else into the room's secure chest (home.chest). Then,
        with the room's storage shelf in view, its Restock with our backpack and Resupply (the
        user's routine, 2026-10-05: the shelf takes the spent pouches and whatever else of the
        pack it may hold, and we keep exactly the loadout; the trip row's `restock`); without
        one, the spent pouches we set off, now empty, into the chest so they don't pile up in
        the pack. The chest is opened first like a player would (open_for: the double-click,
        the server's 0x24); each item is declared to the ledger, lifted and dropped into it at
        the auto position (put_away; live 2026-10-04: `ctl act drop` of a stack into this
        chest). Facet 3 has no map: a walk to the chest or the stockpile, only needed when we
        stand beyond reach, plans on walk memory."""
        pile = self.home.get("stockpile")
        self.unpack(BOARDS)
        stacks = self.in_pack(self.state(), BOARDS)
        stored = 0
        if stacks and pile is not None:
            stored = self.to_stockpile(pile, stacks)
        elif stacks:
            stored = self.to_chest(stacks)
        st = self.state()
        if self.args.resupply != "off" and shelf_mod.find_shelves(st):
            self.stats["restock"] = self.resupply_here("room", restock=True)
        else:
            spent = self.spent_pouches(st)     # the pouch the boards were in is empty only now
            if spent:
                self.to_chest([(s, {"amount": 1}) for s in spent], "spent trapped pouch")
        return stored

    def to_chest(self, items: list, what: str = "board stack") -> int:
        """`items` [(serial, item)] into the room's secure chest (open_for, put_away). Returns the
        boards stored (booked in the trip row)."""
        chest, where = home_mod.chest_serial(self.home), tuple(self.home["chest"]["pos"][:2])
        name = self.home["chest"].get("name") or "chest"
        if self.wait_for(lambda s: self.item(s, chest) is not None, 3.0) is None:
            raise Abort(f"the {name} {self.home['chest']['serial']} isn't in view in the rental room")
        if cheb(self.link.pos(self.link.state()), where) > CHEST_REACH:
            self.doing("store", f"Going to the {name}", where)
            self.mover.walk_to(lambda: where, 1, f"to the {name}")
        self.open_for((chest, True), *[(serial, False) for serial, _ in items])
        stored = 0
        for serial, it in items:
            amount = it.get("amount") or 1
            if what == "board stack":
                self.doing("store", f"Storing {amount} boards in the {name}", where)
            self.put_away(serial, amount, chest, what)
            stored += amount
        log(f"stored {stored} {'boards' if what == 'board stack' else what + '(s)'} in the {name}")
        if what != "board stack":
            return 0
        self.stats["stored"] = self.stats.get("stored", 0) + stored
        return stored

    def to_stockpile(self, pile: dict, stacks: list) -> int:
        """The board stacks into the home's Resource Stockpile (stockpile.deposit), declared to the
        ledger first. Returns the boards added; a stack it didn't take aborts the run with the
        boards still in the pack."""
        serial, where = int(pile["serial"], 16), tuple(pile["pos"][:2])
        if self.wait_for(lambda s: self.item(s, serial) is not None, 3.0) is None:
            raise Abort(f"the resource stockpile {pile['serial']} isn't in view in the rental room")
        if cheb(self.link.pos(self.link.state()), where) > stockpile_mod.STOCKPILE_RANGE:
            self.doing("store", "Going to the resource stockpile", where)
            self.mover.walk_to(lambda: where, stockpile_mod.STOCKPILE_RANGE, "to the resource stockpile")
        total = sum(it.get("amount") or 1 for _, it in stacks)
        self.doing("store", f"Adding {total} boards to the resource stockpile", where)
        for s, _ in stacks:
            self.ledger.expect(("moved_out", s))     # into our own stockpile: not theft
        try:
            res = stockpile_mod.deposit(escape_mod.LinkIO(self.link), self.human, serial, [s for s, _ in stacks])
        except stockpile_mod.StockpileError as e:
            raise Abort(f"resource stockpile: {e}")
        added = sum(a["amount"] for a in res["added"])
        self.stats["stored"] = self.stats.get("stored", 0) + added     # a stop below still counts these
        log(f"added {added} boards to the resource stockpile ({len(res['added'])} stack(s))")
        if not res["ok"]:
            raise Abort(f"resource stockpile: {res['error']}; {len(res['left'])} board stack(s) left in the pack")
        return added

    def spent_pouches(self, st) -> list[int]:
        """Trapped pouches we set off ourselves this run, gone off and empty."""
        return [s for s, p in pouch.pack_pouches(st["world"], self.backpack(st)).items()
                if s in self.pops.own and not p["live"] and not pouch.contents(st["world"], s)]

    def put_away(self, serial: int, amount: int, chest: int, what: str):
        """Lift `serial` out of the pack and drop it into the open chest (declared to the ledger)."""
        self.human.wait("use")
        self.ledger.expect(("moved_out", serial))    # into our own chest: not theft
        self.link.act(actions.lift(serial, amount))
        self.human.wait("drag")
        self.link.act(actions.drop(serial, DROP_AUTO, DROP_AUTO, 0, 0, chest))
        pack = self.backpack(self.link.state())
        if self.wait_for(lambda s: serial not in ledger_mod.pack_items(s, pack), 4.0) is None:
            raise Abort(f"{what} 0x{serial:08X} did not leave the backpack")

    # ------------------------------------------------------------ trips
    def episode(self, row):
        self.memory.episode("lumber", row)

    def trip(self, n):
        """One trip: from home out to the grove (go_out) and harvest (logs into the trapped
        pouch) -> home and into the rental room (to_room) -> convert (the pouch set off and
        opened) -> store the boards in the room's chest. The run ends in the room. A monster
        escape in any phase is followed by that phase again
        (the harvest goes on at the next stand out of reach); an abort while harvesting
        stashes the loose logs in the pouch first when that's safe (salvage). Every trip
        leaves an episode row, an aborted one too (outcome 'aborted' + why): leaving those
        out would flatter exactly the spots where trips get cut short."""
        self.stats = dict(self.pre_stats)
        self.pre_stats = {}
        self.trip_n = n
        self.escapes, self.danger = 0, {}
        self.mover.danger = {}
        self.run_arrived, self.creature, self.avoided = None, self.new_creature_tally(), set()
        self.recent_guards, self.dropped_trees, self.no_route, self.switch_tree = {}, {}, set(), None
        self.recalled_home = False
        # the trip began with resupply_home (run): its time and steps are the trip's overhead too
        t0, s0, b0 = self.pre_trip or (time.time(), self.mover.steps, self.mover.blocked_count)
        self.pre_trip = None
        self.trip_t0 = t0
        self.timing = {"walk_out_s": None, "chop_s": 0.0, "tree_walk_s": 0.0, "lockout_s": 0.0, "stationary_s": 0.0}
        self.travel, self.players_seen, self.hatchet_worn = [], {}, None
        self.suspects, self.pouches_used = {}, 0
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
            except Unsafe as e:
                if self.recalled_home and self.at_home(self.link.state()):
                    self.home_after_recall(e, timed)
                raise
            except Abort as e:
                self.salvage(e)
                raise

            def room_phase():               # convert in the rental room: the logs stay in the pouch until then
                timed("to_room", self.to_room, retry=False)
                timed("convert", self.convert, retry=False)
                timed("store", self.store, retry=False)
            self.guarded(room_phase)        # an escape on the way: home and into the room again
            outcome = "stored"
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
                                f"{self.stats.get('stored', 0)} boards stored")

    def home_after_recall(self, e: Unsafe, timed):
        """An escape recall (a creature's or a player's, recall_out) landed us home: the danger
        stayed behind, so into the rental room, convert and store as a finished trip would, then
        the stop goes on (Seer6, 2026-10-05: boxed-in and red-sighting recalls left run after run
        at the landing, ~1380 logs unconverted). The room may refuse us for a while after PvP; a
        failure here is logged and the stop stands either way."""
        log(f"home by an escape recall ({e}); into the rental room to convert and store before stopping")
        hits = self.link.state()["world"]["self"].get("hits")
        self.watch.acknowledge(hits=hits)   # the recall dealt with the hits it cost
        self.start_hits = hits              # check_guards' "hit points dropped": from here on
        self.swingers.clear()         # and whoever swung or cast at us stayed behind
        self.spelled.clear()
        try:
            timed("to_room", self.to_room, retry=False)
            timed("convert", self.convert, retry=False)
            timed("store", self.store, retry=False)
        except (Abort, Escape) as e2:
            log(f"after the recall home: {e2}")

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

        def by_library(leg):                # out by a rune library's tome (its charges), not our own book
            return leg["leg"] == "out" and (leg.get("landing") or {}).get("source") == "library"
        out["supplies"] = {
            # a charge is spent when the recall lands [INFERENCE: RunUO takes it in the spell's effect]
            "library_charges": sum(1 for leg, t in tries if t[0] == "charge" and t[1] is None and by_library(leg)),
            "own_charges": sum(1 for leg, t in tries if t[0] == "charge" and t[1] is None and not by_library(leg)),
            "recall_casts": sum(1 for _, t in tries if t[0] in escape_mod.SPELL_METHODS),
            "trapped_pouches": self.pouches_used}
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
        """Carried wood stays protected: before a harvest abort ends the run, the loose
        logs go into the trapped pouch (stash), or, with no live pouch left, the logs are
        converted to boards as before; unless stopping at once is safer (unsafe_stop).
        Only players and death interrupt it (mode 'salvage': no timeout, HP,
        creature or speech checks); a failure is logged, not raised."""
        why = self.unsafe_stop(e)
        if why:
            log(f"stopping at once, logs not converted: {why}")
            return
        try:
            st = self.link.state()
            if not self.in_pack(st, LOGS):
                return
            self.mode = "salvage"
            if self.log_pouch(st) is not None:
                log(f"keeping the carried logs in the trapped pouch before stopping ({e})")
                self.stash_now()
                return
            log(f"converting the carried logs before stopping ({e})")
            self.convert()
        except Abort as x:
            log(f"could not stash or convert the carried logs: {x}")
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
        st = self.link.wait(lambda s: (s["world"].get("self") or {}).get("name"), 5.0)
        if st is None:
            raise Abort("the proxy doesn't know the character's name yet (it keys the home: harness/data/homes.json)")
        self.guarded(lambda: self.check_guards(self.link.state()))
        self.home = self.find_home(st)
        self.hatchet(st, self.want_hatchet)
        self.books = self.read_books(st)
        self.recall_book = self.prepare_recall(self.link.state())
        self.guarded(lambda: self.track_ensure("start"))   # its human pauses watch for threats (pause)
        # routes bend around where hostile creatures were seen lately (travel_guard)
        self.mover.danger_tiles = travel_guard.remembered_tiles(self.memory, self.facet, self.link.pos(st)[:2])
        for n in range(1, self.args.trips + 1):
            self.trip_n = n                          # intents from here on are this trip's
            self.pre_trip = (time.time(), self.mover.steps, self.mover.blocked_count)
            self.resupply_home()                    # the loadout from the storage shelf at home
            self.mount_home()                       # ride out on our pet (its ghost revived in the room)
            try:
                self.pouch_ready(self.link.state())  # a trip uses a trapped pouch up: none left, no trip
            except Abort:
                self.back_in_room()
                raise
            self.trip(n)
            if self.break_due:
                log(f"break due: boards stored after trip {n}; stopping for the break (ctl break)")
                self.doing("break_due", "Break due: boards stored; waiting in the rental room for the break")
                return
        log(f"loop complete: {self.args.trips} trip(s); waiting in the rental room")
        self.doing("done", f"Finished: {self.args.trips} trip(s); waiting in the rental room")

    def back_in_room(self):
        """A run that stops at home before its trip (resupply left the room, no pouch) waits in
        the rental room, the safe place, like every run's end."""
        try:
            st = self.state()
            if self.at_home(st) and not home_mod.in_room(self.facet_now(st), self.home):
                self.to_room()
        except Abort as e:
            log(f"back into the rental room: {e}")


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
    ap.add_argument("--libraries", default=places.LIBRARIES, help="rune library table (tests: a simulated library)")
    ap.add_argument("--homes", default=home_mod.HOMES,
                    help="homes by character name: landing, rental room, chest (tests: a simulated home)")
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
    ap.add_argument("--harvest-aspect", choices=("ensure", "off"), default="ensure",
                    help="before heading out each trip, make sure the six worn armor pieces carry the Harvest "
                         "aspect (its hue); activate it through the [aspect menu when one doesn't (5 Arcane "
                         "Essence); a missing piece or failure is a low_supplies juncture, the trip goes on")
    ap.add_argument("--resupply", choices=("on", "off"), default="on",
                    help="before each trip at home, top up the loadout from the storage shelf (shelf.py): the "
                         "rental room's, then the one by the landing when the room's lacks something")
    ap.add_argument("--mount", choices=("on", "off"), default="on",
                    help="before each trip at home, ride our pet (mount.py): the one remembered, else the pet "
                         "whose menu offers Release; a ghost is revived by going into the rental room")
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
    places.use(args.witcher, args.libraries)
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

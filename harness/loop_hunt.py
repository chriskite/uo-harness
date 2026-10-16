"""Hunt loop runner: fight monsters at one spot, heal, loot, leave when hurt (docs/HUNT_LOOP.md).

Built for New Player Dungeon mongbats (user decision 2026-10-01: thresholds are task
arguments). The character stands on --fight-spot (default: --spot, the NPD exit tile)
and fights what comes; leaving is war mode off, a walk back to --spot, one step in
--exit-dir onto the exit teleporter.

A fight spot away from the exit (user request 2026-10-03: deeper in the NPD than the
crowded entrance): each visit walks there with the Mover; the pull range, the corpse
range and the idle return are measured from it. The Mover never routes over a known
teleporter tile (memory `teleporters`, learned by walking onto one; the runner adds
the tile one --exit-dir step from --spot for the session), so the walks avoid the NPD
arrival tile (5536,530), itself an exit teleporter. The walk out under attack takes
time and nothing heals on it, so --leave-at is raised by --leave-per-step per step of
the planned route back to --spot (measured on arrival each visit), at most to
--heal-at - LEAVE_HEAL_GAP (heals still go out above it) and never below --leave-at;
a survival leave from a fight spot runs (Mover urgent: no pauses or sidesteps).

Targets come only from the live world model (world.mobiles holds what the stock
client still has: dead and out-of-range mobiles are pruned, docs/WORLDMODEL.md) and
pass the same monsters-only guard as `ctl act attack` (combat.attackable:
threats.identify monster, notoriety 3-6, on screen). A mob attacking us
(attackers(), below) comes first, the one with the lowest hits; otherwise the nearest
within --pull-range of the fight spot. The state is re-read right before every cast and
every target answer; a target that is no longer live is never targeted (the cursor
is cancelled with the stock Esc packet instead; ANTICHEAT.md §10 A12).

Packets are the stock client's (combat.py): war mode on (0x72), 0x34 unless a status
request is outstanding, 0x05; spells are 0xFF sub 4 then 0x6C; loot is 0x06 on the
corpse, then 0x07 lift + 0x08 drop per item, gold first; war mode off when nothing
is near. Pacing comes from humanize.Human. A blue corpse (0xDEAD notoriety 1: another
player's kill) gets nothing; a refused open stops the loot (docs/HUNT_LOOP.md "Loot rights").

Rules (all CLI arguments): heal below --heal-at (healing.py: a heal potion whenever
one can be drunk, else Heal or Greater Heal by the missing hits, Greater Heal from
--gheal-min-missing); the attack spell while mana >= --mana-reserve + its cost and
its reagents (or a spellstone) are in the backpack (combat.can_cast), else melee;
leave below --leave-at (plus a fight spot's route margin), or below --leave-multi-at
with two or more attackers, or when a hostile player comes close. Outside, rest (Heal
/ Greater Heal when one can be cast, no potions; else natural regeneration) to
--rest-to and go back in
(--rest-to 0: stop after leaving). The run ends outside (an idle character in the
NPD gets killed). A spell the server answers with "More reagents are needed for
this spell." (cliloc 502630: our reagent count was wrong) isn't cast again until
the next visit.

Attackers (attackers()): a mob whose 0x2F swing at us is recent; but Outlands has
never sent one (threats.py docstring), so live also: the mob our own latest swing
is at (the server turns us on whoever attacks us: live 2026-10-03 our character
swung at a mongbat that hit it while the runner was engaged on a frog 12 tiles
off), and a war-mode creature adjacent to us while we took "-N" damage within
SWING_RECENT_S.

Pinned targets (live 2026-10-03): a target engaged at 12 tiles (--pull-range above
10; no spell beyond SPELL_RANGE, no walking to melee) kept the runner idle for 2.5
min; a harpy at 10 tiles answered every Lightning with "Target cannot be seen."
for over a minute. One rule for both: an engaged target that is neither adjacent
nor took damage for PIN_S is dropped and skipped for SKIP_S unless it attacks us.
--pull-range is capped at SPELL_RANGE.

Weapon (live 2026-10-03): the item on layer 1/2 at the start is kept in hand. Each
Lightning cast moved Shackleworth's prismatic staff (an arcane staff; wiki Arcane:
casting while wielding one needs Arcane, Magery and Wrestling 80) into the pack. So
with an arcane staff and any of those skills below 80, or once a cast of ours put the
weapon away: no attack spell (melee with it), and inside no heal spell while a heal
potion is in the pack (wait for its cooldown). Whenever the weapon is in the pack it
is put back on with the stock drag `ctl act equip` sends (combat.equip_packets), also
after the rest outside before going back in.

Stationary Penalty (Outlands debuff, stationary.py; user request 2026-10-03): it comes
301-315 s after our last step (measured), at login and after a recall, and asks for
5 steps. When it is on, the runner walks it off at once, mid-fight too (the steps it
asks for + 1, out and back to the fight spot, never over a known teleporter tile);
before it comes it repositions 2-4 steps out and back (--reposition-s). Leaving to
survive comes first. Episode rows count `stationary_clears` and `repositions`.

Guards: overall timeout, movement stall, the agent gate (Link.act waits it out),
server restriction text, death (`death` juncture, stop; no corpse runs). No speech
hold (user 2026-10-15: "GMs don't check attendance for dungeon crawling and stopping
because a player speaks will definitely get us killed"). Junctures: `threat` when leaving,
`low_supplies` when neither a potion nor a castable heal spell (mana, reagents) is
there, `death`. Job events and one episode row per visit (kills, gold, xp, hits
lost) go to the memory store.

Crawl (--crawl, crawl.py; docs/HUNT_LOOP.md "Crawl"): instead of one fight spot, patrol
the floor's waypoints (the pull range, corpse range and Stationary Penalty walks are
measured from where we stand), fight what comes within reach, go one zone deeper (zones:
internal depth bands of route distance from the exit, numbered from 1, on the one dungeon
level entered) only once the zone before is known and the next one's predicted
risk is acceptable, avoid creature types too strong or too slow for us (no pulls, Mover
danger zones), move on from depleted areas; --leave-at grows with the route steps back
to the exit from wherever we are. Every engagement is a `fight` job event (both modes):
the crawl's model starts from them next run.

Recall in, recall out (--enter-recall / --leave-recall; docs/HUNT_LOOP.md "Recall in, recall
out"; user request 2026-10-03, Urukton Bluffs): start anywhere, recall to a rune
(escape.escape: the same retry rules as the red escape), hunt at the arrival (the spot,
the fight spot and the recall spot default to it), and leave by recalling to the book's
default rune instead of walking to an exit teleporter. A hostile player close, a red in
view or "X is attacking you!" recalls at once: every state read, human pause and server
wait checks for it (look / pause / wait_for, as loop_lumber), a target cursor up is
cancelled first. A recall refused where we stand (dungeons: only near a golden gate) walks
to --recall-spot and recalls from there. At home: deposit the pack's gold in the bank
(--bank-gold), rest, wait out --pk-wait after a hostile player, recall back in.

Pets (--pets, tamer.py; docs/HUNT_LOOP.md "Pets"; learned from Logan Wolf's Mausoleum trip
2026-10-12): the pet team fights, the character never turns war mode on nor casts. Pets on
"All Guard Me" (said at every arrival and after every kill) engage what comes into aggro range;
a target they haven't engaged is approached and waited for, never pulled with an aggressive act
(user 2026-10-12). At a target the pets fight: Discordance, Provocation of another mob in the
fight, Peacemaking; "All Kill" only at a mob already on us that the pets leave alone. Veterinary
supplies keep the pets (and us, at 25 %) up; heal potions as above, no heal spell without Magery;
a retreat from a mob next to us below --retreat-at. "All Follow Me" before every recall or walk
out; a pet dead, too hurt or gone leaves, and a run never goes back in with a pet down. Nothing
ever releases or transfers a pet (tamer.irreversible).

Run:  python harness/loop_hunt.py [--kills 5] [--enter] [--fight-spot X Y | --crawl] [--spell lightning]
      python harness/loop_hunt.py --enter-recall BOOK --enter-rune NAME --leave-recall BOOK --bank-gold 1
      python harness/loop_hunt.py --pets --leave-recall BOOK --crawl      # standing in the hunting area
"""
import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import actions  # noqa: E402
import aggro  # noqa: E402
import combat  # noqa: E402
import crawl  # noqa: E402
import escape as escape_mod  # noqa: E402
import healing  # noqa: E402
import los  # noqa: E402
import nav  # noqa: E402
import moongate  # noqa: E402
import threats  # noqa: E402
import stationary  # noqa: E402
import tamer  # noqa: E402
from agent_link import (Abort, Link, Mover, bank_opened, cheb, containers_to_open, find_banker, log,  # noqa: E402
                        same_floor, serial_of)
from errand_bank import GATING_WORDS  # noqa: E402
from humanize import PROFILES, Human  # noqa: E402
from memory import DEFAULT_DB, Memory  # noqa: E402

ROPE_GRAPHIC = 0x14FA         # an adventurer's rope (a stack of 15 in Logan's pack, 20261014_173858)
ROPE_R = 9                    # live jumps 7, 9, 10 tiles; an 11-tile target was refused: one short
ROPE_COOLDOWN_S = 5.3         # knowledge #13325: 5 s from the use (unless PvP flagged), a margin
ROPE_RETRY_S = 2.0            # after a try that found no landing or got no cursor
ROPE_WAIT_S = 1.5             # live: the cursor 0.1 s after "[Rope", the jump ~1 s after the answer
ROPE_LEGS_MAX = 30
SWING_RECENT_S = 10.0         # a mob whose last swing at us is this recent is attacking us
CASTER_REACH = 10            # a caster in war mode this close while we take damage is attacking us (SPELL_RANGE)
PIN_S = 15.0                  # an engaged target neither adjacent nor hurt this long is dropped...
SKIP_S = 60.0                 # ...and not picked again for this long (unless it attacks us)
LOST_ALIVE_S = 30.0           # an engaged target that left the view alive (0x1D, no death): its death this soon counts
GATE_POTION_WAIT_S = 1.5      # hurt beside the leave gate: a heal potion due this soon is drunk before the click
LOOT_WEIGHT_MARGIN = 10       # stones kept free below the weight limit: overloaded, we can't run (live 475/450)
HEAL_IN_PLACE_FLOOR = 0.15    # --heal-in-place: below this share the hits leave fires even with a heal coming
HEAL_SOON_S = 3.0             # --heal-in-place: a heal potion ready this soon is a heal coming
DAMAGE_TEXT = re.compile(r"^-\d+$")   # Outlands' overhead damage number ("-25"), no 0x0B live
CAST_CURSOR_WAIT_S = 4.0      # a spell's cursor comes after its cast delay
SPELL_RANGE = 10              # [INFERENCE] RunUO spell range (12 pre-ML, 10 ML)
SPELL_BLOCK_S = 10.0          # after "too far away" / "cannot be seen": melee only for this long
NO_LOS_CLILOCS = (500446, 500237)   # "That is too far away." / "Target cannot be seen."
CORPSE_WAIT_S = 4.0           # a dead mob's corpse item shows up within this
CONTAINER_WAIT_S = 3.0
# Loot rights go to whoever did the most damage; opening another player's kill is refused
# (captures 20261003_113952/_123614/_125556: all 35 refused opens were blue corpses, all 90
# opened ones grey). The 0xFF sub 0xDEAD notoriety is on the corpse item (world model).
BLUE = 1                      # notoriety "innocent": a corpse someone else has the loot rights to
LOOT_REFUSED = re.compile(r"players cannot commit aggressive actions|you may not loot this corpse", re.I)
POLL_S = 0.4
TELEPORT_TRIES = 3            # turn, step (+ one retry) onto a teleporter
TELEPORT_WAIT_S = 1.0         # a confirmed step onto a teleporter: the move follows within this
WALK_IN_PETS_S = 20.0         # --enter-via: after 'All Follow Me', how much longer to wait for the pets (a teleporter, a lag)
WALK_IN_LAG_R = 8             # --enter-via: a pet farther than this (or out of view) stops the walk until it catches up
WALK_IN_QUIET_S = 5.0         # --enter-via: first wait this long for a lagging pet without a word (it is on follow)
WALK_IN_FIGHT_S = 90.0        # --enter-via: attacked on the way, stand this long at most while the guarding pets take it
WALK_IN_CALM_S = 4.0          # ...until nothing attacks us and no hit has come for this long
HARD_TIMEOUT_GRACE_S = 180.0  # past --timeout the runner leaves; past this too it aborts
GOTO_Z_TOL = 10               # ctl.GOTO_Z_TOL
# Arcane staves (wiki Arcane: "Players with at least 80 skill in Arcane, Wrestling, and
# Magery can continue to cast spells while wielding an Arcane Staff"). 31038 = Outlands'
# "prismatic staff": live 2026-10-03 (capture 20261003_113952) each Lightning cast by
# Shackleworth (Arcane 60, Magery 60, Wrestling 80) moved it from layer 2 into the pack
# (0x1D + 0x25 ~50 ms after the 0xFF cast request, no message)
ARCANE_STAFF_GRAPHICS = frozenset({31038})
ARCANE_CAST_SKILLS = {8: "Arcane", 25: "Magery", 43: "Wrestling"}   # skill ids (skills.mul)
ARCANE_CAST_MIN = 80.0
LEAVE_HEAL_GAP = 0.05         # a fight spot's raised leave-at stays this far below --heal-at
NPD_SPOT = (5535, 529)        # --spot's default without recall: the NPD exit tile
# Recall mode's threat checks (loop_lumber's pattern): every human pause and every wait for a
# server result reads the state at least this often; a drag (lift -> drop) sleeps plainly.
LOOK_EVERY_S = 0.2
BLIND_PAUSES = frozenset({"drag"})
# "<name> is attacking you!": the server's notice that a player made us their target (live
# 2026-10-02/03, 4 of 4 in the store from Bastet / Rasta Brazil, overhead from our own serial;
# never from a monster in all the NPD hunts), before the first hit and even when hidden.
ATTACKED_BY = " is attacking you!"
RECALL_WALK_MAX = 60          # a recall refused farther than this from --recall-spot doesn't walk there
RECALL_SPOT_RANGE = 1         # walk this close to --recall-spot before recalling from there
REFUSED_NEAR = 2              # a recall refused within this many tiles of here: walk first
BANK_RANGE = 3                # say "bank" this close to the banker (RunUO speech range 12)
BANKER_SEARCH = 18            # tiles around us whose human NPCs are clicked to find a banker
BANKER_CLICKS = 12
NO_WALK_FAILURES = ("dead", "mana", "reagents")   # a refused recall the walk to --recall-spot can't help


class PlayerThreat(Exception):
    """Recall mode: a hostile player showed up during a wait or a walk; the hunt loop
    leaves by recall at once (HuntLoop.look)."""

    def __init__(self, why: str):
        super().__init__(why)
        self.why = why


def key_of(serial: int) -> str:
    return f"0x{serial:08X}"


class HuntLoop:
    def __init__(self, link: Link, memory: Memory, args):
        style_defaults(args)
        self.link = link
        self.memory = memory
        self.args = args
        self.t0 = time.monotonic()
        self.human = Human(args.human, seed=args.seed, fast=args.human_fast, log=log,
                           sleep=self.pause if args.leave_recall else None)
        self.mover = Mover(link, memory, self.human, max_blocked=args.max_blocked,
                           guard=self.check_guards, doors=True, use_map=not args.no_map)
        recall_mode = args.leave_recall is not None or args.enter_recall is not None
        # the exit tile (leaving starts here); recall mode: the arrival, set on the first recall in
        spot = args.spot or (None if recall_mode else NPD_SPOT)
        self.spot = tuple(spot) if spot else None
        self.fight_spot = tuple(args.fight_spot) if args.fight_spot else self.spot
        self.recall_spot = tuple(args.recall_spot) if args.recall_spot else self.spot
        self.refused_from = []                   # tiles where the recall home was refused (this run)
        self.fleeing = False                     # recall mode: leaving; the threat checks are off
        self.walking_out = False                 # on the walk to --leave-gate: heal potions between steps
        self.rope_next = 0.0                     # --rope: the next rope jump not before
        self._drinking = False
        self._looking = False                    # in look(): a pause inside the threat checks sleeps plainly
        self._looked_t = -1e9
        self.attack_scan = 0                     # "... is attacking you!" scan cursor into link.events
        self.pk_left_t = None                    # monotonic time of the last leave from a hostile player
        self.out_steps = 0                       # route steps from the fight spot back to the spot (per visit)
        self.leave_at = args.leave_at            # --leave-at plus the route margin (leave_margin)
        self.spell = combat.spell_id(args.spell)
        if self.spell is None:
            raise Abort(f"unknown spell {args.spell!r}")
        self.words = args.target_name.lower().split()
        self.watch = threats.Watch(threats.Params(flee_on_attack=False))
        self.heard_upto = None       # restriction-text scan cursor into link.events
        self.ev_upto = 0             # kill/death scan cursor into link.events
        self.dead = set()            # serials the server reported dead (0xFF sub 0xDEAD)
        self.corpse_of = {}          # dead mobile serial -> its corpse serial (0xDEAD)
        self.lost_alive = {}         # serial -> (last-seen mob dict, monotonic time): engaged, then gone alive (0x1D)
        self.overweight = False      # the last loot stopped at the weight limit: no more looting until it drops
        self.loot_keep = tuple(w.lower() for w in (args.loot_keep or ()))
        self.corpses = []            # [{mob, name, x, y, t}] kills to loot
        self.looted = set()          # corpse serials handled (looted or refused)
        self.engaged = None          # serial we last sent 0x05 for
        self.engaged_mob = None      # its latest world dict
        self.resent_adjacent = False
        self.spell_block = {}        # serial -> monotonic time until spells at it are skipped
        self.no_reagents = set()     # spell ids the server refused for reagents this visit (502630)
        self.skip = {}               # serial -> monotonic time until pick_target passes it over (pinned)
        self.progress_t = 0.0        # monotonic: the engaged target was last adjacent or hurt
        self.hurt_t = {}             # serial -> monotonic time of its latest damage (event or "-N")
        if args.pull_range > SPELL_RANGE:
            log(f"--pull-range {args.pull_range} is beyond spell range: using {SPELL_RANGE}")
            args.pull_range = SPELL_RANGE
        self.prev_hits = None
        self.low_posted = False
        self.potions = healing.PotionClock()
        self.visit_n = 0
        self.visit = None            # this visit's counters (episode row)
        self.totals = {"kills": 0, "lost_kills": 0, "gold": 0, "xp": 0, "hits_lost": 0, "casts": 0, "heals": 0,
                       "potions": 0, "leaves": 0, "visits": 0, "stationary_clears": 0, "repositions": 0,
                       "banked": 0}
        self.still = stationary.Stationary(self.mover, self.human, args.reposition_s)
        self.left_why = None         # (why, severity) of the last leave
        self.weapon = None           # the item on layer 1/2 at the start: kept in hand (rearm)
        self.cast_disarms = False    # a cast of ours put the weapon in the pack (seen this run)
        self.potion_wait_logged = False
        self.rearm_failed = None     # why the last re-equip couldn't be planned (logged once)
        self.fight_rec = None        # the engagement in progress: a `fight` job event when it ends
        self.patrolling = False      # a crawl patrol walk is under way (check_guards may stop it)
        self.crawl = None
        self.team = tamer.Team(self) if args.pets else None   # --pets: the pet team fights (tamer.py)

    # ------------------------------------------------------------ reporting
    def doing(self, kind: str, text: str, target=None, serial=None):
        """Tell the visualizer what the agent is trying to do (proxy-side only)."""
        self.link.intent(text, kind, target, loop="hunt",
                         target_serial=key_of(serial) if serial is not None else None)

    def _where(self, st):
        pos = st["movement"]["pos"] or [None, None]
        return {"facet": st["world"]["self"].get("map"), "x": pos[0], "y": pos[1]}

    def count(self, what, n=1):
        self.totals[what] = self.totals.get(what, 0) + n
        if self.visit is not None:
            self.visit[what] = self.visit.get(what, 0) + n

    # ------------------------------------------------------------ guards
    def check_guards(self, st: dict):
        if time.monotonic() - self.t0 > self.args.timeout + HARD_TIMEOUT_GRACE_S:
            raise Abort(f"overall timeout ({self.args.timeout:.0f}s + {HARD_TIMEOUT_GRACE_S:.0f}s to leave)")
        mv = st["movement"]
        if mv["stalled"]:
            raise Abort(f"movement stalled ({mv['rejects_in_row']} walks rejected in a row)")
        if threats.is_dead(st) or st["world"]["self"].get("dead"):
            self.died(st, "ghost body")
        self.check_restriction()
        self.track(st)
        self.look(st)
        self.drink_on_the_run(st)

    def drink_on_the_run(self, st):
        """On the walk out to --leave-gate, a red's flight included (Mover guard, between steps): a heal
        potion when hurt below --heal-at and one is ready (user 2026-10-13: banshees catch real players on
        the way to the gate too; 'I just run and heal and hope for the best, with pets guarding'), and
        (--pets) the vet supplies on us when no channel runs (user 2026-10-15: "It needs to heal on the run
        when fleeing a red"). A step doesn't stop the channel healing us: it only shortens the reach to the
        pets to 1 (live 20261014_221341 22:35:50-55 Logan walked 2 tiles mid-channel and its finish still
        healed him and resurrected GraveRobber)."""
        if not self.walking_out or self._drinking:
            return
        me = st["world"]["self"]
        self._drinking = True
        try:
            if self.team is not None and self.team.area_peace(st, walking_out=True):
                return                    # through monsters on the way out: a 2 s peace on them (Team.area_peace)
            if me.get("hits") is None or self.frac(me) >= self.args.heal_at:
                return
            if self.potions.ready(time.monotonic()) and healing.heal_potions(st["world"], self.me(st)):
                self.heal(st, potions=True)
            if self.team is not None and time.monotonic() >= self.team.vet_busy_until:
                self.team.vet(self.link.state())
        finally:
            self._drinking = False

    # ------------------------------------------------------------ recall mode: watchful waits
    def look(self, st=None):
        """Recall mode, during a visit: a hostile player (player_threat) raises
        PlayerThreat from any state read, human pause or server wait, so the recall
        goes out at once (loop_lumber's look). Nothing while leaving (fleeing), at home
        or inside the check itself; without --leave-recall it does nothing."""
        if self.args.leave_recall is None or self._looking or self.fleeing or self.visit is None:
            return None
        self._looking = True
        try:
            self._looked_t = time.monotonic()
            why = self.player_threat(st or self.link.state())
        finally:
            self._looking = False
        if why:
            raise PlayerThreat(why)
        return None

    def pause(self, seconds: float, kind: str):
        """Human.sleep in recall mode: spend a human pause (`kind`) reading the state
        and checking for hostile players every LOOK_EVERY_S and once more at its end,
        right before the action it delays (loop_lumber.pause: live 2026-10-03 Bastet came
        into view during an aim pause). A drag (BLIND_PAUSES) sleeps plainly."""
        if kind in BLIND_PAUSES or self._looking or self.fleeing or self.visit is None:
            time.sleep(seconds)
            return
        t0 = time.monotonic()
        end = t0 + seconds
        while True:
            now = time.monotonic()
            if now >= end or now - self._looked_t >= LOOK_EVERY_S:
                try:
                    self.look()
                except PlayerThreat:
                    log(f"the {kind} pause cut short {now - t0:.2f} s into its {seconds:.2f} s")
                    raise
            now = time.monotonic()
            if now >= end:
                return
            time.sleep(min(LOOK_EVERY_S, end - now))

    def wait_for(self, pred, timeout: float):
        """link.wait for a server result with the hostile-player check on every read (look)."""
        return self.link.wait(lambda s: self.look(s) or pred(s), timeout)

    def drop_cursor(self) -> bool:
        """Before a recall: cancel a target cursor that is up (a spell's) with the
        client's Esc (0x6C cancel), so the book's double-click never goes out under our
        own cursor. True when one was cancelled."""
        cur = ((self.link.last or {}).get("world") or {}).get("target") or {}
        if not cur.get("active") or cur.get("cursor_id") is None:
            return False
        self.cancel(cur)
        log(f"target cursor 0x{cur['cursor_id']:08X} cancelled (Esc) for the recall")
        return True

    def attacked_by(self) -> list[str]:
        """Names in "<name> is attacking you!" since the last call (ATTACKED_BY)."""
        ev, names = self.link.events, []
        for e in ev[self.attack_scan:]:
            text = e.get("text") or ""
            if e.get("ev") == "speech_heard" and text.endswith(ATTACKED_BY):
                names.append(text[: -len(ATTACKED_BY)])
        self.attack_scan = len(ev)
        return names

    def player_threat(self, st) -> str | None:
        """Why a hostile player makes us leave now, else None: a red player within spell range; recall mode also a
        red anywhere in view (Bastet struck 4.6 s after sight, docs/PLAN.md "Red sighting") and a player named in
        "... is attacking you!" (seen or not: Bastet attacked hidden). Greys and oranges are no leave (user
        2026-10-15: "I don't think we need to escape from a gray player. Only reds.")."""
        a = self.watch.update(st, recall_s=0.0, margin_s=0.0)
        # one first seen unlabeled waits the label grace (threats.Params.label_grace_s, Watch's "awaiting label"):
        # live 20261015_210709 21:28:21 a tormented soul (human body, notoriety 3) came into view 12 tiles off
        # before its name, was taken for a grey player and the run left; its name came the same second
        pk = [t for t in a.threats if t.player and t.kind == "red" and 0 <= t.distance <= self.watch.params.player_strike_range
              and "awaiting label" not in (t.reason or "")]
        if pk:
            t = pk[0]
            return f"hostile player {t.name or hex(t.serial)} ({t.kind}) at {t.distance} tiles"
        if self.args.leave_recall is None:
            return None
        reds = sorted((t for t in a.threats if t.player and t.kind == "red" and t.distance >= 0),
                      key=lambda t: t.distance)
        if reds:
            t = reds[0]
            return f"red player {t.name or hex(t.serial)} in view at {t.distance} tiles"
        names = self.attacked_by()
        if names:
            return f"{names[0]} is attacking us (server notice)"
        return None

    def patrol_stop(self, st):
        """The crawl's patrol walk stops here (Mover.walk_to `stop`) when there is
        something to do: crawl.Crawl.interrupt, on the deaths and damage seen so far; --pets: an
        advance that would pull too much (tamer.Team.pull_check)."""
        self.scan_events(st)
        return self.crawl.interrupt(st) or (self.team.pull_check(st) if self.team is not None else None)

    def track(self, st):
        """Hits lost (the visit's, the engagement's, the crawl zone's) and, crawling, the
        zone's time and the creatures in view (crawl.Crawl.account)."""
        hits, lost = st["world"]["self"].get("hits"), 0
        if hits is not None:
            if self.prev_hits is not None and hits < self.prev_hits:
                lost = self.prev_hits - hits
                self.count("hits_lost", lost)
                if self.fight_rec is not None:
                    self.fight_rec["hits"] += lost
            self.prev_hits = hits
        if self.crawl is not None and self.visit is not None:
            self.crawl.account(st, lost)

    def check_restriction(self):
        """Server text restricting assistants (ANTICHEAT.md §8 rule 4): stop."""
        if self.heard_upto is None:
            self.heard_upto = len(self.link.events)
        for ev in self.link.events[self.heard_upto:]:
            if ev.get("ev") == "speech_heard" and ev.get("serial") in (None, 0, 0xFFFFFFFF, "0xFFFFFFFF"):
                text = (ev.get("text") or "").lower()
                if any(w in text for w in GATING_WORDS):
                    self.memory.juncture("hunt", "server_restriction",
                                         f"Server restriction: {ev.get('text')!r}"[:300], "urgent",
                                         {"text": ev.get("text")})
                    raise Abort(f"server restriction message: {ev.get('text')!r}")
        self.heard_upto = len(self.link.events)

    def died(self, st, reason):
        a = self.watch.update(st, recall_s=0.0, margin_s=0.0)
        players = [t for t in a.threats if t.hostile and t.player]
        cause = "pk" if players else ("mob" if self.attackers(st) or a.under_attack else "unknown")
        data = {"reason": reason, "cause": cause, "attackers": self.attackers(st), "threats": a.to_dict()}
        self.memory.juncture("hunt", "death", f"Died ({cause}): {reason}", "urgent", data)
        self.memory.job_event("hunt", "death", data, **self._where(st))
        raise Abort(f"died ({cause}): {reason}")

    def state(self):
        st = self.link.state()
        self.check_guards(st)
        self.scan_events(st)
        return st

    # ------------------------------------------------------------ world lookups
    def me(self, st) -> int:
        return st["movement"]["self_serial"]

    def pos(self, st):
        return self.link.pos(st)

    def frac(self, d: dict, cur="hits", top="hits_max") -> float:
        c, m = d.get(cur), d.get(top)
        return c / m if c is not None and m else 1.0

    def at_hunt(self, st) -> bool:
        if self.spot is None:
            return False
        if self.crawl is not None and self.crawl.floor is not None and self.pos(st) in self.crawl.floor:
            return True
        return min(cheb(self.pos(st), self.spot), cheb(self.pos(st), self.fight_spot)) <= combat.VIEW_RANGE

    def anchor(self, st):
        """Where the pull range, the corpse range and Stationary Penalty walks are measured
        from: where we stand when crawling, else the fight spot."""
        return tuple(self.pos(st)[:2]) if self.crawl is not None else self.fight_spot

    def wanted(self, world, key, mob) -> bool:
        text = ((world.get("labels") or {}).get(key) or mob.get("name") or "").lower()
        return bool(text) and all(w in text for w in self.words)

    def live_target(self, st, serial, spell_range=None):
        """(mob, why not): `serial` is live in the world model, not reported dead,
        passes the attack guard and is on screen (within spell_range if given)."""
        world, key = st["world"], key_of(serial)
        mob = world["mobiles"].get(key)
        if mob is None or serial in self.dead:
            return None, "no longer in the client's world"
        ok, why = combat.attackable(world, key)
        if not ok:
            return None, why
        d = cheb(self.pos(st), (mob["x"], mob["y"]))
        if d > (spell_range if spell_range is not None else combat.VIEW_RANGE):
            return None, f"{d} tiles away"
        return mob, ""

    def attackers(self, st) -> list[dict]:
        """Live mobiles attacking us within SWING_RECENT_S (module docstring): a 0x2F
        swing at us (world.swings), the mob our own latest swing is at, or a war-mode
        creature adjacent to us while we take damage. `how` says which."""
        world, me = st["world"], self.me(st)
        labels = world.get("labels") or {}
        now, pos, found = time.time(), self.pos(st), {}
        for att, sw in (world.get("swings") or {}).items():
            a, d, age = serial_of(att), serial_of(sw.get("defender")), now - (sw.get("t") or 0)
            if age > SWING_RECENT_S:
                continue
            if d == me and a != me:
                found.setdefault(a, ("swings at us", age))
            elif a == me and d != me:
                found.setdefault(d, ("our own swing is at it", age))
        hurt = self.hurt_t.get(me)
        if hurt is not None and time.monotonic() - hurt <= SWING_RECENT_S:
            for key, mob in world["mobiles"].items():
                s = serial_of(key)
                if s != me and mob.get("x") is not None and (mob.get("flags") or 0) & threats.FLAG_WARMODE \
                        and cheb(pos, (mob["x"], mob["y"])) <= 1 and not mob.get("pet") \
                        and mob.get("notoriety") in combat.ATTACKABLE_NOTORIETY \
                        and threats.identify(mob, labels.get(key))[0] == "monster":
                    # a pet (ours or anyone's) in war mode beside us is fighting for someone, not at us
                    found.setdefault(s, ("war mode, adjacent while we take damage", time.monotonic() - hurt))
            if not found:
                # nothing swinging at us or beside us: a caster in war mode in its spell reach is (live
                # 20261015_171928 19:49:58-50:22 a banshee 5 tiles off, -21..-28 every 3.3 s, never a swing)
                for key, mob in world["mobiles"].items():
                    s = serial_of(key)
                    if s != me and mob.get("x") is not None and (mob.get("flags") or 0) & threats.FLAG_WARMODE \
                            and cheb(pos, (mob["x"], mob["y"])) <= CASTER_REACH and not mob.get("pet") \
                            and mob.get("notoriety") in combat.ATTACKABLE_NOTORIETY \
                            and aggro.caster(aggro.type_key(labels.get(key) or mob.get("name"))) \
                            and threats.identify(mob, labels.get(key))[0] == "monster":
                        found.setdefault(s, ("a caster in war mode in reach while we take damage",
                                             time.monotonic() - hurt))
        out = []
        for s, (how, age) in found.items():
            key = key_of(s)
            mob = world["mobiles"].get(key)
            if mob is None or mob.get("x") is None or s in self.dead:
                continue
            out.append({"serial": key, "name": labels.get(key) or mob.get("name"), "how": how,
                        "dist": cheb(pos, (mob["x"], mob["y"])), "hits": [mob.get("hits"), mob.get("hits_max")],
                        "last_swing_age_s": round(age, 1)})
        return sorted(out, key=lambda a: a["dist"])

    def pick_target(self, st, pull: bool = True):
        """The serial to fight now, or None: keep the current one while it's valid and
        nothing else is attacking us; else an attacker with the lowest hits; else (pull)
        the nearest within --pull-range of the anchor (the fight spot; crawling, us). A
        target dropped as pinned is passed over until its skip ends, unless it attacks us;
        crawling, so is a creature type the crawl avoids (too strong or too slow for us)."""
        world, me, pos = st["world"], self.me(st), self.pos(st)
        attacking = {serial_of(a["serial"]) for a in self.attackers(st)}
        if self.team is not None:
            attacking |= self.team.pet_attackers(st)
        mono = time.monotonic()
        cands = []
        for key, mob in world["mobiles"].items():
            s = serial_of(key)
            if s == me or mob.get("x") is None or not self.wanted(world, key, mob):
                continue
            if self.skip.get(s, 0) > mono and s not in attacking:
                continue
            if self.crawl is not None and s not in attacking and s != self.engaged \
                    and self.crawl.avoid(st, key, mob):
                continue
            if s not in attacking and aggro.type_key((world.get("labels") or {}).get(key) or mob.get("name")) \
                    in (self.args.never_aggro or aggro.NEVER_AGGRO):
                continue
            if self.live_target(st, s)[0] is None:
                continue
            cands.append((s, mob))
        if pull and self.engaged is not None and any(s == self.engaged for s, _ in cands) \
                and (self.engaged in attacking or not attacking):
            return self.engaged
        att = [(s, m) for s, m in cands if s in attacking]
        if att:
            if any(s == self.engaged for s, _ in att):
                return self.engaged
            return min(att, key=lambda sm: (self.frac(sm[1]), cheb(pos, (sm[1]["x"], sm[1]["y"]))))[0]
        anchor = self.anchor(st)
        near = [(s, m) for s, m in cands if cheb(anchor, (m["x"], m["y"])) <= self.args.pull_range]
        if pull and near:
            return min(near, key=lambda sm: cheb(pos, (sm[1]["x"], sm[1]["y"])))[0]
        return None

    def worn(self, st, serial) -> bool:
        it = st["world"]["items"].get(key_of(serial)) or {}
        return it.get("container") is not None and serial_of(it["container"]) == self.me(st) and bool(it.get("layer"))

    def worn_weapon(self, st):
        """The item we hold on layer 1 (one-handed) or 2 (two-handed), or None."""
        me = self.me(st)
        return next((serial_of(k) for k, it in st["world"]["items"].items()
                     if it.get("layer") in (combat.LAYER_ONE_HANDED, combat.LAYER_TWO_HANDED)
                     and it.get("container") is not None and serial_of(it["container"]) == me), None)

    def casting_disarms(self, st) -> str | None:
        """Why a cast would put the start weapon in the pack, or None: a cast did so
        this run, or it's an arcane staff and Arcane, Magery or Wrestling is below 80
        (unknown skills count as below)."""
        if self.weapon is None:
            return None
        if self.cast_disarms:
            return "a cast put the weapon in the pack"
        w = st["world"]["items"].get(key_of(self.weapon)) or {}
        if w.get("graphic") not in ARCANE_STAFF_GRAPHICS:
            return None
        skills = st["world"]["self"].get("skills") or {}
        low = [f"{name} {(skills.get(str(sid)) or {}).get('value', 0) / 10:g}"
               for sid, name in ARCANE_CAST_SKILLS.items()
               if ((skills.get(str(sid)) or {}).get("value") or 0) / 10 < ARCANE_CAST_MIN]
        return f"arcane staff with {', '.join(low)} (below {ARCANE_CAST_MIN:g})" if low else None

    def rearm(self, st) -> bool:
        """Put the start weapon back on when it's in the pack: the same stock drag as
        `ctl act equip` (combat.equip_packets: 0x07 lift, human drag pause, 0x13)."""
        if self.weapon is None or self.worn(st, self.weapon):
            return False
        try:
            lift, equip = combat.equip_packets(st["world"], self.me(st), self.weapon)
        except ValueError as e:
            if str(e) != self.rearm_failed:
                self.rearm_failed = str(e)
                log(f"can't re-equip the weapon: {e}")
            return False
        name = (st["world"]["items"].get(key_of(self.weapon)) or {}).get("name") or "the weapon"
        self.doing("equip", f"Equipping {name}")
        self.open_for((self.weapon, False))
        self.human.wait("use")
        self.link.act(lift)
        self.human.wait("drag")
        self.link.act(equip)
        ok = self.wait_for(lambda s: self.worn(s, self.weapon), 3.0) is not None
        log(f"re-equipped {name} 0x{self.weapon:08X}" if ok else f"{name} 0x{self.weapon:08X}: equip not confirmed")
        return True

    def backpack(self, st) -> int:
        pack = combat.backpack(st["world"]["items"], self.me(st))
        if pack is None:
            raise Abort("backpack not known to the world model")
        return pack

    def pack_gold(self, st) -> int:
        """Gold in the backpack at any bag depth (piles merge, so serials change)."""
        return self.pack_amount(st, combat.GOLD_GRAPHIC)

    def pack_amount(self, st, graphic) -> int:
        """How many of `graphic` the backpack holds at any bag depth."""
        return sum(it.get("amount") or 1 for _, it in self.pack_items(st, graphic))

    def pack_items(self, st, graphic) -> list:
        """[(serial, item)] of `graphic` in the backpack at any bag depth."""
        items, pack = st["world"]["items"], self.backpack(st)
        out = []
        for k, it in items.items():
            if it.get("graphic") != graphic:
                continue
            c, depth = it.get("container"), 0
            while c is not None and serial_of(c) != pack and depth < 8:
                c, depth = (items.get(key_of(serial_of(c))) or {}).get("container"), depth + 1
            if c is not None and serial_of(c) == pack:
                out.append((serial_of(k), it))
        return out

    # ------------------------------------------------------------ events
    def scan_events(self, st):
        """Deaths, and our target leaving the world model. On Outlands an in-view kill is
        S2C 0xAF + 0x1D + 0xFF sub 0xDEAD at once (capture 20261001_214649): `prune` (why dead,
        from the 0xAF) of our target is the kill, and `mobile_death` names its corpse; a 0x1D
        (`delete`) of it with neither in the same batch is a live mobile leaving the view: a shade
        wolf's stealth mid-fight comes back 3.5-6 s later (docs/NOTES.md, 20261012_210013), so it is
        not a kill (counted as one, the simulator's runner logged 15 % more kills than it made); a
        death of it that follows counts then. A serial in a mobile_death is never targeted again."""
        new = self.link.events[self.ev_upto:]
        died = {serial_of(e["serial"]) for e in new if e.get("ev") == "mobile_death"
                or (e.get("ev") == "prune" and e.get("why") == "dead")}
        for ev in new:
            kind = ev.get("ev")
            if kind == "mobile_death":
                s = serial_of(ev["serial"])
                self.dead.add(s)
                if ev.get("corpse") is not None:
                    self.corpse_of[s] = serial_of(ev["corpse"])
                lost = self.lost_alive.pop(s, None)
                if s == self.engaged:
                    self.on_kill(st, s)
                elif self.team is not None and (mob := self.team.fought_mob(s)) is not None:
                    self.on_kill(st, s, mob)
                elif lost is not None and time.monotonic() - lost[1] <= LOST_ALIVE_S:
                    self.on_kill(st, s, lost[0])
            elif kind in ("delete", "prune") and self.engaged is not None \
                    and serial_of(ev["serial"]) == self.engaged:
                why = ev.get("why", "delete")
                if why == "dead" or self.engaged in died or self.engaged in self.dead:
                    self.on_kill(st, self.engaged)
                else:
                    log(f"target 0x{self.engaged:08X} left the client's world ({why}); not a kill")
                    if why == "delete":
                        self.lost_alive[self.engaged] = (self.engaged_mob or {}, time.monotonic())
                        if self.team is not None:
                            self.team.guard_needed = True   # its aggro cleared: the pets back on guard
                    self.disengage("lost")
            elif kind == "damage" or (kind == "speech_heard" and DAMAGE_TEXT.match(ev.get("text") or "")):
                self.hurt_t[serial_of(ev["serial"])] = time.monotonic()
        self.ev_upto = len(self.link.events)

    def on_kill(self, st, serial, mob=None):
        """A kill of ours: the engaged target, or (--pets) another mob the pets were fighting (`mob`:
        its last-seen world dict)."""
        engaged = mob is None
        mob = (self.engaged_mob or {}) if engaged else mob
        name = mob.get("name") or "a monster"
        self.lost_alive.pop(serial, None)        # its 0xDEAD after this 0xAF is the same kill
        self.count("kills")
        log(f"killed {name} 0x{serial:08X} (kill {self.totals['kills']}"
            f"{'/' + str(self.args.kills) if self.args.kills else ''})")
        self.memory.job_event("hunt", "kill", {"serial": key_of(serial), "name": name,
                                               "hits_max": mob.get("hits_max"), "visit": self.visit_n},
                              **self._where(st))
        if self.args.loot:
            self.corpses.append({"mob": serial, "name": name, "x": mob.get("x"), "y": mob.get("y"),
                                 "t": time.monotonic()})
        if self.crawl is not None and mob.get("x") is not None:
            self.crawl.on_kill((mob["x"], mob["y"]))
        if self.team is not None:
            self.team.on_kill(serial)
        if engaged:
            self.disengage("kill")

    def disengage(self, outcome: str = "ended"):
        """Stop fighting the engaged target; its engagement ends with `outcome` (kill,
        lost, dropped, switched, fled, ended)."""
        if self.fight_rec is not None:
            self.end_fight(outcome)
        self.engaged, self.engaged_mob, self.resent_adjacent = None, None, False

    def end_fight(self, outcome: str):
        """One `fight` job event per engagement (both modes): time from the first attack,
        hits we lost meanwhile, the outcome; the crawl's model learns from it now and,
        through the store, next run (crawl.load_prior joins the loot's gold)."""
        r, self.fight_rec = self.fight_rec, None
        fight_s = round(time.monotonic() - r["t0"], 1)
        st = self.link.last
        data = {"serial": key_of(r["serial"]), "name": r["name"], "body": r["body"], "fight_s": fight_s,
                "hits_lost": r["hits"], "outcome": outcome, "visit": self.visit_n,
                "mode": "crawl" if self.crawl is not None else "spot"}
        if self.crawl is not None:
            data["zone"] = self.crawl.zone_at(r["at"])
            self.crawl.model.add_fight(r["name"], fight_s if outcome == "kill" else None, r["hits"], outcome)
        self.memory.job_event("hunt", "fight", data, **(self._where(st) if st else {}))

    # ------------------------------------------------------------ actions
    def war_mode(self, st, on: bool) -> bool:
        """The stock Tab toggle: nothing is sent when war mode already is `on`."""
        if bool(st["world"]["self"].get("warmode")) == on:
            return False
        self.link.act(actions.war_mode(on))
        self.wait_for(lambda s: bool(s["world"]["self"].get("warmode")) == on, 3.0)
        return True

    def attack(self, serial) -> bool:
        st = self.state()
        mob, why = self.live_target(st, serial)
        if mob is None:
            log(f"not attacking 0x{serial:08X}: {why}")
            return False
        if self.war_mode(st, True):
            self.human.wait("use")
            st = self.state()
            mob, why = self.live_target(st, serial)
            if mob is None:
                log(f"not attacking 0x{serial:08X}: {why}")
                return False
        if stationary.steps_left(st["world"]):
            # it came on during the wait (we first hurt the target): the next tick walks it off first (unstick)
            log(f"not attacking 0x{serial:08X}: the Stationary Penalty is on")
            return False
        name = mob.get("name") or key_of(serial)
        self.doing("attack", f"Attacking {name}", (mob["x"], mob["y"]), serial)
        for pkt in combat.attack_packets(st["world"], serial):
            self.link.act(pkt)
        if self.engaged != serial:
            log(f"attacking {name} 0x{serial:08X} at {cheb(self.pos(st), (mob['x'], mob['y']))} tiles "
                f"(hits {mob.get('hits')}/{mob.get('hits_max')})")
        self.engage(st, serial, mob)
        return True

    def engage(self, st, serial, mob):
        """`serial` is the engaged target from now; a new one starts its `fight` record."""
        name = mob.get("name") or key_of(serial)
        if self.engaged != serial:
            self.progress_t = time.monotonic()
        if self.fight_rec is None or self.fight_rec["serial"] != serial:
            if self.fight_rec is not None:
                self.end_fight("switched")
            self.fight_rec = {"serial": serial, "name": name, "body": mob.get("graphic"), "t0": time.monotonic(),
                              "hits": 0, "at": tuple(self.pos(st)[:2])}
        self.engaged, self.engaged_mob = serial, mob

    def await_cursor(self, mark):
        """The spell's target cursor (world.target) once a `target` event follows
        `mark`; None on a cliloc instead (fizzle, no mana, not recovered) or timeout."""
        end = time.monotonic() + CAST_CURSOR_WAIT_S
        while time.monotonic() < end:
            st = self.state()
            evs = self.link.events[mark:]
            cur = st["world"].get("target") or {}
            if any(e.get("ev") == "target" for e in evs) and cur.get("active") and cur.get("cursor_id") is not None:
                return cur
            heard = [e.get("cliloc") for e in evs if e.get("ev") == "cliloc"]
            if heard:
                log(f"cast answered with cliloc {heard}")
                return None
            time.sleep(0.1)
        return None

    def cast(self, sid, what: str):
        """0xFF sub 4; the target cursor or None. A 'more reagents needed' answer
        (cliloc 502630) stops casting `sid` until the next visit; the server checks
        reagents again at the target answer (refused_after)."""
        mark = len(self.link.events)
        armed = self.weapon is not None and self.worn(self.link.last or self.state(), self.weapon)
        self.link.act(actions.cast_spell(sid))
        self.count("casts")
        cur = self.await_cursor(mark)
        if cur is None:
            log(f"{combat.MAGERY_SPELLS[sid - 1]} at {what}: no target cursor")
            self.refused_after(sid, mark)
        if armed and not self.cast_disarms and not self.worn(self.link.last, self.weapon):
            self.cast_disarms = True
            log(f"casting put the weapon 0x{self.weapon:08X} in the pack: no attack spell from now on")
        return cur

    def refused_after(self, sid: int, mark: int) -> bool:
        """The server's 'more reagents needed' among the events since `mark`: block
        `sid` for the visit. It comes after the target answer for a spellstone the
        server won't take (live 2026-10-03: 17 Greater Heals logged as heals)."""
        if not combat.no_reagents_answer(self.link.events[mark:]):
            return False
        self.no_reagents.add(sid)
        log(f"the server wants more reagents for {combat.MAGERY_SPELLS[sid - 1]}: "
            f"not casting it again this visit")
        return True

    def spell_ok(self, st, sid: int, reserve: int = 0) -> bool:
        """`sid` can be paid for with `reserve` mana left over (combat.can_cast) and the
        server hasn't refused it for reagents this visit."""
        mana = st["world"]["self"].get("mana") or 0
        return sid not in self.no_reagents and combat.can_cast(st["world"], self.me(st), sid, mana - reserve)

    def cancel(self, cur):
        self.link.act(actions.target_cancel(cur["cursor_id"], cur.get("target_type") or 0, cur.get("cursor_type") or 0))

    def cursor_still(self, st, cur) -> bool:
        now = st["world"].get("target") or {}
        return bool(now.get("active")) and now.get("cursor_id") == cur["cursor_id"]

    def cast_at(self, serial) -> bool | None:
        """The attack spell at `serial`: state re-read right before the cast and
        right before the target answer; a target gone meanwhile gets the cursor
        cancelled, never a 0x6C at it. True when cast; None when dropped because the
        leave rule fired while aiming (the caller leaves at once); else False."""
        st = self.state()
        mob, why = self.live_target(st, serial, SPELL_RANGE)
        if mob is None:
            return False
        name = mob.get("name") or key_of(serial)
        self.doing("cast", f"Casting {combat.MAGERY_SPELLS[self.spell - 1]} at {name}", (mob["x"], mob["y"]), serial)
        cur = self.cast(self.spell, name)
        if cur is None:
            return False
        self.human.wait("aim")
        st = self.state()
        mob, why = self.live_target(st, serial, SPELL_RANGE)
        if not self.cursor_still(st, cur):
            log("the spell's cursor went away before the target answer")
            return False
        if mob is None:
            log(f"target 0x{serial:08X} {why} before the spell landed: cancelling the cursor")
            self.cancel(cur)
            return False
        if self.leave_reason(st):
            log("the leave rule fired while aiming: cancelling the cursor")
            self.cancel(cur)
            return None
        mark = len(self.link.events)
        self.link.act(combat.target_mobile(cur, serial, mob))
        got = self.wait_for(lambda s: any(e.get("ev") == "cliloc" or (e.get("ev") == "damage"
                                                                      and serial_of(e["serial"]) == serial)
                                           for e in self.link.events[mark:]), 1.5)
        if got is not None:
            heard = [e.get("cliloc") for e in self.link.events[mark:] if e.get("ev") == "cliloc"]
            if any(c in NO_LOS_CLILOCS for c in heard):
                log(f"spell at 0x{serial:08X}: cliloc {heard}; melee only for {SPELL_BLOCK_S:.0f} s")
                self.spell_block[serial] = time.monotonic() + SPELL_BLOCK_S
            if self.refused_after(self.spell, mark):
                return False
        return True

    def heal(self, st, potions: bool = True) -> bool:
        """One heal on self by healing.choose: a heal potion whenever one can be drunk
        (`potions`), else Heal or Greater Heal by the missing hits, if it can be cast
        (mana, reagents or a spellstone, not refused this visit). When nothing is
        possible, one low_supplies juncture per visit."""
        world, me = st["world"], st["world"]["self"]
        ready = potions and self.potions.ready(time.monotonic())
        choice = healing.choose(world, self.me(st), ready, self.args.gheal_min_missing, self.no_reagents)
        if choice.kind == "potion":
            if self.drink(st, serial_of(choice.potion)):
                return True
            st = self.state()
            world, me = st["world"], st["world"]["self"]
            choice = healing.choose(world, self.me(st), False, self.args.gheal_min_missing, self.no_reagents)
        if self.team is not None and healing.magery(me) <= 0:
            return False                          # no Magery: the vet supplies heal us too (Team.vet)
        if choice.kind == "spell" and self.at_hunt(st) and healing.heal_potions(world, self.me(st)) \
                and (why := self.casting_disarms(st)):
            if not self.potion_wait_logged:
                self.potion_wait_logged = True
                log(f"waiting for the potion cooldown rather than casting ({why})")
            return False
        if choice.kind == "spell":
            return self.heal_spell(st, choice.spell)
        if choice.missing > 0 and not self.low_posted:
            self.low_posted = True
            potions = sum(it.get("amount") or 1 for _, it in healing.heal_potions(world, self.me(st)))
            data = {"item": "heal", "have": {"mana": me.get("mana"), "potions": potions},
                    "need": {"mana": combat.spell_mana(healing.HEAL),
                             "reagents": combat.missing_reagents(world, self.me(st), healing.HEAL)},
                    "hits": [me.get("hits"), me.get("hits_max")], "why": choice.why}
            self.memory.juncture("hunt", "low_supplies",
                                 f"No heal possible at {me.get('hits')}/{me.get('hits_max')} hits: {choice.why}",
                                 "attention", data)
            log(f"low supplies: {choice.why}")
        return False

    def drink(self, st, serial: int) -> bool:
        """Drink the heal potion `serial` as the user's Razor "Drink Heal Potion" hotkey does: one double-click
        on it wherever it sits in the pack, no bag opened (ANTICHEAT.md "Exception: potions ... like Razor";
        user 2026-10-15: "I don't click potions in a satchel I use a Razor hotkey"; live 20261015_171928 the
        satchel's opening cost 3 s at 41-54 hits). True when it healed; a 'wait' refusal (healing.potion_wait:
        the wait Outlands names, or cliloc 500235) sets the potion clock to it and returns False so the caller
        casts instead."""
        me = st["world"]["self"]
        self.doing("heal", f"Drinking a heal potion ({me.get('hits')}/{me.get('hits_max')} hits)")
        self.human.wait("use")
        mark = len(self.link.events)
        self.link.act(actions.dclick(serial))
        answers = (healing.CLILOC_HEALED, healing.CLILOC_FULL_HEALTH)
        self.wait_for(lambda s: any((e.get("ev") == "cliloc" and e.get("cliloc") in answers)
                                    or healing.potion_wait(e) is not None for e in self.link.events[mark:]), 2.0)
        heard = {e.get("cliloc") for e in self.link.events[mark:] if e.get("ev") == "cliloc"}
        now = time.monotonic()
        self.potions.started(now)
        hits = self.link.last["world"]["self"].get("hits")
        wait = next((w for e in self.link.events[mark:] if (w := healing.potion_wait(e)) is not None), None)
        if wait is not None:
            self.potions.refused(now, wait)
            log(f"heal potion refused: {wait:g} s to wait ({me.get('hits')}/{me.get('hits_max')} hits)")
            return False
        self.count("potions")
        log(f"heal potion: {me.get('hits')} -> {hits} hits")
        return healing.CLILOC_HEALED in heard or (hits or 0) > (me.get("hits") or 0)

    def heal_spell(self, st, sid: int) -> bool:
        """Heal or Greater Heal on self: cast, then answer the cursor with self."""
        me = st["world"]["self"]
        name = combat.MAGERY_SPELLS[sid - 1]
        self.doing("heal", f"Casting {name} on myself ({me.get('hits')}/{me.get('hits_max')} hits)")
        cur = self.cast(sid, "myself")
        if cur is None:
            return False
        self.human.wait("aim")
        st = self.state()
        if not self.cursor_still(st, cur):
            return False
        mark = len(self.link.events)
        self.link.act(combat.target_self(cur, self.me(st), self.pos(st), st["world"]["self"].get("body")))
        self.wait_for(lambda s: (s["world"]["self"].get("hits") or 0) > (me.get("hits") or 0)
                      or combat.no_reagents_answer(self.link.events[mark:]), 2.0)
        if self.refused_after(sid, mark):
            return False
        self.count("heals")
        log(f"{name.lower()}: {me.get('hits')} -> {self.link.last['world']['self'].get('hits')} hits")
        return True

    def open_for(self, *needs):
        """Open what the client must show first (containers_to_open), like a player."""
        world, todo = self.link.state()["world"], []
        for serial, itself in needs:
            todo += [s for s in containers_to_open(world, serial, itself) if s not in todo]
        if todo:
            self.link.open_containers(todo, self.human)

    def loot(self, c) -> None:
        """Loot one corpse like `ctl act loot`: refuse human corpses and blue ones (another
        player's kill: loot rights go to the most damage), walk within LOOT_RANGE, open the
        backpack and the corpse (a refusal stops it: open_answer), then lift + drop each item
        into the backpack, gold first, stopping at the weight limit. An item counts as taken
        once it lies in the backpack (or merged into a pile there), never on a lift reject.
        Before each item: a heal below --heal-at, and stop when a leave rule fires (live
        2026-10-03 hits fell 64 -> 49 of 84 during one loot and the runner left without
        drinking)."""
        st = self.state()
        if c.get("corpse") is None:  # 0xDEAD names it; else the corpse that appeared on its tile
            c["corpse"] = self.corpse_of.get(c["mob"]) or next(
                (serial_of(k) for k, it in st["world"]["items"].items()
                 if it.get("graphic") == combat.CORPSE_GRAPHIC and it.get("container") is None
                 and serial_of(k) not in self.looted
                 and c["x"] is not None and cheb((it["x"], it["y"]), (c["x"], c["y"])) <= 1), None)
        me = st["world"]["self"]
        if self.overweight and me.get("weight") is not None and me.get("weight_max") \
                and me["weight"] >= me["weight_max"] - LOOT_WEIGHT_MARGIN:
            self.corpses.remove(c)
            if c.get("corpse") is not None:
                self.looted.add(c["corpse"])
            log(f"weight {me['weight']}/{me['weight_max']}: not looting the {c['name']}")
            return
        self.overweight = False
        corpse = st["world"]["items"].get(key_of(c["corpse"])) if c["corpse"] is not None else None
        if corpse is None:
            if time.monotonic() - c["t"] > CORPSE_WAIT_S:
                log(f"no corpse seen for {c['name']}; nothing to loot")
                self.corpses.remove(c)
                self.kill_gold(c, 0)
            else:
                time.sleep(POLL_S)
            return
        self.corpses.remove(c)
        self.looted.add(c["corpse"])
        if corpse.get("graphic") != combat.CORPSE_GRAPHIC or corpse.get("container") is not None:
            log(f"0x{c['corpse']:08X} isn't a corpse on the ground; not looting")
            return
        if combat.human_corpse(corpse):
            log(f"0x{c['corpse']:08X} ({corpse.get('name')}) is a human corpse: not looted")
            return
        name = corpse.get("name") or c["name"]
        if corpse.get("notoriety") == BLUE:
            log(f"corpse of {name} is someone else's kill (blue): not looted")
            self.refused(c, "blue", st)
            return
        spot = (corpse["x"], corpse["y"])
        # A crawl fights where it meets things and the target may die well away from where we
        # stand (spells reach 10 tiles, mobs flee), so it walks up to twice the pull range for
        # its own kill (live 14:26:37: a mongbat dead 12 tiles away was left with its gold).
        reach = self.args.pull_range * (2 if self.crawl is not None else 1) + combat.LOOT_RANGE
        if cheb(self.anchor(st), spot) > reach:
            log(f"corpse at {spot} is too far ({cheb(self.anchor(st), spot)} tiles) to loot; leaving it")
            return
        self.doing("loot", f"Looting {name}", spot, c["corpse"])
        if cheb(self.pos(st), spot) > combat.LOOT_RANGE:
            try:
                self.mover.walk_to(lambda: spot, 1, "to the corpse")
            except Abort as e:
                if not str(e).startswith("to the corpse"):
                    raise
                log(f"corpse of {name} at {spot}: {e}; leaving it")   # e.g. on a ledge (sim: 21 runs ended here)
                return
            st = self.state()
        pack = self.backpack(st)
        gold0, sgold0 = self.pack_gold(st), st["world"]["self"].get("gold")
        self.open_for((pack, True))
        self.human.wait("use")
        mark = len(self.link.events)
        self.link.act(actions.dclick(c["corpse"]))

        def answered(s):    # a refusal, or the 0x24 and its contents (they may lag it: an empty corpse waits)
            a = self.open_answer(mark, c["corpse"])
            return a is not None and (a != "open" or bool(combat.corpse_contents(s["world"], c["corpse"])))
        st = self.wait_for(answered, CONTAINER_WAIT_S) or self.state()
        answer = self.open_answer(mark, c["corpse"])
        if answer not in (None, "open"):
            log(f"opening the corpse of {name} refused ({answer!r}): not looted")
            self.refused(c, answer, st)
            return
        inside = combat.corpse_contents(st["world"], c["corpse"])
        # Mastery-chain XP of a kill = the creature's gold value x our damage share
        # (wiki Experience_Gain); solo, the gold the corpse holds is that value [INFERENCE].
        xp = sum(it.get("amount") or 1 for it in inside.values() if it.get("graphic") == combat.GOLD_GRAPHIC)
        self.count("xp", xp)
        taken, rejects = [], 0
        items = combat.loot_order(inside)
        if not self.args.loot_all:
            skipped = [combat.item_name(it) for _, it in items if not combat.worth_looting(it, self.loot_keep)]
            items = [(k, it) for k, it in items if combat.worth_looting(it, self.loot_keep)]
            if skipped:
                log(f"leaving {len(skipped)} item(s) of no value to us: {', '.join(sorted(set(skipped))[:6])}")
        for k, it in items[: self.args.loot_max]:
            st = self.state()
            me = st["world"]["self"]
            if self.leave_reason(st):
                log("a leave rule fired while looting: leaving the rest")
                break
            if me.get("hits") is not None and self.frac(me) < self.args.heal_at and self.heal(st):
                me = self.state()["world"]["self"]
            if me.get("weight") is not None and me.get("weight_max") \
                    and me["weight"] + combat.item_weight(it) > me["weight_max"] - LOOT_WEIGHT_MARGIN:
                log(f"weight {me['weight']}/{me['weight_max']}: no room for {combat.item_name(it)} "
                    f"(~{combat.item_weight(it):.0f} stones); leaving it")
                if it.get("graphic") == combat.GOLD_GRAPHIC or me["weight"] >= me["weight_max"] - LOOT_WEIGHT_MARGIN:
                    self.overweight = True
                    break
                continue
            self.human.wait("drag")
            s, graphic = serial_of(k), it.get("graphic")
            before, lift_mark = self.pack_amount(st, graphic), len(self.link.events)

            def landed(st2):
                now = st2["world"]["items"].get(k)
                if now is not None:
                    return now.get("container") is not None and serial_of(now["container"]) == pack
                return self.pack_amount(st2, graphic) > before    # merged into a pile in the pack

            def rejected():
                return any(e.get("ev") == "lift_reject" for e in self.link.events[lift_mark:])
            for pkt in combat.grab_packets(s, it.get("amount") or 1, pack):
                self.link.act(pkt)
            st2 = self.wait_for(lambda st2: rejected() or landed(st2), CONTAINER_WAIT_S)
            if rejected():
                rejects += 1
                log(f"lift of 0x{s:08X} rejected: not taken")
            elif st2 is not None:
                taken.append({"serial": k, "graphic": f"0x{graphic or 0:04X}", "amount": it.get("amount")})
        st = self.state()
        gained = max(self.pack_gold(st) - gold0,
                     (st["world"]["self"].get("gold") or 0) - (sgold0 or 0) if sgold0 is not None else 0)
        self.count("gold", gained)
        self.kill_gold(c, gained, spot)
        log(f"looted {len(taken)} item(s) from {name}: +{gained} gold, ~{xp} xp"
            + (f" ({rejects} lift(s) rejected)" if rejects else ""))
        self.memory.job_event("hunt", "loot", {"corpse": key_of(c["corpse"]), "mob": key_of(c["mob"]),
                                               "name": c["name"], "gold": gained, "xp": xp, "items": taken,
                                               "lift_rejects": rejects, "visit": self.visit_n}, **self._where(st))

    def open_answer(self, mark, corpse):
        """The server's answer to our corpse double-click, from the events after `mark`:
        "open" (its 0x24), the refusal text ("Players cannot commit aggressive actions in
        that location." / "You may not loot this corpse.", live as 0xAE system text), or
        None while there is neither."""
        for ev in self.link.events[mark:]:
            kind = ev.get("ev")
            if kind == "container_open" and serial_of(ev.get("serial") or 0) == corpse:
                return "open"
            if kind in ("speech_heard", "cliloc") and LOOT_REFUSED.search(ev.get("text") or ""):
                return ev["text"]
        return None

    def refused(self, c, why, st):
        """A corpse that isn't ours to loot (blue, or our open refused): someone else did the
        most damage, so the kill was theirs. Its kill credit is taken back (kills - 1,
        lost_kills + 1); no xp, gold or gold-model sample (the 0 gold isn't the creature's:
        the `loot` event's `refused` keeps crawl.load_prior from learning it)."""
        self.count("kills", -1)
        self.count("lost_kills")
        self.memory.job_event("hunt", "loot", {"corpse": key_of(c["corpse"]), "mob": key_of(c["mob"]),
                                               "name": c["name"], "gold": 0, "xp": 0, "items": [],
                                               "refused": why, "visit": self.visit_n}, **self._where(st))

    def kill_gold(self, c, gold, at=None):
        """Crawling: a kill's gold for the model (0 when its corpse never showed: a pet or
        player took it) and for the zone it lay in."""
        if self.crawl is None:
            return
        self.crawl.model.add_gold(c["name"], gold)
        if at is not None or c.get("x") is not None:
            self.crawl.on_gold(at or (c["x"], c["y"]), gold)

    # ------------------------------------------------------------ leaving / entering
    def leave_reason(self, st):
        """(why, severity) when the rules say leave now, else None: a hostile player
        (player_threat; urgent: without --leave-recall no going back in), two or more
        attackers below --leave-multi-at, below --leave-at plus the fight spot's route
        margin (leave_margin); --pets: a pet dead, too hurt or gone (tamer.Team.leave_reason).
        --heal-in-place (a long way out, user 2026-10-13: leave less, heal in place): the
        two-attacker rule only below --leave-at too, and no route margin."""
        me = st["world"]["self"]
        pk = self.player_threat(st)
        if pk:
            return pk, "urgent"
        if self.team is not None and (why := self.team.leave_reason(st)):
            return why
        if me.get("hits") is None:
            return None
        f, att = self.frac(me), self.attackers(st)
        if self.args.heal_in_place and f >= HEAL_IN_PLACE_FLOOR and self.heal_coming(st):
            # user 2026-10-15 (live 20261015_171928 17:53:31, out at 20/100 with a potion ready, pets left behind):
            # "Logan didn't need to run and leave there, he could have just bandaged himself and positioned a
            # little better": with a heal on its way, heal and reposition; leave when none is
            return None
        multi = min(self.args.leave_multi_at, self.leave_at) if self.args.heal_in_place else self.args.leave_multi_at
        if len(att) >= 2 and f < multi:
            return (f"{len(att)} attackers and hits {me['hits']}/{me['hits_max']} below "
                    f"{multi:.0%}"), "attention"
        if f < self.leave_at:
            out = f" (--leave-at + {self.out_steps} steps out)" if self.leave_at != self.args.leave_at else ""
            return f"hits {me['hits']}/{me['hits_max']} below {self.leave_at:.0%}{out}", "attention"
        return None

    def heal_coming(self, st) -> bool:
        """--heal-in-place: a heal potion ready within HEAL_SOON_S, or (--pets) a vet channel running or one
        that can start now, supplies in the pack (its finish heals us too: live 20261015_171928 19:49:47 the
        two-attacker leave at 37/100 went 1 s before the vet channel that took him to 100 on the way out)."""
        world = st["world"]
        if healing.heal_potions(world, self.me(st)):
            due = (self.potions.last or -1e9) + healing.POTION_COOLDOWN_S - time.monotonic()
            if due <= HEAL_SOON_S:
                return True
        if self.team is None:
            return False
        return time.monotonic() < self.team.vet_busy_until \
            or bool(healing.in_pack(world, self.me(st), tamer.VET_SUPPLIES_GRAPHIC))

    def leave_called_off(self, st, severity) -> str | None:
        """On the way out after a hurt leave (severity attention): why to call it off, Logan back at
        --heal-at with no leave rule firing any more (user 2026-10-15, live 20261015_171928 18:57:20-40:
        out at 8/100, healed on the run to 98 by the gate, and then he waited there for the pets in a
        banshee's sight: "There's no point in running to the gate when he's fully healed")."""
        me = st["world"]["self"]
        if severity != "attention" or me.get("hits") is None or self.frac(me) < self.args.heal_at:
            return None
        return None if self.leave_reason(st) else f"healed to {me['hits']}/{me['hits_max']}"

    def leave(self, st, why, severity) -> str | None:
        """War mode off, back to the spot (running, Mover urgent, when it's a survival
        leave from a separate fight spot), the step onto the exit teleporter. A
        `threat` juncture unless it's the planned end (severity info). "called off" when
        leave_called_off stopped it on the way to the moongate."""
        self.left_why = (why, severity)
        if self.args.leave_recall is not None:
            return self.leave_by_recall(st, why, severity)
        data = {"why": why, "hits": [st["world"]["self"].get("hits"), st["world"]["self"].get("hits_max")],
                "attackers": self.attackers(st), "visit": self.visit_n, "kills": self.totals["kills"],
                "route_steps": self.out_steps}
        if severity != "info":
            self.memory.juncture("hunt", "threat", f"Leaving the hunt: {why}", severity, data)
        self.memory.job_event("hunt", "leave", data, **self._where(st))
        log(f"LEAVING: {why}")
        self.count("leaves")
        self.disengage("fled" if severity != "info" else "ended")
        if self.crawl is not None:
            self.crawl.on_leave(self.pos(st), severity != "info")
        self.doing("leave", f"Leaving: {why}", self.spot)
        if self.war_mode(st, False):
            self.human.wait("read")
        if self.team is not None:
            self.team.follow(severity)
        self.mover.walk_to(lambda: self.spot, 0, "to the exit", max_moves=max(250, 3 * self.out_steps),
                           urgent=severity != "info" and (self.crawl is not None or self.fight_spot != self.spot))
        self.teleport(self.args.exit_dir, "the exit")
        if self.team is not None:
            self.team.landed(self.state(), "out")
        self.end_visit(why)

    def teleport(self, d, what):
        """Step in direction d onto a teleporter (a turn first if not facing it). Some
        deny the step and then move you (the NPD exit, live 2026-09-30), others may
        confirm it and move you right after."""
        for _ in range(TELEPORT_TRIES):
            self.mover.pace(True)
            out = self.mover.step(d, run=True)
            if out == "moved":
                here = tuple(self.pos(self.link.state())[:2])
                if self.link.wait(lambda s: cheb(self.pos(s), here) > 1, TELEPORT_WAIT_S) is not None:
                    out = "teleported"
            if out == "teleported":
                log(f"{what}: teleported to {self.pos(self.link.state())[:3]}")
                return
            if out == "moved":
                raise Abort(f"{what}: the step in direction {d} moved without a teleport")
        raise Abort(f"{what}: no teleport after {TELEPORT_TRIES} steps in direction {d}")

    # ------------------------------------------------------------ recall in, recall out
    def recall(self, book: int, rune: str | None, urgent: bool, leg: str) -> dict:
        """One recall leg with escape.escape (charges first, else the spell; a disturbed
        cast recast as soon as the server takes it): a survival leave keeps on until it
        lands or the escape budget is spent, a planned leg tries 3 casts. A `travel` job
        event either way (leg in / home)."""
        armed = self.weapon is not None and self.worn(self.link.state(), self.weapon)
        try:
            res = escape_mod.escape(escape_mod.LinkIO(self.link), book, rune=rune, log=log,
                                    attempts=None if urgent else 3)
        except escape_mod.RecallError as e:
            res = {"ok": False, "failure": "unusable", "error": str(e), "attempts": 0, "tries": []}
        if armed and not self.cast_disarms and not self.worn(self.link.state(), self.weapon):
            # live 2026-10-03: the recall in (a tome charge, Kal Ort Por) put Shackleworth's
            # prismatic staff in the pack, like his Lightning casts
            self.cast_disarms = True
            log(f"the recall put the weapon 0x{self.weapon:08X} in the pack: no attack spell from now on")
        st = self.link.state()
        self.memory.job_event("hunt", "travel", {"leg": leg, "book": key_of(book), "want_rune": rune,
                                                 "visit": self.visit_n, **res}, **self._where(st))
        return res

    def walk_to_recall_spot(self, urgent: bool):
        self.doing("leave", f"Walking to the recall spot {self.recall_spot[0]},{self.recall_spot[1]}",
                   self.recall_spot)
        self.mover.walk_to(lambda: self.recall_spot, RECALL_SPOT_RANGE, "to the recall spot",
                           max_moves=max(250, 3 * self.out_steps), urgent=urgent)

    def recall_home(self, urgent: bool) -> dict:
        """Recall to --leave-recall's default rune from where we stand. Refused there
        (Outlands dungeons allow it only near a golden gate; escape's 'restricted', or no
        arrival): walk to --recall-spot and recall from there; once refused near here,
        walk there first. The result carries `refused` (the first try) and `walked_first`."""
        here = tuple(self.pos(self.link.state())[:2])
        walked_first = False
        if self.recall_spot is not None and cheb(here, self.recall_spot) > RECALL_SPOT_RANGE \
                and any(cheb(here, t) <= REFUSED_NEAR for t in self.refused_from):
            log(f"recall was refused near {here} before: walking to the recall spot {self.recall_spot} first")
            self.walk_to_recall_spot(urgent)
            walked_first = True
        res = self.recall(self.args.leave_recall, None, urgent, "home")
        here = tuple(self.pos(self.link.state())[:2])
        # "That location is blocked." is the rune's landing, not where we stand; from another region (the town
        # the moongate took us to) the recall spot is no walk at all (live 20261015_204514 21:06:26 at Totem:
        # "to the recall spot: no route")
        if not res["ok"] and res["failure"] not in NO_WALK_FAILURES + ("unusable", "blocked") \
                and self.recall_spot is not None and RECALL_SPOT_RANGE < cheb(here, self.recall_spot) <= RECALL_WALK_MAX:
            self.refused_from.append(here)
            log(f"recall home from {here} failed ({res['failure']}): walking to the recall spot {self.recall_spot}")
            first = res
            self.walk_to_recall_spot(urgent)
            res = self.recall(self.args.leave_recall, None, urgent, "home")
            res["refused"] = {"from": list(here), "failure": first["failure"], "attempts": first.get("attempts")}
        res["walked_first"] = walked_first
        return res

    def rope_ready_now(self, st) -> bool:
        """--rope: an Adventurer's Rope in the pack and its cooldown over."""
        return bool(self.args.rope) and time.monotonic() >= self.rope_next \
            and bool(healing.in_pack(st["world"], self.me(st), ROPE_GRAPHIC))

    def rope_jump(self, goal) -> bool:
        """One Adventurer's Rope jump toward `goal` (Logan's escapes: '[Rope', then the ground target;
        jumps of 7-10 tiles, an 11-tile target refused): the farthest tile of the route there within ROPE_R
        that is in sight, 4 or more tiles off, its z walked along the route (a ledge's z beside it would
        strand us). True when we landed. Knowledge #13325: a Teleport by the spell's rules, 5 s cooldown
        from the use; followers on Follow come along (#5141)."""
        st = self.link.state()
        if not self.rope_ready_now(st):
            return False
        self.rope_next = time.monotonic() + ROPE_RETRY_S
        path, walk = self.mover.plan(st, nav.within(goal, 0), mobiles=False)
        sight = los.sight_for(self.mover.walkers, st["world"]["self"].get("map")) if walk is not None else None
        if not path or sight is None:
            return False
        pos = tuple(self.pos(st)[:3])
        best, cur = None, pos
        for t in path[1:]:
            if cheb(pos, t) > ROPE_R:
                break
            cur = walk.can_walk(cur[0], cur[1], cur[2], nav.direction(cur[:2], t[:2]))
            if cur is None or cur[:2] != tuple(t[:2]):
                break
            if cheb(pos, cur) >= 4 and sight.can_see(pos, cur):
                best = cur
        if best is None:
            return False
        mark = len(self.link.events)
        self.link.act(actions.say_ascii("[Rope"))
        if self.link.wait(lambda s: any(e.get("ev") == "target" for e in self.link.events[mark:])
                          and (s["world"].get("target") or {}).get("active"), ROPE_WAIT_S) is None:
            log("rope: no target cursor")
            return False
        cur = self.link.state()["world"]["target"]
        self.human.wait("aim")
        self.link.act(actions.target_xyz(cur["cursor_id"], best[0], best[1], best[2], 0, cur.get("cursor_type") or 0))
        landed = self.link.wait(lambda s: cheb(s["movement"]["pos"], best) <= 1, ROPE_WAIT_S) is not None
        self.rope_next = time.monotonic() + ROPE_COOLDOWN_S
        self.count("ropes" if landed else "rope_misses")
        log(f"rope to {best[:2]} ({cheb(pos, best)} tiles): " + ("landed" if landed else "no jump"))
        return landed

    def leave_by_gate(self, severity: str) -> dict:
        """--leave-gate: walk to the moongate with the pets still guarding (they fight what comes at
        us on the way and follow: 'All Follow Me' first would leave us the target), then beside it
        'All Follow Me' (tamer.follow: a hurt leave doesn't wait) so they come through, a double-click
        on the gate from that tile (live 20261013_204518; the step onto it when its item isn't in view)
        and the travel to --gate-dest (moongate.travel). From a hostile player (severity urgent), as
        Logan fled reds three times (20261013_173449, 20261014_170144, _173858): 'All Follow Me' at
        once and Adventurer's Rope jumps along the way whenever one is ready (rope_jump).
        -> {ok, from, to, dest, elapsed_s[, error]}."""
        gate = tuple(self.args.leave_gate)
        urgent = severity != "info"
        flee = severity == "urgent"
        self.doing("leave", f"Walking to the moongate at {gate[0]},{gate[1]}", gate)
        self.walking_out = True
        try:
            if self.team is not None:
                if flee:
                    self.team.follow(severity)
                else:
                    self.team.guard()
                path, _ = self.mover.plan(self.link.state(), nav.within(gate, 0), mobiles=False)
                near = tuple(path[-2]) if path and len(path) >= 2 else None
                stop = (lambda s: "a rope is ready" if self.rope_ready_now(s) else None) if flee \
                    else (lambda s: self.leave_called_off(s, severity))
                for _ in range(ROPE_LEGS_MAX):
                    if near is None or cheb(self.pos(self.link.state()), gate) <= 1:
                        break
                    if flee and self.rope_jump(near):
                        continue
                    why = self.mover.walk_to(lambda: near, 0, "to the moongate", urgent=urgent,
                                             max_moves=max(250, 3 * self.out_steps), stop=stop)
                    if why and not flee:
                        return {"ok": False, "called_off": why, "from": list(self.pos(self.link.state())[:2]),
                                "dest": self.args.gate_dest}
                    if not why:
                        break
                if (why := self.leave_called_off(self.link.state(), severity)):
                    return {"ok": False, "called_off": why, "from": list(self.pos(self.link.state())[:2]),
                            "dest": self.args.gate_dest}
                me = self.link.state()["world"]["self"]
                hurt = severity != "info" and me.get("hits") is not None and self.frac(me) < self.args.heal_at
                if not flee:          # fleeing: "All Follow Me" went out at the start of the run here
                    self.team.follow(severity, wait=not hurt)    # hurt: through the gate now, pets or not
            mark = len(self.link.events)
            st = self.link.state()
            me = st["world"]["self"]
            due = (self.potions.last or -1e9) + healing.POTION_COOLDOWN_S - time.monotonic()
            if me.get("hits") is not None and self.frac(me) < self.args.heal_at and 0 < due <= GATE_POTION_WAIT_S \
                    and healing.heal_potions(st["world"], self.me(st)):
                time.sleep(due)    # live 20261015_115604 12:53:42-43.5: due 0.4 s after the gate's click, dead at 43/100
            st = self.state()                                # its guards: a potion if one is due
            serial = moongate.gate_at(st, gate)
            if serial is not None and cheb(self.pos(st), gate) <= 1:
                self.human.wait("gate_macro" if urgent else "use")
                self.link.act(actions.dclick(serial))
            else:
                self.mover.walk_to(lambda: gate, 0, "onto the moongate", gate=gate, urgent=urgent,
                                   max_moves=max(250, 3 * self.out_steps))
            # a leave to survive: the gate's menu at the user's macro pace (live 20261015_210709 21:57:21-25.4 the red's
            # flight took 4.2 s from the gate's tile to Totem: a second "All Follow Me", the reader's pauses; the user:
            # "My macro gets me out basically instantly")
            return moongate.travel(self.link, self.human, self.args.gate_dest, since=mark, log=log, urgent=urgent)
        except (Abort, moongate.GateError) as e:
            return {"ok": False, "from": list(self.pos(self.link.state())[:2]), "error": str(e),
                    "dest": self.args.gate_dest}
        finally:
            self.walking_out = False

    def leave_by_recall(self, st, why, severity):
        """Leave by recalling to --leave-recall's default rune (recall_home), at once and
        before any bookkeeping: a target cursor up is cancelled first (drop_cursor), no
        war mode toggle or walk unless the planned end (severity info). With --leave-gate the
        moongate first (no recall in a dungeon but near a gold gate), then the recall from where
        it took us. Then the usual `threat` juncture and `leave` event (with the recall), the
        visit row, and at home the bank (--bank-gold). A gate or recall that doesn't land stops
        the run."""
        t_fire, pos = time.monotonic(), self.pos(st)
        data = {"why": why, "hits": [st["world"]["self"].get("hits"), st["world"]["self"].get("hits_max")],
                "attackers": self.attackers(st), "visit": self.visit_n, "kills": self.totals["kills"],
                "route_steps": self.out_steps}
        log(f"LEAVING by {'the moongate' if self.args.leave_gate else 'recall'}: {why}")
        self.fleeing = True
        try:
            cancelled = self.drop_cursor()
            self.disengage("fled" if severity != "info" else "ended")
            self.doing("leave", f"Leaving: {why}", tuple(self.args.leave_gate) if self.args.leave_gate
                       else self.recall_spot)
            if severity == "info" and self.war_mode(st, False):
                self.human.wait("read")
            if self.team is not None and not self.args.leave_gate:
                self.team.follow(severity)
            data["react_s"] = round(time.monotonic() - t_fire, 2)
            gate = None
            if self.args.leave_gate:
                gate = data["gate"] = self.leave_by_gate(severity)
                if gate.get("called_off"):
                    log(f"leave called off at {tuple(gate['from'])}: {gate['called_off']}; back to the pets")
                    self.memory.job_event("hunt", "leave_called_off", {**data, "called_off": gate["called_off"]},
                                          **self._where(self.state()))
                    self.left_why = None
                    if self.team is not None:
                        self.team.rejoin(self.state())
                    return "called off"
                if gate["ok"] and self.team is not None:
                    self.team.landed(self.state(), "gate")
            res = self.recall_home(severity != "info") if gate is None or gate["ok"] else \
                {"ok": False, "failure": f"moongate: {gate.get('error')}"}
        finally:
            self.fleeing = False
        data.update(cursor_cancelled=cancelled,
                    recall={k: res.get(k) for k in ("ok", "method", "from", "to", "elapsed_s", "attempts",
                                                    "failure", "refused", "walked_first")})
        if severity != "info":
            self.memory.juncture("hunt", "threat", f"Leaving the hunt by recall: {why}"
                                 + ("" if res["ok"] else f" (recall failed: {res['failure']})"), severity, data)
        self.memory.job_event("hunt", "leave", data, **self._where(st))
        self.count("leaves")
        if self.crawl is not None:
            self.crawl.on_leave(pos, severity != "info")
        if severity == "urgent":
            self.pk_left_t = time.monotonic()
        self.end_visit(why)
        if not res["ok"]:
            raise Abort(f"recall home failed ({res['failure']}) after: {why}")
        log(f"recalled home to {tuple(res['to'])} ({res['method']}, {res['elapsed_s']} s)")
        self.rearm(self.state())            # a recall knocks an arcane staff into the pack
        if self.team is not None:
            self.team.landed(self.state(), "home")
        if self.args.bank_gold:
            self.bank()

    def enter_by_recall(self):
        """Recall to --enter-recall's rune --enter-rune (its default without one). The
        first arrival is the hunt: --spot, --fight-spot and --recall-spot default to it."""
        book, rune = self.args.enter_recall, self.args.enter_rune
        if self.team is not None:
            self.team.follow()
        self.doing("enter", f"Recalling to {rune or 'the default rune'}")
        self.human.wait("use")
        res = self.recall(book, rune, False, "in")
        if not res["ok"]:
            raise Abort(f"recall to {rune or 'the default rune'} failed: {res['failure']} {res.get('error') or ''}")
        log(f"recalled to {rune or 'the default rune'} at {tuple(self.pos(self.link.state())[:2])} "
            f"({res['method']}, {res['elapsed_s']} s)")
        if self.args.enter_via:
            self.walk_in([tuple(t) for t in self.args.enter_via])
        arrival = tuple(self.pos(self.link.state())[:2])
        if self.spot is None:
            self.spot = arrival
            self.fight_spot = self.fight_spot or arrival
            self.recall_spot = self.recall_spot or arrival
            log(f"the hunt: arrival {arrival}, fight spot {self.fight_spot}, recall spot {self.recall_spot}")
        if not self.at_hunt(self.state()):
            raise Abort(f"the recall put us at {arrival}, not near the spot {self.spot}")
        self.rearm(self.state())            # the recall knocked an arcane staff into the pack: before any attack
        if self.team is not None:
            self.team.landed(self.state(), "in")

    def walk_in(self, tiles):
        """After the recall in (a dungeon lets us recall only beside a gold moongate: user 2026-10-13):
        onto each --enter-via teleporter in order, the pets on 'All Follow Me' the whole way, said once
        before the first leg, as the user does (capture 20261013_204518: "All Guard Me" before the recall,
        "All Follow Me" 8 s after landing, four teleporters, the next "All Guard Me" on the fight level;
        live 22:20:56 a guard said at the landing set them fighting there while Logan walked off alone).
        A pet falling behind (WALK_IN_LAG_R, or out of view) stops the walk: 'All Follow Me' again and
        WALK_IN_PETS_S for it to come; something attacking us or our hits falling stops it too, and the
        pets guard until it's over (walk_in_fight). Before each step onto a teleporter, every pet within
        reach of it (they come through with us). The guard comes on the fight level (landed, then care).
        --songs: a song due before each leg (live 20261013_204518: all three went up on the first level
        and on the way in)."""
        self.walk_hits, self.walk_hurt_t = None, -1e9
        if self.team is not None:
            self.team.follow()
        for tile in tiles:
            label = f"to the teleporter at {tile[0]},{tile[1]}"
            self.doing("enter", f"Walking to the teleporter at {tile[0]},{tile[1]}", tile)
            if self.team is not None:
                self.team.song(self.state())
            # the tile the route onto it steps from: a teleporter on stairs is entered from one side only
            # (the Mausoleum's at 5554,701: from 5554,702 northwards)
            path, _ = self.mover.plan(self.link.state(), nav.within(tile, 0), mobiles=False)
            near = tuple(path[-2]) if path and len(path) >= 2 else None
            if near is None:
                raise Abort(f"{label}: no route onto it")
            while why := self.mover.walk_to(lambda: near, 0, label, stop=self.walk_in_stop):
                if why == "under attack":
                    self.walk_in_fight(label)
                else:
                    self.wait_pets(label, None)
            if self.team is not None and not self.team.together(self.link.state(), at=tile[:2]):
                self.wait_pets(label, tile[:2])
            here = tuple(self.pos(self.link.state())[:2])
            self.teleport(nav.direction(here, tile), f"the teleporter at {tile[0]},{tile[1]}")
            if self.team is not None:
                self.team.landed(self.state(), "in")

    def walk_in_stop(self, st) -> str | None:
        """walk_in's stop: something attacking us or our hits falling (a calamity casts from 13 tiles,
        beyond CASTER_REACH: simulator seeds 15 and 16 died to one walking on), else a lagging pet."""
        if self.team is not None and (self.walk_hurt(st) or self.attackers(st)):
            return "under attack"
        return self.pets_lagging(st)

    def walk_hurt(self, st) -> bool:
        """Our hits fell since the last look (walk_in: `walk_hits`, `walk_hurt_t`)."""
        hits = st["world"]["self"].get("hits")
        fell = hits is not None and self.walk_hits is not None and hits < self.walk_hits
        self.walk_hits = hits
        if fell:
            self.walk_hurt_t = time.monotonic()
        return fell

    def pets_lagging(self, st) -> str | None:
        """A pet of the roster out of view or farther than WALK_IN_LAG_R."""
        if self.team is None:
            return None
        pets = {p["serial"]: p for p in self.team.pets(st)}
        for s, name in self.team.roster.items():
            p = pets.get(s)
            if p is None:
                return f"{name} out of view"
            if p["dist"] > WALK_IN_LAG_R:
                return f"{name} at {p['dist']}"
        return None

    def wait_pets(self, label, at):
        """Up to WALK_IN_QUIET_S for every pet within FOLLOW_R of us (of the teleporter `at` when given):
        on follow they catch up by themselves (the user said 'All Follow Me' once for four teleporters);
        then 'All Follow Me' again and WALK_IN_PETS_S more, else Abort: never through a teleporter
        without them."""
        log(f"{label}: waiting for the pets ({self.pets_lagging(self.link.state()) or 'not all beside it'})")
        self.doing("enter", "Waiting for the pets to catch up")
        if self.link.wait(lambda s: self.team.together(s, at=at), WALK_IN_QUIET_S):
            return
        self.team.follow(wait=False)
        if not self.link.wait(lambda s: self.team.together(s, at=at), WALK_IN_PETS_S):
            raise Abort(f"{label}: the pets aren't within {tamer.FOLLOW_R} tiles "
                        f"{'of it' if at else 'of us'} after {WALK_IN_PETS_S:.0f} s; not going on without them")

    def walk_in_fight(self, label):
        """Attacked on the way in: 'All Guard Me' and stand while the pets take it (the team's care:
        potions, vet supplies, stand-off) until nothing attacks us and no hit came for WALK_IN_CALM_S, up to
        WALK_IN_FIGHT_S; then 'All Follow Me' and walk on. On follow the pets would let it hit us all the
        way to the next teleporter."""
        log(f"{label}: attacked on the way in; the pets guard")
        self.doing("enter", "Attacked on the way in: the pets take it")
        self.team.guard_t = -1e9
        self.team.guard()
        end = time.monotonic() + WALK_IN_FIGHT_S
        while time.monotonic() < end:
            st = self.state()
            self.walk_hurt(st)
            if not self.attackers(st) and time.monotonic() - self.walk_hurt_t > WALK_IN_CALM_S:
                break
            if not self.team.care(st):
                time.sleep(0.3)
        self.team.follow()

    def wait_pk(self):
        """After leaving from a hostile player: stay home until --pk-wait has passed."""
        if self.pk_left_t is None:
            return
        end = min(self.pk_left_t + self.args.pk_wait, self.t0 + self.args.timeout)   # never past --timeout
        if time.monotonic() < end:
            log(f"a hostile player at the hunt: waiting {end - time.monotonic():.0f} s more before going back")
        while time.monotonic() < end:
            self.state()
            self.doing("wait", f"Waiting before going back ({end - time.monotonic():.0f} s; a hostile player "
                               f"was at the hunt)")
            time.sleep(min(2.0, max(0.0, end - time.monotonic())))
        self.pk_left_t = None

    # ------------------------------------------------------------ the bank
    def bank(self):
        """At home after a recall: when the pack holds --bank-gold gold or more, find a
        banker (a known "the banker" label, else click the human NPCs near us, like
        errand_bank), walk within BANK_RANGE, say "bank" and drag every gold pile from the
        pack into the bank box right after it opened (no step between: moving closes it in
        RunUO). A `bank` job event with the amount and the gold coins the box holds after."""
        st = self.state()
        gold = sum(it.get("amount") or 1 for _, it in self.pack_items(st, combat.GOLD_GRAPHIC))
        if gold < self.args.bank_gold:
            log(f"{gold} gold in the pack, below --bank-gold {self.args.bank_gold}: not banking")
            return
        self.doing("find_banker", "Looking for a banker")
        serial, label, bpos = find_banker(self.link, self.human, BANKER_SEARCH, BANKER_CLICKS, log)

        def where():
            m = self.link.state()["world"]["mobiles"].get(key_of(serial)) or {}
            return (m["x"], m["y"]) if m.get("x") is not None else tuple(bpos)
        z = (self.link.state()["world"]["mobiles"].get(key_of(serial)) or {}).get("z")
        self.doing("to_bank", f"Going to {label}", where())
        self.mover.walk_to(where, BANK_RANGE, "to the banker", z_ok=same_floor(z) if z is not None else None)
        self.doing("open_bank", "Opening the bank box", where())
        self.human.wait("speak")
        mark = len(self.link.events)
        self.link.act(actions.say_unicode("bank"))
        st = self.link.wait(lambda s: bank_opened(s["world"], self.me(s), self.link.events[mark:]) is not None, 5.0)
        if st is None:
            raise Abort(f"the bank box did not open ({label} at {where()})")
        box = bank_opened(st["world"], self.me(st), self.link.events[mark:])
        stored, piles = 0, self.pack_items(st, combat.GOLD_GRAPHIC)
        if piles:   # the bank gump is open from the speech; the pack and bags may still need opening
            self.open_for(*[(s, False) for s, _ in piles])
        for s, it in piles:
            amount = it.get("amount") or 1
            self.doing("store", f"Banking {amount} gold", where())
            self.human.wait("use")
            self.link.act(actions.lift(s, amount))
            self.human.wait("drag")
            self.link.act(actions.drop(s, combat.DROP_AUTO, combat.DROP_AUTO, 0, 0, box))
            if self.link.wait(lambda s2: s not in {x for x, _ in self.pack_items(s2, combat.GOLD_GRAPHIC)},
                              4.0) is None:
                raise Abort(f"gold pile 0x{s:08X} did not leave the backpack")
            stored += amount
        st = self.state()
        box_gold = sum(it.get("amount") or 1 for it in st["world"]["items"].values()
                       if it.get("graphic") == combat.GOLD_GRAPHIC and it.get("container") is not None
                       and serial_of(it["container"]) == box)
        self.totals["banked"] += stored
        log(f"banked {stored} gold ({self.totals['banked']} this run); the bank box holds {box_gold} gold coins")
        self.memory.job_event("hunt", "bank", {"amount": stored, "piles": len(piles), "box_gold": box_gold,
                                               "banked_run": self.totals["banked"], "visit": self.visit_n},
                              **self._where(st))

    def enter(self):
        ex, ey, ez = self.args.entry
        if self.team is not None:
            self.team.follow()
        self.doing("enter", f"Going to the entrance at {ex},{ey}", (ex, ey))
        self.mover.walk_to(lambda: (ex, ey), 0, "to the entrance", z_ok=lambda z: abs(z - ez) <= GOTO_Z_TOL)
        self.teleport(self.args.entry_dir, "the entrance")
        st = self.state()
        if self.team is not None:
            self.team.landed(st, "in")
        if not self.at_hunt(st):
            raise Abort(f"the entrance put us at {self.pos(st)[:2]}, not near the spot {self.spot}")

    def rest(self) -> bool:
        """Outside: heal with Heal / Greater Heal when one can be cast (healing.choose
        without potions: out of the fight regenerating mana is free, potions cost gold),
        else wait for natural regeneration, to --rest-to hits and --mana-reserve mana.
        --pets: the pets too, to --pet-heal-at with the vet supplies (no heal spell without
        Magery). False when that takes longer than --rest-timeout."""
        end = time.monotonic() + self.args.rest_timeout
        while True:
            st = self.state()
            me = st["world"]["self"]
            need_mana = min(self.args.mana_reserve, me.get("mana_max") or 0)
            pets_ok = self.team is None or self.team.rested(st)
            if self.frac(me) >= self.args.rest_to and (me.get("mana") or 0) >= need_mana and pets_ok:
                log(f"rested: hits {me.get('hits')}/{me.get('hits_max')}, mana {me.get('mana')}")
                return True
            if time.monotonic() > end:
                log(f"rest: still {me.get('hits')}/{me.get('hits_max')} hits, mana {me.get('mana')}"
                    + ("" if pets_ok else ", pets hurt") + f" after {self.args.rest_timeout:.0f} s")
                return False
            if self.team is not None and self.team.vet(st):
                continue
            if self.frac(me) < self.args.rest_to and not (self.team is not None and healing.magery(me) <= 0) \
                    and healing.choose(st["world"], self.me(st), False, self.args.gheal_min_missing,
                                       self.no_reagents).kind == "spell":
                self.heal(st, potions=False)
                self.human.wait("read")
                continue
            self.doing("rest", f"Resting ({me.get('hits')}/{me.get('hits_max')} hits, mana {me.get('mana')})")
            time.sleep(2.0)

    # ------------------------------------------------------------ visits
    def start_visit(self, st):
        self.visit_n += 1
        self.count("visits")
        self.low_posted = False
        self.no_reagents.clear()
        self.attack_scan = len(self.link.events)     # "... is attacking you!" from before this visit is old news
        if self.args.leave_recall is None:
            # the step from the spot in --exit-dir is the exit teleporter: never route over it
            self.mover.teleporter_tiles(st["world"]["self"].get("map")).add(nav.step(self.spot, self.args.exit_dir))
        if self.team is None and (lack := combat.missing_reagents(st["world"], self.me(st), self.spell)):
            log(f"{combat.MAGERY_SPELLS[self.spell - 1]}: no {', '.join(lack)} and no spellstone: melee only")
        self.visit = {"loop": "hunt", "visit": self.visit_n, "t_start": round(time.time(), 1),
                      "spot": list(self.spot), "fight_spot": list(self.fight_spot),
                      **({"style": "pets"} if self.team is not None else
                         {"spell": combat.MAGERY_SPELLS[self.spell - 1]}),
                      "hits_start": st["world"]["self"].get("hits"), "kills": 0, "lost_kills": 0, "gold": 0, "xp": 0,
                      "hits_lost": 0, "casts": 0, "heals": 0, "stationary_clears": 0, "repositions": 0}

    def end_visit(self, why):
        if self.visit is None:
            return
        row = {**self.visit, "t_end": round(time.time(), 1), "ended": why,
               "human_session": dict(self.human.stats)}
        if self.crawl is not None and self.crawl.floor is not None:
            row["crawl"] = self.crawl.summary()
        if self.team is not None:
            row["pets"] = self.team.summary()["pets"]
        self.memory.episode("hunt", row)
        log(f"visit {self.visit_n} done: {row}")
        self.visit = None

    def finished(self) -> bool:
        return bool(self.args.kills and self.totals["kills"] >= self.args.kills)

    def timed_out(self, extra: float = 0.0) -> bool:
        return time.monotonic() - self.t0 > self.args.timeout + extra

    def fight(self, serial):
        """One round against `serial`: attack it (once, and once more when it first
        comes adjacent), then the attack spell while it can be cast with the mana
        reserve kept, else melee. A target pinned beyond reach (neither adjacent nor
        hurt for PIN_S) is dropped and skipped for SKIP_S."""
        if self.team is not None:
            self.team.fight(serial)
            return
        st = self.state()
        mob, why = self.live_target(st, serial)
        if mob is None:
            return
        dist = cheb(self.pos(st), (mob["x"], mob["y"]))
        if self.engaged != serial:
            self.resent_adjacent = dist <= 1
            if not self.attack(serial):
                time.sleep(POLL_S)
            return
        self.engaged_mob = mob
        now = time.monotonic()
        self.progress_t = max(self.progress_t, self.hurt_t.get(serial, 0.0), now if dist <= 1 else 0.0)
        if now - self.progress_t > PIN_S:
            log(f"dropping {mob.get('name') or key_of(serial)} 0x{serial:08X}: {dist} tiles away and not hurt "
                f"for {PIN_S:.0f} s; skipping it for {SKIP_S:.0f} s")
            self.skip[serial] = now + SKIP_S
            self.disengage("dropped")
            return
        if not self.resent_adjacent and dist <= 1:
            self.resent_adjacent = True
            self.human.wait("read")
            self.attack(serial)
            return
        if stationary.steps_left(st["world"]):
            return                        # the penalty is on: unstick walks it off before any attack
        if dist <= SPELL_RANGE and self.spell_block.get(serial, 0) < now and not self.casting_disarms(st) \
                and self.spell_ok(st, self.spell, reserve=self.args.mana_reserve):
            done = self.cast_at(serial)
            if done:
                self.human.wait("read")
            if done or done is None:
                return
        time.sleep(POLL_S)

    def go_back(self, reason) -> bool:
        """After leaving: rest and go back in, or stop (a hostile player without
        --leave-recall, --rest-to 0, or the rest takes too long). Recall mode: wait out
        --pk-wait after a hostile player, recall back in (--enter-recall); False when
        the time is up meanwhile (the run ends at home)."""
        why, severity = reason
        recall = self.args.leave_recall is not None
        if self.team is not None and (down := self.team.down(self.state())):
            self.memory.juncture("hunt", "pet_down", f"Not going back in: {down}", "attention",
                                 {"why": down, "left": why, "pets": self.team.summary()["pets"]})
            raise Abort(f"left the hunt ({why}); not going back in: {down}")
        if recall and (self.finished() or self.timed_out()):
            log("the run is over (--kills / --timeout): not resting or going back in")
            return False
        if (severity == "urgent" and not recall) or self.args.rest_to <= 0 or not self.rest():
            raise Abort(f"left the hunt ({why}); not going back in")
        if not recall:
            self.rearm(self.state())
            self.enter()
        else:
            if self.args.enter_recall is None:
                raise Abort(f"left the hunt by recall ({why}); no --enter-recall to go back in")
            if not (self.finished() or self.timed_out()):
                self.wait_pk()
            if self.finished() or self.timed_out():
                log("the run is over (--kills / --timeout): not going back in")
                return False
            self.enter_by_recall()
        self.start_visit(self.state())
        self.to_spot()
        return True

    def to_spot(self):
        """Walk to the fight spot (the Mover avoids known teleporter tiles), then set
        this visit's leave margin. Crawling: build the floor once (crawl.Crawl.setup);
        the patrol starts from where we stand."""
        if self.crawl is not None:
            try:
                self.crawl.setup(self.state())
            except ValueError as e:
                raise Abort(f"crawl: {e}")
            self.leave_margin(self.state())
            return
        self.doing("to_spot", f"Heading to the hunting spot {self.fight_spot[0]},{self.fight_spot[1]}", self.fight_spot)
        self.mover.walk_to(lambda: self.fight_spot, 0, "to the spot")
        self.leave_margin(self.state())

    def leave_margin(self, st):
        """--leave-at raised by --leave-per-step per step of the planned route from the
        fight spot back to --spot (recall mode: to --recall-spot, where a refused recall
        walks; --leave-gate: to that moongate; Chebyshev distance when no route is planned),
        at most to --heal-at - LEAVE_HEAL_GAP and never below --leave-at. 0 steps when the
        fight spot is that tile. Crawling: the floor's route steps from where we stand, every tick."""
        steps = 0
        out = tuple(self.args.leave_gate) if self.args.leave_gate else \
            self.recall_spot if self.args.leave_recall is not None else self.spot
        if self.crawl is not None:
            steps = self.crawl.route_steps(self.pos(st)) or 0
        elif self.fight_spot != out:
            path, _ = self.mover.plan(st, nav.within(out, 0), mobiles=False)
            steps = len(path) - 1 if path else cheb(self.pos(st), out)
        a = self.args
        raw = a.leave_at + (0.0 if a.heal_in_place else a.leave_per_step) * steps
        cap = max(a.leave_at, a.heal_at - LEAVE_HEAL_GAP)
        self.out_steps, self.leave_at = steps, min(raw, cap)
        if steps and self.crawl is None:
            log(f"fight spot {self.fight_spot}: {steps} steps back to {out}; leaving below "
                f"{self.leave_at:.0%} (--leave-at {a.leave_at:.0%} + {a.leave_per_step:g} x {steps}"
                + (f", capped at --heal-at - {LEAVE_HEAL_GAP:g})" if raw > cap else ")"))
        if self.visit is not None:
            self.visit.update(route_steps=steps, leave_at=round(self.leave_at, 3))

    def unstick(self, st, attackers) -> bool:
        """Outlands' Stationary Penalty (stationary.py; "All damage is reduced to 1"):
        when it is on, walk it off at once, mid-fight too (the steps it asks for + 1,
        out and back to the fight spot); before it comes (301-315 s without a step,
        measured), reposition: 2-4 steps out and back once nothing is on us after a
        draw in [0.8, 1] x --reposition-s, or regardless at --reposition-s. True when
        it walked. Leaving to survive comes before this (hunt order)."""
        kind = self.still.handle(st, self.anchor(st), not attackers and self.engaged is None, self.doing)
        if kind is None:
            return False
        self.count("stationary_clears" if kind == "penalty" else "repositions")
        return True

    def hunt(self):
        """Fight at the fight spot (crawling: patrol the floor) until --kills or --timeout
        (then finish the fights on us, loot and leave), a death or a stop. Recall mode: a
        hostile player seen during any wait or walk (PlayerThreat) leaves at once."""
        self.start_visit(self.state())
        try:
            self.to_spot()
        except PlayerThreat as e:
            self.leave(self.link.last or self.link.state(), e.why, "urgent")
        while True:
            try:
                if self.tick():
                    return
            except PlayerThreat as e:
                self.leave(self.link.last or self.link.state(), e.why, "urgent")

    def tick(self) -> bool:
        """One decision (docs/HUNT_LOOP.md "What it does"); True when the run is over."""
        st = self.state()
        if self.visit is None:                      # left to survive (recall mode)
            return not self.go_back(self.left_why)
        if self.crawl is not None:
            self.leave_margin(st)
        attackers = self.attackers(st)
        winding = self.finished() or self.timed_out()
        reason = self.leave_reason(st)
        if reason is None and winding and ((not attackers and not self.corpses) or self.timed_out(60.0)):
            reason = ("done" if self.finished() else "time is up", "info")
        if reason is not None:
            if self.leave(st, *reason) == "called off":
                return False
            if reason[1] == "info":
                return True
            return not self.go_back(reason)
        me = st["world"]["self"]
        if self.rearm(st):
            return False
        if self.team is not None and not self.team.potion_first(st) and self.team.stand_off(st):
            return False                  # out of an area hit first, unless a potion is ready: it goes at once
        if me.get("hits") is not None and self.frac(me) < self.args.heal_at and self.heal(st):
            self.human.wait("read")
            return False
        if self.team is not None and self.team.care(st):
            return False
        if self.unstick(st, attackers):
            return False
        if self.corpses and not attackers:
            self.loot(self.corpses[0])
            return False
        target = self.pick_target(st, pull=not winding)
        if target is not None:
            if target != self.engaged and self.engaged is not None:
                log(f"switching target to 0x{target:08X}")
                self.disengage("switched")
            self.fight(target)
            return False
        if self.engaged is not None:
            self.disengage()
        if self.war_mode(st, False):
            log("nothing near: war mode off")
            self.human.wait("read")
        if self.team is not None and (why := self.team.hold_reason(st)):
            if self.team.pull_spot(st):
                return False
            self.doing("hold", f"Holding: {why}")
            time.sleep(POLL_S)
            return False
        if self.team is not None and self.crawl is not None and self.team.busy_healing(st):
            self.doing("vet", "Healing up before moving on")
            time.sleep(POLL_S)
            return False
        if self.team is not None and self.crawl is not None \
                and any(time.monotonic() - t < tamer.FIGHT_LINGER_S for _, t in self.lost_alive.values()):
            # live 20261015_115604 12:08:53: the patrol walked on toward the gravebug the moment an apparition
            # blinked out; it came back on the gravebug's tile 0.6 s later and hit Logan
            self.doing("hold", "Waiting for a monster that blinked out to come back")
            time.sleep(POLL_S)
            return False
        if self.crawl is not None:
            self.crawl.idle(st)
            return False
        if cheb(self.pos(st), self.fight_spot) > 0:
            self.mover.walk_to(lambda: self.fight_spot, 0, "back to the spot")
        what = self.args.target_name.strip() or "monsters"
        self.doing("wait", f"Hunting for {what} at {self.fight_spot[0]},{self.fight_spot[1]} "
                           f"({self.totals['kills']} killed)", self.fight_spot)
        time.sleep(POLL_S)
        return False

    def run(self):
        st = self.link.wait(lambda s: s["movement"]["pos"] is not None
                            and s["movement"]["self_serial"] is not None, 5.0)
        if st is None:
            raise Abort("proxy has no player position yet (log in first)")
        self.memory.char_serial = int(st["movement"]["self_serial"])   # tags our rows; scopes our reads
        st = self.state()
        self.weapon = self.worn_weapon(st)
        if self.weapon is not None:
            w = st["world"]["items"].get(key_of(self.weapon)) or {}
            d = self.casting_disarms(st)
            log(f"weapon: {w.get('name') or 'graphic ' + str(w.get('graphic'))} 0x{self.weapon:08X}"
                + (f"; {d}: no attack spell, potions before heal spells inside" if d else ""))
        if self.team is not None:
            self.team.setup(st)
        if self.args.enter_recall is not None and not self.at_hunt(st):
            self.enter_by_recall()
            st = self.state()
        elif self.args.enter and not self.at_hunt(st):
            self.enter()
            st = self.state()
        if self.spot is None:                   # --leave-recall, started at the hunt: here it is
            here = tuple(self.pos(st)[:2])
            self.spot, self.fight_spot = here, self.fight_spot or here
            self.recall_spot = self.recall_spot or here
        if not self.at_hunt(st):
            raise Abort(f"not near the hunting spot {self.spot} (at {self.pos(st)[:2]}); use --enter"
                        + (" or --enter-recall" if self.args.leave_recall is not None else ""))
        if (self.args.crawl or self.args.crawl_loop) and self.crawl is None:
            self.crawl = crawl.Crawl(self, crawl.load_prior(self.memory.con, self.spot, self.args.crawl_depth_risk))
        self.hunt()
        t = self.totals
        log(f"hunt complete: {t['kills']} kill(s) ({t['lost_kills']} more lost to others' loot rights), "
            f"{t['gold']} gold, ~{t['xp']} xp, {t['hits_lost']} hits lost, "
            f"{t['leaves']} leave(s), {t['banked']} gold banked; "
            + ("home" if self.args.leave_recall is not None else "outside"))
        self.doing("done", f"Finished: {t['kills']} kill(s), {t['gold']} gold, ~{t['xp']} xp")


def stop_intent(loop, text):
    """Last words for the visualizer; the proxy may already be gone."""
    try:
        loop.doing("stopped", text[:200])
    except (OSError, ValueError, Abort):
        pass


# Thresholds that differ by fighting style; None on the command line means the style's own.
# Mage (the NPD mongbat runs): as before. Pets (Logan Wolf's Mausoleum trip, 2026-10-12): he drank
# at 58-81 % hits, fell to 35 % once (a bleed) and backed off; the pets engage what comes, so any
# monster is a target and the pull reaches the bard range. And no lingering in a cleared area
# (user 2026-10-13: respawns run on a timer, humans move straight on to the next pack; his crawl
# 20261013_204518 fought 595 of 764 s): the simulated zone crawl spent 30 % of its time on the
# level waiting out --crawl-depleted-s 90 at cleared waypoints. Moving on at once took the crawl deeper and
# into the countess rooms more often, and its deaths from 0.40 to 1.25 a game hour, 46 of 54 on the walk to
# the gate after a leave at 35 %; leaving at 50 % and opening a zone only below 20 hits lost a minute
# brought them to 0.54 with 89,000 gold a game hour against 77,000 before (docs/SIM.md, 98 x 60 min).
# --crawl-dry-s (live 20261015_172342, the user: "wandering aimlessly instead of finding mobs"): the first
# zone, cleared, was patrolled 75 times in 5 min while --crawl-learn-s 600 kept the next one shut.
STYLE_DEFAULTS = {
    "mage": {"heal_at": 0.75, "leave_at": 0.60, "leave_multi_at": 0.80, "pull_range": 3, "target_name": "mongbat",
             "crawl_dwell": 6.0, "crawl_depleted_s": 90.0, "crawl_max_dmg": 30.0, "crawl_dry_s": 0.0,
             "crawl_avoid_types": True, "loot_all": True, "crawl_deep_x": 1.0},
    "pets": {"heal_at": 0.80, "leave_at": 0.50, "leave_multi_at": 0.55, "pull_range": 8, "target_name": "",
             "crawl_dwell": 1.5, "crawl_depleted_s": 5.0, "crawl_max_dmg": 60.0, "crawl_dry_s": 45.0,
             "crawl_avoid_types": False, "loot_all": False, "crawl_deep_x": 2.0},
}


def style_defaults(args):
    """Fill the style-dependent arguments left unset (None) with STYLE_DEFAULTS."""
    for k, v in STYLE_DEFAULTS["pets" if getattr(args, "pets", False) else "mage"].items():
        if getattr(args, k, None) is None:
            setattr(args, k, v)



def arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--spot", type=int, nargs=2, default=None, metavar=("X", "Y"),
                    help="the exit spot: leaving walks here, then steps --exit-dir (default: the NPD exit tile "
                         "5535 529; with --enter-recall the arrival, with only --leave-recall where we start)")
    ap.add_argument("--fight-spot", type=int, nargs=2, default=None, metavar=("X", "Y"),
                    help="the tile to fight on (default: --spot); pull range, corpses and the idle return "
                         "are measured from it")
    ap.add_argument("--crawl", action="store_true",
                    help="patrol the dungeon floor instead of one fight spot (crawl.py; docs/HUNT_LOOP.md 'Crawl')")
    ap.add_argument("--crawl-loop", action="store_true",
                    help="crawl: every zone open; walk the floor's waypoints round in a loop (nearest next by "
                         "route), on to the next once an area is cleared (implies --crawl)")
    ap.add_argument("--crawl-avoid", type=int, nargs=4, action="append", default=None,
                    metavar=("X0", "Y0", "X1", "Y1"),
                    help="crawl: never patrol into this box (repeat); routes keep out of it where they can "
                         "(the Mausoleum's nightstalker room: 5170 690 5188 718, user 2026-10-13)")
    ap.add_argument("--crawl-band", type=int, default=40,
                    help="crawl: route steps from the exit per zone (zones: internal depth bands of the one "
                         "dungeon level entered, numbered from 1)")
    ap.add_argument("--crawl-zones", type=int, default=0,
                    help="crawl: at most this many zones (0: all the floor has)")
    ap.add_argument("--crawl-dwell", type=float, default=None,
                    help="crawl: seconds a waypoint is looked over when nothing shows up (x 0.6-1.4; default 6, --pets 1.5)")
    ap.add_argument("--crawl-depleted-s", type=float, default=None,
                    help="crawl: an area where targets came is depleted after this long without one: move on "
                         "(default 90, --pets 5)")
    ap.add_argument("--crawl-learn-s", type=float, default=600.0,
                    help="crawl: seconds observed in a zone (store history included) before going deeper")
    ap.add_argument("--crawl-deep-x", type=float, default=None,
                    help="crawl: a waypoint's score x this per zone deeper than the first (default 1, --pets 2: "
                         "the user 2026-10-15 'go deeper'; the crawl kept to the near rooms by the gate)")
    ap.add_argument("--crawl-dry-s", type=float, default=None,
                    help="crawl: no target in reach anywhere in the open zones for this long: go deeper without "
                         "waiting out --crawl-learn-s (the risk rule still applies; 0 = off; default 0, --pets 45)")
    ap.add_argument("--crawl-max-dmg", type=float, default=None,
                    help="crawl: the most hits lost per minute a zone may cost (estimated, or predicted for "
                         "the next zone; default 30, --pets 60: user 2026-10-15 'go deeper', healed in place)")
    ap.add_argument("--crawl-depth-risk", type=float, default=1.5,
                    help="crawl: a deeper zone's predicted hits lost per minute = the zone before's x this")
    ap.add_argument("--crawl-max-leaves", type=int, default=2,
                    help="crawl: survival leaves from a zone in one run that close it (come back out)")
    ap.add_argument("--crawl-max-hits", type=float, default=0.5,
                    help="crawl: avoid creature types costing more than this share of our max hits per fight")
    ap.add_argument("--crawl-max-fight-s", type=float, default=180.0,
                    help="crawl: avoid creature types whose kills take longer than this (estimated)")
    ap.add_argument("--exit-dir", type=int, default=4, choices=range(8),
                    help="the step from the spot onto the exit teleporter (default 4 = south)")
    ap.add_argument("--enter", action="store_true",
                    help="start outside: walk to --entry and step --entry-dir to teleport in")
    ap.add_argument("--entry", type=int, nargs=3, default=[1912, 2557, -20], metavar=("X", "Y", "Z"),
                    help="the tile before the entrance teleporter (default: the NPD entrance)")
    ap.add_argument("--entry-dir", type=int, default=0, choices=range(8))
    ap.add_argument("--enter-recall", type=lambda v: int(v, 0), default=None, metavar="BOOK",
                    help="start anywhere: recall with this runebook / rune tome (serial, e.g. 0x57C3DEB6) to "
                         "--enter-rune and hunt at the arrival; also how the runner goes back in")
    ap.add_argument("--enter-rune", default=None, metavar="NAME",
                    help="the rune tome row to recall to (its name, e.g. 'Urukton Bluffs'; default: the "
                         "book's default rune)")
    ap.add_argument("--leave-recall", type=lambda v: int(v, 0), default=None, metavar="BOOK",
                    help="leave by recalling to this book's default rune (home) instead of walking to an exit "
                         "teleporter; a hostile player close, a red in view or 'X is attacking you!' recalls at once")
    ap.add_argument("--recall-spot", type=int, nargs=2, default=None, metavar=("X", "Y"),
                    help="where to walk and recall from when the recall home is refused where we stand "
                         "(dungeons: near a golden gate; default: the arrival / --spot)")
    ap.add_argument("--enter-via", type=int, nargs=2, action="append", default=None, metavar=("X", "Y"),
                    help="after --enter-recall's arrival, step onto these teleporters in order (repeat) to reach "
                         "the hunting level; the last arrival is the hunt (the Mausoleum: 5554 701, 5521 638)")
    ap.add_argument("--leave-gate", type=int, nargs=2, default=None, metavar=("X", "Y"),
                    help="leave by this moongate on the hunting level (dungeons allow no recall except near a "
                         "gold gate, never near a red one: user 2026-10-13), to --gate-dest, then --leave-recall "
                         "home from there")
    ap.add_argument("--gate-dest", default=None, metavar="NAME",
                    help="--leave-gate's destination on its 'Moongate Destinations' menu (e.g. Totem)")
    ap.add_argument("--bank-gold", type=int, default=0, metavar="N",
                    help="after recalling home: with N gold or more in the pack, bank it all (0 = off)")
    ap.add_argument("--pk-wait", type=float, default=600.0,
                    help="recall mode: after leaving from a hostile player, stay home this many seconds "
                         "before recalling back in")
    ap.add_argument("--heal-at", type=float, default=None,
                    help="heal below this share of hits: a heal potion if one can be drunk, else a spell "
                         "(default 0.75; --pets 0.80)")
    ap.add_argument("--gheal-min-missing", type=int, default=None,
                    help="missing hits from which the spell is Greater Heal, not Heal (default: the mana "
                         "break-even for your Magery, healing.gheal_break_even: 19 at Magery 60)")
    ap.add_argument("--leave-at", type=float, default=None,
                    help="leave below this share of hits (default 0.60; --pets 0.50)")
    ap.add_argument("--leave-per-step", type=float, default=0.004,
                    help="--leave-at is raised by this per step of the route from --fight-spot back to "
                         "--spot, at most to --heal-at - 0.05 (the walk out under attack)")
    ap.add_argument("--heal-in-place", action="store_true",
                    help="a long way out (a dungeon's moongate): leave only below --leave-at (no route margin; "
                         "two attackers only below it too) and for a pet only when the vet supplies are out; "
                         "until then heal where we stand (user 2026-10-13)")
    ap.add_argument("--leave-multi-at", type=float, default=None,
                    help="leave below this share of hits when two or more mobs are attacking (default 0.80; "
                         "--pets 0.55)")
    ap.add_argument("--mana-reserve", type=int, default=22,
                    help="mana kept for heals: the attack spell only above this plus its cost")
    ap.add_argument("--spell", default="lightning", help="attack spell (Magery name or 1-64)")
    ap.add_argument("--target-name", default=None,
                    help="words the monster's name must contain (default 'mongbat'; --pets: any monster)")
    ap.add_argument("--pull-range", type=int, default=None,
                    help=f"engage mobs within this many tiles of the spot (at most {SPELL_RANGE}; default 3, "
                         f"--pets 8)")
    ap.add_argument("--pets", action="store_true",
                    help="fight with the pet team, never with war mode or spells (tamer.py; docs/HUNT_LOOP.md 'Pets')")
    ap.add_argument("--pet", action="append", default=None, metavar="NAME|SERIAL",
                    help="--pets: one of our pets (repeat for each); default: the ones remembered for this "
                         "character, else the tamed pets next to us")
    ap.add_argument("--pet-heal-at", type=float, default=0.95,
                    help="--pets: veterinary supplies when a pet in range is below this share of hits")
    ap.add_argument("--standoff-vet-at", type=float, default=0.7,
                    help="--pets: a pet fighting a monster with an area Charge (nightstalker) gets the vet "
                         "supplies only below this share of hits while --vet-range is under 3: we stand 3 tiles "
                         "off that fight and step in to heal [INFERENCE: the threshold]")
    ap.add_argument("--pet-leave-at", type=float, default=0.20,
                    help="--pets: leave when a pet is below this share of hits")
    ap.add_argument("--vet-range", type=int, default=tamer.VET_RANGE,
                    help="--pets: tiles the veterinary supplies reach (2; the Healer's Codex 'Without Borders' "
                         "perk adds 1/2/3)")
    ap.add_argument("--retreat-at", type=float, default=0.55,
                    help="--pets: below this share of hits with a mob next to us, back off and let the pets hold it")
    ap.add_argument("--discord", action=argparse.BooleanOptionalAction, default=True,
                    help="--pets: Discordance on the target the pets fight")
    ap.add_argument("--peace", action=argparse.BooleanOptionalAction, default=True,
                    help="--pets: Peacemaking on the target the pets fight (after the discord)")
    ap.add_argument("--provoke", action=argparse.BooleanOptionalAction, default=True,
                    help="--pets: Provocation of another mob in the fight")
    ap.add_argument("--pull-budget", type=float, default=3.0,
                    help="--pets: while the pets fight monsters worth E, don't advance where more would wake "
                         "(W) when E + W is above this; weights from the wiki difficulty (aggro.weight: a bat "
                         "0.4, a nightstalker 1.8); 0 = off (user 2026-10-13: 'weighted by danger')")
    ap.add_argument("--never-aggro", action="append", default=None, metavar="TYPE",
                    help="--pets: a creature type (no article) never to wake while alone: its sight is no-go and "
                         "one that comes for us is run from (repeat; default aggro.NEVER_AGGRO: the elder vampire "
                         "and the elder vampire countess, user 2026-10-15)")
    ap.add_argument("--crawl-avoid-types", action=argparse.BooleanOptionalAction, default=None,
                    help="crawl: avoid creature types the store's fights say cost too much (--crawl-max-hits, "
                         "--crawl-max-fight-s) or were fled from (default on, --pets off: user 2026-10-15)")
    ap.add_argument("--rope", action=argparse.BooleanOptionalAction, default=True,
                    help="fleeing a hostile player by --leave-gate: 'All Follow Me' at once and an Adventurer's "
                         "Rope jump toward the gate whenever one is ready, as Logan fled reds")
    ap.add_argument("--hold-first-sight", action=argparse.BooleanOptionalAction, default=True,
                    help="--pets, nothing fighting: stop where an idle monster first sees us within its aggro "
                         "range and let it come (up to 10 s each), as Logan at the nightstalker room's edge")
    ap.add_argument("--reposition", action=argparse.BooleanOptionalAction, default=True,
                    help="--pets: in a fight, step to a nearby tile that keeps the pets in vet range with fewer "
                         "idle monsters and casters able to see us (aggro.View.stand_score)")
    ap.add_argument("--songs", action=argparse.BooleanOptionalAction, default=True,
                    help="--pets: keep the three barding songs up (a bard skill at the ground: less damage taken, "
                         "more healing, more damage for 15 min; user 2026-10-13)")
    ap.add_argument("--kills", type=int, default=0, help="stop after this many kills (0 = until --timeout)")
    ap.add_argument("--timeout", type=float, default=3600.0, help="then finish the fight and leave")
    ap.add_argument("--rest-to", type=float, default=0.95,
                    help="after leaving, rest to this share of hits and go back in (0 = stop after leaving)")
    ap.add_argument("--rest-timeout", type=float, default=900.0)
    ap.add_argument("--reposition-s", type=float, default=stationary.REPOSITION_S,
                    help="no step for this long: walk 2-4 steps out and back before the Stationary Penalty "
                         "(applied after 300 s without a step); with nothing on us from 0.8-1x of it")
    ap.add_argument("--loot", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--loot-max", type=int, default=25, help="items taken per corpse")
    ap.add_argument("--loot-all", action=argparse.BooleanOptionalAction, default=None,
                    help="take every item (default on; --pets off: all but reagents, spell scrolls and worn items "
                         "other than jewellery, combat.worth_looting; user 2026-10-15)")
    ap.add_argument("--loot-keep", action="append", default=None, metavar="WORD",
                    help="without --loot-all: also take items whose name has this word, whatever they are (repeat)")
    ap.add_argument("--human", choices=sorted(PROFILES), default="normal",
                    help="human-texture profile (humanize.py); 'off' for deterministic tests")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--human-fast", type=float, default=1.0,
                    help="scale human delays (offline tests of the normal profile only)")
    ap.add_argument("--no-map", action="store_true",
                    help="plan on walk memory only (offline tests against simulated worlds)")
    ap.add_argument("--max-blocked", type=int, default=20)
    ap.add_argument("--control-port", type=int, default=25941)
    ap.add_argument("--state-port", type=int, default=25942)
    ap.add_argument("--char", default=None,
                    help="character selector (name or 0xSERIAL); required by the proxy when several "
                         "characters are logged in")
    ap.add_argument("--memory", default=DEFAULT_DB,
                    help="harness memory (SQLite, docs/MEMORY.md): walk memory, junctures, episodes")
    return ap


def main():
    ap = arg_parser()
    args = ap.parse_args()
    if args.crawl_loop:
        args.crawl = True
    if args.crawl and args.fight_spot:
        ap.error("--crawl patrols the floor: no --fight-spot with it")
    if args.enter_recall is not None and args.leave_recall is None:
        ap.error("--enter-recall needs --leave-recall (the way out)")
    if args.enter and args.enter_recall is not None:
        ap.error("--enter walks to an entrance; --enter-recall recalls in: one of them")
    if args.bank_gold and args.leave_recall is None:
        ap.error("--bank-gold banks at home after a recall: it needs --leave-recall")
    if args.enter_rune and args.enter_recall is None:
        ap.error("--enter-rune names a rune of --enter-recall's book")
    if args.pet and not args.pets:
        ap.error("--pet names a pet of the team: it needs --pets")
    if args.leave_gate and (args.leave_recall is None or not args.gate_dest):
        ap.error("--leave-gate travels to --gate-dest, then recalls home with --leave-recall: give both")
    if args.enter_via and args.enter_recall is None:
        ap.error("--enter-via walks the teleporters after --enter-recall's arrival")

    memory = Memory(args.memory)
    link = Link(args.control_port, args.state_port, char=args.char)
    loop = HuntLoop(link, memory, args)
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
        loop.end_visit("stopped")
        memory.close()
    sys.exit(code)


if __name__ == "__main__":
    main()

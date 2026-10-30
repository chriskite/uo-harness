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

Guards: overall timeout, movement stall, the agent gate (a pause ends the run: GatePaused; a break is waited out),
server restriction text, death (`death` juncture, stop; no corpse runs). No speech
hold (user 2026-10-15: "GMs don't check attendance for dungeon crawling and stopping
because a player speaks will definitely get us killed"). Junctures: `threat` when leaving,
`low_supplies` when neither a potion nor a castable heal spell (mana, reagents) is
there, `death`. Job events and one episode row per visit (kills, gold, xp, hits
lost) go to the memory store. An error that ends the run while we stand alive inside a dungeon that allows no recall
escapes home first (on_error, escape_home: the first level's gold moongate, the hunt level's --leave-gate, a middle
level's remaining --enter-via teleporters and the gate; an `escape` juncture), then exits 1; --go-home takes the same
way out and nothing else (docs/HUNT_LOOP.md "An error inside the dungeon escapes home").

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

Soul jars (--soul-jars, default on; soul_jar.py; docs/HUNT_LOOP.md "Soul jars"; user request 2026-10-17): with
an empty soul jar in the pack and an eerie corpse of the Field of Souls event in view, nothing attacking us, walk
within 2 tiles and double-click the jar (its bags opened first); mid-fight only a corpse that needs no step. With
pets the walk waits and stops as a patrol does (the pets fighting or lagging, the hold and the pull budget). One
try per corpse; a `soul_jar` job event.

Run:  python harness/loop_hunt.py [--kills 5] [--enter] [--fight-spot X Y | --crawl] [--spell lightning]
      python harness/loop_hunt.py --enter-recall BOOK --enter-rune NAME --leave-recall BOOK --bank-gold 1
      python harness/loop_hunt.py --pets --leave-recall BOOK --crawl      # standing in the hunting area
"""
import argparse
import contextlib
import math
import os
import re
import signal
import sys
import threading
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import actions  # noqa: E402
import aggro  # noqa: E402
import aspects  # noqa: E402
import combat  # noqa: E402
import crawl  # noqa: E402
import dropoff  # noqa: E402
import repair  # noqa: E402
import home as home_mod  # noqa: E402
import hunt_return  # noqa: E402
import escape as escape_mod  # noqa: E402
import healing  # noqa: E402
import localmap  # noqa: E402
import los  # noqa: E402
import nav  # noqa: E402
import pathfind  # noqa: E402
import moongate  # noqa: E402
import oxygen  # noqa: E402
import threats  # noqa: E402
import soul_jar  # noqa: E402
import stationary  # noqa: E402
import tamer  # noqa: E402
import task_wrap  # noqa: E402
from agent_link import (Abort, GatePaused, Link, Mover, WalkFailed, bank_opened, cheb,  # noqa: E402
                        containers_to_open, find_banker, log, same_floor, serial_of)
from errand_bank import gating  # noqa: E402
from humanize import PROFILES, Human  # noqa: E402
from memory import DEFAULT_DB, Memory  # noqa: E402

ROPE_GRAPHIC = 0x14FA         # an adventurer's rope (a stack of 15 in Logan's pack, 20261014_173858)
ROPE_R = 9                    # live jumps 7, 9, 10 tiles; an 11-tile target was refused: one short
PLANNED = ("info", "return")   # leaves with no threat behind them: the planned end, taking the loot home
FINISH_CHECK_S = 2.0          # how often the run looks for a `ctl stop --after-trip` request (a meta read)
STOP_POLL_S = 0.5             # how often watch_stop looks for a `ctl stop` (its own store connection)
ROPE_COOLDOWN_S = 5.3         # knowledge #13325: 5 s from the use (unless PvP flagged), a margin
ROPE_RETRY_S = 2.0            # after a try that found no landing or got no cursor
ROPE_WAIT_S = 1.5             # live: the cursor 0.1 s after "[Rope", the jump ~1 s after the answer
ROPE_REFUSED = (501942, 500237, 500446)   # "That location is blocked." (live 13:40:52.9), "Target cannot be seen.", too far
ROPE_CALM_S = 3.0             # a flight's rope only when no player has hit or attacked us this long (rope_worth)
# A rope used while a player is attacking us starts a 2-minute cooldown (live: "You must wait another 2m before you
# may use this again while in combat." 0.84 s after a jump 2.4 s after "Chaos Agent is attacking you!", 2026-10-19
# 19:59:10; "... another 1m 50s ..." 9.8 s after a jump 6.2 s after "Druidx is attacking you!", 2026-10-22 20:00:58.9;
# a jump 0.3 s before the attack line started none). The refusal names what is left (ROPE_COMBAT_RE)
ROPE_COMBAT_COOLDOWN_S = 120.0
ROPE_COMBAT_RE = re.compile(r"You must wait another (?:(\d+)m)?\s*(?:(\d+)s)? before you may use this again "
                            r"while in combat\.")
ROPE_COMBAT_AFTER_S = 20.0    # ...a player's "... is attacking you!" this recent counts as that combat [INFERENCE]
HAMSTRING_ICON = 53           # "Can Only Walk and Cannot Regain Stamina", 3 s from a player's hit, stamina 0: the rope
#                               went out and jumped during one (live 2026-10-19 19:59:09.2, docs/NOTES.md "Hinder")
# A red who hurts from afar (user 2026-10-22: "expand caster to include ranged weapons like the bow, since being near
# them for a second while roping is no worse than being a few tiles away"): harmful spell words from him this recent
# (live 22:03 Flat Lander "Corp Por" x3 and "Por Ort Grav", nothing in his hands), or a bow or crossbow in his hands
# (Druidx, 20:00:56 and 22:03:02: bow animation 18 and arrows 0x0F42 at Logan), or nothing in them
RANGED_WORDS_S = 10.0
ARCHERY_GRAPHICS = frozenset({0x13B1, 0x13B2, 0x0F4F, 0x0F50, 0x13FC, 0x13FD, 0x26C2, 0x26CC, 0x26C3, 0x26CD,
                              0x27A5, 0x27F0, 0x2D1E, 0x2D2A, 0x2D1F, 0x2D2B})   # bows, crossbows (tiledata)
GATE_LEGS_MAX = 30           # walk legs of a planned walk out to the leave gate (a fight or a pet wait between legs)
SWING_RECENT_S = 10.0         # a mob whose last swing at us is this recent is attacking us
CASTER_REACH = 10            # a caster in war mode this close while we take damage is attacking us (SPELL_RANGE; 12, as
#                              the blightmare cast live 2026-10-19 19:54:43, cost the sim 12 more pet-death runs in 144 by
#                              sending the pets at casters farther off: the reposition reads casters to 12, tamer.CASTER_R)
RANGED_REACH = 13            # ...a ranged one (aggro.ranged): the purger's barrage reaches 12, a calamity cast from 13
PIN_S = 15.0                  # an engaged target neither adjacent nor hurt this long is dropped...
SKIP_S = 60.0                 # ...and not picked again for this long (unless it attacks us)
LOOSE_R = 1                  # a monster this close attacking us that the pets aren't fighting becomes the target (pick_target)
LOST_ALIVE_S = 30.0           # an engaged target that left the view alive (0x1D, no death): its death this soon counts
GATE_POTION_WAIT_S = 1.5      # hurt beside the leave gate: a heal potion due this soon is drunk before the click
GATE_CLICK_TRIES = 3          # the gate's double-click again after 500119 (an item use just before it)...
GATE_CLICK_WAIT_S = 0.6       # ...as read this long after each (the refusal came 0.07 s after the click live)
DRINK_TRIES = 2               # a heal potion's double-click again after 500119 (live 2026-10-29 12:36:47.56: 0.26 s after the
#                              vet supplies' click, refused; counted as drunk, no potion again at 56-23 hits, dead at 55.1)
GATE_THROUGH_R = 30          # a jump from beside the leave gate to this far from it: through the gate (live 20261017 16:38:56)
GATE_DASH_R = 8              # attacked on a planned walk out this close to the leave gate: through it, no stand (live 19:04)
FLOOR_DETOUR = 4              # a target in reach: a walk to beside it at most this many steps longer than the beeline...
FLOOR_Z_TOL = 10              # ...ending within this of its surface z (live: a vampire on a ledge 20 above, 11 steps round)
LOOT_WEIGHT_MARGIN = 10       # stones kept free below the weight limit: overloaded, we can't run (live 475/450), and
#                              at it plus one we can't move at all ("You are carrying too much weight and are unable to
#                              move (462/450)", live 2026-10-28 20:09-20:11)
HEAL_IN_PLACE_FLOOR = 0.15    # --heal-in-place: below this share the hits leave fires even with a heal coming
HEAL_SOON_S = 3.0             # --heal-in-place: a heal potion ready this soon is a heal coming
DAMAGE_TEXT = re.compile(r"^-\d+$")   # Outlands' overhead damage number ("-25"), no 0x0B live
CAST_CURSOR_WAIT_S = 4.0      # a spell's cursor comes after its cast delay
SPELL_RANGE = 10              # [INFERENCE] RunUO spell range (12 pre-ML, 10 ML)
SPELL_BLOCK_S = 10.0          # after "too far away" / "cannot be seen": melee only for this long
# A flight from hostile players (leave_by_gate's flee_out; live 2026-10-21 10:35:05-13, hunt-20261021-102636-fdda: two
# reds came down the corridor (5243, 708) -> south, the only way from Maus 3's east wing to the leave gate; the rope
# jumped Logan 9 tiles north onto their tile column, (5244, 716) beside Ticket Tongje, and the walk went on north past
# them: dead 8 s after the leave at (5244, 710))
RED_RECENT_S = 20.0           # a hostile player out of view still counts where last seen this long (hostile_spots)
RED_ZONE_R = 6                # ...the Mover's danger zone round each: the flight's routes bend round them
RED_PASS_R = SPELL_RANGE      # a route to the gate that comes this close to one, nearer than we stand, passes it...
RED_RUN_TPS = 5               # ...and as many more tiles a second it has been out of view (running: a step every 0.2 s)
FLEE_FAR_R = 22               # a flee tile this far from every hostile player is far enough (past the 18-tile view)
FLEE_SEARCH_R = 24            # flee tiles: those the walk rules reach within this square (localmap.steps)...
FLEE_DETOUR = 2               # ...in at most this many times their distance in steps (not round a wall to another room)
FLEE_GAIN = 2                 # a flee leg only toward a tile at least this much farther from the nearest hostile player
FLEE_LEG_STEPS = 6            # steps of a flight's leg before the way to the gate is looked at again
FLEE_MAX_S = 60.0             # flee legs this long at most; then to the gate past them, round them as the zones allow
FLEE_HOLD_S = 0.5             # far enough and the way still passes them: a look again after this
FLEE_LEGS_MAX = 200           # legs, ropes and looks of a flight at most (flee_out)
FLEE_NEAR_FAILS = 6           # failed walks toward the gate a flight tries again from (refused steps expire) before it ends
NO_LOS_CLILOCS = (500446, 500237)   # "That is too far away." / "Target cannot be seen."
CORPSE_WAIT_S = 4.0           # a dead mob's corpse item shows up within this
LOOT_DEFER_S = 1.0            # a corpse in an area hitter's reach (tamer.Team.loot_tile): looked at again this soon
CONTAINER_WAIT_S = 3.0
# Kills whose corpse never holds anything: never walked to or opened. The pincer crab is brooded by a dungeon-nest crab
# mid-fight (docs/SIM.md "Tidal Tomb"); user 2026-10-28: "its corpse cannot be looted, but the runner keeps stopping for
# a while trying to loot them". All 245 opened through 2026-10-28 were empty (0x3C of 0 items, then the full
# CONTAINER_WAIT_S wait: ~4-5 s a corpse, live 22:08:47-22:08:56)
EMPTY_CORPSES = frozenset({"pincer crab"})
LOOT_DETOUR_STEPS = 8         # a loot walk round an area hit's reach: at most twice the distance plus this many steps
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
WALK_IN_ARRIVED_R = 3         # --enter-via: this close to a teleporter's known arrival, we are through it (walk_in_through)
WALK_IN_QUIET_S = 5.0         # --enter-via: first wait this long for a lagging pet without a word (it is on follow)
WALK_IN_FIGHT_S = 90.0        # --enter-via: attacked on the way, stand this long at most while the guarding pets take it
WALK_IN_CALM_S = 4.0          # ...until nothing attacks us and no hit has come for this long
COMING_R = 10                 # a planned walk out stops (and its stand goes on) for a monster in war mode this close coming
#                               at us (HuntLoop.coming): tamer.HOLD_R, the hunt's "letting it come" (live 2026-10-28
#                               22:53:04.5: the lowtide scavenger went to war mode 10 tiles off; the walk met it 3 steps on)
COMING_S = 5.0                # ...while it came nearer within this long (tamer.HOLD_S: one that stops coming is passed over)
COMMAND_R_GO = 8              # wait_pets/fetch_pet: a pet out of a command's reach is walked to this close first
TELE_DASH_R = 15             # --enter-via: attacked this many route steps or fewer from the teleporter walked to: on and
#                               through it, no stand (live 2026-10-20 22:57:15: attacked again 13 steps from 5522,637, the
#                               route's 12 to the tile before it and the step on; the stand there was killing Logan until
#                               the user walked him through. 13 plus a 2-step margin, ~3 s running against a stand of up
#                               to WALK_IN_FIGHT_S; GATE_DASH_R's rule for the leave gate)
WALK_FOUGHT_S = 1200.0        # --fight-via: a fought leg not at its teleporter after this long aborts (the escape)
# Tidal Tomb's oxygen (oxygen.py, docs/HUNT_LOOP.md "Oxygen"; user 2026-10-28: "I would prefer it just fight normally to
# the easiest oxygen refill to get to"): from OXY_MIN_S under water, a leg to the easiest refill in our underwater box
# once our time under reaches the deprivation (732 s) - OXY_MARGIN_S - the leg's estimate: its route steps x OXY_STEP_S.
# The leg is a fought one (walk_in_fought), never a leave's walk: the walk across level 2 killed every simulated level 3
# run before --fight-via (2026-10-26). "Easiest": route steps + OXY_MOB_STEPS for each monster in view within OXY_MOB_R
# of the route. A refill whose leg can't end before the deprivation even from OXY_MIN_S is out of reach: no leg to it
# (simulator 2026-10-28, level 2's crawl from its arrival: legs of ~160 steps across level 2 to the west strip took
# ~7 min and came too late to spare the deprivation). Level 2's west strip is no refill at all (its `skip` in the
# scenario: runs with a death 12 -> 19 of 48 with it); its arrival stairs are (2026-10-29: deprived runs 36 -> 4 of 48,
# deaths 14 -> 11; docs/SIM.md "Oxygen: the refill legs").
OXY_MIN_S = 300.0             # no refill leg before this long under water
OXY_MARGIN_S = 120.0          # the leg's estimate is to end this long before the deprivation
OXY_STEP_S = 3.0              # seconds a route step of a fought leg (simulator 2026-10-28, 119 legs: median 2.2, 75th 3.0)
OXY_MOB_STEPS = 25           # a monster in view near the route costs as many route steps
OXY_MOB_R = 6                 # ... within this many tiles of a tile of it
OXY_PLAN_S = 15.0             # the refills' routes are planned again this often (a map route each)
OXY_RETRY_S = 120.0           # a leg that failed (walk_in_fought's Abort) is tried again after this long
OXY_DRY_S = 2.0               # on dry ground after the hop up: this long, then back down (live 8 s up and down)
OXY_DOLPHIN_WAIT_S = 12.0     # by a dolphin: its refill line within this (every ~5.25 s), else one step closer, then give up
GOLD_GATE_STEPS = 120        # --enter-via: a hostile player on the walk in: flee to a gold moongate this many route steps
#                               off at most and recall home (walk_in_threat; user 2026-10-22 after Botan's kill: "Flee to a
#                               gold gate, recall home"); none that near (past the first teleporter: gold gates are on a
#                               dungeon's first level only): on through the teleporters at flight pace, then the leave gate
# An error that would end the run with us alive inside a dungeon that allows no recall (on_error; user 2026-10-24, after
# hunt-20261024-000844-30b5 aborted on Maus 2 at 00:40:32 and a calamity killed Logan standing there at 01:10:48: "Overseers
# should escape from the dungeon and return home when there is an erroneous stoppage like that"): the way out first
# (escape_home). A way that fails (a walk's no route, the gate's menu, a recall) is looked for again from where it left
# us this often, ESCAPE_RETRY_S apart: a mobile or door that cut the route has moved by then, a level further on has its
# own way
ESCAPE_TRIES = 3
ESCAPE_RETRY_S = 5.0
PET_BUSY_R = 2                # a pet in war mode this close to a monster in war mode is fighting it (patrol_hold)
JAR_DEFER_S = tamer.AT_ONCE_S  # --pets: an eerie corpse whose walk stopped on the pull budget waits this long (past the
#                               "no other way on" memory, so the retry stops at the same place again); a hold ("letting
#                               it come") only until our next kill, and one within soul_jar.FILL_WALK_R not at all
WALK_FAIL_MAX = 3             # failed walks inside the hunt carried on from (walk_failed) before the leave home...
WALK_FAIL_WINDOW_S = 300.0    # ...within this long
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
# The run counters each intent carries as `tally` (doing): the ones the viz's Hunt job pane shows. Not all of
# `totals`: tamer.Team counts open-ended names into it (bard_*, <effect>_ok/_fail, song, vet, ...), and the proxy
# drops a tally of more than proxy.INTENT_WOODS_MAX (32) entries whole (live 2026-10-25: the pane lost its rates
# and "This run" when a Mausoleum pet run reached 33 counters)
INTENT_TALLY = ("kills", "lost_kills", "gold", "banked", "xp", "hits_lost", "heals", "potions", "visits", "leaves")


class PlayerThreat(Exception):
    """Recall mode: a hostile player showed up during a wait or a walk; the hunt loop
    leaves by recall at once (HuntLoop.look)."""

    def __init__(self, why: str):
        super().__init__(why)
        self.why = why


class WalkInFled(Exception):
    """A hostile player on the walk in sent us home by a gold moongate's recall (walk_in_leave); enter_by_recall
    goes back in once ready_to_go_back allows."""

    def __init__(self, why: str):
        super().__init__(why)
        self.why = why


def key_of(serial: int) -> str:
    return f"0x{serial:08X}"


def weight_max(me: dict):
    """The world model's self `stats.weight_max` (the 0x11 status), None until it came. Not a top-level key: the loot
    guards read `me["weight_max"]` until 2026-10-28 and never fired (Logan looted to 462/450 on a leave, stuck)."""
    return (me.get("stats") or {}).get("weight_max")


class HuntLoop:
    def __init__(self, link: Link, memory: Memory, args):
        style_defaults(args)
        self.link = link
        self.memory = memory
        self.args = args
        self.t0 = time.monotonic()
        self.started = round(time.time(), 1)     # the run's start (epoch s), for the viz's Hunt job pane
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
        self.pk_flight = False                   # walking out from a hostile player: no bard skill on the way
        self.walk_out_fought = False             # a planned walk to --leave-gate: it stops for what comes (walk_in_fight)
        self.walk_fighting = False               # in walk_in_fight: its own order, no drink_on_the_run
        self.coming_seen = {}                    # serial -> (monotonic time it was last nearer, its least distance)
        self.walking_in = False                  # on the walk in (walk_in): the hostile-player checks are on (look)
        self.left_level = False                  # this leave went up through --leave-via teleporters (leave_up)
        self.walk_in_red = None                  # why a hostile player on the walk in sent us on to the leave gate
        self.escaping = False                    # the escape home after an error or --go-home: no overall timeout
        self.fought_end = None                   # in a --fight-via leg (walk_in_fought) until then: its own time limit
        self.fought_s = 0.0                      # time spent in fought legs: not counted by the overall timeout's guard
        ox = None if args.no_oxygen else oxygen.load()
        self.oxy = oxygen.Clock(ox) if ox else None   # Tidal Tomb's oxygen clock (oxygen.py; --no-oxygen: none)
        self.oxy_site = None                     # the refill the last plan chose (oxygen_site), and when
        self.oxy_plan_t = -1e9
        self.oxy_retry_t = -1e9                  # no refill leg before this (a failed one)
        self.oxy_why = None                      # why the last refill leg stopped short (oxygen_bail)
        self.escape_tried = False                # escape_home ran: an error after it ends the run where it stands
        self.halt = None                         # why an error ends the run without the escape (a death, a restriction)
        self.rope_next = 0.0                     # --rope: the next rope jump not before
        self.rope_refused = set()                # --rope: (x, y) landings the server refused this run
        self.hostile_seen = {}                   # serial -> ((x, y), monotonic time, name) of hostile players (hostile_spots)
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
        self.lift_ok_t = -1e9        # monotonic time of our last lift the server took (loot: combat.LIFT_GAP_S)
        self.jar_tried = set()       # eerie corpse serials a soul jar was tried at (fill_soul_jar): never again
        self.jar_later = {}          # eerie corpse serial -> (monotonic time before which its walk isn't tried, why)
        self.walk_fails = []         # monotonic times of the failed walks the hunt carried on from (walk_failed)
        self.walk_trouble = None     # why the hunt goes home: walks kept failing (walk_failed)
        self.engaged = None          # serial we last sent 0x05 for
        self.engaged_mob = None      # its latest world dict
        self.resent_adjacent = False
        self.spell_block = {}        # serial -> monotonic time until spells at it are skipped
        self.no_reagents = set()     # spell ids the server refused for reagents this visit (502630)
        self.skip = {}               # serial -> monotonic time until pick_target passes it over (pinned)
        self.ally_fought = {}        # serial -> the ally seen fighting it (ally_seen): not our target, its corpse not ours
        self.floor_seen = {}         # serial -> (its tile, our tile, on our floor?) (on_our_floor's cache)
        self.progress_t = 0.0        # monotonic: the engaged target was last adjacent or hurt
        self.hurt_t = {}             # serial -> monotonic time of its latest damage (event or "-N")
        if args.pull_range > SPELL_RANGE:
            log(f"--pull-range {args.pull_range} is beyond spell range: using {SPELL_RANGE}")
            args.pull_range = SPELL_RANGE
        self.prev_hits = None
        self.prices = memory.prices()   # loot:<name> rows: the carried value (hunt_return)
        self.return_model = None        # hunt_return.model for this visit (start_visit)
        self.return_floor = 0           # gp carried when the visit started (what a drop-off didn't take: return_reason)
        self.in_t = None                # monotonic start of the recall in (enter_by_recall): the next visit's in_s
        self.low_posted = False
        self.potions = healing.PotionClock()
        self.refresh_t = -1e9        # monotonic time of our last stamina potion (refresh_potion)
        self.cure_t = -1e9           # ... of our last cure potion (cure)
        self.mover.refresh = self.refresh_potion
        self.mover.unload = self.drop_gold
        self.mover.no_idle = self.no_idle
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
        self.finish_requested = False  # ctl stop --after-trip asked this run to end at home (finished)
        self._finish_checked = 0.0     # monotonic: the last look at that request
        self.rearm_failed = None     # why the last re-equip couldn't be planned (logged once)
        self.fight_rec = None        # the engagement in progress: a `fight` job event when it ends
        self.patrolling = False      # a crawl patrol walk is under way (check_guards may stop it)
        self.crawl = None
        self.team = tamer.Team(self) if args.pets else None   # --pets: the pet team fights (tamer.py)

    # ------------------------------------------------------------ reporting
    def doing(self, kind: str, text: str, target=None, serial=None):
        """Tell the visualizer what the agent is trying to do (proxy-side only), with the visit
        number as the trip, the run's headline totals so far (`tally`, INTENT_TALLY) and when the run began."""
        self.link.intent(text, kind, target, loop="hunt",
                         target_serial=key_of(serial) if serial is not None else None,
                         trip=self.visit_n or None,
                         tally={k: int(self.totals.get(k, 0)) for k in INTENT_TALLY},
                         started=self.started)

    def _where(self, st):
        pos = st["movement"]["pos"] or [None, None]
        return {"facet": st["world"]["self"].get("map"), "x": pos[0], "y": pos[1]}

    def count(self, what, n=1):
        self.totals[what] = self.totals.get(what, 0) + n
        if self.visit is not None:
            self.visit[what] = self.visit.get(what, 0) + n

    # ------------------------------------------------------------ guards
    def check_guards(self, st: dict) -> bool:
        """The checks every state read and every step of a walk runs (Mover guard). True when one acted (a potion, the
        vet supplies, an area peace on the way out: seconds may have gone by, `st` is old)."""
        if self.args.timeout > 0 and not self.escaping and self.fought_end is None \
                and time.monotonic() - self.t0 - self.fought_s > self.args.timeout + HARD_TIMEOUT_GRACE_S:
            raise Abort(f"overall timeout ({self.args.timeout:.0f}s + {HARD_TIMEOUT_GRACE_S:.0f}s to leave)")
        mv = st["movement"]
        if mv["stalled"]:
            raise Abort(f"movement stalled ({mv['rejects_in_row']} walks rejected in a row)")
        if threats.is_dead(st) or st["world"]["self"].get("dead"):
            self.died(st, "ghost body")
        self.check_restriction()
        self.track(st)
        if self.team is not None:
            self.team.spray_watch(st)     # a wind-up mid-walk for its stop (Team.spray_stop); the cones for routes
        self.look(st)
        return self.drink_on_the_run(st)

    def drink_on_the_run(self, st) -> bool:
        """On the walk out to --leave-gate, a red's flight included (Mover guard, between steps): a heal potion
        when hurt below --heal-at and one is ready (user 2026-10-13: banshees catch real players on the way to the
        gate too; 'I just run and heal and hope for the best, with pets guarding'), and (--pets) the vet supplies on
        us when no channel runs (user 2026-10-15: "It needs to heal on the run when fleeing a red"; 2026-10-17, after
        a flight from an elder vampire countess took Logan from 87 to 11 diseased with no heal: "We absolutely must
        vet while fleeing": a channel whenever we are hurt at all, not only below --heal-at). A step doesn't stop the
        channel healing us: it only shortens the reach to the pets to 1 (live 20261014_221341 22:35:50-55 Logan
        walked 2 tiles mid-channel and its finish still healed him and resurrected GraveRobber; 20261017
        16:38:43.97-49.21 begun, 25 steps, +32). True when something went out.
        Not while a planned walk out stands to fight (walk_fighting: walk_in_fight's own order, the retreat before the
        vet supplies on a pet), and the area peace only on a walk that goes on through what attacks it (not
        walk_out_fought): live 2026-10-28 22:53:08.8-14.6 a planned walk out played it 1 tile from a lowtide scavenger
        (-11, -33, -12 meanwhile, the pets on follow) and stopped to fight anyway; 22:53:17.6-20.7 this guard drank and
        started the vet supplies on Bussin (89 %) at 29 then 6 hits before walk_in_fight's retreat, which then read the
        hits from before both ("at 31/100") and came after Logan's death."""
        if not self.walking_out or self._drinking or self.walk_fighting:
            return False
        me = st["world"]["self"]
        self._drinking = True
        try:
            if self.team is not None and not self.pk_flight and not self.walk_out_fought \
                    and self.team.area_peace(st, walking_out=True):
                return True               # through monsters on the way out: a 2 s peace on them (Team.area_peace)
            #                               (not from a red: live 20261017_133304 13:40:56 it stopped 1.5 s to play
            #                               while Tugarin zmey's Explosions landed, -32 more, dead 2 s later)
            acted = self.cure(st)
            if me.get("hits") is None or me.get("hits_max") is None or me["hits"] >= me["hits_max"]:
                return acted
            if self.frac(me) < self.args.heal_at and self.potions.ready(time.monotonic()) \
                    and healing.heal_potions(st["world"], self.me(st)):
                self.heal(st, potions=True)
                acted = True
            if self.team is not None and time.monotonic() >= self.team.vet_busy_until:
                acted = self.team.vet(self.link.state(), us_below=1.0) or acted
            return acted
        finally:
            self._drinking = False

    # ------------------------------------------------------------ recall mode: watchful waits
    def look(self, st=None):
        """Recall mode, during a visit and on the walk in: a hostile player (player_threat) raises
        PlayerThreat from any state read, human pause or server wait, so the recall
        goes out at once (loop_lumber's look). Nothing while leaving (fleeing), at home
        or inside the check itself; without --leave-recall it does nothing. Live 2026-10-22 18:52:33-18:53:48
        (hunt-20261022-185159-c0c5): the red Botan in view on the walk in's first level 44 s before "Botan is
        attacking you!", no check until Maus 3; the pets guarded, Area Peace twice, the vet in place: dead."""
        if self.args.leave_recall is None or self._looking or self.fleeing \
                or (self.visit is None and not self.walking_in):
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
        if kind in BLIND_PAUSES or self._looking or self.fleeing or (self.visit is None and not self.walking_in):
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
        2026-10-15: "I don't think we need to escape from a gray player. Only reds."). Every hostile player seen is
        noted where it stands (note_hostiles: the flight runs from it even once it is out of view)."""
        a = self.watch.update(st, recall_s=0.0, margin_s=0.0)
        self.note_hostiles(st, a)
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
        advance that would pull too much (tamer.Team.pull_check), or the pets fighting or left behind
        (patrol_hold); an eerie corpse to fill a soul jar at."""
        self.scan_events(st)
        if why := self.crawl.interrupt(st) or (self.team.pull_check(st) or self.patrol_hold(st)
                                               if self.team is not None else None):
            return why
        return "an eerie corpse to fill a soul jar at" if self.soul_corpse(st) is not None else None

    def patrol_hold(self, st) -> str | None:
        """--pets: why not to patrol now (the crawl waits, or goes back to the pets): a pet fighting (pets_busy)
        or one more than WALK_IN_LAG_R off or out of view (pets_lagging). User 2026-10-17, after Logan walked
        off on a 101-step patrol at 17:11:55 while the pets fought a tormented soul: "he should check that his
        pets are not currently fighting something before starting a patrol, and check during a patrol that his
        pets are still near him"."""
        if self.team is None:
            return None
        return self.pets_busy(st) or self.pets_lagging(st)

    def pets_busy(self, st) -> str | None:
        """A live pet fighting: a monster on a pet (Team.pet_attackers: in war mode beside a pet hurt lately),
        or a pet in war mode within PET_BUSY_R of a monster in war mode, not one on another player or his pets
        (Team.elsewhere) that no pet of ours swings at: live 2026-10-24 19:01:21-19:02:07 "GraveRobber is fighting"
        stopped the patrol beside a purger on Gix's boreal faerie wyrm, nine times; guarding pets keep war mode on.
        Not one on a ledge or floor the pets can't reach (on_our_floor): live 2026-10-19 19:34-19:35 an insane
        reanimator at z 30 hit GraveRobber at z 5 below it."""
        if self.team is None:
            return None
        world = st["world"]

        def reach(s) -> bool:
            m = world["mobiles"].get(f"0x{s:08X}")
            return m is not None and m.get("x") is not None and self.on_our_floor(st, s, m)
        if any(reach(s) for s in self.team.pet_attackers(st)):
            return "the pets are fighting"
        now = time.time()
        ours = {serial_of(sw["defender"]) for a, sw in (world.get("swings") or {}).items()
                if serial_of(a) in self.team.roster and sw.get("defender") is not None
                and now - (sw.get("t") or 0) <= SWING_RECENT_S}
        foes = [(m["x"], m["y"]) for k, m in world["mobiles"].items()
                if m.get("x") is not None and not m.get("pet") and serial_of(k) not in self.team.roster
                and serial_of(k) not in self.dead and (m.get("flags") or 0) & threats.FLAG_WARMODE
                and m.get("notoriety") in combat.ATTACKABLE_NOTORIETY and reach(serial_of(k))
                and (serial_of(k) in ours or self.team.elsewhere(st, m) is None)]
        for p in self.team.pets(st):
            if not p["dead"] and p["war"] and any(cheb((p["x"], p["y"]), f) <= PET_BUSY_R for f in foes):
                return f"{p['name']} is fighting"
        return None

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
                text = ev.get("text") or ""
                if gating(text):
                    self.halt = "a server restriction message"
                    self.memory.juncture("hunt", "server_restriction",
                                         f"Server restriction: {ev.get('text')!r}"[:300], "urgent",
                                         {"text": ev.get("text")})
                    raise Abort(f"server restriction message: {ev.get('text')!r}")
        self.heard_upto = len(self.link.events)

    def died(self, st, reason):
        """A `death` juncture and job event, then Abort. Cause "pk" with a hostile player in view, one seen within
        RED_RECENT_S (hostile_seen) or "… is attacking you!" over us that long (live 2026-10-22 18:53:48 Botan
        killed Logan beside him and the juncture said "unknown": no player in view by the ghost's first read)."""
        a = self.watch.update(st, recall_s=0.0, margin_s=0.0)
        players = [t for t in a.threats if t.hostile and t.player]
        now = time.monotonic()
        recent = [n for _, t, n in self.hostile_seen.values() if now - t <= RED_RECENT_S] \
            + [t[: -len(ATTACKED_BY)] for t in self.lines_over_us(st, RED_RECENT_S) if t.endswith(ATTACKED_BY)]
        cause = "pk" if players or recent else ("mob" if self.attackers(st) or a.under_attack else "unknown")
        data = {"reason": reason, "cause": cause, "attackers": self.attackers(st), "threats": a.to_dict(),
                "players_recent": sorted(set(recent))}
        self.halt = "dead"
        self.memory.juncture("hunt", "death", f"Died ({cause}): {reason}", "urgent", data)
        self.memory.job_event("hunt", "death", data, **self._where(st))
        raise Abort(f"died ({cause}): {reason}")

    def state(self):
        st = self.link.state()
        if self.check_guards(st):
            # a guard acted (a potion, the vet supplies, an area peace: seconds): what follows decides on now, not on
            # the read before it (live 2026-10-28 22:53:22 "retreating ... at 31/100 hits", read at 17.6 before the
            # guard's potion and vet supplies; Logan was at 6 by then and dead at 21.93)
            st = self.link.state()
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
        from: where we stand when crawling or before the first arrival (walk_in_fight: no
        fight spot yet), else the fight spot."""
        if self.crawl is not None or self.fight_spot is None:
            return tuple(self.pos(st)[:2])
        return self.fight_spot

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
                # 20261015_171928 19:49:58-50:22 a banshee 5 tiles off, -21..-28 every 3.3 s, never a swing), and a
                # ranged monster in its reach (live 20261017 15:43:14: a corpse purger's barrage from 10 tiles, -23
                # -28 and a disease, and the patrol walked on for 5 s, its 82 steps, until the first disease tick)
                for key, mob in world["mobiles"].items():
                    s = serial_of(key)
                    if s == me or mob.get("x") is None or not (mob.get("flags") or 0) & threats.FLAG_WARMODE \
                            or mob.get("pet") or mob.get("notoriety") not in combat.ATTACKABLE_NOTORIETY:
                        continue
                    kind = aggro.type_key(labels.get(key) or mob.get("name"))
                    reach = RANGED_REACH if aggro.ranged(kind) else CASTER_REACH if aggro.caster(kind) else -1
                    if cheb(pos, (mob["x"], mob["y"])) <= reach and threats.identify(mob, labels.get(key))[0] == "monster":
                        found.setdefault(s, (f"a {'ranged monster' if reach == RANGED_REACH else 'caster'} in war mode "
                                             f"in reach while we take damage", time.monotonic() - hurt))
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
        """The serial to fight now, or None: a monster beside us attacking us that the pets aren't fighting (LOOSE_R)
        while the current one isn't on us; else keep the current one while it's valid and nothing else is attacking
        us; else an attacker with the lowest hits; else (pull) the nearest within --pull-range of the anchor (the fight
        spot; crawling, us). A target dropped as pinned is passed over until its skip ends, unless it attacks us;
        crawling, so is a creature type the crawl avoids (too strong or too slow for us)."""
        world, me, pos = st["world"], self.me(st), self.pos(st)
        atts = self.attackers(st)
        on_us = {serial_of(a["serial"]) for a in atts}
        beside = {serial_of(a["serial"]) for a in atts if a["dist"] <= LOOSE_R}
        attacking = set(on_us)
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
            if (who := self.ally_fight(st, mob)) is not None:
                self.ally_seen(s, mob, who)     # its corpse is theirs whoever ends up killing it (loot)
                if s not in attacking:
                    continue                    # an ally's fight: never our target (the tick drops an engaged one)
            if self.crawl is not None and s not in attacking and s != self.engaged \
                    and self.crawl.avoid(st, key, mob):
                continue
            if s not in attacking and aggro.type_key((world.get("labels") or {}).get(key) or mob.get("name")) \
                    in (self.args.never_aggro or aggro.NEVER_AGGRO):
                continue
            if self.live_target(st, s)[0] is None:
                continue
            if s not in on_us and not self.on_our_floor(st, s, mob):
                # a monster on a ledge or floor the pets can't reach isn't a target, even one hitting a pet from there
                # (live 2026-10-19 19:34:43-35:50: an insane reanimator at z 30 over GraveRobber at z 5 on the walk in,
                # picked, dropped as pinned and picked again every 15 s while the walk stood its whole 90 s)
                continue
            cands.append((s, mob))
        if self.team is not None and self.engaged not in on_us and (
                loose := [(s, m) for s, m in cands if s in beside and not self.team.engaged(st, s)]):
            # the pets on one monster while another chews on us: it becomes the target, so Team.fight's "All Kill" at a
            # mob on us they leave alone sends them at it (simulator level 2, 144 runs: 7 of 24 deaths a fin alone on
            # Logan, the pets at full hits 5-10 off on a zoa, the target kept because it fought the pets)
            s, m = min(loose, key=lambda sm: cheb(pos, (sm[1]["x"], sm[1]["y"])))
            if self.engaged is not None and s != self.engaged:
                log(f"{m.get('name') or 'a monster'} 0x{s:08X} on us and the pets on 0x{self.engaged:08X}: "
                    "it is the target now")
            return s
        if pull and self.engaged is not None and any(s == self.engaged for s, _ in cands) \
                and (self.engaged in attacking or not attacking):
            return self.engaged
        att = [(s, m) for s, m in cands if s in attacking]
        if att:
            if any(s == self.engaged for s, _ in att):
                return self.engaged
            return min(att, key=lambda sm: (self.frac(sm[1]), cheb(pos, (sm[1]["x"], sm[1]["y"]))))[0]
        if not pull:
            return None
        anchor = self.anchor(st)
        near = [(s, m) for s, m in cands if cheb(anchor, (m["x"], m["y"])) <= self.args.pull_range]
        return min(near, key=lambda sm: cheb(pos, (sm[1]["x"], sm[1]["y"])))[0] if near else None

    def ally_fight(self, st, mob: dict) -> str | None:
        """The name of an ally fighting `mob` (threats.ally: green to us, or our guild's tag; or an ally's pet): `mob`
        in war mode with one of them within tamer.ELSEWHERE_R, as Team.elsewhere reads anyone's fight. None when no
        ally is on it. Our own pets never count."""
        if not (mob.get("flags") or 0) & threats.FLAG_WARMODE or mob.get("x") is None:
            return None
        world = st["world"]
        labels, me = world.get("labels") or {}, world["self"]
        ours = {self.me(st)} | (set(self.team.roster) if self.team is not None else set())
        best = None
        for key, o in world["mobiles"].items():
            s = serial_of(key)
            if s in ours or o.get("x") is None or o.get("dead") or not threats.ally(o, me):
                continue
            d = cheb((o["x"], o["y"]), (mob["x"], mob["y"]))
            if d > tamer.ELSEWHERE_R or (best is not None and d >= best[0]):
                continue
            _, player, _ = threats.identify(o, labels.get(key))
            if player or o.get("pet"):
                best = (d, labels.get(key) or o.get("name") or key)
        return best[1] if best else None

    def ally_seen(self, serial: int, mob: dict, who: str) -> None:
        """An ally is fighting `serial` (user 2026-10-28: no kill stealing from DTF or an allied guild): remembered in
        `ally_fought`, so it is never our target while they fight it and its corpse is never looted (loot)."""
        if serial not in self.ally_fought:
            log(f"leaving {mob.get('name') or 'it'} 0x{serial:08X} to {who} (an ally is fighting it)")
        self.ally_fought[serial] = who

    def on_our_floor(self, st, serial: int, mob: dict) -> bool:
        """At our height (its map surface within FLOOR_Z_TOL of our z), or else a short walk reaches a tile beside
        `mob` at its height: at most FLOOR_DETOUR steps more than its straight-line distance. A monster on a ledge
        or floor above or below, reached only by a long way round, isn't a target in reach (user 2026-10-17: "it
        doesn't understand the z-levels put the creatures out of sight above it"; live 21:10-21:11 a vampire at
        (5200, 648, 45) one tile from Logan at (5200, 647, 25), 11 steps away by the stairs: picked, passed over as
        out of sight, picked again 60 s later, holding the waypoint meanwhile). True without map data. Cached per
        (its tile, our tile).
        """
        pos = self.pos(st)
        key = ((mob["x"], mob["y"], mob.get("z")), tuple(pos[:3]))
        seen = self.floor_seen.get(serial)
        if seen is not None and seen[0] == key:
            return seen[1]
        walk = self.mover.walk_map(st)
        if walk is None:
            return True
        z = los.surface_z(walk, mob["x"], mob["y"], mob.get("z") or 0)
        if abs(z - pos[2]) <= FLOOR_Z_TOL:
            ok = True                   # our height: the same-floor rules as before (a detour round a wall is fine)
        else:
            goal = nav.within((mob["x"], mob["y"]), 1, lambda sz: abs(sz - z) <= FLOOR_Z_TOL)
            tele = self.mover.teleporter_tiles(st["world"]["self"].get("map"))
            budget = max(0, cheb(pos, (mob["x"], mob["y"])) - 1) + FLOOR_DETOUR
            ok = pathfind.plan(walk, pos[:3], goal, occupied=tele, max_steps=budget) is not None
        self.floor_seen[serial] = (key, ok)
        return ok

    def floor_z_ok(self, st, mob: dict):
        """A Mover z_ok standing on `mob`'s floor (within FLOOR_Z_TOL of its map surface), or None without map data."""
        walk = self.mover.walk_map(st)
        if walk is None:
            return None
        z = los.surface_z(walk, mob["x"], mob["y"], mob.get("z") or 0)
        return lambda sz: abs(sz - z) <= FLOOR_Z_TOL

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
        if self.oxy is not None and (pos := self.pos(st)) is not None:
            self.oxy.update(tuple(pos[:2]), new, time.monotonic(), st["world"]["self"].get("map") or 0)
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
        if self.args.loot and aggro.type_key(name) in EMPTY_CORPSES:
            self.kill_gold({"name": name, "x": mob.get("x"), "y": mob.get("y")}, 0)
        elif self.args.loot:
            self.corpses.append({"mob": serial, "name": name, "x": mob.get("x"), "y": mob.get("y"),
                                 "t": time.monotonic()})
        if self.crawl is not None and mob.get("x") is not None:
            self.crawl.on_kill((mob["x"], mob["y"]))
        if self.team is not None:
            self.team.on_kill(serial)
        held = [s for s, (_, why) in self.jar_later.items() if why.endswith("letting it come")]
        for s in held:
            # what the walk let come came (the pets fight what comes on) and died: try the corpse again. Not the kill of
            # the very monster the hold named (pull_check names no serial), but a retry whose stop still finds one
            # holding stops and defers again, one stop per kill at most. Live 2026-10-20 23:02:03-23:04:10
            # (hunt-20261020-225836-002b): the vampire bat held for died at 23:02:15, the corpse waited out its 120 s,
            # and the user filled the jar by hand
            log(f"an eerie corpse's walk waited for: {self.jar_later.pop(s)[1]}; a kill since: trying it again")
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
            self.human.wait("hotkey")
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
        self.human.wait("hotkey_aim")
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
        casts instead. "You are already at full health." (healing.CLILOC_FULL_HEALTH: a heal landed between the
        choice and the click) drinks nothing and leaves the clock where it was (ServUO BaseHealPotion: the lock is taken
        only on a drink; live 2026-10-25 00:38:38.9 a vet finish took Logan 72 -> 100 just before the click, the clock
        counted a drink, and at 65 and 45 hits no potion was tried until he died 6 s later). "You must wait to perform
        another action." (tamer.CLILOC_ACTION_WAIT: an item use just before, the server's action delay) drinks nothing
        either: the click again USE_RETRY_S later, DRINK_TRIES in all, and the clock where it was (live 2026-10-29
        12:36:47.56 the potion 0.26 s after the vet supplies' click was refused, logged "heal potion: 56 -> 56 hits" and
        counted as drunk; no potion went out again before the death 7.6 s later, at 23 hits for one)."""
        me = st["world"]["self"]
        self.doing("heal", f"Drinking a heal potion ({me.get('hits')}/{me.get('hits_max')} hits)")
        self.human.wait("hotkey")                     # the user's Razor "Drink Heal Potion" hotkey
        answers = (healing.CLILOC_HEALED, healing.CLILOC_FULL_HEALTH, tamer.CLILOC_ACTION_WAIT)
        for n in range(1, DRINK_TRIES + 1):
            mark = len(self.link.events)
            self.link.act(actions.dclick(serial))
            self.wait_for(lambda s: any((e.get("ev") == "cliloc" and e.get("cliloc") in answers)
                                        or healing.potion_wait(e) is not None for e in self.link.events[mark:]), 2.0)
            heard = {e.get("cliloc") for e in self.link.events[mark:] if e.get("ev") == "cliloc"}
            if tamer.CLILOC_ACTION_WAIT not in heard or healing.CLILOC_HEALED in heard:
                break
            if n < DRINK_TRIES:
                log(f"heal potion: 'You must wait to perform another action.' (an item use just before); clicking it "
                    f"again in {tamer.USE_RETRY_S:g} s")
                time.sleep(tamer.USE_RETRY_S)
        now = time.monotonic()
        hits = self.link.last["world"]["self"].get("hits")
        if healing.CLILOC_FULL_HEALTH in heard and healing.CLILOC_HEALED not in heard:
            log(f"heal potion not drunk: already at full health ({me.get('hits')} -> {hits} hits before the click)")
            return False
        if tamer.CLILOC_ACTION_WAIT in heard and healing.CLILOC_HEALED not in heard:
            log(f"heal potion not drunk: 'You must wait to perform another action.' {DRINK_TRIES} times "
                f"({hits}/{me.get('hits_max')} hits); the potion clock as it was")
            return False
        self.potions.started(now)
        wait = next((w for e in self.link.events[mark:] if (w := healing.potion_wait(e)) is not None), None)
        if wait is not None:
            self.potions.refused(now, wait)
            log(f"heal potion refused: {wait:g} s to wait ({me.get('hits')}/{me.get('hits_max')} hits)")
            return False
        self.count("potions")
        log(f"heal potion: {me.get('hits')} -> {hits} hits")
        return healing.CLILOC_HEALED in heard or (hits or 0) > (me.get("hits") or 0)

    def cure(self, st) -> bool:
        """Poisoned (world.self.poisoned: the server's 0x17 about us) and a cure potion (healing.CURE_POTION_GRAPHIC) in
        the pack: one double-click on it wherever it sits, as drink(); not within healing.RETRY_S["cure"] of the last
        try. User 2026-10-26: "the runner seems not to use cure potions when Logan is poisoned"; live 22:31:09-49 on
        Tidal Tomb level 1 he stayed poisoned 40 s until the user drank one (cliloc 500231 "You feel cured of
        poison!", the flag off). True when the potion went out."""
        world = st["world"]
        now = time.monotonic()
        if not world["self"].get("poisoned") or now - self.cure_t < healing.RETRY_S["cure"]:
            return False
        pots = healing.in_pack(world, self.me(st), healing.CURE_POTION_GRAPHIC)
        if not pots:
            return False
        self.cure_t = now
        me = world["self"]
        self.doing("heal", f"Drinking a cure potion (poisoned, {me.get('hits')}/{me.get('hits_max')} hits)")
        self.human.wait("hotkey")
        self.link.act(actions.dclick(pots[0][0]))
        got = self.link.wait(lambda s: not s["world"]["self"].get("poisoned"), 1.5)
        self.count("cures")
        log("cure potion: " + ("cured" if got is not None else "still poisoned"))
        return True

    def refresh_potion(self) -> bool:
        """A stamina potion (healing.REFRESH_POTION_GRAPHIC, Total Refresh) for a shove the server refused at
        low stamina (Mover.refresh; user 2026-10-15: "We carry stamina potions so if we get stamina limited and
        need to shove we can drink a pot"): one double-click wherever it sits, as drink(). Not within
        healing.RETRY_S["refresh"] of the last. True when stamina rose."""
        now = time.monotonic()
        st = self.link.state()
        me = st["world"]["self"]
        pots = healing.in_pack(st["world"], self.me(st), healing.REFRESH_POTION_GRAPHIC)
        if not pots or now - self.refresh_t < healing.RETRY_S["refresh"]:
            return False
        self.refresh_t = now
        stam = me.get("stam") or 0
        self.human.wait("hotkey")
        self.link.act(actions.dclick(pots[0][0]))
        got = self.link.wait(lambda s: (s["world"]["self"].get("stam") or 0) > stam, 1.5)
        if got is not None:
            self.count("potions")
        return got is not None

    def heal_spell(self, st, sid: int) -> bool:
        """Heal or Greater Heal on self: cast, then answer the cursor with self."""
        me = st["world"]["self"]
        name = combat.MAGERY_SPELLS[sid - 1]
        self.doing("heal", f"Casting {name} on myself ({me.get('hits')}/{me.get('hits_max')} hits)")
        cur = self.cast(sid, "myself")
        if cur is None:
            return False
        self.human.wait("hotkey_aim")
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

    def loot_next(self):
        """The first queued kill to loot now: one waiting for an area hitter (Team.loot_tile) is passed over until its
        LOOT_DEFER_S is up, so the fight goes on meanwhile; None when there is none."""
        now = time.monotonic()
        return next((c for c in self.corpses if c.get("defer_until", 0.0) <= now), None)

    def loot(self, c) -> None:
        """Loot one corpse like `ctl act loot`: refuse human corpses and blue ones (another
        player's kill: loot rights go to the most damage), walk within LOOT_RANGE (--pets, an area hitter in war mode
        in view: to a tile clear of its reach, routed round it, or the corpse kept queued while every tile within
        LOOT_RANGE of it is in reach: Team.loot_tile, loot_next), open the
        backpack and the corpse (a refusal stops it: open_answer), then lift + drop each item
        into the backpack, gold first, stopping at the weight limit. An item counts as taken
        once it lies in the backpack (or merged into a pile there), never on a lift reject.
        Lifts go combat.LIFT_GAP_S apart at least (after the human drag pause); one the server refuses
        with "You must wait another moment ..." is tried again once the gap is over (combat.LIFT_TRIES
        in all). Before each item and each retry: a heal below --heal-at, and stop when a leave rule
        fires (live 2026-10-03 hits fell 64 -> 49 of 84 during one loot and the runner left without
        drinking)."""
        st = self.state()
        if c.get("corpse") is None:  # 0xDEAD names it; else the corpse that appeared on its tile
            c["corpse"] = self.corpse_of.get(c["mob"]) or next(
                (serial_of(k) for k, it in st["world"]["items"].items()
                 if it.get("graphic") == combat.CORPSE_GRAPHIC and it.get("container") is None
                 and serial_of(k) not in self.looted
                 and c["x"] is not None and cheb((it["x"], it["y"]), (c["x"], c["y"])) <= 1), None)
        me = st["world"]["self"]
        wmax = weight_max(me)
        if self.overweight and me.get("weight") is not None and wmax and me["weight"] >= wmax - LOOT_WEIGHT_MARGIN:
            self.corpses.remove(c)
            if c.get("corpse") is not None:
                self.looted.add(c["corpse"])
            log(f"weight {me['weight']}/{wmax}: not looting the {c['name']}")
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
        loot_at = None
        if self.team is not None and corpse.get("container") is None and corpse.get("x") is not None:
            loot_at, why = self.team.loot_tile(st, (corpse["x"], corpse["y"]))
            if why:
                # kept queued (others race us to loot): tried again after LOOT_DEFER_S, at once safe (loot_next)
                if c.get("deferred") != why:
                    log(f"not looting {corpse.get('name') or c['name']} yet: {why}; waiting for the area hitter "
                        f"to die or go")
                c["deferred"], c["defer_until"] = why, time.monotonic() + LOOT_DEFER_S
                return
        c.pop("deferred", None)
        self.corpses.remove(c)
        self.looted.add(c["corpse"])
        if corpse.get("graphic") != combat.CORPSE_GRAPHIC or corpse.get("container") is not None:
            log(f"0x{c['corpse']:08X} isn't a corpse on the ground; not looting")
            return
        if combat.human_corpse(corpse) and self.corpse_of.get(c["mob"]) != c["corpse"]:
            # the server's 0xDEAD names the corpse of the monster we killed: that one is a monster's whatever its body
            # (live 2026-10-17 22:21:37 a forgotten soul, a human body, its corpse unnamed yet, left with its loot)
            log(f"0x{c['corpse']:08X} ({corpse.get('name')}) is a human corpse: not looted")
            return
        name = corpse.get("name") or c["name"]
        if corpse.get("notoriety") == BLUE:
            log(f"corpse of {name} is someone else's kill (blue): not looted")
            self.refused(c, "blue", st)
            return
        if (who := self.ally_fought.get(c["mob"])) is not None:
            # an ally's kill isn't ours to loot even when the server lets us (user 2026-10-28: "Even if we don't damage
            # a mob an ally kills, we can loot the corpse, but we shouldn't as that is rude")
            log(f"corpse of {name}: {who} (an ally) was fighting it; not looted")
            self.refused(c, "ally", st)
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
        if loot_at is not None or cheb(self.pos(st), spot) > combat.LOOT_RANGE:
            goal, r = (loot_at[:2], 0) if loot_at is not None else (spot, 1)
            if loot_at is not None:
                log(f"to the corpse: looting from {loot_at[:2]}, {cheb(loot_at, spot)} from it, out of the area hits' reach")
            # at most twice the distance plus LOOT_DETOUR_STEPS: round an area hit's reach when that is a short detour
            # (the reach among the Mover's danger zones); unbounded it planned 117-123-step detours to corpses 7 tiles
            # off (sim seeds 18-22), and after a refused shove one walked 119 steps away from the pets through two
            # rooms and died there (sim 2026-10-21, seed 1019)
            bound = 2 * cheb(self.pos(st), goal) + LOOT_DETOUR_STEPS
            # a sprayer's wind-up with us in its cone ends the walk (Team.spray_stop): out of it first, the corpse after
            stop = self.team.spray_stop if self.team is not None else None
            try:
                try:
                    with self.team.around_areas(st) if self.team is not None else contextlib.nullcontext() as round_:
                        why = self.mover.walk_to(lambda: goal, r, "to the corpse", max_route=bound, stop=stop)
                except WalkFailed as e:
                    if not round_ or not str(e).startswith("to the corpse: no route within"):
                        raise
                    # through the reach
                    why = self.mover.walk_to(lambda: goal, r, "to the corpse", max_route=bound, stop=stop)
            except Abort as e:
                if not str(e).startswith("to the corpse"):
                    raise
                log(f"corpse of {name} at {spot}: {e}; leaving it")   # e.g. on a ledge (sim: 21 runs ended here)
                return
            if why:
                # kept queued for the wave (Team.spray_stop: we stood in or beside its cone), then the walk again
                log(f"to the corpse: stopped ({why}); looting it after the wave")
                self.looted.discard(c["corpse"])
                c["defer_until"] = time.monotonic() + tamer.SPRAY_WARN_S
                self.corpses.insert(0, c)
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
        stop = False
        for k, it in items[: self.args.loot_max]:
            if stop:
                break
            st = self.state()
            if self.team is not None and self.team.out_of_bubbles(st, near=(spot, combat.LOOT_RANGE)):
                st = self.state()          # a grouper's bubble beside us: out of its reach first, the corpse kept in reach
            me = st["world"]["self"]
            if self.leave_reason(st):
                log("a leave rule fired while looting: leaving the rest")
                break
            if me.get("hits") is not None and self.frac(me) < self.heal_at_now() and self.heal(st):
                me = self.state()["world"]["self"]
            wmax = weight_max(me)
            if me.get("weight") is not None and wmax \
                    and me["weight"] + combat.item_weight(it) > wmax - LOOT_WEIGHT_MARGIN:
                log(f"weight {me['weight']}/{wmax}: no room for {combat.item_name(it)} "
                    f"(~{combat.item_weight(it):.0f} stones); leaving it")
                if it.get("graphic") == combat.GOLD_GRAPHIC or me["weight"] >= wmax - LOOT_WEIGHT_MARGIN:
                    self.overweight = True
                    break
                continue
            s, graphic = serial_of(k), it.get("graphic")
            for attempt in range(1, combat.LIFT_TRIES + 1):
                self.human.wait("drag")
                if (gap := self.lift_ok_t + combat.LIFT_GAP_S - time.monotonic()) > 0:
                    time.sleep(gap)                  # the server's gap between lifts (combat.LIFT_GAP_S)
                st = self.state()
                before, lift_mark = self.pack_amount(st, graphic), len(self.link.events)

                def landed(st2):
                    now = st2["world"]["items"].get(k)
                    if now is not None:
                        return now.get("container") is not None and serial_of(now["container"]) == pack
                    return self.pack_amount(st2, graphic) > before    # merged into a pile in the pack

                def rejected():
                    return any(e.get("ev") == "lift_reject" for e in self.link.events[lift_mark:])
                sent = time.monotonic()
                for pkt in combat.grab_packets(s, it.get("amount") or 1, pack):
                    self.link.act(pkt)
                st2 = self.wait_for(lambda st2: rejected() or landed(st2), CONTAINER_WAIT_S)
                if not rejected():
                    self.lift_ok_t = sent
                    if st2 is not None:
                        taken.append({"serial": k, "graphic": f"0x{graphic or 0:04X}", "amount": it.get("amount")})
                    break
                if not combat.lift_wait_refused(self.link.events[lift_mark:]) or attempt == combat.LIFT_TRIES:
                    rejects += 1
                    log(f"lift of 0x{s:08X} rejected: not taken")
                    break
                log(f"lift of 0x{s:08X}: 'You must wait another moment'; trying again in "
                    f"{max(0.0, self.lift_ok_t + combat.LIFT_GAP_S - time.monotonic()):.1f} s")
                st = self.state()
                if self.leave_reason(st):
                    log("a leave rule fired while looting: leaving the rest")
                    stop = True
                    break
                me = st["world"]["self"]
                if me.get("hits") is not None and self.frac(me) < self.args.heal_at:
                    self.heal(st)
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

    def drop_gold(self, st) -> bool:
        """Mover.unload: over the weight limit (Mover.overloaded) every step is denied, so drop gold at our feet, the
        largest pile's, enough to come to weight_max with a stone to spare (combat.GOLD_WEIGHT_PER a coin), as the user
        asked 2026-10-28 after Logan stood at 462/450 on a Tidal Tomb leave with 40k gold on him. True once the pack
        holds less gold than before (the Mover then plans again and sheds more if the server still refuses)."""
        me = st["world"]["self"]
        wmax, w = weight_max(me), me.get("weight")
        piles = sorted(self.pack_items(st, combat.GOLD_GRAPHIC), key=lambda p: -(p[1].get("amount") or 1))
        if not piles or not wmax or w is None:
            log(f"overweight ({w}/{wmax}): no gold in the pack to drop")
            return False
        s, it = piles[0]
        n = min(it.get("amount") or 1, max(1, math.ceil((max(w - wmax, 0) + 1) / combat.GOLD_WEIGHT_PER)))
        x, y, z = self.pos(st)[:3]
        log(f"overweight ({w}/{wmax}): dropping {n} gold at our feet so we can move")
        self.doing("unload", f"Too heavy to move: dropping {n} gold")
        before = self.pack_gold(st)
        self.human.wait("drag")
        if (gap := self.lift_ok_t + combat.LIFT_GAP_S - time.monotonic()) > 0:
            time.sleep(gap)
        mark, sent = len(self.link.events), time.monotonic()
        self.link.act(actions.lift(s, n))
        self.link.act(actions.drop(s, x, y, z))
        st2 = self.wait_for(lambda s2: self.pack_gold(s2) < before
                            or any(e.get("ev") == "lift_reject" for e in self.link.events[mark:]), CONTAINER_WAIT_S)
        dropped = before - self.pack_gold(st2 or self.state())
        if dropped <= 0:
            log(f"overweight: the drop of {n} gold didn't leave the pack")
            return False
        self.lift_ok_t = sent
        self.wait_for(lambda s2: (s2["world"]["self"].get("weight") or 0) < w, CONTAINER_WAIT_S)
        me2 = self.state()["world"]["self"]
        log(f"dropped {dropped} gold: weight {me2.get('weight')}/{wmax}")
        self.count("gold_dropped", dropped)
        self.memory.job_event("hunt", "gold_dropped", {"gold": dropped, "weight": [w, me2.get("weight")],
                                                       "weight_max": wmax, "visit": self.visit_n}, **self._where(st))
        return True

    def kill_gold(self, c, gold, at=None):
        """Crawling: a kill's gold for the model (0 when its corpse never showed: a pet or
        player took it) and for the zone it lay in."""
        if self.crawl is None:
            return
        self.crawl.model.add_gold(c["name"], gold)
        if at is not None or c.get("x") is not None:
            self.crawl.on_gold(at or (c["x"], c["y"]), gold)

    # ------------------------------------------------------------ soul jars (Field of Souls)
    def soul_corpse(self, st, walk: bool = True):
        """(serial, item) of the eerie corpse to fill a soul jar at now, or None (--soul-jars; soul_jar.py): an
        empty soul jar in the pack and an eerie corpse in view on our floor that we haven't tried, the nearest.
        Without `walk` (a fight is on), and with the pets fighting or behind (patrol_hold), only one within
        soul_jar.FILL_WALK_R, where no step is needed; one whose walk stopped on the pull budget waits JAR_DEFER_S
        unless within soul_jar.FILL_WALK_R (live 2026-10-20 23:03:21: deferred, Logan looted one tile from it)."""
        if not self.args.soul_jars:
            return None
        world = st["world"]
        now = time.monotonic()
        found = [(s, it) for s, it in soul_jar.eerie_corpses(world) if s not in self.jar_tried]
        if not found or not soul_jar.empty_jars(world, self.me(st)):
            return None
        pos = self.pos(st)
        walk = walk and self.patrol_hold(st) is None
        found = [(s, it) for s, it in found if same_floor(it.get("z") or 0)(pos[2])
                 and ((walk and self.jar_later.get(s, (0.0, ""))[0] <= now)
                      or cheb(pos, (it["x"], it["y"])) <= soul_jar.FILL_WALK_R)]
        return min(found, key=lambda f: cheb(pos, (f[1]["x"], f[1]["y"])), default=None)

    def fill_soul_jar(self, serial: int, it: dict) -> None:
        """Fill an empty soul jar at the eerie corpse `serial` (user request 2026-10-17): walk within
        soul_jar.FILL_WALK_R of it, open the jar's bags like a player, double-click the jar and wait for a filled jar
        in the pack. One try per corpse (jar_tried), filled or not. A `soul_jar` job event. The walk stops when
        something attacks us, a leave rule fires or the corpse goes, and --pets, like a patrol (patrol_stop), when the
        pets fight or fall behind or a step would pull more than the budget (Team.pull_check; the corpse then waits
        JAR_DEFER_S): the next tick decides again. Live 2026-10-20 18:22:07 (hunt-20261020-181726-433a) the walk went
        16 steps alone, the pets left fighting a shadowguard, into an idle vampire countess, a corpsebride and a
        vampire bat by the corpse: 100 -> 0 in 20 s."""
        spot = (it["x"], it["y"])
        st = self.state()
        self.doing("soul_jar", f"Filling a soul jar at the eerie corpse at {spot[0]},{spot[1]}", spot, serial)
        if cheb(self.pos(st), spot) > soul_jar.FILL_WALK_R:
            log(f"an eerie corpse at {spot}, {cheb(self.pos(st), spot)} tiles off: walking over to fill a soul jar")

            def stop(s):
                if key_of(serial) not in s["world"]["items"]:
                    return "the eerie corpse is gone"
                self.scan_events(s)
                if self.attackers(s):
                    return "under attack"
                if self.leave_reason(s):
                    return "a leave rule"
                if self.team is None:
                    return None
                if why := self.patrol_hold(s):
                    return why
                if why := self.team.pull_check(s):
                    self.jar_later[serial] = (time.monotonic() + JAR_DEFER_S, why)
                    return f"{why}; the corpse waits {JAR_DEFER_S:.0f} s"
                return None
            try:
                # bounded like a loot walk: the corpse 18 tiles off by a 143-step way round left the view on the way,
                # "the eerie corpse is gone", and was walked to again every 3 s for 2 minutes (simulator 2026-10-24)
                why = self.mover.walk_to(lambda: spot, soul_jar.FILL_WALK_R, "to the eerie corpse",
                                         z_ok=same_floor(it.get("z") or 0), stop=stop,
                                         max_route=2 * cheb(self.pos(st), spot) + LOOT_DETOUR_STEPS)
            except Abort as e:
                if not str(e).startswith("to the eerie corpse"):
                    raise
                log(f"eerie corpse at {spot}: {e}; leaving it")
                self.jar_tried.add(serial)
                return
            if why:
                log(f"walk to the eerie corpse stopped: {why}")
                return
            st = self.state()
        me = self.me(st)
        jars = soul_jar.empty_jars(st["world"], me)
        if key_of(serial) not in st["world"]["items"] or not jars:
            log("the eerie corpse is gone" if jars else "no empty soul jar left")
            return
        jar = jars[0][0]
        self.open_for((jar, False))
        self.human.wait("use")
        before = {s for s, _ in soul_jar.filled_jars(self.state()["world"], me)}
        mark = len(self.link.events)
        self.jar_tried.add(serial)
        self.link.act(actions.dclick(jar))
        st2 = self.wait_for(lambda s: any(f not in before for f, _ in soul_jar.filled_jars(s["world"], me)),
                            soul_jar.FILL_WAIT_S)
        heard = self.link.events[mark:]
        data = {"corpse": key_of(serial), "at": [spot[0], spot[1], it.get("z")], "jar": key_of(jar),
                "spirit": soul_jar.spirit_name(heard), "visit": self.visit_n}
        if st2 is None:
            said = soul_jar.system_lines(heard)
            log(f"no filled soul jar at the eerie corpse at {spot}"
                + (f"; the server: {' | '.join(said[:3])}" if said else ""))
            self.memory.job_event("hunt", "soul_jar", {**data, "filled": False, "heard": said[:3]},
                                  **self._where(self.state()))
            return
        new = next(f for f, _ in soul_jar.filled_jars(st2["world"], me) if f not in before)
        self.count("soul_jars")
        log(f"soul jar filled (0x{new:08X}, the spirit of {data['spirit'] or 'an unknown soul'}): it decays in 60 "
            "minutes unless taken to an Occult Merchant (Prevalia or Corpse Creek)")
        self.memory.job_event("hunt", "soul_jar", {**data, "filled": True, "filled_jar": key_of(new)},
                              **self._where(st2))

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
        if self.left_level:
            return None     # off the hunt level by --leave-via: on to the gate (sim 2026-10-25: else the hunt went on a level up)
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
            self.human.wait("hotkey")
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
        """--rope: an Adventurer's Rope in the pack, its cooldown over and no hold on us (held_now)."""
        return bool(self.args.rope) and time.monotonic() >= self.rope_next \
            and bool(healing.in_pack(st["world"], self.me(st), ROPE_GRAPHIC)) and not self.held_now(st)

    def held_now(self, st) -> bool:
        """A hold on us: Hinder, Entangle or Paralyze (agent_link.HOLD_ICONS) before the end the server gave it, or a
        transfix (Mover.held). No rope then (user 2026-10-22: "I'm sure the 'hold' conditions don't let us rope";
        docs/NOTES.md "Hinder, Hamstring and the rope")."""
        return self.mover.held(st)

    def rope_worth(self, st) -> bool:
        """A flight's rope (rope_ready_now) only while no player is on us (player_calm); or hamstrung (user
        2026-10-22: with a walk's pace only, the rope even while they hit us); or when every hostile player is a ranged
        one (ranged_fight: next to him for the rope's second is no worse than a few tiles off)."""
        return self.rope_ready_now(st) and (self.player_calm(st) or self.hamstrung(st) or self.ranged_fight(st))

    def ranged_fight(self, st) -> bool:
        """Every hostile player the flight runs from (hostile_spots) is a ranged one (ranged_player), and there is one."""
        spots = self.hostile_spots(st)
        return bool(spots) and all(self.ranged_player(st, s) for s in spots)

    def ranged_player(self, st, serial: int) -> bool:
        """A hostile player who hurts from afar: harmful spell words from him within RANGED_WORDS_S
        (threats.harmful_spell), or, in view, a bow or crossbow in his hands (ARCHERY_GRAPHICS, or a tiledata name
        ending in "bow") or nothing in them (a caster: live 22:03 Flat Lander, a mastery chain, leather and a wizard's
        hat, three Energy Bolts and a Lightning)."""
        now = time.time()
        ev, et = self.link.events, self.link.event_t
        for i in range(len(ev) - 1, -1, -1):
            if now - et[i] > RANGED_WORDS_S:
                break
            e = ev[i]
            if e.get("ev") == "speech_heard" and serial_of(e.get("serial") or 0) == serial \
                    and threats.harmful_spell(e.get("text")):
                return True
        world = st["world"]
        if key_of(serial) not in world["mobiles"]:
            return False
        hands = [it for it in world["items"].values()
                 if it.get("layer") in (combat.LAYER_ONE_HANDED, combat.LAYER_TWO_HANDED)
                 and it.get("container") is not None and serial_of(it["container"]) == serial]
        return not hands or any(it.get("graphic") in ARCHERY_GRAPHICS or combat.item_name(it).endswith("bow")
                                for it in hands)

    def hamstrung(self, st) -> bool:
        """Hamstring (buff HAMSTRING_ICON) on us and not past the end the server gave it; without an end (no TimeSync
        yet) while our stamina is at most 1 (it sets stamina to 0 for its 3 s). The buff stays after it ends until the
        server removes it, so its presence alone isn't enough (docs/NOTES.md "Hinder, Hamstring and the rope")."""
        buffs = (st["world"].get("buffs") or {}).get(f"0x{self.me(st):08X}") or {}
        b = buffs.get(str(HAMSTRING_ICON)) or buffs.get(HAMSTRING_ICON)
        if b is None:
            return False
        if b.get("ends_t") is not None:
            return b["ends_t"] > time.time()
        stam = st["world"]["self"].get("stam")
        return stam is not None and stam <= 1

    def player_calm(self, st) -> bool:
        """No player on us: the rope's cast stands us ~1 s (user 2026-10-17: "ropes are useful for getting through
        groups of mobs or getting a lead on a red while they're not currently hitting us ... if a red is already on
        top of us its not good to just stand there and wait for the rope cast to resolve"). On us: '<name> is
        attacking you!' from a player, or a damage number over us while a red stands within spell range, in the
        last ROPE_CALM_S."""
        a = self.watch.update(st, recall_s=0.0, margin_s=0.0)
        players = [t for t in a.threats if t.player]
        names = {(t.name or "").lower() for t in players}
        red_near = any(t.kind == "red" and 0 <= t.distance <= self.watch.params.player_strike_range for t in players)
        for text in self.lines_over_us(st, ROPE_CALM_S):
            if text.endswith(ATTACKED_BY) and (text[: -len(ATTACKED_BY)].lower() in names
                                               or not text[0].islower()):   # a player, seen or not ("a shade wolf" isn't)
                return False
            if red_near and DAMAGE_TEXT.match(text):
                return False
        return True

    def lines_over_us(self, st, within_s: float) -> list[str]:
        """The overhead texts over us (speech_heard on our serial) of the last `within_s`, newest first."""
        me, now = self.me(st), time.time()
        ev, et, out = self.link.events, self.link.event_t, []
        for i in range(len(ev) - 1, -1, -1):
            if now - et[i] > within_s:
                break
            e = ev[i]
            if e.get("ev") == "speech_heard" and serial_of(e.get("serial") or 0) == me:
                out.append(e.get("text") or "")
        return out

    def hostile_spots(self, st) -> dict:
        """{serial: (x, y)} of the hostile players a flight runs from (note_hostiles): one out of view counts where
        it was last seen for RED_RECENT_S (hostile_seen)."""
        self.note_hostiles(st, self.watch.update(st, recall_s=0.0, margin_s=0.0))
        now = time.monotonic()
        for s, (_, t, _) in list(self.hostile_seen.items()):
            if now - t > RED_RECENT_S:
                del self.hostile_seen[s]
        return {s: xy for s, (xy, _, _) in self.hostile_seen.items()}

    def note_hostiles(self, st, a):
        """hostile_seen: where the red players in view stand now, and players in view named in '<name> is attacking
        you!' within RED_RECENT_S. On every hostile-player check (player_threat), not only on the flight: live
        2026-10-21 17:37:09.9 the red I Make Bodies came into view 18 tiles off, the leave fired, he stepped out of
        view at 17:37:10.9 before the flight's first look, and the flight ran up the gate's way into him."""
        players = [t for t in a.threats if t.player and t.distance >= 0]
        if not players:
            return
        named = {t[: -len(ATTACKED_BY)].lower() for t in self.lines_over_us(st, RED_RECENT_S) if t.endswith(ATTACKED_BY)} \
            if any(t.kind != "red" for t in players) else set()
        mobs, now = st["world"]["mobiles"], time.monotonic()
        for t in players:
            if t.kind == "red" or (t.name or "").lower() in named:
                m = mobs.get(f"0x{t.serial:08X}") or {}
                if m.get("x") is not None:
                    self.hostile_seen[t.serial] = ((m["x"], m["y"]), now, t.name or f"0x{t.serial:X}")

    def hostile_zones(self, spots: dict):
        """The Mover's danger zones (keys 'red:<serial>', RED_ZONE_R) round `spots`: a flight's routes bend round
        them (Mover.danger_cost), a moved one asks for a replan; the ones not in `spots` go."""
        mv = self.mover
        for k in [k for k in mv.danger if k.startswith("red:") and int(k[4:], 16) not in spots]:
            del mv.danger[k]
        for s, xy in spots.items():
            k, old = f"red:{s:08X}", mv.danger.get(f"red:{s:08X}")
            if old is None or cheb(old[0], xy) >= 2:
                mv.danger[k] = (xy, RED_ZONE_R)
                mv.replan_requested = True

    def passes_hostile(self, path, spots: dict, here):
        """The serial of a hostile player the route `path` passes (comes within RED_PASS_R of, nearer than we
        stand now), else None. One out of view since `age` s may be anywhere it could have run meanwhile: RED_RUN_TPS
        tiles a second more (live 17:37:10.9-13.3 I Make Bodies walked 9 tiles out of view, onto the gate's way)."""
        now = time.monotonic()
        for s, xy in spots.items():
            r = RED_PASS_R + int((now - self.hostile_seen.get(s, (xy, now))[1]) * RED_RUN_TPS)
            if path and len(path) > 1 and min(cheb(t, xy) for t in path[1:]) < min(cheb(here, xy), r):
                return s
        return None

    def flee_tile(self, st, spots: dict, failed=()):
        """The tile to run to from `spots`: of those the walk rules reach within FLEE_SEARCH_R in at most
        FLEE_DETOUR times their distance (no mobile on it, no known teleporter, not `failed`: walks there that
        failed), the farthest from the nearest of them (up to FLEE_FAR_R), the fewest steps on a tie; None without
        one FLEE_GAIN farther than here (cornered, or far enough already)."""
        walk = self.mover.walk_map(st)
        if walk is None or not spots:
            return None
        x, y, z = self.pos(st)[:3]

        def far(t) -> int:
            return min(FLEE_FAR_R, *(cheb(t, xy) for xy in spots.values()))
        steps = localmap.steps(walk, (x, y, los.surface_z(walk, x, y, z)), radius=FLEE_SEARCH_R)
        skip = self.mover.occupied(st) | self.mover.teleporter_tiles(st["world"]["self"].get("map")) | set(failed)
        best = max(((far(t), -n, t) for t, n in steps.items()
                    if n <= FLEE_DETOUR * cheb((x, y), t) and t not in skip), default=None)
        return best[2] if best is not None and best[0] >= far((x, y)) + FLEE_GAIN else None

    def dead_end(self, st, tile, near, spots: dict) -> bool:
        """Whether running to `tile` (flee_tile's) leaves the way to `near` (beside the gate) still passing `spots`
        (passes_hostile from there, the routes bent round them as the flight's are): every way out passes them, so
        running away only corners us (user 2026-10-22, after the 20:01:02 death in Maus 3's east wing: "Away first
        only outside dead ends"; Druidx came down the wing's only corridor and the flight ran ahead of him to its
        south-west end, 156 steps from the gate, then back toward him)."""
        mv = st["movement"]
        at = {**st, "movement": {**mv, "pos": [tile[0], tile[1], tile[2] if len(tile) > 2 else mv["pos"][2],
                                               *mv["pos"][3:]]}}
        path, _ = self.mover.plan(at, nav.within(near, 0), mobiles=False)
        return path is None or self.passes_hostile(path, spots, tuple(tile[:2])) is not None

    def rope_jump(self, goal, spots: dict | None = None) -> bool:
        """One Adventurer's Rope jump toward `goal` (Logan's escapes: '[Rope', then the ground target;
        jumps of 7-10 tiles, an 11-tile target refused): the farthest tile of the route there within ROPE_R
        that is in sight, 4 or more tiles off, its z walked along the route (a ledge's z beside it would
        strand us). From hostile players (`spots`, hostile_spots) only a landing farther from every one of them
        than we stand: none on the route, the one of the tiles we could walk to within ROPE_R farthest from the
        nearest of them (live 2026-10-21 10:35:06: the route to the gate ran past two reds and the rope landed
        Logan beside one of them). The cursor answered with Esc when a player turned on us meanwhile (player_calm;
        live 10:35:06.07 "Ticket Tongje is attacking you!" between the cursor and our target). True when we
        landed. Knowledge #13325: a Teleport by the spell's rules, 5 s cooldown from the use; followers on Follow
        come along (#5141)."""
        st = self.link.state()
        if not self.rope_ready_now(st):
            return False
        self.rope_next = time.monotonic() + ROPE_RETRY_S
        path, walk = self.mover.plan(st, nav.within(goal, 0), mobiles=False)
        sight = los.sight_for(self.mover.walkers, st["world"]["self"].get("map")) if walk is not None else None
        if sight is None or not (path or spots):
            return False
        # in our sight as a ground target is checked: from our eye (z from the map's surface where we stand, not the
        # world model's z, which lags) to the tile's ground (as the "All Patrol" clicks: 8 of 8 agreed with the
        # server). Live 2026-10-19 10:29:20.6: (5208, 698) from (5207, 689), eye to eye clear by a stale z, "Target
        # cannot be seen." (500237): ~1 s standing (hindered) 2 tiles from the red Malito
        x0, y0, zw = self.pos(st)[:3]
        pos = (x0, y0, los.surface_z(walk, x0, y0, zw))
        eye = (x0, y0, pos[2] + los.EYE)
        occupied = self.mover.occupied(st)      # live 13:40:52.9 "That location is blocked." (a summon on it)
        spots = spots or {}
        tele = self.mover.teleporter_tiles(st["world"]["self"].get("map"))   # never onto one (the Mover's walls too)
        # every one of them ranged (ranged_fight; user 2026-10-22): a landing beside one is no worse than a few tiles
        # off, so out of their sight first, then along the way, then the farthest from them
        ranged = bool(spots) and self.ranged_fight(st)

        def ok(t) -> bool:
            return cheb(pos, t) >= 4 and sight.point_blocked(eye, t) is None and t[:2] not in occupied \
                and t[:2] not in self.rope_refused and t[:2] not in tele \
                and (ranged or all(cheb(t, xy) > cheb(pos, xy) for xy in spots.values()))
        best, cur, on_route = None, pos, []
        for t in path[1:] if path else ():
            if cheb(pos, t) > ROPE_R:
                break
            cur = walk.can_walk(cur[0], cur[1], cur[2], nav.direction(cur[:2], t[:2]))
            if cur is None or cur[:2] != tuple(t[:2]):
                break
            if ok(cur):
                best = cur
                on_route.append(cur)
        hidden = ""
        if ranged:
            mobs = st["world"]["mobiles"]
            eyes = [(xy[0], xy[1], ((mobs.get(key_of(s)) or {}).get("z") or pos[2]) + los.EYE) for s, xy in spots.items()]

            def seen_by(t) -> int:
                return sum(1 for e in eyes if cheb(e, t) <= threats.SPELL_WORDS_RANGE
                           and sight.point_blocked(e, (t[0], t[1], t[2] + los.EYE)) is None)
            # along the way only, the least seen first: ranking every tile in reach "out of their sight first" landed
            # Logan back in the dead end and behind them (sim/fight2001.py: 7 of 24 dead that way, 0 without ranged ropes)
            best = min(enumerate(on_route), default=(0, None), key=lambda it: (seen_by(it[1]), -it[0]))[1]
        elif best is None and spots:
            steps = localmap.steps(walk, pos, radius=ROPE_R)
            cands = [(t[0], t[1], los.surface_z(walk, t[0], t[1], pos[2])) for t, n in steps.items()
                     if n <= FLEE_DETOUR * cheb(pos, t)]
            best = max((t for t in cands if ok(t)), default=None,
                       key=lambda t: (min(cheb(t, xy) for xy in spots.values()), -cheb(t, goal)))
        if best is None:
            if spots:
                log("rope: no landing in reach " + ("in sight" if ranged else "farther from the hostile player(s)"))
            return False
        if ranged:
            n = seen_by(best)
            hidden = (", out of their sight" if n == 0 else f", in sight of {n}") + " (ranged)"
        # in combat with a player when the rope goes out: its 2-minute cooldown starts (ROPE_COMBAT_COOLDOWN_S)
        fighting = any(t.endswith(ATTACKED_BY) and not t[0].islower()
                       for t in self.lines_over_us(st, ROPE_COMBAT_AFTER_S))
        mark = len(self.link.events)
        self.link.act(actions.say_ascii("[Rope"))

        def combat_wait():
            return next((m for e in self.link.events[mark:] if e.get("ev") == "speech_heard"
                         and (m := ROPE_COMBAT_RE.search(e.get("text") or ""))), None)
        # an item use just before (a potion, the vet supplies) makes it "You must wait to perform another action."
        # (500119): no cursor comes; live 2026-10-18 21:14:26.1-27.1 the flight stood the whole ROPE_WAIT_S for it
        if self.link.wait(lambda s: combat_wait() is not None
                          or any(e.get("ev") == "target" or (e.get("ev") == "cliloc"
                                                             and e.get("cliloc") == tamer.CLILOC_ACTION_WAIT)
                                 for e in self.link.events[mark:]), ROPE_WAIT_S) is None \
                or not (self.link.state()["world"].get("target") or {}).get("active"):
            if (m := combat_wait()) is not None:
                left = 60 * int(m.group(1) or 0) + int(m.group(2) or 0)
                self.rope_next = time.monotonic() + left
                log(f"rope: its combat cooldown, {left} s left (\"{m.group(0)}\")")
                return False
            log("rope: no target cursor")
            return False
        cur = self.link.state()["world"]["target"]
        self.human.wait("hotkey_aim")
        if self.pk_flight and not self.player_calm(self.link.state()) and not self.hamstrung(self.link.state()) \
                and not ranged:
            self.cancel(cur)
            log("rope: a player turned on us before the target went out: cursor cancelled (Esc), running on")
            return False
        self.link.act(actions.target_xyz(cur["cursor_id"], best[0], best[1], best[2], 0, cur.get("cursor_type") or 0))
        mark = len(self.link.events)
        landed = self.link.wait(lambda s: cheb(s["movement"]["pos"], best) <= 1
                                or any(e.get("ev") == "cliloc" and e.get("cliloc") in ROPE_REFUSED
                                       for e in self.link.events[mark:]), ROPE_WAIT_S) is not None \
            and cheb(self.pos(self.link.state()), best) <= 1
        refused = next((e.get("cliloc") for e in self.link.events[mark:]
                        if e.get("ev") == "cliloc" and e.get("cliloc") in ROPE_REFUSED), None)
        if refused is not None:
            # never that landing again this run (live 10:29:07.6 (5227, 653, 22), the top of a stack of statics:
            # "That location is blocked." with no mobile or item on it [INFERENCE: the server's fit check there])
            self.rope_refused.add(tuple(best[:2]))
        self.rope_next = time.monotonic() + (ROPE_COMBAT_COOLDOWN_S if fighting and landed else ROPE_COOLDOWN_S)
        self.count("ropes" if landed else "rope_misses")
        away = ""
        if spots:
            away = (f", {min(cheb(pos, xy) for xy in spots.values())} -> "
                    f"{min(cheb(best, xy) for xy in spots.values())} tiles from the nearest hostile player{hidden}")
        log(f"rope to {best[:2]} ({cheb(pos, best)} tiles{away}): " + ("landed" if landed else
                                                                       f"no jump (cliloc {refused})" if refused else
                                                                       "no jump")
            + (f"; in combat with a player: the next not for {ROPE_COMBAT_COOLDOWN_S:.0f} s" if fighting and landed
               else ""))
        return landed

    def leave_by_gate(self, severity: str) -> dict:
        """--leave-gate: walk to the moongate with the pets on 'All Follow Me' (user 2026-10-17, after the
        time-up walk out of 15:43:52 left them fighting behind on guard: "all follow me to run out, not all
        guard"), then beside it 'All Follow Me' again (tamer.follow: a hurt leave doesn't wait) so they come
        through, a double-click on the gate from that tile (live 20261013_204518; the step onto it when its
        item isn't in view) and the travel to --gate-dest (moongate.travel). A planned leave (PLANNED) walks
        as the walk-in does (walk_in_stop): attacked, the pets guard until it's over (walk_in_fight); a pet
        lagging, a wait for it (wait_pets; once it doesn't come, the walk goes on and waits beside the gate).
        From a hostile player (severity urgent), as Logan fled reds three times (20261013_173449,
        20261014_170144, _173858): flee_out, away from them first when the way passes them, Adventurer's Rope
        jumps whenever one is worth it (rope_jump).
        -> {ok, from, to, dest, elapsed_s[, error]}."""
        gate = tuple(self.args.leave_gate)
        urgent = severity not in PLANNED
        flee = severity == "urgent"
        self.left_level = False
        if self.args.leave_via:
            try:
                self.leave_up(severity)
            except Abort as e:
                self.left_level = False
                return {"ok": False, "from": list(self.pos(self.link.state())[:2]), "error": str(e),
                        "dest": self.args.gate_dest}
        self.doing("leave", f"Walking to the moongate at {gate[0]},{gate[1]}", gate)
        self.walking_out, self.pk_flight, self.walk_out_fought = True, flee, severity in PLANNED
        self.coming_seen = {}
        t_walk = time.monotonic()
        jump = {"last": tuple(self.pos(self.link.state())[:2]), "through": False}
        dash = False                    # attacked near the gate on a planned walk out: through it at once

        def through(s) -> bool:
            """From within 2 of the gate to more than GATE_THROUGH_R from it between two reads: through it (a
            step moves one tile). Not the distance alone: a leave from deep in the level starts farther than
            that (live 18:28:24, the hits leave at (5261, 652), 58 tiles off, read as through at once and
            recalled in the dungeon until the escape budget ran out)."""
            here = tuple(self.pos(s)[:2])
            jump["through"] |= cheb(jump["last"], gate) <= 2 and cheb(here, gate) > GATE_THROUGH_R
            jump["last"] = here
            return jump["through"]
        try:
            if self.team is not None and severity in PLANNED and self.left_level and self.args.fight_via:
                # up through --leave-via onto a level the walk in fights through (--fight-via): the walk to the gate is
                # fought the same way (user 2026-10-26: "in TT we'll always have to fight in 2"; simulator the same
                # day, level 3: two of nine deaths on the time-up walk across level 2 to its gate)
                path, _ = self.mover.plan(self.link.state(), nav.within(gate, 0), mobiles=False)
                if path and len(path) >= 2:
                    self.walk_in_fought("to the moongate", tuple(path[-2]))
            if self.team is not None:
                self.team.follow(severity, wait=flee)
                self.walk_hits, self.walk_hurt_t = None, -1e9
                path, _ = self.mover.plan(self.link.state(), nav.within(gate, 0), mobiles=False)
                near = tuple(path[-2]) if path and len(path) >= 2 else None
                lag = {"wait": severity in PLANNED}

                def stop(s):
                    if through(s):
                        return "through the gate"
                    if severity not in PLANNED:
                        return self.leave_called_off(s, severity)
                    if self.walk_hurt(s) or self.attackers(s):
                        return "under attack"
                    if who := self.coming(s):
                        return f"coming: {who}"
                    return self.pets_lagging(s) if lag["wait"] else None
                if flee:
                    if near is not None:
                        self.flee_out(gate, near, through)
                else:
                    for _ in range(GATE_LEGS_MAX):
                        if near is None or cheb(self.pos(self.link.state()), gate) <= 1:
                            break
                        why = self.mover.walk_to(lambda: near, 0, "to the moongate", urgent=urgent,
                                                 max_moves=max(250, 3 * self.out_steps), stop=stop)
                        if why == "through the gate" or through(self.link.state()):
                            break
                        if why and severity in PLANNED:
                            coming = why[len("coming: "):] if why.startswith("coming: ") else None
                            if (why == "under attack" or coming) \
                                    and (d := cheb(self.pos(self.link.state()), gate)) <= GATE_DASH_R:
                                # live 2026-10-17 19:04:15: attacked 2 tiles from the gate, the pets guarded and Logan
                                # stood in an apparition's hits there, dead 3.5 s later (user: "We were at the gate
                                # and Logan just stood there dying instead of going through")
                                log(f"to the moongate: {f'{coming} coming' if coming else 'attacked'} {d} tiles from "
                                    f"it; through it now")
                                dash = True
                                break
                            if why == "under attack" or coming:
                                self.walk_in_fight("to the moongate", "out", coming=coming)
                            elif not self.wait_pets("to the moongate", None, required=False):
                                lag["wait"] = False
                            continue
                        if why:
                            return {"ok": False, "called_off": why, "from": list(self.pos(self.link.state())[:2]),
                                    "dest": self.args.gate_dest}
                        break
                if (out := self.through_gate(gate, t_walk, through)) is not None:
                    return out
                if (why := self.leave_called_off(self.link.state(), severity)):
                    return {"ok": False, "called_off": why, "from": list(self.pos(self.link.state())[:2]),
                            "dest": self.args.gate_dest}
                me = self.link.state()["world"]["self"]
                hurt = severity not in PLANNED and me.get("hits") is not None and self.frac(me) < self.args.heal_at
                if not flee:          # fleeing: "All Follow Me" went out at the start of the run here
                    self.team.follow(severity, wait=not (hurt or dash))   # hurt or attacked: through now, pets or not
            fast = urgent or dash
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
                self.human.wait("gate_macro")         # the user's gate macro, planned leave or not
                for _ in range(GATE_CLICK_TRIES):
                    # an item use just before (the guards' vet supplies on the run, a potion) makes the server answer
                    # 500119 "You must wait to perform another action." and send no menu: again once it's over (live
                    # 2026-10-19 10:19:32.55 the vet, 32.75 the gate refused, the run aborted beside it, Logan dying)
                    mark = len(self.link.events)
                    self.link.act(actions.dclick(serial))
                    self.wait_for(lambda s: any(e.get("ev") in ("gump_open", "cliloc") for e in self.link.events[mark:]),
                                  GATE_CLICK_WAIT_S)
                    if not any(e.get("ev") == "cliloc" and e.get("cliloc") == tamer.CLILOC_ACTION_WAIT
                               for e in self.link.events[mark:]):
                        break
                    log("the moongate: 'You must wait to perform another action.'; clicking it again")
                    time.sleep(tamer.USE_RETRY_S)
            else:
                self.mover.walk_to(lambda: gate, 0, "onto the moongate", gate=gate, urgent=fast,
                                   max_moves=max(250, 3 * self.out_steps))
            # a leave to survive: the gate's menu at the user's macro pace (live 20261015_210709 21:57:21-25.4 the red's
            # flight took 4.2 s from the gate's tile to Totem: a second "All Follow Me", the reader's pauses; the user:
            # "My macro gets me out basically instantly")
            return moongate.travel(self.link, self.human, self.args.gate_dest, since=mark, log=log, urgent=True)
        except (Abort, moongate.GateError) as e:
            if (out := self.through_gate(gate, t_walk, through)) is not None:
                return out
            return {"ok": False, "from": list(self.pos(self.link.state())[:2]), "error": str(e),
                    "dest": self.args.gate_dest}
        finally:
            self.walking_out = self.pk_flight = self.left_level = self.walk_out_fought = False

    def leave_up(self, severity: str):
        """--leave-via: the leave gate a level or more above the hunt's (Tidal Tomb's level 3, its crimson gate on level
        2; user 2026-10-25: "We can leave lvl 3 by going back to the crimson gate on lvl 2, as I did"; live 11:35:19
        Logan stepped S onto (7161, 2221) and walked to the gate at (6897, 1992)). While the walk rules reach no route
        to the gate from here (the planner never routes over a teleporter), onto the --leave-via teleporter still ahead
        that they reach first (escape_teleporter), and the rest in order: planned, as the walk in goes (walk_in_legs:
        the pets on 'All Follow Me', a fight or a lagging pet stops it); a hurt leave or a hostile player, at flight
        pace (walk_in_rush). Abort when neither the gate nor a --leave-via teleporter is in reach."""
        gate = tuple(self.args.leave_gate)
        st = self.link.state()
        if self.mover.plan(st, nav.within(gate, 0), mobiles=False)[0]:
            return
        tiles = [tuple(t) for t in self.args.leave_via]
        k = self.escape_teleporter(st, tiles)
        if k is None:
            raise Abort(f"to the moongate at {gate[0]},{gate[1]}: no route to it nor onto a --leave-via teleporter "
                        f"from {tuple(self.pos(st)[:2])}")
        log(f"to the moongate at {gate[0]},{gate[1]}: a level up, through the teleporters from {tiles[k][0]},{tiles[k][1]}")
        if severity in PLANNED:
            self.walk_in_legs(tiles[k:], kind="leave")
        else:
            self.walk_in_rush(tiles[k:], "Leaving")
            self.fleeing = True                      # walk_in_rush turns the threat checks back on at its end
        self.left_level = True                       # off the hunt level: a healed leave goes on to the gate
        if not self.mover.plan(self.link.state(), nav.within(gate, 0), mobiles=False)[0]:
            raise Abort(f"to the moongate at {gate[0]},{gate[1]}: no route to it from "
                        f"{tuple(self.pos(self.link.state())[:2])} after the --leave-via teleporters")

    def flee_out(self, gate, near, through, choose=None):
        """The flight from hostile players to beside the leave gate (`near`; leave_by_gate, severity urgent): their
        danger zones on the Mover (hostile_zones: the routes bend round them), a look at the way every
        FLEE_LEG_STEPS while any is known (hostile_spots), and at once when one we didn't know of comes into view.
        When the route passes one (passes_hostile), a leg away from them first (flee_tile), from far enough a look
        again after FLEE_HOLD_S, and only cornered, or after FLEE_MAX_S of it, the way past them as the zones bend
        it. A rope jump whenever one is worth it, never landing nearer them (rope_jump). Live 2026-10-21 10:35:05-13
        the only way from Maus 3's east wing ran up the corridor two reds came down: the rope landed Logan beside
        one and the walk went on past them; 17:37:13-22 the red of the leave was out of view at the flight's start
        and two came back along the gate's way while the walk ran on toward them with no look. `choose` (the walk in's
        gold moongates, gold_gate_way): at each leg while one is known, another gate whose way passes none of them
        when this one's does (live 2026-10-22 Botan ran from (5610, 677) to beside the gold gate at (5588, 717))."""
        t_end = time.monotonic() + FLEE_MAX_S
        leg = {"start": 0, "known": set(), "past": False}
        said, failed = [None], set()

        def stop(s):
            if through(s):
                return "through the gate"
            spots = self.hostile_spots(s)
            self.hostile_zones(spots)
            if (not leg["past"] or self.ranged_fight(s)) and self.rope_worth(s):
                return "a rope is worth it"
            if spots.keys() - leg["known"]:
                return "a hostile player in view"
            return "a look again" if spots and self.mover.steps - leg["start"] >= FLEE_LEG_STEPS else None

        def say(line):
            if line != said[0]:
                said[0] = line
                log(line)
        near_fails = 0
        try:
            for _ in range(FLEE_LEGS_MAX):
                st = self.link.state()
                here = tuple(self.pos(st)[:2])
                if cheb(here, gate) <= 1 or through(st):
                    return
                spots = self.hostile_spots(st)
                self.hostile_zones(spots)
                if spots and choose is not None and (alt := choose(st)) is not None and alt[0] != gate and alt[2]:
                    path, _ = self.mover.plan(st, nav.within(near, 0), mobiles=False)
                    if self.passes_hostile(path, spots, here) is not None:
                        say(f"to the moongate: the way to {gate} passes them; to the one at {alt[0]} instead")
                        gate, near = alt[0], alt[1]
                goal, label = near, "to the moongate"
                leg["past"] = False
                if spots:
                    path, _ = self.mover.plan(st, nav.within(near, 0), mobiles=False)
                    s = self.passes_hostile(path, spots, here)
                    if s is not None:
                        xy, fleeing = spots[s], time.monotonic() < t_end
                        who = self.hostile_seen[s][2] if s in self.hostile_seen else f"0x{s:X}"
                        tile = self.flee_tile(st, spots, failed) if fleeing else None
                        if tile is not None and self.dead_end(st, tile, near, spots):
                            # no rope from melee ones: it lands only farther off, back into the dead end (fight2001);
                            # from ranged ones it may land along the way, past them (rope_jump, ranged_fight)
                            leg["past"] = True
                            say(f"to the moongate: the way passes {who} at {xy} ({cheb(here, xy)} tiles off) and every "
                                f"way from here does (a dead end): past them at once, round them as far as the way "
                                f"allows")
                        elif tile is not None:
                            goal, label = tile, "away from them"
                            say(f"to the moongate: the way passes {who} at {xy} ({cheb(here, xy)} tiles off): away "
                                f"from them first, toward {tile}")
                        elif fleeing and min(cheb(here, p) for p in spots.values()) >= FLEE_FAR_R:
                            say(f"to the moongate: the way passes {who} at {xy}; {FLEE_FAR_R}+ tiles from them here, "
                                f"looking again")
                            time.sleep(FLEE_HOLD_S)
                            continue
                        else:
                            say(f"to the moongate: no way round {who} at {xy} ({cheb(here, xy)} tiles off) "
                                f"{'left' if fleeing else f'after {FLEE_MAX_S:.0f} s'}: past them, round them as "
                                f"far as the way allows")
                if (not leg["past"] or self.ranged_fight(st)) and self.rope_worth(st) and self.rope_jump(goal, spots):
                    continue
                leg["start"], leg["known"] = self.mover.steps, set(spots)
                try:
                    why = self.mover.walk_to(lambda: goal, 0, label, urgent=True,
                                             max_moves=max(250, 3 * self.out_steps), stop=stop)
                except WalkFailed as e:
                    if goal == near:
                        # live 2026-10-22 22:03:06: "no route from (5223, 657)" on a stair with Flat Lander on it ended
                        # the run there, Logan alive at 50/100 and no potion or vet more; the refused steps expire
                        near_fails += 1
                        if near_fails > FLEE_NEAR_FAILS:
                            raise
                        log(f"{e}; looking again ({near_fails} of {FLEE_NEAR_FAILS})")
                        time.sleep(FLEE_HOLD_S)
                        continue
                    log(f"away from them: {e}")
                    failed.add(goal)
                    continue
                if why == "through the gate" or (not why and goal == near):
                    return
        finally:
            self.hostile_zones({})

    def through_gate(self, gate, t_walk: float, through) -> dict | None:
        """The walk to the leave gate went through it (`through`: a jump from beside it to more than
        GATE_THROUGH_R away): moongate.travel's success, else None. Live 20261017 16:38:56.8 the hurt walk
        stepped onto the gate's tile (5203, 703) and came out at Totem; the walk's stop then read "healed to
        93/100" there, called the leave off, and the run went back to hunting at Totem, the pets in the
        Mausoleum."""
        st = self.link.state()
        here = self.pos(st)
        if not through(st):
            return None
        log(f"to the moongate: through it on the way, at {tuple(here[:2])}")
        return {"ok": True, "from": list(gate), "to": list(here[:3]), "dest": self.args.gate_dest,
                "elapsed_s": round(time.monotonic() - t_walk, 2), "walked_through": True}

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
            self.disengage("fled" if severity not in PLANNED else "ended")
            self.doing("leave", f"Leaving: {why}", tuple(self.args.leave_gate) if self.args.leave_gate
                       else self.recall_spot)
            if severity in PLANNED and self.war_mode(st, False):
                self.human.wait("hotkey")
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
                        # the pets on "All Follow Me" since the leave fight nothing: guard again at once, past the
                        # guard's 8 s (live hunt-20261021-091743-38b6 09:49:34 called off, the countess on us, no
                        # "All Guard Me" until her kill at 09:49:53; user 2026-10-21)
                        self.team.guard_t = -1e9
                        self.team.guard()
                        self.team.rejoin(self.state())
                    return "called off"
                if gate["ok"] and self.team is not None:
                    self.team.landed(self.state(), "gate")
            res = self.recall_home(severity not in PLANNED) if gate is None or gate["ok"] else \
                {"ok": False, "failure": f"moongate: {gate.get('error')}"}
        finally:
            self.fleeing = False
        data.update(cursor_cancelled=cancelled,
                    recall={k: res.get(k) for k in ("ok", "method", "from", "to", "elapsed_s", "attempts",
                                                    "failure", "refused", "walked_first")})
        if res["ok"]:
            data["out_s"] = round(time.monotonic() - t_fire, 1)     # the way out: the return's T (hunt_return)
        if severity not in PLANNED:
            self.memory.juncture("hunt", "threat", f"Leaving the hunt by recall: {why}"
                                 + ("" if res["ok"] else f" (recall failed: {res['failure']})"), severity, data)
        self.memory.job_event("hunt", "leave", data, **self._where(st))
        self.count("leaves")
        if self.crawl is not None:
            self.crawl.on_leave(pos, severity not in PLANNED)
        if severity == "urgent":
            self.pk_left_t = time.monotonic()
        self.end_visit(why)
        if not res["ok"]:
            # dead on the way out: the death juncture first (live 2026-10-22 22:03:06 "recall home failed (moongate: to
            # the moongate: no route from (5223, 657))" with Logan dead beside Flat Lander and no `death` posted)
            st = self.link.state()
            if threats.is_dead(st) or st["world"]["self"].get("dead"):
                self.died(st, f"ghost body (leaving: {why})")
            raise Abort(f"recall home failed ({res['failure']}) after: {why}")
        self.home_landed(res)

    def home_landed(self, res: dict | None):
        """Home after a recall (`res`: its result; None: we were home already, --go-home): the weapon back in hand,
        the pets' landing check, then the bank (--bank-gold) and the drop-off (--drop-off)."""
        if res is not None:
            log(f"recalled home to {tuple(res['to'])} ({res['method']}, {res['elapsed_s']} s)")
            self.rearm(self.state())            # a recall knocks an arcane staff into the pack
            if self.team is not None:
                self.team.landed(self.state(), "home")
        if self.args.bank_gold:
            self.bank()
        if self.args.drop_off:
            self.drop_off()

    def drop_off(self):
        """--drop-off, home after a recall (Logan Wolf's default rune lands in the house): at home (home.at_home)
        walk to the homes.json `landing` tile and run the drop-off there (dropoff.run: the user's Razor 'New Drop
        Off', then homes.json `dropoff.to_chest` items into the chest). The run goes back in from the house. A
        drop-off that can't run (not at home, an item out of reach, a container that won't open) ends the run there
        with the load (Abort: task_failed at home, a `dropoff` job event either way): going back in with it carries
        home's whole take into the dungeon again (live 2026-10-21 22:32:36-22:32:40: 25.5k gp back to Maus Near 2
        after "a treasure map tome … isn't in view within 2 tiles"; the user paused the gate and took him home)."""
        st = self.state()
        home = home_mod.for_character((st["world"].get("self") or {}).get("name"))
        if not home or not home.get("dropoff"):
            return
        before, t0 = hunt_return.carried(st, self.prices), time.monotonic()
        self.doing("store", "Dropping off the loot at home")
        try:
            pos = self.pos(st)
            facet = (st["world"].get("self") or {}).get("map")
            if not home_mod.at_home(pos, facet, home):
                raise dropoff.DropoffError(f"not at home ({tuple(pos[:2])} facet {facet}; the house is at "
                                           f"{tuple(home['landing'][:2])} facet {home['facet']})")
            landing = tuple(home["landing"][:2])
            if tuple(self.pos(self.state())[:2]) != landing:
                self.mover.walk_to(lambda: landing, 0, "to the home landing tile")
            out = dropoff.run(escape_mod.LinkIO(self.link), self.human, home["dropoff"])
        except (dropoff.DropoffError, Abort) as e:
            self.memory.job_event("hunt", "dropoff", {"ok": False, "error": str(e), "carried": before,
                                                      "elapsed_s": round(time.monotonic() - t0, 1)},
                                  **self._where(self.state()))
            raise Abort(f"drop-off failed at home with {before['total']} gp carried: {e}") from e
        after = hunt_return.carried(self.state(), self.prices)
        log(f"drop-off: {before['total']} gp carried -> {after['total']} (" + "; ".join(
            f"{s['name'].split(' [')[0]}: {' / '.join(s['lines']) or '-'}" for s in out["steps"]) + ")"
            + (f"; into the chest: {', '.join(str(c['name']) for c in out['chest'])}" if out["chest"] else ""))
        self.memory.job_event("hunt", "dropoff", {"ok": out["ok"], "error": out.get("error"), "carried": before,
                                                  "left": after, "chest": out["chest"],
                                                  "elapsed_s": round(time.monotonic() - t0, 1)},
                              **self._where(self.state()))

    def repair_gear(self):
        """--repair, before each recall in: at home (home.at_home) with a homes.json `repair_bench`, double-click it
        (repair.run; from the landing tile when the bench is out of reach), which repairs every worn item and every
        item in the pack: the armor, the instruments' uses (user 2026-10-27: "double click the repair bench before
        each trip into a dungeon to repair our equipment (instrument and armor)"). Away from home: no bench, a log
        line. An empty bench: a low_supplies juncture (item `repair bench charges`) and the run ends at home, like
        the other pre-trip checks (an instrument at 0 uses is lost: live 2026-10-25 17:24:23 the only one broke on
        Maus 3). A bench that doesn't answer ends the run there too. A `repair` job event either way."""
        if not self.args.repair:
            return
        st = self.state()
        home = home_mod.for_character((st["world"].get("self") or {}).get("name"))
        if not home or not home.get("repair_bench"):
            return
        bench = home["repair_bench"]
        pos, facet = self.pos(st), (st["world"].get("self") or {}).get("map")
        if not home_mod.at_home(pos, facet, home):
            log(f"repair: not at home ({tuple(pos[:2])} facet {facet}): going in without the house's repair bench")
            return
        self.doing("equip", "Repairing the gear at the repair bench")
        t0 = time.monotonic()
        try:
            if max(abs(bench["pos"][0] - pos[0]), abs(bench["pos"][1] - pos[1])) > repair.RANGE:
                landing = tuple(home["landing"][:2])
                self.mover.walk_to(lambda: landing, 0, "to the home landing tile")
            out = repair.run(escape_mod.LinkIO(self.link), self.human, bench)
        except (repair.RepairError, Abort) as e:
            self.memory.job_event("hunt", "repair", {"ok": False, "error": str(e),
                                                     "elapsed_s": round(time.monotonic() - t0, 1)},
                                  **self._where(self.state()))
            raise Abort(f"repair bench: {e}; not going in (--no-repair to go without)") from e
        self.memory.job_event("hunt", "repair", {"ok": out["ok"], "repaired": out["repaired"], "line": out["line"],
                                                 "elapsed_s": round(time.monotonic() - t0, 1)},
                              **self._where(self.state()))
        log(f"repair bench: {out['line']}")
        if out["empty"]:
            self.memory.juncture("hunt", "low_supplies", "The repair bench is out of charges: not going in",
                                 "attention", {"item": "repair bench charges", "bench": bench["serial"]})
            raise Abort("the repair bench is out of charges (a repair kit on it adds uses): not going in "
                        "(--no-repair to go without)")

    def return_reason(self, st):
        """(why, "return") when the carried value reaches V* (hunt_return: r, h and the trip from this place's visits,
        --return-at auto) or --return-at N gold, or the pack is within LOOT_WEIGHT_MARGIN of full; else None. Only
        between fights (tick)."""
        if self.args.leave_recall is None or self.args.return_at == "off":
            return None
        me = st["world"]["self"]
        wmax = weight_max(me)
        if wmax and (me.get("weight") or 0) >= wmax - LOOT_WEIGHT_MARGIN:
            return f"taking the loot home: the pack is full ({me.get('weight')}/{wmax} stones)", "return"
        c = hunt_return.carried(st, self.prices)
        m = self.return_model or {}
        target = m.get("v_star") if self.args.return_at == "auto" else int(self.args.return_at)
        # what this visit added: a load the drop-off couldn't take (no room, an error, --no-drop-off) would send us
        # home again at once, every visit (simulator: the 16k the scenario starts with)
        if not target or c["total"] - self.return_floor < target:
            return None
        how = (f"V* {target} from {m['r']:.0f} gold/min, a death every {1 / m['h']:.0f} min on the level, "
               f"{m['trip_min']:g} min out and back" if self.args.return_at == "auto" else f"--return-at {target}")
        floor = f", {self.return_floor} of it from before this visit" if self.return_floor else ""
        return (f"taking the loot home: {c['total']} gp carried (gold {c['gold']}, items {c['items_gp']}{floor}) "
                f">= {how}", "return")

    def enter_by_recall(self) -> bool:
        """recall_in; a hostile player on the walk in that sent us home by a gold moongate (WalkInFled): back in
        once ready_to_go_back allows. False when the run ends at home meanwhile."""
        while True:
            try:
                self.recall_in()
                return True
            except WalkInFled as e:
                if not self.ready_to_go_back(e.why, "urgent"):
                    return False

    def recall_in(self):
        """Recall to --enter-recall's rune --enter-rune (its default without one), then the walk in (--enter-via).
        The first arrival is the hunt: --spot, --fight-spot and --recall-spot default to it."""
        book, rune = self.args.enter_recall, self.args.enter_rune
        self.repair_gear()
        self.check_aspect()
        self.check_instrument()
        self.check_charges()
        if self.team is not None:
            self.team.follow()
        self.doing("enter", f"Recalling to {rune or 'the default rune'}")
        self.in_t = time.monotonic()        # the way in: the return's T (hunt_return), the visit row's in_s
        self.human.wait("use")
        res = self.recall(book, rune, False, "in")
        if not res["ok"]:
            raise Abort(f"recall to {rune or 'the default rune'} failed: {res['failure']} {res.get('error') or ''}")
        log(f"recalled to {rune or 'the default rune'} at {tuple(self.pos(self.link.state())[:2])} "
            f"({res['method']}, {res['elapsed_s']} s)")
        self.attack_scan = len(self.link.events)     # "... is attacking you!" from before the recall is old news
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

    def check_instrument(self):
        """--pets with a bard skill or song on: no recall in without an instrument in the pack (a low_supplies
        juncture, the run ends at home). Live 2026-10-25 17:24:23 Logan's only one, a durable psaltery, broke at a
        Discordance on Maus 3 and the visit went on with no bard skill, area peace in a jam included (user's play
        rules); the Seer: "low_supplies for instrument only fires once the run is inside"."""
        if self.team is None or not self.team.bards() or self.team.instrument(self.state()) is not None:
            return
        self.memory.juncture("hunt", "low_supplies", "No instrument in the pack: not going in", "attention",
                             {"item": "instrument"})
        raise Abort("no instrument in the pack: not going in (the bard skills and songs need one; "
                    "--no-discord --no-peace --no-provoke --no-songs to go without)")

    def check_aspect(self):
        """--armor-aspect: no recall in without the armor aspect on (user 2026-10-25: "make the runner check aspect
        before a trip and refuse to leave home without it"). The Aspect Mastery menu read ("[aspect", one gump:
        aspects.read) for the essence and the armor section's aspect hue, then the worn pieces (aspects.armor_check).
        Off: a low_supplies juncture (item `armor aspect`) and the run ends at home. Live 2026-10-25 21:16:06 the
        Arcane Essence ran out mid-run ("Your aspect items revert to normal"), Logan's platemail went back to its own
        hue, and the runs went on for two hours without the Command aspect's armor and pet bonuses."""
        if not self.args.armor_aspect:
            return
        self.human.wait("read")
        try:
            menu = aspects.read(escape_mod.LinkIO(self.link), self.human)
        except aspects.AspectError as e:
            raise Abort(f"the Aspect Mastery menu can't be read: {e}; not going in (--no-armor-aspect to go without)")
        st = self.state()
        res = aspects.armor_check(menu, st["world"], self.me(st))
        if res["ok"]:
            log(f"armor aspect: {res['aspect']} on {', '.join(res['aspected'])}; {res['charges']} Arcane Essence")
            return
        self.memory.juncture("hunt", "low_supplies", f"Armor aspect off: {res['why']}; not going in", "attention",
                             {"item": "armor aspect", "aspect": res["aspect"], "charges": res["charges"],
                              "plain": res["plain"]})
        raise Abort(f"armor aspect off: {res['why']}; not going in (--no-armor-aspect to go without)")

    def check_charges(self):
        """No recall in without the way home: --enter-recall's and --leave-recall's books read (escape.check_ready, one
        glance each) and a charge for every recall the visit needs (in, and home from the leave), unless Recall can
        be cast (mana and reagents). Short: a low_supplies juncture (item `recall charges`), the run ends at home.
        Live 2026-10-25 17:53:38 the recall home took the tome's last charge and the recall in failed on the spell
        ("reagents") at home; one charge fewer and the home recall would have failed at Totem with the loot."""
        need = {}
        for book in (self.args.enter_recall, self.args.leave_recall):
            if book is not None:
                need[book] = need.get(book, 0) + 1
        for book, n in need.items():
            self.human.wait("read")
            try:
                info = escape_mod.check_ready(escape_mod.LinkIO(self.link), book)
            except escape_mod.RecallError as e:
                raise Abort(f"the book 0x{book:08X} can't be read: {e}; not going in")
            if info["can_cast"] or info["charges"] >= n:
                log(f"{info['kind']} 0x{book:08X}: {info['charges']} recall charge(s)"
                    + (", Recall castable" if info["can_cast"] else ""))
                continue
            self.memory.juncture("hunt", "low_supplies",
                                 f"{info['charges']} recall charge(s) on 0x{book:08X}, {n} needed: not going in",
                                 "attention", {"item": "recall charges", "book": key_of(book),
                                               "charges": info["charges"], "needed": n})
            raise Abort(f"{info['kind']} 0x{book:08X} has {info['charges']} recall charge(s) and Recall can't be "
                        f"cast (mana/reagents): not going in (the way in and home need {n})")

    def resume_inside(self, st):
        """Started inside the dungeon (no recall from here: escape.recall_forbidden) and away from --spot. Live
        2026-10-18 21:29:58: a restart on Maus 3 48 tiles from --spot tried to recall in ("unusable no recall from
        (5209, 689)"). The hunt is the last --enter-via teleporter's known arrival unless --spot says otherwise;
        when the crawl's floor from here holds it, we're on the hunt level: hunt from here. Else the walk in from
        the first --enter-via teleporter we can reach."""
        facet = st["world"]["self"].get("map") or 0
        tiles = [tuple(t) for t in (self.args.enter_via or [])]
        dest = self.memory.teleporters(facet).get(tiles[-1][:2]) if tiles else None
        if self.spot is None and dest is not None:
            arrival = tuple(dest[1:3])
            self.spot, self.fight_spot = arrival, self.fight_spot or arrival
            self.recall_spot = self.recall_spot or arrival
        if self.spot is not None and (self.args.crawl or self.args.crawl_loop):
            c = crawl.Crawl(self, crawl.load_prior(self.memory.con, self.spot, self.args.crawl_depth_risk))
            try:
                c.setup(st)
            except ValueError as e:
                log(f"crawl: {e}")
            else:
                near = c.floor.nearest(self.spot) if c.floor is not None else None
                if near is not None and cheb(near, self.spot) <= WALK_IN_ARRIVED_R:
                    self.crawl = c
                    log(f"started inside, on the hunt level (the crawl floor from {self.pos(st)[:2]} holds "
                        f"{self.spot}): hunting from here")
                    return
        for k, tile in enumerate(tiles):
            if self.mover.plan(st, nav.within(tile[:2], 0), mobiles=False)[0]:
                log(f"started inside the dungeon at {self.pos(st)[:2]}: the walk in from the teleporter at "
                    f"{tile[0]},{tile[1]}")
                self.walk_in(tiles[k:])
                if self.spot is None:
                    here = tuple(self.pos(self.link.state())[:2])
                    self.spot, self.fight_spot = here, self.fight_spot or here
                    self.recall_spot = self.recall_spot or here
                return
        raise Abort(f"started inside the dungeon at {self.pos(st)[:2]}: not on the hunt level and no --enter-via "
                    f"teleporter in reach (no recall from here)")

    def walk_in(self, tiles):
        """After the recall in (a dungeon lets us recall only beside a gold moongate: user 2026-10-13):
        onto each --enter-via teleporter in order, the pets on 'All Follow Me' the whole way, said once
        before the first leg, as the user does (capture 20261013_204518: "All Guard Me" before the recall,
        "All Follow Me" 8 s after landing, four teleporters, the next "All Guard Me" on the fight level;
        live 22:20:56 a guard said at the landing set them fighting there while Logan walked off alone).
        A pet falling behind (WALK_IN_LAG_R, or out of view) stops the walk: 'All Follow Me' again and
        WALK_IN_PETS_S for it to come; something attacking us or our hits falling stops it too, and the
        pets guard until it's over (walk_in_fight), unless we are within TELE_DASH_R route steps of the
        teleporter: then on and through it (walk_in_dash). Before each step onto a teleporter, every pet within
        reach of it: caution only (user 2026-10-20: on Outlands pets on 'All Follow Me' come through with us while
        in view). The guard comes on the fight level (landed, then care).
        --songs: a song due before each leg (live 20261013_204518: all three went up on the first level
        and on the way in).
        A hostile player on the way (look, as on the hunt level: player_threat) ends the walk: walk_in_threat."""
        self.walking_in, self.walk_in_i = True, 0
        try:
            self.walk_in_legs(tiles)
        except PlayerThreat as e:
            self.walking_in = False
            self.walk_in_threat(e.why, tiles)
        finally:
            self.walking_in = False

    def walk_in_threat(self, why, tiles):
        """A hostile player on the walk in (user 2026-10-22, after Botan's kill on the second level, 18:53:48): a gold
        moongate within GOLD_GATE_STEPS (the first level's) whose way doesn't pass them if one does: the flight there
        and the recall home (walk_in_leave, WalkInFled); none (past the first teleporter): on through the rest at
        flight pace (walk_in_rush), then on the hunt level the leave by the gate at once (to_spot)."""
        st = self.link.state()
        log(f"walk in: {why}")
        way = self.gold_gate_way(st)
        if way is not None:
            self.walk_in_leave(why, *way[:2])
        log(f"walk in: no gold moongate within {GOLD_GATE_STEPS} steps; on through the teleporters at flight pace, "
            f"then the leave gate")
        self.walk_in_rush(tiles[self.walk_in_i:])
        self.walk_in_red = f"{why}, on the walk in"

    def gold_gate_way(self, st, quiet: bool = False):
        """(gate, the tile beside it to run to, its way clear) of the gold moongate of the dungeon we stand in within
        GOLD_GATE_STEPS route steps (escape.gold_gates): one whose way passes no hostile player (passes_hostile)
        first, then the nearest; None without one. `quiet`: no log line (flee_out's look at every leg)."""
        here = tuple(self.pos(st)[:2])
        gates, _ = escape_mod.gold_gates(here, st["world"]["self"].get("map"))
        spots, best, seen = self.hostile_spots(st), None, []
        for g in gates:
            path, _ = self.mover.plan(st, nav.within(g, 1), mobiles=False)
            if not path or len(path) - 1 > GOLD_GATE_STEPS:
                seen.append(f"{g} {'no route' if not path else f'{len(path) - 1} steps'}")
                continue
            passes = self.passes_hostile(path, spots, here)
            seen.append(f"{g} {len(path) - 1} steps" + (" past them" if passes is not None else ""))
            key = (passes is not None, len(path))
            if best is None or key < best[0]:
                best = (key, g, tuple(path[-1]))
        if gates and not quiet:
            log(f"walk in: gold moongates from {here}: {'; '.join(seen)}")
        return None if best is None else (best[1], best[2], not best[0][0])

    def walk_in_leave(self, why, gate, near):
        """From a hostile player on the walk in's first level: "All Follow Me" (no wait), the flight to beside the
        gold moongate `gate` (flee_out: away from them first when the way passes them, rope jumps, potions and vet
        supplies on the run, no bard skill), the recall home from there (a dungeon allows it only beside a gold
        gate: escape.recall_forbidden), the `threat` juncture and `leave` event, the drop-off; then WalkInFled.
        A recall that doesn't land stops the run."""
        st = self.link.state()
        t_fire = time.monotonic()
        data = {"why": why, "hits": [st["world"]["self"].get("hits"), st["world"]["self"].get("hits_max")],
                "attackers": self.attackers(st), "visit": self.visit_n, "kills": self.totals["kills"],
                "walk_in": True, "gold_gate": list(gate)}
        log(f"LEAVING the walk in by the gold moongate at {gate}: {why}")
        self.fleeing = self.walking_out = self.pk_flight = True
        try:
            cancelled = self.drop_cursor()
            self.disengage("fled")
            self.doing("leave", f"Fleeing to the gold moongate at {gate[0]},{gate[1]}: {why}", gate)
            if self.team is not None:
                self.team.follow("urgent", wait=False)
            data["react_s"] = round(time.monotonic() - t_fire, 2)
            try:
                self.flee_out(gate, near, lambda s: False, choose=lambda s: self.gold_gate_way(s, quiet=True))
            except WalkFailed as e:
                log(f"to the gold moongate: {e}")
            res = self.recall(self.args.leave_recall, None, True, "home")
        finally:
            self.fleeing = self.walking_out = self.pk_flight = False
        data.update(cursor_cancelled=cancelled,
                    recall={k: res.get(k) for k in ("ok", "method", "from", "to", "elapsed_s", "attempts",
                                                    "failure")})
        if res["ok"]:
            data["out_s"] = round(time.monotonic() - t_fire, 1)
        self.memory.juncture("hunt", "threat", f"Leaving the walk in by a gold moongate's recall: {why}"
                             + ("" if res["ok"] else f" (recall failed: {res['failure']})"), "urgent", data)
        self.memory.job_event("hunt", "leave", data, **self._where(st))
        self.count("leaves")
        self.pk_left_t = time.monotonic()
        if not res["ok"]:
            raise Abort(f"recall home from the gold moongates' level failed ({res['failure']}) after: {why}")
        self.home_landed(res)
        raise WalkInFled(why)

    def walk_in_rush(self, tiles, what: str = "From a hostile player"):
        """From a hostile player past the walk in's first level (no gold moongate there; the user 2026-10-22: "Dash
        on to Maus 3, crimson gate"): the rest of the --enter-via teleporters at flight pace, "All Follow Me" with
        no wait for the pets, no stop for attackers, potions and vet supplies on the run (drink_on_the_run), no bard
        skill; the threat checks off meanwhile (fleeing). `what`: why, for the visualizer (escape_home's way out)."""
        self.fleeing = self.walking_out = self.pk_flight = True
        try:
            self.drop_cursor()
            self.disengage("fled")
            if self.team is not None:
                self.team.follow("urgent", wait=False)
            i = 0
            while (i := self.walk_in_through(tiles, i)) < len(tiles):
                tile = tiles[i]
                label = f"to the teleporter at {tile[0]},{tile[1]}"
                self.doing("leave", f"{what}: through the teleporter at {tile[0]},{tile[1]}", tile)
                path, _ = self.mover.plan(self.link.state(), nav.within(tile, 0), mobiles=False)
                near = tuple(path[-2]) if path and len(path) >= 2 else None
                if near is None:
                    raise Abort(f"{label}: no route onto it")
                self.mover.walk_to(lambda: near, 0, label, urgent=True)
                here = tuple(self.pos(self.link.state())[:2])
                self.teleport(nav.direction(here, tile), f"the teleporter at {tile[0]},{tile[1]}")
                if self.team is not None:
                    self.team.landed(self.state(), "in")
                i += 1
        finally:
            self.fleeing = self.walking_out = self.pk_flight = False

    # ------------------------------------------------------------ the escape home (an error inside, --go-home)
    def no_escape(self) -> str | None:
        """Why an error ends the run where we stand (on_error) instead of after the escape home, else None: the escape
        ran already (the error is its own), a death or a server restriction message (halt; the restriction's rule is
        stop, ANTICHEAT.md §8 rule 4), no --leave-recall to recall home with, dead, or not inside a dungeon that
        allows no recall where we stand (escape.recall_forbidden)."""
        if self.escape_tried:
            return "the escape home ran already"
        if self.halt is not None:
            return self.halt
        if self.args.leave_recall is None:
            return "no --leave-recall"
        try:
            st = self.link.state()
        except Exception as e:  # noqa: BLE001 - the proxy gone: nothing to escape with
            return f"no state ({e})"
        if threats.is_dead(st) or st["world"]["self"].get("dead"):
            return "dead"
        pos = st["movement"]["pos"]
        if pos is None or not escape_mod.recall_forbidden(pos, st["world"]["self"].get("map")):
            return "not inside a dungeon that forbids recall"
        return None

    def on_error(self, e: Exception) -> int:
        """The run ends on an error (an Abort or WalkFailed reaching the top, or a crash): exit code 1. Alive inside a
        dungeon that allows no recall (no_escape), the way out and home first (escape_home, then home_landed: the
        drop-off as after a leave): live 2026-10-24 the walk in's "no route from (5507, 691)" at 00:40:32 left Logan
        alive on Maus 2 with no runner, and a calamity killed him there at 01:10:48. An `escape` juncture says how it
        went (attention once home, urgent when he is still inside); the wrapper's task_failed follows with the last
        'ABORTED:' line, which names the error and the escape. A death, a pause of the agent gate (GatePaused) and
        `ctl stop` (KeyboardInterrupt) never escape (halt; main). A crash with no escape is raised again, as before."""
        what = str(e) if isinstance(e, Abort) else f"{type(e).__name__}: {e}"
        if self.no_escape() is not None:
            if not isinstance(e, Abort):
                raise e
            log(f"ABORTED: {what}")
            stop_intent(self, f"Stopped: {what}")
            return 1
        if not isinstance(e, Abort):
            traceback.print_exc(file=sys.stdout)
        here = tuple(self.pos(self.link.state())[:3])
        log(f"ERROR inside the dungeon at {here}: {what}; escaping home")
        try:
            res = self.escape_home(what)
        except Exception as x:  # noqa: BLE001 - the escape's own crash: reported, not escaped again
            traceback.print_exc(file=sys.stdout)
            res = {"ok": False, "failure": f"{type(x).__name__}: {x}", "from": list(here)}
        if res["ok"]:
            note = ""
            try:
                self.home_landed(res)
            except Abort as x:
                note = f"; at home: {x}"
            end = f"escaped home by {res['way']}{note}"
            log(f"escaped home after: {what} (by {res['way']}{note})")
            self.memory.juncture("hunt", "escape", f"Escaped home after an error inside the dungeon: {what} "
                                 f"(by {res['way']}{note})"[:300], "attention",
                                 {"error": what, "ok": True, "way": res["way"], "from": res["from"],
                                  "to": res.get("to"), "at_home": note[len("; at home: "):] or None})
        else:
            try:
                at = list(self.pos(self.link.state())[:3])
            except Exception:  # noqa: BLE001 - the proxy gone
                at = None
            end = f"escape failed: {res['failure']}"
            log(f"escape failed: {res['failure']}")
            self.memory.juncture("hunt", "escape", f"Escape home failed after an error inside the dungeon: {what}; "
                                 f"{res['failure']}; Logan still inside at {at}"[:300], "urgent",
                                 {"error": what, "ok": False, "failure": res["failure"], "from": res.get("from"),
                                  "at": at, "way": res.get("way")})
        log(f"ABORTED: {what} ({end})")
        stop_intent(self, f"Stopped: {what} ({end})")
        return 1

    def escape_home(self, why: str) -> dict:
        """Out of a dungeon that allows no recall where we stand, and the recall home (on_error, --go-home): the way out
        the level has (escape_once), looked for again from where a failed one left us, ESCAPE_TRIES ways at most. The
        threat checks off (fleeing) and the overall timeout too (escaping); a red on the way: the flights of the leaves
        (flee_out). An `escape` job event. -> the recall's result with `way`, `from`, `ways` (each try) and
        `escape_s`; `ok` False with `failure` when we are still inside."""
        self.escape_tried = self.escaping = True
        t0 = time.monotonic()
        st = self.link.state()
        start = list(self.pos(st)[:3])
        self.walking_in = False
        res, way, ways = {"ok": False, "failure": "not tried"}, None, []
        self.fleeing = True
        try:
            self.drop_cursor()
            self.disengage("ended")
            for n in range(ESCAPE_TRIES):
                if n:
                    log(f"escape: looking for the way out again in {ESCAPE_RETRY_S:g} s (try {n + 1} of {ESCAPE_TRIES})")
                    time.sleep(ESCAPE_RETRY_S)
                way, res = self.escape_once()
                ways.append({"way": way, "ok": res["ok"], "failure": res.get("failure")})
                if res["ok"] or self.halt is not None:
                    break
                log(f"escape: {way or 'no way out'}: {res.get('failure')}")
        finally:
            self.fleeing = False
        out = {**res, "why": why, "way": way, "from": start, "ways": ways,
               "escape_s": round(time.monotonic() - t0, 1)}
        st = self.link.state()
        self.memory.job_event("hunt", "escape", {k: out.get(k) for k in ("why", "ok", "way", "from", "to", "method",
                                                                          "failure", "ways", "escape_s")},
                              **self._where(st))
        return out

    def escape_once(self):
        """One way out from where we stand -> (way, the recall home's result). By evidence (docs/PLAN.md "Escape home
        after an error"): the planner never routes over a teleporter, so each level has the ways it can walk:
        - a recall allowed here (a try before got us out, or beside a gold moongate): the recall;
        - the first level, a gold moongate within GOLD_GATE_STEPS (gold_gate_way): the walk-in leave's flight to it,
          the pets' wait beside it, the recall home (escape_by_gold_gate);
        - the hunt level (a route to --leave-gate): the leave by the gate to --gate-dest, the recall from there
          (escape_by_leave_gate);
        - a hunt level below the gate's (Tidal Tomb's level 3): a route onto a --leave-via teleporter, the leave by the
          gate through them (leave_by_gate's leave_up);
        - a middle level (Maus 2): no route to a gold moongate (no teleporter back to the first level is known) but one
          to an --enter-via teleporter still ahead: on through them at flight pace (walk_in_rush, the nearest by route
          first), then the leave gate. That is the walk in's own way, the one walk_in_threat takes from a red there.
        An Abort on the way (no route, a teleporter that didn't take us, a death) is this try's failure."""
        way = None
        self.fleeing = True                          # a walk_in_rush of a try before turned the threat checks on again
        try:
            st = self.state()
            here, facet = tuple(self.pos(st)[:2]), st["world"]["self"].get("map")
            gate = tuple(self.args.leave_gate) if self.args.leave_gate else None
            if not escape_mod.recall_forbidden(here, facet):
                way = f"the recall from {here}"
                log(f"escape: a recall is allowed at {here}: home")
                if self.team is not None:
                    self.team.follow()
                return way, self.recall_home(True)
            if (g := self.gold_gate_way(st, quiet=True)) is not None:
                way = f"the gold moongate at {g[0][0]},{g[0][1]}"
                log(f"escape: {way} is within {GOLD_GATE_STEPS} steps: there, and the recall home")
                return way, self.escape_by_gold_gate(g[0], g[1])
            if gate is not None and self.mover.plan(st, nav.within(gate, 0), mobiles=False)[0]:
                way = f"the leave gate at {gate[0]},{gate[1]}"
                log(f"escape: {way} is in reach: through it to {self.args.gate_dest}, and the recall home")
                return way, self.escape_by_leave_gate()
            up = [tuple(t) for t in self.args.leave_via or []]
            if gate is not None and (k := self.escape_teleporter(st, up)) is not None:
                way = f"the --leave-via teleporters from {up[k][0]},{up[k][1]} and the leave gate at {gate[0]},{gate[1]}"
                log(f"escape: the leave gate is a level up: {way}, and the recall home")
                return way, self.escape_by_leave_gate()          # leave_by_gate's leave_up takes the teleporters
            tiles = [tuple(t) for t in self.args.enter_via or []]
            k = self.escape_teleporter(st, tiles)
            if k is not None and gate is not None:
                way = f"the teleporters from {tiles[k][0]},{tiles[k][1]} and the leave gate"
                log(f"escape: no gold moongate within {GOLD_GATE_STEPS} steps of {here} and no route to the leave "
                    f"gate: on through {way}, and the recall home")
                self.walk_in_rush(tiles[k:], "Escaping home")
                self.fleeing = True                  # walk_in_rush turns the threat checks back on at its end
                return way, self.escape_by_leave_gate()
            return None, {"ok": False, "failure": f"no way out from {here}: no gold moongate within {GOLD_GATE_STEPS} "
                                                  f"steps, " + ("no route to the leave gate or an --enter-via "
                                                                "teleporter" if gate else "no --leave-gate")}
        except Abort as e:
            return way, {"ok": False, "failure": str(e)}

    def escape_teleporter(self, st, tiles) -> int | None:
        """The index of the --enter-via teleporter still ahead (walk_in_through: past the ones whose known arrival we
        stand at) that the walk rules reach by the shortest route from here, else None. From (5507, 691) on Maus 2 the
        simulator routes 57 steps to 5522,637 and 417 round to 5507,701 (whose teleporters lead back to here)."""
        best = None
        for k in range(self.walk_in_through(tiles, 0), len(tiles)):
            path, _ = self.mover.plan(st, nav.within(tiles[k][:2], 0), mobiles=False)
            if path and (best is None or len(path) < best[1]):
                best = (k, len(path))
        return None if best is None else best[0]

    def escape_by_gold_gate(self, gate, near) -> dict:
        """The escape from the first level: "All Follow Me", the walk-in leave's flight to beside the gold moongate
        `gate` (flee_out: a red's danger zones and ropes when one shows up; another gold gate when his way passes it),
        'All Follow Me' again beside it so the pets come along, the recall home (allowed only there)."""
        self.doing("leave", f"Escaping home: to the gold moongate at {gate[0]},{gate[1]}", gate)
        if self.team is not None:
            self.team.follow(wait=False)
        self.walking_out = True
        try:
            self.flee_out(gate, near, lambda s: False, choose=lambda s: self.gold_gate_way(s, quiet=True))
        finally:
            self.walking_out = False
        if self.team is not None:
            self.team.follow()
        return self.recall(self.args.leave_recall, None, True, "home")

    def escape_by_leave_gate(self) -> dict:
        """The escape from the hunt level: the leave by --leave-gate (leave_by_gate: planned, the pets on 'All Follow
        Me' waited for and the attackers fought as on the time-up walk out; a red's flight when one is in view), then
        the recall home from --gate-dest."""
        severity = "urgent" if self.player_threat(self.state()) else "info"
        gate = self.leave_by_gate(severity)
        if not gate["ok"]:
            return {"ok": False, "failure": f"moongate: {gate.get('error') or gate.get('called_off') or 'not through'}"}
        if self.team is not None:
            self.team.landed(self.state(), "gate")
        return self.recall_home(True)

    def go_home(self):
        """--go-home (the Seer, after a run left Logan somewhere): no hunting. Inside a dungeon that allows no recall,
        the escape's way out and the recall home (escape_home); elsewhere the recall home with --leave-recall; at home
        already, neither. Then home as after a leave (home_landed: the bank, the drop-off). Abort when he isn't home."""
        st = self.setup(pets_required=False)
        here, facet = tuple(self.pos(st)[:2]), st["world"]["self"].get("map")
        house = home_mod.for_character(st["world"]["self"].get("name"))
        self.doing("leave", "Going home (--go-home)")
        if escape_mod.recall_forbidden(here, facet):
            log(f"--go-home: inside the dungeon at {here}: the way out, then home")
            res = self.escape_home("--go-home")
            if not res["ok"]:
                raise Abort(f"--go-home: escape failed: {res['failure']}")
        elif house and home_mod.at_home(here, facet, house):
            log(f"--go-home: at home already ({here})")
            res = None
        else:
            log(f"--go-home: at {here}: the recall home")
            if self.team is not None:
                self.team.follow()
            res = self.recall_home(True)
            if not res["ok"]:
                raise Abort(f"--go-home: recall home failed ({res['failure']})")
        self.escaping = True                         # no overall timeout at home either
        self.home_landed(res)
        log(f"--go-home: home at {tuple(self.pos(self.link.state())[:3])}")
        self.doing("done", "Home (--go-home)")

    def walk_in_legs(self, tiles, kind: str = "enter"):
        """walk_in's legs, one --enter-via teleporter each; `kind` "leave": the --leave-via ones up to the leave gate
        (leave_up), no song on the way."""
        self.walk_hits, self.walk_hurt_t = None, -1e9
        if self.team is not None:
            self.team.follow()
        i = 0
        while (i := self.walk_in_through(tiles, i)) < len(tiles):
            self.walk_in_i = i                  # the leg a hostile player cut short: walk_in_rush goes on from it
            tile = tiles[i]
            label = f"to the teleporter at {tile[0]},{tile[1]}"
            self.doing(kind, f"Walking to the teleporter at {tile[0]},{tile[1]}", tile)
            if self.team is not None and kind == "enter":
                self.team.song(self.state())
            try:
                # the tile the route onto it steps from: a teleporter on stairs is entered from one side only
                # (the Mausoleum's at 5554,700: from 5554,701 northwards, the step denied and then the move)
                path, _ = self.mover.plan(self.link.state(), nav.within(tile, 0), mobiles=False)
                near = tuple(path[-2]) if path and len(path) >= 2 else None
                if near is None:
                    raise Abort(f"{label}: no route onto it")
                dash = False
                if kind == "enter" and tuple(tile[:2]) in {tuple(t) for t in self.args.fight_via or []}:
                    self.walk_in_fought(label, near)
                while why := self.mover.walk_to(lambda: near, 0, label, stop=self.walk_in_stop):
                    if why == "under attack":
                        path, _ = self.mover.plan(self.link.state(), nav.within(tile, 0), mobiles=False)
                        if path and len(path) - 1 <= TELE_DASH_R:
                            log(f"{label}: attacked {len(path) - 1} steps from it; through it now")
                            self.walk_in_dash(label, near)
                            dash = True
                            break
                        self.walk_in_fight(label)
                    else:
                        self.wait_pets(label, None)
                if not dash and self.team is not None and not self.team.together(self.link.state(), at=tile[:2]):
                    self.wait_pets(label, tile[:2])
                here = tuple(self.pos(self.link.state())[:2])
                self.teleport(nav.direction(here, tile), f"the teleporter at {tile[0]},{tile[1]}")
            except Abort as e:
                if self.walk_in_through(tiles, i) == i:
                    raise
                log(f"{label}: {e}; but we are through it already (at its arrival)")
            if self.team is not None:
                self.team.landed(self.state(), "in")
            i += 1

    def walk_in_through(self, tiles, i: int) -> int:
        """The index of the first --enter-via teleporter from `i` on still ahead: past every one whose known
        arrival (the store's teleporters) we stand within WALK_IN_ARRIVED_R of. Live 2026-10-18 21:05:41-43: under
        attack 3 tiles from the last one, the step off the tank went onto its neighbour (5522, 636), also a
        teleporter, and put us on Maus 3 at (5199, 736); the walk on to it then aborted, "no route from (5199, 736)"."""
        st = self.link.state()
        facet = st["world"]["self"].get("map") or 0
        pos = tuple(self.pos(st)[:2])
        known = self.memory.teleporters(facet)
        for j in range(len(tiles) - 1, i - 1, -1):
            dest = known.get(tuple(tiles[j][:2]))
            if dest is not None and (dest[0] or 0) == facet and cheb(pos, dest[1:3]) <= WALK_IN_ARRIVED_R:
                return j + 1
        return i

    def walk_in_stop(self, st) -> str | None:
        """walk_in's stop: something attacking us or our hits falling (a calamity casts from 13 tiles,
        beyond CASTER_REACH: simulator seeds 15 and 16 died to one walking on), else a lagging pet."""
        if self.team is not None and (self.walk_hurt(st) or self.attackers(st)):
            return "under attack"
        return self.pets_lagging(st)

    def walk_in_dash(self, label, near):
        """Attacked within TELE_DASH_R route steps of the teleporter (walk_in): 'All Follow Me' without waiting for the
        pets and on to `near`, the tile its step goes from, at flight pace (no pause, no stop for the attackers);
        a ready heal potion below --heal-at on the way. walk_in then steps through without waiting for the pets
        beside it: on 'All Follow Me' they come through with us while in view (user 2026-10-20); one left behind
        can be claimed from the stablemaster, Logan dead can't. Live 2026-10-20 22:57:15-31
        (hunt-20261020-223926-1d18): attacked 13 steps from 5522,637, the pets guarded and Logan stood in an
        abomination's, a devourer beetle's and an insane reanimator's hits (potions at 60 and 62, the vet at 56)
        until the user walked him through (user: "Logan was dying to a mob instead of running to Maus 3")."""
        self.doing("enter", "Attacked near the teleporter: through it now")
        self.team.follow(wait=False)

        def stop(s):
            me = s["world"]["self"]
            if me.get("hits") is not None and self.frac(me) < self.args.heal_at \
                    and self.potions.ready(time.monotonic()) and healing.heal_potions(s["world"], self.me(s)):
                return "a heal potion"
            return None
        while self.mover.walk_to(lambda: near, 0, label, urgent=True, stop=stop):
            self.heal(self.link.state())

    def no_idle(self, st) -> bool:
        """Mover.no_idle: no walk pause or sidestep while walking out or with a monster in war mode in view (live
        2026-10-17 19:04:08-15: a ~7 s look-around pause 2 tiles from the leave gate while an apparition in war
        mode came 6 tiles onto Logan; dead 3.5 s after its first hit). The runner never sets Mover.danger, the
        lumber guards' zones that already stopped these pauses."""
        if self.walking_out:
            return True
        world = st["world"]
        return any((m.get("flags") or 0) & aggro.WAR and combat.attackable(world, key)[0]
                   for key, m in world["mobiles"].items())

    def walk_hurt(self, st) -> bool:
        """Our hits fell since the last look (walk_in: `walk_hits`, `walk_hurt_t`)."""
        hits = st["world"]["self"].get("hits")
        fell = hits is not None and self.walk_hits is not None and hits < self.walk_hits
        self.walk_hits = hits
        if fell:
            self.walk_hurt_t = time.monotonic()
        return fell

    def coming(self, st) -> str | None:
        """A planned walk out's stop (leave_by_gate) and what keeps its stand going (walk_in_fight): a monster in war
        mode within COMING_R that sees us, fights no one else (Team.elsewhere) and comes on (not aggro.shy: it shoots from
        where it stands), while it comes nearer (nearer than ever within the last COMING_S) -> "<kind> in war mode N
        tiles off", the nearest, else None. Live 2026-10-28 22:53:04.5-09.2 (hunt-20261028-222328-f677): the time-up
        walk's route ran north-east up a lowtide scavenger's way, in war mode from 10 tiles; three steps took Logan
        beside it with the pets on follow behind him, and he died there 12.7 s later."""
        if self.team is None:
            return None
        v, world, now = self.team.view(st), st["world"], time.monotonic()
        best = None
        for m in v.mobs:
            d = cheb(m["pos"], v.pos)
            if not m["war"] or m["shy"] or d > COMING_R or not v.sees(m, v.pos, COMING_R) \
                    or self.team.elsewhere(st, world["mobiles"].get(key_of(m["serial"])) or {}):
                continue
            t, least = self.coming_seen.get(m["serial"], (now, d))
            if d < least:
                t, least = now, d
            self.coming_seen[m["serial"]] = (t, least)
            if now - t <= COMING_S and (best is None or d < best[0]):
                best = (d, m["kind"])
        return f"{best[1]} in war mode {best[0]} tiles off" if best else None

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

    def wait_pets(self, label, at, required: bool = True) -> bool:
        """Up to WALK_IN_QUIET_S for every pet within FOLLOW_R of us (of the teleporter `at` when given):
        on follow they catch up by themselves (the user said 'All Follow Me' once for four teleporters);
        then 'All Follow Me' again and WALK_IN_PETS_S more, else Abort: never through a teleporter
        without them. A pet in view beyond COMMAND_R doesn't hear it: first to within COMMAND_R_GO of it (not beside a
        teleporter: `at` None; fetch_pet). `required` False (the walk out to the gate, which waits for them again
        beside it): False instead, go on without them. True when they came. Attacked or hurt meanwhile: the walk's
        fight (walk_in_fight), then the wait from its start (_wait_together)."""
        log(f"{label}: waiting for the pets ({self.pets_lagging(self.link.state()) or 'not all beside it'})")
        self.doing("enter" if required else "leave", "Waiting for the pets to catch up")
        if self._wait_together(label, at, WALK_IN_QUIET_S, "in" if required else "out"):
            return True
        if at is None:
            self.fetch_pet(label)
        self.team.follow(wait=False)
        if not self._wait_together(label, at, WALK_IN_PETS_S, "in" if required else "out"):
            if not required:
                log(f"{label}: the pets aren't within {tamer.FOLLOW_R} tiles after {WALK_IN_PETS_S:.0f} s; going on")
                return False
            raise Abort(f"{label}: the pets aren't within {tamer.FOLLOW_R} tiles "
                        f"{'of it' if at else 'of us'} after {WALK_IN_PETS_S:.0f} s; not going on without them")
        return True

    def _wait_together(self, label, at, seconds: float, way: str) -> bool:
        """Up to `seconds` for Team.together; a hit on us or an attacker ends it for walk_in_fight (which ends on
        'All Follow Me'), then the wait starts over; still attacked after the fight (it ran out, WALK_IN_FIGHT_S): False
        now (the caller's own way on). Simulator 2026-10-26 (tidal_tomb seed 3, the leave at time up): 'waiting for
        the pets' with both 9 tiles off, an oozing noughtilus took Logan from 100 to dead in 18 s, no potion, before
        'the pets aren't within 3 tiles after 20 s; going on'."""
        self.walk_hits = None
        while True:
            st = self.link.wait(lambda s: self.team.together(s, at=at) or self.walk_hurt(s) or bool(self.attackers(s)),
                                seconds)
            if st is None:
                return False
            if self.team.together(st, at=at):
                return True
            self.walk_in_fight(label, way)
            st = self.state()
            if self.attackers(st):
                return self.team.together(st, at=at)
            self.walk_hits = None

    def fetch_pet(self, label):
        """The nearest pet in view farther than COMMAND_R: walk to within COMMAND_R_GO of it (re-aimed as it moves) so
        the next 'All Follow Me' reaches it; a walk that fails or meets an attacker on us ends it. Live 2026-10-24
        18:26:46-18:27:50 (hunt-20261024-173530-0ca1): on the walk in's first level 'All Guard Me' sent GraveRobber 17
        tiles off after a reanimator apprentice, then a corpse eater; three 'All Follow Me' from 17 tiles left him
        fighting (AlaFlambe at 6 came at once), the walk aborted and the escape home left him there."""
        st = self.link.state()
        far = [p for p in self.team.pets(st) if not p["dead"] and p["dist"] > tamer.COMMAND_R]
        if not far:
            return
        p = min(far, key=lambda p: p["dist"])
        serial, last = p["serial"], (p["x"], p["y"])

        def center():
            nonlocal last
            q = next((q for q in self.team.pets(self.link.state()) if q["serial"] == serial), None)
            if q is not None:
                last = (q["x"], q["y"])
            return last
        log(f"{label}: {p['name']} at {p['dist']}, beyond a command's reach ({tamer.COMMAND_R}); going within "
            f"{COMMAND_R_GO} of it")
        self.doing("enter", f"Going back for {p['name']}", last)
        try:
            why = self.mover.walk_to(center, COMMAND_R_GO, f"{label}: to {p['name']}", max_moves=2 * p["dist"],
                                     stop=lambda s: "under attack" if self.attackers(s) else None)
            if why:
                log(f"{label}: to {p['name']}: stopped ({why})")
        except (WalkFailed, Abort) as e:
            log(f"{label}: to {p['name']}: {e}")

    def walk_in_fight(self, label, way: str = "in", coming: str | None = None):
        """Attacked on the way in (or out, `way`, a planned walk to the gate): 'All Guard Me' and the fight
        tick's order until nothing attacks us and no hit came for WALK_IN_CALM_S (up to WALK_IN_FIGHT_S): out
        of an area hit, a potion below --heal-at, the team's care (vet supplies, retreat, off the tank), the
        pets at what's on us (Team.fight: bard skills, "All Kill" on one they leave alone), then our kills
        looted; then 'All Follow Me' and walk on. Live 22:33:39 the care alone left Logan standing among the
        mobs, dying (the user took over); on follow the pets would let it hit us all the way. `coming` (the walk out's
        stop, HuntLoop.coming): no attack yet, a monster in war mode coming at us; on the way out the stand goes on while
        one comes (live 2026-10-28 22:53:04-09: the walk out went 3 steps up a lowtide scavenger's way, the pets on
        follow behind Logan; dead 12.7 s later). drink_on_the_run stays out of it (walk_fighting): its potion and vet
        supplies came before this order's retreat (live 22:53:17.6-21.1)."""
        self.walk_fighting = True
        try:
            self._walk_in_fight(label, way, coming)
        finally:
            self.walk_fighting = False

    def _walk_in_fight(self, label, way: str, coming: str | None):
        log(f"{label}: {f'{coming} coming' if coming else 'attacked'} on the way {way}; the pets guard")
        self.doing("enter" if way == "in" else "leave", f"{'Something coming' if coming else 'Attacked'} on the way "
                                                        f"{way}: the pets take it")
        self.team.guard_t = -1e9
        self.team.guard()
        end = time.monotonic() + WALK_IN_FIGHT_S
        while time.monotonic() < end:
            st = self.state()
            self.walk_hurt(st)
            attackers = self.attackers(st)
            me = st["world"]["self"]
            if not self.team.potion_first(st) and self.team.stand_off(st):
                continue
            if self.cure(st):
                continue
            if me.get("hits") is not None and self.frac(me) < self.heal_at_now() and self.heal(st):
                self.human.wait("hotkey")
                continue
            if self.team.care(st):
                continue
            target = self.pick_target(st, pull=False)
            if target is not None:
                self.fight(target)
                continue
            if not attackers and time.monotonic() - self.walk_hurt_t > WALK_IN_CALM_S \
                    and not (way == "out" and self.coming(st)):
                if (c := self.loot_next()) is not None:
                    self.loot(c)
                    continue
                break
            time.sleep(0.3)
        self.disengage("ended")
        self.team.follow()

    def fought_stop(self, st) -> str | None:
        """walk_in_fought's stop: something on us or our hits falling, a monster in reach to pull (pick_target), an
        advance that would pull too much or into a shy one's sight (Team.pull_check), the pets fighting or left behind
        (patrol_hold): the crawl patrol's stops without its seek and waypoints."""
        self.scan_events(st)
        if self.walk_hurt(st) or self.attackers(st):
            return "under attack"
        if self.pick_target(st) is not None:
            return "a target in reach"
        return self.team.pull_check(st) or self.patrol_hold(st) if self.team is not None else None

    def walk_in_fought(self, label, near, kind: str | None = None, bail=None) -> bool:
        """--fight-via: the leg to this teleporter fought through as on the hunt level, not walked (user 2026-10-26:
        "in TT we'll always have to fight in 2 in order to get to 3"; simulator 2026-10-26: all 8 level 3 runs died on
        the walk across level 2, at its arrival among the drowned mariners and soulcatchers). The pets on 'All Guard
        Me'; each round the tick's fighting (combat_step: what's in reach pulled and killed, the back-off from a shy
        one's first sight, 'All Patrol' at one, the hold for what sees us), the pets healed and with us, then on toward
        `near` (the tile before the teleporter, or before the leave gate on a planned leave up through --leave-via:
        leave_by_gate; a refill: oxygen_leg) until fought_stop. Abort when not there after WALK_FOUGHT_S (the run's
        overall timeout guard doesn't count a fought leg's time: fought_end, fought_s). `kind`: the viz intent's (else
        "enter" or "leave" by the label); `bail(st)`: a reason to stop short (False back), checked each round. True
        when there. A shy monster backed off from tamer.BACK_OFF_REPEAT times in the leg with no kill between is gone
        on past, the pets on guard (Team.back_off: the leg must arrive)."""
        log(f"{label}: fought through")
        if self.team is not None:
            self.team.leg, self.team.backed, self.team.faced = label, {}, {}
            self.team.guard_t = -1e9
            self.team.guard()
        end = self.fought_end = time.monotonic() + WALK_FOUGHT_S
        try:
            while True:
                if time.monotonic() > end:
                    raise Abort(f"{label}: not there after {WALK_FOUGHT_S:.0f} s of fighting")
                st = self.state()
                if bail is not None and (why := bail(st)):
                    log(f"{label}: {why}; stopping short")
                    return False
                self.walk_hurt(st)
                if self.combat_step(st, self.attackers(st), False):
                    continue
                if self.team is not None and self.team.busy_healing(st):
                    self.doing("vet", "Healing up before moving on")
                    time.sleep(POLL_S)
                    continue
                if self.team is not None and (why := self.patrol_hold(st)):
                    # as the crawl patrol (Crawl.idle): fighting, wait; left behind, back to them (Team.rejoin). Not
                    # wait_pets: its 'All Follow Me' pulls them off what they fight and aborts when they don't come
                    # (simulator 2026-10-26: 3 of 16 fought legs ended "pets not all within reach")
                    if self.pets_busy(st) is None and self.team.rejoin(st):
                        continue
                    self.doing("hold", f"Waiting for the pets ({why})")
                    time.sleep(POLL_S)
                    continue
                if cheb(self.pos(st), near) == 0:
                    return True
                self.doing(kind or ("enter" if label.startswith("to the teleporter") else "leave"),
                           f"Fighting through, {label}", near)
                self.mover.walk_to(lambda: near, 0, label, stop=self.fought_stop)
        finally:
            self.fought_s += time.monotonic() - (end - WALK_FOUGHT_S)
            self.fought_end = None
            self.disengage("ended")
            if self.team is not None:
                self.team.leg, self.team.backed, self.team.faced = None, {}, {}
                self.team.follow()

    # ------------------------------------------------------------ oxygen (Tidal Tomb's levels 2+)
    def oxygen_floor(self) -> float | None:
        """The deprivation's floor share (0.66) while we are deprived under water, else None (oxygen.Clock)."""
        return self.oxy.floor if self.oxy is not None and self.oxy.deprived(time.monotonic()) else None

    def heal_at_now(self) -> float:
        """--heal-at, but deprived of oxygen never above the deprivation's floor: its drain takes back every hit healed
        above it, 5 every 6 s (a potion's 25-45 gone in 30-50 s)."""
        floor = self.oxygen_floor()
        return self.args.heal_at if floor is None else min(self.args.heal_at, floor)

    def oxygen_site(self, st) -> dict | None:
        """The easiest refill in our underwater box (oxygen.sites): a hop's teleporter up (the route onto it, `near` the
        tile its step goes from) or a dolphin (the route to within oxygen.DOLPHIN_STAND_R of it), by route steps plus
        OXY_MOB_STEPS for each attackable monster in view within OXY_MOB_R of a route tile: the ground to fight across,
        not the distance (user 2026-10-28). None when no refill there has a route that its leg can walk before the
        deprivation from OXY_MIN_S on (`reach` route steps)."""
        pos = tuple(self.pos(st)[:2])
        box = oxygen.box_of(self.oxy.ox, pos, st["world"]["self"].get("map") or 0)
        if box is None:
            return None
        world = st["world"]
        mobs = [(m["x"], m["y"]) for k, m in world["mobiles"].items()
                if m.get("x") is not None and combat.attackable(world, k)[0]]
        best = None
        reach = int((self.oxy.limit_s - OXY_MARGIN_S - OXY_MIN_S) / OXY_STEP_S)
        for s in oxygen.sites(self.oxy.ox):
            if s["box"] != box:
                continue
            r = 0 if s["kind"] == "hop" else oxygen.DOLPHIN_STAND_R
            if s["kind"] == "dolphin" and cheb(pos, s["tile"]) <= r:
                path = [pos]
            else:
                path, _ = self.mover.plan(st, nav.within(s["tile"], r), mobiles=False, max_steps=reach)
                if not path or (s["kind"] == "hop" and len(path) < 2) or len(path) - 1 > reach:
                    continue
            n = sum(1 for m in mobs if any(cheb(m, p) <= OXY_MOB_R for p in path))
            site = {**s, "near": tuple(path[-2] if s["kind"] == "hop" else path[-1]), "steps": len(path) - 1,
                    "mobs": n, "cost": len(path) - 1 + OXY_MOB_STEPS * n}
            if best is None or site["cost"] < best["cost"]:
                best = site
        return best

    def oxygen_due(self, st) -> dict | None:
        """The refill to go to now (oxygen_site), else None: under water OXY_MIN_S or longer and past the deprivation
        less OXY_MARGIN_S and the leg's estimate (its route steps x OXY_STEP_S); no leg before oxy_retry_t."""
        if self.oxy is None:
            return None
        now = time.monotonic()
        el = self.oxy.elapsed(now)
        if el is None or el < OXY_MIN_S or now < self.oxy_retry_t:
            return None
        if now - self.oxy_plan_t >= OXY_PLAN_S:
            self.oxy_site, self.oxy_plan_t = self.oxygen_site(st), now
        s = self.oxy_site
        if s is None or el < self.oxy.limit_s - OXY_MARGIN_S - s["steps"] * OXY_STEP_S:
            return None
        return s

    def oxygen_bail(self, st) -> str | None:
        """oxygen_leg's reason to stop short (kept in oxy_why): a leave rule, the hunt's failed walks, the run's end (the
        tick takes it)."""
        why = None
        if (reason := self.leave_reason(st)) is not None:
            why = f"a leave ({reason[0]})"
        elif self.walk_trouble is not None:
            why = self.walk_trouble
        elif self.finished() or self.timed_out():
            why = "the run's time is up"
        self.oxy_why = why
        return why

    def oxygen_leg(self, st, site):
        """Fight our way to the easiest refill and take it (user 2026-10-28: "I would prefer it just fight normally to
        the easiest oxygen refill to get to"): walk_in_fought to it (the pets on guard, what's in reach fought, the
        leave rules on: oxygen_bail), then up the hop and back down (oxygen_hop) or the dolphin's refill
        (oxygen_dolphin). A failed leg (Abort) hunts on and tries again after OXY_RETRY_S. An `oxygen` job event."""
        t0 = time.monotonic()
        site = self.oxygen_site(st) or site
        el = self.oxy.elapsed(t0) or 0.0
        refills = self.oxy.refills
        what = "the teleporter up" if site["kind"] == "hop" else "the dolphin"
        label = f"oxygen: to {what} at {site['tile'][0]},{site['tile'][1]}"
        log(f"{label}: {el:.0f} s under water; {site['steps']} route steps, {site['mobs']} monsters in view near it")
        self.count("oxygen_legs")
        outcome = "stopped"
        self.walk_hits, self.walk_hurt_t = None, -1e9    # walk_hurt's baseline (set by walk_in_legs on the way in)
        try:
            if not self.walk_in_fought(label, site["near"], kind="oxygen", bail=self.oxygen_bail):
                outcome = f"stopped short: {self.oxy_why}"
            else:
                (self.oxygen_hop if site["kind"] == "hop" else self.oxygen_dolphin)(label, site)
                outcome = "refilled" if self.oxy.refills > refills else "not refilled"
        except Abort as e:
            outcome = f"failed: {e}"
            log(f"{label}: {e}; hunting on from here")
        finally:
            if outcome != "refilled":
                self.oxy_retry_t = time.monotonic() + OXY_RETRY_S
            self.oxy_plan_t = -1e9
            if self.team is not None:
                self.team.guard_needed = True
            if self.crawl is not None:
                self.crawl.wp, self.crawl.arrived_t = None, None
            st2 = self.link.state()
            self.memory.job_event("hunt", "oxygen", {
                "refill": site["kind"], "tile": list(site["tile"]), "under_s": round(el, 1), "steps": site["steps"],
                "mobs": site["mobs"], "s": round(time.monotonic() - t0, 1), "outcome": outcome, "visit": self.visit_n,
                "hits": [st2["world"]["self"].get("hits"), st2["world"]["self"].get("hits_max")]}, **self._where(st2))
            log(f"{label}: {outcome} after {time.monotonic() - t0:.0f} s")

    def oxygen_hop(self, label, site):
        """Up the hop's teleporter to dry ground with the pets, OXY_DRY_S there, back down by its `back` teleporter
        (live 2026-10-24 11:13:31-39: up from level 2's west strip, down again 8 s later, the gump full)."""
        self.onto_teleporter(label, site["tile"])
        st = self.state()                                # the clock sees dry ground
        if self.team is not None:
            self.team.landed(st, "oxygen up")
        self.doing("oxygen", "A breath on dry ground", site["back"])
        time.sleep(OXY_DRY_S)
        self.onto_teleporter(label, site["back"])
        st = self.state()
        if self.team is not None:
            self.team.landed(st, "oxygen down")

    def onto_teleporter(self, label, tile):
        """From near it onto the teleporter `tile` with the pets beside it (walk_in_legs' last steps). Standing on it
        (a hop lands on its way back): a step off to a free tile first."""
        st = self.link.state()
        here = tuple(self.pos(st)[:2])
        if here == tuple(tile):
            tps = self.mover.teleporter_tiles(st["world"]["self"].get("map") or 0)
            for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0), (-1, -1), (1, -1), (-1, 1), (1, 1)):
                off = (here[0] + dx, here[1] + dy)
                path, _ = self.mover.plan(st, nav.within(off, 0), mobiles=False)
                if off not in tps and path and len(path) == 2:
                    self.mover.walk_to(lambda: off, 0, f"{label}: off the teleporter")
                    break
            else:
                raise Abort(f"{label}: no free tile beside the teleporter at {tile}")
        path, _ = self.mover.plan(self.link.state(), nav.within(tile, 0), mobiles=False)
        if not path or len(path) < 2:
            raise Abort(f"{label}: no route onto the teleporter at {tile}")
        near = tuple(path[-2])
        self.mover.walk_to(lambda: near, 0, label)
        if self.team is not None and not self.team.together(self.link.state(), at=tile[:2]):
            self.wait_pets(label, tile[:2])
        here = tuple(self.pos(self.link.state())[:2])
        self.teleport(nav.direction(here, tile), f"the teleporter at {tile[0]},{tile[1]}")

    def oxygen_dolphin(self, label, site):
        """Within oxygen.DOLPHIN_STAND_R of the dolphin: fight what comes until its refill line (every ~5.25 s); none in
        OXY_DOLPHIN_WAIT_S, within 4 of it and the same wait again, then Abort."""
        refills = self.oxy.refills
        for closer in (False, True):
            if closer:
                path, _ = self.mover.plan(self.link.state(), nav.within(site["tile"], 4), mobiles=False)
                if not path:
                    break
                if not self.walk_in_fought(f"{label} (closer)", tuple(path[-1]), kind="oxygen", bail=self.oxygen_bail):
                    return
            end = time.monotonic() + OXY_DOLPHIN_WAIT_S
            while time.monotonic() < end:
                st = self.state()
                if self.oxy.refills > refills or self.oxygen_bail(st):
                    return
                if self.combat_step(st, self.attackers(st), False):
                    continue
                self.doing("oxygen", "Waiting for the dolphin's refill", site["tile"])
                time.sleep(POLL_S)
        raise Abort(f"{label}: no refill from the dolphin in {2 * OXY_DOLPHIN_WAIT_S:.0f} s")

    def wait_pk(self):
        """After leaving from a hostile player: stay home until --pk-wait has passed."""
        if self.pk_left_t is None:
            return
        end = self.pk_left_t + self.args.pk_wait
        if self.args.timeout > 0:
            end = min(end, self.t0 + self.args.timeout)               # never past --timeout
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
        if self.in_t is not None:
            self.visit["in_s"] = round(time.monotonic() - self.in_t, 1)
            self.in_t = None
        if self.args.leave_recall is not None and self.args.return_at != "off":
            self.return_floor = hunt_return.carried(st, self.prices)["total"]
            since = hunt_return.LIVE_SINCE
            self.return_model = m = hunt_return.model(
                self.memory.episodes("hunt", since=since), self.memory.job_events("hunt", since, char_serial=self.me(st)),
                self.spot, st["world"]["self"].get("map"))
            log(f"return threshold: home once this visit adds {m['v_star']} gp (V* = r sqrt(2T/h): {m['r']:.0f} gold/min; "
                f"a death every {1 / m['h']:.0f} min here: {m['encounters']} player encounter(s), {m['pk_deaths']} fatal, "
                f"{m['deaths'] - m['pk_deaths']} monster death(s) in {m['minutes']:.0f} min; trip {m['trip_min']:.1f} min: "
                f"out {m['trip']['out']:.1f}, drop-off {m['trip']['dropoff']:.1f}, in {m['trip']['in']:.1f})"
                if self.args.return_at == "auto" else f"return threshold: home once this visit adds {self.args.return_at} gp")

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
        """--kills reached, or `ctl stop --after-trip` (task_wrap.finish_requested for our UO_TASK_ID, read every
        FINISH_CHECK_S; user 2026-10-17, loot piling up: "didn't know there wasn't a way to finish gracefully"):
        the run then winds down as at time up (the fights on us, our kills looted, the way out with the pets, no
        going back in) and exits 0 at home."""
        if self.args.kills and self.totals["kills"] >= self.args.kills:
            return True
        if self.finish_requested:
            return True
        task_id = os.environ.get("UO_TASK_ID")
        if task_id and time.monotonic() - self._finish_checked >= FINISH_CHECK_S:
            self._finish_checked = time.monotonic()
            if task_wrap.finish_requested(self.memory, task_id):
                self.finish_requested = True
                log("stop requested: winding down, out with the pets, home, then done")
        return self.finish_requested

    def timed_out(self, extra: float = 0.0) -> bool:
        """Past --timeout (the whole run's, from its start, every visit together); never without one."""
        return self.args.timeout > 0 and time.monotonic() - self.t0 > self.args.timeout + extra

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
            self.human.wait("hotkey")
            self.attack(serial)
            return
        if stationary.steps_left(st["world"]):
            return                        # the penalty is on: unstick walks it off before any attack
        if dist <= SPELL_RANGE and self.spell_block.get(serial, 0) < now and not self.casting_disarms(st) \
                and self.spell_ok(st, self.spell, reserve=self.args.mana_reserve):
            done = self.cast_at(serial)
            if done:
                self.human.wait("hotkey")
            if done or done is None:
                return
        time.sleep(POLL_S)

    def go_back(self, reason) -> bool:
        """After leaving: rest and go back in, or stop (ready_to_go_back). Recall mode: recall back in
        (--enter-recall); False when the time is up meanwhile (the run ends at home)."""
        why, severity = reason
        if not self.ready_to_go_back(why, severity):
            return False
        if self.args.leave_recall is None:
            self.rearm(self.state())
            self.enter()
        elif not self.enter_by_recall():
            return False
        self.start_visit(self.state())
        self.to_spot()
        return True

    def ready_to_go_back(self, why, severity) -> bool:
        """After leaving (or a walk in a hostile player cut short, WalkInFled): rest, and in recall mode wait out
        --pk-wait after a hostile player. Abort when we may not go back (a pet down, a hostile player without
        --leave-recall, --rest-to 0, the rest takes too long, no --enter-recall); False when the time is up
        meanwhile (the run ends at home)."""
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
        if recall:
            if self.args.enter_recall is None:
                raise Abort(f"left the hunt by recall ({why}); no --enter-recall to go back in")
            if not (self.finished() or self.timed_out()):
                self.wait_pk()
            if self.finished() or self.timed_out():
                log("the run is over (--kills / --timeout): not going back in")
                return False
        return True

    def to_spot(self):
        """Walk to the fight spot (the Mover avoids known teleporter tiles), then set
        this visit's leave margin. Crawling: build the floor once (crawl.Crawl.setup);
        the patrol starts from where we stand. A hostile player on the walk in that sent us on through the
        teleporters (walk_in_rush): PlayerThreat here, so the visit's first act is the leave by the gate (hunt)."""
        if self.crawl is not None:
            try:
                self.crawl.setup(self.state())
            except ValueError as e:
                raise Abort(f"crawl: {e}")
            self.leave_margin(self.state())
        if (why := self.walk_in_red) is not None:
            self.walk_in_red = None
            raise PlayerThreat(why)
        if self.crawl is not None:
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
            except WalkFailed as e:
                self.walk_failed(e)

    def walk_failed(self, e: WalkFailed) -> None:
        """A walk inside the hunt failed (Mover: no route, blocked, boxed in, ...): Logan stands where he was, so the
        ticks go on from there (fight, heal, vet) rather than ending the run in the dungeon. Live 2026-10-20 19:23:30 a
        retreat's "too many blocked moves" ended the run on Maus 3 and Logan and both pets died standing by 19:30.
        More than WALK_FAIL_MAX within WALK_FAIL_WINDOW_S: something keeps him from moving here; the planned leave
        home (`walk_trouble`, an info leave: the run ends at home)."""
        if self.visit is None:
            raise e
        now = time.monotonic()
        self.walk_fails = [t for t in self.walk_fails if now - t < WALK_FAIL_WINDOW_S] + [now]
        log(f"a walk failed: {e}; carrying on from here ({len(self.walk_fails)} in {WALK_FAIL_WINDOW_S:.0f} s)")
        if len(self.walk_fails) > WALK_FAIL_MAX and self.walk_trouble is None:
            self.walk_trouble = f"{len(self.walk_fails)} failed walks in {WALK_FAIL_WINDOW_S:.0f} s (last: {e})"

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
        if reason is None and self.walk_trouble is not None:
            reason = (self.walk_trouble, "info")
        if reason is None and winding and ((not attackers and not self.corpses) or self.timed_out(60.0)):
            reason = ("done" if self.finished() else "time is up", "info")
        if reason is None and not winding and not attackers and self.engaged is None and not self.corpses:
            reason = self.return_reason(st)
        if reason is not None:
            if self.leave(st, *reason) == "called off":
                return False
            if reason[1] == "info":
                return True
            return not self.go_back(reason)
        if self.combat_step(st, attackers, winding):
            return False
        if self.team is not None and self.crawl is not None and self.team.busy_healing(st):
            self.doing("vet", "Healing up before moving on")
            time.sleep(POLL_S)
            return False
        if self.team is not None and self.crawl is not None \
                and any(time.monotonic() - t < tamer.FIGHT_LINGER_S and key_of(s) not in st["world"]["mobiles"]
                        for s, (_, t) in self.lost_alive.items()):
            # live 20261015_115604 12:08:53: the patrol walked on toward the gravebug the moment an apparition
            # blinked out; it came back on the gravebug's tile 0.6 s later and hit Logan. Once it is back in view the
            # fight takes it up (pick_target) and there's nothing left to wait for (simulator 2026-10-24: the full
            # FIGHT_LINGER_S after every blink, 2.3 % of the game time once the step off the tank no longer filled it)
            self.doing("hold", "Waiting for a monster that blinked out to come back")
            time.sleep(POLL_S)
            return False
        if (site := self.oxygen_due(st)) is not None:
            self.oxygen_leg(st, site)
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

    def combat_step(self, st, attackers, winding: bool) -> bool:
        """The tick's fighting, in its order: the weapon back in hand, out of an area hit, a cure, a heal, the pets' care,
        unstuck, an eerie corpse, the loot, the pets on 'All Patrol', a target and its fight, then (nothing to fight) the
        back-off from a shy monster's first sight, 'All Patrol' at one, the hold for one that sees us. True when it did
        something (one decision). Shared by the tick and a fought leg of the walk in (walk_in_fought)."""
        me = st["world"]["self"]
        if self.rearm(st):
            return True
        if self.team is not None and not self.team.potion_first(st) and self.team.stand_off(st):
            return True                   # out of an area hit first, unless a potion is ready: it goes at once
        if self.cure(st):
            return True
        if me.get("hits") is not None and self.frac(me) < self.heal_at_now() and self.heal(st):
            self.human.wait("hotkey")
            return True
        if self.team is not None and self.team.care(st):
            return True
        if self.unstick(st, attackers):
            return True
        if not attackers and (eerie := self.soul_corpse(st, walk=self.engaged is None)) is not None:
            self.fill_soul_jar(*eerie)     # before looting: other players race us to an eerie corpse
            return True
        if not attackers and (c := self.loot_next()) is not None:
            self.loot(c)
            return True
        if self.team is not None and self.team.patrol_watch(st):
            return True                   # the pets on "All Patrol" at a purger: we stand, then 'All Guard Me'
        target = self.pick_target(st, pull=not winding)
        if target is not None:
            if target != self.engaged and self.engaged is not None:
                log(f"switching target to 0x{target:08X}")
                self.disengage("switched")
            self.fight(target)
            return True
        if self.engaged is not None:
            self.disengage()
        if self.war_mode(st, False):
            log("nothing near: war mode off")
            self.human.wait("hotkey")
        if self.team is not None and self.team.back_off(st):
            return True                   # a corpse purger would see us before the pets: back behind them
        if self.team is not None and not winding and self.team.patrol_pull(st):
            return True                   # an idle purger in reach: the pets sent to wake it ("All Patrol")
        if self.team is not None and (why := self.team.hold_reason(st)):
            if self.team.pull_spot(st):
                return True
            self.doing("hold", f"Holding: {why}")
            time.sleep(POLL_S)
            return True
        return False

    def setup(self, pets_required: bool = True):
        """The run's start: our position and serial from the proxy, the weapon kept in hand, the pet roster (--pets;
        without `pets_required`, --go-home, no pet in view goes on without the team). -> the state."""
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
            try:
                self.team.setup(st)
            except Abort as e:
                if pets_required:
                    raise
                log(f"{e}; going on without the pets")
                self.team = None
        if self.team is not None and self.args.avoid_backlash \
                and tamer.casters_only(st["world"]["self"].get("name"), self.team.roster):
            never = tuple(self.args.never_aggro or aggro.NEVER_AGGRO)
            self.args.never_aggro = never + tuple(k for k in aggro.BACKLASH_TYPES if k not in never)
            log(f"every attack pet casts: never pulling {', '.join(aggro.BACKLASH_TYPES)} (its Backlash fizzles their "
                f"spells; --no-avoid-backlash to fight it)")
        return st

    def run(self):
        st = self.setup()
        entered = True                          # False: a hostile player on the walk in sent us home, the run is over
        if self.args.enter_recall is not None and not self.at_hunt(st) \
                and escape_mod.recall_forbidden(self.pos(st), st["world"]["self"].get("map")):
            try:
                self.resume_inside(st)            # no recall from here: started inside the dungeon
            except WalkInFled as e:
                entered = self.ready_to_go_back(e.why, "urgent") and self.enter_by_recall()
            st = self.state()
        elif self.args.enter_recall is not None and not self.at_hunt(st):
            entered = self.enter_by_recall()
            st = self.state()
        elif self.args.enter and not self.at_hunt(st):
            self.enter()
            st = self.state()
        if entered:
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
                         "the hunting level; the last arrival is the hunt (the Mausoleum: 5554 700, 5507 701, 5507 696, 5522 637)")
    ap.add_argument("--fight-via", type=int, nargs=2, action="append", default=None, metavar=("X", "Y"),
                    help="the --enter-via teleporters (repeat) whose leg is fought through as on the hunt level rather "
                         "than walked: the pets on guard, what's in reach pulled and killed, the shy shooters' first "
                         "sight kept on the pets, then on (Tidal Tomb's level 2 on the way to 3: 6884 1929)")
    ap.add_argument("--no-oxygen", action="store_true",
                    help="no oxygen rule under water (Tidal Tomb's levels 2+, oxygen.py): no refill legs, --heal-at as "
                         "given while deprived (the simulator's 'off' arm)")
    ap.add_argument("--leave-gate", type=int, nargs=2, default=None, metavar=("X", "Y"),
                    help="leave by this moongate on the hunting level (dungeons allow no recall except near a "
                         "gold gate, never near a red one: user 2026-10-13), to --gate-dest, then --leave-recall "
                         "home from there; on a level above the hunt's, reached by --leave-via")
    ap.add_argument("--leave-via", type=int, nargs=2, action="append", default=None, metavar=("X", "Y"),
                    help="the teleporters (in order, repeat) from the hunting level up to --leave-gate's level, "
                         "stepped onto before the walk to the gate when the gate isn't reachable from where we "
                         "stand (Tidal Tomb's level 3: 7161 2221, its crimson gate 6897 1992 on level 2)")
    ap.add_argument("--gate-dest", default=None, metavar="NAME",
                    help="--leave-gate's destination on its 'Moongate Destinations' menu (e.g. Totem)")
    ap.add_argument("--bank-gold", type=int, default=0, metavar="N",
                    help="after recalling home: with N gold or more in the pack, bank it all (0 = off)")
    ap.add_argument("--return-at", default="auto", metavar="auto|off|GOLD",
                    help="recall mode: take the loot home once the carried value (gold + loot:<name> prices) reaches "
                         "V* = r sqrt(2T/h) (auto; hunt_return), a fixed gold value, or never (off); a full pack always")
    ap.add_argument("--drop-off", action=argparse.BooleanOptionalAction, default=True,
                    help="home after a recall: walk to the house's landing tile and run its drop-off (homes.json "
                         "dropoff), then back in from there")
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
                         "it is never pulled; one that attacks us or the pets is fought (repeat; default "
                         "aggro.NEVER_AGGRO: the elder vampire and the elder vampire countess, user 2026-10-15)")
    ap.add_argument("--avoid-backlash", action=argparse.BooleanOptionalAction, default=True,
                    help="--pets: when every attack pet casts (data/sim/tamer/<character>.json), add aggro.BACKLASH_TYPES "
                         "(the giant astral zoa: its Backlash fizzles spells) to the never-aggro set (user 2026-10-27: "
                         "'Maybe we should just avoid it when our pet team relies on caster(s)')")
    ap.add_argument("--crawl-avoid-types", action=argparse.BooleanOptionalAction, default=None,
                    help="crawl: avoid creature types the store's fights say cost too much (--crawl-max-hits, "
                         "--crawl-max-fight-s) or were fled from (default on, --pets off: user 2026-10-15)")
    ap.add_argument("--rope", action=argparse.BooleanOptionalAction, default=True,
                    help="fleeing a hostile player by --leave-gate: 'All Follow Me' at once and an Adventurer's "
                         "Rope jump toward the gate whenever one is ready, as Logan fled reds")
    ap.add_argument("--patrol-pull", action=argparse.BooleanOptionalAction, default=True,
                    help="--pets: wake an idle corpse purger or blightmare with 'All Patrol' at a tile beside it while "
                         "we stand out of its sight, so its shots go to the pets; 'All Guard Me' once they're on it "
                         "(user 2026-10-18, docs/NOTES.md 'All Patrol'). On by default since 2026-10-22 (user: \"stay "
                         "out of their aggro range and patrol the pets into them\"; with Team.cover and the rest of "
                         "docs/NOTES.md 'Half the deaths in the simulator')")
    ap.add_argument("--hold-first-sight", action=argparse.BooleanOptionalAction, default=True,
                    help="--pets, nothing fighting: stop where an idle monster first sees us within its aggro "
                         "range and let it come (up to tamer.HOLD_S 5 s each), as Logan at the nightstalker room's edge")
    ap.add_argument("--reposition", action=argparse.BooleanOptionalAction, default=True,
                    help="--pets: in a fight, step to a nearby tile that keeps the pets in vet range with fewer "
                         "idle monsters and casters able to see us (aggro.View.stand_score)")
    ap.add_argument("--songs", action=argparse.BooleanOptionalAction, default=True,
                    help="--pets: keep the three barding songs up (a bard skill at the ground: less damage taken, "
                         "more healing, more damage for 15 min; user 2026-10-13)")
    ap.add_argument("--kills", type=int, default=0, help="stop after this many kills (0 = no limit)")
    ap.add_argument("--timeout", type=float, default=0.0,
                    help="seconds from the run's start (all its visits): then finish the fight and leave; 0 (the "
                         "default, user 2026-10-17: the return rule takes the loot home) = no limit, the run ends "
                         "by --kills, `ctl stop --after-trip` or a leave rule that doesn't go back in")
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
    ap.add_argument("--armor-aspect", action=argparse.BooleanOptionalAction, default=True,
                    help="no recall in without the worn armor's aspect on and Arcane Essence left (the Aspect Mastery "
                         "menu read at home before each trip, HuntLoop.check_aspect; user 2026-10-25)")
    ap.add_argument("--repair", action=argparse.BooleanOptionalAction, default=True,
                    help="before each recall in from home: double-click the house's repair bench (homes.json "
                         "repair_bench; HuntLoop.repair_gear): worn gear and the pack's instruments repaired; an empty "
                         "bench ends the run at home (user 2026-10-27)")
    ap.add_argument("--soul-jars", action=argparse.BooleanOptionalAction, default=True,
                    help="Field of Souls: with an empty soul jar in the pack, walk to an eerie corpse in view and "
                         "fill the jar there (soul_jar.py; user 2026-10-17)")
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
    ap.add_argument("--go-home", action="store_true",
                    help="no hunting: from wherever we stand, home (the Seer after a run left the character out). "
                         "Inside a dungeon that allows no recall, the way out an error's escape takes (the first "
                         "level's gold moongate, the hunt level's --leave-gate, a middle level's remaining --enter-via "
                         "teleporters, then the gate); elsewhere the recall with --leave-recall; then the drop-off. "
                         "Give it the hunt's own arguments")
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
    if args.fight_via and not {tuple(t) for t in args.fight_via} <= {tuple(t) for t in args.enter_via or []}:
        ap.error("--fight-via names --enter-via teleporters: give each as --enter-via too")
    if args.go_home and args.leave_recall is None:
        ap.error("--go-home recalls home with --leave-recall: give it")

    memory = Memory(args.memory)
    link = Link(args.control_port, args.state_port, char=args.char)
    link.end_on_pause = True                 # the user took over: the run ends (agent_link.GatePaused)
    loop = HuntLoop(link, memory, args)
    watch_stop(args.memory)
    code = 1
    try:
        code = run_loop(loop)
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
        loop.end_visit("stopped")
        memory.close()
    sys.exit(code)


def run_loop(loop) -> int:
    """The run (--go-home: HuntLoop.go_home) and its exit code: 0 when it ended as planned; an error (Abort, WalkFailed,
    a crash) goes to HuntLoop.on_error, which escapes home first when it left us alive inside a dungeon that allows no
    recall. GatePaused (the user took over) and KeyboardInterrupt (`ctl stop`) are BaseExceptions: main ends on them."""
    try:
        (loop.go_home if loop.args.go_home else loop.run)()
        return 0
    except Exception as e:  # noqa: BLE001 - every error ends the run; on_error decides about the escape
        return loop.on_error(e)


def watch_stop(db):
    """Under task_wrap (UO_TASK_ID): a thread that interrupts the main thread (KeyboardInterrupt, even in a
    sleep or the agent gate's wait) once `ctl stop` asks to stop this task, so the visit row is written
    (main's finally) before the wrapper's terminate: live 2026-10-17 the 15:41 and 16:17 visits (25 min, 22.7k
    gold) were lost to stops, the store's model never saw them. The wrapper waits task_wrap.SELF_STOP_S."""
    task_id = os.environ.get("UO_TASK_ID")
    if not task_id:
        return

    def poll():
        mem = Memory(db)
        try:
            while not task_wrap.stop_requested(mem, task_id):
                time.sleep(STOP_POLL_S)
        finally:
            mem.close()
        signal.raise_signal(signal.SIGINT)   # wakes a sleep on Windows too (_thread.interrupt_main doesn't)
    threading.Thread(target=poll, name="watch_stop", daemon=True).start()


if __name__ == "__main__":
    main()

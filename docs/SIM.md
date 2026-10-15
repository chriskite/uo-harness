# Mausoleum simulator (`harness/sim/`)

An offline game world for the hunt runner (user request 2026-10-13: "train our combat runner in the
emulated game world ... run tests much faster"). It is not an Outlands server: it is the one dungeon
level Logan Wolf fought on (2026-10-12, capture `20261012_210013`), on the real map, with the creatures,
pets and mechanics the `--pets` runner meets there, each number calibrated from that capture or the
wiki. The runner under test is the unmodified `loop_hunt.HuntLoop` (`tamer.Team`), on virtual time:
a 30-minute hunt takes 2–5 s of wall time (260–670× real time), and 24 runs take under 20 s on 7
processes.

```mermaid
flowchart LR
  subgraph Runner[unchanged runner code]
    HL[loop_hunt.HuntLoop + tamer.Team<br/>Mover, crawl, escape, humanize]
  end
  subgraph Sim[harness/sim]
    CL[clock.SimClock<br/>time.* patched in harness modules]
    LK[link.SimLink<br/>the Link API]
    WM[world.runtime.WorldRuntime<br/>the client's world model]
    W[world.World<br/>the server: map, mobs, pets, mechanics]
  end
  HL -- act / send --> LK
  LK -- C2S --> WM
  LK -- C2S --> W
  W -- S2C --> WM
  LK -- state / movement --> HL
  CL -. advance: World.run_until .-> W
  HL -. sleep / monotonic .-> CL
```

## Running

```
python harness/sim/run.py --runs 24 --minutes 30 --compare -- [loop_hunt arguments]
python harness/sim/run.py --runs 24 --set tamer.FALLBACK_S=0          # an experiment on a constant
python harness/sim/run.py --log run.log --seed 7                       # one run, with the runner's log
python harness/test_sim.py                                             # the tests (~1.5 s)
python harness/sim/calibrate.py --logs <logs dir> [--scan 20261010] [TAG...]   # refresh the observations
```

- Defaults added to the runner's arguments: `--pets --leave-recall <tome> --enter-recall <tome>
  --enter-rune Mausoleum --crawl --timeout <minutes> --quiet --triage-url ""`, and the scenario's
  `runner` block: `--enter-via` the four teleporters Logan took in (two of them short hops on the
  second level) and `--leave-gate 5203 703 --gate-dest Totem`. The character starts at home (the DTF
  landing) with both pets, recalls to the gold gate on the first level, walks in, hunts, leaves by the
  crimson moongate to Totem and recalls home from there, by the runner's own rules. `--at-hunt` starts
  on the fight level instead.
- **Memory persists across runs** (user 2026-10-13). The runs are split into `--careers` (default
  `--jobs`) careers. Each career runs one after another on one memory store, as the live runner keeps
  its store between runs: crawl priors, fights, avoided types, learnt teleporters. `--fresh` gives
  every run a new store. Summaries carry `career` and `career_run`.
- **Parallel runs**: one process per career (`--jobs` workers, default CPU threads - 1), so the
  careers bound the parallelism. The desktop has 16 cores / 32 threads (user 2026-10-13): batches
  since then use `--runs 98 --jobs 16 --careers 14` (careers of 7 runs, as the earlier `--runs 49
  --jobs 7` ones).
- Each run prints a row (kills, gold, damage taken, deaths, pet deaths, vet uses, potions, rule
  violations); `all:` aggregates (kills and gold per game hour, deaths per hour, runs with a death /
  a pet death / a pet left behind, crashes, violations); `--compare` sets the runs per hour on the level
  beside the live trip's; `--json` prints every run's summary (with `death_causes`: who did the damage
  in the 15 s before a death, `junctures`, `kills_by`, `bard`, ...).
- Seeds make runs reproducible (the world's randomness and the runner's `--seed`).

## Pieces

| File | What it is |
|---|---|
| `sim/clock.py` | `SimClock` + `install()`: every loaded harness module's `time` becomes a view of the virtual clock (`monotonic`, `time`, `sleep`, `strftime` on game time), so the runner's 100+ direct `time.*` calls run unchanged. `sleep(dt)` advances the world. |
| `sim/link.py` | `SimLink`: `act`/`send` (walks get their sequence number, as the proxy stamps them), `state`/`movement` (the server's position as movement truth, the client model's snapshot, events), `wait`/`pos`/`open_containers` from `agent_link.Link`. A state read costs 20 ms of game time, a packet 2 ms. |
| `sim/world.py` | The server (module docstring): walking by `pathfind.Walk.can_walk` on the install's map (z from the map), line of sight by `los.Sight`, monster and pet AI, bard skills, vet supplies, potions, Stationary Penalty, corpses and loot, the rune tome (escape.py's gump layout), our death. |
| `sim/packets.py` | The S2C packets in the Outlands V10/V12 shapes the world model reads. |
| `sim/scenario.py` | Loads `harness/data/sim/mausoleum.json` and builds each creature's spec from the wiki stats and the capture (its docstring gives the order of evidence); `fit_bard` re-derives the barding skill. |
| `sim/calibrate.py` | The capture extractor that wrote `mausoleum_observed.json` (its rules are in the file's `rules`). |
| `sim/run.py` | Runs, pool, aggregate, comparison. |
| `harness/los.py` | Line of sight (ServUO `Map.LineOfSight`), shared with the live harness: 61 of 61 server answers on the trip agree (59 accepted targets seen, both "Target cannot be seen." blocked) once our z comes from the map; ~100,000 checks/s. |
| `data/sim/mausoleum.json` | The scenario: player, pets, pack, tome rows, bard/vet/potion numbers, defaults, specials, 42 spawns, the trip reference. Every number has its evidence in a `note`. |
| `data/sim/mausoleum_kb.json` | Wiki stats of 31 creatures, both pets, 22 mechanics, with knowledge ids. |
| `data/sim/mausoleum_observed.json` | The capture's observations (86 KB), every number with its sample size. |

## Calibration (2026-10-13)

What the capture and the wiki fixed:

- **Creature hits are the wiki's**: every creature that died in view took exactly its wiki Hits + 1 in
  "-N" numbers (shadowguard 8001, shade wolf 5001, bound soul 8001, corpse purger 7001, ...). Gold on the
  corpse is the wiki Gold Value (lifted medians within -5..+17 %).
- **Our pets' real hits** come from the "-N" numbers vs the 0xA1 share: the gravebug ~4.5 hits per point
  (450), the phoenix ~6.6 (660). Their attack periods from the attack animations: 1.91 s and 1.80 s;
  their hits from the attributed numbers (gravebug median 76, phoenix 198-238).
- **Monster melee on pets** from the attributed hits (shadowguard 20-44, shade wolf 18-37, nightstalker
  21-79, ...), swing ~3 s; types never fought take the wiki melee x 0.5 (the observed/wiki ratio).
  Casters' spells are [INFERENCE] (the wiki's spell damage x 0.5 every 6 s): none was attributable.
- **Barding**: the wiki formula `max(S/100 - difficulty/100, 0.33 x S/100)` with S = 149 fitted by
  maximum likelihood to the trip's 47 answered attempts; durations and refusal texts as the trip's.
- **Aggro range 10** with sight: the capture's war-mode switches were 1-13 tiles from us (median 7). The
  wiki's flat 20-tile Perception Range put the sim's banshees on us from across the room (6 of 9 deaths).
- **Threat** (revised 2026-10-13 from the trip, docs/NOTES.md "Guarding pets take a monster's aggro"):
  once one of us is within its aggro range, a monster goes for a guarding pet within 6 tiles of us
  (`guard_draw_r`), else the nearest of us. Damage builds threat x 4.0 for the gravebug (Tank) and x 0.3
  for the phoenix, fitted so the gravebug takes most of the damage as live. Our aggressive acts add 400,
  but only before any pet has hurt the monster.
- **Specials, from what reached Logan** (all 23 of his hits were these):
  - Bleeds: 8-10 every 3 s for 5 ticks, then "You are no longer bleeding.".
  - The bound soul's bleed lands on everyone within a tile (an opener of 23-32, 2 per 19 swings with
    Logan beside it) [INFERENCE: area].
  - The vampire bat's bleed hits its target.
  - The nightstalker's Charge: a jump up to 12 tiles every ~8.5 s, 37-42 on up to 3 within a tile.
  - The vampire countess' transfix (three fights over 20261012_210013, 20261013_173449 and _204518):
    an attempt every ~5.3 s; with no copy of hers up half succeed (6 of 12), with one up none (0 of
    5). Each hits the pet for 36-45 at once and makes a hostile copy of it, with the pet's name, that
    wakes 0-2.5 s later, goes for any of us but its original within 3 tiles (live, 4 of its 10 hits
    were on Logan, 28-37 each) and vanishes after 9.9-11 s (n=6). Until then the pet itself stands
    transfixed: no step, no attack.
  - The corpsebride's wail, every 7.6 s: 13-25 on each of us within a tile 75 % of the time (the pets
    beside it live: about 18 of 24), 20 % at 2-3 tiles (Logan: 2 of 11) [INFERENCE: an area hit].
  - The shade wolf's Vanish: 4.3-15 s into a fight, then every 19-22 s, it is unseen and can't be hit
    for 3.5-6 s. It comes back on the target it had, with a swing at once.
- **Barding songs** (2026-10-13, the second trip): a bard skill at the ground plays a song for 15
  minutes, 10 s between songs: less damage to us and the pets from creatures (bleeds and area hits
  too), more healing received, more damage by us and the pets. 15.0 % each on the second trip, 17.7 %
  since the user's Effective Barding links (the crawl, 20261013_204518), which also lengthened the
  bard skills' effects ~18 % (discord 71 s, peace 43 s, provoke 36 s); the scenario has the new values.
- **Moongate menus** open with the last destination selected, as live; the runner then presses Travel
  on the first menu. A double-click on a gate within a tile opens its menu, and Travel works from
  there, as Logan left on the crawl.
- **Pet pace**: one step per 0.21 s out of war mode (n=470 and 471, capture), the pace of our run;
  `calibrate.py` writes it as `step_interval_s_not_fighting` (2026-10-13; 0.3 s before, uncited).
  The world's step and swing timers keep their cadence across its 0.1 s ticks (`World._due`). Before
  that fix every 0.21 s step re-armed from the tick it fired on, so pets walked at 0.3 s anyway and
  fell 9-10 tiles behind a runner leaving a room.

`run.py --runs 24 --minutes 30 --compare`, per hour on the level (sim 8.7 h over 24 runs; the trip 13.1 min):

| | sim | trip |
|---|---|---|
| kills | 66 | 115 |
| damage to us | 1453 | 1703 |
| vet supplies | 121 | 197 |
| heal potions | 26.5 | 27.5 |
| provocations | 0 | 18 |
| discord success | 0.58 | 0.60 |
| peace success | 0.53 | 0.57 |
| gravebug : phoenix damage taken | 6.3 | 9.5 |

The runner kills at about 58 % of Logan's rate there: it explores an unknown floor (85 crawl waypoints)
where he walked straight to the next pack, and waits for mobs to come rather than opening (user
2026-10-12). It never provokes: Logan's 4 provocations went at mobs not yet fighting (an opener).

## What the simulator found (and what was fixed)

- **The crawl never moved with our pets out** (fixed, `crawl.py`): our own pets fell through to the
  monster branch of `Crawl.observe` (threats.identify calls every non-human a monster), so they were
  "targets in reach" and the crawl held at its waypoint for minutes.
- **Pets counted as attackers** (fixed, `loop_hunt.attackers`): a war-mode pet adjacent to us while we
  took damage was "attacking us", so two pets beside us made the two-attacker leave rule fire. Now a
  pet line or a non-attackable notoriety excludes it (also in `tamer.pet_attackers`).
- **Resting after the run's time was up** (fixed, `loop_hunt.go_back`): a leave at the end rested at home
  before noticing; it now stops at once.
- **Pets left behind vs dying while waiting for them** (`tamer.follow`; first calibration, before aggro 10
  and the threat fit): recalling at once after a low-hits leave left a pet more than 3 tiles off behind
  in 13 of 24 runs; standing 3 s for the pets killed us in 8 of 16 (bleeds and ranged hits finish us during
  the wait and the cast). Now a hurt leave
  falls back from the attackers while the pets come (`FALLBACK_*`), a leave for a pet while we are fine
  waits the full 10 s. On the final model 6 of 24 runs still leave a pet behind and 6 of 24 die once in
  30 minutes: the leave thresholds are the open question (below).
- **Thresholds** (24 runs x 30 min each, final model):

  | runner arguments | kills / game h | deaths / game h | runs with a pet left behind |
  |---|---|---|---|
  | defaults (`--leave-at 0.35`, `--heal-at 0.80`) | 58.0 | 0.60 | 6 |
  | `--leave-at 0.5` | 47.5 | 0.23 | 13 |
  | `--leave-at 0.5 --heal-at 0.9` | 52.6 | 0.56 | 11 |
  | `--leave-at 0.6 --heal-at 0.9` | 34.5 | 0.35 | 14 |

  Leaving earlier trades kills for deaths, and every extra leave is another chance to strand a pet.
  Deaths cluster in the shade wolf room at (5180, 710) (several melee mobs at once) and under banshees
  (5205-5210, 705-720). Nothing adopted as a default: the monsters' damage to the player is the least
  calibrated number (23 hits on the trip), see the captures wanted.
- **Recall ping-pong once the vet supplies ran out** (fixed, `tamer.rested`): with no supplies the rest
  counted every pet as rested, so a pet below `--pet-leave-at` made the run leave on arrival, rest,
  recall in and leave again, up to 436 recalls in a game hour (17 of 48 one-hour runs). The rest now
  waits for natural regeneration to `--pet-heal-at` and stops the run past `--rest-timeout`.
- **One-hour runs** (48 x 60 min each, defaults, after that fix):

  | measure | pets step 0.3 s, 59 vet | pets 0.21 s (captured) | + 200 vet supplies |
  |---|---|---|---|
  | game hours | 28.8 | 32.3 | 36.6 |
  | kills / hour on the level (trip: 115) | 69 | 71 | 72 |
  | gold / game hour | 50,800 | 48,700 | 63,100 |
  | deaths / game hour | 0.52 | 0.28 | 0.36 |
  | runs that strand the gravebug on a hurt leave | 10 | 6 | 6 |
  | runs out of vet supplies | 27 | 33 | 0 |
  | median run length (of 60 min) | 38 | 45 | 60 |

  Kills (first batch): banshee 610, shadow minion 371, apparition 220, vampire bat 82, shadowguard 73,
  shade wolf 60, nightstalker 3, reanimated knight 2 (median time from target to kill: shadow minion
  14 s, vampire bat 15 s, shade wolf 18 s, banshee 22 s, shadowguard 30 s, apparition 32 s; 53
  apparitions dropped as unengaged). Deaths in the last batch (13): 4 to a nightstalker and 3 to shade
  wolves, mostly in the room at (5177-5182, 699-711); 3 under banshees (5207, 704-716); 3 to shadow
  minions (5207, 691-695, and one at home after a leave on 30-odd hits).
- **How monsters got onto us instead of the pets** (the first aggro rule, 13 seeds, 240 times a
  monster picked us; replaced the same day, below):
  - 154: **the first sighting**. A monster picks the nearest of us within its 10-tile aggro range
    (guarding pets first, but only those in range and sight). The runner walks first and the pets
    follow 1-3 tiles behind (live too: within 3 tiles at the end of 16 of the trip's 22 10+ step walks),
    so a monster at 9 tiles sees only us. With the pets at 0.3 s a step they fell 9-22 tiles behind
    the runner's 0.20 s run and this was most deaths; the capture's 0.21 s (n=470) halved them.
  - 40: the pets were in range but out of the monster's sight (around a corner or a door).
  - 46: **our aggressive acts**: Discordance/Peacemaking/"All Kill" at it adds 400 threat on us (user
    2026-10-12: aggressive acts pull aggro); a pet must deal 100+ (gravebug, x4) to take it back.
  - It then stays on us: it switches only to a pet with more threat, and both guarding pets go for the
    same monster (the nearest one on us). Seed 2: a nightstalker took us at 9 tiles (pets 12), both
    pets fought the reanimated knight, and the nightstalker hit us 43 then 34 (the knight 23) and
    killed us from full hits in 3 s; the runner had just discorded the knight.
  - Live, that rule is harsher than the server (docs/NOTES.md "Guarding pets take a monster's aggro"):
    on the trip all 11 monsters we hadn't acted on went to a pet first, and none of Logan's 23 hits in
    13 minutes was an ordinary swing or spell from a monster that had picked him. They were bleed ticks
    and openers (bound souls, a vampire bat), one nightstalker Charge (an area hit, knowledge #14625:
    the -42 on him landed with a -37 on the gravebug) and the countess' transfixed copies.
- **The revised aggro and specials** (2026-10-13; 48 x 60 min, defaults, 200 vet supplies):

  | measure | old rule | revised | revised, `--pet-leave-at 0.35` |
  |---|---|---|---|
  | game hours | 36.6 | 41.6 | 44.9 |
  | kills / hour on the level (trip: 115) | 72 | 78 | 79 |
  | gold / game hour | 63,100 | 74,000 | 74,300 |
  | damage to us / hour on the level (trip: 1,703) | 1,321 | 71 | - |
  | heal potions / hour on the level (trip: 27.5) | 23.8 | 0.9 | - |
  | deaths / game hour | 0.36 | 0.17 | 0.18 |
  | runs that strand a pet | 6 | 0 | 1 |
  | runs with a pet death (the gravebug in 10) | 0 | 10 | 2 |

  The risk moved from us to the tank:
  - Leaving at `--pet-leave-at` 0.20 (90 of 450 hits) is too late: in 6 of the 10 the run was already
    leaving for the gravebug (below 20 %) when it died during the "All Follow Me" and the recall,
    with monsters still on it; in 3 it died before the leave. At 0.35, 2 runs of 48 lose it
    instead of 10, at no cost in kills.
  - We take far less than Logan did: 71 an hour against his 1,703. That isn't distance: the runner
    stood within a tile of a fighting monster as often as Logan (13 % of the time a war-mode monster
    was in view vs 12 % live). It is the route: the crawl never reached the bound souls and corpse
    purgers (x 5261-5277, y 645-661) where Logan took two bleeds. His other damage was one countess
    fight, so the trip's 13 minutes are one sample, not a rate.
- **The loop crawl** (`--crawl-loop`, 2026-10-13). This batch includes the pace-grid fix, the shade
  wolf's Vanish and the copies picking any of us. 48 x 60 min, `--pet-leave-at 0.35`, 200 vet
  supplies:

  | measure | zone crawl | loop crawl |
  |---|---|---|
  | game hours | 37.7 | 25.1 |
  | kills / hour on the level (trip: 115) | 78 | 107 |
  | gold / game hour | 74,200 | 79,700 |
  | damage to us / hour on the level (trip: 1,703) | 102 | 686 |
  | heal potions / hour on the level (trip: 27.5) | 1.3 | 7.9 |
  | deaths / game hour | 0.21 | 0.68 |
  | runs with a pet death | 3 | 9 |
  | runs that strand a pet | 3 | 6 |
  | median run length (of 60 min) | 60 | 34 |

  - Looping kills at about Logan's rate and dies three times as often. 7 deaths were to the countess'
    copies (5184-5188, 681-682), where Logan fought and survived by running. 5 were in the
    nightstalker room (5180-5182, 704-715), which Logan never entered: he only saw its wolves and its
    nightstalker from 18 tiles. 3 were to shade wolves, 1 each to a banshee and a corpsebride.
  - The tour (85 waypoints, nearest next from the exit) takes the nightstalker and countess rooms at
    stops 7-11 and the east at 29-33 and 49-80. So no run reached the bound souls before it ended:
    none killed one.
  - Our damage in 8 x 30 min (1,736) was the nightstalker's Charge landing beside the pet it fought
    (867) and the countess' copies (772); the rest was under 40 a source. With guarding pets,
    monsters picked us almost only while the pets were following (the leave) or on "All Kill".
- **With the real way out** (the crimson moongate; 49 x 60 min, `--pet-leave-at 0.35`, careers of 7
  runs on one memory store):

  | measure | zone crawl | loop crawl |
  |---|---|---|
  | game hours | 22.9 | 4.3 |
  | kills / hour on the level (trip: 115) | 80 | 87 |
  | deaths / game hour | 1.14 | 10.07 |
  | runs with a death / a pet death (of 49) | 26 / 24 | 43 / 41 |
  | median run length (of 60 min) | 19 | 3 |

  - Most deaths happen on the walk out: 24 of the zone crawl's ended "moongate: died", 42 of the
    loop's. They cluster in the countess' room (5175-5190, 680) and at the gate (5200-5205, 695-700).
  - The gate is reachable from the west rooms only through the countess' room, 50-110 steps; from the
    arrival, through the nightstalker room (114 steps from (5207, 721)).
  - The loop reaches the nightstalker room 3 minutes in, takes the first leave there and dies on the
    way out in almost every run. A career's memory doesn't save it: it dies before it has a fight to
    learn from.
  - With the same rules, the zone crawl's death rate went 0.18 -> 0.66 a game hour (fresh memory)
    once recall-anywhere was gone.
- **Heal in place, out of the nightstalker room** (user 2026-10-13; the scenario's `runner` block then
  added `--heal-in-place --crawl-avoid 5170 690 5188 718`, the room reopened later the same day, below;
  49 x 60 min, careers, `--pet-leave-at 0.35`):

  | measure | zone crawl | loop crawl |
  |---|---|---|
  | game hours | 35.4 | 18.1 |
  | kills / hour on the level | 80 | 84 |
  | gold / game hour | 74,000 | 66,600 |
  | deaths / game hour | 0.39 | 2.10 |
  | runs with a death / a pet death (of 49) | 14 / 7 | 38 / 22 |
  | leaves / game hour | 1.0 | 2.1 |
  | median run length (of 60 min) | 60 | 18 |

  - The loop now reaches the east: 19 bound souls and 13 corpse purgers killed.
  - It still dies too often. 25 of its 38 deaths are on the walk to the gate, clustered at
    (5200-5210, 690-700), the banshee room around the gate, and in the countess' room.
  - Before the loot fix (below), 19 runs ended on "to the corpse: no route".
  - With heal potions between steps on the walk out (user: "I just run and heal and hope for the
    best, with pets guarding"; `drink_on_the_run`), 49 more runs: 10.1 potions a hunting hour (was
    6.9) but deaths 2.35 a game hour, no better. The walk out is as dangerous for a real player
    (user: banshees catch players on their way to the gate too).
- **An unreachable corpse ended the run** (fixed, `loop_hunt` loot): the walk to it raised Abort("no
  route"). Now that corpse is left.
- **The second trip's model, and the barding songs** (capture 20261013_173449; 2 x 49 x 60 min per
  arm, seeds 0 and 1000, careers, `--pet-leave-at 0.35`, heal in place, out of the nightstalker room).
  The transfixed pet now stands out of the fight for the copy's ~11 s, one copy at a time; the
  corpsebride wails; `--songs` (default on) keeps the three songs up.

  | measure | zone, songs | zone, no songs | loop, songs | loop, no songs |
  |---|---|---|---|---|
  | game hours | 78.3 | 80.8 | 40.7 | 33.6 |
  | kills / hour on the level | 85 | 82 | 89 | 87 |
  | gold / game hour | 78,400 | 75,700 | 67,300 | 69,400 |
  | deaths / game hour | 0.33 | 0.31 | 1.70 | 2.11 |
  | runs with a death / a pet death (of 98) | 26 / 7 | 25 / 13 | 69 / 38 | 71 / 46 |
  | damage to us / hunting hour | 121 | 118 | 946 | 909 |
  | damage to the pets / hunting hour | 8,479 | 11,027 | 18,628 | 20,294 |

  - The songs spare the pets: about half the runs with a pet death in the zone crawl, a fifth fewer
    deaths in the loop. Our own damage doesn't fall: most of it is now the corpsebride's wail, the
    transfixed copies and, in the loop, the nightstalker (174 an hour, though the crawl stays out of
    its room) [INFERENCE: its Charge reaches 12 tiles, onto us passing the room on the way to the gate].
  - The loop still dies five times as often as the zone crawl.
- **With the crawl's updates** (20261013_204518: the barding after the links, the transfix hit on the
  pet, the gate double-clicked from a tile, songs on the way in, the teleporter pet fix; same 2 x 49 x
  60 min per crawl, songs on): zone 83 kills a hunting hour, 0.34 deaths a game hour, 10 runs of 98
  with a pet death; loop 84 kills, 1.47 deaths, 35. No run failed at a teleporter (2 of 14 had before
  the fix).
  - Against Logan's crawl on the same level (12.7 min, his loop plus the nightstalker room and the
    east): 137 kills an hour, ~780 damage an hour to him, no death. The simulated loop takes about as
    much damage (961) but kills 40 % slower and dies. He fought nearly without a break (595 of 764 s,
    6 engagements, the longest 11 kills); the gap is in how fast the next fight starts and ends, not
    in the damage [INFERENCE].
- **The nightstalker room open, and aggro management** (user 2026-10-13; `aggro.py`, docs/HUNT_LOOP.md
  "Aggro"; the vampiric consort spawned where Logan met one; 98 x 60 min per arm, `--jobs 16 --careers
  14`, `--pet-leave-at 0.35`; "off" = `--pull-budget 0 --no-reposition --never-aggro none`):

  | measure | zone, aggro on | zone, off | loop, aggro on | loop, off |
  |---|---|---|---|---|
  | deaths / game hour | 1.10 | 0.81 | 3.19 | 3.85 |
  | runs with a death / a pet death | 50 / 47 | 42 / 44 | 45 / 73 | 45 / 79 |
  | kills / hunting hour | 79 | 80 | 89 | 91 |
  | median run (min) | 21 | 26 | 3 | 3 |
  | the consort's damage on the pets | 0 | 0 | 108 | 843 |

  - With the room open both crawls die far more (the zone crawl had 0.34 deaths and 10 pet-death runs
    with it closed). Most pet damage in the loop is the nightstalker's (6,000 an hour).
  - The aggro rules keep the consort off (843 -> 108) and help the loop a little; the zone crawl is
    within noise or worse.
- **The gravebug's Devour mid-fight** (fixed 2026-10-13 on the user's question; docs/NOTES.md "Aggro
  management"): the simulator fed it 36 every 14 s and only out of a fight; live it feeds 54 every
  15 s, mostly mid-fight. Same arms after the fix:

  | measure | zone, aggro on | zone, off | loop, aggro on | loop, off |
  |---|---|---|---|---|
  | deaths / game hour | 0.81 | 0.88 | 3.54 | 3.90 |
  | runs with a death / a pet death | 43 / 50 | 44 / 47 | 49 / 71 | 51 / 75 |
  | kills / hunting hour | 84 | 82 | 101 | 83 |
  | gold / game hour | 74,200 | 72,700 | 69,300 | 52,800 |
  | median run (min) | 28 | 23 | 4 | 3 |

  - The aggro rules now give the loop more kills and gold (+31 %) and slightly fewer deaths; the zone
    crawl is even. Both still die far more than with the room closed.
  - An earlier reading that the simulator overrates two monsters on the tank (19.6/s vs live 8.5/s) came
    from a small sample of early-death runs; on longer runs it is 5.9/s. The room's toll is more likely
    how the runner walks in: from the south past the stalker and the knight, waking both, where Logan
    held at the north edge and let them come in stages [INFERENCE].
- **Hold at first sight** (`--hold-first-sight`, 2026-10-14; docs/HUNT_LOOP.md "Aggro"): with nothing
  fighting, stop where an idle monster first sees us and let it come, as Logan did at the nightstalker
  room. 98 x 60 min per arm, the other aggro rules on:

  | measure | zone, hold | zone, no hold | loop, hold | loop, no hold |
  |---|---|---|---|---|
  | deaths / game hour | 0.51 | 0.81 | 2.23 | 3.54 |
  | runs with a death / a pet death | 29 / 40 | 43 / 50 | 49 / 61 | 49 / 71 |
  | kills / hunting hour | 82 | 84 | 97 | 101 |
  | gold / game hour | 71,100 | 74,200 | 64,600 | 69,300 |
  | median run (min) | 32 | 28 | 6 | 4 |

  - A third fewer deaths in the zone crawl and over a third fewer in the loop, for 4-7 % less gold.
  - The loop still dies four times as often as the zone crawl; the nightstalker still does most of its
    pet damage (4,800 an hour).
- **The pull spot** (2026-10-14): holding now starts a tile outside the first monster's aggro range,
  and the runner first steps (up to 3) to where the fewest of the waiting monsters (by weight, at least
  one) would see it. Same arms, hold on: zone 0.47 deaths a game hour (0.51 without the spot), 28 runs
  with a death, 46 with a pet death (40), 88 kills a hunting hour (82), 78,300 gold a game hour
  (71,100); loop 2.14 deaths (2.23), 40 runs with a death (49), 62 with a pet death (61), 95 kills
  (97), 63,700 gold (64,600). More kills and gold in the zone crawl, fewer runs with a death in the
  loop; the pet deaths didn't move. The nightstalker still does 5,300 an hour on the pets in the loop.
- **The 2026-10-14 runs** (docs/NOTES.md "Logan Wolf's Mausoleum runs of 2026-10-14"): the reanimated
  knight now charges like the nightstalker (live -32 on Logan once in 3 charges) and the character
  carries 15 Adventurer's Ropes (the simulator answers `[Rope` with the prompt and a cursor, jumps up to
  10 tiles in sight, takes the following pets along, 5 s cooldown). Same arms: zone 0.40 deaths a game
  hour, 24 runs with a death, 42 with a pet death, 87 kills a hunting hour, 77,100 gold; loop 2.47, 50,
  59, 96, 65,500. The knight's charges do 69 (zone) and 216 (loop) an hour to us. The ropes don't run:
  no players in the simulator, so no flight from one.
- **Faster from one fight to the next** (2026-10-14, user: "can you make it move from one fight to the
  next faster?"). The zone crawl spent 30 % of its time on the level holding at cleared waypoints
  (`--crawl-depleted-s` 90 s, a virtual-time profile of the runner); the pets style now waits 5 s
  there (`--crawl-dwell` 1.5), and doesn't walk on while a vet channel runs or a pet in reach still
  needs one (`tamer.Team.busy_healing`). 98 x 60 min: zone 98 kills a hunting hour (87), 85,100 gold
  a game hour (77,100), 32 runs of 98 with a pet death (42), but 1.17 deaths a game hour (0.40);
  loop 99 kills (96), 67,400 gold (65,500), 50 pet-death runs (59), 1.86 deaths (2.47).
- **What killed us at the faster pace** (2026-10-14): the crawl opened its deep zones sooner and spent
  more time there (zone 4 opened 28 times in the 98 runs against 16; 37 countess kills against 11), and 46
  of the 54 deaths came after a leave at 35 %: about half 20-29 tiles from the gate in the countess and
  corpsebride rooms 1-25 s after it, 13 at the gate itself waiting for the pets. Arms (98 x 60 min each,
  same seeds; the runs are deterministic per seed):

  | arm | kills/h | gold/game h | deaths/game h | runs with a death |
  |---|---|---|---|---|
  | fast pace, leave 35 %, zones <= 30 | 103 | 89,300 | 1.25 | 53 |
  | old pace (90 s / 6 s), leave 35 % | 83 | 72,500 | 0.58 | 38 |
  | fast, zones <= 15 | 91 | 84,700 | 0.64 | 38 |
  | fast, leave 50 % | 106 | 89,900 | 0.79 | 37 |
  | fast, leave 60 % | 103 | 86,000 | 0.93 | 43 |
  | fast, leave 50 %, zones <= 20 | 100 | 89,100 | 0.54 | 29 |
  | fast, leave 50 %, zones <= 15 | 92 | 84,700 | 0.65 | 36 |
  | old pace, leave 50 % | 86 | 75,100 | 0.67 | 41 |

  The pets style now leaves at 50 % and opens a zone only below 20 hits lost a minute (STYLE_DEFAULTS).
  With everything below too (the vet resurrection, the daemon, the purger, the souls, Maus 4): zone 95
  kills an hour, 86,600 gold a game hour, 0.48 deaths (31 runs), 24 runs with a pet death, 11 pets
  vetted back; the old defaults in the same world: 104, 92,600, 0.82 (48). Loop: 100, 71,700, 1.51.
  The same code without Maus 4's links (a different random draw) gave 0.62 deaths: differences of 0.1-0.15
  a game hour are within the noise of 98 runs.
- **A target that left the view alive counted as a kill** (fixed, `HuntLoop.scan_events`): a 0x1D
  without the 0xAF of a death (a shade wolf's Vanish) was a kill, so the runner logged 4,967 kills for
  the simulator's 4,220 and walked off mid-fight; now 4,310 for 4,078.
- **New in the world** (captures 20261014_210020 and _221341, docs/NOTES.md): the forgotten souls (4 at
  the consort, their fire marks 0x6D70, no damage), the reanimated daemon (its bone burst on each of us
  within 5: pets 94-107, us 39-49, every ~15 s), the corpse purger's disease (2 of 3 barrage hits; -21
  every 5 s, 4 ticks, stacking), the vet resurrection of a pet's ghost (31 %), Maus 4's two links and four
  of its monsters (wiki stats; no fight there yet), the second level's hop at (5512, 669).

## Limits

- The fight level is modelled whole, but the first two levels only along Logan's way in: his
  teleporters and the monsters first seen near it.
- No other players or tamers (the trip saw six players), no item loot beyond gold, no skill gain,
  no pet levels or xp, no Society job. The vampire's feeding heal isn't modelled and its drain and
  the poisons are generic; the bound soul's bleed as an area on everyone within a tile, the
  transfixed copies' hits (2000) and their pick among us within 3 tiles are [INFERENCE].
- Not modelled: Frenzy, seen live as "*frenzies*" on a shade wolf and the nightstalker (+10 % attack
  speed, knowledge #14625). Nor the nightstalker's Vanish and Backstab: its one fight never showed them.
- **Entering and leaving the Mausoleum** (since 2026-10-13). Before that the simulator let the runner
  recall out from anywhere, so every earlier leave, death and stranded-pet number above is too kind.
  - Recall works only outside the dungeon or within 2 tiles of a gold gate (`dungeon`; user).
  - The tome lands at (5626, 688) beside one.
  - The four teleporters and the crimson moongate's menu are as captured (docs/NOTES.md "Getting in
    and out of the Mausoleum").
  - Not known: the gold gates' menus and the ways back up.
- Pet AI is a model: Follow ignores attackers, Guard takes whatever attacks us or the pets, Kill holds
  its target, pets within 3 tiles come along on a recall (ServUO's TeleportPets range [INFERENCE]).
- The rune tome's charges aren't spent (36 on the trip); the ping-pong above would have emptied it.
- Monster positions and respawns aren't carried across a career's runs: only the runner's memory is.

## Captures wanted (live, on Logan Wolf)

Each is a normal play session with the proxy capturing as always; tell the coding agent the times.

0. **Two or three monsters on the gravebug at once** (the nightstalker room as on the crawl is ideal,
   barding as usual): the captures have 64 s with two on it and 10 s with three or more, too little to
   check the simulator's piles (docs/NOTES.md "Aggro management").
1. **One creature type at a time**, Peacemaking off, pets on guard: kill 3+ of each of shadowguard,
   nightstalker, shade wolf, banshee, bound soul, vampire. Gives monster swing period and damage per hit
   on pets and on us, and the casters' spell damage and cadence (none could be attributed: 2-4 types
   were usually fighting at once, and monster attack animations are rarely sent).
2. **Aggro range**: pets on "All Stay" out of the way (or stabled), walk toward an idle monster in plain
   sight and stop at 14, 12, 10, 8 and 6 tiles for ~10 s each; 2-3 types. The sim's aggro range 10 is
   inferred from censored data.
3. **Phoenix vs gravebug damage**: "AlaFlambe kill" on one monster with the gravebug on stay, and the
   reverse (428 of the trip's numbers couldn't be attributed).
4. **Damage to us**: let one monster hit us for a while at full hits with potions ready (e.g. open with a
   discord on a lone shade wolf). The trip had only 23 hits on us; the sim's deaths hinge on this number.
5. **Respawn**: clear a room and stay 20+ minutes nearby (one weak candidate, 123 s, on the trip).
6. **The vampire countess, a few more fights** (user offered, 2026-10-13; one more came in
   20261013_173449, cut short by two reds): the copies' hits (whom and how much: the second fight's copy
   reached the phoenix while the bat and the bride hit it too) and her own melee and spells. The
   timing (attempts, one copy at a time, ~11 s, the pet frozen meanwhile) is now from 2 fights.
7. **The rest of the level** (and the way back out): walk every room of the fight level once, and the
   teleporters from it. The sim has 42 spawns from where Logan happened to see monsters.
8. If it happens anyway (don't force it): a full stable/claim at home (the pet death and its vet
   resurrection came in 20261014_221341, one try).

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
  - The vampire countess' transfix: an attempt every ~5.3 s, 3 of 7 succeed. Each makes a hostile copy
    of a pet, with the pet's name, that goes for any of us within 3 tiles (live, 4 of its 10 hits were
    on Logan, 28-37 each) and vanishes after 4-10 s.
  - The shade wolf's Vanish: 4.3-15 s into a fight, then every 19-22 s, it is unseen and can't be hit
    for 3.5-6 s. It comes back on the target it had, with a swing at once.
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
- **Heal in place, out of the nightstalker room** (user 2026-10-13; the scenario's `runner` block now
  adds `--heal-in-place --crawl-avoid 5170 690 5188 718`; 49 x 60 min, careers, `--pet-leave-at 0.35`):

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
6. **The vampire countess, a few fights** (user offered, 2026-10-13): the transfix attempts and copies
   (how often, how long, whom they hit and for how much), and her own melee/spells. 7 of the loop's
   deaths and much of our damage are hers; the copy model rests on one fight (3 copies).
7. **The rest of the level** (and the way back out): walk every room of the fight level once, and the
   teleporters from it. The sim has 42 spawns from where Logan happened to see monsters.
8. If it happens anyway (don't force it): a pet death and its resurrection, and a full stable/claim at
   home, so the runner can learn both flows.

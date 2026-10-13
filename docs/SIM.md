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
  --enter-rune Mausoleum --crawl --timeout <minutes> --quiet --triage-url ""`; a fresh temporary memory
  store per run. The character starts at home (the DTF landing) with both pets, recalls in, hunts, and
  goes home and back by the runner's own rules. `--at-hunt` starts on the level instead.
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
- **Threat**: guarding pets take a monster's aggro first (knowledge #2074); damage builds threat x 4.0
  for the gravebug (Tank) and x 0.3 for the phoenix, fitted so the gravebug takes most of the damage as
  live; our aggressive acts add 400.
- **Pet pace**: one step per 0.21 s out of war mode (n=470 and 471, capture), the pace of our run;
  `calibrate.py` writes it as `step_interval_s_not_fighting` (2026-10-13; 0.3 s before, uncited).

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
- **How monsters get onto us instead of the pets** (13 seeds, 240 times a monster picked us):
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

## Limits

- One level, as walked on the trip; other rooms, other levels and the teleporters between them aren't
  modelled (the tome row lands on the level's entry).
- No other players or tamers (the trip saw six players), no item loot beyond gold, no skill gain,
  no pet levels or xp, no Society job. The vampire countess' transfix (hostile copies of our pets) and
  the vampire's feeding heal aren't modelled; bleed/drain/poison are generic.
- Where recall works inside the Mausoleum is unknown: allowed anywhere (`recall.allowed_from` null).
- Pet AI is a model: Follow ignores attackers, Guard takes whatever attacks us or the pets, Kill holds
  its target, pets within 3 tiles come along on a recall (ServUO's TeleportPets range [INFERENCE]).
- The rune tome's charges aren't spent (36 on the trip); the ping-pong above would have emptied it.
- Each run starts with an empty memory database, so the crawl's learned avoidance (e.g. "fled from it
  in 37% of fights") never carries over between runs as it would live.

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
6. **Recall inside the Mausoleum**: try the rune tome on the fight level, the middle level and the arrival;
   note where it is refused.
7. **The rest of the level** (and the way back out): walk every room of the fight level once, and the
   teleporters from it. The sim has 42 spawns from where Logan happened to see monsters.
8. If it happens anyway (don't force it): a pet death and its resurrection, and a full stable/claim at
   home, so the runner can learn both flows.

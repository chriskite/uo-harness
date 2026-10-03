# Hunt loop (`harness/loop_hunt.py`)

A programmatic task like the lumber loop: fight monsters at one spot, heal, loot, leave when
hurt. Built for New Player Dungeon mongbats (user decision 2026-10-01: the thresholds are task
arguments). With `--crawl` it patrols the dungeon floor instead of standing on one spot ("Crawl"
below). Start it with `ctl run hunt [args…]` (docs/OVERSEER.md) or directly:

```
python harness/loop_hunt.py --kills 5                 # standing in the NPD
python harness/loop_hunt.py --enter --kills 5         # from outside the entrance
python harness/loop_hunt.py --enter --fight-spot 5536 509   # deeper in, away from the crowded exit
python harness/loop_hunt.py --enter --crawl --pull-range 8  # patrol the floor ("Crawl")
```

## Arguments

| Argument | Default | Meaning |
|---|---|---|
| `--spot X Y` | `5535 529` | The exit spot (the NPD exit tile): leaving walks here, then takes the exit step. |
| `--fight-spot X Y` | `--spot` | The tile to fight on. Each visit walks there; the pull range, the corpse range and the idle return are measured from it. Candidates below ("Fight spots"). |
| `--crawl` | off | Patrol the floor instead of one fight spot ("Crawl"); not with `--fight-spot`. The pull range, the corpse range and Stationary Penalty walks are measured from where we stand. |
| `--crawl-band` / `--crawl-floors` | `40` / `0` | Route steps from the exit per level; at most this many levels (0: all). |
| `--crawl-dwell` / `--crawl-depleted-s` | `6` / `90` | A waypoint where nothing showed up is looked over for about this long (× 0.6–1.4); an area where targets came is left as depleted after this long without one. |
| `--crawl-learn-s` | `600` | Seconds observed in a level (store history included) before the next one may open. |
| `--crawl-max-dmg` / `--crawl-depth-risk` | `30` / `1.5` | The most hits lost per minute a level may cost (estimated; for an unvisited level: the level above's × the risk factor). |
| `--crawl-max-leaves` | `2` | Survival leaves from a level in one run that close it again. |
| `--crawl-max-hits` / `--crawl-max-fight-s` | `0.5` / `180` | Avoid a creature type costing more than this share of our max hits per fight, or taking longer than this per kill (estimated). |
| `--exit-dir D` | `4` (south) | The step from the spot onto the exit teleporter. |
| `--enter` | off | Start outside: walk to `--entry`, step `--entry-dir` to teleport in. |
| `--entry X Y Z` / `--entry-dir D` | `1912 2557 -20` / `0` | The tile before the NPD entrance teleporter and the step that triggers it. |
| `--heal-at` | `0.75` | Heal below this share of max hits (healing.py: a heal potion if one can be drunk, else a spell). |
| `--gheal-min-missing` | the mana break-even for your Magery (19 at 60) | Missing hits from which the heal spell is Greater Heal; below it, Heal. |
| `--leave-at` | `0.60` | Leave below this share of max hits (plus the fight spot's route margin). |
| `--leave-per-step` | `0.004` | The route margin: `--leave-at` + this × the steps of the route from the fight spot back to `--spot`, at most `--heal-at` − 0.05. |
| `--leave-multi-at` | `0.80` | Leave below this share when two or more mobs are attacking. |
| `--mana-reserve` | `22` | Mana kept for heals (two Greater Heals): the attack spell only while mana ≥ reserve + its cost. |
| `--spell` | `lightning` | Attack spell (Magery name or 1-64). |
| `--target-name` | `mongbat` | Words the monster's name (or click label) must contain. |
| `--pull-range` | `3` | Engage mobs within this many tiles of the spot. At most 10 (the spell range): a larger value is capped, with a log line. |
| `--kills N` | `0` | Stop after N kills (0: until `--timeout`). |
| `--timeout S` | `3600` | Then finish the fights on us, loot, and leave. 180 s past it the runner aborts wherever it is. |
| `--rest-to` | `0.95` | After leaving: Heal / Greater Heal when one can be cast (no potions; else natural regeneration) outside to this share of hits (and `--mana-reserve` mana), then go back in. `0`: stop after leaving. `--rest-timeout` (900 s) bounds the rest. |
| `--reposition-s` | `240` | No step for this long: walk 2–4 steps out and back to the fight spot before the Stationary Penalty comes (300 s); with nothing on us already from a draw in 0.8–1 × it. |
| `--loot` / `--no-loot`, `--loot-max` | on, 25 | Loot our kills' corpses (items per corpse). |
| `--human`, `--seed`, `--human-fast`, `--no-map`, `--quiet`, `--triage-url`, ports, `--memory` | | As in the lumber runner. |

## What it does

Each tick re-reads the proxy state and decides, in this order:

1. **Leave** when a rule fires (`threat` juncture, job event `leave`):
   - a hostile player (threats.py: red/grey/orange) within 12 tiles: urgent; the run stops outside;
   - two or more attackers and hits below `--leave-multi-at`;
   - hits below `--leave-at`, raised for a fight spot away from the exit (below).
   Leaving is war mode off (if on), the walk back to `--spot`, then the step in `--exit-dir`. The
   runner expects a teleport: a deny followed by the move (the NPD exit, live), or a confirmed step
   and the move right after. Outside it rests and goes back in (except after a hostile player).
2. **Speech hold** (speech_guard.py, as in the lumber runner) once no fight is on: nothing is sent
   until the overseer acks `speech_nearby`. Leaving to survive overrides the hold.
3. **Heal** below `--heal-at`, one heal per tick (`harness/healing.py`, shared with `ctl act heal`;
   user decision 2026-10-02):
   - **A heal potion whenever one can be drunk**: any heal potion (graphic `0x0F0C`) in the
     backpack at any bag depth, smallest stack first, its bags opened first, the stock
     double-click. "Can be drunk" means not at full health and 10 s since the last drink (the
     runner's own clock). If the server still refuses (cliloc 500235, e.g. after a drink in the
     client), the clock restarts and a spell goes out in the same tick.
   - **Otherwise a spell by the missing hits**: Greater Heal ((40–50) × Magery/100 for 11 mana)
     once the missing hits reach `--gheal-min-missing`, else Heal ((10–12) × Magery/100 for 4
     mana; wiki Magery). The default threshold is the mana break-even, where Greater Heal
     restores more hits per mana than Heal: Heal's average × 11 / 4 (19 at Magery 60). A spell
     is only chosen when it can be paid for: its mana and its reagents (one each: Heal garlic,
     ginseng, spiders' silk; Greater Heal those plus mandrake root) in the backpack at any bag
     depth, or a spellstone (an item named "…spellstone…" or "…bauble…") instead of reagents
     (`combat.can_cast`, also used by `ctl act heal` and the recall escape). If the chosen spell
     can't be paid for, the other one is cast.
   - **No potion and no castable heal spell** (mana, reagents): one `low_supplies` juncture per
     visit (`need` lists the missing Heal reagents); nothing is cast.
   - **Resting outside:** spells only, when one can be cast; otherwise natural regeneration
     (no cast attempts). Mana regenerates for free there, while potions cost gold.
   - **"More reagents are needed for this spell."** (cliloc 502630: the server disagrees with
     our count): that spell isn't cast again until the next visit (heals and the attack spell).
4. **Stationary Penalty** (below): when it is on, walk it off at once (mid-fight too); when no
   step was taken for `--reposition-s`, reposition.
5. **Loot** our kills' corpses when nothing is attacking us, like `ctl act loot`: human corpses
   refused, walk within 2 tiles, open the backpack (stock dclick) and the corpse, then lift + drop
   each item into the backpack, gold first, up to the weight limit.
6. **Fight** the target:
   - Targets come only from the live world model (`world.mobiles` holds what the stock client still
     has; docs/WORLDMODEL.md) and pass the `ctl act attack` guard (`combat.attackable`: threats.py
     monster, notoriety 3-6, on screen). Serials the server reported dead (`mobile_death`) are never
     targeted.
   - A mob attacking us comes first: the current one, else the one with the lowest hits.
     Otherwise the nearest within `--pull-range` of the fight spot. "Attacking us" (`attackers()`,
     within 10 s): a `0x2F` swing at us in `world.swings`, **or the mob our own latest swing is
     at, or a war-mode creature adjacent to us while we take damage** ("-N" overhead text or a
     `0x0B`). Outlands has never sent a `0x2F` with us as the defender (all 2172 stored swings
     are our own, no `0x0B` either), so live only the last two work: the server turns our swings
     on whoever attacks us.
   - Attack once (war mode on, `0x34` unless the client has a status request outstanding, `0x05`),
     and once more when the mob first comes adjacent (the overseer's NPD procedure).
   - The attack spell while mana ≥ `--mana-reserve` + its cost, its reagents (Lightning:
     mandrake root, sulfurous ash; table `combat.SPELL_REAGENTS`, ClassicUO SpellsMagery.cs) or a
     spellstone are in the backpack, and the mob is within 10 tiles; else melee (the visit start
     logs "melee only" when the reagents are missing). Cast, wait for the cursor, a human aim
     pause, **re-read the state**, then `0x6C` on the mob's current tile. If the mob is gone
     meanwhile, or a leave rule fired while aiming, the cursor gets the stock Esc `0x6C` instead
     (ANTICHEAT.md §10 A12). "That is too far away" / "cannot be seen" means melee only at that
     mob for 10 s.
   - **A pinned target is dropped:** one that is neither adjacent nor took damage for 15 s
     (`PIN_S`) is disengaged and passed over for 60 s (`SKIP_S`) unless it attacks us. The runner
     never walks to melee.
7. **Idle**: war mode off when nothing is near, back to the fight spot, wait.

**A fight spot away from the exit** (`--fight-spot`, user request 2026-10-04: the exit tile is
crowded). Each visit (at the start and after every re-entry) walks from the arrival to the
fight spot with the Mover (map route) before fighting; leaving walks back to `--spot` and
takes the exit step there.
- **Teleporter tiles**: the Mover never routes over a known teleporter tile (except as the goal
  tile itself): the memory store's `teleporters` table, learned whenever a step lands somewhere
  else, plus, for the session, the tile one `--exit-dir` step from `--spot` (the exit, known
  from the arguments). The NPD arrival tile (5536,530) is an exit teleporter too (Live below)
  and in the table since 2026-10-02, with (5535,530) and (5537,530).
- **The leave margin**: nothing heals during the walk out, and mobs follow. On reaching the
  fight spot the runner plans the route back to `--spot` and leaves below
  `min(--leave-at + --leave-per-step × steps, max(--leave-at, --heal-at − 0.05))` for that
  visit; the cap keeps a band where heals still go out. Defaults: 20 steps → 68 %, 49 steps
  → 70 % (capped; raise `--heal-at` with it for deep spots). 0.004 per step is ~0.4 hits per
  running step for 100 hits [INFERENCE: about two mongbats on us while running]. The episode
  row records `route_steps` and `leave_at`. The other rules are unchanged: a hostile player
  is urgent at once, and two attackers leave below `--leave-multi-at`.
- **A survival leave from a fight spot runs** (Mover `urgent`: no pauses, sidesteps or
  reading waits); the planned end (`done`, time up) walks as usual.

### Fight spots (map0, from the exit spot 5535,529)

Map routes with the known teleporter tiles avoided (`pathfind.plan`, 2026-10-04); none
crosses the arrival tile (all come in from the north, via 5536,528):

| Fight spot | Steps out | Leave below (defaults) | Where |
|---|---|---|---|
| `5536 509` | 20 | 68 % | North corridor |
| `5539 507` | 22 | 69 % | North corridor, a little further |
| `5515 518` | 49 | 70 % (capped) | The west room |

(5537,505), further up the north corridor, is a wall / skull pile: no route. Not yet hunted
live; how many mongbats come by each is unknown.

**The weapon** (since 2026-10-04): the item on layer 1 or 2 at the start stays in hand.
- **An arcane staff takes casting skill.** Wiki [Arcane](https://wiki.uooutlands.com/Arcane):
  "Players with at least 80 skill in Arcane, Wrestling, and Magery can continue to cast spells
  while wielding an Arcane Staff". Live 2026-10-04 (capture 20261003_113952) each Lightning
  cast by Shackleworth (Arcane 60, Magery 60, Wrestling 80) moved his prismatic staff
  (graphic 31038, the only arcane staff known: `ARCANE_STAFF_GRAPHICS`) from layer 2 into the
  pack, `0x1D` + `0x25` about 50 ms after the cast request, without any message.
- With an arcane staff and Arcane (skill 8), Magery or Wrestling below 80 (unknown counts as
  below), or once any cast of ours put the weapon in the pack: **no attack spell** (melee with
  the weapon; the staff's Arcane Buildup is the damage), and **inside, no heal spell while a
  heal potion is in the pack**: it waits for the potion cooldown. With no potion left a heal
  spell still goes out. Outside, the rest casts heals as before.
- **Re-equip**: whenever the weapon is in the pack (not mid-cast), and after the rest before
  going back in, it is put back on with the stock drag `ctl act equip` sends
  (`combat.equip_packets`: its containers opened first, `0x07` lift, a human drag pause,
  `0x13` on its layer; the prismatic staff's tiledata layer is 0, so the layer comes from
  `world.worn_layers` or `combat.KNOWN_LAYERS`).

A kill is our target's `prune` (why dead) or `0x1D`, or its `mobile_death` (S2C `0xFF` sub `0xDEAD`,
which also names the corpse). Live, an in-view kill is `0xAF` + `0x1D` + `0xDEAD` at once
(capture 20261001_214649).

Packets come from `harness/combat.py`, shared with `ctl act attack/target/loot/cast` so the two can't
drift: `attack_packets`, `target_mobile`, `target_self`, `grab_packets`, `loot_order`,
`human_corpse`, `attackable`, `spell_id`.

## Stationary Penalty (user request 2026-10-04; `harness/stationary.py`)

Outlands puts a debuff on a character that stands still: Outlands buff `0xFF` sub 8, icon 277,
title "Stationary Penalty", "All damage is reduced to 1. Move {value} more steps to remove this
effect" (the wiki's Mining page: it also stops mining "until they move more than 5 steps"). The
runner fought under it for most of some visits (live 2026-10-02: 99 of 165 min in the NPD).

**Measured** (2026-10-04, the timed replays of the captures in `logs/` with the penalty, 26
of them, and the memory store):
- **Standing still: 301.0–314.8 s after our last one-tile step**, in all 40 such cases (the
  ~15 s spread looks like a periodic server check [INFERENCE]). Fighting and casting don't count
  as activity: the runner got it at the exit spot mid-hunt (12:42, 12:56, 13:08, 13:22 on
  10-02, each 301–315 s after its last step). Teleports don't reset the clock either: 13:36:39
  on 10-02 it came 29 s after the NPD exit teleport, 310 s after the last step.
- **At login** (every login, ~1 s in, the update sent 4×) and **at once after most other
  teleports** (recalls, moongates and the like 23/28, leaving a rental room 21/22), **but never
  on the NPD entrance or exit** (0/73) or entering a rental room (0/22).
- **Clearing:** the buff's one timer value is `{value}`, the steps still to walk: 5 when it
  comes, then a re-send with 4, 3, 2, 1 on each step that changes our tile, and the removal
  (sub 9) on the 5th. f1 4620 and f2 1 never change (539 updates). 87 removals came after
  exactly 5 tile changes (runs count; the count survives the NPD teleport), one after 10 (a step
  after 13 min standing with 1 left set it back to 5), 3 at a jump into a rental room. A step
  back onto the tile just left counts: 10-02 14:00:54 (5535,528) → 14:01:31 (5535,529) went 2 → 1.
- **What it did to our damage doesn't show.** The overhead numbers on our Lightning's target
  within 2.5 s of the cast (10-02): 174 with it, 138 without, in both mostly 27–35 (plus "-2"s,
  likely disease ticks [INFERENCE]); "-1" 3 times with it. Shackleworth's staff melee with it:
  31–44 (10-03). Kills per minute in the NPD: 0.43 with it, 0.44 without (10-02, 99 / 66 min);
  0.48 / 0.40 (10-03); looted gold per minute 6.2 / 5.5 (10-02). Whether it bites only in PvP or
  on harvesting is unknown [INFERENCE]; the runner clears it anyway, 6 steps are cheap.

**The rule** (tick order: after the leave rules, the speech hold, the re-equip and the heal;
before loot and fight):
- **On:** walk it off at once, mid-fight too: the steps it asks for + 1, out to a tile
  ⌈(steps + 1) / 2⌉ (else one more) from the fight spot and straight back (Mover routes, human
  pacing). The out tile is never a known teleporter tile (the exit and arrival tiles, the
  `teleporters` table) nor occupied; tiles we have stood on before (walk memory) are tried
  first, and one whose whole route runs over them is taken (the Mover never routes over a known
  teleporter). Up to 3 such walks; if it is still on, again after 20 s.
- **Before it comes:** no step for `--reposition-s` (240 s, 60 s under the measured 301 s): 2 or
  4 steps out and back; with nothing on us and not engaged, already from a draw in 0.8–1 × it
  (192–240 s). The clock is the Mover's: a step that changed our tile; teleports don't count
  (they don't reset the server's either).
- A survival leave comes first (the leave rules are checked before it each tick).
- Logged ("Stationary Penalty: 5 step(s) to go…", "…cleared after 6 step(s)", "no step for
  N s: repositioning…"); the visit row counts `stationary_clears` and `repositions`.

The lumber runner does the same before each chop (docs/LUMBER_LOOP.md §2).

## Crawl (`--crawl`; `harness/crawl.py`; user request 2026-10-03)

Standing on one spot earns 0.4–0.5 kills/min and the Stationary Penalty comes for it; the user
asked for a dungeon crawl: roam each floor, fight what it meets, and go only as deep (each floor
more dangerous) as it can fight efficiently. The plan below was written before the code; the
decision and the rejected alternatives are in docs/PLAN.md "Hunt crawl".

### The NPD floor graph (map files and the store, 2026-10-03)

`python harness/crawl.py floor --memory harness/data/harness.db` (read-only) prints it.
- **One floor.** From the arrival side (5536,528, z 0), a BFS with the client's walk rules
  (pathfind.Walk) that never steps on a known teleporter reaches **10,829 tiles**, x 5448–5616,
  y 363–535, the farthest **182 route steps** from the exit spot (5535,529). One storey: no tile
  has two walkable heights; z is 0 (8,513 tiles) or 1 (1,714), with ramps and pits down to −21
  and a few raised tiles up to 22.
- **No deeper floor behind a teleporter is known.** The store's `teleporters` table holds the
  entrance (1912,2556/2557 → 5536,530) and the exit tiles (5535,529), (5535,530), (5536,530),
  (5537,530) → 1911/1912,2556 only. The client's Atlas packs (`ClassicUO/Data/Client/*.xml`)
  mark only the NPD's overworld entrance (Dungeons.xml, 1913,2566); `teleprts.txt` lists no
  places. The map has walkable areas next to it that the arrival can't reach (3,651 tiles at
  x 5595–5677, y 430–546; 2,601 at x 5380–5489, y 300–388): other dungeons, or NPD parts behind
  server teleporters nobody has stepped on [INFERENCE: which is unknown].
- **So "deeper" is farther from the one exit**: level k = the tiles `40 k` to `40 k + 39` route
  steps out (`--crawl-band`). Every level enters and leaves through the exit spot; its route to
  the exit is its band.

| Level | Route steps to the exit | Tiles | Waypoints (radius 8) |
|---|---|---|---|
| 0 | 0–39 | 566 | 10: 5532,528 5541,528 5539,521 5534,517 5528,506 5545,506 5533,501 5554,506 5533,494 5542,492 |
| 1 | 40–79 | 2,765 | 38 |
| 2 | 80–119 | 2,885 | 41 |
| 3 | 120–159 | 3,148 | 43 |
| 4 | 160–182 | 1,465 | 15 |

Everything hunted so far is level 0: all 386 floor tiles of the box the runner has fought in
(x 5528–5547, y 498–530) are under 40 steps out.

### Measured priors (the store, 2026-10-03: 29 finished hunt visits, 213 min)

`python harness/crawl.py priors --memory harness/data/harness.db` prints the model's view of them.
- **Level 0 base**: 8.3 gold/min and 23.4 hits lost/min (the visits' gold and `hits_lost` over
  their minutes); 20 of the 29 visits ended in a hits leave.
- **Per creature**, kills rebuilt from the event log (`crawl.fights_from_events`: the first attack
  or cast intent at the serial to its `kill` job event; hits lost = the "-N" overhead numbers on
  us in between, disease ticks included; gold from the loot event):

| Creature | Kills | Fight s (median / mean) | Hits lost per kill (median / mean) | Gold per kill (kills with gold) | Engaged, then died to someone else |
|---|---|---|---|---|---|
| mongbat | 153 | 29 / 60 | 7 / 29 | 13.8 (100 of 153) | 24 of 178 |
| giant rat | 10 | 20 / 73 | 10.5 / 36 | 14.4 (7 of 10) | 3 of 14 |
| headless | 10 | 29 / 25 | 1.5 / 5 | 23.1 (7 of 10) | 0 of 10 |
| skeleton, zombie, wounded harpy, giant frog | 1 each | | | | |

- **Where**: every engagement was within x 5525–5547, y 498–530 (level 0). Deaths seen in view
  during hunts (`mobile_death`): mongbat 371, giant rat 84, headless 45, zombie ("rotting") 21,
  giant frog 21, skeleton 20, colossal frog 19, wounded harpy 14, rime guar 9 (pets among them
  [INFERENCE]). Nothing is known about levels 1–4.
- **Kill stealing**: 35 % of the mongbat kills brought no gold (no corpse, or a pet's owner
  looted it) and 13 % of the engaged mongbats died to someone else first.
- At 94 max hits the model avoids none of them (mongbat estimate: 60 s per kill, 29 hits lost per
  fight, 13.8 gold per kill).

### Design

- **Floor and waypoints.** On the first visit the runner builds the floor from where it stands
  (map BFS as above, never over a known teleporter; walk memory without a map) and covers it
  greedily with waypoints: the roomiest uncovered tile (clearance from the walls) becomes one and
  covers the tiles within `--pull-range` *route* steps, so rooms get centred waypoints and
  corridors a chain; none within 2 tiles of a teleporter. Each tile belongs to its route-nearest
  waypoint: that waypoint's area. Building the NPD floor takes ~1 s.
- **Patrol.** Nothing to fight or loot: walk to the next waypoint (Mover routes, human pacing:
  run, pauses, sidesteps). The walk stops as soon as there is something to do (`Mover.walk_to`
  `stop`): a target within `--pull-range` of us, an attacker, a leave rule, speech nearby. The
  next waypoint maximises staleness (time since our last stay, capped at 10 min) × value (1 +
  0.5 per kill there this run, up to 3) × crowd (0.3 when players or pets were seen there in the
  last 5 min: they take the kills) × the level's gold rate / (1 + route steps / 20), with ±15 %
  noise: nearby stale areas first, a natural sweep that loops back.
- **At a waypoint.** Nothing showed up: a glance of about `--crawl-dwell` s, then on. Targets
  came (met on the way there or at it): stay while they keep coming; `--crawl-depleted-s` without
  one and the area is depleted, skipped for 10 min.
- **The efficiency model** (`crawl.Model`). Per creature type: its fights (time to kill, hits lost,
  gold per kill, outcome); estimates are shrunk toward the pool of all fights with 2 pseudo-fights.
  A type is **avoided** when its estimated hits lost per fight exceed `--crawl-max-hits` × our max
  hits, its kills take longer than `--crawl-max-fight-s`, or we fled in more than 30 % of its
  fights (one flee from a type never killed is enough; 153 mongbat kills absorb a bad fight).
  Avoided means: never pulled (fought only when it attacks us), a Mover danger zone of 6 tiles
  around it (also where it was last seen, for 5 min), and its area is not patrolled to.
- **Levels.** Per level: minutes, hits lost, gold, kills, survival leaves. Level 0's rates start
  at the store's base, level k's at level k−1's estimate (hits × `--crawl-depth-risk`), both with
  5 pseudo-minutes. The next level opens only when the current one has `--crawl-learn-s` of
  observation (earlier runs count) and the next one's predicted hits lost/min ≤ `--crawl-max-dmg`.
  A level comes back down (closed for the run) when its own estimate exceeds that after
  `--crawl-learn-s`, or after `--crawl-max-leaves` survival leaves from it. With the measured
  base (23.4 × 1.5 = 35 > 30) level 1 opens once level 0 has proven cheaper than ~20 hits/min,
  e.g. after 10 min at 15 hits/min.
- **Safety.** The leave rules are unchanged and checked first every tick; the route margin
  follows us: `--leave-at` + `--leave-per-step` × the route steps from where we stand to the exit
  (capped at `--heal-at` − 0.05), so a survival leave from deep runs (Mover urgent) the shortest
  route back to the exit spot and takes the exit step; the Mover never steps on a known teleporter
  but the exit. The episode row's `route_steps`/`leave_at` are the ones at the leave.
- **Persistence.** Every engagement (both modes) is a `fight` job event; level openings and
  closings are `crawl_level` events; each visit row has a `crawl` block. The next run's model
  starts from them (`crawl.load_prior`), so a creature fled from stays avoided and a known level
  doesn't have to be learnt again.
- **Moving keeps the Stationary Penalty away**: patrol walks are steps; the longest stay is a
  depleted-area hold (90 s), well under the 240 s reposition.

### Limits

- Not run live yet [INFERENCE: everything below level 0 is unmeasured; "deeper is more dangerous"
  is the user's statement, built in as `--crawl-depth-risk`]. Levels are distance bands on one
  floor, not teleporter floors; floor transitions through a teleporter aren't implemented (the
  NPD has none known).
- A survival leave is blamed on the target engaged at that moment, whatever else hit us.
- The avoid radius (6 tiles) and the 10 min area rest are guesses [INFERENCE: aggro ranges and
  spawn timers unknown]; mobs that stay out of reach are still dropped by the pin rule (the
  runner never walks to melee).
- Kill stealing is seen only as players or pets in an area and as kills without gold.

### Starting a crawl (overseer)

`run hunt --enter --crawl --pull-range 8 --mana-reserve 999 --target-name "" --gheal-min-missing 1 --timeout 3600`

The live fight-spot arguments plus `--crawl` (no `--fight-spot`). It starts in level 0 (the
area hunted so far) and opens level 1 only when the numbers allow it; watch the log for
"crawl: going deeper", "coming back up", "avoiding" and "depleted".

## Guards

Overall timeout, movement stall, the agent gate (actions wait out a pause or break), server
restriction text (`server_restriction` juncture, stop), death (`death` juncture with the attackers
and threats, stop: no corpse runs), the speech hold above.

## Memory

One episode per visit (loop `hunt`): `visit`, `t_start`/`t_end`, `spot`, `fight_spot`, `route_steps`
(the route back to `spot`; crawling, from where we stood at the leave), `leave_at` (with the route
margin), `spell`, `hits_start`, `kills`, `gold`, `xp`, `hits_lost`, `casts`, `heals`, `leaves`,
`stationary_clears`, `repositions` (Stationary Penalty walks), `ended` (the leave reason, `done`,
`time is up` or `stopped`). Job events `kill`, `loot` (`mob`, `name`, `gold`, `xp`, items), `leave`,
`death`, `speech_hold`, `speech_clear`, and **`fight`** per engagement, both modes (`serial`, `name`,
`body`, `fight_s` from the first attack, `hits_lost` meanwhile, `outcome` kill / fled / dropped /
lost / switched / ended, `mode`, crawling also `level`); `leave` has `route_steps`. Crawling:
`crawl_level` (a level opened or closed, with the rates) and the visit row's `crawl` block
(`spot`, `band`, `unlocked`, `closed`, `avoided` types with why, `waypoints` held, `depleted`
areas, per-level `s`, `hits`, `gold`, `kills`, `leaves`); crawl.load_prior reads them back. Gold
looted is the backpack gold delta (or the status gold delta, whichever is larger; the loot act's
own reply was unreliable live).

**XP** is Outlands mastery-chain experience: a kill gives the creature's gold value × our damage
share ([wiki Experience_Gain](https://wiki.uooutlands.com/Experience_Gain); no mastery chain needed
to accumulate it). No capture shows a per-kill XP message (docs/NOTES.md "Experience"), so `xp` is the gold the corpse held when
opened, before looting [INFERENCE: equal to the creature's value for a solo kill; the weekly bonus
and Fortune scale gold and XP alike per the wiki]. Unlooted kills have no `xp`. The Jobs page's
Hunting dashboard shows kills, gold and XP (docs/VISUALIZER.md §2.4).

## Test

`python test_loop_hunt.py [default|staff|fight|crawl]` (~160 s for all four, private ports): a
simulated NPD behind the real proxy. A mongbat
that died to someone else (`0xDEAD` only) must never be touched; the first mongbat flies in and hits
hard (heal path), dies to Lightning, its gold is looted; two more come in swinging and trigger the
two-attacker rule; the runner rests outside with a Greater Heal and goes back in; the one the server
doesn't re-send (pruned, out of range) must never be targeted; the second kill ends the run outside.
Attack, cast, target, cancel and loot packets are compared byte-wise with the builders. The pack
holds the Heal / Greater Heal / Lightning reagents; the server answers the first Lightning with
502630 anyway, and Lightning must not go out again until the second visit. A second run wields
the prismatic staff (no skills sent, so below 80): no Lightning at all, no heal spell inside
while a potion is in the pack, the rest's heal cast puts the staff in the pack (as live), and
it is re-equipped with the stock lift + `0x13` on layer 2 before the runner goes back in. A third
run fights at `--fight-spot 5541 535`, 6 tiles SE of the exit spot, whose straight route crosses
the arrival tile: every attack goes out on the fight spot, both leaves walk back to the spot
before the exit step, and the episode rows hold the fight spot, the route length and
`--leave-at` + 0.004 per step. The default run's rows hold `fight_spot` = `spot`, 0 steps,
`--leave-at` as given.

A fourth run crawls (`--crawl --crawl-band 22`, `--no-map`: the floor comes from the store's walk
memory, seeded with the sim's rooms; steps into a wall are denied): a hall at the exit, a corridor
north to room R1 (a mongbat), a corridor west to room R2 (22+ route steps out: level 1) with a
troll in its corner that hits 15 every 0.8 s next to it. The server sends mobiles as they come
within 18 tiles. Checked: the runner patrols ≥ 4 waypoints through all five areas and fights in
more than one; R2 is entered only after the `crawl_level` event opening level 1; the troll is
fought once, the leave from R2 (≥ 20 route steps out, `--leave-at` raised) is a threat juncture,
runs back to the exit spot and steps out there (never the arrival tile); the troll's `fight` is
`fled` and the crawl avoids it: back inside it is never attacked or come within 2 tiles of; R1,
once its mongbat is dead, is left as depleted for another waypoint; the second mongbat (it comes
once we are back in) is killed, both looted, their `fight` events carry time and level, and the
rows carry the `crawl` block (troll avoided, level 1's leave). `python harness/test_crawl.py`
covers the geometry (coverage, areas and levels by route through a wall, the walk-memory floor),
the model's shrinkage and avoid rules, level opening and closing, the waypoint choice and the
store prior.

In every run the arrival tile (5536,530) is a teleporter in the memory store and in the sim (as
live), and the sim applies the Stationary Penalty as live: the `0xFF` sub 8 byte for byte as in
capture 20261003_123614 (icon 277, f1 4620, f2 1, the timer = steps left), re-sent per step that
changes our tile, removed (sub 9) on the 5th; while it is on our damage is 1. It comes at login;
in the fight-spot run also when we first hurt the first mongbat (mid-fight), and after 14 s
without a step (live 300 s) with `--reposition-s 6`. Checked: no attack (`0x05`, a Lightning
cast, a target on a mob) goes out while it is on; the login one is gone before the first
attack; the mid-fight one is walked off before the next attack; the runner's clear took ≥ 6
steps and is counted in the rows; the exit tile is stepped on only from the spot when leaving
and the arrival tile never; standing still inside, the runner repositions (counted) and the
14 s penalty never comes inside.

## Live (2026-10-02, Hackworth, NPD, runs 1–11 under the overseer)

72 counted kills and ~980 gold in ~3 h 15 min. Leaving below `--leave-at`, the exit teleport, resting
outside and re-entering all worked; no deaths. Heal potions were drunk live (healing.py). Logs:
`logs/tasks/hunt-20261002-*.log` (local).

- **`--pull-range 3` idles** while bats sit 5–6 tiles from the exit tile; 8 engaged the room.
- **Non-target attackers are never fought.** `pick_target` filters every candidate, attackers
  included, by `--target-name` (`wanted()`); a wounded harpy and giant rats on the exit tile took
  ~45 hits in 10–15 s per visit while the runner waited for a mongbat. `status.attackers` stayed
  empty for them too. Workaround: `--target-name ''` (any monster the attack guard allows).
  Fix needed: attackers should bypass the name filter.
- **Heal isn't always in the spellbook.** healing.py picks Heal for small losses; Hackworth's book
  lacks it ("You do not have that spell!"). Workaround `--gheal-min-missing 1`. Fix needed: choose
  only spells the book holds.
- **A speech hold sends nothing, even mid-fight**: no heals and no swings. Hits fell 99 → 48 until
  the survival leave overrode the hold. Combat and heals should continue during a hold.
- **The rest ends at the mana reserve.** With the default 22 it re-entered with ~24 mana, cast it
  away and bounced out in 25 s. `--mana-reserve 70` made visits last.
- **The arrival tile (5536,530) is also an exit teleporter.** Walking back from a corpse over it
  put the runner outside, and it aborted "back to the spot: no route" (it plans inside only).
- **Counted kills overstate income in a crowded room.** Tamers' pets take killing blows (loot is
  theirs), so 30–100 % of a run's kills were empty or had no corpse.
- Logged heal deltas often read +1 ("greater heal: 46 -> 47"): the hits are read before the heal
  lands.
- Many casts are ruined by hits (cliloc 500641) or spell recovery (502644). Mongbats inflict
  "Diseased" (damage every 5 s).

## Live (2026-10-04, NPD; the pin cases with Shackleworth)

- **No reagents, no spellstone** (Hackworth's spellstone stayed on his corpse; docs/NOTES.md):
  the runner cast without checking reagents, so every cast would have been refused with "More
  reagents are needed for this spell." and retried each tick, resting outside too (from the
  code; not run live that way). Fixed: it casts only what `combat.can_cast` pays for, and a
  502630 blocks the spell for the visit.
- **Pinned on an unreachable target:** with `--pull-range` above 10 it engaged a colossal frog
  at 12 tiles and did nothing for 2.5 min while a mongbat hit us (our own swings killed it:
  the server turned them on the mongbat). Then, with `--pull-range 10`, a wounded harpy at 10
  tiles answered every Lightning with "Target cannot be seen." for over a minute. Fixed by the
  pin rule, the pull-range cap and the live attacker signals above.

## Not yet

- The spell range (10) and the corpse wait (4 s) are [INFERENCE].

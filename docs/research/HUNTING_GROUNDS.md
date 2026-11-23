# Hunting Grounds (event, 2026-11-21 11:00 EST to 2026-11-28 11:00 EST)

Three parts:
1. The official rules (§1), from the forum post.
2. What Logan Wolf's first two runs showed (§2, §3). The source is capture `session_20261122_140905`, store
   session 307, 2026-11-22 14:57-15:28 local time (CST, UTC-6).
3. The strategy derived from both (§4).

The same facts are in the knowledge store: `ctl know search "Hunting Grounds" --tag hunting_grounds`. During a
run the viz's map shows the §4 strategy live (zone, points to shoot, when to head back): docs/VISUALIZER.md §2.22.
The runner `harness/loop_hg.py` plays it unattended; its simulator results are in docs/SIM.md "Hunting Grounds simulator".

## 1. Official rules (forum post)

Source: <https://forums.uooutlands.com/index.php?threads/hunting-grounds-sat-november-21st-at-11am-est-to-sat-november-28th-at-11am-est.6392/>
(Luthius, 2026-11-13, edited 2026-11-18). Text marked "changed this year" in the post was orange in the
original. Last year's event (2025) had 2000+ competitors. The winner, Rockle, turned in 17,000 points
(`ctl know` #4639).

**Overview.** A week-long event with temporary access to a new Hunting Grounds region. Players hunt deer,
carve Antler Trophies and turn them in to a Game Warden for [EventScore.

**Hunting Permits**
- Killing creatures or earning gold anywhere (dungeons, wilderness, oceans) gives a chance at a Hunting
  Permit in the backpack. The chance scales with your % of damage dealt to the target.
- Permits are bound to the player and can't be used by anyone else. They can't be stolen, but they
  **drop to the corpse on death**.
- Entering costs one permit.

**Hunting Official.** NPC near the **Prevalia bank** or the **Corpse Creek bank**. Saying "Event", "Hunting" or
"Permit" near it, or double-clicking it, opens the event menu. With a bound permit in the backpack, "Enter
Hunting Grounds" spends it and teleports you in.

**Followers.** Not allowed. Stable them before entering.

**Session.** 15 minutes in the region. After that you're ejected to the last town you used a Moongate from,
and you arrive **hidden**. The session also ends when you leave the region by any means, including the green
exit Moongate in the lodge.

**Instances.** Two identical instances run at once. Entrants alternate between them (player 1 → instance 1,
player 2 → instance 2, …) to prevent overcrowding.

**Region rules**
- Traditional combat against players or creatures is disabled. Only the hunting actions work.
- No harmful actions against players.

**Equipment**
- Stats, skills and PvM bonuses (aspects, codexes, mastery chains) have no effect. No equipment or resources
  are needed.
- Items in your hands go to the backpack, and you're given a **Bow that can't be removed**.

**Hunting Lodge.** North side of the map. It has the **Game Warden** (turn in trophies, resupply ammo) and the
exit Moongate. You start here.

**Bucks**
- Each buck's name gives its point value ("N point buck"), which is what its trophy is worth.
- Values are random, but **the farther from the lodge a buck spawns, the higher its value is likely to be**.
  The maximum is 14.

**Mental state** (shown over the deer as (calm) / (nervous) / (fleeing))

| State | Movement | How it starts | How it ends |
|---|---|---|---|
| Calm | moves sporadically | (default) | |
| Nervous | moves frequently | a **hidden** player nearby may cause it; the chance rises with proximity | back to calm after 10 s with no player seen or sensed |
| Fleeing | moves constantly | spots a **non-hidden** player; a shot near it; or it was nervous and a hidden player stays near | back to nervous after 10 s with no player seen or sensed |

**Hotbar.** Updates every 5 s and can't be closed permanently. It shows:
- score (trophies turned in)
- time left
- held trophies
- the action buttons
- ammo
- wind (weathervane)

**Actions**

| Action | How | Rules |
|---|---|---|
| Hide | Hide button, or the Hiding skill (button or client/Razor hotkey) | Always succeeds. 5 s cooldown in the region |
| Stealth | Automatic once hidden | Unlimited steps, no Stealth skill needed. You're revealed if you **run**, **step over a deer** or **shoot** |
| Track | Track button, or the Tracking skill | Always succeeds. 5 s cooldown. Gives the last location of the **nearest** deer. Doesn't reveal you |
| Shoot | Double-click the deer (war mode or not), or the Shoot button then target the deer | You must be **stationary for 2 s**. Range 12 tiles with the starter bow |
| Carve | Double-click the corpse, the Carve button, or the Forensic Eval skill | Only your own kills. Corpses vanish after **30 s**, carved or not |

**Hit chance.** Depends only on the deer's state, not distance:

| State | Hit chance |
|---|---|
| Calm | 90% |
| Nervous | 30% |
| Fleeing | 3% |

**Grazes.** A hit usually kills, but it may graze instead. A grazed deer flees at once and leaves a blood
trail for 20 s. The starter bow grazes 25% of the time; upgraded weapons graze less.

**Ammo.** 15 shots, one per Shoot action. Resupplied at the Game Warden.

**Held trophies**
- You can hold at most **5**. With 5 you must turn in before you can collect more.
- You can turn in fewer than 5.
- Held trophies **don't count** if you leave or get ejected. Mind the clock.

**Turn in and resupply.** Double-click the Game Warden, then "Turn in Trophies and Resupply". This turns all
held trophies into [EventScore and restores 15 ammo.

**Weapon upgrades.** Every 3 trophies turned in (any value) upgrades the weapon automatically: more range,
less graze.

**Wind (changed this year)**
- Wind carries human scent. Its direction shows as an in-game visual and as the hotbar weathervane.
- A deer **directly downwind** of you gives you a **30% penalty** to staying undetected.
- A deer **somewhat downwind** (the adjacent directions) gives a **15% penalty**.

**Score and prizes**
- Type `[EventScore` to see your score. The post says scores update hourly.
- At the end, Rank % = the share of players you beat. Example: 100th of 1000 = 90%.
- Prizes go to the bank box. You get **every** prize at or below your Rank %:

| Rank % | Prize |
|---|---|
| 0-5% | Outlands Collectable Card |
| 5-25% | Boar's Head Trophy |
| 25-50% | Butcher Block Table |
| 50-75% | Wool Bedrolls (random hue) |
| 75-95% | Elk Skeleton Remains |
| 95-100% | Silver Stag Statue |

## 2. Measured mechanics (Logan Wolf, 2026-11-22, runs 1 and 2)

Method: the store's events for session 307, plus the capture's S2C 0x77/0x20 packets for deer positions,
decoded with `harness/world/parsers.py`. Outlands sends u32 coordinates. 450 distinct bucks were seen;
64 shot attempts were made, of which 50 were real shots (the other 14 were rejected).

### Geography

| Feature | East instance (runs 1-2) | West instance (runs 3-4) |
|---|---|---|
| Hunting Official (Prevalia bank) | (1600, 1530) | |
| Game Warden (Willow) | (8342, 89) | (8030, 89) |
| Entry tile | about (8340, 90) | about (8028, 90) |
| Region (from player and deer positions) | x 8218-8474, y 72-330 | x 7906-8162, y 72-330 |

Each region is about 256 × 258 tiles, with the lodge at its north edge. The two instances are the same map side
by side, the west one shifted 312 tiles west. Points by distance south of the lodge apply to both.

**Points rise with distance south of the lodge.** Buck values by dy = (median y of the buck) − 88:

| dy (tiles south of lodge) | Bucks seen | Mean points | Mix |
|---|---|---|---|
| 0-60 | 138 | 2.1 | 2:130, 4:8 |
| 60-120 | 103 | 2.7 | 2:73, 4:25, 6:4, 8:1 |
| 120-160 | 70 | 4.2 | 2:27, 4:21, 6:13, 8:6, 10:3 |
| 160-200 | 90 | 7.6 | 2:3, 4:13, 6:22, 8:26, 10:12, 12:14 |
| 200-260 | 42 | 9.1 | 6:4, 8:18, 10:13, 12:7 |

No 14-point buck was seen. Only even values appeared.

### Weapon tiers

From the warden gump (`Range`/`Graze` table, gump id 3914586893):

| Trophies turned in | Weapon | Range | Graze |
|---|---|---|---|
| 0 | starter bow | 12 | 25% |
| 3 | Recurve Bow | 14 | 20% |
| 6 | Hunting Bow | 16 | 15% |
| 9 | Great Bow | 18 | 10% |
| 12 | Ancient Rifle | 20 | 5% |

**Upgrades reset each run.** Run 2 unlocked the Recurve Bow again at its first turn-in, although run 1 had
ended with the Ancient Rifle.

### Clocks and speeds

**Session clock**
- Start: "\*You now have 15 minutes in this realm\*", spoken over **our own head**.
- Warnings: "You now have 10 minutes left remaining in this realm." and the same line at 5 minutes.
- End: "Your permit for the Hunting Grounds has expired." This came 899 s and 904 s after the start lines.

**Movement**
- Running while visible: 0.2 s/tile (1918 of 2131 visible steps).
- Stealth while hidden: 0.4 s/tile (1153 of 1302 hidden steps).
- On foot only: mounts are rested on entry ("Your mount finds a quiet place to rest safely.").

**View range.** Every buck entered the world model at exactly 18 tiles.

**Shooting and carving**

| Step | Measurement |
|---|---|
| Shot → death | 1.2-1.6 s after the target response |
| Shot cooldown | about 7 s. A shot 6.0 s after the last was refused ("You must wait a few moments before shooting again."); one at 7.7 s went through |
| Shot → carve | Carve attempts 2.6-6.5 s after the shot were refused ("You must wait a moment before carving again."). Successful carves came 4.7-9.7 s after the shot. You must be within **2 tiles** of the corpse (every carve was at 1-2 tiles) |
| Carve → hide | They share a skill timer. Hide 1.0-2.8 s after a carve was refused; from 3.0 s it worked |
| Hide → hide | The 5 s cooldown matches the post |

**Stationary rule.** "You must remain stationary for a moment before shooting." refused 9 of 64 shot attempts.

**Range rule.** "That is out of range of your weapon." refused a shot at 18 tiles with range 16.

**Trophy cap**
- A 5th carve says "Players can only carry 5 trophies at a time. Return to the game warden…".
- Shooting while holding 5 is refused: "You are already holding 5 trophies…". Three shots were wasted this way.

**Wind**
- Wind changes every 30 s exactly ("Wind is now blowing to the X.").
- Each change moves one octant either way, e.g. south ↔ southeast.
- Over 15 minutes it drifts across the compass.

### Deer reactions (distance to us when the server said so)

- `*flees*`: 379 seen, all within 20 tiles. 88 were within 10 tiles and 222 within 15; the median was 15.
  This count includes flights caused by other players (about 15 others were in the instances), so the
  radius is an upper bound.
- `*looks nervous*` (only hidden players cause it): 18 seen. 17 were within 10 tiles and all 18 within 15.
- **[INFERENCE]** Deer notice a hidden player within about 10 tiles and a visible one within about 20
  tiles. View range is 18, so a deer you can see can probably already see you if you're not hidden.

### Shot outcomes

50 real shots. "State" is the deer's last label before the shot. Labels are broadcast about every 5 s, so
some are stale.

| State | Kill | Graze | Miss | Hits (kill + graze) |
|---|---|---|---|---|
| Calm | 24 | 2 | 5 | 26/31 = 84% (post: 90%) |
| Nervous | 5 | 5 | 3 | 10/13 (stale labels, probably calm at the shot) |
| Fleeing | 1 | 0 | 5 | 1/6 |

- Grazes made up 9 of 37 hits (24%).
- Two grazed 10-point bucks were chased. Both chases missed: a grazed deer flees.
- Ammo was never short. The most shots on one trip was 12, out of 15.

### Signals for automation

- **Hotbar gump** 4100931131: button 1 Carve, 2 Hide, 3 Track, 4 Shoot. Its lines are `Score`, `NNm Left`,
  `Held Trophies`, `Ammo`, `15/15`, `Wind`.
- **Warden gump** 3914586893: button 1 "Turn in Trophies and Resupply". Its lines include held points and
  `Nm Ns Left`.
- **Official's menu** 515811277: button 2 "Enter Hunting Grounds".
- **Deer**: body 234, names "N point buck". Their state labels are type-0 speech from the deer: "(calm)",
  "(nervous)", "(fleeing)", "\*flees\*", "\*looks nervous\*", "\*grazed\*".
- **Kill**: "You have felled a deer! Carve it to claim a trophy." over our head. The corpse is "an animal
  corpse".
- **Carve**: "N point trophy added to carried trophies."
- **Turn-in**: "Ammunition resupplied and K trophies turned in for P points (S new score)". S is the run's
  total so far.
- **The [EventScore board updates live**, despite the post and the server's line "Event scores for this
  event are updated every 1 hour."
  - At 15:13:05 it showed Logan at 82, 17 s after his 82-point turn-in.
  - At 15:28:54 it showed him at 154 = 82 + 72.

### Eight runs (added 2026-11-22 night)

**Source.** Runs 1-8 of 2026-11-22: captures `20261122_140905`, `20261122_163459` and `20261122_181713`. Runs
1, 2 and 6 were in the east instance; runs 3, 4, 5, 7 and 8 in the west. Buck positions are given in the east
frame (x − 312 for the west), with dy measured from y 89. The calibration file is
`harness/data/sim/hunting_grounds.json`.

**Buck values by depth** (1585 bucks, each at its median position):

| dy | Bucks | Mean points | Mix |
|---|---|---|---|
| 0-40 | 134 | 2.07 | 2:130, 4:3, 6:1 |
| 40-80 | 229 | 2.27 | 2:202, 4:23, 6:4 |
| 80-120 | 211 | 2.89 | 2:138, 4:54, 6:17, 8:2 |
| 120-140 | 137 | 4.12 | 2:50 … 14:1 |
| 140-160 | 149 | 4.94 | 2:31, 4:52, 6:37, 8:23, 10:6 |
| 160-180 | 188 | 6.39 | 2:15, 4:41, 6:60, 8:43, 10:22, 12:7 |
| 180-200 | 228 | 7.80 | 4:26, 6:58, 8:70, 10:44, 12:23, 14:3 |
| 200-220 | 194 | 8.77 | 6:32, 8:51, 10:69, 12:25, 14:5 |
| 220-245 | 98 | 8.90 | 6:11, 8:37, 10:36, 12:12 |

- 14-point bucks exist: 9 of them, 8 at dy ≥ 180.
- Value tracks distance from the lodge. Its correlation is 0.76 with dy, 0.77 with Euclidean distance and 0.77
  with Chebyshev distance.
- Across the deep band, x hardly matters. At dy ≥ 180 the mean is 7.9-8.6 in every 32-tile column.
- `hunting_grounds.BANDS` uses this table since the same night. The recommended zone moved from y ≥ 249 to
  y ≥ 269 (dy ≥ 180).

**Deer density.** Deer in view per 1000 visible tiles of the region, sampled every 5 s in runs 4-8:

| dy | Deer per 1000 tiles |
|---|---|
| 0-60 | 0.3-0.7 |
| 60-120 | 1.0-1.4 |
| 120-160 | 1.1-1.2 |
| 160-240 | 1.5-2.0 |

[INFERENCE] That is about 100 deer per instance. Over a run, 220-285 different bucks came into view, so felled
deer come back.

**Deer movement**, in tiles per 5 s after a mood label:

| Mood | Tiles per 5 s | Windows with movement | Notes |
|---|---|---|---|
| Calm | 5.6 | 82% | |
| Nervous | 10.8 | 94% | |
| Fleeing | 15.3 | | net displacement 9.6; about 0.17 s a tile while moving |

**Mood changes**, per second of a deer within the distance, by our state:

| Our state | Change | Rate |
|---|---|---|
| Visible | calm → flee | 0.27-0.33/s anywhere within 20 tiles; 0.18 within 4 |
| Visible | nervous → flee | about 0.25/s |
| Hidden | calm → nervous or flee | about 0 at 4-20 tiles (0.02/s flee at 8 and 16) |
| Hidden | nervous → flee | 0.14/s within 4 tiles; under 0.02 farther |

Here "hidden" runs from "You hide" to the next shot or running step.
- [INFERENCE] Some of the hidden flights come from other players.
- A visible player within view makes a calm deer bolt in about 3 s.
- A hidden player can walk to within a few tiles of a calm deer almost without risk.

**Being held still.** "You are no longer hindered." comes 0.07-0.2 s after "You have felled a deer!" (the shot
holds you until it lands) and 0.6-0.8 s after a carve. Walks in between are refused.

**Server timers** (runs 4-8):

| Event | Timing (82 kills) |
|---|---|
| Target response → felled | 1.29-1.75 s, median 1.49 |
| Carve accepted | 4.66 s or more after the shot |
| Carve refused | up to 5.68 s after the shot (27 refusals) |
| Hide after a carve | refused up to 2.98 s, accepted from 3.01 s |

The carve gate is [INFERENCE] 3.2-4.2 s after the kill.

**Between runs.**
- The expired permit drops you on a random Prevalia tile: (1650, 1456), (1641, 1564), (1778, 1504) and
  (1656, 1525) were seen.
- The user then recalled or walked to the Hunting Official, Easton, at (1600, 1530), double-clicked him and
  pressed "Enter Hunting Grounds".
- The permit is a stack of graphic 63999, "hunting permit".
- Each lodge's exit moongate is graphic 3948, hue 1196, four tiles north of the warden: (8030, 85) in the west.

**What the eight runs scored:**

| Run | Instance | Score | Trophies | Points per trophy | Lost at expiry |
|---|---|---|---|---|---|
| 1 | E | 82 | 13 | 6.3 | 0 |
| 2 | E | 72 | 12 | 6.0 | 44 |
| 3 | W | 78 | 13 | 6.0 | 0 |
| 4 | W | 144 | 18 | 8.0 | 0 |
| 5 | W | 146 | 15 | 9.7 | 2 |
| 6 | E | 126 | 16 | 7.9 | 0 |
| 7 | W | 134 | 14 | 9.6 | 2 |
| 8 | W | 150 | 17 | 8.8 | 0 |

The viz advice was on from run 4. Runs 4-8 average **140**, and that is the benchmark for the runner
(docs/SIM.md "Hunting Grounds simulator").

## 3. Logan's two runs, trip by trip

| Trip (start → end) | Length | Deepest dy | First shot | Shots | Kills | Trophy points | Back (last kill → turn-in) |
|---|---|---|---|---|---|---|---|
| R1 entry 14:57:52 → 15:02:28 | 276 s | 196 | +43 s | 5 | 5 | 2,2,6,4,6 = 20 | 90 s |
| R1 15:02:28 → 15:09:28 | 420 s | 224 | +89 s | 10 | 5 | 8,12,12,10,10 = 52 | 54 s |
| R1 15:09:28 → 15:12:48 | 200 s | 134 | +59 s | 6 | 3 | 6,2,2 = 10 | 25 s |
| R2 entry 15:13:45 → 15:18:30 | 285 s | 54 | +33 s | 12 | 5 | 2,2,2,2,4 = 12 | 19 s |
| R2 15:18:30 → 15:21:05 | 155 s | 166 | +49 s | 4 | 2 | 6,4 = 10 | 29 s |
| R2 15:21:05 → 15:25:03 | 237 s | 222 | +43 s | 7 | 5 | 10,8,8,12,12 = 50 | 44 s |
| R2 15:25:03 → expired 15:28:44 | 221 s | 237 | +39 s | 6 | 5 | 8,8,8,10,10 = **44 lost** | (didn't make it) |

- Run 1 scored 82 and run 2 scored 72. Run 2 lost 44 more: its 5th carve came at 15:28:08 with 36 s left at
  dy 239, and the run back needs about 0.2 × 239 + 10 ≈ 58 s.
- Low-value trips (all-2-point bucks near the lodge, short 2-3 trophy turn-ins) earned 10-20 points for
  155-285 s.
- Deep trips (dy ≥ 160) earned 44-52 points for 221-420 s.
- The best trip (R2 15:25) killed 5 deep bucks in 141 s after its first shot, about 28 s per kill.

Leaderboard on 2026-11-22 at 15:13 and 15:28:
- 33-34 pages of 10, so about 335 players.
- #1 Elisa Shadowbane 2,278; #10 1,180.
- Logan was #207 at 82 points (37.0%), then #136 at 154 (59.3%).

**Permits so far**
- 7 received on 2026-11-21. The stack's label read "hunting permit : 7" at 21:22.
- 2 spent on 2026-11-22, which leaves **5** [INFERENCE: no other use seen].
- Where they came from:

| Source | Permits | Time |
|---|---|---|
| Logan's pets hunts at (5199,737) and (5689,1248) | 3 | about 261 kills over 3 h |
| An ice-themed dungeon session (rime royal lancers, frigid archfiends: 1,800-2,600 gold value each) | 4 | 21 min (21:01-21:22), 66 creature deaths in view |

**[INFERENCE]** The permit chance grows with the creature's value or gold, so harder content farms permits
several times faster.

### Runs 3 and 4 (2026-11-22 evening, west instance): before and with the viz advice

Run 3 (16:19-16:34, capture `20261122_140905`) was played without the advice. In run 4 (16:35-16:50, capture
`20261122_163459`) the proxy had been restarted and the viz showed it (docs/VISUALIZER.md §2.22).

| | Run 1 | Run 2 | Run 3 | **Run 4 (advice)** |
|---|---|---|---|---|
| Score | 82 | 72 (+44 lost) | 78 | **144** |
| Trophies turned in | 13 | 12 | 13 | **18** |
| Points per trophy | 6.3 | 6.0 | 6.0 | **8.0** |
| Median dy of kills | | | 143 | **192** |
| Kills per real shot | 60% (runs 1-2) | | 57% (13/23) | **78% (18/23)** |
| Shots at nervous/fleeing deer | | | 3 | **0** |
| Refused for not standing still | 9 (runs 1-2) | | 7 | **3** |

Run 4, trip by trip:

| Trip | Length | Points | Notes |
|---|---|---|---|
| 1 | 277 s | 40 | |
| 2 | 248 s | 38 | |
| 3 | 208 s | 50 | 12-8-10-12-8, with the Great Bow |
| 4 | 136 s | 14 | 2 kills; 165 s were left at its start |
| 5 | 28 s | 2 | a 2-pointer 4 tiles from the lodge, turned in 1 s before expiry |

- Nothing was lost.
- 144 is near the model's ~159 per permit (§4.2), and nearly double the 77 average of runs 1-3.
- The live advice measured the run home to the east warden (only the east instance was known then). In the
  west instance that put the slack 14-60 s short: at 16:49:31 the badge said 3 s to spare where 22 s were left.
  It erred safe, but trip 4 probably ended a kill early. Fixed the same evening.

## 4. Strategy

The score is (permits) × (points per permit). Each permit is worth one 15-minute run, so both factors matter.
The runs above averaged 77 points per permit (82 and 72). The policy below aims at 140-170.

### 4.1 Why go deep

Only 5 trophies fit per trip, so the binding constraint is **trophy slots per round trip**. Ammo isn't a
constraint (15 per trip, at most 12 used), and neither is the number of deer.

Model: a 5-kill trip at about 30 s per kill (observed 28-38 s in the deep band), a 0.2 s/tile run each way,
and 10 s at the warden.

| Hunt band (dy) | Points per trophy | Trip time | Points per trip | Points per second |
|---|---|---|---|---|
| 0-60 (~30) | 2.1 | 172 s | 10.5 | 0.061 |
| 60-120 (~90) | 2.7 | 196 s | 13.5 | 0.069 |
| 120-160 (~140) | 4.2 | 216 s | 21 | 0.097 |
| 160-200 (~180) | 7.6 | 232 s | 38 | 0.164 |
| 200-260 (~220) | 9.1 | 248 s | 45.5 | 0.183 |

Hunting the deep south (y ≥ 250) earns 2.5-3× as much per second as hunting near the lodge, even after the
longer run. A 2-point buck spends a trophy slot that is worth about 9 points in the deep band.

**Bow upgrades in the model** (added 2026-11-22 at the user's question).

An upgrade doesn't change the **hit** chance, which depends only on the deer's state (90/30/3%). It lowers
the **graze** chance, so it raises the kill chance per calm shot:

| Weapon (trophies) | Graze | Kill per calm shot | Usable range |
|---|---|---|---|
| starter (0) | 25% | 67.5% | 12 |
| Recurve (3) | 20% | 72% | 14 |
| Hunting (6) | 15% | 76.5% | 16 |
| Great (9) | 10% | 81% | 18 |
| Ancient Rifle (12) | 5% | 85.5% | 18 (20 nominal) |

Range above 18 is wasted. All 450 bucks came into view at exactly 18 tiles, and you can't target a deer you
can't see. The Great Bow already shoots anything in view.

To see how much upgrades matter, I ran a dynamic program over a 15-minute run
(`V(t, trophies turned in)` = the best expected score from time t). Each trip chooses a band and 1-5 kills,
costs 0.4·dy + 10 + k·T_kill seconds, and must end by 885 s.
- Kill time: T_kill = a/p_kill + 6 s carve. Here a is the search, approach and shot time per attempt, minus
  0.4 s per extra tile of range up to 18.
- a = 18 s reproduces the observed ~30 s per kill.
- A graze costs the same as a miss: the deer is gone either way.

With a = 18 s, T_kill falls from 32.7 s (starter) to 24.2 s (Rifle), 26% faster. Results:

| Kill-time parameter a | 12 s | 18 s | 24 s | 30 s |
|---|---|---|---|---|
| Expected score with upgrades | 190 | 159 | 136 | 114 |
| ... upgrades ignored | 182 | 141 | 127 | 106 |
| First trip 5 deep (dy 220) | 190 | 158 | 136 | 114 |
| First trip 3 at dy 180, to unlock the Recurve | 187 | 159 | 121 | 114 |
| Warm-up: 3 two-pointers by the lodge | 188 | 146 | 120 | 99 |
| Warm-up: 4 two-pointers by the lodge | 190 | 145 | 115 | 99 |

**Conclusions**
- Upgrades are worth about +5-13% per run, mostly because later trips get faster. Trip 3 kills about 20%
  faster than trip 1.
- They don't change the route. Upgrades count trophies, not points. The deep policy already turns in the
  most trophies a trip can hold. A warm-up trip by the lodge buys tiers early but costs 10-15 points
  whenever a ≥ 18 s, and only ties when kills are very fast (a = 12 s).
- A sensitivity run gave the same answer: with range worth 1.5 s per tile, deep-first scored 184 against
  168-173. Only an implausible 3 s per tile (6 s kills) made a warm-up tie.
- What upgrades change is execution:
  - From the Great Bow (trip 3 onward), shoot from where you first see the deer and skip the stealth approach.
  - Later trips are shorter, so re-check the endgame table (step 8) against the time left. A Rifle-tier trip
    of 5 deep kills takes about 220 s, not 245 s.
  - Nervous deer are still not worth it. With the Rifle a nervous 12-point buck is worth 0.3 × 0.95 × 12 =
    3.4, against 6.8 for a calm 8-point buck.

The tier sequence under the deep policy:

| Trip | Trophies turned in before it | Weapon | Graze | Range |
|---|---|---|---|---|
| 1 | 0 | starter bow | 25% | 12 |
| 2 | 5 | Recurve | 20% | 14 |
| 3 | 10 | Great Bow | 10% | 18 |
| 4 | 15 | Ancient Rifle | 5% | 18 effective |

### 4.2 Per-run procedure

1. **Before entering** (the clock hasn't started):
   - Stable followers.
   - Have a bound permit in the backpack.
   - Know the Hide, Shoot and Carve hotkeys.
2. **Run out (0-45 s).**
   - Don't hide and don't stop. Run straight south to y ≈ 250-300.
   - Ignore every buck on the way; they're 2-4 point bucks.
   - Running while visible scares deer near the path, which doesn't matter up north.
3. **Hunt in the deep band.**
   - While no buck worth ≥ 6 points is in view (the view range is 18), keep running to reposition. Running is
     twice as fast as stealth.
   - **Hide the moment a target comes into view.** At 18 tiles a visible player is probably already inside a
     deer's 20-tile flee radius.
   - Stealth toward the target, but stop **outside 10 tiles** (the nervous radius) and inside weapon range:
     11-12 tiles with the starter bow, 14 with the Recurve. With the Great Bow or better (range ≥ 18 = view
     range), shoot from where you first see the deer.
   - Keep the deer **upwind**: never stand where the wind blows from you toward the deer (30% / 15% penalty).
     Wind moves one octant every 30 s. Approach across the wind or from the downwind side, and re-check every
     30 s.
4. **Pick targets.** Shoot **calm** deer only. A shot's expected value is points × hit chance × (1 − graze).
   With the starter bow:

   | Target | Expected value |
   |---|---|
   | Calm 10-point | 6.75 |
   | Nervous 10-point | 2.25 |
   | Fleeing | about 0 |

   A nervous deer turns calm again after 10 s without sensing anyone, so back off and wait. Don't chase a
   grazed deer.
5. **Shoot.**
   - Stop, wait **≥ 2.2 s** (the server needs 2 s stationary; 9 of 64 attempts were refused), then
     double-click the deer.
   - Don't fire again within 7 s.
   - Shooting reveals you.
6. **Carve.**
   - Walk to the corpse, within 2 tiles.
   - Carve **≥ 5 s after the shot** (earlier attempts are refused). The corpse lasts 30 s.
   - Hide again **≥ 3 s after the carve** (shared skill timer).
   - Then pick the next target nearby. The deep band had several 8-12 point bucks in view at once.
7. **Go back.**
   - Leave as soon as the 5th trophy is carved. Shots at 5 held trophies are refused anyway.
   - **Hard deadline:** start back when time left ≤ 0.2 × (y − 88) + 20 s. For example, 66 s at y = 320.
   - Run back while visible. At the warden, double-click and press "Turn in Trophies and Resupply".
   - The hotbar shows time left (every 5 s), and so does the warden gump (`Nm Ns Left`).
8. **Endgame.** After a turn-in with R seconds left:

   | R | What to do |
   |---|---|
   | ≥ about 190 s | A full deep trip: 45 s out, 5 kills, 45 s back |
   | 100-190 s | A shorter trip to dy 160-190 for 1-3 trophies. Each is worth 7-9, and turning in fewer than 5 is allowed |
   | < 100 s | Hunt the 2-point bucks within about 40 tiles of the lodge and keep the deadline rule |

   Leftover trophies are lost at expiry, so the deadline rule matters more than one extra kill.
9. **Expected result**
   - The dynamic program (§4.1, "Bow upgrades in the model"), with a = 18 s calibrated to the observed
     ~30 s per kill, gives **about 159 points per permit** including upgrades, against 141 if upgrades did
     nothing.
   - At the best observed cadence (25-28 s per kill): about 4 trips, **about 170-190**.

### 4.3 Permits

- With 5 permits in hand, the policy above is worth about 700-850 more points, putting Logan near ~900-1,000.
  On 2026-11-22 that would have been the top ~20 (about 94%).
- Final ranks depend on the whole week. The field grows (2000+ last year) and the top scores keep climbing:
  last year's winner reached 17,000.
- **[INFERENCE]** One permit (≈ 150 points) costs about 17 minutes in the Grounds plus however long it takes
  to farm. More points come from farming permits faster.
- On 2026-11-21, high-value dungeon creatures dropped 4 permits in 21 minutes; the low-tier pets hunt
  dropped about 1 per hour. Farm permits where creature value is highest and Logan can still take a large
  share of the damage, since the chance scales with damage share.
- Permits drop to the corpse on death. Spend or bank them regularly rather than carrying a stack into
  dangerous content [INFERENCE: banking a bound item is assumed to work; not tested].
- Check `[EventScore` → "Show My Ranking" after a session to see the Estimated Rank %. It updates live
  (§2), so it can steer how many more permits are worth farming toward the next prize tier: 75% (Elk
  Skeleton Remains) and 95% (Silver Stag Statue).

### 4.4 Open questions (test on the next run)

- Does hiding right after the shot (before walking to the corpse) work, and does carving while hidden reveal
  you? If both are fine, stealthing to the corpse spares the other deer nearby.
- Does Track point at the nearest deer of any value, or only at deer not already in view? Track was used only
  3 times; it may help when no buck is in view.
- Does the deep band near y ≈ 330 (the south edge) hold 14-point bucks?
- Is the flee radius really about 20 tiles for a lone visible player? The measurement includes other
  players' effects.

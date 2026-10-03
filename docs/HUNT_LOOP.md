# Hunt loop (`harness/loop_hunt.py`)

A programmatic task like the lumber loop: fight monsters at one spot, heal, loot, leave when
hurt. Built for New Player Dungeon mongbats (user decision 2026-10-01: the thresholds are task
arguments). Start it with `ctl run hunt [args…]` (docs/OVERSEER.md) or directly:

```
python harness/loop_hunt.py --kills 5                 # standing in the NPD
python harness/loop_hunt.py --enter --kills 5         # from outside the entrance
python harness/loop_hunt.py --enter --fight-spot 5536 509   # deeper in, away from the crowded exit
```

## Arguments

| Argument | Default | Meaning |
|---|---|---|
| `--spot X Y` | `5535 529` | The exit spot (the NPD exit tile): leaving walks here, then takes the exit step. |
| `--fight-spot X Y` | `--spot` | The tile to fight on. Each visit walks there; the pull range, the corpse range and the idle return are measured from it. Candidates below ("Fight spots"). |
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
4. **Loot** our kills' corpses when nothing is attacking us, like `ctl act loot`: human corpses
   refused, walk within 2 tiles, open the backpack (stock dclick) and the corpse, then lift + drop
   each item into the backpack, gold first, up to the weight limit.
5. **Fight** the target:
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
6. **Idle**: war mode off when nothing is near, back to the fight spot, wait.

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

## Guards

Overall timeout, movement stall, the agent gate (actions wait out a pause or break), server
restriction text (`server_restriction` juncture, stop), death (`death` juncture with the attackers
and threats, stop: no corpse runs), the speech hold above.

## Memory

One episode per visit (loop `hunt`): `visit`, `t_start`/`t_end`, `spot`, `fight_spot`, `route_steps`
(the route back to `spot`), `leave_at` (with the route margin), `spell`, `hits_start`,
`kills`, `gold`, `xp`, `hits_lost`, `casts`, `heals`, `leaves`, `ended` (the leave reason, `done`, `time
is up` or `stopped`). Job events `kill`, `loot` (`mob`, `name`, `gold`, `xp`, items), `leave`, `death`,
`speech_hold`, `speech_clear`. Gold looted is the backpack gold delta (or the status gold delta,
whichever is larger; the loot act's own reply was unreliable live).

**XP** is Outlands mastery-chain experience: a kill gives the creature's gold value × our damage
share ([wiki Experience_Gain](https://wiki.uooutlands.com/Experience_Gain); no mastery chain needed
to accumulate it). No capture shows a per-kill XP message (docs/NOTES.md "Experience"), so `xp` is the gold the corpse held when
opened, before looting [INFERENCE: equal to the creature's value for a solo kill; the weekly bonus
and Fortune scale gold and XP alike per the wiki]. Unlooted kills have no `xp`. The Jobs page's
Hunting dashboard shows kills, gold and XP (docs/VISUALIZER.md §2.4).

## Test

`python test_loop_hunt.py` (~100 s, private ports): a simulated NPD behind the real proxy. A mongbat
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
run fights at `--fight-spot 5541 535`, 6 tiles SE of the exit spot, with the arrival tile
(5536,530) a teleporter in the memory store and in the sim (as live), right on the straight
route: every attack goes out on the fight spot, both leaves walk back to the spot before the
exit step, the arrival tile is never stepped on, and the episode rows hold the fight spot,
the route length and `--leave-at` + 0.004 per step. The default run's rows hold
`fight_spot` = `spot`, 0 steps, `--leave-at` as given.

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

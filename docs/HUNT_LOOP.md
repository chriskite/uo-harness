# Hunt loop (`harness/loop_hunt.py`)

A programmatic task like the lumber loop: fight monsters at one spot, heal, loot, leave when
hurt. Built for New Player Dungeon mongbats (user decision 2026-10-01: the thresholds are task
arguments). With `--crawl` it patrols the dungeon floor instead of standing on one spot ("Crawl"
below). With `--enter-recall` / `--leave-recall` it hunts at a place reached and left by recall,
banking its gold at home ("Recall in, recall out" below). With `--pets` the character's pets do the
fighting, a tamer's way ("Pets" below). Start it with `ctl run hunt [args…]`
(docs/OVERSEER.md) or directly:

```
python harness/loop_hunt.py --kills 5                 # standing in the NPD
python harness/loop_hunt.py --enter --kills 5         # from outside the entrance
python harness/loop_hunt.py --enter --fight-spot 5536 509   # deeper in, away from the crowded exit
python harness/loop_hunt.py --enter --crawl --pull-range 8  # patrol the floor ("Crawl")
python harness/loop_hunt.py --enter-recall 0x57C3DEB6 --enter-rune "Urukton Bluffs" \
    --leave-recall 0x57C3DEB6 --bank-gold 1             # Urukton Bluffs from town ("Recall in, recall out")
```

## Arguments

| Argument | Default | Meaning |
|---|---|---|
| `--spot X Y` | `5535 529`; recall mode: the arrival | The exit spot (the NPD exit tile): leaving walks here, then takes the exit step. With `--enter-recall` the first arrival; with only `--leave-recall` where the run starts. |
| `--fight-spot X Y` | `--spot` | The tile to fight on. Each visit walks there; the pull range, the corpse range and the idle return are measured from it. Candidates below ("Fight spots"). |
| `--crawl` | off | Patrol the floor instead of one fight spot ("Crawl"); not with `--fight-spot`. The pull range, the corpse range and Stationary Penalty walks are measured from where we stand. |
| `--crawl-band` / `--crawl-zones` | `40` / `0` | Route steps from the exit per zone (internal depth bands, numbered from 1; not dungeon levels); at most this many zones (0: all). |
| `--crawl-dwell` / `--crawl-depleted-s` | `6` / `90` (`--pets` 1.5 / 5) | A waypoint where nothing showed up is looked over for about this long (× 0.6–1.4); an area where targets came is left as depleted after this long without one. |
| `--crawl-learn-s` | `600` | Seconds observed in a zone (store history included) before the next one may open. |
| `--crawl-dry-s` | `0` (`--pets` 45) | No target in reach anywhere in the open zones for this long: the next zone may open before `--crawl-learn-s` (its predicted risk still has to pass `--crawl-max-dmg`). Live 20261015_172342 the first zone, cleared, was patrolled 75 times in 5 min (the user: "wandering aimlessly"). |
| `--crawl-max-dmg` / `--crawl-depth-risk` | `30` (`--pets` 60) / `1.5` | The most hits lost per minute a zone may cost (estimated; for an unvisited zone: the zone before's × the risk factor). With `--heal-in-place` only for opening a zone: one closes on its survival leaves (`--crawl-max-leaves`), not on damage healed where we stand (live 20261015_181036: zone 2 closed at 40/min right after the countess room was cleared without a leave). Pets 60 since 2026-10-15 (the user: "go deeper"; it was 20, from the simulator before the heal-in-place and positioning work). |
| `--crawl-max-leaves` | `2` | Survival leaves from a zone in one run that close it again. |
| `--crawl-max-hits` / `--crawl-max-fight-s` | `0.5` / `180` | Avoid a creature type costing more than this share of our max hits per fight, or taking longer than this per kill (estimated). |
| `--exit-dir D` | `4` (south) | The step from the spot onto the exit teleporter. |
| `--enter` | off | Start outside: walk to `--entry`, step `--entry-dir` to teleport in. |
| `--entry X Y Z` / `--entry-dir D` | `1912 2557 -20` / `0` | The tile before the NPD entrance teleporter and the step that triggers it. |
| `--enter-recall BOOK` / `--enter-rune NAME` | off / the book's default rune | Start anywhere: recall with this runebook / rune tome (serial) to the tome row `NAME`, hunt at the arrival; also how the runner goes back in. Needs `--leave-recall`. |
| `--leave-recall BOOK` | off | Leave by recalling to this book's default rune (home) instead of the exit walk; a hostile player close, a red in view or "X is attacking you!" recalls at once. |
| `--recall-spot X Y` | the arrival / `--spot` | A recall home refused where we stand (dungeons: only near a golden gate) walks here and recalls again. The route margin is measured to it. |
| `--bank-gold N` | `0` (off) | At home after a recall: with N gold or more in the pack, bank all of it. Needs `--leave-recall`. |
| `--pk-wait S` | `600` | Recall mode: after leaving from a hostile player, stay home this long before going back in. |
| `--return-at` | `auto` | Recall mode: take the loot home once this visit's carried value reaches V\* = r·sqrt(2T/h) (`auto`), a fixed gold value, or never (`off`); a full pack always ("Taking the loot home"). |
| `--drop-off` / `--no-drop-off` | on | Home after a recall (Logan's default rune is the house): the walk to the landing tile and the drop-off (homes.json `dropoff`), then back in from there; a drop-off that can't run (not at home, an item out of reach) ends the run at home. |
| `--repair` / `--no-repair` | on | Before each recall in from home: double-click the house's repair bench (homes.json `repair_bench`): worn armor and the pack's instruments repaired; an empty bench (or no answer) ends the run at home ("Recall in, recall out"). |
| `--go-home` | off | No hunting: from wherever the character stands, home, then the drop-off; exit 0 at home. Inside a dungeon that allows no recall, the escape's way out ("Guards": an error inside escapes home); elsewhere the recall with `--leave-recall`; at home already, the drop-off only. Give it the hunt's own arguments (the Seer: docs/OVERSEER.md §5's command plus `--go-home`). No pet in view: it goes without them. |
| `--no-oxygen` | off | No oxygen rule under water (Tidal Tomb's levels 2+; "Oxygen" below): no refill legs to the dolphins, `--heal-at` as given while deprived. |
| `--heal-at` | `0.75` (`--pets` 0.80) | Heal below this share of max hits (healing.py: a heal potion if one can be drunk, else a spell). |
| `--gheal-min-missing` | the mana break-even for your Magery (19 at 60) | Missing hits from which the heal spell is Greater Heal; below it, Heal. |
| `--leave-at` | `0.60` (`--pets` 0.50) | Leave below this share of max hits (plus the fight spot's route margin). |
| `--leave-per-step` | `0.004` | The route margin: `--leave-at` + this × the steps of the route from the fight spot back to `--spot`, at most `--heal-at` − 0.05. |
| `--leave-multi-at` | `0.80` (`--pets` 0.55) | Leave below this share when two or more mobs are attacking. |
| `--mana-reserve` | `22` | Mana kept for heals (two Greater Heals): the attack spell only while mana ≥ reserve + its cost. |
| `--spell` | `lightning` | Attack spell (Magery name or 1-64). |
| `--target-name` | `mongbat` (`--pets`: any monster) | Words the monster's name (or click label) must contain. |
| `--pull-range` | `3` (`--pets` 8) | Engage mobs within this many tiles of the spot. At most 10 (the spell range): a larger value is capped, with a log line. |
| `--kills N` | `0` | Stop after N kills (0: no limit). |
| `--timeout S` | `0` | Seconds from the run's start, all its visits together: then finish the fights on us, loot, and leave; 180 s past it the runner aborts wherever it is. 0 = no limit (user 2026-10-17: the return rule takes the loot home); the run ends by `--kills`, `ctl stop --after-trip` or a leave that doesn't go back in. |
| `--rest-to` | `0.95` | After leaving: Heal / Greater Heal when one can be cast (no potions; else natural regeneration) outside to this share of hits (and `--mana-reserve` mana), then go back in. `0`: stop after leaving. `--rest-timeout` (900 s) bounds the rest. |
| `--reposition-s` | `240` | No step for this long: walk 2–4 steps out and back to the fight spot before the Stationary Penalty comes (300 s); with nothing on us already from a draw in 0.8–1 × it. |
| `--loot` / `--no-loot`, `--loot-max` | on, 25 | Loot our kills' corpses (items per corpse). |
| `--soul-jars` / `--no-soul-jars` | on | Field of Souls: with an empty soul jar in the pack, walk to an eerie corpse in view and fill the jar there ("Soul jars" below). |
| `--human`, `--seed`, `--human-fast`, `--no-map`, ports, `--memory` | | As in the lumber runner. |

## What it does

Each tick re-reads the proxy state and decides, in this order:

1. **Leave** when a rule fires (`threat` juncture, job event `leave`):
   - a red player (threats.py) within 12 tiles: urgent; the run stops outside (greys and oranges no leave
     since 2026-10-15, the user: "I don't think we need to escape from a gray player. Only reds."; one first
     seen without its name waits the 1 s label grace: a tormented soul, human body, notoriety 3, sent a live
     run home as a "grey player" at 21:28:21);
   - two or more attackers and hits below `--leave-multi-at`;
   - hits below `--leave-at`, raised for a fight spot away from the exit (below).
   Leaving is war mode off (if on), the walk back to `--spot`, then the step in `--exit-dir`. The
   runner expects a teleport: a deny followed by the move (the NPD exit, live), or a confirmed step
   and the move right after. Outside it rests and goes back in (except after a hostile player).
   With `--leave-recall` leaving is a recall home instead ("Recall in, recall out").
2. **Speech hold** (speech_guard.py, as in the lumber runner) once no fight is on: nothing is sent
   until the overseer acks `speech_nearby`. Leaving to survive overrides the hold.
3. **Heal** below `--heal-at`, one heal per tick (`harness/healing.py`, shared with `ctl act heal`;
   user decision 2026-10-02):
   **Poisoned first** (`HuntLoop.cure`, since 2026-10-26; the tick, the walk's fight and the walk out): with the
   server's poison flag on us (0x17, `world.self.poisoned`) and a cure potion (`0x0F07`) in the pack at any depth,
   one double-click on it, as the heal potion's, then "cure potion: cured" or "still poisoned"; again after 1.5 s
   at the soonest. User 2026-10-26: "the runner seems not to use cure potions when Logan is poisoned" (live 22:31:09-49
   on Tidal Tomb level 1 he stayed poisoned 40 s until the user drank one: "You feel cured of poison!", cliloc
   500231). The simulator sends the flag and takes the potion; test_sim `test_cure_potion`.
   - **A heal potion whenever one can be drunk**: any heal potion (graphic `0x0F0C`) in the
     backpack at any bag depth, smallest stack first, its bags opened first, the stock
     double-click. "Can be drunk" means not at full health and 10 s since the last drink (the
     runner's own clock). If the server still refuses (cliloc 500235, e.g. after a drink in the
     client, or Outlands' "You must wait N second(s) before using another healing potion."), the clock
     is set to the wait it names (500235: a full cooldown) and a spell goes out in the same tick.
     "You are already at full health." (cliloc 1049547: a heal, e.g. a vet finish, landed between the
     choice and the click) is no drink: the clock stays where it was ("heal potion not drunk: already at
     full health"; ServUO's potion lock is taken only on a drink). Before 2026-10-25 it counted as one,
     and live 00:38:38.9 it left Logan without a potion at 65 and 45 hits for the 6 s to his death.
    "You must wait to perform another action." (cliloc 500119: an item use just before, the server's action delay)
    is no drink either: "heal potion: 'You must wait to perform another action.' (an item use just before); clicking it
    again in 0.6 s", `loop_hunt.DRINK_TRIES` 2 clicks in all, then "heal potion not drunk: 'You must wait to perform
    another action.' 2 times (N/M hits); the potion clock as it was". Before 2026-10-29 it was logged as a drink ("heal
    potion: 56 -> 56 hits") and counted on the clock: live 12:36:47.56, 0.26 s after the vet supplies' click, Logan went
    without a potion through 56 -> 23 -> 62 hits to his death 7.6 s later.
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
5. **Soul jar** (`--soul-jars`, "Soul jars" below) when nothing is attacking us: an empty soul jar in the pack
   and an eerie corpse in view: walk within 2 tiles of it and double-click the jar. Mid-fight only one that
   needs no step.
6. **Loot** our kills' corpses when nothing is attacking us, like `ctl act loot`: human corpses
   refused, except the one Outlands' 0xDEAD names as our kill's (a forgotten or tormented soul, a reanimated
   knight: human bodies; 59 such corpses were left on 2026-10-17 before this), walk within 2 tiles, open the backpack (stock dclick) and the corpse, then lift + drop
   each item into the backpack, gold first, up to the weight limit. See "Loot rights" below:
   blue corpses are skipped, a refused open stops at once, only items that reach the pack count.
7. **Fight** the target:
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
8. **Idle**: war mode off when nothing is near, back to the fight spot, wait.

**A fight spot away from the exit** (`--fight-spot`, user request 2026-10-03: the exit tile is
crowded). Each visit (at the start and after every re-entry) walks from the arrival to the
fight spot with the Mover (map route) before fighting; leaving walks back to `--spot` and
takes the exit step there.
- **Teleporter tiles**: the Mover never routes over a known teleporter tile (except as the goal
  tile itself): the memory store's `teleporters` table, learned whenever a step lands somewhere
  else, plus, for the session, the tile one `--exit-dir` step from `--spot` (the exit, known
  from the arguments). The NPD arrival tile (5536,530) is an exit teleporter too (Live below)
  and in the table since 2026-10-02, with (5535,530) and (5537,530).
- **A teleport the walk didn't mean** (`Mover.step_back`): a step onto a tile not known as a teleporter that moves
  us anyway, the walk's goal tile included (since 2026-10-24; before, a goal tile counted as meant), steps straight
  back through a known teleporter within 4 tiles leading within 4 of where we were; none known, it steps off the
  tile it landed on and back onto it ("teleported to (x, y); no known way back: stepping off it and back on"), as
  the user got out of the room below [INFERENCE: other doors may lead back the same way]. A walk whose goal was that
  tile then ends ("<label>: (x, y) is a teleporter (it put us at
  …)") rather than replanning onto it; a walk through a teleporter it knew ends there ("through the teleporter
  at …"). Live 2026-10-24 12:22:36: the step off the tank onto (5242, 612), Maus 3's mini-boss room door, put Logan
  in the room at (5242, 587) under its custodian; the walk took the tile as meant, found "no route", and he stood
  there until the user stepped him off the arrival and back on (out at (5243, 612)). Both tiles are seeds now
  (`harness/data/teleporters.json`); test_sim `test_boss_room_door`.
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

Map routes with the known teleporter tiles avoided (`pathfind.plan`, 2026-10-03); none
crosses the arrival tile (all come in from the north, via 5536,528):

| Fight spot | Steps out | Leave below (defaults) | Where |
|---|---|---|---|
| `5536 509` | 20 | 68 % | North corridor |
| `5539 507` | 22 | 69 % | North corridor, a little further |
| `5515 518` | 49 | 70 % (capped) | The west room |

(5537,505), further up the north corridor, is a wall / skull pile: no route. Not yet hunted
live; how many mongbats come by each is unknown.

**The weapon** (since 2026-10-03): the item on layer 1 or 2 at the start stays in hand.
- **An arcane staff takes casting skill.** Wiki [Arcane](https://wiki.uooutlands.com/Arcane):
  "Players with at least 80 skill in Arcane, Wrestling, and Magery can continue to cast spells
  while wielding an Arcane Staff". Live 2026-10-03 (capture 20261003_113952) each Lightning
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

## Recall in, recall out (`--enter-recall`, `--leave-recall`; user request 2026-10-03)

For hunting grounds reached by recall, first Urukton Bluffs (the rune in Shackleworth's blessed
tome "New Player Locations", 0x57C3DEB6; its default rune is Cambria, by the bankers).

- **In** (`--enter-recall BOOK --enter-rune NAME`): from anywhere (town), `escape.escape` to the
  tome row `NAME` (charges first, else the spell; the red escape's retry rules, at most 3 casts
  for this planned leg). The first arrival is the hunt: `--spot`, `--fight-spot` and
  `--recall-spot` default to it. Then the weapon goes back on (live 2026-10-03: the recall in, by
  a tome charge, put Shackleworth's prismatic staff in the pack like a cast; the runner notes it
  as a cast that disarms) before any walk or attack, and the visit starts as in the NPD (walk to
  the fight spot, Stationary Penalty, fight).
  **Not without the way home** (since 2026-10-25, `HuntLoop.check_charges`, before every recall in): the
  `--enter-recall` and `--leave-recall` books are glanced at (`escape.check_ready`) and each needs a charge per
  recall of the visit (one tome for both: 2), unless Recall can be cast (mana and reagents). Short: a `low_supplies`
  juncture (item `recall charges`) and the run ends at home ("… has N recall charge(s) and Recall can't be cast
  (mana/reagents): not going in"). Else "runetome 0x…: N recall charge(s)". Live 2026-10-25 17:53 the recall home
  took Logan's tome's last charge and the recall in failed ("reagents"); one fewer and he'd have stood at Totem
  with the loot. With --pets and a bard skill on, no instrument in the pack ends the run there too (`check_instrument`).
  **The armor aspect** (`--armor-aspect`, on by default since 2026-10-25; `check_aspect`, user: "make the runner check
  aspect before a trip and refuse to leave home without it"): before each recall in, the Aspect Mastery menu is read
  ("[aspect", one gump, `aspects.read`) and the worn armor checked (`aspects.armor_check`): Arcane Essence left and a
  worn armor piece in the armor section's aspect hue (Command: 1782, the menu's icon hue too). Off: a `low_supplies`
  juncture (item `armor aspect`) and the run ends at home ("armor aspect off: …; not going in"); on: "armor aspect:
  Command on arms, chest, …; N Arcane Essence". Live 2026-10-25 21:16:06 the essence ran out mid-run ("Your aspect
  items revert to normal (no arcane essence remaining).", the platemail 1782 -> 2207) and two hours of runs went
  without it. Characters without an armor aspect run with `--no-armor-aspect`; the simulator passes it (no menu).
  **The repair bench** (`--repair`, on by default since 2026-10-27; `HuntLoop.repair_gear`, user: "double click the
  repair bench before each trip into a dungeon to repair our equipment (instrument and armor)"): first of the checks
  before each recall in, at home with a homes.json `repair_bench` (Logan Wolf's 0x5B3BF0CF, 1 tile off the landing;
  from the landing tile when it's out of reach), one double-click (`repair.run`): the worn armor and the pack's
  instruments (their uses back to full; an instrument at 0 uses is lost) repaired. Logs "repair bench: Repaired N
  items." / "… in need of repairs." and writes a `repair` job event (`ok`, `repaired`, `line`, `elapsed_s`). Out of
  charges: a `low_supplies` juncture (item `repair bench charges`) and the run ends at home ("the repair bench is
  out of charges …: not going in"); a bench that doesn't answer in 8 s (one more click after 0.6 s of silence)
  ends it there too. Away from home (no bench): "repair: not at home …: going in without the house's repair
  bench". `--no-repair` goes without; the simulator has no home, so it only logs that line.
- **Out** (`--leave-recall BOOK`): every leave rule (hits, two attackers, hostile player, time up,
  done) recalls to the book's default rune instead of walking to an exit. A survival leave
  recalls at once from where it stands: no war-mode toggle, no walk, a spell cursor that is up is
  cancelled first (Esc `0x6C`), and the escape recasts until it lands or its 20 s budget is
  spent; the planned end turns war mode off first and tries 3 casts.
- **Hostile players** (recall mode adds to the NPD rule "a hostile player within 12 tiles"): a red
  anywhere in view (Bastet struck 4.6 s after sight, docs/PLAN.md "Red sighting") and a player
  named in "<name> is attacking you!" (seen or not: Bastet attacked hidden; that notice came 4/4
  times from players and never from a monster in the store). Not just once per tick: every state
  read, every human pause (every 0.2 s and at its end, as loop_lumber's `pause`) and every wait
  for a server answer (`wait_for`) checks, so the recall goes out at once (`PlayerThreat`). A
  drag (lift → drop) is never interrupted.
- **Refused recall** (`--recall-spot X Y`, default the arrival): Outlands dungeons allow
  Recall/Gate only within 8 tiles of a golden gate (docs/research/TRAVEL_DEATH.md). When the
  recall home fails where we stand (escape's new `restricted`: RunUO's 501802 "Thy spell doth not
  appear to work..." / 1019004, [INFERENCE] for Outlands' wording; or any other failure but death,
  mana, reagents), the runner walks to the recall spot (running on a survival leave) and recalls
  again; from then on it walks there first when it stands within 2 tiles of a refused tile. The
  route margin (`--leave-per-step`) is measured to the recall spot. A recall that still doesn't
  land stops the run (urgent `threat` juncture with the failure).
- **At home**: the weapon back on, then `--bank-gold N`: with N gold or more in the pack, find a
  banker (a known "the banker" click label, else single-click the human NPCs within 18 tiles,
  nearest first: the bank errand's search, now `agent_link.find_banker`), walk within 3 tiles,
  say "bank" and drag every gold pile from the pack into the box right after it opened (job
  event `bank`: `amount`, `piles`, `box_gold` = the gold coins in the box afterwards, checks not
  counted, `banked_run`). Then rest as in the NPD; after a hostile player wait until `--pk-wait`
  has passed since the leave; past `--timeout` or `--kills` the run ends at home; else recall in.
- **Stationary Penalty**: it comes at once after most recalls (above), so on arrival the walk to
  the fight spot or the penalty walk clears it before the first attack. The 60 s travel lockout
  after a recall stops harvesting only [INFERENCE: nothing in the captures shows it limiting
  combat]; the runner doesn't wait for it.
- **Crawl** works with it: the floor graph builds from the landing (Urukton offline, map0: 4,789
  tiles, 178 route steps deep, 74 waypoints; `crawl.py floor --spot 5248 2821 --start 5248 2821
  --z 33 --memory ''`). Not recommended there (below).
- **Memory**: `travel` job events per recall leg (`leg` in / home, the escape result: ok, method,
  rune, from, to, elapsed_s, attempts, tries, charges, failure); the `leave` event's `recall`
  (ok, method, from, to, attempts, failure, `refused` = the first try's failure and tile,
  `walked_first`), `react_s` (rule fired → recall start) and `cursor_cancelled`.

**Urukton Bluffs** (2026-10-03, the overseer's scouting, docs/NOTES.md): the rune lands at
(5248,2821,z33) on a raised landing inside the sanctuary ("You are entering a sanctuary
dungeon."), a golden moongate at (5246,2822) where recall is allowed (the recall spot default,
the arrival, is 2 tiles from it). Orcs, orc mages, an orc lord and a cave bear are on lower
levels with innocent prevalian soldiers fighting them; Shackleworth died in a manual fight ~85
steps from the landing, losing ~94 hits in 15 s (about 6 hits/s). So: fight at the landing (the
default), a small pull range, no crawl, and leave early: a recall takes ~2.1 s and a hit can
disturb it. Overseer command:

```
run hunt --enter-recall 0x57C3DEB6 --enter-rune "Urukton Bluffs" --leave-recall 0x57C3DEB6 --bank-gold 1 --target-name "" --pull-range 4 --heal-at 0.85 --leave-at 0.7 --leave-multi-at 0.9 --mana-reserve 999 --gheal-min-missing 1 --timeout 3600
```

### Taking the loot home (`--return-at`, `--drop-off`; user request 2026-10-17)

User, after two deaths to reds with a full pack: "modify the runner to leave and drop off loot once the value of our
loot exceeds the risk/reward of being killed by reds or mobs, like the lumber runner does" (`harness/hunt_return.py`).

- **The rule** (the lumber runner's, LUMBER_LOOP §6 "Return trigger"): the carried value grows at `r` gold a minute
  on the level, a death (reds or monsters, `h` a minute) loses all of it, a round trip costs `T` minutes; the cost
  per gold banked, rT/V + hV/(2r), is least at **V\* = r·sqrt(2T/h)**. A visit leaves once what it added reaches
  V\* (the value carried at the visit's start is a load a drop-off didn't take: it doesn't count), or when the pack
  is within `LOOT_WEIGHT_MARGIN` of full. Only between fights: nothing on us, nothing engaged, no corpse to loot.
  The leave is planned (severity `return`: no `threat` juncture, the walk to the gate at a normal pace with the
  pets following and waited for, the crawl doesn't mark the place) and the run goes back in.
- **The inputs**, from the store at each visit's start, this character's within 120 tiles of the spot since the first
  live-server run (2026-10-15 21:07; the test shard had no player killers):
  - `r`: gold a minute from the visit rows, gold only.
  - `h` (user 2026-10-17: "count red escapes as part of the risk"), split as the lumber runner splits it: player
    encounters a minute × P(death | encounter), plus monster deaths a minute. An encounter is a `leave` for a player
    (red in view, hostile grey, "… is attacking you!"), or a PK death with no such leave. The encounter rate is
    Gamma-Poisson, (1 + encounters) / (60 + minutes); P(death) is Beta(1, 3) with PK deaths against escapes. A
    death is a PK death by its cause, or a player leave within 300 s of it: both of 2026-10-17's were logged
    "unknown"/"pk" with the leave for the red a moment after. Monster deaths: (0.5 + deaths) / (60 + minutes).
    Priors [INFERENCE].
  - `T`, measured since 2026-10-17 and in three parts, each the mean of its samples with a prior worth one sample:
    - the way out: the `leave` event's `out_s`, from the leave firing to home; prior 1 min;
    - the drop-off: the `dropoff` event's `elapsed_s`; prior 0.5 min;
    - the way in: the visit row's `in_s`, from the recall in to the arrival; prior 3 min.

    The priors come from that day's logs: a red leave home in 9 s, the steward and the drop-off 29 s, walk-ins of
    3.5 and 2.2 min. Waiting out `--pk-wait` and resting to heal aren't a return's cost, so they aren't counted.
  - Live 2026-10-17 15:40: 1314 gold/min. 6 player encounters, 2 fatal (P(death) 0.30), and 1 monster death in
    78 min: h 0.026, a death every 38 min. T 4.5 min, all priors so far. **V\* ≈ 24,400**; the deaths-only
    model with T 6 min gave 26,700.
- **The carried value**: the pack's top level (where loot lands; our bags of supplies aside): its gold plus every item
  priced in the store under `loot:<name>` (the client's name, lower case, no " : N" or article), else a kind's row
  (`loot:aspect core` 10k for an unseen core, `loot:skill scroll`). Priced 2026-10-17: the user's (research materials
  25k, plant chemicals 1k, chromatic core 35k, gold ore map 12k, avarhide skinning map 15k, blood aspect core 14k,
  arcane scroll 1k, other cores 10k), the wiki's gem prices (citrine 20 ... diamond 100), the Discord #buy ladders
  for the other ore, skinning and lumber maps (2026-10-04), distillations 1k, phylacteries 1.5k, skill scrolls 5k.
  Aspect cores by kind from the Discord posts since September (NOTES "Loot prices for the return rule").
  `ctl lumber price "loot:<name>" <gp>` adds one.
- **The drop-off** (`--drop-off`, default on; homes.json `dropoff`): home after any recall (since 2026-10-21 Logan
  Wolf's house, his tome's default rune; before, the rental room through the house steward): `home.at_home` → the walk
  to the landing tile → `dropoff.run` (the user's Razor drop-off and the chest); the run then rests there and recalls
  back in from the house. The tomes sit in the wooden shelf (homes.json step `in`): back from a trip the client has
  forgotten its contents, so the drop-off opens it and waits for them. A drop-off that can't run (not at home, an
  item out of reach, a container that won't open) ends the run at home with the load (`Abort`, task_failed; a
  `dropoff` job event with the error): until 2026-10-21 the run went back in with it (live 22:32:36, 25.5k gp back
  to Maus Near 2). A refused step still lets the run go on. The simulator has no home (`--no-drop-off` in run.py's
  defaults); it counts what we carried at each landing home as banked (docs/SIM.md "Red players", "Gold banked").
- `--return-at auto` (default) | `off` | `GOLD` (a fixed value).

## Soul jars (`--soul-jars`; `harness/soul_jar.py`; user request 2026-10-17)

Outlands' Field of Souls event (2026-10-17 to 10-24, docs/NOTES.md "Field of Souls"): an empty soul jar
double-clicked within 4 tiles of an eerie corpse becomes a filled jar, the ticket into the event (+30 score).
Eerie corpses are rare and players race for them: in about 4.5 hours of play on 2026-10-17, 5 came into Logan's view
in the Mausoleum area and he used 2 (the store's `item_seen`). The user:
"I'd like the hunt runner to be able to fill soul jars on its own when we have a jar and an eerie corpse is found".

- **When**: each tick, after the heal, the pets' care and the Stationary Penalty and before looting, when nothing is
  attacking us, an empty soul jar (`0xA99F`) is in the pack at any bag depth (the runner loots the ones monsters drop:
  `combat.worth_looting` takes them) and an eerie corpse (`soul_jar.eerie_corpses`: "dead" corpse art with its eerie
  whispers a few serials on, docs/NOTES.md "Field of Souls") is in the world model (in view) on
  our floor (`same_floor`) and not tried yet: the nearest. While a target is engaged (the pets mid-fight), and with
  `--pets` while the pets fight or lag (`patrol_hold`: `pets_busy`, `pets_lagging`), only a corpse within 2 tiles
  counts, so the runner never walks away from a fight or from the pets for one. A crawl's patrol walk stops for one
  (`patrol_stop`).
- **How**: the Mover walks within `soul_jar.FILL_WALK_R` = 2 tiles (Logan filled from 2; the event post says 4).
  The walk stops when something attacks us, a leave rule fires or the corpse goes, and with `--pets` on the
  patrol's guards: the pets fighting or behind (`patrol_hold`), or a step that would pull more than the budget or
  a monster in sight to let come (`Team.pull_check`; the corpse then waits `JAR_DEFER_S` = `tamer.AT_ONCE_S`
  120 s, past the pull check's "no other way on" memory, while the crawl takes those monsters). Since 2026-10-20 a
  wait for a monster to come (the hold, "...: letting it come") ends at our next kill: "an eerie corpse's walk
  waited for: <why>; a kill since: trying it again" (the pull check names no monster; a retry that still finds
  one to hold for stops and waits again). A deferred corpse within 2 tiles is tried at once: no step, nothing to
  pull. Live 2026-10-20 23:02:03-23:04:10 the bat held for died 12 s after the stop, Logan later looted one tile
  from the corpse, and the user filled the jar by hand. The next tick
  decides again; a walk that can't get there gives the corpse up, and so does one whose route is longer than twice
  the distance plus 8 steps, like a loot walk (since 2026-10-24: in the simulator a corpse 18 tiles off by a 143-step
  way round left the view on the way, "the eerie corpse is gone", and was walked to again every 3 s for 2 minutes).
  Live 2026-10-20 18:22 the unguarded walk went
  16 steps alone into an idle vampire countess, corpsebride and vampire bat by the corpse and Logan died
  (docs/NOTES.md "Field of Souls"). Then the jar's bags are opened like a player
  (`containers_to_open`), the jar double-clicked, and the runner waits up to 3 s for a filled jar (`0xA9A3`) in
  the pack (live it came 44 ms after the click).
- **One try per corpse** (`jar_tried`), filled or not. Live, nothing was ever seen of a fill that failed, so a miss
  logs whatever the server said and moves on.
- **Memory**: a `soul_jar` job event: `corpse`, `at` (x, y, z), `jar`, `spirit` (from "spirit energy"'s "'My name
  was …'"), `filled`, `filled_jar` or `heard` (the server's lines on a miss); the run's tally counts `soul_jars`.
- **Not done by the runner**: taking the filled jar to an Occult Merchant (Prevalia or Corpse Creek). It decays 60
  minutes after the fill; the log line says so.
- **The sound**: the proxy plays a ghostly wail the first time any of our characters sees each eerie corpse,
  runner or not (`alerts.eerie`; docs/NOTES.md "Field of Souls").
- Test: `test_sim.py` `test_soul_jar` (the simulator's eerie corpse and jar: walk, fill, no jar no walk, mid-fight),
  `test_soul_jar_guards` (the 18:22 room: no walk while the pets fight, the walk stops short of the idle monsters).
  The simulator spawns eerie corpses with `run.py --eerie`, and `harness/sim/fight1822.py` replays the minute
  (docs/SIM.md).

## Pets (`--pets`; `harness/tamer.py`; user request 2026-10-12)

The user's tamer, **Logan Wolf**, fights with pets; this style is learned from his Mausoleum trip of
2026-10-12 (capture `20261012_210013`, 21:16–21:31; analysis in docs/NOTES.md "Logan Wolf's tamer
fighting at the Mausoleum"). He is a tribard tamer (Taming, Lore, Veterinary, Herding 120;
Peacemaking, Discordance, Provocation 80; Tactics 18) with a bonded phoenix "AlaFlambe" (4 control
slots) and a bonded gravebug "GraveRobber" (1 slot, the tank). On the trip he never turned war mode
on nor swung, and killed 25 creatures in 14 minutes (his Society job "Kill 25,000 Gold Worth of
Creatures" went 290 → 24,243).

```
run hunt --pets --leave-recall <TOME> --crawl              # standing on the hunting level, pets out
run hunt --pets --pet GraveRobber --pet AlaFlambe --enter-recall <TOME> --enter-rune "Mausoleum Inside" --leave-recall <TOME>
```

| Argument | Default | Meaning |
|---|---|---|
| `--pets` | off | The pet team fights; never war mode, an attack or a spell (not even a heal spell without Magery). |
| `--pet NAME\|SERIAL` | the tamed pets within 3 tiles, plus the remembered ones in view | One of our pets (repeat). Without it the roster is whatever pets the user brought (since 2026-10-24): every pet within 3 tiles with a "(tame)" / "(bonded)" line (a non-human, non-attackable mobile there without one yet is single-clicked first, as the client does, so its line comes: "looked at N untagged mobile(s) beside us: …"), and the pets remembered from earlier runs in view anywhere (a start inside the dungeon). Remembered per character (memory meta `own_pets`). The run won't start without one. A pet of ours standing out in view that shouldn't hunt: stable it. |
| `--pet-heal-at` | `0.95` | Veterinary supplies when a pet within `--vet-range` is below this share of hits (or poisoned), in a fight (`aggro.View.engaged`). With no fight on only below `tamer.CALM_HEAL_AT` 0.80, and only that (or a ghost, or us below `--heal-at`) holds the crawl's patrol, a channel included (`Team.busy_healing`; since 2026-10-24, user: "waiting to heal before moving on when a pet is just missing a few HP"; the next fight's vets top it up; test_sim `test_calm_heal`). Resting outside still waits for this share. |
| `--pet-leave-at` | `0.20` | Leave when a pet is below this. |
| `--vet-range` | `2` | Tiles the supplies reach. The Healer's Codex perk "Without Borders" adds 1/2/3 tiles (knowledge #11343); the user is levelling it (2026-10-12), so raise this with its tier. |
| `--retreat-at` | `0.55` | Below this share of hits with a mob next to us: run 7 tiles from every attacker and let the pets hold it. |
| `--discord` / `--peace` / `--provoke` | on | Each bard skill (`--no-…` turns one off). |
| `--songs` | on | Keep the three barding songs up (`--no-songs` turns them off). |
| `--heal-at`, `--leave-at`, `--leave-multi-at`, `--pull-range`, `--target-name`, `--crawl-dwell`, `--crawl-depleted-s`, `--crawl-max-dmg` | `0.80`, `0.50`, `0.55`, `8`, any monster, `1.5`, `5`, `20` | The pets style's own defaults (STYLE_DEFAULTS; the mage defaults are unchanged). The leave at 50 % and zones only below 20 hits lost a minute came with moving on at once from a cleared area (docs/SIM.md "Faster from one fight to the next"). |

**The rules** (each from the capture or a user statement):

1. **Guard, don't pull.** "All Guard Me" at every arrival (guarding stops at a recall or a level
   transition, wiki Animal Taming) and after every kill; Logan said it 26 times for 25 kills. Also at once,
   past the 8 s between guards (`GUARD_REPEAT_S`), when a pet comes out of a vampire countess' transfix with
   something on us (an attacker, or a hit on us within 5 s; `Team.free_pets`: her `*transfixed*` over a hostile
   copy named as the pet, the pet free when the copy leaves the view, at most 12 s): "<pet> free of the transfix
   after N s, <who> on us: 'All Guard Me'" (user 2026-10-21: "once the tank is transfixed and the countess
   aggros on our character ... 'All Guard Me' to get the aggro back on the tank once it is un-transfixed"; live
   hunt-20261021-091743-38b6 09:48:53-09:49:53 none in six releases). And once the target of an "All Kill" the
   pets took (no "Target cannot be seen.") is dropped, passed over, out of view or dead (`Team.kill_order_over`,
   since 2026-10-24): "All Kill" ends guarding (guarding pets keep war mode on, live; after an "All Kill" at a
   monster they can't reach they went out of it within 1 s and followed), so "'All Kill' target 0x… dropped: the
   pets off guard since; 'All Guard Me' due", then the guard in care's order. Live 2026-10-23 23:29:07-32 three
   "All Kill"s at a reanimated daemon, dropped 23:29:34, no "All Guard Me" after; at 23:30:22.5 a blightmare woke on
   Logan and cast at him with both pets beside him out of war mode until 2.4 s after the next "All Kill" (23:30:30.6),
   dead at 36.96 (docs/NOTES.md "Dead with the pets off guard"; `sim/fight2330.py` 3 of 96 seeds dead before, 0
   after). The
   user (2026-10-12): a mob aggroes on what comes into its aggro range and line of sight; pets on
   guard engage it before it engages the character, while an aggressive act of ours (Discordance,
   "All Kill") makes the mob aggro on the character. So a target the pets haven't engaged is only
   approached to 6 tiles (`APPROACH_R`, [INFERENCE] aggro range) and waited for; one that neither
   fights the pets nor us for 15 s is dropped for 60 s. An idle target within 6 tiles that can't see
   us there (a Wall-flagged static blocks too, as for the hold) is passed over at once for those 60 s
   (`Team.unseen`; live 20261017_181210: three targets 6 tiles off behind walls were waited for 15 s
   each and dropped; user: "waiting for <mob> to come on when the mob can't see Logan through walls").
   The patrol meets it later the usual way (the hold, the pull spot). Walking on into its sight instead
   cost the simulator 9 more runs with a death in 48 (NOTES "Targets behind walls").
   A monster at another height (its surface more than `FLOOR_Z_TOL` 10 off our z) that only a long way round on
   foot reaches (no walk to beside it at its height within `FLOOR_DETOUR` 4 steps more than the beeline:
   a ledge or a floor above or below, `HuntLoop.on_our_floor`) isn't a target at all unless it attacks us (one
   hitting a pet from up there doesn't count, nor does it make the pets "busy" for the patrol: live 2026-10-19
   19:34:43-35:50 an insane reanimator at z 30 over GraveRobber at z 5 was picked, dropped and picked again every
   15 s while the walk in stood its whole 90 s), and doesn't hold a crawl waypoint either; neither does a passed-over
   one (user 2026-10-17: "it doesn't understand the z-levels put the creatures out of sight above it"). One the
   short way reaches is approached **on its floor**: the walk ends within 6 tiles standing within `FLOOR_Z_TOL` of
   its surface (`Team.approach`, `HuntLoop.floor_z_ok`). The beeline to within 6 tiles ended on the ledge above it,
   where it was off our floor again, and the patrol's next step made it a target again: live 2026-10-21
   22:27:28-22:28:42, 40 rounds between (5180, 684) and (5181, 685) over two shade wolves 28 z below (user: "stuck
   bumping into a wall trying to approach a shade wolf"; `sim/fight2227.py`).
   **Someone else's fight**: a target in war mode within `ELSEWHERE_R` 2 of another player or a pet not ours,
   not on us or our pets, with neither pet in war mode beside it (within 2; the guarding pets keep war mode between
   fights: live 2026-10-19 10:10:57-10:11:28 two tormented souls on other players were waited for 15 s each while
   the pets stood at our side in war mode 7 tiles off, the old "within 12" rule) (`Team.elsewhere`), won't come on: the
   pets get "All Kill" at it at once when we see it within 10 (a no-riskier step to see it first, `bard_step`;
   no such step, e.g. behind a wall: a walk toward it until we see it, at most 10 moves, `walk_to_see`, and one
   still unseen 10 s after that is passed over for 60 s; farther, the approach), every 12 s while they haven't
   taken it (user 2026-10-17: "We need to just 'all kill' if the mob is already aggro'd on something else"). The
   simulator found the walk missing: from Logan's live tile (5242, 683) the first soul stood behind a wall
   (los.Sight), no tile within 2 steps saw it, and the fixed rule still waited 15 s (test_sim
   `test_on_another_player`). **Only while it is near full hits** (since 2026-10-24): one they already have below
   `tamer.THEIRS_BELOW` 0.8 of its hits is passed over for 60 s instead ("passing over <it>: <who> has it at N %, its
   loot is theirs"): the most damage takes the corpse (live 12 % of the credited kills were blue; in the simulator 63
   of 63 kills after such an "All Kill" were). test_sim `test_theirs_passed_over`. **Not while our "All Kill" at it is
   under `KILL_REPEAT_S` 12 s old** (since 2026-10-25): it was near full hits then, and the hits it loses while the
   pets run there are partly theirs; once 12 s pass with no pet beside it, the share counts again (live: 25 of 34 such
   "All Kill"s were dropped 1-5 s later at 52-79 %, and 11 of the 21 corpses seen were ours all the same).
   test_sim `test_theirs_after_kill_sent`.
   **An ally's fight is left alone** (since 2026-10-28; user: "detect when a player already killing a mob is an ally
   (member of DTF or an allied guild, a 'green' player) and not attack that mob. Goal is to avoid 'kill stealing'
   allies or looting their kills"): a monster in war mode within `ELSEWHERE_R` 2 of an ally (`threats.ally`: notoriety
   2, green to us, or our guild's tag; or a green pet not ours) is never a target (`HuntLoop.ally_fight`, in
   `pick_target`) unless it attacks us or a pet of ours, so no "All Kill", no bard skill, no approach: "leaving <it>
   0x… to <ally> (an ally is fighting it)". The serial is remembered (`ally_fought`) and its corpse is never looted,
   whoever killed it and whatever the server allows: "corpse of <it>: <ally> (an ally) was fighting it; not looted",
   the kill credit taken back. A blue player's fight is still taken as above. Limits: an ally fighting from range
   (an archer, a mage) more than 2 tiles off isn't seen until the monster comes to them [INFERENCE: a melee monster
   does]. test_loop_hunt `ally_courtesy`, test_sim `test_ally_left_alone`.
2. **Bard skills only at a target the pets fight** (`engaged`: hurt within 10 s, a pet within 12
   tiles, the phoenix's reach). **Provocation first when two monsters are in the fight** (since 2026-10-26,
   `tamer.PROVOKE_FIRST` "pairs", `Team.provoke_first`: the target onto the one nearest it). The user on Tidal Tomb's
   level 2: "In that area I'm generally able to keep the mobs provo'd onto each other". In the simulator, level 3 runs
   with a death went 12 -> 5 of 24, level 2's damage on Logan 2,192 -> 1,708 an hour, and Maus 3 was no worse. Then
   Discordance, then Provocation of another mob in the fight (the other onto a second other, or onto our target), then
   Peacemaking on the target (it stops hitting the pets while they kill it). A provoked target isn't pacified. 51 of
   Logan's 60 bard/"All Kill" target answers on the trip were at such a target by this rule; the other 9 were
   openers the user now advises against. Cooldowns: one bard skill every 5 s, peace/provoke 10 s
   after a success (wiki Peacemaking); "You must wait a few moments to use another skill."
   (cliloc 500118) moves the clock; "beyond your skill" stops that skill on that creature type;
   a Barding Break or "That target is currently under a barding break effect." makes it immune to
   peace/provoke for 20 s [INFERENCE: difficulty / 10 s]; "Target cannot be seen." blocks it 10 s. An "All Kill"
   answered so isn't sent at that monster again while we and it stay on the tiles it was refused from, past the 10 s
   too ("'All Kill' at 0x…: cannot be seen; not again from here while it stays put"; `Team.kill_unseen`; live
   2026-10-23 23:29:47 and 23:29:59 a reanimated daemon behind a wall on Logan was refused twice from (5241, 693);
   user: "keeps trying to 'All Kill' a reanimated demon that is not in line of sight"; test_sim `test_kill_unseen_once`).
   **In sight first** (user 2026-10-17: "A lot of times it tries to bard a mob that cannot be seen and
   fails"): a skill goes only at a target we see by the server's rule (`Team.bard_sees`, `aggro.View.we_see`:
   from us to it, within 10), a provoked pair both seen. One due but unseen: a step (up to 2, at most every
   3 s, never mid-channel) to the tile that sees it with the fewest eyes on us, fewer than here (a step costs 0.3),
   under the reposition's rules (`Team.bard_step`); none: no skill this tick. **A target not on us** (on a pet), a
   peek: up to 4 steps (`PEEK_STEPS`), only the eyes that would wake to us counted (idle monsters before a pet, a
   --never-aggro one, the backspawn; not those already fighting, the target included) and no more of them than here,
   the pets kept within --vet-range only while a vet channel runs; the skill goes from the new tile at once and the
   cover or the reposition walks us back out next tick (since 2026-10-25: the cover and the caster reposition keep us
   out of the sight of a blightmare, purger or caster the pets fight, so no tile ever counted as good enough: 0 skills
   at 96 shy kills 2026-10-22..25; round a corner from two awake purgers, 23:31, one skill in 50 s with four monsters
   on the pets, the only seeing tile 4 steps off; docs/NOTES.md "No bard skill at a caster or shy monster").
   **Barding songs** (`--songs`; user 2026-10-13, capture 20261013_173449): the same skills at a
   ground tile beside us play the Songs of Discordance, Peacemaking and Provocation on us and the
   pets for 15 minutes (live 15.0 % each: less damage taken from creatures, more healing received,
   more damage). One song every 10 s whenever nothing more urgent is due, fights included (a care
   action after the retreat, vet supplies, guard and walk to a hurt pet), each again a minute before
   it ends. The user sang them mid-fight too.
  **The song's ground tile** (`Team.song_spot`, since 2026-10-26): the nearest tile 1-3 off with a floor within 10 z
  of ours that we see (los.Sight, eye to the floor). Where that floor is the tile's land and the land is valid: the
  land (graphic 0), as every Mausoleum song live. Graphic 0 is the land at its own height (RunUO LandTarget), out of
  sight wherever a floor stands over it: the invalid land 0x244 (Tidal Tomb, z -100; live 2026-10-26 21:52-21:54 every
  song got "Target cannot be seen.", now logged "<song>: (x, y, z) graphic 0x0000 cannot be seen"), and valid land
  under a raised floor (since 2026-10-27: live 01:04:42-01:05:22 on Tidal Tomb level 1's ledge, land at z 19 under the
  stone 0x71E and the floor 0x51E at 24, four songs refused so). There it names the flat floor static (height 0) on
  top, as the user's 7 songs did; not a block under it (22:08:16-38 three songs named the stone 0x71E/0x71F under the
  pavers and got no answer), and not a tile with something taller on it. No such tile (the Mausoleum arrival's stairs
  top): "<song>: no floor tile within 3 tiles in our sight; not singing here", the cursor cancelled. The simulator
  answers graphic 0 at the land's own height too. test_sim `test_songs_tidal_floor` (the arrival and the ledge).
   **The instrument**: when the one last played is gone (after a death, the lute left on the corpse; worn out:
   "You broke your instrument trying to play it.", cliloc 502079, logged as "the instrument broke") the
   server asks "What instrument shall you play?" (cliloc 500617) with a cursor before every bard skill
   and song; the runner answers it with an instrument from the pack (`Team.instrument`: by graphic,
   `tamer.INSTRUMENT_GRAPHICS`, else by name, `INSTRUMENT_NAME`; any bag depth) and then the skill's own
   cursor. With none in the pack it cancels, posts a
   `low_supplies` juncture and stops the bard skills and songs (`Team._instrument`). Live
   20261015_171928 after the 18:40 death the runner answered the prompt with the skill's target ("That
   is not a musical instrument.", 54 times): two runs without one bard skill or song. **No recall in
   without one** (since 2026-10-25, `HuntLoop.check_instrument`): with any bard skill or song on, a run at
   home with no instrument in the pack posts `low_supplies` (item `instrument`) and ends there ("no instrument
   in the pack: not going in"); `--no-discord --no-peace --no-provoke --no-songs` go without. Live 2026-10-25
   17:24:23 Logan's durable psaltery (0x4E2A, unknown by graphic then) broke at a Discordance and the visit went
   on without bard skills; the user's replacement, a bamboo flute (0x2805), is why names count too.
   A bard skill's cursor is cancelled when Logan is below `--heal-at` with a heal potion ready
   (`Team.potion_first`; live 19:19:30.9-35.2 a Discordance answered at 42 hits, the potion 3.6 s later).
   **Area Peacemaking in a jam** (user 2026-10-15: "in a jam you can peace the area around you by
   activating Peacemaking and targeting yourself. It puts a short duration peace on mobs close to you. Good
   if you are walking through a group of mobs or get ganged up on"; wiki: a check per non-tamed creature
   within 8 tiles, 2 s pacified each, the usual cooldowns; live 20261015_171928 17:22:01 the user's own
   answered "You play successfully, briefly pacifying one or more nearby creatures."): `Team.area_peace`
   when two or more attack us within 3, or one while we're below `--retreat-at`, or on the walk out to the
   gate past anything in war mode within 3 (first in the care order; on the walk out between steps; since 2026-10-28
   not on a planned walk out, which stops and fights what attacks it: "Something coming on a planned walk out" below).
   fight1949, 24 seeds: deaths 1 -> 0, damage on Logan 147 -> 83, lowest hits 0 -> 39.
3. **"All Kill" only at a mob already on us** that the pets leave alone for 4 s (its aggro is on us
   anyway), or at the pets' target when a live pet within 12 of it stands out of war mode for 5 s
   (`Team.idle_pet`; live 20261015_210709 21:19:51-20:43 the phoenix came out of a countess' transfix and
   stood 45 s while the gravebug fought her alone), at most every 12 s per mob; the target cursor is
   answered after an aim pause and a fresh read (the monsters-only guard). Not re-guarded until the kill,
   unless a pet comes out of a transfix with something on us (rule 1).
4. **Vet supplies** (graphic `0x6496`, one double-click, no target): every pet within range and us
   at 25 % (patch 2023-08-14); a 5.0–5.3 s channel (43 on the trip) with the range checked at the
   finish, so no step during it. Used for a hurt or poisoned pet in range, or for us below
   `--heal-at` whatever the potions and the pets' distance (they heal us with no pet in reach: live
   09:16:36-41 on 2026-10-15, +22; the user: "Logan should have vet supply-bandaged himself when he got
   low even though he wasn't close enough to heal the pets"), ahead of the stand-off and spacing steps;
   a hurt pet out of range is walked to when nothing is on us: without the Healer's Codex "Without Borders"
   (`--vet-range` below 4) next to it (1 tile: bandaging reaches 2 only after 5 s stationary); with it to the
   vet tile (`Team.vet_tile`): within `--vet-range` of the pet, 3+ off every pet and monster in war mode (the
   spacing rule), out of fire, the least deep in an area hitter's reach, then within `--vet-range` - 1 (a step
   of the pet's during the channel), the fewest idle monsters seeing it: "to a hurt pet: (x, y), N from <pet>
   (--vet-range 5), D into an area hit's reach" (user 2026-10-21: "we seem to go stand directly on our tank which
   is getting us wallopped by the demon's AOE, but our Without Borders healing codex perk lets us heal from 5
   tiles away"; live 09:53:52-56 the walk went to 1 of the gravebug, beside the reanimated daemon, and its bone
   burst hit Logan -41; test_sim `test_vet_tile_off_tank`). No such tile: within `--vet-range` - 1 of it. No
   supplies: a `low_supplies`
   juncture (item `veterinary supplies`). Resting outside waits for every pet to reach
   `--pet-heal-at`, by the supplies or, with none, by natural regeneration; past `--rest-timeout`
   the run stops rather than go back in with a hurt pet (that pet's leave rule would fire on arrival).
5. **Self care**: heal potions double-clicked where they sit in the pack, no bag opened, as the user's
   Razor "Drink Heal Potion" hotkey does (2026-10-15; ANTICHEAT.md "Exception: potions ... like Razor"); the retreat above (live 21:27:50 a bleed took Logan from 72
   to 35 hits in 2 s; he ran 18 tiles while drinking and the pets held the mob). The retreat goes to
   the tile 7+ from every attacker within 14 steps that the fewest idle monsters would see
   (`Team.retreat_tile`; live 20261015_090607 the straight-away tile was in a second apparition's
   sight and it came for Logan: user 2026-10-15, a monster that sees us while the pets fight aggroes
   on us directly). A step away in a fight (the retreat, the stand-off, the back-off, the
   fall-back, back to the pets, and since 2026-10-22 the spacing off the tank, the pull spot, out of the fire, the
   reposition, the bard step and the cover) whose route only mobiles block gives up at once (`Mover.walk_to`
   `mobile_wait=0`; after a stamina potion when short): the tick goes on to the potion and the vet.
   Before 2026-10-20 it waited up to `MOBILE_WAIT_S` 90 s where it stood (live 2026-10-15 22:57:56 dead
   a second after "cut by mobiles … waiting"; simulator fight1822 seed 5, dead in the countess
   corridor while "waiting"). Any other walk that isn't a flight (a loot walk, to a hurt pet, an approach, a
   patrol) ends its wait for mobiles as soon as our hits fall below where they were when it began ("cut by
   mobiles; hurt waiting (A -> B)"); the leave's urgent walk to the gate keeps waiting and drinking (simulator
   2026-10-21: 29 of 84 deaths stood "cut by mobiles we couldn't shove; waiting", 13 of them "off the tank", 11 of
   those at (5192, 672, 25) in the countess corridor).
   **Out of a Charge's reach** (user 2026-10-13). A charger is a nightstalker (knowledge #14625:
   it teleports up to 12 tiles next to its target and hits up to 3 within 1 tile), or any type seen
   to say "*charges*". While one is in war mode, the runner keeps 3 tiles from it and from the pet
   beside it (`tamer.Team.stand_off`, up to 6 steps, running). Other area hitters likewise
   (`tamer.AREA_R`): an apparition 3 (its hit lands on everyone within 2: 20261015_090607 and _115604),
   a reanimated daemon 6 (its bone burst reached Logan at 5, 20261014_221341), since 2026-10-26 a corpse purger 3 (its
   barrage lands on the ground round its target's tile, spread 2, and the disease stacks) and a master reanimator 3
   (its chain lightning jumps from its target to everyone near, through walls; [INFERENCE] the 3), and an oozing
   noughtilus 3 (Tidal Tomb: its Ooze Aura, 4 bursts on everyone within 2 every 7-12 s; user 2026-10-26: "We should
   not stand next to oozing noughtiluses, they do AOE poison bursts"; simulator level 1, 8 runs: its damage on Logan
   1,118 -> 421, all damage on him 2,868 -> 1,886). **Since 2026-10-28 every area hitter alike** (user: "we should
   treat all these 'area hitters' the same and avoid standing in their range"): `AREA_R` also takes every type whose
   hit lands on everyone within r of itself in the simulator's scenarios (burst, ooze, wail and bleed_area specials,
   not one centred on its target), at r + 1. That adds the razorfin, voidfin, frenzyfin, greater razorfin and greater
   frenzyfin 3 (their spins), the colossal scourgeon 3, the tidal sea serpent 4, the drowned buccaneer 2, the
   corpsebride 3 and the bound soul 2. Hits reaching beyond `AREA_MAX_R` 5 (the phantasm's lament 12) are left to the
   caster rules. Simulator 2026-10-28 (24 runs, 96 on Maus 3): level 2 damage on Logan 2,017 -> 1,644 an hour, deaths 7
   -> 5; level 3 3,379 -> 1,971; Maus 3 deaths 7 -> 5, banked +5 %. **Since 2026-10-28 22:53** (Logan dead on Tidal Tomb
   level 1 beside both) also a hit on everyone within r of its target beside it or of where it comes up on its target:
   the lowtide scavenger's large swing (everyone within 1 of the tile it faces) and the dungeon-nest crab's Dig (it goes
   under and comes up on its target's tile 1.5-2.1 s later, a hit within 1), both `AREA_R` 2 (docs/NOTES.md "Tidal
   Tomb: dead on level 1 on the way out"). **A hit centred on its target, a pet up to 12 off** (since 2026-10-29; Logan
   dead on Tidal Tomb level 2, docs/NOTES.md "Tidal Tomb: level 2 death 2026-10-29 12:36"): the combustive zoas' scorch
   and the astral zoas' spell land on the pet they target and on each of us within 3 of it, wherever the zoa stands
   and whether or not it sees us. `tamer.TARGET_AREA` takes every scenario `burst` with `center` "target" and a radius
   (reach 12 or 11, keep 4); `Team.target_zone` makes each live pet within that reach of one in war mode a zone of 4,
   logged once a monster: "area hit on its target: the awake combustive zoa 0x... at (x, y) hits each of us within 3
   of the pet it targets; keeping 4+ off the pets within 12 of it". The stand-off steps out of it, the routes bend
   round its tiles (the Mover's `avoid` "target_area", as the cone), and the cover, the reposition and the bard step
   no longer pick a tile inside any area zone (they checked only its centres, so the stand-off walked us back out of
   where they put us). With `--vet-range` 5 the vet supplies reach the pets from 4-5. **The Tidal Tomb shooters' target
   areas** (since 2026-10-29 evening; docs/NOTES.md "Tidal Tomb: the zoas' lightning, the scorch's susceptibility, the
   shooters' hits"): the drowned mariner's shot and the soulcatcher's spell land on their target, a pet 1-11 off, and on
   each of us within 1 of it, so `TARGET_AREA` also takes every scenario `ranged_observed` entry with an `area_r` (reach
   its `range` 11, keep 2): "area hit on its target: the awake drowned mariner 0x... at (x, y) hits each of us within 1
   of the pet it targets; keeping 2+ off the pets within 11 of it". Only a hit coming at least every
   `TARGET_AREA_EVERY_S` 15 s makes a zone: the zoas' lightning (everyone within 4 of its target, one every 22-87 s)
   does not, since keeping 5 off the pets of every awake zoa cost more than the hits it saved (simulator level 2, 144
   runs: runs with a death 24 -> 33, pet deaths 223 -> 271). **Ablation** (level 2, 432 runs, seeds 1-432, the new
   model): with neither rule 85 runs with a death; this one alone 101, the next one alone 93, both 69; Logan's damage an
   hour 1,512 / 1,584 / 1,321 / 1,328 (docs/SIM.md "Tidal Tomb: the zoas' lightning, ..."). Kept together only.
   **An ocean bedrock's spray** (since 2026-10-26; user: "I stand
   behind the ocean bedrock so it doesn't do its spray attack on me"; docs/NOTES.md "The ocean bedrock's spray"). Its
   frontal cone is in the zone at 1 a tile (`Team.spray_cone`, `nav.in_cone`): 90°, 8 tiles, along its 0x77 direction
   and toward each pet beside it. That holds while it is awake, unless it is pacified or provoked onto a monster still
   alive. Its wind-up (animation action 18, `spray_due`) with us in the cone starts the stand-off at once, past the
  repeat gap: the wave reaches ring k 0.71 + 0.1·(k−1) s later. **Out of its cone whatever the walk** (since
  2026-10-28; user, live hunt-20261028-215803-e48f: "Logan is still standing directly in ocean bedrock spray";
  docs/NOTES.md "Tidal Tomb: Logan in the bedrock's spray"). Every awake bedrock's cone (`Team.sprayers`: neither
  pacified nor provoked onto a live monster) is among the Mover's `avoid` tiles (`Team.spray_watch`, each tick and on
  every step from the hunt's Mover guard), so every route (patrol, approach, loot, vet, spacing, stand-off) bends round
  it at 30x a tile and crosses it only when there is no other way. A wind-up with us in the cone or a step from it
  ends a walk under way (`Team.spray_stop`: the stand-off, the walk to a pet, off the tank, the loot walk ("to the
  corpse: stopped (…); looting it after the wave") and every advance through `pull_check`), and no such walk starts
  then (`Team.spray_wait`: "ocean bedrock's spray: no walk beside its cone till its wave is past"). On the wind-up the
  stand-off takes the clear tile the fewest steps off, of those no worse for idle eyes than here: "ocean bedrock's
  spray: stepping out of its cone to (x, y), N step(s)". Otherwise its tile ranks one within 2 tiles more of a cone
  (`SPRAY_AHEAD`, the bedrock walks at its target) below one beside or behind the bedrock, as the user stands. An awake
  bedrock within `HOLD_R` that fights no one else holds an advance like an idle monster that sees us ("holding here:
  an awake ocean bedrock at N tiles, out of its spray cone: letting it come", `HOLD_S` from first sight): it comes on
  facing what it fights. A corpse whose every tile within 2 that we reach lies in a cone waits: "not looting X yet: the
  corpse at (x, y) lies in the ocean bedrock's spray cone, every tile within 2 of it we reach too; …". test_sim
  `test_spray_windup_fewest_steps`, `test_spray_loot_wait`, `test_spray_hold_awake`. **An apparition's blink**
   (since 2026-10-17; user: "when an apparition vanishes we know its going to pop out on the player/pet it
   was aggro'd on ... right now we just wait there to get hit for like -47"): on its "*phases*" the pet it
   fights (its latest swing's defender, else the nearest pet within 2 of it) is where it lands 1.1-1.6 s
   later with the hit, so that pet's tile is in the zone for 2 s (`Team.blink_tiles`) and the stand-off goes at
   once, past its 2 s repeat gap and the tank's heal; one blinking onto Logan follows him, so nothing to step
   from. **A clearwater grouper's bubble** within 1 of us (since 2026-10-28; "Fire and bubbles on the ground"
   below): out of its reach before anything else in the stand-off, every tick (`Team.out_of_bubbles`). The stand-off
   comes first
   in each decision, mid-channel too, after only a heal potion that is ready (it goes at once) (live
   20261015_171928 18:57:09-19.5: an apparition came onto Logan's tile, -59; he stood there through the
   potion and a vet channel and took -60 more; a step doesn't stop a channel healing us, it only shortens
   its reach to a pet to 1). It goes to the clear tile within 6 steps that the fewest idle monsters would
   see before a pet, then the one farthest from the nearest of them, then the fewest steps, then the lowest (x, y, z)
   (`Team.standoff_tile`; the walk order's ties cost the pets in the simulator, docs/SIM.md "Tidal Tomb: Logan in the
   bedrock's spray"), within `--vet-range` of the pets when such a tile is out of their sight, else beyond it; when
   every clear tile is seen by more of them, or nearer one, than here, it stays (live 19:49:40 the nearest clear
   tile was 10 from a second apparition in its sight, and it came onto Logan 5 s later; 20261015_204514 21:02:02 it
   went 3 tiles from an idle reanimated knight, which woke and hit him -46: the user, "Logan went and stood
   right in front of a reanimated knight while the pets were fighting another mob"; test_sim
   test_standoff_sight: 35e7f83 went from 8 to 6 tiles of it, now 8 to 13). When the tank in the zone needs the
   vet and the zone reaches farther than `--vet-range` (the daemon's 6 against 5), the stand-off steps in to the
   same vet tile ("to heal the tank: (x, y), N from <pet> ..."), never mid-channel; before, it stayed wherever it
   stood (beside the tank too). Healthy again, the tank's stand-off goes out of the zone (beyond the vet's reach
   when no clear tile is in it) and a hurt tank brings it back to the vet tile: live 09:53:44 out, 09:53:52 in,
   09:54:06 out.
   **A caster on us**: a caster in war mode within 10 while we take damage, nothing swinging at us, is
   an attacker (`HuntLoop.attackers`; live 19:49:58-50:22 a banshee 5 tiles off, -21..-28 every 3.3 s, never
   a swing); with no pet within `--vet-range` + 2 the runner goes back to the pets (`Team.rejoin`), and
   the reposition out of a war caster's sight runs mid-channel too, up to 4 steps.
   **Off the tank** (user 2026-10-15: "Since our healing distance is 5 now I don't see a good reason to be
   standing next to the tank while it's fighting"): with `--vet-range` 4+, in a fight (`aggro.View.engaged`: a
   monster in war mode on us or the pets, or within 8), a pet that stood next to a monster in war mode within the
   last 8 s (`FIGHT_LINGER_S`), or the monster, within 2 of us sends us up to 4 steps to a tile 3 from both
   and within `--vet-range` - 1 of every pet, the one the fewest idle monsters see (`Team.spacing`,
   before the vet supplies, never mid-channel). Not once the fight is over: until 2026-10-24 it went on for
   `FIGHT_LINGER_S` after the last monster, so after every kill Logan stepped off the guarding pets as they came back
   to him, every 1.5 s, before looting (user: "stepping off the tank repeatedly when there are no monsters around";
   simulator 170 such steps a game hour, now 92, all in fights; test_sim `test_no_spacing_after_fight`). The crawl
   doesn't walk on meanwhile after a target left the view alive, until it is back in view or `FIGHT_LINGER_S` is up
   (until 2026-10-24 always the full 8 s).
   - It steps in only to heal that pet: below `--standoff-vet-at` (0.7) instead of `--pet-heal-at`.
   - `--vet-range` 3+ (the Healer's Codex "Without Borders") heals from outside, so no step-in is
     needed.
   - The first Charge of a fight can't be dodged this way: it lands on the guarding pet, which
     follows 1 tile from us. Live, Logan took exactly that one (-42 at 21:21:15) and none after.
6. **Pets come along**: "All Follow Me" before every recall or walk out (user directive
   2026-10-11, knowledge #17043). A planned leave, or one for a pet while we aren't hurt, waits up
   to 10 s for every pet within 3 tiles; a leave because we are hurt falls back from the attackers
   (up to 10 steps / 4 s, until the pets are near) instead of standing, and a hostile player
   recalls at once (the simulator's trade-off: standing killed us, not waiting stranded pets,
   docs/SIM.md). Hurt beside the leave gate: "All Follow Me" and through at once, the pets with us or
   not (live 20261015_090607 a banshee killed Logan in the 7 s he stood there; user 2026-10-15: pets left
   in the dungeon are retrieved at the stablemaster, "Retrieve Lost Followers"). The waits read the
   state through the runner's guards, so a due potion is drunk. After a recall a missing pet is an
   urgent `pet_left_behind` juncture.
7. **Leaving for the pets**: a pet below `--pet-leave-at`, or out of view for 30 s, or dead (a bonded
   ghost) for `RES_TRY_S` (30 s) with vet supplies in the pack (at once without). With a pet dead or
   gone the run never goes back in (`pet_down` juncture).
   - **A dead pet is vetted back** (live 20261014_221341: GraveRobber died at 22:35:53.1 and the channel
     Logan had begun finished 2.0 s later with "You are able to resurrect your follower.", 31 % hits,
     no gump; knowledge #11547: 80 Veterinary). At its death: "All Follow Me" (the ghost follows), the
     vet supplies for a ghost within `--vet-range`, a walk to one beyond it, no crawl walk meanwhile;
     back alive: "All Guard Me".
8. **Kills**: the engaged target, and any mob the pets fought (in war mode next to a pet that was
   hurt, or engaged) that dies within 60 s; their corpses are looted like the mage's. A target that
   leaves the view alive (0x1D without the 0xAF of a death) is no kill: a shade wolf's stealth
   mid-fight; the pets are put back on guard, and its death within 30 s still counts.
   **What's looted** (user 2026-10-15, first "only loot gold, gems, instruments, and high-value loot like
   cores, maps, distills, etc. I wouldn't grab reagents or scrolls or heavy magic armor/weps"; then, after
   that whitelist left research materials, "quite valuable", in a corpse at 20:55:18: "it's probably better
   to just _not_ take the regs scrolls and armor/weapons"): with the pets style (`--no-loot-all`) gold
   first, then everything but reagents (graphics 0x0F78-0x0F91), spell scrolls (0x1F2D-0x1F72,
   0x2260-0x227C, or "scroll" in the name) and anything worn other than jewellery (a tiledata layer
   outside ring, talisman, necklace, bracelet, earrings: weapons, shields, spellbooks, armour, clothing)
   and Outlands' own weapon and armour art, which tiledata marks Wearable on layer 0 ("unidentified cestus" in the
   client; since 2026-10-17, the user: "I don't really loot unidentified items much since they're heavy. I just loot
   the instruments")
   (`combat.worth_looting`; `--loot-keep WORD` takes an item with that word anyway); the rest stays in
   the corpse. **Outlands' hued valuables on those graphics are taken** (user 2026-10-17, all three left live
   in 20261017_110506): an arcane scroll (a pink blank scroll, 0x0EF3 in hue 2609), a blood aspect core (0x0F91,
   the wyrm's heart graphic, in hue 2087), a skill scroll (0x227A "scroll of calling" in hue 2291): any hue but
   0 on a reagent, spell-scroll or blank-scroll graphic, or "arcane scroll" / "aspect core" in the name
   (`combat.worth_looting`, test_combat.py). **Weight**: an item that would bring us
   within 10 stones of the limit (`LOOT_WEIGHT_MARGIN`; tiledata weight, gold ~0.01 a coin
   [INFERENCE]) is left; at the limit looting stops until the weight drops. Live 20261015_181807 the
   take-everything loot reached 471/450 and Logan couldn't move. The weight limit is the world model's
   `self.stats.weight_max` (`loop_hunt.weight_max`): until 2026-10-28 the guard read a top-level `weight_max` that
   doesn't exist and never fired; live 2026-10-28 20:08-20:09 Logan looted four corpses on the leave's walk from
   447/450 to 462/450, and the server refused every step ("You are carrying too much weight and are unable to move").
   **Over the limit anyway, gold is dropped** (since 2026-10-28; user: "Have it drop gold when its overweight"): the
   Mover's walk checks before each step (and on a denied one) whether we are over (`Mover.overloaded`: the state's
   weight above `weight_max`, or the server's "You are carrying too much weight and are unable to move" since the last
   step) and calls the runner's `drop_gold` (`Mover.unload`): from the largest pile in the pack, enough coins to come to
   `weight_max - 1` at `combat.GOLD_WEIGHT_PER`, lifted and dropped on our own tile ("overweight (462/450): dropping
   1300 gold at our feet so we can move", then "dropped N gold: weight W/450"; `gold_dropped` in the totals and a
   `gold_dropped` job event), then the walk plans again; still over, it drops more. No gold: the steps go out and
   their denials count as before. test_mover `test_overweight_unload`, test_loop_hunt `overweight_drop`.
9. **Never a pet's release or transfer** (user 2026-10-12: the pets are expensive): the commands
   are a fixed list (`tamer.COMMANDS`: All Guard Me, All Follow Me, All Kill, All Stop, All Stay,
   All Come), Razor-shaped (`actions.razor_say`), and anything matching `release`/`transfer` is
   refused by the runner and by `ctl act say` / `ctl act menu_pick`.

**Aggro** (`aggro.py`; user 2026-10-13: "positioning and managing aggro is the number one most
important thing in dungeon PvM in Outlands"). Line of sight is `los.Sight` (ServUO's rules, checked
live), monster z from the map surface, aggro range per type as the captures measured it (`aggro.aggro_range`,
mausoleum.json `aggro_observed`: 10, the shadow minion and the calamity too since 2026-10-29; 210 of 211 wakes at 10
or less, 102 idle monsters in our sight at 11-13 for over 5 s; the user first "12 or 13", then "smaller than 14"). **Whoever it sees
first** (user 2026-10-15: "Any mob that 'sees' Logan before it sees a pet is going to aggro on him
directly and that's pretty much a death sentence with this build"): a tile is scored by the idle
monsters that would see us there before a pet (`View.first_on_us`: in range and sight, no pet nearer
them), so the reposition, the spacing off the tank and the retreat pick tiles out of their sight or with
the pets between; the pull spot steps back so the pets, a tile or two behind us, end up between us and
what comes.

**Corpse purgers, blightmares and the standing casters** (`aggro.shy`: since 2026-10-26 any mage or ranged AI in the wiki
data from difficulty 60 up, plus the shadowguard: on the Mausoleum the purger, the blightmare, the master and insane
reanimators, the banshee, the shadow minion, the calamity, the fleshweaver and the muddled vampire hunter; on Tidal
Tomb the drowned mariner and buccaneer, the soulcatcher and every zoa (user: they are "conceptually similar to the
ranger attackers and casters in Mausoleum 3"; docs/SIM.md "Level 2's shooters"). User 2026-10-26: "I keep the pets
between me and mobs so mobs
aggro on the pets ... I avoid being sighted by hostiles"; his fight of the reanimator's room with "All Patrol" from out
of sight against two deaths there where they saw Logan first, docs/NOTES.md "How the user plays the casters and
purgers"; user 2026-10-17: "We're frequently getting diseased by the corpse
purgers"; the blightmare since 2026-10-21, after two live deaths in its spells and Spellblight disease from 10-12 tiles
at its spawn (5243, 687), docs/NOTES.md "Dead in a blightmare's reach again"): they shoot from where they stand, and
about 2 s after one of us comes into their range and sight (never under 1.5 s live) they take the nearest of us in
their sight then, not the first seen (measured 2026-10-29, docs/NOTES.md "Shooters' and casters' first target"); their
barrage or spells reach us wherever the pets stand. None is held for (none comes on to the pets in time).
**The step-in** (since 2026-10-29; user: "'All Guard', walk up, then walk away from the mob slightly so the pets end
up between Logan and the mob"; `tamer.Team.step_in`, `step_back`, `step_in_watch`): one idle shy monster, nothing
fighting, the pets on guard and with us ('All Guard Me' first if due), and not one whose hit on a pet reaches 3 or more
round it (the zoas, the purger, the master reanimator: their patrol stays, `STEP_IN_AREA_MAX`). Before the step into
its sight the runner plans the walk up (up to 4 steps) and the step back; the steps and turns after the onset must fit
in 1.5 s less a 0.75 s margin at 0.2 s each (`WAKE_FLOOR_S`, `STEP_IN_MARGIN_S`), and the tile back has a pet a tile
inside its range and sight (the pets followed step by step) with Logan out of its sight or a clear tile (Euclidean,
`STEP_IN_GAP`) farther than that pet; not into an area zone, fire, or another idle monster's sight. The steps go out
without a route planned between ("step-in on the soulcatcher 0x… at (x, y): up 2 steps to (x, y), back to (x, y) in 3
steps, out of its sight, Bussin in it" / "…, AlaFlambe nearer by 1.4"); the runner then holds there until its first
act lands ("step-in: the soulcatcher 0x… took AlaFlambe 6.0 s after the step-in"; or on us, gone, 10 s with no act),
out of an area hit's zone first, and fights as before. Two step-ins at one monster with no kill, then not again. When the
walk has already brought us into its first sight, the same plan from there (`step_back`). No such tile ("no step-in at
the … : no step-back tile within 3 steps"): the back-off below. Simulator (docs/SIM.md "The shy monsters' wake, and
the step-in"): level 2 runs with a death 21 -> 19, pet deaths 326 -> 264, Maus 3 damage on Logan -10 %.
An advance (patrol, approach) stops on a step into an idle one's sight before the
pets ("a corpse purger would see us", "a blightmare would see us"; the crawl then patrols elsewhere), and the runner
steps back (up to 3, out of its sight first, then, with `--patrol-pull`, to a tile from which a patrol spot is in
reach, else farther from it, the pets within vet range; `Team.back_off`: "back to … out of its sight, in reach to send
the pets at it"). Until 2026-10-25 a back-off from a purger seeing us at 11 went to 14, where no spot 1-2 from it was
within 11 of us, so no patrol went out and the crawl walked away (live 15:57:40; 30 of 38 such back-offs 10-22..25 had
none); spots 3 off (the demo's) now reach from 14 (docs/NOTES.md "Backed off from a purger, never patrolled at"). One
backed off from is kept off for 60 s (`Team.kept_off`, `KEEP_OFF_S`): its sight is a crawl danger zone (no waypoint
near it, routes bend round, not sought; live 2026-10-18 12:05:09-25 the patrol and an approach walked back into a
purger four times, then its barrage). **A leg that must arrive** (`walk_in_fought`: the walk in with `--fight-via`,
the walk to the moongate after `--leave-via`, the oxygen legs) backs off from one idle shy monster 3 times
(`tamer.BACK_OFF_REPEAT`) with no kill between, then goes on past it with the pets on guard ("…: backed off from the
soulcatcher 0x… 3 times with no kill; going on past it with the pets on guard"; `Team.faced`, until a kill or the
leg's end): live 2026-10-29 18:13:23-18:14:28 the oxygen leg to level 2's stairs and the back-off undid each other 18
times, the soulcatcher between them and its 'All Patrol' refused (docs/NOTES.md "Tidal Tomb: the oxygen leg's back-off
loop"). Not on the hunt level, where the crawl repeats them too: there the simulator had more deaths with it.
**Awake**, its reach is a crawl danger zone while it stays in war mode (12 for
the blightmare, `tamer.CASTER_R`; 13 for the purger, `RANGED_R`): no waypoint within 2 of it, routes bend round, a
patrol toward a waypoint there picks another ("crawl: keeping out of the reach of the awake blightmare at …", "… is
near a creature we avoid now; choosing another"; live 2026-10-21 18:21:09 the patrol went on toward a blightmare that
had cast at Logan from 12 two seconds before, 12 -> 10 tiles, dead). The same for an **area hitter passed over**
(`tamer.AREA_R`: a reanimated daemon 6, an apparition 3) while it is awake and skipped: its reach is a crawl danger zone
("crawl: keeping out of the reach of the passed-over reanimated daemon at …"; live 2026-10-23 23:29:34-23:30:19 the
patrol went on to a waypoint 3 tiles from a dropped daemon, its bone burst came and the stand-off took Logan back out,
six rounds; test_sim `test_patrol_off_passed_over_area_hitter`). An approach stopped by such a monster ("would
see us": a shy one before the pets, a never-aggro one) passes its target over for 60 s (`Team.approach`; the
simulator's runner backed off, approached the same way and held again within 1-3 s, 7 times an hour); one that comes
on is fought. Never an approach to an idle shy monster, no bard skill on one the pets haven't hurt and that isn't on
us, nor a provocation of one (our skill draws its aggro; once the pets fight it the skills go, as the user's
Discordance in the demo). A walk to a hurt pet stops on a step into an idle shy monster's sight before the pets.
Ranged monsters in war mode count as casters for the reposition (out of their sight within 13, even mid-channel); from
a shy one a step out of its reach counts too (13 tiles from a blightmare), though it still sees us there.

**An awake one on someone else is walked past** (since 2026-10-24; user: "Logan keeps getting stuck in a loop 'Out of
the <mob>'s shot' when a purger or blightmare is fighting someone else", then "We can just walk past when one is
fighting someone else"; live 19:01:21-19:02:07 nine rounds at a purger on Gix's pets in the corridor, 19:02:36-55 five
at a blightmare). One in war mode with another player or his pets beside it (`Team.elsewhere`), and neither on us nor
on a pet of ours (`attackers`, `pet_attackers`), isn't a shot to keep out of (`Team.shy_threats`): no cover from it,
no stop at its shot's edge, the patrol goes on past (its reach still bends routes: a soft crawl danger zone). In
reach and in sight it gets "All Kill" as any monster on someone else does. Every other awake one is fought by the
pets (on us: "All Kill" at once, below; on a pet: the guard; an idle one: "All Patrol" at it, `Team.patrol_pull`);
these rules only keep Logan's own tile and walks out of its shot while they do:
- **The patrol picks only waypoints it reaches without stepping into its shot** (`Crawl.shot_tiles`, the floor BFS
  with them blocked; seek's monster in view too). None left but through it: "crawl: every way on runs through the
  awake corpse purger's shot; waiting out of it", until the pets kill it, it leaves or calms.
- **An advance stops on a step into its shot** (`Team.pull_check`: "an awake corpse purger would see us"): the patrol
  rests that waypoint for 60 s (`crawl.AWAKE_SHY_REST_S`) and goes elsewhere; an approach to another target passes
  that target over. Simulator check 2026-10-24: an awake purger on a pet is killed in 14.5 s, the same as before
  these rules.

**A pet beside a monster on someone else isn't fighting** (`HuntLoop.pets_busy`): guarding pets keep war mode on, so war
mode beside it counts only when no other player or pet of theirs is at it (`Team.elsewhere`) or a pet of ours swings
at it; live "GraveRobber is fighting" stopped every patrol within 2 of Gix's fight. test_sim
`test_walk_past_purger_on_another`.

**Out of a shy monster's shot first** (`Team.cover`, since 2026-10-22; user 2026-10-21: "All you really have to do for
disease casters like corpse purgers and blightmares is stay out of their aggro range and patrol the pets into them. If
the pets are busy fighting something else just stay out of their aggro range or keep the pets between us and the
mob"). An awake purger or blightmare that sees us within its reach (12 / 13), not one on someone else (above): walk (up to 8 steps, at most every
2 s, mid-channel too) to the nearest tile out of every such shot, out of area hits and fire, the fewest idle eyes
first: "cover: the corpse purger shoots us here; to (x, y), N steps, out of its shot". In care it comes right after
the retreat, before the vet supplies on anyone, the spacing and the rest; the spacing off the tank and the vet tile
keep out of such shots too. The reposition's caster rule (4 steps, the pets kept in vet range, no more idle eyes)
found no tile at any of its tries in the purger room (simulator seed 1003: an awake purger 8 off took Logan 100 -> 13
in 13 s while he stepped off the tank and vetted). One on us gets "All Kill" at once (not after `KILL_AFTER_S`: its
aggro is on us already, and 4 s of its shot and stacking disease is 40-60 hits), and there is no walk back to
the pets through its shot (`Team.rejoin`; seed 2072 walked 12 steps under one, 100 -> 33). The crawl never seeks
one in view (`Crawl.seek`): walking up to it puts us in its shot before the pets. **No walk to a hurt pet into one's
shot** (`Team.to_pet`, since 2026-10-22): a pet above `--pet-leave-at` whose every vet tile is in an awake shy
monster's shot isn't walked to ("to a hurt pet: GraveRobber (70%) is only in reach from the corpse purger's shot;
staying out of it"); it is vetted once the shooter is dead or the pet comes out. Below `--pet-leave-at`, or a
ghost, the walk goes on as before. Live 19:39:36-53 four such walks went in, and cover walked him out each time
(docs/NOTES.md "Dead on the walk in to a red", the follow-up).
**A monster beside us that the pets aren't fighting is the target** (`HuntLoop.pick_target`, `loop_hunt.LOOSE_R` 1, since
2026-10-29): a monster within 1 attacking us that no pet has hurt lately, while the current target isn't on us, takes
the target over, logged "a razorfin 0x... on us and the pets on 0x...: it is the target now". `Team.fight` then says
"All Kill" at it at once when the pets fight another monster (`KILL_AFTER_BUSY_S` 0, not `KILL_AFTER_S` 4: guarding,
they don't turn from their fight to it). Before, `pick_target` kept a target that fought the pets even with another
monster on us (its docstring said otherwise): in the simulator 7 of level 2's 24 deaths (144 runs) were a fin alone on
Logan, the pets at full hits 5-10 off on a zoa. Ablation with the target areas above.
**No walk to a hurt pet from out of an awake caster's sight into its reach** (`Team.to_pet`, since 2026-10-25): with
an awake caster or ranged monster (not a corpse purger or blightmare: their shot rule above; the casters made shy on
2026-10-26 keep this rule, test_sim `test_to_pet_not_into_caster_reach`) that doesn't see us here (`CASTER_R` 12,
`RANGED_R` 13), a live pet at `tamer.TO_PET_CASTER_AT` (80 %; 50 % until 2026-10-25 21:49, when GraveRobber went
unvetted at 56-64 % for 20 s beside a reanimated daemon, user: "didn't use vet supplies on GR for quite a while") or more
isn't walked to when the vet tile (or the pet's tile) is within that reach of it by distance, sight or not, and nearer
it than here: "to a hurt pet: GirthyEarthy (92%) is only in reach 4 from the awake master reanimator, out of its
sight 12 off here (80% or more); staying out of it". Below 80 %, or a ghost, the walk goes on; so does one with us in
its sight already. Live 2026-10-25 00:38:27-45
the reposition had taken Logan out of a master reanimator's sight 12 off, and the walk took him to 4 of it for the
tank at 92 %; its spells and a shadowguard's landed on all of us at once, Logan behind a wall by our map, 100 -> 0 in
6 s (docs/NOTES.md "Dead vetting a pet in two casters' reach").

**"All Patrol" at a purger or a blightmare** (`--patrol-pull`, on by default since 2026-10-22; `Team.patrol_pull`,
user 2026-10-18 "We can send our pets to a spot away from us, e.g. to within aggro distance of a purger, while we're
not in aggro range"; the demo in docs/NOTES.md "All Patrol"; since 2026-10-21 at an idle blightmare too, as a shy
monster: untried live). Nothing fighting, nothing on us, both pets within vet range, and the purger (within 14)
not seeing us before the pets: the runner says "All Patrol" (his Razor macro's bytes, hue 0x0239) and clicks a tile
1-3 from it (3 since 2026-10-25, the demo's spot), within 11 of us and in our sight (los.Sight, as the server's 500237
check), with no other monster within 4 of it nor seeing where we'd stand to vet the pets there. It stands; once the
purger is awake with a pet on it,
"All Guard Me" (they fight on: live), at once if something comes onto us, and after 10 s without it (the purger then
passed over for 60 s). A pass-over of the fight (`HuntLoop.skip`: "it can't see us", pinned, blocked on the way)
doesn't stop it; only its own refusal or timeout does (`patrol_failed`; live 2026-10-25 15:52:21-29 the "can't see
us" pass-over kept a purger 4 tiles off from ever being patrolled at). A purger or blightmare within 14 not patrolled
at logs why, on a new reason and every 30 s: "no 'All Patrol' at the corpse purger 0x… N tiles off: <why>" (something
is fighting / on us, it would see us before the pets, a pet out of view or beyond `--vet-range`, refused or timed out
lately, no spot within 11 of us and 3 of it, no spot beside it in our sight clear of other monsters). Off by default
from 2026-10-18 to 10-22: then the simulator's loop crawl (144 runs) lost 71
runs to a death against 54 keeping off purgers (the second purger of the pair took Logan as the fight drew him in).
With the cover above it pays: deaths an hour 1.05 -> 0.41 and gold an hour +4 % in 200 runs as live (docs/NOTES.md
"Half the deaths in the simulator"). The §5 command doesn't name it, so live runs went without it until the default
changed (user 2026-10-22: "the overseer did not use the --patrol-flag").

| Argument | Default | Meaning |
|---|---|---|
| `--pull-budget` | `3.0` | While the pets fight monsters worth E, an advance (patrol, approach) stops where more would wake (W, idle monsters that would see us within 15 tiles) when E + W is above it. With nothing fighting, a step that shows us to two or more idle monsters worth more than it together stops the patrol and rests the waypoints within 15 tiles (`crawl`: "patrolling elsewhere"); walking out of such a spot goes on. The pull spot puts us where they don't all wake together. A patrol walk refused again before its first step for the same reason (the first, "would wake 0.4 more onto 3.0 (budget 3)", logged once) holds 0.4 s a time (`Crawl.stop_last`, since 2026-10-29: live 12:35:39-42 and 12:36:42-43 the tick walked again at once, 329 and 100 "stopped" lines). 0 = off. |
| `--never-aggro TYPE` | the elder vampire, the elder vampire countess | Never wake it alone (repeat for other types; any given replaces the default). The vampiric consort until 2026-10-15; the user then: "we don't need to avoid any mob specifically, we should be able to handle everything on Maus 3 with enough care. The consort and elder vampires areas can get a little sketchy but it's all doable" (the code still avoided the consort by default until the next change); after the eleventh live run (an elder vampire countess froze both pets and killed Logan): "I suppose the runner can avoid the elder vampires for now". One that attacks us or the pets is fought like any other (run from until 2026-10-24). |
| `--avoid-backlash` / `--no-avoid-backlash` | on | When every attack pet of the team casts (`tamer.casters_only` from `harness/data/sim/tamer/<character>.json`: the phoenix team, the nightmares, the imp and fire minion; not the mantis team), the giant astral zoa (`aggro.BACKLASH_TYPES`) joins the never-aggro set: never pulled, its sight no-go, fought only if it attacks. Its spell stacks Backlash, a 0.3 chance a stack to fizzle each spellcast for 30 s; under it AlaFlambe lost 54 of 140 casts, and the giant's fight took 111 s against 47 (user 2026-10-27: "Maybe we should just avoid it when our pet team relies on caster(s)"; docs/NOTES.md "Backlash"). |
| `--crawl-avoid-types` / `--no-crawl-avoid-types` | on (`--pets` off) | The crawl avoids creature types the store's fights say cost too much or were fled from (live 20261015_171928 it set the vampire countess aside after one leave). |
| `--reposition` / `--no-reposition` | on | In a fight, step to a better tile for line of sight. A caster in war mode within 12 (`tamer.CASTER_R`; a blightmare cast from 12 live) or a ranged monster within 13 with sight of us: up to 4 steps to a tile fewer of them see, that no more idle monsters see first, and that keeps the pets in vet range; out of a caster's sight is worth the steps by itself (before 2026-10-19 one caster's sight never beat a step's cost: live 19:54:39-57 a blightmare 12 off cast at Logan 18 s, 100 -> 4, dead). From a shy one (a purger, a blightmare: they don't come on) out of its reach counts as out of its sight (before 2026-10-21 a tile it still saw counted as an eye more: live 18:20:51-18:21:07 a blightmare 12 off cast at Logan 16 s while the step to 13 was refused; for the casters that come on the sim lost 43 runs to a death in 144 against 33, so not for them); logged "reposition: blightmare 0x… 12 tiles off can cast at us here; to (x, y) (1 -> 0 in reach)". Mid vet channel only while the channel heals us (our share of hits no higher than the pets': a step cuts its reach to the pets to 1). |
| `--hold-first-sight` / `--no-hold-first-sight` | on | Nothing fighting: stop where an idle monster sees us within its aggro range (its type's, 10 or 13; it was 14, the user's 13 plus a tile: live 21:34:05 a shadowguard 14 tiles off held the patrol 10 s and never woke), and let it come; the pull spot must stay in its sight. |

- **Weights** ("weighted by danger"): (wiki difficulty - 50) / 40, clamped to 0.25-3: a vampire bat
  0.43, a shadow minion 0.53, a shade wolf 0.92, a banshee 1.6, a nightstalker 1.8, a shadowguard 2.05,
  the countess 2.4.
- **Hold at first sight** (Logan at the nightstalker room's north edge: the wolves and the stalker came
  to him 5-10 s after first sight, the knight only when he moved on): with nothing fighting, the patrol,
  an approach and the idle tick stop as soon as an idle monster within 10 tiles would see us, for up to `HOLD_S` 5 s
  per monster (10 s until 2026-10-24: wakes take 1.4-2.6 s, median 2.2, n=188, and 11 of 13 holds in the 16:12 run
  woke within 3.5 s); one that hasn't come by then is passed over. For the hold, a Wall-flagged static blocks its sight too:
  the Mausoleum's 0x00D1/0x00D9/0x00DA wall pieces carry Wall alone, no NoShoot (user 2026-10-17: "Logan is
  waiting for mobs that can't see him through walls to 'come on'"; NOTES "16:12 run's holds"). Targeting, the
  pull budget and the stand-off keep the server's rule.
- **No patrol without the pets** (user 2026-10-17, after Logan set out on a 101-step patrol at 17:11:55 while the
  pets fought a tormented soul: "he should check that his pets are not currently fighting something before
  starting a patrol, and check during a patrol that his pets are still near him"). `HuntLoop.patrol_hold`: a pet
  fighting (a monster on a pet, or a pet in war mode within 2 of a monster in war mode) or a pet more than 8 tiles
  off or out of view. Before a patrol leg the crawl waits while they fight, or walks back to a pet left behind
  (`Team.rejoin`). During a leg the walk stops on either. Simulator, 48 seeds: deaths 32 to 29, pet deaths 35 to
  30, damage on Logan 40957 to 35624. Holds: 31 for a pet fighting, 18 for a pet 9-12 tiles off.
  **Pets standing locked off a fight** (since 2026-10-24, docs/NOTES.md "Pets locked on a fight they can't reach"):
  "All Guard Me" doesn't call pets off a monster they're on, and one on another player's side they can't reach isn't a
  fight of ours to `pets_busy`. So `Team.rejoin` checks the pet it walks back to. If that pet is in view within
  `tamer.COMMAND_R` (12), stands on the tile it stood on at the last walk back (within 30 s, `STUCK_REJOIN_S`), and
  `pets_busy` is None, it says "All Follow Me" instead of walking ("AlaFlambe at (5243, 692) (11 tiles) stands where
  it stood at our walk back 8 s ago, no fight of ours there: 'All Follow Me' instead of another walk back"). No walk back
  for 10 s (`STUCK_FOLLOW_S`), so the crawl waits for them. "All Guard Me" comes once they're within 3, after the 10 s,
  or at once if something attacks us ("after 'All Follow Me': the pets with us; 'All Guard Me' due"). Live
  21:40:56-21:41:30 the patrol, the reposition out of the daemon's reach and the walk back went round seven times in 45 s.
  Simulator (120 runs): repeat walks back 618 -> 175, no deaths added.
  **The pull spot**: before holding, once per group, the runner steps (up to 3) to the tile where the
  fewest of them would see us before a pet, then the fewest by weight within their range, but at least one
  near enough to see the pets beside us; not in a Charge's reach or a never-aggro monster's sight; they
  come to the pets from there in stages instead of being walked into.
- **Never-aggro monsters**: their sight within 16 tiles is no-go: the patrol stops on a step into it or
  nearer one already seeing us (a step out of it goes on: `Team.pull_check`, live 21:12-21:16 the patrol
  stopped before every first step, 3225 times, standing in an elder vampire's sight), and the crawl then rests
  that waypoint; the crawl skips waypoints within 16 of one and routes keep out (`crawl.Crawl.observe`); the
  runner never pulls one. One that comes for us in war mode is fought like any other monster on us: the pets
  on guard, the target once it attacks us or a pet (`HuntLoop.pick_target`), the leave rules when we're hurt.
  Until 2026-10-24 it was run from (`Team.evade`: "All Follow Me", to the gate when that was away from it, else
  22 tiles beyond it, until it stopped chasing; "I run until the consort leashes and I'm in an area that I can
  manage"); the user then asked to remove the running (docs/NOTES.md "Elder vampires: no more running").
  - **Healing on the run** (user 2026-10-17: "We absolutely must vet while fleeing"). Between the steps of
    every flight (a red's, a walk out to the gate), the vet supplies start whenever we're hurt
    at all and no channel runs. A heal potion is drunk below `--heal-at` (`HuntLoop.drink_on_the_run`). A
    step doesn't stop the channel healing us. Live 16:38:32-41 a flight from an elder vampire countess had
    none of it: diseased, 87 to 11 hits in 9 s.
- **Backspawn** (user 2026-10-15: "We know nightstalkers spawn in that room though. When a mob respawns
  behind you we call it backspawn. A human knows not to stand out in the open when stealth mobs are going to
  respawn there"; live 20261015_171928 18:40:10 an unseen nightstalker hit Logan -58 from 2 tiles, the side
  away from the pets, and killed him in 2.7 s): the spawn spots of stealthers (the wiki's stealth: the
  nightstalker, the shade wolf, the vampiric consort) come from the store's kills of them, the Mausoleum's
  observed first sightings (harness/data/sim/mausoleum.json spawns) and where one is first seen idle this
  run (`Team.backspawn`); since 2026-10-26 a corpse purger's and a blightmare's too, from the first sightings only,
  within their reach of 12 (`aggro.respawn_r`; user: "I move to spots where if a mob respawns (like the dreaded
  blightmare) I won't be standing in its sight"; the other shy casters' spots too cost the simulator gold and deaths). A
  tile within 7 of a stealther's spot (12 of a purger's or blightmare's), in its sight, with no pet nearer the spot
  than the tile scores as that monster idle there (`aggro.View.backspawn_exposed`, in `stand_score` and the pull spot):
  the spacing, reposition and retreat tiles keep the pets between Logan and the spawns. Repositioning
  keeps spacing's 3 tiles off the tank.
- **Line of sight in a fight** ("situational": between two rooms, stay out of sight of the room the
  pets aren't fighting in; "if it's easy to stand out of LoS of a caster like the banshee while still
  being able to heal our pets ... it's better"): every 3 s, a tile within 2 steps that keeps the pets
  near us within `--vet-range`, out of a Charge's reach, with a better score (idle monsters that would
  see us x 10 by weight, a never-aggro one x 100, casters in war mode with sight of us x 1; 0.3 a step);
  never mid-channel.

**In and out of a dungeon** (user 2026-10-13: you can't recall in a dungeon except near a gold
moongate, and never near a red one).

- **In:** `--enter-via X Y` (repeat) steps onto those teleporters in order after the recall in. The tile
  is the teleporter itself, the one whose step the server refuses before it moves you, not the tile
  you stand on when it does (live 2026-10-15 three of the four were one tile off; NOTES "Getting in
  and out of the Mausoleum").
  - The approach is the tile the route onto each one arrives from (a teleporter on stairs is entered
    from one side only).
  - **Through by another step** (`walk_in_through`, since 2026-10-18): within 3 tiles of a teleporter's known
    arrival (the store's teleporters), it and those before it count as passed. Live 21:05:41-43 the step off the
    tank under attack went onto (5522, 636), a teleporter beside the known (5522, 637), and out to Maus 3; the walk
    on to it aborted "no route from (5199, 736)". The tiles the fight steps choose from (`Team.tiles_within`:
    spacing, reposition, the pull spot, the back-off) leave out known teleporter tiles and the tiles beside them.
  - **Started inside** (`resume_inside`, since 2026-10-18): with `--enter-recall` where no recall is allowed
    (escape.recall_forbidden), the hunt is `--spot` or the last `--enter-via`'s known arrival; on its level (the
    crawl's floor from here holds it) the run hunts from where it stands, else it walks the rest in from the first
    `--enter-via` teleporter it can reach. Live 21:29:58 a restart 48 tiles from `--spot` tried to recall.
  - **The pets follow the whole way** (since 2026-10-15, as the user does it, capture 20261013_204518: one
    "All Follow Me" after the landing for all four teleporters, the first "All Guard Me" on the fight
    level; live 22:20:56 the runner's "All Guard Me" at the landing set the pets fighting there and Logan
    walked 75 steps to the first teleporter alone). A pet farther than 8 tiles or out of view stops the
    walk: 5 s for it to catch up, then "All Follow Me" again and 20 s more, else Abort. **A pet in view farther than
    `COMMAND_R` (12)** doesn't hear that command (since 2026-10-24; [INFERENCE] from the captures: a pet 11-13 off came
    on 9 of 15, one 15-17 off on 0 of 7), so first the walk to within 8 of it ("<label>: GraveRobber at 17, beyond a
    command's reach (12); going within 8 of it", `fetch_pet`), stopped by an attacker on us. Live 2026-10-24
    18:26:46-18:27:50 "All Guard Me" on the first level sent GraveRobber 17 off after a reanimator apprentice and a
    corpse eater; three "All Follow Me" didn't reach him, the walk aborted and the escape left him there. test_sim
    `test_walk_in_fetch_pet`.
  - **Attacked on the way** (something swinging at us, or our hits falling: a calamity casts from 13
    tiles): "All Guard Me" and the fight tick's order (stand-off, a potion, the pets' care, the pets at
    what is on us, our kills looted) until nothing attacks us for 4 s (at most 90 s), then "All Follow Me"
    and walk on. Live 22:33:39, with only the pets' care, Logan stood among the mobs dying.
    **While waiting for the pets too** (`_wait_together`, since 2026-10-26; the walk in's waits and the walk out to
    the gate's): a hit or an attacker ends the wait for that fight, then the wait starts over; still attacked when the
    fight's 90 s run out, the walk goes on its own way (the walk in: no pets, Abort; the walk out: on to the gate).
    Simulator 2026-10-26 (tidal_tomb seed 3, the leave at time up): "to the moongate: waiting for the pets" with
    both 9 tiles off, an oozing noughtilus took Logan from 100 to dead in 18 s with no potion. test_sim
    `test_wait_pets_attacked`.
  - **Attacked near the teleporter** (`walk_in_dash`, since 2026-10-20; live 22:57:15-31 Logan stood fighting 13
    steps from 5522,637 until the user walked him through to Maus 3): attacked within `TELE_DASH_R` 15 route steps
    of the teleporter walked to, no stand: "<label>: attacked N steps from it; through it now", "All Follow Me"
    without waiting for the pets, on to the tile before it at flight pace with no stop for the attackers (a ready
    heal potion below `--heal-at` on the way), then the step through with no wait for the pets beside it. Farther
    out it still stands. The leave gate's `GATE_DASH_R` below is the same rule.
  - Before each step onto a teleporter (not after a dash), every pet within 3 tiles of it, else the waits above.
    A caution: on "All Follow Me" pets come through with us while in view (user 2026-10-20, Outlands), not only
    from within 3 tiles. `--songs`: a song due before each leg.
  - **A hostile player on the way** (since 2026-10-22; live 18:53:48 the red Botan, in view from the first level
    on, killed Logan on the second while the pets guarded and he played Area Peace: docs/NOTES.md "Dead on the walk
    in to a red"). The threat checks of the hunt level run on the walk in too: a red in view or "… is attacking
    you!" ends it at once ("walk in: <why>"). The user's rule:
    - **A gold moongate within `GOLD_GATE_STEPS` 120 route steps** (the first level's three): "walk in: gold
      moongates from (x, y): …", "LEAVING the walk in by the gold moongate at (x, y): <why>". It is the gate's flight
      (`flee_out`: "All Follow Me" with no wait, away from them first when the way passes them, ropes, potions and
      the vet on the run, no bard skill), and it switches to another gold gate when the way to this one passes them
      ("to the moongate: the way to (x, y) passes them; to the one at (x, y) instead"). Then the recall home, a
      `threat` juncture, the drop-off, `--pk-wait`, and back in.
    - **None** (past the first teleporter): "walk in: no gold moongate within 120 steps; on through the teleporters
      at flight pace, then the leave gate". The rest of the teleporters go at flight pace with no stop for
      attackers and no wait for the pets. On Maus 3 the visit's first act is the red's leave by the crimson gate
      ("LEAVING by the moongate: <why>, on the walk in").
- **Out:** `--leave-gate X Y --gate-dest NAME` (`moongate.py`).
  - The runner says "All Follow Me" and walks to the gate (user 2026-10-17, after a time-up walk out on
    "All Guard Me" left the pets fighting behind: "all follow me to run out, not all guard"), and says it
    again beside the gate. A planned leave (time up, `--kills`, the return rule) walks like the walk-in: attacked,
    the pets guard until it's over; a pet more than 8 tiles off or out of view, a wait for it (once it doesn't
    come in 25 s the walk goes on and waits beside the gate). Then it double-clicks the gate from that tile, as Logan did (the step onto it only when the gate
    item isn't in view), and picks the destination by its label.
  - **The gate a level up: `--leave-via X Y`** (repeat, in order; since 2026-10-25). Tidal Tomb's level 3 has no
    gate known; the user leaves it "by going back to the crimson gate on lvl 2" (live 11:35:19 a step S onto
    (7161, 2221), 11:36:55 the gate at (6897, 1992)). While the walk rules find no route to the gate (the planner never
    routes over a teleporter), the leave first steps onto the `--leave-via` teleporter still ahead that they reach
    (`HuntLoop.leave_up`): a planned leave as the walk in goes (`walk_in_legs`, no song), a hurt leave or a hostile
    player's at flight pace (`walk_in_rush`). Then the gate as below. Once through one, a hurt leave isn't called off
    for being healed: before that, the simulator's hurt leaves healed on level 2 and hunted there for the rest of the
    run. Level 3: `--enter-via 6609 2060 --enter-via 6884 1929 --fight-via 6884 1929 --leave-gate 6897 1992
    --leave-via 7161 2221 --gate-dest Totem`.
  - **A leg fought through: `--fight-via X Y`** (repeat; each one also an `--enter-via`; since 2026-10-26, user: "in
    TT we'll always have to fight in 2 in order to get to 3"). The walk-in leg to that teleporter is fought, not
    walked (`HuntLoop.walk_in_fought`): the pets on 'All Guard Me' and each round the tick's fighting
    (`combat_step`: what's in reach pulled and killed, the back-off from a shy one's first sight, 'All Patrol' at
    one, the hold for what sees us). The pets are healed up and gathered before each advance (the crawl patrol's
    wait, or `Team.rejoin`), and the walk toward the tile before the teleporter stops on the patrol's stops
    (`fought_stop`). After `WALK_FOUGHT_S` (20 min) without getting there the leg aborts. Then 'All Follow Me' and
    through. Simulator: level 3 runs with a death 15 of 16 -> 9 of 16 (docs/SIM.md "Level 2's shooters").
    **The planned leave fights the same way** (since 2026-10-26): up through `--leave-via` onto a level the walk in
    fights through (any `--fight-via` given), the walk to the leave gate is a fought leg to the tile before it
    (`leave_by_gate`); a hurt leave or a hostile player's still runs. The run's hard overall timeout (`--timeout` +
    180 s) doesn't count the time spent in fought legs (`fought_s`).
  - **A hurt leave is called off** once Logan is back at `--heal-at` on the way out and no leave rule
    fires any more (`HuntLoop.leave_called_off`; user 2026-10-15, live 20261015_171928 18:57:20-40: out
    at 8/100, 98 by the gate, then 10 s there waiting for the pets in a banshee's sight: "There's no point
    in running to the gate when he's fully healed"). "All Guard Me" at once (the pets were on "All Follow Me"
    for the leave; live 2026-10-21 09:49:34 none until the countess' kill 19 s later), then he walks back to
    within `--vet-range` - 1 of the nearest pet (`Team.rejoin`) and the hunt goes on.
  - **Through the gate on the way** (`HuntLoop.through_gate`): a jump from within 2 tiles of the gate to more
    than 30 tiles from it between two reads went through it and counts as the gate taken. Live 16:38:56 a hurt
    walk stepped onto the gate's tile and came out at Totem; its stop then read "healed" there, called the leave
    off, and the run hunted at Totem with the pets left in the Mausoleum. Until 2026-10-17 18:28 the distance
    alone counted, so a leave fired more than 30 tiles from the gate (most of zone 3) was "through" before a
    step and recalled inside the dungeon (live 18:28:22, the hits leave at 9/100: four recalls, then aborted).
  - **No recall inside the dungeon** (user 2026-10-17: "trying to recall out of the dungeon ... which is not
    allowed"): `escape.recall` refuses (RecallError, before the book opens) anywhere in a known dungeon box
    more than its `recall_r` from a gold moongate (`escape.recall_forbidden`, the Mausoleum's `dungeon` block
    in harness/data/sim/mausoleum.json, the simulator's rule too). Every recall goes through it: the hunt and
    lumber runners and `ctl recall`. The leave then fails as "unusable" and the run stops where it is.
  - **Attacked near the gate** (live 19:04, dead 2 tiles from it while the pets guarded): a planned walk out that is
    attacked within `GATE_DASH_R` 8 tiles of the gate doesn't stand and fight (`walk_in_fight`) but goes through at
    once: "All Follow Me" without waiting for the pets, the gate at its macro's pace. Farther out it still stands.
  - **Something coming on a planned walk out** (since 2026-10-28; live 22:53 Logan died on Tidal Tomb level 1, docs/NOTES.md
    "Tidal Tomb: dead on level 1 on the way out"): the walk also stops for a monster in war mode within `COMING_R` 10
    that sees us, fights no one else and comes on (not a shy shooter) while it keeps coming nearer (`COMING_S` 5 s;
    `HuntLoop.coming`): "to the moongate: lowtide scavenger in war mode 9 tiles off coming on the way out; the pets
    guard". Then the stand as when attacked (`walk_in_fight`: 'All Guard Me', the fight tick's order), which on the
    way out also goes on while one still comes; within `GATE_DASH_R` of the gate, through it instead ("... coming 5
    tiles from it; through it now"). Live the route's first 3 steps went up a scavenger's way with the pets on follow
    behind Logan; in the simulator (test_sim `test_tidal_leave_coming`) the walk now stops at 9-10 tiles and the pets
    take it with Logan 8+ off (before: 1).
  - **The walk's guard stays out of the stand** (`walk_fighting`): `drink_on_the_run` (potion, vet supplies, area
    peace between steps) doesn't run inside `walk_in_fight`, whose own order puts the stand-off and the retreat before
    the vet supplies on a pet (live 22:53:17.6-21.1 the guard drank and started the vet supplies on Bussin at 89 % with
    Logan at 29, then 6; the retreat came after his death). A planned walk out doesn't play the area peace "walking
    out past ..." (`walk_out_fought`): it stops for what attacks it anyway (live 22:53:08.8-14.6 the play cost 6 s
    beside the scavenger, -11, -33, -12, the pets on follow); a hurt leave or a flight still does. A guard that acted
    makes `HuntLoop.state()` and the Mover read the state again (live 22:53:22 "retreating ... at 31/100 hits" was read
    before the guard's potion and vet supplies, at 17.6).
  - **No walk pauses in a fight or on the way out** (`HuntLoop.no_idle` → `Mover.no_idle`): no humanize look-around
    or sidestep while walking out or with a monster in war mode in view (the same 19:04 death began with an 8.5 s
    pause 2 tiles from the gate).
  - **Hotkey pace**: pet commands, bard skills and their targets, songs, potions, vet supplies, war mode, the rope
    and the gate wait humanize `hotkey` 0.25 s / `hotkey_aim` 0.3 s / `gate_macro`, as Logan plays them off Razor
    hotkeys (ANTICHEAT.md §8.14).
  - **Fleeing a hostile player** (an urgent leave; `--rope`, default on): "All Follow Me" at once, then
    the run to the gate. An Adventurer's Rope jump only when one is ready **and no player is on us**
    (`rope_worth`, since 2026-10-17; user: "ropes are useful for getting through groups of mobs or getting a
    lead on a red while they're not currently hitting us ... its not good to just stand there and wait for the
    rope cast to resolve"): no player's "… is attacking you!" and no damage on us with a red within 12, in the
    last 3 s (`ROPE_CALM_S`); otherwise run. `[Rope` (0x03 speech, as Logan's client sends it), the farthest
    route tile within 9 in sight with no mobile on it (live jumps 7-10, an 11-tile target refused), its z walked
    along the route; a refusal ("That location is blocked.") ends the wait at once; 5 s cooldown. No bard skill
    on that walk (`pk_flight`). Logan fled reds this way three times (docs/NOTES.md "Logan Wolf's Mausoleum
    runs of 2026-10-14"); live 2026-10-17 13:40 the rope and a Peacemaking stood him 3 s under a red's
    Explosions (NOTES "Inn-room runs").
    At the gate: no second "All Follow Me", the gate's double-click and its menu's buttons at a macro's pace
    (humanize `gate_macro`, ~0.15 s; every leave but the planned end) (user 2026-10-15: "We do need to greatly
    speedup our activation of the red moongate though. My macro gets me out basically instantly"; live
    20261015_210709 21:57:21-25.4 the flight from Back Problems took 4.2 s from the gate's tile to Totem, while
    he hit Logan; in the simulator now 0.7 s).
  - **Never toward them** (`flee_out`, since 2026-10-21; live 10:35:13 Logan died roping and walking up the
    corridor two reds came down, NOTES "Dead leaving from two reds on Maus 3"). The hostile players (`hostile_spots`:
    reds in view, players in view named in "… is attacking you!", each kept where last seen for 20 s out of view)
    are noted on every hostile-player check, the leave's own included (`note_hostiles`; live 17:37:22 the leave's
    red was out of view before the flight's first look, NOTES "Dead from two reds on the gate's way"), and are
    danger zones on the Mover (`red:<serial>`, 6 tiles: the routes bend round them). Every 6 steps the way
    to the gate is looked at again, and at once when one not known before comes into view; when it comes within
    10 of one of them nearer than we stand (`passes_hostile`; one out of view: 5 tiles more for every second since,
    as far as he could have run), the flight runs away from them first (`flee_tile`: of the tiles the walk reaches
    within 24, the farthest from the nearest of them up to 22, 2+ farther than here; no teleporter tile), waits from
    22+ off, and only cornered, or after 60 s of it, goes to the gate past them as far round them as the way allows
    ("to the moongate: no way round …"). A rope lands only farther from every one of them than we stand (off the
    route when the route goes their way: the walkable tile within 9 farthest from them; none: no rope), and its
    cursor is answered with Esc when a player turns on us between the cursor and our target (live 10:35:06.07).
    **Not into a dead end** (since 2026-10-22; user: "Away first only outside dead ends"; live 20:01:02 the
    flight ran ahead of three reds down Maus 3's east wing into its south-west end, then back into them, NOTES
    "Dead in the east wing's dead end"). Before running away it asks whether the way to the gate from the flee tile
    would still pass them (`dead_end`). If so, every way out passes them, and it goes past them at once, round them
    as far as the zones bend the route: "… and every way from here does (a dead end): past them at once". No rope
    on such a leg: a rope lands only farther from them, back into the dead end. Once past them the way no longer
    passes them, and the flight goes on as above. Simulator `sim/fight2001.py`: 24 of 24 dead before, 0 after.
    **The rope while hamstrung, and its combat cooldown** (since 2026-10-22; NOTES "Hinder, Hamstring and the
    rope"). Hamstrung (buff icon 53: 3 s at a walk's pace, from a player's hit), the rope goes even with a player on
    us (user: rope when slowed even if attacked; live the rope jumped during one). A rope used within 20 s of a
    player's "… is attacking you!" makes the server refuse the next for 2 minutes ("You must wait another 1m 50s
    before you may use this again while in combat."). So such a jump sets the next try 120 s out, and a refusal's
    time left is read back. Hinder (icon 19) is a hold, not a slow walk. No rope during a hold (Hinder, Entangle,
    Paralyze, a transfix: `held_now`; user: "the 'hold' conditions don't let us rope").
    **From ranged reds, the rope even while they hit us** (since 2026-10-22; user: "expand caster to include ranged
    weapons like the bow, since being near them for a second while roping is no worse than being a few tiles away").
    A red is ranged (`ranged_player`) with harmful spell words from him in the last 10 s, a bow or crossbow in his
    hands, or nothing in them. When every red the flight runs from is ranged (`ranged_fight`), the rope goes with them
    on us, on a dead end's "past them at once" legs too, and lands along the way to the goal, the tile fewest of them
    see first, then the farthest along ("…, out of their sight (ranged)" / "in sight of N (ranged)"). A melee red keeps
    the rules above. Simulator replays: `sim/fight2203.py` (Flat Lander, 22:03) 3 of 24 dead before and after;
    `sim/fight2001.py` 0 of 24 before and after. Ranking every tile in reach "out of their sight first" instead lost 7
    of 24 in fight2001: the landings went back into the dead end. Then the simulated caster's spell landed even when
    Logan was out of its sight by then; since 2026-10-23 (docs/SIM.md "Red players") range and sight are checked when
    its cast ends and the spell is held while they fail, and reds hamstring: fight2203 0 of 24 dead with this runner,
    2 of 24 with the one before this rule; fight2001 0 of 24.
  - Then "Travel to Destination" on the re-sent menu, or on the first one when the menu opens with
    that destination already selected (it remembers the last one, live), and `--leave-recall` home
    from the town.
  - The leave margin's route steps are measured to the gate.
- **For the Mausoleum:**
  `--enter-via 5554 700 --enter-via 5507 701 --enter-via 5507 696 --enter-via 5522 637
  --leave-gate 5203 703 --gate-dest Totem` (docs/NOTES.md "Getting in and out of the Mausoleum").
- **`--heal-in-place`** (user 2026-10-13: "leave less and heal in place"). With a long walk out, a leave
  is a last resort.
  - The character leaves only below `--leave-at`, with no route margin, and only with no heal coming (a
    heal potion ready within 3 s, or a vet channel running: `HuntLoop.heal_coming`) or below 15 %
    (user 2026-10-15, after a leave at 20/100 with a potion ready: "he could have just bandaged himself
    and positioned a little better"). The two-attacker rule fires only below `--leave-at` too.
  - A pet below `--pet-leave-at` is healed, not left for, while vet supplies last. The runner walks to
    it even with something on us.
  - Until then: the vet supplies, potions and the retreat.
  - Logan never left mid-trip: he vetted, drank and ran 18 tiles from the countess' copies.
- **Fire and bubbles on the ground** (`Team.fire_tiles`). Fire is `tamer.FIRE_GRAPHICS`, the forgotten souls' 0x6D70
  (user 2026-10-14: "learning not to stand in fire"). It is never a pull spot or a repositioning tile, it is a danger
  tile for routes, and the runner steps off it (`Team.out_of_fire`, within 2 steps, the pets kept within
  `--vet-range`) when it is under us, never mid-channel. Live it hurt no one standing on or beside it (docs/NOTES.md
  "Logan Wolf's run through Maus 4").
  - **A clearwater grouper's bubbles** (Tidal Tomb level 2+, since 2026-10-28; user: "Standing in the clearwater
    group's exploding bubbles is no good"; docs/NOTES.md "Tidal Tomb: the clearwater grouper's bubbles"):
    - A bubble is a ground item, graphic 0xDD8F-0xDDAE (`BUBBLE_GRAPHICS`) with hue 0x0B40 (`BUBBLE_HUE`). It pops
      1.88-2.04 s after it lands and hits each of us within 1 (`BUBBLE_R`): 31-45 on Logan.
    - For that life, every tile within 1 of it (`Team.bubble_tiles`) counts as fire. It is never a tile to step to,
      stand off to, loot from (`loot_tile`) or heal from (`vet_tile`), and routes go round it.
    - Standing in that reach, `Team.out_of_bubbles` takes a tile within 3 steps (`BUBBLE_STEPS`) out of every
      bubble's reach and fire, and off every area hit's centre tile. It ranks them by the depth in an area hit's reach
      (`area_zone`), then by keeping the pets within `--vet-range` (and when looting, the corpse within 2), then by the
      fewest steps.
    - It runs first in `stand_off`: mid-fight, mid-channel, and before a potion unless one is ready (`potion_first`).
      It also runs between a loot's lifts, and from `out_of_fire` in the care.
    - Log: "bubbles at (x, y): stepping to (x, y)", the bubble's tile and ours. With no such tile, once per bubble:
      "bubbles at (x, y): no tile out of their reach within 3 steps; staying".
- **`--crawl-avoid X0 Y0 X1 Y1`** (repeat): no waypoint in the box, and its tiles cost routes
  `DANGER_COST_X` (they keep out where they can). For the Mausoleum, `5170 690 5188 718` is the
  nightstalker room Logan never went into (user 2026-10-13: stay out for now).

**Not built**: claiming or stabling the pets, a corpse run of our own, and Herding (Logan double-clicked
his shepherd's crook once). Never run live yet; run in the Mausoleum simulator (docs/SIM.md) since
2026-10-13.

## Oxygen (Tidal Tomb's levels 2+; `harness/oxygen.py`; user request 2026-10-28)

Under water, from 732 s since the last refill, Logan and the pets lose hits every 6 s down to 66 % of their max
(docs/NOTES.md "Oxygen, measured"). The user: "its going to need to either go back to level 1 for a breath or fight to
the dolphins on level 3", and, on the plan, "I would prefer it just fight normally to the easiest oxygen refill to get
to" (docs/PLAN.md "Oxygen"). Off with `--no-oxygen`.

- **The clock** (`oxygen.Clock`, fed by `scan_events` with every new event and our position):
  - Our seconds under water count from the last refill: dry ground (outside every `oxygen.underwater` box), or the
    system line "Your oxygen is refilled." (a Recovery Dolphin).
  - The server's 'Oxygen' gump 0xEC5F8754 (every 6 s under water) holds it to what its bars allow. The second bar's
    k-th step comes at (k - 1) x 30.1 + 10 s, ± 3 s (scenario `oxygen.gump`, fitted on the store's gumps). A count off
    by less than a step goes to the nearer edge, by more (a run started under water) to the middle.
  - The deprivation's warning line sets it to 726 s, its hit line to 732 s at least.
  - Replayed through the clock, the live stream of 2026-10-24 put the first hit at 733 s (live 732).
- **The refills** (`oxygen.sites`, from data/sim/tidal_tomb.json's `oxygen` block, as `tamer.py` reads the
  Mausoleum's spawn spots): each dolphin, and each `hops` entry without a `skip` (a teleporter up to dry ground and the
  one back down). Only those in our own underwater box count: a level 2 hunt never goes for a level 3 dolphin.
  - Level 2: **the arrival stairs** ((6943, 2004) up, a step N from (x, 2005); (6718, 1984) down, a step S from
    (x, 1983); walked by the user 2026-10-29). Its west strip ((6834, 2079) up, (6609, 2060) down) carries a `skip`: in
    the simulator its legs ran into the strip's arrival, where the shooters are, and cost deaths without sparing the
    deprivation (below). Deleting the `skip` takes it again.
- **The easiest** (`oxygen_site`): the map route to each (onto a hop's teleporter, to within 9 of a dolphin:
  `DOLPHIN_STAND_R`, live 90 of 96 refills at 7-9 tiles), its steps plus `OXY_MOB_STEPS` 25 for each attackable
  monster in view within `OXY_MOB_R` 6 of a route tile. A refill the leg can't reach before the deprivation, even
  starting at `OXY_MIN_S` (more than (732 - 120 - 300) / `OXY_STEP_S` = 104 route steps), is out of reach.
- **When** (`oxygen_due`, in the tick after the fighting, the vet and the blink hold, before the patrol): from
  `OXY_MIN_S` 300 s under, once our time reaches 732 - `OXY_MARGIN_S` 120 - the route steps x `OXY_STEP_S` 3.0 (the
  simulator's fought legs: median 2.2 s a step, 75th percentile 3.0). Routes are planned again every `OXY_PLAN_S` 15 s.
- **The leg** (`oxygen_leg`) is a fought leg, never a leave's walk: `walk_in_fought` to the refill, the pets on 'All
  Guard Me', each round the tick's fighting (`combat_step`), the crawl's rejoin for a lagging pet. Unlike the walk in,
  the leave rules stay on (`oxygen_bail`: a leave rule, failed walks, the run's time up stop it short; the tick
  then leaves). An idle shy monster in the way is backed off from 3 times at most with no kill between, then gone on
  past with the pets on guard (the **leg that must arrive** rule under "Pets"; live 2026-10-29 18:13-18:14 the leg to
  the stairs went back and forth at a soulcatcher 19 south of them 18 times, 470 -> 560 s under water).
  - A hop (`oxygen_hop`): onto the teleporter with the pets beside it (`onto_teleporter`), `OXY_DRY_S` 2 s on dry
    ground, a step off the landing tile (itself the teleporter down) and back onto it.
  - A dolphin (`oxygen_dolphin`): the tick's fighting until its refill line, `OXY_DOLPHIN_WAIT_S` 12 s; none, to
    within 4 and the same wait again.
  - A failed leg (an Abort) hunts on and tries again after `OXY_RETRY_S` 120 s. Every leg leaves an `oxygen` job event
    (refill kind, tile, seconds under at the start, route steps, monsters near it, seconds, outcome, hits).
- **Healing while deprived** (`heal_at_now`, `Team.heal_at`): `--heal-at` and `--pet-heal-at` go no higher than the
  deprivation's floor (66 %): the drain takes back every hit healed above it, a potion's 25-45 in 30-50 s.
- **Not covered:** the fought walk in across level 2 to level 3 (11-20 game minutes in the simulator) has no refill
  leg of its own; it ends 11 tiles from the arrival's dolphin, which refills on the first leg there.
- **Simulator** (docs/SIM.md "Oxygen: the refill legs"; 48 runs a side, one tree): level 3, Logan's deprivation
  damage 4,452 -> 2,435 and the pets' 57,207 -> 29,657, runs with a death 28 -> 25, banked an hour +13 %. Level 2 by
  the arrival stairs (2026-10-29): Logan's deprivation damage 5,056 -> 520, the pets' 63,430 -> 6,374, runs deprived 36
  -> 4, runs with a death 14 -> 11 (none during a leg), banked an hour +3 %. Level 2 by the west strip (2026-10-28):
  runs with a death 12 -> 19 (10 during a leg), the deprivation no lower; hence its `skip`. The back-off limit in the
  legs (2026-10-29, docs/SIM.md "Tidal Tomb: the oxygen leg's back-off loop"; level 2, 144 runs a side, the patrol
  answered as live): legs ending past 732 s 55 -> 8, Logan's deprivation damage 30,873 -> 21,112, runs deprived 87 ->
  46, runs with a death 21 -> 18 (none during a leg), banked an hour +10 %.

## Stationary Penalty (user request 2026-10-03; `harness/stationary.py`)

Outlands puts a debuff on a character that stands still: Outlands buff `0xFF` sub 8, icon 277,
title "Stationary Penalty", "All damage is reduced to 1. Move {value} more steps to remove this
effect" (the wiki's Mining page: it also stops mining "until they move more than 5 steps"). The
runner fought under it for most of some visits (live 2026-10-02: 99 of 165 min in the NPD).

**Measured** (2026-10-03, the timed replays of the captures in `logs/` with the penalty, 26
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

**The rule** (tick order: after the leave rules, the re-equip and the heal;
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

**Vocabulary** (user, 2026-10-03; the Outlands/UO convention): a **floor** or **dungeon level**
is a real game level, numbered from 1 and up the deeper it goes; deeper floors would sit behind
teleporters. The NPD is **dungeon level 1** only, as far as the map shows. A **zone** is the
crawl's own depth band on one floor (route distance from the exit), also numbered from 1:
internal, not a game level.

### The NPD floor graph (dungeon level 1; map files and the store, 2026-10-03)

`python harness/crawl.py floor --memory harness/data/harness.db` (read-only) prints it.
- **One floor: dungeon level 1.** From the arrival side (5536,528, z 0), a BFS with the client's walk rules
  (pathfind.Walk) that never steps on a known teleporter reaches **10,829 tiles**, x 5448–5616,
  y 363–535, the farthest **182 route steps** from the exit spot (5535,529). One storey: no tile
  has two walkable heights; z is 0 (8,513 tiles) or 1 (1,714), with ramps and pits down to −21
  and a few raised tiles up to 22.
- **No deeper floor (dungeon level 2+) behind a teleporter is known.** The store's `teleporters` table holds the
  entrance (1912,2556/2557 → 5536,530) and the exit tiles (5535,529), (5535,530), (5536,530),
  (5537,530) → 1911/1912,2556 only. The client's Atlas packs (`ClassicUO/Data/Client/*.xml`)
  mark only the NPD's overworld entrance (Dungeons.xml, 1913,2566); `teleprts.txt` lists no
  places. The map has walkable areas next to it that the arrival can't reach (3,651 tiles at
  x 5595–5677, y 430–546; 2,601 at x 5380–5489, y 300–388): other dungeons, or NPD parts behind
  server teleporters nobody has stepped on [INFERENCE: which is unknown].
- **So "deeper" on level 1 is farther from the one exit**: zone z = the tiles `40 (z − 1)` to
  `40 z − 1` route steps out (`--crawl-band`). Every zone enters and leaves through the exit
  spot; its route to the exit is its band.

| Zone | Route steps to the exit | Tiles | Waypoints (radius 8) |
|---|---|---|---|
| 1 | 0–39 | 566 | 10: 5532,528 5541,528 5539,521 5534,517 5528,506 5545,506 5533,501 5554,506 5533,494 5542,492 |
| 2 | 40–79 | 2,765 | 38 |
| 3 | 80–119 | 2,885 | 41 |
| 4 | 120–159 | 3,148 | 43 |
| 5 | 160–182 | 1,465 | 15 |

Everything hunted before the crawl is zone 1: all 386 floor tiles of the box the runner has fought in
(x 5528–5547, y 498–530) are under 40 steps out.

### Measured priors (the store, 2026-10-03: 29 finished hunt visits, 213 min)

`python harness/crawl.py priors --memory harness/data/harness.db` prints the model's view of them.
- **Zone 1 base**: 8.3 gold/min and 23.4 hits lost/min (the visits' gold and `hits_lost` over
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

- **Where**: every engagement was within x 5525–5547, y 498–530 (zone 1). Deaths seen in view
  during hunts (`mobile_death`): mongbat 371, giant rat 84, headless 45, zombie ("rotting") 21,
  giant frog 21, skeleton 20, colossal frog 19, wounded harpy 14, rime guar 9 (pets among them
  [INFERENCE]). Nothing is known about zones 2–5.
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
  last 5 min: they take the kills) × the zone's gold rate / (1 + route steps / 20), with ±15 %
  noise: nearby stale areas first, a natural sweep that loops back.
- **At a waypoint.** Nothing showed up: a glance of about `--crawl-dwell` s, then on. Targets
  came (met on the way there or at it): stay while they keep coming; `--crawl-depleted-s` without
  one and the area is depleted, skipped for 10 min.
  - **Looked at counts** (`Crawl.glimpse`, `SEEN_R` 7; user 2026-10-24: "patrolling to the ends of hallways when
    we've already killed the monsters there"): every waypoint in our line of sight within 7 tiles counts as visited
    (its staleness starts over), wherever we stand; a patrol leg (not one toward a monster in view) stops as soon as
    its waypoint is in sight within 7 ("patrol to (x, y): stopped (looked at it from N tiles)") and that counts as
    arriving there. Before, only a waypoint stood on was visited, so a hallway walked past or looked down was walked
    to its end later.
  - **Monsters in view first** (`Crawl.seek`, user 2026-10-17): a monster we'd fight within 18 tiles beyond the
    pull range sets the next waypoint (its area, open zones only, not near an avoided creature, the best by its
    gold a kill over the time the kill costs: the fight, `SEEK_KILL_S` 20 s more, 0.3 s a route step; by gold a
    fighting minute over the route until 2026-10-25, when a tormented soul won over a reanimated daemon at the same
    range and the patrol walked away from the daemon), stops a patrol elsewhere and ends a waypoint's dwell. Live
    before: 68 of 159 patrol starts walked away from every such monster in view. Not one the fight passed over
    (`HuntLoop.skip`: out of our sight, pinned, another player's): until 2026-10-24 such a one was sought too, and the
    patrol went back to the souls in their cells over and over.
- **The efficiency model** (`crawl.Model`). Per creature type: its fights (time to kill, hits lost,
  gold per kill, outcome); estimates are shrunk toward the pool of all fights with 2 pseudo-fights.
  A type is **avoided** when its estimated hits lost per fight exceed `--crawl-max-hits` × our max
  hits, its kills take longer than `--crawl-max-fight-s`, or we fled in more than 30 % of its
  fights (one flee from a type never killed is enough; 153 mongbat kills absorb a bad fight).
  Avoided means: never pulled (fought only when it attacks us), a Mover danger zone of 6 tiles
  around it (also where it was last seen, for 5 min), and its area is not patrolled to.
- **Zones.** Per zone: minutes, hits lost, gold, kills, survival leaves. Zone 1's rates start
  at the store's base, zone z's at zone z−1's estimate (hits × `--crawl-depth-risk`), both with
  5 pseudo-minutes. The next zone opens only when the current one has `--crawl-learn-s` of
  observation (earlier runs count) and the next one's predicted hits lost/min ≤ `--crawl-max-dmg`.
  A zone is closed again for the run (come back out) when its own estimate exceeds that after
  `--crawl-learn-s`, or after `--crawl-max-leaves` survival leaves from it. With the measured
  base (23.4 × 1.5 = 35 > 30) zone 2 opens once zone 1 has proven cheaper than ~20 hits/min,
  e.g. after 10 min at 15 hits/min.
- **Safety.** The leave rules are unchanged and checked first every tick; the route margin
  follows us: `--leave-at` + `--leave-per-step` × the route steps from where we stand to the exit
  (capped at `--heal-at` − 0.05), so a survival leave from deep runs (Mover urgent) the shortest
  route back to the exit spot and takes the exit step; the Mover never steps on a known teleporter
  but the exit. The episode row's `route_steps`/`leave_at` are the ones at the leave.
- **Persistence.** Every engagement (both modes) is a `fight` job event; zone openings and
  closings are `crawl_zone` events; each visit row has a `crawl` block. The next run's model
  starts from them (`crawl.load_prior`), so a creature fled from stays avoided and a known zone
  doesn't have to be learnt again. Rows written before the rename (0-based `levels`) are read
  as zones from 1.
- **`--crawl-loop`** (user 2026-10-13): fight the whole level the way a human does. A human clears
  a pack, moves straight on to the next and keeps going, roughly in a loop. Respawns run on a
  timer, so nothing comes back within seconds of a clear.
  - Every zone is open from the start; there's no zone learning or closing.
  - The waypoints form one tour: from the one nearest the exit, always on to the nearest unvisited
    one by route (`crawl.tour`), and the last leads back to the first.
  - The runner walks the tour in order, past areas it cleared within 10 min and areas of avoided
    creatures.
  - There's no hold at a waypoint: a cleared area is marked depleted and the walk goes on at once.
  - Built for the Mausoleum `--pets` hunt: the zone gating never got the crawl past x 5224, short
    of the bound souls and corpse purgers Logan fought (docs/SIM.md).
- **Moving keeps the Stationary Penalty away**: patrol walks are steps; the longest stay is a
  depleted-area hold (90 s), well under the 240 s reposition.

### Limits

- Run live only in zone 1 so far [INFERENCE: everything beyond zone 1 is unmeasured; "deeper is
  more dangerous" is the user's statement, built in as `--crawl-depth-risk`]. Zones are distance
  bands on dungeon level 1, not floors; floor transitions through a teleporter (to dungeon level
  2+) aren't implemented (the NPD has none known).
- A survival leave is blamed on the target engaged at that moment, whatever else hit us.
- The avoid radius (6 tiles) and the 10 min area rest are guesses [INFERENCE: aggro ranges and
  spawn timers unknown]; mobs that stay out of reach are still dropped by the pin rule (the
  runner never walks to melee).
- Kill stealing is seen only as players or pets in an area and as kills without gold.

### Starting a crawl (overseer)

`run hunt --enter --crawl --pull-range 8 --mana-reserve 999 --target-name "" --gheal-min-missing 1 --timeout 3600`

The live fight-spot arguments plus `--crawl` (no `--fight-spot`). It starts in zone 1 (the
area hunted so far) and opens zone 2 only when the numbers allow it; watch the log for
"crawl: going deeper to zone", "coming back out", "avoiding" and "depleted".

## Guards

Overall timeout, movement stall, the agent gate, server restriction text (`server_restriction` juncture,
stop), death (`death` juncture with the attackers and threats, stop: no corpse runs). **A paused gate ends
the run** (since 2026-10-18; user: "have the run end once I pause the agent gate", after a run walked on out
of the Mausoleum while he played Logan): the first state read or refused action that shows it raises
`agent_link.GatePaused` (`Link.end_on_pause`, set in `main`), the visit row is written, "stopped: the agent
gate was paused (the user took over)", exit 1, and nothing more is sent. A scheduled break is still waited
out. test_loop_hunt `pause`.

**Transfixed: walks wait it out** (`agent_link.Mover.held`, since 2026-10-20). A vampire countess sometimes
transfixes Logan himself, not a pet: "The creature overpowers your will and transfixes you!", every step denied
and items refused ("You cannot use items while in this state.") until "You are no longer hindered." 11.1 s later
(live twice). Every walk (retreat, gate, patrol, back to the pets) stops stepping while that holds, still
runs its guard and `stop`, logs "<label>: held in place; waiting it out" and "free to move again
after N s", and plans again. The denied steps don't count toward `--max-blocked` and aren't remembered as
blocked tiles. At most `HELD_MAX_S` 13 s if the end line is missed. Before, a retreat at 19:23:23 counted 21
denials in 7 s and the run aborted ("too many blocked moves"), leaving Logan on Maus 3. test_sim
`test_transfixed_walk`. **The buff holds too** (since 2026-10-24): Hinder, Entangle or Paralyze on us
(`agent_link.HOLD_ICONS` 19/20/135 before their `ends_t`) is a hold for the walk's checks, and a step denied
while one holds is never taken for a door's denial. Live 2026-10-24 00:40:29 on the walk in, "Your feet
stick to a strange substance!" (Entangle) came as Logan stepped beside a door on Maus 2. The next step was denied
and read as the door's. The door's retry then only turned him: the denial had kept his facing. The door was
walled and the run aborted ("no route"), leaving him there. Now an open-door retry that only turns him steps
once more (`Mover._try_door`). test_mover `test_held_at_door`. Transfixed, Logan is silenced too: a pet command
then gets "You have been silenced and cannot speak." and the pets never take it (live 2026-10-17 15:27:06 an "All
Guard Me", 2026-10-21 09:49:20.84 the leave's "All Follow Me"), so `Team.say` waits the transfix out first ('saying
"All Guard Me": held in place; waiting it out'): nothing else can be done meanwhile (steps denied, items and skills
refused).
test_sim `test_countess_reguard`.

**A failed walk doesn't end the run** (`agent_link.WalkFailed`, `HuntLoop.walk_failed`, since 2026-10-20). A
walk the Mover gives up on (no route, too many blocked moves, too many replans, boxed in, cut by mobiles, over
its move bound) leaves Logan where he stood. Inside the hunt the ticks carry on from there (fight, heal, vet):
"a walk failed: <why>; carrying on from here (N in 300 s)". More than `WALK_FAIL_MAX` 3 within
`WALK_FAIL_WINDOW_S` 300 s and he goes home by the planned leave ("LEAVING by the moongate: 4 failed walks in
300 s (last: …)"); the run then ends at home (exit 0). `--max-blocked` (20) now counts one walk's denials, not
the run's: the run's total grows for hours (the 19:23 retreat's first denial was already the run's 8th), and
the abort names the walk ("retreat: too many blocked moves (21 in this walk)"), so the walks that catch their
own failures (the retreat: "retreat cut short") see it. Before, any of these ended the run in the dungeon:
live 2026-10-20 19:23:30 Logan was left on Maus 3, and he and both pets died standing by 19:30. test_sim
`test_walk_failed_carries_on`.

**An error inside the dungeon escapes home** (`HuntLoop.on_error`, `escape_home`, since 2026-10-24). User, after Logan
was left alive on Maus 2 by the walk in's "no route" (00:40:32) and a calamity killed him there 30 minutes later
(docs/NOTES.md "Entangled beside a door on the walk in"): "Overseers should escape from the dungeon and return home
when there is an erroneous stoppage like that." Any error that ends the run (an `Abort`, a `WalkFailed`, a crash)
while Logan is alive inside a dungeon where recall is forbidden (`escape.recall_forbidden`) and `--leave-recall` is
given now gets him home first, and the run still exits 1:
- `ERROR inside the dungeon at (x, y, z): <error>; escaping home`
- `escape: …` names the way out it takes (below); a way that fails is looked for again from where it left him,
  `ESCAPE_TRIES` 3 ways at most, `ESCAPE_RETRY_S` 5 s apart (`escape: <way>: <why>`).
- Home: `recalled home to …`, the bank and the drop-off as after a leave, `escaped home after: <error> (by <way>)`,
  then `ABORTED: <error> (escaped home by <way>)`, the line the wrapper's task_failed quotes. An `escape` juncture
  (severity attention) and an `escape` job event.
- Not home: `escape failed: <why>`, then `ABORTED: <error> (escape failed: <why>)`; the `escape` juncture is
  urgent ("… Logan still inside at (x, y, z)").

The way out by level (the planner never routes over a teleporter, so each level has the ways it can walk; docs/PLAN.md
"Escape home after an error"):
- **First level** (a gold moongate within `GOLD_GATE_STEPS` 120 route steps, `gold_gate_way`): the walk-in leave's
  flight to it (`flee_out`: a red's danger zones and ropes if one shows up), "All Follow Me" again beside it, the
  recall home.
- **Hunt level** (a route to `--leave-gate`): the leave by the crimson gate as on time up (`leave_by_gate` planned:
  the pets on "All Follow Me" waited for, attackers fought; a red's flight if one is in view), to `--gate-dest`, the
  recall home from there.
- **Middle level** (Maus 2: no route to a gold moongate, none to the leave gate): the `--enter-via` teleporters still
  ahead, the nearest by route first, at flight pace (`walk_in_rush`), then the leave gate as above.
- **Hunt level below the gate's** (Tidal Tomb's level 3: no route to the leave gate, one onto a `--leave-via`
  teleporter): the leave by the gate, up through them first (`leave_up`).
- A recall allowed where he stands (a try before got him out): the recall.

No escape after a death (`death` juncture) or a server restriction message (stop, ANTICHEAT.md §8 rule 4), on a
paused agent gate (`GatePaused`: the user took over) or `ctl stop`, outside a no-recall dungeon, or when the error came
from the escape itself. The overall timeout no longer aborts once the escape began. `--go-home` takes the same way out
on its own. test_sim `test_escape_after_walk_in_error` (the live walk in's failure at (5507, 691): home by the
teleporters and the leave gate, both pets along), `test_go_home` (from the first level, Maus 2 and Maus 3),
`test_go_home_tidal_level3` (up the arrival strip to level 2's crimson gate),
`test_no_escape_on_death_or_stop`.

**No speech hold** (removed 2026-10-15; user: "GMs don't check attendance for dungeon crawling and
stopping because a player speaks will definitely get us killed"). The hunt doesn't read nearby speech
or invulnerable players in view: no `speech_nearby`, no `gm_suspected` from the runner, no Laya
triage, and `--quiet` / `--triage-url` are gone. The lumber runner keeps its hold. Store rows from
before still carry `speech_hold` / `speech_clear` events, and the Jobs page still counts them.

## Memory

One episode per visit (loop `hunt`): `visit`, `t_start`/`t_end`, `spot`, `fight_spot`, `route_steps`
(the route back to `spot`; crawling, from where we stood at the leave), `leave_at` (with the route
margin), `spell`, `hits_start`, `kills`, `lost_kills` (kills taken back: someone else had the loot
rights), `gold`, `xp`, `hits_lost`, `casts`, `heals`, `leaves`,
`stationary_clears`, `repositions` (Stationary Penalty walks), `ended` (the leave reason, `done`,
`time is up` or `stopped`). Job events `kill`, `loot` (`mob`, `name`, `gold`, `xp`, items,
`lift_rejects`; `refused` = `blue` or the server's refusal text for a corpse not ours), `leave`, `soul_jar`
("Soul jars"), `escape` (`why`, `ok`, `way`, `from`, `to`, `failure`, `ways` tried, `escape_s`: "An error inside the
dungeon escapes home", and `--go-home`),
`death`, `speech_hold`, `speech_clear` (until 2026-10-15), and **`fight`** per engagement, both modes (`serial`, `name`,
`body`, `fight_s` from the first attack, `hits_lost` meanwhile, `outcome` kill / fled / dropped /
lost / switched / ended, `mode`, crawling also `zone`); `leave` has `route_steps`. Crawling:
`crawl_zone` (a zone opened or closed, with `floor` and the rates) and the visit row's `crawl`
block (`spot`, `floor` = the dungeon level, `band`, `unlocked` = the deepest open zone, `closed`,
`avoided` types with why, `waypoints` held, `depleted` areas, per-zone `s`, `hits`, `gold`,
`kills`, `leaves`); crawl.load_prior reads them back. Gold
looted is the backpack gold delta (or the status gold delta, whichever is larger; the loot act's
own reply was unreliable live).

**XP** is Outlands mastery-chain experience: a kill gives the creature's gold value × our damage
share ([wiki Experience_Gain](https://wiki.uooutlands.com/Experience_Gain); no mastery chain needed
to accumulate it). No capture shows a per-kill XP message (docs/NOTES.md "Experience"), so `xp` is the gold the corpse held when
opened, before looting [INFERENCE: equal to the creature's value for a solo kill; the weekly bonus
and Fortune scale gold and XP alike per the wiki]. Unlooted kills have no `xp`. The Jobs page's
Hunting dashboard shows kills, gold and XP (docs/VISUALIZER.md §2.4). While a run goes, the Live
page's Hunt job pane shows its totals, gold/hr and kills/hr over the last 5 min and damage per
second (§2.16): every intent carries the run's headline `totals` as `tally` (`INTENT_TALLY`: the pane's
counters, under the proxy's 32-entry cap), its start as `started` and the visit number as `trip`.

### Loot rights (2026-10-03)

Loot rights go to whoever did the most damage; looting another player's kill is a criminal act the
server blocks (user, 2026-10-03). The corpse's S2C `0xFF` sub `0xDEAD` carries its notoriety, kept
on the corpse item as `notoriety` (latest value; docs/WORLDMODEL.md). In captures 20261003_113952,
_123614 and _125556 (timed replay through `WorldRuntime`) the agent opened 125 corpses: all 35 refused
with "Players cannot commit aggressive actions in that location." were notoriety 1 (blue), all 90
opened were 3 (grey); in 125556 alone 29 / 65. Before this change the runner lifted anyway after a
refusal (a second refusal, `27 05`, the item back in the corpse, `0x1D` ~190 ms later), counted the
vanished item as taken ("looted 1 item(s) from a mongbat: +0 gold, ~21 xp", 125556 13:03:58) and
counted the corpse's gold as xp. Now (`loop_hunt.loot`):
- A **blue corpse** (notoriety 1) is skipped before any walk or open: no packet, logged "corpse of X
  is someone else's kill (blue): not looted".
- An **ally's kill** (the monster was seen in an ally's fight, `ally_fought`; "Someone else's fight" above) is skipped
  the same way even when its corpse is grey and the server would let us open it (user 2026-10-28: "we can loot the
  corpse, but we shouldn't as that is rude"), refused as "ally".
- A kill whose corpse is **always empty** (`EMPTY_CORPSES`: the pincer crab, a dungeon-nest crab's broodling; since
  2026-10-28) is never queued for the loot: no walk, no open (each cost ~4-5 s, 245 of 245 empty: docs/NOTES.md
  "Tidal Tomb: a pincer crab's corpse is always empty"). It still counts as a kill, with a gold-model sample of 0.
  test_loop_hunt `empty_corpses`.
- **No loot walk into an area hitter's reach or a shy monster's shot** (`--pets`, `Team.loot_tile`, since
  2026-10-21). With an area hitter (`tamer.AREA_R`, a charger) or an awake corpse purger or blightmare (in war mode,
  `Team.shy_awake`; its shot: sight within 12 or 13, `Team.shot_at`) in view, the corpse is looted from a tile clear
  of every area hit's reach (`area_zone`, the stand-off's model) and out of every such shot within `combat.LOOT_RANGE`
  2 of it, out of fire, the fewest monsters in war mode within 3, the fewest idle eyes, the fewest steps (the vet
  tile's chooser, which ranks the vet tile by the shots too): "to the corpse: looting from (x, y), N from it, out of
  the area hits' reach". Every loot walk's route is bounded to twice the distance + 8 steps (`LOOT_DETOUR_STEPS`;
  round an area hit's reach among the Mover's danger zones when that is a short detour, `Team.around_areas`, else the
  plain walk within the same bound): unbounded it planned 117-123-step detours to corpses 7 tiles off, and after a
  refused shove in the countess corridor at (5182-5184, 682) it walked 119-123 steps through two rooms away from the
  pets (simulator 2026-10-21: 14 of 41 deaths in one batch); longer, the corpse is left ("corpse of X at (x, y): to
  the corpse: only a N-step detour …; leaving it"). When every tile within 2 of the corpse is in reach or in a shot it
  stays queued (other players race us to loot): "not looting <name> yet: the corpse at (x, y) lies D into an area
  hit's reach, every tile within 2 of it too; waiting for the area hitter to die or go" (or "… lies in the shot of
  the awake corpse purger, every tile within 2 of it too; …"), looked at again every
  `LOOT_DEFER_S` 1 s (`HuntLoop.loot_next` passes it over meanwhile, so the fight goes on) and looted as soon as the
  hitter is dead, gone or out of war mode. **Nor to beside an awake monster** (since 2026-10-26): not to a tile
  within 2 of a monster in war mode near the corpse (not a shy one: their shot rule above), as the spacing off the
  tank. If no tile within 2 of the corpse is that clear it waits ("… lies beside the awake ocean bedrock, every tile
  within 2 of it too"). In the simulator, Tidal Tomb seed 6 walked "to the corpse" at 100 hits to beside a provoked
  ocean bedrock and was at 13 seven seconds later. Here clear and within reach: looted from here. User 2026-10-21; live
  hunt-20261021-091743-38b6 09:54:10 "to the corpse" walked to (5242, 676), a tile from the reanimated daemon
  fighting the pets, 8 s before its death. `ctl act loot` doesn't walk. test_sim `test_loot_off_area_hitter`.
  **Nor into an idle shy monster's first sight** (since 2026-10-26): when the loot tile (or the corpse) lies where an idle
  shy monster would see Logan before a pet (`Team.shy_first`), the loot waits ("not looting X yet: the corpse at (x, y)
  lies in an idle master reanimator's sight before the pets'"); the patrol pull or the fight wakes it on the pets first.
  Simulator, the live 19:30 room (test_sim `test_caster_room_pets_first`): the shadowguard patrolled and killed from the
  doorway, then "to the corpse" 9 steps into the room woke the reanimator onto Logan.
  **Nor from a clearwater grouper's bubbles' reach** (since 2026-10-28; "Fire and bubbles on the ground" above): a
  corpse with a bubble within 2 is looted from a tile out of every bubble's reach. When every tile within 2 is in reach,
  the loot waits ("… lies in a clearwater grouper's bubbles' reach, every tile within 2 too"); a bubble lasts ~2 s. A
  bubble landing beside us mid-loot steps us out between lifts, keeping the corpse within 2 (live 2026-10-28
  21:13:48-51: Logan looted from beside one, -40).
- Our open answered by "Players cannot commit aggressive actions in that location." or "You may not
  loot this corpse." (the server's 0xAE system text; the client's Auto Open Corpses got the latter,
  ANTICHEAT.md A14) **stops at once**: no lift. The runner waits for the corpse's `0x24` or such a
  text after its `0x06` (the contents often arrive with the corpse, so they prove nothing). Every
  corpse is handled once: a refused one is never retried. [INFERENCE] The text carries no serial, so
  a client-made double-click answered in the same ~0.1 s would count as ours.
- An item counts as **taken only when it lands in the backpack** (its `0x25` into the pack, or gone
  with the pack's pile of that graphic grown: RunUO merges gold); a `0x27` lift reject (`lift_reject`
  event) means not taken (`lift_rejects` in the loot event).
- **Lifts 0.6 s apart at least** (`combat.LIFT_GAP_S`, after the human drag pause; since 2026-10-21). The server
  refuses a lift within ~0.5 s of the last one it took: "You must wait another moment before you may pick up an
  item from here." and `27 05` (the store: 144 refusals 0.06-0.50 s after the last taken lift, two at 0.511 and
  0.571; accepted from 0.387 s once, 0.467 s up; a refusal doesn't restart its clock). Such a refusal is tried
  again once the gap is over, `combat.LIFT_TRIES` 3 lifts in all, the leave rules and a heal below `--heal-at`
  checked before each retry: "lift of 0x…: 'You must wait another moment'; trying again in N s". Other refusals
  stay "not taken" at once. User 2026-10-21: "the runner loots items faster than the allowed delay, so we're
  leaving valuable loot behind"; live hunt-20261021-091743-38b6 left 5 items so (lifts 0.41-0.49 s apart).
  `ctl act loot` paces and retries the same way. test_sim `test_loot_lift_gap` (the simulator refuses lifts
  within `world.LIFT_GAP_S` 0.5 s).
- A blue or refused corpse is **someone else's kill**: its kill credit is taken back (`kills` - 1,
  `lost_kills` + 1; `--kills` counts only ours), it adds no xp, no gold and no gold-model sample
  (`kill_gold` isn't called). Its `loot` event has `refused` (`blue` or the text) with gold 0, and
  crawl.load_prior gives such kills no gold sample (gold unknown, not 0), so the crawl's per-type
  gold estimate doesn't learn 0 from another player's kill. The fight itself (time, hits lost) is
  still recorded: those costs were ours.

## Test

Loot rights run (`python test_loop_hunt.py rights`, in process, ~1 s): `HuntLoop.loot` over a
`WorldRuntime` fed with the sim's packets. Four of our kills beside the spot, gold in each corpse
before any open: the blue one gets no packet at all, the refused open gets one `0x06` and no lift,
the lift answered `27 05` + back in the corpse + `0x1D` isn't counted, the fourth is looted; totals
kills 4 -> 2 (`lost_kills` 2), xp only from the two opened corpses, gold only the landed pile.

Pets run (`python test_loop_hunt.py pets`, in process, ~10 s; tamer clocks shortened): a scripted
server with our gravebug and phoenix beside us and an idle shade wolf W1 5 tiles off. The first
packet is "All Guard Me" byte for byte as Logan's Razor sent it; no skill or "All Kill" before the
pets engage W1; Discordance at W1 (alone in the fight), then once W2 joins against the phoenix the pair provoked,
W1 incited onto W2 with two cursors (`PROVOKE_FIRST` "pairs"), and no peace at the provoked W1; vet supplies for the
hurt gravebug and later for us; "All Guard
Me" after the kills; W3 hitting us gets "All Kill" only after 0.8 s, then Discordance and
Peacemaking; three kills (W2 never our target); no `0x72`, `0x05` or menu pick; every speech a
Razor-shaped pet command; the phoenix's ghost flag (`bf 0019`) is a leave reason, `go_back`
refuses with a `pet_down` juncture; release/transfer phrases refused.

`python test_loop_hunt.py [rights|pets|default|staff|fight|crawl|recall]` (a few minutes for all, private ports): a
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
north to room R1 (a mongbat), a corridor west to room R2 (22+ route steps out: zone 2) with a
troll in its corner that hits 15 every 0.8 s next to it. The server sends mobiles as they come
within 18 tiles. Checked: the runner patrols ≥ 4 waypoints through all five areas and fights in
more than one; R2 is entered only after the `crawl_zone` event opening zone 2 (floor 1); the troll is
fought once, the leave from R2 (≥ 20 route steps out, `--leave-at` raised) is a threat juncture,
runs back to the exit spot and steps out there (never the arrival tile); the troll's `fight` is
`fled` and the crawl avoids it: back inside it is never attacked or come within 2 tiles of; R1,
once its mongbat is dead, is left as depleted for another waypoint; the second mongbat (it comes
once we are back in) is killed, both looted, their `fight` events carry time and zone 1, and the
rows carry the `crawl` block (dungeon level 1, troll avoided, zone 2's leave). `python harness/test_crawl.py`
covers the geometry (coverage, areas and zones by route through a wall, the walk-memory floor),
the model's shrinkage and avoid rules, zone opening and closing, the waypoint choice and the
store prior.

A fifth run (`recall`) hunts by recall with the prismatic staff (`--enter-recall`/`--leave-recall`
with the sim's tome, `--fight-spot` 10 tiles from the golden gate, `--bank-gold 1`, `--pk-wait 2`):
it starts in town by the banker, recalls in by the tome row "Urukton Bluffs" (button 101), kills
and loots A; a red comes into view 15 tiles off (beyond the 12-tile rule): the tome is double-clicked within 1.5 s, the
recall at the fight spot is refused after the cast (501802, as RunUO), it runs to the arrival and
recalls home by the default row (100), finds the banker by a click label, says "bank", drags the
121 gold in, rests, waits out `--pk-wait`, recalls in. D attacks; when first hurt the server says
"Bastet is attacking you!" with nobody in view: it moves within 1 s, walking to the arrival first
(refused near the fight spot before), recalls home (nothing to bank), back in, kills and loots D,
recalls home ("done") and banks 23. Checked besides: no attack after either threat until home,
every recall knocks the staff into the pack and it is re-equipped after each arrival before the
first attack, the Stationary Penalty each recall brings is walked off before the first attack,
the `travel` (in / home, the refused one `restricted`), `bank` (amounts, box gold), `leave`
(`recall.refused`, `walked_first`) events, two urgent `threat` junctures, three visit rows.

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
  the survival leave overrode the hold. (The hunt has had no speech hold since 2026-10-15; see Guards.)
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

## Live (2026-10-03, NPD; the pin cases with Shackleworth)

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

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
| `--crawl-dwell` / `--crawl-depleted-s` | `6` / `90` | A waypoint where nothing showed up is looked over for about this long (× 0.6–1.4); an area where targets came is left as depleted after this long without one. |
| `--crawl-learn-s` | `600` | Seconds observed in a zone (store history included) before the next one may open. |
| `--crawl-max-dmg` / `--crawl-depth-risk` | `30` / `1.5` | The most hits lost per minute a zone may cost (estimated; for an unvisited zone: the zone before's × the risk factor). |
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
| `--heal-at` | `0.75` (`--pets` 0.80) | Heal below this share of max hits (healing.py: a heal potion if one can be drunk, else a spell). |
| `--gheal-min-missing` | the mana break-even for your Magery (19 at 60) | Missing hits from which the heal spell is Greater Heal; below it, Heal. |
| `--leave-at` | `0.60` (`--pets` 0.35) | Leave below this share of max hits (plus the fight spot's route margin). |
| `--leave-per-step` | `0.004` | The route margin: `--leave-at` + this × the steps of the route from the fight spot back to `--spot`, at most `--heal-at` − 0.05. |
| `--leave-multi-at` | `0.80` (`--pets` 0.55) | Leave below this share when two or more mobs are attacking. |
| `--mana-reserve` | `22` | Mana kept for heals (two Greater Heals): the attack spell only while mana ≥ reserve + its cost. |
| `--spell` | `lightning` | Attack spell (Magery name or 1-64). |
| `--target-name` | `mongbat` (`--pets`: any monster) | Words the monster's name (or click label) must contain. |
| `--pull-range` | `3` (`--pets` 8) | Engage mobs within this many tiles of the spot. At most 10 (the spell range): a larger value is capped, with a log line. |
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
   With `--leave-recall` leaving is a recall home instead ("Recall in, recall out").
2. **Speech hold** (speech_guard.py, as in the lumber runner) once no fight is on: nothing is sent
   until the overseer acks `speech_nearby`. Leaving to survive overrides the hold.
3. **Heal** below `--heal-at`, one heal per tick (`harness/healing.py`, shared with `ctl act heal`;
   user decision 2026-10-02):
   - **A heal potion whenever one can be drunk**: any heal potion (graphic `0x0F0C`) in the
     backpack at any bag depth, smallest stack first, its bags opened first, the stock
     double-click. "Can be drunk" means not at full health and 10 s since the last drink (the
     runner's own clock). If the server still refuses (cliloc 500235, e.g. after a drink in the
     client, or Outlands' "You must wait N second(s) before using another healing potion."), the clock
     is set to the wait it names (500235: a full cooldown) and a spell goes out in the same tick.
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
   each item into the backpack, gold first, up to the weight limit. See "Loot rights" below:
   blue corpses are skipped, a refused open stops at once, only items that reach the pack count.
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

**A fight spot away from the exit** (`--fight-spot`, user request 2026-10-03: the exit tile is
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
| `--pet NAME\|SERIAL` | remembered, else the tamed pets within 3 tiles | One of our pets (repeat). The roster is remembered per character (memory meta `own_pets`). The run won't start without one. |
| `--pet-heal-at` | `0.95` | Veterinary supplies when a pet within `--vet-range` is below this share of hits (or poisoned). |
| `--pet-leave-at` | `0.20` | Leave when a pet is below this. |
| `--vet-range` | `2` | Tiles the supplies reach. The Healer's Codex perk "Without Borders" adds 1/2/3 tiles (knowledge #11343); the user is levelling it (2026-10-12), so raise this with its tier. |
| `--retreat-at` | `0.55` | Below this share of hits with a mob next to us: run 7 tiles from every attacker and let the pets hold it. |
| `--discord` / `--peace` / `--provoke` | on | Each bard skill (`--no-…` turns one off). |
| `--songs` | on | Keep the three barding songs up (`--no-songs` turns them off). |
| `--heal-at`, `--leave-at`, `--leave-multi-at`, `--pull-range`, `--target-name` | `0.80`, `0.35`, `0.55`, `8`, any monster | The pets style's own defaults (STYLE_DEFAULTS; the mage defaults are unchanged). |

**The rules** (each from the capture or a user statement):

1. **Guard, don't pull.** "All Guard Me" at every arrival (guarding stops at a recall or a level
   transition, wiki Animal Taming) and after every kill; Logan said it 26 times for 25 kills. The
   user (2026-10-12): a mob aggroes on what comes into its aggro range and line of sight; pets on
   guard engage it before it engages the character, while an aggressive act of ours (Discordance,
   "All Kill") makes the mob aggro on the character. So a target the pets haven't engaged is only
   approached to 6 tiles (`APPROACH_R`, [INFERENCE] aggro range) and waited for; one that neither
   fights the pets nor us for 15 s is dropped for 60 s.
2. **Bard skills only at a target the pets fight** (`engaged`: hurt within 10 s, a pet within 12
   tiles, the phoenix's reach): Discordance first, then Provocation of another mob in the fight
   (the other onto a second other, or onto our target), then Peacemaking on the target (it stops
   hitting the pets while they kill it). A provoked target isn't pacified. 51 of Logan's 60
   bard/"All Kill" target answers on the trip were at such a target by this rule; the other 9 were
   openers the user now advises against. Cooldowns: one bard skill every 5 s, peace/provoke 10 s
   after a success (wiki Peacemaking); "You must wait a few moments to use another skill."
   (cliloc 500118) moves the clock; "beyond your skill" stops that skill on that creature type;
   a Barding Break or "That target is currently under a barding break effect." makes it immune to
   peace/provoke for 20 s [INFERENCE: difficulty / 10 s]; "Target cannot be seen." blocks it 10 s.
   **Barding songs** (`--songs`; user 2026-10-13, capture 20261013_173449): the same skills at a
   ground tile beside us play the Songs of Discordance, Peacemaking and Provocation on us and the
   pets for 15 minutes (live 15.0 % each: less damage taken from creatures, more healing received,
   more damage). One song every 10 s whenever nothing more urgent is due, fights included (a care
   action after the retreat, vet supplies, guard and walk to a hurt pet), each again a minute before
   it ends. The user sang them mid-fight too.
3. **"All Kill" only at a mob already on us** that the pets leave alone for 4 s (its aggro is on us
   anyway), at most every 12 s per mob; the target cursor is answered after an aim pause and a
   fresh read (the monsters-only guard). Not re-guarded until the kill.
4. **Vet supplies** (graphic `0x6496`, one double-click, no target): every pet within range and us
   at 25 % (patch 2023-08-14); a 5.0–5.3 s channel (43 on the trip) with the range checked at the
   finish, so no step during it. Used for a hurt or poisoned pet in range, or for us below
   `--heal-at` with no heal potion ready; a hurt pet out of range is walked to (1 tile: bandaging
   reaches 2 only after 5 s stationary) when nothing is on us. No supplies: a `low_supplies`
   juncture (item `veterinary supplies`). Resting outside waits for every pet to reach
   `--pet-heal-at`, by the supplies or, with none, by natural regeneration; past `--rest-timeout`
   the run stops rather than go back in with a hurt pet (that pet's leave rule would fire on arrival).
5. **Self care**: heal potions as the mage style drinks them (Logan's sit in a potion satchel inside
   a bag: every bag depth is searched); the retreat above (live 21:27:50 a bleed took Logan from 72
   to 35 hits in 2 s; he ran 18 tiles while drinking and the pets held the mob).
   **Out of a Charge's reach** (user 2026-10-13). A charger is a nightstalker (knowledge #14625:
   it teleports up to 12 tiles next to its target and hits up to 3 within 1 tile), or any type seen
   to say "*charges*". While one is in war mode, the runner keeps 3 tiles from it and from the pet
   beside it (`tamer.Team.stand_off`, up to 6 steps, never mid-channel).
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
   docs/SIM.md). After a recall a missing pet is an urgent `pet_left_behind` juncture.
7. **Leaving for the pets**: a pet dead (bonded ghost), below `--pet-leave-at`, or out of view for
   30 s. With a pet dead or gone the run never goes back in (`pet_down` juncture).
8. **Kills**: the engaged target, and any mob the pets fought (in war mode next to a pet that was
   hurt, or engaged) that dies within 60 s; their corpses are looted like the mage's.
9. **Never a pet's release or transfer** (user 2026-10-12: the pets are expensive): the commands
   are a fixed list (`tamer.COMMANDS`: All Guard Me, All Follow Me, All Kill, All Stop, All Stay,
   All Come), Razor-shaped (`actions.razor_say`), and anything matching `release`/`transfer` is
   refused by the runner and by `ctl act say` / `ctl act menu_pick`.

**Aggro** (`aggro.py`; user 2026-10-13: "positioning and managing aggro is the number one most
important thing in dungeon PvM in Outlands"). Line of sight is `los.Sight` (ServUO's rules, checked
live), monster z from the map surface, aggro range 10 (the simulator's) [INFERENCE].

| Argument | Default | Meaning |
|---|---|---|
| `--pull-budget` | `3.0` | While the pets fight monsters worth E, an advance (patrol, approach) stops where more would wake (W, idle monsters that would see us within 12 tiles) when E + W is above it. 0 = off. |
| `--never-aggro TYPE` | vampiric consort | Never wake it alone (repeat for more types). |
| `--reposition` / `--no-reposition` | on | In a fight, step to a better tile for line of sight. |
| `--hold-first-sight` / `--no-hold-first-sight` | on | Nothing fighting: stop where an idle monster first sees us within its aggro range, and let it come. |

- **Weights** ("weighted by danger"): (wiki difficulty - 50) / 40, clamped to 0.25-3: a vampire bat
  0.43, a shadow minion 0.53, a shade wolf 0.92, a banshee 1.6, a nightstalker 1.8, a shadowguard 2.05,
  the countess 2.4.
- **Hold at first sight** (Logan at the nightstalker room's north edge: the wolves and the stalker came
  to him 5-10 s after first sight, the knight only when he moved on): with nothing fighting, the patrol,
  an approach and the idle tick stop as soon as an idle monster would see us within 11 tiles (a step
  outside its aggro range), for up to 10 s per monster; one that hasn't come by then is passed over.
  **The pull spot**: before holding, once per group, the runner steps (up to 3) to the tile where the
  fewest of them by weight, but at least one, would see us within their aggro range (10), not in a
  Charge's reach or a never-aggro monster's sight; they come from there in stages instead of being
  walked into.
- **Never-aggro monsters**: their sight within 13 tiles is no-go: the patrol stops before it, the
  crawl skips waypoints within 15 of one and routes keep out (`crawl.Crawl.observe`); the runner never
  pulls one. One that comes for us in war mode is run from ("I run until the consort leashes and I'm
  in an area that I can manage"): "All Follow Me", then to the gate when that is away from it, else 22
  tiles beyond it, until it stops chasing. The leave rules take over when we're hurt ("when all else
  fails ... I will probably head for the gate").
- **Line of sight in a fight** ("situational": between two rooms, stay out of sight of the room the
  pets aren't fighting in; "if it's easy to stand out of LoS of a caster like the banshee while still
  being able to heal our pets ... it's better"): every 3 s, a tile within 2 steps that keeps the pets
  near us within `--vet-range`, out of a Charge's reach, with a better score (idle monsters that would
  see us x 10 by weight, a never-aggro one x 100, casters in war mode with sight of us x 1; 0.3 a step);
  never mid-channel.

**In and out of a dungeon** (user 2026-10-13: you can't recall in a dungeon except near a gold
moongate, and never near a red one).

- **In:** `--enter-via X Y` (repeat) steps onto those teleporters in order after the recall in.
  - The approach is the tile the route onto each one arrives from (a teleporter on stairs is entered
    from one side only).
  - Before each step: "All Follow Me", then up to 20 s more for every pet within 3 tiles of the
    teleporter, else Abort. They come through only if near it (a pet 3 tiles behind us is 4 from the
    teleporter: in the simulator it stayed behind on the second level's one-way hops).
  - "All Guard Me" again after each teleporter; `--songs`: a song due before each leg, as Logan
    sang them on the first two levels.
- **Out:** `--leave-gate X Y --gate-dest NAME` (`moongate.py`).
  - The runner walks to the gate with the pets still guarding and says "All Follow Me" beside it.
    Then it double-clicks the gate from that tile, as Logan did (the step onto it only when the gate
    item isn't in view), and picks the destination by its label.
  - Then "Travel to Destination" on the re-sent menu, or on the first one when the menu opens with
    that destination already selected (it remembers the last one, live), and `--leave-recall` home
    from the town.
  - The leave margin's route steps are measured to the gate.
- **For the Mausoleum:**
  `--enter-via 5554 701 --enter-via 5506 701 --enter-via 5507 697 --enter-via 5521 638
  --leave-gate 5203 703 --gate-dest Totem` (docs/NOTES.md "Getting in and out of the Mausoleum").
- **`--heal-in-place`** (user 2026-10-13: "leave less and heal in place"). With a long walk out, a leave
  is a last resort.
  - The character leaves only below `--leave-at`, with no route margin. The two-attacker rule fires
    only below `--leave-at` too.
  - A pet below `--pet-leave-at` is healed, not left for, while vet supplies last. The runner walks to
    it even with something on us.
  - Until then: the vet supplies, potions and the retreat.
  - Logan never left mid-trip: he vetted, drank and ran 18 tiles from the countess' copies.
- **`--crawl-avoid X0 Y0 X1 Y1`** (repeat): no waypoint in the box, and its tiles cost routes
  `DANGER_COST_X` (they keep out where they can). For the Mausoleum, `5170 690 5188 718` is the
  nightstalker room Logan never went into (user 2026-10-13: stay out for now).

**Not built**: claiming or stabling the pets, resurrecting a pet, and Herding (Logan double-clicked
his shepherd's crook once). Never run live yet; run in the Mausoleum simulator (docs/SIM.md) since
2026-10-13.

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

Overall timeout, movement stall, the agent gate (actions wait out a pause or break), server
restriction text (`server_restriction` juncture, stop), death (`death` juncture with the attackers
and threats, stop: no corpse runs), the speech hold above.

## Memory

One episode per visit (loop `hunt`): `visit`, `t_start`/`t_end`, `spot`, `fight_spot`, `route_steps`
(the route back to `spot`; crawling, from where we stood at the leave), `leave_at` (with the route
margin), `spell`, `hits_start`, `kills`, `lost_kills` (kills taken back: someone else had the loot
rights), `gold`, `xp`, `hits_lost`, `casts`, `heals`, `leaves`,
`stationary_clears`, `repositions` (Stationary Penalty walks), `ended` (the leave reason, `done`,
`time is up` or `stopped`). Job events `kill`, `loot` (`mob`, `name`, `gold`, `xp`, items,
`lift_rejects`; `refused` = `blue` or the server's refusal text for a corpse not ours), `leave`,
`death`, `speech_hold`, `speech_clear`, and **`fight`** per engagement, both modes (`serial`, `name`,
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
Hunting dashboard shows kills, gold and XP (docs/VISUALIZER.md §2.4).

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
- Our open answered by "Players cannot commit aggressive actions in that location." or "You may not
  loot this corpse." (the server's 0xAE system text; the client's Auto Open Corpses got the latter,
  ANTICHEAT.md A14) **stops at once**: no lift. The runner waits for the corpse's `0x24` or such a
  text after its `0x06` (the contents often arrive with the corpse, so they prove nothing). Every
  corpse is handled once: a refused one is never retried. [INFERENCE] The text carries no serial, so
  a client-made double-click answered in the same ~0.1 s would count as ours.
- An item counts as **taken only when it lands in the backpack** (its `0x25` into the pack, or gone
  with the pack's pile of that graphic grown: RunUO merges gold); a `0x27` lift reject (`lift_reject`
  event) means not taken (`lift_rejects` in the loot event).
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
pets engage W1; Discordance at W1, then W2 (fighting the phoenix) incited onto W1 with two cursors
and no peace at the provoked W1; vet supplies for the hurt gravebug and later for us; "All Guard
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

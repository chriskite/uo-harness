# Hunt loop (`harness/loop_hunt.py`)

A programmatic task like the lumber loop: fight monsters at one spot, heal, loot, leave when
hurt. Built for New Player Dungeon mongbats (user decision 2026-10-01: the thresholds are task
arguments). Start it with `ctl run hunt [args…]` (docs/OVERSEER.md) or directly:

```
python harness/loop_hunt.py --kills 5                 # standing in the NPD
python harness/loop_hunt.py --enter --kills 5         # from outside the entrance
```

## Arguments

| Argument | Default | Meaning |
|---|---|---|
| `--spot X Y` | `5535 529` | The tile to fight on (the NPD exit tile). Leaving starts here. |
| `--exit-dir D` | `4` (south) | The step from the spot onto the exit teleporter. |
| `--enter` | off | Start outside: walk to `--entry`, step `--entry-dir` to teleport in. |
| `--entry X Y Z` / `--entry-dir D` | `1912 2557 -20` / `0` | The tile before the NPD entrance teleporter and the step that triggers it. |
| `--heal-at` | `0.75` | Greater Heal on self below this share of max hits. |
| `--leave-at` | `0.60` | Leave below this share of max hits. |
| `--leave-multi-at` | `0.80` | Leave below this share when two or more mobs are attacking. |
| `--mana-reserve` | `22` | Mana kept for heals (two Greater Heals): the attack spell only while mana ≥ reserve + its cost. |
| `--spell` | `lightning` | Attack spell (Magery name or 1-64). |
| `--target-name` | `mongbat` | Words the monster's name (or click label) must contain. |
| `--pull-range` | `3` | Engage mobs within this many tiles of the spot. |
| `--kills N` | `0` | Stop after N kills (0: until `--timeout`). |
| `--timeout S` | `3600` | Then finish the fights on us, loot, and leave. 180 s past it the runner aborts wherever it is. |
| `--rest-to` | `0.95` | After leaving: Greater Heal / regenerate outside to this share of hits (and `--mana-reserve` mana), then go back in. `0`: stop after leaving. `--rest-timeout` (900 s) bounds the rest. |
| `--loot` / `--no-loot`, `--loot-max` | on, 25 | Loot our kills' corpses (items per corpse). |
| `--human`, `--seed`, `--human-fast`, `--no-map`, `--quiet`, `--triage-url`, ports, `--memory` | | As in the lumber runner. |

## What it does

Each tick re-reads the proxy state and decides, in this order:

1. **Leave** when a rule fires (`threat` juncture, job event `leave`):
   - a hostile player (threats.py: red/grey/orange) within 12 tiles: urgent; the run stops outside;
   - two or more attackers and hits below `--leave-multi-at`;
   - hits below `--leave-at`.
   Leaving is war mode off (if on), back to the spot, then the step in `--exit-dir`. The runner
   expects a teleport: a deny followed by the move (the NPD exit, live), or a confirmed step and the
   move right after. Outside it rests and goes back in (except after a hostile player).
2. **Speech hold** (speech_guard.py, as in the lumber runner) once no fight is on: nothing is sent
   until the overseer acks `speech_nearby`. Leaving to survive overrides the hold.
3. **Heal**: below `--heal-at`, Greater Heal and target self. Without the mana for it, one
   `low_supplies` juncture per visit.
4. **Loot** our kills' corpses when nothing is attacking us, like `ctl act loot`: human corpses
   refused, walk within 2 tiles, open the backpack (stock dclick) and the corpse, then lift + drop
   each item into the backpack, gold first, up to the weight limit.
5. **Fight** the target:
   - Targets come only from the live world model (`world.mobiles` holds what the stock client still
     has; docs/WORLDMODEL.md) and pass the `ctl act attack` guard (`combat.attackable`: threats.py
     monster, notoriety 3-6, on screen). Serials the server reported dead (`mobile_death`) are never
     targeted.
   - A mob swinging at us (`world.swings`, within 10 s) comes first: the current one, else the one
     with the lowest hits. Otherwise the nearest within `--pull-range` of the spot.
   - Attack once (war mode on, `0x34` unless the client has a status request outstanding, `0x05`),
     and once more when the mob first comes adjacent (the overseer's NPD procedure).
   - The attack spell while the mana allows and the mob is within 10 tiles: cast, wait for the
     cursor, a human aim pause, **re-read the state**, then `0x6C` on the mob's current tile. If the
     mob is gone meanwhile, or a leave rule fired while aiming, the cursor gets the stock Esc
     `0x6C` instead (ANTICHEAT.md §10 A12). "That is too far away" / "cannot be seen" means melee
     only at that mob for 10 s.
6. **Idle**: war mode off when nothing is near, back to the spot, wait.

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

One episode per visit (loop `hunt`): `visit`, `t_start`/`t_end`, `spot`, `spell`, `hits_start`,
`kills`, `gold`, `hits_lost`, `casts`, `heals`, `leaves`, `ended` (the leave reason, `done`, `time
is up` or `stopped`). Job events `kill`, `loot` (gold, items), `leave`, `death`, `speech_hold`,
`speech_clear`. Gold looted is the backpack gold delta (or the status gold delta, whichever is
larger; the loot act's own reply was unreliable live).

## Test

`python test_loop_hunt.py` (~20 s, private ports): a simulated NPD behind the real proxy. A mongbat
that died to someone else (`0xDEAD` only) must never be touched; the first mongbat flies in and hits
hard (heal path), dies to Lightning, its gold is looted; two more come in swinging and trigger the
two-attacker rule; the runner rests outside with a Greater Heal and goes back in; the one the server
doesn't re-send (pruned, out of range) must never be targeted; the second kill ends the run outside.
Attack, cast, target, cancel and loot packets are compared byte-wise with the builders.

## Not yet

- Not run live yet.
- Mana only: no potions or bandages.
- The spell range (10) and the corpse wait (4 s) are [INFERENCE].

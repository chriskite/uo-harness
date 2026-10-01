# Threats in the overworld: player killers, thieves, monsters

Research for overworld lumberjacking (overseer phase): notoriety and where PvP happens, Tracking and
Detect Hidden, escape math for a recall under threat, thieves, overworld monsters, and the detection
intel that shapes our responses. Offline only: Outlands wiki, official patch notes, the local
upstream ClassicUO source, upstream RunUO source, and our own captures in `logs/`. Nothing here was
tested in-game for this document. Written 2026-09-29.

Travel spells, the Heat of Battle lockouts, death and resurrection are covered in
[TRAVEL_DEATH.md](TRAVEL_DEATH.md). This file cites those facts only where the escape math needs
them.

**Confidence labels** (every row carries one; same scheme as TRAVEL_DEATH.md):

- **H**: current Outlands wiki page, official patch note, or our own capture/source reading.
- **M**: an Outlands wiki page that is old or conflicts with another page, or a count derived from
  noisy data.
- **L**: `[INFERENCE]`: generic RunUO/OSI behaviour (RunUO is the emulator lineage, but Outlands
  has heavily customised it), or my own reasoning.

Citations are reference-style links; wiki revision ids are in §9.

---

## 0. Summary

- **The harness never attacks a player.** Attacking a player gives us Heat of Battle (no recall,
  no inn room), flags us criminal against innocents, and draws staff attention — the exact
  detection risk the design avoids ([Test Shard][w-ts]). The PK and thief logic is detection and
  evasion, never retaliation.
- **Guards never protect a lumberjack.** Lumberjacking is blocked in town regions, apart from
  Shelter Island for Young players ([Lumberjacking][w-lj]). Guards exist only in guard zones (towns)
  ([Guard Zones][w-gz]). So every legal chopping spot is outside guard protection.
- **A red name isn't proof of a player killer.** Our captures show monsters and animals as grey (3)
  and mongbats turning criminal (4). Human-bodied NPCs like Brigands also exist in the Wilderness.
  Notoriety must be combined with body, name and behaviour (§1.4).
- **Recall from a threat spotted at the edge of view doesn't work against a mounted attacker.**
  Recall takes 2.00 s on Outlands ([SpellCircles][w-sc]). A mounted runner covers 10 tiles/s
  ([MovementSpeed.cs:9-12][c-ms]). The view range is 18 tiles (§3.1). The derived flee radius for a
  mounted spell caster is about 29–49 tiles (§3.4), which is beyond the view range. Early warning
  (Tracking, §2), being mounted, and carrying little are the real levers.
- **A thief can take about 200 boards per successful steal at 100 Stealing.** Logs and boards weigh
  0.025 stones each ([Harvesting][w-harv]). The steal amount per stack is
  `(5 · Stealing/100) / unit weight` ([Stealing][w-steal]), and the Stealing cooldown is 5 s. The
  counter is to keep other players out of reach (the RunUO steal range is 1 tile) and carry little.
- **Hidden players are invisible to the world model.** The server sends a remove packet to anyone
  who can't see them (RunUO). A player who vanishes nearby is itself a warning signal (§2.4).

---

## 1. PvP: notoriety and where players can attack

### 1.1 Notoriety codes (wire)

| Code | Name (ClassicUO / RunUO) | Client colour | Meaning on Outlands | Conf. | Source |
|---|---|---|---|---|---|
| 1 | Innocent / Innocent | blue ("cyan") | Ordinary player/NPC. Attacking one in a guard zone → instant guard whack | H | [NotorietyFlag.cs:10,47][c-noto], [Server/Notoriety.cs:33][ru-snoto], [Guard Zones][w-gz] |
| 2 | Ally / Ally | green | Guild/alliance member, party-like "green" | H | [NotorietyFlag.cs:11,49][c-noto] |
| 3 | Gray / CanBeAttacked | grey | Attackable by *you* (e.g. a thief to his victim for 2 min, an aggressor), and in our captures: all animals and monsters | H | [NotorietyFlag.cs:12,52][c-noto], [Thieves Guild][w-tg], §1.4 |
| 4 | Criminal / Criminal | grey | Criminal timer (2 min) running. Criminals flag grey even to guildmates | H | [NotorietyFlag.cs:13,51][c-noto], [Criminality][w-crim] |
| 5 | Enemy / Enemy | orange | Guild war enemy, faction enemy, ship captain | H | [NotorietyFlag.cs:14,54][c-noto], [Ships][w-ships] |
| 6 | Murderer / Murderer | red | ≥ 5 murder counts. Each count needs 72 h of logged-in time to decay | H | [NotorietyFlag.cs:15,56][c-noto], [Murder Counts][w-mc] |
| 7 | Invulnerable / Invulnerable | yellow | Invulnerable NPCs (vendors, bankers) | H | [NotorietyFlag.cs:16,58][c-noto]; captures (§1.4) |

**How notoriety reaches us on Outlands:**

| Fact | Conf. | Source |
|---|---|---|
| 0x20 (V10, 28 B) carries the notoriety at byte 9 for any mobile, and the harness stores it | H | [WORLDMODEL.md:161][d-wm], `harness/world/runtime.py:173-177` |
| The Outlands 0x77 MobileMove has **no** notoriety/flags field (upstream 0x77 has them: `PacketHandlers.cs:2808-2811`), and neither does 0x78 | H | [WORLDMODEL.md:216,326][d-wm] |
| Notoriety **changes** arrive as a re-sent 0x20: two mongbats (0x000A6A4B, 0x000A6A74) went 3 → 4 inside session 20260929_204225 | H | throwaway scan of the 27 `logs/session_*.s2c.raw` captures (this doc) |
| Our own notoriety comes in 0x22 ConfirmWalk byte 2 (`& 0xBF`; 0 or > 7 is coerced to 1) | H | [WORLDMODEL.md:181][d-wm], `harness/world/runtime.py:97-100` |

### 1.2 Flagging rules that matter to a gatherer

| Fact | Conf. | Source |
|---|---|---|
| Off Shelter Island, "players are able to freely attack or steal from another player (almost anywhere)" | H | [New Player Guide][w-npg] |
| On Shelter Island, players can't perform hostile actions against other players | H | [Shelter Island][w-si] |
| Guard zone: attacking a blue → instant guard whack. A crime in town is guard-whackable for 30 s if someone calls guards. A criminal from outside can enter town and is whackable for 30 s after the last crime | H (page dated 2020) | [Guard Zones][w-gz] |
| Saying "Guards" in a guard zone kills an attacker; for a thief only if the theft was noticed | H | [New Player Guide][w-npg] |
| Criminal flag: 2 min, grey to everyone, not wiped by death | H (page dated 2020) | [Criminality][w-crim] |
| Looting a blue corpse makes you criminal; `[PreventCriminalLooting` and `[PreventCriminalHealing` toggle protection against doing it by accident | H | [New Player Guide][w-npg], [Commands][w-cmd] |
| Murderers: 5+ counts = red. Murder fee 2000 gp + 500 per accomplice on resurrection. Region restrictions after death; the **Wilderness counts as a dungeon** for restrictions, at 50 % duration | H | [Murder Counts][w-mc] |
| Warning Ravens and Blackmarks only apply to dungeon levels 1–2 (not the Wilderness) | H | [Murder Counts][w-mc], [Patch 2025-01-25][p-2501] |
| Lawless (everyone grey) zones: the seas, Corpse Creek, Daemon Keep, Strangelands, the Omni Boss lair | H | [Ships][w-ships], [PvP Events][w-pvpe], wiki search "lawless" / "grey zone" |
| A weekly **Sanctuary Dungeon** forbids murder, thieving and griefing | H | [Dungeons][w-dung] |
| Mounts are overworld-only; entering a dungeon auto-stashes the mount | H | [Mounts][w-mounts] |
| Heat of Battle comes from *your own* aggressive action against a player (attack, steal, harmful spell, re-targeting your attacker). Automatic retaliation swings don't count. It blocks recall, gate, moongates and entering an inn room | H | [Heat of Battle][w-hob] |

**Consequence:** a victim who never acts against the attacker keeps recall available. Our policy of
never retaliating against players is also the policy that keeps the escape open.

### 1.3 Typical PK tactics against gatherers

| Tactic | Mechanics | Conf. | Source |
|---|---|---|---|
| **Hunt with Tracking.** Hunting mode "Innocent Players" / "All Players" auto-checks and puts an arrow on the target; 100 tiles at GM Tracking (50 in dungeons) | Finds a lumberjack far beyond our 18-tile view range | H | [Tracking][w-track] |
| **Arrive mounted.** 10 tiles/s mounted run vs 5 on foot | Closes an 18-tile gap in < 2 s | H (client constants), L (Outlands server) | [MovementSpeed.cs:9-12][c-ms] |
| **Paralyze then damage.** Paralyze (5th circle, 1.50 s cast) holds a player for `10 · Magery/100` s (10 s at GM); you can't cast while paralyzed (RunUO) | Stops both running and recall | H / L | [SpellCircles][w-sc], [RunUO Spell.cs:506][ru-spell] |
| **Interrupt the escape.** Hostile spells of circle 4–8 always interrupt; circles 1–3 interrupt on the first hit, then open a 5 s immunity window per circle; a melee hit interrupts a cast | Our 2 s recall has to finish before the first hit | H | [Magery][w-mag], [Wrestling][w-wres] |
| **Hamstring (melee, 80+ weapon skill).** Victim's stamina set to 0 for 3 s: forced to walk (2.5 tiles/s) | Kills the on-foot run escape | H | [Hamstring][w-ham] |
| **Teleport / Adventurer's Rope.** 10 tiles in line of sight; 15 s (spell) / 2 min (rope) cooldown when PvP-flagged | Adds up to 10 tiles to an attacker's reach once | H | [SpellCircles][w-sc], [Adventurer's Rope][w-rope] |
| **Stealth ambush.** Hidden players are invisible; stealth gives `5 + 10 · Stealth/100` steps per activation; running reveals | Arrives without any sighting | H | [Stealth][w-stealth], [Hiding][w-hide] |
| **Recall in / gate in** next to a tracked target | The mobile appears inside view range without walking in (§2.4) | L | [INFERENCE] from Tracking + recall mechanics |
| **Camp chokepoints** (moongates, bridges, passes) | Blocking moongates with objects is griefing; standing near them isn't | L | [Moongates][w-mg] griefing text; "Chokepoints" isn't an Outlands wiki term (full-text search: 0 hits) |

---

## 2. Tracking and Detect Hidden

### 2.1 Tracking on Outlands (wiki, rev 31329)

| Fact | Conf. | Source |
|---|---|---|
| Success chance = Tracking / 100 | H | [Tracking][w-track] |
| Range for visible targets = `20 + 80 · Tracking/100` tiles; halved in dungeons | H | [Tracking][w-track] |
| Hidden targets: base hidden range = 10 % of the normal range (10 tiles at 100 Tracking). Detect Hidden adds `40 · DH/100` tiles (halved in dungeons): 30 tiles at DH 50, 50 at DH 100 | H | [Tracking][w-track] |
| Inside a house, results show only mobiles in that house | H | [Tracking][w-track] |
| Not moving for 5 min → cumulative −10 % success per further minute. [INFERENCE] this applies to the tracker (the sentence says "a player") | H / L | [Tracking][w-track] |
| **Hunting mode**: automatic checks (normal 5 s skill cooldown) against one category: Aggressive Creatures, Passive Creatures, Townsfolk, All Players, All Hostile Players, Enemy, Murderer, Criminal, Innocent, Friendly Players | H | [Tracking][w-track] |
| Hunt frequencies: Current Combatant, Always Get Closest, Follow Only Individual, New When No Arrow, New When No Target | H | [Tracking][w-track] |
| A new hunt hit places the tracking arrow and a **system message naming what is tracked and how many spaces away** | H | [Tracking][w-track] |
| Arrow on a **player**: points to the position at the time of the check and doesn't update. On a **creature**: updates every second | H | [Tracking][w-track] |
| Right-click cancels the arrow; the client has a "Tracking Arrow Max Range" option | H | [Tracking][w-track], [Options][w-opt] |
| Tracking also gives damage bonuses (PvM 25 %, PvP 10 % of base weapon damage at GM) | H | [Tracking][w-track] |

Worked ranges (visible / hidden without DH):

| Tracking | Success | Range (overworld) | Hidden range |
|---|---|---|---|
| 0 | 0 % | 20 | 2 |
| 50 | 50 % | 60 | 6 |
| 80 | 80 % | 84 | 8.4 |
| 100 | 100 % | 100 | 10 |

### 2.2 Effective warning distance (derived)

A PvP hunt result is a snapshot: the arrow doesn't move. With checks every Δ = 5 s at success
probability p, a threat moving at v tiles/s can close about `v · Δ / p` tiles between the moment it
enters the Tracking range and the expected first hit. So the effective warning distance is roughly

    R_warn ≈ R_T − v · Δ / p          (R_T = 20 + 80·T/100, p = T/100, Δ = 5 s)

| Tracking | R_T | vs mounted (v = 10) | vs on foot (v = 5) |
|---|---|---|---|
| 60 | 68 | 68 − 83 → **no gain** | 68 − 42 = 26 |
| 80 | 84 | 84 − 63 = 21 | 84 − 31 = 53 |
| 100 | 100 | 100 − 50 = 50 | 100 − 25 = 75 |

**L** (derivation; assumes the hunt check interval equals the 5 s cooldown and a straight-line
approach). Only about 90+ Tracking gives a real margin over the 18-tile view range against a
mounted player. Against foot traffic and monsters, 60–80 already helps.

### 2.3 How the client shows it (protocol)

| Fact | Conf. | Source |
|---|---|---|
| Skill use is C2S 0x12 with type byte 0x24 and ASCII `"<skill id> 0"` | H | [OutgoingPackets.cs:1134-1160][c-out] |
| Legacy text/graphic menu **0x7C OpenMenu** (registered at [PacketHandlers.cs:238][c-ph]): u32 serial, u16 menu id, u8-prefixed ASCII title, u8 count, then per entry u16 graphic, u16 hue, u8-prefixed ASCII name. If the first graphic is 0 it's drawn as a "gray menu" of text rows | H | [PacketHandlers.cs:2956-3057][c-ph] |
| Menu answer: C2S **0x7D** `serial, menu id, index (1-based), graphic, hue` (`Send_MenuResponse`) or `serial, menu id, code` for gray menus | H | [OutgoingPackets.cs:1354-1400,1402-1433][c-out] |
| **0xBA DisplayQuestArrow** (registered [PacketHandlers.cs:263][c-ph]): bool display, u16 x, u16 y, and from client 7.0.9.0 a u32 serial; display=0 removes the arrow | H | [PacketHandlers.cs:4042-4075][c-ph] |
| On Outlands 0xBA is a fixed **14 bytes** (upstream 10) | H | [PROTOCOL.md:8][d-proto] |
| [INFERENCE] The +4 bytes are x and y widened to u32, the same widening Outlands applied in 0x20 (1 + 1 + 4 + 4 + 4 = 14). Needs a capture | L | [WORLDMODEL.md:155-164][d-wm] pattern |
| RunUO tracking is gump-based (`TrackWhatGump` → `TrackWhoGump`) plus a `QuestArrow`, not 0x7C; the arrow timer re-sends the target position every 2.5 s | L | [RunUO Tracking.cs:191,316,368][ru-track] |
| [INFERENCE] The Outlands Tracking window (wiki screenshot: category arrows, hunt frequency) is a server gump (0xB0 / compressed 0xDD), and the arrow is 0xBA. The harness already parses 0xDD gumps and 0x1C/0xAE/0xC1/0xCC messages, but **not 0xBA or 0x7C** | L | [PROTOCOL.md:90][d-proto], [LUMBER_LOOP.md §8][d-lumber] |

### 2.4 Hidden players and what the world model can see

| Fact | Conf. | Source |
|---|---|---|
| When a mobile hides, the server sends its **remove packet** to every client that can't see it; clients that can see it get a fresh "mobile incoming" | L | [RunUO Mobile.cs:7994-8006][ru-mobile] |
| Flag bit 0x80 = Hidden in the mobile flags byte | H | [EntityFlags.cs:19][c-flags] |
| None of the 96 mobiles seen via 0x20 in our 27 captures had the Hidden bit | H | throwaway capture scan (this doc) |
| 0xC4 Semivisible has an empty handler in ClassicUO | H | [PacketHandlers.cs:271,4860][c-ph] |
| View (update) range: the server sent 0xC8 = 18. First-sighting distances in our captures peak at 18 (47 of 492), but the tail runs up to 96, so they're noisy (login bursts and dead-reckoned self position) | H / M | [PROTOCOL.md:90][d-proto], throwaway capture scan |

**Usable signals [INFERENCE]:**

- **Vanish:** a player-bodied mobile disappears (0x1D) while clearly inside the view range and not
  walking out of it. That means hide, recall, logout or death.
- **Appear:** a player first appears well inside the view range (for example ≤ 12 tiles) instead of
  at the edge. That means recall/gate-in, unhide, or resurrection.

### 2.5 Detect Hidden and Reveal

| Fact | Conf. | Source |
|---|---|---|
| Detect Hidden: success = skill %, reveals all hidden within `8 · DH/100` tiles of the target point (or self) | H | [Detecting Hidden][w-dh] |
| Works at 0 skill inside a house; minimum 80 for tinker traps | H | [Detecting Hidden][w-dh] |
| A revealed player can't hide again for 5 s (10 s if revealed by a creature ability) | H | [Hiding][w-hide] |
| Reveal spell: radius `3 · Magery/100`, 6th circle, 4.00 s cast | H | [SpellCircles][w-sc] |
| Hiding needs to be out of sight of mobiles; chance = skill %. Running always reveals a stealther | H | [Hiding][w-hide], [Stealth][w-stealth] |
| Reveal immunity (60 s after chest progress) never applies to murderers, criminals or Red Hand thieves | H | [Patch 2025-01-25][p-2501] |

---

## 3. Escape math

### 3.1 Inputs

| Quantity | Value | Conf. | Source |
|---|---|---|---|
| Step time | on foot: walk 400 ms, run 200 ms; mounted: walk 200 ms, run 100 ms → **2.5 / 5 / 5 / 10 tiles/s** | H (client), L (server) | [MovementSpeed.cs:9-25][c-ms] |
| The Outlands server accepts on-foot running at 0.2 s/step (the harness's enforced agent pacing) | H | [MOVEMENT.md:64][d-move] |
| Distance metric: diagonal steps cost one step, so reach is Chebyshev distance `max(|dx|,|dy|)` | L | [INFERENCE], 8 directions in [MovementSpeed.cs:34-103][c-ms] |
| Recall cast time **2.00 s** (4th circle default 1.25 s) | H | [SpellCircles][w-sc] |
| Magery cast recovery 0.2 s | H | [Magery][w-mag] |
| Melee hit interrupts a cast | H | [Wrestling][w-wres] |
| Hostile spells circle 4–8 always interrupt; circles 1–3 the first time (then a 5 s window) | H | [Magery][w-mag] |
| Creature/environment damage interrupts unless a `Resist/100` roll succeeds | H | [Resisting Spells][w-rs] |
| RunUO: damage also cancels a spell whose **target cursor** is up (Sequencing state) | L | [RunUO Spell.cs:408-448][ru-spell] |
| RunUO: you can't move while casting (`BlocksMovement` defaults to true) | L | [RunUO Spell.cs:200-204,479][ru-spell] |
| Outlands client has a "Block movement while casting" option, so moving while casting is **not settled** for Outlands | M | [Options][w-opt], [TRAVEL_DEATH.md §1.2](TRAVEL_DEATH.md) |
| Spell target range: RunUO uses 12 tiles (10 under ML rules) | L | [RunUO Paralyze.cs:95][ru-para] |
| Damage delays: Harm 0.05 s, Lightning 0.25 s, Magic Arrow / Fireball / E-bolt 0.5 s, Explosion 2.5 s | H | [SpellCircles][w-sc] |
| Precasting (holding a finished spell) works on Outlands | H | [Combat Overview][w-co] |
| Can't start a cast while paralyzed / frozen | L | [RunUO Spell.cs:506][ru-spell] |
| Monster speeds: RunUO standard active speeds 0.1–0.8 s/tile (10 down to 1.25 tiles/s); Outlands values unpublished | L | [RunUO BaseCreature.cs:1864-1867][ru-bc] |
| View range 18 tiles | H | §2.4 |

### 3.2 Model

Worst case: the attacker comes straight at us, precast, with no reaction lag of their own. We are
standing still chopping when we detect them at Chebyshev distance `D`.

- `t_r`: our reaction, from the packet to the start of the cast. It includes the humanised reaction
  delay and opening the runebook / pressing the rune's recall button.
- `T_c`: cast time (2.0 s). With the spellbook-plus-rune flow a target step `t_s` follows (≈ 0.5 s)
  and is also vulnerable (L). With the runebook button it's 0.
- `v_a`: attacker speed. `v_c`: our speed while casting (0 if frozen, else our run speed).
- `r_a`: attacker reach (melee 1; spell ≈ 12, L; +10 once if they teleport).
- `δ_a`: time from reaching range to the interrupting hit (precast damage delay; ~0 for Harm or an
  instant melee swing).

Recall succeeds iff the attacker can't land the interrupt before we finish:

    D − r_a > v_a·t_r + max(0, v_a − v_c)·(T_c + t_s) − v_a·δ_a

so the **flee radius** (distance at which we must already be casting) is

    R_flee = r_a + v_a·t_r + max(0, v_a − v_c)·(T_c + t_s) − v_a·δ_a + m

with a safety margin `m` of 2 tiles. The `max(0, ·)` is there because if we out-run the attacker
while casting, the gap is smallest at the moment the cast starts.

### 3.3 Assumptions

1. Straight approach, clear terrain, and the attacker notices us at once (worst case; real humans
   add 0.3–1 s).
2. Hit chance 100 % (worst case; melee can miss). Wrestling defence odds are on the
   [Wrestling][w-wres] page.
3. Detection at the first 0x20 for that mobile (packet latency ignored).
4. `t_r = 1.5 s` (1.0 s humanised reaction + 0.5 s to press the runebook button), `T_c = 2.0`,
   `t_s = 0`, `δ_a = 0`.
5. No Magic Reflect or Protection effects on our side.

### 3.4 Numbers

| Threat | r_a | v_a | we frozen (v_c = 0) | we run mounted while casting (v_c = 10) | we run on foot while casting (v_c = 5) |
|---|---|---|---|---|---|
| Mounted mage PK (precast) | 12 | 10 | **49** | 29 | 39 |
| Mounted melee PK | 1 | 10 | **38** | 18 | 28 |
| On-foot mage | 12 | 5 | 31.5 | 21.5 (gap grows) | 21.5 |
| On-foot melee | 1 | 5 | **20.5** | 10.5 | 10.5 |
| Fast monster (0.2 s/tile), melee | 1 | 5 | 20.5 | 10.5 | 10.5 |
| Slow monster (0.4 s/tile), melee | 1 | 2.5 | 11.75 | 6.75 | 6.75 |
| Monster archer/mage | ~12 | 2.5–5 | 23–31.5 | 18–21.5 | 18–21.5 |

**L** (derivation from the inputs above).

Reading the table:

- With the 18-tile view range, recall-on-sight is safe only against slow monsters and, at the
  margin, on-foot melee. Against anything mounted, casting from standing still fails in the worst
  case.
- Whether Outlands lets you run while casting changes the answer by 20 tiles. This is the most
  valuable single fact to capture.
- Levers, in order: earlier detection (Tracking ≥ ~90, §2.2); be mounted; lower `t_r` (the runebook
  button ready, reaction not padded past human norms); treat any unknown player approaching as a
  threat, not only reds; carry little so a failed escape costs little (LUMBER_LOOP §6).
- The hazard `h` in LUMBER_LOOP §6 should use `P(escape fails | sighting)`, derived from this table
  per threat class, rather than a flat number.

### 3.5 Escape-mode choice (derived, L)

1. `D ≥ R_flee(class)` → recall now.
2. `D < R_flee` and we're mounted → run directly away, preferring routes that break line of sight.
   The gap holds against a mounted attacker and grows against foot traffic. Cast once the estimated
   gap is ≥ `R_flee` or the threat has been out of view for ≥ 2 s.
3. `D < R_flee`, on foot, attacker mounted → no winning move. Cast immediately; the attacker may
   hesitate. If interrupted, run and re-cast after the disturb recovery.
4. Paralyzed → nothing to do until it wears off (`10 · Magery/100` s). A trapped pouch breaks
   paralyze ([New Player Guide][w-npg]), but that is a combat item; it's a user decision (§8).
5. Never attack back. That keeps Heat of Battle off and recall/inn access open
   ([Heat of Battle][w-hob]).

After escaping, the field has a 60 s harvest lockout after recall ([Harvesting][w-harv]), plus the
Stationary Harvest Penalty: after a recall, or 5 min standing still, lumberjacking fails until you
walk 5 steps ([Patch 2025-01-25][p-2501], **H**).

---

## 4. Thieves

### 4.1 Stealing and Snooping on Outlands

| Fact | Conf. | Source |
|---|---|---|
| Stealing from **blue** players requires **Red Hand** Thieves Guild membership (80 Stealing to join). Grey Hand steals from creatures only | H | [Stealing][w-steal], [Thieves Guild][w-tg] |
| Players outside the guild may still steal from anyone who flags green, orange, grey or red *to them* | H | [Thieves Guild][w-tg] |
| 80 Snooping is required for any steal. Success = Stealing/100; +25 % vs Disarmed or Hamstrung targets | H | [Stealing][w-steal] |
| Stealing cooldown 5 s (June 2024). Stealing counts as "using" an item: 500 ms item delay | H | [Stealing][w-steal] |
| Max weight of a single stolen item = `10 · Stealing/100` stones | H | [Stealing][w-steal] |
| From a stack: `(5 · Stealing/100) / unit weight × U(0.9, 1.1)` items per success | H | [Stealing][w-steal] |
| Logs and boards weigh 0.025 st each | H | [Harvesting][w-harv] |
| **→ Boards per successful steal: 200 at 100 Stealing (180–220), 160 at 80.** One steal every 5 s | H (arithmetic on H facts) | derived |
| Commodity deeds are not blessed and can be stolen/looted. [INFERENCE] a deed is one light item, so one successful steal can take the whole deed | H / L | [Harvesting][w-harv] |
| "Prevent Non-Friendly Lift" items can never be stolen: Arcane Rune Tome, Lumber Map Tome, Storage Shelf, Resource Stockpile and others | H | [Stealing][w-steal] |
| RunUO: newbied/blessed items can't be stolen; the thief must be within **1 tile** of the item's world location | L | [RunUO Stealing.cs:42,99,181,189][ru-steal] |
| Snooping: success = Snooping/100; players within 8 tiles get "You notice X … peek into Y's belongings" with chance `100 % − 75 % · Snooping/100`; **no message if the snooper is hidden**; 1 s snoop cooldown; needs line of sight | H | [Snooping][w-snoop] |
| Snooping no longer flags the snooper grey | H | [Thieves Guild][w-tg] |
| Trapped pouch: a snoop on a non-empty trapped pouch explodes it (notice of a thief); snooping an empty one is blocked | H | [New Player Guide][w-npg], [Snooping][w-snoop] |
| Stolen items can't be lifted by anyone for 30 s | H | [Stealing][w-steal] |
| No stealing or snooping for 2 min after the thief's death | H | [Stealing][w-steal] |

### 4.2 What happens to the thief

| Fact | Conf. | Source |
|---|---|---|
| A steal attempt (success or fail) makes the thief **grey/attackable to the victim only** for 2 min; it is **not** a criminal action by default | H | [Thieves Guild][w-tg] |
| It becomes criminal if noticed by any player or invulnerable NPC (outside houses) | H | [Thieves Guild][w-tg] |
| Notice chance: +10 % per non-friendly player within 8 tiles (5 % without LOS), +5 % per human NPC (2.5 % without LOS), plus town Suspicion | H | [Stealing][w-steal] |
| Noticed outside town → just a system message. In town → NPCs call guards; players can call guards within 30 s from ≤ 14 tiles | H | [Stealing][w-steal] |
| A Red Hand thief turns grey (not criminal) to anyone who attacks them, and can't report murders | H | [Thieves Guild][w-tg] |
| Stealing gives the thief Heat of Battle: 2 min vs blue targets, 30 s vs grey/red/orange, so **the thief can't recall away** for that time | H | [Thieves Guild][w-tg] |
| Thieves can now **lockpick locked containers inside another player's backpack**, and nobody can drop items into a locked container in their own backpack | H | [Stealing][w-steal], [Thieves Guild][w-tg] |
| The New Player Guide still says a locked box protects its contents "as long as it remains locked". This conflicts with the lockpicking rule above | M | [New Player Guide][w-npg] |

### 4.3 Counter-strategies

| Option | Assessment | Conf. |
|---|---|---|
| **Attack a grey thief** | Mechanically possible (grey to the victim for 2 min). But it gives us Heat of Battle: no recall, no inn room ([Heat of Battle][w-hob]), and PvP draws staff attention. **Rejected.** | H |
| **Move away** | The RunUO steal range is 1 tile (L). Keeping every non-friendly player ≥ 2 tiles away denies stealing entirely. Cheap and human-like | L |
| **Recall away** | Ends the encounter. A thief with Heat of Battle can't recall after us (2 min). Costs the 60 s lockout plus 5 steps | H |
| **Guards** | Useless where we chop (no guard zones), §1.2 | H |
| **Locked pack container** | Can't drop into a locked container, and thieves can lockpick them. Low value | H |
| **Carry less** | ~200 boards per steal at 100 Stealing: carried boards above a few hundred are at risk within seconds | H (arithmetic) |
| **Don't carry deeds** | A deed is probably one light item: one steal takes it all. Make deeds in the room (secure container) | L |
| **Unstealable travel item** | Keep the recall source unstealable: blessed runebook or rune tome, or the Arcane Rune Tome (Prevent Non-Friendly Lift) | H (list), L (blessed) |
| **Trapped pouch as tripwire** | Exploding pouch = thief alert, and it breaks paralyze. Requires setup; user decision | H |

---

## 5. Monsters in the overworld

### 5.1 What is documented

| Fact | Conf. | Source |
|---|---|---|
| 259 pages in Category:Wilderness; 257 parsed creature infoboxes. AI types: Melee 163, Mage 39, Ranged 28, MeleeMage 27. Difficulty deciles: 26, 39, 46, 55, 68, 85, 107, 137, 195 | H | wiki Category:Wilderness via the MediaWiki API (throwaway scan, this doc) |
| **Stealthed Wilderness creatures** (infobox "Stealth" column): Brigand Ambusher 37.5 (ranged), Arboreal Watcher 42.4 (ranged), Arboreal Stalker 43.5, Bushwhacker 71.4, Drow Blademaster 71.6, Drow Ranger 76.6, Sandstalker 103.3, Minotaur Skirmisher 132.4, Magma Serpent 161.7, Phase Spider 1022, Abyssal Assassin 1491, Lich Primarch 1619. [INFERENCE] invisible until they attack, like hidden players | H / L | same scan, e.g. [Brigand Ambusher][w-bamb], [Bushwhacker][w-bush] |
| Forest/lumber-themed and road spawns (difficulty, melee damage per hit): Corpser 12.2 (10–20), Skeletal Woodsman 26.4 (20–30), Brigand Footman 29.7 (30–40), Ghostly Lumberjack 34.1 (40–50), Arboreal Defender 38.5 (40–50), Brigand Leader 39.9 (50–60), Troll 44.2, Ogre 46.8, Arboreal Warden 47.3 (ranged), Brigand Hedge Mage 51.7 (mage), Horsethief 66.2 (ranged, 70–80), Brambler 79.2, Arboreal Tree-Tongue 84.4 (mage), Bridge Troll 90.4, the Gloomwood family 93–147, Ancient Oak 131.2 | H | same scan, e.g. [Horsethief][w-horse] |
| Animals near the bottom (sheep 0.4, great hart 1.1, mongbat 2.1, wolf 4.4) | H | same scan |
| Official live stats (hits, skills, difficulty, gold) spreadsheet, linked from the Jan 2025 rebalance patch; "most wilderness spawns" were buffed then | H | [Patch 2025-01-25][p-2501] |
| An aggro range exists as a creature parameter (Pit Trials list "Creature Aggro Range +20" as a wave modifier); the base value isn't published | H | [Pit Trials][w-pit] |
| RunUO default perception range 16 tiles; `FightMode.Aggressor` creatures only fight back; others acquire players on approach | L | [RunUO BaseCreature.cs:1671][ru-bc] |
| Tracking distinguishes "Aggressive Creatures" from "Passive Creatures" (so the server has that classification) | H | [Tracking][w-track] |
| Pilfering says "you cannot pilfer non-aggressive creatures (Goats/Bulls/etc)" | H | [Thieves Guild][w-tg] |
| Weekly region bonus can give a region +25 % faster respawn; Wilderness and Ocean are separate bonus regions | H | [Region Bonus Board][w-rbb] |
| Gold value of a spawn grows the longer it sits (Progressive Loot Bonus). [INFERENCE] spawns persist at their spot until killed | H / L | [Thieves Guild][w-tg] |
| Creature damage interrupts our casts unless a Resisting Spells roll succeeds (`Resist/100`) | H | [Resisting Spells][w-rs] |
| "Chokepoints": no Outlands wiki page or text match. Not an Outlands mechanic as far as the wiki shows | H (absence) | wiki full-text search 2026-09-29 |

### 5.2 What our captures show (Shelter Island only)

| Body (graphic) | Name | Notoriety | Conf. |
|---|---|---|---|
| 3 | a zombie | 3 | H |
| 5 / 6 | seagull, eagle / crow | 3 | H |
| 30 | a wounded harpy | 3 | H |
| 39 | a mongbat | 3, then 4 (two serials, session 20260929_204225) | H |
| 207 / 234 / 237 | sheep / great hart / hind | 3 | H |
| 400 / 401 | townsfolk and vendors | 7 (most); 3 for three female NPCs (Riane, Evangeline, Gana), meaning unknown | H |
| 400 | other players (e.g. "Farmforfarm", "Vorn") | 1 | H |

Source: throwaway replay of the 27 `logs/session_*.s2c.raw` captures with `harness/replay.py`
(`replay_session`, final world state per capture) and a direct 0x20 scan for changes.

### 5.3 Implications (L)

- Monsters aren't red here, so **red + human body (0x190/0x191)** is a strong player-killer signal on
  Shelter. The overworld has human-bodied NPC enemies (the Brigand family, Horsethief, Drow). Their
  notoriety is unknown until captured, so name and behaviour must disambiguate (§7, T1).
- Monster speed and aggro radius per species should be **learned from captures** (distance when the
  creature starts closing, step interval from 0x77 timestamps) and stored per graphic in memory,
  not guessed.
- Choose chopping spots by the spawn seen there: the job log records every aggressive mobile seen
  per spot, and spots with stealth or ranged spawns rank lower.

---

## 6. Detection and self-defence policy

| Policy / detection intel | Consequence for the harness | Conf. | Source |
|---|---|---|---|
| Staff finding a gatherer unresponsive for ~2 min while gathering → jail | Captcha is human-solved by default: the runner pauses and beeps (user decision 2026-10-01). Auto-solve from the layout's tilepic glyphs runs when the viz toggle says `auto` (ANTICHEAT §8.8) | H | [ANTICHEAT.md:146][d-ac] |
| In-game speech only from allowlisted keywords | "Guards" could be allowlisted, but it's useless outside towns | H | [PLAN.md:81][d-plan] |
| Griefing detection: trapping players and blocking moongates | Our flight paths must never park in a moongate or doorway | H | [Moongates][w-mg] |
| Human-like inefficiency in every runner | Flee reactions use the humanised reaction delay: not instant, not padded past human norms | H | [PLAN.md:83][d-plan] |
| Test commands `[TestRes`, `[TestBlessedGear`, `[Go` exist | They'd mask the real death costs. User decision (§8) | H | [Test Shard commands][w-tscmd] |

---

## 7. Recommended harness behaviours

All distances are Chebyshev tiles. Every parameter is a default for the user or the optimiser to
tune. All decisions post junctures through `Memory.juncture(source="threat", …)`.

**T1: threat classifier** (a pure function of the state snapshot; unit-testable)

- `HUMAN_BODIES = {0x190, 0x191}`. Add elf 0x25D/0x25E and gargoyle bodies only if seen on
  Outlands.
- `player_like(m)`: human body, notoriety ≠ 7, and not a known NPC. A known NPC is a name/title in
  the speech labels (e.g. "Len the banker"), a serial previously seen with notoriety 7, or a name
  matching the Wilderness creature list (§5.1).
- Classes:
  - `pk`: `player_like` and notoriety 6.
  - `hostile_player`: `player_like` and notoriety 3/4/5.
  - `unknown_player`: `player_like` and notoriety 1/2.
  - `monster`: non-human body and not in `PASSIVE_BODIES`.
  - `animal`: `PASSIVE_BODIES`, initially {0xCF sheep, 0xEA great hart, 0xED hind, birds 5/6}.
  - `npc`: notoriety 7 or a known NPC.
- `STEALTH_SPAWN_NAMES`: the §5.1 list. If the job log shows one near a spot, rank that spot lower.

**T2: flee radius per class** (§3.2 formula, parameters in one table)

- `t_r = 1.5 s`, `T_c = 2.0 s`, `t_s = 0` (runebook), `m = 2`.
- `v_a`: 10 if the threat's measured step interval is < 150 ms (mounted), else 5. Monsters use the
  learned per-graphic speed, default 5.
- `r_a`: 12 for pk/hostile_player (assume a caster), 1 for melee monsters, 12 for Mage/Ranged AI
  monsters.
- `v_c = 0` until moving while casting is proven (§9, D4).
- The derived `R_flee`: pk mounted 49 (!), pk on foot 31.5, fast melee monster 20.5, slow monster
  11.75.

**T3: reactions**

| Trigger | Action | Juncture |
|---|---|---|
| `pk` seen at any distance | Escape per §3.5 (recall if `D ≥ R_flee`, else run first if mounted, else cast at once). Never target it | urgent |
| `hostile_player` within 18 | Same as pk | urgent |
| `unknown_player` closing: distance falls on 3 consecutive updates and `D ≤ R_CAUTION = 12` | Stop chopping, face away, ready the runebook. If `D ≤ 6` → recall | attention |
| Any non-friendly player within `STEAL_GUARD = 2` for ≥ 1 s | Step away to ≥ 4 tiles (humanised). If it follows twice → recall | attention |
| System/cliloc "You notice … peek / steal" naming us, or pack item count drops unexplained | Recall; don't return to the spot for `THIEF_COOLDOWN = 20 min` | urgent |
| Player vanishes at `D ≤ 14` (0x1D not explained by walking out) | Treat as possible hider: leave the spot (walk ≥ 20 tiles) or recall; spot hazard +1 | attention |
| Player appears at `D ≤ 12` | Same as `pk` if red, else as closing `unknown_player` | attention |
| Damage to self (0xA1 hits drop, or 0x0B on our serial) from an unseen source | Recall immediately (stealth attacker) | urgent |
| Aggressive monster within its learned aggro radius (default 12) and closing | Walk away perpendicular to its approach. If `D < R_flee(monster)` or HP < `HP_RECALL = 60 %` → recall | info / attention |
| Paralysis (no confirms for walk requests, or a paralyze message) | Wait; post juncture; no inputs spam | urgent |
| Death (0x2C / ghost body) | Stop; hand to the death flow in TRAVEL_DEATH.md §5 | urgent |

**T4: exposure limits**

- `MAX_CARRY_BOARDS_OVERWORLD = 400`: two steals' worth at GM Stealing. Combine with Q* from
  LUMBER_LOOP §6 as `min(Q*, cap)`.
- No deeds carried; deeds are made in the room.
- The recall source must be unstealable (blessed runebook/tome or the Arcane Rune Tome).
- Before leaving Shelter the human sets `[PreventCriminalLooting` and `[PreventCriminalHealing` on,
  so the agent can't flag itself grey by accident.
- After any recall: walk ≥ 5 steps, then wait out the 60 s lockout before chopping. Move ≥ 5 steps
  every < 5 min while chopping (Stationary Harvest Penalty; also the Tracking stand-still penalty).

**T5: Tracking (if the template has it)**

- Hunting mode on "All Hostile Players" (or "Murderer Players"), frequency "New When No Arrow".
  Parse the system message (target and distance) and 0xBA. Treat a hit as a sighting at that
  distance.
- Only worth the skill points at Tracking ≥ ~90 against mounted threats (§2.2).

**T6: hazard learning** (feeds LUMBER_LOOP §6 `h`)

- Log every sighting per spot and class: `job_event(job="lumber", kind="threat_seen",
  data={class, D, noto, graphic, v_est})`.
- Log escapes and their outcome: `kind="escape"`, `data={mode, D, R_flee, ok, interrupted}`.
- Use the per-class observed `P(escape fails)` rather than the worst-case table once ≥ 10 escapes
  are logged.

---

## 8. Open questions for the user

1. **TestWorth's template for the overworld:** Magery (own recall vs scroll charges), Tracking, Detect
   Hidden, Resisting Spells, Hiding? Tracking only pays off at ~90+ against mounted threats (§2.2).
   Resisting Spells directly reduces monster interrupts.
2. **Mount:** get a horse before going to the overworld? A horse can be ridden regardless of Taming
   ([Mounts][w-mounts]). Being mounted cuts the flee radius vs mounted threats by up to 20 tiles
   (§3.4).
3. **Unknown blue players:** flee on approach (safer, costs yield and looks skittish) or only on red
   or grey? Observed PvP on the Test Shard is rare, so the base rate is low.
4. **Combat items:** may the agent carry and use trapped pouches, potions, or an Adventurer's Rope as
   escape tools?
5. **Monsters:** is the agent ever allowed to fight monsters (for example weak animals that
   aggress), or does it always avoid and recall?
6. **Test commands:** may it use `[TestRes` / `[TestBlessedGear` after death, or must it do the
   realistic recovery? (Shared with TRAVEL_DEATH.md Q6.)
7. **Region:** which overworld forest first? The spawn list per region decides whether the
   stealth-spawn and Brigand logic matters.
8. **Consenting helper:** can you (or a friend on a second account) stage a thief/red approach for
   the captures below? Staged PvP would put Heat of Battle on both parties and pollute the capture,
   so script the encounter carefully.

---

## 9. Demos / captures needed

All on the Test Shard with the proxy recording and the user playing. None involves attacking
anyone.

| # | Capture | Answers |
|---|---|---|
| D1 | Walk up to a Brigand/Horsethief (Wilderness) and a red player if one is around; record 0x20 | NPC vs player notoriety; name/title patterns; the T1 classifier |
| D2 | A friend hides, unhides and recalls away at ~8 tiles | 0x1D on hide/recall, 0x20 on reveal; the vanish/appear signals (§2.4) |
| D3 | Use Tracking (menu and Hunting mode) | Gump ids and layout, the result message text, 0xBA bytes (confirm the 14-byte layout) |
| D4 | Cast Recall from a runebook: once standing, once while pressing run keys | Cast start → teleport latency; **whether you can move while casting on Outlands** (±20 tiles in `R_flee`) |
| D5 | Let a weak monster hit you during a recall | Interrupt message (cliloc), disturb recovery, Resist roll |
| D6 | Ride and run a straight line; watch another rider | Mounted step interval server-side (0x22 timing) and in others' 0x77 timestamps |
| D7 | Walk away from a stationary NPC until it disappears, then back | True view range (0x1D / 0x20 distance), to replace the noisy §2.4 estimate |
| D8 | Approach aggressive Wilderness spawns one at a time | Distance at which each starts closing (aggro radius) and its step interval (speed) |
| D9 | Consenting thief snoops/steals a board stack (only if staff-sanctioned) | "You notice …" message ids, the amount taken, grey flag timing |
| D10 | Get flagged by a friendly aggressor (consensual) | Heat of Battle buff (0xDF) as seen by the victim vs the aggressor |

---

## 10. Sources (read 2026-09-29)

Wiki pages were read through the MediaWiki API (`action=parse&prop=wikitext`, and `prop=text` for
creature infoboxes); `rev` is the current revision id at reading time.

[w-track]: https://wiki.uooutlands.com/Tracking "rev 31329"
[w-dh]: https://wiki.uooutlands.com/Detecting_Hidden "rev 32957"
[w-steal]: https://wiki.uooutlands.com/Stealing "rev 31673"
[w-snoop]: https://wiki.uooutlands.com/Snooping "rev 30069"
[w-tg]: https://wiki.uooutlands.com/Thieves_Guild "rev 32767"
[w-crim]: https://wiki.uooutlands.com/Criminality "rev 6059 (2020)"
[w-gz]: https://wiki.uooutlands.com/Guard_Zones "rev 6057 (2020)"
[w-mc]: https://wiki.uooutlands.com/Murder_Counts "rev 31580"
[w-hob]: https://wiki.uooutlands.com/Heat_of_Battle "rev 31577"
[w-mag]: https://wiki.uooutlands.com/Magery "rev 31527"
[w-sc]: https://wiki.uooutlands.com/Template:SpellCircles "rev 32649"
[w-rs]: https://wiki.uooutlands.com/Resisting_Spells "rev 33062"
[w-wres]: https://wiki.uooutlands.com/Wrestling "rev 32921"
[w-co]: https://wiki.uooutlands.com/Combat_Overview "rev 24978"
[w-hide]: https://wiki.uooutlands.com/Hiding "rev 29114"
[w-stealth]: https://wiki.uooutlands.com/Stealth "rev 30975"
[w-mounts]: https://wiki.uooutlands.com/Mounts "rev 29121"
[w-ham]: https://wiki.uooutlands.com/Hamstring "rev 32654"
[w-lj]: https://wiki.uooutlands.com/Lumberjacking "rev 32846"
[w-harv]: https://wiki.uooutlands.com/Harvesting "rev 31109"
[w-ts]: https://wiki.uooutlands.com/Test_Shard "rev 29137"
[w-tscmd]: https://wiki.uooutlands.com/Template:CommandsTestShard "rev 29011"
[w-npg]: https://wiki.uooutlands.com/New_Player_Guide "rev 33048"
[w-cmd]: https://wiki.uooutlands.com/Commands "rev 30548"
[w-opt]: https://wiki.uooutlands.com/Options "rev 31010"
[w-pit]: https://wiki.uooutlands.com/Pit_Trials "rev 32769"
[w-rbb]: https://wiki.uooutlands.com/Region_Bonus_Board "rev 21173"
[w-dung]: https://wiki.uooutlands.com/Dungeons "rev 4203"
[w-si]: https://wiki.uooutlands.com/Shelter_Island "rev 31103"
[w-ships]: https://wiki.uooutlands.com/Ships "rev 32955"
[w-pvpe]: https://wiki.uooutlands.com/PvP_Events "rev 24130"
[w-rope]: https://wiki.uooutlands.com/Adventurer%27s_Rope "rev 28841"
[w-mg]: https://wiki.uooutlands.com/Moongates
[w-horse]: https://wiki.uooutlands.com/Horsethief "rev 27283"
[w-bamb]: https://wiki.uooutlands.com/Brigand_Ambusher "rev 26443"
[w-bush]: https://wiki.uooutlands.com/Bushwhacker "rev 27206"
[p-2501]: https://uooutlands.com/news/patch-monster-rebalance-pack-ox-mechanic-changes-and-more/ "Patch 2025-01-25"
[c-ph]: ../../ClassicUO-main/src/ClassicUO.Client/Network/PacketHandlers.cs
[c-out]: ../../ClassicUO-main/src/ClassicUO.Client/Network/OutgoingPackets.cs
[c-noto]: ../../ClassicUO-main/src/ClassicUO.Client/Game/Data/NotorietyFlag.cs
[c-flags]: ../../ClassicUO-main/src/ClassicUO.Client/Game/Data/EntityFlags.cs
[c-ms]: ../../ClassicUO-main/src/ClassicUO.Client/Game/Data/MovementSpeed.cs
[ru-snoto]: https://github.com/runuo/runuo/blob/master/Server/Notoriety.cs
[ru-spell]: https://github.com/runuo/runuo/blob/master/Scripts/Spells/Base/Spell.cs
[ru-para]: https://github.com/runuo/runuo/blob/master/Scripts/Spells/Fifth/Paralyze.cs
[ru-track]: https://github.com/runuo/runuo/blob/master/Scripts/Skills/Tracking.cs
[ru-steal]: https://github.com/runuo/runuo/blob/master/Scripts/Skills/Stealing.cs
[ru-mobile]: https://github.com/runuo/runuo/blob/master/Server/Mobile.cs
[ru-bc]: https://github.com/runuo/runuo/blob/master/Scripts/Mobiles/BaseCreature.cs
[d-wm]: ../WORLDMODEL.md
[d-proto]: ../PROTOCOL.md
[d-move]: ../MOVEMENT.md
[d-lumber]: ../LUMBER_LOOP.md
[d-ac]: ../../ANTICHEAT.md
[d-plan]: ../PLAN.md

Local evidence produced for this doc (throwaway, not committed): a replay of all 27
`logs/session_*.s2c.raw` captures via `harness/replay.py` (`s2c_packets`, `replay_session`,
`WorldRuntime`). It gave the 0x20 notoriety/body/name table (§5.2), the notoriety change events
(§1.1), the Hidden-flag count and the first-sighting distance histogram (§2.4). Line numbers for
RunUO files refer to the `master` branch as fetched on 2026-09-29.

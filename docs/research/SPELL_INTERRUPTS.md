# Spell interruption on Outlands, measured, and what it means for the recall escape

Research of 2026-10-03 after Hackworth's two deaths to the red PK Bastet (docs/NOTES.md "PK death
in the Terran wilds" and "Second Bastet death"). Sources: every cast our characters made in the
memory store (`harness/data/harness.db`, read-only: sessions 2026-09-28 … 2026-10-03 18:05, 583 own
casts), the session captures `logs/session_20261002_183845.jsonl` (Terran) and
`logs/session_20261003_170434.jsonl` (Nusero, replayed with `replay.timed_packets`), the runner log
`logs/tasks/lumber-20261003-175808-6b09.log`, the wiki pages [Magery][w-mag], [Camping][w-camp] and
[Heat of Battle][w-hob] (read 2026-10-03), and the memory-store knowledge #514, #531, #957, #327,
#265, #953, #956. Times are the proxy clock (local). Confidence: **H** measured or wiki-stated and
consistent, **M** measured on few cases, **L** inferred.

## 1. What we measured

A cast starts when the server echoes the power words (speech type 10 from our serial, 0.05 s after
our press). The result is the target cursor (`0x6C`), the arrival (Stationary Penalty buff 277 on
self, new mobiles, or a map change), or a failure cliloc.

| Spell (circle) | Casts | Words → result, median (range) | Wiki cast time | Disturbed |
|---|---|---|---|---|
| Lightning (4) | 394 | 1.27 s (1.14–1.38) | 1.25 s | 19 |
| Greater Heal (4) | 120 | 1.27 s (1.20–1.42) | 1.25 s | 7 |
| Mark (6) | 28 | 1.77 s (1.65–1.81) | 1.75 s | 0 |
| Recall (4), charge or spell | 27 timed | 2.03 s (1.95–2.09) | 2.00 s | 3 (all Bastet) |

**Every disturbance coincides with damage to us** (the `500641` arrives in the same tick as our
hits drop). What hit us, and whether it broke the cast:

| Source | Hits during a cast | Disturbed | Conf. |
|---|---|---|---|
| Creature melee (mongbats, giant rats, a harpy; Hackworth/TestWorth, Magery 50–60, no Resisting Spells or Inscription, Wrestling ~61) | 59 | 26 (44 %, about 31–57 %); no link to the damage (6–12 per hit) | H |
| Damage over time (−4 every 5.0 s with buff 22, no attacker animation) | 13 | 0 | M |
| Bastet's Weaken (1st circle, 0 damage), Harm (2nd, −1), melee (−23) | 3 | 3 | M |

**Recovery after a disturbed cast.** The next cast is refused with 502644 "You have not yet
recovered from casting a spell." for

$$t_{rec} = \max\left(0.2,\ 1 - \sqrt{\frac{e}{T}}\right)\ \text{s}$$

where $e$ is how far the cast had got and $T$ its cast time (RunUO's pre-AOS
`GetDisturbRecovery`). All 30 live retries after a disturbance fit it (proxy delay + 0.055 s RTT ≥
$t_{rec}$ ⇔ taken). The deciding cases: Lightning disturbed 0.13 s in ($t_{rec}$ 0.67 s) was refused at
0.46 s and 0.59 s and taken at 1.14 s; Lightning disturbed 1.20 s in was taken 0.35 s later; so no
fixed delay fits. **H.** After a finished cast the wiki gives 0.2 s ("Casting recovery time"). A
refusal costs no recovery: the next press after it works as soon as $t_{rec}$ has passed.

**PvP rules (wiki, [Magery][w-mag] "Spell Interrupts"):** hostile 4th–8th circle spells always
interrupt; a hostile 1st, 2nd or 3rd circle spell interrupts the first time, then opens a 5 s
window per circle in which more spells of that circle don't (each hit resets it). Resisting Spells,
Inscription and Arcane+Wrestling only stop creature/environment interrupts (#514); in PvP only not
being hit helps, and Wrestling only avoids melee hits (#531). Heat of Battle (our own aggression
against a player) blocks Recall; we never attack, and auto-defence swings don't count.

**Hiking** (other players seen in our captures, 7 hikes): "*hiking to destination*" overhead at 0,
2 and 4 s, gone 5.0–5.2 s after the first. The wiki: 60 Camping, a **secure campfire** (lit kindling
secures after 57–60 − 45 × Camping/100 s = 30–33 s at Camping 60, lasts 10 min) unless an Explorer's
Pack effect is on, **no combat of any type within 30 s and no combat with a player within 2 min**,
frozen 5 s. Players say a hike can't be interrupted but the hiker can still be killed (#265, #327).
We have no capture of our own Atlas gump.

**Hamstring** (Bastet's weapon): "You are no longer hamstrung." 3.08 s and 3.15 s after "Their
attack hamstrings you!". His melee: a hit every 1.30 s, 14–23 per hit (Nusero 97 in 6.5 s, plus
Lethal Poison; Terran 102 in 6.4 s).

## 2. The two Bastet deaths, timed

**Terran, 2026-10-02 19:43 (mounted, no recall escape yet).** In view at 18 tiles (0.00 s); runner
abort 0.29 s; "Bastet is attacking you!" 2.80 s; first hit 4.58 s; dead 10.96 s. He cast nothing.

**Nusero (witcher_282), session 20261003_170434, on foot (the horse died 10-03 10:24).**

| Time | Event |
|---|---|
| 38.433 | Bastet's `0x20`: appears 11 tiles away with the Stationary Penalty (he stood hidden, #956) |
| 38.923 | his words "Des Mani" (Weaken, 1st circle) |
| 39.351 | runner double-clicks the tome (0.92 s after he appeared) |
| 39.486 | our "Kal Ort Por": recall 1 starts (would land 41.52) |
| 40.314 | Weaken lands on us (effect 0x3779, sound 0x1E6; hits 100 → 95 max): **500641**, 0.83 s into the cast; "Bastet is attacking you!" |
| 40.481 | recall 2 pressed 0.17 s after the 500641: **502644** ($t_{rec}$ 0.36 s) |
| 40.699 | his "An Mani" (Harm, 2nd circle) |
| 41.047 | recall 3 starts (0.73 s after the disturbance) |
| 42.007 | Harm lands (effect 0x374A, sound 0x1F1, −1): **500641**, 1.00 s into the cast |
| 42.07 | escape gives up ("recall 3/3"), no guarded place within 250 tiles, runner stops at 42.33 |
| 42.19, 43.80 | his "In Nox" (Poison) twice: no poison and no effect ever reach us |
| 43.097 | his explosion potion (overhead countdown "4" … "1" from 39.06) blows on his own tile: his hits 111 → 92 |
| 43.678, 45.199 | his first two swings, adjacent now: both **miss** |
| 46.500 … 53.007 | six hits (−18 hamstring, −17, −23, −14, −17 + Lethal Poison, −8); dead 53.012 |
| 48.351 | the overseer's own recall (6 s after the runner stopped): disturbed by the −23 at 0.59 s |

Weaken took 1.39 s and Harm 1.31 s from his words to landing (cast 0.5/0.75 s plus targeting).

## 3. Answers

### 3.1 Could a different escape have survived?

- **Terran: yes, the recall at sight that the runner does since 2026-10-02.** Sight + 0.3 s reaction
  + 2.1 s lands at ~2.4 s, before "attacking you" (2.8 s) and the first hit (4.6 s). He cast
  nothing. **M** (one encounter).
- **Nusero, first recall: no reaction time would have done it.** He revealed himself by casting:
  Weaken landed 1.88 s after he appeared, and the earliest possible recall (≈0.1 s to press +
  2.03 s) lands at ≈2.1 s. Nothing showed him earlier: he was hidden beyond our Tracking's ~8-tile
  hidden range (#953), and the hunt saw him at 42.0, after he had revealed. **H.**
- **Nusero, retrying: probably yes.** Nothing damaged us from 42.007 to 46.500. The old escape
  retried at once, so try 2 hit 502644 (it pressed 0.17 s after the disturbance, $t_{rec}$ was
  0.36 s), and with a 3-cast limit it stopped at 42.07. A recast at 42.007 + $t_{rec}$(1.00 s of 2.0)
  = 42.30 lands at ~44.4. In that window his only actions were the self-inflicted potion blast,
  two Poison casts that never reached us, and two swings that missed. So in the recorded timeline
  a fourth cast lands; with a different roll his 43.68 swing could have hit (he hit 6 of 8) and
  broken it, then a fifth cast would face his melee every 1.3 s. **M.** Replaying his landed hits
  against the old and new `escape()` (`test_escape.py` `test_escape_nusero_replay`): the old code
  gives up at 2.73 s after the double-click (live: 2.72 s), the new one lands at 5.0 s (44.4).
- **Magic Reflection would have saved Nusero.** The recall-breaking spell was the opener, Weaken
  (1st circle). Reflected, it doesn't reach us, and recall 1 lands at 41.52, before Harm lands at
  42.01 (0.49 s to spare; he was still 5+ tiles away until 43.6). **M** [INFERENCE: a reflected
  spell doesn't disturb the reflector; he might open with two quick spells once he knows we reflect].
- **Hiking: not in either encounter.** At Nusero his Weaken at 1.9 s is player combat, which blocks
  hiking for 2 min; a 5 s hike can't be started before it. At Terran a hike started at sight leaves
  at ~5.3 s, after the first hit (4.6 s, −15): it works only if a hit during the freeze doesn't
  cancel it (unknown) and only with a secure campfire already burning next to the tree (30–33 s to
  secure, 10 min life). Never faster than recall. **M.**
- **Running: no.** At Nusero we were on foot (0.2 s/step) and he was mounted (0.1 s/step). At Terran
  both were mounted: equal speed keeps the gap only while the terrain allows, there was no guard
  zone in reach, and stopping for a 2 s recall gives a mounted PK 20 tiles. His spells reach us
  anyway while we run in line of sight. **M.**
- **Healing or potions: no.** His melee does ~15 HP/s against our 100 hits; a Greater Heal heals
  24–30 at Magery 60, takes 1.25 s and is broken by the same hits. **M.**

### 3.2 What changed in the code (implemented)

`harness/escape.py` `escape()` (the lumber runner's red and creature escapes via `recall_out`):

- **No cast limit; a 20 s budget** (`ESCAPE_BUDGET_S`) from the first press, ending early on
  arrival, death (ghost body), or a refusal no retry can fix (heat of battle, unmarked, blocked,
  reagents, mana). A PK has to land a fresh interrupt within every 2 s cast, and giving up hands
  him the time (Nusero: 4.4 s before his first hit).
- **Retries wait exactly the disturb recovery** (`disturb_recovery`, + 0.05 s): `recall()` opens
  the book at once and holds the casting press until then, so the round trip overlaps the wait and
  no try is wasted on 502644. How far each cast got (`cast_s`, from our own power words) and the
  wait (`wait_s`) go into the `tries` record.
- 502644 waits 0.25 s, "You cannot cast a spell while frozen." (502643/502646, not seen live)
  0.5 s; refusals don't count as casts (`attempts` = casts that started), so `ctl recall`
  (`attempts=1`) and the travel legs (`attempts=2/3`) keep their meaning.
- `loop_lumber.expect_casts` named a non-existent `escape.REAGENTS`; it now tells the ledger the
  Recall reagents of the spell casts that started.

Not changed: the trigger (any red in view, an attacker, "is attacking you!") and the guard flight
after a failed escape. Starting earlier on weaker cues wouldn't have changed Nusero (see 3.1); the
runner's 0.92 s reaction there (0.29 s at Terran) is worth shortening but not decisive.

**Exploiting the 1st–3rd circle windows** needs nothing extra: the best moment to recast is the
end of the recovery right after any interrupt, which the escape now does. A PK who keeps casting
can still beat a 2.0 s recall: his interrupts came 1.3–1.4 s apart (words to landing), and only
two of his circles were spent when he paused.

**Hiking as an escape: needs captures first.** What to record (Test Shard or live, with the proxy):
making kindling (dagger on a tree, or Carpentry from boards), lighting it and "Your campfire is now
secure." with its delay; opening the Atlas (`[Atlas` / double-click): gump id, layout, the category
and entry buttons and "Hike to Location"; the server texts and timing of a hike (freeze,
"*hiking to destination*", departure); the refusal texts within 30 s of a creature fight and
without a secure fire; and whether a hit during the 5 s freeze cancels it (let a weak creature hit
us after starting a hike). Only the last decides whether hiking beats recall against a melee PK.

### 3.3 Character and gear (the user decides)

| Option | Effect, with numbers | Verdict |
|---|---|---|
| **Magic Reflection before working at a pvp spot** | Bounces the opener (Nusero: would have saved us with 0.49 s to spare). 5th circle: min 50, 100 % at **70** Magery, so at Magery 60 it succeeds **~50 %** (from a scroll +20 effective → 100 %; PLAN.md says "100 % at 60", which is wrong per the wiki table). 14 mana, 1.5 s cast, garlic + mandrake + spider's silk. In PvP it reflects 1 spell (stays for a 2nd with 35 % × Inscription/100 = 0 % for us); 30 s cooldown from the reflection. Duration on Outlands and its buff icon: unknown, needs a capture (cast it once, record the buff/messages, watch for removal) | **Best single change**; Magery 70 or scrolls |
| **Detect Hidden** | The Nusero PK stood hidden; Tracking 73 finds hidden players only within ~8 tiles. Tracking 100 + Detect Hidden 50 → 30 tiles, + DH 100 → 50 tiles (#953). That is warning before he can act | Strong for spots with a stealther history |
| **Tracking 73 → 100** | Visible range 78 → 100 tiles, hidden range 8 → 10 tiles without DH | Small alone |
| **A (bonded) horse** | Equal speed with a mounted PK instead of half; the Prevalia stables quest gives a bonded horse (TRAVEL_DEATH §1.4). Needed for any flight | Needed, not sufficient |
| **Resisting Spells or Inscription 100** | PvM only (#514): would stop the 44 % of creature hits that break our casts (hunting Lightning/Greater Heal, creature-stop recalls). Nothing against a PK | PvM comfort only |
| **Wrestling 61 → 100** | Only fewer melee hits (≈50 % avoid at 100 vs 100, #531); Bastet hit 6 of 8 swings | Marginal |
| **Hiking (Camping 60, have it)** | Possibly uninterruptible, but needs a burning secure fire within reach of every tree and no combat in the last 30 s / 2 min; 5 s freeze | Only after the capture above |

[w-mag]: https://wiki.uooutlands.com/Magery
[w-camp]: https://wiki.uooutlands.com/Camping
[w-hob]: https://wiki.uooutlands.com/Heat_of_Battle

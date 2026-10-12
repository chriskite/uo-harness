# OPTIMIZER.md — review of the lumber optimizer (`harness/lumber_opt.py`)

Review date 2026-10-11. Read-only: no code, data or doc was changed. All numbers come from a
consistent snapshot of `harness/data/harness.db` (sqlite backup API, last trip ≈ 2026-10-12 00:xx
UTC). The real `lumber_opt` functions were run on that snapshot: `trip_obs`, `plan`, `tree_yield` and
`regrowth`. The game mechanics come from the store's `knowledge` table (16 227 active rows) and from
docs/LUMBER_LOOP.md §2/§12 and docs/NOTES.md. Knowledge ids are given as `kNNNN`.

Data: 266 lumber episode rows. 233 of them are Outland Dan's, from 2026-10-05 to 10-11. There are 251
`lumber plan` records (chat `action` rows that hold the pick and the greedy run), 270 counted
sightings, and 21 proxy deaths, 12 of them lumber-related since 10-05.

## Status: recommendations 1–6 implemented (2026-10-11)

The user asked for all six changes (§3) and for the stored data to be brought in line. The decision
record is in docs/PLAN.md, "Lumber optimizer review fixes"; the model text is in docs/LUMBER_LOOP.md §6.
The review below is kept as written. The data changes:
- 10 prices were recorded: `hatchet:verite` 5 000 gp and `armor:harvest_suit` 1 000 gp (both from
  the user), plus the other hatchet materials priced by tier.
- 7 lumber deaths that had no runner `death` job event were backfilled from the proxy.
- The store was backed up to `harness/data/harness-pre-optimizer-20261011.db` first.

The live store after the change (`ctl lumber audit --days 8`, then a plan with no proxy):

| Check | Before | After |
|---|---|---|
| Field logs actual/predicted per day (10-06…10-11) | 1.29, 1.19, 1.24, 1.19, 1.11, 1.07 | 1.27, 1.18, 1.19, 1.13, 1.08, 1.04 |
| Deaths predicted vs actual (prequential; 10-05…10-11 before, …10-12 after) | 7.5 vs 12 (P = 0.08) | 8.2 vs 11 (P = 0.21) |
| Sent home predicted vs actual, 10-06…10-11 (after: escape-event trips counted too) | 121 vs 124 | 122 vs 133 |
| Logs per tree | 19.3 (frozen) | 23.3 (2.55 successes × 9.14 logs/success) |
| Regrowth pairs | 277 tree pairs | 277 tree + 972 stand pairs (still 65 min) |
| Recovery after a death | 20 min (assumed) | 58 min (14 deaths, shrunk) |
| Gear lost on death | 0 gp | 6 000 gp (suit + verite exceptional hatchet) |
| λ prior CV | 0.35 | 0.15 (the floor; the between-spot spread fits below it) |
| Expected pick value / greedy | 0.847 | 0.884 |
| Effective arms (exp entropy of P(best)) | 32.6 | 43.5 |

What remains:
- **λ is still under-predicted by 2–19 %.** The logs per success at each day's cut lags that day's
  (8.84 vs 9.22 on 10-11), and successes per field hour still drift about 1.3 %/day; neither is
  modelled.
- **Few dry trips inform capacity yet.** Only 8 count; 12 more are older than the forests. Capacity
  still sits on its prior for most spots, but it now learns with every dry trip.
- **Exploration.** With the drift removed, spots look more alike: more arms are near-tied, so
  exploration costs less per pick (0.884) while spreading over more spots. Whether to restrict
  exploration is still the user's call.
- **Data that can't be backfilled.** Stand-level yields before 2026-10-04 can't be recovered (the
  server never named the tree).

## Verdict

The design is sound: Gamma-Poisson rates, competing-hazard renewal-reward, and Thompson sampling
over spots. Several pieces are well calibrated. **But the sharp rise in output (stored boards per
active hour: 1 610 on 10-05 → 3 000–4 200 on 10-06…10-11) comes mostly from the character getting
better and from engineering changes, not from the optimizer learning.**

Three of the model's learned inputs stopped learning on 2026-10-04: grove capacity, logs per tree and
regrowth. One input drifts and the model reads the drift as a property of the spot: logs per
success, through the Harvest-aspect procs. The death risk is underestimated, both how often deaths
happen and what a death costs. Thompson sampling gives up ~15 % of posterior-mean value per run to
exploration. That cost is shrinking slowly, and it is driven mainly by hazard uncertainty.

| Area | Status |
|---|---|
| Sent-home hazard h_S | **Good**: calibrated in aggregate, and it ranks spots correctly |
| Field rate λ | **Biased**: under-predicts by 7–29 % every day; most of the gap between spots is measurement date, not spot |
| Grove capacity (binds for 69 of 85 eligible spots) | **Not learning**: yielding share frozen at the pooled prior for 85 of 91 spots visited since 10-04 |
| Regrowth window, logs per tree | **Frozen** on pre-Smart-Harvest data (277 pairs, 471 cycles) |
| Death hazard and death cost | **Underestimated**: ~1.6× fewer deaths than observed, 3 of 12 deaths not attributed, gear valued at 0, recovery 20 min vs ~47 min measured |
| Exploration | Working as designed, but costly: effective ~33 arms, pick/greedy expected value 0.85 |
| Objective (logs/h as the stand-in for gp/h) | **Fine** for choosing the spot (the share of coloured wood doesn't vary by spot) |

## 1. Does more evidence lead to better outcomes?

Realized output per UTC day, all trips (`field` = chopping time, `dur` = trip time):

| Day | Trips | Field h | Field logs/h | Stored/trip-h | Share field | Lumberjacking | logs/success |
|---|---|---|---|---|---|---|---|
| 10-05 | 30 | 2.9 | 3 054 | 1 610 | 0.81 | 50–80 | 7.98 |
| 10-06 | 38 | 8.0 | 3 913 | 3 033 | 0.86 | 81–100 | 8.12 |
| 10-07 | 26 | 4.4 | 4 066 | 3 182 | 0.86 | 100 | 8.23 |
| 10-08 | 26 | 6.5 | 4 410 | 3 727 | 0.91 | 100–101 | 8.21 |
| 10-09 | 35 | 6.5 | 4 539 | 3 409 | 0.88 | 102–103 | 8.39 |
| 10-10 | 50 | 10.8 | 4 715 | 3 956 | 0.89 | 103–106 | 8.81 |
| 10-11 | 26 | 6.5 | 4 937 | 4 187 | 0.91 | 106–107 | 9.22 |

Over the same days the hatchet went iron → bronze → gold/copper/verite, and forests (10-07) and
library hops (10-07) went in.

How much of the gain is the optimizer's:
- **Within the same spot, the field rate rises 3.7 %/day.** That is a weighted fixed-effects
  regression over 24 spots with ≥ 2 trips of ≥ 10 min; the pooled trend is 7.5 %/day. Split into
  parts: logs/success +2.4 %/day, successes per field hour +1.3 %/day, attempts per field hour
  −0.2 %/day.
  - So about half of the pooled gain is the character, and the rest is a mix of spot choice,
    skill jumps and engineering.
- **Quality of the spots visited**, as fixed-effect quality vs the mean over all spots: −13 % (10-05),
  0 % (10-06), then +2.5 %, +5.7 %, +4.2 %, +3.8 %, +8.4 % (10-07…10-11). Spot choice adds a modest
  and noisy few percent.
- **The exploration phase is ending.** Share of legs that were a spot's first visit: 10-07 17/26,
  10-08 15/26, 10-09 16/35, 10-10 10/50, 10-11 0/26 (4th+ visits: 14/26). Legs per spot since 10-04:
  30 spots once, 24 twice, 18 three times, 19 four or more.
- **The overseer follows the plan.** Since 10-07 the run started after a plan was the plan's pick
  in every matched case. The "other" spots on 10-05/06 (30 runs) were overrides.
- **Price of exploration, by the model's own beliefs.** The posterior-mean value of the Thompson
  pick divided by the greedy spot's, median per day: 0.87, 0.76, 0.79, 0.81, 0.84, 0.91 (10-06…10-11).
  At the current state, the expected pick value is 0.847 × greedy and the effective number of arms
  (exp entropy of P(best)) is 32.6. The highest P(best) is 0.125.

**Predictive validity (prequential).** For each day D, `plan` ran on the data before 00:00 UTC of D,
and its per-spot posteriors were scored on D's trips:

| Day | Field logs: actual/predicted | Sent home: predicted / actual | Deaths: predicted / actual | Prior λ | φ |
|---|---|---|---|---|---|
| 10-06 | 1.29 | 18.8 / 19 | 1.65 / 5 | 2 286 | 28.2 |
| 10-07 | 1.19 | 11.4 / 17 | 0.87 / 0 | 3 301 | 17.1 |
| 10-08 | 1.24 | 18.7 / 16 | 0.99 / 1 | 3 450 | 16.7 |
| 10-09 | 1.19 | 19.2 / 22 | 0.99 / 1 | 3 800 | 16.3 |
| 10-10 | 1.11 | 32.0 / 29 | 1.42 / 1 | 4 046 | 15.1 |
| 10-11 | 1.07 | 20.8 / 21 | 0.96 / 4 | 4 342 | 12.5 |

- **λ is biased low every day.** By how old the spot's data is: first visits 1.25 (n = 42),
  revisits with data 0–1 d old 1.17 (24), 1–2 d old 1.14 (19). The gap shrinks as skill and the
  aspect level off.
- **λ barely predicts beyond the time trend.** The correlation of log predicted λ with log realized λ
  on revisits is 0.68 overall. Within a day it is −0.00, 0.34, 0.43 and 0.03 (days with ≥ 4 trips).
- **h_S ranks spots.** Revisits split into tertiles of predicted h_S had realized rates of 1.22, 1.47
  and 3.32 per hour. Low and mid spots are over-predicted about 2×, because shrinkage pulls them
  toward the pooled 2.9/h.
- **The value ranking has some validity.** Tertiles of predicted net value had realized field λ of
  4 020, 4 495 and 4 944.

## 2. Findings, by severity

### F1. The drift in logs per success is credited to spots (high)

**Mechanics.** Quantity per node is fixed except for Harvest-aspect procs, which double it
(k334, k155, k1061; k2631: a better hatchet doesn't add logs). The measured logs/success rose
7.4–8.0 → 9.22 as the aspect tier and XP grew (`harvest_aspect` rows; `ctl act aspect`).

**What the model does.**
- It rescales a trip only for its success chance p (`adjusted_field_h`). p is ≈ 0.98 from 10-06
  on, so the rescale no longer does anything.
- The docstring and LUMBER_LOOP §6 claim that the recency weighting absorbs the aspect. With a
  14-day half-life it can't absorb +2.4 %/day: a trip 4 days old weighs 0.82 and is ~10 % stale.

**Effect.**
- Spots visited early look worse than spots visited lately. The time drift over a week (~25 %)
  exceeds the measured spread between spots (fixed-effect SD 0.18 on the log scale with one outlier
  at 1 612 logs/h; the other 23 spots lie within 3 304–4 072, ~±10 %).
- The λ ranking is therefore dominated by visit date, and the λ prior mean lags. It is built from
  spot means, so first visits are under-predicted by 25 %.
- Every trip row already has `logs` and `successes`, so the per-trip logs/success is observable.

### F2. Grove capacity, logs per tree and regrowth stopped learning on 2026-10-04 (high)

**Capacity.** Since Smart Harvest, attempts are recorded on the stand tile, not the tree
(docs/MEMORY.md `harvest_nodes`). `tree_yield` therefore adds no tried/yielded tiles.
- Of the 91 spots visited since 10-04, 6 have any tried tiles, all of them old.
- The other 85 keep the pooled Beta prior (share 0.61, worth 10 tiles) forever. Their capacity draw
  keeps a CV of about 24 % that no amount of evidence shrinks.
- Capacity binds (`grove_bound`) for 69 of the 85 eligible spots, so it sets Q and the value of most
  spots.

**The model ignores the evidence that does exist.** A `dry` trip, or "every tree left was within a
creature's reach", measures capacity directly. Logs at such endings vs today's `grove_logs`:

| Spot | Logs at the ending | Today's `grove_logs` |
|---|---|---|
| witcher_105 | 542 | 5 778 |
| witcher_265 | 914 | 6 540 |
| witcher_162 | 1 215 / 1 469 / 2 408 | 4 442 |
| witcher_137 | 1 198 | 551 |
| witcher_43 | 2 232 | 1 524 |

Big forests are over-estimated and some small groves under-estimated. Other players empty stands too
(witcher_247: 16 of 33 stands dry on arrival, docs/NOTES.md).

**Logs per tree** = 19.3 from 471 pre-10-04 cycles. That was measured at 7.6 logs/success; at today's
9.2 the same trees would give ~23 [INFERENCE: the node size is in successes, procs double the
amount].

**Regrowth** = 65 min, fit on 277 pre-10-04 pairs (P(regrown) is 0.52 at 41–63 min and 0.67 at
63–243 min). That fits the community figure of a random 30–60 min respawn (k2078) together with
depletion by others.
- The curve levels off at 0.84–0.85 for gaps of 4–24 h, which is other players' chopping, not
  regrowth.
- `nothing_near` marks (12 372) are excluded by design, so the window can never update.
  docs/NOTES.md 376 also notes that gaps shorter than the window are no longer observed.

### F3. The death risk is underestimated, and so is the cost of a death (high for Q and spot choice)

**How often.** Prequential predictions for 10-05…10-11 total 7.50 deaths against 12 lumber-related
deaths observed (Poisson P(X ≥ 12) = 0.079). Realized 0.263 deaths per field hour vs a mean predicted
0.164.

**Attribution.** `attribute_deaths` drops deaths more than 40 tiles (`CURRENT_SPOT_MARGIN`) from the
spot, even when they fall inside a trip of that spot. That lost 3 of 12:
- witcher_268, 10-06 01:42, 47 tiles off (the recall failed, dead);
- witcher_180, 10-11 21:44, 150 tiles off, "died while running from players";
- witcher_296, 10-11 21:07, at the home landing 16 min into the trip, after an escape recall.

These are exactly the escape deaths (k17040, k17044: hits disturb the recalls).

**Exposure.** Hazards act only during field time. The walk from the landing into the grove (up to
300 tiles) is in `walk_out_s`, which counts as overhead, so it carries no risk.

**Cost of a death.**
- `gear_at_risk` = 0 gp: the newest row's verite exceptional hatchet has no price, and armor isn't
  counted at all. Deaths lose the Harvest-aspect armor set (k4104, k4102: hatchets stay on the
  corpse).
- `RECOVERY_H` is 20 min [INFERENCE]. The measured minutes from a death to the next lumber trip were
  88, 112, 83, 15, 14, 9, 20, 101, 135, 35, 26, 60 (median ~47; this includes overseer latency).
- P(death | sighting) pools to 0.031. Deaths cluster at 18–24 UTC (7 of 12 deaths in 48 % of field
  hours). Not significant with n = 12, but time of day is unmodelled.

**Effect.** Q* and the value of deadly spots are too high. Several deaths came at spots the planner
had just picked (witcher_336, 193, 180; the overseer recorded "avoid" notes, k4112, k17040).

### F4. The prior and exploration are wider than the data (medium)

- The λ prior CV has a floor of 0.35 (`PRIOR_CV_MIN`). The measured between-spot spread is ~0.06–0.18
  after removing drift.
- `rate_prior` takes τ² from the raw variance of spot means. That includes sampling noise and the F1
  drift, and it counts each spot in its own prior (minor with 70+ spots).
- φ was inflated by the drift early on: 28 on 10-06, 10.7 now, which matches the lump factor
  E[X²]/E[X] ≈ 9–10 of aspect-doubled chops.
- The spread is driven mostly by hazards. With hazards and overhead fixed at their means in the
  draws, the effective arms drop from 32.6 to 17.0 and the expected pick value rises 0.847 → 0.894.
  With the capacity share fixed: 28.4 and 0.850.
  - `HOME_SHRINK_H` = 2 h against ~0.3 field h per visit means h_S needs many visits to separate
    spots.
  - Sent-home counts across spots are overdispersed (Pearson χ² 200 on 75 dof, 140 events in 47 h),
    so the spot effect is real and worth learning. It is mixed with runner-policy changes (the
    faction-tag and precast triggers of 10-06), which the model also treats as spot properties.
  - 62 % of sent-homes are player-triggered and 35 % monster-triggered.

### F5. Correct as built (verified)

- **Quasi-Poisson Gamma posterior for λ.** The Poisson-process likelihood doesn't depend on the
  stopping rule (trips stop at a quota or at an event), so the Gamma update is valid. The within-spot
  trip residual SD (0.10 on the log scale for trips ≥ 10 min) matches the φ-implied ~8 % for a
  20-min trip.
- **Competing-hazard renewal-reward (`trip_terms`).** Closed forms checked against the docstring;
  P_D ≤ 1. A sent-home trip correctly stores its load. Exponential stays fit a Poisson exposure
  model.
- **Sent-home calibration** (table above): aggregate within ±10 % on 5 of 6 days.
- **Rescaling by success chance.** The formula gave 0.69 vs a measured 0.70 at 69.1 skill. Measured
  successes/attempts were 0.98–0.999 at 100+ skill against p 0.975–0.989.
- **Logs per hour as the objective for spot choice.**
  - Since 10-07: 186 873 logs, 8.4 % coloured, worth 27.9 gp/log vs ordinary 26.8.
  - The coloured share across 46 spots has χ² 441.6 on 45 dof, ≈ the lump factor of ~9, so it shows
    no spot effect. This matches k13036/k2078 (colour is rolled at respawn, independent of place).
  - `gp_per_log` already uses `board:ordinary` = 26.8, not woods.json's 9.5.
- **Wood colour above skill.** 70 avarwood logs were cut at skill ≤ 107 although woods.json has
  min_skill 110. The rule "colours above our skill count as regular" (`success_p`) is slightly off,
  probably because effective skill includes bonuses (k304). The effect on p is negligible.

### F6. Smaller points

- **The half-life of 14 d has never acted**: the data span 8 days. Whether 14 d suits hazards (policy
  and PK presence change in hours to days) vs λ is untested.
- **Q* rarely binds.** Only 69 of 235 legs since 10-04 ended at their quota. 140 legs ended on a
  threat, and their field hours (20.8) nearly equal the quota-complete trips' (23.2). Mean stay is
  ~20 min at h_S ≈ 2.9/h. Value is dominated by h_S, capacity and λ, not by Q.
- **After a sent-home a library hop often follows**, so the real cost of the next cycle is
  `hop_out_s`, not the full T. The spot-choice model charges a full T: a small bias against spots
  with a high h_S.
- **Runner errors count as spot evidence**: TypeError, ConnectionAbortedError, "no hatchet" (3).
- **No permanent calibration check exists.** `test_lumber_opt.py` tests behaviour on synthetic rows,
  not prequential accuracy. The plan records in `chat` (pick, greedy, expected values) make a
  backtest possible.

## 3. Recommended changes (not made; for decision)

1. **Rescale for the character, not just p.** Normalise each trip's logs by its own logs/success
   (`logs/successes` on the row) to today's logs/success, or fit a shared character/time effect
   (log λ = spot + character(t)). This removes F1's confound and the 25 % first-visit bias.
2. **Learn capacity from trip endings.** Treat a `dry` or creature-blocked leg's logs (plus
   `carried_in` handling) as an observation, censored from below when the leg ended otherwise, of
   available trees × share × logs per tree. Or record per-stand yields and drop the per-tile Beta.
   Re-estimate logs per tree from logs/success × successes per node.
3. **Regrowth from stand data.** Use pairs of a dry stand followed by a later attempt in reach of it
   (censored), or accept the 30–60 min mechanics prior explicitly. Report the regrowth estimate as
   frozen until then.
4. **Death model.**
   - Attribute any death between a trip's t0 and t1 + `DEATH_LINK_S` to that trip regardless of
     distance, including escape deaths and deaths at the home landing.
   - Count the walk from the landing as exposure.
   - Price the gear and the aspect armor (`ctl lumber price`; prompt for unpriced items instead of
     counting 0).
   - Measure recovery from death → next trip.
5. **Tighten the priors to the data.**
   - Derive the λ prior's τ² from a random-effects fit (between-spot only) and drop the 0.35 floor or
     lower it to ~0.15.
   - Consider a separate h_S for player and monster triggers, and a time-of-day term once n allows.
6. **Track calibration.** Add the prequential table above as a `ctl lumber audit` or test-like
   report: daily actual/predicted λ, h_S and deaths, and pick/greedy value. Then later sessions can
   see whether changes 1–5 help.

Decision needed (user): whether to keep the Thompson sampler's exploration as is (it buys
behavioural variety, ANTICHEAT.md §8.3) or to add a floor on P(best) or a top-k restriction. Fixing
1–5 first would shrink the 15 % exploration cost on its own by narrowing posteriors that are now too
wide.

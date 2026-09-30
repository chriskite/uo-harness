# ROADMAP.md: Phase 5, the overworld lumber job under an AI overseer

Written 2026-09-29 (night) from the user's 10-point brief. Research is in `docs/research/`:
- **ECONOMY**: tools, woods, shelf, deeds, prices
- **TRAVEL_DEATH**: recall, runebooks, moongates, Young, death, resurrection, towns
- **THREATS**: PvP, Tracking, escape math, thieves, monsters, rules

The overseer design is in `docs/OVERSEER.md`. The Shelter loop remains the proving ground
(`docs/LUMBER_LOOP.md`).

Ground rules that shape everything below:
- **No PvP ever.** The Test Shard Code of Conduct bans unsanctioned PvP (THREATS §6), and
  attacking gives *Heat of Battle*, which blocks recall and entering the inn room. So even a grey
  thief is avoided, never attacked. That answers the "grab a sword" idea in item 3.
- **The captcha stays human.** The overseer's job is to wake you (ANTICHEAT §8.8).
- **Human pacing and human-length sessions.** No 24/7 operation (AGENTS.md).
- **Only act on what a player could see.** Targets must be on your level (ANTICHEAT §8.15).

## Status per item

| # | Item | Built tonight (offline, tested) | Next | Blocked on the user |
|---|---|---|---|---|
| 1 | AI overseer | Juncture/chat bus in the memory store (schema v2). `harness/ctl.py`: `status`, `run lumber\|bank`, `stop`, `wait`, `ack`, `say`/`think`, and a whitelisted `act`. `task_wrap.py` posts `task_done`/`task_failed`. `loop_lumber` posts `captcha` (urgent, auto-acked when you solve it), `threat`, `theft_suspected` and `death`. The viz Overseer tab has chat, thoughts, actions, open junctures and a heartbeat | Run a first supervised session: an omp session in overseer mode (`docs/OVERSEER.md` §6) runs the lumber task and handles junctures. Later: a standalone API overseer daemon on the same bus | Try `ctl` with me watching. Decide on the API-daemon later (it needs an API key) |
| 2 | Escape from PKs | `harness/threats.py`: classifies every mobile (red/grey/orange/blue/npc/monster, player vs NPC with sources), ETA and flee radius. The loop logs `pk_seen` and on flee-level threats stops, with an urgent juncture | The escape action itself: recall from a runebook, humanised. The flee radius per THREATS §3/§7: recall-on-sight is **not** safe against a **mounted** PK (flee radius 38–49 tiles > 18 view range) unless moving while casting is allowed (D4) | Runebook demo (D6/D7). Template choice: Magery/Tracking/Resist, mount. Home town |
| 3 | Thieves | `harness/ledger.py`: pack accounting with expected changes. Unexplained losses become a `theft_suspected` juncture plus a `theft` job event, and **the loop carries on** (no confusion). Wood-aware | Behaviours from THREATS §7 T3/T4: keep non-friendly players ≥ 2 tiles away (steal range 1), cap carried boards (400 = two GM steals), never carry deeds, make the recall source unstealable, and after a theft notice recall and avoid the spot for 20 min | Consent/sanction for a staged thief capture (THREATS D9), optional |
| 4 | Rune library | none (needs the runebook/Mark flows) | Data model: runes (book, slot, name, facet/x/y, spot stats) linked to `harvest_nodes` by region. The overseer picks spots by observed yield and hazard | Who marks runes: 60 Magery or a helper (TRAVEL_DEATH Q4). Mark demo (D9) |
| 5 | Jobs analytics page | Viz **Jobs** page: logs/hr, logs/trip, trips, active hours, deaths by cause (PK/mob), thief losses, captchas, value, logs/hr over time, per-trip bars and table, event timeline, wood breakdown. `harness/jobs.py` + `/api/jobs` | Grows as job_events accumulate. Add per-spot and per-region views when overworld data exists | none |
| 6 | Monsters | `threats.py` treats creatures as aggressive by default off Shelter (parameterised), with melee range and speed | Learn per-graphic aggro radius and speed from sightings (THREATS T6). Walk-away-perpendicular behaviour | Monster demo (THREATS D8), region choice |
| 7 | Death → healer → inn → restock | Death detection: ghost body in threats and ledger, a `death` juncture and job event with cause | The recovery flow of TRAVEL_DEATH §5: ghost walk → healer resurrection gump → corpse run policy → recall home → room → restock. It needs 0x2C death parsing in the world model | Death/res demos D1–D5 (doable while Young in the New Player Dungeon). Policy on `[TestRes`, corpse runs |
| 8 | Shelf restocking | Research: it's the **Storage Shelf** (loadout + Begin Resupply; it can top up runebooks with recall scrolls). The Test Shard has fully stocked public shelves at North Prevalia and Corpse Creek, but reaching them ends Young | Shelf automation after the gump capture. "10 recall scrolls" = a full runebook (the max is 10 charges) | Shelf demo (ECONOMY/TRAVEL D8). Choice of public shelf vs our own |
| 9 | Wood values | `harness/data/woods.json`: 10 woods, skill needs, hues, base chances, commodity amounts, from the wiki; value only for ordinary (9.5 gp). The ledger splits logs by wood; trip rows now carry `woods` | Return trigger on carried **value** `V* = ρ·sqrt(2T/h)`, checked after every chop (ECONOMY §6) | Prices for colored woods (Vendor Search, manual). A colored-chop capture to confirm hues |
| 10 | Vendor prices | Research: **Vendor Search is a website behind an OutlandsID login**. The in-game command only opens it. Recommendation: never scrape it; keep a dated manual price table | A small price table (`harness/data/prices.json`, dated, manual) read by the value model | Tomorrow: you browse Vendor Search and read off board / colored-board / commodity prices together |

## Order of work (proposal)

1. **Supervised Shelter runs.** Exercise items 1 and 5 end to end while still Young: the omp
   overseer runs `ctl run lumber --trips N`, answers your chat, handles `task_failed` and
   `captcha`, and the Jobs page fills in.
2. **Young-safe captures** (the Young restriction is still in force):
   - death/resurrection in the New Player Dungeon (TRAVEL D1–D5)
   - NPC buy lists (ECONOMY 1)
   - hatchet wear (ECONOMY 3)
   - tree respawn probes (ECONOMY 4)
   - renounce-gump recognition (TRAVEL D11; decline it)
3. **Decisions**: template, mount, home town, recall method, shelf and death policy. The
   questions are below.
4. **Leave Shelter** (your action, since it's permanent): rent a mainland room, then do the
   runebook/recall, shelf, Mark, moongate and Tracking captures.
5. **Build** recall escape → death recovery → shelf restock → rune library → the value-based
   return trigger → hazard learning per region. Each step is offline-tested against capture-based
   simulators like `test_loop_lumber.py`, then proven live with you watching.

## Questions for you (deduplicated from the three research docs)

1. **Template for the overworld lumberjack:** Magery (recall by spell) or scrolls only? Tracking
   (it only pays off at ~90+ against mounted PKs)? Resisting Spells (fewer recall interrupts)?
   Camping (hiking)?
2. **Mount:** get a horse before leaving Shelter? It cuts the needed flee radius by up to 20
   tiles.
3. **Home town:** Totem (inn, bank and healer within 21 tiles), Prevalia (Test Shard shelves) or
   Horseshoe Bay (nearest)? A new room there?
4. **Runebook:** buy or craft? Bless it (62 500 gp; wiki pages disagree) or accept the loss risk?
   Where do the recall scrolls come from?
5. **Who marks runes** (60 Magery), or a helper/public library?
6. **Death policy:**
   - may the agent resurrect and resume on its own, or stop and wake you?
   - `[TestRes` or the realistic path?
   - corpse runs: always, only with no red/grey in range, or never?
7. **Unknown blue players closing in:** flee (safer, skittish) or react only to red/grey?
8. **Escape tools:** may it carry and use potions, trapped pouches, or an Adventurer's Rope?
9. **Monsters:** always avoid and recall, or may it fight weak aggressors?
10. **Gold:** may the agent spend gold (NPC buys, player vendors), and with what daily cap?
11. **Wood values:** gp from Vendor Search or a tier ladder? Is 9.5 gp/ordinary board OK?
12. **Deeds** under the daily Test Shard wipe: keep "no deeds" or deed at a stockpile?
13. **Carry cap / locked box** for colored stacks (thieves can now lockpick locked containers in
    packs)?
14. **Staged PvP captures** (a thief, a red approach) with a friend: only with staff sanction.
    Want to pursue it?

## Demos to record (proxy running, you playing; none attacks anyone)

While still Young:
- death → resurrection gump (cancel once, then accept) → corpse loot
- NPC buy lists (mage, scribe, the hatchet seller): open Buy, then cancel
- single-click the hatchet before and after ~20 chops (wear)
- walk into the Shelter moongate and **decline** the renounce-Young prompt (so the loop can
  recognise and abort on it)

After leaving Shelter:
- runebook: open it, drop a scroll on it, recall via the gem; also `[RecallCharge` / `[Recall`
  with one interrupted cast
- Storage Shelf: loadout, Begin Resupply, Resupply Runebooks
- Mark and rename a rune (if someone has Magery)
- public moongate
- Tracking (menu + Hunting mode)
- ride in a straight line (mounted speed)
- approach a few aggressive spawns (aggro radius)

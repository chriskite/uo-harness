# ECONOMY.md: lumberjacking economics on Outlands (research, 2026-09-29)

Offline research for the overworld lumber phase: harvest mechanics, wood types, commodity deeds,
the restock shelf, supplies, prices, and the inputs a value-aware "head home" rule needs
(docs/LUMBER_LOOP.md §6). The data file for code is `harness/data/woods.json` (§2).

**Confidence labels.**
- **H**: the official Outlands wiki plus our own capture or harness data agree.
- **M**: the official Outlands wiki alone, or two independent community sources.
- **L**: a single community source.
- `[INFERENCE]`: my reasoning, not observed.

"Generic UO" (RunUO/OSI) is cited only as a contrast, and never as a claim about Outlands.
All wiki pages were read on 2026-09-29.

---

## 1. Lumberjacking mechanics

### 1.1 Harvest action and yield

| Fact | Conf. | Source |
|---|---|---|
| Lumberjacking always uses Smart Harvest: double-click the equipped hatchet and it attempts every nearby tree | M | [Lumberjacking](https://wiki.uooutlands.com/Lumberjacking) |
| Conflict: the Smart Harvest page says to use the tool and **target yourself / your status bar**. Only mining skips the self-target. Our demo got a target cursor after the double-click (cliloc 1010018) and targeted trees one by one | H (conflict itself) | [Smart Harvest](https://wiki.uooutlands.com/Smart_Harvest), docs/LUMBER_LOOP.md §12.1 |
| Success chance for regular wood = `(Skill/100) × (1 + ToolBonus)`. Each colored wood has its own formula (§2) | M | [Lumberjacking](https://wiki.uooutlands.com/Lumberjacking) |
| Shelter Island halves the harvest chance. **Our data agree:** 14 successes in 45 attempts = 31 % at Lumberjacking ≈ 60, against the predicted 60 % × 0.5 = 30 % (harness.db `harvest_attempts`, live runs 2026-09-30 03:17–04:07 UTC) | H | [Shelter Island](https://wiki.uooutlands.com/Shelter_Island), `harness/data/harness.db` |
| **Yield per success: 5–10 logs, mean 7.9** (n = 14, Shelter, same data). The wiki gives no number. Generic RunUO gives a fixed 10 per harvest (`ConsumedPerHarvest = 10`), so Outlands differs | H (our data) | harness.db; [RunUO Lumberjacking.cs](https://raw.githubusercontent.com/runuo/runuo/master/Scripts/Engines/Harvest/Lumberjacking.cs) |
| Result texts: success "You chop some logs and put them in your backpack.", fail cliloc 500495, depleted cliloc 500493 "There's not enough wood here to harvest." Colored woods: "You chop some dullwood logs…" etc. Double yield: "Harvest double yield". Out of range/none: "You do not see any harvestable resources nearby". Tool break: "You broke your axe" | H (first three, captures) / L (the rest) | loop_mine timelines 204225, 224710; gugutz lumber.razor; DanStock script (links in §7) |
| 60 s harvest lockout after recall / moongate / hike / teleport / Adventurer's Rope | H | [Harvesting](https://wiki.uooutlands.com/Harvesting), LUMBER_LOOP §12.1 |
| Resource Maps are exempt from the 60 s lockout | M | [Resource Maps](https://wiki.uooutlands.com/Resource_Maps) |
| Mining has a "Stationary Penalty" (no mining after a recall or idling until you've moved > 5 steps). The wiki doesn't state it for lumberjacking: **unknown** | M (mining only) | [Mining](https://wiki.uooutlands.com/Mining) |
| Log and board weigh 0.025 st each; 1 log → 1 board | H | [Harvesting](https://wiki.uooutlands.com/Harvesting), LUMBER_LOOP §12.1 |

### 1.2 How much a tree holds and regrowth

- **Wiki:** "Trees are randomized when they respawn and have a chance to uncover colored logs." Each
  respawn re-rolls the tree's color, and that chance "is not affected by outside parameters". The
  wiki gives no respawn time and no amount per tree. **M** for the qualitative statements,
  **unknown** for the numbers. Source: [Lumberjacking](https://wiki.uooutlands.com/Lumberjacking).
- **Our data (Shelter, n = 3 depleted trees):**
  - (1924, 2588): 10 + 6 + 7 = 23 logs before cliloc 500493.
  - (1924, 2591): 8 + 8 + 6 = 22.
  - (1926, 2572): 8 + 5 = 13. It may have been partly chopped before; this was the first logged
    visit.
  - The first two trees were revisited about 11–13 min later. The logs from that revisit (7 and 6)
    are included in the sums above, and the trees depleted right after it.
  - So nothing regrew within about 13 min. `[INFERENCE]` A full tree holds about 20–25 logs.
- **Generic RunUO (contrast only):**
  - Banks are 4×3 tiles and hold 20–45 logs.
  - A bank respawns 20–30 min after depletion.
  - The Outlands numbers are consistent with that, but that is unverified. Source:
    [RunUO Lumberjacking.cs](https://raw.githubusercontent.com/runuo/runuo/master/Scripts/Engines/Harvest/Lumberjacking.cs).
- **Unknown:**
  - the respawn time
  - whether "tree" means a tile bank or a single static
  - whether a colored tree holds the same amount

### 1.3 Skill gain and caps

| Fact | Conf. | Source |
|---|---|---|
| 0–50 trainable from NPCs (Bowyer, Carpenter, Lumberjack); 50–100 by chopping; 100–120 only from colored chops | M | [Lumberjacking](https://wiki.uooutlands.com/Lumberjacking), [Template:TrainableSkills](https://wiki.uooutlands.com/Template:TrainableSkills) |
| At 100.0–119.9 only a successful **colored** harvest can raise the skill. Dullwood lumber maps take you 100 → 120 | M | [Lumberjacking](https://wiki.uooutlands.com/Lumberjacking) |
| Every harvest attempt on a Resource Map gets a skill-gain check | M | [Resource Maps](https://wiki.uooutlands.com/Resource_Maps) |
| Harvesting skills gain more slowly than other skills and get no Shelter dungeon bonus | M | [Harvesting](https://wiki.uooutlands.com/Harvesting), [New Player Guide](https://wiki.uooutlands.com/New_Player_Guide) |
| Shelter Island: no skill rises above 80 | M | [Shelter Island](https://wiki.uooutlands.com/Shelter_Island) |
| Individual cap 100; up to 120 with Skill Mastery Scrolls (+1 each, looted); total cap 700 (720 with Epic scrolls/orbs) | M | [Skill Mastery](https://wiki.uooutlands.com/Skill_Mastery), [Skills & Stats](https://wiki.uooutlands.com/Skills_%26_Stats) |
| A gain needs a chance to fail | M | [Skills & Stats](https://wiki.uooutlands.com/Skills_%26_Stats) |

Consequence: on Shelter (cap 80) TestWorth can reach dullwood (65) through bronzewood (80), but
it only finds them where a tree rolls that color (§2).

### 1.4 Harvest bonuses

| Bonus | Effect | Conf. | Source |
|---|---|---|---|
| Hatchet material/quality | Tool Bonus multiplies the success chance: exceptional +0.04, mastercrafted +0.04, dull copper +0.02 … avarite +0.18 (the table steps +0.02 per tier; bonuses stack) | M | [Lumberjacking](https://wiki.uooutlands.com/Lumberjacking) |
| Harvest Aspect armor | Effective harvesting skill +(3 + 1/tier). Chance of 2× yield (6 % + 2 %/tier). Both take effect 60 s after activation | M | [Harvest Aspect](https://wiki.uooutlands.com/Harvest_Aspect) |
| Mastery chain links "Effective Harvesting Skill" | Add to effective skill (stated on the Mining page) | M | [Mining](https://wiki.uooutlands.com/Mining) |
| Camping skill | Backpack max weight +200 st × Camping/100, max item count +50 × Camping/100 | M | [Camping](https://wiki.uooutlands.com/Camping) |
| Trade Caravans (guild) | 5 Harvest Progress per log for harvesters with ≥ 80 skill. Guilds need 2 000 000 progress to start a caravan | M | [Trade Caravans](https://wiki.uooutlands.com/Trade_Caravans) |

### 1.5 Tools (hatchets)

| Fact | Conf. | Source |
|---|---|---|
| Iron hatchet: **500 uses** base. Exceptional/mastercrafted +250 uses; colored +125 (dull copper) up to +1125 (avarite) | M | [Lumberjacking](https://wiki.uooutlands.com/Lumberjacking) |
| TestWorth's hatchet shows "(500 uses remaining)" and "[newbied]" | H | ClassicUO journal `2026_09_29_20_27_24_journal.txt:81-83` |
| Crafted with Tinkering: Hatchet = 5 iron ingots at 15 Tinkering; colored = 5 colored ingots at 25 (dull copper) up to 110 (avarite) | M | [Template:Tinkering](https://wiki.uooutlands.com/Template:Tinkering) |
| Starting kit for the Lumberjacking skill: Hatchet ×2, Saw, Axe | M | [Starting Items](https://wiki.uooutlands.com/Starting_Items) |
| Graphics 0x0F43/0x0F44 "hatchet" (artdata); TestWorth wields 0x0F44 on layer 2 | H | Outlands `artdata.uoo` via `harness/uomap.py`; `harness/data/loops/lumber.json` |
| **NPC price and which NPCs sell hatchets on Outlands: unknown.** The wiki has no NPC shop lists except the Banker. No capture includes a hatchet in a buy list | none | [Banker](https://wiki.uooutlands.com/Banker) (the only NPC page) |
| Unknown whether a use is spent per attempt or per success. The harness can measure it from the "(N uses remaining)" label (§8) | none | none |

---

## 2. Wood types

| Wood (woods.json name) | Min skill | Success chance (× (1+ToolBonus)) | Tree color chance | Log/board hue | Crafted-item bonus tier |
|---|---|---|---|---|---|
| Regular (`ordinary`) | 0 | (S − 0)/100 | 81.75 % | 0 | none |
| Dullwood | 65 | (S − 25)/80 | 4.0 % | 2419 | 1 |
| Shadowwood | 70 | (S − 30)/80 | 3.5 % | 2406 | 2 |
| Copperwood | 75 | (S − 35)/80 | 3.0 % | 2413 | 3 |
| Bronzewood | 80 | (S − 40)/80 | 2.5 % | 2418 | 4 |
| Goldenwood | 85 | (S − 45)/80 | 2.0 % | 2213 | 5 |
| Rosewood | 90 | (S − 50)/80 | 1.5 % | 2425 | 6 |
| Verewood | 95 | (S − 55)/80 | 1.0 % | 2207 | 7 |
| Valewood | 99 | (S − 59)/80 | 0.5 % | 2219 | 8 |
| Avarwood | 110 | (S − 65)/80 | 0.25 % | 1763 | 9 |

- **Names, skills, formulas, chances: M.** Source: [Lumberjacking](https://wiki.uooutlands.com/Lumberjacking).
  The wiki's lumber formulas divide by 100 and 80, while the mining ones divide by 1 and 0.8. Read
  both as fractions `[INFERENCE]`. Our Shelter success rate matches the /100 reading (§1.1).
- **Skill below a tree's color:** the wiki sentence is garbled ("you will find the highest color or
  you can harvest instead"). Mining says a too-low skill "will default to Iron Ore", and that a
  colored node falls back to iron on 50 % of swings. Whether lumber behaves the same is
  **unknown**.
- **Hues: M (two independent community sources, not yet captured).**
  - [Item names and serials](https://outlands.uorazorscripts.com/script/23d5660a-f8fd-491d-97b3-1afa3d73c84d)
    lists "Material Type Hues": Leather/Board and log/Iron 0, Dullwood 2419 … Avarwood 1763.
  - DanStock's lumber script colors its "you harvest dullwood" etc. messages with the same numbers
    ([script](https://outlands.uorazorscripts.com/script/c87ec994-4970-4e52-8035-5191a8c51552)).
  - Colored woods share their hues with the ore/ingot/hide of the same tier.
  - Ordinary hue 0 is **H**: log 0x1BDD and board 0x1BD7 at hue 0 in capture 20260929_204225.
- **Graphics: H for ordinary.** Logs are `0x1BDD` on the wire. Artdata names 0x1BDD–0x1BE2 "log(s)"
  and 0x1BD7–0x1BDC "board(s)". Colored logs keep 0x1BDD (7133) and differ only by hue: the
  community scripts do `findtype 7133|7127 … if hue != 0` (L, gugutz). So code must identify a wood
  by **(graphic, hue)**.
- **Relative value:**
  - Colored boards give wooden weapons, shields, instruments and fishing poles a stepwise bonus.
    For example, dullwood adds +15 durability / +5 % AR / +1 tactics / +2 % fishing; avarwood adds
    +55 / +45 % / +9 / +18 %. Source: [Modifiers](https://wiki.uooutlands.com/Modifiers). That
    ranks them, tier 1 → 9.
  - Colored boards are also ingredients, e.g. 4 × <color>wood board per material spellbook and
    martial manual ([Template:Inscription](https://wiki.uooutlands.com/Template:Inscription)).
  - **No gp value for any colored wood is published.** The wiki says only "Coloured resources will
    vary with demand" ([New Player Guide](https://wiki.uooutlands.com/New_Player_Guide)).
- **Other wood uses:** kindling via dagger/carpentry
  ([Camping](https://wiki.uooutlands.com/Camping)); ships and furniture
  ([Template:Carpentry](https://wiki.uooutlands.com/Template:Carpentry)); Bowcraft merged into
  Carpentry ([Carpentry](https://wiki.uooutlands.com/Carpentry)).
- **Lumber Resource Maps** hold 180 (dullwood) down to 60 (avarwood) resources in steps of 15. They
  expire 30 min after activation. Only one active map item is allowed at a time, and **scripted
  gump replies to the map menu are forbidden** (wiki). The harness must never automate map gumps.
  Source: [Resource Maps](https://wiki.uooutlands.com/Resource_Maps).

### `harness/data/woods.json`
- Follows the shared contract: name, log_graphics, log_hue, board_hue, min_skill, value_gp, notes.
- Two **extra** keys per wood: `base_chance_pct` (tree color chance) and `commodity_qty` (5000 or
  2500). Readers can ignore them.
- `value_gp` is filled only for ordinary: 9.5, the midpoint of the wiki's "around 9–10 gp" per
  board. All colored woods are `null`.

---

## 3. Commodity deeds

| Fact | Conf. | Source |
|---|---|---|
| Blank Commodity costs 5 gp from any Banker (buy list: "Blank Commodity 5gp, Vendor Rental Contract 100gp"). Graphic 0x1E23, hue 2241, name "blank commodity" | H | [Commodities](https://wiki.uooutlands.com/Commodities), [Banker](https://wiki.uooutlands.com/Banker), capture 204225 (Len the banker) |
| Use: double-click the blank → "What resource to you wish to create a commodity for?" → target the stack | H | Commodities; capture 204225 |
| Quantum: bandages 10 000. Regular ingots/boards/leather/reagents/arrows/bolts/cannonballs 5 000. **Colored ingots/boards/leather 2 500.** Feathers/blank scrolls 2 500. Empty bottles 500. Boards, not logs | M | [Commodities](https://wiki.uooutlands.com/Commodities) |
| **"Commodity for that item must be of at least 5000."** fires on a 12-board stack in the backpack. The 5 000 minimum is checked against the targeted stack | H | capture 204225 @15:11, journal `2026_09_29_20_42_27_journal.txt:755-756` |
| **Outlands commodities are NOT blessed and can be stolen or looted.** OSI deeds are blessed and must be filled in the bank box, so Outlands differs | M (Outlands) / contrast | [Commodities](https://wiki.uooutlands.com/Commodities), [uo.com](https://uo.com/wiki/ultima-online-wiki/items/commodity-deeds-commodity-deed-boxes) |
| Filled board commodity item: "crate" 19239 (0x4B27) for regular, 19227 (0x4B1B) for colored (hue = material hue) | L | [Item names and serials](https://outlands.uorazorscripts.com/script/23d5660a-f8fd-491d-97b3-1afa3d73c84d) |

**What deeds are used for:**
- **Trade.** Bulk goods become one light item ([Commodities](https://wiki.uooutlands.com/Commodities)).
- **Crafting ingredient**, in the "Board Commodity" unit
  ([Template:Carpentry](https://wiki.uooutlands.com/Template:Carpentry)):
  - Storage Locker: 1
  - Storage Shelf: 2
  - Large Ship / Large Dragonship: 1
  - Carrack: 2
  - Galleon: 3
  - Longship: 4
  - Ship of the Line: 5
- **Resource Stockpile.** Filled deeds of crafting ingredients can be deposited (target yourself, or
  double-click the deed and target the stockpile). If blank deeds are stored in the stockpile, it
  can give filled commodities back out
  ([Resource Stockpile](https://wiki.uooutlands.com/Resource_Stockpile)).

**Unknown (Outlands):**
- whether a stack above 5 000 is split or consumed whole
- whether the stack must sit in the bank box or backpack (the backpack passed the location check,
  if there is one)
- the weight of a filled deed
- how to redeem one outside a stockpile

---

## 4. The shelf (restocking) and lumberjack supplies

### 4.1 Storage Shelf and Storage Locker

"The shelf" on Outlands is the **Storage Shelf**. [INFERENCE] The user most likely means either the
Test Shard's public shelves or one of our own in the rental room.

| Fact | Conf. | Source |
|---|---|---|
| Stores frequently used resources and exceptional weapons/armor. You define a **Loadout** (per character, shared across all shelves), then press **Begin Resupply**: it equips missing gear and tops pack quantities up to the loadout amounts | M | [Storage Shelf](https://wiki.uooutlands.com/Storage_Shelf) |
| Categories cover what a lumberjack needs: Reagents, Bags/Pouches, **Trapped Pouches**, Bandages, Potions, **Axes**, **Blank Commodities**, Adventurer's Rope, spell scrolls by circle (Recall is circle 4) | M | Storage Shelf |
| Settings: **Resupply Runebooks** (adds recall scrolls from the shelf to runebooks/rune tomes you carry), Downgrade Resources, Stacked/Single-item container choice. Missing items are reported as one system message per item | M | Storage Shelf |
| Must be locked down or secured in a house **or inn room**. Crafted at 120 Carpentry (2 Board Commodities + manuals) | M | [Storage Overview](https://wiki.uooutlands.com/Storage_Overview), [Template:Carpentry](https://wiki.uooutlands.com/Template:Carpentry) |
| **Storage Locker:** same contents and interface, works from the **bank box**, cheaper (1 Board Commodity + diagrams/manuals; 110 Carpentry + 110 Tinkering) | M | [Storage Locker](https://wiki.uooutlands.com/Storage_Locker), Storage Overview |
| There is also a "Restock" mechanic: drag crates onto the shelf, or use "Restock". Resource Stockpile has a single-click "Restock" context entry | M | [Armor Crates](https://wiki.uooutlands.com/Armor_Crates), [Resource Stockpile](https://wiki.uooutlands.com/Resource_Stockpile) |
| **Test Shard:** fully stocked Storage Shelves, Resource Stockpiles etc. stand at the town centers of **North Prevalia** (two sets, both sides of the fountain) and **Corpse Creek** | M | [Test Shard](https://wiki.uooutlands.com/Test_Shard) |
| The Crafting Queue can't use a shelf/locker as a source | M | [Crafting Queue](https://wiki.uooutlands.com/Crafting_Queue) |

[INFERENCE]
- A Test Shard lumberjack could restock hatchets, recall scrolls, regs and trapped pouches for free
  from the North Prevalia shelf.
- Reaching it means leaving Shelter, which ends Young status permanently
  ([New Player Guide](https://wiki.uooutlands.com/New_Player_Guide)).
- The gump layout, gump id and button ids are **not captured**. The harness can't drive the shelf
  until a human demo is recorded (§8).

### 4.1a Getting items on the Test Shard (researched 2026-09-30, user question)

- **There is no item-creation command.** The Test Shard commands are `[TestRes`,
  `[TestIgnoreMaxDamageCap`, `[TestMaxMeleeDamageRolls`, `[TestMaxSpellDamageRolls`,
  `[TestBlessedGear` and `[Go` ([Template:CommandsTestShard](https://wiki.uooutlands.com/Template:CommandsTestShard),
  rev 29011). H
- **Items come from fully stocked public objects** at the North Prevalia fountain (two sets) and
  Corpse Creek ([Test Shard](https://wiki.uooutlands.com/Test_Shard), rev 29137). H
  - Resource Stockpile: crafting ingredients, Arcane Essence
  - Storage Shelf: crafted armour, weapons, tools, consumables
  - Prevalia Trunk: Prevalia Merchant items
  - Magic Item Vault, Trophy Hutch, Wardrobe, Flooring Rack, Landscaping Tilebox
- **Player editor statues** (the overseer, live): besides skills and stats, a "Player Testing"
  page (gump 0xB04DBB14, statue 0x40042777) offers buttons Maximum Wealth (100), Storage Shelf
  (Filled) (101), Resource Stockpile (Filled) (102), All Tomes (103), Complete All Codexes (104)
  and Level All Aspects (105). `[INFERENCE]` These give the character those items or states. Not
  pressed yet; the user decides.
- **Runebook:** crafted with Inscription ([Runebook](https://wiki.uooutlands.com/Runebook)). It
  needs arcane scrolls, which are loot-only on live
  ([Arcane Scroll](https://wiki.uooutlands.com/Arcane_Scroll)). `[INFERENCE]` On the Test Shard
  the Resource Stockpile is the likely source (the wiki doesn't list its contents).

### 4.2 Supplies and where they come from

| Item | Purpose | Source / price | Conf. |
|---|---|---|---|
| Hatchet (2+) | chop, convert | starting kit; Tinkering (5 ingots); shelf "Axes"; NPC price **unknown** | M / none |
| Recall scrolls | travel without Magery (0 Magery from a runebook) | Inscription ≥ 40 (2 blank scrolls each); player vendors; "in a pinch" Mage vendors "at a higher cost" (no number) | M ([New Player Guide](https://wiki.uooutlands.com/New_Player_Guide), [Template:Inscription](https://wiki.uooutlands.com/Template:Inscription), [Runebook](https://wiki.uooutlands.com/Runebook)) |
| Runebook | 16 runes, 10 recall charges | Inscription 100: 25 blank + 10 arcane scrolls. Not blessed. Bless deed from the Prevalian Merchant: 62 500 gp ([Storage Overview](https://wiki.uooutlands.com/Storage_Overview)) vs 50 000 gp (New Player Guide), **the two pages conflict** | M |
| Rune Tome | 26 runes, 50 recall + 50 gate charges (expansion deeds up to 999) | Inscription 120: 50 blank + 25 arcane scrolls. Bless 125 000 vs 100 000 gp (same conflict) | M ([Rune Tome](https://wiki.uooutlands.com/Rune_Tome)) |
| Blank recall runes | Mark home/spots | Mage vendors (price unknown). Blank rune hue 2882, marked 0, graphic 7956 | M / L (hue) |
| Public rune libraries | reach spots without marking | at many moongates (e.g. Outpost, Andaria); ExploreOutlands shows "Witchers Rune System" markers | M ([New Player Guide](https://wiki.uooutlands.com/New_Player_Guide), [Vendor Search](https://wiki.uooutlands.com/Vendor_Search)) |
| Reagents (Recall spell): Black Pearl, Bloodmoss, Mandrake Root | self-cast recall (40 Magery minimum, 100 % at 60, 2.0 s cast; not while in Heat of Battle) | Mage shops, player vendors, loot; NPC price **unknown** | M ([Magery](https://wiki.uooutlands.com/Magery)) |
| Trapped pouch | break Paralyze, warn of snooping thieves | crafted/Magic Trap; shelf category | M ([New Player Guide](https://wiki.uooutlands.com/New_Player_Guide)) |
| Locked box | items inside a locked box in your pack can't be stolen | carpentry/tinkering | M (New Player Guide); community scripts lock boards in a wooden box + copper key (L, DanStock) |
| Blank commodities | deeds | Banker 5 gp | H |

**Shelter-specific:**
- Buying from Shelter vendors or using the Shelter bank needs Young status
  ([Shelter Island](https://wiki.uooutlands.com/Shelter_Island)).
- Shelter has "Zara the scribe" and "Sherwin the mage" (journal
  `2026_09_29_20_42_27_journal.txt:758-759`). Those are candidate sellers of recall scrolls, blank
  runes and regs while TestWorth is still Young `[INFERENCE]`.
- Captured Shelter buy lists so far:
  - Dusty the cook: Skillet 25 gp, Rolling Pin 25 gp, Cheese 3 gp (164548)
  - Len the banker: Blank Commodity 5 gp, Vendor Rental Contract 100 gp (204225)

---

## 5. Prices and where to see them

### 5.1 Vendor Search (official)

- **What it is:** a **website**, <https://portal.uooutlands.com/vendor-search>. It lists goods on
  player vendors across the world. Log in with your OutlandsID.
- **In game:**
  - `[VendorSearch` launches that website.
  - `[Vendor` / `[Vendors` opens only your **own** vendors menu (owner/landlord/renter), not a
    market search.
  - Sources: [Commands](https://wiki.uooutlands.com/Commands), [Vendors](https://wiki.uooutlands.com/Vendors).
- **What it shows:**
  - items for sale; sorting and search
  - "Locate Vendor": the next time you focus a client, it opens a vendor-locator gump with a
    tracking arrow
  - a map pin, and a link to ExploreOutlands for nearby rune markers
- **Freshness:** new listings appear after about 5 min (deliberate delay); bought or removed items
  disappear within about 10 s.
- **Rate limits:** short- and long-term limits, undisclosed. Guild-only vendors aren't listed. Price
  charts are "planned", not live.
- **Conf.: M.** Source: [Vendor Search](https://wiki.uooutlands.com/Vendor_Search).
- **Harness implication:**
  - The page is a login-gated Angular SPA; checked 2026-09-29, the raw HTML has no data.
  - Scraping it would need the user's OutlandsID credentials and would run into undisclosed abuse
    limits. **The harness should not automate it.**
  - Prices from it are manual user input (or screenshots), recorded with a date.
  - It's unknown whether it covers the Test Shard. `[INFERENCE]` It probably covers only live
    vendors.

### 5.2 Player vendors (mechanics that affect prices)

- Placed in public houses only; **not in inn rooms**.
- Daily fee: "20 + 0.2 % of the listed price of all items" every 24 h (wiki wording).
- Stackables can be priced per item, and buyers can take partial stacks.
- "Purchase (Send to Bank)" adds 2 %, capped at 25 000 gp.
- Default price is 99 999, and an unpriced item isn't for sale for 6 h.
- Guild/alliance discounts of 0–100 %.
- **Conf.: M.** Source: [Vendors](https://wiki.uooutlands.com/Vendors).

### 5.3 Published prices

| Good | Price | Conf. | Source |
|---|---|---|---|
| Regular board | "around 9–10 gp" (player to player) | M (undated wiki) | [New Player Guide](https://wiki.uooutlands.com/New_Player_Guide) |
| Iron ingot / plain leather (context) | 12–14 gp / ~3 gp | M | same |
| Colored boards | "vary with demand": **no number published** | none | same |
| Board commodity (5 000 regular) | not published. [INFERENCE] 5 000 × 9–10 ≈ 45–50 k gp, if the per-board price holds in bulk | none | none |
| Blank commodity | 5 gp | H | capture + Banker |
| NPC buys boards? | Shelter NPCs refused ("You have nothing I would be interested in"). No NPC board buyer is documented | H (Shelter only) | LUMBER_LOOP §2 |

**Context only (not evidence of prices):**
- Players trade raw resources in the official Discord buy/sell channels
  ([forum 2019](https://forums.uooutlands.com/index.php?threads/lumberjacking-guide.1475/)).
- Community scripts go home at 500 resources (gugutz) or at 2 500 boards (DanStock). That shows how
  much players are willing to carry, not a value.

---

## 6. Inputs for a value-aware "head home" rule (LUMBER_LOOP §6)

§6 today uses a board count `Q` and `Q* = r·sqrt(2T/h)`. With several woods, the carried quantity
should be **value** `V = Σ_w n_w·v_w` rather than a count. Replace `r` with a value rate
`ρ = Σ_w r_w·v_w`; then `V* = ρ·sqrt(2T/h)` under the same model `[INFERENCE]`. Because rare woods
arrive in lumps, V can jump past `V*` with a single chop, so the check runs after every harvest
result. These are the concrete inputs:

1. **Per-wood value `v_w`** (gp/board, or a user-set utility weight): woods.json `value_gp`. Colored
   values are null today (§5.3), so the user supplies them (Open question 1).
2. **Per-wood harvest rate `r_w`**, which splits into:
   - the tree color mix `p_w` (the wiki base chances, §2), restricted to `min_skill ≤ effective skill`
   - the per-attempt success `s_w(skill, tool bonus)` (formulas §2; ×0.5 on Shelter)
   - logs per success (observed 5–10, mean 7.9; × (1 + P(double)) with Harvest Aspect)
   - attempt cadence (about 8–10 s per attempt in the live runs)
   - measured from `harvest_attempts` plus a per-wood outcome field (needs hue on the success
     event, §8)
3. **Depletion and regrowth:** logs per tree (~22–23 observed) and the respawn time (unknown). These
   set how fast `r` decays around a spot, and so the walking time between trees.
4. **Round-trip overhead `T` and its cash cost:**
   - walk or recall time
   - the 60 s harvest lockout after each recall/teleport
   - room entry/exit, conversion, storing
   - recall **cost per trip in gp** (scroll price or reagents; unknown, §4.2)
   - cast failure/fizzle rate
5. **Hazard split by what is lost:**
   - PK death: the whole carried value `V` plus unblessed/un-newbied gear, plus recovery time
     (resurrect and re-equip), from the death/travel research
   - thief: roughly one stack per successful steal `[INFERENCE]`. Stacks in a locked box are safe
     (wiki). So a single colored stack is at more risk than its share of V suggests
   - monsters: time lost and occasionally death
   - `h` per region and time of day, as in §6 (Gamma-Poisson)
6. **Protection state:** whether boards sit in a locked box, whether the hatchet/runebook is
   newbied or blessed. This changes the loss fraction per hazard.
7. **Capacity:**
   - max weight: Str-based, plus Camping (+200 st × Camping/100)
   - max item count: +50 × Camping/100
   - logs and boards weigh the same (0.025 st), so weight binds only at about 20 000 pieces, and
     count matters only once there are many colored stacks (up to 20: logs + boards × 10 woods)
8. **Tool state:** hatchet uses remaining (500 base) and spares carried. When the tool runs out,
   the trip ends.
9. **Deed quantum:** 5 000 ordinary / 2 500 colored boards per deed. This matters only if value is
   realized as deeds (trade or crafting). Below the quantum, stock just accumulates.
10. **Test Shard horizon:**
    - Rooms and houses are wiped daily at 00:00 UTC ([Test Shard](https://wiki.uooutlands.com/Test_Shard)).
    - Stored value is worth zero after midnight, which caps the useful horizon of any trip.
    - Test Shard gp and boards have no real value, so `v_w` is a policy choice there.
11. **Captcha handoff time:** the human answer latency, charged to `T` or to field time. It pauses
    harvesting, but carried value stays exposed.

---

## 7. Sources used (all read 2026-09-29)

**Outlands wiki:**
[Lumberjacking](https://wiki.uooutlands.com/Lumberjacking) ·
[Harvesting](https://wiki.uooutlands.com/Harvesting) ·
[Smart Harvest](https://wiki.uooutlands.com/Smart_Harvest) ·
[Mining](https://wiki.uooutlands.com/Mining) ·
[Commodities](https://wiki.uooutlands.com/Commodities) ·
[Banker](https://wiki.uooutlands.com/Banker) ·
[Storage Shelf](https://wiki.uooutlands.com/Storage_Shelf) ·
[Storage Locker](https://wiki.uooutlands.com/Storage_Locker) ·
[Storage Overview](https://wiki.uooutlands.com/Storage_Overview) ·
[Resource Stockpile](https://wiki.uooutlands.com/Resource_Stockpile) ·
[Test Shard](https://wiki.uooutlands.com/Test_Shard) ·
[Vendor Search](https://wiki.uooutlands.com/Vendor_Search) ·
[Vendors](https://wiki.uooutlands.com/Vendors) ·
[Commands](https://wiki.uooutlands.com/Commands) ·
[New Player Guide](https://wiki.uooutlands.com/New_Player_Guide) ·
[Shelter Island](https://wiki.uooutlands.com/Shelter_Island) ·
[Resource Maps](https://wiki.uooutlands.com/Resource_Maps) ·
[Modifiers](https://wiki.uooutlands.com/Modifiers) ·
[Harvest Aspect](https://wiki.uooutlands.com/Harvest_Aspect) ·
[Camping](https://wiki.uooutlands.com/Camping) ·
[Skill Mastery](https://wiki.uooutlands.com/Skill_Mastery) ·
[Skills & Stats](https://wiki.uooutlands.com/Skills_%26_Stats) ·
[Magery](https://wiki.uooutlands.com/Magery) ·
[Runebook](https://wiki.uooutlands.com/Runebook) ·
[Rune Tome](https://wiki.uooutlands.com/Rune_Tome) ·
[Template:Tinkering](https://wiki.uooutlands.com/Template:Tinkering) ·
[Template:Carpentry](https://wiki.uooutlands.com/Template:Carpentry) ·
[Template:Inscription](https://wiki.uooutlands.com/Template:Inscription) ·
[Starting Items](https://wiki.uooutlands.com/Starting_Items) ·
[Trade Caravans](https://wiki.uooutlands.com/Trade_Caravans) ·
[Rental Room System](https://wiki.uooutlands.com/Rental_Room_System) ·
[Armor Crates](https://wiki.uooutlands.com/Armor_Crates)

**Community (L unless corroborated):**
- [gugutz lumber.razor](https://raw.githubusercontent.com/gugutz/outlands-razor-scripts/master/resources/lumber/lumber.razor)
- [DanStock lumber autopilot](https://outlands.uorazorscripts.com/script/c87ec994-4970-4e52-8035-5191a8c51552)
- [Item names and serials](https://outlands.uorazorscripts.com/script/23d5660a-f8fd-491d-97b3-1afa3d73c84d)
- [Forum: Lumberjacking guide? (2019)](https://forums.uooutlands.com/index.php?threads/lumberjacking-guide.1475/)

**Generic UO (contrast only):**
[RunUO Lumberjacking.cs](https://raw.githubusercontent.com/runuo/runuo/master/Scripts/Engines/Harvest/Lumberjacking.cs) ·
[uo.com commodity deeds](https://uo.com/wiki/ultima-online-wiki/items/commodity-deeds-commodity-deed-boxes)

**Local evidence:**
- `harness/data/harness.db` `harvest_attempts` (51 rows, live 2026-09-30 UTC)
- `python harness/loop_mine.py timeline` over `logs/session_*` (buy lists, harvest messages)
- ClassicUO journals under `…/ClassicUO/Data/Client/JournalLogs/` (read-only)
- artdata via `harness/uomap.py`

---

## Open questions for the user

1. **Wood values.**
   - What should `v_w` be for colored woods: gp you read off Vendor Search, or a simple utility
     ladder (e.g. tier multipliers)?
   - Is 9.5 gp/board acceptable for ordinary?
   - On the Test Shard, gp is notional. Do you want value in gp at all, or "boards banked, weighted
     by color"?
2. **Which shelf?**
   - The public, fully stocked Storage Shelves on the Test Shard (North Prevalia / Corpse Creek)?
   - Or a shelf/locker of our own? That costs 1–2 Board Commodities plus crafting manuals to build.
3. **When does TestWorth leave Shelter?** Leaving ends Young status for good: no Shelter bank or
   vendors, harvesting opens to PvP. Colored woods above bronzewood and skill above 80 need it.
4. **Recall method:** recall scrolls from a runebook/rune tome (0 Magery), or train Magery to 60 and
   carry Black Pearl/Bloodmoss/Mandrake? Scrolls are simpler to automate. Magery is cheaper per
   trip once trained `[INFERENCE]`.
5. **Should the agent ever spend gold** (NPC buys, player vendors), and with what per-day cap?
6. **Commodity deeds under the daily Test Shard wipe:** stay with "no deeds" (LUMBER_LOOP §12.4),
   or deed at the North Prevalia stockpile?
7. **Carry policy for colored stacks:** lock them in a box (wooden box + key) to protect them from
   thieves at the cost of extra actions?
8. **Hazard priors per region** for `h` (§6): which forests do you consider PK-heavy?

## What the harness needs (demos, captures)

The proxy already parses buy lists (`0x74`), context menus, gumps and cliloc messages. Each item
below is a human-played session through the proxy, or a passive measurement in the running loop.

1. **NPC buy lists:**
   - mage (Sherwin), scribe (Zara), and whichever Shelter NPCs sell hatchets, recall scrolls,
     blank runes and reagents
   - context menu "Buy" only, then cancel
   - result: prices and item graphics from `VENDOR buy list` lines
2. **A colored chop.** The first "You chop some <color> logs" event with the resulting log item, so
   we can confirm hue 24xx and graphic 0x1BDD. Then convert it, to see whether the board keeps the
   hue. The loop should log hue on each success so `harvest_attempts` gets a wood column
   (ThreatsLedger/ledger owner).
3. **Hatchet wear.** Single-click the hatchet ("(N uses remaining)") before and after a counted
   series of attempts. This settles per-attempt vs. per-success wear, and captures the break
   message.
4. **Tree respawn.** Revisit depleted trees at +15/30/45/60 min (passive in the loop, as spot
   eligibility probes). This gives the respawn time and logs per tree.
5. **Storage Shelf demo** (Test Shard, North Prevalia; after the Shelter decision):
   - open the shelf
   - edit the Loadout (hatchets, recall scrolls, regs, trapped pouches, blank commodities)
   - Begin Resupply
   - toggle "Resupply Runebooks"
   - result: gump id, button ids, text-entry ids and the "missing item" messages
6. **Commodity deed at quantum** (Test Shard stockpile or accumulated boards):
   - a successful deed from ≥ 5 000 boards: what is consumed, the deed's graphic, hue, name and
     weight
   - whether it works from the backpack
   - redeeming it
7. **Recall-from-runebook flow** (shared with travel research): the runebook gump id, the "recall
   (charge)" button, drag-drop of recall scrolls to add charges, and the 60 s lockout after it.
8. **Vendor Search prices (manual):** the user notes board, colored-board and board-commodity prices
   with a date. The harness keeps them as a dated price table; it never scrapes the portal.

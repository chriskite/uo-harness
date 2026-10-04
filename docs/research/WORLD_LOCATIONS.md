# WORLD_LOCATIONS.md — places to go: the Witcher rune system, points of interest, travel networks

Research for letting the lumber job (docs/LUMBER_LOOP.md §6) and the overseer reach the whole
overworld, not only the land around banks. Web research and the client's own data files, read
2026-10-03; nothing here was tried in-game. Confidence: **H** = primary source or our own
measurement, **M** = community source, **L** = inference.

## 1. The Witcher rune system

| Fact | Conf. | Source |
|---|---|---|
| A community rune set made by the player **Witcher** for treasure-map hunting: runes marked next to the treasure-map dig locations, kept in **14 rune tomes** ("5 old world, 5 new world, 2 expansion and 2 wildlands"; the 2 expansion tomes cover the ~1 400 dig spots staff added to the old lands later). He sells the set at 50 000 gp per tome | M | [Witcher's (updated) Guide to Tmaps](https://forums.uooutlands.com/index.php?threads/witchers-updated-guide-to-tmaps-how-you-can-and-why-you-should-be-running-your-own-tmaps.3439/) (forum, 2021, edited 2024-06) |
| Runes are **numbered 1–353** (a few with a letter suffix, e.g. `4a`). Tome names carry their range: 1-24, 25-49, 50-75, 76-99, 100-123, 124-149, 150-175, 176-201, 202-227, 228-249, 250-275, 276-301, 302-327, 328-353 | M | ["Witcher Travel" Razor script](https://outlands.uorazorscripts.com/script/de9a4695-2f68-47ee-af8c-46dbe65542bb) (2025-02) |
| The rune number → coordinates table is public: `https://exploreoutlands.com/locations/Witchers_Tmap_Locations.csv`, rows `id,x,y`, **359 rows** (facet 0), x 239–4450, y 63–3795 | H (fetched 2026-10-03) | ExploreOutlands (the site's "Witcher's Rune System" layer) |
| **Public copy: the Cambria Rune Library** at (1706, 3181), just south of the Cambria moongate (1705, 3154). The script lists the 14 tome serials there (0x546B015F, 0x546AF790, 0x43757C23, 0x43756325, 0x43755561, 0x43752730, 0x437548AB, 0x43751C83, 0x43752E07, 0x43755E41, 0x4B99B20C, 0x546ACD06, 0x4374EB77, 0x4374E53D; 2025-02, may have changed) | M | Witcher Travel script; Witcher's guide ("One public library exists at Cambria moongate") |
| A second full Witcher set, plus tomes with **every Point of Interest in alphabetical order** and all dungeons, shrines and towns, is in the player-run **Adventure Time guild hall** rune library (location only in a forum screenshot). Its owner says the tomes are kept "full of Recall scrolls" (charges) | M | [Free Vendor Locations & Rune Library](https://forums.uooutlands.com/index.php?threads/free-vendor-locations-rune-library.5848/) (forum, 2025-03 to 2026-03) |
| Other public rune libraries stand "around many of the moongates (e.g. Outpost, Andaria)" and help "for the initial journey to unlock Camping hiking spots" | H | [New Player Guide](https://wiki.uooutlands.com/New_Player_Guide) |
| How players use a library tome: double-click the tome (locked down, so it never enters the pack), open the rune's page, Recall (own Magery + reagents, a scroll, or the tome's charges) or Gate. The script needs to rename a tome page, so it wants the books in the pack or a tome with access rights | M | Witcher Travel script |
| **Gump ids match ours:** the script's runebook gump 1551740969 = **0x5C7DB029** and rune-tome gump 167090027 = **0x09F5976B**, the ids `harness/escape.py` already parses (docs/NOTES.md "Runebook and rune tome gumps"). Its tome rename gump is 1263030975 = 0x4B4852BF (not captured by us) | H (cross-check) | script vs our captures |

So a character that can recall (Hackworth has Magery) can stand in the Cambria library and reach
any of ~359 wilderness points spread over the whole map, without marking anything itself.

## 2. Points of interest and the World Atlas

| Fact | Conf. | Source |
|---|---|---|
| **The client ships the Atlas marker packs**, read-only, in `ClassicUO/Data/Client/*.xml` (ClassicUO world-map marker format, facet 0): `POI.xml` "Atlas - Points of Interest" **251**, `Healer_Caravans.xml` 42, `Moongates.xml` 10, `Townships.xml` 10, `Shrines.xml` 9, `Dungeons.xml` 14, `Sub-Dungeons.xml` 5, `Dungeon_Gates.xml` 438 (golden/crimson gates, stairs), `PvP_Locations.xml` 79, `Banks_and_Healers.xml` 40 (11 banks), plus New Lycaeum, Kings Faire and quest markers | H | install dir listing 2026-10-03 |
| The wiki's [Points of Interest](https://wiki.uooutlands.com/Points_of_Interest) page lists the same places in three groups: Original Lands, First Expansion, Second Expansion "Wildlands" | H | wiki |
| ExploreOutlands serves the same packs (`locations/POIs.json`, `moongates.json`, `townships.json`, …) plus treasure chests (`tchest.json`), PvP events, progressive spawns, smugglers and shop locations | H (fetched) | `https://exploreoutlands.com/locations/datasets.csv` |
| **Hiking** (60 Camping or 60 Cartography) goes to any Atlas entry, but each hiking location must first be **unlocked by visiting it and securing a campfire there** | H | New Player Guide; Camping details in TRAVEL_DEATH.md §1.6 |
| Many POIs are monster camps by name (Brigand Camp 1–3, Orc Fort 1–3, Lizardmen Fort, Savage Fort, Daemon Keep, Dragon Roost, Necromancers Swamp, …) | H (names) | POI.xml |

## 3. What this means for lumbering (measured on the map, 2026-10-03)

Tree statics from `map0.uoo` (`uomap.UoMap.find_trees`, the runner's own candidate source):

| Set | Points | Median trees within 15 tiles | ≥ 25 trees within 15 | More than 110 tiles from every bank |
|---|---|---|---|---|
| Witcher runes | 359 | 7 | 50 | **354** |
| POI.xml | 251 | 5 | 29 | **242** |
| Healer caravans | 42 | 7 | 3 | 42 |

- Within 30 tiles, 15 POIs have ≥ 100 trees (the Terran seed area, which yielded 1 200–2 000 logs
  per field hour, has 115 in a 60×60 window). Top: **Loggers Cabin** (1141, 2284) 171, Zebra Falls
  (992, 1380) 152, Haunted Manor (4005, 1148) 135, Burnt Farmlands (1090, 1897) 131, Ruined House
  (1938, 1013) 128, Wintertop (3535, 526) 125, Brigand Camp 3 (1413, 1377) 121, … Cedar Forest
  (3244, 583) 108, Logging Camp (1974, 826) 104.
- Almost all of these places lie outside the band `ctl lumber discover` searches today (30–110
  tiles from a bank marker). With walking to a bank as the only way home, they're out of reach in
  practice; with recall they're a 2 s cast away (+ the 60 s harvest lockout after any travel,
  TRAVEL_DEATH §2).

## 4. Design: user-approved 2026-10-03, built the same day

The user approved the design with one change (commit the Witcher table: it's small and the
numbers rarely change) and answered the open questions:
1. The library tome is a regular rune tome, locked down in a house and usable by anyone.
2. Use public libraries heavily for now; we'll buy or copy our own set later.
3. Every lumber character will have at least 60 Camping and 60 Magery (likely 80 Magery).
4. Use the Cambria library.

A spot is a tree area plus **how we get there** (`access`) and **how we get home** (`home`):

- **Built:**
  - `harness/data/witcher_runes.json`: 360 runes, id → name and the ExploreOutlands dig tile.
    Names were read live from the 14 Cambria tomes on 2026-10-03.
  - `harness/data/rune_libraries.json` (since 2026-10-04): every rune library we know, per tome its
    serial, title, position and rows (name as the tome shows it, the tile the rune lands on). Cambria's
    14 tomes (tiles = the Witcher table; its runes landed exactly there) and the **DTF guild house**
    (36 tomes, 839 runes, tiles read from each rune's detail page; §5). Read by `harness/places.py`,
    which also reads the client's Atlas XMLs.
  - `escape.recall(io, book, rune="286")` finds a tome row by name and recalls (a tome charge,
    else the spell). Overseer use: `ctl act recall --witcher 286` (the library you stand in),
    `ctl act recall --library dtf --rune NAME [--tome TITLE]`; `ctl runes find|near` says which rune.
  - Spot `access {"method": "witcher", "rune": N, "library": "cambria"}`: the runner walks to the
    tome, recalls out, and waits out the 60 s lockout. Spot `home {"method": "recall"}`: it recalls
    with our book's default rune (the PK escape's) and walks to the spot's banker (LUMBER_LOOP §13).
  - `ctl lumber discover --from witcher`: since 2026-10-03 (user) one grove per rune, up to 200
    tiles from it. Windows of ≥ 25 trees are ranked across all runes, most trees first and then
    nearest to the rune, and the rune's landing needs a walking route of ≤ 300 tiles into its
    window. Runes named after monster places, towns and learned guard points are left out. Until
    then it searched only 21 tiles around each rune (routes ≤ 60): 62 candidates out of 360 runes,
    212 with too few trees. A throwaway probe of the 200-tile search on 2026-10-03 found 126
    groves (25–204 trees, median 96 tiles from the rune, median walk 113 tiles; 433 route checks
    in ~250 s).
  - The planner treats standing at a library as being at hand for every rune spot it can reach
    from there (any library holding the spot's rune). The runner recalls out from the library
    nearest to where the trip starts (`places.library_for`). The walk to the library, both recalls
    and the lockout are part of the trip overhead (LUMBER_LOOP §6).
- **Not built:**
  - hiking to Atlas POIs (needs the campfire and Atlas gump flows, and each POI unlocked by a visit)
  - marking our own runes at the best spots (needs a Mark capture; Magery ≥ 60 is a given now)
  - reading a runebook's sextant lines into tiles (our own book: names only)

**Live trial (Hackworth, 2026-10-03):**
- With `ctl act recall --witcher 291`, standing by the tomes, the agent found row 15 of tome
  276-301. It recalled with a public charge and landed exactly on the table's tile (1526, 3040).
- The first manual try had already reached rune 286 at (1765, 2007) the same way, for 10 mana.
- Nine of the 14 tomes had charges (10–44 of 50) that morning; five had none.
- The Cambria moongate area is a player vendor mall with no banker seen. The Cambria bank marker is
  at (1750, 3003), about 180 tiles north of the library: that is the default home bank and the
  per-trip walk out.

Still open:
- A runebook with a home rune at the Cambria bank, and a hatchet, for Hackworth. Without them he
  can recall out but can't come back by recall.
- Do we buy a Witcher set (50 000 gp per tome, 14 tomes) or a few tomes for the regions we use?

## 5. The DTF guild house rune library (live 2026-10-04, Outland Dan)

Outland Dan is in the guild **DTF**, whose house keeps a rune library: the home of our first real
lumberjack and, from now on, the place to go for any destination our own book doesn't have (user,
2026-10-04). Read with `ctl act read_tomes dtf`; data in `harness/data/rune_libraries.json`, the
gumps and quirks in docs/NOTES.md "Rune libraries: the DTF guild house".

- **Where:** 36 locked-down rune tomes in the house at (4150, 1427–1429) and (4152–4154, 1431).
  From (4152, 1429) every one is within the 2-tile reach. Dan's own runebook's default rune is
  "DTF Loot Chest", in the house, so recall home lands there.
- **What:** a full **Witcher set** (15 tomes, "Witcher 1-22A" … "328-353": all 360 runes, rows named
  just "N", lower-case letter suffixes like "22a") plus 21 tomes of named places: towns/shrines/
  alliances, public and alliance dockmasters, A–Z points of interest (5 tomes), dungeons and their
  entrances ("Bad Places", "Bad Places II", "Outside Bad Places", "DFP …"), factions, Time Quests,
  Wildlands, "Friendly Houses 1" and a 2-rune "Sellbows" tome. 839 runes, every tile read. Every
  tome had 45–50 recall charges when read.
- **Its Witcher runes don't land where Cambria's do:** they were marked by someone else. 58 of the
  359 with a dig tile differ from the ExploreOutlands table, most by 1–17 tiles, rune 284 by 36,
  rune 165 by 100 (x 4143 instead of 4043: a typo in the CSV or a different spot); rune 83, which
  the table lacks, lands at (1155, 1850). So a library row's own tile, not the Witcher table, is
  where a recall lands; lumber discovery from this library uses it.
- **Which library a lumber trip uses:** the one holding the spot's rune nearest to where the trip
  starts, so Dan's trips go from the guild house and Hackworth's from Cambria, whatever library the
  spot was discovered from.

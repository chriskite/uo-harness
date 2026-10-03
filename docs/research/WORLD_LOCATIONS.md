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

## 4. Proposed design (not built; needs the user's go-ahead)

A spot becomes a tree area plus **how we get there** and **how we get home**:

- **Ways in:** walk from a bank (today); moongate + walk; recall to a Witcher rune via a library
  tome (Cambria, or a set we buy or copy); hike to an unlocked Atlas POI; recall to a rune we
  marked ourselves (Mark: 60 Magery, a blank rune 20 gp at Shelter).
- **Ways home:** walk to the bank (today); recall to a home rune next to a bank (our runebook's
  default rune is already the PK escape, `escape.py`); then bank as now.
- **Candidates from places, not only from banks:** every Witcher point, POI and caravan with
  enough trees around it becomes a candidate spot, with its access method, the name-based monster
  hint (camps, forts) as a hazard prior, and the travel cost from the methods it needs (recall
  cost in reagents or scrolls, the 60 s lockout, the trip to the library).
- **Marking our own runes** at spots that prove good (the optimizer's top spots), so we stop
  depending on a library for them; the rune name ties it to the spot id (TRAVEL_DEATH §1.4).
- **Data:** read the client's Atlas XMLs at runtime like `Banks_and_Healers.xml` (read-only install
  data). The Witcher table is third-party data from ExploreOutlands: fetch it on demand into the
  gitignored store rather than committing it, and verify a rune before trusting its slot (the
  library's tomes can be reordered; the script warns about exactly that).

Open questions (need the user or a capture):
1. Using a public library tome: does Recall from a locked-down tome need our own Magery/reagents,
   or does it spend the tome's charges? Capture one use at the Cambria library.
2. Rights: is an agent using a public library book a problem socially (staff or the owner)? It's
   what the libraries are for, but the agent would use them a lot.
3. Hackworth's Camping and Magery values (hiking needs 60 Camping; Mark needs 60 Magery).
4. Where exactly the Adventure Time guild hall is (forum screenshot only).
5. Do we buy a Witcher set (50 000 gp per tome, 14 tomes) or a few tomes for the regions we use?

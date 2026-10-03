# Travel, leaving Shelter Island, death and recovery on Outlands

Research for the overworld phase (overseer, overworld lumberjacking, death recovery, rune library,
restocking). Offline only: wiki, official patch notes, forum guides, client-shipped data and our own
captures/journals. Nothing here was tested in-game for this document. Written 2026-09-29.

**Confidence labels** (every row carries one):

- **H**: current Outlands wiki page, official patch note, or our own capture/journal.
- **M**: older community guide (date given), community map-marker pack shipped with the client, or a
  wiki statement that conflicts with another source.
- **L**: `[INFERENCE]`, meaning generic RunUO/OSI behaviour or a string present in `Cliloc.enu`.
  The client ships the full OSI string table, so the presence of a string doesn't show that Outlands
  sends it.

Citations are reference-style links. Wiki revision ids are listed in §8 so the text can be re-checked
against the version read.

---

## 1. Travel

### 1.1 Magery travel spells

| Spell | Circle | Reagents | Mana | Cast time | Min / 100 % Magery | Conf. | Source |
|---|---|---|---|---|---|---|---|
| Recall | 4th | Black Pearl, Bloodmoss, Mandrake Root | 11 | **2.00 s** (4th-circle default is 1.25 s; Recall is listed separately) | 40 / 60 | H | [SpellCircles][w-sc] |
| Mark | 6th | Black Pearl, Bloodmoss, Mandrake Root | 20 | 1.75 s | 60 / 80 | H | [SpellCircles][w-sc] |
| Gate Travel | 7th | Black Pearl, Mandrake Root, Sulfurous Ash | 40 | **4.00 s** | 70 / 90 | H | [SpellCircles][w-sc] |
| Teleport (the rope uses its rules) | 3rd | Bloodmoss, Mandrake Root | 9 | 1.00 s | 30 / 50 | H | [SpellCircles][w-sc] |

- **Anyone can recall from a scroll.** Recall can be cast "from a scroll in a rune book or rune tome,
  or from a scroll onto a loose marked rune, even with 0 Magery skill". **H** [SpellCircles][w-sc],
  [Runebook][w-rb]
- Casting from a scroll adds +20 effective Magery, which only affects cast chance. Casting recovery
  is 0.2 s. **H** [Magery][w-mag]
- `[AutoUseSpellScrolls` makes the server use spell scrolls from the backpack automatically when
  casting. **H** [Commands][w-cmd]
- Gate from a runebook needs 50 Magery. For a Rune Tome the wiki says "50 Magery (or just 40 mana?
  Needs Testing)". **H/M** [Runebook][w-rb], [Rune Tome][w-rt]
- **Dungeons:** Recall, Mark and Gate are blocked, except within 8 tiles of a Golden (level-1)
  dungeon moongate. You also can't recall or gate *into* a dungeon unless the target is within 8 tiles
  of one. Crimson gates (levels 2–3) don't allow it. **H** [SpellCircles][w-sc],
  [Dungeon Moongates][w-dmg], [About][o-about]
- A recall scroll or a runebook charge still needs mana: `[INFERENCE]`, not stated anywhere →
  demo D7.

### 1.2 Fizzle and interruption

| Fact | Conf. | Source |
|---|---|---|
| Hostile player spells of circle 4–8 interrupt the target 100 % of the time. Circles 1–3 interrupt 100 % first, then 0 % inside a 5 s per-circle window | H | [Magery][w-mag] |
| A 2018 community guide says circles 1–3 then drop to 25/50/75 % in the window. That conflicts with the current wiki; take the wiki | M | [PvP guide 2018][f-pvp] |
| Creature/environment damage interrupts spells. Resisting Spells gives "(Effective Magic Resist / 100) chance to avoid Spell Interruption from Creature/Environment Damage", and Inscription gives the same | H | [Resisting Spells][w-rs], [Inscription][w-ins] |
| Equipping any item (weapon, clothing, armour) during a cast interrupts it. Drinking a potion mid-cast doesn't, but drinking one while holding a precast cancels the spell | H | [Combat Overview][w-co] |
| **Movement while casting: not documented.** The Outlands client has a "Block movement while casting" option under Options → Spells, which suggests the server doesn't simply freeze you. Upstream ClassicUO has no such option (grep of `ClassicUO-main/src` found none) | M | [Options][w-opt] |
| RunUO default: you're held in place during a cast ("You are frozen and cannot move.", cliloc 500111) | L | Cliloc.enu 500111 |
| Heat of Battle (your own aggressive action against a player, including harmful spells or re-targeting an attacker) blocks Recall, Gate, all moongates and entering an inn room. Monster combat is **not** listed as a blocker for recall | H | [Heat of Battle][w-hob], [Magery][w-mag] |
| Candidate message strings: "The spell fizzles." 502632, "Your concentration is disturbed, thus ruining thy spell." 500641, "More reagents are needed for this spell." 502630, "Wouldst thou flee during the heat of battle??" 1005564, "That rune is not yet marked." 501803, "There are no charges left on that item." 502412, "This book needs time to recharge." 502403, "Something is blocking the location." 501025 | L | local `Cliloc.enu` via `harness/uo/cliloc.py` |

Implication for the red-name escape (LUMBER_LOOP §11.2): a recall is ≥ 2.0 s from cast start, plus
reaction time and a possible failure. With no Resisting Spells, one monster hit ruins it. A PK's
4th-circle-or-higher hostile spell always interrupts it. The escape has to start **before** contact
(on sighting), and there has to be a fallback: re-cast, or run to break line of sight.

### 1.3 Runebooks, Rune Tomes, recall scrolls and charges

| Fact | Conf. | Source |
|---|---|---|
| Runebook: up to **16 runes** and up to **10 recall scrolls**. Not blessed by default | H | [Runebook][w-rb] |
| "Always have 10 recall scrolls in the runebook" = a full book: 10 is the runebook maximum | H | [Runebook][w-rb] |
| Adding charges: "drag and drop a Recall scroll onto a rune book or tome to add a charge", so **1 scroll = 1 charge**. Travel: "click on the blue gem beside the location" | H | [New Player Guide][w-npg] |
| Crafting: Runebook = 25 blank + 10 arcane scrolls, 100 Inscription. Rune Tome = 50 blank + 25 arcane scrolls, 120 Inscription | H | [Tomes][w-tomes] |
| Rune Tome: 26 runes, a base of 50 recall + 50 gate scrolls. Expansion deeds go up to 999 recall charges. Runes and scrolls are added by drag and drop; runes can be reordered with Manage Runes | H | [Rune Tome][w-rt] |
| Bless: Runebook Bless Deed from the Prevalian Merchant, **62 500 gp** ([Runebook Bless Deed][w-rbd]). The New Player Guide says 50k for books and 100k for tomes. Prices conflict, so take the deed page (newer rev) | M | [Runebook Bless Deed][w-rbd], [New Player Guide][w-npg] |
| A blessed Rune Tome doesn't drop from the backpack on death and can't be stolen | H | [Rune Tome][w-rt] |
| Text commands: `[Recall <name>` casts Recall on the first rune with a matching name in any runebook or tome in the backpack. **`[RecallCharge <name>` uses a book/tome charge.** `[Gate`/`[GateCharge` work the same way | H | [Commands][w-cmd] |
| These commands aren't in the Assistant's `outlandscommands.def` shipped with the client. They're wiki-only until tested | M | local `ClassicUO/Data/Plugins/Assistant/outlandscommands.def` |
| Recall scrolls: crafted at ≥ 40 Inscription, sold on player vendors, or sold by Mage NPC vendors "at a higher cost" | H | [New Player Guide][w-npg] |
| Storage Shelf setting **"Resupply Runebooks"**: on resupply the shelf adds recall scrolls it holds to any runebook/tome the player carries | H | [Storage Shelf][w-shelf] |
| Runebook Transcriber: copies a filled book into an empty one (entries, default rune, name). Sold for gold only on the Prevalia Market | H | [Runebook Transcriber][w-rbt] |

`[RecallCharge home` is the most automation-friendly path: one speech line, no gump, no target
cursor. The runebook gump (blue-gem buttons) is the fallback. Neither has been captured → demos D6/D7.

### 1.4 Runes and rune libraries

| Fact | Conf. | Source |
|---|---|---|
| Blank recall runes: bought from Mages "in any mage shop located in any town" | H | [Blank Recall Rune][w-rune] |
| Mark the rune with the Mark spell (60 Magery, see §1.1). Double-click a marked rune to rename it. Store runes in a Runebook or Rune Tome | H | [Blank Recall Rune][w-rune] |
| "Players may mark most locations with a few exceptions." The exceptions aren't listed. Known ones: dungeons away from golden gates, and rental rooms (no recall or gate *into* a room) | H | [SpellCircles][w-sc], [Rental Room System][w-rrs] |
| **Public rune book libraries** stand "around many of the moongates (e.g. Outpost, Andaria)" | H | [New Player Guide][w-npg] |
| Player-run libraries exist too, e.g. the Adventure Time guild hall's library of tomes with Points of Interest (forum, 2025) | M | [Forum: Rune Library][f-runelib] |
| **Cortina the Runekeeper** (1909,2567), outside the New Player Dungeon entrance, gives quest "Rune All You Like": **earn 2,500 gold inside the New Player Dungeon** (monster kills or dungeon chests); no failure conditions; eligible only below 5,000,000 gold earned over all accounts (OutlandsID; Hackworth's account shows 1,157). Reward: a **Blessed "New Player Runetome"** pre-filled with runes to places important for new players (which ones: unknown until earned). Same patch: a **Bonded Horse** quest at the Prevalia Stables (harvest apples from an Apple Cart in the city) and a Codex Starter Bundle quest (kill 25,000 gold of creatures), same eligibility | H | live gump 2026-10-04 (docs/NOTES.md), [Patch notes 2025-07-07](https://forums.uooutlands.com/index.php?threads/patch-notes-for-july-7-2025-new-player-patch.5941/) |
| **Witcher rune system and the client's Atlas packs** (2026-10-03): 14 community rune tomes with ~359 numbered runes over the whole map, a public set at the Cambria Rune Library (1706, 3181); the POI/caravan/shrine/dungeon marker packs ship with the client in `Data/Client/*.xml`; hiking locations must first be unlocked by visiting them and securing a campfire. Details and what they mean for lumbering: [WORLD_LOCATIONS.md](WORLD_LOCATIONS.md) | H/M | see there |

For the harness, "rune library" means our own catalogue: rune name → (book serial, slot, facet,
x, y, purpose: home/field/bank/healer), stored in `Memory.meta`, plus in-game books named for
`[RecallCharge`. Rune names have to be unique across the books carried, because the command takes
the *first* match ([Commands][w-cmd]).

### 1.5 Moongates

| Fact | Conf. | Source |
|---|---|---|
| Public ("Town", dark blue) moongates stand outside every township and are always open. They reach all townships plus The Arena | H | [Moongates][w-mg] |
| Other gate types: player gates (blue); gold one-way dungeon exits (upper and lower floors); **death gates (monochrome), visible only to dead players, "potentially saving time when trying to recover your body's contents"**; faction gates; event gates at banks | H | [Moongates][w-mg] |
| A dungeon moongate sends you to a random location inside each town. Shelter Island and The Arena aren't offered; if either was your last selection you default to Prevalia (blue) or Corpse Creek (red) | H | [Dungeon Moongates][w-dmg] |
| `[Moongate` "will attempt to use the nearest moongate to player" | H | [Commands][w-cmd] |
| The moongate gump is documented only as images on the wiki. Layout and gump id are unknown → demo D10 | H | [Moongates][w-mg] |
| No impassable items within 4 tiles of a public moongate | H | [Moongates][w-mg] |
| Public moongate coordinates (facet 0): Anchor's Rest 3993,524 · Andaria 2331,647 · Cambria 1705,3154 · Corpse Creek 954,822 · Horseshoe Bay 2007,2077 · Outpost 2974,621 · Prevalia 1474,1497 · Totem 3900,2803 · **Shelter Island 1985,2533** · Terran 867,1558 | M | client `Data/Client/Moongates.xml` ("Atlas - Moongates" pack) |

### 1.6 "Hike" = Camping fast travel through the World Atlas

| Fact | Conf. | Source |
|---|---|---|
| Hiking is fast travel to any World Atlas entry. It needs **60 Camping or 60 Cartography** | H | [Camping][w-camp], [World Atlas][w-atlas] |
| You must stand at a **secure campfire**. Kindling comes from a dagger on a tree or from Carpentry (0 skill, boards). A fire secures after `(57..60) − 45 × Camping/100` s and lasts 10 min. An active Explorer's Pack effect removes the campfire requirement | H | [Camping][w-camp] |
| Conditions: no combat of any type within 30 s, no combat with a player within 2 min. The player is **frozen 5 s** before departure | H | [Camping][w-camp] |
| Arrival is random within 5 tiles of the entry; for townships, anywhere within the town limits. Dungeon entries have Hike Outside / Hike Inside (inside = near a random golden gate) | H | [Camping][w-camp], [World Atlas][w-atlas] |
| Every character starts with a blessed/newbied Atlas; `[Atlas` gives a new one. Categories: Custom, Dungeons, Healer Caravans, Moongates, New Player, Personal (hike to house/ship), POI, Shrines, Sub-Dungeons, Townships. 3 custom entries (+2 or +4 with account enhancements) | H | [World Atlas][w-atlas], [Camping][w-camp] |
| Atlas "Begin Tracking to Location": a persistent arrow, plus a system message every 10 s with the step count to the destination | H | [World Atlas][w-atlas] |
| Bedroll at a secure campfire = instant logout (not in dungeons) | H | [Camping][w-camp] |

### 1.7 "Rope" = Adventurer's Rope

A single-use, stackable item (100 Tailoring: 10 leather + 1 beeswax; storable in a Storage Shelf).
Double-click it and pick a location; it teleports you "following the normal rules for the Teleport
spell" (within 10 tiles, line of sight). 5 s cooldown; 2 min when PvP-flagged. **H**
[Adventurer's Rope][w-rope], [SpellCircles][w-sc]

### 1.8 Chivalry Sacred Journey (for completeness)

Creates a holy gate to towns, dungeons, shrines or a custom location (the custom location must be a
valid Recall/Gate spot). Blocked for 2 min after PvP and for 30 s after any combat. **H**
[Chivalry][w-chiv], [New Player Guide][w-npg]

### 1.9 Travel lockouts and timers

| Lockout | Duration / rule | Conf. | Source |
|---|---|---|---|
| **Harvest after travel**: Recall, Moongate, Hike, Teleport or Adventurer's Rope (the wiki's list; "hike" and "rope" are §1.6 and §1.7) | 60 s | H | [Harvesting][w-harv] |
| Same lockout after a rental-room exit (teleport). Live text: **"You have recently traveled and must wait 19 seconds before you may begin harvesting."** | 60 s | H (live) | journal `2026_09_29_20_42_27_journal.txt:739`; `harness/data/loops/lumber.json:71-74`; PLAN.md "60 s lockout after the room exit confirmed" |
| Does a second travel reset or extend the timer (room exit, then recall to the field)? Wording "whenever… 60 second delay" | `[INFERENCE]` reset to 60 s from the last travel | L | [Harvesting][w-harv] |
| Heat of Battle: no recall, gate, moongate or inn room | while the buff is shown | H | [Heat of Battle][w-hob] |
| Hiking: no combat 30 s / no player combat 2 min | — | H | [Camping][w-camp] |
| Rental room: no access within 2 min of PvP | 2 min | H | [Rental Room System][w-rrs] |
| Overloaded (over weight or item cap): no Recall, Hike or "Magical Travel" | while overloaded | H | [Backpack][w-bp] |
| Blackmark (murderers on dungeon level 1): no Recall/Gate/Hike to locations inside dungeons | 4 h cumulative | H | [Murderers & Resurrection][w-mr] |
| Help menu "Character Is Stuck": teleport to a random town | once per 12 h | H | [Help][w-help] |
| Criminal flag survives death | full 2 min | H | [Criminality][w-crim] |

Backpack caps are 125 items / 450 stones plus the Camping bonus (+200 st and +50 items at 100
Camping) ([Backpack][w-bp], [Camping][w-camp]). Boards weigh 0.025 st each ([Harvesting][w-harv]),
so weight never blocks recall for a lumberjack. Item count can, if the pack is full of loose items.

### 1.10 Restricted areas

| Area | Restriction | Conf. | Source |
|---|---|---|---|
| Dungeons | no Recall/Mark/Gate except within 8 tiles of a golden gate; no mounts | H | [About][o-about], [Dungeon Moongates][w-dmg] |
| Rental rooms | can't recall, gate or hike **into** one; you can leave that way | H | [Rental Room System][w-rrs] |
| "IP-Restricted Regions (such Undermountain, Urukton Bluffs)" | pack animals left behind; other rules not listed | H | [Patch Jun 7 2022][o-jun22] |
| Sanctuary dungeons | murderers can't enter; no mining; no revealing | H | [Sanctuary Dungeon][w-sanct] |
| Test Shard | no Time Dungeon | H | [Test Shard][w-ts] |
| Ships | "You cannot recall off of a ship." (502352) | L | Cliloc.enu |
| Towns | lumberjacking is blocked in town regions (Shelter excepted while Young) | H | NOTES.md:75, [Shelter Island][w-si] |

---

## 2. Leaving Shelter Island (Young status)

| Fact | Conf. | Source |
|---|---|---|
| New characters start on Shelter Island with Young status and keep it **until they leave the island** | H | [Shelter Island][w-si] |
| Young is also lost by saying **"I renounce my young player status"** | H | [New Player Guide][w-npg] |
| A 2018 guide says Young lasts "your first two days". The current wiki says it lasts until you leave; take the wiki | M | [Forum guide 2018][f-guide] |
| Ways off the island: **Moongates, Hiking (Camping + Atlas), Recall, Gate Travel "and several other options"** | H | [Shelter Island][w-si] |
| 2018 guide: "a moongate up the stairs to the Northeast… or a passageway under the Inn". The passageway is unverified today | M | [Forum guide 2018][f-guide] |
| Any action that would take a Young player off the island first asks for confirmation that they're **renouncing Young, which is permanent**. After confirming, they leave freely | H | [Shelter Island][w-si] |
| **Recalling or hiking out of a rental room rented on Shelter now also drops Young** (the loophole was fixed) | H | [Patch Sept 20 2021][o-sep21] |
| Renting on Shelter while Young and entering the room keeps Young | H | [Rental Room System][w-rrs] |
| Without Young, a player can still return to Shelter Island and use the New Player Dungeon, but gets no Shelter bank or vendor access and no newbied-item handling. The New Player Guide: "will not be able to interact with Shelter Island NPCs" | H | [Shelter Island][w-si], [New Player Guide][w-npg] |
| When Young is lost, all [Newbied] items become normal and drop on death | H | [Shelter Island][w-si] |
| Renounce clilocs in the client: 502085 "You have chosen to renounce your `Young' player status.", 502086 (chose not to), 3006307 "Renounce Young Status" | L | Cliloc.enu |
| The Shelter Island moongate is at 1985,2533, 62 tiles (Chebyshev) from innkeeper Jayne (1932,2595) | M | Moongates.xml; lumber.json:14 |

**Consequence for the room (important).** "A player can enter their room from any town, but will
always exit to the town from which they originally rented" ([Rental Room System][w-rrs]). A room
rented on Shelter would drop the non-Young TestWorth back on Shelter at every exit, and the Shelter
innkeeper may refuse to serve a non-Young player (New Player Guide, above). Leaving to the overworld
means **renting a new room in a mainland town**. The Test Shard wipes all inn rooms daily at
00:00 UTC ([Test Shard][w-ts]), so a daily re-rent is needed anyway. Whether a Shelter rental stays
valid after renouncing is unknown → open question Q2.

---

## 3. Death

### 3.1 What happens

| Fact | Conf. | Source |
|---|---|---|
| You become a ghost and the screen goes grey. Ghosts can walk through doors, creatures and players | M | [Forum guide 2018][f-guide] |
| Client side: S2C `0x2C` DeathScreen → death music, a death-screen timer, war mode forced off. The player counts as dead when the body is `0x0192`/`0x0193` (human ghosts) or another ghost body. Options: "Enable Death Screen", "Black & White mode for dead player" | H (code) | `ClassicUO-main/src/ClassicUO.Client/Network/PacketHandlers.cs:1745-1763`, `Game/GameObjects/Mobile.cs:132-142`, [Options][w-opt] |
| The harness world model has no `0x2C` handler and no `dead` flag yet (grep of `harness/world`) | H | `harness/world/runtime.py` |
| Everything that isn't **Blessed** or **Newbied** falls to the corpse. You come back in a death robe with only blessed/newbie items | H/M | [Shelter Island][w-si], [Forum guide 2018][f-guide] |
| Young: starting gear is [Newbied] and doesn't drop. Items lose Newbied status if a non-Young player picks them up or uses them. Starting bandages and arrows are character-bound | H | [Shelter Island][w-si], [New Player Guide][w-npg] |
| **Blessed** items never drop, Young or not | H | [Shelter Island][w-si] |
| Runebooks, Rune Tomes and **commodity deeds are not blessed** by default. Deeds can be looted | H | [Runebook][w-rb], [Harvesting][w-harv] |
| Aspect gear drops to the corpse; the aspect experience stays with the character | H | [About][o-about] |
| **No insurance system found.** The wiki search for "insurance"/"insured" returns nothing; blessing is item- or deed-based | L `[INFERENCE]` | wiki full-text search (§8) |
| **No statloss or skill loss for non-murderers found.** The murderer penalty is a gold "Murder Fee" paid at resurrection (≥ 5 counts; 2000 gp + 500 per accomplice), plus region restrictions. The About page also lists a 15 min IP restriction and a 2 h lockout from the dungeon of death | H (murderers) / L (none for blues) | [Murderers & Resurrection][w-mr], [About][o-about], [Patch Jun 7 2022][o-jun22] |
| Fame: dying outside Faction Struggles, Arenas or Lawless/Grey zones loses 10 % of current Fame | H | [Reputation][w-rep] |
| After death: no stealing or snooping for 2 min (thieves). A house deed can't be placed for 5 min | H | [Stealing][w-steal], [Housing][w-house] |
| Test Shard: `[TestRes` resurrects you and your followers. `[TestBlessedGear` toggles "gear always considered blessed" | H | [Test Shard][w-ts] |

### 3.2 Resurrection

| Route | Details | Conf. | Source |
|---|---|---|---|
| Town healer NPC | "Healers are the place you go to when you die… run towards them and they will offer you the ability to resurrect." You come back with very little life | M | [Forum guide 2018][f-guide] |
| **Healer Caravans** (wilderness) | "Find a caravan to be resurrected." The Atlas has a Healer Caravans category to hike or track to | H | [World Map][w-wm], [Healer Caravans][w-hc], [World Atlas][w-atlas] |
| Ankh / shrine | The 2018 guide mentions "an anhk". Outlands shrines are Shrine Corruption event sites. Whether they resurrect is unknown | M/unknown | [Forum guide 2018][f-guide], [Shrines][w-shr] |
| Player with bandages | Needs 80 Healing + 80 Anatomy; 100 % at 100 Healing; 24 % at 80 → 62 % at 99 | H | [Healing][w-heal] |
| Resurrection spell | 8th circle, 5.00 s cast | H | [SpellCircles][w-sc] |
| Test Shard | `[TestRes` | H | [Test Shard][w-ts] |
| Murderer (≥ 5 counts) | pays the Murder Fee before resurrecting ("Murderer Penalty Choice") | H | [Murderers & Resurrection][w-mr], [About][o-about] |

**Resurrection gump.** In RunUO the healer's offer is `ResurrectGump`: title cliloc 1011022
"Resurrection", body 1011025 "It is possible for you to be resurrected here by this healer. Do you
wish to try?", buttons CONTINUE (1011011) / CANCEL (1011012) ([RunUO ResurrectGump.cs][r-resgump]);
all of those strings are in the local Cliloc.enu. Outlands has "freshly skinned" all menus and gumps
([About][o-about]), so the gump id, layout and button ids are **unknown** → demo D3. **L**

**Young death.** OSI/RunUO move a Young ghost to the nearest healer together with their items (client
cliloc 1046293: "While you are still in young status, you will be transported to the nearest healer
(along with your items) at the time of your death."). Whether Outlands does this is unknown → demo
D2. **L**

**Criminals.** A criminal is refused ("Thou art a criminal. I shall not resurrect thee.", 501222).
The criminal flag survives death for its full 2 min ([Criminality][w-crim]). **L** for the NPC message.

### 3.3 Corpse

| Fact | Conf. | Source |
|---|---|---|
| "Your corpse will remain for **15 minutes**" | M (2018 guide) | [Forum guide 2018][f-guide] |
| "Standard corpse decay is typically 15 minutes for most creatures", with faster decay for empty creature corpses | H (creatures) | [Patch Apr 4 2024][f-apr24] |
| The player corpse shows everything the player wore | H | [Patch Sept 20 2021][o-sep21] |
| Others can loot it. Looting a blue corpse makes the looter criminal (grey) unless they set `[PreventCriminalLooting`; the client can also block blue-corpse looting in grid loot | H | [New Player Guide][w-npg], [Client Options][w-cop] |
| "Flag Green to Party" lets party members loot your corpse | H | [Party][w-party] |
| Returning: dungeon deaths → golden gates or ghost-only death gates out to town; overworld deaths → nearest healer caravan. Resurrect, resupply, go back for the body "if it is still there" | M | [Forum guide 2018][f-guide], [Moongates][w-mg] |
| Corpse item: graphic `0x2006` (the client's `IsCorpse` test) | H (code) | `ClassicUO-main/src/ClassicUO.Client/Game/GameObjects/Item.cs:125-126` |

### 3.4 Healers near our venues

- **Shelter Island:** healers "Marti the healer" and "Minka the healer" (journals
  `2026_09_28_21_16_23_journal.txt:22-23`); marker at 1908,2585. **H/M**
- Town healers and caravans: §4 table, and `Healer_Caravans.xml` has 42 caravans (e.g. Shelter Haven
  Caravan 1598,2576; Cambria Gate 1473,2854; Horseshoe Bay Swamps 2225,2250). **M**

---

## 4. Towns, guard zones, inns and safe spots

### 4.1 Guard zones

| Fact | Conf. | Source |
|---|---|---|
| Attacking a blue inside a guard zone → instant guardwhack | H | [Guard Zones][w-gz] |
| A crime in town is exempt after 30 s without anyone calling guards; a new crime resets the timer. Entering town within 30 s of a crime outside makes you whackable; the message is "Guards may now be called on you." | H | [Guard Zones][w-gz] |
| A victim says **"Guards"** in a guard zone → the attacker is killed. A thief is killed if noticed (NPC notice → automatic; a player must call within 30 s and within 14 tiles) | H | [New Player Guide][w-npg], [Stealing][w-steal] |
| **Which towns are guarded isn't listed on the wiki.** Corpse Creek is the lawless murderer haven ("nowhere safe"). `[INFERENCE]` The other nine townships are guarded | H (CC) / L | [About][o-about] |
| Shelter Island: no hostile player actions at all | H | [Shelter Island][w-si] |
| Guards don't protect you *outside* town. A PK can attack anywhere off-island, and theft is possible "almost anywhere" | H | [New Player Guide][w-npg] |

### 4.2 Inns, banks, healers and moongates per town

Coordinates come from the client-shipped marker packs `Banks_and_Healers.xml` ("Black Tea") and
`Moongates.xml` / `Townships.xml` ("Atlas"). They're community data, not verified in-game (**M**).
Distances are Chebyshev tiles from the inn marker. Every town has an innkeeper: "Innkeepers in any
town (including Corpse Creek, Shelter Island, and Sanctuary)", and saying "Innkeep" to any town
human gives a pointer arrow to the nearest one ([Rental Room System][w-rrs]).

| Town | Inn | → bank | → healer | → public moongate | Guarded |
|---|---|---|---|---|---|
| Shelter Island | Jayne 1932,2595 (lumber.json:14) | 23 (bank 1955,2583) | 24 (1908,2585) | 62 (1985,2533) | no hostile actions |
| **Totem** | 3847,2902 | 21 | 21 | 99 | L: yes |
| **Horseshoe Bay** (nearest to Shelter) | 1978,2198 | 31 | 59 | 121 | L: yes |
| Andaria | 2158,660 | 32 | 56 | 173 | L: yes |
| Outpost | 3049,513 | 32 | 56 | 108 | L: yes |
| Prevalia (south inn) | 1945,1660 | 44 | 45 | 417 (Prevalia gate 1474,1497) | L: yes |
| Prevalia (north inn) | 1640,1451 | 50 | 110 | 166 | L: yes |
| Terran | 729,1460 | 48 | 71 | 138 | L: yes |
| Cambria (west inn) | 1677,3015 | 73 | 75 | 139 | L: yes |
| Cambria (east inn) | 1895,3009 | 145 | 199 | 190 | L: yes |
| Anchor's Rest | 3832,499 | 100 | 70 | 161 | L: yes |
| Corpse Creek | 827,713 | 37 | 30 | 127 | **no (lawless)** |

The Test Shard resource stockpiles and Storage Shelves are at the town centres of **North Prevalia**
(two sets, either side of the fountain) and Corpse Creek ([Test Shard][w-ts]). Restocking from them
favours Prevalia as the Test Shard home.

### 4.3 Safe-spot candidates (home rune)

We can't recall into a room, so the "home" rune is a spot next to the innkeeper. Criteria from
existing facts: inside a guard zone; within the **11-tile vendor range** where the room menu
buttons work (lumber.json:55-56); not blocking a moongate (4-tile rule); markable (unknown for
building interiors, Q7).

1. **Totem**: inn, bank and healer all within 21 tiles. Best recovery loop (res → bank → room) if the
   woods near Totem suit lumberjacking. **M**
2. **North/South Prevalia**: has the Test Shard shelves/stockpiles, two inns, two banks and two
   healers. The south inn is 44–45 tiles from its bank and healer. **M**
3. **Horseshoe Bay**: the closest mainland town to Shelter; bank 31 tiles, healer 59. **M**
4. Avoid Corpse Creek (lawless) and Cambria's east inn (145–199 tiles to the services).

Arrival by town moongate or dungeon gate is random within town ([Dungeon Moongates][w-dmg],
[Camping][w-camp]), so only a rune gives a deterministic home position.

---

## 5. Automated recovery flow: die → resurrect → return to the inn → restock

"Capture" = a user demonstration recorded through the proxy (the loop_mine timeline), as for the
lumber demo. Items marked ✅ are already proven live.

| # | Step | Detection / UI involved | Harness today | Needs capture |
|---|---|---|---|---|
| 0 | Pre-trip invariants | Runebook in pack with ≥ N charges (count shown in the runebook gump or on single-click, unknown), runes `home`, `field-*`; tool spare in the room | none | **D6** runebook gump; charge readout |
| 1 | Detect death | S2C `0x2C`; self body → `0x0192/0x0193`; hits 0; possible cliloc "You are dead." (501974, L); a corpse `0x2006` appears at our last position | no `0x2C` handler, no dead flag | **D1** |
| 2 | Stop the loop, raise an urgent juncture (`Memory.juncture(..., severity='urgent')`), record the corpse serial/position/time (15 min clock) | — | juncture API exists | — |
| 3 | Young-death transport? | Position jump to a healer + possible notice gump (L) | — | **D2** (Shelter/NPD, while Young) |
| 4 | Choose a resurrection point | Nearest town healer or healer caravan (§3.4, §4.2); ghost pathing may differ (walk-through doors/mobiles; the ClassicUO pathfinder has a `PSS_DEAD_OR_GM` state) | Mover plans for the living | **D4** ghost walking (does MoveAuthority step pacing hold; speed) |
| 5 | Walk as a ghost to the healer (or take a golden/death gate from a dungeon) | Movement only; ghosts can't cast, so no recall | Mover | D4; D10 if a gate is used as a ghost |
| 6 | Healer offers resurrection | **Resurrection gump** (Outlands skin; RunUO text 1011025, CONTINUE/CANCEL); offer range unknown | gump reply action exists (`actions.py`) | **D3** gump id, button ids, offer range, cooldown when cancelled |
| 7 | After resurrection | Death robe equipped, low HP; items on the corpse | — | D3 (same session) |
| 8 | Corpse run (optional, < 15 min) | Walk to the corpse, double-click → corpse container gump and/or Outlands **Grid Loot** gump (client option "Corpse Gump Mode: Both" by default); drag items back; re-equip the hatchet | container open/drag builders exist; grid loot unknown | **D5** |
| 9 | Travel to the home inn | `[RecallCharge home` (speech) **or** runebook gump → blue gem; 2 s cast; watch for fizzle/interrupt/no-charges messages | `cast_spell` + target proven live; runebook path not captured | **D6, D7** |
| 10 | Enter the room | Say "room" within 11 tiles of the innkeeper → menu `0x8EAEFBDB` → Enter button 4 → "You enter the rental room." | ✅ (lumber.json:53-62) | — |
| 11 | Restock | Secure container in the room (✅ store path). Storage Shelf: gump → "Begin Resupply" (loadout), "Resupply Runebooks" | container ✅; shelf none | **D8** shelf gump (Test Shard shelves in N. Prevalia) |
| 12 | Leave and go back out | Door → Exit to Town (random inn room ✅) → recall `field` or walk; **60 s harvest lockout** after each travel | ✅ lockout wait | D7 confirms whether a recall right after the room exit resets the timer |
| 13 | Resume the loop and close the juncture | `juncture_ack`, chat_post summary | exists | — |

Failure branches the overseer must own: fizzle/interrupt → retry ≤ k, then walk; no charges → walk
home and restock scrolls; corpse camped by a red → abandon (items are a sunk cost) and restock;
criminal flag → wait out the 2 min before a healer will serve; Heat of Battle → can't recall or
enter the room (we never attack players, so this should only happen by mistake).

---

## 6. Open questions for the user

1. **TestWorth's skills besides Lumberjacking 60.2** (lumber.json:5): Magery? Camping or
   Cartography? Inscription, Resisting Spells, Healing? This decides recall-by-spell vs. by charge,
   hiking, and interrupt resistance.
2. **Home town after leaving Shelter:** Totem, Prevalia or Horseshoe Bay (§4.3)? Do we rent a new
   room there, and does renouncing Young end or strand the Shelter rental?
3. **Runebook supply:** buy or craft one? Bless it (62 500 gp) or accept the loss risk? Source of
   recall scrolls (Mage NPC, player vendor, Test Shard shelves)?
4. **Who marks runes?** Mark needs 60 Magery. Otherwise: pre-marked runes from another character,
   or a public library book.
5. **Corpse-run policy:** always go back within 15 min, only when no red/grey is in range, or
   never?
6. **Death recovery policy:** may the agent use `[TestRes` / `[TestBlessedGear` / `[Go` on the Test
   Shard, or only the realistic path (res shrine or wandering healer, corpse run, re-equip)?
7. **Inn-interior marking:** is marking allowed inside an inn building or next to the innkeeper
   (the "few exceptions" to Mark are undocumented)?
8. Is the double 60 s lockout per return trip (room exit + recall out) acceptable, or should the
   field rune be a walk-in spot near town?

## 7. Demos / captures needed

All on the Test Shard with the proxy recording and the user playing. Suggested order: D1–D3 while
still Young (New Player Dungeon monsters attack Young players, [Forum guide 2018][f-guide]); the rest
after the user moves TestWorth off the island.

| ID | Demo | What to record | Answers |
|---|---|---|---|
| D1 | Die once (NPD monster) | S2C `0x2C`, body change, corpse item, messages | death detection |
| D2 | Same death, while Young | position jump? notice gump? items kept? | Young-death transport (§3.2) |
| D3 | Walk the ghost to Marti/Minka, accept resurrection; cancel once first | gump id/layout/buttons, offer range, re-offer delay, post-res HP and robe | resurrection gump |
| D4 | Ghost walking ~30 tiles through a door | walk acceptance/pacing, walk-through | ghost movement |
| D5 | Open own corpse, drag everything back, re-equip | corpse gump + grid loot gump ids, lift/drop | corpse recovery |
| D6 | Open runebook, drop a recall scroll on it, recall via blue gem | runebook gump id, button ids, charge count display, messages | runebook UI, charges |
| D7 | `[RecallCharge <name>` and `[Recall <name>` (+ one interrupted cast by walking or taking a hit) | server texts, cast duration, movement-during-cast behaviour, lockout text after arrival | text-command travel, interrupt rules |
| D8 | Storage Shelf in North Prevalia: edit loadout, Begin Resupply, Resupply Runebooks | shelf gump(s) and flow | restock |
| D9 | Mark a rune + rename it (if anyone has Magery) | Mark target flow, rename prompt | rune library |
| D10 | Use a public moongate (and the `[Moongate` command) | moongate gump id/layout | moongate travel |
| D11 | **Recognition only:** the renounce-Young confirmation when stepping into the Shelter gate. Capture the gump, then decline (or the user accepts when intentionally leaving) | gump id/text so the loop aborts reliably | Young safety guard |
| D12 | Hike via the Atlas (needs 60 Camping/Cartography) | atlas gump, campfire secure message, 5 s freeze | hiking (optional) |

---

## 8. Sources (read 2026-09-29)

Wiki pages were read through the MediaWiki API (`action=parse&prop=wikitext`); `rev` is the revision
id read. The full-text wiki search (`list=search&srwhat=text`) returned no page for "insurance",
"insured", "statloss", "rune library" (except NPG/Vendor Search) or "runekeeper".

[w-sc]: https://wiki.uooutlands.com/Template:SpellCircles "rev 32649"
[w-mag]: https://wiki.uooutlands.com/Magery "rev 31527"
[w-rb]: https://wiki.uooutlands.com/Runebook "rev 22021"
[w-rt]: https://wiki.uooutlands.com/Rune_Tome "rev 22387"
[w-rbd]: https://wiki.uooutlands.com/Runebook_Bless_Deed "rev 32894"
[w-rbt]: https://wiki.uooutlands.com/Runebook_Transcriber "rev 32905"
[w-tomes]: https://wiki.uooutlands.com/Tomes "rev 30651"
[w-rune]: https://wiki.uooutlands.com/Blank_Recall_Rune "rev 11837"
[w-mg]: https://wiki.uooutlands.com/Moongates "rev 20259"
[w-dmg]: https://wiki.uooutlands.com/Dungeon_Moongates "rev 5298"
[w-camp]: https://wiki.uooutlands.com/Camping "rev 31559"
[w-atlas]: https://wiki.uooutlands.com/World_Atlas "rev 32863"
[w-rope]: https://wiki.uooutlands.com/Adventurer%27s_Rope "rev 28841"
[w-chiv]: https://wiki.uooutlands.com/Chivalry "rev 32826"
[w-harv]: https://wiki.uooutlands.com/Harvesting "oldid 31109"
[w-hob]: https://wiki.uooutlands.com/Heat_of_Battle "rev 31577"
[w-bp]: https://wiki.uooutlands.com/Backpack "rev 31672"
[w-mr]: https://wiki.uooutlands.com/Murderers_%26_Resurrection "rev 31580"
[w-help]: https://wiki.uooutlands.com/Help "rev 4663"
[w-crim]: https://wiki.uooutlands.com/Criminality "rev 6059"
[w-rrs]: https://wiki.uooutlands.com/Rental_Room_System "rev 31283"
[w-sanct]: https://wiki.uooutlands.com/Sanctuary_Dungeon
[w-ts]: https://wiki.uooutlands.com/Test_Shard "rev 29137"
[w-si]: https://wiki.uooutlands.com/Shelter_Island "oldid 31103"
[w-npg]: https://wiki.uooutlands.com/New_Player_Guide "rev 33048"
[w-cmd]: https://wiki.uooutlands.com/Commands "rev 30548"
[w-opt]: https://wiki.uooutlands.com/Options "rev 31010"
[w-cop]: https://wiki.uooutlands.com/Client_Options "rev 30944"
[w-co]: https://wiki.uooutlands.com/Combat_Overview "rev 24978"
[w-rs]: https://wiki.uooutlands.com/Resisting_Spells "rev 33062"
[w-ins]: https://wiki.uooutlands.com/Inscription "rev 31935"
[w-heal]: https://wiki.uooutlands.com/Healing "rev 29684"
[w-hc]: https://wiki.uooutlands.com/Healer_Caravans "rev 5778"
[w-wm]: https://wiki.uooutlands.com/World_Map "rev 30230"
[w-shr]: https://wiki.uooutlands.com/Shrines "rev 29852"
[w-rep]: https://wiki.uooutlands.com/Reputation "rev 22385"
[w-steal]: https://wiki.uooutlands.com/Stealing "rev 31673"
[w-house]: https://wiki.uooutlands.com/Housing "rev 31575"
[w-party]: https://wiki.uooutlands.com/Party "rev 30002"
[w-gz]: https://wiki.uooutlands.com/Guard_Zones "rev 6057"
[w-shelf]: https://wiki.uooutlands.com/Storage_Shelf "rev 32366"
[o-about]: https://uooutlands.com/about/
[o-sep21]: https://uooutlands.com/news/patch-sept-20-corpse-update-and-bug-fixes/
[o-jun22]: https://uooutlands.com/news/patch-jun-7-dungeon-overhaul-murder-count-changes-customizations-and-so-much-more/
[f-apr24]: https://forums.uooutlands.com/index.php?threads/patch-notes-for-april-4-2024.5420/
[f-guide]: https://forums.uooutlands.com/index.php?threads/completely-new-to-uo-guide.662/ "2018, last edited 2019-02-24"
[f-pvp]: https://forums.uooutlands.com/index.php?threads/uo-outlands-pvp-mechanics-guide-merging-with-trystans-comprehensive-pvp-guide.451/ "2018"
[f-runelib]: https://forums.uooutlands.com/index.php?threads/free-vendor-locations-rune-library.5848/ "2025-03-26"
[r-resgump]: https://github.com/runuo/runuo/blob/master/Scripts/Gumps/ResurrectGump.cs

Local, read-only: `C:/Program Files (x86)/Ultima Online Outlands/ClassicUO/Data/Client/{Moongates,
Banks_and_Healers,Healer_Caravans,Shrines,Townships}.xml`; `…/Cliloc.enu` (via `harness/uo/cliloc.py`);
`…/Data/Client/JournalLogs/*.txt`; `…/Data/Plugins/Assistant/outlandscommands.def`;
`ClassicUO-main/src/` (upstream client); repo `harness/data/loops/lumber.json`, `docs/PLAN.md`,
`docs/NOTES.md`, `docs/LUMBER_LOOP.md`.

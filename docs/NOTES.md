# Operational knowledge base

Facts learned during the 2026-09-27 research session that don't belong in the report but must not be lost.

## Environment & client

- Install root: `C:\Program Files (x86)\Ultima Online Outlands`. `Outlands.exe` = launcher/patcher (args `-installed`, config `Outlands.exe.json` → PatchSettings). Real client: `ClassicUO\ClassicUO.exe`.
- Client version string: `ClassicUO [STANDARD_BUILD] - 1.0.2.544` (from `ClassicUO/Logs/*_network-disconnects.txt`, which also record disconnect stack traces). The launcher patched the installed client to **1.0.2.550** on 2026-09-28 14:21 (file time; the JWT `version` claim says 550 from then on). The workspace `ClassicUO.exe` copy used for Ghidra is still 544 (sha256 `127ce6a8…`; installed 550 is `743abdea…`). Its traffic still frames with the 544 table (0 desyncs in 11 audited sessions).
- Both binaries are **NativeAOT**: no CLR header, no `coreclr`/`hostfxr`/`runtimeconfig` strings, `Rhp*` Redhawk symbols, one embedded `ReadyToRun` header, `BSJB` managed-metadata blob in `.rdata`. No embedded DLLs to carve — assemblies are compiled into the binary. ILSpy/dnSpy/dnfile are useless here; Ghidra (native) is the tool.
- `ClassicUO/settings.json` holds plain-text `ip`/`port` (proxy insertion point), `loginserver` (`https://login.uooutlands.com`), shard/character selection, and **live account credentials** (`email`, obfuscated `outlandsid_pw`). Treat the file as a secret; never commit or log it.
- Native DLLs shipped beside the exe: SDL3, FNA3D, FAudio, cimgui, libtheorafile, zlib, vcruntime, WPF `_cor3` DLLs (used by the Razor assistant UI).
- Profile data: `ClassicUO/Data/Profiles/<OutlandsID>/<Shard>/<Char>/` (gumps, macros, journal.xml…). Plaintext journal logs: `ClassicUO/Data/Client/JournalLogs/*.txt`. Assistant profile/scripts: `ClassicUO/Data/Plugins/Assistant/` (`settings.csv` ships with stale template values; `outlandscommands.def` lists every `[`-command).
- World-map marker XMLs in `Data/Client/` (POI, moongates, dungeons, banks) — free navigation data for the world model later.
- **Map data in the install dir (surveyed read-only 2026-09-29).**
  - Outlands ships its world data in **proprietary `.uoo` containers**, not the standard MUL/UOP files: `map0.uoo`–`map5.uoo` (map0 = 585 MB, updated 2026-09-27), `art.uoo`, `artdata.uoo`, `landdata.uoo`, `landtiles.uoo`, `texmaps.uoo`, `gumps.uoo`, `anim.uoo`, `fonts.uoo`. There are no `statics*.mul`, `staidx*.mul` or `tiledata.mul` files.
  - The magic numbers differ: `map0.uoo` starts `3f3632e5 01000000`; `art`/`artdata`/`landdata.uoo` start `6dab7f1e 01000000`. The art entries look compressed.
  - **Decoded 2026-09-29, see docs/MAP.md.** The reader is `harness/uomap.py` (read-only mmap; `python harness/uomap.py tile X Y [--map N]`), tested by `harness/test_uomap.py`. In `mapN.uoo` land is 6 B/cell (u32 id, **i16 z**), blocks are row-major `by*W+bx`, and there is an i64 statics LUT with 9 B records (cell delta, u32 graphic, u16 hue, i16 z). `landdata.uoo` has 48 B records and `artdata.uoo` 72 B (u64 flags, weight @8, height u32 @32, name[36] @36). Flags are upstream TileFlag plus four Outlands-only high bits.
  - map2/map3 are blank placeholders. **Rental rooms are on facet 3** (0xBF/0x08), and map3.uoo has no geometry for them. Doors (e.g. the Shelter inn's) are dynamic 0xF3 items, not statics.
  - Standard files present: `hues.mul`, `radarcol.mul`, `multi.idx/.mul`, `Multimap.rle`, `facet00.mul`–`facet05.mul`, `speech.mul`, `skills.mul`, `light.mul`, `sound.mul`, `cliloc.*`.
- **`facet00.mul` is a ready-made 1 px/tile top-down picture of Outlands map 0** (dated 2024-12-21, so it may predate recent map edits).
  - Format as in upstream `MultiMapLoader.LoadFacet`: `u16 width, u16 height`, then per row an `i32` byte count followed by `(u8 run, u16 ARGB1555 color)` runs.
  - The file is 10752×6144 and decodes exactly: all bytes consumed, every row sums to the width, 2.9 s in pure Python.
  - The 1 px/tile scale was proven with all 40 facet-0 markers in `Data/Client/Banks_and_Healers.xml`: each one lands on a non-water pixel at scale 1.0, versus ≤57% at scales 1.25–2.0.
  - Shelter Island (the bank run's area, 1963,2597) renders as the town plaza next to the bank.
  - Confirmed: map0.uoo's header is 1344×768 blocks = 10752×6144 tiles.
- **Client settings the harness assumes (user, 2026-10-01):** Always Run **on** (the client default; agent routes always run, `ctl act walk` runs unless `--walk`) and Auto Open Doors **off**. With the proxy re-anchoring the client after every agent step, Auto Open Doors would send a second open-door request next to the Mover's and shut the door again (ANTICHEAT.md §10 A11).
- **Client data drift seen 2026-10-08 (not investigated):** `test_uomap` now counts 71376 artdata
  records (it pins 71292; 72522 on 2026-10-28, artdata.uoo dated 2026-10-27 20:36). The
  `test_pathfind` part of this note was not drift: its 9 (later 34) unwalkable moves around
  (3954..3961, 513..521) are on a player house's floor ("Mounted movement and player houses":
  the Anchors Rest house, found 2026-10-28).

## Login & identity

- Flow: launcher → OutlandsID auth over `https://login.uooutlands.com` (Cloudflare) → JWT (claims: `outlandsid`, `purpose`, `mahid` = multi-account household, `mahleader`) → game server `play.uooutlands.com:2593`. New devices require email 2FA (Feb 2026 change); this machine is a recognized device.
- API model names found in binary (System.Text.Json source-gen contexts): `WebLoginRequestModel`, `PanelLoginRequestModel`, `PortalLoginResponseModel`, `PatchLoginResponseModel`, `GameLoginRequestModel/GameLoginResponse`, `ClientLoginRequestModel`, `GetClientInfoResponse`, `LinkRequestModel`, `CreateOutlandsIDModel`, `CreateGameAccountModel`, `GetOutlandsIDFromGameAccountRequest/Response`, `GetTotalPrevCoinsRequest/Response`, `VerifyOutlandsIDModel`, `VerifyDeviceModel`, `VerificationStatusResponse`, `AcceptPrivacyPolicyRequestModel`, `PrivacyPolicyCheckResponseModel`, `TPMModel`, `AccountInfo`, `ServerInfo`. Full list: `api_surface.txt`.
- `ServerClientRestriction`, `VersionRestrictions`, `IsMostRecentVersion`, `IsMostRecentGameFilesVersion`: client/game-file version gating at login — keep files stock and the launcher will handle patching.
- Launcher carries AWS SigV4 signing strings → patch downloads from AWS infra.
- **Our characters' player serials** (from the S2C 0x1B login confirm; `memory.py backfill-identity`
  on the 2026-10-08 store filled 81 sessions, 78 of them from the raw capture's first 0x1B):
  TestWorth 0x00094375, Hackworth 0x0020F127, Shackleworth 0x003D701F, Outland Dan 0x0014683F,
  Logan Wolf 0x00104A60 (first seen session 20261005_191829). 5 sessions stayed without a serial
  (clients closed at the character screen). The store now records `account`/`char_name`/
  `char_serial` per session live (docs/MEMORY.md v6). `test_world_replay` had been failing on
  every Outland Dan and Logan Wolf capture because its `OUR_SERIALS` list predated them.
- **Several clients at once (2026-10-08):** the proxy serves them all; with more than one logged
  in, every state/control request must name its character (`--char`/`UO_CHAR`, docs/PLAN.md
  "Several characters at once"). One shared agent gate for all of them.

## Protocol nuggets (for Phase 1–2)

- `Send_UOLive_HashResponse` (client→server `0x3F`): UltimaLive. Server subcommand `0xFF` requests CRC16s of the 5×5 map blocks around the player; client answers so the server can push stale map/statics updates. Source: `ClassicUO-main/src/ClassicUO.Client/Game/UltimaLive.cs`, `Network/OutgoingPackets.cs:4510`. NOT anti-cheat.
- Outlands-proprietary, not in upstream CUO or Razor CE: `Speedhack`, `AutoClicking`, `AutoKeyboard` (violation-category enum), `Send_TimeSyncPingReq` (timing channel), `CAPTCHA_GUMP_ID`, `IsRazorBlockedSysMessage`, `EnablePvpScriptRestrictions`/`IsPvpFlagged`/`DisableWhenPvpFlagged`, all of §Login models.
- **Tiledata item names carry plural markup** (found 2026-10-03: the Avatar panel showed "Moving 2 amethyst%s% from the ground container to the back"). Scan of all 0x1167C artdata.uoo names: `%s%` ×176 (`slab%s% of bacon`), `%es%` ×7, `%ies/y%` ×7 (`rub%ies/y%`), `%ves/f%` ×3 (`bread loa%ves/f%`), `%es/e%` ×2 (`pil%es/e% of hides`), one stray `%` (`Executioner's Cap%`); never two markers in one name. Rule (ClassicUO `StringHelper.GetPluralAdjustedString`, from memory, not re-read; the data fits it): `%A/B%` → A for a plural, B for a singular; `%A%` → A or nothing. `uomap.display_name(name, plural)` resolves it; `ctl._tile_name(graphic, amount)` and `ctl.item_label(it, amount)` use it (plural above 1). Raw `ItemTile.name` keeps the markup (`combat.py`'s reagent comments quote it). `[INFERENCE]` names the server sends (clicks, labels) carry no markup; not checked.
- Compression: UO-standard Huffman server→client. Upstream CUO `Network/` is the reference implementation.
- Client→server packet class names seen in metadata: `Send_WalkRequest`, `Send_ClickRequest`, `Send_GumpResponse`, `Send_TextEntryDialogResponse`, `Send_ASCIISpeechRequest`, `Send_UnicodeSpeechRequest`, `Send_LiftRequest`/`Send_DropRequest`, `Send_SkillsRequest`, `Send_StatusRequest`, `Send_AttackRequest`, … (mirror upstream `OutgoingPackets.cs`).
- **Traffic obfuscation (see docs/CIPHER.md; S2C part CORRECTED 2026-09-29):** C2S = standard UO protocol XORed with a **single per-session key byte** `S` after a 5-byte cleartext client preamble `ef 0000000c`. The server prelude is **13 bytes**, `ff 00 0d | 7×00 | 0c K S`: byte 11 `K` = S2C XOR key, byte 12 `S` = C2S XOR key. Login packet = `91 <len> <name\0> <JWT>`; the HTTPS-issued JWT is the entire game credential (no password on wire). **S2C = from byte 13, XOR `K`, then static Huffman, one flush segment per packet** (53 277/53 277 segments frame exactly across 18 captures; decoder `harness/uo/s2c.py`). The old "unencrypted Huffman from byte 19" decode was wrong and produced garbage that earlier docs misread as a custom dialect.
- Test proxies must use their own `--control-port` (test_proxy 12599, test_actions 12597, test_movement 12598) so tests run safely while the live proxy holds 25941.
- The stock client auto-requests names for mobiles coming into range: it sends `09 <serial>` + `34 edededed 04 <serial>` to several mobiles in the same millisecond (session_20260929_163420 +3.17 s, while walking). The server answers with `0x1C` type-6 labels (e.g. "Len the banker"), so the world-model event log usually already contains NPC titles and agents rarely need to click.
- Bank box: saying "bank" within range of a banker gets `0x2E` (bank-box item, graphic 0x0E7C, layer 0x1D, parent = player) + `0x24` (open, gump 0x4A) + `0x3C` (contents), ~50 ms later (sessions 161433 and 163420).
- **C2S uses the client's own (Outlands v12) length table, not upstream's** (2026-09-29). Every `NetClientExt.Send_*` pads to `PacketsTable.GetPacketLength(id)`, so `0x08` drop = 22 B and `0x6C` target = 27 B (u32 x/y/z/graphic). `uo/packets.py` `C2S_OVERRIDES` now holds every id where that table differs from upstream. Under the old `{0x91: -1}` a real client target framed as 19 B and its tail swallowed the next keepalives as a 106-byte `0x00`, and the proxy rejected correct drops/targets. Frame C2S only with `packet_length(buf, overrides=C2S_OVERRIDES)`.
- Outlands senders branch on the protocol version at settings+0x68 (live 12): `>= 10` writes widened u32 coordinates. Upstream `OutgoingPackets.cs` shows only the `< 10` form for drop/target, so don't copy layouts from it without checking the decompile. `Send_TargetCancel`'s widened form sends `7fffffff` three times, where upstream sends `ffffffff 00000000`.
- Context menus use standard `BF` subs 0x13/0x15 (captured). The captured C2S `0xFF` dialect subs are 3 (keepalive), 4 (cast spell) and 9 (item detail). Skills use plain `0x12` type `0x24`, and `Send_UseSkill` is the only skill sender.
- C2S `0x6C` (27 B, u32 x/y/z/graphic at 11/15/19/23) and `0xB1` text entries (UTF-16 unit count) are parsed with the client's layouts since 2026-09-29. The old 19-byte/u16 and byte-count readings came from mis-framed captures.
- **Cliloc** (2026-09-29): `Cliloc.enu` in the install root is **uncompressed** (byte 3 ≠ 0x8E): header i32+i16, then `i32 number | u8 flag | i16 len | utf8`. 107 922 entries, 500000–3011032. S2C `0xC1` = serial, graphic, type, hue, font, cliloc u32, name[30], args UTF-16**LE**; `0xCC` adds affix flags u8 after the number and an affix asciiz after the name, with args UTF-16**BE**. The Outlands handler `DisplayClilocString @ 0x140196760` reads exactly like upstream. Harvest messages: 500498 "You put some logs into your backpack.", 500488/500493 "There's not enough wood here to harvest.", 500495/500496 "…fail to produce any useable wood.", 1072540+ "You chop some … logs…". Render with `harness/uo/cliloc.py`.
- S2C `0xBF` sub 0x14 context menus arrive in mode 2 (cliloc u32, index u16, flags u16); e.g. "Giles the thief": 3006123 Open Paperdoll, 3006103 Buy, 3006104 Sell, … (session 141253). S2C `0x74` buy list = container u32, count u8, (price u32, u8 len, name incl. NUL). C2S `0x3B` buy = vendor u32, flag 2, (layer 0x1A, serial u32, amount u16)…
- Outlands map/art files aren't the stock names: `facet00.mul`–`facet05.mul`, `art.uoo`, `artdata.uoo`, `anim.uoo` in the install root. The map and tiledata formats are decoded in docs/MAP.md, and so are the gump and item art pixels (`gumps.uoo`, `art.uoo`: raw-deflate RGB555; art.uoo ids are item graphics with no 0x4000 offset, read by `harness/uoart.py`, 2026-10-02). `anim.uoo` isn't decoded.
- **S2C 0x21 move-reject is 15 B with z = i32be at offset 11** (`seq, x u32, y u32, dir, z i32`). For example `21 fb 00000778 000009fd 80 ffffffec` is z −20 (found while validating docs/MAP.md). `harness/world/layouts.py` read i8@11, which gave 0 for z 0…255; **fixed 2026-09-29** (tests use the real packet). The proxy's re-anchor already wrote the i32 form.
- **Mobiles don't block walking in UOO:** you shove through them with enough stamina (user,
  2026-09-29). **Measured 2026-10-22** (user: "in Outlands I can run through mobs when I have full stamina, and they
  block me when I don't"; `harness/sim/shove_scan.py` over the 82 captures since 10-13): only at full stamina,
  monster or pet, with "You shove something out of the way." (cliloc 1019042; 1019043 for "something invisible") and
  10 stamina off (25 -> 15 at all 83 shoves into monsters with the cliloc); below full the step is refused (0x21). On
  foot Logan (25 stamina, on Maus 3) loses a point every 15 steps (19 before 10-17) and gets one back every 2.04 s
  (n=4,255): 24/25 after a run is common, and then a mobile blocks. A Total Refresh takes him to full (15-17 -> 25).
  The simulator does the same since then (docs/SIM.md "Shoving and stamina"). `Mover` logs the stamina with each
  refused step. Live attempt 2 failed because the planner treated NPCs in the inn's one-tile upstairs hallway as
  walls (LUMBER_LOOP.md §13).
- **S2C 0x6C with cursor type 3 is a cancel, not a cursor** (ClassicUO TargetManager:
  IsTargeting = cursorType < 3). The server sent one after a moongate Travel reply. The world
  model showed it as an active cursor until 2026-09-30.
- **Buffs on Outlands arrive as 0xFF sub 8/9** (OutlandsBuffUpdate/RemoveBuff), not 0xDF (none
  in the live session). Examples: "Stationary Penalty", with the description "All damage is
  reduced to 1. Move {value} more steps to remove this effect", timer value 5, f2 = 1; and a buff
  titled by cliloc 1075655. `{value}` is `timers[0].value` (decoded 2026-10-03, see "Stationary
  Penalty decoded" below; the parser called it `seconds` until the evening of 2026-10-03, but it is
  never a duration: the timer's `end` is, docs/WORLDMODEL.md sub 8); f2 is always 1.
  The server doesn't always send sub 9 when a timed buff ends, so the world model's `buffs` keeps
  ended ones. The viz (BuffList, Self panel and Inspector) hides a buff whose `ends_t` is past
  (live: wall clock; replay: newest event time); buffs without an end stay until sub 9 (2026-10-17).
- **Str/Dex/Int** from 0x11 were parsed but dropped by the world model (set as attributes the
  snapshot doesn't export). Fixed 2026-09-30; they're now in `self.stats`.
- **Agent gump replies leave the client's copy on screen:** the stock client closes a gump
  itself when it answers, but an agent `0xB1` bypasses the client. So since 2026-09-30 the proxy
  also sends the client a close for that gump id (S2C 0xBF sub 4, button 0, which only disposes
  it; ClassicUO PacketHandlers.cs:4154-4183). It's client-only like the 0x21 re-anchor and
  covered by `test_movement.py`. Seen live: the moongate menu stayed drawn after the overseer's
  Travel.
- **A creature that goes for you shows the war-mode flag (0x40)** in its mobile updates. In the
  NPD capture 20260930_110946, 10 of 15 mongbats had it (the ones that fought). Sheep, giant
  rats, hinds, an eagle, a great hart, zombies and a harpy never did and never attacked. So
  `threats.Params` treats unknown creatures as harmless unless they're in war mode, murderer-red
  or known aggressive. Before 2026-09-30 it counted every unknown creature off Shelter as
  dangerous, which stopped lumber trips at Horseshoe Bay for a goat and a walrus.
- **A new NPC's click label lags its first mobile update by ~60 ms; threats.py misreads it until
  then.** Juncture 44 (2026-10-01, session 191355): the lumber runner aborted on "grey
  0x00411866, assumed player" at 18 tiles. It was **Beaman the battle trainer**, a stationary
  Shelter NPC at (1914, 2636). Its one 0x20 reads body 0x190, notoriety 3 (gray), hue 0x03FD,
  flags 0x40 (war mode: it spars with a dummy, 0x6E/0xC0 every ~4 s), with no 0x20 player bit
  and no 0x77 moves (it never moved). Timeline: 0x20 at 20:37:48.832; client 0x09/0x34/0x98 at
  .837; 0x1C type-6 label "Beaman the battle trainer" + 0x11 name at .894. The runner's
  assessment ran at .886, between the two, so `identify()` fell through to "human body, no npc
  evidence (assumed player)". With the label it's `npc`/ignore, and the restarted run worked
  15 tiles from the trainer without a stop. Battle trainers are notoriety 3 (Riane too), so
  every one entering view could trip this.
  - **Fixed (2026-10-01): label grace in `threats.Watch`.** A human that would be hostile only by
    the assumed-player fallback (no 0x20 player bit, no label) gets `watch` for
    `Params.label_grace_s` = 1 s after Watch first sees it, then flees as before. A player bit or
    any label skips the grace. Replaying session 191355's S2C through WorldRuntime + Watch gives
    `watch` at the trainer's 0x20 and `npc`/ignore at its 0x1C. Rejected: a Shelter-wide "greys
    are harmless" rule, which needs the island boundary and doesn't fix the race elsewhere.
- **z while walking comes from the map (since 2026-09-30):** ConfirmWalk (0x22) carries no z. So
  the proxy's MoveAuthority computes z after each confirmed step with the client's walk rules
  (`SessionTap._step_z` → `pathfind.Walk.can_walk`, with the world model's ground items), the way
  the stock client does (CalculateNewZ). Server anchors (0x1B/0x20/0x77/0x21) still overwrite
  it.
  - Before this, z changed only on anchors. It was wrong on 12 of 57 anchors in the live session
    20260930_091704, and wrong in the client re-anchor (the fabricated 0x21 carries our z). That
    broke `ctl goto` ("no route" right after arriving) and had the overseer believe it was in the
    moongate cave when it was on the hill.
  - Map z matched all 57 there, and every mapped anchor of the demo capture (`test_pathfind.py`).
  - Steps the rules can't place count as `movement.z_misses`, and z stays until the next
    anchor.
  - Facets without map data (rental rooms, facet 3) keep the anchor-only z.
  - **A surface at exactly z -128 holds (found 2026-10-28).** Upstream's CalculateNewZ marks "no
    surface" with resultZ -128 (sbyte z); `Walk.new_z` copied it, so a tile standing only at
    -128 refused every step. Outlands' z is an int: the client sent and the server confirmed
    SE (6886,1980) -> (6887,1981) (session 20261024_225113, 2026-10-25 11:29:48.843, z -126)
    and NW back (20261025_210102, 21:20:58.591, z -127), both past the corner (6887,1980),
    underwater sand at land z -128 and nothing else (Tidal Tomb). A diagonal needs both corners
    (CanWalk), so those two moves were the only ones of the 275,040 in walk memory our rules
    refused there. `new_z` now returns None for "none". Map0's land and statics never go below
    -128, so the other -128 starts don't matter [INFERENCE: the decompile
    `decompiled/map_formats.c` named in pathfind.py is not in the repo to read the int version's
    sentinel]. 117,223 land cells (0.18 %) sit at -128, 101,149 of them impassable (0x244 void
    mostly); the 16,074 others (underwater sand, "vulkan") now stand. No capture has a server z
    of -128 (the lowest is -127), and none walked onto one of the 1293 tiles of the Tidal Tomb box
    (6800-7049, 1900-2099) that stand only at -128.
  - `--no-map-z` turns it off.
- **The server auto-retaliates (live 2026-09-30, New Player Dungeon):** when monsters (mongbats)
  attacked TestWorth, it fought back and killed them with war mode off. In the 10 minutes before,
  no C2S attack (0x05) or war-mode (0x72) packet came from the agent or the client (session
  20260930 after 10:02, proxy jsonl). So "never attack" means never *initiate*; auto-defence is
  server-side and normal for any player. It also means weak monsters won't kill a standing
  character (death demo).
- **Shelter has a cave/cellar level at z −20** under the inn and the forest west of it
  (`cave floor` statics 0x053B–0x053F under land at z 5). Harvest and vendor ranges are
  accepted across it (2D) `[INFERENCE]`. Goals must be height-aware (LUMBER_LOOP.md §13,
  run 3 finding).
- **Stock-client shapes the agent must copy (2026-09-30, human captures):**
  - a right-click is `09 <serial>` then `bf 0009 0013 <serial>` 0-1 ms apart (30/30); Outlands
    has tooltips off, so ClassicUO's DelayedObjectClickManager sends the click first
  - a click on a mobile is `09` then `34 edededed 04 <serial>` 0 ms apart (1834/1834 in the
    audited captures)
  - held run key: steps every 200 ms (565 of 1102 intervals in 200-220 ms, session 204225);
    human walks change heading on 21 % of walk packets
  - auto-open doors: `12 0005 58 00` 55-101 ms after the walk that faces the door, before the
    step into it
  - server cursor cancel: `6c 00 00000000 03` + zero padding (27 B); the client answers it with a
    cancel carrying its *old* cursor id if it was still targeting
- **Server hitches confirm walks late:** 2.0-2.3 s after the send in 20260930_123206 and 091704
  (normal RTT ~50 ms); in 091704 the server sent nothing at all for those 2.3 s. The proxy waits
  3 s (`CONFIRM_TIMEOUT_S`) and still recognizes a confirm up to 5 s after that (docs/MOVEMENT.md).
  Live 2026-09-30 19:00:00 local = 00:00:00 UTC (session 182751, Prevalia): a 6.8 s freeze ended
  with ~280 S2C `0x1D` deletes in one burst, and then the walk's confirm. That was the daily
  Test Shard wipe clearing rooms and houses (ours too). It was not a world save: saves come every
  ~15 min, are announced ("The world will save in 15 seconds." … "World save complete. The
  entire process took 2.2 seconds.") and took 2.2 s. The confirm landed inside the late window:
  hidden, ladder moved on, 0 resyncs. `Mover` still counts such a walk as one `blocked` (it gave
  up at the timeout).
- **The midnight-UTC Test Shard wipe clears rental rooms.** Afterwards the innkeeper's room
  menu has no rented-room button 7, and its button 4 means "Rent This Room" (5,000 gp/week:
  "Click twice more to confirm your rental room agreement."). `ctl act gump` doesn't guard
  this; the lumber runner aborts without button 7.
  - **The room menu while a room is rented** (gump `0x8EAEFBDB`, Kay the innkeeper at Outpost,
    3052,512, live 2026-10-03):
    - **4 "Enter Your Room"** ("You enter the rental room."; the lumber config's `enter_button`).
    - 5 "Invite Player"; 6 "Visit Other Rooms".
    - **7 "Expand"**, which opens the Expand Rental Room gump: +175 lockdowns, +1 secure, +2,500
      rent. In that gump, 2 is Expand and 3 is Cancel (cancelled at no cost).
    - 1 and 3: unlabelled.
    - The menu also shows the next payment ("6 Days 17 Hours"), lockdowns 125/350 and the bank
      balance.
    - So button 7 is only the "a room is rented" marker. Never press it to enter.
- **Outlands sends overhead titles as type-0 "speech" (2026-10-01, all 27 captures).** A click
  (`0x09`) on a player gets the label (type 6) plus type-0 lines from that player's serial: the
  title ("Viceroy", "Legendary Woodsman") and the guild tag ("[Veteran, J4F]"), 0.04-0.06 s after
  the click (66 of 66). Pets answer "(bonded)"; mobs show damage as type-0 numbers ("-57"). Real
  speech ("bank" from Kanbalt and Tirera in 123206) came 19-235 s after any click. So "someone
  spoke" needs the click-echo window (`speech_guard.CLICK_ECHO_S`).
- **Mobile packets in this dialect:** `0x20` MobileUpdate carries any mobile: `serial, body:u32,
  notoriety, hue:u16, flags, x:u32, y:u32, 2 bytes, dir, z:i32` (28 B). `0x77` is only `serial,
  x:u32, y:u32, z:i32, dir` (18 B), no body/flags/notoriety. Test fakes must use these (a
  ClassicUO-shaped 17-byte `0x77` fails to parse).
- Frontend toolchain: **Bun 1.4.2** installed 2026-09-29 (user-level, `irm bun.sh/install.ps1 | iex`) at `~\.bun\bin\bun.exe`, added to the user PATH (new terminals only; in the agent's git-bash shell call it via PowerShell or the full path). There is no Node/npm on this machine. The visualizer frontend (docs/VISUALIZER.md) uses Bun for install/bundle/test: React + TSX.

## CAPTCHA facts (wiki)

- Triggers: lumberjacking, mining, fishing, forensic evaluation, sheep shearing, lockpicking chests — every 5–10 min of activity. (Land fishing currently exempt.)
- Mechanics: enter the dotted digits, click Okay twice; success suppresses next captcha for 10–15 min. Fail ×3 = 6 h harvest block (scales with priors). Same captcha persists across relog until solved; closing it cancels the harvest attempt.
- Digits are fixed shapes with dots displaced — template-matching territory. **Solver built 2026-09-30: `harness/captcha.py` reads the digits from the gump layout's tilepic dot clusters against `harness/data/captcha_font.json` (mined from the 364 captured captchas, 1,093 references since 2026-10-20; digit 0 is synthetic, unverified, and none of the 1,092 real digits was a 0; every other digit has 105–133 real references). Margin-gated; falls back to pause + beep. Tests: `harness/test_captcha.py`, including a leave-one-session-out check (ANTICHEAT.md §8.8 "Live").** **Since 2026-10-01 (user decision) it only runs in captcha mode `auto`:** the mode is `meta.captcha_mode` in the memory store (missing = `human`), switched from the viz header; in `human` the runner pauses and beeps until the client shows "Captcha successful." (ANTICHEAT.md §8.8).
  - **Growing the dataset (since 2026-10-03): `python harness/captcha_mine.py [--dry-run] [TAG ...]`.** It replays every `logs/session_*` capture (`replay.timed_packets`), takes each real captcha (S2C `0xB0`/`0xDD`, gump id 1 with a `textentry`), pairs it with our `0xB1` answer to that gump serial and keeps it only if the server's "Captcha successful." follows within 10 s (pairing stops at the next captcha gump). New layouts are appended to `captcha_samples.json` and their normalized digit clusters to `captcha_font.json`; known layouts are skipped, so a rerun is a no-op. For each new captcha it prints whether the pre-run font solves it and its weakest digit margin (out-of-sample evidence). After it adds anything, bump the hard-coded sample count and measured figures in `harness/test_captcha.py` and rerun it (~1 min with `UO_TEST_WORKERS=8`). It re-finds all 24 hand-mined samples byte-identically. First run (2026-10-03, dry run): 26 accepted, 2 new, both in 20261003_150103, which kept recording after the hand mining: 437 (button 606) and 792 (button 933). The current font solves both right, min margins 0.58 and 0.43. With 150103's own sample and references removed, the 357 captcha solves right at margin 0.50. 15 captures from 2026-09-28/29 morning don't replay (`timed_packets` row/raw mismatch at the prelude); their S2C streams hold no captcha gump.
  - **Dataset 364 (2026-10-20, all 172 captures).** 364 accepted captchas, 55 already present, **309 new from 40 sessions** (20261003_150103 … 20261020_102748; the most from 20261009_102148 with 51, 20261011_124101 with 25, 20261020_102748 with 19). 306 of the 309 were answered live by the solver and 3 by the human (643 on 10-10 21:37, 343 on 10-19 20:08 during the hand-mining capture, 124 on 10-20 17:14). Over all 364: 348 solver answers, 16 human, and no captcha answer was ever rejected. Every "Captcha successful." in the captures (364) pairs with a mined sample. Not mined: the 15 captures above and the empty 20261015_165115 don't replay (no `0xB1` to gump id 1 in their C2S either); two captchas got no accepted answer: 20261017_190952 20:31:36 (never answered; a fresh one 20 s later was answered by the solver and accepted) and 20261019_133551 18:05:38 (closed by the client 38 min later, button 0, no text). Out of sample against the font of 2026-10-06 (55 captchas, 166 references), the 309 read 308 right, 1 refused, 0 wrong with the old split, and 309 right with the new one (below).
  - **The 17:14:57 refusal (124, mining run `mining-20261020-171059-5dee`, "auto-solve gave up") was a digit under the margin, not the layout gates.** The 1 has two stray dots at x 125–126, 32 px right of its stem; the 2 starts at x 145. The split then cut at the widest x gaps, and the 32 px gap left of the strays beat the 19 px one right of them, so the 1 got 11 dots (x 55–93) and the 2 got 15 (x 125–220). That 2 read "2" at 0.0574 against "3" at 0.0597: margin 0.038 (gate 0.09). The 1 read at 0.633 and the 4 at 0.532. The human typed 124 and it was accepted at 17:15:27.
  - **Fix: the layout says which dot belongs to which digit (`captcha.digit_clusters`, 2026-10-20).** All 364 layouts list each digit's tilepics together, digits left to right, and draw each digit with one tile graphic, random per digit. Adjacent digits share a graphic in 25 of them, never all three. So the split now cuts the tilepic list where the graphic changes. Inside a shared run it takes the plausible cut leaving the tightest clusters in x (least summed squared x deviation), which agrees with the old split on all 25. More than three graphic runs is refused. The old x-gap split had given a stray dot in the gap to the wrong digit in 15 of the 364 (16 dots); 14 still read right. With the 2026-10-06 font the new split reads 124 at margins 0.775 / 0.628 / 0.532, so the fix is general, not via the font: against the 363 other captchas' references even the old split would have read it (the 2 at 0.558), but the font of the day couldn't. The 4 references the old split had cut wrong (the 7 and 9 of 792, the 1 and 5 of 315) were re-cut from their samples.
  - **Results (2026-10-20):** in sample 364/364 right. Leave-one-session-out over the 53 sessions: 362 right, 2 refused, 0 wrong, the same for both splits: 482 of 20261005_194043 (the 8 against a 4 at 0.033) and 229 of 20261015_093103 (right, margin 0.067); the closest solved margin is 0.115 (356 of 20261013_103242). The synthetic noise bootstrap (now 200 sampled references × 5 draws) reads 94.48 % right and 31.1 % rejected, so its bars went to 94 % / 35 % (the same drift as on 10-03 and 10-06). Fixture: `harness/testdata/captcha_strays.json` (`test_captcha.py` "stray dots of the 1 lying nearer the 2", also read with the font's first 166 references, the 2026-10-06 font). `read_digit` against 1,093 references took 136 ms (~14 ms against 166 on 2026-10-08) and 398 ms with 8 running at once (memory-bound numpy temporaries); `_costs` now scores 16 references per pass, the same result bit for bit, 82 ms alone and 85 ms with 8 at once. `test_captcha.py` reads in worker processes (`UO_TEST_WORKERS`, `cpu_budget`): 53 s with 8.
- **The harvest attempt that raised the captcha resumes after the solve** (all 24 captures, and the Smart Harvest self-target at 23:20 in `20261001_214649`: captcha, solve, then "nothing nearby"): the server answers that attempt right after "Captcha successful.", at once for an instant check (500493 not enough wood, 500489 not a tree, "You do not see any harvestable resources nearby.") or one chop time later (~4.1 s: logs or 500495 fail). So the runner's next chop result after a captcha belongs to the attempt it sent before the captcha.
- Loop-relevant mechanics (wiki, read 2026-09-29; details and links in docs/LUMBER_LOOP.md §2):
  - Lumberjacking uses Smart Harvest: double-click the hatchet and target yourself; the server chops a nearby tree with wood left. **Confirmed (user, and capture `20261001_214649` at 23:20 and 23:47):** the hatchet's cursor answered with our own serial gives the ordinary chop results (e.g. cliloc 500495), and with no tree in reach "You do not see any harvestable resources nearby." plus "You cannot produce any wood from that." over our head. All of Jaseowns' public lumber scripts do the same (ANTICHEAT.md §3). **The runner uses it since 2026-10-04** (built offline, attended live trip pending; docs/PLAN.md "Smart Harvest for lumber"). Range: 2 tiles, measured from the runner's `stand` job events (below); which tree it picks is unmeasured.
    - **The client's self-target, byte for byte** (replayed through the proxy's SessionTap, `test_loop_lumber.py unit_capture_smart_harvest`): `6c 00 <cursor id> 00 <our serial> <x u32> <y u32> <z i32> 00000190`, the server's cursor being type 1 (location allowed). `combat.target_self` with movement's `pos` and `world.self.body` builds exactly that. **Its z is movement's (0 at both tiles), not the world model's `world.self.z`, which read 10 there.**
    - **"You cannot produce any wood from that." alone means the targeted thing isn't harvestable:** at 23:40.8 the user answered the hatchet's cursor with his worn leather chest and the server put that line as a label (type 6) over the chest (serial `0x4B6BB8A5`), with no "nothing nearby" line. With a self-target it comes over us, right after the system's "You do not see any harvestable resources nearby.".
    - **The server turns us toward the tree it chops** (RunUO's harvest does `from.Direction = GetDirectionTo(target)` [INFERENCE for Outlands]): at 23:47 (1905,2616) the self-target was followed by `0x6E` (chop animation) and a `0x77` for us with dir 3 (SE); the only tree within 6 tiles (map0 `find_trees`) stood at (1906,2617), SE at distance 1. We already faced SE from the last step, so this sample can't prove the turn. The chop sound `0x54` 0x013E plays at our own tile, not the tree's. At 23:20 (1918,2612) 'nothing nearby': the only tree static within 6 was 0xACA9 at distance 2 (a graphic with 1 not_tree in harvest memory), so that sample bounds nothing.
    - **Reach is 2 tiles (Chebyshev), measured 2026-10-08 from the memory store** (4 768 `stand` job events, 10-04 → 10-08, facet 0, map0 `find_trees`; the runner had `SMART_RANGE = 1` until then and uses 2 since). Of 1 029 mid-stand changes of `faced` (the server turned us between two chops), the nearest map tree in the new direction stood at distance 1 in 657, 2 in 348, 3+ in 15, none in 9; a random direction at the same stands would give 231 / 145 / 387 / 267. So it chops trees at distance 2 and not beyond.
    - **Consequence: 1 194 of 4 768 stands (25 %) answered "nothing nearby" on the first attempt.** They cost ~1.5 s on the stand + ~1.3 s walking there (median 1.2 s / 0.7 s), 54 min = 3.5 % of 25.5 h of stand + between-stand time. Replayed offline: "skip a planned stand when every map tree within 2 of it is within 2 of a stand that said 'nothing nearby' in the last 45 min" flags 734, of which 730 were really dry (61 % of them; 4 false, 90 logs). With 90 min, 142 false (regrowth lands between 45 and 90 min). Most of the rest have one tree within 2 that no dry stand covered (depleted from a stand that ended on quota, break or interruption, or by someone else).
    - **Stands dry on arrival in trees we never chopped can't be predicted from our data** (first run at `SMART_RANGE = 2`, witcher_247, 2026-10-08 21:15–21:23): 16 of 33 stands said "nothing nearby" at the first attempt, none within 2 tiles of any stand of ours ever, so someone else had emptied them. Tree graphic doesn't separate them (10 graphics, dry and wood alike). A dry arrival barely predicts the next one either: P(next stand in the trip dry on arrival) = 30 % after a dry arrival vs 24 % after one with wood (4 735 pairs). Each costs ~2.5 s. Every stand still *ends* on "nothing nearby": that line is the only signal that the trees in reach are empty.
  - Harvesting is blocked in town regions, except Shelter Island while Young.
  - Shelter Island ([wiki](https://wiki.uooutlands.com/Shelter_Island)): no hostile player actions; bank and vendors need Young; harvest chance 50 % of normal; skills cap at 80. Leaving the island by moongate, hike, recall or gate asks you to confirm renouncing Young, and that's permanent.
  - TestWorth is Young as of 2026-09-29: the Young-only "Welcome to Shelter Island" gump `0xC16E0192` opens at login (sessions 163420, 202723).
  - **Moongate gumps (all captures through 20261001_191355).** Moongates are walkable; stepping onto one only opens a gump, sent *before* that step's confirm `22`, and the server never closes it (user, 2026-10-01; captures). Both ids are fixed across sessions:
    - `0xE0E675B8` "Moongate Destinations": button 2 "Travel to Destination", 10–21 (22 at Prevalia) the destinations, closable. Picking a destination then 2 travels (sessions 120126, 123206, 182751). Closing it with button 0 was done in 100200 and 123206.
    - `0xE2544541` "Young Player Status" (the renounce prompt, Young only): buttons 1 Guide, 2, 3; no text entries; closable. "Leaving Shelter Island will cause you to renounce your Young Player status…" (newbied items stop being newbied and drop on death; other players may do harmful actions outside towns; you may flag criminal). In 120126 the human pressed 2, got "Click again to confirm your selection." and pressed 2 again: that is the renounce. A Young character gets it after choosing a destination at the public gate, and directly when stepping on a player-cast gate (191355 at 28:09, (1966,2533,38); nothing replied then, the character stayed Young, and the prompt stayed up in the client).
    - **Since 2026-10-01 the Mover closes the gump of a moongate a route only passes over** (stock `0xB1` button 0 after a reaction pause; `agent_link.Mover.close_gate_gumps`; never noclose/buttonless/captcha gumps). A `ctl act goto` onto a gate's tile leaves its gump for `act gump`, which allows only button 0 on the renounce prompt ("renounce" in its text).
  - **Player-cast moongates are a fixture of Shelter's north end.** Vendor-house advertisers ("Outpost Gater", "Soflo") keep casting Gate Travel (`Vas Rel Por`) near King's Tower: `0x0F6C` gates kept reappearing at (1966,2533,38), (1966,2536,40) and (1960,2531,30) with fresh serials over the whole session (27–75 min). A static gate item `0x40000D5B` stands at (1977,2533,50). Lumber routes between the bank and the northern trees pass through these tiles.
  - **Hackworth** (the 2026-10-01 fresh Young character): Camping, Magery, Magic Resist, Spirit Speak, Tracking, Wrestling, Mining 60, Lumberjacking 69 at the end of session 20261001_191355 (replayed skills).
  - Test Shard ([wiki](https://wiki.uooutlands.com/Test_Shard)): every house and inn room is cleared every 24 h at midnight UTC. Test resource stockpiles are only in North Prevalia and Corpse Creek, which are off-island for a Young character. Documented test commands: `[TestRes`, `[TestIgnoreMaxDamageCap`, `[TestMaxMeleeDamageRolls`, `[TestMaxSpellDamageRolls`, `[TestBlessedGear`, `[Go`. None of them grants gold.
  - TestWorth had 0 gold (2026-09-29). Shelter NPC vendors answered "You have nothing I would be interested in" to sell attempts (sessions 141253, 164548). Update the same day: a mongbat kill in the New Player Dungeon gave 21 gp (the live state port shows `gold 21`), and the user confirms TestWorth holds a Rental Room Credit Deed, so renting needs no gold.
  - 60 s harvest lockout after recall, gate, hike, teleport or rope.
  - Log/board weight is 0.025 st.
  - Commodity deeds (5 gp at a banker) list boards (5 000 regular / 2 500 colored), not logs.
  - Rental rooms: say `rent`/`room`/`house` to an innkeeper. You exit to a random inn room. No recall in. Floor items decay after 1 h unless locked down or secured.
  - **Rental-room exits land upstairs** in the Shelter inn: a random room at z 20 (demo (1932, 2589, 20); live agent run (1938, 2584, 20)), with doors between the room and the stairs. 2D walk memory can't tell the floors apart, so pathing out needs z-aware map data (docs/LUMBER_LOOP.md §13).
  - **The lumber runner banks the boards (since 2026-10-01, LUMBER_LOOP.md §12.5).** A fresh character needs a hatchet, worn, in the backpack or in a bag in it at any depth (worn first, then the shallowest; the runner opens the bags on the way before using it, LUMBER_LOOP.md §13; with none known it aborts at start), and Young status (the Shelter bank and its banker serve only Young characters). Nothing in lumber.json is per character: the runner reads self, backpack and hatchet from the world model. The banker (Len, `0x000001EA`) is a world NPC, and the same serial shows in sessions 163420 and 204225. The runner walks to his live position; if he isn't in view, it uses the demo position.

## Tracking (live 2026-10-01, session 20261001_214649)

The user used Tracking (Hackworth, skill 60) in the client on Shelter: Hunting mode on innocent
players, then on murderers (none on Shelter), then the four category buttons, then Hunting mode on
passive creatures. Everything below is from that capture; the harness only watched.

- **Start:** C2S `12 0009 24 "38 0"` (UseSkill 38). The server answers with cliloc 1011350 "What do you wish to track?" and gump `0xFE5C638B`, closable, no text entries. Every click gets a fresh copy of the gump (new serial, same id) showing the new state.
- **Gump buttons:**
  - 1 Guide
  - 2–5 the classic categories Aggressive / Passive / Townsfolk / Players. Each click is a skill use: the gump goes away (use the skill again to get it back; "You must wait a few moments to use another skill.", cliloc 500118, came 3.2 s after one). All four answered "You are unable to detect signs of anything outside your field of vision." and nothing else, even Passive while a pack llama stood 20 tiles away (Hunting found it 27 s later; a failed roll or a shorter category range, [INFERENCE] either). So the categories only report what is **outside** the view, and no list gump has been seen yet.
  - 11, 12, 13 the Hide Party/Guild, Hide Allies and Hide House toggles
  - 6 Begin/Stop Hunting (its art changes 4008 → 4009 while hunting)
  - 8 / 7 next / previous hunting mode. The server says "You will now hunt …" and changes the hue of the mode icon (tilepichue 8454 at 411,46). Cycle from button 8: criminal players → innocent players → friendly players → aggressive creatures → passive creatures → townsfolk → all players → all hostile players → enemy players → murderer players → criminal players. Button 7 goes back.
  - 9 / 10 arrows either side of the hunt frequency text: "Always Get Closest" was the current one (not changed)
- **Hunting:** button 6 → "You begin hunting." from self, plus buff icon 173 (cliloc 1110004 "Tracking Hunting") on self (`0xFF` sub 8; removed by sub 9 on "You stop hunting."). Each hit is:
  - a system line "Now tracking: Joel Embiid (3 spaces to target)"
  - the arrow, `0xFF` sub `0x1A` mode 0 {arrow id, target serial, x, y, z, "[Hunting] <name>"}. Its x/y equalled the mobile's position (docs/WORLDMODEL.md §5 sub 0x1A).
  - A new hit first cancels the previous arrow (mode 1). The ids count up (0, 1, 2, …), and the same target hit again gets a new id. Hits came 11 s after Begin, then 5.4 s and 16.5 s apart; on the llama 5.8 s after Begin, then 43 s later (same spot).
  - **The arrow is a snapshot.** No packet moves it; only a new hit (respot) does (user, 2026-10-01, and no arrow traffic between hits in the capture).
- **Beyond the view (passive-creature hunt):** "Now tracking: a pack llama (20 spaces to target)" + arrow {serial `0x0132954E`, (1931,2596), z 21}. That mobile was never sent to us (no `0x20`/`0x77`/`0x78` for its serial in the whole session): the arrow is the only source of its serial and position. The z of 21 (we stood at z 10) settles the 4th u32 as z, not the facet.
- **"Distance to destination: 7 steps."**: a system line every ~5.4 s (gaps 5.3–10.9 s), from 6 s after the second arrow (a player 7 tiles away) until 4.6 s before the llama arrow replaced it. It kept coming after "You stop hunting" and through the murderer hunt, because that arrow was never cancelled. The two llama arrows got none in 100+ s. [INFERENCE] the line reports the distance to a player arrow's spot (creature arrows don't get it, or it only fires within some range).
- **Murderer hunt with no murderer around:** nothing at all, no "nothing found" line. The skill still gained (`0x3A` single-skill updates 60.1 → 60.5 over ~50 s), so hunting checks run without a target.
- **The arrow carries no notoriety.** The hunt mode says what kind of mobile was hit, so a consumer has to follow the mode from the "You will now hunt …" lines (or the gump's mode icon hue).
- **Not seen yet (needs a capture off Shelter):** a red or grey target; a player beyond the view; the hidden-target case; the category list itself.
- **World model (since 2026-10-01):** arrows come out as `quest_arrow_set` / `quest_arrow_cancel` events with the target serial, and `world.tracking` keeps {hunting, mode, arrow, recent hits with the mode at hit time} (`world/state.py` TrackingState; mode only from System lines, begin/stop only from our own serial, so a player can't spoof them by speaking). Replaying this capture gives hunting = passive creatures with the llama arrow up. The agent sets the mode and starts/stops Hunting with `ctl act track <mode>|off` (docs/OVERSEER.md). Nothing feeds `threats.py` or the runners from hits yet.
- **`ctl act track reds`, live 2026-10-01 22:12 (session 20261001_214649, at 1537–1547 s):** with the gump closed and the mode unknown to the running proxy, it sent the stock UseSkill (`120009243338203000`, byte-equal to the client's own), then 5 × button 8 (passive creatures → townsfolk → all players → all hostile players → enemy → murderer players, one "You will now hunt …" line per click), then button 6: "You begin hunting." and the Hunting buff on. Clicks 0.9–2.6 s apart, each `b1 0017 <latest gump serial> fe5c638b <button> 0 0` like the client's. Forward and back were both 5 steps; forward is taken on a tie. The live proxy predates `world.tracking`, so `status.tracking` stays empty until the proxy restarts; the act itself reads the server's lines and the gump, so it works either way.

## World model keeps dead and out-of-range mobiles (live 2026-10-01, session 20261001_214649; fixed 2026-10-01)

Found while the overseer fought mongbats in the New Player Dungeon (the user watched the client).
Times are seconds after 1790911000 (22:16:40 local).
- **Ghosts:** the overseer cast Lightning at mongbats that `status` showed 1–5 tiles away. The
  server answered "That is too far away." (a range failure, not LOS), and the user saw no mongbat
  on screen. The proxy's `WorldRuntime` kept every mobile until an S2C `0x1D`. A timed replay of
  the capture (`harness/replay.py` `timed_packets`, which pairs each jsonl row's time with the raw
  packet) shows both ghosts left the client by range, long before the attacks:
  - `0x002C1E27`: last update at 176.2 (5535,524); the client dropped it at 176.6 (its own
    close-status `bf 000c 002c1e27`, which ClassicUO `Entity.Destroy` sends) as we walked away.
    It died out of our sight: its corpse's `0xFF` sub `0xDEAD` `4fedc20d 002c1e27 01 "a mongbat
    corpse"` came at 491.7 when we walked back into range, and again at 544.6 with notoriety 3.
    The agent attacked it at 507.1 and 548.2 and targeted it 3 times at 856–895.
  - `0x002C3593`: last `0x77` at 888.0 (5535,528). We teleported out (~3600 tiles) at 906.4 and
    the client dropped it at once (`bf 000c` at 906.46). Its corpse's `0xDEAD` came at 1139.2 in
    the burst of 9 corpses we saw on returning, so the agent's `05` at 1156.1 and its 9
    Lightning targets (1160–1235) were all at a dead mongbat.
  - `0xDEAD` is the corpse's data, sent with `0xAF` + `0x1D` when a mobile dies in view (6/6 in
    the session) and alone whenever a corpse comes into view or its notoriety changes. Only 13
    of the 37 were followed by a `0x1D` for the owner. Its second u32 is the owner's serial.
  - `World.ProcessDeletes` (S2C `0xFF` sub 5, ~1/s: 13 880 in this session) is decompiled now
    (`decompiled/process_deletes.c`): ClassicUO's view-range prune, server-paced, at the range the
    server sets with `0xC8` (18 on Outlands: `c8 12` at login), no dead-mobile rule.
- **Fixed (2026-10-01):** the world model prunes like the client (docs/WORLDMODEL.md §7
  "Pruning"): range on every self move and sub 5, death (`0xAF`, `0xDEAD`), facet change, with
  children; dropped mobiles go to `world.last_seen` (for `ctl npcs`/`goto` and the viz only).
  `harness/audit_ghost_targets.py` replays a capture and lists packets at serials the client no
  longer had; on this session it flags exactly the agent's 3 × `05` and 12 × `6C` at the two
  mongbats (ANTICHEAT.md A12) and no client packet except 94 queued queries for mobiles the
  server deleted in the same burst. On session 20261001_191355 it also flags 3 agent single
  clicks (`09`) at NPCs 139–822 s after they left the view (Limmon, Billiam Gatherer, L H R Z).
- **Combatants are on the wire:** S2C `0x2F` Swing `attacker defender` (e.g. 28 × `0020f127 →
  002c3fb9` in our last fight) and `0x0B` damage by serial. `world.swings` keeps the latest swing
  per attacker; `ctl status` lists `attackers` (swung at us within 10 s, nearest first).
- **Mongbats (NPD, Shelter):** Lightning (Magery 60, spellstone, no reagents) did 33 to a mongbat,
  about 15 % of its health, so roughly 220 hp. Two at once hit Hackworth for 7–10 each every
  2–3 s.

## Experience (mastery-chain XP; wiki read 2026-10-02)

- UO has no XP; Outlands does. A kill gives each damaging player **creature gold value × damage share** ([Experience_Gain](https://wiki.uooutlands.com/Experience_Gain)). The gold value comes from the creature's DifficultyValue and is "the expected amount of gold on its corpse" before weekly bonus / Guild Favors / Fortune, which scale the XP too. The same amount goes to every experience system the player qualifies for (mastery chain, weapon codexes, grimoire, …); aspects get 1/100 of it.
- Mastery-chain XP accumulates without a chain (one is needed to see it: `[MasteryChain` or the chain's gump) and is shared across the account; link 1 unlocks at 250 000 XP ([Mastery_Chain](https://wiki.uooutlands.com/Mastery_Chain)).
- **No per-kill XP text on the wire:** no S2C packet in any capture under `logs/` (raw bytes, ASCII and UTF-16 both byte orders; 2026-10-02 scan, includes the NPD mongbat capture 20261001_214649) contains "experience" or "Mastery". A cliloc-only message can't be ruled out without the cliloc table [INFERENCE: none]. The harness therefore estimates XP as the gold the corpse held when opened (`loop_hunt` loot event `xp`; user note 2026-10-02: "experience is basically the gold value of the monster") [INFERENCE: exact for solo kills; party or shared damage lowers the real figure]. Mongbat corpses held 19–23 gp in the live runs of 2026-10-02 (4 loots took 0).

## Mounted movement and player houses (live 2026-10-02, session 20261002_153718)

- **Mounting is an equip on the mount layer.** S2C `2e 530beeb0 00003ea2 00000000 19 0020f127 0724` at 15:44:20: item 0x530BEEB0, graphic 0x3EA2 (horse), layer 0x19, on self. Later self `0x78`s list it; ctl `status` shows `equipment.mount`. The body stays 0x0190.
- **Dismount and remount on the wire (user, session 20261002_183420).** Dismount at 18:34:29: the client double-clicks itself (`06 0020f127`). The server deletes the mount item (`1d 530beeb0`, sent twice), then draws the horse as a mobile, 0x0033C447 (`0x20` body 0xCC, then `0x78`), and the client queries it like any new mobile (`09`, `34`, `98`). Remount at 18:34:32: the client double-clicks the horse (`06 0033c447`). The server deletes the horse mobile (`1d 0033c447`) and re-equips the same item: `2e 530beeb0 00003ea2 00000000 19 0020f127 0724`. The client then closes the horse's status bar (`bf 000c 0033c447`). The mount item keeps its serial across the round trip. Timed replay: `StateStore.mounted()` reads on foot from the first `1d` (18:34:29.887) and mounted again from the `2e` (18:34:32.887), so the proxy floor and the agent cadence switch with it. Another rider's mount (`2e 405dd993 00003ea5 … 19 002b8a95`, 18:34:26) doesn't count: it isn't on self. No steps were taken while on foot, so the on-foot floor after a dismount hasn't been seen on the wire.
- **Nothing in the movement protocol changes when mounted.** Same `0x02` packet, run flag, seq ladder, token seeds and `22 <seq> <noto>` confirms. Agent run steps, timed replay:

  | | steps | gap min / median | confirm latency median / p90 |
  |---|---|---|---|
  | on foot (15:37–15:44) | 183 | 0.207 / 0.214 s | 0.058 / 0.070 s |
  | mounted (15:44–17:39) | 3195 | 0.200 / 0.220 s | 0.057 / 0.069 s |

  Mounted, the server confirmed every agent step at the on-foot pace. There was one 3 s rejection and no client resyncs, and the per-confirm re-anchor (fabricated `0x21`) kept working.
- **The stock client rides at 0.1 s per step, and Outlands accepts it** (user ride, 17:55:20–17:55:26). 56 client run steps mounted. Same-direction gaps (53): min 0.083, p10 0.097, median 0.100, p90 0.104 s; turns 0.100 / 0.105 s. All 56 confirmed (median 0.057 s, max 0.120 s), at most 2 in flight, no deny, no resync. This matches ClassicUO MovementSpeed.cs (`STEP_DELAY_MOUNT_RUN` 100, `_MOUNT_WALK` 200). The client's 0.1 s gaps on foot earlier in the session are turn-then-step pairs (turn delay), not mounted steps.
- **The agent rides at the mounted cadence (since 2026-10-02, user decision).** `StateStore.mounted()` is true when an item on layer 0x19 sits on self, the same test the client uses (FindItemByLayer(Mount)). The state port reports it as `movement.mounted`. Both follow from it: the proxy floor (`MOUNTED_RUN_STEP_S` 0.1 / `MOUNTED_WALK_STEP_S` 0.2, otherwise 0.2 / 0.4) and the agent cadence (`humanize.STEP_CADENCE_*_MOUNTED`, via `Mover.pace` and `ctl act walk`). A dismount deletes the mount item (0x1D), so the next step is on-foot paced. A Mover step used to take 5–7 full state fetches (loop top, before the send, outcome polls every 50 ms, position, moongate and door checks). A full fetch is 20–60 ms, occasionally 100 ms, at 600 kB with a busy screen; a `snapshot: false` query is ~1 ms. Now the outcome is polled with light queries every 10 ms, and the step ends with one full state that the next step reuses (`Mover.fresh_state`). Simulated with the live latencies (57 ms confirm, 25 ms full state): mounted gaps are 0.110 s on the deterministic profile and 0.109 s median on the normal one.
- **Live, mounted lumber trip at Terran (session 20261002_181221, 18:13–18:15, after the proxy restart):** 104 agent steps, all with the mount equipped. Same-direction run gaps (73): min 0.104, p10 0.106, median 0.111, p90 0.117 s. Turn gaps (22): median 0.112 s. All 104 confirmed (median 0.060 s, p90 0.093 s), with no deny, no rejection and no client resync, and one re-anchor per confirm. The agent sits ~10 ms above the user's 0.100 s median. That is the 3–15 ms step jitter, which never goes below the stock cadence.
- **Lumberjacking works mounted** (first tested at Shelter, policy.json `mount`): the 17:33 Corpse Creek trip chopped 104 logs on horseback.
- **Player houses block walking, and the map files don't show them.** A house arrives as S2C `0xF3` with data_type 2. Its graphic is a `multi.mul` id, not an art id. The client draws the pieces listed in `multi.idx/.mul` around the house's tile (it loads `multi.idx`/`multi.mul`; ClientVersion ≥ 7.0.9.0 records: 16 B = graphic u16, dx/dy/dz i16, flags u32, unknown u32; drawn iff flags ≠ 0). Before 2026-10-02 the harness walk rules ignored the pieces and read the multi id as an art graphic. Corpse Creek house 0x154 at (943,774,z1), footprint x 941–945, y 771–778: the server denied 12 agent moves into it on the trip to the banker (17:38–17:41). Every one is walkable on the bare map, and every one is refused with the pieces (test_pathfind). This session saw 12 houses (Horseshoe Bay, Corpse Creek, Shelter). No `0xD8` custom-house packets so far.
- **0xF3 z is signed** (i32): `ffffffe7` = −25 for an item below ground; the Horseshoe Bay houses sit at −5. The layout read u32 until 2026-10-02.
- **The world model used to drop houses the client keeps.** The server sends a house from beyond the 18-tile view: the Corpse Creek house came at 22 tiles at 17:38:42, 17:41:11 and 17:44:15. The client keeps a multi while it is within view range + the multi's reach (HouseManager.IsHouseInRange; reach = the max |dx|/|dy| of its multi.mul records, 4 for 0x154). The harness pruned it at the next `0xFF` sub 5. Replaying the session, the house was missing from the model at all 12 denies; with the client's rule (`StateStore.prune_range`, since 2026-10-02) it is there at all 12. Without this, the walk-rule fix above would never have seen that house.
- Houses are only known once in view (18 tiles), so a long route can still be planned through one. The Mover's per-step map check then refuses the step and replans, with no server deny. Live check (2026-10-02, the agent at (865,1570) with houses 0xA2 at (880,1566) and 0x16D at (881,1580) in view): the house pieces add 49 blocked tiles around 0xA2. A route to (881,1566) now goes in through the front, (879,1568) → (880,1567), not through the north wall.
- **A house floor carries walks the map calls water: the Anchors Rest house (found 2026-10-28).** `test_pathfind`'s
  "all server-confirmed moves are walkable" checked walk memory on the bare map. 34 of its 36 refusals (2026-10-28,
  275,040 facet-0 moves; 9 of them on 2026-10-08, filed as client data drift) are Logan's walks at Anchors Rest in 13
  sessions, 2026-10-07 21:28 to 10-27 22:04, all client-sent (`src` client); in 20261027_220057 from his arrival at
  (3961,519,z2) (0x20 22:04:28.781). They cross player house multi 0x1B4 (serial 0x53082186) at (3956,516,z-4), 817
  multi.mul pieces over x 3951-3961, y 509-523, sent as 0xF3 data_type 2 1-5 s before each crossing (e.g.
  20261027_220057 22:04:28.780, the first step 22:04:29.218). Its ground floor is "wooden planks" (0x4C7-0x4C9 on the
  tiles looked at; Surface, z 1) on water land (z -5, Impassable), entered through the door items 0x6EF/0x6F0 at
  (3960,520)/(3961,519).
  The server puts Logan at z 1 on it: 0x20 (3960,520,z1) at 22:04:29.423 there, and all 18 0x21 denies inside it (3
  sessions, at (3956,518), (3958,517), (3957,519)) carry z 1. With the house and the session's ground items
  (`pathfind.Walkers.get`) every one of the 34 walks at z 1. Not a rule bug: the test left out what the client is
  sent. The other two refusals were the z -128 sentinel ("z while walking comes from the map"). `test_pathfind` now
  checks a move the bare map refuses again with the ground items and houses (0xF3, test_uomap `scan_session`) of the
  sessions that confirmed it (`nav.build_from_logs(sources=...)`), and pins five of the house moves.

## PK death in the Terran wilds (live 2026-10-02, 19:43; the user's death experiment)

The user asked for Hackworth to lumber in dangerous places until killed, for threat data
(chat#1218/#1262/#1280). Overseer task logs: `logs/tasks/lumber-20261002-192807-7320.log`
and the session capture of that time (local).

- **Corpse Creek is lawless, so everyone reads grey** ("You are now entering a lawless region."). A
  red can't be told from a grey there by notoriety, and `threats.py` aborts the lumber runner on
  any grey player within 18 tiles. A stationary grey by the Corpse Creek healer (Evil Palacinka,
  never approached) aborted two trips in a row.
- **The kill, timed from the memory-store `events` (proxy clock):**

  | t (s) | 19:43 | Event |
  |---|---|---|
  | 0.00 | 18.072 | Bastet's labels arrive: entered view (0x0009E217, notoriety 6, mounted, 111 hits, "Serial Killer [Prevalia]", [Aggressive Captcha, DVLS]) |
  | 0.29 | 18.358 | lumber runner aborts: "red Bastet at 18 tiles (ETA 0.6 s)" |
  | 1.12 | 19.194 | the chop already in flight reports a fail (500495) |
  | 2.80 | 20.869 | "Bastet is attacking you!" |
  | 4.58 | 22.651 | first hit −15, "Their attack hamstrings you!" |
  | 6.48 | 24.549 | −32 |
  | 6.89–10.93 | 24.957–29.006 | Bastet overhead "5", "4", "3", "2", "1" (0xAE regular, hue 0x846, one per second): an **explosion-potion fuse** (see below) |
  | 8.35 | 26.418 | −25 |
  | 9.65 | 27.717 | −23 |
  | 10.96 | 29.028 | −7, `death` at (871,1481), "You have lost a moderate amount of fame." |
  | 12.14 | 30.213 | explosion: 0xC0 type 2 (at a location) graphic 0x36BD at (866,1480,30), sound 0x0207; item 0x53DB79C2 deleted in the same tick |

  **~11.0 s from first sight to death; 4.6 s to the first hit.** 102 damage in 5 hits over 6.4 s.
  The runner's ETA (0.6 s) was far too pessimistic: the first attack came 2.8 s after sight.
  Outside guards; the last guard message was the exit at 18:40.

  **Weapon, not explosion.** All 102 damage came from his weapon: every hit (our 0xA1 hits update)
  landed within 3 ms of a Bastet attack animation (0x6E actions 0x1C/0x1D/0x1A, mounted) and a swing
  sound 0x023C at his tile (870,1480). No spell cast (no cast sound or animation, no effect on us)
  and no explosion before death. The potion's fuse ran out ~1.2 s after "1": it blew 4 tiles west
  of him, 5 from our corpse, after we were dead. No throw (moving effect) reached us. Whether he
  threw it there late or dropped it can't be told from the capture. Item 0x53DB79C2's serial sits
  between blood items created at 24.549 and 26.417, close to the first "5" at 24.957; the
  client never received it, and it was deleted at the blast [INFERENCE: the primed potion]. A
  server-side fuse message and a player's typed countdown look the same on the wire (0xAE from
  Bastet's serial). Sources: `logs/session_20261002_183845.jsonl` (local) and the `events` table.
- **What the harness didn't see.** Tracking in Hunting mode on murderer players was on the whole
  time and recorded **no hit** before or during the attack (`status.tracking.hits` empty).
  `status.attackers` stayed empty because the server sent **no `0x2F` swing with Bastet as the
  attacker**: the only two (22.651, 26.615) are Hackworth → Bastet (auto-defence). His hits arrive
  only as damage numbers. So "being attacked" can't be read from `0x2F`; "X is attacking you!" and
  damage numbers are the signals.
- **Escape window [INFERENCE].** There were 4.6 s between first sight and the first hit, so a
  recall started at sight (Magery cast ~2 s, before any hit could disturb it) might have got out.
  The abort did nothing protective: it stopped the runner and left him standing.
- **After death:** a "Report Murder" gump (0x1F3940C6, Accept 2 / Decline 3; the user chose
  decline, but it had closed by itself after the resurrection). Ghost walk 144 steps to Galatea
  the healer (Terran, 735,1531); within 2 tiles the "Resurrection" gump (0xB04C9A31, Accept 1).
  Back at 100/100 in a robe. Shirt, short pants and shoes came back in the pack; spellbook, dagger
  and scissors stayed in it. The corpse kept the hatchet, the bag with the spellstone (bandages,
  potions), the leather tunic, 968 boards and 447 logs. The riderless horse followed the ghost and
  stayed ours (notoriety 2); a double-click remounted it.

## Lumber yields, regrowth and success chance (memory store, as of 2026-10-02 23:00)

Read from `harvest_attempts` and the lumber `episodes` while building the optimizer
(`harness/lumber_opt.py`; docs/LUMBER_LOOP.md §6). Characters TestWorth and Hackworth, Test Shard.

- **Depleted trees come back after ~45–65 min, not 20.** 137 cases of a depleted tree tried
  again later (the runner's old `--regrow-min 20` produced them): regrown 0/14 at 15–30 min,
  7/50 at 30–45, 12/25 at 45–60, 44/48 after 60 (a fail or a success counts as regrown; a
  depleted answer as not). The isotonic fit reaches P = 0.6 at 65 min. The runner's default is
  now 45 and `ctl lumber plan` passes the fitted value. Retry gaps shorter than the window in
  use are no longer observed, so the estimate can only move up from here unless the window is
  shortened on purpose.
- **Success matches the wiki formula.** Terran 2026-10-02 (Hackworth, Lumberjacking 69.1, iron
  hatchet): 44 successes in 63 attempts = 0.70; Σ tree-colour chance × (skill − offset)/divisor
  gives 0.69 (woods.json).
- **Field rate 1 200–2 000 logs per hour** off Shelter at 69 skill, mounted: the Terran trip got
  334 logs in ~595 s between the first chop and the end of the harvest (9.4 s per attempt,
  7.6 logs per success). Logs per success over the last 300 successes: 7.59. Per-trip logs scatter
  ~14× more than a Poisson count (quasi-Poisson φ over trips), so one trip says little.
- **Until 2026-10-02 only trips that reached the bank were recorded.** The aborted Terran and
  Corpse Creek runs (greys, unknown outcomes, the PK) left no episode row; the Terran death came
  11 s after the runner had stopped, so no lumber `death` job event either. The proxy's own
  `death` event (`events` table, ev `death` with x/y) has it; with no trip row around it, the
  optimizer blames it on the spot whose area it's in (terran_wilds).
- **Tree types and wood types are different names (user, 2026-10-06).** A tree is named for its
  kind (walnut, cedar, oak, yew, ash, …; the tiledata tree names, and place names like "Cedar
  Forest"), and those are fine to use for trees. What a chop gives, the logs and boards, follows
  the Outlands wood ladder in `harness/data/woods.json` (ordinary, dullwood, shadowwood, copperwood,
  bronzewood, goldenwood, rosewood, verewood, valewood, avarwood). The colour comes from the
  wood chance roll (woods.json base chances × skill), not from the tree's kind. So `board:<wood>`
  prices, trip `woods` breakdowns and any "X logs/boards" use the wood names only; heartwood,
  bloodwood and frostwood (stock UO woods) don't exist on Outlands at all.
- **Update 2026-10-11 (Outland Dan, 233 trips 10-05..10-11; OPTIMIZER.md):**
  - *Logs per success drift up with the Harvest aspect*: 7.4–8.0 (10-05) → 9.22 (10-11) as the
    aspect grew (a node's quantity is fixed except for the aspect's doubling procs, k334/k155).
    Within a spot the field rate rose 3.7 %/day, 2.4 %/day of it logs per success, 1.3 %/day
    successes per field hour; attempts per field hour stayed flat. The optimizer rescales each
    trip to today's logs per success since then.
  - *Coloured wood doesn't vary by spot*: 8.4 % of 186 873 logs since 10-07 were coloured; across
    46 spots the share's χ² is 441.6 on 45 dof, ≈ the ~9-log lump factor, so no spot effect.
  - *Stand regrowth*: 972 (dry stand, later stand on the same tile) pairs from the runner's `stand`
    events, all at gaps ≥ 60 min (the window censors shorter ones): 76–86 % regrown at 60–120 min,
    84 % at 4–24 h (others' chopping keeps it below 1). The fit stays at 65 min.
  - *Deaths to time*: 12 lumber deaths in 47 exposed hours (0.26/h); from a death to the next lumber
    trip 9–156 min (median ~47; the overseer's reaction included). 3 of them fell 47–2 243 tiles from
    their spot (escape runs, the home landing after an escape recall).

## Grove tree counts: what the runner counts (2026-10-07, viz grove layer)

The user saw more trees in game than a spot's "N trees". Read with `lumber_opt.grove_view` (the
viz map's grove layer, docs/VISUALIZER.md §2.12) on the 2026-10-07 store:
- **A spot's area is a square:** centre ± radius in Chebyshev distance (29×29 tiles at the
  Witcher spots' radius 14). The runner's candidates are the trees inside it (`candidate_trees`,
  now via `lumber_opt.area_trees`), plus `add_local_trees` near wherever an escape leaves it.
- **A tree is one tile:** an impassable static whose tiledata name says tree (not potted, not a
  stump, not 0x0C9E). One per tile (`uomap.find_trees`); the big trees' other art ("snow tree"
  branches and crowns, "leaves") is passable and isn't counted.
- **witcher_23** (Ruined Snow Fortress): 69 trees inside, **59 more within 10 tiles outside**.
  All 57 passable "snow tree" statics within 24 tiles sit on a counted trunk tile.
- **witcher_123** (Savannah and Watering Hole): 27 trees inside, 22 within 10 tiles outside, 2
  tree-named statics not counted.
- [INFERENCE] So most of the trees the user sees but the count misses are just past the square's
  edge, not missing from the map reading. The layer shows both; whether a passable "snow tree"
  tile could be chopped is untested.

## Cambria Witcher library and a death on an overseer ride (live 2026-10-03, Hackworth)

- **The Cambria Witcher library works as documented** (docs/research/WORLD_LOCATIONS.md). Moongate
  arrival (1693, 3153); the 14 locked-down rune tomes stand on benches at (1705–1708, 3180), the
  serials the Witcher Travel script lists. A tome only opens within **2 tiles** (from 3: no gump,
  no message). Single-click says only "a rune tome / [mastercrafted by Wild Arms] / [locked
  down]"; the main page lists rows "N - Place name" (e.g. "286 - Midlands Ruins 1 (South)"), and
  the 14 tomes hold all 359 rune ids of the ExploreOutlands table plus an extra "83".
- **Several tomes carry recall charges anyone may use** (2026-10-03 10:20): 25-49 40/50, 50-75
  1/50, 124-149 44/50, 176-201 42/50, 228-249 38/50, 250-275 33/50, 276-301 40/50, 302-327
  18/50, 328-353 10/50; the other five 0/50. The row's gem button (100 + row) recalled with a
  charge: "Kal Ort Por", 10 mana (87 → 77), and we landed **exactly** on the table's tile (1765,
  2007). No reagents needed.
- **Death on a long `ctl act goto` (10:23:54).** From that rune the overseer (this session) sent
  two chained gotos toward the Horseshoe Bay moongate, the first to a waypoint picked blind. The
  map route crossed a harpy nest around (1880–1890, 1965–2000): harpies and mountain harpies came
  into view at 10:23:30, a witch harpy ("Spell Siphon", "malediction") at 10:23:39. Nothing
  reacted: **`ctl act goto` has no threat watch** (the lumber runner's threats.Watch, escapes and
  HP-drop stop live only in the runner), and it doesn't stop on damage. First hit 10:23:47 (−38),
  dead 7 s later at (1884, 2036), the waypoint; the riderless horse was killed after. The second
  goto then walked the ghost on to (2004, 2077). The map planner knows terrain, not spawns, and a
  250-tile wilderness route is only as safe as what it passes. Lessons: no long blind gotos
  through unknown wilderness; give goto the runner's threat checks (stop and back off on hostile
  creatures, stop on damage, stop when dead); learn monster areas from sightings.
- **Two-column rune tomes broke the default-rune parse (fixed 2026-10-03).** A full tome (more than
  13 runes) draws its rows in two columns at the same heights. `escape.parse_runetome_main` keyed
  rows by height, so it counted 13 rows and read the right column's default. A PK escape with such a
  tome would have pressed the wrong rune. Rows are now matched to their texts by position. The
  Witcher tome 276-301 layout is the fixture (`runetome_main_witcher_276`, test_escape.py).
- **Long gotos:** the map planner gives up past ~200 tiles of wilderness ("no route" from
  (1765, 2007) to Horseshoe Bay and from (1526, 3040) to the Cambria library). Two legs of ~120
  tiles each worked. The planner's search limit, not the terrain [INFERENCE].
- **Cambria:** the moongate (arrival (1693, 3153); the Moongates.xml marker says (1705, 3154)) sits
  in a player vendor mall. No banker was seen there, and the bank marker is (1750, 3003), ~180
  tiles north of the library. The Horseshoe Bay moongate item is at (2025, 2077, 11), not the
  marker's (2007, 2077).
- **Guarded goto, first live use (10:42–10:49):** Horseshoe Bay healer → moongate (190 steps), and
  Witcher rune 291 → Cambria in two legs (327 steps). No hostile creature came into view (`avoided`
  empty), so this proved the guard harmless on ordinary walks, not its avoidance.

## Rune libraries: the DTF guild house (live 2026-10-04, Outland Dan)

- **Tomes:** 36 locked-down rune tomes (gump 0x09F5976B, as at Cambria). The tiledata name is
  "runetome" for graphics 0x71AF and 0xBF63–0xBF6A (decorative tome art); `escape.book_kind`
  finds them all. Single-click: the title, "a rune tome", "[locked down]". The main page's
  top-left text is the title ("302-327", "Alliance Dockmasters 2"; `escape.runetome_title`).
- **Detail page = the rune's tile, no recall:** button 200+i opens the page with runes i and
  i+1 (pairs from an even row; 224 → runes 24 and 25). Each column shows the rune's name at
  y≈26 and its tile "(x, y)" at y≈52 (`escape.parse_runetome_detail`). Names are centred: a
  long right-hand name starts ~75 px left of its tile, which broke a first parse that wanted the
  name within 60 px (4 runes unread, fixed; fixture `runetome_detail_dtf_bad_places_24`).
- **Paging:** button **5** (art 4007, bottom right) shows the next pair; the last page has no 5.
  Button 2 (art 4011, bottom middle) is on the first and the last page [INFERENCE: back to the
  main page; not pressed]. Main page: 3 = "Manage Runes", 2 = "Rename". A full tome reads in ~13 s
  at a reading pace (open, 200, 12 × 5, close) with nothing cast and no charge spent.
- **Reading a whole library:** `ctl act read_tomes <id> [name…]` (`escape.read_runetome` per tome)
  reads every tome within 2 tiles and saves `harness/data/rune_libraries.json`. From (4152, 1429)
  all 36 DTF tomes are in reach; the act read them in 463 s (839 runes, every tile), the same
  rows as a first scripted read an hour earlier, one tome's charges down by one meanwhile.
- **Live recall (17:00):** `act recall --library dtf --rune "Prev North"` used row 5 of "Towns
  Shrines & Alliances" with a charge (45 left) and landed exactly on the row's tile (1606, 1524),
  press → arrival 2.16 s. Dan's book's default "DTF Loot Chest" brought him back to (4134, 1429),
  18 tiles west of the tomes (a 19-step goto).
- **Duplicate names:** 20 names are in two DTF tomes ("Cambria" as a town and as a dock,
  "Deceit 2" in two faction tomes…). `ctl act recall --library dtf --rune Cambria` refuses and
  lists both; `--tome TITLE` picks one. `ctl runes find|near` print the exact command.
- **The guild set's Witcher runes land off the CSV tiles** (58 of 359, up to 100 tiles;
  docs/research/WORLD_LOCATIONS.md §5): store and trust each library's own tiles.
- **Outland Dan's own runebook** (0x49865F8F, 10 charges when read, 11 runes): Prev Bank, Cambria
  MG, Terran MG, Cambria Bank, Anchor's Rest, Ossuary, Anchor's Rest MG, Khal Draco, SSC, Shelter
  Stairs, DTF Loot Chest (the default). He can cast Recall (`recall --check`: can_cast true).

## Rental room via the DTF house steward (live 2026-10-04, Outland Dan)

History since 2026-10-21: home moved to Logan Wolf's house (next section); no runner enters the room.

- **Access:** Dan has no room of his own; he is Co-Owner of Logan Wolf's (the user's other
  character). A house steward reaches rental rooms like an innkeeper does: Chase the house steward
  (0x009F57FB, ~4138, 1435, invulnerable) in the DTF guild house. His context menu: 0 "Open
  Paperdoll", **1 "Room"**.
- **Room menu without a room** (gump 0x8EAEFBDB): "You do not currently have a Rental Room" (rent
  one at an innkeeper by saying 'Room'), button 1 top-left (unlabelled), **2 "Visit Other Rooms"**.
  That opens the list "Visit Room / Access Level": **button 100 "Logan Wolf (DTF)", Co-Owner**
  (100 + row [INFERENCE: one row seen]), 2 "Return".
- **Inside:** "You enter the rental room.", facet 3 at (403, 923, 1); `ctl map` has no geometry for
  facet 3. The wooden door 0x5CDC6B4F (403, 929) opened the room menu from 6 tiles: next payment
  (10,000 gp due in 6 d 21 h), lockdowns 376/700, secure containers 3/4, **3 = End Rental Contract**
  (between that label and "(click for details)"), **7 = Expand**, 4 Exit to Town, 5 View Players,
  **6 Exit to House Steward** (used: back to (4134, 1429) in the guild house, facet 0).
  `ctl act gump` now refuses 3 and 7 on this gump when it shows those labels (`ROOM_REFUSED`).
- **Contents (single-click):** secure backpack 0x5CF2B3BC (0 items), secure "paragon chest
  (drake)" 0x4AE0DD2C (2 items, 31 st), secure storage shelf 0x6CEB65CD (graphic 0xAFC5),
  locked-down "(sealed) paragon chest (fleshweaver)" 0x68C0B8A8.
- **The secure chest for the lumber loop's boards:** "paragon chest (drake)" 0x4AE0DD2C stands at
  (404, 922, 2), 1 tile from the arrival (403, 923, 1). A drop into it worked live 2026-10-04
  (`ctl act drop` of a rope stack: the chest opened by double-click, lift `0x07` + drop `0x08` with
  auto position; then taken back out). `harness/data/homes.json` names it per character.
- **Mounts:** the DTF guild house has the purchasable house option that stashes mounts while
  you're in the house; the rental room doesn't (user, 2026-10-04). Leaving the guild house into
  the room: "Your mount returns."; back into the guild house: "Your mount finds a quiet place to
  rest safely." So Dan is on foot inside the guild house, mounted elsewhere.
- **One act each way:** `ctl act room enter [OWNER]` / `ctl act room leave [steward|town]` do the
  whole flow (buttons found by their labels, not fixed ids). Live 17:31: enter 7.0 s (a 4-step walk
  to Chase included), leave 7.0 s, `enter logan` the same; `enter nobody` refused with the list
  ("Logan Wolf (DTF)") and closed the menu. Fixtures: `harness/testdata/room_gumps.json`.
- **Say "room", don't right-click (user, 2026-10-06):** the harness's injected right-click on the
  steward got his context menu ("Open Paperdoll / Room") drawn by the client, and since the client
  never made that click it never closed it: the menu stayed up on screen. `room.enter` now walks
  within 2 tiles and says "room" (`ROOM_WORD`, plain 0xAD: not a speech.mul keyword) instead; the
  room menu opens the same way (the room menu's own text already says to rent "by saying 'Room'").
  Live 2026-10-06 13:25 at Chase from 2 tiles: `act room enter logan` ok in 6.5 s (4-step walk,
  "room", Visit Other Rooms, Logan Wolf's row, "You enter the rental room."), no context menu.
  The lumber sim's steward no longer answers a right-click, and the main scenario checks he is
  never right-clicked. Same risk remains for other injected right-clicks (`mount.py` pet menu,
  `ctl act menu`, vendor `buy`) [not changed].
- Memory: #4082 (procedure, `char:outland_dan`; #4081 superseded); superseded on 2026-10-21 by #17142 (now #17146).

## Logan Wolf's house (live 2026-10-21)

User 2026-10-21: "We've moved from an inn room into our own house." Read through ctl ~21:10, both
characters logged in, standing in the house, facet 0. Decision: docs/PLAN.md "Home moves to Logan
Wolf's house"; data: `harness/data/homes.json`, `harness/home.py`.
- **Landing:** "Logan's House" lands at (3575, 3251, 6), where Logan Wolf stood. At home =
  within `home.HOME_R` (6) tiles of it on facet 0. Interior ~x 3570-3578, y 3249-3254; door
  0x4C7C2469 at (3579, 3252), house sign at (3580, 3251).
- **Items:** the room's serials, now in the house (distance from the landing): stockpile
  0x62645C82 (3574,3249,6) 2; storage shelf 0x6CEB65CD (3573,3249,6) 2; chest "paragon chest (drake)"
  0x4AE0DD2C (3575,3250,23) 1; bank deposit safe 0x431E86F5 (3575,3249) 2; recycler 0x6264BB54
  (3573,3250) 2; tomes 0x62659A52 / 0x431E6BAA / 0x62658F0D / 0x62657BB3 at 1; magic item vault
  0x6264CB96. **New forge 0x56372DCC** (3574,3254,6), 3 tiles off; the room's forge 0x51CF24B3 is gone.
- **The four drop-off tomes moved into the "wooden shelf" 0x5656B661** (3573,3251,6), 2 tiles off (user,
  later on 2026-10-21; the chest then read z 19). `act dropoff` first refused them ("isn't in view within
  2 tiles": it measured items on the ground only). `dropoff.run` now takes an item's reach from the
  outermost container on the ground (`dropoff.ground_item`) and opens a container the server hasn't
  opened for us first. Live ~22:00: all 8 steps answered, the aspect tome took 1 item ("1 aspect items
  added to tome."), the stockpile 1; Logan's loadout at the house shelf: "No resupply:" Wizard's
  Satchel, Potion Satchel and the five platemail pieces.
- **Runes:** Outland Dan's runebook 0x49865F8F (9 charges): Prev Bank, Cambria MG, Terran MG,
  Cambria Bank, Anchor's Rest, Ossuary, Anchor's Rest MG, SSC, Shelter Stairs, **"DTF Loot Chest"
  (4134,1429) = the default**, **"Logan's House" (3575,3251)**; Khal Draco was removed. So Dan's
  default does not go home: the way home is "Logan's House" by name. Logan Wolf's rune tome
  0x444AB1F3 (47 charges): **default = "Logan's House"** (row 25), "DTF Loot Chest" row 23 at
  (4135,1431), "Maus Near 2" row 24.
- **The DTF guild house** is now only the way to the rune library (stand 4152,1429, reached by
  "DTF Loot Chest") and its stocked shelf 0x40B84C55 (4133,1427,11), whose recall scrolls top up
  Dan's runebook. It rests a ridden mount when we recall in and gives it back on the recall out.
- **Mounts (user, 2026-10-21):** the house "auto stashes mounts" like the guild house: a ridden mount
  rests when we recall in and comes back on the recall out. So at home Dan is on foot and his
  remembered horse 0x0154FE11 isn't in view (as seen 2026-10-21); the runner records `resting` and
  checks it rides after the recall out (`mount_after_recall`).
- **How the runners use it** (offline-proven 2026-10-21, not yet run live; LUMBER_LOOP §13 "Trip"):
  `prepare_recall` takes a book whose default lands at home, else the book holding "Logan's House"
  by name (Dan's runebook); `come_home` (phase `to_room`) recalls and walks to the landing tile when
  beyond 2 tiles; library trips `to_library_house` (travel leg `to_library`) by "DTF Loot Chest",
  then `guild_resupply` when the house shelf left something out or the book is short; resupply
  `where` is `home`/`guild`; mining smelts at the house forge (`home_forge`, `smelt` event `at: home`).
- Memory: #17146 (procedure, pinned: the house as home and how to get there; supersedes #17142).
- **Repair bench 0x5B3BF0CF** "a repair bench" (graphic 0x72AA) at (3576,3251,6), 1 tile east of the landing,
  locked down by the user 2026-10-27 (capture 20261027_220057; their uses from repair kits: 20 tailoring kits
  "You add repair kits into the repair bench (200/5000 uses remaining).", capture 20261027_230843 23:15:15). A
  double-click (0x06) repairs every worn item and everything in the pack (KB "repair benches") and answers with
  one system line: "Repaired N item(s)." 2.1 s after it, the hammer (0x54 sound 42) once a second meanwhile (the
  DTF guild house's public bench 0x40C3A1BD: "Repaired 1 item." 22:03:16, "Repaired 7 items." 23:03:00, its
  label "[3567/5000 charges remaining]"); "That repair bench is out of charges (use any repair kit on the bench to
  add additional charges)." 0.05 s after (23:08:12, before the kits); "You do not have any items equipped or in
  your backpack in need of repairs." 0.04 s after (23:15:24). No gump, no cursor. The hunt runner double-clicks it
  before each recall in (`repair.py`, docs/HUNT_LOOP.md "Recall in, recall out").

## Storage shelves (live 2026-10-04/05, Outland Dan)

Wiki: [Storage Shelf](https://wiki.uooutlands.com/Storage_Shelf). Fixtures:
`harness/testdata/shelf_gumps.json`. Act: `ctl act resupply` (`harness/shelf.py`).
- **What it is:** players stock a shelf with items; each character has **one Loadout, saved per
  character and shared by every shelf** (the user set Dan's up: trapped pouches, reagents, the
  armor and other essentials). **Resupply** equips missing loadout gear and tops pack quantities
  up to the loadout amounts. The shelf only gives what it holds: per missing item the server says
  **"No resupply: <item>"** (System speech; live "No resupply: Hatchet"), and when it can give
  nothing it says **"Unable to resupply: no items available."** (live at both shelves; also when
  the only shortfall was an item it lacks).
- **Only some gear goes in (user, 2026-10-05):** for some item types the shelf takes only
  player-crafted GM (exceptional) items with full uses: not a vendor-bought hatchet, not a hatchet
  with uses spent, but a freshly crafted GM coloured hatchet. That is why Dan's loadout's 4
  hatchets came back "No resupply: Hatchet" at the DTF shelf. Since the evening of 2026-10-05 the
  user stocks GM coloured hatchets and the loadout asks for the best one (see "Resource Stockpile
  and GM coloured hatchets").
- **Where:** the DTF guild house has two "spring storage shelf" items stacked on (4133,1427): z 6
  `0x40050A3B` answers "That is secure." (cliloc 501647: not for Dan), z 11 `0x40B84C55` opens and
  is stocked (2 tiles from the home landing 4134,1429: no walk). Logan Wolf's rental room has
  "storage shelf" `0x6CEB65CD` at (402,921,2), 2 tiles from the arrival; nearly empty (3 of one
  reagent), to be stocked later.
- **Gump 0xC0B1026D** (Razor scripts wait for 3232825965). Buttons, by the labels on the live
  layout: **7 Resupply** (label right of it), 1000 Restock, 1001 Edit Loadout, 16 Clear (the
  label left of it), 3 the category page arrow ("Page 1/6"), 9 the loadout page arrow ("Page
  1/2"), 24 Rename, 22/23 Retrieve one/many, 30/40 the material selector, 50–57 the categories,
  130+/230+/330+ the item rows of the three columns (a click opens "Retrieve Items" 0xBEC6217A).
  The left pane shows the stock per item; the right pane "Loadout (37 Items)" the loadout, 10 per
  page with amounts. Dan's (2026-10-05): 7 reagents ×10 and black pearl ×10, the six shadowhide
  studded pieces ×1, a bag ×1, heal/cure/refresh potions ×5, trapped pouches ×3, hatchets ×4. The
  older #54 button map (Test Shard, 2026-09-30: "3 = Restock, 7 = Edit Loadout") doesn't match
  this layout. `ctl act gump` refuses 1000 and 16 here; `act resupply` presses only 7 and 0.
- **Live resupply:** with 2 trapped pouches against the loadout's 3, Resupply at the DTF shelf
  added one (hue 38) into the bag already holding the others, with "No resupply: Hatchet". After a
  pouch went into the room chest: the room shelf said "Unable to resupply: no items available.",
  the DTF shelf gave it back. `act resupply` took 4.9 s at the DTF shelf (no walk); the shelf's
  answer is its lines, then the shelf's gump again (closed with 0).
- **The lumber runner resupplies itself (since 2026-10-05):** before every trip at home, the room's
  shelf, then (it lacked something) out of the room and the landing's shelf (LUMBER_LOOP §13 "Trip"
  1a). Live 10:07 (task `lumber-20261005-100749-ece4`): the room's shelf "nothing to give" (3.2 s),
  out of the room, the DTF shelf "nothing to give" (Dan's pack already held the loadout but the
  hatchets), then the trip as before: 101 boards into the chest, exit 0 in the room.
- **Restock with the backpack, then Resupply (user's routine, 2026-10-05: "I generally just
  'restock' the shelf and target my own backpack letting it suck up everything. Then resupply."):**
  Restock (button 1000) → "Which container do you wish to restock this container from? (you may
  target yourself or a nearby friendly pack animal)" and a target cursor; the backpack targeted →
  "16 items were added." (live at the room shelf 0x6CEB65CD: a spent pouch, the reagents, the
  potions and the three trapped pouches left the pack; the spellbook, spyglass, spare clothes and
  runebook stayed). Resupply right after gave the loadout back and said **"Partial resupply:
  Greater Heal Potion"** (3 of 5): a third kind of line besides "No resupply:" and "Unable to
  resupply:". `ctl act resupply --restock` does both (`shelf.resupply(restock=True)`); the runner
  does it at the room shelf after storing the boards (spent pouches no longer go into the chest).
  `act gump` still refuses 1000 by hand.
- **Dropping a container onto a shelf adds its contents, not the container:** a spent (empty)
  pouch dropped on the room shelf bounced back with "That container does not contain any items
  that may be added."; with another spent pouch inside, the inner one went in ("1 items were
  added.") and the outer came back. The shelf gump opens after each drop.
- **A lift right after the hatchet's double-click is refused:** in that trip one stash (the chop's
  logs into the trapped pouch during the next chop) came 0.47 s after the hatchet's double-click
  and got "You must wait to perform another action." (cliloc 500119); the logs stayed loose until the
  next chop's stash. The stash now waits STASH_AFTER_S (0.8 s) after the chop's target.
- **Resupply recharges the runebook (live, events DB 2026-10-07/08):** Dan's home runebook
  `0x49865F8F` read Charges 10, 9, 8, 7 before each use on 2026-10-08 11:27–11:45 (the recall rows'
  `charges` are the count before the use), 7 again at 11:47:35 after the death; the DTF shelf's
  "Resupply complete." at 11:48:29 came 10 s before a read of 10. Every jump in the charge reads since
  2026-10-07 (9→10, 7→10, 8→10, 1→10) follows a resupply. So the overseer's `act recall --check` after
  a resupply is right, not stale: the shelf tops the book up to 10 from its recall scrolls (the
  Discord KB's "storage shelf runebook recharging"; ROADMAP row 8). A low count only matters between
  resupplies (the runner resupplies before every trip). **The rule (user, 2026-10-12):** a shelf tops a
  runebook up when it holds recall scrolls *and* the character's shelf option for it is enabled; it is
  enabled for all of the user's characters, so only the shelf's scroll stock decides.
- **The book stopped being recharged (live 2026-10-11, Seer-21):** the runebook read 5 charges at
  the start of run 1 (12:42), 1 at run 2, and 0 at runs 3–5. Every room-shelf resupply answered
  "resupplied (room) from 0x6CEB65CD: nothing". Since a full pack makes the runner skip the landing's DTF
  shelf, nothing topped the book up. [INFERENCE] The room shelf holds no recall scrolls. The runner then
  recalls by spell (`escape`: spell on the book, 2.1–5.6 s, 11 mana + black pearl, blood moss,
  mandrake), and every recall landed; mana never fell below 80. The user: recall scrolls are stocked on
  the guild shelf (0x40B84C55). Since 2026-10-11 the runner also goes to the landing's shelf when the
  book holds fewer than 10 charges (LUMBER_LOOP §13 "Trip" 1a).

## Resource Stockpile and GM coloured hatchets (live 2026-10-05, Outland Dan)

Wiki: [Resource Stockpile](https://wiki.uooutlands.com/Resource_Stockpile),
[Lumberjacking](https://wiki.uooutlands.com/Lumberjacking) ("Colored Hatchets"). Code:
`harness/stockpile.py`, `ctl act stockpile`, `LumberLoop.store` / `to_stockpile`.

- **The user bought for Logan Wolf's room (2026-10-05):** "a resource stockpile" 0x62645C82
  (graphic 0x59FA, locked down) at (403,921,2), 2 tiles north of the room's arrival (403,923); also
  a magic item recycler 0x6264BB54, a magic item vault 0x6264CB96 (both secure, empty), an aspect
  item tome 0x62657BB3, a skill mastery tome 0x62658F0D, a treasure map tome 0x62659A52 (all
  locked down, empty). Names from single clicks; each also says "[blessed for 44m]".
  **All boards now go into the stockpile** (user: "It is where we will now drop off all our
  boards"; homes.json `stockpile`); spent trapped pouches go into the room's storage shelf by its
  Restock (see "Storage shelves").
- **Its menu:** double-click → gump 0x6ECE2ABE, lines `Guide`, `Resource Stockpile`, one count per
  row, `Settings`. The ingots/boards page lists ingots (tile 7154) and boards (tile 7127) per
  material hue: 0 (regular) and 2419, 2406, 2413, 2418, 2213, 2425, 2207, 2219, 1763 (the woods'
  hues); row buttons 100–109 ingots, 110–119 boards (retrieval). Button **2** = Add Items (bottom
  left, no text label), 3–7 category tabs, 12 Settings.
- **Deposit, as tested (4 stacks, 18 boards):** button 2 → "Target an item or container of items
  you wish to add to this Resource Stockpile. Target yourself to add all valid items in your
  backpack." and a target cursor (type 0). Targeting a board stack in the pack → "You add 1 item(s)
  to the Resource Stockpile.", the stack is deleted, and the menu comes back with the new count
  under a new serial (no cursor). So it's one press and one target per stack, then close the menu
  with 0. `stockpile.deposit` does exactly that; the overseer's `ctl act stockpile` runs the same
  flow on every board stack in the pack. Never target yourself: per the wiki that adds every valid
  item in the pack, reagents and tools included, depending on the player's Settings.
- **Logs are refused (live 2026-10-27 08:3x, SeerOutlandDan4):** with no hatchet to convert, `act stockpile
  0x440D4F8D` (814 logs, loose in the backpack) answered "You do not find any items that could be added to a
  Resource Stockpile." and left the stack; targeting a pouch of log stacks got the same. Only boards (and
  ingots) go in, so logs stay in the pack until a hatchet converts them (`act convert --store`).
- **A whole pouch at once (user's suggestion, live test 2026-10-05 22:05–22:17):**
  - **Retrieval works:** the stockpile's Retrieval menu (row button 110 regular boards, 111
    dullwood) is gump 0xCB355E84, lines "Retrieve Items", "Available", the count, "Retrieve", "",
    "Create Commodity". Text entry 1 is the amount and button 2 retrieves it ("Retrieved 10
    items." into the pack); entry 3 with button 4 is for commodity deeds.
  - **One stack in a pouch:** 10 boards dropped into a live trapped pouch, Add Items, then
    targeting the pouch: "You add 1 item(s) to the Resource Stockpile.", and the pouch stayed armed
    (hue 38).
  - **Two stacks in a pouch:** 5 regular + 5 dullwood in it: "You add 2 item(s) ...", one
    target.
  - **The new code, live:** `ctl act stockpile` on 4 + 4 boards in that pouch: "added 8 boards (1
    target(s))".
  - **What changed:** `stockpile.targets` aims at the pouch holding boards, never at the loot bag
    (it holds reagents) or ourselves. `store` sets off a live pouch only on the chest path.
  - A decoy captcha gump (0x4F52A7EB, no buttons) stayed open in the client from the last trip.
- **Board counts:** after the tests 18 in the stockpile (10 + 5 regular, 3 dullwood). Then (user,
  2026-10-05) every board stack in the chest went in with `ctl act stockpile <serials>`: a stack in
  the open chest can be targeted directly, no lift. 36 copperwood first, then 147 shadowwood, 36
  bronzewood, 8311 regular, 145 dullwood. The menu then read 8326 regular, 148 dullwood, 147
  shadowwood, 36 + 36 (8693 in all; gump text lines are deduplicated, so the two 36s show once).
  The chest holds no boards now.
- **A partial lift moves the lifted serial:** `ctl act drop 0x5E9DB872 <pack> --amount 10` (of
  8326) put 0x5E9DB872 into the pack with 10, and the 8316 left in the chest became 0x62759AC6
  (RunUO's split). `ctl act drop` had expected the reverse and reported `moved: false`; fixed.
- **GM coloured hatchets (user: the shelf loadout now hands out "the best quality colored hatchet
  available"):** Dan was wearing 0x6270D238, hue **2418** (bronze, which confirms that hatchets.json
  hue on a hatchet). Click labels: "exceptional bronze hatchet", "(1500 uses remaining)",
  "[mastercrafted by Sage Godfrey]". 1500 = 500 base + 250 exceptional + 250 mastercrafted + 500
  bronze, so the wiki's tool bonus is 0.08 + 0.04 + 0.04 = 0.16. The world model keeps only the
  latest label per serial (`labels`), so hatchet_kind sees the material (hue) but not the quality.
  `LumberLoop.hatchet` now picks the best material in hand or in the pack (worn first among equals).
  `ctl act resupply` at the room shelf right after: "Unable to resupply: no items available."

## First live room-storage trip (2026-10-04, Outland Dan)

Task `lumber-20261004-220633-af62` (`logs/tasks/`, local), session `logs/session_20261004_220218.jsonl`.
`ctl run lumber --spot horseshoe_bay --trips 1 --logs-per-trip 200 --regrow-min 65 --timeout 2400
--hatchet iron` (Horseshoe Bay chosen over the plan's explore pick witcher_284 for its record: 7
trips, P(death) 0.03), started at the guild-house landing after a first attempt (below). The room
exit (door → "Exit to House Steward" → (4134,1429)) was proven by that first attempt at 22:03:12.
- **Out:** 15 steps to the DTF tomes, tome "Witcher 48-68" rune `67` by a charge (2.17 s), landing
  (2167,2202), 59 tiles from the grove; walk_out 21.7 s to the first chop.
- **Field:** 527 s chopping at 11 stands (Smart Harvest), 240 logs with the earlier attempt's 11 and
  "Harvest double yield/loot triggered." procs; Lumberjacking 50.1 → 51.7.
- **Home:** the runebook's default 'DTF Loot Chest' (2.19 s) → (4134,1429), 4 steps to Chase,
  `room.enter` into Logan Wolf's room (to_room 9.5 s); the trapped pouch set off by us (1 hit,
  acknowledged), 240 logs → 240 boards (convert 14.7 s), boards and the spent pouch dropped into the
  paragon chest 0x4AE0DD2C (store 4.1 s). Exit 0 in the room.
- **First attempt's false alarm (fixed):** the Harvest aspect's proc puts an S2C 0xC0 fixed effect
  on us, graphic 0x37BE hue 0x8A0, with "Harvest double yield/loot triggered." and two sounds
  (`c0 03 0014683f 0014683f 000037be …`). `threats.spell_on_us` counted it as a spell landing on us
  with nothing in view and the runner recalled home ("taking damage, no creature in view") after
  11 logs. 0x37BE is now a BENIGN_EFFECT. That aborted trip row (horseshoe_bay, sent home) is a
  false hazard sample in the planner's data.
- **Script-paced chopping, live 22:34–22:39 (task `lumber-20261004-223449-d94a`, same session):**
  hatchet ~0.2 s after each reply, cursor answered ~0.1 s after it comes, the stash during the
  next chop (humanize `SCRIPT_MEDIAN`, docs/PLAN.md). Target to target 4.92 s median over 39
  cycles (before: 9.36 s over 62); target to the server's reply 4.17 s both times, so the cycle is
  now the server's chop time plus ~0.75 s. No "You must wait to perform another action" (500119),
  no unrecognised outcome; 114 logs (the grove ran dry, Horseshoe Bay chopped 30 min before), 114
  boards into the chest.
- **A new run's false thief alarm (fixed):** the run started at 22:33:10 in the room stopped at
  once on "trapped pouch went off without our double-click". The proxy's event ring still held the
  previous run's own set-off in the room (22:17:04 convert: the explosion and sound at our tile), and
  a new runner reads the ring from its start, with no click of its own to pair them with.
  `check_pouches` now ignores events from before the run started (`_pop_since`).

## First overseer shift on the room loop: a death at Wintertop (2026-10-05, Outland Dan)

An overseer subagent ran the lumber shift (docs/OVERSEER.md §5). Run 1 (`witcher_254`, explore):
101 logs, then a gargoyle's spell and a harpy (100 → 53 hits): the runner recalled home in 2.2 s and
stopped, as designed. Run 2 (task `lumber-20261005-104549-6355`, `witcher_196`, explore, DTF rune
"Wintertop" (3509,526), 19 tiles from the grove): **Dan died**, his bonded horse too.
- **The chain (log 10:46:04–10:46:16):** a gazer 5 tiles from the landing → escape to (3499,529) →
  `harvest_trip` ran again from the top (it is `guarded`: re-run after an escape) → `go_out` saw us
  farther from the grove than the landing and **set out for the home rune library from the field**
  ("to the rune library: no route", an Abort) → while it stopped, a rime spirit champion (grey,
  shield bash -42) closed in → the recall home was disturbed 0.43 s into the cast → the second try:
  **"This book needs time to recharge."** (cliloc 502406, the runebook double-clicked ~1 s after its
  use; escape.py knew only 502403, so the gump wait timed out as "didn't open" and the escape gave
  up) → guard flight: no route → dead 4 s later.
- **Fixed:** `go_out` does nothing once out (`afield`, now also set the moment the recall lands):
  an escape at the landing harvests on (offline: `test_loop_lumber.py` scenario `landing_escape`,
  which fails without the fix). And a recharging book (502406 → "recharging") no longer ends the
  escape: for its default rune the Recall **spell is cast and answered with the book itself**,
  which needs no gump. **Live 10:59:** cast Recall, target runebook 0x49865F8F from (4150,1430) →
  landed on 'DTF Loot Chest' (4134,1429), 9 mana ("Select Marked item." as the cursor's prompt).
  Without reagents or mana it waits RECHARGE_WAIT_S (1 s) and looks again (`test_escape.py`
  `test_recharging_book`).
- **Left as is:** the runner's `death` juncture isn't posted when it has already exited; the overseer
  saw the ghost by `status`. The rime spirit champion took the player path (recall, guard flight):
  it recalls either way.
- **After:** the overseer walked the ghost to the Anchor's Rest West Caravan healer (Shawn, gump
  0xB04C9A31 Accept 1), recalled home and went into the room. The corpse at (3500,529) kept the
  hatchet, armor, ~106 logs and supplies (no corpse runs, policy #2). `ctl act resupply` at the DTF
  shelf then re-dressed him: all six studded pieces (worn; no Harvest aspect until activated again),
  reagents, potions, pouches and a bag; no hatchet (the shelf has none it will give).
- **The spell-on-book fallback's first live use crashed the log line (run lumber-20261005-123221-09e7,
  12:38:06):** a giant rat kept coming, the escape recall was disturbed 0.117 s into the cast, the
  next try went by the Recall spell on the book and **landed home**, but `escape()`'s per-try log
  line computed `rune + 1` with no rune index (the default rune) → TypeError, trip aborted, exit 1
  (juncture #249). Fixed: it logs "rune the default"; `test_escape.py` now runs `escape()` over
  the recharging book.
- **The spell on the book's reagents read as theft (run lumber-20261005-124519-d565, 12:46:21):** an
  ettin's escape recall went charge (disturbed) → spell on the book (landed, 2.48 s); the ledger then
  flagged 1 black pearl, blood moss and mandrake root as "left the pack unexplained" (juncture 252)
  because `expect_casts` counted only method `spell`. Now `escape.SPELL_METHODS` (spell,
  spell_on_book) is what spends reagents, for the ledger, the trip row's `recall_casts` and the jobs
  report (`test_loop_lumber.py` unit `unit_recall_reagents`, failing before). The false `theft` job
  event (907) and juncture 252 were deleted from the memory store so the planner doesn't count a
  theft at witcher_66.
- **Run 6, nearly a death at witcher_58 (lumber-20261005-133032-d0fe, 13:30–13:31):** landing DTF
  'Hollow Hills' (1839,2074), 33 tiles from the grove. The first tree was unreachable; the way to the
  next (28 tiles) was "cut by mobiles we couldn't shove", and 2 s later the planner went round them:
  **a 255-step route** south through the wilds (riding). A fen daemon at 8 tiles → escape on foot
  (43 steps); a brackish water (ranged) hit on the way: -0, -0 (spells), -20, -16 → "walking on" each
  time → recall at 64/100, -44 during the 2.2 s cast, home at 7/100. Fixed: a route to a tree longer
  than max(30, 3 × its distance) is refused before a step (next stand), and a second hit that costs
  hits on the same walk-away sends us home (LUMBER_LOOP.md §13 "Running from a creature").
- **The first overseer shift on the fixed loop (12:14–13:35, Seer-2):** 6 runs, 3 stored: 653 +
  1142 + 981 = **2776 boards**; Lumberjacking 53.9 → 66.2. The mount rode out every trip after the
  fixes. Hatchet 0x60CB5AEB down to 163 uses.
- **The trip row's hatchet `uses` is the table's total, not uses left (investigated 2026-10-05):**
  `lumber_opt.hatchet_kind` sets `uses` = hatchets.json `base_uses` (500) + material/quality
  extras, and `row_hatchet` copies it into every row. Outlands has tooltips off (no 0xD6/0xDC in the
  captures): the server gives the count only as a click label, "(N uses remaining)" (0xAE type 6,
  hue 946) answering a 0x09. The runner never clicks the hatchet, so `hatchet_uses_seen`
  (memory.uses_seen, passive) was null on every row; the overseer's clicks at 13:33:37 got 163 and
  500. One use per successful chop holds exactly: 337 chop lines 12:16–13:31, 500 − 337 = 163. The
  rows' `successes` summed to 335: two chop replies arrived during an escape (12:38:05) or a
  keep-away (12:56:33) and weren't counted [INFERENCE: no attempt() waiting for them].
- **A rune library landing that puts you elsewhere (13:52, run lumber-20261005-135213-6edd):** DTF
  tome 0x442596F7 rune "Jonny's House" (1817,1865) landed Dan at (1809,1871), beyond LANDING_SLACK;
  the runner recalled home and aborted. [INFERENCE: a house that moves recalls to its door.] Now
  such a rune is remembered as a bad landing and passed over (LUMBER_LOOP.md §13 "Recall travel");
  witcher_55's landing became rune '52' (1729,1881), 63 tiles from the grove.
- **Death at witcher_149 (run lumber-20261005-140752-f1d8, 14:08, overseer Seer3):** landing DTF rune
  '154' (3642,315), 77 tiles from the grove. 4 s into the walk a hoarfrost at 7 tiles (3636,304) →
  ESCAPE to (3647,301), 4 tiles away, but by a **36-step route** east through a door at (3660,302)
  (two blocked moves at the door, 14:08:24–26) → a second hoarfrost (0x00557536, 14 tiles off at
  the start) hit -38 at 1 tile, 62/100 → "walking on" → dead at 14:08:28. No recall was tried. The
  static map plans that escape in 3 steps even with the danger costs (checked offline), so the
  long route came from what the live walk map adds [INFERENCE: a house multi's walls]. Fixed: a
  map-planned escape route longer than max(12, 2 × the goal's distance) is refused, and with no
  short one the runner recalls home at once; a hit from a creature next to us while walking away
  sends us home (LUMBER_LOOP.md §13). Both hatchets were in the pack and are on the corpse.
  **The door was another player's house** (user): you can't walk into one uninvited, so the escape
  stopped there. The user's direction (2026-10-05): run much farther from mobs before recalling or
  expecting them to stop (a few steps don't break aggro), and never move into aggro range of mobs we
  can see while lumbering (no fighting them for now). Built: foreign house doors locked to the
  planner, aggro zones round every visible creature, escapes of 20 tiles, a run out of reach before a
  creature recall (LUMBER_LOOP.md §13 "Keep away from creatures in view").
- **First run with the keep-away rules (lumber-20261005-160037-560c, witcher_23, Seer4):** an air dragon
  (0x00569849, body 0x0C, notoriety 3, idle) at (1685,660) ruled out ~30 trees within 13 tiles; the
  walk to the next clear tree (1704,665, ~30 tiles) took 2 min 17 s with 123 "danger ahead changed"
  replans as the dragon wandered, swinging between a 59- and a 37-step route. Birds (crow, swallow,
  starling: body 0x06) are passive and made no zones. The user saw Dan keep heading for that one tree
  past others. Fixed: zones replan only when they touch the rest of the route (`test_mover.py`
  `test_danger_replan_on_route_only`), and the walk re-thinks its tree every 5 s with the trees
  around us as candidates (LUMBER_LOOP.md §13). Same run, 16:10: the dragon came back into view by
  the next target tree (1693,663); the walk replanned round its zone and went on to that tree, and
  the dragon found Dan there (escape, run out of reach 1 s, recall, 0 hits lost). Now the walk ends
  the moment a zone covers its tree (`test_loop_lumber.py` `zone_on_way`).
- **Ping-pong between trees (lumber-20261005-161957-b86c, witcher_267, 16:22:32–16:25:08):** two norse
  bear riders and a norse hammerman patrolling in and out of view: next_stand picked trees that were
  clear at that read, the walk dropped them ("a creature's zone covers it now") a second later, 84
  replans, Dan going 2621↔2629 for 2.5 min with no chop, until the trees around (2658,457) joined.
  Now a creature that leaves the view keeps its zone at its last tile for 60 s and a dropped tree
  waits 120 s (unless nothing else is left).
- **Prevalia Gate (lumber-20261005-163659-92ea, witcher_105, 16:37):** a ratman gutterrunner 6 tiles from
  the landing; the walk to a clear tree passed 2 tiles from it (the zone we stood in was left out of
  routing) → escape; the run out of reach stopped at its first goal with the ratmen 9 tiles behind and
  recalled, -34 hits. Now a zone we stand in shrinks to just inside our distance (no closer), and the
  run out of reach keeps going with fresh goals until 14 tiles or its 60 steps. The ping-pong runs
  also flooded the live viz (7–8 k S2C 0xF3 in 2.5 min as Dan crossed the view edge back and forth);
  it showed Dan bouncing for minutes after he had recalled home (investigated separately).
- **Convert left logs (lumber-20261005-164216-2557, witcher_40, 17:18):** the trip itself went well (tree
  drops and switches, no replan storm, 0 escapes). At home the pouch held 4 log stacks (other woods and
  what aborted runs 1–3 left); one hatchet use brought no cursor and the 4-try convert loop aborted
  ("logs left after 4 conversions") with a 9-log stack left and nothing stored; the overseer finished
  by hand (2747 boards in the chest). Now convert tries once per stack plus 3 retries
  (`test_loop_lumber.py` `convert_stacks`, failing before).
- **A blocked rune (lumber-20261005-172734-983f, witcher_165, 17:28):** DTF tome rune "Kaern's Manor"
  (3947,145): after each Kal Ort Por the server said **"That location is blocked."** (cliloc 501942,
  System), which escape.py didn't know, so each try waited 5 s for an arrival ("no arrival … 4.9 s")
  and the run aborted at the library. Now 501942 is a `blocked` failure (no retry), and a recall out
  that fails blocked / unmarked / restricted marks the rune as a bad landing (`why`), so plans pass it
  over (witcher_165 now lands at 'Ice Prison', 79 tiles out). Same shift: witcher_40 sat out 30 min as
  "player threat or death here" because the convert abort counted as an aborted trip with a sighting
  (lumber_opt eligibility) [not changed].
- **Run 8 ping-pong (lumber-20261005-175337-5074, witcher_48, 17:55:11–17:55:56):** 48 "covers it now"
  drops alternating trees 1775,1065 and 1795,1035: next_stand fell back to dropped trees when nothing
  else was free, so the 120 s cooldown didn't hold. Now it holds whatever is left (the harvest ends
  instead). The overseer also saw llamas, forest ostards and a bison get zones: farm/grazing animal
  names are now passive (`threats.Params.passive_names`).
- **Cliff ping-pong (lumber-20261005-181911-a999, witcher_98, 18:25:20–18:27:43, Seer5):** tree_rethink
  said "tree 1493,2010 is nearer and clear" 13 times by straight line; the tree (z 25, facet 0) stands on
  a slope `pathfind.plan` reaches from none of (1488,2010), (1503,2011), (1481,2003) (offline check;
  an unreachable plan costs ~0.9 s exhausting 30000 nodes), so next_stand skipped it and picked far trees
  again. Now rethink plans the nearer trees' routes, switches only if 8 steps shorter, and hands that
  tree to next_stand; trees with no route leave the trip's candidates.
- **False thief alarm at the guild-house landing (lumber-20261006-111829-94a3, witcher_40, Seer7,
  11:38:56):** a pk_escape recall from a grey ("khiizriel", 5 tiles) landed home with 1253 logs.
  Before the room, an explosion *sound* near us (the busy landing: someone else's pouch) read as
  "trapped pouch went off without our double-click (sound)" and stopped the run at the landing. Our
  pouch 0x6508F1EB was still armed: `act convert --store` had to set it off, and stored all 1253.
  Now only one of our own live pouches going off (hue 38 → 0) is a thief (`pouch.thief_pops`); a
  sound or explosion alone is not. **What it really was (user, confirmed in the session log):** the
  PK's Explosion spell, which detonates after a delay. At 11:38:55.39, 0.4 s after the landing
  home, an effect (0xC0 type 3) on Dan and the 0x307 sound on his tile, then hits 100 → 69 (0xA1 at
  11:38:59.38). So `home_after_recall` now runs in mode 'salvage' (only players and death count):
  damage from a spell cast before the recall no longer stops the room phase (`red_aim` sends that
  blast; failing before).
- **A late recall arrival left Dan at a hot landing (lumber-20261006-101348-70b3, witcher_23, Seer7,
  ~10:15, a death):** the recall out from the DTF tome ("Ruined Snow Fortress") landed after 5.18 s
  (the power words came, the cast ran 4.92 s). `escape._arrival` gave up at `ARRIVE_WAIT_S` (5 s)
  with "no arrival", and the retry needed the tome, now out of reach ("0x400D6218 is not a runebook or
  rune tome"). The run aborted with Dan already at the landing (1705,613), on foot (the mount
  rests until a successful recall out), at 79 hits and falling. The overseer's `act recall` then
  failed ("book's gump didn't open") and he died. Now, once our power words are heard, the arrival
  wait runs at least `LATE_ARRIVAL_S` (10 s) past them (`test_escape.py`
  `test_escape_late_arrival`, failing before). Why it was late (Seer7, from the session log): Kal Ort
  Por at 10:14:17.7, then "The world is saving" / "World save complete" (10:14:18–21), which
  delayed the landing to 10:14:22.8. The runner had exited at 10:14:23, so Dan stood dismounted for
  ~15 s while a snow elemental (0x005C4FC1) swung from 10:14:27.8: −21, −23, −27, −30, dead at
  10:14:37.9. The overseer's book clicks at 10:14:44 came from a ghost (cliloc 1019048). Lost on the
  corpse (1705,613): the six-piece exceptional shadowhide harvest-aspect studded set, the GM bronze
  hatchet 0x6270D238, the trapped pouches, reagents and potions. The room shelf then had none of
  those ("No resupply:" for each); it gave a plain downgraded hatchet. Also: a plain abort away
  from home now recalls home and stores first (`home_on_abort`), so a run never ends standing in
  the field.
- **Out-of-reach run stood still planning (lumber-20261006-095344-7066, witcher_23, Seer7, 09:54):** a
  snow elemental followed an escape, so monster_stop ran `gain_distance`; its walk to an escape
  goal had no route bound, and the search took 8 s (09:54:09 → 09:54:17) while Dan stood and fell
  100 → 28 (0xA1 at 09:54:12/15), then offered a 161-step route. The trip row said hits_lost 0
  because gap mode skipped creature_hit. Now the out-of-reach walks are bounded like escapes
  (max(`ESCAPE_ROUTE_MIN`, `ESCAPE_DETOUR` × goal distance), fast failure, next goal), and gap mode
  books the hits lost. Earlier that morning (lumber-20261006-095124-76e4) a garou hit for 25 from 13
  tiles at the same spot.
- **PK death at a faction waypoint; the speech hold did not delay the recall (lumber-20261006-140711-048c,
  witcher_66, Seer7 run 16, 14:08:55):** Seer7 suspected that the speech hold (Bee Loga's "come",
  14:08:37 → its ack at 14:08:50) had blocked the escape. The session log says otherwise. Bee Loga (0x00447976) came into
  view at 14:08:32.4 as a **blue** (0x20 notoriety 1, flags 0x20), in a group of five players by "FACTION
  WP 17". KeepAway stepped Dan 2 tiles off at 14:08:34. Bee Loga stayed blue through the whole hold, and a blue is no
  flee-level threat, with or without a hold (player threats recall even in a hold: `check_threats`
  checks `players` before the `escape` flag). At 14:08:50.57 the server sent 0xAA (our combatant = Bee
  Loga) and a fresh 0x20 with **notoriety 4**: he attacked. The runbook double-click went out 0.75 s
  later (14:08:51.32; "18.52 s after first sight" counts from the blue sighting). Explosion (0x36BD)
  and Flame Strike (0x3709) effects landed on Dan at 14:08:53.1-53.5, so the cast was disturbed 1.5 s in.
  The book then said "recharging" twice, and he died at 14:08:55.3 (a ghost at 1784,2153) from 100 hits in ~4 s. The ack landing at 14:08:50 just
  before the attack was a coincidence. The open question is policy, not a bug: a group of unknown
  blues next to a faction waypoint, one walking up to 2 tiles and talking, gets no reaction until one
  of them flags. [INFERENCE: a single recall can't beat a multi-mage burst that starts at 3 tiles.]
  The signs were in the log: click echoes with faction tags ("Elite Mercenary [Cambria]",
  "Banner Captain [Cambria]", guild "[Officer, LoK]") from 14:08:32.5, and precasts ("In Por Ylem"
  14:08:43.9, "Vas Ort Flam" 14:08:45.4). Since then the runner leaves on both (docs/PLAN.md "Leave on
  faction tags and on precasts").
- **Attackers by faction; guild title hidden as a test (user, 2026-10-09 ~22:35):** Dan's guild DTF is
  aligned with **Terran**, but Dan doesn't participate (his click echo carries only "[Farm Around Find
  Out, DTF]", no faction tag), so to faction players he is a plain blue and attacking him makes them
  criminal (wiki Factions: only opposing-faction participants are free targets). Yet every
  faction-tagged attacker so far came from the **other three** factions: Bastet [Prevalia] (3 kills,
  10-02/03), Bee Loga and Soshuto Nakakata [Cambria] (witcher_66 death, 10-06, by "FACTION WP 17"),
  mauna kea [Cambria] (witcher_193 death, 10-09), Lola the Butcher [Andaria] (witcher_211, 10-09,
  Dan at 36/100). None of the other attacks happened near a known waypost (5 known of 18) or the
  Purification Pyre (Daemon Keep, ~1306,1750). [INFERENCE] The "DTF" guild tag over Dan may mark him as a
  Terran guild's member to their enemies. The user turned Dan's guild title display off to test it; no
  code change. Compare attacks by faction-tagged players before and after. Side effect to watch:
  `threats.friendly` knows guildmates by our own guild tag (world.self.guild, from our click echo); after a
  proxy restart with the title hidden it stays unset, so tagged DTF guildmates (Terran) would count as
  faction-tagged strangers (watched alone, recalled from in a group or within 12 tiles).
- **PK death by a lone mage at witcher_162 (lumber-20261006-160011-d152, Seer8 run 5, 16:33:30):** "Lord
  Saisho Hakai" (red, guild tag "[BJV]", no faction tag) came into view at 17 tiles at 16:33:23.0 with no
  Tracking hit before. The book was double-clicked 0.49 s later and Kal Ort Por began at 16:33:23.8.
  He attacked at 16:33:24.5 ("Corp Por") and hit for −42 at 16:33:25.0, which disturbed the cast 1.26 s in.
  The book was then recharging, and Recall cast on the book began at 16:33:25.6: Energy Bolt −43 at
  16:33:27.0, before its cursor. The book again: Kal Ort Por at 16:33:28.3, Magic Arrow −17 at 16:33:29.2,
  dead (−102 in 4.2 s, ~1906 logs on the corpse). No guarded place within 250 tiles. A mage chaining
  bolts every ~2 s disturbs every 2 s recall cast; standing still there was no way out. The runner logged
  try 2 as "recharging" with no cast counted, but it was a disturbed Recall-on-book cast. `escape._recharging`
  now reports that cast's own failure ("disturbed", method `spell_on_book`, `cast_s`), and the retry waits
  its disturb recovery (`test_escape.py`).
- **Escape latency: a stale proxy and a store write before the escape (2026-10-06):**
  - **Stale proxy.** The live proxy had been running since 2026-10-04 16:23, so it predated the
    closed-gump bound (`CLOSED_GUMPS_MAX`, 2026-10-05). It kept 5,631 closed captcha gumps: every
    state-port read was 7 MB and ~60 ms, and every runner poll and escape round trip paid that. It also
    lacked the faction tags and the speech `body`, which run in the proxy's world model. After the user's
    stack restart (16:5x): 14 KB, 0.5 ms. **Code in `harness/world/` only takes effect when the proxy
    restarts.** Check its age when a world-model fix seems not to work live (`Win32_Process`
    CreationDate of the `proxy.py` python process).
  - **A store write before the escape.** In `red_aim`, 0x20 → the chop cursor's cancel took 0.28 s. The
    `pk_seen` job event (SQLite commit beside the proxy's memory writer) cost 0.26 s of it. The escape's
    own walk re-entered the threat check, which flushed it again before the first step. Now
    `check_threats` defers its store writes (`later`: pk_seen in view and from Tracking, the
    faction-waypost spot mark) until the outermost check, and any escape it started, is over. Sight → the
    first step away: 0.29 → 0.04 s (sim). `LOOK_EVERY_S` went 0.2 → 0.05 s and `Link.wait`'s poll
    0.1 → 0.05 s now that a read is cheap.
  - **Faster recall sequences researched** (Razor scripts, the Outlands wiki and patch notes; full report
    from the RecallSpeedResearch agent, summarised): `[RecallCharge <rune>` / `[Recall <rune>` (official
    speech commands since 2025-06-04) skip the book's gump round trip, about 0.1-0.2 s; a Recall precast
    held on its cursor while walking (answered with the book on a threat) would land in ~0.1 s instead of
    ~2.1 s, but a hatchet use cancels it; Outlands Recall is 2.00 s, and a 4th-8th circle hostile spell
    always disturbs it (Protection is armor only). An Energy Bolt chain (one every ~1.95 s) can't be
    out-cast standing, hence option E. Not built yet: both need live checks first.
- **`[RecallCharge` live test (2026-10-06 17:34-17:36, Outland Dan, guild-house landing ↔ Cambria Bank):**
  the official speech command (`say_unicode("[RecallCharge <rune name>")`, any rune in a book in the
  backpack; patch notes 2025-06-04) works: our power words came **0.082/0.086/0.088 s** after the speech
  packet, and we landed **2.10/2.16/2.16 s** after it. Our runebook gump path (`escape.recall`): double-click
  → land **2.19/2.25/2.21 s** (double-click → button ≈ 0.1 s, button → land 2.12-2.14 s). So the command saves
  ~0.1 s per recall and needs no gump. One command 0.3 s after a landing got 502644 "You have not yet
  recovered from casting a spell"; one ~0.7 s after landing (2.9 s after the previous charge press) worked
  ("You generate mana for your spell." first). That doesn't settle whether it also obeys the book's
  post-use lockout (seen at ~1.8 s on 16:33). It spends a book charge like the button; 6 charges
  used by the test. The room shelf doesn't refill runebook charges, the guild shelf 0x40B84C55 does.
  Not adopted in `escape.py` yet.
- **Seer8 shift (14:13–17:55, 2026-10-06):** 11 runs, 8,555 boards into the stockpile, Lumberjacking
  99.1 → 100.0 (run 1). Deaths: the inherited 14:08 ghost (resurrected by Irma the healer, 2018,2255, ~2 min
  walk) and run 5 (witcher_162, the red Lord Saisho Hakai; Morgana the healer, 3816,417, 532 steps, the
  cliffs south forced a route east). The room shelf was empty both times; the guild shelf re-geared
  everything. Run 11: Tracking found the red Bastet at 71 tiles and the runner recalled home 2 s later.
  Skipped witcher_23/66 all shift and witcher_162 after the death. **Fixed (cd3e7be): a false
  `theft_suspected`** (juncture 421, run 11, 17:51:06; nothing was lost). The recall at 17:50:53 had put
  the hatchet 0x6627697C in the pack (0x1D + 0x25). Convert's double-click on it (c2s 0x06,
  t 1791327066.408, session_20261006_164724) brought the server's 0x1D remove at .462 and the 0x2E equip
  (layer 2, on us) at .478. The runner's state read fell between the two, with the hatchet in no
  container at all. `ledger.observe` saw it gone (`to: gone`): not on us, so not `equipped`, and with no
  expectation it was `unexplained`. The next view saw it worn, but by then it was out of the baseline.
  Now a whole item gone with no cause stays in the baseline for `settle_s` (1 s) from the first view
  without it. If it turns up on us it is `equipped`; back in the pack, it is nothing. Still gone after
  that, it is booked as before, so a thief's grab costs one window. Stack decreases are booked at once.
  Tests: `test_ledger.py` `test_hatchet_used_from_pack` (the live 0x25/0x1D/0x2E through WorldRuntime,
  failing before) and the single-item theft in `test_death`, booked once settled.
- **Seer6 shift (20:15–23:25, 2026-10-05):** 15 runs, 8033 boards into the stockpile, Lumberjacking 81.1
  → 90.5, 1 death (norse bear rider at witcher_268, resurrected by Malila (2734,617); witcher_268
  disabled). Best trips: the greedy picks witcher_23 (2410 incl. ~1380 carried logs, then 1747) and
  witcher_40 (1326). Explore picks gave 262–392 boards each (small groves), so the overseer went to
  greedy picks from run 14 on (user's goal: boards). Spots witcher_36, witcher_46 and witcher_253
  were re-enabled after false box-ins (fixed). Hatchet 0x6270D238 (GM bronze) ended at 516 uses; none
  bought. Open then: there was no `ctl act convert` (logs left after aborts waited for the next trip),
  and `lumber plan` printed no command for its greedy pick (the overseer used the pick's trip args with
  `--spot`). Both closed 2026-10-06:
  - `ctl act convert [--store]` (docs/OVERSEER.md §2): `harness/convert.py` is now the one convert
    flow, IO-agnostic like shelf.py/stockpile.py; the runner calls it over `WatchIO` (escape.LinkIO
    whose every read runs the runner's guards and threat checks) with its own hatchet use (captchas,
    hesitation), pop watch and ledger as hooks, and ctl over `_CtlIO` with a plain hatchet use (a
    captcha stops it). It returns the boards added per stack (the pack's board count before and after
    each ok text), logs left and the pouches set off. `--store` then runs `act stockpile` and, when
    that worked, `resupply --restock`, the runner's store order; chosen because finishing a trip by
    hand is always all three in the room (the chest fallback for a home without a stockpile is left to
    `act drop`). Tests: `test_ctl.py` `test_convert` (fake server: a live pouch, a use without a cursor,
    --store), `test_loop_lumber.py` `convert_stacks` for the runner.
  - `lumber plan` returns `greedy`, the greedy spot's own run (its Q*, trips, timeout, regrowth window
    and hatchet computed for that spot by the same code as the pick, `run_for` in lumber_opt.plan), and
    `--exploit` makes the pick itself greedy (`pick.chosen_by`). `test_lumber_opt.py`
    `test_greedy_command`.
- **Boxed in at Jungle Baths (lumber-20261005-201519-1738, witcher_137, Seer6, 20:15:50–20:20:29):**
  from the landing (2760,3422) the walk to tree 2821,3409 replanned round creature zones 178 times,
  swinging between routes of ~40 and ~62 steps from (2777,3427) and (2785,3432) (danger replans
  weren't counted against `too many replans`). The user saw Dan running back and forth,
  "surrounded on all sides", and asked for a recall home and the place marked unworkable: main
  stopped the task and `act recall` landed home, Seer6 disabled witcher_137. Now a walk aborts as
  "boxed in" after `DANGER_STALL_REPLANS` (10) danger replans without a shorter route, and the
  runner disables the spot, posts an attention `stuck` juncture and recalls home (`boxed_in`).
- **Caught at Sacred Pools (lumber-20261005-223558-365b, witcher_82, 22:36, Seer6):** the landing
  (1388,1839) was 6 tiles from a headless (1386,1833). The zone we stand in shrank to 5, then
  shrank again on every step in (it is recomputed from where we stand), so the route to tree 1367,1829
  crept to 4 tiles. The headless came at 3, caught up at 1 while we walked away, and we recalled
  home (99/100, 0 boards). Now a shrunk zone keeps its radius while its creature stands still
  (`aggro_zones`), and depth costs lead the route out.
- **Runs ending at the landing with logs unconverted, and the witcher_46 swing (Seer6, 20:57–21:15):**
  run 4 (lumber-20261005-205742-040a, witcher_46): the walk swung between (2798,424) and (2801,426),
  routes 86 and 62 steps, on every step as wolves and a wolfhound moved round a reaper. It was boxed
  in and recalled home. Run 5 (lumber-20261005-210141-d98d, witcher_98): 868 logs, then a red at
  18 tiles and a pk_escape recall. Each creature or player recall stopped the run at the guild
  landing, so ~1380 logs stayed unconverted in the pouch over the shift (0 boards stored). Now:
  - after any escape recall that lands home, the trip goes into the room, converts and stores
    before the stop (`home_after_recall`; the recall's damage, swings and spells are acknowledged
    first);
  - a zone that moves asks for a replan only once it touches the next `DANGER_LOOKAHEAD` (8)
    route tiles;
  - a creature that swung at us and left the view is still a creature (`Watch.monsters`), not a
    player aggressor.
- **Wisp box-in at witcher_36 (lumber-20261005-203104-4d45, 20:39:25–20:39:50):** the threats were an
  eagle (passive body) and "a wisp" at 17 tiles (`default` aggression, so a 13-tile zone). The wisp
  wandered and the walk swung between routes of 31–93 steps from (1529,1034) and (1548,1045), until
  the new boxed-in rule disabled the spot and recalled home. User: Dan stood right on top of a wisp
  unharmed, "IIRC wisps are passive" (RunUO's Wisp is FightMode.Aggressor; the Outlands wiki gives
  no aggression, and a wisp has 6000 hits and 175 Magery, so fighting it is out). Now "a wisp" is
  in `passive_names`. The walk also had no hysteresis: a danger replan now favours the route it was
  on (`STICKY_X` 1.25 on other tiles), and zone tiles cost by depth (half of `DANGER_COST_X` flat,
  half rising to the centre), so a route that can't stay out keeps to the rims (user: "I saw paths
  Dan could take ... that didn't run straight into mobs"). witcher_137's mobs (re-read from capture
  `session_20261005_194043`): an ophidian mage, a jaguar, a ratman and stranglevines (body 8) with
  13-tile zones around the Jungle Baths stables; the named humans there are notoriety-7 stablemasters
  and vendors (ignored); no wisp.
- **Death at Norse Settlement (lumber-20261005-204128-193d, witcher_268, 20:43:00, Seer6):** landing
  (2728,621); "a norse bear rider" (body 400, notoriety 3, flags 0, a creature label: a monster,
  `default`) stood 10–15 tiles off for 60 s. Meanwhile the runner planned nine trees, each a ~7 s
  unbounded A* that came back as a 245–277-step detour (the direct way is 67 steps on the static
  map, 0.05 s offline: the rider's zone made it costly), with no threat check during any search.
  20:42:53 the rider went notoriety 4 and war mode at 9 tiles; 20:42:58 at 0 tiles Dan had 35 hits
  (−65); read as a grey *player* the runner recalled on the spot (no run), the cast was disturbed
  1.4 s in, dead at 20:43:00. Fixes: `pathfind.plan(max_steps=, tick=)`: tree and walk plans stop
  looking past the detour bound (fast), and every `TICK_EVERY` (1500) expansions the Mover's guard
  runs (a threat raises out of the search); `threats.Watch` keeps a serial that was a monster a
  monster when its notoriety goes 4/5 without the player flag. Seer6 had him resurrected by Malila
  (2734,617), home, horse revived, Harvest aspect re-activated.
- **Air dragon death (lumber-20261005-185806-6bdc, witcher_23, 19:07:42, Seer5; capture
  session_20261005_093927):** air dragon 0x00569849 (notoriety 4) in view 19:07:15 at 8 tiles; ESCAPE
  1 and 2 from it (it followed both). 19:07:31.25 a humanize `walk_pause` of 8.93 s idled Dan at
  (1728,643) on the way to a tree while it closed in. 19:07:36.78 its anim 0x0C, 19:07:37.78–38.69 ten
  0xC0 effects from it to us (breath), 19:07:38.66 hits 100 → 39; the gap run (begun 37) met a closed
  door, replanned 44 → 83 steps; 19:07:42.47 second breath, dead (horse 0x0154FE11 died too). Lost on
  the corpse: ~428 logs, harvest-aspect studded set, hatchet 0x61AF05AD, reagent bag. Fixes: no walk
  pauses with a creature zone set; escapes urgent; a creature that follows us after an escape ends
  the trip; and (user, 2026-10-05) run and recall until home, never stopping in the field
  (`run_and_recall`, urgent `threat` juncture `keep_running` after 2 failed recalls).
- **Seer5 shift (18:14–19:12):** 4 runs, 1418 boards (220 converted by hand after the cliff
  ping-pong), Lumberjacking 77.9 → 80.1, 1 death. witcher_210 disabled (grove unreachable from the
  landing). After the res the first recall failed on mana (10); it worked at ~20. Hatchets: none
  left (one broke, the spare went with the corpse); the shelf has none, so the next run buys one.
- **Shift summary (Seer4, 15:57–17:56):** 8 runs, 3962 boards (2747 finished by hand after the convert
  abort, 1215 in run 7), Lumberjacking 66.8 → 77.9, no death, no disturbed recall, 5 captchas
  auto-solved, every trip ridden. Hatchet 0x61AF05AC is down to 8 uses; the spare 0x61AF05AD is in
  the room chest (the runner only looks in the pack).
- **The death robe (user, 2026-10-05: take it off after the resurrection):** `ctl act gump <res gump> 1`
  now takes it off once alive. Live 11:05: the worn "death robe" 0x60935864 (graphic 0x1F03, layer
  robe) was lifted (0x1D 43 ms after the 0x07) and dropped into the pack, and it **never came back**:
  the server took it away. `unequip` reports that as `gone`, and the death robe counts as off.
  **Every lifted item gets a 0x1D on Outlands** (a hatchet 47 ms after its lift, 12:01): a first
  fix that read "gone after the lift" as deleted skipped the drop and left a bought hatchet held
  on the server's cursor until a raw drop into the pack brought it back; fixed. Codex #13
  superseded by #4094, then #4095 (with the mount).

## Healing on the run: live checks (built offline 2026-10-06, first live run pending)

The lumber runner now uses a trapped pouch / cure / heal / refresh potion while running from a
threat and between recall casts (docs/PLAN.md "Healing on the run", docs/LUMBER_LOOP.md §13). Dan's
pack (ctl status 2026-10-06 22:50): "Yellow Potion" ×5 0x0F0C (Greater Heal), "Orange Potion" ×5
0x0F07 (cure), "Red Potion" ×5 0x0F0B (refresh), trapped pouches, all directly in the backpack.
**Restart the proxy first:** `world.self.poisoned` is world-model code (harness/world/), live only
after a proxy restart; without it a poisoned Dan is never cured and heal potions are drunk while
poisoned (they do nothing then [INFERENCE: RunUO BaseHealPotion]). Check on the first flight:

- **Self flag 0x01 when paralyzed:** `world.self.stats.flags` should read 0x21 while frozen
  [INFERENCE: ClassicUO Flags.Frozen]; never seen on Dan (all 1,378 captured self 0x20s were 0x20).
  If it stays 0x20, only a "frozen" cast refusal (`frozen_hint`) pops the pouch.
- **Self 0x17 poison now in `world.self.poisoned`** (`curl -s http://127.0.0.1:8080/api/state |
  jq.exe -c '.world.self.poisoned'`: null until the server's first 0x16/0x17 about us, then
  true/false).
- **Drinking while mounted and running works** (no free hand needed on Outlands, wiki Alchemy; a
  drink should not stop the run): look for `flight aid:` log lines and the `flee_aid` job events,
  and that steps keep coming after them.
- **The book click after a drink:** is a double-click within 0.5 s of a potion refused ("You must
  wait to perform another action", cliloc 500119 [INFERENCE: RunUO UseReq])? The runner waits
  0.55 s; a `RECLICK_S` reclick or a missing gump in the escape's tries would say the delay is longer.
- **The pouch pop breaks paralysis** and costs ~1 hit (PopWatch books it as ours, no thief alarm).

## Viz lag (2026-10-05, session 20261005_093927; fixed 2026-10-05)

- **Symptom (user):** during the lumber ping-pong (t 1791235352–508 = 16:22–16:25 and
  1791235980–6160 = 16:33–16:36) the viz map kept showing Dan bouncing between trees for minutes
  after he had recalled home.
- **The live viz at 16:50:** viz_server pid 13116 (`--live --host 0.0.0.0`, up since 13:58) held
  3.2 GB of private memory and had used 2820 s of CPU. Its only clients were 6 connections from
  192.168.42.83, a LAN device.
- **Measured** by replaying the capture through window 1 at real cadence (`viz_feed.ReplayDriver`,
  4 Hz pump, 216 s):
  - **The state frame was 1.84 MB, and 1.69 MB of it was `world.gumps`.** The world held 1134
    gumps (902 closed captchas, 164 guide pages, 43 charge prompts): the world model never dropped
    a closed gump, so every snapshot grew all session.
  - The state changed on 638 of 864 pumps (each step moves the position), so every viewer got
    ~5.4 MB/s. Events came at 62/s (7351 of the 13341 were `item_seen`), each its own SSE message.
  - Each SSE connection had a FIFO of up to 20000 frames (~1250 states, ~2.2 GB, ~5 min at that
    rate) before the connection was dropped. A new state waited behind every older one.
  - A reader limited to 1.5 MB/s: lag 7 s after 10 s, 147 s after 200 s, still growing, with 585 MB
    queued for it.
  - **Not the bottleneck:** the state port (each poll gets everything since its cursor, capped at
    5000; 2.4 ms per query), the pump (5 ms per publish), and a desktop browser (headless Chromium
    kept up with the 5.4 MB/s stream, even at 6× CPU throttle).
- **Fix** (docs/VISUALIZER.md §2.10):
  - The world keeps open gumps plus the 20 newest closed ones (`world/state.py`
    `CLOSED_GUMPS_MAX`), which brings the state to ~170 KB.
  - Each SSE connection has a mailbox (`viz_feed.Subscriber`) holding only the newest state plus
    the unsent events, written as one `world_events` batch and one state. It's bounded at 2000
    events; superseded entity churn and the oldest churn go first, while speech, clilocs, gumps
    and intents keep their order.
  - The browser parses only the newest state of a burst.
- **After:** the same 1.5 MB/s reader with today's 1.8 MB states stays 2–3.7 s behind, flat. With
  the gump bound too it is ≤ 0.1 s behind. `harness/test_viz.py` `test_burst` pins it: a 4 MB/s
  reader has the newest state 0.24 s after a burst of this size; the old FIFO took 11.5 s.
- **Takes effect** for the viz at its next restart, after `bun run build`. The gump bound needs
  the next proxy restart; until then a slow reader trails by a few seconds but no longer falls behind.

## Our mount (live 2026-10-05, Outland Dan's bonded horse)

User: the overseer and the runner must get the horse back and ride it after a death. Evidence from
session log t 1791215164–1791215431 (the Wintertop death) and a live test at 11:30:
- **Riding** is an item on layer 0x19 on us ("deck" 0x4816EF0E for Dan's horse); off it, the horse
  is a mobile (0x0154FE11, body 0xE4, hue 0x76E, notoriety 2) that follows us, followers 0/5 → 1/5.
  Dismount: the stock double-click on ourselves. Mount: the stock double-click on the horse.
- **Our pet's context menu** (cliloc mode 2): Animal Lore 1002007, then Outlands' own 3006314–3006323
  Kill, Patrol, Guard, Follow, Come, Move, Stay, Stop, **Release**, Transfer (Stable 3006324, Tame
  3006325 nearby in Cliloc.enu). Release is offered to the owner only [INFERENCE: RunUO], so
  `harness/mount.py` takes "Release" in the menu to mean "ours".
- **A bonded pet's death:** at t 1791215178.5 the horse got 0xAF (death, corpse 0x6090E6F8), 0xA1
  hits 0/100 and **0xBF sub 0x19 `bf000b 0019 00 <serial> 01`** (bonded status, dead = 1); the
  ghost keeps the same serial and body and follows us. The world model now keeps it as
  `mobiles[s].dead` and emits `pet_status`.
- **What revived it — not the healer:** the ghost walked next to Dan to Shawn the healer (3575,526,
  1 tile from Dan when he took his own Resurrection gump); only Dan's gump 0xB04C9A31 opened (three
  0xDD, no other gump) and the horse stayed dead through the recall home (0xBF sub 0x19 dead = 1
  again on arrival at 1791215422). **Entering the rental room through the steward** (t 1791215430.6)
  re-sent it on facet 3 without the flag at 10/100 hits: alive. [INFERENCE: the room's teleport
  takes pets the way a stable does, and a stable claim revives a bonded pet (wiki: "stable it and
  take it back out").] One observation.
- **The guild house rests a ridden mount (live 12:00 and 12:32):** coming into the DTF guild house
  (4134,1429) by a recall home **or out of the rental room** → "Your mount finds a quiet place to
  rest safely." (the mount item 0x1D, followers stay 0/5, no horse in view); leaving it by a recall
  out ("Kal Ort Por" 12:32:56 → "Your mount returns." 12:32:58) or into the room (`act room
  enter`) → "Your mount returns." So outside the room at home a remembered mount that isn't in
  view is resting: `mount_home` records `resting` and goes, and `mount_after_recall` checks we ride
  after the recall out (the first version detoured into the room for it each trip: useless, the
  overseer's report, run lumber-20261005-123221-09e7). Scenario `ghost_horse`, two trips.
- **Built:** `ctl act mount` (ride our pet within 3 tiles: the one remembered for this character in
  meta `own_mounts`, else the pet whose menu offers Release; a ghost gets "revive it first"; live
  11:31 it mounted 0x0154FE11 after a dismount, and a second call answered `already`). The runner's
  `mount_home` (`--mount on`, default) does the same before each trip at home, revives a ghost by
  out of the room and back in through the steward, and writes the trip row's `mount`; a remembered
  mount it can't ride is an attention `low_supplies` juncture (item `mount`), and the trip goes on
  foot. Offline: `test_loop_lumber.py` scenario `ghost_horse`.
- **A following (not ridden) mount can't be ridden in the guild house (live 2026-10-08 13:26:52):**
  after the 11:45 death at witcher_336 the horse came home walking beside Dan (alive, a follower,
  not resting). The runner's double-click at the home landing got **"Your mount refuses to let you
  ride it while in this area."** (System speech); after the recall out the horse was beside Dan
  again at the grove (it came along the recall), but `mount_after_recall` only waited for a resting
  mount's "Your mount returns.", so run `lumber-20261008-132635-28a9` went on foot until the overseer
  stopped it. Now `mount.try_mount` reads that line as `refused`, `mount_home` records the trip row's
  `mount.deferred`, and `mount_after_recall` double-clicks a mount that isn't under us once it's in
  reach (up to `MOUNT_FOLLOW_S` 4 s), `mount.mounted_at_landing`. Scenario `horse_follows`.

## Aspects (live 2026-10-04, user demo on Outland Dan)

Session `logs/session_20261004_162411.jsonl` from line 39895 (local), memory-store events after
t 1791153675; gump fixtures `harness/testdata/aspect_gumps.json`. Wiki: Aspect_Mastery.
- **Losing it:** the user dropped the Harvest-aspected chest 0x46143D1A on the ground (lift `0x07`,
  drop `0x08` to 4136,1434). The server re-sent it hue **0x0966 (2406)** and named it "exceptional
  shadowhide studded chest" (Outlands item-name packet `FF … 0015 01 0001 <serial> <name>`); back in
  the pack and worn again (`0x13`) it stayed plain. With the aspect: hue **2086** and "…shadowhide
  **harvest aspect** studded chest". The other five pieces kept theirs. The wiki: gear loses its
  aspect when it goes anywhere but the backpack, on death, theft, or when the essence runs out.
- **The menu:** saying `[aspect` (unicode speech, hue 690 font 3: `actions.say_unicode`, byte-equal
  to an older capture) opens gump **0x907FC735**: top row "Charges" 429 (Arcane Essence; "Add" 100,
  "Manage Hues" 104, "Warn When Below" 50 + "Set" 101), then three sections top to bottom:
  - **weapon:** arrows 4/5, active tier 20, Activate **8**
  - **spellbook:** arrows 9/10, active tier 22, Activate **13**
  - **armor:** arrows 14/15, active tier 24, Activate **17**, PvP Mechanics 18

  Each shows the selected aspect ("Harvest"), "Tier 1", "361/1000xp", the active tier, the cost
  "5", its bonuses (armor: armor rating 25 %, effective harvesting skill +4, 8 % double yield, 4.2 %
  damage resist for 60 s after a harvest) and the aspect icon in its hue (2086 for Harvest).
  Bottom: "Aspect Details" 102/103, 106 ("[Redline"), 105. Dan's three sections all showed Harvest.
- **Activating:** armor Activate (17) → System "Click again to confirm." and the gump again → 17
  again → "Harvest aspect armor activated." (from Dan), "Harvest skill and yield bonuses become
  available in 1 Minute.", the gump with Charges **424** (5 spent); 60 s later "Harvest skill and
  yield bonuses are available." Every armor piece was re-sent (`0x2E`), the chest with hue 2086.
  Then the user closed the gump (0).
- **Already aspected:** the second press answers "Your armor is already of that aspect." and
  spends nothing (`ctl act aspect activate armor`, live, 424 → 424).
- **Acts:** `ctl act aspect` reads the menu (charges, per section aspect/tier/xp/active tier) and
  closes it: this makes the Harvest tier observable (LUMBER_LOOP §6 listed it as a gap).
  `ctl act aspect activate <weapon|spellbook|armor> [ASPECT]` steps the section's arrows to ASPECT
  and does the double press. Live: read 6.4 s; activate (already) 10.2 s. The real activation
  through the act is untested; the user's own presses above are what it repeats.
- **Lumber runner (since 2026-10-04, user):** before heading out each trip it checks the worn suit
  by hue and activates Harvest through the menu when a piece lacks it (LUMBER_LOOP §13 "Harvest
  aspect armor"; `harness/aspects.py` holds the menu flow `ctl act aspect` uses too).
- Memories: #4085 (procedure, all characters; #4083 superseded), #4084 (Dan's tier, essence and armor serials).

## Cortina's rune tome quest (live 2026-10-03, Hackworth)

- **Cambria → Shelter by moongate:** the Cambria gate item is `0x4000069E` at (1693, 3153, 25). In
  its "Moongate Destinations" gump Shelter Island is button **18** (10 Anchor's Rest … 17 Totem,
  18 Shelter Island, 19 Corpse Creek, 20 The Arena, 21 Prevalia Meadows), then 2 travels; arrival
  (1977, 2533, 50). Picking a destination re-sends the gump with a new serial: press 2 on the new
  one. A non-Young character got no renounce prompt.
- **Cortina the Runekeeper** `0x000023F5` at (1909, 2567). Double-click opens quest gump
  `0x6EB3EC0B` ("My Quest List"): title "Rune All You Like", task "Earn 2,500 Gold / While Inside
  New Player Dungeon", requirement "less than 5,000,000 Gold on OutlandsID (current total is
  1,157)", failure conditions none, reward "New Player Runetome (Blessed) (will be added to
  backpack)". Buttons: 15 Accept Quest, 17 My Quest List, 12 (her name); closed with 0, **not
  accepted**. So a non-Young character can still talk to this Shelter NPC (the New Player Guide's
  "will not be able to interact with Shelter Island NPCs" doesn't hold for her). Patch note and
  sister quests: docs/research/TRAVEL_DEATH.md §1.4.
- **Guard false positives on Shelter, cause found and fixed (6b7f31f).** The goto from the gate
  "avoided" a phoenix and a gravebug at (1970, 2528): notoriety-1 "(bonded)" pets in war mode
  beside their owner. Later "avoiding a sheep" by the bank turned a 44-step route into 142: two
  sheep turned war mode beside "Billiam Gatherer (Young)" and died 17–31 s later. War mode was
  the only evidence, and it was checked before `passive_bodies`. Now pets (the "(tame)",
  "(bonded)" or "(summoned)" line, always type 0 hue 946) count as hostile only when red or
  swinging at us, and a passive body is never hostile by war mode alone. The four false
  `monster_seen` rows were deleted from the store.
- **Outlands never sends 0x2F with us as the defender.** All 2172 stored swings are our own, and
  there are no 0x0B damage packets. Damage to us shows only as overhead "-N" (hue 946 on self),
  so `status.attackers` is always empty live. The hunt runner now finds attackers by our own
  swing's defender and by war-mode monsters adjacent while we take "-N".
- **Shelter's banker refuses a non-Young character** ("Alas, my goods and services are only
  available to those with young player status.", Len, 2026-10-03). The bank box is per character,
  so any mainland banker opens the same box: Outpost is the closest from a moongate (arrival
  (2974, 611) → bankers Duane/Osmond at (3053, 543)/(3045, 542), 125 steps; nearest-bank
  distances per gate from the client marker packs: Outpost 76, Totem 78, Corpse Creek 120, the
  rest 140–261).
- **No reagents, no spellstone, no NPD income.** Hackworth's spellstone ("arielle's bauble") stayed
  on his Terran corpse. Spellstones come only with a new character's starting kit (wiki
  [Starting Items](https://wiki.uooutlands.com/Starting_Items): the **Mage** template gives
  Magery/Meditation/Eval Int/Wrestling 50, a book with Lightning and Greater Heal, and a
  **2250-charge spellstone**). A mongbat has ~220 hits (above), i.e. ~7 Lightnings for ~16–21
  gold, so buying Lightning reagents to hunt them would lose gold [INFERENCE: NPC reagent prices
  not read], and Wrestling (2–8 damage) is too slow. With bought reagents the hunt runner would
  also retry failed casts: it didn't check reagents before casting (fixed in 6b7f31f: casts are
  checked against `combat.SPELL_REAGENTS` or a spellstone, and 502630 blocks the spell for the
  visit).
- **`ctl say` is chat to the overseer, not game speech.** In-game speech is `ctl act say <text>`.
- **Shackleworth (2026-10-03), the quest character:** a new Young "Arcane Mage" on the same
  account. Magery, Eval Int, Meditation, Necromancy, Focus and Arcane 60, Wrestling 80. A
  prismatic staff (12–24 damage), arielle's bauble, 10 each of yellow/orange/red potions,
  100 bandages. Cortina's quest was accepted at 11:42. In the accepted-quest gump the labels of
  buttons 13 and 16 are ambiguous by position; **16 is Complete Quest** (the user's click at
  14:39:41 answered `0x6EB3EC0B` with button 16, then "You have completed the quest!"), so 13 is
  Abandon. 7 is Track Progress.
- **Quest done 14:39, about 3 h after accepting** (~110 kills, mostly mongbats plus giant rats and
  headless; overseer subagent). Progress at Cortina: 12:16 2,300 left, 12:58 1,864, 13:38 1,165,
  14:39 complete. The fight spot (5539, 507) made ~250 gold per 15 min; the crowded entrance far
  less. Spent 150 gold on 15 lesser heal potions from Minka; the gold spent didn't reduce quest
  progress (it counts gold earned). Banked 1,818.
- **The reward, "New Player Locations"** (rune tome [blessed], graphic 0x71AF): 50/50 recall
  charges, 0/50 gate. Runes: Prevalia Bank, Prevalia Innkeeper, Prevalia Stables, Prevalia
  Society Hall (Quests), Shelter Island Bank, Shelter Island Moongate, New Player Dungeon,
  Prevalia Sewers, Ratman Hovel, Urukton Bluffs.
- **Moving items between characters on one account (2026-10-03):** a rental room. The user rented
  one at Outpost with Hackworth's Rental Room Credit Deed (room interior facet 3, around (195,
  1677)); Shackleworth could enter it too, and the user moved the tome, a hatchet and 1,900 gold
  across. The blessed tome carried over fine. Per the wiki, items on a room's floor decay after an
  hour unless locked down ("I wish to lock this down") or in a secure container ("I wish to secure
  this" on a container on the floor; costs 125 lockdowns, a small room has 2 secures / 350
  lockdowns): the user's plan is a secure container as the shared stash.
- **Hackworth's home rune, Cambria (1752, 3001):**
  - Garritt the mage, Cambria (1759, 2981): reagents 3 gp each, blank recall rune 20, recall
    scroll 200, **Mark scroll 500**, spellbook 60, 20 arcane staves 50 each, training arcane staff 25.
    His list on 10-03 had black pearl, blood moss, mandrake root, garlic and ginseng only: mage
    vendors sell all eight reagents but sell out when players buy the stock, and refresh
    periodically (user, 2026-10-03; knowledge #967).
  - **Mark is 6th circle: 60 Magery to cast, 100 % at 80** (wiki Magery: each circle's "Min
    Required / 100% Success" line follows its spell table: 1st 0/30 … 4th 40/60 (Recall), 5th 50/70 (Magic Reflection), 6th 60/80, the
    same for Reveal and Invisibility). At Magery 60.0 from the spellbook all 20 casts fizzled (12 reagent sets
    used). **Cast from the scroll it worked first time** (double-click the scroll, target the rune;
    "You generate mana for your spell."): a scroll casts as if lower-circle [INFERENCE from RunUO].
    Adding a scroll to a book: drop it on the spellbook.
  - The tome's runes can be dropped (rune detail page "Drop Rune": "You remove the rune from the
    rune tome." → a "recall rune" in the pack) and re-added (drop the rune onto the tome). The
    marked rune is named after the region ("Cambria"). We dropped Shelter Island Bank, marked it at
    the Cambria bank and set it as default (detail page button 24 for the right-hand rune).
  - The Prevalia moongate is up a stair north of the Moongates.xml marker: item `0x400000AC` at
    (1475, 1486, 55); a public library of 14 rune tomes sits by it at (1480, 1501–1502).
  - Cambria bank → library walk is ~300 route steps (178 tiles straight); the old 250-move cap
    aborted the first Witcher trip. Walks now have no move limit by default (user decision): the
    replan cap and "no route" still end hopeless walks.
- **Ghosts get moved (live 2026-10-03, Hackworth after the 19:08 death).** An unresurrected ghost is
  moved by the server about every 30 min with "Your spirit grows weak and you seek resurrection.":
  (873,1481) → (1693,1492) at ~19:38 → (1651,1586) at ~20:08, next to Prevalia. He was resurrected
  by Sergio the healer (0x0004925B, (1684,1554); Kenton stands upstairs at z 65) at **100/100
  hits**, naked (clothes in the pack), with the Hamstring debuff (icon 53) still shown. The
  Resurrection gump 0xB04C9A31 opened (journal `gump_open`) but **was missing from
  `status.gumps_open`**; it was answered by its serial from the journal [open bug].
- **Hackworth's spellbook lacks Magic Reflection** ("You do not have that spell!"). Garritt's Magic
  Reflect scroll is 300 gp; Garritt had spider's silk again (stock 999) at ~20:15, so the stock
  refreshes. Hackworth now carries 10 each of garlic, mandrake root and spider's silk; bank 177 gp.
- **Urukton Bluffs (live 2026-10-03, Shackleworth).** The New Player Locations rune lands at
  (5248,2821,z33) on a raised landing inside the dungeon: "You are entering a sanctuary dungeon."
  (and "You have left the protection of the town guards."); a golden moongate at (5246,2822)
  allows recall. Below: orcs, an orc captain, blood orcs, orc mages, an orc lord, goblins, a cave
  bear, and **human-bodied "an orc hunter"** (body 400, notoriety 3, flags 0), fought by NPC
  soldiers ("a prevalian footman/mage/captain/marksman", human bodies, innocent).
  - `threats.identify` called the orc hunters **grey players** and the soldiers innocent players
    (no player flag; "human body, assumed player"). Fixed (0add311): a human body with a
    creature-style name ("a"/"an") and no player flag is a monster at notoriety 3 and an NPC at 1–2;
    4–6 stay players. Their corpses ("an orc hunter corpse") are monster corpses. **Since
    2026-10-08 (user: "we're frequently treating human-bodied monsters as players"):** 4 and 5 are
    monsters too, and a name of two or more all-lowercase words counts as creature-style ("ghostly
    footman", no article). Evidence: every named player sighting on record (20 `pk_seen` rows,
    2026-10-02..08, e.g. Bastet, Bee Loga, khiizriel) carried the 0x20 flag, while all 6 threat
    junctures from an assumed player were spawns: "a rime spirit soldier" (4, body 401, ended a
    witcher_146 trip with 2 260 logs), "a brigand hedge mage" (4), "ghostly footman" (3). Reds (6)
    stay players whatever their name. `test_threats.py` `test_npcs_and_players`.
  - **Death by a manual overseer fight, 20:29:56:** the recall in (a tome charge, "Kal Ort Por")
    put the arcane staff in the pack; the overseer walked ~85 steps into a room with goblins, orc
    mages, an orc lord and a cave bear and attacked one orc with fists (-2 per hit); it took
    -25 -12 -15 -11 -14 -21 in 15 s. User decision: **no manual fights in dungeons**, only the hunt
    runner fights there (OVERSEER §5). `ctl act recall` now puts a weapon the cast moved to the
    pack back on (`weapon` in its reply).
  - **Corpse run (20:40–20:50).** A ghost may use the golden moongate: its "Moongate Destinations"
    list there is Anchor's Rest 10 … Totem 17, Corpse Creek 18 (Travel 2); Totem lands ~27 steps
    from Jacinda the healer (3862,2883). The resurrection gump opens while the ghost walks up, and
    any step after it opens makes Accept fail ("You have moved too far from your original location
    to be resurrected."): stop, then press Accept. Back at 94/94. First try (the overseer, staff
    and bag): 24 s walk in, 2 s per item, dead 8 s after reaching the corpse. Second try, scripted
    (one process, staff only): 35 s in, the staff in 1.8 s; then other players killed the orcs and
    everything else was taken. The first corpse had decayed to **bones (graphic 0x0ECD) that still
    held its items** (the second spellstone, the tunic). `act recall` re-wielded the staff after
    the cast on its first live use (`weapon: rewielded`).
  - **A spellstone bound to someone else blocks casting (20:56).** The "second spellstone",
    `0x63611A72`, wasn't ours. A single click reads "a spellstone", "(500 charges)", "[bound to Da
    Cajun Crusada]". Ours, `0x57064E09`, reads "(2828 charges)", "[bound to Shackleworth]". Both
    show as "arielle's bauble" (graphic 0x023B). With the foreign one loose in the pack and ours
    in a bag, the server answered 17 of 17 Greater Heals with **502630 "More reagents are
    needed"**. It sends that **after the target answer**, so the runner logged each cast as a
    heal while mana stayed at 91/91. Once ours was loose and the foreign one in a bag, a Greater
    Heal landed ("You generate mana for your spell."). Whether the server checks only the first
    stone it finds is unknown [INFERENCE].
    - Fixes: `combat.reagents` skips a stone labelled "[bound to <someone else>]".
    - `loop_hunt` checks for 502630 after the target answer too (`refused_after`: the heal isn't
      counted, the spell is blocked for the visit).
- **Lumber data audit (1a5d59f; LUMBER_LOOP §6 "What the optimizer learns from").** Trip rows
  now carry the travel legs (every cast with method/ok/failure/seconds, the book, Witcher rune,
  walk to the library, mana and reagents), `lockout_s`, `stationary_s`, `travel_s`, `supplies`,
  `skill_end`/`skill_gain`, `weight_end`, `players_seen` and `hatchet_uses_seen`. These are JSON
  fields, so there was no schema change. A runner records them from its next start.
- **Travel events before 2026-10-03 store the tome's row index in `rune`**, not the Witcher rune
  id (the recall result overwrote it); the id survives in the row name ("291 - …"). New events
  carry `witcher_rune` and `book`.
- **lumber_opt:** the 60 s travel lockout is overhead, not field time; a trip that never reached a
  tree has no field time; priced supplies come off each trip's value. Prices recorded 2026-10-03
  (Garritt, Cambria): `reagent:<black_pearl|blood_moss|mandrake_root|garlic|ginseng>` 3 gp,
  `recall_charge` 200 gp (a recall scroll adds one charge).
- **Not passively observable:** Harvest Aspect tier (only the `[aspect` gump, Aspect Mastery
  0x907FC735, shows "Harvest Tier N"; since 2026-10-04 `ctl act aspect` opens, reads and closes
  it, see "Aspects"); skill gains (no message, only per-trip snapshots); hatchet uses left (only
  a click label "(N uses remaining)", read back by `Memory.uses_seen`).
- **After pulling viz changes:** `cd viz && bun run build` (viz/dist is gitignored), then restart
  `viz_server.py`. Edit-tool relative paths resolve to the session cwd, not a git worktree: use
  absolute worktree paths there.
- **Tracking while lumbering (since 2026-10-03, 7cc9c75; LUMBER_LOOP.md §13 "Tracking reds"):** the
  runner keeps Hunting murderer players on for the whole run with `ctl act track`'s clicks
  (`harness/tracking.py`), re-enabling it when the hunt lines or buff 173 say it's off, at most
  once per 30 s. Before this nothing tracked on 10-03: every login drops the hunt.
- **What ends Hunting (store, measured):** only Stop and a relog. A relog removes buff 173 without
  a stop line and keeps the mode. Recall, death and resurrection, and chopping keep it on (the buff
  is re-sent). In 121 min of murderer hunting there were 0 murderer hits, even with Bastet in view
  at 18 tiles (Corpse Creek); why is open [INFERENCE: Tracking 60's chance or range, or the
  lawless region]. The hit reaction is so far tested only in the simulator.
- **A tracked red within 100 tiles** (`--track-react-range`; 100 since 2026-10-14 after the Thugnificent
  death, 80 from 2026-10-03, 40 until the third Bastet death below, 60 for one day 2026-10-08/09 until the
  witcher_193 death below), or a known PK at any distance (since 2026-10-14), at a pvp spot is a red
  escape (recall home, "tracking: <name> N
  spaces"). Farther reds (e.g. sitting in a house) are `pk_seen` events with `counted: false` and
  don't feed the spot hazard. Trip rows carry `tracking` coverage (on_frac, hits).
- **Third Bastet death (live 2026-10-03 19:08, terran_wilds, Hackworth).** Tracking found him at
  55 tiles at 19:07:50 ("beyond 40: logged only"); mounted, he struck 5.5 s later. He came into
  view at 54.70 during the chop's 2.1 s aim pause; the runner answered the chop cursor at 56.61
  and only then opened the tome (57.20, 2.5 s after sight); "Bastet is attacking you!" at 56.69,
  his hits broke the old 3-try escape, the guard flight was too late. Lost: 1,070 logs, the last
  hatchet, 60 gp, reagents. Two of his three kills were in the Terran wilds.
- **The runner was blind inside every pause and result wait** (`Human.wait` was a plain sleep,
  `Link.wait` had no threat check). Fixed (c4db365): every lumber wait checks threats every 0.2 s
  and once more right before its action (`LumberLoop.pause`/`wait_for`); an open cursor is
  cancelled with the stock 0x6C before any escape; the recall event logs `react_s` (sight → book
  double-click). A simulated red during the aim pause: 0.00–0.20 s, was 0.91 s.
- **Creature stops recall home first (3316e5b):** live at witcher_291 the runner converted logs
  for 12 s under monster attack (85 → 40 hits) and exited in the field; the overseer's recall
  landed at 15/100. Now damage, a creature that keeps coming, or too many escapes end the run
  without converting and, away from home, with a recall home first (urgent `threat` juncture).
- **A pickpocket, not an attack (live 2026-10-03 17:24, witcher_291, Hackworth).** "Caputo Wood"
  (0x0073C056, a blue) walked up to 1 tile. At 17:24:55.143 a 0x20 MobileUpdate changed his
  notoriety 1 → **3** (attackable to us only; in RunUO the mark of someone who just aggressed us
  [INFERENCE for Outlands: a steal attempt does it]), and 75 ms later our 10 mandrake root
  (0x57E5A7EC, the whole stack) was deleted from the pack. No damage, no swings, no "You notice"
  text. The runner saw "grey at 1 tile" and recalled home with a tome charge, correctly. It
  didn't book the loss: the threat check ran before the pack ledger and raised into the escape.
  Fixed: the ledger runs first, and the escape recall books pack losses before stopping
  (`theft` job event, `theft_suspected` juncture). Travel recalls by spell now tell the ledger
  about the reagents they spend, so they never read as theft.
- **A trapped pouch popped by its owner (live 2026-10-04, session `20261004_113229`, Hackworth at
  (1620,1549,50), user-driven; `loop_mine.py timeline 20261004_113229` 2:31 and 2:52).** For the
  log-pouch plan (docs/PLAN.md "Keep thieves off the logs").
  - **Buying:** Errol the provisioner sells "Trapped Pouch" at 25 gp. They come as pouch `0x0E79`
    with **hue 38** (red). Hue 38 is how the community scripts find them (`findtype "pouch" backpack 38`).
  - **The pop:** a double-click on it (C2S `0x06`). 56 ms later the server sends one flush with:
    - `0xAE` from our own serial with text "-1" (the damage number), and `0xA1` hits 100 → 99.
    - `0x54` sound `0x0307` at our tile.
    - Five `0xC0` effects, item `0x36BD` (the explosion), on our tile and the tiles around it. Their
      x/y/z are u32 (`c0 02 src dst 36bd 0000 | x u32 y u32 z u32 | x u32 y u32 z u32 | speed 0a dur 0f …`).
    - System `0xAE` "You now have N trapped pouches remaining." N counts the hue-38 pouches left.
    - `0x25` re-sending the pouch in the backpack with **hue 0**: it is now an ordinary pouch. The world
      model shows the hue change (38 → 0); the trap state has no other visible field.
  - Hits regenerate to 100 about 4 s later.
  - The world model doesn't parse `0x54` sounds (30 unparsed in this session). The proxy log's `hex`
    field stops at 64 bytes, so long messages need the `.raw` capture (the timeline reads it).
  - **A thief's snoop shows the explosion on us** (user, 2026-10-04: known for sure from play). So
    the `0xC0` `0x36BD` effects at our tile and the `0x54` `0x0307` sound arrive whoever opens the
    pouch, together with the `0x25` hue 38 → 0 re-send of the pouch. Still unmeasured: whether a
    thief's pop also damages us and sends the "remaining" line. A pop we didn't cause = explosion
    and/or hue change with no C2S `0x06` of ours on that pouch just before.
  - **Built on it (2026-10-04, offline; docs/PLAN.md "Keep thieves off the logs"):** the world model
    parses `0x54` (Outlands 18 B: mode, sound u16, volume u16, x/y u32, z i32) and turns sounds and
    `0xC0` type-2 location effects within 2 tiles of us into `sound` / `effect` events. The five
    explosions sit on x±1, y±1 and (x+1, y+1, z+11), none on our own tile: exactly RunUO's
    TrapableContainer MagicTrap, which also damages the one who opened it [INFERENCE: a thief's pop
    costs the thief the hit, not us]. A lift sends `0x1D` for the lifted item at once (2:50.9, twice),
    so the ledger must expect a drag's vanish (`("moving", serial, container)`).
  - **The vendor paid from the bank:** Hackworth had 0 gp in the pack; Errol's line was "The total of
    thy purchase is 75 gold, which has been withdrawn from your bank account." `ctl act buy` now allows
    a buy above the pack's gold and books the amount from that line.
- **Casting moves the hatchet to the pack** (hatchets are two-handed): "That must be equipped
  for any serious chopping." The runner's hatchet use re-equips it.
- **The lumber runner runs from creature damage (afcd4b0).** When one creature hits at hits ≥ 60 %
  (`--creature-recall-at`), it walks out of that creature's reach (ranged: the 12-tile spell range
  + 2, user decision; melee: flee radius + 2) and chops on at a tree outside it. A re-hit within
  10 s of arriving (`--creature-rehit-s`), low hits, two possible attackers or no escapes left
  still recall home without converting. Outlands never names the attacker, so
  `threats.hit_attackers` infers it (adjacent creatures, else every creature within 12 tiles =
  ranged); each episode is a `monster_hit` job event, and a sole attacker's body is learned as
  aggressive/ranged with its reach raised to the farthest hit. Gazer (body 22) is ranged from the
  start. Trees within reach of a known-aggressive creature in view wait until it leaves.
- **Second Bastet death (live 2026-10-03 18:04, witcher_282 Nusero Island SE, Hackworth).**
  Timeline from the store: 18:04:38.44 Bastet ("Serial Killer [Prevalia]", "[Aggressive
  Captcha, DVLS]", red) appears at 11 tiles. He carries the **Stationary Penalty buff (icon 277),
  removed at 40.05**, so he had just recalled or gated in on top of us [INFERENCE: hunting us by
  Tracking]. 18:04:38.92 he casts ("Des Mani"); the runner opened the tome 0.9 s after he
  appeared and started a charge recall at 39.49; at 40.31 "Bastet is attacking you!" with 500641
  (concentration disturbed): his spell landed 1.9 s after he appeared, before a 2.0 s recall
  could finish. Retries got 502644 (not recovered) and disturbed; no guarded place within 250
  tiles; the overseer's own recall was disturbed too, then "the book's gump didn't open"; dead.
  Tracking on murderers was on and gave no warning: his first hit came at 18:04:47 ("1 space").
  Lost: 113 logs, hatchet, ~40 gp, reagents; the blessed tome survives. **A mage PK who recalls
  onto us beats a 2 s recall escape**: the only defences are not being found (spot choice,
  shorter field time where he hunts) or something faster than a recall [open]. He killed us at
  Terran on 10-02 too; Nusero had a second red at witcher_280 the same hour.
- **Spell interrupts measured (docs/research/SPELL_INTERRUPTS.md).** Recall takes 2.03 s (charge or
  spell, 27 casts). At Magery 60 a creature melee hit mid-cast breaks it 44 % of the time (26/59);
  damage-over-time ticks never did (0/13); Bastet's spells and melee 3/3. After a disturbed cast the
  server refuses the next one for max(0.2, 1 − √(elapsed/cast time)) s (30/30 live retries). At
  Nusero the old 3-try escape spent try 2 on that refusal and gave up at 42.07 s, while nothing hit
  us until 46.50 s.
- **The escape recasts until it lands (d6e6c63):** a 20 s budget instead of 3 casts; each recast
  is pressed right at the end of the disturb recovery, with the book opened during the wait;
  refusals don't count as casts; death ends it. Replaying Bastet's landed hits, the old code fails
  as it did live and the new one lands at 44.4 s.
- **What would have saved Nusero:** Magic Reflection (it bounces the opening Weaken; recall 1 lands
  0.49 s before his Harm), or the new retries if his 43.68 s swing misses as it did. Hiking (needs a
  secured campfire and no combat for 30 s), running (we were on foot, he was mounted) and healing
  (he did ~15 HP/s) would not. Magic Reflection is 5th circle, ~50 % at Magery 60 (100 % at 70 or
  from a scroll). Captures still needed: a Magic Reflection cast (buff icon, duration) and a hike
  (campfire, Atlas gump, whether a hit during the 5 s freeze cancels it).
- **Lumber trip size is a renewal-reward rate (86075f3, LUMBER_LOOP §6):** three hazards per
  field hour, each learned per spot and shrunk to a pooled rate: death h_D (the trip banks
  nothing; every unblessed item carried is lost at full price: all hatchets, priced reagents;
  Young loses nothing), sent home h_S (the load comes home) and theft h_T (takes a share f, prior
  0.5). Q runs 200–10,000 logs, capped by (weight_max − weight)/0.025, no stint cap; `--timeout`
  scales with the trip. On the store at 23:06 UTC threat stops dominated (sent home 1.45/h pooled,
  witcher_291 3.0/h), so Q* rose to ~1,000–1,800 with P(death) per trip 3–9 %. Theft events
  don't record the load carried yet; f learns from carried_end + taken until they do.
- **Overseer findings (LumberShift):** Horseshoe Bay ran ~1,400 logs/h of chopping but went dry
  at 605 logs. From the Cambria bank `act goto` can't route straight to the moongate; go via the
  rune library (1708, 3181). A goto that passes onto the gate closes its gump: `dclick
  0x4000069E` reopens it (Horseshoe Bay is button 13).
- **Overseer tooling, live:** `ctl junctures` without `--after` listed the oldest 100, hiding an
  open speech hold for 163 s; it now lists the newest. `act drop --amount 800` split a gold stack
  correctly but returned `ok:false` ("the world model doesn't show the item moved"); not fixed.
- **Stationary Penalty decoded (2026-10-03, 26 captures):** buff 0xFF sub 8, icon 277. `{value}`
  (steps left) is `timers[0].value`: 5, 4, 3, 2, 1, then removed (sub 9) on the 5th step that
  changes our tile; runs and stepping back onto the tile just left both count. It comes 301–315 s
  after the last one-tile step (all 40 cases; fighting, casting and teleports don't reset the
  clock), at every login, and at once after most other teleports (recalls/moongates 23/28,
  leaving a rental room 21/22), never on the NPD entrance or exit (0/73).
- **It didn't cut our PvM damage** despite its text: on 10-02 the runner hunted 99 of 165 NPD
  minutes under it with the same Lightning numbers (27–35), kill rate (0.43 vs 0.44/min) and loot
  [INFERENCE: it may apply to PvP or harvesting only]. Harvesting is blocked under it (wiki
  Mining). Both runners now clear it (`harness/stationary.py`, fc9ad5a): walk the steps + 1 out
  and back over known tiles, never a teleporter, and reposition after `--reposition-s` (240 s)
  without a step. Rows count `stationary_clears` and `repositions`.
- **Spellstone known only by graphic after a proxy restart:** item names reach the world model
  through clicks/labels, so the reagent gate saw no "bauble" and blocked spell heals (juncture 161,
  13:26). `combat.reagents` now falls back to the tiledata name ("arielle's bauble", 0x023B).
- **Hunt crawl (ee62f02):** `run hunt --enter --crawl --pull-range 8 --mana-reserve 999
  --target-name "" --gheal-min-missing 1` patrols the NPD instead of standing on one spot
  (docs/HUNT_LOOP.md "Crawl"). The NPD is one connected floor, dungeon level 1 (10,829 tiles,
  farthest tile 182 route steps from the exit), with no known teleporter floors. Dungeon levels
  count from 1, the game's convention. The crawl's own 40-step depth bands are **zones 1–5**
  (7f7dd89; were "levels 0–4"): zone 2 opens only after ~10 min of zone 1 showing under ~20
  hits lost/min. A crawl walks up to twice the pull range to loot its own kill (38a5d36).
- **Fight priors from the store:** every engagement is now a `fight` job event. Rebuilt from the
  event log: mongbat 153 kills, median 29 s and 7 hits lost per kill, 13.8 gold per kill, 35 % of
  kills without gold (pets/players). A creature the crawl fled from stays avoided in later runs
  (never pulled, 6-tile danger zone; still fought if it attacks). `python harness/crawl.py
  floor|priors --memory harness/data/harness.db` prints the floor graph and priors.
- **Arcane staff vs casting:** "Players with at least 80 skill in Arcane, Wrestling, and Magery
  can continue to cast spells while wielding an Arcane Staff"
  ([wiki Arcane](https://wiki.uooutlands.com/Arcane)). Below that, every cast moves the staff
  to the pack (0x1D + 0x25 ~50 ms after our 0xFF cast, no message). The hunt runner now melees
  with the staff, uses potions first inside, and re-equips it with the stock lift + 0x13.
- **The prismatic staff (graphic 31038) has tiledata layer 0** (flags include Wearable).
  `ctl act equip` falls back to the layer the server last wore it on (`world.worn_layers`), then
  `combat.KNOWN_LAYERS` {31038: 2}. `worn_layers` and the pet flag need a proxy restart.
- **Hunt runner pins (fixed):** with `--pull-range` above the 10-tile spell range it engaged a
  frog 12 tiles away and waited forever. A harpy behind a wall answered "Target cannot be seen"
  (500237) for minutes. A target neither adjacent nor hurt for 15 s is now dropped for 60 s,
  and the pull range is capped at 10. The loot loop also never healed: hits fell 64 → 49 of 84
  while looting and it left without drinking; loot() now heals and checks the leave rules
  before each item.

## Evening lumber shift: Magic Reflection and runner gaps (live 2026-10-03 21:43–22:38, Hackworth, LumberSeer)

Task logs: `logs/tasks/lumber-20261003-214343-7dee.log` (Horseshoe Bay), `…-221714-5a7d.log`
(witcher_280), `…-222221-9fb8.log` (Corpse Creek). The overseer's account: `agent://LumberSeer`.
- **Magic Reflection, first captures.** The buff is icon **138**, title "Magic Reflection", no
  description, timer value 0.0 and end 0: **it has no end**. One cast lasted 34.2 min and ended only when a
  gazer larva's spell hit it ("Magic reflect removed."). Recasting while it's up is refused with
  "That spell is already currently in effect." and costs no mana. It cost 13 mana. A successful
  cast writes no journal line; a fizzle writes "The spell fizzles." (cliloc 502632). At Magery 60,
  2 of 4 casts landed. So the open "how long, how to see it" questions in PLAN.md "PK survival"
  have answers: it lasts until it absorbs a spell, and `status.buffs` icon 138 shows it.
- **Reactive Armor and Outland Dan's book (live 2026-10-10, rental room, `ctl act cast`):** Dan's
  "exceptional spellbook" holds both Magic Reflection and Reactive Armor. Both landed on the first try at
  Magery 80.2. Reactive Armor ("Flam Sanct") is buff icon **139**: title "Reactive Armor", timer value 0.0,
  no end, no target cursor and no journal line. With Magic Reflection the pair took 12 mana (100 → 88).
  `status.buffs` also keeps long-expired buffs (Protection, Hamstring) with `expired: true`, and trip rows
  list them: read `expired` before calling a buff up. The lumber runner keeps both wards up since
  2026-10-10 (docs/LUMBER_LOOP.md §13 "Wards").
- **Escape recalls landed on the first try both times** (2.2 s and 2.29 s, tome charges). At Corpse
  Creek the flight started 0.45 s after the grey player was first seen, and the runner cancelled
  its open target cursor first.
- **Gaps seen (all dealt with on 2026-10-03, each item below):**
  - At witcher_280 the larva's first spell ("Magic reflect removed.", 22:17:32) didn't count as an
    attack. The runner reacted only to the −14 hits 4 s later, 7.75 s after first sight. Reacting
    to that line, or to any spell from a visible hostile, would have moved the flight ~4 s earlier.
    **Fixed 2026-10-03.** Capture 20261003_213125 at 22:17:32.136 holds the System line, an S2C 0xC0
    fixed effect 0x37B9 on Hackworth (Magic Reflection taking it) and the bolt reflected onto the
    larva (0xC0 type 1 on it, its hits 100 → 99); at 22:17:35.869 the next spell: 0x374A on us,
    "Spell siphon active.", the "-14" 0.5 s later. Sounds (0x54) carry only a position; the Spell
    Siphon buff arrives with its line. Across all 2026-09-30..10-03 captures, the spell signals on us
    (0xC0 effects our own casts didn't make, i.e. not graphic 0 = cast start, 0x375A, 0x3735, nor the
    heals/cures 0x376A/0x373A, plus those three lines) numbered 93, every one with an attack: 89 with
    a hits drop within 3 s, the other 4 two spells that cost no hits (this one; an explosion 0x36BD,
    15:34:22 in 20261003_150103). The
    world model now parses 0xC0 (Outlands 52 B, graphic u32) and emits `effect` when self is source or
    target; `threats.spell_on_us` reads a spell on us from that effect (lightning, a non-benign fixed
    graphic, a moving effect at us naming its caster) or from "Magic reflect removed." / "You absorb
    their spell." / "Spell siphon active."; `damage_signal` counts `spells`, and the lumber runner's
    `creature_hit` treats one like a hits drop (0 lost): at witcher_280 a run at 22:17:32.1 (one
    attacker at 100/100). The effect half needs a proxy restart; the lines work on the running one.
    LUMBER_LOOP.md §13 "Spells on us count as damage"; tests `test_loop_lumber.py gazer_reflect`,
    `unit_capture_spell_witcher`, `harness/test_threats.py` test_spells, `test_world_units.py`
    test_effect_c0.
  - Juncture 222 said "2 creatures attacking" while `attackers` was empty and only the larva was
    hostile. **Fixed 2026-10-03.** Two causes: `attackers` in the threat/pk_escape junctures and the
    recall event was the 0x2F swingers only (never sent with us as defender on Outlands: always []),
    while the count came from `threats.hit_attackers`, which blamed the war-mode larva at 10 and also
    the calm cougar at 8 (aggression "default", which the threat list showed as "passive creature").
    Now `attackers` = swingers/casters plus the ones `creature_hit` blamed, a hostile creature in
    reach from afar leaves out unknown-aggression ones not known to be ranged (222 replayed: the larva
    only), and a calm creature's reason reads "passive creature (default)". Test
    `unit_capture_juncture_222`, test_threats `test_hit_attackers`.
  - Trip 1's row listed "a stinky mongbat" and "a wet mongbat" under players seen (probably named
    pets), and its `buffs` held raw cliloc ids ('1044416', '1110004') instead of names.
    **Not pets: they are players (no change to the classification).** 0x3D56E5 / 0x3DB217 at the HB
    bank (21:43:00): body 0x190, flags 0x20 (the player bit), notoriety 1, a 0x78 with a backpack
    (layer 0x15), a mount (0x19: 0x3E9F, 0x3EA0), clothes and hair, 0x11 hits 100/100 and 90/90, no
    "(tame)"/"(bonded)" line; a pet has none of that. Pinned by `unit_capture_named_players`.
    **Buffs fixed 2026-10-03:** `lumber_opt.character` names a buff like `status.buffs` (the title,
    else the cliloc rendered: "Magic Reflection", "Tracking Hunting"); test `unit_capture_buffs`.
  - The hatchet sat in the backpack the whole shift and chopping still worked; `worn: False` on the
    trip row. **Recording fixed 2026-10-03; the runner was right.** Every cast moves the hatchet to
    the pack (`0x1D` + `0x25`: 21:43:17 Magic Reflection; 22:16:59 the Magic Reflection recast the
    server refused with "That spell is already currently in effect."; 22:17:36 and 22:36:59 recalls),
    and the first chop's double-click makes the server equip it (`0x1D` + `0x2E` layer 2, 21:44:39,
    22:17:32, 22:22:40); it stayed in hand while chopping. The row's `worn` was read at the trip
    start, right after the cast. Now `hatchet.worn` is whether it was in hand when the last chop's
    cursor came, `worn_at_start` the start reading (LUMBER_LOOP.md §13 "Hatchet"); test
    `unit_capture_hatchet`.
  - The "Spell Siphon" buff (icon 167) stayed in `status.buffs` for 20+ min after the hit (possibly
    stale). **Not stale; fixed what we read (2026-10-03).** Its sub 8 (22:17:35.871) carries value
    0.06 and a timer end 3 600 000 ms after its stamp: a one-hour debuff. The server re-sent it
    with the same end after the moongate (22:21:20) and again at 22:21:31, and no removal
    came before the capture ended (22:45, 32 min left); in 20261003_150103/170434 its sub 9 came
    3–13 s after the hour. `ctl status` had shown the 0.06 as `timers_s` (seconds), and nothing
    showed the end. Now the parser names the f32 `value`, the world model keeps the server clock
    (sub 3) and gives each buff `ends_t`, and `status.buffs` shows `values`, `ends_in_s` and
    `expired` (docs/WORLDMODEL.md sub 8, OVERSEER.md `status`). The server doesn't always remove
    an ended buff (20261003_111419: Spell Siphon 888 s past its end; 13 such self buffs in all
    captures); the world model keeps those, like the stock client, and status marks them
    `expired`. Tests: `test_world_units.py` "a buff's end on the server's clock" (the captured
    packets), `test_ctl.py` status.buffs. The live proxy must be restarted to carry it: until then
    its buffs have `seconds`, not `value`, so `values` read null and the runners' Stationary
    Penalty clear walks its default 5 steps (`stationary.CLEAR_STEPS`).
  - A tile `act goto` onto a moongate (2974,611 / 2025,2077 / 1693,3153) closed the gate's gump on
    arrival, against the documented range-0 rule; a double-click on the gate reopened it.
    **Fixed 2026-10-03.** The overseer used the default range 0 (`act goto 2974 611`, `act goto
    1693 3153`). `ctl _act_goto` chose the gate to keep by looking for a moongate on the goal
    tile when the goto started, but all three started 28+ tiles away (3035,514 / 2008,2224 /
    1708,3181), outside the view, so the gate wasn't in the world model, `gate` was None and the
    Mover closed the gump on arrival like a gate passed over (B1 button 0 at 21:40:54, 22:16:35,
    22:21:09). Now a goto onto a tile or ground item with range 0 means to use whatever gate
    stands on that tile, seen yet or not. Test: `test_ctl.py` "goto x y onto a moongate first
    seen on the way" (the pre-fix ctl sends the B1 there; passing over a gate: `test_mover.py`).
- **Horseshoe Bay ran dry again at 613 logs** (30 min, 4 trees unreachable at the end), matching
  the 605 of the afternoon: the grove holds ~610 logs per regrowth window (docs/PLAN.md "Tree
  density enters as grove capacity").
- **Open risk: the daily cap strands a run (found 2026-10-03 22:46 from the code, not seen live).**
  The break has a warning and a grace (`break_due`, 10 min, then the runner banks and exits), but
  the daily cap has neither: when `active_today_s` reaches `DAILY_CAP_S` the gate blocks until
  midnight and the runner aborts where it stands (agent_link.py module doc: "an exhausted daily
  budget aborts"; agent_gate.py `state`). At 22:46 the next break was due in 2770 s and the cap in
  3020 s, so the cap would have closed during the break's grace while the trip walked home. A
  break test should start with more than ~15 min of daily budget beyond the break, or the cap
  should get the same due-then-grace treatment as the break.

## Traffic audit of the 2026-10-02/03 captures (2026-10-03)

All 20 sessions 20261001_214649 … 20261003_150103 replayed in timed order (`replay.timed_packets`
→ `WorldRuntime`); 10-03 findings unless a date is given.

- **The proxy layer handled everything.** Every jsonl row matches its raw packet; 0 parse
  failures, 0 anomalies. 17 `c2s_stale_token_dropped` (the client re-sent spent fastwalk token 1,
  forced to 0): every walk was confirmed. 375 fabricated target cancels = 375 spent-cursor drops.
  All 63 server denies (`blocked`) have a cause on the wire: client steps into closed doors
  (0x06A5/0x06ED at the Outpost bank, 0x06E5 the rental-room door) and once into a mobile; agent
  steps onto the NPD teleporter tiles (1912,2556), (5535,530), (5537,530) (a teleport answers as a
  deny, docs/HUNT_LOOP.md), 3 onto the door tile (5532,505) and one into a mobile.
- **World-model gaps (no state lost):** S2C `0x76` (22 B) is unhandled, but every one so far
  (since 2026-09-29) comes on a map-region jump (rental room in/out, some logins) and is followed
  by the self `0x20` the model uses. Layout `76 <x u32> <y u32> <z i32> <5 × 00> <width u16> <height
  u16>`: the Outpost room interior `x 195 y 1677 z 1, 0x0A00 × 0x0800` (2560 × 2048, facet 3),
  the mainland `0x2A00 × 0x1800`. `0x2C` (death screen; 10-02 19:43 PK, 10-03 10:23) is parsed
  and consumed since 2026-10-03 (`2c <action u8>`: 0 before the ghost body, 2 after, in the same
  flush; no event, the body-based `death` stays the signal; docs/WORLDMODEL.md).
- **Hunt loot refused, counted as taken.** 35 of 125 agent corpse opens in the NPD hunts
  (113952, 123614, 125556) got "Players cannot commit aggressive actions in that location.", 25 of
  them within 5 s of our own "You have gained a little fame." (our kill). The runner then lifts
  anyway: a second refusal, `27 05` (lift reject), the item back in the corpse (`0x25`) and, 190 ms
  later, a `0x1D` for it. The world model loses the item, so `loot()` counts it as moved: e.g.
  13:03:58 "looted 1 item(s) from a mongbat: +0 gold, ~21 xp". The `xp` estimate also counts the
  refused corpse's gold. Gold accounting is right (measured pack gain). **Rule (user, 2026-10-03):
  loot rights go to whoever did the most damage and the corpse is theirs; looting another
  player's kill (a blue corpse) is a criminal act, blocked by default.** The corpse's notoriety in
  `0xFF` sub `0xDEAD` (u8 after the owner serial) tells it before any walk: all 35 refused corpses
  had notoriety 1 (innocent, blue), all 90 looted ones 3 (grey). The world model drops that byte
  (`WorldRuntime.mobile_death` keeps serial, corpse and name only).
- **The client's Auto Open Corpses re-fires on every re-anchor.** 212 "You may not loot this
  corpse." in the three hunt captures. In 125556 the client double-clicked 92 corpses 262 times
  (one 42 times, in bursts of 5 at 0.2–0.45 s [INFERENCE: agent walk-offs]); 195 of the 262
  came within 0.5 s after a fabricated re-anchor `0x21` with nothing in between. ANTICHEAT.md A14.
  **User decision 2026-10-03: Auto Open Corpses is turned off in the client** (the agent opens its
  own corpses), as Auto Open Doors was on 2026-10-01.
- **O'hii trees (0x0C9E, tiledata "o'hii tree") can't be chopped:** all 9 in the harvest memory
  answered 500489 "You can't use an axe on that.", 0 successes (all three 10-03 not-a-tree answers
  at witcher rune 291, Hidden Valley). Fixed 2026-10-03: `uomap.find_trees` now skips them
  (`UoMap.UNCHOPPABLE_TREES`); over (1515..1550, 3010..3045) it went 66 → 59 trees. 0xACA9
  ("tree") has 1 not_tree in memory, one sample only, so it is still listed.
- **Character creation on the wire (113853/113952, Shackleworth).** From the character list:
  C2S `ff 0014 00000008 "Shackleworth\0"` (Outlands sub 8, name check) → S2C `ff 0008 0000000f 01`
  (sub 0x0F: 01 = name accepted [INFERENCE from what followed]), then a 62-byte C2S `0x00` create
  (upstream: 104/106 B). The first try got S2C `53 05` (ClassicUO ServerErrorMessages 0x53 code 5
  "Another character from this account is currently online", cliloc 3000012): Hackworth had left
  the game 1 min before. The second went straight into the world (`0x1B`, `0x55`), and the
  template is picked afterwards in a server gump, `0xDB945D3E` (Premade Template (Beginner) /
  Custom Template (Advanced); pages of templates with stats, skills, items and a wiki button;
  button 10 "Enter Outlands With This Template", pressed three times by the user).
- **More gump ids:** `0x2F4B567E` "Shelter Island Maximum Skill Level Reached for Wrestling 80.0"
  (Guide, button 0 only); `0x19C9F0B7` "Quest Ready for Completion" ("Rune All You Like", button 1
  View Quest; closed with 0).
- **Fixes landed the same day (2026-10-03):** hunt loot rights (docs/HUNT_LOOP.md "Loot rights":
  blue corpses skipped, a refused open stops the loot, an item counts only once in the pack,
  refused kills give no kill/xp/gold sample; world model keeps the corpse's 0xDEAD notoriety and
  emits `lift_reject` for `0x27`), `0x2C` consumed, o'hii trees filtered from `find_trees`,
  `harness/captcha_mine.py`. The live proxy must be restarted to carry corpse notoriety; until then
  the runner sees none and relies on the refusal stop. `test_world_replay.py` knows Shackleworth's
  serial `0x003D701F`.
- **`harness/test_uomap.py` failed 3 checks on the new captures (fixed 2026-10-03, test only;
  uomap/nav/pathfind unchanged).** The 20 failing walked tiles and 2 positions had four causes:
  - **No facet on walked tiles.** The walk memory came from `nav.build_from_logs`, which has no
    facet, so the Shelter room (39–40, 39–42; 20260930_182751/201411) and the Outpost room
    (195,1677–1680; 20261003_145333/145743/145951) counted as facet 0. Now each session gives
    its proxy `step` rows on the facet logged before them (0xBF sub 8), or, without step rows,
    its raw-pair reconstruction on the one facet its S2C stream shows (memory.ingest's rule).
    The hard-coded Shelter-room exclusion is gone.
  - **Zero-height impassables.** The test's `blocked_at` treated them as 1 high. The client's
    CalculateNewZ (and pathfind) lets you stand on the surface they sit on, and the server
    confirmed such steps: fence 0xB2D7 at (1919,2608–2609) z0 (20261001_191355, t 1790904379)
    and tiledata-height-0 lamp posts on raised walkways, e.g. (2042,2212) z20 (20260930_123206
    and later).
  - **Houses.** (1706,3180) z 2 (20261003_101549, t 1791040854): the Cambria library stands in
    a house multi (0xF3 data_type 2, graphic 0xA2 at 1706,3176 z0). Its piece 0x070A at
    (1706,3180) gives z 2. The test now adds the surfaces of every ground item and multi piece
    seen on the facet as extra standing heights.
  - **Two positions the walk rules don't model**, left out with their evidence in the test:
    the creation spawn (1955,2625) z 6, which the server sets (Hackworth's first capture
    20260930_223009 and his 20261001_191355 login, Shackleworth's create in 113952; the
    plank tops out at 7) and which is left out only while held on the login tile. And
    (1615,1519) on the Test Shard (TestWorth, 20260930_123206, t 1790802889.636): a confirmed
    SW step into a tile the client's statics wall off (stone wall + iron fence). View-range
    item updates and a 125-move dead-reckoned chain that ends on the next server deny prove
    the position, so the server's map has no wall there [INFERENCE].

## Runebook and rune tome gumps (live 2026-10-02, TestWorth on the Test Shard)

Read from the memory-store `gump_open` events and the session capture
`logs/session_20261002_213830.jsonl` (local). Fixtures: `harness/testdata/escape_gumps.json`.
`harness/escape.py` implements both flows.

- **Runebook, gump 0x5C7DB029** (the RunUO RunebookGump). Entry i (0-based):
  - 2+6i: recall with a charge
  - 3+6i: drop the rune ("You have removed the rune.")
  - 4+6i: set default ("New default location set.")
  - 5+6i: cast Recall
  - 6+6i: cast Gate

  The default entry's set-default button is drawn with art **2360**, the others with 2361. A
  freshly filled book has no default until one is set. Lines start `"Charges: ", "<n>",
  "Max Charges: ", "10"`. A freshly marked rune is named after the region ("Prevalia"); the
  player renames it.
- **Runebook names and tiles (live 2026-10-04, Outland Dan's book, memory-store `gump_open`
  session 64; fixture `runebook_dan_dtf`).** Page 1 lists the 16 slots (croppedtext names, hue
  81 = Felucca/map 0 per RunUO's GetMapHue; unfilled slots "Empty" in hue 0). Pages 2–9 show two
  entries each, left column x≈130, right x≈290: button 2+6i (art 2103) at the column's top, the
  name (croppedtext), two `text` lines below it at y 80 and 95: latitude then longitude in
  sextant form (`"17° 8'N"`, `"162° 21'W"`), and the recall icon 5+6i (filled entries only).
  - **The text list is interned:** equal strings are sent once. Dan's 11 runes had 21 sextant
    strings, 'Shelter Stairs' reusing Khal Draco's longitude line 31, and in the TestWorth book
    both entries pointed at the one "Prevalia" name. So `escape.runebook_entries` ties each
    text to its entry by page and column in the layout, never by list order.
  - **Sextant → tile** (RunUO Sextant.Format, map 0, `escape.sextant_to_tile`): x = 1323 +
    lon·5120/360, y = 1624 + lat·4096/360, east and south positive, wrapped mod 5120 / 4096 (past
    180° east the server prints west). Degrees and minutes are truncated, so the minute's middle
    is used: a tile within one tile of the mark (round trip over the whole map in the test). Dan's
    'DTF Loot Chest' 17°8'N 162°21'W = (4134, 1429), the DTF guild-house landing; 'Prev Bank'
    8°47'N 19°53'E = (1606, 1524); 'Cambria MG' (1706, 3130).
  - Tiles carry no facet; the name's hue gives it (81 → 0, seen live; 10 → 1, 1154 → 4 from
    RunUO's GetMapHue, not seen live; Ilshenar and Malas share 1102, left unknown).
  - `escape.read_book` reads either book kind without recalling (`ctl act recall --check`) and
    `places.remember_book` keeps it per character in meta `own_books`, so `places.landings` can
    rank the own book's runes with the library rows. `recall(rune=NAME)` finds a runebook entry
    by name (case-blind) and presses 2+6i / 5+6i like the default path.
- **Rune tome, gump 0x09F5976B.**
  - **Main page** ("Manage Runes"): row i has button **100+i**, the gem, which recalls with a
    charge. Without one: "That rune tome is out of recall charges." Button **200+i** opens the
    rune's detail page. The **default row's name is drawn in hue 63**, the others in 2655.
  - **Charges:** recall charges are the "n/50" text right of the recall icon (art 2271); gate
    charges sit right of art 2291.
  - **Detail page:** runes in pairs. The left column (even rune) has buttons 10–17, the right
    (odd) 20–27: Cast Recall, Use Charge, Cast Gate, Gate charge, "Current Default Rune" / "Set as
    Default Rune", Drop Rune, Rename, Show on World Map. Text "(x, y)" gives the rune's tile. The
    numbering past two runes wasn't seen; escape.py finds the column's recall icon instead.
- **Timings, button press → arrival** (proxy clock):
  - runebook spell 2.05 s; runebook charge 2.07 s
  - tome spell 2.11–2.13 s, including the detail-page round trip; tome charge 2.13–2.15 s
  - the book's gump opens 50–70 ms after the double-click
- **A double-click within ~0.15 s of another action is refused** with cliloc 500119 ("You must
  wait to perform another action."). escape.py clicks once more after 0.6 s.
- **Mana:** a tome *charge* recall still cost 11 mana (74 → 63). The spell costs about the same.
  Runebook charge mana wasn't measured cleanly.
- **Dropping into a book:** a rune or recall scroll is dropped onto the book item. It's refused
  while that book's gump is open: "You cannot place objects in the book while viewing the
  contents." Feedback: "You add the rune to the rune tome.", "You add 2 recall charges to the rune
  tome." `ctl act drop` no longer waits for a container gump on books (`opens_as_container`).
- **Recall to the spot you stand on** gives no position jump, so escape.py can't see an arrival.
- **Test Shard sources:**
  - Runebook: Inscription 100, 25 blank + 10 arcane scrolls (stockpile cat 5: 107/112; scribe's
    pen 105; Books and Tomes 100, page 3 button 200).
  - **Rune Tome: Inscription 120** (set in the player editor, page 2 entry 110), 50 blank + 25
    arcane, same menu button 201. It's item graphic 0x71AF, tiledata name "runetome".
  - Reagents: shelf buttons 130/131/132.
  - Blank runes and recall scrolls: Garrett the mage (20 / 200 gp).

## Guard zone notices (live 2026-10-02, TestWorth in Prevalia, Test Shard, session 20261002_213830)

- Clilocs **500112** "You are now under the protection of the town guards." / **500113** "You
  have left the protection of the town guards." (System, hue 946). They are the only signal for
  guard zones: the client data has no zone boundaries.
- **They lag and skip crossings.** Times are seconds after 1790998000, from the store's `events`:
  - 674.130: step (1481,1513)→(1481,1512), its `walk_confirm`, then the 500113 in the same ms.
    This is the clean case.
  - Flight in: steps onto (1478,1498) at 703.252 and (1477,1497) at 703.466, no notice. Then
    (1475,1495) at 705.351; the 500112 came with the *next* walk's ack, a turn, at 705.558.
  - Out again: steps (1475,1495)→(1478,1498) at 803–810 with 2.5 s pauses, no notice. Step onto
    (1479,1499) at 829.199, no notice. The 500113 came at 839.153 with a turn at (1479,1499),
    3.3 s after the second step onto that tile.
  - Then 14 more out/in crossings (1478,1498)↔(1479,1499) over 95 s and a walk south into the
    zone at (1483,1515) (where earlier sessions got a 500112): no notice for ~5 min.
  - The store as a whole: of 159 notices, 97 came ≥ 3 s after the last step and 69 rode on a
    non-moving walk confirm (a turn or deny).
  - [INFERENCE] The server evaluates and/or announces the zone on its own schedule, possibly
    rate-limited; the rule wasn't pinned.
- **Consequences (harness/guards.py):**
  - A notice only says which side we're on when it arrives, so only enter notices become
    learned points, at the tile we stand on.
  - A flight can't wait for a notice: arrival at a learned point counts as safe.
- Prevalia has two guarded areas with an unguarded band between them along x≈1475–1485,
  y≈1498–1513. Enter points: (1474,1495), (1475,1495) north; (1483,1515), (1484,1515) south.

## Test Shard

- Test-only commands: `[TestRes` (res self+followers), `[TestIgnoreMaxDamageCap`, `[TestMaxMeleeDamageRolls`, `[TestMaxSpellDamageRolls`, `[TestBlessedGear`, `[Go` (warp self+followers). Use these for fast harness iteration.
- Character creation offers skill templates (sets skills to 60) — handy for capability unlocks.
- **Client requires elevation (2026-09-28).** Launching `ClassicUO.exe` from a medium-integrity shell crashes in `UOFileManager.Load` with `Access to the path '<install>\animdata.def' is denied` (Event Viewer 1026/BEX64, fault offset 0x806816). Root cause: the Outlands loader opens game files in a way that needs write capability (their world-data/UltimaLive infrastructure writes into the install dir; upstream already has `OpenOrCreate, ReadWrite` opens), but `Program Files (x86)` grants `BUILTIN\Users` only ReadAndExecute — so a non-elevated client dies on the first file. The launcher (`Outlands.exe`) elevates via UAC for exactly this reason; the client inherits the high integrity level from it. **Always launch the client elevated**: via the launcher, or `Start-Process ClassicUO.exe -Verb RunAs`. Note: upstream `-ip`/`-port` CLI args exist but the Outlands fork ignores them for the game connection (the address comes from the login response). Not a lock/ACL/mandatory-label issue (verified with handle.exe + icacls).
- **Traffic interception: SOLVED LIVE (2026-09-28, see docs/INTERCEPTION.md).** The game server address is a literal IP from the HTTPS login response (`play.uooutlands.com` doesn't exist in DNS; `-ip`/`-port` args and `settings.json` are ignored for the game connection). Interception = WinDivert user-space NAT (`harness/divert_nat.py`, elevated): full src+dst loopback rewrite (dst-only gets martian-dropped), return rule rewrites proxy src back to the server IP. Proxy upstream binds ports 25940–25960 with retry loop (TIME_WAIT → WinError 52 on fixed port). Runbook in INTERCEPTION.md §Operation.
## Razor scripting surface (copilot mode, Phase 5)
- Outlands fork of Razor CE; extended script engine: `findtype`/`dclicktype`/`targettype`/`lifttype` by name-or-graphic with hue/src/qty/range, `findtypelist`, `findlayer`, `targetexists`, `followers`, `hue`, `name`, `paralyzed`, `invul`, `warmode`, `noto`, `dead`, `maxweight`/`diffweight`/`diffhits`/`diffmana`/`diffstam`, `counttype`, `gumpexists`, `ingump`, `{{var}}` interpolation, loop `index` variable, `ground` alias. Docs: wiki.uooutlands.com/Razor_Scripting.
- Scripts live in `ClassicUO/Data/Plugins/Assistant/Scripts/` (empty at research time). Macros in `Macros/`, per-char profiles in `Profiles/`.

## Razor profiles and hotkeys (verified 2026-10-11; the viz Razor page, docs/VISUALIZER.md §2.15)

Dir: `ClassicUO/Data/Plugins/Assistant/` = `Profiles/*.xml` + `chars.lst`, `Scripts/**/*.razor`,
`Macros/**/*.macro`, `Language/Razor_lang.enu`, `spells.def`, `outlandscommands.def`.
- **Profile XML**: UTF-8 with BOM, CRLF, tab indent. One `<hotkeys>` block of
  `<key mod="M" key="VK" send="True|False">NAME</key>`, or `<hotkeys />` when empty; an empty block
  can also be `<hotkeys>` CRLF `</hotkeys>` (Logan Wolf Tribard Tamer.xml), which harness/razor.py
  rewrites as `<hotkeys />` on a save. Profiles also hold `<passwords>`: never return or log raw
  profile XML.
- `mod` flags Alt=1, Ctrl=2, Shift=4. `key` = Win32 VK; mouse -1 Wheel Up, -2 Wheel Down, -3 Middle,
  -4 X Button 1, -5 X Button 2 (left/right click can't be bound; mouse hotkeys ignore `send`).
  `send` = also pass the key to the client.
- NAME = `L:<id>` (a Razor_lang id or a client cliloc) or a plain string. One combo per action;
  on load Razor silently drops names it doesn't register, and when two actions share a combo the
  first wins.
- Razor reads a profile only when it loads it and rewrites the whole file from memory on
  disconnect, client exit and profile switch: an external edit to a loaded profile is lost. So the
  viz locks the profile of every logged-in character (chars.lst `0xSERIAL=Profile`, serial without
  zero padding, matched to `/api/sessions`), and every profile when it can't tell (more
  `ClassicUO.exe` processes than proxy sessions, or no tasklist).
- **Magery spell names**: `L:<3002010 + (circle - 1) * 8 + n>`, n = spells.def SpellNo, which runs
  1-8 *within* each circle (not 1-64; upstream `Spell.GetID`). Checked against Cliloc.enu: 3002021
  Cure (circle 2 n 3), 3002040 Lightning, 3002063 Mana Vampire, 3002074 Summon Water Elemental.
- Skills: `L:<1044060 + skill index>` for skills whose skills.mul action flag is set (24 on this
  install, `uomap.action_skills`). Outlands commands: `[Command | SubMenu | LangId` →
  `L:<LangId>`, subcategory `lang[1249 + SubMenu]` (1000 → 2249 Menus).
- Dynamic names: `Play Script: <rel>`, `Play: <rel>` (rel = path under Scripts/Macros without the
  extension, `\` separators), `Dress: X` / `Undress: X` / `Toggle: X` per `<dresslists><list name>`,
  `Organizer Agent-NN` / `Set Organizer HotBag-NN` and `Restock Agent-NN` / `Set Restock HotBag-NN`
  per `<agents>` `Organizer-N` / `Restock-N` child.
- **Permissions**: Profiles has `BUILTIN\Users:(RX)` only, so a non-elevated Python can't write it.
  `grant_razor_profiles.ps1` (run once, elevated) gives the user Modify on that folder only
  (user-approved exception to AGENTS.md Constraint 1, 2026-10-11).

## NativeAOT analysis methodology (worked well)

- String triage: extract ASCII + UTF-16 separately (`extract_strings.py`); AC-relevant names are ASCII in the metadata heap, user-facing messages are UTF-16.
- Metadata-heap names have **zero code xrefs** in Ghidra (referenced by index) — don't chase them; instead:
  - Check **IAT xrefs on Win32 API name strings** to distinguish real imports (`GetAsyncKeyState`, `SetWindowsHookEx`, `EnumWindows`) from dead name-table entries (`K32EnumProcesses`, `K32EnumProcessModulesEx`, `IsDebuggerPresent` — not imported).
  - Cross-check names against upstream source trees (ClassicUO `main`, Razor CE `master`) — instantly resolves upstream features (e.g. UltimaLive) vs Outlands-proprietary additions.

## Ghidra headless gotchas

- Project-location directory **must exist beforehand** (`Directory not found` otherwise).
- The `-import` path must contain **no spaces/parens** — copy the binary to a clean path first (laptop: `C:\Users\chris\uo-harness\ClassicUO.exe`). The desktop's repo path (`C:\Users\Chris Kite\uo-harness`) has a space, so there the copy must go outside the repo (e.g. `C:\re\ClassicUO.exe`). The `ghidra_scripts/*.java` read/write repo files relative to their own location (`getSourceFile()`), so the repo path itself may contain spaces.
- `getReferencesTo()` returns `ReferenceIterator`, not an array.
- Import+analysis of this 67 MB binary: ~30–40 min on this machine. Rerunning only scripts: `-process <file> -noanalysis`.
- Invocation: `analyzeHeadless.bat <projdir> <projname> -import <clean-path.exe> -scriptPath <dir> -postScript <Script.java> -analysisTimeoutPerFile 7200 -max-cpu 8`

## Windows shell / tooling gotchas (this machine)

- The agent shell is git-bash-like: **backslashes in unquoted paths get eaten** (use forward slashes or quote), `$_` gets expanded (breaks inline PowerShell — use `.ps1` files), `timeout` is GNU (no `/t`), `copy` doesn't exist (use `cp`).
- The agent shell starts programs through Windows directly: **a shebang script fails** (`./x`: "%1 is not a valid Win32 application"), and an extensionless name doesn't resolve to `x.cmd`. `.cmd` files run (`./ctl.cmd status`; quotes, `&` and exit codes pass through), and so does `bash x`.
- **Short commands:** plain `python` on this shell's PATH is the Python 3.13 per-user install (`sys.executable` = `%LOCALAPPDATA%\Programs\Python\Python313\python.exe`), so the full path is unnecessary. **JSON: call `jq.exe`** (real jq 1.8.2, `winget install jqlang.jq`): the agent shell's builtin `jq` is jaq 2.3, whose `.a.b` errors with "cannot use null as iterable (array or object)" when `.a` is missing or null (jq gives null), killing the whole filter, e.g. `{pos, two: .equipment.two_handed.name}` on `ctl status` with nothing in that hand (2026-10-04). `./ctl.cmd status | jq.exe -c '{pos, hits}'`.
- **The agent shell's PATH is fixed at session start** (desktop setup, 2026-10-04): tools installed mid-session (winget Python, Bun) aren't found until omp restarts. Workaround inside a command: `export PATH="C:\\Users\\<you>\\AppData\\Local\\Programs\\Python\\Python313;$PATH"; python ...` — PATH here is Windows-format (`;`-separated, `C:\` paths; a `/c/...` entry or `:` separator doesn't work), the export lasts for that one command only, and async (background) jobs don't see it, so give them the full `C:/...` interpreter path.
- `Outlands.exe` **elevates (UAC)** — background launches hang on hidden UAC prompts; use `launch_game.ps1` and have the user accept the prompt.
- **Never write a self-elevating `.ps1` without a one-shot guard** (2026-10-03). `allow_viz_lan.ps1` first relaunched itself with `Start-Process -Verb RunAs` whenever `IsInRole(Administrator)` was false; the elevated child still saw false, so it spawned itself in a loop and flooded the desktop with PowerShell windows. A non-elevated process can't kill elevated ones either. Admin-only scripts now just refuse to run unless the shell is already elevated, and the user runs them from an admin PowerShell.
- **Admin check: `IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)`, never `IsInRole("Administrator")`** (cause of the loop above, found 2026-10-05). The string overload looks up a group by that literal name, and the group is `BUILTIN\Administrators`, so it is false even when elevated. Measured in one elevated PowerShell on the desktop: `'Administrator'` False, `'BUILTIN\Administrators'` True, the enum True. The refusing version of `allow_viz_lan.ps1` therefore refused in an admin shell too, until the fix.
- **The viz kept "dying" (2026-10-03, root cause NOT confirmed).** Five deaths in about 8 hours, with exit codes 127 and 1 and no Python traceback; the job output was only `ConnectionResetError` noise from dropped keep-alive sockets (phone, headless browser). What the evidence shows: (1) the supervised job pid is the outer `bash -l -c` wrapper, with python a grandchild three bashes down, so an exit 127/1 from the job is the wrapper, not python (once, python 39708 kept serving after its wrapper had been reported failed); (2) no `Application Error`/WER entry for python.exe in 30 h, so no native crash was recorded, and no reboot (boot 09-28); (3) nothing in `harness/` kills viz (`task_wrap.py` terminates only identity-checked task pids); (4) the job was `persist=false`, i.e. tied to the omp session. Leading hypothesis `[INFERENCE]`: the session supervisor kills the wrapper (and sometimes python with it) when the omp session is restarted or resumed. Changes made so the next death is diagnosable: run as `python -u -X faulthandler ... 2>&1 | tee -a logs/viz_server.log` plus a `[wrapper] viz exited rc=... at ...` line, set the job to `proc://<id>/mode` = `detached` (note: **writing the mode respawns the job**), and `VizServer.handle_error` drops `ConnectionError` tracebacks so real errors stay visible. If it dies again, `logs/viz_server.log` says whether python exited itself (rc, faulthandler dump) or was killed from outside (no wrapper line).
- **Update 2026-10-04: the viz death is a native crash in the live view (confirmed by `faulthandler`).** `logs/viz_server.log` after 13 h 53 m up: `Windows fatal exception: access violation`, wrapper `rc=139`, the only thread in native code was `windows_capture/__init__.py:250 start_free_threaded` ← `liveview.LiveCapture._start` ← `latest` ← `viz_server._live_stream` (a viewer opened the live view). No Python traceback is possible for that, which is why the earlier deaths looked silent; exit codes 127/1 were the supervisor wrapper. The user had closed and reopened the game between the capture starting and the crash. Not reproduced: 60 rapid start/stop cycles and 10 idle-stop cycles on the real game window, and 8 close/reopen cycles of a throwaway tk window polled at 10 Hz (windows-capture 2.0.1, Python 3.13) all ran clean. `[INFERENCE]` trigger: a new `start_free_threaded` racing the teardown of the capture on the closed window. Fix: the native capture now runs in a helper process (`python harness/liveview.py --helper HWND FPS`, BGR crops on its stdout, EOF on its stdin = stop), so a crash costs only the helper (`window capture helper crashed (exit 0x…)`, restart after 5 s). Side effect, also a bug: after the game closed, the old code kept serving the last frame of the dead window; now the viewer gets "no visible window" and the next poll captures the new window. Checked: helper hard-killed mid-stream (server survived, recovered in 5.5 s), real window start/stop, `/api/live.jpg` through the server, idle stop after 15 s. No permanent regression test for the crash itself (needs a desktop window and the native library).
- winget works but: source update may fail (harmless), some installers block on UAC, 900 s job timeout can kill slow installs mid-flight.
- `ssh-keygen -N '""'` in this shell sets a literal `""` passphrase — use `-N ''`.
- `ls -l` / directory listings show **stale sizes (even 0 B) for capture files the live proxy still has open**. NTFS updates the directory entry lazily. Use `wc -c` or open the file to measure it (seen on session_20260929_204225 while it was live).
- Git remote: SSH host alias `github.com-uoharness` in `~/.ssh/config` pins deploy key `~/.ssh/uo_harness_deploy` (repo: `chriskite/uo-harness`).
- **Harness memory store (docs/MEMORY.md):** `harness/data/harness.db` is SQLite in WAL mode, which puts `-wal`/`-shm` files next to it (gitignored). The proxy commits every 0.25 s or 500 rows. `Popen.terminate()` on Windows is TerminateProcess, so the proxy's shutdown `flush()` doesn't run, and the last ≤0.25 s of events can be lost. That's why the e2e tests sleep 1 s before reading the store and stop the proxy only afterwards. Ctrl-C and a normal exit do flush. Ingesting three captures (≈29k events) takes about 3.6 s.
- **Viz screenshots: Playwright on the installed Edge, not `msedge --headless --screenshot` (2026-10-20).**
  `msedge --headless=old --screenshot` shoots at page load, before `/api/jobs` answers ("loading job
  analytics…"), and adding `--virtual-time-budget` never returns: the viz's SSE stream and 15 s polls keep
  virtual time from settling (killed at 90 s). Python Playwright is installed: `p.chromium.launch(channel="msedge",
  headless=True)`, `page.get_by_text("…").wait_for()` on the text that proves the data came, then
  `page.screenshot` (or `locator("section.mining-job").screenshot` for one panel) and a `pageerror` listener.
  - Run the script from its own folder, not straight from `%TEMP%`: a stray `%TEMP%\dis.py` there shadows the
    stdlib `dis` module (the script's folder comes first on `sys.path`) and `import playwright` dies in `inspect`.
  - For the Live page's job panes with no run going, replay a capture that has one: `viz_server.py --replay
    20261020_102748 --rate 10 --port 8097 --no-facet --no-live` shows the Mining job pane ~15 s in (replays
    feed the logged `agent_intent` rows; the jobs and plan routes read the live store).
- **Game-window screenshots (`harness/screen.py`, `ctl screenshot`):** these use Windows
  Graphics Capture via the `windows-capture` package (PyPI 2.0.1; `pip install --user`; it pulls
  in numpy and opencv-python). A plain desktop BitBlt only sees what is on top: the game window
  was 100% covered by the viz and the terminal on this machine, and the capture showed those.
  WGC gets the window's own content, including the elevated ClassicUO window. The game window
  caption is `UO - <character> - <version>`, with a 2560×1494 client area here. Frames cover the
  DWM frame bounds, title bar included, so screen.py crops to the client area.
- **`test_ctl.py` speed (2026-10-04): 120 s → ~21 s.** Profiling showed ~90 % of the time was
  ctl's own fixed waits (1.5 s listening after each `act` send, 4 s for a cast cursor, 3 s move
  confirmations), not Python startup (~0.15 s per call, ~25 s total). The test now runs
  `ctl.main(argv)` in-process (stdout captured, exit code returned) and swaps the `time` module
  of ctl, agent_link, humanize and tracking for a 10× clock (`FastClock`; all four share it
  because they pass monotonic stamps to each other, e.g. `Human.pace_step(sent_at)`; wall-clock
  `time()` untouched). `stop` keeps the real clock: its grace waits on the task wrapper, a real
  process. The `wait` wake tests still spawn the real `python ctl.py`, so the CLI entry stays
  covered. Production ctl is unchanged.
- **`test_loop_lumber.py` speed (2026-10-04): ~10 min → ~2 min.** Its 15 end-to-end scenarios
  each run a real proxy, the simulated server and the runner in real time (proxy step floors, the
  sim's 2.1 s recalls and lockouts), so a fake clock as in test_ctl would have to reach three
  processes. They already had private ports and temp dirs, so the file now runs them as parallel
  child processes (`LOOP_TEST_JOBS`, default 8; 32 cores here) and the unit checks inline. Per
  scenario 3–73 s; `main` (114 s) is the critical path: ~25 s of it is trip 1 learning the town
  wall from walk denials (`--no-map`, by design). Also fixed then: the simulator's teleport `0x20`
  sent direction 0 while it kept its own facing, so after a recall a step the proxy meant as a
  turn moved the simulated character (a one-tile desync, seen as tracking/gazer distances off by
  one once trips began with a room exit); it now sends its facing like a real server.
- **Whole-suite speed (2026-10-08): ~30 min serial → ~105 s with `python harness/run_tests.py`.**
  Serial times on the desktop before (idle, HEAD 08c436f): test_captcha 1008 s, test_loop_hunt 209,
  test_loop_lumber 201, test_world 76 (= units + replay), test_world_replay 72, test_travel_guard 58,
  test_pathfind 42, test_mover 41, test_nav 39, test_ctl 28. Each check line kept its verdict
  (compared file by file against the HEAD outputs; only temp paths, ports, timestamps, measured
  seconds and printed-set order differ, as they do between two HEAD runs). What each fix was:
  - **captcha.py: 0.36 s → ~14 ms per `read_digit`.** `_costs` scores every reference and every
    alignment shift at once in numpy; only the references and shifts within float noise (1e-9
    relative) of the minimum are rescored with the original Python arithmetic (`_shift_cost`), so
    the distances, digits and margins are bit-identical (checked on all 2,824 `read_digit` calls
    the test makes, held-out fonts included). The live solve speeds up the same way.
  - **uo/s2c.py: `S2CStream.feed` ~4× faster.** It walked the Huffman tree bit by bit, and
    `del pend[:pos]` after every packet made a whole-file feed quadratic. Now one de-XORed byte at
    a time through a lazily filled (node, byte) → (symbols, flush, node) table, and the consumed
    bytes are dropped once per feed. Same (wire, packet) pairs and `buffered` on all 78 captures,
    whole and in random chunks.
  - **world/state.py, parsers.py, replay.py:** `prune_range` without the per-entity closure,
    struct-cached fixed layouts, `bytes.translate` for the C2S XOR (identical states over 76
    captures). test_world_replay, nav.build_from_logs and test_uomap fan the captures out to worker
    processes (results merged in file order, so set order is unchanged).
  - **test_mover / test_travel_guard / test_ctl:** a skip-ahead clock (`VirtualClock`,
    `FastClock`): `time.sleep` returns at once and moves `monotonic()` forward, so pacing gaps and
    time windows see the same elapsed time. Nothing they test sleeps in another thread.
  - **test_loop_lumber (all 30 scenarios at once, `LOOP_TEST_JOBS` default = CPU count) and
    test_loop_hunt (its 5 proxy scenarios as parallel children):** the scenarios are sleep-bound
    real-time runs, so the file takes about as long as its longest scenario (`main` ~100 s, crawl
    ~50 s).
  - **Free ports and temp log dirs in every test** (test_errand and test_agent_gate shared
    12611-12613; test_movement and test_proxy shared 12593/12594 and `logs_test`), and connect
    retries instead of fixed 1 s startup sleeps. A passing run deletes its temp log dir; a failing
    one prints the path.
- **The suite oversubscribing the CPU breaks timing checks.** With the log-scanning tests each
  spawning up to 32 workers at suite start, `test_ctl`'s "heartbeat advances on every poll" (0.4 s
  for a spawned `ctl wait --poll 0.1`) failed in 1 of 2 whole-suite runs and 1 of 3 runs next to
  those tests; with the pools capped at `UO_TEST_WORKERS` (run_tests.py sets CPU count // jobs) it
  passed 4 of 4 next to them. HEAD's test_ctl fails the same check in 8 of 20 copies run at once.
  Then `test_movement`'s 0.25–0.3 s pacing windows failed once in 3 suite runs ("walk gated:
  pacing"): test_loop_lumber started its 30 scenarios (each a proxy + runner) at once, and total CPU
  read 98 / 88 / 70 % for the suite's first seconds. Its first wave now launches 0.25 s apart
  (`LAUNCH_GAP_S`; peak 76 %, the file still ~100 s); 5 of 5 suite runs after that were clean.
- **Windows loopback connects:** `socket.create_connection(..., timeout=x)` to 127.0.0.1 costs
  ~7–10 ms (non-blocking connect + select), a blocking connect ~0.15 ms; a connect to a closed
  local port is refused only after ~2 s of SYN retries. test_ctl starts its unreachable-proxy CLI
  check first so those 2 s overlap other sections.
- **Two logical CPUs left to the game (2026-10-17, user: "We need to reduce the number of cores used when testing
  by 2, it's lagging the game pretty badly").** `harness/cpu_budget.py`: `limit()` sets the process's CPU affinity
  to every logical CPU but the first two (0 and 1: on this 16-core/32-thread desktop, one physical core), and child
  processes inherit it (Windows `SetProcessAffinityMask`; checked: a spawned child read mask 0xfffffffc, 30 of
  32). Called by run_tests.py, sim/run.py (batches), test_loop_hunt, test_loop_lumber, test_nav, test_pathfind,
  test_uomap and test_world_replay; pools size from `workers()` (CPU count - 2): run_tests' jobs and
  `UO_TEST_WORKERS`, nav.build_from_logs, the replay tests, `LOOP_TEST_JOBS` (still at least 8: sleep-bound), and
  sim/run.py's `--jobs` (CPU count - 3). The simulator's default careers stay CPU count - 1 (`DEFAULT_CAREERS`),
  so a batch's draws match the earlier ones whatever `--jobs` is. Live runners never call it. With 30 CPUs the
  suite failed test_loop_hunt twice in a row on "after re-entering, the potion ... was refused": the runner killed
  D (`--kills 2`, the run's end) before the scripted client drink at the last drink + 10.5 s, a race the
  hotkey-paced casts had made likelier; the fake world now keeps D alive until that drink. The suite run after:
  test_loop_hunt ok; test_uomap, test_pathfind and test_world_replay fail as before the change (client data).
- **Simulator speed (2026-10-28, docs/SIM.md "Speed"): 1.61x less CPU a run from two caches.** Profile a run with
  cProfile around `sim.run.run_once` in one process (no pool) and group self time by file; compare CPU time
  (`time.process_time`), not wall time: with other batches on the desktop the same run's wall time varied 24-36 s.
  Check a speed change for exactness by comparing whole run summaries (minus `wall_s`/`speed_x`) on fixed seeds:
  the simulator is deterministic per seed. test_pathfind's "all N server-confirmed moves are walkable" fails with 36
  moves near (3957, 516) before and after the change (the same list); it isn't the caches.

## Backups (NAS, since 2026-10-01)

- **What:** `harness/backup.py` copies the gitignored data that can't be recreated easily to `\\STARGAZER\files\uo-harness` (mapped as `F:` interactively): gzipped snapshots of `harness/data/harness.db` (`db/`) and, since 2026-10-03, of the Discord capture `harness/data/discord.db` (`discord/`, same snapshot method and retention, skipped while it doesn't exist), the Discord images `harness/data/discord_media/` (`discord_media/`, additive, skipped while absent), `logs/` (session captures, screens, overseer task logs), repo-root `*.pcapng`/`*.etl`/`*.png`/`divert.log` (`artifacts/`) and the 1.4 GB Ghidra project (`ghidra/`). The module docstring lists what is deliberately left out (git-tracked files, `logs_test*/`, downloads, regenerable dumps, credentials, the Discord browser profile).
- **Schedule:** Task Scheduler task `uo-harness backup`, hourly, registered by `register_backup_task.ps1` (re-run it to change anything). It runs `pythonw.exe` (no console window over the game) as the current user, non-elevated, only while logged on, so no password is stored.
- **A computer without `ghidra/` (the desktop):** the tree used to be required, so robocopy's "source not found" made every run `FAIL` (desktop, 2026-10-04). It's now skipped while absent like `discord_media/`; that also keeps `/MIR` from ever mirroring an empty local dir over the share's project.
- **Forum KB (since 2026-10-08):** `harness/data/forum_kb.db` is snapshotted like `discord_kb.db` into `forum_kb/` (`forumkb-*.db.gz`), from whichever computer has it, skipped while absent. The capture it reads, `outlands_web.db`, is not backed up: `outlands_web.py crawl all` rebuilds it, and `web_kb.py sync` adopts its existing entries by their `web-kb:` ref when its state is gone.
- **Mapped drives are per logon session**, so a scheduled task doesn't reliably see `F:`. The script uses the UNC path, which works through the logon session's SMB connection (verified: `schtasks /run` → Last Result 0, snapshot written).
- **DB snapshot:** SQLite online backup API from a `mode=ro` connection (never checkpoints or writes the live store; rows still in the WAL are included), switched to rollback-journal mode so the file stands alone, `PRAGMA quick_check` before shipping, then written as `.part` and renamed. Backups of an unchanged store are byte-identical, so a sha256 in `db/latest.json` skips duplicates. Retention: every snapshot from the last 48 h, then the newest per day, kept indefinitely (~6.5 MB each; 38 MB raw). Restore: stop the proxy, gunzip to `harness/data/harness.db`, delete stale `-wal`/`-shm`.
- **Restore drill (2026-10-01, from the share, into a scratch dir):** all 3 snapshots gunzipped; the latest matched the sha256 recorded at backup time; the documented procedure (gunzip over a stale DB, delete stale `-wal`/`-shm`) gave `PRAGMA integrity_check` ok, schema v4. `memory.py --db <restored> stats`, `Memory.walk_memory(0)` (4774 tiles) and `Knowledge.search` (FTS) all worked on it, and all 11 tables matched the live store row-for-row (247,502 events). Note: the harness's `memory.connect` switches a restored file back to WAL on first open; that's expected.
- **Copies** are robocopy (size+time incremental, `/FFT` for NAS timestamp granularity). `logs/` and `artifacts/` are additive (local deletions stay on the share). `ghidra/` is `/MIR` so the copy stays one consistent project. First full run took 35 s on the LAN, then ~5–10 s.
- **Status:** `F:/uo-harness/last_backup.json` (last run, per-part result) and `logs/backup.log` (one line per run, `OK`/`FAIL`). Exit code 1 if any part failed; the other parts still run.
- Tests: `python harness/test_backup.py` (retention plan, WAL rows in the snapshot, restore, dedupe).
- **Two computers:** the `harness.db` snapshot runs only on the computer that holds the store (`dbhandoff.not_held_here`; `last_backup.json` shows `"skipped": "held by …"` otherwise). Once the handoff is in use, a computer that pushed and hasn't pulled back doesn't back the store up. Without that rule, a laptop waking from sleep would immediately run its missed backup (the task has `-StartWhenAvailable`), and its stale store would land as the newest `db/` snapshot. Before the first push, nothing changes.
- **Discord DBs ship only from the capture computer (since 2026-10-08):** `discord.db` and `discord_kb.db` are snapshotted only where `harness/data/discord_profile/` (the Discord login, which only the capture computer has) exists; elsewhere `last_backup.json` shows `"skipped": "not the Discord capture computer"`. Reason: the desktop now keeps a read-only restored copy for Nystul's `uo_discord`; once the laptop captures more, the desktop's older copy would otherwise land as the newest snapshot. Verified with a real run on the desktop 2026-10-08 (both parts skipped) and `test_backup.py`.
- **A read-only Discord copy on another computer (the desktop, 2026-10-08):** restore `discord/<latest>.db.gz` → `harness/data/discord.db` and `discord_kb/<latest>.db.gz` → `harness/data/discord_kb.db`, then `python harness/discord_search.py index` builds `discord_vec.db` (the main Python has fastembed; 19,457 chunks of 97,724 messages took 8.9 s on the GPU). The `sha256` in `latest.json` is of the uncompressed DB, not the `.gz`. git-bash here has no `gunzip`; use Python's `gzip`. The copy is frozen at its snapshot (2026-10-04 16:00 for the first restore): repeat the restore to refresh it, and never run `discord_capture.py`/`discord_kb.py` on that computer.
- **`models/` (since 2026-10-04):** the fine-tuned checkpoints (`models/laya-triage`, the deployed Laya triage model) are copied to `models/` on the share with robocopy `/E /XO`: additive, and never an older file over a newer one, so a computer that hasn't pulled the newest checkpoint can't regress the backup. Skipped while `models/` doesn't exist.

## Two computers (memory store handoff, since 2026-10-04)

- **What:** `harness/dbhandoff.py status|push|pull` moves `harness/data/harness.db` between computers through `\\STARGAZER\files\uo-harness\handoff\`. Setup of a second computer: `SETUP.md`. Decision: docs/PLAN.md "Two computers: hand the memory store off".
- **Leaving:** stop everything that has the store open, then run `push`. **Arriving:** run `pull`. `status` tells which of the two is due. The first push (no `owner.json`) creates generation 1.
- **Writer check:** `PRAGMA journal_mode=DELETE` on a WAL store succeeds only for the sole connection. Verified 2026-10-04 with a second process holding an idle connection, plain or `mode=ro`: "database is locked". After that process was killed, the switch succeeded and removed `-wal`/`-shm`. So `push` refuses while the proxy, a runner, the viz or the Telegram bridge has the store open. The pushed store is left in rollback-journal mode; `memory.connect` puts it back in WAL on the next open.
- **Fork check:** this computer's last push or pull is kept in `harness/data/handoff.json` (gitignored), with a content fingerprint: per table, the row count and a sha256 over its rows in rowid order. FTS shadow tables and `knowledge_vec` are left out because they're derived. Fingerprinting the live store (1.15M events, 179 MB) took 1.9 s on 2026-10-04. Opening the store with `memory.connect` doesn't change the fingerprint. Anything that writes rows does: a proxy session, `ctl know search` (access counts), the overseer heartbeat. So run nothing on a computer that doesn't hold the store.
- **Share layout:** `owner.json` holds `gen`, `file`, `sha256` (of the uncompressed store), `fingerprint`, `holder` (null while released), `pushed_by`/`pushed_t` and `pulled_t`. Next to it is `harness-genNNNN.db.gz`, the newest generation only; history stays in `db/`, and every push also writes a `db/` snapshot. `owner.json` is written last, so a push that dies halfway leaves the previous generation valid.
- **Logs:** `push` copies `logs/` to the share the same way the backup does. `pull` copies the share's `logs/` into `logs/` with robocopy `/XO` (only files newer than ours) and leaves out each computer's own `backup.log`.
- **Recovery:** if the holder died without pushing, run `pull --force` on the other computer. It gets the last push; the dead holder's newer hourly `db/` snapshot can be restored by hand (see "Backups") and then pushed with `push --force`. `pull --discard-local` resolves a fork in favour of the share and keeps the local store as `harness/data/harness.db.prev`. `push --force` resolves it in favour of this computer.
- **Telegram config travels too (user request 2026-10-04):** `push` uploads `harness/data/telegram.json` (bot token, paired chat) to `handoff/telegram.json` when this computer has one; otherwise the share keeps the last one. Its sha256 goes into `owner.json`. `pull` verifies it and installs it; a different local file is kept as `telegram.json.prev` (gitignored). So the bridge runs on the holder, and since `push` needs the bridge stopped (it has the store open), there's never a second poller on the token. The token therefore sits on the NAS share in plain text, the one credential there; `backup.py` still doesn't copy it. The CLI prints only `installed`/`unchanged`, never the content. The `telegram_*` cursors are `meta` rows, so they travel with the store.
- **The Laya checkpoint travels too (user request 2026-10-04):** `triage.py serve` defaults to the fine-tuned `models/laya-triage` (`triage.CHECKPOINT`), which isn't in git, so a computer without it couldn't serve v2. `push` uploads the folder to `handoff/model/` only when its content changed: a manifest of every file's size and sha256, hashed, goes into `owner.json` as `model_sha256` + `model_files`. That also happens on a push that only releases the store, so a retrain travels. `pull` installs it before the store: it copies into `<checkpoint>.pull.part`, checks every file against the manifest, then swaps it in and keeps a different local checkpoint as `<checkpoint>.prev` (843 MB). A mismatch raises and installs nothing. If a running laya-serve holds the files, the swap is refused before anything else changes: stop it and pull again. Output: `"model": "uploaded (846 MB)" | "unchanged" | "installed" | "not on the share" | "no local checkpoint"`; `status` shows whether the share's checkpoint matches this computer's. Skipped entirely while `triage.CHECKPOINT` is `stock`. Measured 2026-10-04 with the real checkpoint (local disk, not the NAS): upload 2.0 s, unchanged re-push 0.8 s (the manifest hash), install 1.4 s. Over the network it is bounded by the link: about 7 s at gigabit [INFERENCE].
- **Not carried:** `discord*.db`, `discord_media/`, `settings.json`, the venvs, the Laya training data (`harness/data/triage/`; rebuild per "Laya speech triage").
- **The desktop (host `Titan`, user `Chris Kite`; the laptop is `protostar`, user `chris`), set up 2026-10-04:** RTX 4090 (sm_89, driver 595.97); the same CUDA 13 wheels work (`ctl know search` → `recall: hybrid (cuda)`). First `pull` refused with "this store has unsynced changes": an empty `harness.db` (all `memory.py stats` counts 0) already existed, created by a harness command run before the pull [INFERENCE: the first `./ctl.cmd status`, same minute]. Check `memory.py stats` is all zero, then `pull --discard-local` (the empty store stays as `harness.db.prev`). Don't run harness commands before the first pull on a new computer.
- Tests: `python harness/test_dbhandoff.py` (writer refusal, WAL rows travel, holder rules, unchanged push only releases, fork refusal and `--discard-local`, derived tables ignored, backup gating, `telegram.json` install/keep/fill-in, checkpoint upload-when-changed/install/`.prev`/corrupt copy refused).

## Stack supervisor (since 2026-10-04)

- **What:** `python harness/stack.py up` (in a console; Ctrl-C there = `down`) brings up and keeps up the proxy, the viz (`--live`), laya (`triage.py serve`) and the Telegram bridge, and watches the divert NAT. `status`, `restart <service>` and `down` from any other shell talk to it through files in `logs/stack/` (`state.json` written every second, `stop`, `restart-<name>`). Per-service output: `logs/stack/<name>.log`; the supervisor's own: `logs/stack/stack.log`. Decision: docs/PLAN.md "One supervisor for the stack".
- **Flags:** `--no-viz`, `--no-laya`, `--no-bridge`, `--no-nat`, `--viz-host H` (default `0.0.0.0` since 2026-10-05, user request: the stack's viz is for the LAN; `127.0.0.1` keeps it local). The bridge is on by default and is skipped (re-checked every minute) while no bot token is configured.
- **LAN reach needs three things (2026-10-05, desktop):** the viz bound to `0.0.0.0` (before 2026-10-05 the stack passed no `--host`, so viz_server's own default `127.0.0.1` applied); the firewall rule from `allow_viz_lan.ps1` (it wasn't on the desktop; from an Administrator PowerShell run `powershell -ExecutionPolicy Bypass -File .\allow_viz_lan.ps1`, because a bare `.\allow_viz_lan.ps1` fails with "running scripts is disabled": every execution-policy scope is Undefined, i.e. Restricted); and the adapter on the **Private** profile, since the rule is `-Profile Private`. The desktop's `Ethernet 2` was `Public` (`Get-NetConnectionProfile`); fix it from an Administrator PowerShell with `Set-NetConnectionProfile -InterfaceAlias 'Ethernet 2' -NetworkCategory Private` (done 2026-10-05). A running supervisor keeps the argv it started with, so `restart viz` doesn't pick up a new host: restart `up`.
- **Swapping one service's argv without restarting the proxy (2026-10-05):** kill the managed child (`taskkill /PID <pid> /T /F`) and start the replacement detached (PowerShell `Start-Process … -WindowStyle Hidden -RedirectStandardOutput/-Error logs/stack/<name>-*.log`, so no agent shell owns it) inside the supervisor's 2 s first backoff. The supervisor then finds the port taken and watches the new process as `external`. Done for the viz on the desktop: `viz: port 8080 taken by pid 13116; watching it`, `0.0.0.0:8080` answered on the LAN IP. Costs: one `service_down` juncture (no `service_up` follows for an external service), and the external process isn't restarted if it dies; then the supervisor starts its own with its startup argv.
- **That swap doesn't last; replace the supervisor instead (2026-10-06).** The swapped-in viz died on 2026-10-05 21:45 and the old supervisor restarted its own, as did three `restart viz` requests after it, all with its startup argv, so the viz was back on `127.0.0.1`. To load new supervisor code while a game session runs through the proxy: `taskkill /PID <supervisor> /F` (no `/T`: the children are separate processes and keep running), then `Start-Process <python> -ArgumentList 'harness/stack.py','up' -WorkingDirectory <repo> -WindowStyle Minimized` (its own console, so CTRL_BREAK works, and no agent shell owns it). The new supervisor adopts everything as `external`; kill the one service whose argv changed and it starts its own, managed. Done on the desktop: supervisor 28064 → 43672, viz 42820 (`127.0.0.1`) → 44784 (`--host 0.0.0.0`, managed), the proxy's connection to `35.71.142.123:2593` intact. Until they exit, the proxy, laya and the bridge stay `external`: `restart` and `down` leave them alone, and when one exits it is restarted without a `service_down` juncture.
- **Adoption:** a service whose ports already listen (the bridge: its command line, via `Get-CimInstance`) is watched as `external` and never stopped by `down`; when it goes away the supervisor starts its own. Live check 2026-10-04 with the user's stack up (proxy 28952, viz 6100, laya 38488, NAT): `up --no-bridge` showed all three `external` and the NAT `up`, and after `down` all four ports were still served by the same pids.
- **Stopping a child cleanly on Windows:** the only signal a parent can send a child is CTRL_BREAK (`os.kill(pid, CTRL_BREAK_EVENT)`, child created with `CREATE_NEW_PROCESS_GROUP`, same console). Python's default for SIGBREAK is to die with no cleanup, so each child starts through a `-c` bootstrap that sets `signal.signal(SIGBREAK, default_int_handler)` and `runpy.run_path`s the script (its folder put first on `sys.path`, as `python script.py` does). The services' existing `except KeyboardInterrupt` paths then run: the proxy's `amain` `finally` saves the agent gate and flushes the memory store. Verified 2026-10-04: a real proxy and a replay viz on spare ports were up in 1.6 s and stopped within 1 s with no traceback, and the proxy's gate file was written. CTRL_BREAK needs a console: run `up` from one (pythonw or a detached start would fall back to `taskkill` and could lose the proxy's last ≤ 0.25 s of events).
- **laya-serve.exe outlives its Python wrapper** (NOTES "Laya speech triage"): the supervisor records each port's owner pid while the service is up and kills those too when the service exits or stops.
- **Restarts:** backoff 2, 5, 10, 30, 60 s, reset after 5 min up; a service that isn't ready in time (proxy 90 s, viz 60 s, laya 240 s) counts as an exit. One `service_down` juncture (attention, with the last log lines) per outage, `service_up` (info) when back, `service_flapping` (attention) after 5 exits in 10 min. A juncture that can't be written (no store, e.g. after a handoff push) is only logged.
- **NAT:** watched on its lookup port 25943. Down (or a start that fails) → urgent `nat_down` and one run of `start_proxy_nat.ps1` per outage, which asks UAC; started only once the proxy is up, because the script would otherwise start a proxy of its own. `python harness/stack.py restart nat` tries again. `down` leaves the NAT running.
- **Handoff:** `down` stops everything that has the store open that the supervisor started (proxy, viz, bridge), which `dbhandoff.py push` needs; an `external` one stays up and has to be stopped by hand.
- **A stack "restart" can leave old code running (2026-10-06).** After the 2026-10-06 supervisor replacement (no `/T`), the proxy, laya and bridge from 2026-10-04 16:23 kept running as orphans. The user's `down` + `up` then adopted them as `external` and only restarted the viz, so the 10 h daily cap (efd6213) wasn't live: viz showed `today 7:22 / 8:00`, `/api/state` gate `daily_cap_s: 28800`. Before you count on a code change being live, run `python harness/stack.py status`: any `external … not ours` service is still the old process. Stop those by pid (`taskkill /F /T /PID …`; laya is `triage.py serve` plus its `.venv-laya` `laya_serve_local.py` children), then `up`.
- **A game session can drop under a run (2026-10-21 00:23:30).** `logs/stack/proxy.log`: "('127.0.0.1', 64987) closed", "Unhandled exception in client_connected_cb", `OSError: [WinError 121] The semaphore timeout period has expired` from `reader.read` in `proxy._relay` (which of the relay's two sockets timed out isn't logged). The proxy process stayed up; `ctl status` answered `{ok: false, error: "state port: no active session", sessions: []}` and the hunt run ended "ABORTED: state port: no active session" (hunt-20261020-233736-5260, Logan mid-leave on Maus 3 with 33k gp). Only the user can log back in (Constraint 1); the runner and the Seer can do nothing until a session shows again.
- Tests: `python harness/test_stack.py` (from a console: stand-in services on free ports; start, hard-kill restart with the junctures, the clean CTRL_BREAK stop, adoption and replacement, an orphaned port owner reaped, restart/stop requests, `status`).

## Laya speech triage (since 2026-10-01)

- **What:** `harness/triage.py` asks [Laya](https://github.com/NandhaKishorM/laya) two yes/no questions about each line a character says during a harvest job. Policy and eval numbers: docs/PLAN.md "Laya speech triage"; juncture fields: docs/OVERSEER.md `speech_nearby`.
- **Install (done 2026-10-01, re-run on a new machine):** a separate venv so torch never enters the harness's Python:
  `py -3.13 -m venv .venv-laya && .venv-laya/Scripts/python.exe -m pip install torch==2.14.1 --index-url https://download.pytorch.org/whl/cu130 && .venv-laya/Scripts/python.exe -m pip install "laya[serve]"`
  `.venv-laya/` is gitignored. The first install (CPU wheel, then laya 0.3.23) took 168 s; swapping in the CUDA wheel took 276 s. **Wheel index matters for this GPU** (RTX 5070 Laptop, compute capability 12.0 = sm_120, driver 595.97): the `cu128` index stops at torch 2.11.0, so torch 2.14.1 needs `cu130` (or `cu132`). Check with `python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_arch_list())"`: the cu130 build lists `sm_120`. A torch without CUDA makes laya-serve fall back to CPU **silently**; `python harness/triage.py health` shows `"device": "cuda"` when it took.
- **Checkpoints** download to `~/.cache/huggingface/hub` on first use (`convaiinnovations/laya`, ModernBERT-large, 421M; the eval also pulled `laya-multilingual`; 2.37 GB together). First in-process load took 198 s including the download; a cached `triage.py serve` (CPU) listens after 9.6 s and holds ~2 GB RAM.
- **Harmless warnings at start:** huggingface_hub's "To support symlinks on Windows…" (the cache copies instead; `serve` sets `HF_HUB_DISABLE_SYMLINKS_WARNING`), and Laya's "this checkpoint ships invalid temperatures … choice:11+" (affects choice questions with 11+ options; we only ask noul).
- **GPU vs CPU (2026-10-01, `eval_triage.py --repeat 2`, 141 requests each, ClassicUO running on the same GPU, same torch 2.14.1+cu130 build for both):**

  | | forward pass (`infer_ms`) median / p90 | runner round trip median / p90 |
  |---|---|---|
  | CPU, `LAYA_THREADS=4` | 901 / 1086 ms | 917 / 1098 ms |
  | GPU, `LAYA_CUDA_AMP=fp16` | 59 / 67 ms | 73 / 88 ms |

  About 15× faster in the forward pass, 12.5× in the round trip. The first GPU request after a start takes ~1.2 s (CUDA warm-up); after that it stays in the table's range. Scores match: max |Δ| 0.0020 (`check`) and 0.0027 (`direct`) against the CPU run, and no line crosses `ESCALATE_CHECK`. VRAM: 727 MiB with the client alone, 2556 MiB with laya-serve idle (fp32 weights; fp16 is autocast only), 3422 MiB peak under the back-to-back eval, of 8151. GPU utilisation (client + Laya) peaked at 46 %, mean 29 %, during that burst of 141 requests in ~10 s; in play it's one forward pass per line spoken nearby. The client's frame rate wasn't measured (nothing in the harness reads it). Earlier, with torch's default 24 CPU threads and three questions in-process, CPU was 0.55–0.9 s. The English checkpoint is pinned (`model: english`); the router alone sent one French line to `multilingual`.
- **Port 25970.** 25940–25960 is the proxy's upstream bind range (divert_nat excludes it), so the service sits outside it.
- **Stopping:** Ctrl+C in the `serve` terminal stops both processes. Killing only the Python wrapper (TerminateProcess, e.g. `Popen.terminate()`) leaves the server listening on 25970. Stock: `laya-serve.exe`, stop it with `powershell Stop-Process -Name laya-serve`. Fine-tuned (the default since v2): a `.venv-laya\Scripts\python.exe harness/laya_serve_local.py` child, which that doesn't catch; find its PID with `netstat -ano | grep ":25970 "` and `powershell Stop-Process -Id <pid>` (never by name: other harness processes are `python.exe` too).
- **When it's down:** the runner logs "speech triage unavailable (…)" once per outage, each verdict carries `error`, and calls back off for 60 s (a refused localhost connect can take ~2 s on Windows [INFERENCE: Windows TCP SYN retries; not timed here]). The speech hold itself is unchanged.
- **Re-measure** after changing questions, the prompt or the checkpoint (bump `triage.VERSION`): `python harness/eval_triage.py` against the running service. Zero-shot findings: a bare one-line prompt does worse than the prompt with nearby players and recent speech; `score` and `choice` phrasings were no better than noul; the multilingual checkpoint separated worse than English on this set, even on the Turkish/French lines.
- **Tamer pet commands are not speech to us (2026-10-02):** at the New Player Dungeon, tamers' "all guard", "All Stop", "All Guard Me", "All Kill", "ALL KILL" each held the hunt (junctures 52/56/57/58/60), and Laya scored "All Guard Me" `check` 0.57, just under the 0.6 staff-hint line. `speech_guard.pet_command` now drops a whole-line `all <command>` or `<pet name> <command>` (the name slot must be the first word of a non-human mobile's name on screen and not a word of our own name; commands: speech.mul's English pet keywords 0x155-0x170 plus `unfriend`). Extra words, a bare command, or a speaker with staff hints still hold; dropped lines stay in later lines' `context`.
- **Spawned soldiers' barks are not speech to us (2026-10-06, witcher_265, Seer7 run 14):** "an andarian footman" ("*shield bash*", "For Andaria!"), "an andarian marksman" and "an andarian captain" ("You will never defeat the might of Andaria!"), fighting an ogre lord, held the lumber job five times (junctures 386-390), one per new serial. They had a human body (0x190/0x191), notoriety 1 and flags 0x40/0x42 (no player flag 0x20), which `speech_guard.is_character` counted as "human body, no npc evidence". It now asks `threats.identify` as the threat watch does: a creature-style name ("a"/"an" …) on a human body without 0x20 is an NPC at notoriety 1-2 and a monster at 3, so it isn't held for; at 4-6 it still counts (a red stays a player whatever his name).
  - **…also when the soldier is already gone (2026-10-06 15:13, witcher_265, Seer8 run 2):** "an andarian
    captain" (0x005F0B9D, in view since 15:11:54) said "*charges*" at 15:13:25.6 and was deleted (0x1D)
    at 15:13:26.6. The runner looked the speaker up after the delete, found no mobile, and read "not on
    screen" as a hidden-staff hint: a speech hold plus `gm_suspected` and the staff alarm (junctures
    399/400). The speech header carries the speaker's body and name, so `speech_heard` now has `body`, and
    an off-screen speaker with a non-human body or a spawn-style name (lowercase "a"/"an …") is not a
    character. A human body with a player's name off screen still counts (hidden staff).
- **Company NPCs are not speech to us (2026-10-20, Time Quest Hub, mining-20261020-160438-0cf5):** "Roan of Grere's
  Fighters", "Delric of Grere's Rangers", "Jareth of Grere's Magi" and "Alden of Grere's Magi" idle near the hub
  (3510-3543,1927-1954). They have notoriety 1, a human body 0x190 and flags 0x00 (no player flag), so they counted as
  "human body, no npc evidence". Their idle lines held mining (junctures 1496/1497 and earlier). "You like dags?"
  scored as an attendance check (Laya 0.83): `gm_suspected` 1498, the urgent staff alarm. The user: "Just another
  NPC"; "ignore those NPCs". `threats.identify` now calls a "<Name> of <Owner>'s <Group>" label on a human body
  without 0x20 an NPC (`_COMPANY_NPC`). The speech guard and the threat watch then skip them. The same label with the
  player flag still counts as a player.
- **Fine-tuned checkpoint (v2, 2026-10-04).** Decision and numbers: docs/PLAN.md "Fine-tuned for escalation, deployed as v2". `triage.CHECKPOINT` = `models/laya-triage` (gitignored, 843 MB; fp16 weights like the stock file, tokenizer/encoder config copied byte-for-byte from the base). `serve` runs it through `harness/laya_serve_local.py` (laya-serve with `laya.router.DEFAULT_MODELS["english"]` pointed at the directory; laya.serve has no env var for a local path), so clients still send `model: english`; `/health` shows the revision as `null`. A missing directory makes `serve` exit with a message: rebuild it (below) or run stock.
  - **Rebuild the data** (plain Python, reads `harness/data/harness.db` and `discord.db`, ~7 s): `python harness/triage_data.py` → `harness/data/triage/{train.jsonl, heldout.jsonl, composition.json}` (gitignored: real player lines). Deterministic for a given seed and DB contents; more memory-store speech changes the split and the counts.
  - **Train** (~5 min): `HF_HUB_DISABLE_SYMLINKS_WARNING=1 .venv-laya/Scripts/python.exe -u harness/triage_train.py --train-layers 6 --epochs 4 --mem-gb 3.2` → `models/laya-triage/` + `train_log.json` (per-epoch CE and calibration-split accuracy, wall time, peak VRAM). The base is the stock checkpoint at `triage.STOCK_REVISION` from the HF cache. Measured with the client running (1.2–3.9 GB of VRAM in use by other processes before each run): 288 s training, 293 s total, peak 2756 MiB allocated / 3068 MiB reserved, under the `--mem-gb` cap (`torch.cuda.set_per_process_memory_fraction`). The cap matters: full fine-tuning of all 421M parameters with AdamW would need ~6.5 GB [INFERENCE: 16 B/parameter], more than the 8 GB card has free next to the client and laya-serve. Seeds are fixed, but GPU kernels aren't deterministic, so a rerun gives a close, not identical, checkpoint [INFERENCE: not rerun].
  - **Evaluate a candidate next to the live service:** `python harness/triage.py serve --checkpoint models/laya-triage --port 25971` (another terminal; a second full copy of the model on the GPU, so check `nvidia-smi` first), then `python harness/eval_triage.py --url http://127.0.0.1:25971 --save harness/data/triage/eval_X.json --compare harness/data/triage/eval_baseline.json`. The eval reads `heldout.jsonl` by default (`--heldout ""` skips it) and prints, for both sets, every line's check/direct old → new and which cross `ESCALATE_CHECK`. The frozen stock baseline is `harness/data/triage/eval_baseline.json` (47 lines + 104 held-out; `eval_baseline_v1.json` is the 47-only first save); a stock instance (`--checkpoint stock --port 25972`) reproduced it to 0.0000, and so did a second run of the candidate. Stop your instance by PID (see Stopping), not by name.
  - **Latency is unchanged** (same architecture): candidate 34 ms median forward pass, stock 32 ms, measured back to back with `--repeat 2` on the GPU next to the client (a first stock pass while the GPU was cold read 71 ms).
  - **Deploy / roll back:** deploy = `triage.CHECKPOINT` + a `VERSION` bump, then restart `triage.py serve`. Roll back for one start with `python harness/triage.py serve --checkpoint stock`; for good, `CHECKPOINT = STOCK` and bump `VERSION`. Stock is pinned (`LAYA_REVISION` = `STOCK_REVISION`, 55cf4c4): unpinned, a start on 2026-10-04 downloaded hub commit 7b928d8 (846 MB) instead.

## Telegram bridge (since 2026-10-02)

- **What:** `harness/telegram_bridge.py` puts the overseer chat and the junctures that wake it on a Telegram bot chat, and turns the user's replies into `user` chat rows. Setup, what's forwarded and the delivery rules: docs/OVERSEER.md §8; the decision: docs/PLAN.md "Overseer chat on Telegram".
- **Secret:** the bot token lives in `harness/data/telegram.json` (gitignored, with `.part` for the atomic write; `backup.py` doesn't copy it either, as with other credentials). `harness/dbhandoff.py` does carry it between computers via the NAS share (see "Two computers"). A new token comes from BotFather (`/revoke` invalidates a leaked one). The bridge never logs request URLs, which carry the token (`Bot.call` raises `ApiError` with only the code and description).
- **Reachability (2026-10-02):** `api.telegram.org` answers from this PC without a proxy: `telegram_bridge.py send` with a bogus token got `401: Unauthorized` (exit 1). Not tested end to end with a real bot yet (no token in the repo or environment); the offline proof is `harness/test_telegram_bridge.py` plus a smoke run of the real `run` process against the fake API, where a phone message woke `ctl wait` (`data.via: telegram`) and `ctl say` and an urgent `speech_nearby` juncture reached the fake chat, both loud.
- **Game-side footprint:** none. The bridge's HTTPS goes to Telegram, never to the Outlands servers, and it doesn't touch the client, the proxy or the capture.
- **Bot API behaviour it relies on:** `getUpdates` keeps unconfirmed updates for 24 h and confirms everything below the `offset` you pass; a second poller on the same token gets 409 Conflict; 429 bodies carry `parameters.retry_after`; `sendMessage` text is limited to 4096 characters; `disable_notification` delivers without sound. Source: https://core.telegram.org/bots/api ([INFERENCE] from the docs; only the 401 case was seen live).

## Discord capture (since 2026-10-03)

- **What:** `harness/discord_capture.py` records Outlands Discord history into `harness/data/discord.db` (gitignored, backed up to the NAS `discord/` folder by `backup.py`). The decision and the rejected alternatives: docs/PLAN.md "Discord history capture".
- **Install (done 2026-10-03):** `py -3.13 -m venv .venv-discord && .venv-discord/Scripts/python.exe -m pip install patchright zstandard fastembed-gpu "nvidia-cuda-runtime==13.*" "nvidia-cublas==13.*" nvidia-cufft nvidia-cudnn-cu13` (patchright 1.63.0, zstandard 0.25.0, fastembed-gpu 0.8.1 with onnxruntime-gpu 1.30.0, numpy 2.5.3, CUDA runtime 13.4.92, cuBLAS 13.8.0.4, cuFFT 12.4.0.43, cuDNN 9.27.0.42). `fastembed` (CPU) and `fastembed-gpu` must not both be installed: they ship conflicting `onnxruntime` packages, so uninstall `fastembed onnxruntime` first. No browser download: it drives the installed Microsoft Edge (`--channel msedge`; Edge 154 here; Chrome isn't installed). Patchright's README setup is persistent context + `channel` + `headless=False` + `no_viewport=True` with no UA/header overrides; checked: `navigator.webdriver` is `false` and the UA is stock Edge.
- **Run:** `.venv-discord/Scripts/python.exe harness/discord_capture.py serve` opens the window (profile `harness/data/discord_profile/`, which holds the login: gitignored, never backed up; the login survived a restart) and listens on `127.0.0.1:25980`. Every other subcommand talks to that port and runs under the plain harness Python: `status`, `shot` (PNG in `logs/discord/`), `goto URL`, `click SEL`, `fill SEL TEXT`, `press KEY`, `eval JS`, `guilds`, `channels GUILD` (category tree with captured counts), `crawl GUILD --channels A,B --until YYYY-MM-DD`, `crawl-stop`, `quit`. `stats`, `search QUERY [--channel NAME]` and `ignore CHANNEL_ID...` use the DB directly. Log: `logs/discord/capture.log`.
- **Serve can die silently; the crawl queue dies with it (2026-10-03).** At 15:18 serve and its browser vanished mid-#newplayer: no "serve stopped" line, no traceback, no process left (cause not found; [INFERENCE] killed with the shell that launched it). The queue lives only in serve's memory, but each channel resumes from its oldest stored message. To recover: start serve **detached** so no shell owns it (`Start-Process` the venv python with `harness/discord_capture.py serve`, `-WindowStyle Hidden` hides only the console; the Edge window still shows), check `status`, then send the same `crawl GUILD --channels …` again; channels already at their start are skipped.
- **What it records:** only responses the web app requested: `GET /api/v*/channels/<id>/messages` (scrolling and jumps), guild message search, thread/forum lists; plus the gateway WebSocket, decoded with a decompressor that lasts the whole connection (zstd-stream or zlib-stream, read from the URL's `compress=` parameter). From the gateway it takes READY/GUILD_CREATE channel lists and live MESSAGE_CREATE/UPDATE. Each message is upserted by id, and an edit replaces the stored text, with the FTS index following through triggers. `captures` logs every recorded response (status, count), so 429s and 403s show up there.
- **Images (since 2026-10-03, user request):**
  - Every image attachment (`content_type` image/*) of a stored message is queued in the `media` table and downloaded by serve's background loop. Files go to `harness/data/discord_media/<channel>/<attachment id>-<filename>` (gitignored; backed up additively to the NAS `discord_media/` by `backup.py`). `media.path` is relative to that folder, with `/` separators.
  - Each download is checked: sha256 and size are recorded, and the first files had valid PNG/JPEG headers with matching bytes. Downloads are one at a time, 0.5–2 s apart, capped at 50 MB per file. Videos and other files are not downloaded; their metadata stays in `messages.attachments`.
  - **CDN links are signed and expire after ~24 h** (`ex=` is the expiry as hex epoch, e.g. `6ac285c5` = 2026-10-04). So downloads must happen within a day of the capture, and serve does them whenever it runs. A 403/404/410 marks the row `expired`. Recapturing the message, by opening the channel at that point, brings a fresh URL and re-arms it (`pending`).
  - The download is a plain HTTPS GET of the public signed URL (stdlib urllib with the browser's UA string). It carries no token, so only the IP ties it to the account. It fetches the same file the app displays.
  - The volume is noticeable: the first ~7.3k messages carried 338 images, 191 MB.
- **Crawl:** for each channel, jump to the oldest stored message (`/channels/G/C/<id>`, an `around=` load) and send mouse-wheel bursts over the message list's scrollable ancestor until a `before=` page arrives. Aim at the scroller's rectangle, not `[data-list-id='chat-messages']` itself: that `<ol>` is the full list height and mostly off screen (live: y = -2318, height 5986), so wheeling at its box missed and the first run stalled. After each page it waits 2–6 s, plus 20–90 s on 6 % of pages, and takes a 5–15 min break every 60–90 min. A `before=` page shorter than its `limit` means the channel's start (`crawl.reached_start`); `--until` stops earlier. After 3 jumps that load nothing it gives up on the channel (`crawl.note`). Every 150 pages it re-jumps, to drop the app's in-memory list. On a captcha it beeps and waits for a human to solve it in the window. On a logout it beeps and stops. After a 429 it pauses 10–15 min.
- **Live facts (2026-10-03):**
  - The web app pages history small: `limit=10` for a channel's first page, `limit=20` per older page. Measured pace: 11 pages (220 messages) in ~75 s, so ~10k messages/hour.
  - The gateway is `compress=zlib-stream`.
  - In-app navigation by `history.pushState` + `popstate` opens a channel without a page reload.
  - Joining via Accept Invite raised an hCaptcha (human-solved). Account creation and email verification raised none.
  - **The guild behind `discord.gg/outlands` is new; the community isn't.** The guild "Outlands Community" (`1520125994659348631`, 8,843 members) has an ID that decodes to 2026-06-26, and all of its channel IDs decode to 2026-06-26…28. The decoding checks out: stored messages' snowflake times match the API's `timestamp` field. The user knows the Outlands Discord as many years old, so [INFERENCE] the server was re-created in June 2026 (the TRADE-ARCHIVE category dating from the same week fits a migration). Older history, if any survives, is in a different guild. Message IDs can't predate their channel's creation, so this guild's channels hold nothing older than late June 2026.
  - The user's picks, in order of importance: `#harvesting` 1520906212319695108, `#newplayer` 1520238957562957824, `#scripting` 1520835143600705546. Later candidates: `#buy`/`#sell` (under TRADE [VERIFIED USERS ONLY]; also `#buy-archive`/`#sell-archive`, `#pricecheck`) for price history, `#template-builds` for build tips.
  - **`#general` 1520125995267395828 is on the ignore list (user: never).** Its live gateway messages are dropped too.
- **Semantic search (since 2026-10-03, `harness/discord_search.py`; design and rejected options: docs/PLAN.md):**
  - **Use:** `.venv-discord/Scripts/python.exe harness/discord_search.py search "how do I level lumberjacking fast" [-k 8] [--channel harvesting] [--since 2026-09-01] [--json]`. Each hit is one conversation chunk with channel, date, a jump link to its first message, the fused `score`, the cosine `similarity`, and `word_match` (an FTS hit landed in the chunk). `--json` is for scripts and LLM batch jobs. For exact words or item names, `python harness/discord_capture.py search WORDS` (FTS only, AND of the words, single messages) is still there.
  - **Index:** `harness/data/discord_vec.db`. `search` refreshes it before answering: new or changed chunks are embedded, vanished ones dropped. `discord_search.py index` refreshes without searching. Gitignored and not backed up (`backup.py` skips it): delete it and the next search rebuilds it.
  - **Model:** `BAAI/bge-small-en-v1.5` (384-d) via fastembed, downloaded once (~67 MB) to `~/.cache/fastembed`. Fastembed's default cache is a temp dir that Windows may clean, hence the explicit `cache_dir`. Harmless stderr: huggingface_hub's symlink warning (silenced) and its unauthenticated-HF_TOKEN notice on download. Since 2026-10-03 the loader is `harness/embedder.py`, shared by discord_search, discord_kb and `ctl know search`/`brief` (docs/MEMORY.md "Ranked recall").
  - **GPU by default everywhere (user directive 2026-10-03):** embedding runs on the RTX 5070 via onnxruntime-gpu's CUDA EP. `onnxruntime.preload_dlls()` loads CUDA/cuDNN from the environment's `nvidia-*` wheels, so no system CUDA install is needed. **PyPI's onnxruntime-gpu 1.30 is built for CUDA 13**: with the `-cu12` wheels the session fails on `cublasLt64_13.dll` and fastembed silently falls back to the CPU (fastembed's hint about a CUDA 12 index is for older builds). Driver 595.97 supports CUDA 13.2. If CUDA doesn't load, `embedder.model()` falls back to the CPU; `UO_EMBED_CPU=1` forces the CPU. The `index` and refresh log line names the device, `ctl know search` reports it as `recall: hybrid (cuda)`. GPU and CPU vectors agree: cosine ≥ 0.99999 over 2797 common chunks.
  - **System Python has the GPU stack too (installed 2026-10-03, for `ctl`):** `python -m pip install "fastembed-gpu==0.8.1" "onnxruntime-gpu==1.30.0" "nvidia-cuda-runtime==13.4.92" "nvidia-cublas==13.8.0.4" "nvidia-cufft==12.4.0.43" "nvidia-cudnn-cu13==9.27.0.42" "numpy==2.5.3"`, the venv's versions. Never also install CPU `fastembed`/`onnxruntime` there (they clash with the `-gpu` packages). The first model load after the install took 14.6 s (cold DLLs); warm loads take 0.8–1.0 s plus ~0.3–0.5 s for the first query on the GPU (CPU: ~1.0 s load, 17 ms query). A `ctl know search` takes ~1.9 s against ~0.2 s for `know stats`; embedding all 945 knowledge entries took 0.8 s on the GPU (14.6 s on the CPU).
  - **Speed:** on the GPU, a full rebuild of 3446 chunks (20.2k messages) took 8.8 s including the model load, ~400 chunks/s. On the CPU it ran at ~17 chunks/s (2670 chunks in 159 s), so the GPU is ~23× faster end to end, and ~37× on a bare batch of 512 long texts (0.42 s vs 15.6 s). A search with the model load takes ~3.5 s, and an incremental refresh of a few hundred new messages 1–4 s.
  - **Quality check (2026-10-03, 14.2k messages):**
    - "what's the fastest way to raise my woodcutting skill" put three #harvesting exchanges about leveling harvesting (T-maps / fishing / lumberjacking) on top. None of them contains the word "woodcutting" (similarity ~0.70).
    - "can I use razor scripts to automate gathering" found an ore-mining loop script exchange and the CompanionBot `!scripts` link list.
    - "which hatchet should a new player buy" was weaker: one direct hatchet question among general new-player chat. Short questions with no answer in the same chunk rank alongside topical chatter.
  - **Chunks:** one channel's consecutive messages less than 10 min apart, at most 12 messages / 1200 characters. The text is `#channel YYYY-MM-DD` and then `author: content` lines, with embed and forward text appended. The hash covers channel, first message id and text, so an identical repost is its own chunk.
- **Email links:** this network's DNS resolver (`exeter.kites.house`) sinkholes `click.discord.com` to 0.0.0.0 (Discord's email click tracker), so links in Discord emails fail with ERR_ADDRESS_INVALID. Workaround: `curl -s -D - -o /dev/null --resolve click.discord.com:443:162.159.136.232 '<link>'` (that IP is from 1.1.1.1) prints the `Location: https://discord.com/verify#token=…` to open with `discord_capture.py goto`.
- **Game-side footprint:** none. It only talks to Discord, never to the client, the proxy or the Outlands servers.
- **Discord knowledge base (since 2026-10-03, `harness/discord_kb.py`; design, rules and rejected options: docs/PLAN.md "Discord knowledge base"):**
  - **Use:** `.venv-discord/Scripts/python.exe harness/discord_kb.py run` does extract → consolidate → promote → digest. Each stage also runs alone (`extract [--channels a,b] [--limit N]`, `consolidate [--recluster]`, `promote [--db PATH] [--dry-run]`, `digest [--out PATH]`), plus `search WORDS [--all]` (FTS over the facts) and `stats` (windows, claims, facts by verdict, promoted per target, summed LLM cost). Default channels: #harvesting, #newplayer, #scripting, #template-builds, #patch-notes, #announcements (the last two are `official`). Only UTC days before today are processed.
  - **Re-run any time:** everything is incremental. New days are extracted, backfilled days re-extracted, changed clusters re-adjudicated, and promote is a no-op for facts already promoted. `--max-cost` (list-price USD, default 100 since 2026-10-04, was 40) caps one run; exit 2 means the cap was hit and a rerun resumes.
  - **The "cost" is notional (checked 2026-10-04):** the calls report `provider: anthropic`, `credentialId: 2`, and credential 2 in omp's `~/.omp/agent/agent.db` `auth_credentials` is `credential_type = oauth`, i.e. the user's Claude subscription, not an API key. `usage.cost` is omp's list-price estimate, so `llm_calls.cost` measures subscription usage, not money billed; the practical limit is the subscription's usage allowance (a call refused for quota fails its window, and a rerun retries it).
  - **State:** `harness/data/discord_kb.db` (gitignored; backed up to the NAS `discord_kb/` folder as `discordkb-*.db.gz`, since its LLM output costs money to regenerate). Tables: `windows`, `claims` (each with cited message ids, verbatim quote, author ids, embedding), `clusters`, `facts`, `promotions` (per target knowledge DB), `llm_calls` (every attempt with tokens, cost, seconds, error).
  - **Outputs:** `knowledge` entries tagged `discord` with ref `discord-kb:<fact id> <jump link>` (source `community`, or `doc` when official; importance <= 6), and the committed digest `docs/research/DISCORD_KB.md` (official, consensus and single_source facts by section).
  - **Trial promote:** `promote --db /tmp/copy.db` into a copy of harness.db is safe; promotion state is per target DB, so it doesn't disturb the real store's bookkeeping.
  - **`omp` gotchas:** close stdin on the call (`stdin=DEVNULL`, or `< /dev/null` in a shell). A first `subprocess.run` probe without it hung until killed, while the same command with `< /dev/null` answered in 2 s; the inherited stdin is the likely cause [INFERENCE]. `usage.input` in omp's `message_end` excludes prompt-cache reads/writes; the real prompt size is `input + cacheRead + cacheWrite` (that sum is what `llm_calls.input` stores). `usage.cost.total` is in USD. Sonnet (`claude-sonnet-5-5`) was billed $2/M input and $10/M output tokens.
  - **Long runs from an agent shell:** the agent's bash tool kills its job (and the python child) at its timeout (300 s by default), mid-run. Launch the full run detached (PowerShell `Start-Process ... -RedirectStandardOutput logs/discord_kb_run.log`) and read the log; an interrupted run loses only its in-flight calls.
  - **First full run (2026-10-03, CLUSTER_SIM 0.86):** 228 windows (#harvesting, #newplayer, #patch-notes; #scripting and #template-builds held only same-day messages, #announcements wasn't crawled yet) → 5334 claims kept, 44 dropped by the grounding checks → 4203 clusters (85 % singletons; the largest has 16 claims) → facts: single_source 3280, consensus 801, disputed 58, outdated 26, official 19, not_useful 18, wrong 1. 820 official/consensus facts promoted into harness.db. Cost $26.26 over 761 calls (extraction $14.18 for 1.58M prompt / 0.78M output tokens; adjudication $12.08, 533 calls of 8 clusters), roughly 35 min of wall time at concurrency 4 (an interrupted run plus the resumed one). 4 adjudication replies failed validation (a `rules` section, a missing cluster, trailing data) and all passed on the corrective retry.
  - **Quality checks (same run):** 10 random clusters with ≥ 3 claims were all one topic (some combine related sub-facts into one compound statement), so the threshold stayed at 0.86. Residual duplicates across clusters: among the 4100 digest facts, 6 pairs have statement cosine ≥ 0.95, 25 ≥ 0.92, 80 ≥ 0.90 (e.g. two Item Identification wand facts). Lowering CLUSTER_SIM to 0.82 and `consolidate --recluster` would merge more but re-adjudicates everything (~$12) and replaces every promoted entry; not done.
  - **Rebuild after the full crawl (2026-10-04):** 496 new windows (#newplayer back to 2026-06-27, #template-builds 16.1k msgs, #scripting 2.4k, #announcements 146; #patch-notes still 6 posts, its crawl ended "stuck: no older page after 3 jumps") plus 2 re-extracted days that had been partial (their 42 clusters dissolved). Totals: 803 windows, 20,053 claims kept, 126 dropped → 14,016 clusters (79 % singletons, the largest 79 claims) → facts: single_source 10,105, consensus 3,388, official 173, disputed 214, outdated 82, not_useful 53, wrong 1. Promote: 2,765 added, 339 updated, 25 retracted (verdicts that dropped), 1 confirmation of an existing entry; 3,561 active in harness.db. 1,953 calls, $71.11 list price (subscription usage), ~2.5 h at concurrency 4, 0 window failures. One adjudication batch failed twice on "Extra data" (a second JSON object after the reply); `parse_json` now takes the first complete object, and a rerun adjudicated those 8 clusters. Pre-embedding the 3,106 new knowledge entries for `ctl know search` took 3.5 s on the GPU.
  - **Quality checks (rebuild):** 10 random clusters with ≥ 3 claims were all one topic (some hold conflicting values, e.g. arcane essence at 20 gp vs 1k gp, which adjudication resolves). Duplicates across clusters grew with the corpus: of 13,666 digest facts, 26 pairs have cosine ≥ 0.95, 166 ≥ 0.92, 459 ≥ 0.90 (~3 % of facts in a pair ≥ 0.90). Threshold unchanged; a `consolidate --recluster` at 0.82 would re-adjudicate all ~14k clusters (~$45 list price) and replace every promoted entry.

## Outlands wiki, news and forums (since 2026-10-08)

Decision and rejected options: docs/PLAN.md "Wiki, patch notes and forums in the knowledge base".
Code: `harness/outlands_web.py` (capture), `harness/web_kb.py` (wiki + news → `knowledge`),
`harness/forum_kb.py` (forums → the Discord KB pipeline). Tests: `test_web_kb.py`, `test_forum_kb.py`.

- **Run (system Python, from the repo root):**
  - `python harness/outlands_web.py crawl wiki|news|forums|all [--limit N] [--nodes 3,16]`; `stats`;
    `text wiki:<pageid>|news:<postid>|thread:<id>` prints a stored doc as the pipelines read it. Log:
    `logs/outlands_web.log`. A first full crawl takes hours: launch it detached (`Start-Process`
    with `-RedirectStandardOutput`, as for discord_kb), one process per host is fine; re-runs fetch
    only what changed.
  - `python harness/web_kb.py sync [--dry-run] [--db PATH]` (always wiki and news together),
    `chunks <doc key>` (preview a doc's entries), `stats`.
  - `python harness/forum_kb.py run [--max-cost 40]` (or the stages `extract [--nodes] [--limit]`,
    `consolidate`, `promote [--dry-run]`, `digest`, `search`, `stats`; same rules and cost guard
    as discord_kb, exit 2 = cost cap hit, rerun resumes). Extract per section as the crawl
    finishes them; consolidate once at the end (a cluster whose members change is re-adjudicated).
- **The sites (probed 2026-10-08):**
  - Wiki: MediaWiki 1.41.1, API at `https://wiki.uooutlands.com/api.php`, 2 139 articles in
    namespace 0 (no redirects), 1.83 MB of wikitext. 1 366 articles are under 100 bytes of
    wikitext (trait groups, achievement lists built by templates), so the capture stores
    `action=parse` HTML, not wikitext. Extensions: Scribunto, no TextExtracts. The wiki has no
    robots.txt.
  - News: WordPress REST `https://uooutlands.com/wp-json/wp/v2/posts`, 653 posts (2018-10-03 →
    2026-10-01), one category; by title prefix 240 PATCH (1.9 MB of text), 100 EVENT, 93 VIDEO (no
    text), the rest announcements ("The PvP Patch Has Arrived!", expansions, rule changes).
    `outlands_web.news_kind` tags 249 `patch` (the word anywhere in the title), 106 `event`, 205
    `news`, 93 `video` (skipped). Patch posts
    style section headings as bold paragraphs, not `<h*>`, hence web_kb's "short line before a list
    is a heading" rule. Pre-2020 patch posts sometimes only say "Please click the title to review
    the patch notes" (the notes were on the forum).
  - Forum: XenForo 2 at `https://forums.uooutlands.com/index.php?…` (no public API, readable
    logged out, 20 posts per thread page). 19 sections, 5 007 threads / 27 850 posts in total on
    2026-10-08; crawled: the six sections in `outlands_web.FORUM_NODES` (docs/PLAN.md). Patch notes
    are posted in Patches (432 threads, 2017-09-02 → 2026-09-17; 3 225 posts, 572 by staff, most by
    Luthius; ~3.5M characters of text, extrapolated from 2.74M in 294 of the threads) and mostly
    duplicate the news posts (260 of the 370 threads since 2018-11 have a news PATCH post within 2
    days), so Patches isn't crawled.
  - **Staff markup:** a post is staff when its name carries `username--staff|admin|moderator` or
    its user cell has the "Staff Member" `message-userBanner`. Developers such as Luthius have only
    `username--moderator username--admin` and the banner, no `username--staff`; the first crawl
    keyed on `username--staff` alone and missed them (caught by the ForumKB agent, fixed, forum
    tables wiped and recrawled).
- **Gotchas met while building it:**
  - A `web_kb.py sync` that held one write transaction on `outlands_web.db` for 4 min locked both
    running crawlers out ("database is locked" after the 30 s busy timeout; both died). The sync now
    commits its state after every entry, and a thread that hits a lock is logged and skipped.
  - After that crash the forum listing walk (stop at the first page with nothing new) skipped the
    threads the dead run had listed but not fetched; the fetch list now comes from the DB (every
    thread whose fetched last-post/replies differ), regression test `test_forum_resume`.
  - `knowledge.add` treats the same topic with ≥ 85 % word overlap as a duplicate. A patch post
    that repeats a heading with near-identical text (2026-08-12: "Decode Map" for treasure and for
    resource maps) confirmed its own first entry 21 times in the first news sync. Topics of a
    repeated heading now end in `#2`, `#3`…; the 18 affected entries were repaired by hand
    (confirmations 0, confidence 0.8, ref back to their own key) and the re-sync added the 21
    sections and renamed 122 repeated-heading topics (superseded versions kept).
  - The wiki copies patch text word for word: the first wiki sync (run alone, `--only wiki`, since
    removed) confirmed 301 news entries with its identical sections (and overwrote their refs). The
    dedupe now spans both sources with the wiki first, a sync always covers both, and the 301
    entries were handed to the wiki by hand (confirmation undone, a new version with the wiki
    topic, ref and source superseding the news one). Test: `test_wiki_copies_patch`.
- **First syncs (2026-10-08, live store):** news: 6 140 chunks from the 560 posts with text, 6 119
  added in 379 s (~60 ms each: two FTS lookups and a commit per add), 4.5 s to embed on the GPU.
  Wiki: 6 284 chunks from 2 139 articles (crawl 2 804 s, 2 149 API calls, all HTTP 200), 2.7 s to
  embed. After the fixes above: **12 123 entries, 6 284 wiki (median 366 characters) + 5 839 news
  (median 231)**, 0 self-confirmations; an unchanged re-sync takes 3 s. Store afterwards: 6 294
  `wiki`, 6 012 `doc` (news + Discord official), 3 388 `community` active entries; a hybrid search
  over all of it took 150–280 ms after the model load.
- **Recall check (trial sync of 7 619 news + wiki chunks into a copy of the store, 11.7k entries):**
  a hybrid `Knowledge.search` took 100–115 ms after the model load (it reads all vectors per
  search). "what bonus does a bronze hatchet give to lumberjacking" → the wiki's Lumberjacking >
  Colored Hatchets table first (cosine 0.86); "what happens when I fail the captcha three times" →
  four Captcha wiki sections on top; "how do I get a rental room" → the 2020-06-09 patch that
  introduced rental rooms; "storage shelf loadouts" → the Discord fact, then the 2026-09-04 patch
  section.
- **First forum KB run (2026-10-08):** crawl 2 707 s for the six sections (1 243 threads, 9 306
  posts: General Discussion 5 803, Player Guides & Macros 1 674, New Player Questions 1 267, Corpse
  Creek 480, Active Development 50, Announcements 32; 4 235 GETs in all, every one HTTP 200, no
  challenge). Extraction: 1 323 thread windows → 10 045 claims kept, 89 dropped by grounding,
  $27.66 list price (1 323 calls, 3.11M prompt and 1.52M output tokens). Clusters: 7 768 (6 440
  one-author, not adjudicated, docs/PLAN.md). Adjudication: 1 328 clusters in 166 calls, $4.77, 0
  failures →
  consensus 821, official 167, single_source 211, disputed 90, outdated 36, not_useful 3. Promote:
  381 added, 607 official/consensus facts held back as last seen before 2023. Total $32.43.
  Digest `docs/research/FORUM_KB.md` (389 kB). The first `know search` afterwards took ~1 s (it
  embedded the 381 new entries); later ones ~0.2 s. "how do I become a thief and what can I
  steal" `--tag forum` → the 2026-07-09 staff notes on the thief's target.
- **First incremental refresh (2026-10-17, 9 days after the first runs):** the three crawls,
  launched detached together, finished in 8 s (news: 656 posts, 3 new), 23 s (forums: 171 threads
  listed, 1 to fetch, 8 posts) and 27 s (wiki: 2 141 articles, 15 changed, mostly the Halloween
  pages). The listing walk stops at the first page with nothing new, so the forums read 1–2 pages
  per section. `web_kb.py sync`: 65 added, 8 updated, 12 retracted, 12 103 unchanged, 23 s; the
  2026-10-12 patch "QoL Updates, Distro Loot Bag, Container Gumps" is now first in `ctl know search
  "distribution loot bag" --tag patch`. `forum_kb.py run`: 1 window, 2 claims, 2 clusters
  adjudicated, no new promotions (381 unchanged), $0.02. A refresh takes about 2 minutes, so it can
  be run before any session that needs current patch notes.

## Dead on the home rune: fenasiqareem at witcher_296 (live 2026-10-11 16:06–16:07, Seer-22)

Log: `logs/tasks/lumber-20261011-154542-2883.log` (lines ~763–800). Junctures 857–860. Store events 16:06:52–16:07:11.

**Before the escape**
- **Arrival:** the blue "fenasiqareem" ([HOOK], 0x001D8C84) came into view at 16:06:52. Another [HOOK]
  player, "Lord slaine.", was also around.
- **First spell:** at 16:06:53.8 he said "Des Mani". Magic Reflection bounced it: "Magic reflect removed
  (PvP)." at 55.37, with "fenasiqareem is attacking you!". Reactive Armor took his first hits (-1, -1).
- **Self care:** at 16:06:55.5 the self care drank a heal potion for **1 hit missing** (+1).
- **The runner's reaction:** at 16:06:54 it ran 18 tiles first ("within 7 tiles: running before the
  recall").

**The escape**
- **Telekinesis and a potion:** at 16:06:56.7 "fenasiqareem has applied telekinesis to you". At 16:06:59.5
  "An explosion potion has stuck to you!" (Greater Explosion, overhead count 5…1).
- **First recall:** the run hit its 60-move cap at 16:07:03 and Dan recalled from there. His "Corp Por"
  (-35 at 16:07:04.6) disturbed the cast 1.39 s in.
- **Second heal refused:** at 16:07:05.6 the next heal potion got "You must wait 1 second before using
  another healing potion." The runner's PotionClock then waited a fresh 10 s, so no potion was drunk
  after that.
- **More damage:** the stuck potion exploded (-36) at 16:07:06.2, leaving hits about 18/89. Weaken had
  lowered his max hits.
- **Second recall:** cast standing at 16:07:08.3 after "clear", landed home (4134,1429) at 16:07:10.39.

**After landing**
- **The kill:** -20 at 16:07:10.475, 0.09 s after arrival, and Dan was dead. [INFERENCE] The source was
  his second Corp Por (16:07:07.5) or a damage tick; nothing hostile was in view at home.
- **Known-PK mark:** the runner marked him: "known PK from now on: fenasiqareem (killed us)". Seer-22
  declined the Report Murder gump.
- **Stranded ghost:** Dan is a ghost in the guild house. The book has 0 charges (the room shelf stopped
  recharging it), so a ghost can't recall to a healer. The nearest known healer is Bernadette (3020,3734)
  by the corpse; Seer-22 found none near the guild house.

**Gaps found**
- **Potion refusal:** the refusal names its wait ("1 second"), but `healing.PotionClock` restarts a full 10 s
  on any refusal.
- **Cooldown spent at the start:** the first heal potion went at 1 hit missing (the 'PK Getaway' rule: a
  potion whenever off cooldown), so its cooldown was running when the big hits came.

## Locked house doors and spell walls on an escape route (live 2026-10-11 16:43, Jimii Hendrix)

Log: `logs/tasks/lumber-20261011-163321-38d5.log` (lines 432-444). Store events 16:43:28-16:43:47 (session 95);
ground items from a replay of `logs/session_20261011_124101` (`replay.timed_packets` → `WorldRuntime`) at 16:43:37.
- **What happened.** Fleeing from (4201,806), the 39-step route was planned before house multi 0x6B847EA2
  (graphic 0xE9, 1966 multi.mul pieces) at (4235,802,z0) came into view (16:43:29.9, Dan 27 tiles off; its door
  items 0x6B847EA5-EB0 at 16:43:31). At (4225,808) the per-step check refused a step into its wall and
  replanned: 33 steps up the house's outside stairs (4236,810 z5 → 4235,805 z25) to its metal double door
  0x0675/0x0677 at (4235,803)/(4236,803) z25. Each open request (0x12) got cliloc 500024 "Opening door..." and
  1061637 "You are not allowed to access this." (16:43:37.277, 38.340, 39.346, 40.672), each step was denied,
  and the second request per leaf waited a "read" pause: ~4 s at the door while Jimii hit Dan.
- **Why the house wasn't treated as foreign.** It was recognised as a house (multi.mul has its pieces; the
  doors were inside `house_rect` (4225,790)-(4246,813)), but "the house we're in" was the 2D rect test: the
  replan tile (4225,808) is the rect's one-tile rim, and the stairs top (4235,804) is inside the pieces'
  bounding box, so its doors counted as ours and stayed open to the planner. The rect of a big house holds its
  yard, porch and outside stairs.
- **Fix (pathfind.Walkers.get).** `inside` is now (x, y, z), and a house counts as ours only when we're shut
  in it: no walk from where we stand leaves its pieces' bounding box without stepping onto a door tile
  (`Walkers._enclosed`, a flood fill with can_walk, cached per house and door layout; 4-17 ms here). From
  (4225,808,0) and (4235,804,25) all 12 doors of 0xE9 are locked; from its interior (4235,800,25) none.
  The Corpse Creek multi 0x154 (live 2026-10-02) is open on its east side (945,775), so standing at
  (943,776) no longer unlocks its door either. test_pathfind.py checks the 16:43 geometry.
- **Doors that refuse us are walls at once (agent_link.Mover).** When an open request is answered with
  1061637 (`DOOR_REFUSED_CLILOC`), or the step is still denied after the second open request, that door tile
  and the door leaves next to it are walls for the rest of the walk_to (`Mover.walled`, used by every plan
  as hard tiles), and the walk replans at once, without the second request and without a session-long deny.
  Nothing is remembered past the walk (user, 2026-10-11). `_try_door` no longer waits a "read" pause in an
  urgent walk.
- **Spell walls.** Jimii Hendrix said "In Sanct Ylem" at 16:46:03.27 (after the death). 1.47 s later three
  items of graphic **0x4D1B** (tiledata "wall of stone": Impassable, NoShoot, Wall, height 18) appeared in a
  row at (4186-4188,799,z0), with particle effects 0x376A on the same tiles; all three were deleted at
  16:46:20.654 (15.9 s). Not RunUO's 0x0080-0x0082 [INFERENCE: none seen]. The field spells' art by
  tiledata name: "Field of Poison" 0x3914-0x3929, "field of energy" 0x3946-0x3964 (Impassable), "field of
  paralysis" 0x3967-0x398A, "Field of fire" 0x398C-0x399F; fire, poison and paralysis fields aren't
  Impassable in tiledata. Lasting decoration uses the art too: fire field items at (1176-1182,1340-1349)
  were in view 12:54-13:29 the same day.
- **Fields are walls; dispel when boxed in (agent_link.Mover).** `FIELD_GRAPHICS` items are hard tiles for
  every plan (passable ones too), and a step whose tile got a field after the plan is replanned without
  being sent (no lasting deny: fields go). `Mover(dispel=...)`: when no route exists within the walk's bound
  (`max_route`; unbounded walks: no route at all) but one runs through fields, the first field on it is
  handed to `dispel(serial, item)` once per serial per walk_to; after a cast that went out the walk looks
  for the field's delete for up to `FIELD_GONE_WAIT_S` (1 s) and replans. Without a dispel callable, or
  when it returns False, the walk fails "no route" as before. test_mover.py checks both.

## Seer-24 shift: a hatchetless trip, an empty guild shelf (live 2026-10-11 17:20–23:30)

Nine runs, 23,337 boards, no deaths. The first live day of the guard flight (af2f4b0): every spot's
`guard route from <spot>` came out "reached" except witcher_116, which was an island (59,025 expansions).
The two "to the guards" flights were from faction-tag blues; both recalls landed. There were no escape
heals, no dispels, no locked doors and no heal potions.
- **Hatchetless trip (`lumber-20261011-194604-aa35.log` lines 6-13, juncture 887).**
  - Dan carried one gold hatchet, 636 uses for 1464 needed. `hatchets_ready` put it in the room chest
    "for a fresh one from the shelf". The shelf answered "Unable to resupply: no items available.", and
    the loop broke on "the shelf gave none" with the hatchet left in the chest. The runner logged "only
    0 uses carried … going anyway" and recalled out bare.
  - Seer-24 stopped him at the landing, then took the chest's five spare hatchets (1595 uses).
  - Gaps: the stow comes before we know the shelf can replace it; nothing takes a stowed hatchet back
    or uses the chest's spares; 0 uses doesn't stop the trip. Fixed 2026-10-12 (`hatchets_from_chest`,
    no trip with 0 uses; LUMBER_LOOP §13 "Trip" 1a'). The user stocked the room shelf with verite hatchets.
- **Guild shelf "nothing to give".** Every landing visit (17:44, 18:05, 18:46, 19:46, 19:48) answered
  "it had nothing to give", yet the book read 6 → 7 → 9 → 9 → 10 at the run starts. The shelf's reply
  doesn't list a book recharge as an added item (rule above: scrolls + the enabled option).
- **`lumber plan` NameError** `HOME_PRIOR` (`lumber_opt.py` 1389) at 21:47, gone about 60 s later. The
  cause was the optimizer edits in flight (4581ade/9586988), not the runner.
- **Seer-25 (2026-10-12): the server went silent mid-trip.** Session `logs/session_20261012_102435`
  had its last S2C at 12:45:06. The client kept sending for 90 s, then the connection closed at
  12:46:36. Run `lumber-20261012-124420-6891` aborted "state port: no active session" with Dan chopping
  at witcher_57 (1813,2163). The proxy and ClassicUO stayed up, and the internet answered right after,
  so the drop was upstream (server or route). The harness can't log back in (Constraint 1): only the
  user can. Runs 1-2 that day stored 6645 + 3258 boards.
  A second drop came the same way: session `20261012_135452` had its last S2C at 15:45:08 and closed at
  15:46:38, again 90 s of server silence, with Dan safe in the room. Run `lumber-20261012-154630-8ff9`
  aborted at its start because "the book's gump didn't open" (the server was already silent). Both drops
  came at ~:45 past the hour, 3 h apart. Every earlier session's close (10-11, 10-12) had ≤1 s of silence
  before it. The proxy's `close` event doesn't say which side closed. **Cause (user):** the server was
  down for patching both times. A patch looks like this: S2C stops, about 90 s later the connection
  closes, and the user logs back in once the server is up.
  **2026-10-26 13:45, a patch that announced itself:** session `20261026_105736` got three System `0xAE`
  broadcasts at 13:45:11–13: "Staff message from Luthius:", "Patching!", "The world is saving, please
  wait.". S2C then went silent and the connection closed 90 s later. Run `lumber-20261026-131436-abc9`
  aborted at witcher_254 (1122,1098) with ~666 logs in the pouch. A System "Patching!" broadcast is a
  global message, not a staff check on us. The jsonl `hex` is capped at 64 bytes, so get the full text by
  decoding `.s2c.raw` (`uo.s2c.S2CStream` with the `viz_feed.prelude_keys` key).
- **2026-10-13 14:12:35, a different drop (Seer-28):** session `20261013_103242` closed 40 ms after normal
  chopping traffic (chop lines, an equip, a container add), with no S2C silence and no server message,
  while the same ClassicUO process stayed up. The user called it a server-side hiccup. Run
  `lumber-20261013-134300-281d` aborted at witcher_177 with ~2500 logs in the pouch ("could not stash or
  convert the carried logs: state port: no active session").
- **Dan's own horse was blamed for hits (found 2026-10-13, Seer-28).** In `lumber-20261013-161548-a508`, a
  red's first-sight recall landed Dan at the guild-house landing (16:47:38). A spell effect then counted
  as a hit (0 hits lost), and the runner blamed 0x0154FE11, Dan's horse resting there (body 0xE4, no
  label, 0 tiles). The resulting Escape came out of `try_hop`'s hatchet clicks, inside the red's Unsafe,
  and crashed the run (job event 13592; traceback in the task log, lines 1271-1360).
  - **Earlier blames.** The job events hold five `monster_hit` rows blaming the horse since 2026-10-09.
  - **Every horse became aggressive.** The first row, a sole attacker, taught body 0xE4 aggressive.
    That made a wild "a horse" (0x006C89C1) a hostile sighting on 2026-10-10, which kept teaching it.
  - **Fixed 2026-10-13.** `threats.Params.own_pets` holds every remembered mount (`mount.own_serials`),
    and such pets are never in the threats. `learned_params` skips their hits and sightings, and any
    sighting with a passive name. `try_hop` treats an Escape as "no hop". On a copy of the live store,
    0xE4 was aggressive before the fix and is not after it.
- **Dead to Thugnificent at witcher_52 (live 2026-10-14 14:31-14:32, Seer-30).** Log
  `logs/tasks/lumber-20261014-141733-90b4.log` lines 488-546; juncture 1067.
  - **Timeline.** Tracking logged the red, a known PK since 10-10, at 97 tiles at 14:31:23 ("beyond 80:
    logged only"). He came into view 14 s later. Three recalls were disturbed 1.4-1.8 s into the cast,
    with runs of 17 and 5 moves between them, two Heals (54 → 39, 6 → 12), and hits 100 → 0 in 31 s.
    The Report Murder gump was declined.
  - **Tracked reds beyond 80 do come.** Of 75 tracking hits beyond the react range in the store, 10
    had the same red in view within 120 s. Seven of those were at 81-99 tiles: Dzho'gahr, SuperSkills,
    Thugnificent twice, Jimii Hendrix (killed Dan 10-11), Iesus Nazarenus and lxl. 46 of the 75 hits
    were at 80-99 tiles.
  - **Also seen.** Stamina read 0/25 at the flight's first aid. The guard route was planned at 476
    steps, then "no route within 470 steps" 3 s later.
- **lxl, 2026-10-14 11:38 (Seer-30, `lumber-20261014-112045-b23a.log` lines 786-790).** The red's
  spell was reflected as the escape recall landed. About 9 s later, try_hop's hatchet clicks counted
  that reflect as "taking damage, no creature in view", and the run stopped at the landing with 1201
  logs in the pouch. The room path runs in mode 'salvage' for exactly this (2026-10-06); the hop
  decision doesn't. `attacked_by_players` keeps only named players in view, so lxl wasn't marked a
  known PK.
- **The harvest spun in place at witcher_48 (live 2026-10-16 11:43:31-11:53:11, Seer-34).** Log
  `lumber-20261016-113013-5c88.log` lines 2812-15603. A black bear had been escaped from, and
  tree 1763,1070 was dropped for a zone and waited its 120 s cooldown in the list.
  - **The loop.** `next_stand` handed out the 4 trees at 1782-1784, each inside the bear's zone.
    harvest_trip skipped them (`out_of_reach`), and next_stand dropped each from the list. The list
    was then down to the waiting tree, `next_stand` returned None, and `add_local_trees` put the same
    4 back. That ran ~12 000 times with no pause and no chop, and Dan stood still until the
    overseer's stop. `zone_stalled` never fired, since it needs 5 trees dropped for zones and only 1
    was.
  - **Earlier runs.** The same spin ran in `lumber-20261008-140452-1f74` (~4.5 min) and
    `lumber-20261009-225707-3ed7` (~10 min), and each ended on its own.
  - **Fixed 2026-10-17.** `add_local_trees` leaves out trees within an escaped creature's reach.
- **"That cannot be seen.": the library walk ended on the floor above the shelves (live 2026-10-19, Seer-41/42).**
  The DTF guild house library floor is z 6, its tomes stand at z 7-22, and there are upper floors at z 26 and
  ~45-49 over the same tiles.
  - **What failed.** The walk to a tome targets only (x, y), within 1 tile. At 11:34 and from 17:59 the planner
    took a 28-36-step route up the stairs instead of the usual 15-17 on the ground floor, and ended at
    (4151,1430)/(4155,1430)/(4151,1427) at z 26-27. Every tome double-click got the System line "That cannot
    be seen." (13 in the store, 11 of them that day), and the landing shelf didn't open from up there either.
  - **Runs.** Five runs aborted at the start: 4c55, 5c2a, 4aea, 2783, 2fec. Seer-42 recalled Dan to the
    ground floor by the book's default rune.
  - **Cause of the switch.** Why the planner preferred the stairs from 17:59 isn't known. One plan saw "an
    object arrived after the plan" upstairs.
  - **Fixed 2026-10-19.** `agent_link.not_above(tome z)`: the walk ends on a floor at or below the tome, at
    most two storeys down (Cambria's floor z 0-6 under tomes at z 25-37 passes).

## A "thief" who killed: gorilkaenjoyer at witcher_105 (live 2026-10-10, Seer-17)

Log: `logs/tasks/lumber-20261010-141818-e65c.log`. Junctures 780 (death), 781 (threat), 782 (task_failed). Store
events 14:59:04–14:59:23.
- **The approach.** The blue gorilkaenjoyer (0x00006A2E, mounted, notoriety 1) played lumberjack animations (action
  29) beside Dan's stands. On 2026-10-07 he had already come to 1 tile at witcher_178, and Dan recalled. Here he
  came to 2 tiles at 14:59:05 and KeepAway stepped Dan off. At 14:59:13 he closed again and the thief rule recalled
  standing.
- **The kill.** He attacked 0.6 s into the cast: "gorilkaenjoyer is attacking you!" over Dan (hue 34) at
  14:59:14.68. He hit about every 2 s: -23, then -24 ("Your reactive armor spell has been nullified."), then
  -29, and so on. He disturbed all five standing casts: two book charges and three Recall-on-book casts,
  disturbed 0.56–1.77 s in. A heal potion (+23) didn't help. Dan was dead at 14:59:23.
- **The 0x2F swings** showed Dan → him (auto-defence), as with Bastet. Again no swing named him as the attacker.
- **What changed (PLAN "Known PKs fled on sight"):**
  - Every attacker is now a known PK, fled on sight.
  - The thief recall runs from the suspect after one standing cast.
  - `record_death` reads attack lines heard inside the escape, so the death is "pk", not "unknown".
  - The Report Murder gump is always declined.
- **After the death:** no healer within ~865 tiles that Dan knew. Seer-17 waited for the server's ghost move. The
  corpse held the hatchet, the pouches, ~1200 logs and the Harvest armor.

## Nystul, the viz assistant: headless omp with extension tools (probed 2026-10-08, omp 18.8.3)

Design: docs/VISUALIZER.md §2.13 and docs/PLAN.md "Nystul the Wizard".
- **Extension tools work in print mode.** `omp -p --mode json --no-session --no-extensions … -e <abs path>/harness/nystul_ext.ts --tools uo_list` registered and called the extension's tool. `--no-extensions` turns off discovery only; an explicit `-e` still loads. The file needs no imports: `pi.zod` builds the schemas and `pi.exec(cmd, args, {signal})` returns `{code, stdout, stderr, killed}`. Extension execute order is `(toolCallId, params, signal, onUpdate, ctx)`.
- **`--tools` limits the built-ins only.** With `--tools uo_list` the model listed all eight `uo_*` tools of the extension (registered `loadMode: "essential"`) and none of omp's built-ins (read, bash, …). So the extension's tool list is the whole surface; keep every tool in it read-only.
- **JSON event fields:** `tool_execution_start` `{toolCallId, toolName, args (object), intent}`; `tool_execution_end` `{toolCallId, toolName, result: {content, details}, isError}`; `message_update.assistantMessageEvent` `{type: text_delta, delta}` (also `text_start`/`text_end`, `toolcall_start`/`toolcall_delta`/`toolcall_end`); `message_end.message` `{role, content[], model, stopReason (toolUse between tool turns, stop at the end), usage {input, output, cacheRead, cacheWrite, totalTokens, cost.total}}`. One `message_end` per assistant turn: sum their usage for the run's cost.
- **CRLF gotcha:** `NYSTUL_PY="$(python -c 'import sys;print(sys.executable)')"` in git-bash keeps the `\r`; the extension's `pi.exec` then failed with "Executable not found in $PATH: \"…python.exe\r\"". `harness/nystul.py` sets `NYSTUL_PY=sys.executable` itself.
- **The model sees tool names with a leading underscore** (it called them `_uo_list` in its reply) while the events carry `uo_list` [INFERENCE: the provider's tool-name prefixing]. Harmless; the events and the prompt use the plain names.
- **Cost:** a one-lookup answer (Sonnet, thinking medium) cost ~$0.01–0.03 and 4–6 s; a four-query SQL answer $0.02 and 10 s (live 2026-10-08).

## Simulator runs found runner bugs (2026-10-13, docs/SIM.md)

- With `--pets --crawl` the crawl never left its waypoint: our pets were counted as monsters in reach
  (`crawl.observe`); and our war-mode pets next to us counted as attackers in `loop_hunt.attackers`
  (the two-attacker leave rule). Both fixed.
- A hurt leave that recalls at once strands a pet more than 3 tiles off; one that stands waiting dies to
  bleeds. `tamer.follow` now falls back from the attackers while the pets come.
- `walk_in` counted the pets within 3 tiles of us before stepping onto a teleporter, but in the
  simulator they come along counted from the teleporter [INFERENCE live: ServUO TeleportPets from the
  mover's tile]: a pet 3 tiles behind was 4 from it and stayed behind on the
  second level's one-way hop (5507, 697), and the run aborted at the next teleporter. Songs on the way
  in shifted the timing enough to show it (2 of 14 runs). Now counted from the teleporter tile.
- Out of vet supplies, `tamer.rested` counted a pet below `--pet-leave-at` as rested, so the run
  recalled in, left on arrival for that pet, and recalled out again: up to 436 recalls in a game hour
  (17 of 48 one-hour runs). Fixed: the rest waits for natural regeneration, bounded by
  `--rest-timeout`. Live this would also have burned the tome's charges in a pattern no player makes.
- Live pets follow at our pace: Logan's gravebug and phoenix stepped every 0.21 s out of war mode
  (n=470/471, capture 20261012_210013) and ended 16 of 22 walks of 10+ steps within 3 tiles of him
  (the rest 4-8, and 18 on the retreat while they held a mob). The sim's
  uncited 0.3 s left them 9-22 tiles behind the runner, so monsters found only us in their 10-tile
  aggro range; at 0.21 s the deaths per game hour went 0.52 -> 0.28. Still most monster aggro on us
  comes from walking first (pets 1-3 tiles back) and from our bard acts (docs/SIM.md).
- **Guarding pets take a monster's aggro; what reaches us is bleeds and area hits** (capture
  20261012_210013, the user's claim checked 2026-10-13). This covers the 25 monsters that engaged in
  view on Logan's trip, pets on "All Guard Me" every time.
  - 11 had no target cursor of ours on them before they reached or hit someone. All 11 went to a pet
    first.
  - All 23 '-N' on Logan, by cause (texts over/to him at the same instant):
    - 15 bleed ticks: 8-10 every 3 s for 15 s until "You are no longer bleeding."
    - 3 bleed openers:
      - a bound soul twice: -23 and -32 with "They begin to draw blood from you!". Both times it
        had just reached the gravebug and kept meleeing the gravebug, with Logan one tile off.
      - a vampire bat once: -22 with "The creature strikes ferociously, causing you to bleed!".
    - 1 nightstalker Charge: "*charges*", a 4-tile jump, -42 on Logan and -37 on the gravebug at the
      same instant. Knowledge #14625: it teleports up to 12 tiles and hits up to 3 targets within 1
      tile.
    - 4 from the vampire countess' transfixed copies of his own pets.
  - None was an ordinary melee swing or spell from a monster that had picked Logan, apart from
    possibly the bat's opener: that bat had our Discordance before the pets hit it, during the countess
    fight. Whether the bound soul's bleed is an area effect or a swing at the nearest non-target is
    [INFERENCE].
  - Who chose whom can't be told apart from the pets striking first: the phoenix's barrage usually
    lands within ~1 s of the monster turning to war mode.
  - The simulator's rule (nearest of us within 10 tiles; a bard act +400 threat on us whenever it
    happens) doesn't match this (docs/SIM.md).
- The simulator now follows that (2026-10-13, docs/SIM.md "The revised aggro and specials").
  - A monster goes for a guarding pet within 6 tiles of us. Our acts pull it only before any pet has
    hurt it.
  - The bleeds, the nightstalker's Charge and the countess' transfixed copies are modelled as they
    hit Logan.
  - Deaths per game hour fell 0.36 -> 0.17, but the gravebug died in 10 of 48 hours at
    `--pet-leave-at` 0.20 (2 of 48 at 0.35).
  - The sim runner meets no bound souls: its crawl never reaches x 5261-5277, y 645-661, where Logan's
    bleeds came from.
- **Stealth mid-fight is the shade wolves', and they come back to the pet** (capture
  20261012_210013, the user's question 2026-10-13: "their aggro is cleared when they stealth, so
  they can attack the player if we don't put the pets back on guard").
  - Two shade wolves (0x00061DAB, 0x000607BE), each fighting the gravebug next to Logan, dropped out
    of view while alive (0x1D) 4 times: 4.3-15 s into the fight and 19-22 s apart. They came back
    1-5 tiles from him.
  - Each was gone 3.5-5.8 s and came back in the same 0x20 as a hit on the gravebug (21-28). Their
    other hits ran 4-22. The first wolf's opening -40 on the gravebug came straight out of stealth.
  - Every hit after they came back went on the gravebug; none on Logan. Logan's next "All Guard Me"
    came 1.2-4.2 s after three of the returns and 26 s after the fourth, never before one. The pets
    were on guard from the "All Guard Me" just before each fight (21:20:03, 21:28:43; no recall or
    "All Kill" in between). The no-guard case never happened, so the hypothesis is neither
    confirmed nor refuted.
  - The capture's one nightstalker never stealthed: it died 22 s in. It charged 3 times (8.5 s
    apart) and frenzied once. It came into view 18 tiles off, not out of stealth.
  - Idle shade wolves 14-18 tiles off flicker out and back within 0.4 s: the view edge, not stealth.
  - The simulator models the observed Vanish: unseen 3.5-6 s, its target kept.

- **Getting in and out of the Mausoleum** (user 2026-10-13; capture 20261012_210013).
  - **Recall in a dungeon works only near a gold moongate**, into it and out of it, and never near a
    red one. The user takes a gate out even where a recall would work.
  - **In:** at 21:16:30 the rune tome (gump 0x09F5976B) landed Logan at (5626, 688, 0), 1 tile from
    a moongate item 0x40001CCA, graphic 0x0F6C, hue 2822 [INFERENCE: the gold one], at (5626, 689,
    0). Two more hue-2822 gates stand on that level 0, at (5618, 723) and (5588, 717). He walked
    the teleporters (5554, 700) -> (5547, 696, 10), level 1, and (5522, 637) -> (5199, 737, 17),
    the fight level. **Corrected 2026-10-15:** this note first gave (5554, 701) and (5521, 638), the
    tiles he stood on; the teleporter is the next tile, whose step is denied (0x21 reports where we
    stand) before the move (captures 20261012_210013 and 20261013_204518: the step N from (5554, 701),
    the step E from (5521, 637)). Live 22:33:53 `--enter-via 5554 701` stepped onto 5554,701 and
    stopped there; the user stepped one more north and went through.
  - **Out:** at 21:31:00 he stepped onto the crimson moongate 0x4010E11F (0x0F6C, hue 2820) at
    (5203, 703, 0), in the fight level's banshee room.
    - "Moongate Destinations" (0xE0E675B8; Totem is button 17), then button 2 "Travel to
      Destination" on the re-sent gump. Arrival (3859, 2890) at 21:31:04.
    - Both pets came along: clicked there 0.1 s after the arrival.
    - At 21:31:04.75 the rune tome from there: home at (4134, 1429) 2 s later.
  - The simulator modelled neither until this note: its tome row put us on the fight level, and it
    let the runner recall out from anywhere (docs/SIM.md "Limits").
  - **Two short hops on the second level** that the map files don't show. At 21:17:37 a step onto
    (5506, 701) was denied (0x21 at z -5), then a self 0x77 put Logan at (5507, 698). **Corrected
    2026-10-15:** the step was north, onto (5507, 701) (0x02 direction byte 0x80, running north, in
    20261013_204518 at 20:46:41); live 22:44:06 a step NW onto (5506, 701) was taken and went nowhere. The second hop is
    (5507, 696): there he sent two steps north at once, the first to (5507, 697) taken, the 0x21 deny of
    the second reporting (5507, 697); live 22:47:03 a step onto (5507, 697) went nowhere. A step onto
    (5507, 697) put him at (5507, 693). Without them the map's A* takes 468 steps round, against his
    ~150. Both are in the scenario and in `--enter-via`.
  - **The way out goes through the countess' room.** On the map the crimson gate's room is reached
    from the west rooms only via (5190, 682), and from the arrival via the nightstalker room. In the
    simulator, with the walk out modelled, most deaths are on that walk (docs/SIM.md "With the real
    way out").
- **The simulator's link delivers replies inside the send** (found 2026-10-26: Provocation never went out in the
  simulator; docs/SIM.md "Provocation never went out"). `SimLink.send` runs the World on the packet and pumps its S2C
  reply into `link.events` before `act` returns. Live, `agent_link.Link.act` only sends, and replies reach `events`
  on a later state read. So runner code that waits for a reply MUST take its `len(h.link.events)` mark *before* the
  `act`; a mark taken after it works live and misses the reply in the simulator. `Team.use`'s second Provocation
  cursor was the one case found: 0 provocations answered in two 144-run blocks before the fix, 1,317 after.

## Logan Wolf's tamer fighting at the Mausoleum (capture 20261012_210013, analysed 2026-10-12)

The user played a Mausoleum trip on Logan Wolf (0x00104A60) by hand, 21:13–21:31, and asked for a
combat runner learned from it (built: `loop_hunt.py --pets`, docs/HUNT_LOOP.md "Pets"). Read by
replaying the capture through `world.runtime.WorldRuntime` with `replay.timed_packets`, events
timestamped per packet. Facts:

- **Template** (0x3A, skills.mul names): Animal Taming, Animal Lore, Veterinary, Herding 120;
  Peacemaking, Discordance, Provocation 80; Tactics 18 (no Musicianship, no Magery). 100 str,
  25 dex, 100 int; followers 5/5. Pets: **AlaFlambe** 0x0026EF56 (phoenix, body 832, 4 slots,
  Epic Barrage to 12 tiles) and **GraveRobber** 0x000DDEFE (gravebug, body 387, 1 slot, the tank:
  it took nearly all the damage, poisons, and `*devours corpse*` 33 times, each followed by two
  `+18`). Both `(bonded)`, notoriety 2 to us; their hits come as a share of 100.
- **Trip shape:** claimed both pets at home ("You claim your follower.", after a Healer's Codex /
  class points session in a gump), "All Follow Me", a rune tome (graphic 29103) to a Society
  quartermaster (Corrine, 4 jobs incl. "Kill 25,000 Gold Worth of Creatures"), the tome again to the
  Mausoleum (5605,708), through its teleporter chain to 5190–5280 × 640–740, fought there
  21:16–21:31, recalled out, "room", stabled the pets ("You stable your followers."). Society job
  290 → 24,243 in 14 min, 25 kills; engagements 8–50 s (median ~22 s). Loot by hand: gold
  (~600–1,500 a corpse), gems, arcane scrolls.
- **He never fought himself:** war mode off the whole trip, no `0x05`, no spell. Actions: "All
  Guard Me" 26×, "All Kill" 5×, Discordance 31 uses (18 ok / 12 fail of 30 answered), Peacemaking
  16 (8 / 6), Provocation 9 (2 / 2 answered), veterinary supplies 43 channels, heal potions 6
  drunk, one cure potion.
- **Pet commands are Razor-shaped speech:** every pet command in his captures (56 in
  20261010_204420, 20261011_115828, 20261012_210013) is `ad <len> c0 03b1 0003 "ENU\0" <keywords>
  <utf8> 00 00`: hue 0x03B1 (Razor's default speech hue; his Razor profiles set none) and a
  double NUL. His typed speech ("room", "[damage") is hue 0x0170 with one NUL, Outland Dan's client
  0x02B2. `actions.razor_say` builds the Razor form (checked byte for byte); ANTICHEAT.md §8.20.
- **Razor's three speech paths** (Razor CE source, markdwags/Razor master, read 2026-10-17; the
  Outlands build is assumed to match: the pet-command bytes do). Every path ends a keyword-encoded
  body in UTF-8 + `00 00` (`Packet.WriteUTF8Null`); unencoded bodies are UTF-16BE + `00 00`, the
  same as the stock client's, so only hue and the encoded ending can tell them apart.
  - *Built-in "Say" hotkeys* (pet commands, `HotKeys/Misc.cs` → `Player.Say(msg)`): hue = Razor
    config `SpeechHue` (default 0x03B1, `UI/Config.cs`). This is `actions.razor_say`.
  - *Recorded macros* (`Macros/Actions.cs` `SpeechAction`, files like
    `Assistant.Macros.SpeechAction|0|368|3|ENU|0|room`: type, hue, font, lang, keyword count +
    raw keyword bytes, text): hue = `World.Player.SpeechHue`, which `Core/Commands.cs OnSpeech`
    sets from every `0xAD` the client sends; before the client has spoken this session, the
    recorded hue. Keywords are the recorded bytes, not recomputed. So an unencoded macro line
    ("room", "[Pouch", "[disarm") is byte-identical to the client typing it.
  - *Script `say 'x'`* (`Scripts/SpeechCommands.cs`): `Player.Say(Config SysColor, x)`, default
    0x0044 [upstream source only: no Outlands capture has a client `0xAD` at 0x0044].
  - The user's macros were recorded in each character's ClassicUO profile `speech_hue`
    (`ClassicUO/Data/Profiles/<acct>/UO Outlands/<char>/profile.json`): `DTF Room` 368 (Logan
    Wolf 0x0170), `Pouch` 690 (Outland Dan 0x02B2), `Disarm` 569 (Satanic Panic 0x0239). All
    client `0xAD` in logs/ (2026-10-17 tally): unencoded lines only in those profile hues
    ("room", "[aspect", "[go"); encoded lines ("bank", "withdraw N") in the profile hue with one
    NUL (typed; no bank macro exists); 0x03B1 only on the pet commands.
- **Agent speech hue is stamped by the proxy** (2026-10-17, ANTICHEAT.md §8.20): `say_unicode`
  defaults to the placeholder hue 0 (`actions.SPEECH_HUE_OWN`); `proxy.SessionTap.speech_hue`
  fills in the client's last typed hue, else `cuo_profile.speech_hue` (account from 0x91, name
  from 0x5D), else 0x02B2; a new learned or profile hue is logged as `ev: speech_hue`. A proxy older than this
  change would forward hue 0: restart the proxy whenever harness code with this change runs.
- **Targeting is Razor-fast:** cursor to answer median 0.23 s after Discordance, 0.44 s after
  Peacemaking, 0.11 s after "All Kill"; the server's cursor follows the skill use (0x12 type 0x24
  "15 0", byte-identical to `actions.use_skill`) in 0.05–0.07 s. He presses hotkeys repeatedly:
  early presses get cliloc 500118 "You must wait a few moments to use another skill." (16×) and
  500119 "You must wait to perform another action." for item uses.
- **Bard texts** (System `0xAE`): prompts "What do you wish to discord? (you may target yourself for
  an area effect or the ground for a group effect)", "… to pacify? …", "What do you wish to
  incite? …" then "Whom do you wish them to attack?" (a second cursor); success "You play
  successfully, disrupting your opponent and weakening them significantly. (1m)" / "(1m 15s)",
  "You play successfully, pacifying your target. (38s)" / "(30s)", "You play successfully,
  provoking your targets. (30s)"; fail "You fail to discord your opponent. You estimate the task
  to be fairly reasonable." (also "somewhat challenging", "beyond your skill"), "You fail to
  pacify …", "You fail to incite anger amongst your opponents. …"; "You must wait a few moments
  before you may provoke or pacify another creature."; "That target is currently under a barding
  break effect."; "Barding break avoided for spare parts" (a Mastery Chain link). Bard targets at
  1–9 tiles; "All Kill" at 9 tiles answered "Target cannot be seen." (cliloc 500237).
- **Order per engagement:** walk in with the pets guarding, Discordance on the mob the pets fight,
  then Peacemaking on it (it stops hitting the pets); Provocation when two more mobs were near;
  "All Kill" at a mob the pets weren't on. 51 of 60 target answers were at a mob already hurt
  with a pet within 12 tiles; the 9 others were openers. **User (2026-10-12):** aggression comes
  from being in a mob's aggro range and line of sight; with pets guarding they engage it rather
  than it engaging the character, while starting with an aggressive act (All Kill, a discord)
  makes the mob aggro on the character. The runner never opens with one.
- **Veterinary supplies** (graphic 0x6496 / 25750, a stack of 59 in a bag): one double-click, no
  target; "You begin using veterinary supplies." → "You finish using veterinary supplies." 5.0–5.3
  s later (43×). Pets healed at 0–2 tiles at the finish (+96/+113/+147 on the gravebug, +17–22 on
  himself: they heal the player too since patch 2023-08-14); "You are too far away from any of
  your followers to heal them." when both were 3–4 tiles off at the finish, so the range is
  checked then. "You or your nearby followers do not require healing." (53×) when nothing needed
  it. The Healer's Codex perk **Without Borders** (+1/2/3 patient range, knowledge #11343) was
  added after this trip and is being levelled (user 2026-10-12): `--vet-range`.
- **Self care:** heal potions (graphic 0x0F0C, 30 in an exceptional bronzehide potion satchel
  inside a bag) at 58–81 % hits; "Potion satchel used." means it wasn't consumed. Refusals named
  the wait ("You must wait 4 seconds before using another healing potion."). The worst moment:
  21:27:50 "The creature strikes ferociously, causing you to bleed!" with a vampire countess and a
  corpsebride on him, 72 → 35 hits in 2 s; he ran 18 tiles while spamming potions (9 clicks, 1
  drunk) and the pets held the mobs. Vampires: "They begin to draw blood from you!".
- **Damage numbers:** `-N` / `+N` overhead (0x1C from the mobile) on every creature, pet and us
  throughout, without `[damage` (which only starts a tracker: "Damage tracker is now running.").
  Poison over a creature or pet: `*looks violently ill*`.
- **Never release a pet** (user 2026-10-12: "make sure we don't release my pets on accident
  they're quite expensive"): pets are released by "<name> release" (patch 2019-06-04) or the
  context menu's Release; Transfer gives one away. The runner says only `tamer.COMMANDS`;
  `ctl act say` and `ctl act menu_pick` refuse anything matching release/transfer.

## Mausoleum facts measured for the simulator (capture 20261012_210013, 2026-10-13)

`harness/sim/calibrate.py` (rules in its output) and `harness/los.py`'s validation; docs/SIM.md uses them.

- **Damage numbers add up to the wiki's hit points**: the "-N" numbers over every creature that died in
  view sum to its wiki Hits + 1 (shadowguard 8001, shade wolf 5001, bound soul 8001, corpse purger 7001,
  corpsebride 6001, spare parts 2501, ...). Corpse gold is the wiki Gold Value (lifted medians within
  -5..+17 %). So the wiki creature pages (in `ctl know`) are the creatures' real stats.
- **Damage-number hues** [INFERENCE]: 89 over creatures for our side's damage; 2417 while two provoked
  creatures fight and on poison ticks; 946 over our pets and us.
- **Outlands 0x77 carries no flags**: a mobile's war mode (0x40) arrives only in 0x20 updates.
- **Our pets' real hits**: the gravebug loses ~4.5 hits per point of its 0xA1 share (450 hits), the phoenix
  ~6.6 (660); levelled, about twice the wiki's tamed base (225 / 350). Attack animations (action 5 / 4)
  every 1.91 s / 1.80 s.
- **The vampire countess transfixes our pets**: after `*attempts to transfix*`, hostile copies named
  GraveRobber / AlaFlambe (notoriety 3/4, `*transfixed*`) appear and fight us (the copy hit for ~32-60).
  A runner must never take a mobile for our pet by name alone: ours are notoriety 2 with their own serial.
- **Aggro**: monsters switched to war mode 1-13 tiles from us (median 7) when the switch was in view;
  almost always closing on a pet. The wiki's flat 20-tile Perception Range (knowledge #4329) is when they
  notice; sight decides.
- **Line of sight**: ServUO's `Map.LineOfSight` rules (eye at z + 14) explain all 61 target answers on
  mobiles (59 accepted, both "Target cannot be seen." blocked: a portcullis at 5244,662 and a stone wall
  at 5265,656). The world model's own z is often stale (no per-step z): take z from the map surface.
- **Levels**: the rune lands at x 5554-5626, y 688-709; a teleporter at (5554,701,20) -> (5547,696,10)
  to x 5506-5547, y 638-706, z -18..10; another at (5521,638,-18) -> (5199,738,17) to the fight level x
  5174-5278, y 646-738, z 0-25 (786 s of the trip).
- **Barding skill**: the trip's 47 ok/fail answers fit the wiki formula `max(S/100 - difficulty/100,
  0.33 S/100)` best at S = 149 (printed 80 + bonuses).

## Logan Wolf's second Mausoleum trip (capture 20261013_173449, analysed 2026-10-13)

The user went in to fight the vampire countess again (docs/SIM.md "Captures wanted"), sang the barding
songs partway, and left 3 minutes later chased by two reds. In the region 17:36:54-17:40:38. Read with
`calibrate.Session` and ad-hoc replays (`replay.timed_packets` + `WorldRuntime`). The phoenix and the
gravebug were his; two bonded black widows in view belonged to someone else (notoriety 2, no command).

- **One countess fight, 17:39:03-17:39:31** (with a vampire bat and a corpsebride), 27 s: ~9,500 damage
  in the "-N" numbers over her.
  - `*attempts to transfix*` at :09.4, :14.6, :19.8, :25.3, :30.8 (5.2-5.5 s apart, as the first trip).
    One worked (:14.6): a copy named GraveRobber (graphic 387, notoriety 3) on the gravebug's tile.
  - **The transfixed pet is out of the fight**: the gravebug made no step and no attack animation
    from :14.6 until the copy's 0x1D at :25.6 (11.0 s); its 0xA1 share stayed 50. The first trip's three
    copies show the same: no animation of their originals from appearing to 0x1D, 9.9-10.8 s each. Their
    originals could still be healed (21:27:18-23: 56 -> 81 %).
  - **At most one copy at a time**: over both fights 4 of 8 attempts with no copy of hers up worked, 0 of
    4 with one up (the first trip's next copies appeared right after the previous 0x1D, 21:27:28.7 and
    21:27:39.5-39.7).
  - The copy went to war mode 0-2.5 s after it appeared (n=4), then walked 6 tiles to the phoenix
    rather than to Logan at 8. The simulator's copy never picks its original.
- **Corpsebride wail**: `*wails*` every 7.5-7.8 s while it fights (25 wails over 4 fights in both
  captures), with -13..-25 at once on the pets beside it (about 18 of 24 chances at 1 tile) and on Logan
  2 of 11 times at 2-3 tiles (-25, -19). Logan acting on the bride made no difference (he had targeted
  0x613c5 2.6 s before a wail that missed him at 2 tiles). Modelled as an area hit on each of us, 75 %
  within a tile and 20 % at 2-3 [INFERENCE].
- **Shadowguard on the gravebug** (17:38:12-17:38:43, alone): -21, -41, -32, -31, -18, -34 (n=6), a
  successful discord from 17:38:27.7.
- **Barding songs** (knowledge #11226/#11233/#11234): the bard skill answered at the ground (0x6C
  type 1, serial 0, a land tile 1-3 from us, graphic 0; `actions.target_xyz` reproduces the live bytes
  exactly) plays a song on us and the followers for 15 minutes:
  - 17:39:22.2 "You are now under the effect of a Song of Discordance (15.0% reduced damage from
    creatures for 15 minutes)."
  - 17:39:51.0 "... a Song of Peacemaking (15.0% healing amounts for 15 minutes)."
  - 17:40:12.5 "... a Song of Provocation (+15.0% damage against creatures for 15 minutes)."
  - 15.0 % = 5 % x effective barding 300 / 100 (#11234). A second song 3.8 s after the first:
    "You must wait a few moments before performing another barding song." (the wiki: 10 s between
    songs, apart from the skill timer). The skill cursor itself came 3.6 s after a song, under the 5 s
    skill gap of a targeted use.
  - The first trip had none: the user forgot them. `loop_hunt --songs` (default on) keeps them up.
- **Two reds**: KalVasKikUrAss and Jimii Hendrix (notoriety 6, [BJV]) came into view together at
  17:40:14.5 18 tiles east, in the doorway (5200, 682) between the countess' room and the gate's.
  - Logan started running 2 s later, south through the nightstalker room (5175, 684 -> 5181, 721),
    the only other way to the gate, with both chasing at 1-6 tiles. A nightstalker and a reanimated
    knight charged on the way. Spells: Corp Por (energy bolt), Des Mani (weaken) twice, Rel Por, Ort Por
    Ylem ("KalVasKikUrAss has applied telekinesis to you."); "X is attacking you!" for each.
  - `[Rope` (an Adventurer's Rope, knowledge #13325: a Teleport, 5 s cooldown) at 17:40:30.4:
    "Where do you wish to traverse to?", a ground target 7 tiles on, (5198, 723) -> (5205, 721). An
    earlier `[Rope` at 17:40:19.0 got no prompt.
  - On the crimson gate at 17:40:36.4; Travel at :37.9; Totem 17:40:38; the rune tome home 4.5 s later.
  - **The pets stayed behind**: the gravebug was fighting shade wolves in the nightstalker room ("You
    are too far away from any of your followers to heal them." at 17:40:28.8); neither was in view at
    Totem or at home. Pets follow a Teleport or rope only on Follow (patch 2025-01-25, #5141).
- **The gate menu opens with the last destination selected**: its radio button is drawn 9724/9721, the
  others 9721/9724 (Prevalia on the first trip's first menu, Totem on this one). With the wanted one
  already selected, Travel (button 2) works on the first menu: Logan pressed it 1.5 s after the menu
  opened. `moongate.travel` now does the same.
- At Totem: "A stationary penalty is in effect (must move 5 tiles to remove)." right after the drink,
  as after every moongate.

## Logan Wolf's full Mausoleum crawl (capture 20261013_204518, analysed 2026-10-13)

"A pretty good crawl" (user). In the region 20:46:00-20:58:44, 12.7 min on the fight level. The user
had added Effective Barding links to the mastery chain just before (user 2026-10-13).

- **Totals on the fight level**: 29 kills by our side (137 an hour; the first trip 115, the simulator's
  crawls 85-89), 26,951 gold lifted, 4 heal potions, 29 vet supplies, no death. Only 5 hits on Logan
  (-48 an apparition, -48 beside a nightstalker, -22, -23 in the countess fight, -24 "Their accursed
  spell weakens you, reducing your melee and spell damage!" beside a shadow minion): 13 a minute. The
  fighting was nearly continuous: 6 engagements, 595 of the 764 s, the longest 237 s and 11 kills.
- **His route** (one loop, then the east): arrival (5199, 738) -> the apparitions south (5208, 716) ->
  the banshee room (5208, 704) -> north (5209, 687) -> west into the countess' room (5188, 682) ->
  **into the nightstalker room** (5174, 683 -> 5178, 691: 4 shade wolves, the nightstalker, a
  reanimated knight, 2 min) -> south (5180, 718) -> east (5200, 723) -> the banshee room again ->
  the countess' room again (a new countess) -> east (5206, 683) -> north-east (5220, 663) -> (5232, 651)
  -> the bound souls' and corpse purgers' room (5258-5278, 653) -> back west (5230, 653) -> (5213, 667)
  -> the gate. Kills per room came as he walked in; he never waited for a respawn.
- **Barding after the links**: every number grew ~18 %, success didn't (fit_bard S 151 on 50 answers,
  149 before). Songs 17.7 % (were 15.0), discord "(1m 11s)" 17 of 18 times (was "(1m)"; "(1m 26s)" on
  a corpse purger just provoked), peace "(43s)" 6 of 7 (was "(38s)"), provoke "(36s)"/"(43s)" (was
  "(30s)"). The scenario's bard block has the new numbers (`links_note`).
- **Songs before the fight level**: Discordance at 20:46:06 on the first level, right after the
  arrival, Peacemaking 14 s later on the way to the teleporter, Provocation at 20:46:54 on the second
  level. A second Discordance at 20:49:36 mid-fight [INFERENCE: a ground target meant for a wolf].
  `walk_in` now plays a due song before each leg.
- **The countess again** (two fights with transfixes): copies at 20:52:51.0 and 20:53:12.5, 10.9 and
  10.0 s to their 0x1D, war mode 2.4 and 2.3 s after appearing, the gravebug without an attack animation
  meanwhile. The transfix itself hit the gravebug -36 at that instant, as on both earlier trips (-36,
  -37, -45): modelled (`pet_damage`). Over 3 fights: 6 of 12 attempts with no copy up worked, 0 of 5
  with one up. A countess also transfixed another player's pet (Broken Covenant) at 20:58:17.
- **Respawns**: the countess at (5174-5176, 681) killed 20:48:10.6, a new one in view at 20:52:34.6
  with Logan in the room since 20:51:35: 205-264 s. A shade wolf 148.5 s (calibrate). The simulator's
  2-5 min fits both.
- **Reanimated knights charge too** (`*charges*` at 20:50:09.6 and 17:40:23.1), with no damage number
  within 0.8 s either time. The runner learns a charger from the overhead anyway (`tamer.chargers`).
- **The nightstalker's charges**: 5 in its fight, 7.6-17 s apart, 3 with a -10..-19 on the gravebug
  within 0.5 s.
- **Out through the crimson gate without stepping on it**: "All Follow Me" at (5208, 685), a run of
  18 tiles at 0.2 s a step, then a double-click on the gate 0x4010E11F from (5204, 702), a tile away.
  The menu opened at once with Totem selected; Travel 0.1 s later; the jump came from that tile, to
  Totem at (3848, 2867) (not the first trip's 3859, 2890: the arrival varies). The rune tome 1.4 s
  later, home (a rental room), both pets along. `leave_by_gate` now double-clicks the gate from the
  tile beside it (the step onto it only without the item in view); the simulator opens the menu on a
  double-click within a tile.

## Aggro management: the user's rules and what the captures show (2026-10-13)

- **User**: "positioning and managing aggro is the number one most important thing in dungeon PvM in
  Outlands": don't pull too many mobs at once; never let a super dangerous mob like a vampiric consort
  aggro while we're alone (not in a group of players); break line of sight strategically. His
  answers: never-aggro list = the vampiric consort only; pull size "weighted by danger"; line of
  sight is "situational" (between two rooms with the pets fighting in room A, stay out of sight of
  room B's mobs so they don't aggro and cast on us; standing out of a caster's sight, e.g. a banshee's,
  while still able to heal the pets is better, all else equal); a consort that aggroes anyway: "I run
  until the consort leashes and I'm in an area that I can manage to fight the mobs without getting
  overwhelmed. When all else fails and I'm getting beat up I will probably head for the gate."
  Built as `aggro.py` (docs/HUNT_LOOP.md "Aggro").
- **The consort on the crawl** (20261013_204518): first seen 20:57:51.6 at (5243, 633), 18 tiles north,
  body 401, notoriety 4; in war mode at 12 tiles (20:57:55.5); at the gravebug 20:58:00, a -71 on it
  at 20:58:08; Logan said "All Follow Me" at 20:58:10.8 and ran west, it chasing in war mode to (5230,
  655), ~22 tiles from where it woke, then out of view; he left by the gate 26 s later. Wiki: 14,000
  hits, melee 60-70, difficulty 168, stealth.
- **The nightstalker room, Logan's way** (20:48:23-20:50:25): in from the north (5174, 685) and held at
  its north edge (5175-5179, 687-692), ~10 tiles from the stalker's spot. Three wolves and the stalker
  went to war 5-10 s after first sight, a fourth wolf 50 s later, the reanimated knight (5176, 705) only
  at 20:50:08 when he walked south: everything came to him in stages. 8 vet channels in 2 min, 794
  damage on the gravebug (37 hits), one on him.
- **The gravebug's Devour heals it mid-fight** (user 2026-10-13 asked whether the simulator counted it;
  it didn't, properly): `*devours corpse*` over it 67 times in the three captures, every 15.0-15.2 s at
  the shortest while a corpse was near, 60 of them with it hurt within 5 s; then "+18" three times 0.5 s
  apart (19 times; "+21" x3 11 times under a 17.7 % Song of Peacemaking), 54 a feed, 2,942 in all
  against the 7,854 damage it took. Knowledge: "Can consume nearby corpses to heal 10% of maximum
  health" (45 of 450; 54 live [INFERENCE: its healing bonuses]), and it may feed on the same corpse
  again. The simulator had it feed twice (36) every 14 s and only out of a fight; now three ticks every
  15 s whenever it is hurt with a corpse within 2 tiles [INFERENCE: the range].
- **Damage on the tank by the number of war monsters next to it** (gross "-N", 0.5 s samples, positions
  from the last update within 3 s). Captures (all three): alone 7.5/s (253 s), two 8.5/s (64 s), three
  or four ~18/s (10 s). Simulator, 4 zone runs of 30 min: first measured 7.3 / 19.6 / 24.2/s, which
  looked like piles at 2.3x live; but those runs died early in a few deadly fights (184 s alone, 40 s
  with two). With the in-fight Devour the same 4 runs lived longer: 3.2/s alone (2,219 s), 5.9/s with
  two (399 s), 23.9/s with three (20 s). The rates depend on which fights the sample holds; there is
  no clear sign the simulator overrates piles (an earlier note here said it did: withdrawn). The live
  sample is thin.

## Logan Wolf's Mausoleum runs of 2026-10-14 (captures 20261014_170144, 20261014_173858)

Two runs, both ended by reds. In the region 17:02:50-17:23:52 (21 min) and 17:39:13-17:47:20 (8 min).

- **Totals**: 43 kills (123 an hour), 30,680 gold, 25 hits (516) on Logan, 8 potions, 45 vet channels;
  then 16 kills, 16,591 gold, 13 hits (272), 2 potions, 26 vet channels.
- **Reds are common here.** Players with notoriety 6 seen in the region: none on 20261012_210013 (14.6
  min) or 20261013_204518 (12.7 min); 3 on 20261013_173449; 4 on 20261014_170144 (two on the second
  level at 17:03, one at the crimson gate at 17:10, Malito at 17:23); 2 on 20261014_173858. Three of the
  five runs ended running from reds.
- **Logan died to Malito** (20261014_170144) in the bound souls' room (5275, 653), ~75 route steps from
  the gate: first seen 17:23:04.8 at 17 tiles; "Malito is attacking you!" 1.4 s later; "Malito has
  applied telekinesis to you." 17:23:07.7; "An explosion potion has stuck to you!" 17:23:08.2; two
  `[Rope` prompts (one jump, 10 tiles, at 17:23:10.4); hits -38 and -41 (the explosions), -31 from "a
  daemon" (summoned), -12; dead by ~17:23:13 ("You decline to report the player." at 17:23:17.9), 8 s
  after first sight. He ran to the gate as a ghost; the pets stayed behind (not in view at Totem or home).
- **The second escape** (20261014_173858): DaRth VaDer VI and Niktiku at 17-18 tiles (17:46:54.5); a
  `[Rope` 3.4 s later (no jump), "All Follow Me" at 17:46:59.4, then a run of ~50 tiles to the gate in
  21 s with three hits on the way (-23, -20 by a shadow minion, -22); out at 17:47:20.
- **Adventurer's Rope** (an item, "adventurer's rope : 15", graphic 0x14FA, used by the `[Rope` server
  command, sent as 0x03 ASCII speech `03 000e 00 0033 0000 5b526f706500`; `actions.say_ascii`): the
  server answers "Where do you wish to traverse to?" and a cursor (type byte 8); the ground target (0x6C
  type 1) teleports us. Jumps of 7, 9, 10 tiles; an 11-tile target didn't jump (20261014_173858
  17:39:56.6); "You are no longer hindered." ~1 s after each answer. Logan used it on all three red
  escapes and once on the second level under fire from unseen casters (-24, -28, -19 at 17:39:58-17:40:03).
  The runner now flees a hostile player with "All Follow Me" at once and rope jumps along the way
  (`loop_hunt.rope_jump`, `--rope`); in the simulator (no players) it saves 0-2.6 s of a 16-28 s run to
  the gate; its point live is breaking contact.
- **The nightstalker room from the south** (20261014_173858): in via (5185, 722), held at its south edge
  (5180, 713-714) ~35 s: two wolves first, then the knight (it charged: -32 on Logan at 17:41:16.6, the
  first knight Charge seen to hit), the nightstalker, a wolf, the countess. The same entrance the
  simulated loop uses; holding there worked live. The scenario now gives the knight a Charge and
  `tamer.CHARGERS` has it.
- **Buff potions**: "You increase your damage resistance to creature-casted spells." on arrival at the
  fight level (17:40:28.4) and "Your magic resist potion has worn off." 120 s later; "You may now use a
  strength potion." twice (17:42:27.7, 17:45:05.2).
- **Songs end with a message**: "Your Song of Discordance effect ends." 904 s after it began
  (17:03:14.2 -> 17:18:18.0), likewise Peacemaking and Provocation; `tamer.Team.update` now marks the
  song over. The second run's songs printed 16.8 % (17.7 % in the first) [INFERENCE: a gear change].
- **The elder vampire countess**: fought in the north room (5201-5210, 640-650) with ~12 other players around;
  it also transfixes ("*attempts to transfix*"). A "A contested boss has spawned in The Mausoleum!" notice came
  at 17:45:36.9 during that fight, but the elder vampire countess is a regular mob, not the contested boss (user
  2026-10-15; this note had taken it for the boss).
- The countess transfixed both pets in turn on the first run: the gravebug at 17:18:25.1, the phoenix
  at 17:18:35.8 (10.7 s later, the first copy just gone), as the one-copy-at-a-time model has it.

## Logan Wolf's run through Maus 4 (capture 20261014_210020, analysed 2026-10-14)

12 min 18 s in the dungeon (21:01:38-21:13:56), 22 kills, 7 hits (163) on Logan, 26,438 gold banked; all
three songs up the whole time (16.8 %).

- **Maus 4** (the dungeon's 4th level): in by stepping E at (5253, 736) on the fight level (21:09:31.5: a
  0x21 deny there, then a self 0x77 at (5697, 713, 0)); he stood near the arrival 50 s, walked north
  (z 0 -> -18 between y 673 and 669), then W along y 627 to (5700, 627), which put him back on the fight
  level at **(5266, 627, 0)** (21:10:54.9; a deny, then 0x77). Box walked: x 5697-5719, y 627-715. No fight
  there: other players were clearing it (darkscales, heralds of night, muddled vampire hunters, incubi,
  fleshweavers, meat puppets, a manticore, two vampiric consorts). The walk back out through the door he
  came in by failed: the proxy had left the client a tile off the server (docs/MOVEMENT.md "client resets
  its own walker"); fixed 2026-10-14.
- One more teleporter on the second level: (5512, 669, -10) -> (5515, 659, -10) at 21:02:19.6. The crimson
  gate took him to (2212, 586, 30), not Totem.
- **The consort and the forgotten souls** (5235-5252, 624-632, around (5250, 630); the bound souls' room's
  west side): 4 forgotten souls (#15327: 8,000 hits, MeleeMage, Magery 125, spells 25-35) and the consort
  (#11046: 14,000). Totals of our numbers to each death: 8,001 a soul, 14,001 the consort (the wiki's hits
  exactly). The consort: discord 21:11:08, peace 21:11:22, "All Kill" twice, a provoke of a soul onto it
  21:11:46.4 (after three fails and two "wait"s); dead 21:12:03 to the pets, the provoke adding nothing.
  GraveRobber took 988 in 46 numbers there; the phoenix 14; Logan 20.
- **The forgotten souls' fire**: 0x6D70 ground items (hue 0x08D8), 7 of them, each on the tile of whom a
  soul's spell hit at that instant (13-20: the gravebug 6 times, the phoenix once), gone after 5.4-5.7 s.
  One cast at 21:12:11.7 was a 3-target fireball (0xC0 gfx 0x36D4 from the soul to Logan, the phoenix and
  the gravebug; -20, -14, -16). **No tick damage**: the phoenix stood on one 5.7 s, Logan beside it, and
  neither took a further hit; the gravebug's numbers under one are its melee rate. The user's rule (don't
  stand in fire) is in the runner anyway: never a tile to step to, costly to route through, stepped off
  when under us (`tamer.FIRE_GRAPHICS`, `Team.out_of_fire`). Banshees cast the same 3-target fireball
  (-19, -20 on Logan) and leave no fire.
- Other short-lived ground items: blood 0x1645 / 0x122A-0x122F (~5.3-5.5 s); 0xB266 and 0xAC09 (1.0 s,
  z+10 on the phoenix: its effects [INFERENCE]); 0xD2A4-0xD2AE an animated item.

## Logan Wolf's death, corpse run and a pet's vet resurrection (capture 20261014_221341, analysed 2026-10-14)

22:14-22:38, 38 kills, 40 hits (962) on Logan, 33,232 gold at the end.

- **His death** (22:17:57.7, (5216, 663), the north vampire rooms): 100 -> 0 in 5.5 s while walking to
  corpses with the elder vampire countess transfixing: a vampire bat -20 with a bleed, a bleed tick -6, a
  shade wolf -46, the bat -15, -15. No potion in the last 30 s; a vet channel begun 1.8 s before the end.
  The server: corpse 0xF3 + 0x89, 0x2C 00 then 02, self body 0x192 at 0 hits, 0xAA 0, a death shroud
  (0x2E layer 0x16, 0x204E), cliloc 1019048.
- **The corpse run**: as a ghost to the crimson gate (its gump opened by walking onto it), Totem, the
  healer Jacinda's "Resurrection" gump (Accept, button 1) 22.4 s after the death: alive at 100/100 with
  a robe, "Your mount returns.". Home by recall, then the stablemaster's "Retrieve Lost Followers": "You
  retrieve 2 of your followers." (the pets had stayed in the Maus, alive, at 4 % and 10 %); vet; back in
  and his own corpse looted (0x24 open, 0x07/0x08 drags; 4,360 gold) 22:20:38-22:20:48.
- **The elder vampire countess copies both pets** ("*transfixed*", hostile notoriety-3 copies named as
  the pets).
- **A reanimated daemon** (body 0x9, #15334: 16,000 hits, MeleeMage, magic resist 200; two seen at (5239-
  5242, 699-701)): its bone burst (action 10, ~50 0xC0 gfx 0x091D around it out to ~5 tiles) hits each of
  us there once: the gravebug -94/-97, the phoenix -104/-107, Logan -39/-49 at 5 tiles (3 bursts, 15.4 s
  apart in the second fight). The first died in 36 s under discord + peace (no burst after the peace),
  +2,240 gold. The second killed the gravebug.
- **The corpse purger's disease** (22:33:52-22:34:17.6): it picked Logan from 10 tiles (no pet on it),
  its barrage hit him 22, 26 and 26, the 2nd and 3rd with "You have contracted a foul disease!"; each
  infection ticked -21 four times ~5 s apart, the two side by side (168 in all, down to 21 hits); "You are
  no longer diseased." at the last. No poison flag, no 0x17. He drank potions at the 10 s cooldown (+39,
  +43, +50), the vet healed him (+28, +23), a bandage; no cure. The simulator now models it.
- **GraveRobber died and the vet supplies brought it back** (22:35:53.1): 0xAF with a corpse, then the
  server re-sent it (0x20, 0xA1 0, 0xBF sub 0x19 dead; its body stays 0x183) before the 0xFF sub 0xDEAD.
  Logan said "All Follow Me" (the ghost followed); the channel he had begun 3.1 s before finished 2.0 s
  after the death: "You are able to resurrect your follower.", 0xBF sub 0x19 alive, 31 %, then +166.
  No gump. The world model used to drop the ghost at the 0xDEAD (and bring it back alive at the next 0x77);
  it keeps it now (`WorldRuntime.mobile_death`), and the runner vets a ghost instead of leaving
  (`tamer.RES_TRY_S`).
- Overweight: "You are carrying too much weight... (475/450)"; he dropped 1,999 and 1,000 gold.
- A 0.3 s server teleport to Maus 4 and back at 22:33:47 with no step of ours [INFERENCE: a server glitch].

## The pets runner's first live run: dead in 46 s (test shard, 2026-10-15, capture 20261015_090607)

Task hunt-20261015-091609-dea7 (`run hunt --pets --crawl --heal-in-place --leave-recall <tome> --leave-gate
5203 703 --gate-dest Totem --timeout 900`) from the fight level's arrival (5201, 737), full hits, both pets.
The proxy had been restarted on 80ef85a (the door fix). `ctl --char "Logan Wolf"` answered "unknown
character" though `status` listed the session; without `--char` it worked.

- 09:16:21 it targeted an apparition 8 tiles off and walked 2 steps toward it; the apparition came next to
  Logan: -46 on him and -38 on the phoenix at the same instant (action 6), and the same 8.8 s later (-47,
  the gravebug -36): an area hit within 1. Potions at :25 and :38, a vet channel.
- 09:16:32 at 53 % the retreat backed him 7 tiles west, to (5205, 726): 10 tiles from a second, idle
  apparition in sight of it; it came straight for him and hit him -49 from on his tile (09:16:39.6).
- 09:16:40 at 37 % the 50 % leave fired; 23 steps to the gate, the pets left guarding (fighting both
  apparitions 20 tiles south). At the gate 61 hits; "All Follow Me" at :48, then `follow`'s fall-back
  stood 4 s waiting for pets that didn't come (no attacker on us to back away from), reading the state
  past the guards, so the potion ready since :48 (29 in the pack) was never drunk. A banshee 5 tiles off
  (idle at 16 when the walk began) cast -25, -24, -14; dead at 09:16:55.5, 0.4 s before the runner's
  Totem pick. The run ended "recall home failed (moongate: the menu didn't come back...)".
- The apparitions blinked out of view twice (0x1D) at 1 tile; the runner rightly didn't count a kill.
- Fixed (user 2026-10-15: "its fine if our pets stay or die in the dungeon, we can easily retrieve them at
  the stablemaster"; "while our pets are fighting the apparition, another mob that sees us will aggro on
  us directly"): hurt beside the gate, "All Follow Me" and through at once (`Team.follow(wait=False)`);
  every wait in a leave reads the state through `HuntLoop.state` (its guards drink); the back-off tile is
  the one the fewest idle monsters see (`Team.retreat_tile`, aggro.View.stand_score); apparitions and
  reanimated daemons are area hitters to stand off (`tamer.AREA_R`: 2 and 6); the simulator's apparition
  has the hit (`burst`, radius 1, 8.8 s).

## The pets runner's second live run (test shard, 2026-10-15 11:58-12:02, capture 20261015_115604)

Task hunt-20261015-115818-98a2, same command, on 1e6d467; stopped by hand at 12:02:44 (the user: Logan stood
next to an apparition "getting blasted for no reason"). 7 kills logged (5 real: apparitions and banshees),
no death, the lowest 52/100.

- **The apparition's hit reaches 2 tiles**: Logan -39 at 2 tiles at 12:02:06.1 (the gravebug -37 beside
  it), -48 at 0; no pet hit at 3-5. The runner stood off at 2 (`AREA_R` 2, from the first run's 0-1 hits);
  now 3, and the simulator's burst radius 2.
- **The songs were replayed though already up**: all three ran from before the run (Discordance 702 s
  left); the runner knew only the songs it played itself. Our song buffs are icons 59 Discordance, 58
  Peacemaking, 57 Provocation (status `buffs`, with ends); `Team.song_left` reads them. The user: the songs
  are fine; discord/peace/provoke at a mob must target the mob, once the pets have its aggro (the runner
  does that: 11:58:48, 11:59:19/:26, 11:59:51, 12:00:13, 12:02:03/:11 at mobs the pets fought).
- **A kill counted twice** (12:00:22 and 12:02:18): a mob that left the view alive, then died, got its
  0xAF kill and then its 0xDEAD one (`lost_alive` kept); fixed in `on_kill`.
- The second apparition again came onto Logan's tile (12:01:58, -48) from 7 tiles as it woke.
- The user then fixed Logan's Healer's Codex and taming bestiary setup (2026-10-15 12:05) [INFERENCE: the
  vet range and pet perks may change: `--vet-range` follows the codex's "Without Borders" tier].

## The third live run: the apparition comes back on the tank (test shard, 2026-10-15 12:08-12:10)

Task hunt-20261015-120822-42d7 with `--vet-range 5` (Logan's Healer's Codex "Without Borders" at tier 3:
+3 tiles, knowledge #11343), stopped by hand at 12:10 to change the spacing; no death, 2 kills.

- An apparition that was attacking Logan came onto his tile from 7 tiles in one move (12:08:36.3 at
  (5217, 723), 12:08:37.6 on (5210, 725)) and hit him -45 with the phoenix -38.
- It blinked out (0x1D) twice for 0.6-1 s and came back on the gravebug's tile each time; the gravebug held
  its tile meanwhile. At 12:08:53 the patrol walked Logan to 2 tiles from the gravebug the moment it blinked
  out; 0.6 s later it was back there and hit him -46 (the gravebug -37, the phoenix -38).
- The user: "The apparition is going to pop out and AOE next to our tank so we should not be standing right
  next to GR"; "Since our healing distance is 5 now I don't see a good reason to be standing next to the
  tank while it's fighting". Now `Team.spacing` (HUNT_LOOP "Off the tank") and the crawl holds after a
  target leaves the view alive.

## The fourth live run: up the stairs and dead in 25 s (test shard, 2026-10-15 12:33, capture 20261015_115604)

Task hunt-20261015-123341-2b22 from (5201, 741). Logan had stood there, so the runner walked off the
Stationary Penalty first; its first step went onto (5199, 738), the Mausoleum fight level's stairs beside the
arrival, up to level 2 at (5522, 638, -23) with both pets. "No route" on level 2 aborted the run; he stood
there at 64/100 next to an abomination and died at 12:36:05 after the user's tries to get back down.

- **The stairs are a band beside the arrival (5199, 737)**: steps onto (5199, 738), SE to (5200, 738) and NE to
  (5200, 736) all went up; on level 2 (5522, 637) (S from 5522,636, N from 5522,638) comes down to
  (5199, 737, 17). Four bounces at 12:35:40-42; the client restarted its step count each time (the proxy's
  `c2s_client_reset`), asked for a resync (0x22) at 12:35:42.6, and then sent no walk at all, alive or dead,
  until an agent step's re-anchor (fabricated 0x21, 12:37:24) [INFERENCE: the client's walker waited on the
  resync; the user's client may have needed a relog].
- The runner knew no teleporter there: it learns them only by stepping onto one, and Logan always walked in
  by hand. Now `harness/data/teleporters.json` seeds the Mausoleum's (with the evidence for each tile),
  `Mover.teleporter_tiles` adds them to the store's, and a walk that is teleported anyway steps back through
  a known one beside it that leads where it came from (`Mover.step_back`).
- **`./ctl.cmd` in the agent's shell ran the worktree's ctl** (the shell resolves `./ctl.cmd` against its
  configured working directory, the logan-tamer worktree, whatever `cd` came before): all four live runs
  today used the worktree's code (the same commits as main) and its own fresh memory store
  (`harness/data/harness.db` there, 176 KB), not the main store. Start live runs with
  `python "<main checkout>/harness/ctl.py" run hunt ...`.
- **2026-10-17 14:27 (live, `hunt-20261017-142351-97c8`): the same stairs again, a fourth tile.** The first
  live walk-in that ended on Maus 3 (the runner's own, 3.5 min with eight kills on the way) logged "no step for
  211 s: repositioning" on arrival: `Stationary.still_s` counted from the runner's start because its first
  look, the first `tick`, came only after the walk-in, and the first look set the baseline without taking
  the walk's steps. The reposition's out tile (5199, 736), one N of the arrival, was a teleporter nobody
  knew (now in teleporters.json). A walk *aimed* at a tile doesn't step back from a teleport, because a
  walk-in aims at teleporters, so the run aborted on level 2 with "back: no route". Fixed: the clock counts
  from the Stationary's construction, and `walk_off` calls `Mover.step_back` after a teleport on the way out
  (`harness/test_stationary.py`, failing before). The user put Logan back on Maus 3 by hand. A run started
  there with `--spot 5199 737` (the arrival every visit row there is keyed by) hunts without a walk-in.
- **The resumed run's red escape and the first automatic drop-off (14:37, `hunt-20261017-142916-e70e`,
  user: "Beautiful escape from that red!").** Red Chaos Agent came into view at 17 tiles at 14:37:18 (10 kills,
  8785 gold that visit). "All Follow Me", then a rope 9 tiles towards the gate: he wasn't hitting yet, so
  `rope_worth` allowed it. Eight steps to the moongate at 14:37:23, Totem, the tome home at 14:37:26, both pets
  along. Home, the steward let him into the room at 14:37:31 and `dropoff.run` took the 10607 gp carried to 0
  by 14:37:56, with both pets beside him. Then the runner waited out --pk-wait before going back in. Eight
  seconds from sighting to the gate.
- **The first live planned return (15:38, `hunt-20261017-151852-2301`).** At 15:38:22, between fights, the
  runner left with 28287 gp carried (gold 15587, items 12700; 1144 of it from before the visit) against V* 26710:
  17 kills in 17 min. 47 steps to the gate at a walk in 20 s, Totem, the tome home at 15:38:48. Into the room at
  15:38:52 and the drop-off to 0 by 15:39:18, then the recall back in at 15:39:23, the run fighting on the walk-in
  by 15:39:37. The trip home took 26 s and the drop-off 30 s, against the 6 min the first model assumed. That is why
  `T` is now measured (`out_s`, `elapsed_s`, `in_s`; docs/HUNT_LOOP.md "Taking the loot home").
- **The 15:43 disease and the user's takeover (same run).** Zone 3, around (5250, 653). Other players were
  fighting corpse purgers there (Immunize, Combat Medic and Triage buffs on three player serials).
  - 15:43:10: the runner held for a purger in sight at 10 tiles ("letting it come").
  - 15:43:13.5: it gave up the hold and set out on an 82-step patrol west.
  - 15:43:14: the purger's barrage hit Logan -23 and -28, and "You have contracted a foul disease!". The disease
    ticked -20 at :19, :24, :29 and :34, and "You are no longer diseased." came at 15:43:34.4, the 20 s running out.
  - The patrol walked on for 5 s, until the first tick, because `HuntLoop.attackers` counted no ranged monster.
    It knew only swings, war-mode monsters beside us and casters within 10. The wiki marks the purger "ranged"
    (Corpse Barrage up to 12 tiles), and the calamity and eldritch experiment too.
  - The heals: a potion at 30/100 (15:43:19.8). A flight from an elder vampire coming at 16 tiles
    (15:43:21-24, "All Follow Me"). The vet supplies on Logan (15:43:24, +23 at :29.96, and again at :30.6).
    Another potion at :33.7.
  - The user took control at 15:43:32 (the agent gate closed). By 15:43:42 he was at full health ("You are
    already at full health.").
  - 15:43:52: the run's --timeout 1500 came up, and its planned leave set out for the gate with the pets on
    guard (leave_by_gate: "All Follow Me" only beside the gate). The user took over again at 15:44:02, and the
    run sat paused in that walk until `ctl stop`.
  - Fixed: a ranged monster (`aggro.ranged`) in war mode within RANGED_REACH 13 while we take damage is an
    attacker, so the patrol stops at the first hit (`harness/test_attackers.py`).
- **The user's answers (2026-10-17).**
  - "Yes all follow me to run out, not all guard." `leave_by_gate` now says "All Follow Me" at the start of
    every walk out. A planned leave walks like the walk-in: attacked, the pets guard and Logan stands until
    it's over (`walk_in_fight`, "on the way out"); a pet over 8 tiles off is waited for (`wait_pets`
    `required=False`: after 25 s the walk goes on and waits beside the gate).
  - "I don't think it was 25 minutes into that trip": right, `--timeout` counted from the run's start, every
    visit together. The run started 15:18:52, the first visit took 17 min, the trip home 1 min, and the second
    visit had been on Maus 3 2.5 min. "I don't think we need a timeout in general now that we have the rules to
    leave when loot accrues": `--timeout` now defaults to 0, no limit, and the OVERSEER Maus command dropped
    `--timeout 1500`.
  - Simulator, 48 seeds (17-64), 20 min each, fresh stores:

    | Walk out | Deaths | Deaths on a planned leave | Pet deaths | Damage on Logan | Gold |
    |---|---|---|---|---|---|
    | Guard (before) | 31 | 6 | 39 | 29701 | 922k |
    | Follow, stand and fight when attacked (shipped) | 34 | 11 | 42 | 31580 | 957k |
    | Follow, "All Guard Me" and walk on when attacked | 35 | 9 | 40 | 30674 | 931k |

    A planned leave counts as one whose why is time up or the return rule. In the shipped variant three of the
    runs ended on the simulator's hard timeout (1200 + 180 s) mid-fight on the way out, which a live run without
    `--timeout` doesn't have. [INFERENCE] Follow costs the simulator a few deaths on the walk out, because the
    attackers chase Logan instead of the guarding pets. That is within the noise of 48 runs, and it is the
    user's call.
- **16:38, `hunt-20261017-161210-a590`: diseased, a flight with no heal, through the gate by foot.** The user: "I
  took control, Logan was standing still and slowly walking while dying", then "We absolutely must vet while
  fleeing".
  - 16:38:24-32: he stood, the pets on guard, "waiting for a shadowguard to come on", Discordance up.
    "You have been ensnared and diseased!" at 16:38:25.2, -24 every ~5 s until 16:38:45.4. One potion at 76/100.
  - 16:38:32.9: an elder vampire countess came at 11 tiles in war mode: "All Follow Me" and a run of 9 s, steps
    every 0.2 s. Hits went 86 to 11 (-24, -30, -24) with no potion (cooldown) and no vet channel.
    `drink_on_the_run` acted only on walks out (`walking_out`), not in `Team.evade`.
  - 16:38:41.8: the hits leave at 11/100. A potion (+53), then the vet (begun 16:38:43.97, +32 at 16:38:49.21
    after 25 running steps: a step doesn't stop the channel).
  - 16:38:56.8: the walk to the gate's approach tile stepped onto the gate's tile (5203, 703). At 16:38:57.1 he
    was at Totem (3848, 2867). The walk's stop then read "healed to 93/100", the leave was called off, and the
    run went back to hunting at Totem ("Nothing to patrol to; waiting") with the pets in the Mausoleum. The user
    recalled home (16:39:03 "Kal Ort Por") and brought him into the room, and the pets came along. The run idled
    there until `ctl stop`.
  - Fixed:
    - Heal on the run in every flight, a monster's included: the vet supplies whenever we're hurt at all
      (`Team.vet(us_below=1.0)`), a potion below --heal-at, no area peace while running from a monster.
    - A walk to the gate that ends more than 30 tiles from it is the gate taken (`HuntLoop.through_gate`).
  - Simulator, 48 seeds, 20 min: deaths 34 to 33, deaths on a planned leave 11 to 8. No run evaded a monster, so
    the evade change isn't exercised there [INFERENCE: the simulator's elder vampires don't chase like this one].
- **The 16:12 run's holds** (user: "Logan is waiting for mobs that can't see him through walls to 'come on'").
  Replaying `session_20261017_155426` at each of the 17 "letting it come" holds (a throwaway script: the held
  type's nearest idle mobile, `los.Sight` from it to us):
  - Every held monster was in sight by the server's rule (only Window|NoShoot block).
  - 11 of the 13 within 10 tiles woke in 0.5-3.5 s. Two weren't found in the replay.
  - Three never woke: a shadow minion 12 tiles off (16:30:05) and two at 13 (16:33:18-24, z 15 under us at
    z 25).
  - The lines cross Mausoleum wall pieces flagged Wall alone (0x00D1, 0x00D9, 0x00DA; 0x00C5 carries
    Wall|Impassable|NoShoot), which the server's rule sees through. With Wall blocking too, the 16:30:05
    minion is out of sight, and so is the 16:38:21 shadowguard, which woke in 0.7 s anyway.
  - So the hold now counts only monsters within HOLD_R 10 that see us with Wall blocking too
    (`aggro.View.wakes(strict=True)`, `los.Sight(walls=True)`).
  - Simulator, 48 seeds: deaths 33 to 32, but damage on Logan 31916 to 40957. The simulator's monsters see
    by the server's rule (world.py `can_see`), so there they do see through those walls. Which rule the
    live monsters use is the open question [INFERENCE: the user's observation says Wall blocks].
- **Visits lost to `ctl stop`.** The store had no row for the 15:41 visit (stopped at 15:44) or the 16:17 visit
  (stopped at 16:39: 25 min, 22.7k gold, the hits leave), so the return model still read 96 min at 17:05. The
  wrapper's `child.terminate()` is TerminateProcess on Windows: `main`'s `finally: end_visit("stopped")` never
  ran. Now the hunt runner's `watch_stop` thread (under task_wrap) polls the stop key every 0.5 s and raises
  SIGINT in the process. `signal.raise_signal` wakes a `time.sleep` on Windows; `_thread.interrupt_main`
  doesn't, a smoke waited out the whole 30 s sleep. The KeyboardInterrupt runs the finally. The wrapper waits
  `SELF_STOP_S` 5 s for the child before terminating it.
- **Targets behind walls** (18:15 run, `hunt-20261017-181520-e0ca`, capture 20261017_181210; user: "The runner is
  still 'waiting for <mob> to come on' when the mob can't see Logan through walls"). The 16:12 fix covered the
  patrol's hold ("letting it come"), not the fight's wait: `Team.fight` approached a picked target to
  `APPROACH_R` 6 by distance alone, then waited `PIN_S` 15 s and dropped it. Replaying the capture (a throwaway
  script, `los.Sight` from the target to us at 3 s steps):
  - 18:17:02 a shade wolf, 18:19:44 a vampire bat (z 25 over us at z 0), 18:24:48 a tormented soul: each
    idle 6 tiles off for the whole 15 s, out of sight by the server's rule too (blocked at (5183, 711),
    (5178, 686, 28), (5246, 666)).
  - 18:17:17 a reanimated knight: 6 tiles, out of sight, then in war mode at 4 tiles in sight from 18:17:25
    without hurting anyone; dropped at 18:17:33. Not this fault.
  - Now an idle target within `APPROACH_R` that doesn't see us with Wall blocking too (`Team.unseen`) is
    passed over at once for `SKIP_S` 60 s; the patrol meets it later through the hold and the pull spot.
  - Simulator, 48 seeds (17-64), 20 min, with the gate fix below in every row but the first:

    | Variant | Runs with a death | Deaths/h | Gold/h | Kills/h |
    |---|---|---|---|---|
    | Before | 31 | 2.57 | 74.3k | 84.8 |
    | Gate fix only | 32 | 2.60 | 74.2k | 84.2 |
    | Walk on into its sight (walk_to 1, stop once it sees us within 6) | 41 | 4.17 | 66.7k | 77.6 |
    | Pass it over (shipped) | 31 | 2.46 | 71.8k | 81.3 |

    [INFERENCE] Walking round the wall shows Logan to the target, and to whatever is in its room, before the
    pets: the "whoever it sees first" death.
- **A leave from far off the gate was "through it" before a step** (same run, 18:28:22-45). The hits leave fired
  at (5261, 652), 9/100, 58 tiles from the leave gate. The 16:38 fix's `GATE_THROUGH_R` read any position more
  than 30 tiles from the gate as having come out of it, so the walk's first stop said "through the gate"
  without a step, and the runner recalled inside the dungeon four times ("no arrival"), gave up after the 20 s
  escape budget and aborted with Logan at 9/100 there. Now only a jump between two reads from within 2 tiles
  of the gate to beyond 30 counts (`leave_by_gate`'s `through`). test_sim `test_leave_from_far` (failing
  before). The simulator had the same fault on every time-up leave from zone 3 ("recall home failed
  (restricted)").
- **No recall inside the dungeon, whatever asks for it** (user 2026-10-17, after the 18:28 run: "The runner is
  also trying to recall out of the dungeon for some reason, which is not allowed"). The gate fix above removes
  the cause seen live; as a backstop `escape.recall` now raises RecallError before the book opens when we stand
  in a known dungeon box more than `recall_r` 2 from a gold moongate (`escape.recall_forbidden`, reading the
  Mausoleum's `dungeon` block in harness/data/sim/mausoleum.json, now with `name` and `facet` 0). It covers
  loop_hunt, loop_lumber and `ctl recall`. Only the Mausoleum is known (box 5150-5760 x 600-760: all four
  levels walked so far); other dungeons need their own block. test_escape `test_no_recall_in_dungeon`.
- **Dead 2 tiles from the gate (18:50 run, `hunt-20261017-185015-7931`, capture 20261017_183126; user: "We were at
  the gate and Logan just stood there dying instead of going through").** `ctl stop --after-trip` at 19:02:59, the
  planned walk out from (5265, 652). Replayed:
  - 19:04:08.07 the last step, onto (5205, 701), 2 tiles from the gate (5203, 703). No agent packet for 8.5 s: a
    humanize walk pause (`Human.after_step`: a 3-9 s look-around or a long micro pause). The hunt runner never sets
    `Mover.danger`, the only thing that stopped those pauses (the lumber guards' zones).
  - Meanwhile an apparition in war mode came from 6 tiles (19:04:11.5) onto his tile (19:04:14.0): -26 at 14.3.
  - 19:04:15 the walk's stop saw the hit: "attacked on the way out; the pets guard" (`walk_in_fight`), "All Guard
    Me" at 16.5, then -44 at 17.1, -32 at 18.7: dead at 19:04:18.7, still on (5205, 701).
  - Fixed: `Mover.no_idle` (host predicate) skips walk pauses and sidesteps; the hunt runner's (`HuntLoop.no_idle`)
    holds while walking out or with an attackable monster in war mode in view. Attacked on a planned walk out
    within `GATE_DASH_R` 8 of the gate: "All Follow Me" without the wait and through the gate at once, no stand.
    test_mover `test_no_pause_in_danger`, test_sim `test_gate_dash` (failing before: a stand until the 600 s
    timeout).
- **The runner was person-paced where Logan uses hotkeys** (user: "the runners actions are just so slow compared to
  a human with Razor macros"). Cursor to answer: runner 1.08 s median (n 59, 20261017_183126), Logan by hand 0.38 s
  (n 69, 20261012_210013) and 0.26 s (n 106, his own play in the 18:31 session); skill to skill 2.44 s (runner) vs
  0.71 s (him). Every fight action now waits humanize `hotkey` 0.25 s / `hotkey_aim` 0.3 s (ANTICHEAT.md §8.14).
  Simulator, 48 seeds (17-64), 20 min, both fixes against 53bcdd3:

  | Code | Runs with a death | Deaths/h | Gold/h | Kills/h | Runs with a pet left behind |
  |---|---|---|---|---|---|
  | 53bcdd3 | 31 | 2.46 | 71.8k | 81.3 | 0 |
  | No idling, gate dash, hotkey pace | 25 | 2.00 | 75.7k | 85.6 | 2 |

  [INFERENCE] The two pets left behind are gate dashes that went through without them; the stablemaster's "Retrieve
  Lost Followers" brings them back (user), and the overseer stops on `pet_left_behind`.
- **A minute standing at a waypoint under a ledge (21:07 run, `hunt-20261017-210718-eae4`; user: "Why is Logan
  standing still for a full minute now?", then "it doesn't understand the z-levels put the creatures out of sight
  above it").** 21:10:37-40 a vampire countess and a vampire, "8 tiles" off, were picked and at once passed over as
  out of sight; 21:10:42 Logan reached the waypoint (5198, 642) and stood there until 21:11:38, when their 60 s skips
  ran out, they were picked and passed over again. The live state then: the vampire at (5200, 648, 45), Logan at
  (5200, 647, 25), one tile apart on the map and 11 steps apart by the stairs. Two faults:
  - `crawl.observe` counted every wanted monster within --pull-range as a target in reach, passed-over ones too,
    so the waypoint stayed "productive" and never depleted.
  - Targets were by map distance alone: one on a ledge 20 above was "1 tile" off.
  - Fixed: a target needs a walk to beside it at its height within `FLOOR_DETOUR` 4 steps more than the beeline
    (`HuntLoop.on_our_floor`, a bounded `pathfind.plan`, cached per both tiles) when its surface is more than
    `FLOOR_Z_TOL` 10 off our z, unless it attacks; the crawl's target-in-reach mark needs the same and no skip.
    test_sim `test_ledge_target_not_picked` (failing before). The first version demanded the short walk at our
    height too (a detour round a wall); the height-only rule keeps same-floor targets as they were.
  - Simulator, 144 runs (seeds 17-160, 20 min), against 5a26e98: deaths 2.04 vs 1.97 a game hour, runs with a
    death 79 vs 75, gold 78.9k vs 78.5k an hour (noise: the same code gave 25 and 30 runs with a death on two seed
    sets), runs with a pet left behind 0 vs 6.
- **The same run's end: two reds, hamstrung 6 tiles from the gate, dead (21:16:48-55.9).** Fleeing an elder
  vampire, then "red player 0x2a377 in view at 18 tiles": "All Follow Me", a rope 9 tiles (landed), a potion
  74 -> 86, the vet supplies on the run, the walk to the gate at run pace. 21:16:52.4-55.3 the steps went out as
  walks (0x02 direction without 0x80, 0.41 s apart): stamina at 1 or less, which is what a hamstring does for 3 s
  [INFERENCE from the 2.9 s window; the stock client walks then too: `stamina_ok`]. Dead at (5207, 699) at
  21:16:55.9; the Report Murder gumps named Ted Bundtcake and "This Game SuX". No runner fault found in the flight.
  - Before it, 21:12-21:16: 3225 "patrol to ...: stopped (a elder vampire would see us)" lines. `pull_check`'s
    never-aggro reason fired whenever one saw us at all, so a patrol stopped before its first step and the crawl
    retried at once (no sleep), standing in the elder vampire's sight. Now the reason fires only on a step that
    brings a new one into sight or goes nearer, and the crawl rests that waypoint and picks another.
    test_sim `test_never_aggro_walk_out` (failing before). Simulator, 144 runs (seeds 17-160, 20 min), against
    dbfcd9c: deaths 1.95 vs 2.04 a game hour, runs with a death 76 vs 79, gold 80.6k vs 78.9k an hour.
- **Bard skills at targets out of sight** (user 2026-10-17: "The runner needs to do better at disco/peace/provo'ing
  mobs. A lot of times it tries to bard a mob that cannot be seen and fails"). Today's hunt logs: 209 successes,
  89 failed rolls, 63 presses refused by the skill timer, **44 "out of sight or reach"** (the same target again 10 s
  later, up to three times). Replayed every agent target answer in 20261017_181210, _183126, _210503 (a throwaway
  script: our tile and the target's from the world model, z from the map, `los.Sight`): all 17 "Target cannot be
  seen." (500237) were out of sight, 78 of the 79 accepted ones in sight; the same either way round on this data, but
  the target check is RunUO's `from.InLOS(target)` (us to it; the simulator's too) and the line isn't symmetric at the
  ends: a tile in the test geometry saw the purger one way and not the other. Now a skill goes only at a target in
  our sight (`Team.bard_sees`), a provoked pair both; a due one unseen gets a step to a tile that sees it
  (`Team.bard_step`, up to 2 steps, the reposition's rules, no more eyes on us than here; checked by hand in the
  simulator: (5266, 652) to (5268, 652), then in sight). test_sim `test_bard_sight` (failing before).
  - Simulator, 144 runs (seeds 17-160, 20 min) against 6841b33 (76 runs with a death, 1.95 deaths and 80.6k gold
    a game hour):

    | Variant | Runs with a death | Deaths/h | Gold/h |
    |---|---|---|---|
    | In sight only, no step | 79 | 2.04 | 81.5k |
    | Step allowing REPOSITION_GAIN more eyes | 85 | 2.27 | 78.5k |
    | Step only where no more eyes (shipped) | 75 | 1.90 | 81.4k |
- **Monsters fighting other players** (user 2026-10-17: "The runner seems not to have a concept of mobs that are
  fighting other players (and their pets). It 'waits for them to come on' but the mob is already aggro'd on something
  else so we're wasting our time standing around. We need to just 'all kill' if the mob is already aggro'd on
  something else"). `Team.fight` only knew "on us", "on our pets" (hurt with a pet near: `engaged`) and else "wait
  for it to come on"; a monster another player fought was hurt by his blows, so it was never dropped as pinned
  either. Now `Team.elsewhere`: in war mode within `ELSEWHERE_R` 2 of a player not us or a pet not ours, and none of
  our pets in war mode within 12 of it: "All Kill" at it when in our sight within 10 (else a no-riskier step to see
  it, or the approach), every `KILL_REPEAT_S` 12 s until the pets take it. [INFERENCE: the server gives no "who it
  fights"; adjacency to another player or pet while in war mode stands in for it.] test_loop_hunt `elsewhere_run`
  (failing before). The simulator has no other players, so its numbers can't move.
- **Kills on human bodies left unlooted** (user 2026-10-17: "Many times the runner kills a mob and then doesn't loot
  it. 22:23ish we killed a forgotten soul and didn't loot"). 22:21:37 "killed forgotten soul", then "0x4275218A
  (None) is a human corpse: not looted"; again 22:22:18. Today's hunt logs: **59** such skips, every one right after a
  kill of a human-bodied monster (tormented soul 19, forgotten soul 14, reanimated knight 10, bound soul 7, newly
  reanimated 3, one each insane reanimator, abomination, vampiric consort, corpse eater, calamity, blightmare). The
  rule (`combat.human_corpse`: a corpse of a human body is a player's unless named "a|an … corpse") read the corpse
  before its name came, and "forgotten soul corpse" has no article anyway. Every mobile death in two captures
  (265 in 20261017_215839, 398 in _183126) came with Outlands' 0xDEAD naming its corpse, so now the corpse 0xDEAD
  names for the monster we killed is looted whatever its body; any other human-bodied corpse still isn't.
  test_loop_hunt `soul_corpse` (failing before). The rest of today's no-loots: 50 blue (another player's kill), 6
  "no corpse seen", 1 too far.
- **Patrolling away from monsters on screen** (user 2026-10-17: "We're constantly 'patrolling' away or 'hunting for
  monsters' when there are high value mobs on screen we could go fight instead of walking away"). Replayed every
  patrol start of hunt-20261017-220821-bd55 (capture 20261017_215839, a throwaway script: the monsters we'd fight in
  the world model within 18 tiles at that moment): **102 of 159** had one in view, and **68** of those walked away
  from all of them. The crawl only fought what `pick_target` offered (within --pull-range, not passed over); the
  waypoint choice scored staleness, value and distance and ignored what was in view, and the waypoint dwell
  ("Hunting for monsters around") ignored it too. Now `Crawl.seek`: a monster we'd fight within 18 tiles (not
  never-aggro, not an avoided type, on our floor), beyond the pull range or passed over, whose area is in an open
  zone and not near an avoided creature, the best by the store's gold a fighting minute over the route there; the
  next waypoint is its area; a patrol elsewhere stops for one (once a second at most); a dwell ends for one. The
  patrol there stops on the usual rules (a target in reach, the hold when it sees us, the pull budget).
  test_sim `test_crawl_seeks_in_view` (a wolf 15 tiles west, its area just visited: the old crawl ended 19 and 10
  tiles off, now 8). The simulator's disease tick could remove a disease twice when the hit before killed (a crash
  in seed 147 once the runs went elsewhere): guarded.
  - Simulator, 144 runs (seeds 17-160, 20 min) against 2acbb09+ (the loot fix):

    | Code | Runs with a death | Deaths/h | Gold/h | Kills/h |
    |---|---|---|---|---|
    | Before | 73 | 1.81 | 87.8k | 95.2 |
    | Seek monsters in view | 59 | 1.45 | 95.9k | 102.0 |
- **Corpse purger diseases** (user 2026-10-17: "We're frequently getting diseased by the corpse purgers. See if you
  can reduce how often that happens, it's serious DOT that is hard to recover from and makes us vulnerable"). Replayed
  every disease text in six of the day's captures (a throwaway script): 5 of 8 were "You have contracted a foul
  disease!" with a corpse purger in war mode 6-10 tiles off seeing Logan (the others: a creature's spell, a torment,
  the 16:38 ensnare). The task logs put each of the 4 in hunt runs right after "holding here: corpse purger in
  sight at 10 tiles: letting it come" (18:27:58, 23:14:08, 23:15:05, 23:27:00). 23:14:08-12 replayed: Logan at 10,
  the pets a tile behind him at 11; it woke at 9 at 23:14:11 with the pets already hitting it (-191, -189, ...),
  yet its barrage went to Logan, the nearest it saw: -23, then -26 with the disease, 100 -> 51 in a second, the
  gravebug beside it. A ranged monster doesn't "come on"; holding in its sight is what put its aggro on us.
  - Disease isn't curable with what Logan carries (Alchemy's Panacea and similar mitigations are skill-based: the
    research KB), so the lever is who it sees first.
  - Tried in the simulator (it rarely meets purgers on the zone crawl: 1 kill in 144 runs; the loop crawl does, 167),
    144 runs each, seeds 17-160, `--crawl-loop` unless noted:

    | Variant | Runs with a death | Deaths/h | Disease dmg on Logan | Purger dmg on Logan | Purgers killed |
    |---|---|---|---|---|---|
    | Before | 52 | 1.28 | 6,613 | 5,978 | 167 |
    | Never wake one (advance stops before its sight, not a target, not sought) | 74 | 2.03 | 2,754 | 3,537 | 86 |
    | Back off behind the pets (shipped) | 57 | 1.45 | 289 | 657 | 67 |
    | Back off, zone crawl (live default) | 59 vs 59 before | 1.45 vs 1.45 | 0 | 0 | 0 |

    Keeping off every ranged type (the calamity too) cost the zone crawl 10 more runs with a death (twice the calamity
    fights), so only the purger (`aggro.SHY`). Never waking purgers left them awake around later fights and deaths
    rose; backing off lets them wake on a pet.
  - Now: `Team.back_off` when an idle purger would see us before a pet (out of its sight or farther, up to 3 steps,
    the pets within vet range; the pets trail us, so they meet it first), from the hold and from the fight; no
    approach to an idle purger; no bard skill at one not on us; ranged monsters in war mode count as casters for the
    reposition (within 13) and the stand score. test_sim `test_purger_back_off` (the 23:14 geometry).
  - **The first live shift with the back-off (2026-10-18 11:39-12:07, hunt-20261018-113915-8228)**: 5 back-offs, no
    stall, no pet left behind, but still diseased by a purger at 12:05:26. 12:05:09-25 replayed from the task log:
    back off, then "toward a target" (a bound soul it couldn't see) or "patrol to (5283, 651)" (Crawl.seek patrolling
    toward the purger itself) on into its sight, "holding here: corpse purger in sight at 10 tiles: letting it
    come", back off, four times; then the patrol "stopped (under attack)" and the barrage. The sim showed the same
    loop in that geometry (test_sim `test_purger_no_ping_pong`: 7 back-offs, 37 of 97 looks in its first sight).
    Fixed: no hold for a purger; an advance stops on a step into an idle purger's sight before the pets (the
    never-aggro rule: only a step that brings it into sight or nearer; the crawl rests that waypoint); one backed
    off from is kept off for 60 s (`Team.kept_off`), its sight a crawl danger zone (no waypoint near it, not sought,
    routes bend round), dropped when it wakes. After: 1 back-off, 2 looks. An "All Kill" at an idle purger once the
    pets were nearer it never fired in 144 runs (the back-off leaves it beyond 10 tiles or out of sight), so it went.

    | Loop crawl, 144 runs | Runs with a death | Deaths/h | Gold/h | Disease dmg | Purger dmg | Purgers killed |
    |---|---|---|---|---|---|---|
    | Back off (01122fc) | 57 | 1.45 | 95.7k | 289 | 657 | 67 |
    | Back off + keep off | 54 | 1.36 | 98.9k | 0 | 0 | 0 |

    The zone crawl: 59 runs with a death, 1.45/h, 95.9k gold/h both. Purgers now go unfought in the sim unless they
    wake by themselves; live they also wake on other players and wander.
- **A paused agent gate ends the hunt run** (user 2026-10-18, after the 12:07 run kept walking out of the
  Mausoleum under `stop --after-trip` while he played Logan, and sat gated until the overseer stopped it at 12:33:
  "have the run end once I pause the agent gate"). `Link.end_on_pause` (set by loop_hunt's `main` only; the lumber
  runner and ctl still wait) raises `agent_link.GatePaused` on the first state read whose `gate.paused` is set or
  on an "ERR agent paused" refusal; a BaseException like ctl stop's KeyboardInterrupt, so no `except Abort` on the
  way swallows it. The visit row is written, exit 1 (`task_failed`). test_loop_hunt `pause`: ended 0.4 s after the
  pause, no packet after it (before: gated until the test's 60 s kill, no visit row).
- **"All Patrol" (user demo 2026-10-18 17:55:57-18:00:11, capture session_20261018_175134; user: "We can send our
  pets to a spot away from us, e.g. to within aggro distance of a purger, while we're not in aggro range").**
  - *How he sends it*: a Razor recorded macro, `Macros/All Patrol.macro` = `Assistant.Macros.SpeechAction|0|569|3|ENU|
    4|32|231|21|240|All Patrol`, played by the profile hotkey Shift+S (`<key mod="4" key="83">Play: All Patrol`). On
    the wire `ad 001d c0 0239 0003 454e5500 20e715f0 "All Patrol" 00 00`: keywords 231/351, hue 0x0239 (the hue the
    macro was recorded in, Satanic Panic's profile hue, not Razor's 0x03B1 of the pet-command hotkeys), Razor's
    double NUL. `actions.razor_say("All Patrol", hue=0x0239)` builds it byte for byte. 8 uses.
  - *Server*: at once a location cursor, `6c 01 <id> 00 ...` (target_type 1, cursor_type 0 neutral), the System line
    "Click on the location where you wish your followers to patrol." and his own overhead "All Patrol". He answered in
    0.3-0.8 s: `6c 01 <id> 00 00000000 x y 0 0000` (serial 0, z 0, graphic 0: land). A tile out of Logan's sight:
    cliloc 500237 "Target cannot be seen." and nothing happens (3 of 8). los.Sight (eye at z+14 to the ground tile)
    agrees on all 8 (5 clear and accepted, 3 blocked and refused). Accepted at 4-11 tiles; nothing farther tried.
  - *The pets*: they run there (11 tiles in ~3.5 s) and attack an idle monster within ~1-3 tiles of the spot on
    arrival (5 of 5: a corpse purger 3 off, bound souls 3 and 1, a nightstalker 3, a tormented soul 3). After the
    kill they stay and mill about the spot in war mode (17:57:37-45 around (5268-5275, 651-655)) until "All Follow
    Me" / "All Guard Me". GraveRobber closes to melee; AlaFlambe stops 4-7 tiles short.
  - *The purger shoots the pets*: 17:55:57.5 Logan at (5248, 653), an idle purger at (5262, 652) 14 tiles off, the
    spot (5259, 652). It woke at 17:56:01 on GraveRobber and fought him; a second purger came on from (5275, 655) to
    5 tiles from Logan (17:56:20-30) and stayed on the pets too. Logan 100/100 from 17:55:50 to 18:00:25 (both purger
    fights, Logan walking in to 8 tiles of the first), no disease.
  - *"All Guard Me" mid-fight doesn't call them off*: guard at 17:56:36 and 17:58:03 while they fought; they fought
    on in war mode until the target died (17:57:06, 17:58:19).
  - *Costs seen*: the first patrol pulled the purger, a bound soul and the second purger onto GraveRobber alone
    (AlaFlambe hanging back): dead at 17:56:36 (98 % -> 0 in 35 s), resurrected with the vet supplies at 17:56:52.
    The user: "Ignore GR dying I just didn't have my vet supply loop running, just a mistake".
    The pets on patrol don't guard Logan: 18:00:10.9 patrol, the pets on a tormented soul at the spot, a second one
    came onto Logan (18:00:26.8 "The creature torments you with a foul disease!", 100 -> 39 by 18:00:42) while
    GraveRobber, next to both, stayed on the first.
  - **Built as `--patrol-pull`, off by default (2026-10-18; user after the demo: "Ignore GR dying I just didn't
    have my vet supply loop running").** `Team.patrol_pull` / `patrol_spot` / `patrol_watch` (docs/HUNT_LOOP.md);
    the simulator models the command (docs/SIM.md). Loop crawl, 144 runs each, seeds 17-160:

    | Variant | Runs with a death | Pet-death runs | Deaths/h | Gold/h | Disease dmg | Purger dmg | Purgers killed | Patrols |
    |---|---|---|---|---|---|---|---|---|
    | Keep off (HEAD, = `--no-patrol-pull`) | 54 | 46 | 1.36 | 98.9k | 0 | 0 | 0 | 0 |
    | Patrol, first cut | 76 | 103 | 2.04 | 91.3k | 1,955 | 4,609 | 161 | 92 |
    | + walk to a hurt pet stops at any advance rule (pull_check) | 73 | 101 | 1.92 | 94.8k | 221 | 970 | 102 | 83 |
    | + bard skills at a purger the pets fight | 65 | 100 | 1.67 | 94.8k | 493 | 1,232 | 101 | 89 |
    | pull_check stop narrowed to a purger's first sight (the zone crawl lost pets with it: 64 vs 48) | 68 | 98 | 1.77 | 92.9k | 1,751 | 3,394 | 166 | 86 |
    | + no provocation of a purger the pets haven't hurt (shipped, flag on) | 71 | 97 | 1.86 | 92.6k | 1,768 | 3,502 | 170 | 87 |

    The zone crawl with the shipped code: 57 runs with a death (59 before), pet deaths 50 (48), 1 patrol. The
    patrolled purger never hit Logan (0 of 1,213 in the first diagnosis); the damage came from the second purger of
    the pair (the sim's two purger spawns stand 8 apart, (5266, 655) and (5274, 652), as live 17:56:17), out of view
    when the patrol goes and taking Logan by first sight as he moved about the fights that followed (bound souls,
    repositions, vet, the walk out). Sim purger fights also run slower than live (docs/SIM.md "Limits"), so the
    sim may undersell it; it stays opt-in until a live trial. Unchanged by the flag: bard skills now also go at a
    purger the pets fight (the demo's Discordance), no provocation of one they haven't, and a walk to a hurt pet
    stops on a step into an idle purger's first sight.
  - **First live use (2026-10-18 22:13:40, hunt-20261018-220541-66e2, SeerLoganWolf8):** "'All Patrol' to (5275,
    651), 2 from the corpse purger 0x0019D2BF at 13 tiles", "the pets are on it; 'All Guard Me'"; Discordance on
    it; it died 22:14:24 (+920), the second of the pair (0x0019D507) came into the fight and died 22:14:43 (+977).
    No potion on Logan from 22:13:40 to 22:15:20 (the next two against a bound soul), no "You have contracted a foul
    disease!" all shift, the pets above 90 %. One sample; 8 runs that shift, the other 7 met no purger.
- **Dead in a blightmare's sight (2026-10-19 19:54:39-55:01, hunt-20261019-192245-e3d7, capture session_20261019_192054;
  user: "Logan really should have gotten out of LoS of that blightmare that was killing us").** Tormented souls on
  the pets at (5239-5241, 675); Logan stood at (5243, 672) from 19:54:39 to 19:54:57; a blightmare in war mode at
  (5243, 684), 12 tiles south with sight of him, cast at him: -26 (40.7), -21 (44.1), -21, -28, -28, -24, -21, -21,
  -24, four "The creature's spell blights you with a foul disease!" (its Spellblight, -21 ticks), 100 -> 4 despite
  two potions and three vet channels; the hurt leave at 4/100 came after a loot, dead before the gate. The
  reposition saw nothing to do: `CASTER_R` 10 (it was at 12), a vet channel running (no reposition mid-channel
  unless a caster in reach), and one caster's sight is 1.0 of stand_score, never `REPOSITION_GAIN` 1.0 over a
  step's 0.3. Fixed: `CASTER_R` 12; with a caster/ranged monster in reach seeing us, a tile fewer of them see is
  taken by itself (no more idle eyes, the pets in vet range, up to 4 steps); mid-channel only when the channel
  heals us (`Team.channel_for_us`). test_sim `test_caster_at_12` (the 19:54 geometry; before: no move). Sim, 144
  runs each, against fdee022:

  | | Runs with a death | Deaths/h | Pet-death runs | Gold/h |
  |---|---|---|---|---|
  | Zone, before | 60 | 1.51 | 54 | 95.9k |
  | Zone, moving mid-channel whenever a caster saw us (+ CASTER_REACH 12) | 54 | 1.30 | 76 | 97.1k |
  | Zone, mid-channel only for our own channel, + CASTER_REACH 12 | 54 | 1.29 | 66 | 97.7k |
  | Zone, shipped (CASTER_REACH stays 10) | 57 | 1.41 | 57 | 98.5k |
  | Loop, before | 63 | 1.63 | 43 | 98.1k |
  | Loop, shipped | 58 | 1.51 | 42 | 99.5k |

  `loop_hunt.CASTER_REACH` 12 (a caster in war mode that far counted as on us while we take damage, so a target)
  cut 3 more deaths but sent the pets at casters farther off: 9 more pet-death runs; it stays 10. `test_hunt` lost
  its incidental "left within 8 min of seed 1" check (the draws with the fix kept him alive and hunting).
- **Stuck on the walk in under an insane reanimator on a ledge (2026-10-19 19:34:19-35:50, hunt-20261019-192245-e3d7,
  capture session_20261019_192054; user: "This is the second time I've seen the runner get stuck here. The 'insane
  reanimator' is on a higher z level and can't come to us").** Attacked by a calamity on the way to the last
  teleporter, the walk-in fight began; after the kill an insane reanimator stood at (5507, 660, z 30) in war mode,
  over GraveRobber at (5507, 661, z 5), taking 3-4 % off him every 3.5-4 s. `Team.pet_attackers` (war mode within 1
  tile of a hurt pet, z not looked at) made it "attacking", which waived both the 60 s skip and the floor rule in
  `pick_target`: "target ... dropping (pinned) ... target" four times, until `WALK_IN_FIGHT_S` 90 s ran out at
  19:35:50. Fixed: a monster off our floor (`on_our_floor`) is a target only when it attacks us; and it doesn't make
  the pets "busy" for the patrol (`pets_busy`). test_sim `test_ledge_attacker` (before: picked).
- **A clean escape from the red Malito (2026-10-19 10:29:06-30, hunt-20261019-102426-3a9c, capture
  session_20261019_095934; user: "That was an AWESOME escape").** Seen at 17 tiles at 10:29:06.5, "LEAVING ... red
  player in view at 18 tiles", "All Follow Me" 0.5 s later; he closed to 2 tiles in 4 s (Telekinesis, a stuck
  explosion potion with a "5" count, Flamestrike -27, Mind Blast -27, the potion's blast -24, Explosion, Energy
  Bolt, Magic Arrow, his pets on "all kill"). Logan ran 72 steps to the crimson gate (arrived 10:29:25.9, ~0.27 s
  a step with replans), healed on the way with two potions (+21 at 11.2, +25 at 21.5, 10.3 s apart) and three vet
  channels (begun 10.5 / 17.5 / 24.6, +22 and +15 at their finishes; no refused try), low point 32/100 at 17.0;
  the gate's menu came at once, Totem 10:29:26, the tome home at 10:29:30, the pets with him all the way (1-2
  tiles). Weak spot: both rope jumps failed ((5227, 653) "That location is blocked." 501942; (5208, 698) "Target
  cannot be seen." 500237), each followed by a denied step and "You are no longer hindered." ~1 s later.
  - Fixed (user: "I would expect we'd never try to rope to a tile we can't see"): `rope_jump` picks a landing in our
    sight as a ground target, eye (z from the map's surface where we stand: the world model's z lags) to the tile's
    ground, the check the "All Patrol" clicks matched 8 of 8; the old eye-to-eye check with the model's z passed
    (5208, 698), the ground check finds it blocked at (5207, 693, 15). A refused landing (501942, 500237, 500446)
    isn't tried again that run. (5227, 653) sits on a stack of statics topped at z 22 with nothing on it: why the
    server called it blocked is open [INFERENCE: its fit check]. The simulator now answers a rope with the live
    refusals and the ground-sight rule. test_sim `test_rope_sight` (the 10:29:20 tile; before: 500237).
  - First live flight after the fix (2026-10-19 11:23:15-32, hunt-20261019-112052-4400): the red "Lady This Game
    SuX" in view at 17 tiles 2 s after the arrival on Maus 3; "All Follow Me", rope to (5208, 730) 9 tiles landed
    (11:23:17), a vet channel on the run, rope to (5204, 704) 4 tiles landed beside the gate (11:23:23), Totem
    11:23:24, home 11:23:27, in the room 11:23:32 with both pets; 3 hits lost in all.
- **Stuck beside the leave gate (2026-10-19 10:19:31-35, hunt-20261019-100618-80b2, capture
  session_20261019_095934; user: "Logan just standing right next to the red gate but not going through. He was
  dying so I moved him manually").** A planned leave (the return rule, 21,393 gp) reached (5204, 702) beside the
  crimson gate at 10:19:31.94, hit 100 -> 79 there. "All Follow Me" 32.30; the state read before the click ran the
  guards, whose on-the-run vet supplies went at 32.55 (us at 79); the gate's double-click at 32.75 got cliloc 500119
  "You must wait to perform another action." (0.07 s later) and no menu; `moongate.travel` waited its MENU_WAIT_S
  and the run aborted ("no 'Moongate Destinations' menu at (5204, 702)"), leaving Logan there with the pets. Fixed:
  the click goes again after 500119 (`GATE_CLICK_TRIES` 3, 0.6 s apart). The simulator's gate now obeys the action
  delay as live. test_sim `test_gate_after_vet` (before: that abort).
- **Two walk-in bugs from the 2026-10-18 evening shift (SeerLoganWolf8), fixed.**
  - hunt-20261018-210443-1cbe 21:05:41-43: attacked 3 tiles from the last teleporter (5522, 637), "keeping off
    the tank: (5522, 636)" chose a tile that was a teleporter too (unknown till then; teleporters come in
    fields), the step put Logan on Maus 3 at (5199, 736), and the walk on to (5522, 637) aborted "no route from
    (5199, 736)". Now `walk_in_through` counts a teleporter as passed within 3 of its known arrival, and
    `Team.tiles_within` leaves out known teleporter tiles and their neighbours. test_sim
    `test_walk_in_through_neighbour` (before: "no route onto it").
  - hunt-20261018-212957-c791 21:29:58: restarted on Maus 3 at (5209, 689) with `--spot 5199 737`, 48 tiles off;
    `at_hunt` only looked within 18 tiles (the crawl floor isn't built before the start), so it recalled: "unusable
    no recall ... inside the Mausoleum". Now `resume_inside` when recall is forbidden where we stand. test_sim
    `test_restart_inside` (before: that abort).
- **The red flight that stood still (2026-10-18 21:14, hunt-20261018-211045-0f63, capture session_20261018_203339;
  user: "Why was that attempt to run from a red so awful? We kept standing still").** Chaos Agent (red) came on at
  1 tile at 21:14:21.4 mid-fight (a vampire countess, a bat, a corpsebride); the first running step 0.5 s later.
  From 21:14:21.9 to the user's takeover at 21:14:35.3 the runner covered 14 tiles in 13.4 s, about 4 s of running:
  it stood four times, ~7.7 s in all, -35 at 22.7 and -13/-13/-21 meanwhile.
  - 23.0-23.9: a step denied, then a vet channel begun on us (fine: a step doesn't stop it).
  - 25.6-28.1: a heal potion refused by its cooldown (10.3 s after the last), then "[Rope" got 500119 (an action
    just before) and the rope waited its whole 1.5 s for a cursor that never came; then the potion.
  - 30.4-32.4 and 33.8-35.3: the vet supplies tried while a channel ran that **his own Razor vet loop** had begun
    (client 0x06 at 21:14:29.1; also 21:14:14.0): "You must wait a few moments before you may use this item again."
    isn't a reply `Team.vet` knew, so each try stood out the 1.5 s reply wait; the second came after a step denied
    by a shadowguard in the way (a shove needs full stamina: a Total Refresh at 35.7).
  - The user then played without pausing the gate, and the runner's potions/walk interleaved with his steps
    (21:14:35.7-37.3) until it died at 21:14:44 (he was ensnared at 41.4).
  - Fixed: that line is a "busy" reply (ends the wait at once, no try until the finish line); a channel begun by
    anyone (TEXT_VET_BEGIN, his loop's too) marks the vet busy (`Team.update`, now read at the top of `Team.vet`);
    the rope's cursor wait ends on 500119. The sim answers a try mid-channel with the live line. test_sim
    `test_vet_channel_not_ours` (before: a try went out).

## The fifth live run: two apparitions on Logan, dead at the gate (test shard, 2026-10-15 12:53)

Task hunt-20261015-125300-8e1f (main's ctl and store, `--vet-range 5`). 12:53:25 the runner held where an
apparition saw it at 9 tiles; at 12:53:29.5 one apparition, at 12:53:33.3 a second from 9 tiles off, each came
onto Logan's own tile and hit him -48 and -49 (potion between: 52 -> 89). The 50 % leave at 41, 23 steps to
the crimson gate (hits 42 there), "All Follow Me" and the gate's click at 12:53:42.4, menu at once; -26 and
-18 at 12:53:43.3-43.5 and dead, 0.4 s before his next potion was due.

- **The user's aggro rules** (2026-10-15): "You just have to have the pets on guard if they're not
  currently fighting something. If they're already fighting something, you position yourself so the pets
  are between you and the mob, or you make sure you're out of LoS of the mobs. That way they only aggro on
  the tank pet." "Aggro range I'm sure is longer than 9 tiles, I think its 12 or 13." "Any mob that 'sees'
  Logan before it sees a pet is going to aggro on him directly and that's pretty much a death sentence
  with this build since he can't tank or fight himself." The runner had been "positioning Logan poorly so
  he was closer to mobs than his pets were".
- Now: `aggro.AGGRO_R` 13; tiles scored by `View.first_on_us` (idle monsters in range and sight with no pet
  nearer them); the pull spot puts the pets between; the simulator's monsters take whichever of us they
  see first (the old "a guarding pet within 6 draws it" was too kind) with aggro range 13; a potion due
  within 1.5 s is drunk before the gate's click.

## The sixth live run: the first complete one (test shard, 2026-10-15 17:23-17:39, capture 20261015_171928)

Task hunt-20261015-172342-b8a0 on fe0247c (`--vet-range 5`, the aggro rules above), from (5200, 737). 15 min
on the level, then "time is up": the gate to Totem and the tome home with both pets. 16 kills (apparitions,
banshees, shadow minions, a vampire bat, a shade wolf), 17,052 gold, 165 hits lost, no death; the lowest 54.

- Hits on Logan (6): an apparition on his tile as it woke, -46 (17:24:09.9, the runner had walked 2-5 steps
  "toward a target" at it); an apparition back from a blink 2 tiles off, -39 (17:33:18); banshee spells -22,
  -23, -18, -17 at 4-6 tiles.
- The crawl stayed in zone 1 for 11.4 min (`--crawl-learn-s` 600 on the main store's empty history; the
  user: "wandering aimlessly"; `--crawl-dry-s` since 75fe7d8), then opened zone 2 (predicted 19.7 hits/min
  <= 20) and fought the shadow minions and a wolf north of the gate.
- A patrol route crossed the crimson gate's tile (17:35:19, "closing gump 0xE0E675B8 ... passing through").
- Open: "toward a target" still walks Logan at a monster the pets haven't engaged; the hold at first sight
  and the patrol alternate every second or two near banshees 13-14 tiles off (they rarely come).

## The seventh live run: zone 2, the countess room, a leave the user didn't want (test shard, 2026-10-15 17:48-17:54)

Task hunt-20261015-174850-2166 (`--crawl-learn-s 180 --crawl-max-dmg 35`). The crawl began with no zone history
(the start was 21 tiles from the last; fixed: earlier visits within 40 tiles count, 99996a1), opened zone 2 at
17:52:54 and went west into the vampire countess' room (5179-5189, 681-685). 7 kills.

- 17:53:26 "off the tank": a shove into (5182, 682) was denied, the replan found a 124-step route, and the walk
  wandered 8 moves while the countess, a corpsebride and a copy hit Logan, 100 -> 20. Now every care walk
  (spacing, pull spot, fire, reposition, stand-off, retreat) refuses a route longer than its few steps.
- 17:53:31 the 50 % leave at 20/100 with a heal potion ready; through the gate at 92, the pets left behind
  (retrieved at the stablemaster). The user: "Logan didn't need to run and leave there, he could have just
  bandaged himself and positioned a little better." Now with `--heal-in-place` no hits leave while a heal is
  coming, down to 15 %.
- The crawl set the countess aside after the leave ("fled from it in 37% of fights (est.)"). The user: "we
  don't need to avoid any mob specifically ... it's all doable": `--crawl-avoid-types` off and no
  `--never-aggro` default for the pets style.
- Logan carries 40 bandages (0x0E21); the runner has no bandage use (the live self-bandage is 0xBF sub 0x2C on
  his own serial, ~16 s for +16, 20261014_221341); the vet supplies heal him too.

## The ninth live run: a nightstalker from behind, dead in 2 s (test shard, 2026-10-15 18:31-18:40, capture 20261015_171928)

Task hunt-20261015-183114-fbc4 on fb1250e: zones 2 and 3 open from the start, the valuables-only loot (it left
scrolls, reagents, armour; weight 242 -> 327 on gold), 15 kills (shade wolves, banshees, apparitions, a shadowguard,
shadow minions) in 9 min, then the countess room (5175-5178, 683-688).

- 18:39:53-56 a corpsebride and the vampire countess on the pets; "keeping off the tank" put Logan at (5176, 688),
  3 south of the pets; potions and the vet kept him at 71-100.
- 18:40:10.0 **a nightstalker not seen before** (none in view at 18:40:09.6) hit Logan -58 from (5176, 690), 2
  tiles south of him, the side away from the pets; 18:40:11.7 its Charge (action 24) -39 with the gravebug -28;
  dead at 18:40:12.7 (93 -> 36 -> 0 in 2.7 s). Both pets died after him. [INFERENCE: the nightstalker's stealth
  (the wiki's Vanish and Backstab); it came up the corridor from the stalker room.]
- Healing on the flight from a red: the runner already drank heal potions on any walk out to the gate
  (`drink_on_the_run`; I told the user it didn't); now it also starts the vet supplies on Logan there (a step
  doesn't stop a channel healing him: 20261014_221341 22:35:50-55).

## The tenth live run: an apparition on Logan's tile, a leave he no longer needed (test shard, 2026-10-15 18:56-18:58, capture 20261015_171928)

Task hunt-20261015-185652-4514 on 3b062e5 (backspawn) from the arrival (5207, 737).

- 18:57:06 held at (5207, 726), "apparition sees us at 10 tiles: letting it come", the pets beside him (the
  phoenix a tile nearer it). 18:57:07.8 it went to war mode, "*phases*", gone; 18:57:09.0 back **on Logan's
  tile**, -59 (the phoenix -37). It came for Logan although a pet was nearer it [INFERENCE: waking apparitions
  go for the player: also the fifth and sixth runs].
- He stood on its tile for 10 s: the potion first (`heal`: the satchel opened 11.0, drunk 13.3), then a vet
  channel (15.2) during which the stand-off never ran ("never mid-channel"); 18:57:19.5 its next hit -60 (8/100).
  Now the stand-off comes first in `HuntLoop.tick`, mid-channel too, running.
- 18:57:20 the leave (8/100, below the heal-in-place floor) to the gate 23 steps away: potions and the vet on the
  run, 98 by 18:57:27.6 near the gate. There he said "All Follow Me" and waited 10 s for the pets 18 tiles back
  in the apparition fight while a banshee 4 tiles off cast at him (-34, -32, -34). The user: "There's no point in
  running to the gate when he's fully healed". Now a hurt leave is called off at `--heal-at` with no leave rule
  firing (`HuntLoop.leave_called_off`) and he walks back to the pets (`Team.rejoin`).
- 18:57:39.5 the user's own client walked Logan back toward the pets (0x02 from `client`); the runner's gate
  walk read a position 2 tiles off its next route step and crashed (`nav.direction`: "not a single step").
  The Mover now replans from wherever it finds itself. Logan died at 18:57:44.6 at (5207, 724), the apparition's
  area hit and the banshees.

## The eleventh live run: the elder vampire countess, and no instrument since the ninth death (test shard, 2026-10-15 19:12-19:19, capture 20261015_171928)

Task hunt-20261015-191213-47a3 on ab3a5b2 from (5201, 741): 10 kills (apparitions, shadow minions, corpsebrides,
a shade wolf), 7,910 gold in 7 min, dead at 19:19:41.4.

- **The stand-off worked**: 19:12:35 an apparition on Logan (-56), "out of charge reach" 2 steps at once, then
  the potion; it died to the pets without another hit on him.
- **No bard skill or song has worked since the ninth run's death**: Logan's pack had the indestructible
  supremely melodious lute (0x415FFCA8, 0xEB3), a drum (0xE9C) and a tambourine (0xE9E) at 18:30; after the
  18:40 death a lap harp (0xEB2) was in it. Every skill then got cliloc 500617 "What instrument shall you
  play?" with a cursor, which the runner answered with the skill's target: 500619 "That is not a musical
  instrument." (3 times in the tenth run, 51 in this one; logged as "no answer" / "out of sight or reach").
  The runner now picks an instrument from the pack for that prompt (`Team._instrument`), and the simulator
  asks it when the instrument last played is gone.
- **The elder vampire countess** (5216, 638), a regular mob: 19:19:00 "toward a target" walked Logan 2 steps
  to 6 tiles of it; she took the gravebug at 19:19:03.7 (-64) and transfixed it (19:19:04.8 a hostile
  "GraveRobber" copy), then the phoenix (19:19:22.9 an "AlaFlambe" copy). With both pets frozen she and the copies
  came onto Logan: -41 (19:19:05.9), -44 (14.1, the gravebug copy beside him), -31 -27 (31.0-31.1, the phoenix
  copy and the countess beside him), -27 (32.8), -35 (36.5).
- 19:19:30.9 a Discordance went out at 100 hits; -58 meanwhile; its cursor answered at 42 (19:19:31.6, the
  instrument prompt again); the potion only at 19:19:35.2 (+34). Now a bard cursor is cancelled when a potion is
  due (`Team.potion_first`).
- 19:19:35 the retreat from the copy (7 tiles), 19:19:37 the leave at 15/100 (50 steps to the gate), the vet on
  the run; the countess' spell from 15 tiles (-17) killed him at 19:19:41.4.
- The user then: "I suppose the runner can avoid the elder vampires for now, but they're not contested bosses,
  they're regular mobs". `aggro.NEVER_AGGRO` is now the elder vampire and the elder vampire countess (their sight
  no-go, never pulled, run from when one comes). It had still been the vampiric consort: 1c1ab51 dropped the
  `--never-aggro` default but the code fell back to `NEVER_AGGRO`, so the consort was still being avoided
  against the user's 2026-10-15 "we don't need to avoid any mob specifically".

## The twelfth live run: a second apparition, then 20 s at the gate in a banshee's fire (test shard, 2026-10-15 19:49-19:50, capture 20261015_171928)

Task hunt-20261015-194924-eeab on 0f55eb8. The songs played at last (the instrument fix: "Song of Discordance:
playing for 15 min" 19:49:30).

- Apparition A idle at (5218, 722), B at (5195, 724), the hold at (5208, 726). 19:49:39.5 A on Logan's tile, -46;
  the stand-off went 2 tiles west to (5206, 726), 10 from B in its sight; the potion only at 19:49:44 (the
  satchel opened first, 41 -> 44); 19:49:45.3 B on Logan's tile, -46.
- 19:49:47 the two-attacker leave at 37/100, 1 s before the vet channel; 98 by the gate, "leave called off" at
  19:49:55 (the new rule worked), but the pets were out of view in the apparition fight and `rejoin` only knew
  pets in view: Logan stood at (5202, 704) while a banshee 5 tiles off cast at him (-22 -25 -28 -22 -27 -28 -21
  -24, 3.3 s apart; no swing, so not an "attacker"); the 8/100 leave at 19:50:16; through the gate with both
  pets (they came on "All Follow Me").
- At Totem the tome's recall: "That location is blocked." (cliloc 501942) on the DTF Loot Chest rune; the runner
  then tried to walk to the Mausoleum recall spot from Totem ("no route") and aborted. Logan alive at Totem.
- The user: "he stood where a 2nd apparition aggrod him and then went and stood by the gate getting hit just
  standing there"; then "Please prove that the runner can handle that same fight correctly before we try
  again in game", and "I don't click potions in a satchel I use a Razor hotkey for 'Drink Heal Potion'".
- Fixes (HUNT_LOOP "Self care", `--pull-budget`): potions by serial, no bag opened; the stand-off to the clear
  tile the fewest idle monsters see; a ready potion before the stand-off; a caster in war mode in reach is an
  attacker; `rejoin` to the pets' last-seen spot, also when something is on us with no pet near; the
  reposition out of a caster's sight mid-channel; a held monster still held at the pull spot; the pull spot
  in the fight path too; "at once": two idle monsters worth more than the budget seeing the next tile stop the
  patrol and rest the waypoints near; heal_coming counts a vet channel that can start; no attack while the
  Stationary Penalty is on (a race the faster potion exposed in test_loop_hunt). The simulator now has the
  apparition's blink, the banshee's observed spells and a wake delay; the fight replay is in docs/SIM.md
  "The 19:49 live fight".

## The thirteenth live run: 41 kills, 35,926 gold in 20 min (test shard, 2026-10-15 20:46-21:06, capture 20261015_204514)

Task hunt-20261015-204605-d465 on 35e7f83, the live arguments: no death, no leave until "time is up", 41 kills,
35,926 gold (the first 16 min: 28,023 gold, ~104,000 an hour on the level), 771 hits lost, the songs and
discords working. The user: "Good run overall".

- 20:55:18 the valuables-only loot left research materials; the user: "quite valuable", then "it's probably
  better to just _not_ take the regs scrolls and armor/weapons" (combat.worth_looting since 98dd9b6).
- 21:02:02 a nightstalker unseen till then beside Logan; the stand-off went 3 tiles to (5185, 703), 3 from an
  idle reanimated knight (the log: "seen by 1.2 idle before a pet"; every clear tile in the pets' reach was
  in its sight or a stalker spawn's); it woke at 21:02:07 and hit him -46. Now `Team.standoff_tile` weighs
  nearness to the idle monster and stays rather than step nearer one.
- 20:49:52 and 21:04:29 "toward a target" walked Logan at monsters the pets hadn't engaged (27 and 2 steps):
  still open.
- Weight: 327/450 at 28,023 gold (gold ~0.01 stone a coin); the 10-stone margin stops the loot at ~37,000 gold
  carried.
- Home: the moongate to Totem, then the tome's recall twice: "disturbed" and "That location is blocked."
  (501942) on the DTF Loot Chest rune, as in the twelfth run; the runner then tried to walk from Totem to the
  Mausoleum recall spot ("no route") and stopped. Now a blocked rune, or a recall spot more than 60 tiles off,
  is no walk: the run stops in town with the recall's failure. [INFERENCE: on the test shard something stands
  on that rune's landing tile.]

## The first run on the live server (2026-10-15 21:10-, capture 20261015_210709, upstream 52.223.17.219)

Task hunt-20261015-211040-f9c6 on b4340ab, the test-shard arguments unchanged: the live character's serials are
the test shard's (the tome 0x444AB1F3, the pets 0x000DDEFE and 0x0026EF56). 28 kills by 21:23:56, no leave.

- 21:11:13-19 the "at once" rule stopped the patrol every 2 s by the gate's banshee room: stop, "patrolling
  elsewhere", the next waypoint's route through the same spot, stop again. Now it stops once per spot per
  2 minutes (f244c86).
- 21:19:38 and 21:19:51 a vampire countess transfixed AlaFlambe; from 21:19:58 to ~21:20:43 the phoenix stood
  with war mode off while GraveRobber fought her alone (the user: "AF was not fighting mobs, GR was fighting
  alone"). GraveRobber, transfixed five times, was back in war mode each time (her target). Now an idle pet
  beside the fight gets "All Kill" at the target (`Team.idle_pet`).
- 21:28:21 "LEAVING by the moongate: hostile player 0xd1df6 (grey) at 12 tiles": a tormented soul (body 401, a
  human body, notoriety 3, no 0x20 flag) first seen unlabeled; its name came the same second. `threats.Watch`
  already held such a sighting as "awaiting label" for 1 s, but `HuntLoop.player_threat` read only `player` and
  `hostile`; now it skips the awaiting-label ones (reds in view still leave at once). Gate, Totem, the tome
  home at 21:28:44: 41 kills, 28,981 gold in 18 min, no death. The user: "pleasantly surprised by that run".

## Live runs 2-3 and the first trips from the inn room (live server, 2026-10-15 21:33-22:58, capture 20261015_210709)

- **Run 2** (hunt-20261015-213328-0b4a): 5 kills, ended 21:37 by the grey player Hallik at 3 tiles. Since ef30477
  only reds end a hunt.
- **Run 3** (hunt-20261015-214603-14bd): 23 kills, then a red, "Back Problems", at 18 tiles (21:57:07): "All Follow
  Me", rope jumps at 21:57:10 (8 tiles) and 21:57:18 (6), at the gate 21:57:21, a second follow 21:57:22, "Back
  Problems is attacking you!" 23.2, the gate's double-click 23.4, Travel 25.4, Totem, the tome home 21:57:27. The
  user: "Holy shit that escape from reds actually worked!". Since f630e69 the double-click comes at a macro's pace
  (0.7 s from the gate's tile in the simulator) and a flight skips the second follow.
- **From the inn room** (Logan Wolf's rental room, facet 3, (403, 923)): a recall out of it works (user; four trips).
  1. 22:20:47: "All Guard Me" at the Maus Near 2 landing set the pets fighting there, and Logan walked to the first
     teleporter alone (stopped by hand). The walk-in now goes on follow (HUNT_LOOP "In and out of a dungeon").
  2. 22:32:50: attacked on the way, the runner only ran the pets' care; Logan stood among the mobs, dying. The user
     took over. The walk-in fight now runs the fight tick's order. Then the step onto (5554, 701) "moved without a
     teleport".
  3. 22:42:44 and 22:45:58: two more teleporter tiles were wrong, (5506, 701) and (5507, 697).
  4. 22:48:36: all four teleporters with both pets (22:49:46-22:50:32), 15 kills on the fight level.
  The corrected tiles: 5554,700 / 5507,701 / 5507,696 / 5522,637, each the denied step's target read from the
  0x02 direction byte in 20261013_204518.
- **Dead at 22:57:57 (run 4).** 22:57:45 a vampire countess transfixed GraveRobber, at 22:57:52 AlaFlambe; their
  copies and she took Logan from 100 (a potion at 22:57:47) to 0 in 13 s (-28, -17, -7, -13, -7, -7, -21, -18, -7,
  -6). The retreat at 22:57:54 (50/100) was cut by mobiles: "shove into (5184, 682) denied (stamina 15/25)", then
  "cut by mobiles we couldn't shove; waiting", dead a second later. The user: "We carry stamina potions so if we get
  stamina limited and need to shove we can drink a pot". Since then `Mover.refresh` (HuntLoop.refresh_potion: the
  Total Refresh potion, one double-click) drinks before a denied shove and before waiting on a route cut by
  mobiles, and the denied tiles are tried again (test_mover `test_shove_denied`).
- **The user's drop-off** (Razor "New Drop Off", Scripts/New Drop Off.razor, hotkey F5 in "Logan Wolf Tribard
  Tamer"): eight `menu <serial> <index>` lines and one `target` of the backpack. Razor sends each context menu
  request and its pick back to back (the same 0.1 s), without waiting for the menu. The items (click labels):
  treasure map tome 0x62659A52, mastery chain link tome 0x431E6BAA, 0x62658F0D (skill scrolls [INFERENCE: "No
  skill …", "1 skill …"; the capture keeps only 64 bytes of a packet]), aspect item tome 0x62657BB3, resource
  stockpile 0x62645C82 ("Restock" 3006349 + the pack: gems and empty bottles went in), bank deposit safe 0x431E86F5
  ("You depo…": 28,981 gold at 21:29:49), 0x6264BB54 ("Add All Unidentified Items From Backpack" 1095568, the
  2nd entry: "7 recycl…"), storage shelf 0x6CEB65CD (Resupply 3006348). It left research materials (graphic 0x5F72,
  "research materials : 2"). `ctl act dropoff` does the same, picking entries by cliloc, then research materials
  and resource maps into the room chest (homes.json "Logan Wolf" `dropoff`; test_dropoff.py). Live 2026-10-16 with an
  empty pack (twice, 25 s each with the walk to the arrival tile): every step answered, "No undecoded treasure
  maps found in backpack.", "No mastery chain links …", "No skill mastery scrolls …", "No aspect items …", "You do
  not find any items that could be added to a Resource Stockpile.", the safe nothing (no gold), "0 recyclable
  items found and added.", the shelf "Unable to resupply: no items available." (the pack held the loadout: 30 heal,
  15 cure, 15 refresh potions, 180 vet supplies). Labels in the room: "a skill mastery tome" 0x62658F0D, "magic
  item recycler" 0x6264BB54 (the user confirmed), "resource container" for the stockpile, the chest "wooden chest".
  - **Paced like the script since 2026-10-17** (user: "The dropoff can be sped up greatly. My Razor dropoff script
    finishes like instantly"). Razor sent all eight request/pick pairs and the backpack target in 0.12 s
    (21:29:49.407-.526: pairs 3-4 ms apart, the target 94 ms after the stockpile's pick). `dropoff.run` waited a
    person's "menu" pause (1.3 s median) before each request and each pick and up to 1.5 s for each line: 21.7 s
    against the offline fake room with the normal profile. Now one hotkey press (0.25 s), then each pick right on its
    menu's arrival (the entry still chosen by cliloc, one round trip a step), the stockpile's cursor answered at once,
    and each step's line taken from what arrives before the next menu (the server answers in order): 0.32 s against
    the fake, about a round trip a step live [INFERENCE: not yet run live]. test_dropoff checks the line attribution.
  - **Skill mastery orbs go to the chest since 2026-10-20** (user: "The overseer failed to drop off a skill orb that is
    loot"). "a skill mastery orb" 0x51F6DD09, graphic 0x5740, looted 22:48:49 in hunt-20261020-223926-1d18; no
    drop-off step took it (the skill mastery tome's step is for scrolls [INFERENCE: not tried with an orb]), so it
    would have stayed in the pack. homes.json `to_chest` now has graphic 0x5740 and the name "skill mastery orb";
    `dropoff.chest_items` on the live state picked only the orb.
  - **Seeds go to the chest since 2026-10-20** (user: "add seeds (e.g. the 'very common seed' in our pack) to the list of
    things to drop off"). "very common seed" 0x51F5EDA4, graphic 0x5736, at the pack's top level. `to_chest` now has
    graphic 0x5736 and the name "seed" (the other rarities by name; their graphic not seen yet); `dropoff.chest_items`
    on the live state picked only the seed.
    Graphic 0x5736's tiledata name is "seed of renewal" (`combat.item_name({'graphic': 0x5736})`), so a seed without its
    tooltip name shows that way in the drop-off events' `unpriced` lists (user 2026-10-21: the client calls them "common
    seed" and the like). The 00:06 drop-off (job event 24127) moved 0x51F5EDA4 and a nameless 0x522C4324 by graphic.

## Inn-room runs of 2026-10-16/17 (live server, captures 20261016_*, 20261017_110506)

- **2026-10-16 09:25 (hunt-20261016-092542-b56c)** from the DTF landing: all four teleporters with both pets, three
  fights on the way in (a calamity, a devourer beetle, an abomination), the fight level at 09:28:46. Ended 09:32:52 by
  the user's pause ("walk gated too long"): a player had reported reds. The visit row counts 0 kills: the walk-in's
  kills and gold come before the visit starts.
- **2026-10-17 11:06 (hunt-20261017-110657-c26b)** from the room after `act dropoff`: the fight level by ~11:09.
  - 11:11:05 it left an **arcane scroll** in a banshee's corpse: a blank scroll 0x0EF3 in hue 2609, which the "scroll
    in the name" rule threw out. The user: "valuable loot", "They're pink blank scroll graphics". Since 006cdac a blank
    scroll graphic in any hue but 0 is taken.
  - 11:12:45 **stuck on a walkable tile for a minute**: (5192, 671, 25), on the fight level's north side, where the
    floor is z 25 all along y 667-683 (not a ledge; the user stepped him south with no trouble). One step south was
    refused: the player King Vitaman (0x00397F8E) showed on (5192, 672) 0.4 s later. The map planner kept the
    refusal as a wall for the session, and from (5192, 671) that one-tile passage is the only way south on the map
    (`pathfind.plan` with the move denied: no route), so every waypoint came out "no route … skipping it for 600 s"
    until the user moved him. Since then a refusal on a map-walkable step expires after `DENY_RETRY_S` (30 s), and a
    plan with no route tries those steps again at once (test_mover `test_shove_denied`).
- **2026-10-17 11:43 (hunt-20261017-114313-9ad6)**, the first run on 4e7091e: the songs played from the
  tambourine in the user's locked wooden box (no instrument prompt). 30 kills. 12:01:06 a leave at 6/100 hits was
  called off on the way out (a potion to 100). **Dead to a group of reds at 12:06:03.** 12:05:52 "LEAVING by the
  moongate: red player 0x20df0e in view at 18 tiles": NewbieMurderer ("Legendary Alchemist", [MILK]) at (5226, 653),
  Logan at (5244, 671). "All Follow Me", then the rope 9 tiles **north** to (5243, 662), along the route to the
  gate (70 steps from there: the map's way west runs along the north side), so nearer the red, not away. Within
  2 s: "Copium is attacking you", "NewbieMurderer is attacking you", "Eatbrain …", "BerpziD …" (four players),
  "Corp Por" (Explosion) from NewbieMurderer; -28, -24, -31, a potion (+29), -26, -21, dead in 11 s from the
  sighting. [INFERENCE] four reds together can't be outrun on a 70-step way out; but the escape went toward
  them. Open: plan the flight's route (and the rope's landing) away from the reds' side, or an escape that
  doesn't need the gate (no recall works there but beside a gold moongate).
- **2026-10-17 13:35 (hunt-20261017-133459-6175): dead to the red Tugarin zmey at 13:40:58**, 8 s after the sighting
  (18 tiles; "Legendary Serial Killer", [Zmeishche Tugarishche, RUS], a tamer: "ALL KILL" to a primordial whelp,
  an earth elemental and a daemon). The store's `events` hold the full lines (the capture keeps 64 bytes a
  packet). 13:40:51.8 "All Follow Me"; 13:40:52.1 "Tugarin zmey is attacking you!"; the rope's target answered
  13:40:52.9 "That location is blocked." (501942: a summon on the tile [INFERENCE]) but the runner waited out the
  1.5 s landing wait, so the first step came at 13:40:54.4; Explosions (Corp Por) -27, -31; 13:40:56.1 an **area
  Peacemaking on the way out** (the summons in war mode within 3), "You fail to pacify", 1.5 s standing; -32;
  dead after 2 steps in 7 s. The user: "we died to a red again while we just stood still". Since then: no bard
  skill while walking out from a hostile player (`HuntLoop.pk_flight`), the rope's landing wait ends on the
  server's refusal (`ROPE_REFUSED`), and the rope never aims at a tile a mobile stands on.

## Loot prices for the return rule (2026-10-17, the store's prices table, items `loot:<name>`)

The client's names differ from tiledata's (`combat.item_name` falls back to tiledata): 0x0FB9 is "a chromatic core"
(tiledata "chroma core"), 0x1120A "gold ore map" (tiledata "ore map": the metal is in the name only), 0x1120D
"avarhide skinning map", 0x7166 "plant chemicals" (tiledata "bottle"), 0x79A5 "unidentified cestus" (tiledata
"caesti"). Loot events keep graphic and amount only, so names came from the captures' 0xFF sub 0x15 name replies.
- **The user's**: research materials 25k, plant chemicals 1k, chromatic core 35k, gold ore map 12k, avarhide skinning
  map 15k, blood aspect core 14k, arcane scroll 1k; other aspect cores 10k "for now". Gems "at their vendor price":
  the wiki's Tinkering table (knowledge #13200-13202, "Gem $"): citrine 20, tourmaline 30, amber 40, amethyst 50, ruby
  60, sapphire 70, emerald 80, star sapphire 90, diamond 100.
- **Discord** (harness/data/discord.db, #buy/#sell/#pricecheck; a throwaway regex over the messages): one buyer's
  ladder on 2026-10-04 for every map tier (dull copper / shadow iron / copper ore 4k, bronze 6k, gold 13-16k, agapite
  20k, verite 32k, valorite 40k, avarite 75k; skinning dullhide-copperhide 100, bronzehide 200, goldenhide 300,
  rosehide 2.5k, verehide 5k, valehide 7k, avarhide 20k; lumber dullwood-copperwood 200 ... valewood 9k, avarwood 35k);
  "any distillation" 1k, "any phylactery" 1.5-2.5k, junk skill scrolls 5-6k on sale and 3k wanted, arcane scrolls
  ~1-1.1k, chromatic cores 28-32k, aspect cores by kind (arcane 7-9k, blood 9k wanted, air 8-11k, frost 14k, shadow
  20k, madness 20-25k, void 28k, command 32-38k, temporal 3-3.5k, eldritch 5k). Recorded at the WTB ladder for maps,
  1k for distillations, 1.5k for phylacteries, 5k for skill scrolls; the user's numbers where given.
- **Aspect cores by kind** (user: "add the Discord prices"), the median of the posts since 2026-09-01 (struck-through
  ones left out), `loot:<kind> aspect core`: command 32.5k, harvest 30k, fortune 25k, lightning 25k, shadow 24k,
  madness 22.5k, poison 15k, air 9.5k, arcane 8k (WTB 7k, sales 8-9k), temporal 3k; void 28k, frost 14k, eldritch 5k
  from August sales only. Blood stays the user's 14k (Discord WTB 9k). Gadget 1k (the user: "only worth 1k"; Discord had
  only WTB ranges, "100-300k"); any other kind 10k.
- Unidentified magic weapons and armour are no longer looted (their tiledata art is Wearable on layer 0; the user:
  "heavy"); instruments still are (an identify may show "supremely melodious").

## Field of Souls: eerie corpses and soul jars (Halloween event 2026-10-17 to 10-24)

Event rules: the 2026-10-17 news post (`ctl know search "Field of Souls" --tag news`); tactics, scoring by damage
share and Logan's first run: `ctl know search "Field of Souls" --tag fos`. Code: `harness/soul_jar.py`.

- **Eerie corpse** (live 2026-10-17, Logan; captures `20261017_151814` 15:42:56–15:47:17 and `20261017_172358`
  17:26:17–17:26:34, map0): a ground item of random human-corpse art from tiledata's "dead" range `0xB1C0`–`0xB21C`
  (seen `0xB1C5`, `0xB1F3`, `0xB201`, `0xB202`, `0xB20B`), click label "an eerie corpse". It spawns with its
  pieces, each a separate item on the next serials: blood `0x122D`, `0x122F`, `0x122A`, bones `0x1D0B`,
  `0x1D09`, and last "eerie whispers" `0x6C6F` (a spell effect) at serial +6..+8. Neither the art nor the
  whispers alone is unique: the store holds 64 "dead"-art and 321 whispers ground items since September (decor,
  spells). But "dead" art followed within 10 serials by a whispers item is unique: all 5 such pairs in the store
  are that day's eerie corpses, every one with the same pieces in the same order. That is the rule
  (`soul_jar.CORPSE_ART`, `WHISPERS_GAP`; until 2026-10-17 evening the code keyed on `0xB1F3` alone and would have
  caught 1 of the 5).
- **How often**: in Logan's ~4.5 h of play on 2026-10-17, 5 eerie corpses came into view: 13:38 (5188,667),
  15:22 (5179,680), 15:42 (5243,667), 17:05 (5195,683) and 17:26 (5634,695), all in the Mausoleum area. He filled
  jars at the 15:42 and 17:26 ones. The first came into view 18 tiles off and left the world model 5 s later (out
  of range) while he fought on; he filled his jar at it 4 minutes later.
- **The session clock** (Logan's three runs): the server names the spirit 3.05–3.09 s after the instance gump's
  answer and ejects us 892.2–893.9 s after the naming. Ejection is "Your body can no longer sustain itself within
  the Field of Souls." spoken over **our own head** (our serial, type 0), not as system text; `SpiritWatch`
  missed it until 2026-10-17 evening. A filled jar's click label counts down too: "[14m 30s left until
  ejected]" at 18:32:50.3 matched the 18:47:20.3 ejection exactly; "[13m 30s ...]" at 15:57:10.6 was 3 s
  short. `fos_field.FieldLog` keeps the clock (`SESSION_S` 893). The state port's `field` drives the viz's
  countdown, and `fos_warning` 90 s before the end sounds `alerts.toll` (two deep bell tolls) in a live
  session. A replay of run 3 warned at 18:45:51.1, 89.2 s before the real ejection.
- **Soul jars**: empty `0xA99F` ("an empty soul jar", stackable, tiledata "soul jar"; monster loot or 1,000 gp from
  an Occult Merchant), filled `0xA9A3` ("a filled soul jar captured by <name>", single-click: "(contains the soul of
  <spirit>)", "[will expire in 59m 57s]").
- **A fill** (Logan 2 tiles from the corpse, the jar in a pouch in the pack): one flush 44 ms after the jar's
  double-click: the empty jar deleted, two effect items (`0xAB42`, and `0xA428` "spirit energy" saying "'My name was
  Karin Winckel....'"), the system lines "You should visit the Occult Merchant in Prevalia or Corpse Creek with this
  filled soul jar to put this spirit to rest." and "You increase your [EventScore by 30. Current score is 30", the
  corpse and its pieces deleted, then the filled jar (a new serial) in the backpack. What the server says when no
  corpse is in range wasn't seen.
- **The alarm** (user request 2026-10-17: "a spoooky alert sound when an eerie corpse is seen (once per corpse)"):
  the proxy watches every live session's world events. The first recognition of each eerie corpse (above; either
  piece's `item_seen` may come first) plays
  `alerts.eerie`, a 2 s synthesized wail: a glide 330 → 494 → 196 Hz with 5.5 Hz vibrato and a voice a tritone up.
  It plays on its own thread (`winsound.PlaySound` with `SND_MEMORY` can't be asynchronous, and the relay must not
  wait). The proxy also emits the proxy event `eerie_corpse` {serial, map, x, y, z}, which goes to the store's
  events, and prints a console line. The seen serials are shared by all sessions and kept for the proxy's life,
  so a corpse that leaves the view and comes back, or that a second character sees, sounds once. `UO_QUIET` mutes
  it. Preview: `python -c "import sys; sys.path.insert(0, 'harness'); import alerts; alerts.eerie().join()"`.
  Replaying Logan's four captures of 2026-10-17 through SessionTaps sharing one `EerieSeen` gave exactly the 5
  alarms above, and `soul_jar.eerie_corpses` found each corpse in the world state at its alarm.
- **The spirit alert** (user request 2026-10-17: "add an alert for our named spirit coming into view"): on entering
  the field the server names our spirit: "If you put the spirit of Remon Stace to rest, you will receive a 33.0%
  bonus to all further points earned this session." (live 17:27:09, 3 s after entering; 15:55:50 for Karin Winckel).
  Eerie spirits are human-bodied mobiles named like players. The stock client single-clicks every mobile as it
  comes into view (`0x09`), and the server's click label (`0x1C` type 6) carries the name 43 ms later: Remon
  Stace at 17:28:32.18 → .22, Karin Winckel at 15:57:02.02 → .06. So the label is the sighting.
  `soul_jar.SpiritWatch` (one per session, in the proxy) remembers the name. On the first label, `0x98` name or
  speech from a mobile of that name that is in view, it plays `alerts.spirit` and emits the proxy event
  `spirit_seen` {serial, name, x, y, z}. `alerts.spirit` is a 1.4 s rising bell chime (A5 C6 E6 A6), distinct from
  the wail. It fires once per field session, also when the spirit was already in view when named. It forgets the
  name at "Your body can no longer sustain itself within the Field of Souls." or 16 minutes after the naming.
  Replaying both of Logan's runs gave one chime each, at 15:57:02 and 17:28:32. Preview:
  `python -c "import sys; sys.path.insert(0, 'harness'); import alerts; alerts.spirit().join()"`.
- **The hunt runner fills jars** (`--soul-jars`, default on; docs/HUNT_LOOP.md "Soul jars").
- **A takeover mid-walk ended the run** (`hunt-20261017-172523-bbe2`, 17:26:28-17:29:48). The user took over on
  the walk-in to fill a jar at the 17:26 corpse. The step the runner sent as the gate closed came back 3 tiles off,
  (5615, 691) → (5618, 692), and was saved as a teleporter; the user had moved Logan. The row is deleted from the
  store. `Mover.step` then gave up after 40 gate polls (200 s): "ABORTED: walk gated too long", with Logan in the
  Mausoleum. Now a walk waits out a closed gate like `Link.act` does, logging the close and the reopening once.
- **Dead after a soul-jar walk (2026-10-20 18:22:07-27, hunt-20261020-181726-433a, capture session_20261020_180909;
  replay `replay.timed_packets` → `WorldRuntime`).** Logan at (5197, 682, z 25) on Maus 3, the pets beside a
  shadowguard 3 tiles off as it woke (the patrol was "letting it come"); an eerie corpse at (5179, 680), 18 tiles
  west, with an idle vampire countess (5177, 682), a corpsebride (5176, 678) and a vampire bat (5175, 682) beside
  it. "walking over to fill a soul jar": 16 steps alone; the pets stayed on the shadowguard at (5196-5198, 682-684),
  15-17 tiles behind him for the rest of his life. A spell at (5189, 682) at 18:22:09.8 (-26, the countess casting
  at ~12 [INFERENCE: the only caster awake in view]), -21 as the jar filled (18:22:13.7, +30 score, Gilles Styles),
  potion 54 -> 95, then -10, -30, -9 beside the bride and bat; the retreat went 7 tiles west (away from the pets,
  `Team.retreat_tile` doesn't look at them), -8, -25 at (5172, 685); the hurt leave at 14/100 had a 51-step route
  to the gate; dead 18:22:27. The walk's `stop` knew only attackers, leave rules and the corpse; none of the
  patrol's guards. Fixed: with `--pets` the jar walk isn't started while the pets fight or lag, and stops on
  `patrol_hold` or `Team.pull_check` (the corpse then waits `JAR_DEFER_S` 120 s). test_sim `test_soul_jar_guards`
  (before: walked; `jar_later` didn't exist). Simulator, the 18:22 geometry (the sim's pets follow on guard, so it
  shows the group, not the pets left behind), 20 seeds, 20 s each: lowest hits mean 56 -> 67, runs below 30 hits
  3 -> 1, jars filled within the 20 s 20 -> 0; 250 s each (4 seeds): the crawl pulls the three one at a time and
  every jar is filled after.
  Not changed: a retreat with the pets far off goes wherever the fewest idle monsters see, not toward them.
- **Transfixed on a retreat: "too many blocked moves" (2026-10-20 19:23:23-30, hunt-20261020-183232-1def, capture
  session_20261020_180909).** Logan at (5180, 680) with a vampire countess, a corpsebride and both transfixed pet
  copies on him; 54 -> potion 100 -> 30 by 19:23:23.04 (-21, -13, -36). At that instant: "The creature overpowers your
  will and transfixes you!" (system text), self flags 0x20 -> 0xA0 until 19:23:33.65, buff icons 57/58/59/19/140.
  The retreat (logged 19:23:23) sent 21 steps in 7 s, every one denied, and the Mover aborted the run ("too many
  blocked moves (21)"). He stood alive (vet +35, regen to 86) and the run was over, Logan left on Maus 3. Item use
  was refused too: "You cannot use items while in this state." x3 at 19:23:27.13. "You are no longer hindered." at
  19:23:34.15, 11.1 s after the transfix, the same as the only other one on Logan in the store (2026-10-17
  15:27:03, 11.1 s). Against that, the store has 110 "*transfixed*" pet copies. Fixed: `Mover.held` /
  `wait_held` (docs/HUNT_LOOP.md "Guards"). The simulator now transfixes Logan in `player_share` 0.018 of a
  countess' transfixes (docs/SIM.md). test_sim `test_transfixed_walk` (before: "too many blocked moves (21)", as
  live; after: free after 11.0 s, arrived). Run A/B, 48 x 20 min, seed 17: identical, so no run in the batch had a
  transfix on Logan change its course (2 % of transfixes is rare).
  - **Then dead with no runner (19:30).** Logan stood at (5180, 680) with no task: the Seer may not fight or walk
    in a dungeon, and the user hadn't answered yet. AlaFlambe's "(bonded)" death ~19:30:05, GraveRobber's ~19:30:25,
    then Logan. Two further fixes: (1) `--max-blocked` counted the run's denials (7 in the run's 50 minutes before
    the transfix); now it counts one walk's, and the abort names its walk, so the retreat's own catch sees it.
    (2) A failed walk inside the hunt no longer ends the run: `WalkFailed`, `HuntLoop.walk_failed` (docs/HUNT_LOOP.md
    "Guards"). test_sim `test_walk_failed_carries_on` (before: `WalkFailed` out of `hunt()`).
- **Stood fighting 13 steps from the last teleporter (2026-10-20 22:57:06-31, hunt-20261020-223926-1d18).** On the
  walk-in to 5522,637 (onto Maus 3), attacked 58 steps out (the pets guard, potion 79 -> 100, vet 75), then again
  at (5513, 647), route 12 to the tile before the teleporter (cheb 10): the pets guarded and Logan stood among an
  abomination, a devourer beetle and an insane reanimator (vet 56, potions at 60 and 62, a discord, a provocation)
  until the user took over at 22:57:30.8 and walked him through (user: "I had to take control because Logan was
  dying to a mob instead of running to Maus 3"). Fixed: attacked within `TELE_DASH_R` 15 route steps of the
  teleporter, `walk_in_dash` (docs/HUNT_LOOP.md "Recall in, recall out", In). test_sim `test_tele_dash` (before: a
  stand under "All Guard Me" until the 780 s test timeout; after: on Maus 3 in 3.6 s, both pets through). Simulator
  48 x 20 min seed 17, before / after (with the simulator's teleporter rule below): deaths 25 / 21 runs (1.95 / 1.57
  per game hour), kills 100.6 / 106.0 and gold 94,650 / 98,583 per hour, runs with a pet death 18 / 21, a pet left
  behind 1 / 0.
  - **Pets on "All Follow Me" come through a teleporter while in view** (user 2026-10-20, Outlands; this replaces
    the earlier [INFERENCE] of RunUO TeleportPets' 3 tiles). The normal walk-in still waits for them within 3 tiles
    of the teleporter before its step (a caution now, not a need); the dash doesn't wait. The simulator's
    teleporters bring pets on Follow within its view (18) too.
- **An eerie corpse passed up, safe to fill (2026-10-20 23:02:02-23:04:10, hunt-20261020-225836-002b).** User: "The
  runner passed up an eerie corpse that was safe to capture." The walk to the corpse at (5223, 656), 18 tiles off,
  stopped at 23:02:03: "vampire bat in sight at 10 tiles: letting it come; the corpse waits 120 s". The bat died at
  23:02:15, but `jar_later` kept the corpse out of `soul_corpse` until ~23:04:03; at 23:03:21 Logan looted a
  corpsebride at (5222, 655), one tile from it, and still didn't try (the defer was filtered before the within-reach
  rule). At 23:04:10 the user paused the gate to fill it by hand. Fixed: a hold's defer ends at our next kill, and
  a deferred corpse within `soul_jar.FILL_WALK_R` is tried at once (docs/HUNT_LOOP.md "Soul jars"). test_sim
  `test_soul_jar_defer` (before: not tried after the kill nor from one tile; after: both). Eerie corpses aren't in
  the simulator's batches (tests place them), so no batch.

## Field of Souls: the field map (reaper spawns, 2026-10-17)

Code: `harness/fos_field.py`. The viz overlay: docs/VISUALIZER.md §2.17. User: "map the reaper spawns".
- **The field is one island, two instances.** Normal: x 6200–6395, y 600–803 (map0); its entry at (6314, 783).
  Hard: the same island shifted by (+874, −32), entry at (7192, 747). All 2,350 land tiles sampled on a 3-tile grid
  over the island are equal at that shift; the neighbouring shifts match ≤ 20 %. So both runs map onto one frame.
- **Reapers look like players.** Bodies 400/401 (players and eerie spirits too); hayman 400, roaming terror 63,
  roaming nightmare 780. Only the click label ("a reaper of souls") tells them apart. The stock client clicks each
  mobile on sight, so the label's moment is the sighting.
- **First-sight distances** (102 reapers, both runs): 63 exactly 18 tiles off (they walked into the view's edge),
  18 at 15–17, 21 at ≤ 14 = appeared inside the view = spawns (`SPAWN_R` 14). None of the 21 came in the first
  3 s after a jump of ours (entering fills the whole view at once, `ARRIVAL_S`).
- **Corpses:** ~190 field-monster corpses came into view per run (172 and 173 reapers), but only 27 and 33
  reapers died *in view* (0xAF prunes the mobile as dead, its 0xDEAD comes ≤ 2 s later). The rest died elsewhere
  and their corpse came into view later. Only in-view deaths count on the map (they match the time watched).
- **What the proxy records** (since 2026-10-17 evening; proxy events, so in the store): `fos_session` at the
  spirit's naming, `fos_mob` per field monster first labelled (with `dist` and `spawn`), `fos_corpse` per corpse
  (with `in_view`). `python harness/fos_field.py backfill` replays the captures of field sessions without them
  (events seq ≥ 1,000,000,000 so they never collide with a live session's). It was run for 20261017_155426 and
  _172358: 70 + 68 `fos_mob`, 191 + 189 `fos_corpse`. `python harness/fos_field.py map` prints the totals.
- **What two runs show** (30 min watched): 21 spawns and 60 in-view deaths, too few for spawn points. The
  densest so far: the east shore around (6370, 660) (Hard 7244, 628), 3 spawns in ~1.5 min watched. Deaths are
  densest at (6294, 710) (Hard 7168, 678) and on the east shore. Each run adds ~10 spawns and ~30 deaths.
- **Reading a run back from the store** (how runs 3–6 were analysed, 2026-10-18). Window: `fos_session` to the
  ejection line over our own head. There are no 0x0B damage packets. The overhead numbers come as `speech_heard`
  over the mobile:
  - hue 89 "-N" over a monster: damage dealt by us and our pets;
  - hue 946 "-N" over us or a pet: damage taken;
  - hue 2213 "+N": a heal.

  Every payout is a system line "You increase your [EventScore by N. Current score is S", so points per kill and
  points inside come from those. An effigy is "You will now receive a 4.0% bonus …". Eerie spirits are graphic
  400, hue 23999 and notoriety 3. Players share body 400, so the graphic alone doesn't pick them out. Deathbloom
  plants are item `0x5D4A` with a `0xAA12` piece on the next serial, both labelled "deathbloom". Positions of
  items and mobiles need a world replay (`replay.timed_packets` through a `proxy.SessionTap`, as
  `fos_field.replay_events` does; ~3 s per session), because `item_seen` rows carry no position.

## Other players at the Mausoleum (19 live captures 2026-10-13 to 10-19, measured 2026-10-19)

Measured for the simulator's other players (docs/SIM.md "Other players"). A throwaway script replayed every capture
(`replay.timed_packets` -> `WorldRuntime`) and sampled once a second while Logan Wolf (0x00104A60) stood on Maus 3
(x 5170-5290, y 620-740): every mobile within 18 tiles, `threats.identify` for players (not Logan, not a ghost), his
pets (0x000DDEFE, 0x0026EF56) apart, other pets = non-human bodies with a pet tag or notoriety 1/2. Live server only:
20261013_173449, _204518, 20261014_170144, _173858, _210020, _221341, 20261015_210709, 20261017_110506, _133304,
_151814, _155426, _181210, _183126, _210503, _215839, 20261018_113717, _175134, _203339, 20261019_095934 (the
2026-10-15 test-shard captures left out). 6.69 h, 24,098 samples.

- **How often someone is there**: at least one other player in view 27.4 % of the time; 0.36 on average, 1.3 when
  any (seconds with 0/1/2/3/4+: 17,486 / 5,148 / 1,087 / 283 / 94; at most 10).
- **Who**: 92 % of player-seconds blue (8,044), 2.8 % red (246), 2.7 % green (233), 0.9 % grey (80); in war mode 37 %
  of them. Every player's 0x20/0x77 carries flags 0x20 (20261019_095934: all 8 players near Logan at 10:10-10:11).
- **Coming and going**: 565 in-view appearances (gaps over 30 s split them) by 240 names (65 unnamed: no click label
  yet); 551 arrived while Logan was there: 82 an hour. In view they stay briefly: median 8.0 s (p10 1.0, p25 3.9, p75
  25, p90 44, mean 17.5; n 525 not cut by Logan leaving). They mostly run through.
- **Pets**: 266 of the 565 had a pet or summon not ours within 4 tiles (0.35 such pets in view on average). Tags: 6,308
  bonded, 2,128 summoned, 20 tame sightings; notoriety 1 (8,158), 2 (290), 6 (57). Most seen: a giant black widow
  (894 s), a black widow (769), liches (547, three names), a vampire (419, summoned), a colossal spitting viper (354), a
  sun wyrmling (298), a colossal black widow (225).
- **Where** (player-seconds by the nearest scenario spawn; biased to where Logan was [INFERENCE]): the apparition at
  (5210, 717) 709, the shadow minion (5210, 696) 645, the shadowguard (5242, 651) 598, the corpsebride (5229, 651)
  523, the shadowguard (5193, 681) 487, the vampire bat (5207, 683) 447, the shade wolf (5210, 660) 398: all over
  the west and middle rooms, none favoured.
- **What they fight**: the captures' 0x2F swings are only Logan's own (225 at monsters, 1 at a player), so "fighting"
  is a monster in war mode within 2 tiles of another player or pet and more than 2 from Logan and his pets: 320
  monsters, 393 such episodes: shade wolves 54, banshees 42, shadowguards 38, tormented souls 32, corpsebrides 30,
  vampires 26, vampire bats 26, shadow minions 23, nightstalkers 18, forgotten souls 17, elder vampires 16, elder
  vampire countesses 15, apparitions 13, vampire countesses 9, consorts 7, purgers 7.
- **How long a monster on them lasts**: of the 393 episodes 164 ended in its death (within 5 s of the last sample
  beside them), 52 with it out of war mode, 84 out of our view. With the start seen (n 97): median 9.9 s to death
  (p10 1.3, p25 3.9, p75 16.3, p90 22.5); shadowguard 11.0 s (n 15), shade wolf 8.3 (13), banshee 1.5 (13, its
  dematerialize ends the adjacency), corpsebride 17.6 (10). Their damage rate (the type's hits x its share at the
  start / seconds, n 89): p10 178, p25 306, median 456, p75 625, p90 885 hits a second, three times our pets'.
- **Their corpses**: 160 of the 164 were blue to us (0xDEAD notoriety 1), 4 grey; of all 279 monsters seen dying after
  being on another player, 230 blue. No "-N" damage numbers over their fights (20261019_095934 10:10:51-10:11:30: Lord
  Karpola's potions on two tormented souls showed only the "5".."1" countdowns).
- **Onto us**: 31 of the 393 episodes were followed within 15 s by the monster in war mode beside Logan or a pet and
  away from any other player: an upper bound on switches to us (our own "All Kill" at it counts too).
- **Live loot rights**: the 29 hunt task logs of 2026-10-16..19 have 62 "someone else's kill (blue)" against 516
  "killed" lines (12 %).
- **The simulator with them** (docs/SIM.md "Other players"; `run.py --others`, 144 runs each, seeds 17/65/113 x 48,
  20 min, the code of 27b3e84):

  | | Runs with a death | Deaths/h | Gold/h | Kills/h | Pet-death runs | "All Kill" at a monster on a player | Kills lost to their loot rights | s engaged on one before ours hurt it |
  |---|---|---|---|---|---|---|---|---|
  | Zone crawl, players off | 58 | 1.46 | 96.0k | 101.6 | 53 | - | 0 | - |
  | Zone crawl, players on | 45 | 1.05 | 80.5k | 84.3 | 42 | 333 | 363 | 1,455 (375 monsters) |
  | Loop crawl, players off | 62 | 1.60 | 98.1k | 104.8 | 43 | - | 0 | - |
  | Loop crawl, players on | 52 | 1.27 | 82.2k | 81.6 | 43 | 274 | 318 | 1,234 (309) |

  With them a sixth less gold and kills (they kill 260-270 monsters an hour across the level, 30-37 an hour in our view
  against the captures' 42) and a quarter fewer deaths [INFERENCE: fewer monsters left to wake]. The runner never
  opened one of their corpses (0 refused opens); 9 % of its credited kills were theirs (live 12 %). In view the
  simulator has someone 31-33 % of the time (live 27 %), 0.39-0.44 on average (0.36), 66-80 arrivals an hour (82),
  stays median 10-11 s (8).
- **Found by the simulator**: from Logan's live tile at 10:10:57 the tormented soul on Lord Phaze stood behind a wall,
  and 4b06515's "All Kill" rule still waited 15 s (it needs a tile seeing it within bard_step's 2 steps). Fixed by
  walking round until it is seen (27b3e84, docs/HUNT_LOOP.md "Someone else's fight"). Players on, the fix against the
  code before it: zone crawl 45 vs 51 runs with a death (1.05 vs 1.21 an hour), gold 80.5k vs 82.1k, seconds engaged
  on a monster on a player before ours hurt it 1,455 vs 1,991, "All Kill" at one 333 vs 280; loop crawl 52 vs 56 (1.27
  vs 1.38), 82.2k vs 82.1k, 1,234 vs 1,352 s.

## The 18:22 death replayed, and fight step-aways blocked by mobiles (2026-10-20)

The death and the soul-jar fix are in "Field of Souls" ("Dead after a soul-jar walk", 4c72c12). Added on the
improve-maus branch:
- **The minute in the simulator** (`harness/sim/fight1822.py`, docs/SIM.md "The 18:22 live death"): from 18:22:00,
  Logan at (5210, 680), the shadowguard, the countess room and its neighbours as the capture has them, the corpse, no
  respawns, 4 game minutes, 24 seeds. On the final calibration of "The simulator against the live runs" below: the
  runner of the morning (c334fce) 7 deaths, 24 jars filled, Logan's lowest hits median 43; the merged runner (main's
  jar guards, transfix wait and failed-walk carry-on, and the fix below) 1 death, 13 jars, 55. The deaths left are
  the countess room's own fight (corpsebride wails, transfixed copies). With the Tank draw adopted later the same
  day ("The simulator against the live runs"): 3 deaths, 9 jars, 53.
- **A step away in a fight that mobiles block no longer stands waiting** (`Mover.walk_to` `mobile_wait=0` for the
  retreat, stand-off, back-off, evade, fall-back and back-to-the-pets walks; `WalkFailed` at once): `MOBILE_WAIT_S`
  90 s let a retreat stand "cut by mobiles we couldn't shove; waiting" while hit (live 2026-10-15 22:57:56, dead a
  second later; the replay's seed 5 in the countess corridor, which now lives). `test_mover` checks it.
- Not changed: the retreat's direction (it ranks tiles by the idle monsters that see them, not by the pets).

## The simulator against the live runs (2026-10-20, docs/SIM.md "Parity with the live runs")

`harness/sim/parity.py` compares Logan's live pets visits since the live server (the store's episodes and `fight`,
`death`, `leave` job events; the captures for damage on the pets and the monsters met) with a simulator batch, per
hour on the level, with a bootstrap 95 % interval over the live visits. What it found, and what changed:
- **The simulator's clock ran before the live-server cut.** `sim/run.py`'s EPOCH was 2026-10-12, before
  `hunt_return.LIVE_SINCE` (2026-10-15 21:07), so the return rule never read a career's own visits ("a death every 80
  min", its prior, in every run), and each career run restarted at the same game time. Now game time starts
  2026-10-21 00:00 and a career's next run starts 600 s after the last ended (live median 685 s between runs, 43 gaps).
- **The simulated crawl stayed shallow**: 77 % of its time in zones 1-2 against the live 46 %. Live runs start from
  a store with hours of zone knowledge and open zones 2-5 at once; the careers started empty. `run.py --prior <store>`
  seeds every career with the live store's hunt history (episodes and hunt job events): zone shares 15/31/31/21/2 %
  live, 17/22/36/21/4 % simulated.
- **Spawns against the live sightings** (idle first sightings over 37 visits put one monster at each spawn): out a
  second banshee and a second shadowguard beside the first, and a shadow minion at (5210, 696), a vampire bat at
  (5207, 683) and a shade wolf at (5208, 672) that were never seen; in the two apparition spawns of the live
  clusters (one before), a second vampire at (5196, 641) and the blightmare at (5243, 687) (met 2.5 an hour live,
  absent before; its spells and Spellblight from the 10-19 fight). The simulator had met 2-3x the live banshees,
  shadowguards, minions and bats an hour (a ratio against live rates the capture mapping below had cut; on all 41
  visits the removals still hold: 1 pair of banshees and none of shadowguards within 5 tiles and 30 s, 1-3 sightings
  ever by the removed minion, bat and wolf).
- **Bard success by type**: the formula put every target past difficulty 100 at its 49 % floor; live Discordance
  succeeded 70 % (458/653; the shadowguard 79 %, the shade wolf 52 %), Peacemaking 68 % (111/163). The scenario now
  carries the live rates per type with 10+ attempts (`bard.observed`).
- **The phoenix hit harder than modelled**: 134 live kills from full hits (souls, shadowguards, purgers, brides,
  minions, knights) took 905,861 in 2,614 s from our first hit, with 1,409 gravebug and 1,460 phoenix attack
  animations (periods 1.86 s and 1.79 s, as modelled); at the gravebug's median 99 a hit that is 530 a phoenix
  attack, where the simulator applied 386. Its damage x 1.373; kill times from our first hit came from 1.0-1.6x of
  live to 0.9-1.3x. Not the creatures' hits: live kills from full took exactly the scenario's hits + 1.
- **Eerie corpses**: 2.5 an hour came into view live; the simulator now has them (`--eerie`), 2.4.
- **Visits read from another character's capture** (`parity.capture_char`, `capture_windows(..., me_serial)`): a
  visit's capture was the first whose rows spanned its start; a second character's day-long sessions (0x000ED575 at
  (400-449, 900-949): 20261018_113327, 20261019_095918, 20261019_133709) spanned 16 of Logan's visits, so their
  damage on him and the pets counted 0 and their monsters met none. Live per hour on all 41 visits: the damage on
  Logan 2,315 (read 1,617), the gravebug 10,417 (7,373), the phoenix 2,111 (1,533); monsters met 1.25-1.6x. Scans
  keyed to visit windows must check the capture's own serial (its login: `rt.state.self.serial` within ~8 packets).
- **The first contact** (user's call 2026-10-20, "Adopt the Tank draw"): where Logan was the nearest seen at a wake,
  live the gravebug took the first contact 61 % (99 wakes, 2026-10-15 audit), the simulator 29 %, so its monsters,
  the nightstalker above all (Logan nearer at 31 of 44 simulated wakes against 4 of 22 live), went for Logan. A
  guarding Tank in sight within the monster's range + 3 now takes the wake with `tank_draw` 0.9 (61 % simulated):
  deaths 2.0 -> 1.5 an hour, the damage on Logan 2,308 -> 1,757. Against the corrected live 2,315 it had been on par
  and is under since.
- **A vet channel keeps its range when we step** (live: 126 of 128 channels with our steps in them healed, 44 with
  the nearest pet 2-8 off); the simulator had cut it to 1 and answered "too far" 30 times an hour (live 0.6).
- **A blind ranged pet walks to a shot**: the simulated phoenix stood 25 % of its targeting time behind a pillar or
  the gravebug in a gap without an attack (one fight 100 s on the gravebug alone); live it went 10+ s without an
  attack in 1.2 % of the gravebug's fighting time. Fights over 60 s beside the gravebug: 5.4 an hour -> 0.8.
- **Spawns moved and added**: a shadowguard 3 tiles east to its live sightings (in pull reach through a wall, the
  runner walked 155-168-step routes at it; live never over 100); a corpse purger at (5229, 732), 12 idle first
  sightings there (purgers met 3.2 an hour -> 7.8, live 9.7).
- **A countess doesn't fight a pet she holds**: while a copy of the gravebug was up, 2 of the countesses' 47 hits on
  us went to the held gravebug, 28 to the phoenix, 17 to Logan (with it free 107 of 134 on it; animation within
  0.15 s, one candidate only). The simulator now has her drop a pet whose copy is up; the 18:22 replay 3 deaths of
  24 -> 1.
- **Logan takes 16 % of our side's damage live, the simulator gave him 8 %**: by type his share of the attributed
  hits was 0.03-0.23 live against 0.00-0.05 (the shadowguard 0.18/0.05, the banshee 0.17/0.02). Not the bard skills
  (a Discordance on a monster hitting the pets left his share of its hits at 4 %, 360 casts). `world.RETARGET_P` 0.15:
  a monster on a pet turns to Logan when he is nearer it and in sight [INFERENCE: mechanism]; his damage 0.66x ->
  0.90x, deaths 0.9 -> 1.3-1.4 an hour. Turning to him anywhere in a caster's reach instead: deaths 4.5x at 0.05.
- **The gravebug draws whoever is nearest at a wake**: live a monster's first victim was the gravebug 73-79 % whoever
  of us was nearest (1,173 wakes; the phoenix nearest: gravebug 74 %, phoenix 7 % of 58). The Tank draw now takes
  the phoenix's wakes too: the phoenix as first victim 20 % -> 14 % (live 10 %), its damage 3,600 -> 3,095 an hour.
- **A sixth of Logan's damage is disease ticks**: 96 of his 603 numbers on 41 visits were -21 (the purger's and the
  blightmare's disease tick), 4.1 infections an hour: "You have contracted a foul disease!" (purger barrage) 1.4,
  "The creature's spell blights you with a foul disease!" (blightmare) 1.4, "You have been ensnared and diseased!" 0.9
  (source not seen: nothing in war mode within 12 or a shadowguard 6 off), "The creature torments you with a foul
  disease!" (a tormented soul's melee) 0.4. The simulator: 1.7 an hour. A tormented soul's "5 s hits from 3 tiles"
  were these ticks on the nearest-monster attribution.
- **The nightstalker goes for the pets** (live: first victim never Logan, 0 of 35; 1 of its 111 attributed hits on
  him): scenario `defaults.pets_first`. It had been the simulator's top killer (18 of ~70 deaths a batch).
- **The phoenix holds where it has a shot**: live in fights it stood 3+ tiles from Logan 57 % of the time; the
  simulated one stepped back to within 2 between shots (11 %), so a nightstalker's charge at it landed beside him
  (17 % of landings within a tile of Logan against the live 8 %). Now its distances from him match live by the tile.
- **Pets hardly catch the diseases**: -21 chains 5 s apart came 0.32 infections an hour on the gravebug and none on
  the phoenix live, 2.0 on Logan; the scenario's `pet_chance` 0.01 (the simulated gravebug had ~24 an hour).
- **Spawns spread and idle monsters drift**: live idle first sightings stood a median 3 tiles from their spawn's point
  (q75 4, q90 6) and idle monsters in view moved in 11 % of seconds. `defaults.spawn_spread` 4 and `wander_per_s`
  0.11 (`world.wrng`, so the world's other draws stay put). With them the retarget refitted: 0.05 (0 gave Logan's
  damage 0.75x and deaths 1.2 an hour; 0.15 0.99x and 2.0).
- **The level is emptier than its spawns**: live idle monsters in Logan's view 1.52 on average, a spawn's point
  occupied at his first approach 36 % and 7+ minutes after his last pass 23 %; with the spread and wander the
  simulator still had 1.72, 59 % and 59 %. `others.unseen` (an idle spawn out of his view gone at 1/600 a second, 60 %
  of the spawns up at the start) gives 1.50, 49 %, 47 % [INFERENCE: other hunters]. A longer respawn instead
  emptied the spots he had just passed (8 % at 1-3 minutes against the live 28 %).
- **The purger's barrage was 2x on Logan**: live his numbers with a purger nearest (disease ticks out) 14 / 20.5 / 27
  (n=26); the scenario put [15, 38] x 1.6 on him. Now `ranged_observed` [11, 21, 15.5].
- **Melee on Logan is ~1.2x the gravebug's, not 1.6x**: by type 0.88-1.41 (1.18 weighted, 58 of his attributed
  hits); `defaults.melee_to_player_x` 1.2, spells keep 1.6 (the banshee's 1.74).
- **The simulator's level box missed the north rooms**: `area` ended at y 640, so the north-east room and the elder
  vampires' hall (10 % of live Logan's time on the level) fell out of its hours, and the damage on the walk in counted.
  Now y 610 and the counters gate on `World._on_level`. The "north-east room never visited" finding before it was
  this: the sampler skipped y < 640.
- **The dear rare spawns respawn slower**: met an hour live the blightmare 4.4, the reanimated daemon 3.2, the
  master reanimator 2.6, the consort 1.5 against 5.7, 4.3, 3.9, 3.1 at 120-300 s; now 180-480 s (the blightmare) and
  360-720 s [INFERENCE: fitted]. Gold 1.23x -> 1.19x.
- **The server tells a heal's size**: a heal potion's cliloc 1008158 carries the number healed (the affix): 612 live
  drinks, those that didn't fill Logan 30 / 34 / 41 (q25 / median / q75), under 30 hits a median 40; the trip's six
  had put it at 25-40. A vet channel on him: his hits' rise at "You finish using veterinary supplies." 21 / 29 / 36
  (q10 / median / q90, n=167; 12-25 before). Live he fell below 15 hits 2.9 times an hour and mostly lived; with the
  old heals the simulated one fell 1.2 times and nearly always died. Now `potions.heal` 28-50, `vet.heal_self` 20-37:
  deaths 1.2 -> 0.9 an hour.
- **Casters' shots stray to Logan**: live his share of a caster's attributed hits 0.12-0.23 (purger, shadowguard,
  banshee, minion), simulated 0.00-0.05. `world.SHOT_AT_PLAYER_P` 0.2 (a shot at a pet goes to him when he is in
  reach and sight): his damage 0.63x -> 0.99x, potions 0.64x -> 1.02x, deaths unchanged; turning whole monsters to
  him instead (`RETARGET_P` up) raised deaths before the damage.
- **Compare the simulator with the visits of the runner it runs**: the live pets visits since 10-15 21:10 were run
  by several runners; the crawl's seek of monsters in view (11c884b) came at 10-17 23:08, and the simulator's crawl
  sought 74 times an hour against 21 on all live visits. The "where the crawl goes", gold, bard and west-wing gaps
  were mostly that. `parity.py --since` defaults to `RUNNER_SINCE` (2026-10-17 23:08) now: 32 visits, 3.76 h.
  On that yardstick (288 runs, after a second tormented soul spawn at (5236, 685), the rare spawns' respawns and the
  eerie rate refitted to it): kills 0.99x, gold 0.98x, the damage on Logan 0.97x, hits lost 0.99x, potions 1.00x,
  discord and peace successes and fails, stand-offs, retreats, leaves, walk-out deaths, soul jars, eerie corpses,
  every monster type met within the live interval.
  **Then**: the phoenix's `threat_x` 0.3 -> 0.1 (live 81 / 9.6 / 9.4 % of the monsters' attributed hits on the
  gravebug / phoenix / Logan; 0.1 gives 80 / 9.4 / 11 %), the nightstalker takes the Tank before the phoenix (live 94 %
  of its hits on the gravebug), and the vet supplies heal a pet 104-202 (the gravebug's bar rose a median 30 % at a
  live channel's finish, 145 of them; 40-120 before). On the yardstick now: kills 0.98x, gold 0.97x, the damage on
  Logan 0.95x, the phoenix 1.22x and the gravebug 0.92x (both inside the interval), potions 0.99x, vet 1.11x.
  **Still apart** (docs/SIM.md "Still apart"): pull holds 1.22x, discord fails 1.19x (at the interval's top), vet
  1.11x, deaths 1.4 an hour against 0.5 (inside the interval).
- **A bootstrap over visits under-reads a rare count's interval**: resampled visits can't hold more events than the
  visits that had one; 2 live deaths in 3.76 h read 0-1.4 an hour, the exact Poisson interval 0.06-1.92.
  `parity.boot` widens a count of 200 or fewer to its Poisson interval.
- **Repeated holds point at runner loops**: merging a hold reason's repeats within 20 s, live 89.6 holds an hour
  were 78.3 episodes, simulated 109.7 were 85.0; the simulated repeats were mostly "a corpse purger would see us" 1-3 s
  apart (back off, approach the same target the same way, hold). `Team.approach` passes such a target over now.
- **Blightmare deaths**: per engagement as lethal simulated as live (1 death in 5 live blightmare infection episodes,
  10 in 45 simulated; "The creature's spell blights you with a foul disease!" 8 times live, 4 in the 10-19 19:54
  death), but the simulated blightmares reach Logan 2.7 times an hour against 0.9 on all live visits.

## Live run of 2026-10-21 (hunt-20261021-091743-38b6, capture session_20261021_085153)

- **No "All Guard Me" through a vampire countess fight** (user: "once the tank is transfixed and the countess aggros on
  our character, it looks like we're not saying 'All Guard Me' to get the aggro back on the tank once it is
  un-transfixed"). True. The countess 0x00379747 on Maus 3, 09:48:27-09:49:53: the guards at 09:48:45 and 09:48:53
  (after two kills), then none until her kill. Store events: her `*transfixed*` over a hostile copy named as the pet
  (GraveRobber 3650410 09:48:44.58, AlaFlambe 3650486 09:48:55.37, GraveRobber 3650556 09:49:05.48, AlaFlambe 3650606
  09:49:11.06, GraveRobber 3650980 09:49:30.09, AlaFlambe 3651017 09:49:35.17; Logan's own copy 3650892 at 09:49:19.87
  with "The creature overpowers your will and transfixes you!"); each copy's 0x1D 10.1-11.1 s later (09:48:54.77,
  09:49:05.71, 16.54, 21.90, 40.23, 45.71): no "end" line over a pet, the copy's removal is the release. The runner's
  "target GraveRobber 0x0037B36A" (09:48:52) and "0x0037B3FC" (09:49:11) were these hostile copies (notoriety 3,
  pacified with the area peace), not our pets. Their attack animations line up with most of Logan's hits: -21
  09:48:51.01, -21 09:49:10.50, -21 16.33, -17 19.74, -23 38.45, -16 40.68, -20 44.06.
  Causes: (1) "All Guard Me" came only at an arrival, a kill, a pet back alive, a lost target or the end of an evade;
  nothing on a pet's release (six releases, the fight on throughout); the 8 s `GUARD_REPEAT_S` would also have held a
  release 1 s after a guard back (09:48:53.69, release 09:48:54.77). (2) A called-off leave (09:49:34 "healed to
  100/100; back to the pets") walked back with no guard, the pets left on the leave's "All Follow Me". (3) That
  "All Follow Me" (09:49:20.84) never reached them: Logan was transfixed at 09:49:19.87 and the server answered "You
  have been silenced and cannot speak." at 09:49:20.89 (also 2026-10-17 15:27:06.20, an "All Guard Me"); the pets fought
  on at the countess on the 09:48:57 "All Kill" (her -227/-224/-347 at 09:49:23.9-24.8). Fixed: `Team.free_pets` says
  "All Guard Me" at a release with something on us (an attacker or a hit within 5 s), past the 8 s; a called-off leave
  guards before the walk back; `Team.say` waits Logan's own hold out. The simulator now silences speech while Logan is
  held. test_sim `test_countess_reguard`, `test_leave_called_off`. 48 x 20 min, seed 17, `--vet-range 5` (the same
  simulator): deaths/h 0.96 -> 0.88 (runs with a death 14 -> 13), kills/h 109.6 -> 110.4, gold/h 107,145 -> 106,806,
  damage on Logan/h 2,278 -> 2,258, pet deaths 1 -> 1, a pet left behind 0 -> 1 (seed 40: the gravebug 9 tiles off
  didn't come to "All Follow Me" for 25 s on a time-up walk out; 92da05a's batch had one too), guards/h 186.5 -> 193.7.
- **Walking onto the tank beside a reanimated daemon** (user: "we seem to go stand directly on our tank which is
  getting us wallopped by the demon's AOE, but our Without Borders healing codex perk lets us heal from 5 tiles away so
  we don't need to stand so close"; the run had `--vet-range 5`). True. The daemon 0x003794C4 stood at (5241, 675)
  (the target answers of 09:53:48-09:54:10). 09:53:44 "stand-off to (5247, 668) ... beyond the vet supplies' reach of
  the pets" (no tile clear of its 6-tile zone is within 5 of the gravebug beside it); 09:53:52 "to a hurt pet: route 9
  steps from (5247, 668)", "arrived at (5241, 674)": `Team.to_pet` walked to range 1 of the gravebug whatever
  `--vet-range`, a tile from the daemon; 09:53:55 vet; 09:53:56.05 its bone burst Logan -41 (the phoenix -109); 09:54:06
  the stand-off out again, and the 09:54:14.65 burst (gravebug -91, phoenix -118) missed him. The stand-off and to_pet
  alternate by design (out while the tank is whole, in to heal it); the "in" leg was the fault, and the stand-off's own
  heal case (`vet_range` < the zone) stayed wherever it stood, beside the tank too. Also seen: 09:54:10 "to the corpse"
  walked to (5242, 676), beside the daemon, 8 s before its death (the loot walk ignored area zones; fixed after, see
  "Loot walks into an area hitter's reach" below).
  Fixed: `Team.vet_tile` (within `--vet-range` of the pet, 3+ off pets and monsters in war mode, the least deep in an
  area hit's reach, then within `--vet-range` - 1), used by `to_pet` and by the stand-off's step in to heal the tank.
  test_sim `test_vet_tile_off_tank` replays it: before "to a hurt pet: route 9 steps from (5247, 668)", "arrived at
  (5241, 674)" (the live tile, 1 from the daemon), the stand-off staying 2 from it; after both go to (5240, 669), 5 from
  the gravebug and 6 from the daemon (its burst radius 5). Sim seed 17, `--vet-range 5`, before / after: 48 x 20 min
  deaths/h 0.88 / 0.67 (runs with a death 13 / 10), kills/h 110.4 / 108.9, gold/h 106,806 / 106,624, damage on Logan/h
  2,258 / 2,408, pet deaths 1 / 1; 144 x 20 min deaths/h 1.27 / 1.12 (52 / 48 runs), kills/h 112.6 / 111.0, gold/h
  109,815 / 109,099, damage on Logan/h 2,410 / 2,421, pet deaths 5 / 4, pets left behind 6 / 4 (daemons are rare in
  the simulator: 4-5 killed in 41-43 h, the daemon's damage on Logan 4-8 an hour).
- **Loot left in corpses: lifts too fast** (user: "the runner loots items faster than the allowed delay, so we're
  leaving valuable loot behind (message like 'you must wait' and it just moves on instead of waiting and grabbing the
  loot)"). True: six lifts refused this session, at 09:47:46.62, 09:53:01.32, 10:00:37.33, 10:05:45.81, 10:06:54.76 and
  10:07:53.80, each 0.38-0.50 s after the last lift the server took, each answered 0.04 s later by "You must wait
  another moment before you may pick up an item from here." and `27 05`, logged "lift of 0x… rejected: not taken" and
  the item left. The whole store (2026-10-02 to 10-21, 23 sessions, 52,708 lifts): 144 such refusals, 0.06-0.50 s after
  the last accepted lift (two at 0.511 and 0.571 s, proxy-timed); accepted lifts from 0.387 s (one), then 0.467 s up
  (5 below 0.5 s, 174 at 0.5-0.6 s); a refused lift doesn't restart the server's clock (one 0.323 s after a refusal and
  0.776 s after the last taken lift was accepted). So the server's gap is ~0.5 s from the last lift it took. The
  runner's only pause was the human drag (median 0.55 s, lognormal). Fixed: `combat.LIFT_GAP_S` 0.6 s between lifts
  (`loop_hunt.loot`, `ctl act loot`), a "must wait" refusal tried again (`combat.LIFT_TRIES` 3). The simulator refuses
  lifts within 0.5 s (`world.LIFT_GAP_S`); its corpses hold one gold pile, so its batches hardly meet it: 48 x 20 min
  seed 17 `--vet-range 5` on the runner before the fix gave the same numbers with the model as without; before / after
  the fix deaths/h 0.67 / 0.75, kills/h 108.9 / 112.4, gold/h 106,624 / 108,731, pet deaths 1 / 2 (the pacing's waits
  shift the runs' timing; noise).
- **Loot walks into an area hitter's reach** (user: yes to fixing the 09:54:10 walk noted above). 09:54:10 "to the
  corpse: route 5 steps from (5247, 675)", "arrived at (5242, 676)": a tormented soul's corpse a tile from the
  reanimated daemon at (5241, 675), in war mode on the pets, inside its bone burst's 6; it died at 09:54:18 and the
  burst at 09:54:14.65 hit both pets (-91, -118). `HuntLoop.loot` walked to within 1 of any corpse whatever was near.
  Fixed: `Team.loot_tile` (the vet tile's chooser, `_reach_tile`: clear of every area hit's reach, within LOOT_RANGE 2,
  out of fire), the route round the reach when that is a short detour (`Team.around_areas`, bounded to twice the
  distance + 8 steps), and a corpse with every tile within 2 of it in reach kept queued and looked at every second
  (`HuntLoop.loot_next`), looted once the hitter is dead or gone. A first, unbounded version planned 117-123-step
  detours to corpses 7 tiles off and doubled the simulator's deaths (2.28/h against 1.03, 82 runs with a death against
  43; shadow minion deaths 3 -> 25 on those walks). test_sim `test_loot_off_area_hitter`: before, Logan 2 and 5 tiles
  from the live daemon; after, 6 (the corpse kept queued, then looted after the death; or looted from (5247, 677), 2
  from it). Also seen, not changed: an unbounded plain loot walk after a refused shove planned 117 steps (sim seed 18,
  no area hitter about; the pre-fix runner too).
  Sim 144 x 20 min seed 17 `--vet-range 5`, before (145b080) / after: deaths/h 1.03 / 1.10 (runs with a death 43 / 47),
  kills/h 114.1 / 115.0, gold/h 110,798 / 112,122, damage on Logan/h 2,386 / 2,401, the area hitters' (apparition,
  daemon, nightstalker, knight) on Logan/h 519 / 461, pet deaths 5 / 4, pets left behind 0 / 4 (2 events: see below).
- **"Pets left behind" counted again in each later run of a career** (`sim/run.py`: a run's `junctures` were the
  career store's whole list, so `runs_with_pet_left_behind` counted a pet left behind in run k again in runs k+1...).
  The 252bed2 batch's 4 were 2 events: seed 70 (career 22, run 1; counted again in seeds 101 and 132) and seed 146.
  Neither came from the loot or re-guard changes: seed 70, a time-up walk out on "All Follow Me", the gravebug (not
  held, no target, mode follow) stood at (5197, 683) from t 1202 s to 1232 s while Logan waited 9 tiles off at (5206,
  686) and then went (the simulator's pet step, greedy then a 40-step A*, found no way: [INFERENCE] a simulator pathing
  gap; live pets path server-side); seed 146, the phoenix transfixed by a countess during a hurt leave that doesn't wait
  for the pets (as designed). Another seed block (1017, 144 x 20 min, the counting fixed): before / after 252bed2 pets
  left behind 1 / 0, deaths/h 0.90 / 1.01 (runs with a death 39 / 43), kills/h 113.2 / 113.6, gold/h 111,394 /
  111,372, pet-death runs 3 / 1. Over both blocks runs with a death 82 / 90 of 288 (within noise: ~8 either way).
  Earlier batches' "pets left behind" here (eb4aba4's 0 -> 1, 2445d12's 6 / 4) were counted the same way. Fixed: a
  run's junctures are those posted in it (test_sim `test_career_junctures`).

## Dead leaving from two reds on Maus 3 (live 2026-10-21 10:35:13, hunt-20261021-102636-fdda)

Capture session_20261021_085153 (0x77 is serial u32, x u32, y u32, z i32, dir: `world/layouts.py`), the store's events.

- **The leave.** 10:35:05.11 "LEAVING by the moongate: red player 0x15abe in view at 17 tiles": 'Ticket Tongje' 0x15abe
  ('Legendary Serial Killer', '[PEC]', notoriety 6) at (5243, 708), Logan at (5243, 725), the pets beside him. Tongje
  ran south down Logan's own column, a 0x77 every 0.18-0.21 s (712 at 06.18, 716 at 07.07, 718 at 07.45); 'Di ve nha'
  0x6545 ('[PEC]', red) came into view at (5243, 707) at 06.23 behind him. The way to the gate (5203, 703) from Maus
  3's east wing is one corridor north (x 5240-5246, y 696-719) and round by (5242, 654): 135 steps from (5243, 726),
  through them.
- **The rope toward them.** "All Follow Me" 05.39; "[Rope" 05.99 (no player on him yet: `rope_worth` true); the cursor
  06.04; **"Ticket Tongje is attacking you!" 06.07** (and 0xAA, the server making Tongje his combatant), Tongje at
  (5243, 711), 14 tiles; our target (5244, 716) still went out at 06.54, 0.47 s later, against the user's rule (a rope
  only while no player is hitting us yet). The target was the route's farthest tile within 9: north, toward them.
  Landed 07.33 at (5244, 716), Tongje at (5243, 717): beside him. The pets didn't come along (both pets' next 0x20 at
  07.59 at (5243, 725), 9 tiles behind).
- **The walk past them.** "to the moongate: route 124 steps from (5244, 716)", north. Two steps refused with 500111
  "You are frozen and cannot move." (08.09, 09.08; the cause isn't in the packets [INFERENCE: a paralyze]). Di ve
  nha's "Corp Por" 07.59, 09.88, 11.90, Tongje's "Vas Ort Flam" 08.71, "Corp Por" 11.00, 13.35 (2.1-2.4 s apart each;
  both stood 2-5 tiles off to cast); on Logan -33 -31 (10.12, 100 -> 36), a potion (+27, 63), the vet, then on north at
  11.56: (5244, 715 ... 710) past them, -28 12.39, -27 13.20, -9 13.51: dead at (5244, 710), Tongje at (5243, 713),
  Di ve nha at (5243, 711). 8.4 s from the leave.
- **Not ours: the swings at Tongje.** 0x2F Logan -> 0x15abe at 07.36 and 12.54 are the server's auto-defence after the
  0xAA (as with Bastet, "PK death in the Terran wilds"): no 0x05 or 0x72 from the agent or the client, Logan's 0x20
  flags 0x20-0x22 throughout (never 0x40, war mode); the same swings at monsters at 10:26:45 and 10:29:34.
- **Fixed** (`loop_hunt.flee_out`, `rope_jump`; docs/HUNT_LOOP.md "Never toward them"): the hostile players are danger
  zones on the Mover (6 tiles); a way to the gate that comes within 10 of one, nearer than we stand, makes the flight
  run away from them first (the reachable tile farthest from them), from 22+ off it waits, and only cornered (or after
  60 s) does it go past them, round them as far as the way allows; a rope lands only farther from every one of them
  (off the route if need be); a player's attack line between the rope's cursor and our target cancels the cursor.
  New log lines: "to the moongate: the way passes Ticket Tongje at (5243, 711) (15 tiles off): away from them first,
  toward (5245, 733)", "rope to (5244, 732) (6 tiles, 15 -> 21 tiles from the nearest hostile player): landed",
  "rope: a player turned on us before the target went out: cursor cancelled (Esc), running on", "to the moongate: no
  way round … past them, round them as far as the way allows", "rope: no landing in reach farther from the hostile
  player(s)".
- **The simulator** now has red players a test places (`World.add_red`, from these numbers; docs/SIM.md) and the rope's
  0.8 s from our target to the landing (06.54 -> 07.33). test_sim `test_red_flight` (this ground, both reds): before,
  the rope 12 -> 3 tiles from Tongje and the nearest red 0 tiles in the first 3 s; after, the rope 9 -> 15 and 16 -> 22,
  the nearest 9. `test_rope_cancel_attacked`: before the jump went out after the attack line, after the cursor is
  cancelled. Seeds 1-20 of the live ground: before, 20 dead in 10.3 s on average (9.2-16.5), nearest red in the first
  3 s 0, 87 of Logan's own moves brought him nearer the nearest red; after, 20 dead in 13.2 s (11.5-20.4), nearest 9-11,
  35 such moves (cornered at the wing's south end, going round them). One red from behind (Logan at (5243, 716), the
  red at (5243, 734), the gate's way north): 10 of 10 through the gate, before and after.
- **No way out of that wing past two chasing casters.** In the simulator they keep 3-4 tiles behind and kill him in
  the pocket. The only other way from the east wing is the teleporter at (5253, 736) to Maus 4 and back onto the fight
  level by (5700, 627) -> (5266, 627) (Logan walked it on 2026-10-14 in 83 s; `harness/data/sim/mausoleum.json`
  `teleporters`); the Mover doesn't route across levels, so a flight doesn't take it.

## Dead from two reds on the gate's way (live 2026-10-21 17:37:22, hunt-20261021-172808-cc2f)

The runner of ad908ed (loaded at 17:28:08). Capture session_20261021_164924, the store's events, the task log.

- **Before.** Logan fought a tormented soul and a nightstalker with the pets in the room at (5235-5245, 674-677),
  stand-offs out of the nightstalker's charge, hits 24 -> 42 (potion 37:08.1), -14, the vet +28.
- **The reds, from the capture (0x20/0x77).** I Make Bodies 0x44E2D ('Legendary Invader', '[Do I have consent?, Syn]',
  notoriety 6) into view at 37:09.91 at (5219, 661, 25), the top of the ramp west of the corridor, 18 tiles from Logan
  at (5236/5237, 676): the leave ("LEAVING by the moongate: red player I Make Bodies in view at 18 tiles"). He walked
  (5220, 660) 10.31, (5221, 659), (5222, 658) 10.72 and was gone at 10.92 (0x1D, 19 tiles). Botan 0x32A620 ('Legendary
  Serial Killer [Andaria]', '[Soul Reaper, Syn]', red, flags 0x62: war mode) into view at 12.03 on the same tile,
  in and out of view (0x1D 13.63). Both came back down the ramp and east along the gate's way: I Make Bodies (5229,
  654, 12) at 13.27, one tile every 0.20 s along y 654 to (5241, 654) at 16.33, (5243, 656) at 16.84, "I Make Bodies
  is attacking you!" 16.85 (Logan at (5243, 660), 4 tiles); Botan a row north (y 653) to (5239, 653) 16.13, "Botan is
  attacking you!" 17.31 at (5241, 655). Mason. 0x44BF53 ('[Veteran, Syn]', red, war mode) into view at 20.77 at (5217,
  663), on the same ramp top.
- **What the flight did.** "All Follow Me" 11.11; `flee_out`'s first look ~11.4 found no hostile player: I Make
  Bodies had gone out of view at 10.92 and `hostile_spots` noted players only when the flight itself looked (the
  leave's `player_threat` saw him and kept nothing). So: no "the way passes …" line, the rope with no players to
  keep from (the route's farthest tile, "rope to (5242, 674) (5 tiles): landed", the old line's form), and "to the
  moongate: route 82 steps" north up x 5243 toward y 654, the gate's way, where they came. From 13.27 the reds were
  in view and became danger zones ("danger ahead changed; replanning" at each step: the routes bent round them
  tile by tile), but that leg had begun with none known, so it never stopped for a look: 17:37:15-21 north to (5243,
  658) beside I Make Bodies, then west along y 654 between both. Hits: potion 78 -> 100 at 18.68; -25 19.41 (his
  "Corp Por" 19.42), -33 19.65, -10 20.32, -12 21.84, -21 21.86: 100 -> 0 in 2.5 s, dead at (5233, 655) at 21.87,
  Botan a tile off at (5234, 654).
- **Not ours.** No war mode, no 0x05: Logan's 0x20 flags 0x20 throughout.
- **Fixed** (`loop_hunt`): `note_hostiles` notes every hostile player on each hostile-player check, the leave's
  `player_threat` included, so the flight starts from where the leave's red was; a flight leg stops for a look as
  soon as a hostile player it didn't know of comes into view ("a hostile player in view"); one out of view counts as
  passed by a route within 10 tiles + 5 a second since he was seen (as far as he could have run): the zones bent the
  route round where he was, not round where he went. The simulator now sends the live 0x1D when a mobile leaves view
  range (docs/SIM.md "Red players"), and `add_red` takes a live red's route. test_sim `test_red_out_of_view` replays
  17:37 with both reds' capture tiles: before, "rope to (5242, 674) (5 tiles): landed", "to the moongate: route 82
  steps from (5242, 674)", then "danger ahead changed" up x 5243 and beside them (the nearest red 1 tile in the first
  9 s; the live log almost line for line); after, "to the moongate: the way passes I Make Bodies at (5219, 661) (18
  tiles off): away from them first, toward (5241, 675)", "rope to (5241, 675) (4 tiles, 18 -> 22 tiles from the
  nearest hostile player): landed", "… 22+ tiles from them here, looking again", the nearest red 17 tiles in the
  first 9 s. In the simulator the reds then hunt him down the east wing (they know where he is: [INFERENCE] of the
  model) and he still dies there, 22 s after the leave instead of 15.
- **The leave rule itself (the user's call; evidence only).** Both deaths of the day were flights to the crimson gate
  (5203, 703) along the way the reds came: at 10:35 down the corridor from the north, at 17:37 from the ramp top at
  (5217-5219, 661-663), which the gate's route crosses ((5242, 654) -> (5224, 657) -> (5216, 665) -> (5208, 673));
  all three reds of 17:37 came into view on that one tile [INFERENCE: they come in by the crimson gate, the way in
  from town, so a flight to it runs at the next ones]. At 17:37 neither red came at Logan's room: I Make Bodies walked
  east along y 654, 20 tiles north of it, and named Logan only at 4 tiles, after the flight had taken him up the x
  5243 corridor to meet him. His hits at the leave (24-57 with the mob fight) didn't decide it: at 100/100 he died in
  2.5 s once both were within 5. There is no capture of a red meeting Logan while he stayed with the pets, so whether
  holding there (the pets on guard) or running elsewhere (the east wing's teleporter to Maus 4 at (5253, 736)) does
  better is not known.

## Dead in a blightmare's reach again (live 2026-10-21 18:21:15, hunt-20261021-175514-caa5)

Visit 3 on Maus 3, the run started 17:55:14 (after 0a6bb7f). Capture session_20261021_164924 (0x20 flags, 0x77,
0xA1, effects), the store's events (speech_heard numbers, intents), the task log. No player in view; the pets
(GraveRobber, AlaFlambe) whole at the end. The same blightmare spawn and the same tiles as the 10-19 19:55 death above
("Dead in a blightmare's sight").

- **Who hit him.** One blightmare, 0x003C5C2A, at (5242, 685) from 18:20:40 (its last 0x77 18:20:46, the same tile) to
  the end: war mode (0x20 flags 0x40) from 18:20:48.5 to 18:21:14.6, its cast animation (7) every 1.5-1.9 s, the
  18:21:01.4 bolt (graphic 14036) from it to Logan. Its spells on Logan: -27 51.8 (with "The creature's spell blights
  you with a foul disease!"), -24 56.8, -26 57.8, -27 01.9, -23 07.0 (the second "spell blights you"), -25 10.3, -28
  11.9, -12 15.0 (its 13.6 cast [INFERENCE]: no disease effect with it). The two infections' -21 ticks (effect 14186):
  56.8, 01.8, 06.9, 11.8 (the first one's four) and 12.1 (the second's first). The only other hit, -13 at 48.2, was
  the tormented soul 0x003C59EF a tile or two off as he left the pull spot (its swing animation at 48.2 [INFERENCE]; it
  then fought the pets: GraveRobber -23 -17 -20, AlaFlambe -16). No shadowguard or other caster: the shadowguard names
  are corpses.
- **Hits** (0xA1): 100 (potion 44.3) -> 87 (48.2) -> 60 (51.8) -> 100 (potion 54.9, +39) -> 55 (56.8) -> 29 (57.8)
  -> 73 (vet +39, 58.3) -> 84 -> 36 (01.8-01.9) -> 69 (vet +32, 04.0) -> 74 -> 100 (potion 05.6, +26) -> 56
  (06.9-07.0) -> ~92 (vet +31, 09.4) -> 67 (10.3) -> 76 (vet ticks) -> 6 (11.8-12.1: -21, -28, -21 in 0.3 s) -> 11
  -> 0 (15.0).
  Potions 44.3, 54.9, 05.6 (`healing.POTION_COOLDOWN_S` 10 s: the next one 15.6, after the death); the vet channel
  begun 10.7 on him would have finished ~15.7. At 6/100 nothing could heal in time: only being out of its sight before
  its 13.6 cast (he was 10 -> 14 tiles off between 12.7 and 14.0).
- **How he got there.** 18:20:44 at (5244, 672), 13 tiles off: "crawl: blightmare in view at 13 tiles; not waiting
  here", "patrolling toward it ((5243, 682))". 45: an approach to a soul, (5245, 675) (10 off, in its sight; "would
  wake 2 worth 4.1 at once"), the pull spot (5246, 675) (10 off, out of its sight by los.Sight); 48.0 the patrol on, a
  step to (5245, 675) ("would wake 2.3 more onto 1.8": the 2.3 is the blightmare's weight), 48.3 "off the tank" to
  (5242, 673), 12 off and in its sight; 48.5 it woke on Logan (first sight 45.5 or 48.0: its wake delay can't tell).
  From 49.5 to 18:21:05 he stood at (5242, 673) with the pets 4 off on the soul ("Waiting for the pets", the soul's
  fight, "Healing up before moving on"): five spells, two infections, two potions, three vet channels. Kill 18:21:05,
  loot at (5244, 673) (12 off: the -23 and the second infection), then 18:21:09 the crawl's patrol went on to its
  waypoint (5243, 682) toward the blightmare at 61/100, (5245, 675) 10 off: "stopped (under attack)", "target a
  blightmare at 10 tiles", "Pets on guard; waiting for a blightmare to come on" (KILL_AFTER_S), vet at 71/100, then
  the 0.3 s burst. "LEAVING by the moongate: hits 6/100", dead at (5243, 667) at 15.0, 18 tiles from it.
- **The runner's three faults.** (1) The patrol and an approach walked into an idle blightmare's first sight with the
  pets behind; it stands and casts from 10-12, it doesn't come on (the corpse purger's case, `aggro.SHY`). (2) The
  reposition (the 10-19 fix) refused the step out of its reach: from (5242, 673) to (5242, 672), 13 off, it still
  sees him, and `stand_score` counts a caster's sight to 18 tiles, so the "no more idle eyes" test took the step as an
  eye more (19 against 18; replayed in the simulator on the live tiles: `reposition` False). Sixteen seconds in its
  reach. (3) After the loot the crawl walked on toward its waypoint by the awake blightmare that had just cast at him
  (12 -> 10), at 61/100, two infections running and the potion 3.5 s old.
- **Fixed.** `aggro.SHY` takes the blightmare: an advance stops on a step into an idle one's sight before the pets
  ("a blightmare would see us"), `Team.back_off` steps out of it, kept off 60 s as a crawl danger zone; no approach to
  it, no hold for it, no bard skill before the pets fight it (and `--patrol-pull`, off, would send the pets at one).
  `Team.reposition`: from a shy caster or ranged monster a tile out of its reach counts as out of its sight (it
  doesn't come on; `Team.shy_beyond`), logged "reposition: blightmare 0x… 12 tiles off can cast at us here; to (5242,
  672) (1 -> 0 in reach)". `crawl.Crawl.observe`: an awake (war-mode) shy monster's reach (12 for the blightmare, 13
  for the purger) is a crawl danger zone while it stays awake ("crawl: keeping out of the reach of the awake
  blightmare at (5242, 685)", "crawl: (5243, 682) is near a creature we avoid now; choosing another"). Not changed:
  the loot walk into its reach (one spell), the 4 s "waiting for … to come on" before "All Kill" at a caster on us.
  The reposition rule out of reach for every caster was tried first: the casters that come on follow (sim seed block
  1017: 43 runs with a death in 144 against 33, deaths/h 1.02 against 0.76, kills/h 109.0, gold/h 107,469).
- **Tests** (test_sim, each failing on 0a6bb7f): `test_blightmare_reach` (the 18:20:49 tiles, a channel on us: before
  no step, after (5242, 672), 13 off, 0 in reach); `test_blightmare_first_sight` (the 18:20:44 scene, the runner's
  ticks for 60 s, seeds 3, 5, 7: before seed 3 dead (the blightmare on him 2 s, 58 of its damage) and seed 5 hit for
  22; after none on him); `test_blightmare_awake_patrol` (the 18:21:09 tiles, the crawl's waypoint (5243, 682) by the
  awake blightmare: before the patrol to 10 tiles off, after the waypoint dropped, never nearer than 12). The scene
  replayed whole (`sim/fight1820.py`, from 18:20:44, 3 min, seeds 1-48): before 22 deaths, 40 runs with its spells on
  him (148), lowest hits median 13; with only the reposition and the awake rule still 22 (the simulator's blightmare
  woke on the pets and turned to Logan nearer it); after 0 deaths, none of its spells, lowest hits median 100 (it
  backs off and patrols elsewhere; no kill in that room).
- **Batches** (20 min runs, `--vet-range 5`; before 0a6bb7f / after):

  | | seed 17, 48 runs | seed 1017, 144 runs |
  |---|---|---|
  | deaths/h | 0.61 / 0.61 | 0.76 / 0.76 |
  | runs with a death | 9 / 9 | 33 / 33 |
  | kills/h | 112.0 / 112.2 | 111.1 / 110.7 |
  | gold/h | 108,202 / 108,100 | 108,943 / 108,569 |
  | pet-death runs | 4 / 4 | 2 / 2 |
  | blightmare damage on Logan (disease) | 21 (68) / 0 (0) | 186 (289) / 277 (306) |
  | deaths with a blightmare in the last 15 s | 0 / 0 | 1 / 2 |
  | back-offs | 1 / 4 | 4 / 22 |

  The batches meet few blightmares (12-13 in 43 game hours on the 1017 block, 2-3 on the 17 one): no cost to kills or
  gold beyond noise. Both blightmare deaths after (seeds 1081 and 1086; 1081 also before) had it walk up to Logan, 2
  and 1 tiles, after his steps out of its reach or sight: the simulator's blightmare follows, live it stood (docs/SIM.md
  "Limits").

## Half the deaths in the simulator (2026-10-21/22, goal run on the optimize-maus branch)

Goal (user 2026-10-21): deaths an hour in the simulator at most half the current runner's, gold an hour not
significantly worse (one-sided Welch t-test on per-run gold per game hour, p >= 0.05), the simulator not made easier.

- **The batch**: `python harness/sim/run.py --runs 200 --seed 1001 --careers 20 --jobs 16 --others --eerie --prior
  <frozen> --json -- --vet-range 5 --crawl-learn-s 180` (the parity batch as live plays, 200 runs). `<frozen>` is a
  copy of the main store's `episodes` (loop hunt) and `job_events` (job hunt) rows older than `run.py`'s `EPOCH`, the
  only rows `seed_prior` reads (90 episodes, 4,446 events), so the batch can't drift as the live store grows. A batch
  is deterministic: the same code and seeds gave the same 200 summaries twice. Deaths an hour = deaths / game hours
  (`sim_s`), as `run.py`'s `all:`. Seeds 2001-2200 were a holdout for every candidate.
- **Baseline** (41412d3): 84 deaths in 80.2 game hours, 1.047 an hour; per-run gold an hour 98,707 (sd 16,187);
  holdout 83 in 79.3 h, 1.046.
- **Where the 84 died** (the runner's logs; the damage sources in the last 15 s): 29 stood in a Mover wait "cut by
  mobiles we couldn't shove; waiting" (13 "off the tank", 11 of them at (5192, 672, 25) in the countess corridor; 10 "to
  the corpse"), a wait in which no tick runs (no potion, no vet); 35 left at 1-14 hits (`HEAL_IN_PLACE_FLOOR`) and died on
  the walk out; by top source the corpse purger 27 (2.4 % of the gold) and the blightmare 14 (2.5 %); a purger's shot
  (~20) and its stacking disease (17 every 5 s, two or three at once) took Logan 100 -> 6 in 6-15 s.
- **Kept** (each step measured on seeds 1001-1200; the whole set on both blocks below):
  - `Mover._walk_to`: a non-urgent walk's wait for mobiles ends when our hits fall ("hurt waiting"); the fight's care
    walks (spacing, pull spot, fire, reposition, bard step) never wait (`mobile_wait=0`). 84 -> 70.
  - Every loot walk bounded to twice the distance + 8 steps: after a refused shove in the countess corridor the loot
    walk replanned 119-123 steps through the south rooms (14 of 41 deaths in one batch); after it none over 100.
  - `Team.cover` (the user's rule: "stay out of their aggro range"): out of an awake purger's or blightmare's shot
    first; the spacing, the loot tile and the vet tile out of it too; `--patrol-pull` on by default; no crawl seek of
    a shy monster; "All Kill" at once at one on us; no rejoin walk under one. With "All Patrol" on, the first cover
    (the reposition's 4-step rule) gave 48 deaths, the own 8-step walk 35.
- **Final** (this branch, `--patrol-pull` default on, the batch command unchanged):

  | 200 runs | deaths | game h | deaths/h | ratio | gold/h pooled | gold/h per run | Welch p (worse) | pet-death runs | pet left behind |
  |---|---|---|---|---|---|---|---|---|---|
  | seeds 1001-1200, baseline | 84 | 80.2 | 1.047 | | 101,403 | 98,707 | | 4 | 1 |
  | seeds 1001-1200, final | 38 | 92.5 | 0.411 | 0.393 | 105,285 | 103,686 | 0.9996 | 4 | 2 |
  | seeds 2001-2200, baseline | 83 | 79.3 | 1.046 | | 101,041 | 96,337 | | 6 | 2 |
  | seeds 2001-2200, final | 44 | 91.4 | 0.481 | 0.460 | 105,302 | 103,285 | 1.0000 | 4 | 4 |

  Runs ended without a death but not done: 4 and 3 (baseline 3 and 3), most "overall timeout (1800s + 180s to
  leave)" on a time-up walk out (the batch's 30-min `--timeout`; live runs have none).
  Merged into main with that night's runner changes (04c4523: the approach ends on the target's floor; 1709477):
  seeds 1001-1200 47 deaths in 91.8 h, 0.512 an hour (ratio 0.489), gold an hour per run 104,554 (p 1.0000). Within
  the batch's noise of the branch's 38 [INFERENCE: ±8 deaths between equivalent arms], but the margin is thin: check
  it again after the next runner change.
- **Tried, not kept**: `HEAL_IN_PLACE_FLOOR` 0.35 (69 deaths against 70) and `--retreat-at 0.8` (69): noise.
  `--never-aggro` the purger and the blightmare: 44 deaths but pet-death runs 4 -> 11 and pets left behind 1 -> 7 (the
  evade runs off without them). A `--crawl-avoid` box over the north-east room (5252 640 5285 662): 35-41 deaths with
  the earlier fixes, gold the same, but it gives up the bound souls' room; the user's way (patrol the pets in, keep out
  of the shot) got there without it. "All Patrol" at any idle target that can't see us (the user's 2026-10-22 report of
  the runner wandering the soul cells "not engaging anything"): patrols 340 -> 1,895, pass-overs 4,888 -> 3,338 in
  ~89 h, but tormented souls killed 1,210 -> 1,219, gold an hour 105.9k -> 104.8k and deaths 48 -> 60 (patrols "on
  it" by type: apparition 229, tormented soul 172, blightmare 155, shadowguard 141, nightstalker 128) [INFERENCE: a
  woken apparition, shadowguard or nightstalker comes on to us as well as to the pets]. Not kept; the wandering is
  still open.
- **The simulator, looked at and not changed then** (no live evidence at the time; the shove was measured and fixed
  the same day, docs/NOTES.md "Mobiles don't block walking in UOO" and docs/SIM.md "Shoving and stamina": the original
  runner 1.047 -> 0.835 deaths an hour, main's 0.512 -> 0.267, seeds 1001-1200): it refused every step into a monster's
  tile whatever the stamina (`World._walk`, `blocker.kind == "monster"`), where ServUO lets a player with full stamina
  shove any mobile [INFERENCE: Outlands the same]; the refused shoves at 25/25 are what started the long loot walks and
  the corridor waits. Its blightmare walks after us when we break its sight (docs/SIM.md "Limits"), so the cover may
  be worth more live than here.

## The first house trip: ping-pong at a ledge, the drop-off that couldn't see the tomes (live 2026-10-21 22:17-22:33, hunt-20261021-221736-1743)

Capture session_20261021_164924. Visit 1: 25 kills, 25,504 gp, left by V\* (26,410) at 22:32:24.
- **Ping-pong at the ledge** (user: "Logan is stuck bumping into a wall trying to approach a shade wolf").
  22:27:28-22:28:42, 40 rounds of `patrol to (5177, 681): route 3 steps from (5181, 685)` / `stopped (a target in
  reach)` / `target a shade wolf 0x003EACBE at 7 tiles` / `toward a target: route 1 steps from (5180, 684)` /
  `arrived at (5181, 685)`. Logan on the Maus 3 ledge at z 25; the wolves below at (5176-5180, 691, -3) (0x20/0x77;
  their way up is the ramp west, (5177, 689) at z 2). `on_our_floor` (the walk to beside it at its height within
  the beeline - 1 + `FLOOR_DETOUR`): from (5180, 684) 11 steps against a budget of 10, a target; from (5181, 685) 12
  against 9, not one. The approach walked the beeline to within 6 in x/y, onto the ledge's edge at (5181, 685),
  where the wolf stopped being a target; the patrol's first step back made it one again. It ended when the wolf
  moved and `Team.unseen` passed it over. **Fix:** the approach ends standing on the target's floor (`z_ok`,
  `HuntLoop.floor_z_ok`). `sim/fight2227.py` (the capture's tiles, 2 min, 12 seeds) before / after: 655 / 9
  approaches, 655 / 0 of them ending on the ledge, 15 / 24 kills (both wolves every seed after), no deaths either way.
  Batch, 24 runs seed 17 `--others --eerie --prior` the main store, before / after: deaths 1.42 / 1.00 per h, runs
  with a death 13 / 10, kills 93.2 / 98.4 per h, gold 99.9k / 104.9k per h (noise-level; no cost).
- **The drop-off saw no tomes.** 22:32:36 `recalled home to (3575, 3251)`, then `drop-off: a treasure map tome
  0x62659A52 isn't in view within 2 tiles: stand on the home landing tile; going on with the load`, and at 22:32:40
  the recall back to Maus Near 2 with 25.5k gp; the user paused the gate (22:32:47) and took him home. Logan was on
  the landing tile. The tomes sit in the wooden shelf 0x5656B661 (2 tiles off); the world model drops a container's
  contents with its 0x1D (`WorldState.delete`), so back from the dungeon the shelf is known and its tomes aren't
  until it is opened again. `act dropoff` at 22:17 worked because the shelf had been opened earlier in the session
  (0x3C of its contents at 22:02:25). **Fix:** homes.json steps name their container (`in`); a step whose item is
  unknown is reached through it: the shelf opened, then its contents waited for (`test_dropoff.py` "back from a
  trip": before, exactly the live error). And a drop-off that can't run now ends the run at home (`Abort`,
  task_failed) instead of going back in with the load (docs/PLAN.md "Home moves to Logan Wolf's house").

## Dead on the walk in to a red (live 2026-10-22 18:53:48, hunt-20261022-185159-c0c5)

The first run after a400be6, from the house. Capture session_20261022_163222 (Botan's tiles from his 0x77s and the
0x20s the server also sent for his mobile), the task log.
- **18:52:33** the red Botan (0x0032A620, "Legendary Serial Killer", [Soul Reaper, Syn], notoriety 6) came into view
  at (5610, 677) on the first level, 18 tiles from Logan at about (5592, 688) after a raw flesh fight. He ran south to
  (5606, 701) and out of view at 18:52:42 (at exactly 18 tiles, the view's edge: not a hide, docs below "Red players
  met live"), back at (5593, 711) at 18:53:04, then along y 707 to the
  first teleporter and through it at 18:53:14, 3 s after Logan. "Botan is attacking you!" at 18:53:17 at (5542, 708);
  his hits on Logan were -10 to -26 each, a Greater Explosion potion's countdown among them.
- **The walk in had no hostile-player check.** `look` returned at once while `visit` was None, and the visit starts
  only on Maus 3. So the runner saw "attacked on the way in; the pets guard", drank and vetted in place for 20 s,
  played Area Peace on Botan twice ("none pacified") and retreated 7 tiles "the pets hold it". It died at (5535, 713)
  at 18:53:48. The death juncture said "unknown", attackers [] (no player in view by the ghost's first read).
- **Fix** (user's choice 2026-10-22: "Flee to a gold gate, recall home" on the first level, "Dash on to Maus 3,
  crimson gate" past it): the threat checks run on the walk in (`walking_in`). A red in view or "… is attacking
  you!" ends the walk (`walk_in_threat`). With a gold moongate within 120 route steps, Logan flees to it
  (`walk_in_leave`: flee_out, switching to another gold gate when the way to this one passes them; ropes, potions
  and the vet on the run, no bard skill), recalls home, drops off, waits `--pk-wait` and goes back in. With none,
  he goes on through the rest of the teleporters at flight pace (`walk_in_rush`), and on Maus 3 the visit's first
  act is the gate leave. `died` now calls it "pk" when a hostile player was seen, or attacked us, within 20 s.
- **Simulator** (`harness/sim/fight1853.py`, 12 seeds each). level2: Botan through the first teleporter 3 s after
  Logan, his live tiles. Before 12 of 12 dead, after 0 of 12, all home via the crimson gate. level1: his live tiles
  from (5610, 677). Before 0 of 12 dead: the simulated red doesn't follow through teleporters, so the old runner got
  away to Maus 3. After 0 of 12 dead, all home via a gold gate. test_sim `test_walk_in_red_first_level` (a red
  rushing from 11 tiles: dead before the gate switch, home after) and `test_walk_in_red_second_level`.
- **Limit:** the simulated red doesn't take teleporters. Live, Botan followed Logan through one, so the rush to
  Maus 3 (about 110 steps over three teleporters, then 35 to the crimson gate) can still have a red on it the whole
  way [INFERENCE].
- **The next run (hunt-20261022-191304-536b, after the fix) and a cover ping-pong.** Visit 1: 25 kills, left by V\*
  with 29,711 gp carried; visit 2 left by V\* at 19:44:52 with 29,787; no red. 19:39:36-53: GraveRobber fought a
  corpse purger he had been patrolled onto at
  (5228, 737). `to_pet` walked Logan to (5232, 733) to vet him, 4 from him and in the purger's shot, and `cover`
  walked him out to (5229, 729). That happened four times in 17 s, until the kill: vet_tile ranks a tile's shot second
  and had no unshot tile within vet range of him. Fix: `to_pet` doesn't walk a pet above `--pet-leave-at` into such a
  shot (HUNT_LOOP.md "Out of a shy monster's shot first"). test_sim `test_to_pet_not_into_shot`: the live tiles, the
  old rule walked to (5232, 733) in its shot, the new one stays at (5229, 729). Simulator, 48 runs as live
  (`--others --eerie --prior`, seed 17, 30 min), before / after: deaths/h 0.26 / 0.25 (6 / 6 runs with a death),
  pet deaths 2 / 2 runs, gold/h 109,978 / 109,423, kills/h 100.8 / 99.8.

## Dead in the east wing's dead end (live 2026-10-22 20:01:02, hunt-20261022-191304-536b)

Visit 3 of the run, after visits of 29.7k and 29.8k gp taken home. Capture session_20261022_163222, the task log
(lines ~2584-2640).
- **The ground.** Maus 3's east wing (x 5224-5247, y 655-737) is a cul-de-sac. Its only way out is north up the
  x ≈ 5243 corridor to y 654, then west to the crimson gate: 94 steps from (5244, 685), 156 from the wing's
  south-west end (5224, 737). The 2026-10-21 10:35 and 17:37 deaths were in the same wing.
- **20:00:40.1** the red Druidx came into view at (5243, 667) in that corridor, 18 tiles north of Logan at
  (5244, 685). Viny followed 1.1 s later on his tile, and OIOOOIIO appeared at (5243, 719) at 20:00:55.7.
  "Druidx is attacking you!" came at 20:00:41.8, then "Corp Por" (Energy Bolt) at 42.4 and 56.2. All three ran
  south down the corridor after him, a step every ~0.2 s.
- **The flight** ("away from them first", 2026-10-21) ran south ahead of them. It roped twice (20:00:42 and :49)
  and reached the south-west end at 20:01:01. Its next leg went back toward them ("route 34 steps", toward
  (5245, 713)), and Logan died at 20:01:02 at (5228, 737). The pets were left near an awake corpse purger.
- **Fix** (user 2026-10-22: "Away first only outside dead ends"). When the way to the gate passes them, the flight
  first asks whether running to its flee tile would still leave every way to the gate passing them
  (`HuntLoop.dead_end`: passes_hostile on the zone-bent route from that tile). If so it goes past them at once,
  round them as far as the danger zones bend the route: "… and every way from here does (a dead end): past them at
  once". No rope on such a leg: from them a rope lands only farther off, back into the dead end. Outside a dead end,
  away first as before. test_sim `test_red_flight` and `test_red_out_of_view` now check for the dead end. Their
  old "away first" / "8+ tiles in the first seconds" checks pinned the replaced rule. Both sims still die there:
  their reds rush at once from deep in the wing, as before the change.
- **Simulator** (`harness/sim/fight2001.py`, the reds' live tiles, 24 seeds): before 24 of 24 dead ("away
  first"), after 0 of 24 dead, all through the gate ("dead end: past"). The simulated red's damage model is
  docs/SIM.md "Red players" [INFERENCE: three live reds hit harder than it].
- **Hinder** (the user: "a status that makes us walk slower, not a hold"): see the next section. The captures show
  Hamstring is the slow walk and Hinder is a hold.

## Hinder, Hamstring and the rope (62 Logan captures, read 2026-10-22)

A subagent replayed every Logan capture (`replay.timed_packets` on the raw files, so long packets weren't cut) and
read the store's walk events. Outlands sends buffs as 0xFF sub 8 (add) / 9 (remove), never 0xDF. The server
usually lets a buff expire without a sub 9, so only `ends_t` (state.py `buff_ends_t`) or the end line tells it's over.
- **Hinder** (icon 19, gump 4543, "Unable to Move or Take Actions"; the wiki's Buff Bar: "Full Paralysis") is a
  hold, not a slow walk. On Logan: his own rope's cursor 64 times (1.0 s), filling a soul jar 27 (2.0 s), a vampire
  countess transfix 4 (11 s), a flamekeeper knockback 4 (~1 s, ended by a sub 9 with no text). No player ever
  hindered him. No start text; it ends with "You are no longer hindered." (95 of 99). During it, 542 step
  requests were sent and none moved, nearly all denied with 0x21. Items and speech: refused only in the transfix
  ("You cannot use items while in this state.", "You have been silenced and cannot speak."). During a soul-jar or rope
  Hinder, potions, bandages and pet commands worked.
- **Hamstring** (icon 53, gump 4523, cliloc 1110000 "Can Only Walk and Cannot Regain Stamina") is the slow walk.
  It hit Logan 3 times, all from players ("Their attack hamstrings you!"): This Game SuX 2026-10-17 21:16:51.99,
  Chaos Agent 2026-10-19 19:59:06.81, Botan 2026-10-22 18:53:19.54. It lasts 3.0 s with stamina 0, and steps were
  confirmed every ~0.41 s against 0.21 running. It ends with "You are no longer hamstrung." Items worked. **The rope
  worked during one**: 2026-10-19 19:59:09.22, 2.4 s into it, prompt, answer and landing at (5242, 655) 19:59:10.43.
- **Entangle** (icon 20, "Unable to Move", 115 times, 1-5 s, ends "You are no longer entangled.") and **Paralyze**
  (icon 135, "You cannot move!", self flags 0x21, "You are frozen and cannot move." on steps, broke with damage after
  3.5 s; once, from Ticket Tongje 2026-10-21 10:35:06.66) also refuse every step. Items worked during an Entangle.
  Their denials counted toward `--max-blocked` (`Mover.held` started only on the transfix line) until 2026-10-24:
  `Mover.held(st)` now reads these icons too (docs/HUNT_LOOP.md "Transfixed: walks wait it out").
- **The rope's combat cooldown.** A rope used while a player is attacking us starts a 2-minute cooldown: "You must
  wait another 2m before you may use this again while in combat." (2026-10-19 19:59:10.12, a jump 2.4 s after "Chaos
  Agent is attacking you!"), "… another 1m 50s …" (2026-10-22 20:00:58.9, 9.8 s after a jump made 6.2 s after
  "Druidx is attacking you!"). A jump 0.3 s before the attack line started none. Outside fights with players, jumps
  5.4-6.2 s apart always went.
- **Never tried:** a rope during a hostile Hinder, an Entangle or an active Paralyze. The user (2026-10-22): "I'm sure
  the 'hold' conditions don't let us rope". During a transfix speech is silenced too, and [Rope is speech. So the
  runner tries no rope while one holds (`HuntLoop.held_now`: icons 19/20/135 before their `ends_t`, or the
  transfix).
- **Built** (user 2026-10-22: rope even when attacked while slowed). `rope_worth` allows the rope while hamstrung
  (`HuntLoop.hamstrung`: icon 53 before its `ends_t`, or stamina <= 1 without one) even with a player on us. A rope
  landed within 20 s of a player's "… is attacking you!" sets the next try 120 s out ("…; in combat with a player:
  the next not for 120 s"), and the refusal's time left is read back ("rope: its combat cooldown, N s left").
  The simulator now has this cooldown (`sim/world.py` ROPE_COMBAT_COOLDOWN_S). On it, `sim/fight2001.py` with the
  dead-end rule: 4 of 24 dead without the rope changes (the flight stood for refused ropes), 0 of 24 with them.
  test_sim `test_rope_combat_cooldown`.

## Dead at the east wing's mouth to a red (live 2026-10-22 22:03, hunt-20261022-211657-79a9)

Visit 3 of the run (visits 1 and 2 took 31.5k and 28.6k home). Task log lines 2675-2698, capture
session_20261022_163222.
- 22:02:59 Logan at (5239, 654), the east wing's mouth. The red Flat Lander was in view at (5222, 659), 17 tiles west
  on the way to the gate. The flee tile was back into the wing, so the dead-end rule went "past them at once". He ran
  at a normal running pace (0x77 every ~0.2 s, as Logan's 0.21): (5222, 659) at 22:02:59.75 to (5230, 651) at
  22:03:01.3, then back west to (5226, 651), where he stood ~2 s. He wasn't mounted: players can't ride in a dungeon
  (user 2026-10-22). The gap closed because they ran at each other, Logan west along y 655 toward the gate's way.
  Logan's hits went 92 -> 58 by 22:03:05, then a potion took him to 51.
- **The run ended with Logan alive.** At 22:03:05 the route went up the stair to (5223, 657, z 25) beside him. The
  next plan found "no route from (5223, 657)" even without mobiles: the steps the server refused there are walls
  for `DENY_RETRY_S` [INFERENCE]. leave_by_gate returned the error, and leave_by_recall aborted ("recall home failed
  (moongate: to the moongate: no route …)") at 22:03:06 with Logan at 50/100. No potion or vet after that. No
  `death` juncture was posted: the run was gone when he died.
- **Fixes:** a failed walk toward the gate no longer ends the flight. It looks again after `FLEE_HOLD_S`, up to
  `FLEE_NEAR_FAILS` 6 times ("…; looking again (n of 6)"). A failed leave that finds Logan dead posts the death
  first (`died`, "ghost body (leaving: …)"). Neither would likely have saved him once he was beside the red: the
  rule ran him toward a red standing on the way out, and the rope's first use in a fight with a player buys one jump
  [INFERENCE].
- User (chat 6159, after death #2): the reduced shelf loadout is "the new normal". He cut the vet supplies, bandages
  and potions the shelf gives (the Seer counted ~30 heal potions, 70 vet supplies, 5 Total Refresh after it) so the
  deaths to reds waste less (OVERSEER.md §5 step 2). The user clarified 2026-10-22 that "new normal" meant the
  loadout, not the deaths.
- **Flat Lander's kit** (a replay of the capture to 22:03:05, `replay.timed_packets`): nothing in his hands (layers 1/2
  empty), a hued leather suit (hue 2406), a wizard's hat, a mastery chain (0x0E9A, layer 9). Spells: "Corp Por" at
  +2.70, +4.91, +7.07 and "Por Ort Grav" at +9.49 s after 22:03:00, cast animations every ~2.2 s. Hits on Logan -34,
  -30, -30, -33, plus -4/-1 ones with no words (he said "All Kill" at +2.15: [INFERENCE] a pet's). Magic resist
  potions wouldn't help: the wiki says they "only work against pets and creature spells, but not player spells".
- **Ranged reds** (user: rope from casters and bows even while they hit us): built, docs/HUNT_LOOP.md "From ranged
  reds". `sim/fight2203.py` replays this death: 3 of 24 dead before and after.

## Walled in the east wing (live 2026-10-30 23:43:37, hunt-20261030-230618-058b)

Visit 3 of the run (visit 2 left at 23:22 for a red at 18 tiles and got home). Task log lines 1571-1595, capture
session_20261030_175703 (`sim/red_scan.py 20261030_175703`, then a replay for the ground items and the 1 Hz mobiles).
- **The reds.** Vowxin Retp (in view 23:43:26.36 at (5255, 653), east to (5258, 653) by 26.99, (5261, 653) by 29.30),
  S p a c e B a r and Silent Assassin (into view 29.17 at (5254, 653) and (5251, 653), east to (5257, 653) and
  (5256, 653) by 30.04; Silent Assassin to (5257, 654) at 32.45, (5259, 652) at 34.55). Each held a spellbook (0x0EFA,
  layer 1) and a shield, flags 0x20 (no war bit seen). Words: Vowxin "Vas Ort Flam" (Explosion) 27.09, "Corp Por"
  29.44, 31.62, 33.79; S p a c e B a r "In Sanct Ylem" (Wall of Stone) 29.70, "Vas Ort Flam" 31.80, "Corp Por" 34.36;
  Silent Assassin "In Sanct Ylem" 30.69, "In Por Ylem" 34.65, "Vas Flam" 35.53. All three ranged by the runner's rule
  (spell words within 10 s).
- **Logan** at (5273, 652), pets on him at (5273, 653) (war, then off on "All Follow Me" at 27.0). Hindered 27.72-28.77
  (buff 19). The flight said "a dead end: past them at once"; the rope at 28.57 landed at (5264, 655) 6 tiles from
  Vowxin, "in sight of 1 (ranged)": the ranged-red rule as designed (docs/HUNT_LOOP.md "From ranged reds": along the
  way, the tile fewest see; only Vowxin was in view then). Then west (5263, 655) 29.90 ... (5258, 654) 31.48, the pets
  following to (5259, 654).
- **Damage** (hits 100 -> 0): 31.49 -1 (Vowxin's swing, anim 11), 31.80 -21 (his Energy Bolt) and -30 (an Explosion's
  0x36BD on Logan) to 48; potion +26 to 74 at 32.54; 33.71 -1 (S p a c e B a r's swing); 33.93 -33 (Vowxin's Energy
  Bolt) to 40; 35.75 -1, 35.80 -6 (Silent Assassin's Magic Arrow, bolt 0x36E4 from (5260, 652)), 36.11 -28 (an Energy
  Bolt or the Fireball), 36.22 -7 (an Explosion) to 0. Spells, the swings 1 each.
- **The shadowguard** at (5248, 652) in war mode (the crawl's "awake" zone), walking east to (5253, 653) by 35.2: west
  of the wall.
- **Why "no route".** At 31.19 three "wall of stone" items (0x4D1B, tiledata Wall|Impassable|NoShoot) stood at
  (5254, 652), (5254, 653), (5254, 654): Silent Assassin's Wall of Stone of 30.69. The corridor there (x 5254-5259,
  y 651-655) leaves west only by (5253, 652-653); the wall sealed it. The Mover takes 0x4D1B as a field
  (`FIELD_GRAPHICS`: a hard block) and as an impassable ground item. With it, everything reachable from (5258, 654) is
  the east wing's 287 tiles (5254-5283, 645-661): no route existed at all. The danger zones (the reds' `red:` zones,
  the shadowguard's crawl zone) are costs on the route, never blocks; without the wall the plan from (5258, 654) took
  ~1,000 expansions with or without them. The rope at 35 found "no landing in reach in sight": from ranged reds it
  lands only along the route, and there was none. The "away from them first, toward (5235, 655)" at 23:43:36 came
  after the death had cleared the view (the items gone at 36.23) [INFERENCE: (5235, 655) is behind the wall].
- **The reaction.** The 6 looks (`FLEE_NEAR_FAILS`, made for refused steps that expire) stood Logan 4.7 s on one tile
  1-3 tiles from three casters. A human would duck out of their sight: the alcove south at (5259-5262, 657-661) is 5
  steps off and none of the three saw into it, and wait there for the wall to go (10 s [INFERENCE: RunUO's]).
- **Fix:** no route in a flight never stands us in their sight. `flee_out` names a field that cuts the way
  (`field_cut`: "… no route from (5258, 654): a wall of stone at (5254, 653) cuts the way; looking again till it goes")
  and doesn't count it against the 6; meanwhile the leg goes to `cover_tile`'s tile ("…; out of their sight
  meanwhile, toward (5261, 658) (seen by 3 -> 0)"), the tile in reach fewest of them see, then the fewest steps; from
  melee reds the farthest. The leave's start plans `near` through fields when it's walled already. `sim/fight2343.py`
  replays this (the wall as live, `World.add_wall`): 21 of 24 dead before (and 3 runs ended alive by the flight's
  "no route" abort), 24 of 24 after; standing 1+ s in a red's sight after a "no route" 26.9 s in 11 runs (longest
  3.9 s) before, 1.2 s in 1 run after. Three casters in a sealed dead end kill in the simulator either way.

## 'All Kill' at an unseen reanimated daemon, the patrol/stand-off ping-pong (live 2026-10-23 23:29-23:30, hunt-20261023-220741-f118)

User, in this shift: "The runner keeps trying to 'All Kill' a reanimated demon that is not in line of sight, and
doesn't seem to notice the 'target cannot be seen' system message." Capture session_20261023_201849 (0x6C cursors,
0xC1 clilocs) and the task log:
- A reanimated daemon 0x0055B64F stood at (5240, 700) the whole time (no 0x77 for it), fighting LiquidElsa. The crawl
  sought it ("patrolling toward it ((5243, 698))"). From (5242, 694) three "All Kill"s (23:29:07, :19, :32) were
  answered with no cliloc (seen), but the pets never took it [INFERENCE: no way for them to reach it]. At 23:29:34 it was
  dropped (`PIN_S`, skipped 60 s).
- The patrol went on to the same waypoint, 3 tiles from it. Its bone burst (0xC0 from it to six mobiles, Logan -34 at
  23:29:36 and -41 at 23:29:51) put it among his attackers, which overrides the skip. The stand-off then took him to
  (5241, 693), 7 tiles off and behind a wall. "All Kill" there at 23:29:47 and 23:29:59 got 500237 "Target cannot be
  seen." both times. The runner logged the first and blocked it for `LOS_BLOCK_S` 10 s, then sent it again from the same
  tile. From 23:30:02 to 23:30:19 the patrol (to (5243, 697)) and the stand-off (back to (5241, 693)) alternated five
  times. A blightmare then came up, and Logan died at 23:30:37 (the death has its own section).
- The simulator's map agrees that (5241, 693) doesn't see (5240, 700) while (5242, 694) does. It reproduced both loops:
  `Team.fight` sent 6 refused "All Kill"s in 60 s from (5241, 693), and the scene with the HEAD crawl stood 19 of 80
  half-seconds inside the bone burst's reach with 17 stand-offs in 40 s.
- Fixed: a "cannot be seen" answer blocks "All Kill" at that monster while we and it stay on those tiles
  (`Team.kill_unseen`: 6 -> 1; another tile that sees it sends again). An awake area hitter that is passed over is a
  crawl danger zone of its `AREA_R` while skipped (`Crawl.observe`): 19 -> 0 half-seconds in reach and 17 -> 0
  stand-offs. Tests: test_sim `test_kill_unseen_once`, `test_patrol_off_passed_over_area_hitter`.

## Dead with the pets off guard (live 2026-10-23 23:30:37, hunt-20261023-220741-f118)

Visit 4 of the run, 16 kills and 12,021 gp on him (lost). Capture session_20261023_201849 (0x20 flags, 0x77, 0xA1,
0xC0, 0xAD speech, overhead numbers), the task log (lines ~3920-3996), the Seer's report (the pets at 92 % and 97 %
beside him, no attacker listed at the death). Many players about; none near the blightmare until 23:30:30.

- **The pets weren't guarding.** Guarding pets keep war mode on: "All Guard Me" 23:28:52.4, both in war mode 52.5.
  "All Kill" at the reanimated daemon (previous section) 23:29:07.2: both out of war mode at 08.2; 19.8: war 20.5,
  out 20.6; 32.4: war 33.1, out 33.2 [INFERENCE: the order failed, they couldn't reach it, and they went back to
  following]. The runner dropped the daemon at 23:29:34 and set nothing to guard again (only kills, arrivals and a
  few special cases did); the "All Kill"s of 23:29:46.9 and 59.2 were refused ("cannot be seen"). From 23:29:08 to
  23:30:32.9 the pets were out of war mode, following him.
- **The blightmare.** 0x0055BE2F and the tormented soul 0x0055BE54 first came into view at 23:30:18.0, 13 and 11
  tiles off, mid-ping-pong [INFERENCE: spawned then]. 23:30:19 at (5241, 693) the runner logged "would see us before
  the pets at 8 tiles" and backed off to (5241, 692); 23:30:20 "passing over a blightmare: it can't see us from 7
  tiles" (los.Sight). Its war mode came at 23:30:22.51 with nobody but us within 9 tiles; its first cast effect
  landed on Logan at 25.15, so it woke on him [INFERENCE: it saw him at (5241, 693) 23:30:19-20, or the sight model is
  wrong for (5241, 692)].
- **The fight.** 23:30:23 cover: to (5247, 687), 8 steps. The south tiles out of its reach ((5240, 698), 7 steps)
  lost on `stand_score` to the reanimated daemon in war mode at (5240, 700), 8 % hits, fought by a party (it died
  23:30:30). The walk passed 4 tiles from the blightmare in its sight: -27 and "The creature's spell blights you with
  a foul disease!" at 25.66, at (5243, 689). Out of its sight at (5247, 687) he had it after him at once: (5245,
  686) 26.49, (5246, 687) 26.83, beside him, a cast 26.86. Heal potion 26.71 (+27, 100). Cover 23:30:27 to (5247,
  689); it stepped to 2 tiles. A song (Provocation 29.5), then "target a blightmare at 2 tiles", "All Kill" 30.61:
  5 s after its first spell on him. Pets in war mode 32.94, their first damage on it 33.88 (-205: ~8,000 hits).
  Hits: -21 30.73 (79), -26 and a disease 31.79 (53), area peace failed 34.44, cover 34 to (5245, 686) through its
  tile, "shove into (5247, 687) denied (stamina 23/25)" and a refresh potion at ~36, -24 and a disease 35.42, -21
  35.79 (9), its bolt to seven mobiles 36.52, -10 36.96: dead. The heal potion's 10 s (`healing.POTION_COOLDOWN_S`)
  from 26.71 ended at 36.71, 0.25 s before; he was inside the cover walk then. Its cast animations came every 0.7-2.3
  s throughout.
- **Cause** [INFERENCE: the order of weight]: the pets off guard (a dropped "All Kill" ended their guarding, and the
  runner didn't know); with them guarding the blightmare's first spell on him puts them on it at once (as in the
  simulator; not seen live with a caster). Then the cover walks: past it in its sight and to 2-3 tiles from it, which
  it followed (docs/SIM.md "Limits": the follow seen live), and the 5 s before "All Kill" while care walked and sang.
- **Replayed** (`sim/fight2330.py`, from 23:30:22.5: the live tiles, the blightmare awake on Logan, the daemon at 8 %
  with two other players on it, the runner's state as live: pets following, taken for guarding, the daemon dropped).
  On 17fdc54 the runner logged what it did live: cover to (5247, 687) 8 steps, cover to (5248, 689), "All Kill" at 2
  tiles, cover through its tile and "shove ... denied (stamina 24/25)", "drank for stamina to shove". Seeds 1-96:

  | | deaths | its hits on him | lowest hits median | damage on him, mean | kills |
  |---|---|---|---|---|---|
  | 17fdc54 | 3 | 185 | 46 | 134 | 177 |
  | guard after a dropped "All Kill" | 0 | 102 | 66 | 79 | 188 |

  The simulator's blightmare casts every 3.4 s against live's 0.7-2.3 s, so it kills fewer than live.
- **Tried and not kept** (seeds 1-48; 17fdc54: 3 deaths, lowest hits median 61, damage mean 125):
  - cover never nearer a shooter than here, the way there too (`tiles_within` limited, the route bent round it) and
    clear of area hits' reach (cover kept only the hitters' own tiles out): 7 deaths, median 32, damage 187. From
    (5241, 692) no tile within 8 steps qualified; he stood in its shot 4-6 tiles off while the pets went at it;
  - "All Kill" at a shy monster on us before cover (in care): 26 deaths alone (sent from behind cover: "cannot be
    seen", which then held fight's own "All Kill" by `kill_unseen`); with a sight check 2 deaths but median 35,
    damage 146 (our "All Kill" keeps its aggro on us);
  - with the guard rule: all three, 2 deaths, damage 118; the kill-first (with the sight check) and the guard rule, 0
    deaths, damage 117; the guard rule alone, 0 deaths, damage 72 (shipped).
- **Fixed.** `Team.all_kill` records the order the pets took (`kill_order`; not on "cannot be seen"); `Team.care`
  sets `guard_needed` once that target is skipped (dropped, passed over), out of view or dead (`kill_order_over`,
  logged "'All Kill' target 0x0055B64F dropped: the pets off guard since; 'All Guard Me' due"), and the guard goes
  out in care's order; `guard` and `on_kill` clear it. Live that would have been 23:29:34-35. Test: test_sim
  `test_guard_after_dropped_kill` (no "All Guard Me" while the target is on; once dropped one, the pets guarding;
  fails on 17fdc54: none, the pets left on the kill). Simulator batch (96 runs of 20 min, seed 17, `--others --eerie`,
  `--vet-range 5 --crawl-learn-s 180`), 17fdc54 / after: deaths an hour 0.19 / 0.15 (runs with a death 6 / 5),
  kills an hour 97.5 / 98.8, gold an hour 103,211 / 104,175, banked an hour net 94,889 / 97,313, no pet deaths.
- **Not changed**: cover's tiles near an awake blightmare (the replay rejects the "never nearer" rule; the follow is
  real live, so a cover tile 2-3 tiles off out of its sight buys a second or two); no heal potion inside a cover walk;
  the wake at (5241, 692) the sight model said it couldn't see.

## Entangled beside a door on the walk in: the door walled, the run aborted (live 2026-10-24 00:40, hunt-20261024-000844-30b5)

Visit 2's walk back in. Capture session_20261023_201849 and the task log:
- 00:40:25 the third teleporter put Logan at (5507, 693, -5) on Maus 2, beside a fight of other players (Drekk, two
  summoned ancient mummies, a ghoul). 00:40:29.17 a step NW to (5507, 691) shoved through a mobile (cliloc 1019042).
  "Your feet stick to a strange substance!" came with it, and the 0xFF sub 8 buff icon 20 (Entangle) on him.
- 00:40:29.31 a step N into the door at (5507, 690) was denied (the server's 0x21 still faced NW). The Mover took it for
  the door's denial, and "You are no longer entangled." followed at 30.24. 00:40:30.63 brought the open-door request,
  then the step N at 30.78. The server confirmed it as a turn: no move, its 0x20 put him at (5507, 691) facing N.
  `_try_door` read that as "still denies the step after opening it" and walled the door. "no route from (5507, 691)"
  followed, and the run aborted at 00:40:32 with Logan alive on Maus 2 (task_failed 1842).
- Fixed: `Mover.held(st)` also counts HOLD_ICONS buffs (19/20/135; it was transfix-only), so a step denied while held
  waits the hold out and plans again. A door retry that only turned him steps once more. test_mover
  `test_held_at_door` (before: "too many blocked moves (13 in this walk)"; after: through the door). The
  `HOLD_ICONS` constant moved from loop_hunt to agent_link; `HuntLoop.held_now` is `Mover.held(st)`.
- **Then the second death: left there, idle.** Nobody moved Logan after the abort: no runner, no overseer command.
  He stood at (5507, 691, -8) on Maus 2, between the first level's teleporters and Maus 3. 30 minutes later a calamity
  (0x00566A3B) attacked at 01:10:32. His hits went 100 -> 78 -> 54 -> 29 -> 10 -> 0, and he was dead at 01:10:48. The
  pets died at 01:11:51 and 01:13:02. No `death` juncture: only task_failed 1842 at 00:40:33 says anything. The user:
  "From now on Overseers should escape from the dungeon and return home when there is an erroneous stoppage like
  that." Since then an error inside a no-recall dungeon gets him home before the run exits (docs/HUNT_LOOP.md "An
  error inside the dungeon escapes home"; from (5507, 691): on through 5522,637 and the leave gate, Totem, the
  recall), and the Seer has `--go-home` for a run that ended anyway. test_sim `test_escape_after_walk_in_error`
  replays the abort (before: "ABORTED", Logan still at (5507, 691); after: home with both pets).

## Into the mini-boss room off the tank (live 2026-10-24 12:22:36, hunt-20261024-112241-b911)

Visit 3 on Maus 3, the north end of the north-east hall. Capture session_20261024_102346 and the task log:
- 12:22:35 "keeping off the tank: (5242, 612)". The tile was the door of the mini-boss room (the user's "Cistern
  Gorgon's", NOTES "Red players met live"), not yet known as a teleporter. The step N from (5242, 613) at 12:22:36.6
  put Logan at (5242, 587, 0) inside, the pets with him ("teleported: … remembered"). The patrol had walked to
  (5243, 614) and stood on (5242, 613) before without trouble.
- The walk had the tile as its goal, so the Mover counted the teleport as meant (a walk-in's) and didn't step back.
  "off the tank: no route within 8 steps from (5242, 587)", then more off-the-tank steps N to (5242, 578), toward
  "a custodian" (0x005BA2E2, first in view at (5240, 566) at 12:22:49). The room's guard: no loot (DISCORD_KB
  "ossuary custodian", "boss room mini-boss"). Hits fell to 57 by 12:22:56 (a heal potion: 57 -> 100), the pets to
  93 and 96.
- The user walked him out: S from (5242, 578) to (5242, 586) at 12:22:59-23:00.7, then the step S onto (5242, 587),
  the room's arrival tile (0x21 deny at (5242, 586), 0x77 to (5243, 612) at 12:23:00.9). The run went on, and the
  Seer ended it at the next trip home (`stop --after-trip`, task_done 12:24:46, 59,024 gp over 3 visits, 0 deaths).
- Players vanish next to that door: across the last 60 captures (sim/entry_scan.py), last seen at (5243, 613) 10
  times, (5243, 614) 9, (5243, 612) 7, (5242, 613) 4. They arrive at (5243, 612) 46 times. Which other tiles of the
  door row teleport is unknown [INFERENCE: the doorway is wider than (5242, 612)].
- Fixed: a teleport counts as meant only when the tile was a known teleporter before the step. Else the Mover steps
  back, and with no known way back it steps off the arrival and back on (what the user did). A walk whose goal was
  that tile ends there. Both tiles are seeds in `harness/data/teleporters.json` and in the simulator's scenario.
  test_sim `test_boss_room_door` (before: left in the room, "no route from (5242, 587)"; after: out at (5243, 612)
  with the way out known or not).

## Elder vampires: no more running (2026-10-24, simulator)

User 2026-10-24: remove the runner's running from an elder vampire and an elder vampire countess. Removed `Team.evade`
(and `aggro.View.chasing`, `tamer.EVADE_*`, the "evade" intent and `evades` count): one that comes for us in war mode
is fought like any other monster on us. Kept: their sight is no-go for the patrol and routes, and the runner never
pulls one (`--never-aggro`, aggro.NEVER_AGGRO).

- **A simulator crash found on the way:** since the armor change (ab89414) a transfixed copy needs the transfix's
  `melee_player`, which only the vampire countess' transfix had. The elder vampire countess' copies raised KeyError in
  `World._transfix` (6 of 200 runs crashed in the first baseline). Hers now has the countess' `[44, 64]` (both
  batches below are after that fix).
- **Before and after** (200 runs, seed 1001, 20 careers, `--others --eerie --prior harness/data/harness.db --
  --vet-range 5 --crawl-learn-s 180`, 30 game min each):

  | | before (evade) | after (fight) |
  |---|---|---|
  | game h | 95.5 | 96.8 |
  | deaths / game h | 0.21 (20; Poisson 95 % 0.12-0.30) | 0.19 (18; 0.10-0.27) |
  | runs with a death | 20 | 18 |
  | runs with a pet death | 3 | 2 |
  | runs with a pet left behind | 5 | 2 |
  | gold looted / h | 107,663 | 108,464 |
  | gold banked / h | 105,172 | 106,310 |
  | kills / h | 97.1 | 97.5 |
  | damage on Logan / h | 1,565 | 1,582 |
  | evades | 145 in 54 runs | 0 |
  | the runner's fights with an elder | 0 | 26 (14 kills; median 21.6 s, 26 hits lost) |
  | deaths with an elder's damage in the last 15 s | 0 | 0 |

  The 54 runs that evaded before, the same seeds after: deaths 3 and 3, banked 101.9k and 104.2k an hour, no pet
  death either way. Every difference is within the noise; fighting them costs nothing measurable here.
- [INFERENCE] The simulator's elders are modelled thinly: the elder vampire's feed and the elder countess' transfix
  carry the vampire's and the vampire countess' numbers (scenario notes), from 2-3 live fights each. Live, the
  eleventh run's elder countess froze both pets and killed Logan (20261015_171928 19:19:41).

## GraveRobber left on the first level: a command out of reach (live 2026-10-24 18:26:46-18:27:50, hunt-20261024-173530-0ca1)

Visit 4's walk back in. Capture session_20261024_102346 and the task log:
- 18:26:46 at (5570, 701) on the first level a hit on Logan (-12, hits 88), "attacked on the way in; the pets guard".
  "You are no longer hindered." came at 18:27:29, so he was held for part of it. On "All Guard Me" GraveRobber ran 17
  tiles NE to (5582, 684) after a reanimator apprentice (dead 18:27:12), then fought a corpse eater there, in war
  mode, at 96-100 % hits. AlaFlambe went 7 off and came back.
- Nothing hit Logan after that, so the walk-in's fight ended at 18:26:51 with "All Follow Me". AlaFlambe was beside
  him within 3 s. GraveRobber, at 17, fought on through that call, the next (18:27:07) and the escape's (18:27:28). At
  18:27:27 the walk aborted ("the pets aren't within 3 tiles of us after 20 s; not going on without them").
- The escape (b3d6e53) did as designed: the rope, the gold moongate at (5588, 717), "All Follow Me", 11 s beside it,
  the recall at 18:27:47. GraveRobber had come back into view at 18:27:45, at (5590, 702), 15 off and coming. "PETS
  LEFT BEHIND (home): GraveRobber". The run exited 1 at home: the escape juncture, then task_failed.
- The log's "not waiting (hurt)" on the second call was wrong text. It meant "this call doesn't wait; the caller
  does", and Logan wasn't hurt. It says "not waiting here" now.
- **Command range** [INFERENCE]: 71 "All Follow Me" with a pet 7+ tiles off in the last 25 captures. From 11-13 tiles
  the pet was beside us 3 s later in 9 of 15 cases; from 15-17 tiles, in none of 7. Above ~14 a pet doesn't take the
  command. ServUO's pets take commands within their perception range [INFERENCE: Outlands' own value unknown].
- The simulator had every pet hear every command at any distance, so it couldn't show this. Its leash calls a guarding
  pet off a target more than 14 from us; live GraveRobber fought at 18. The leash isn't changed: one sighting.
- Fixed: on the walk in, a lagging pet in view farther than 12 gets a walk to within 8 of it before the second "All
  Follow Me" (`HuntLoop.fetch_pet`). The simulator's pets hear commands within 14 only. test_sim
  `test_walk_in_fetch_pet` replays the geometry (before: the same abort with GraveRobber at (5582, 684); after:
  through the first teleporter with both pets).
- **Simulator, before and after** (60 runs of 60 min, seed 101, `--others --eerie --prior harness/data/harness.db`;
  before = b96bf10, after = the runner fix and the 14-tile command range): deaths 10 and 10, pet deaths 18 and 17,
  5,560 and 5,566 kills, 112.5k and 113.7k gp banked an hour, over 52.8 and 52.5 hunting hours. No change beyond the
  noise.

## Cover loops at a purger or blightmare on someone else (live 2026-10-24 19:00:30-19:02:55, hunt-20261024-185409-49f0)

User: "Logan keeps getting stuck in a loop 'Out of the <mob>'s shot' when a purger or blightmare is fighting someone
else". Task log, and capture session_20261024_102346:
- 19:01:21-19:02:07, nine rounds in 46 s. Gix of Phyrexia's boreal faerie wyrm and colossal blade fought a corpse
  purger at (5267, 653) in the corridor (its hits 73 -> 56 %). Logan was in the pocket north of it at (5267, 648). Every
  "patrol to (5243, 716)" (84 steps, south past it) went to (5264, 653), 3 from it, then "stopped (GraveRobber is
  fighting)". GraveRobber, guarding (war mode, flags 64 all through), stood at (5264, 652), 2 from Gix's wyrm and blade
  at (5266, 653) and 3 from the purger, his hits at 100. Then
  "cover: the corpse purger shoots us here; to (5267, 648)", and the patrol again. Twice between rounds: "a corpse
  purger … is fighting a boreal faerie wyrm out of our sight: walking to see it", one step, cover.
- 19:02:36-55, five rounds: "toward a target" at tormented souls past an awake blightmare at (5245, 687), into its
  shot, then cover back to (5246, 675).
- Why: an awake shy monster's reach was only a soft danger zone (`Mover.danger_cost`), and the corridor had no way
  round, so the route crossed it. Guarding pets keep war mode on between fights, so `pets_busy` read a pet within 2 of
  another player's fight as fighting. Nothing stopped an advance at the edge of its shot.
- First fix (6b4f926): the patrol chose only waypoints it reached without stepping into an awake shy monster's shot
  and waited out of it when none was left; advances stopped at the shot's edge; no walk to see one on someone else.
  `pets_busy` skips a monster another player's side fights unless a pet of ours swings at it. Simulator, 120 runs of 60
  min, seeds 101 and 201, `--others --eerie --prior harness/data/harness.db`, before = 4ea4fbe: covers 3,247 and 2,785;
  deaths 26 in 103.8 h and 27 in 98.7 h (11 and 8 with a purger or blightmare in the last 15 s); pet deaths 44 and 45;
  110.8k and 113.7k gp banked an hour. The waits out of a shot in seed 101's batch: 77, median 5 s, longest 32 s.
- Then the user: "We can just walk past when one is fighting someone else". One on another player's side and not on us
  or our pets (`Team.shy_threats`) is out of all three rules: no cover, no edge stop, not in the waypoint filter; the
  walk to see it and "All Kill" are back. The waiting and the edge stop stay for every other awake one. test_sim
  `test_walk_past_purger_on_another` (at 6b4f926: a cover and no step south of y 649 in 40 s; now: no cover, on to
  y 737; the pet beside the fight not "fighting"). The same 120 runs against 6b4f926: deaths 27 in 98.7 h and 24 in
  98.2 h (8 and 7 with a purger or blightmare), pet deaths 45 and 48, covers 2,785 and 2,795, 113.7k and 116.4k gp
  banked an hour.

## Red players met live (31 Logan captures 2026-10-13..22, measured 2026-10-22/23)

For the simulator's reds (docs/SIM.md "Red players"). `harness/sim/red_scan.py` replays every capture (`replay.timed_packets`
-> `WorldRuntime`) and extracts each red (notoriety 6, a player: `threats.identify`'s rules) with its packets, Logan's
tile, speech, damage numbers ("-N" overhead: Outlands sends no 0x0B), buffs and 1 Hz snapshots; the live server only
(the 10 test-shard captures out). "Runner era" = since 2026-10-15 21:10. A group = reds appearing within 30 s of each
other; an appearance = a red in view after 30+ s out of it.

- **Exposure and rate.** Logan alive on Maus 3 12.30 h (runner era 11.22 h), on the walk-in levels 2.76 h (2.55; 92
  walk-ins, median 104 s). Maus 3: 24 groups, 39 appearances; runner era 20 groups, **1.78 an hour** (Poisson 95 %
  1.09-2.75). Sizes 1 / 2 / 3 / 4: 14 / 6 / 3 / 1; later members 0-3.8 s after the first, 2 of 15 at +11 and +15 s.
  Walk-ins: 4 groups in 2.55 h (1.57 an hour; 4.3 % of walk-ins). 9 of 24 groups came within 2.6 min of Logan's
  arrival on Maus 3. Repeat names: Chaos Agent 5, This Game SuX 4, Dead End 3; guild tags Syn (4 players), PEC, BJV...
- **Outcomes (runner era, 18 runner groups on Maus 3).** 8 red deaths (44 %; 0.71 an hour, 95 % 0.31-1.40), 9 escapes
  through the gate (first sight to the gate median 9.3 s), 1 reached the gate and died to apparitions. First sight to
  death median 9.5 s (7.8-23.5). **Geometry decided it**: with a red nearer the crimson gate than Logan at first sight
  7 of 12 groups killed him, otherwise 0 of 5; the east wing (x 5224-5247, y 655-737; 27 % of his time) had 7 groups
  and 4 deaths, every one by reds coming down its only way out. Walk-ins: 1 death in 92. The store's death `cause` says
  "pk" for only 2 of its 8 red kills (the others "unknown"/"mob"), and the 22:03 death has no row.
- **Where they come from.** 35 of 39 first sightings at 17-18 tiles (walked into view): the z 25 ramp top
  (5214-5222, 655-666) 8, the west stair 5, the west balcony (5200, 682, 25) 4, the ramp top east (5226, 653) 4, the
  east corridor 6, by the crimson gate 3. **None near the L2->L3 arrival (5199, 737)** or the Maus 4 return. The crimson
  gate is exit-only (wiki), so they walk in. 29 of 35 far first sightings ran straight at Logan (Tracking: 50 tiles in
  a dungeon at GM, wiki). Chaos Agent twice came out of stealth beside him with a hit (2 of 39).
- **Where players come and go on Maus 3** (`harness/sim/entry_scan.py` over 35 live captures, 14.1 h, 1,611 player
  appearances: a player appearing within 14 tiles of Logan after 30+ s unseen, not after his own jump or arrival). 30
  pop-ins not from hiding: 14 on the L2->L3 arrival (5199, 736-737, z 17) and 2 beside it, 6 on (5243, 612, z 0) at the
  north end of the north-east hall, 8 scattered; none clustered on the z 25 walkway, where all 167 first sightings (16
  reds) were at 15-18 tiles and walked in. **(5242-5244, 612-613) is the door of the mini-boss room, the Cistern
  Gorgon's** (user 2026-10-23; its other side about (5199, 842)): 23 appearances and 23 exits within 3 tiles were
  players going into that room and coming back out. **Players reach Maus 3 only from level 1 down** (user), so the
  L2->L3 arrival is the one way in. In their first 15 s players went from the arrival north up x 5205-5209 (19 of 32),
  out of the Gorgon's room south down x 5243 to y 645-654 and west along y 652-653 (13 of 20). Exits within 14 tiles:
  44 at the crimson gate, 4 into the Gorgon's room, 5 at the arrival. Two hue-2825 "moongates" at (5190, 680, 25) and
  (5212, 708) showed only after Logan's deaths [INFERENCE: ghosts' gates].
- **The attack.** 24 of 39 appearances attacked; **24 of 27 that came within 5 tiles**. The attack line came a median
  2.9 s after first sight (p25 1.5, p75 6.2) at a median 4 tiles. On the walk-in levels 1 of 10 appearances attacked
  (Dead End and Mammoth overtook him at 1-3 tiles; Botan stalked at 17-18 tiles for 30 s, then attacked).
- **Kits (Maus 3 appearances):** mages 30 of 39 (a spellbook, a shield or empty hands), melee 7 (Chaos Agent 4 of
  them), archers 2 (Botan: a heavy crossbow and a rapier; Druidx: a bow and a crossbow plus Energy Bolt).
  - Words heard: Energy Bolt 28, Explosion 8, Teleport 6, Telekinesis 5, Magic Arrow 5, Lightning 4, Weaken 3, Greater
    Heal 3, Flamestrike 2, one each of Reveal, Heal, Poison, Mind Blast, Bless, Protection, Magic Reflection. Openers
    (n 21): Energy Bolt 13, Telekinesis 2, Explosion 2, Magic Arrow 2, Weaken 1, Greater Heal 1.
  - Energy Bolt: standing 1.38-2.59 s (median 1.82, n 20), words to its damage 1.39-2.58 s (median 1.88), 22-34 (median
    28, n 21); Explosion words to damage 1.41 and 2.05 s, 33; Flamestrike 2.71-2.75 s, 27-41; Lightning 11-22; one
    caster's words 2.21 s apart (median, n 27). Distance at a damaging spell's words: median 4 (p10 1, p90 7, never
    over 8, n 43); casters keep closing in (1 tile a cast while Logan stands).
  - Telekinesis then "An explosion potion has stuck to you!" and a 5..1 countdown: 33-41 (Malito, This Game SuX,
    KalVasKikUrAss). One Paralyze in 24 attackers (Ticket Tongje, broken by damage after 3.5 s).
  - Melee swings at <= 1 tile hit 23 of 39 (59 %), 10-28 (median 13), 1.34-1.51 s apart (2.0-2.26 with a double axe or
    halberd). Hamstring 3 times ("Their attack hamstrings you!", 3 s), disarm twice (5 s), Deadly Poison once.
  - Gross damage a second on Logan from the attack line: one caster 12-19, two 17-20, four 39, melee 6.3-6.9.
  - 7 of 24 groups had a summon or pet in view (daemons, elementals, a drake...): fireballs and -1..-4 chips.
- **Moving.** 1-tile moves: p10 0.187, median 0.202, p90 0.31 s (n 1,648): running, no stamina walking. 9 jumps of 7-10
  tiles in 39 appearances (Teleport "Rel Por", 1.0 s standing; without words [INFERENCE] a rope). Botan followed Logan
  through a teleporter 3 s behind; **no red ever followed him through the crimson gate** (0 of 13 escapes; at Totem
  the runner recalls 0.2-0.9 s after arriving).
- **Monsters and pets.** A monster in war mode within 2 tiles of a red: 1-3 snapshots in 9 of 24 groups; reds' hit
  bars fell to 50-74 % at worst; one died (Viny on L2, came into view at 9/120). No red was seen hitting a monster; our
  pets never damaged a red and no red hit our pets.
- **PvP numbers on our side.** Heal potions flagged in PvP (within 60 s of a player's attack line or hit) healed 21-29,
  median 24 (n 10, not capped) against 29-53, median 34 (n 115) otherwise; vet supplies on Logan 15-22, median 18 (n 8)
  against 5-39, median 29 (n 129).
- **After a death.** The corpse's contents never reach the client, so looting isn't observable; reds stayed 20 s to
  5 min in view after (NewbieMurderer, Druidx, Flat Lander). Resurrection a median 63 s after the death (30-328 s);
  the next run a median 21 min later (n 8; the human response). Carried loot at a red death: mean 13.3k gp (n 8).
- **How long they stay.** The same red seen again in one capture 0.8-17.3 min apart (n 9); of Logan's next visits
  within 16 min 3 of 8 met the same red, of those 18-78 min later 0 of 7 [INFERENCE: they stay 15-20 min].
- Corrections: Botan's 18:52:41 disappearance (10-22) was at the view's edge, not a hide; the 10-18 21:06 death at the
  gate was to apparitions after a red-triggered leave; the 10-17 16:55 death was the user playing by hand.

## Red players: the game rules (research 2026-10-22, the knowledge base, wiki and patch notes)

`[k N]` = the store's `knowledge` row N (`./ctl.cmd know search`); RunUO/ServUO only where Outlands is silent.

- **Getting in.** Magic travel into a dungeon lands only within 8 tiles of a golden moongate (wiki Magery [k 11588],
  [k 17041]); reds recall in there too ([k 7826]). Crimson gates (levels 2-3) are exit-only "escape hatches" with no
  recall near them ([k 11711], [k 13694-13697], forum [k 16839]); from Shelter/Arena they default to Corpse Creek for
  reds. No mounts in a dungeon (wiki Mounts [k 11388]).
- **Heat of Battle** (wiki [k 13531-13532]; patch 2020-09-29 [k 8074-8075]): our own aggressive act on a player (not
  the server's auto-defence swings) blocks every moongate, recall, gate and hike: 2 min for a criminal act (a red
  attacking a blue), 30 s for a non-criminal one (us attacking a red), (1 + attackers) min after a murder, at most
  5 min. So a red that attacked us can't follow through the crimson gate; "All Kill" at a red would lock our gate 30 s.
- **The PvP flag** ([k 7678], [k 7333]): set on both sides by a harmful spell or damage between players, 60 s for its
  PvM restrictions. While flagged a rope or Teleport starts a 2-minute cooldown on both (wiki Adventurer's Rope
  [k 13326]) and pets don't follow a rope or Teleport ([k 5163]); a heal potion heals 15-25 (patch 2025-06-06
  [k 4909]). Razor's PvP script restrictions apply only in structured PvP, not to PKing ([k 14233-14234]).
- **Magery** (wiki [k 11580-11615]): cast times by circle (Energy Bolt and Explosion 1.75 s, Flamestrike 2.0, Lightning
  1.25, Paralyze 1.5), 0.2 s recovery, no Faster Casting found; the caster stands during the cast and may move while
  holding the target (precast; RunUO, Discord); range (12) and sight are checked at targeting (ServUO Target.Invoke).
  PvP damage at GM: Energy Bolt / Explosion 31.5-40.5 before our Resist. Mana regen ~1 a second (wiki Meditation,
  forum [k 16898]). Paralyze 10 s at GM, broken by damage, our GM Resist resists 15 % ([k 13568]). Magic Resist
  potions don't work against player spells ([k 13493]). Hostile spells and hits break our casts
  (docs/research/SPELL_INTERRUPTS.md).
- **Hamstring** (wiki [k 14382-14390]): melee or wrestling at 80+, never a bow; on a hit while toggled: 0 stamina for
  3 s (a walk only), the target immune 30 s, the attacker's cooldown 30-53 s.
- **Archery** (wiki [k 13328]): bow range 10, crossbows 8; 0.5-1.0 s standing before a shot in PvP.
- **Tracking** (wiki [k 12857-12865]): range 20 + 80 x skill/100, halved in a dungeon (50 tiles at GM); success =
  skill; 5 s between checks; against a player the arrow is where he was at the check. Stealth: 15 steps a use at GM,
  10 s skill delay, running reveals ([k 14160]). Forum advice ([k 16888]): avoid dead ends, loop around approaching PKs.
- **Death.** Everything not Blessed is lootable; no stat or skill loss; bonded pets become ghosts ([k 1205], [k 11547]).
  A red who dies gets at least 15 min of region restriction in that dungeon ([k 16217], [k 5725]).
- **Unsettled:** whether pets on Guard engage a player attacking their owner (no capture shows it); the explosion
  potion's throw range; the Hamstring chance per toggled hit; the 2020 patch's 2 h dungeon lockout for a killed red.

## Mining: what the repo and the store know (research 2026-10-19, no capture yet)

Read for a mining runner modelled on `loop_lumber.py`. **Nothing about mining has been observed**: the store has 0
mining episodes, job events, ore clilocs or ore items, and no session contains a dig (the "hand mining" of
20261003_150103 is captcha data mining, `captcha_mine.py`). `harness/loop_mine.py` is the M0 demo-timeline tool,
not a miner. Everything below is wiki/community/client-file evidence until a demo capture confirms it.
- **No cursor (wiki).** Smart Harvest page (knowledge k13044): "Mining no longer requires targeting yourself. A
  double click on the pickaxe triggers Smart Harvest"; Mining page k12626 the same. The older Discord fact k157
  (dclick + target self) predates it. Jaseowns' "Ultimate Mining Script" (fetched 2026-10-19, last change 2024-04-19)
  agrees: equip the pickaxe if the right hand is empty (`dclicktype 'pickaxe'`), then only `hotkey 'Use item in
  hand'`, no Target Self, polling sysmsgs 25 × 250 ms after each use.
- **Its message substrings:** "You dig" (success), "You loosen" (fail), "You do not see any" / "any harvestable"
  (nothing left in reach: "Move to next spot"), "harvesting is not allowed", "travel" (60 s lockout), "You must
  wait", "world is saving" / "save complete", "Captcha successful". Stock Cliloc.enu ids: 1007072-1007080 "You dig
  some <iron..valorite> ore and put it in your backpack.", 503044 generic dig, 503043 / 501869 "You loosen some
  rocks but fail to find any useable ore.", 503040 / 501866 "There is no metal here to mine.", 501862 / 503036
  "You can't mine there.", 501864 "You can't mine while riding or flying." (Outlands allows mounted mining, k1713),
  501988 smelt success. Outlands sent the lumber success as plain text, not a cliloc, so match both.
- **Its weight/smelt logic:** stop below 30 stones free (`diffweight <= 30`); ore (graphic 6585 = 0x19B9) moved in
  30s onto a friendly pack horse/llama (body 291/292) until its label reads ~[1960+/2000; smelt by double-clicking
  each ore stack within 2 tiles of a forge (`findtype "forge"` or 0x0FB1), on the pack animal too; with no forge
  in reach the dclick answers "You do not see any nearby forges"; ingots (7154 = 0x1BF2) into a hue-38 pouch.
- **Tiledata (artdata.uoo):** pickaxe 0x0E85/0x0E86 (layer 1, one-handed, wearable), shovel 0x0F39/0x0F3A (treasure
  only, k12515), iron ore 0x19B7-0x19BA (hue = colour), iron ingot 0x1BF2, forge 0x0FB1 / 0x19A9. Land tiles named
  "rock" (279 ids), "cave" (61), "flat mountain" (3) are the candidate mineable terrain; Outlands keeps its own
  valid land/static list (patch 2019-02-25, k9636), so mineability has to be learned like `not_tree`.
- **Mechanics (wiki):** ore weighs 2 stones (k13575; 8 before 2022-06-07, k6712), ingots 0.025: carried as ore,
  weight ends a trip after ~310 ore for Outland Dan (weight_max 650 with his Camping, user 2026-10-19; ~28 st of gear);
  smelted on the way (natural forges below) it stops
  binding, as for logs. One ore smelts to 2 ingots and needs the skill to mine that colour (k12631); whether
  smelting can fail is disputed (wiki k13579: chance by Mining skill; community k198: never fails). Colour table
  k12628/k12629 (min skill, `(skill − offset)/0.8 × (1 + tool bonus)`, base node chances iron 81.75 % … avarite
  0.25 %); a coloured node falls back to iron on 50 % of swings and below its skill always. Pickaxe bonus/uses
  k12630 (iron 1000 uses).
  Nodes respawn in 30-60 min with a random colour (k2078); a node gives one colour per round (forum f542). 60 s
  lockout and Stationary Penalty as for lumber (k12626/7); captcha the same gump (NOTES "CAPTCHA facts"). No mining
  in town regions except Shelter while Young. Forges: towns, player houses, caves (k12625), Nusero (k986).
- **Nodes are 8×8 map blocks (community, single sources; RunUO's mining bank is 8×8 too [INFERENCE]):** Discord KB
  "8x8 grid on world map" (zoomed world map draws the gridlines), "mining node grid inside caves" (within 2 tiles of
  a grid intersection you reach all 4 surrounding nodes), "along mountainsides … inconsistent … touching the
  mountain", "ore per mining node" 7-11, "harvest node depletion time" 10-20 s, "mining node spacing" 6-10 tiles;
  trees are exempt from the 8×8 rule. The map file is stored in the same 8×8 blocks.
- **Node geometry from the map files: `python harness/ore_nodes.py scan` (whole facet, ~4 s, writes the gitignored
  `harness/data/ore_nodes.json`) and `ore_nodes.py near X Y [--radius R]`** (one place: families in reach, best
  stands, single-family stands for test digs, forge sites, nearest rune). Rules in its docstring: mineable = land
  named rock / cave / flat mountain and "cave floor" statics (`--land` / `--statics` change the families); reach =
  Chebyshev 2 from a walkable tile (approximation: passable land or a SURFACE static, no IMPASSABLE static, z
  ignored); outside `lumber_opt.TOWN_RADIUS` of the atlas towns and `lumber_forest.GUARD_KEEP` of the learned guard
  points. It reads the map in 128-block-row bands through `UoMap.land_rows` / `statics_rows` (vectorized, checked
  against `land()` / `statics()` in test_uomap.py). map0 overworld (x < 4600: ocean from 4500 to 4700, dungeon
  levels east of it), 2026-10-19: 15,992 node blocks; stands reaching 1/2/3/4 nodes 138,942 / 73,563 / 4,184 /
  12,377; dungeon side 22,354 more blocks. Hits: Granite Quarry (3406,979) 21 blocks within 24 tiles and 114
  four-node stands; Mountain Cave Tunnel (3315,630) 37 / 208; Prevalia Quarry 34; Snow Cave 28; Old Papua Cave
  through its cave-floor statics. Misses show Outlands' own tile list: Tarsands Mine is "sandstone rock" / "ash"
  land, Mt. Petram's caves "obsidian". Not in client data: the valid id list, region rules (towns, no-harvest areas,
  dungeons), and node state (ore left, colour, respawn), which only digging reveals.
- **Natural forges sit in the mining terrain.** 129 small-forge statics (0x0FB1) on map0, 96 in the overworld, 7 of
  those within a town radius; many stand on "rock" land: Andaria Rolling Hills (2482,677) (2551,665) …, Prevalia
  Quarry (1193,1066) (1227,1142) (1254,1148) (1259,1116), Sandshear Quarry (3731,2492), Vine Covered Cave
  (826,1068,z-10), Desert Quarry (1347,2809,z-50). 78 have ≥ 10 node blocks within 40 tiles. The script groups all
  forge statics (0x0FB1, 0x197A-0x19A9) into 193 sites, 17 in towns; 3,116 of the 15,992 overworld node blocks lie
  within 40 tiles of a non-town site. Jaseowns' script checks for these ("natural forges").
  The store also saw 808 distinct dynamic 0x0FB1 forges (player houses/vendors; `item_seen` carries no position).
- **Live hand-mining capture (2026-10-19, Outland Dan, session 20261019_200350, `loop_mine.py timeline`).** Before it
  the user trained Mining at Ace the miner in Prevalia (context menu "Train Mining", 500 gp dropped on him, cliloc
  501540). At Prevalia Quarry, from 4:41 to 14:12 into the session:
  - **Dig = a stock double-click on the pickaxe, no cursor.** The capture has 0 target cursors. Double-clicking the
    pickaxe in the pack equips it (layer 1). Double-clicking it in the hand digs, and the result comes ~1.0 s
    later. Pressing again sooner gives cliloc 500119 "You must wait to perform another action." (20×). As with
    lumber, each dig closes the last decoy captcha gump (random id, no buttons) and opens a new one.
  - **Results:** 141 × cliloc 1007072 "You dig some iron ore and put it in your backpack.", 1 ore each, 48 of them
    preceded by the plain text "Harvest double yield/loot triggered." (2 ore). 86 × cliloc 503043 "You loosen some
    rocks but fail…", 37.9 % fails for Dan's skill. Each success is followed by "(Harvest Aspect N/25000 xp)" from
    self. Nothing in reach: plain text "You do not see any harvestable resources nearby." (44×). 503040 "no metal"
    never came. No colored ore. Total: 189 iron ore.
  - **Gates:** the first dig on the landing raised the real captcha at once (gump 1, answered by hand, "Captcha
    successful."). Then the server said "A stationary penalty is in effect (must move 5 tiles to remove)."; there was
    no "harvest" lockout cliloc for it. After 5 steps a dig 52 s after the recall said "You have recently traveled
    and must wait 7 seconds before you may begin harvesting." A world save ("The world is saving, please wait." …
    "World save complete") passed with nothing else.
  - **ore_nodes.py predictions held.** The single-family stand (1150,1065) (only "cave floor" statics in reach) gave
    8 successes / 12 ore, so **cave-floor statics are minable**. The rock-only stand (1176,1117) (2 nodes) gave
    27 ore. The 4-node stand (1192,1094) gave 44. That is ~11-13 ore per node, matching the community's 7-11 /
    10-15. Other stands: 9-20 successes each. Stands the user tried after others had drained the blocks answered
    "do not see" at once.
  - **Smelting:** at (1193,1065), within 2 tiles of the natural forge (1193,1066), a stock double-click on an ore
    stack (10, then 179) answered cliloc 501988 at once. There was no cursor and no fail, and 189 ore became 378 iron
    ingots (0x1BF2) in one stack. Map forge statics work.
- **Pack animals (store facts, PackAnimal scout 2026-10-19):** Stablemaster prices (k103, `buy_list` sessions
  40/41): pack horse 500, pack llama 500, Pack Ox 2000 gp; capacity 2000 / 4000 st (k5144) and **125 items** (live
  labels "[N/CAP stones] [N/125 items]", type 0 hue 946, answered to the client's own 0x09; one ox hit 125/125 at
  2217 st). Pets recall/gate/teleport with their owner (k11547) but pack horses/llamas never into or out of a
  dungeon (k8564, k16221); they enter the rental room with you (k13865/k13870); can't be bonded (k3111); thieves
  can steal from them (k14152, k16091; ~2.5 ore per steal at 100 skill by k16579 [INFERENCE]); their corpse keeps
  the pack for 5 min (k8777). Community: "bank often instead of bringing a pack horse" (Discord KB "mining pack
  horse advice"), "cannot run with the player when fleeing". None of our characters has owned one. Missing in the
  harness: no flow drops onto a mobile (`ctl _act_drop` refuses non-item containers), labels aren't parsed,
  `threats.passive_names` lacks "a pack ox", the thief guard only watches 2 tiles around us, ledger/pouch watch only
  our pack, `tamer.Team` treats any roster member's death as "pet down".
- **Store schema gap (closed by v7, 2026-10-19):** `harvest_nodes` / `harvest_attempts` have no skill column, and
  `lumber_opt.logs_per_success`, `store_inputs` and `grove_view` read every row, so mining rows would bias the lumber
  planner. The mining runner writes its own tables instead (`mine_attempts`, `mine_blocks`, `mining_spots`).
  Episodes and job events are keyed by a free-text loop/job name (`mining`) and need nothing.
- **Runes near rock:** Witcher 206 Granite Quarry (3406,979), 317 Sandshear Quarry (3784,2484), 325 Tarsands Mine
  (3970,2584; Tarsands is dangerous, k246); DTF tomes add Prevalia Quarry (1178,1119), Mt. Petram caves. A "quarry
  miner" NPC emotes "*digs*" near Witcher 254 (k4090).
- **The mining runner (built offline 2026-10-19; docs/PLAN.md "Mining runner"; first live runs on Outland Dan the same evening).**
  - Run it from the plan: `./ctl.cmd --char "Outland Dan" mining plan` → `ctl run mining --spot forge_1227_1142
    --trips 1 --field-min 60 --timeout 6300` (the plan's `command`). By hand for a short test:
    `./ctl.cmd --char "Outland Dan" run mining --spot forge_1227_1142 --trips 1 --hops 0 --field-min 10`.
  - Seed spots: `python harness/ore_nodes.py spots` (~4 s) writes 51 spots to `harness/data/mining_spots.json`
    (committed), every one active since 2026-10-19 (user: "All 49" candidates on, the planner explores them; before,
    only the two Prevalia Quarry forges were). Two have no landing with a walking route in (forge_431_1977,
    forge_789_1809: `why_not` "no landing"). The capture's forge is forge_1193_1066 (63 node blocks; nearest rune
    "12 - Urk Fort/Smugglers Den"). (1254,1148) lies within 40 of the richer Shrine:Honor site (1279,1149).
  - **No danger words (user 2026-10-19: "The spot I recalled into was safe").** Until then `places.danger_hint`
    left out every rune or forge site whose name held a monster-place word (orc, brigan, smuggler, crypt, …): the
    capture's own forge was dropped for "Smugglers Den" (the seed file had 44 spots, 51 without the filter), and
    `lumber discover` and the landings skipped such runes too. Removed everywhere; only the DTF guild's "Bad
    Places" tomes (dungeons and their entrances, named so by the guild) still keep their runes out of the landings.
  - `ctl mining plan` without the proxy needs `--char NAME` for the home (no mining trip row names the character
    yet). With no mining trips the score is 585 ore/h: the 1300 prior less the pooled hazards' priors
    (0.5 per field hour each).
  - Dig loop: `dig_use` 0.2 s after the reply, reply wait `--attempt-timeout` 5 s, at most 120 digs a stand. A stand
    ends at "You do not see any harvestable resources nearby." (its 8x8 blocks depleted for `--regrow-min` 60), at
    a weight smelt (fewer than `--smelt-free` 40 stones free), or at the field time. The server's stationary text
    without the buff in view walks 6 steps out and back (`Stationary.walk_off`); more than 3 at one stand abort.
  - Smelting: walk within 2 of the spot's `forge`, a double-click per ore stack 0.65 s apart (`smelt_use`), then the
    ingots into the trapped pouch (`stash`). A `smelt` job event per visit (`why` weight / leg end / leaving, `ok`,
    `ore`, `ingots`, `s`). **Only near full weight or before leaving the spot** (user 2026-10-19: "just before we get
    full on weight or leave the spot"):
    - weight: fewer than `--smelt-free` 40 stones free;
    - leg end: dry, field time up, a break or `stop --after-trip`;
    - leaving: a plain abort at the spot, before its recall home.
  - A threat's recall goes home at once with the ore raw. In the first live run Dan dug ~225 ore over 5 stands at
    forge_1193_1066 with no smelt due. A tracked red (AIice Cooper, 77 tiles) then sent him home with 263 raw ore
    (weight 563/650; mining-20261019-215055-990f). That ore is smelted with the next leg's, at weight or on leaving.
    (The user rejected a batch smelt between stands once 40 ore was carried, built in 2a255fe: too often.)
  - A stack the forge refuses or doesn't answer is left for the rest of the leg (`unsmeltable`), not retried in a
    loop. A travel-lockout answer to a smelt is waited out and the stack tried once more [INFERENCE: whether the
    lockout holds smelting is unseen].
  - **At home the ingots go into the room's Resource Stockpile** (since 2026-10-19 evening; before, they stayed
    carried and the room shelf's Restock was skipped because it takes live trapped pouches).
    - The user's drop-off by hand at 22:44:39 (capture session_20261019_214740):
      - a single click on the stockpile 0x62645C82, then its context menu entry index 0, "Restock" (cliloc 3006349);
      - the cursor (type 0) answered with the backpack 0x46F90508 (graphic 0x0E75, x y z 0);
      - 46 ms later the server deleted the two ingot stacks (1080 + 10 of hue 2419) out of the live trapped pouch
        0x4D1FF262 and said "You add …" (the capture keeps 64 bytes of a packet).
    - The pouch stayed armed (Dan's overseer set it off by hand afterwards). The 67 raw ore loose in the pack and the
      reagents in their bag stayed (the stockpile's Settings).
    - `MineLoop.store` → `ingots_to_stockpile` does the same with `dropoff.run` and one step (stockpile.RESTOCK_ENTRY,
      target pack). It declares the whole pack to the ledger, then books `stored`/`stockpiled` (the ingots that left
      the pack) in the leg row. Then the shelf's Restock and Resupply run as for lumber, only when no ingot is left
      in the pack. test_loop_mining mine_main / mine_hop check it.
    - Lumber still sets its pouch off at home: the hatchet's conversion needs the logs out of it (user: "lumber does
      have to pop the pouch in order to turn the logs into boards, whereas the ingots go straight into the stockpile").
  - Live checks still open: whether a packed pickaxe's double-click equips it as in the capture (else equip with
    `combat.equip_packets`), whether Prevalia's map forges smelt for the runner, and the mounted-mining answer
    (cliloc 501864 aborts the run).
  - **"a quarry miner" is a decorative NPC, not a threat** (user 2026-10-19). In the first live run
    (mining-20261019-215055-990f, forge_1193_1066, 21:54:06) Dan walked away from one ("ESCAPE 1/8: monster a quarry
    miner at 8 tiles"). Its zone then bent the next walk into a 118-step detour. The NPC was 0x001E54C0: grey
    (notoriety 3), human body 400, no player flag. Body 400 is in the learned aggressive bodies, taught by 14
    `monster_seen` sightings of brigands and a rebel soldier on it (15 more on body 401), so every grey human-bodied
    NPC with a creature label counted as aggressive.
  - Fix: "a quarry miner" joined `threats.Params.passive_names`, and a passive name now beats a learned aggressive
    body (`threats._aggressive`: bodies are shared, labels aren't). `travel_guard.remembered_tiles` skips
    passive-name sightings, as `learned_params` already did, so routes no longer bend round the quarry for 30 days.
    The tracked-red recall in the same run (Piers Beardkeep at 67 tiles) was the 100-tile tracking rule working as
    meant.
  - **Two spots disabled after the first runs with all spots on** (user 2026-10-19):
    - forge_789_1809: its landing, rune "69", put Dan inside Orc Fort 3. Orcs at 7 tiles at once, recalled 0.76 s after
      sight (mining-20261019-222352-b8b5).
    - forge_1696_1438, the "Paladin" forge: it sits inside Prevalia, 92 tiles from the town's atlas marker
      (1604,1524), beyond `lumber_opt.TOWN_RADIUS` 50, so the town filter missed it. Its 434 map "stands" were along
      the city walls. Every one gave "no route" at ~3 s of planning each (mining-20261019-222522-86e8, stopped by
      hand). The two "Prevalia Sewers" forges (75 and 80 tiles from the marker) may be the same.
  - Since then the runner disables a spot by itself after `UNWORKABLE_STANDS` (8) stands in a row with no route or
    "Harvesting is not allowed in this area." It writes a `disabled` mining_spots row ("N ore stands in a row
    unreachable or not minable (runner, date)") and an `unworkable` job event. The leg then ends early (episode
    `unworkable`) and may hop on (test_loop_mining.py mine_unworkable).
  - **Caves under mountains: the map's stands ignore z** (live 2026-10-19, forge_2599_620, mining-20261019-222839-f574).
    - The area is a cave of 'cave floor' statics at z 0 (3314 of them) under 'snow' land at z 65, the mountain's
      surface. `ore_nodes`' 2D walkable test takes passable land as ground, so the snow over the rock counted as stands
      within reach of the cave floor below.
    - A flood with `pathfind.Walk`'s rules from the landing (2598,531,z 5) reaches 1710 of the 2357 stands, all of
      them at walker z -10..19, in 0.4 s. The 647 others lie on the surface, which is a separate walk component.
    - 6 of the run's 7 "no route" stands were among them (land z 65, no static). Each cost a full 30000-expansion
      A*, ~3 s. The seventh, (2625,582), is reachable at z 0; danger zones blocked the replan.
    - Fix: `MineLoop.walk_steps` floods `localmap.steps` (BFS over the walk rules, z-aware, a diagonal one step;
      `localmap.reachable` shares its `_flood`) from where we stand. The flood stays within `STAND_MARGIN` (40) of
      the box around the area and us, takes ~0.4 s here, and is cached while we stand on the same tile.
      - `candidate_stands` keeps only the stands it reaches and logs "N of M map stands … can't be walked to from here".
      - On the Prevalia walls spot (forge_1696_1438, from the Paladin landing at z 60) the same filter drops 299 of 434
        stands, including every logged "no route" one.
    - `next_stand` ranks by walking steps, not Chebyshev tiles (Chebyshev only without a map), and skips stands the
      walk from here doesn't reach.
      - Before: the first stand from the landing (2598,531) was 51 tiles away and 137 steps round the cave.
      - Now: (2582,582), 64 steps.
      - Mobiles, danger zones and learned denies aren't in the flood; the Mover's A* still goes round them.
  - **Escape goals in rock, Dan stood under arrows** (live 2026-10-20 10:35, Desert Outpost forge_1494_2559,
    mining-20261020-103013-9d9c; recall landing "Desert Outpost" from the DTF library).
    - What happened: a nomadic horse archer (0x0029864D, body 400, ranged) came within 8 tiles of Dan at (1502,2586),
      shooting from (1500,2578). `escape_tiles` gave three straight-away goals, (1505,2598), (1495,2598) and
      (1520,2597), all unstandable. Each walk failed "no route within 24 steps", and the run before the recall took 0
      steps. Six recalls in a row (book charge and spell alternating, 1.2–1.8 s into the cast) were disturbed by
      15–24 hits each, 100 → 19 hits, with two heal potions drunk. The user then ran Dan to (1484,2596), 18 steps
      west-southwest, and the next recall landed.
    - Also there: a nomadic rifleman (notoriety 3) and a fire elemental.
    - Fix: escape goals come from the z-aware walk flood (docs/LUMBER_LOOP.md "Escape goals are walkable tiles on the
      map"). Replayed: (1487,2598) in 15 steps.
  - **The first drop-off by the runner** (mining-20261020-105606-baf5, 11:47:42).
    - The trip had hopped forge_1396_1206 → forge_2599_620 → forge_2497_708 → forge_1279_1149, with 7 smelts on
      the way (6 at weight, 1 at a leg's end).
    - Result: "added 2554 ingots to the resource stockpile (Restock with the backpack: You add 3 item(s) to the
      Resource Stockpile.)". The room shelf's Restock and Resupply then ran.
  - **A false unworkable disable** (same run, 11:47:26, forge_1279_1149, Shrine of Honor caves).
    - Dan stood at (1297,1129) in a cave passage that the map walks only east and west. A zombie (0x002CBE7D) stood
      at (1281,1127), among the next stands.
    - Every walk west searched its zone for ~3 s (30000 expansions at 30× tile cost) and gave "no route": 8
      stands in a row, then the forge. The map says 16 steps.
    - The spot was disabled. I re-activated it the same hour.
    - Fixes in `loop_mining`:
      - `next_stand` leaves out stands inside the aggro zone of a creature in view (the Mover's ("seen", serial)
        zones), as it already did for escaped ones.
      - A walk to a stand on the map is bounded to `STAND_ROUTE_MIN + STAND_DETOUR` × its walking steps, so a
        detour round a zone fails at once.
      - On the map, a stand with no route no longer counts toward `UNWORKABLE_STANDS`. The map says the stand is
        walkable, so what cut the way is passing. Only "Harvesting is not allowed" still counts there.
  - **A smelt counted 0 ingots** (same run, 11:42:13): "smelted 109 ore into 0 ingots", then "stashed 218". The
    ingots came after the forge's answer (cliloc 501988), and the count was taken at once. `smelt` now waits up
    to `INGOT_WAIT_S` (2 s) for 2 ingots an ore before counting.
  - **A world save aborted a hop** (mining-20261020-114825-eee6, 11:58:04–07, at the DTF rune library).
    - "The world is saving, please wait." came at 11:58:04.48. The tome's double-clicks went out at 05.08 and 05.71.
      The server answered nothing until "World save complete" at 07.53. The tome's gump came at 07.61, with
      500119 for the second click.
    - `escape._open` had given up at its 2 s gump wait ("the book's gump 0x09F5976B didn't open"). The run
      aborted outside the room with 586 ingots carried.
    - Since then `agent_link.Link` tracks the save (`save_t0`, from the start and end lines), and `Deadline`
      keeps a wait's time from running during a save (at most `WORLD_SAVE_MAX_S` 15 s). `Link.wait` and the
      book-gump wait in `escape._open` use it, and no re-click goes out during a save (test_escape
      `test_open_through_world_save`).
    - **The drop-off had the same gap** (hunt-20261029-160655-f26a, 16:57:47, Logan at home, capture
      20261029_100942). The shelf 0x5656B661's double-click went out at t=…064.10, and "The world is saving, please
      wait." came 0.04 s later, at 064.14. "World save complete" came at 067.66 and the shelf's 0x24 at 068.28. That was
      4.2 s after the click, past dropoff's 3 s wait. The run aborted at home with 41,231 gp carried: "the container
      0x5656B661 holding drop-off items didn't open: ['The world is saving, please wait.']". Since then
      `dropoff._Flow.wait`, the shelf-contents wait and the chest-move wait use `Deadline`, so a save no longer uses
      up the wait (test_dropoff "a world save as the shelf opens": failed before with the live message).
  - **When ingots are stored** (user 2026-10-20, "the same way we store boards"; docs/PLAN.md "Store the ingots
    the way lumber stores its boards").
    - At a trip's end, an abort's way home or `stop --after-trip`: always, in the room.
    - Between spots (a leg cut short, home at the landing): only when the load is too risky for the next leg.
      `mining_opt.hop_choice` compares the load × P(death) with `STORE_FIRST_S` at the spot's ore rate. The `hop`
      job event has `risk` {carried_ore, p_death, risk_ore, store_cost_ore}, and the log says "storing the N
      ingots first is worth more …" or "hop … (risks X ore; storing would cost Y)" ("the room would cost Y" before 2026-10-21). `STORE_FIRST_S`
      went 60 → 40 s on 2026-10-21: home is a recall and a few steps, no room in between.
    - Before this (2026-10-19 evening to 2026-10-20 noon) every hop carried the ingots on, e.g. 1242 and 2554
      ingots in mining-20261020-105606-baf5.
  - **Pickaxes wear out in the hand** (capture session_20261020_102748).
    - The server re-sends the worn pickaxe's 0x2E equip after its digs, then deletes it (0x1D) while worn.
    - 0x4C982C26: 532 re-sends from 10:30:46, gone at 11:20:45.
    - 0x4D057279: 1005 re-sends, gone at 12:18:24. With no spare left, the leg aborted home
      (mining-20261020-115828-d1e0: "no pickaxe worn, in the backpack or in a bag in it"; the 1522 ingots were
      stored).
    - [INFERENCE] about 500–1000 digs a pickaxe. Its uses-left label isn't read, unlike the lumber runner's hatchets.
    - The room chest 0x4AE0DD2C held 7 spare pickaxes (graphic 0x0E86) and 15 hatchets.
    - Since then `MineLoop.hatchets_ready` tops up to `PICKAXES_CARRY` (2) pickaxes from the room's chest before
      each trip in the room (`pickaxes_from_chest`; the trip row's `pickaxes` {have, from_chest}). With none
      carried or in the chest it posts `low_supplies` and makes no trip (test_loop_mining mine_main).
  - **Ingot values** (user 2026-10-20: "A commodity deed of 5000 iron ingots is worth 150k gold"): price row
    `ingot:iron` = 30 gp (`ctl lumber price ingot:iron 30 --source user`, prices id 89). The user's prices the same
    day, in gp per ingot (source user):

    | Metal | gp | Metal | gp |
    |---|---|---|---|
    | dull copper | 24 | gold | 80 |
    | shadow iron | 26 | agapite | 160 |
    | copper | 28 | verite | 181 |
    | bronze | 52 | valorite | 285 |
    | | | avarite | 743 |

    Every metal now has a price.
    - Since then each leg row has `ingots_by_metal` (made by its smelts) and `stored_by_metal` (into the stockpile),
      keyed by `loop_mining.METALS`. That map comes from the hatchets' materials table (harness/data/hatchets.json):
      0 iron, 2419 dull copper, 2406 shadow iron, 2413 copper, 2418 bronze, 2213 gold, 2425 agapite, 2207 verite,
      2219 valorite, 1763 avarite (from community Razor scripts). Any other hue is "hue N".
    - Live 2026-10-20 13:04:22 the first drop-off with the counts: 304 iron, 22 dull copper, 16 copper, 14 shadow iron.
    - [INFERENCE] that Outlands keeps these hues: its stockpile lists ingots in exactly these hues. Dan's drop-off on
      2026-10-19 22:44:39 held 10 ingots of hue 2419 beside 1080 of hue 0.
    - The `smelt` job event has `metals`; the drop-off log line names them ("added 640 ingots … (630 iron, 10 dull
      copper; …)").
  - **Dan died at the Prevalia Cemetary Crypt landing** (2026-10-20 13:14:57, mining-20261020-131415-e57a,
    forge_1821_1808, the overseer's explore pick, P(best) 0.03).
    - The DTF rune "Prevalia Cemetary Crypt" (1892,1785) lands in a graveyard with cursed remnants: 4 serials
      0x002DC8CC/CD, 0x002DCB94, 0x002DC6CF, ranged, one shot hit for 57.
    - Timeline:
      - A remnant was in flee range 1 s after the landing.
      - The first escape leg went 12 steps north. A recall at 13:14:42 was disturbed by a second remnant.
      - The run out of reach ("running 14 tiles from 2 creatures") then went 44 steps south, deeper into the
        graveyard, with 3–4 attackers by (1894,1798).
      - At 13:14:49 the runner judged "a cursed remnant keeps its distance (12 tiles, a shot every 4.85 s)" and
        stood still to cast right after a shot. That cast was disturbed too: another remnant was 1 tile off.
      - It then waited 5 s for a shot that didn't come, cast again, and died (57 → 0 from one shot at 12 tiles).
    - The death's position change was taken for the recall's arrival: "recall try 2: arrived … escaped by recall to
      (1883, 1810)". So the leg row's why reads like a threat sent home.
    - Done: on the user's word ("it's a graveyard full of mobs") the landing is marked bad
      (`lumber_opt.mark_bad_landing`, meta lumber_bad_landings 0:1892,1785) for lumber and mining alike, and
      forge_1821_1808 is disabled.
    - `mining_opt.trip_obs` now also counts the job's `death` events within a leg (or `DEATH_SLACK_S` 10 s after it)
      as a death, in the plan and the Jobs page. With that, the spot shows 1 death and the pooled death hazard went
      to ~0.45/h (test_mining `test_death_events`).
    - Fixed the same day (user: "fix those issues"):
      - `escape._arrival` ends with failure "dead" when we die. A ghost's position change no longer passes for an
        arrival, and `retry_wait("dead")` is None (test_escape `test_death_is_no_arrival`).
      - `gain_distance` asks `escape_tiles` for goals away from every hostile creature in view, the ones after us
        first. With no such tile (a crowd on every side) it asks away from the ones after us.
      - `run_and_recall` takes no pacer stand-and-cast while another hostile creature is within `RECALL_GAP` or
        swinging or casting at us (`crowd_besides`): it runs first ("… keeps its distance, but X is after us too:
        running first").
      - Tests: test_loop_lumber `unit_escape_crowd`.

  - **The room's small forge** (2026-10-20, bought and placed by the user in Logan Wolf's DTF rental room).
    - `ctl status` ground_items with Dan in the room: "forge", serial 0x4FD0EBA1, graphic 0x0FB1 (in
      `ore_nodes.FORGES`), at (403,923,2) on facet 3: the room's arrival tile (until it was moved, below).
    - It is homes.json `forge` for Outland Dan and Logan Wolf. The mining runner smelts the carried ore there
      before the store (docs/PLAN.md "With a forge in the rental room").
    - Confirmed live 2026-10-20 14:10:16 (mining-20261020-135409-1160, forge_1173_2397): "smelted 158 ore into 316
      ingots at the room's forge (home)", the convert phase took 1.7 s, then "added 902 ingots to the resource
      stockpile". The leg's other 293 ore had been smelted at the spot's forge for the weight.
    - **Moved 2026-10-20 ~22:05** (user): on the arrival tile it blocked walks onto (403,923). At 21:58:37 the user
      stepped Logan off the tile; `act dropoff`'s walk back then failed twice ("walking to the arrival tile (403, 923):
      too many blocked moves (21 in this walk)"; `act goto 403 923 --z 1`: "no route"). Only a room entry lands there.
      Re-placed as serial 0x51CF24B3 at (400,923,2) (homes.json updated); the room smelt walks to `forge_range` of it.
      Keep the arrival tile clear of furniture: the drop-off's items are reached from it.
  - **Yield vs skill** (measured 2026-10-20: capture session_20261020_102748, 4333 dig replies at skill 55.0–68.5; 25 leg
    rows that day).
    - Each successful dig names its metal: cliloc 1007072 iron, 1007073 dull copper, 1007074 shadow iron, 1007075
      copper, 1007076 bronze (1007077–1007080 gold–valorite by RunUO's order, not seen yet). Counts: 3039 iron, 63
      dull copper, 53 shadow iron, 32 copper, 10 bronze, 1095 fails (503043). The hue names of `METALS` agree.
    - Success rate (any metal) by 3-point skill bin: 54: 65.7 %, 57: 72.7 %, 60: 72.7 %, 63: 75.8 %, 66: 78.8 %.
      That's about 1.18 × skill (or skill + 11 points), above the wiki's skill × (1 + tool bonus) for a plain pickaxe
      (k12628). [INFERENCE] The Harvest aspect suit adds a bonus. The double-yield rate was 28.4 % of successes.
    - Colours come well below the wiki's skill requirements (k12628/9): shadow iron first at 55.6 (wiki 70), copper
      at ~61.7 (75), bronze at 67.4 (80). No gold or better in 4333 digs, so a lower threshold exists. [INFERENCE]
      roughly 12–15 points under the wiki's.
    - Field time that day: 1.33 s per dig, 0.951 ore per dig, 1326 ore per field hour. Digging was 51.5 % of field
      time, the 60 s travel lockouts 8.4 %, smelting 2 %. The rest (38 %) was walking between stands and escapes:
      1.32 s per ore.
    - The ore was worth 59.8 gp each (2 ingots; 92 % iron; dull copper, shadow iron and copper ingots are worth less
      than iron). Supplies were 23 own-book recall charges (the price row `recall_charge` is 8 gp: user 2026-10-20,
      "Recall scrolls are 8gp each from player vendors"; it was 200 gp), 83 reagents and 1 trapped pouch, ~460 gp
      in all: noise. Field time was 82.6 % of the hours running (death downtime left out). Net: ~56k gp per clock
      hour, with the death's 35 min downtime included.
    - Projection at 100 skill (user request, 2026-10-20), from the wiki's base node chances and colour formula, the
      measured 1.18 multiplier, a colour share of 0.8 calibrated on dull copper to bronze, and doubles and the
      per-ore walk unchanged: success 98.6 %, 1.27 ore per dig, ~1520 ore per field hour, 66.6 gp per ore (68.1 if
      avarite unlocks). That is ~101k gp per field hour and ~71k gp net per clock hour (range ~66k–85k: death
      hazard 0.25–0.37 per running hour, the per-ore walk fixed or shrinking with the digging).
    - The Jobs > Mining tile "gold / active hr" (`jobs.compute_mining` `gp_per_active_hour`: gold stored over the
      legs plus gaps of up to 2 min between them) leaves out breaks, stops and a death's downtime. It read 72.8k for
      that day up to 14:38, the projection's base window. On that measure the projection at 100 is ~93k (88k–110k).
      The legs from 14:38 to 16:04 (skill 68.5–72, the planner's best spots) already read 93.5k: the choice of spot
      and the failed legs weigh as much as skill.

## Monster casts on the wire: 0x6E then 0xC0 then '-N' (cast_scan, 2026-10-23)

`harness/sim/cast_scan.py` over 103 captures since 2026-10-10 (33 with samples in the Mausoleum; table and rules in
docs/SIM.md "Cast and flight times; traces"). Protocol facts it rests on:
- **The runtime keeps only effects that involve us** (`world/runtime.py` `_h_effect`): a monster's bolt at a pet never
  becomes an event, so scans of other mobiles' casts read the raw S2C 0xC0 (52 B Outlands form, layouts 0xC0).
- **A Mausoleum spellcaster's spell is three packets:** its 0x6E cast animation (the action per type: blightmare 7,
  banshee and shadowguard 6, shadow minion 5, countess 4, reanimators and forgotten soul 233, reanimated daemon 12,
  familiar 16, vampire 13), the moving 0xC0 ~1.2 s later (graphic 14036 = 0x36D4, speed 7, explode 1; the blightmare's
  hue 2821, the reanimated daemon's 2850), and the '-N' over the target 0.50 s after the effect at any distance
  (median 0.51 / 0.51 / 0.50 s at 1-3 / 4-7 / 8+ tiles, n=119 / 127 / 82).
- **The calamity's shot** is different: graphic 7816, speed 10, hue 1165, its 0xC0 0.05 s after its animation (10), the
  number 0.33 s later (q10-q90 0.08-0.88).
- **No 0xC0 at all** from the corpse purger, elder vampire, eldritch experiment and reanimator zealot (their hits come
  0.8-1.7 s after an animation), nor from our phoenix AlaFlambe (2551638): its fire carries no moving effect.
- Our pets aren't always flagged `pet` in the model: the "(tame)" line comes only after a click, so AlaFlambe and
  GraveRobber showed up under their names as monsters in some captures (a scan keyed on `m.pet` misses them).

## Tidal Tomb: Logan's first visit (2026-10-24 10:59:55-11:14:06, capture 20261024_102346)

The user played it. It is the evidence behind the simulator's `tidal_tomb` scenario (docs/SIM.md "Tidal Tomb").

**Getting in and out:**

- **The rune.** "Tidal Tomb Inside" is in the runebook **"Bad Places II"**: the 11th rune of its gump 0x09F5976B,
  button 110 at 10:59:53. Logan's own "A Rune Tome" has no Tidal row (its gump at 11:14:07). The rune libraries
  also list "Tidal Tomb Inside" (6653, 2097) and the outside entrance, Witcher 346 (3826, 3591).
- **The arrival** is (6653, 2097, -16) on level 1, 2 tiles from the hue-2822 **golden moongate 0x4003D7B5** at
  (6652, 2095). A **crimson moongate 0x4001AA04** stands on the same tile. A second golden gate 0x4003D7B7 is at
  (6642, 2062).
- **The exit is a teleporter south of the arrival.** A step S from (6656, 2108) put Logan outside at
  (3861, 3585, -59) at 11:00:06. Just outside, Oswald the Archeologist gives the "Tidal Tomb Collectable Card"
  quest: slay 10,000 gold worth of creatures "Inside of Tidal Tomb Dungeon Underwater Areas". The quest's progress
  still counted on level 1, though. A step N from (3861, 3586) put him back in at (6656, 2109) at 11:00:51.
- **Level 1 -> level 2:** a step S from (6718, 1983) at 11:08:37, landing at (6943, 2004, -38).
- **Level 2 -> level 1's west end:** a step N from (6834, 2080) at 11:13:31, landing at (6609, 2060). A step S from
  (6609, 2059) at 11:13:39 put him back on level 2 at (6834, 2079).
- **Teleporter pairs land on each other's 'from' tile.** 2 of 2 pairs were seen both ways. All six tiles are in
  `harness/data/teleporters.json`; the way up from level 2's arrival is [INFERENCE].
- **Out:** Logan double-clicked the crimson moongate 0x4003D7B8 at (6913, 2071, -100) on level 2. Its
  "Moongate Destinations" rows are the Mausoleum crimson gate's, Anchor's Rest .. Corpse Creek. Travel with Totem
  selected landed him at (3867, 2900, -40), not the (3859, 2890) of the Mausoleum's gate. From there the tome took
  him home.
- **No recall was tried inside.** The scenario applies the user's dungeon rule: recall only near a gold gate.

**The levels**, from a map flood fill with pathfind.Walk from each arrival:

- Level 1: x 6608-6738, y 1932-2109, z -24..85.
- Level 2: x 6817-6967, y 1909-2098, z -127..-10.
- Every one of Logan's 1,153 steps there is walkable on the install's map.

**Oxygen.** Patch notes 2025-11-16 (knowledge #4641-4645): levels 2+ are underwater. After 10 minutes without a
refill comes minor deprivation, after 20 major. Level 1 or the outside refills it, and so do Recovery Dolphins at
level 3's entrance. Live on level 2:

- The server sent the gump **0xEC5F8754 "Oxygen"** every 6 s, closing the old one.
- Its layout holds two `tilepichue` bars at (58, 50) and (58, 64). The second's art stepped from 28135 down one
  every 30 s, reaching 28125 at 11:13:14.
- It was back at 28135 on level 1's west end (11:13:39).
- [INFERENCE, refuted 2026-10-25: 24 steps, 732 s; "Tidal Tomb: the later visits" below] 28135 -> 28115 is the 10
  minutes. The first bar stayed 28135.

**Monsters on level 1 and their specials:**

- **Oozing noughtilus: Ooze Aura.** 4 numbers 0.3 s apart on everyone within 2 tiles, 0.5 s after its animation 10,
  every 7-12 s.
  - The gravebug took 2-5, Logan 4-7.
  - Twice Logan had "*stumbles around in confusion and pain*" over him, then "You receive a free cure potion
    consumption": the aura's greater poison, cured at once.
- **Ruby smallshell: Pierce Claw.** "A ruby-sharp strike temporarily reduces your armor!" with a 22-23 on Logan,
  then "Your armor is no longer pierced." 30 s later. This happened twice.
- **Pincer crabs emerge.** 8 new crabs, each with "*emerges*" beside a fight. Each came 0.5 s after a dungeon-nest
  crab's "*digs*" or a lowtide scavenger's "*takes large swing*". All 11 pincer crabs died; they have 500 hits.
- **Bottomfeeder: "*feeds*".** 4 ticks 0.5 s apart on the gravebug.
- **Who hit Logan:** 11 hits of 22-32, from monsters on him: flying mantas, ruby smallshells, a singing noughtilus,
  and on level 2 a soulcatcher and a clearwater grouper. None of it was area damage beside a pet.
- **Barding.**
  - The creature Guide gump 0xC38EC76C shows "Barding Chance" per creature, e.g. a flying manta 74.1 %. The wiki
    formula says 93 % for it.
  - Discord lasted 68 s in 21 of 23 successes, peace 34 s, provoke 42 s.
  - Songs were 16.9 %.

**Other players.**

- Four blue players with pets were seen on level 2.
- **A red, "BUL SA PA oOo"** (notoriety 6, human body), appeared on level 1's west end at 11:13:34, 18 tiles from
  Logan. It followed him down the teleporter 3 s after him and stayed within 1-3 tiles for ~10 s
  (11:13:42-11:13:53), but it never hit him (no damage, no harmful act on the wire). It left north at 11:13:57.
  Logan said "All Follow Me" at 11:14:01 and took the crimson gate out at 11:14:05.
  - `logs/red_scan/20261024_102346.json` has the packets.
  - `sim/calibrate.py` now keeps reds out of the creature types.

## Tidal Tomb: the later visits and level 3 (2026-10-24 19:32-21:04, 2026-10-25 11:21-11:36)

The user played all of them. Evidence for the simulator's `tidal_tomb` changes of 2026-10-25 (docs/SIM.md "Tidal
Tomb"):

- Capture 20261024_102346 had four more visits that evening, after the scenario was first built from its morning:
  19:32-19:41 (level 1), 19:48-20:05, 20:07-20:20 and 20:45-21:04 (levels 1-3). Pets GirthyEarthy, fireminion, a
  searing imp and a tidal mantis. AR 133.
- Capture 20261024_225113, 2026-10-25 11:21:30-11:36:55: levels 1-3. Pets NightmareA, NightmareB and the searing imp.
  AR 122.
- `sim/calibrate.py --scenario tidal_tomb` now covers both captures and the three levels: 4,224 s and 925 s in the box.

**Getting in and out:**

- **In:** every time by "Bad Places II" button 110 (19:32:41; 2026-10-25 11:21:28), from the runebooks on a shelf at
  the house landing (4152-4154, 1431). Its neighbours there: "Towns Shrines & Alliances", "Wildlands Wilderness",
  "Bad Places", "Friendly Houses 1". Arrival (6653, 2097, -16) every time.
- **The teleporters are strips that keep the column.** Each pair lands on the other's row:
  - level 1's west end (6608-6610, 2060) -> level 2 (6833-6835, 2079);
  - level 1's exit (6656-6657, 2109) -> outside (3861-3862, 3585), and back;
  - level 2's north end (6884-6885, 1929) -> level 3 (7162-7163, 2221);
  - level 3's arrival row (7161-7162, 2221) -> level 2 (6883-6884, 1929).
  - So level 3's arrival tile is itself a teleporter. Landing on one doesn't move you; stepping onto it does. A step
    S from the tile north of the arrival took Logan back up (2026-10-24 20:58:00, 21:04:20; 2026-10-25 11:35:19).
  - All eight new tiles are in `harness/data/teleporters.json`. The strips' other columns are unknown.
- **Five moongates.**
  - Golden, on level 1: 0x4003D7B5 (6652, 2095), 0x4003D7B7 (6642, 2062), and a new one, 0x4003D7B6 (6685, 2064, -18).
  - Crimson, on level 2: 0x4003D7B8 (6913, 2071, -100), and a new one, 0x4003D7B9 (6897, 1992, -126), 8 tiles S of
    the strip to level 3.
  - Every menu opened listed the same nine 'Moongate Destinations' rows, Anchor's Rest .. Corpse Creek. That includes
    the golden 0x4003D7B6 at 19:41:11. The scenario had guessed a lone Totem row for the golden gates.
- **Totem's landing tile varies.** Six trips put Logan at (3867, 2900), (3870, 2891), (3846, 2876), (3848, 2867),
  (3882, 2893) and (3859, 2890).

**Level 3:**

- Map flood fill from the arrival: x 7049-7255, y 1908-2221, z -55..3, 14,873 tiles.
- Logan saw only its first part: he stood at x 7156-7201, y 2153-2221, z -50, for 822 s over the two visits.
- **A Recovery Dolphin**, 'recovery dolphin' 0x00004888 (body 151, notoriety 7, '(refills oxygen)'), stands at
  (7164, 2210), 11 tiles N of the arrival. Same serial and tile on both days.
  - Near it: 'Your oxygen is refilled.' (and '*oxygen refilled*' over Logan) every 5.06-5.38 s (n=26 gaps, median
    5.25 s), within 8 tiles.
  - None came at 10-13 tiles for 16 s or longer.
  - The first came 0.5-2.3 s after he walked into range: the dolphin ticks on its own clock.
- **Monsters**, each at the same spot every time:
  - a tidal sea serpent at (7185-7187, 2191);
  - a giant astral zoa at (7156-7158, 2193-2196);
  - a giant combustive zoa at (7197-7198, 2172-2174);
  - a greater razorfin at (7181, 2171);
  - colossal pincer crabs at (7176, 2151) and (7217, 2183).
  - Out-of-sight respawns took at most 4.6 min (the astral zoa) and 5.4 min (the serpent).
  - Every kill took the wiki's hits + 1 in numbers: serpent 20,001 (x3), astral zoa 18,001 (x2), colossal pincer crab
    18,001, greater razorfin 20,001.
  - Gold per corpse was within -4 % .. +11 % of the wiki's value.
- **Tidal sea serpent.**
  - Swings of 59-71 on a Nightmare and 49-58 on GirthyEarthy, ~3.4 s apart.
  - Every 7.5-8.0 s (median 7.7) it also hits each pet within 3 tiles at once: 106-126 on the Nightmares and the imp,
    85-95 on GirthyEarthy.
  - Logan stood 4-7 tiles off and was never hit by it (11 of 11).
- **Giant astral zoa**, a mage that stands still, has three attacks:
  - every ~6 s (5.0-8.0), 78-82 on each pet 5-7 tiles from it, and 35-56 on Logan near them. Each of Logan's came with
    'Astral backlash impacts your ability to concentrate while casting!'; 'You are no longer suffering spell
    backlash.' followed ~35 s later. On 2026-10-24 the tank beside it (GirthyEarthy) never took these, only 70-78 alone
    at the same cadence.
  - every 3.1-3.6 s in a row, 29-51 on 3 (twice 4) of the team at random, out to 12 tiles; on Logan 29-39;
  - its single spells on the pet it fights, 35-54, 2.5-5 s apart.
- **Giant combustive zoa.** Only one fight (2026-10-25 11:34:13-11:34:50), and it killed the three pets.
  - Every 5.3-6.1 s, one number on each pet and on Logan at 8-12 tiles.
  - The pets' numbers rose fight-long: 82-87, 89-95, 97-103, 110-112, 112-118, 123-128. Its single hits on a pet rose
    too, 40 -> 74.
  - Logan's were 41, 32 and 40, each with 'The creature scorches you and you become more susceptible to damage from
    them!'.
  - [INFERENCE: a stacking +~10 % like Nusero's Scorching Breath (#7518: +10 % for 60 s, max +30 %); here past +50 %.]
- **Greater razorfin.** Swings of 36-70 (median 54, ~3.4 s) on GirthyEarthy, plus a steady 18 every 1.27 s (n=60).
  The 18s went on 14 s after its death: a bleed with no text.
- **The pets.**
  - 2026-10-25, all three died on level 3: NightmareB to the serpent at 11:32:04, then NightmareA at 11:34:44 and the
    imp and NightmareB at 11:34:50 to the combustive zoa. Each time the veterinary supplies gave 'You are able to
    resurrect your follower.' (bonded). Logan retreated up the strip at 11:35:19 and out by 0x4003D7B9 at 11:36:55.
  - 2026-10-24: the imp died at 21:01:32, the fireminion at 21:01:39 and 21:03:46 (the second astral zoa and serpent).
  - Level 3 over both days: 7 kills, 7 pet deaths, 726 damage on Logan (19 hits of 29-56) and 14 heal potions in 822 s.
    Logan didn't die.
  - The Guide gump on a Nightmare (11:20:15): 228 hits, level 14, control slots 2, attack speed 2.86 s, spell damage
    24.0-30.6, wrestling 76.3, armor 24.6.

**Oxygen, measured** (2026-10-24 19:52:11, under on level 2 until the deprivation):

- 'You suffer minor oxygen deprivation.' came from 20:04:23: 732 s under, not the patch notes' 10 minutes. It came 9
  times, 6.0 s apart (6.00-6.06), not 5.
- Logan lost 5 each, then one 2 that took him to the 66 % floor.
- The tidal mantis lost 32 (5 %) five times, then 7, then 1 three times: what it regenerated above the floor. So a
  hit never takes you below the floor.
- The 'Oxygen' gump's second bar stepped 28135 -> 28111, one step every 30.25 s: 24 steps to the deprivation. Then the
  first bar started down (28134 at 20:04:23). [INFERENCE: major deprivation when the first bar runs out too, ~24 min.]
- On 2026-10-25 the level 2 -> 3 strip at 7.6 min under and the dolphin beside level 3's arrival kept it from coming.
- **The gump's bars, fitted** (2026-10-28, the store's 644 'Oxygen' gumps of session 204 and the deprivation of session
  192; `tidal_tomb.json` `oxygen.gump`): the second bar's first art goes ~10 s under water (the full gump at -701.3 s
  before the empty one, 28134 at -695.8), then one every 30.1 s (14 steps in 421.1 s; 23 in 695.6 s), not 30.25.
  24.1 s after the last step came 'You are now experiencing minor oxygen deprivation! (continuous minor health loss
  when above 66% health).', and 30.1 s after it the first hit's line with the first bar's first step. So the k-th step
  lands at (k - 1) x 30.1 + 10 s under and the first hit at 23 x 30.1 + 10 + 30 = 732 s. Replayed through the runner's
  clock (harness/oxygen.py), that stream put the first hit at 733 s.
- **Level 2's arrival is stairs both ways** (the user walked them 2026-10-29 10:40:05-10:40:43, store session 205; the
  0x21 denies give the tiles). Up: a step N from (x, 2005), x 6942-6944, onto (x, 2004) lands on (x - 225, 1984) on
  level 1, 5 times. Down: a step S (once SE) from (x, 1983), x 6717-6719, onto (x, 1984) lands on (x + 225, 2004), 6
  times. Each landing is the other way's row, as at the west strip, so a round trip is a step off and back on. The
  'Oxygen' gump read 28134 before (10:40:28) and full (28135, 28135) on every return to level 2, after ~1 s on level
  1. The captures before had none of it: every step there went off the tile after an arrival.

**Barding: the instrument changed between the days** (user 2026-10-25: "Barding depends on the quality of the
instrument").

- Which instrument Logan played shows in the captures: a double-click on an instrument plays it, and its name comes
  from the single-click (0xAE).
  - 2026-10-24 10:26:19 and 19:32:51: an **eminently melodious sackbut** (+15 barding skill, wiki Magic Item
    Modifiers #13809). So the first visit and the evening's were played with it. The user remembered "maybe
    supremely" for before; the capture says eminently.
  - From 2026-10-24 22:17:39 a durable **exceedingly melodious tambourine**, and 2026-10-25 10:58:19 a durable
    **exceedingly melodious psaltery** (+20).
  - Also clicked those days, not played in the captures: surpassingly melodious harps and a lute (+10), a supremely
    melodious bamboo flute (+25).
- With the sackbut: songs 16.9 %, discord 68 s, peace 34 s, provoke 42 s.
- With the psaltery: songs 17.7 %, discord 71 s (19 of 21; 86 s twice), peace 43 s (7 of 8), provoke 36 s (6 of 6).
- The creature Guide's "Barding Chance" on an oozing noughtilus: 87.7 % with the sackbut, 94.2 % with the psaltery
  (its "Barding Duration" 68 s -> 71 s). (Read on 2026-10-25 as Item ID multiplying the bonus, knowledge #3870; the
  songs below show no multiplier: Logan has no Item Identification.)
- The Mastery Chain gump on 2026-10-25: 12 links, Follower Damage +16 %, Effective Barding Skill +18. After the trip,
  a 13-link limit.

**Barding from the effective skill** (2026-10-26; user: the links as an input to the simulator, and "the only thing
that matters is the 'melodious' level", not the instrument's kind). Evidence: the memory store's songs and chain gumps,
the hunt task logs' 4,293 bard answers 2026-10-15..26 (`harness/sim/bard_scan.py`).

- **Logan's chain over time** (gump id 2785326818 in the store): 2026-10-13 17:49 "8 Links Installed, 12 Link Limit,
  6 Follower Damage (+16.00%), 2 Effective Barding Skill (+6.00)"; 17:50 12 installed, Effective Barding +18.00 (six
  bronze links of 3.00); unchanged to 2026-10-25 11:19; 11:37 a 13-link limit; 11:38 7 Follower Damage (+18.50 %: a
  silver 2.50), the same through 21:33. The gump shows only each type's count and total, not the materials.
- **A song prints 15 % x E / 100, cut to a tenth**, E = the effective barding skill = 80 (Peacemaking, Discordance,
  Provocation) + the instrument's quality bonus + the Effective Barding links: 15.0 % (E 100: exceedingly, before the
  links, 2026-10-13 17:39), 16.2 % (108: surpassingly), 16.9 % (113: eminently, the sackbut), 17.7 % (118:
  exceedingly), 18.4 % (123: supremely, since 2026-10-25 17:36). 16.8 % (E 112) 2026-10-14..17 [INFERENCE: a skill a
  point lower then]. The wiki says 5 % x E / 100 (#11234). Others: 4.0 % on 2026-10-02/03 (before the template), 15.8 %
  on the test shard 2026-10-15 morning, 9.9 % twice on 2026-10-23 21:26-21:41 (not Logan's E; unexplained).
- **A success lasts ceil(base x (E + bonus) / 100)**, base 60 s for Discordance, 30 s for Peacemaking and
  Provocation, the same for every creature type; bonus 25 on a target already barded (Discordance on one pacified or
  provoked, #11223; Peacemaking or Provocation on one discorded). 2,957 of 2,979 durations in the task logs fit; the
  other 22 fall within an hour of an instrument change. Logan's Peacemaking almost always follows his Discordance, so
  it lasts 30 x (E + 25): 43 s at E 118, not the 36 s a fresh target gets.
- **One point of E is one point of chance**: p = max((E + bonus - d) / 100, 0.33 x (E + bonus) / 100) with d per skill
  and type. Over the eras (E 108-123), a slope of 1 beats a slope of 0 by 4.8 in log-likelihood on 3,000 Discordances
  and 4.6 on 1,000 Peacemakings (0.5-1.5 fit alike). No 95 % cap: raw flesh and newly reanimated took 120 of 120. The
  wiki difficulty is not the server's d: Discordance's fitted d sit at 23-57 on types the wiki puts at 67-205, and
  Peacemaking's are 15-35 higher than Discordance's on the same type (median 76 against 45.5).
- **Barding break avoided** happens with no Break Ignore link: 53 "Barding break avoided for <name>" lines
  2026-10-12..26 against 238 "*barding break*" overheads (any player's breaks in view). A base chance exists; its size
  is not separable from these counts.
- The Discorded debuff scales with the printed Discordance, not E (#11224: 80 / 120 x 25 % = 16.67 %): the links and
  the instrument don't change it.

**Leaving level 3.** The user's way out: back up the arrival strip (a step S onto (7161, 2221) at 11:35:19) and the
crimson gate 0x4003D7B9 at (6897, 1992) on level 2 (11:36:55). The hunt runner does the same since 2026-10-25 with
`--leave-via 7161 2221` (docs/HUNT_LOOP.md "The gate a level up").

**Other players:**

- The red *BUL SA PA oOo* showed up again, on level 2 at (6882, 2007) at 11:36:52, 18 tiles off, saying "In Vas
  Mani". Logan took the crimson gate 3 s later.
- 'A murder has occurred within this area' came at 11:31:41 on level 3. The player Romeno died on level 1 at
  11:22:01.

**A calibrate.py fix the second visit needed.** Two numbers summed time across visits as `t1 - t0`, which included
the 8 hours between them: the AR seconds (36,280 s at AR 133 for 4,225 s there) and `damage_to_us_per_minute_in_region`.
They now sum the time in the region (`Session.seconds_in_region`, also in `source.captures`). One visit's numbers are
unchanged.

## Tidal Tomb: level 1 walked whole (2026-10-26 20:30:34-20:45:36, capture 20261026_183552)

The user played it to check the simulator's level 1 (docs/SIM.md "Level 1 walked whole"). Pets GraveRobber and
AlaFlambe. AR 120 the whole visit, not 133.

- **In:** at 20:28 he was on Maus 3 (5242, 681); recalls by his tome 0x444AB1F3 followed. At 20:30:29, standing at the
  DTF guild house (4152, 1432), he used the library tome **"Bad Places II" 0x457BB5CB** in a wooden shelf: gump
  0x09F5976B, button 110, "Tidal Tomb Inside". Arrival (6653, 2097) at 20:30:34. `ctl runes near 6653 2097 --character
  "Logan Wolf"` lists no rune of his own books there (the books as last read by `act recall --check`).
- **Covered:** he stood within 12 tiles of 96 % of the level's floor. He met 106 monsters of 10 types: aquamarine and
  ruby smallshells, oozing and singing noughtilus, bottomfeeder, dungeon-nest crab, flying manta, lowtide scavenger,
  mermadon and pincer crab. He killed 89, 27 of them pincer crabs. Damage on him: 13 hits, 288 in all, 6-28 each.
- **Lowtide scavenger, `*takes large swing*`:** 14 from 5 scavengers in fights, 7.6-8.0 s apart (median 7.86), the
  first 0.9-4.6 s after it went to war mode.
  - 0.5 s later a 14-21 (median 18) on its target, GraveRobber each time. Twice AlaFlambe took a 21 / 23 at the
    same instant.
  - A pincer crab `*emerges*` beside it at that instant: 9 times, with no nest crab digging at that moment.
  - Never with 2 of its own crabs up. In capture 20261024_102346 the first two swings of each scavenger brought a
    crab (13 of 13), the next ones none.
  - Its regular hits on the gravebug are 9-14, ~3-3.5 s apart.
- **`*scavenges*`** (15): 14 while it stood idle, every ~10 s, with no damage and no crab. An idle emote [INFERENCE].
- **Nest crab `*digs*`:** 12, a crab 0.48-0.54 s after 9 of them.
- Ruby smallshell's Pierce Claw on Logan once; the status AR stayed 120.
- `*barding break*` over a singing noughtilus twice, each right after a successful provocation on it.
- An oozing noughtilus came back 144 s after its death, on the same tile (6635, 2018).
- Kills took 4.8-11.9 s a type (median from the first number to the death), pincer crabs 1.4 s.
- **Songs on this floor** (the runner's first live run here, hunt-20261026-215149-a52b, 21:52-21:54): every song at a
  tile beside Logan, sent as the land (graphic 0) at his z, got cliloc 500237 "Target cannot be seen." (the runner
  logged "no answer"). The floor is statics over the invalid land 0x244 at z -100, and RunUO's LandTarget takes the
  land's own height. The user's songs named the floor static under the clicked tile (0x8C62 at -18, 0x50B1 at -16,
  0x053D, 0x0522, 0x58C6; 1-4 tiles off) and all 7 played. Fix: `Team.song_spot` (docs/HUNT_LOOP.md "Barding songs").
  The first fix's run (hunt-20261026-220225-0528) played all three, but at 22:08:16-38 three songs named the stone
  block 0x71E/0x71F (height 5) under the pavers at its own z and got no answer at all; flat floor statics (0x051A at
  51) played. Since then it names the flat floor on top.
  The runner's 2026-10-27 shift (hunt-20261027-233648-7e3b) found the same on level 1's ledge, which is valid land
  under a raised floor: at 01:04:42-01:05:22 four songs named the land (graphic 0) at z 24, over land at z 19 under
  the stone 0x71E and the floor 0x51E at 24, and each got "cannot be seen". So the server takes graphic 0 at the land's
  own height on valid land too. Every live Mausoleum song spot checked (seven, 10-26 18:44-19:13) had the land as
  its floor. Since then the runner names the land only where the land is the floor, else the flat floor static
  (docs/HUNT_LOOP.md "Barding songs"). The simulator answers graphic 0 the same way.

## Tidal Tomb: levels 2 and 3 by hand (2026-10-26 22:36:47-22:48:42, capture 20261026_183552)

The user played it after the runner's level 1 shift: in by level 1's west strip, level 2 for 9.0 min, level 3's first
part for 2.6 min, back up the arrival row. Pets GraveRobber and AlaFlambe. Evidence for the simulator's 2026-10-26
level 2/3 changes (docs/SIM.md "Levels 2 and 3 by hand").

- **No new creature type.** 45 monsters first seen there, all of the scenario's types. He stood only where the
  earlier visits had (every tile within 12 of one of theirs).
- **Spawns:** today's idle sightings at the strip's arrival (x 6840-6855, y 2080-2098) were the types the earlier
  visits had seen idle on the same tiles (razorfin, frenzyfin, soulcatcher, biolumen shellpod, drowned mariner,
  astral zoa), yet the scenario had none of them there: the old "only where the first visit couldn't see" rule had
  dropped them.
- **Damage:** level 2, Logan 262 in 8 hits (1,740 an hour), GraveRobber 2,401, AlaFlambe 460. Level 3, Logan 44 in one
  hit, GraveRobber 1,958 and AlaFlambe 1,125 in 2.6 min: the giant astral zoa's 70-78 on GraveRobber every ~5 s, the
  sea serpent's 35-97, then the greater razorfin's swings and its 18 bleed ticks. GraveRobber died at 22:48:00 to the
  greater razorfin ("You are able to resurrect your follower." at 22:48:05).
- **The Recovery Dolphin reaches 12 tiles, not 8.** 'Your oxygen is refilled.' every ~5.2 s with Logan 10-12 tiles from
  it (22:47:51-22:48:11, (7172, 2198)). Over all three captures, one-second samples on level 3 with a refill within
  5.5 s: 7-9 tiles 90 of 96, 10-12 52 of 75, 13 2 of 23, 14+ 8 of 207. Its sight doesn't matter (16 of 21 at 12 out of
  sight). The 2026-10-25 note "none at 10-13 tiles" was wrong.
- **Frenzyfin '*frenzies*'** (2 here, 11 in all captures): after the emote three rounds of hits 0.6, 0.85 and 1.1 s
  later on every pet beside it, 20-32 each (2026-10-25 11:26:06 on three pets at once, 25-31), ~15-30 s apart on one
  frenzyfin.
- Once each, not modelled: a combustive zoa's scorch on Logan (22:38:43 "The creature scorches you and you become more
  susceptible to damage from them!"), an ironscale grouper's '*deflects*' (22:45:41, no damage number near it).
- Other players on level 2: iniquitous ("[OG Fallen Lord, -FL-]", casting In Por Ylem, Uus Sanct, In Jux Sanct),
  sirgrundy, Lunar Sniper; none hostile to us.

## The ocean bedrock's spray, and level 2's other specials (2026-10-26, from four captures)

The user (2026-10-26): "I stand behind the ocean bedrock so it doesn't do its spray attack on me. In that area I'm
generally able to keep the mobs provo'd onto each other." Read from his hand play in captures 20261024_102346 (10-24
20:51-21:04), 20261024_225113 (10-25 11:28-11:37) and 20261025_210102 (10-25 21:20-21:32). 33 sprays from 11
bedrocks, all on level 2's north strip (x 6880-6898, y 1934-1996, near the crimson gate). 20261026_183552 has no
bedrock fight: the one in view fought the player SirGrundy. The scratch analysis is not in the repo.

- **The warning:** animation action 18 (frames 8) on the bedrock (body 0xB3). The wave starts 0.69-0.76 s later
  (median 0.71; 33 of 35 action-18s; the other two were cut off by its death). No text.
- **The start and the wave:** sounds 0x0811 and 0x080F at its tile. Then each wave tile gets sound 0x0026 and a 0xC0
  moving effect with graphic 0xDD8F. One Chebyshev ring of tiles a 0.1 s: ring k lands at +0.1·(k−1) s (1,780 tiles).
  The runtime drops sounds and effects more than 2 tiles from us, so live only the action 18 (`animation` event) and
  the bedrock's direction reach the runner.
- **Shape:** a 90° cone, 8 tiles long, along its facing. Every tile within 45° either side counts, edges included:
  facing a cardinal way it is a triangle, a diagonal way the whole quadrant. 1,720 of 1,780 wave tiles fit
  (`nav.in_cone`). Walls clip it. It passes through mobiles.
- **Its aim:** it faces what it fights: the tank, the creature it was provoked onto, or, while closing in, the way it
  walks. Its 0x77 direction pointed at that target every time.
- **Damage:** Logan 29-48 (median 38, n 6), the pets 31-47 (median 40, n 48), with no fall-off with distance. Logan was
  hit 6 of 6 times he stood in an unprovoked bedrock's cone, and 0 of 20 times he stood outside it.
- **Provoked, it sprays only its provoke target:** 251-296 on the grouper (49 after that grouper's own barding break).
  11 of ours standing in those waves took nothing. Normal sprays came back once the target died, or after a barding
  break right after the provoke (10-25 21:31:38.57). Peacemaking stopped it (one 16 s gap). Discordance did not.
- **Cadence:** 7.52-7.94 s apart (median 7.77). The first came 1.3-2.7 s after it went to war.
- **Its melee:** action 10 every ~3.4 s (3.33-3.5 clean), about 1 s later when a spray falls between. It hit the tank
  for 20-33 (n 9) and never hit Logan. calibrate's 2.01 s and 21-40 mixed the spray in.
- **Where the user stood:** while an unprovoked bedrock was awake within 12 tiles (167 one-second samples), Logan was
  beside it 56 %, in the cone 28 %, strictly behind it 9 %, and in front beyond 8 tiles 8 %. So "behind" meant off the
  line from the bedrock to the tank. The pets were in its cone 81 % of the time: they are what it fights.
- **His provocations:** the bedrock and the grouper beside it (ironscale or clearwater) onto each other: 6 successes, 3
  fails, 3 "too far". On level 2 on 10-26 (no bedrock): 2 provocations succeeded (combustive zoa onto lighthouse
  shellpod; voidfin onto biolumen shellpod), plus 24 Discordance and 17 Peacemaking. The pets were mostly on 'All
  Guard Me' (27), with 'All Kill' 7 times.
- **A spin on the fish:** voidfin, razorfin and frenzyfin (and the greater razorfin on level 3) all have it. Sound
  0x090A on its tile, its facing turning through all 8 directions, then 0.27-0.36 s later a hit on every one of ours
  within 2 tiles. It comes every ~7.7 s: voidfin 7.82 (21-58, median 40, n 58), razorfin 7.68 (9-36, median 28, n 33),
  frenzyfin 7.56 (9-30, median 22). No usable warning.
- **The ironscale grouper's '*deflects*'** (once, 22:45:41) reflected SirGrundy's arrow; it doesn't touch a tamer.
- **Built 2026-10-26:**
  - Simulator: the bedrock's `spray` special and its 3.4 s melee; the fish spins as `burst` specials with `id` "spin".
  - Runner: the cone of an awake bedrock that is neither pacified nor provoked onto a live monster is an area zone
    (`tamer.Team.spray_cone`). The stand-off steps out of it at once on the bedrock's action 18 (`spray_due`).
  - Provocation first when two monsters are in the fight (`PROVOKE_FIRST`; docs/SIM.md "Level 2's bedrocks").

## Tidal Tomb: level 3's west entrance and the boss approach (2026-10-27 22:10-22:59, capture 20261027_220057)

The user played it by hand. All C2S came from `client`; the runner never ran in this capture. The other two new
captures have no Tidal Tomb time: 20261026_230638 is Outland Dan mining and 20261027_230843 is Satanic Panic. The
scratch analysis is not in the repo.

- **Windows:**
  - W1 22:10:06-22:13:25, level 1 only.
  - W2 22:14:07-22:34:05: L1, L2, then L3 by the new entrance, up to the boss approach. He left by a new crimson gate
    on L3.
  - W3 22:43:47-22:59:07: L1, L2, L3's old arrival for 28 s, then back to L2 and out by L2's gate. GraveRobber and
    AlaFlambe were with him throughout.
- **A second level 3 entrance:** a step N from (6821, 1910) in level 2's north-west corner lands at (7092, 2193, -38)
  (22:21:37.30). By a map flood fill it is the same level 3 as (7162, 2221), 70 tiles west of it. A Recovery Dolphin
  0x00004849 stands at (7088, 2185). The way back is taken to be the arrival tile [INFERENCE: not stepped].
- **Level 3 has its own crimson gate:** 0x4003D7BA at (7107, 2055, -30), in its middle room. He left by it at
  22:34:05 to Totem (3884, 2901, -40).
- **The boss approach:**
  - The way in: a step N from (7104, 1945) at level 3's north end lands at (7321, 2167, 20); a step S from
    (7321, 2166) goes back.
  - Dolphin 0x00004932 stands at (7321, 2154). The wiki puts dolphins at level 3's entrances and at boss and
    mini-boss rooms (#4645).
  - He stepped 8 tiles in and came back 3.4 s later. No boss was seen; the pets stayed on L3.
  - The map: a corridor x 7320-7335 y 2105-2167, a large chamber x 7329-7400 y 2060-2108 (the likely boss room
    [INFERENCE]), and an east corridor x 7374-7395.
- **Monsters:** every type seen is in the wiki kb. Eight level 3 types are new to the scenario's spawns: colossal
  scourgeon, drowned buccaneer, drowned swashbuckler, giant kelpdweller, giant toxic zoa, greater frenzyfin, greater
  voidfin and tidal krait. The idle-sighting rule added 35 spawns (L1 2, L2 10, L3 23).
- **Damage** (on Logan / GraveRobber / AlaFlambe):

  | level | time | Logan | GraveRobber | AlaFlambe |
  |---|---|---|---|---|
  | L1 | 295 s | 296 | 1,352 | 359 |
  | L2 | 1,186 s | 912 | 6,283 | 3,634 |
  | L3 | 773 s | 377 | 8,586 (39,976 an hour) | 4,145 |

  GraveRobber died three times and was resurrected each time: twice on L3 to the giant combustive zoa (22:27:43,
  22:28:15), once on L2 to an ocean bedrock under Festering Wound (22:55:04).
- **Pressure** (buff icon 286):
  - A hit adds a stack. Exactly 30 s after the first stack (30.0-30.15 s, n=11) every stack lands at once as equal
    numbers, and the buff goes.
  - A colossal scourgeon's hit on every pet within 2 tiles, every 7.5-7.9 s: 67 a stack.
  - A tidal sea serpent's burst: 67 a stack.
  - A giant pincer crab's pinch: 35 a stack on the pets, 10 on Logan ('Claws pinch you in place, piercing your armor
    and applying pressure buildup!', with an entangle: 6 s, "Tidal Tomb: level 2 death 2026-10-30 18:29").
  - A scourgeon (L2): 32 a stack.
  - On L3 Pressure was 1,474 of the pets' 12,731 damage. A pet that dies loses its stacks.
- **Other specials:**
  - The giant toxic zoa: Lethal Poison on every hit (icon 134); pets tick 41 every 5.0 s.
  - The greater frenzyfin: a spin (sound 0x090A twice), plus '*frenzies*' rounds every 15-22 s.
  - The drowned swashbuckler: '*ripostes*' every 8.5-10.2 s, and '*adventurers rope*' (a jump to its target 6-8
    tiles off).
  - The drowned buccaneer: a bleed on both pets at once (seen once).
  - L2's biolumen shellpod: Festering Wound, a healing penalty ('Searing light burns your skin!').
  - L2: Glowmark (more aggro), from a lighthouse shellpod or the soulcatcher beside it.
  - The razorfin: a bleed of 9-10 every ~3 s.

## Tidal Tomb: overweight on the leave, the loot guard dead (live 2026-10-28 20:08-20:11, hunt-20261028-184111-a3b8)

Level 1 shift, pets AlaFlambe (the tank) and Bussin. Visit 5, 40,369 gp carried (visits 1-4 left at 43-51k gp by the
return rule, never by weight):
- 20:08:19 "LEAVING by the moongate: taking the loot home: the pack is full (447/450 stones)" (`hunt_return`: weight
  >= limit - `LOOT_WEIGHT_MARGIN`). The walk out was attacked twice; the walk-in fight loots its kills, and four corpses
  were looted at 20:08:52-20:09:35 (+387, +451, +377, +425 gp and items).
- The server then said "Weight limit exceeded (454/450)" .. "(462/450)" and refused every step: 66 `walk_deny` in
  20:08:15-20:11:20, with "You are carrying too much weight and are unable to move (461/450)" / "(462/450)" 58 times
  (the store's speech_heard). The runner read them as blocked moves: "move N from (x, y) blocked" in all 8 directions,
  shoves denied at stamina 0-1/25, stamina potions to shove, rope jumps refused (cliloc 500237), then "too many
  blocked moves (21 in this walk)", the escape's walk to the gold moongate the same, and the user paused the gate at
  20:11:14.
- Cause: the loot guards (`HuntLoop.loot`'s corpse skip and its per-item room check) and `ctl act loot`'s stop read
  `me["weight_max"]`, but the world model has it only under `me["stats"]`; both were dead since they were written
  (2026-10-15 fa8db6b): no hunt log ever has "no room for" or "not looting the". The return rule read it right.
- Fix: `loop_hunt.weight_max` (and ctl) read `stats.weight_max`; test_loop_hunt `weight_guard` (fails before: two
  lifts, the second corpse opened). Not done: a reading of "unable to move" in the walker to drop loot.

## Backlash: the astral zoas fizzle the casters (2026-10-27)

User 2026-10-27: "The giant astral zoa really sucks because our phoenix gets the Backlash debuff that nerfs its
spellcasting. Maybe we should just avoid it when our pet team relies on caster(s)." Read from every Tidal Tomb capture
with an astral zoa fight (20261024_102346, 20261024_225113, 20261025_210102, 20261026_183552, 20261027_220057), all
hand play. The scratch analysis is not in the repo.

- **What it is:** Outlands buff 0xFF sub 8, icon 17, cliloc 1110022 "Backlash", "Chance to Fizzle on Spellcast". It
  is sent for pets and other players' pets too. Each stack's value is its fizzle chance: 0.2 from an astral zoa
  (level 2), 0.3 from a giant astral zoa (level 3).
- **Stacks:** every hit adds its own 30 s timer. Up to 6 were seen at once; the wiki's "Stackable: No" is wrong. On
  Logan: "Astral backlash impacts your ability to concentrate while casting!", and "You are no longer suffering spell
  backlash." 30.0-30.1 s after the last one.
- **Which attack:** only the zoa's big spell. The giant's: 70-94 on pets, 35-56 on Logan, every 5.6 s (median), the
  first 3.1-3.5 s into the fight. The level 2 zoa's: 42-60 on pets, 35-42 on Logan, every 7.35 s. Every 70+ number
  from the giant carried it (92 of 92); its volley and single spells almost never did (2 of 120).
- **Whom it hits:** one victim and whoever stands within 3 tiles of it, not a ring round the zoa. In 70 attacks the
  victims were never more than 3 apart, and 101 of 107 we had near it that weren't hit stood 4+ from the nearest
  victim. With the phoenix team the giant's spell went on the tank 19 times and on the phoenix 8.
- **The effect:** every caster pet fizzled only under it (imp 78, fireminion 63, AlaFlambe 54, the Nightmares 57; 0
  without). A fizzle shows as effect 0x3735 on the pet about 1 s after its cast animation. Each stack rolls its own
  chance, 1 - (1 - v)^n: 52.4 fizzles expected against 54 seen, better than a flat or summed chance.

  | AlaFlambe in Tidal Tomb | casts | fizzled | damage a cast | median gap between casts |
  |---|---|---|---|---|
  | under Backlash | 140 | 54 | 383 | 1.62 s |
  | without | 2,480 | 0 | 629 | 1.60 s |

- **A fizzle costs the whole cast** and changes nothing else: the next cast comes as usual. Epic Barrage never
  fizzled [INFERENCE: abilities aren't spellcasts].
- **The giant fights, phoenix team:** 111.2 s with the phoenix hit 7 times (25 fizzles), against 47.0 s with it hit
  once (3 fizzles).
- **Logan:** he cast no spell there, so its effect on a spell recall is unmeasured (charge recalls aren't spellcasts
  [INFERENCE]). His bard skills weren't touched: 17 of 21 succeeded under it.
- **On 10-27 he avoided the giant.** Another player killed the one at (7081, 2039). At the old L3 arrival he stood 15
  tiles from the one at (7158, 2197) for 28 s and left. Giant astral zoas have been seen at (7156-7158, 2193-2198),
  (7081, 2039) and (7221, 2163).

## Bard rates and cooldowns live (2026-10-27)

User 2026-10-27: does the simulator model the provocation chance right, and how does a discord before a provocation
compare with provoking first? His hypothesis: "the higher fail rate and longer cooldown on provo means we could leave
our pets fighting undiscorded mobs too long if we focused on provo first." Scanned: every capture from 2026-10-10,
8,534 bard uses in the Mausoleum and Tidal Tomb, 5,299 of them answered with a known effective skill. The scratch
analysis is not in the repo.

- **Rates live vs the simulator:**

  | skill | live | sim before | verdict |
  |---|---|---|---|
  | Discordance | .718 (2,629/3,661) | .721 | right |
  | Peacemaking | .666 (862/1,295) | .677 | right, but Tidal Tomb .600 against .688 (its types at the medians) |
  | Provocation | .615 (211/343) | .518 | too low (z +3.7) |

- **The provocation fit:** a Provocation's d is the mean of the two creatures' Discordance d plus 31.5 (343 answers,
  log-likelihood -213.8). The per-type provoke fits gave -228.8 and made it fail too often (177.7 expected ok against
  211). Most provocations live came after a discord: the +25 barded bonus. Provoking an undiscorded pair at E 123 is
  only about .41-.47.
- **Fail answers:** "fairly reasonable" 972 / 433 / 118 (discord / peace / provoke), "easy" 73 / 5 / 2, "somewhat
  challenging" 0 / 3 / 14. Also 237 "cannot be seen", 75 "too far" and 253 `*barding break*` overheads.
- **Cooldowns: provocation's is not longer.**
  - The skill timer is 5.0 s from the target answer, for all three skills, success or fail. The next use was
    accepted from 4.98 s; 500118 refusals came up to 5.0 s.
  - The calm timer blocks only Peacemaking and Provocation: about 10.5 s after a success (10.42-10.56) and 5 s after
    a fail.
  - After a "barding break" refusal there is no timer: the next use went through 1.1-3 s later.
  - A provocation takes about 0.4-0.5 s longer from use to answer (two cursors).
- **The runner counted its 5 s from the use, not the answer:** 20-31 % of its next tries were refused (500118) and
  lost 1 s each.
- **Durations:** discord 65 / 68 / 71 / 74 s at E 108 / 113 / 118 / 123; peace and provoke 42-45 s barded. These
  match the simulator.
- **A failed provocation draws nothing:** the incited creature newly hit Logan after 1 of 132 fails, against 26 of
  1,032 failed discords. The simulator had turned it on Logan.
- **A discord's chance gets no +25 on a provoked creature:** 34 of 47, against 33.7 expected without the bonus and
  44.0 with. Its longer duration stays.
- **A provoked pair fights briefly:** first hit a median 1.8 s after the answer, last 7.3 s. One of the two is gone a
  median 6.4 s after (181 of 211). Their hits are big: median 134 in the Mausoleum and 185 in Tidal Tomb, about 7.1
  times the attacker's hit on a pet (the simulator used 4).
- **Barding breaks:** provoked creatures broke at about 0.5 times the expected (difficulty / 100) % a second (36
  against 72.6), pacified ones at 1.55 times (115 against 74.4).

## Five more Tidal Tomb specials (2026-10-28, from the five Tidal Tomb captures)

User 2026-10-28: can the swashbuckler's rope, the buccaneer's bleed, Festering Wound, Glowmark and the razorfin's bleed
be modelled? Read from captures 20261024_102346, 20261024_225113, 20261025_210102, 20261026_183552 and
20261027_220057, all hand play. The scratch analysis is not in the repo.

- **The buff packets count stacks exactly.** The server sends pets' and Logan's buffs with one timer per stack:

  | buff | icon | value per stack | each stack |
  |---|---|---|---|
  | Bleed, "Damage Every 3 Seconds" | 21 | razorfin 12, greater razorfin 16, buccaneer 24 | 5 ticks 3.0 s apart; stacks run side by side |
  | Festering Wound, "Healing Amount Reduction" | 234 | 0.05 | 60 s; up to 8 at once; cleared on death |
  | Glowmark, "Draws Additional Aggro" | 235 | 0.25 | 30 s; up to 4 at once |

- **The fins' bleed rides on their spin, not their swing.** Razorfin: 57 of 57 spin hits started a stack. Logan got 7
  of 7 with 'Razor-sharp fins cause you to bleed!', and the 35 that hit an already-bleeding victim each added a new
  stack. Plain swings started one in 4 of 82 (5 %).
  - Ticks: 9 on the pets, 8-10 on Logan. 'You are no longer bleeding.' came 15.0-15.1 s after his last stack.
  - The greater razorfin's spin: 45-78 on the pets, every 7.77 s, 13 of 13 hits bleeding, 5 ticks of 18.
  - The scenario's old greater razorfin bleed (18 every 1.27 s on every hit) was these stacks overlapping.
- **Drowned swashbuckler, '*adventurers rope*' (n=2):**
  - Both came 0.43-0.48 s after it went to war, its target 6-7 tiles off. 0.41-0.43 s later it stood on the tile its
    target held at the rope.
  - None in ~45 s with its target beside it, and never twice: the cooldown is unmeasured (at least 20 s).
  - The overhead text marks a rope; animation 9 alone is also an ordinary swing.
- **Drowned buccaneer (one fought, n=2):** its animation 14, then 0.38 s later a bleed stack on both pets beside it at
  once, with no number. Logan 6-7 off got none. Twice, 7.66 s apart. Ticks 18, 5 a stack. No shot of its own was seen
  in the 37 s fight.
- **Biolumen shellpod, Festering Wound:** 65 applications by its barrage. Its animation 4 is followed 0.23-0.75 s
  later by 1-2 numbers on its target and a stack.
  - Numbers: pets 11-30 (median 17), Logan 20-24. Every 7.93 s (median), 0-8 tiles.
  - The cut can't be measured: the gravebug's devour heals looked uncut, the vet's came to 0.93 ± 0.04 under ~3.3
    stacks (5 % a stack predicts 0.84), and Logan's two potions under one stack showed nothing. The knowledge base has
    nothing either.
- **Glowmark is the lighthouse shellpod's, not the soulcatcher's:** 26 of 26 marks followed the lighthouse shellpod's
  animation 4 by 0.24-0.64 s, its numbers with it (pets 14-27). Every 8.18 s (median), 1-7 tiles.
  - Its effect on targeting couldn't be seen: the marked pet was already the tank in 8 of 9 windows (GirthyEarthy
    took 73 % of the hits marked, 72 % unmarked).
  - Logan, marked at 2026-10-27 22:51:48, took nothing in the next 30 s.
- **Modelled 2026-10-28:**
  - Stacked bleeds: `bleed` with `stack`, and a `bleed` rider on a burst.
  - The rope kind.
  - Bursts with no number (`damage_pet` null): the buccaneer's bleed and the shellpods' barrages, whose numbers are
    already in their swings [INFERENCE].
  - The `festering` debuff (heals x (1 - 0.05 a stack), devour exempt) and the `glowmark` debuff (threat the marked one
    draws x (1 + 0.25 a stack)): the buffs' own values, since the captures can't size them.

## Tidal Tomb: the clearwater grouper's bubbles (2026-10-28, from five captures)

User 2026-10-28 ~21:15, live run hunt-20261028-211014-9150 (Logan with Bussin and AlaFlambe, level 2): "Standing in
the clearwater group's exploding bubbles is no good". Read from captures 20261024_102346, 20261024_225113,
20261025_210102, 20261027_220057 and 20261028_182147: every capture with a clearwater grouper in a fight. There were
36 volleys and 282 bubbles. The scratch decoder (`world.parsers` and `world.runtime.WorldRuntime` replayed over the
jsonl) is not in the repo. Neither the knowledge base (`tidal_tomb_kb.json`: the grouper's `specials` is empty; its
mechanics have only the decorative "Bouncing Air Bubble" loot) nor the wiki names the ability.

- **The throw:** 0xC0 type 0 (moving), graphic 0xDD8F, hue 0, speed 1, duration 0, source and target serial 0. It
  flies from a tile beside the grouper to the bubble's tile. In all 36 volleys the throw's origin was 1 tile from a
  clearwater grouper; none came from an ironscale grouper on its own. The ocean bedrock's spray wave uses the same
  moving graphic (above), so the throw alone doesn't identify a bubble.
- **The bubble:** 0.06 s after the throw, a ground item (0xF3) with graphic 0xDD8F and **hue 0x0B40** appears on that
  tile. Re-sends of the same serial step its graphic through the frames 0xDD8F-0xDDAE (~0.03 s a frame) and loop. It
  is deleted (0x1D) **1.88-2.04 s after it lands** (median 1.94, n=282): that is the pop. The 0xDD8F-0xDDAE range with
  hue 0x0B40 appeared only as bubbles: 3,640 updates in 20261024_102346 alone. With hue 0 it appeared 1-45 times a
  capture, never as a bubble.
- **The pop:** sound 0x09CA on the bubble's tile. Each mobile hit gets sound 0x09C8 on its own tile, then its number.
  A splash, 0xAB7C hue 0x07C9, is drawn a tile south-east of the bubble; 0x7B5C hue 0x0B3E and 0x122A-0x122F appear on
  the victim's tile.
- **Reach: 1 tile.** Of the 52 hit sounds, 32 were on the bubble's tile and 20 one tile off. None were 2 or more off:
  0 of 22 times for Logan and 0 of 48 for the pets standing 2 off.
  - Not everyone within 1 is hit. Of us not hit by a bubble in the 2.5 s before, 29 of 39 on the tile were hit, and 21
    of 50 one tile off.
  - Nobody was hit twice in a volley: 0 of 50 within 2.5 s of their last bubble hit.
  - Monsters beside a bubble take its pop too (0x09C8 on a giant pincer crab's tile, 10-24 11:09:37.88).
- **Damage:** Logan 31-45 (median 41, n=6), the pets 34-49 (median 45, n=15).
- **Volleys:** 8 bubbles in 34 of 38 volleys, thrown 0.1 s apart (a volley spreads over 0.59-0.92 s).
  - Tiles 0-4 from the throw's origin: 7, 40, 90, 89 and 61 bubbles at Chebyshev distance 0, 1, 2, 3 and 4.
  - Volleys come every 7.5-9.0 s (median 7.9, n=25).
  - The first came 1.2-6.1 s after the grouper went to war (10 of 11 groupers; one at 9.9 s).
- **The live hits (capture 20261028_182147, the grouper 0x000E1FED):**
  - 21:13:35, -44: on the bubble's tile (6951, 2015).
  - 21:13:43, -34: one tile off.
  - 21:13:50.85, -40: the bubble at (6950, 2011) beside his loot tile (6950, 2010). The hunt log 21:13:47-51 has "to
    the corpse: looting from (6950, 2010)" and the loot. The bubble had shown at 21:13:48.91, 1.94 s before.
  - 21:13:58.75, -42: the bubble at (6949, 2010).
- **Not the bubbles: the 0x36D4 / 0x3709 bursts of hue 0x0B16.** At 21:13:21 a moving 0x36D4 went from (6948, 2010) to
  the tiles round it, and a fixed 0x3709 landed on each. (6948, 2010) is the tile of the ironscale grouper AlaFlambe
  fought; Logan stood 16 tiles off. The same pattern comes from the tile of whatever the phoenix fights: ruby
  smallshells and dungeon-nest crabs on level 1, 4,572 of them in 20261028_182147. It appears only in captures with
  AlaFlambe out (12,327 in 20261024_102346, 1,128 in 20261028_165135; none in 20261024_221001, 20261026_105736,
  20261026_213634 or 20261027_230843). Its hue 0x0B16 is on the phoenix's own 0x374A effects too. [INFERENCE] It is
  the phoenix's area attack, not a monster's.
- **Built 2026-10-28:**
  - Runner: a bubble's reach (`tamer.Team.bubble_tiles`) is fire for the bubble's life (`fire_tiles`). It is never
    a tile to step to, loot from or heal from, and routes go round it. Standing in it, `Team.out_of_bubbles` steps out
    at once, from `stand_off` (mid-fight, mid-channel, before a potion) and between a loot's lifts.
  - Simulator: the grouper's `bubbles` special.
  - docs/HUNT_LOOP.md "Fire and bubbles on the ground"; docs/SIM.md "The clearwater grouper's bubbles" (the
    numbers).

## Tidal Tomb: a pincer crab's corpse is always empty (2026-10-28)

User 2026-10-28: "'a pincer crab' is a mob summoned by the dungeon-nest crabs and its corpse cannot be looted, but the
runner keeps stopping for a while trying to loot them".

- All 245 pincer crab corpses the hunt runner opened (every `logs/tasks/hunt-*.log` through 2026-10-28) gave "looted
  0 item(s) from a pincer crab: +0 gold".
- The open is answered (capture 20261028_182147, 22:08:48.79: 0x06 on corpse 0x4483F872, then 0x24 and a 0x3C of 0
  items). The runner's open waits for contents that never come (`CONTAINER_WAIT_S` 3 s) on top of the walk and the
  use pause: ~4-5 s a corpse (live 22:08:47 -> 22:08:52 -> 22:08:56, two of them).
- The giant and colossal pincer crabs aren't broodlings and drop gold as usual.
- Built: the runner never queues the corpse (`loop_hunt.EMPTY_CORPSES`); the simulator's pincer crab drops no gold
  (`gold_observed`).

## Tidal Tomb: Logan in the bedrock's spray (2026-10-28)

User 2026-10-28 ~22:20, live run hunt-20261028-215803-e48f (level 2, pets Bussin 0x000EA04F and AlaFlambe 0x0026EF56):
"Logan is still standing directly in ocean bedrock spray. Figure out why". Read from capture 20261028_182147 (a
throwaway replay: each bedrock action 18 with the bedrock's tile and 0x77 direction, Logan's tile and his hits 0.5-1.8 s
later) and the task log. The runner's level 2 runs in it: hunt-20261028-211014-9150 and -215803-e48f; no earlier capture
has the runner on level 2 (18:35:23 and 18:35:38, 2 hits in the cone at 7 and 5, were the user's hand play).

- **The runner's sprays:** 9 wind-ups 21:15-22:20. Logan stood in the cone at 5 of them and was hit at 3: 22:04:46.92
  (-42), 22:18:04.08 (-42), 22:18:12.25 (-34). In the cone at 6 and 7 without a hit: 21:15:11.57 (the walk to the
  moongate) and 22:13:58.12. Out of it at the other 4 (22:04:54, 22:19:52-22:20:16): no hit.
- **The bedrocks walk at their target.** 0x000EAEEF went (6953, 2057) -> (6955, 2055) -> (6958, 2048) from 22:04:44 on,
  war mode (flags 0x40) from 22:04:46, facing Logan; 0x000ED280 came (6955, 2056) -> (6950, 2059) -> (6948, 2060)
  22:18:01-05 at the pets.
- **22:04:46.92, gap (a), confirmed: the patrol walked back into the cone.** "holding here: ocean bedrock in sight at 10
  tiles: letting it come" (22:04:44) ended when it woke: the hold was for idle monsters only (`hold_reason`,
  `View.wakes`). Then "patrol to (6955, 2055): route 7 steps" (the waypoint beside it, in its cone), one step to the
  pull range ("a target in reach"), the stand-off back out to 9 ("stand-off to (6956, 2047): 1 step(s)"), the patrol
  in again, twice. Nothing kept a walk's route out of the cone: its tiles were in the zone at 1 (`area_zone`), and
  `around_areas` puts only reaches over 1 among the Mover's dangers.
- **22:18:04.08, gap (b), confirmed: a 5-step stand-off down the cone.** The bedrock at (6949, 2059) facing west at the
  pets 3 in front of it, Logan 7 down its cone at (6942, 2060). "stand-off to (6947, 2056): 5 step(s)" at 22:18:03:
  `standoff_tile` ranks the steps last, after the idle eyes and the vet reach of the pets, and its walk had no stop at
  a wind-up. The wave came mid-walk, -42 at 22:18:04.97. Two steps west put him 9 off.
- **22:18:12.25, gap (c), confirmed: the loot walk into the cone.** The shellpod's corpse at (6944, 2060), the bedrock
  at (6948, 2060) facing north-west. Logan at (6949, 2056) was out of it; "to the corpse: route 4 steps", arrived at
  (6945, 2059), 3 down the cone, -34. Every tile within 2 of the corpse that he could reach lay in the cone; the ones
  out of it are wall. `loot_tile`'s "every tile in reach" check counted the wall tiles as clear, `_reach_tile` found no
  clear tile, and None fell to the walk "as before".
- **Proactive positioning:** `vet_tile`, `spacing` and `standoff_tile` never chose a tile in the cone (it is in the
  zone), but the walks to them went through it. In the simulator (48 level 2 runs, before) 35 of the 55 sprays on Logan
  came mid stand-off walk, 3 on a walk to a hurt pet, 1 off the tank, 1 to the corpse, 9 while barding.
- **Built 2026-10-28:** the cone in the Mover's `avoid` tiles for every walk; a wind-up's stop on the walks and the
  fewest-steps tile out; the hold for an awake bedrock coming on; the loot wait. docs/HUNT_LOOP.md "Pets" (the
  rule), docs/SIM.md "Tidal Tomb: Logan in the bedrock's spray" (the numbers), docs/PLAN.md (the decision).
- **The pet cost, 2026-10-29 (simulator only).** The change doubled pet deaths in its own 96-run batches (9 -> 18,
  12 -> 19). The cause is read from 384-run batches of level 2 on HEAD (seeds 1-384), one fbf76ab piece turned off per
  row, with each pet death's new readout (`World.die`: who hurt it and what healed it in its last 15 s, Logan's
  distance, who fought it, the runner's intents):

  | fbf76ab on, except | spray hits on Logan | pet deaths |
  |---|---|---|
  | nothing (HEAD) | 282 | 88 |
  | all of it (the baseline) | 493 | 73 |
  | ties to the lowest tile, as before | 242 | 53 |
  | `SPRAY_WARN_S=0` | 420 | 54 |
  | no `Mover.avoid` cone | 281 | 93 |
  | no hold for an awake bedrock | 317 | 85 |
  | no `spray_stop` / `spray_wait` | 299 | 94 |
  | no fewest-steps tile at a wind-up | 345 | 65 |
  | `SPRAY_AHEAD=0` | 392 | 61 |
  | ties to the highest tile / the walk order's last / a hash / nearest the pets | 268 / 254 / 276 / 249 | 77 / 78 / 73 / 84 |

  - The pets took 34-36k a hunt hour in every row: no piece hurt them more. Their deaths came at the level's monsters
    (voidfins, giant pincer crabs, drowned mariners, groupers) with Logan mostly beyond the vet supplies' reach (54-77 %),
    rarely near a bedrock (its damage in the last 15 s at 2 of HEAD's 88).
  - Some of the cost is exposure: Logan died less, so the runs hunted longer (155.2 h against 150.4).
  - **Cause:** fbf76ab dropped the tile from `standoff_tile`'s ranking, so a tie went to `tiles_within`'s walk order
    (north first) in every stand-off. Before, it went to the lowest (x, y, z). Nearly every stand-off is a tie on the
    idle eyes (1,376 of 1,396 "seen by 0 idle"). Only the pieces that change the stand-off's tile moved pet deaths. The
    tie order alone spans 53-88; [INFERENCE] the logs show no mechanism for it.
  - **Fixed 2026-10-29:** ties to the lowest tile again (`test_standoff_ties`). Over 768 runs (seeds 1-768): spray hits
    on Logan 952 -> 472 against the baseline (fbf76ab alone 575), pet deaths 153 -> 123 (fbf76ab alone 175), banked an
    hour 108,583 -> 110,349 (fbf76ab alone 101,119). docs/SIM.md "Tidal Tomb: Logan in the bedrock's spray" has the
    full tables.

## Tidal Tomb: dead on level 1 on the way out (2026-10-28 22:53)

Run hunt-20261028-222328-f677 (Logan with Bussin and AlaFlambe, level 1, `--vet-range 5 --heal-in-place`), capture
20261028_182147. The user asked to go home; the Seer sent `stop --after-trip`; Logan died at (6673, 2036) at
22:53:21.93 ("ABORTED: died (unknown): ghost body (leaving: done)" at 22:53:26). The pets lived. The capture replayed
through `world.runtime.WorldRuntime` (scratch scripts, not kept):

- **22:53:00** "holding here: lowtide scavenger in sight at 10 tiles: letting it come" (0xF2544, idle at (6679, 2029)).
- **22:53:01** "LEAVING by the moongate: done", "All Follow Me" (01.70; the pets' war flags off at 02.07). The
  scavenger went to war mode at 04.49, 10 tiles off. "to the moongate: route 90 steps" at 06. The route runs north-east
  first (the simulator's map: (6670, 2039) -> (6679, 2030) in 9 steps, 91 to the gate): up the scavenger's way.
- **22:53:07.45-08.80** three steps north-east to (6673, 2036); the scavenger came 6, 4, 3 tiles off meanwhile. The
  walk's stop knew only hits and swings ("under attack"). **Cause: the walk walked into a monster coming at it, the
  pets on follow behind Logan.**
- **22:53:08.80** the step's guard (`drink_on_the_run`) played the area peace "walking out past 1 in war mode within
  3": the skill at 10.07, its cursor at 10.80, the target at 13.59, "You play successfully" at 14.57 (6 s). Meanwhile
  -11 (09.18), the large swing -33 (10.34, Bussin -21 and AlaFlambe -24 beside him at once), -12 (13.59). Pacified
  for 2 s, but the walk stopped anyway ("attacked on the way out" at 17; no step after 08.80). **Cause: an area peace
  meant for walking on through, on a walk that stops for what attacks it.** [INFERENCE] The 2.8 s from cursor to
  target and the 2.5 s from the play to the stop are runner latency (pauses or a busy machine); nothing in the log
  names them.
- **22:53:17.5** -15; "All Guard Me" 17.60 (the pets in war mode 17.85). A dungeon-nest crab came beside him at 17.10,
  -12 at 17.92 (31 -> 19).
- **22:53:17.6-21.1** `walk_in_fight`'s first state read ran the guard again: a heal potion (click 18.20, +37 at
  18.81, then at once the scavenger's second large swing -27 and both pets -24 / -21: "heal potion: 31 -> 29"), then
  the vet supplies "Bussin at 89%" (click 20.73, begun 21.12) with Logan at 29, then 6 after the nest crab's Dig
  (18.84 '*digs*', gone 19.35, back on Logan's tile 20.38: -23 on him, -22 and -24 on the pets). **Cause: the guard's
  potion and vet supplies ran before walk_in_fight's own order (stand-off, retreat); the channel kept him standing.**
- **22:53:21.93** -8 (a pincer crab, anim 31), 7 -> 0. **22:53:22** "retreating 7 tiles from a lowtide scavenger, a
  dungeon-nest crab at 31/100 hits": decided on the state read at 17.6, before the guard's potion and vet (**cause:
  stale state**); logged after he died, so it never stepped.

The suspected gaps, checked:

1. **The walk walked into the coming monster: confirmed** (above; the route's first steps lead up its way).
2. **The large swing and the Dig are area hits and weren't in `tamer.AREA_R`: confirmed.** Measured over the six
   Tidal Tomb captures 20261024_102346, 20261024_225113, 20261025_210102, 20261026_183552, 20261027_220057 and
   20261028_182147 ("-N" numbers over each of us at the instant, positions from the replay):
   - **The large swing** (239 that landed on one of us, 0.46-0.52 s after '*takes large swing*'): everyone within 1
     of the tile it faces (its 0x77 direction; its target's tile): 306 of 314 of us in that 3x3 hit (the front tile 272
     of 277, its own tile 11 of 12), 0 of 137 elsewhere within 3 (behind it, 2 to a side, the 2-ahead corners).
     Logan 20-33 (median 27, n 9); GraveRobber 3-23 (median 17), AlaFlambe 4-34 (median 24), Bussin 5-30 (23).
   - **The Dig** (230 '*digs*' with animation 18): the crab is deleted (0x1D) at its broodling's 0.5 s and is back
     1.52-2.06 s after the dig (median 1.76, n 224) with animation 27 on its target's tile, 0-16 tiles from where it
     dug (median 6). Every one of us within 1 of it then is hit: 316 of 317 at 0-1, 4 of 48 at 2, 0 of 99 at 3-4.
     Logan 18-34 (median 28, n 89, 40 of them on his own tile: it came up on him); GraveRobber 4-22 (median 17.5).
     The scenario's note had called the wiki's Dig "not the broodling"; it is both.
3. **Stale state, wrong priority at low hits: confirmed, one cause.** The guard inside `HuntLoop.state()` acted for
   ~3.5 s and the state it returned was from before; the vet supplies on a pet came from the guard, ahead of the
   retreat. Refuted as separate bugs: "self before a pet" (one channel heals every pet in reach and us; the label names
   the first need) and "the retreat never moved" (Logan died at 21.93, before its decision was logged).
4. **Anything else on a stop mid-approach: the area peace on a planned walk out** (above). Not a runner decision: the
   "died (unknown)" label (no attacker in the ghost's state 4 s later).

- **Built 2026-10-28:** the planned walk out's stop for a monster coming (`HuntLoop.coming`, `COMING_R` 10), the stand
  going on while one comes; no `drink_on_the_run` inside `walk_in_fight` (`walk_fighting`) and no area peace on a
  planned walk out (`walk_out_fought`); a fresh state after a guard that acted (`HuntLoop.state`, the Mover); the
  large swing and the Dig in `AREA_R` (2 each). Simulator: the large swing on each of us within 1 of its target
  (Logan's own numbers), the Dig (`surface_s`, the crab's `dig` burst). docs/HUNT_LOOP.md "Out" and the area hitters
  (the rules), docs/SIM.md "Tidal Tomb: dead on level 1 on the way out" (the numbers), docs/PLAN.md (the decision).

## Tidal Tomb: level 2 death 2026-10-29 12:36

Run hunt-20261029-122604-bc07 (Logan with AlaFlambe 0x0026EF56 and Bussin 0x000EA04F, level 2, `--vet-range 5
--heal-in-place`, leave line 0.5), capture 20261029_100942, decoded with `replay.timed_packets` -> `WorldRuntime` plus
the raw 0x54 / 0x6E / 0xC0 (scratch scripts, not kept). Logan died at (6893, 2065) at 12:36:55.15 (t 1793295415.15).

- **12:35:30-12:36:47 Logan at 100/100 throughout.** The "100 -> 56" was one hit, at 12:36:47.02.
- **12:36:38.57** a combustive zoa 0x00070BB4 at (6885, 2068) in war mode. 12:36:41.70 a drowned mariner 0x00071482
  at (6900, 2067), war. 12:36:42 Logan "back behind the pets" at (6893, 2065): 8 from the zoa, 6 from the mariner, 3
  from the pets at (6895, 2062), who stayed there to the end (10 from the zoa, 4 from the mariner) casting at it.
  By the runner's sight (los.Sight) the zoa could not see (6893, 2065): "keeping out of the reach of the awake
  combustive zoa", then "every way on runs through ...; waiting out of it".
- **12:36:47.02 the zoa's scorch**: sound 0x0A70 on the pets' tile, 0x0A72 on each victim's tile, 'The creature
  scorches you and you become more susceptible to damage from them!', Logan -44 (100 -> 56), AlaFlambe -52, Bussin
  -50. Its 0.07 s earlier action 4 is its usual swing animation.
- **12:36:47.30** the vet supplies' click ("vet supplies (115 left): AlaFlambe at 92%"); **47.56** the heal potion's;
  **47.61** "You must wait to perform another action." (500119). Nothing was drunk; the runner logged "heal potion:
  56 -> 56 hits" at 12:36:49 and its potion clock counted a drink, so no potion went out again.
- **12:36:51.56** a lightning bolt (0xC0 type 1) on Logan, AlaFlambe and Bussin at once; 51.80 Logan -34 (57 -> 23),
  AlaFlambe -27, Bussin -19. **52.51** the vet channel's finish: Logan +30 (53), the pets +147 / +86. **53.10** the
  vet again ("us at 53/100"), +4, +4.
- **12:36:54.90** bolts on all three again; **55.14** the second scorch (8.12 s after the first, 0x0A70/0x0A72 and the
  same line): Logan -45 (62 -> 17), AlaFlambe -57, Bussin -56; **55.15** the bolts' numbers: Logan -18 (17 -> 0),
  AlaFlambe -74, Bussin -30. Dead.
- **Logan's 141 damage:** the zoa's scorch 89 (44, 45), its lightning 52 (34, 18). Nothing from the mariner reached
  him: its shots (moving effect 0x79C5 after its action 14/19) went to AlaFlambe. The "combustive zoa -108" the journal
  showed is the pets' damage on the zoa (10,000 hits; -1,008 and -1,046 in single numbers).
- **The bolts are the zoa's** [INFERENCE: by elimination, the only other awake monster was the mariner]: over the
  seven Tidal Tomb captures with the runner's era, a bolt on several of ours at once came only with zoas awake (astral,
  toxic, combustive, the giants); 2026-10-25 11:23:30 with a combustive zoa alone at this same spawn, three pets struck
  and Logan 6 off spared. The combustive zoa's: 4 volleys (Logan -33, -34, -18) in ~7 min of its fights. Not modelled.

**The scorch, measured** (captures 20261024_102346, 20261024_225113, 20261025_210102, 20261026_183552,
20261027_220057, 20261028_165135, 20261028_182147, 20261029_100942): 58 scorches of 14 combustive zoas, 50 of 5 giant
ones.

- It lands on the zoa's target, always a pet of ours (0x0A70 on its tile 89 of 89 times one of ours stood there),
  1-10 tiles from a combustive zoa, 1-12 from a giant one, and hits each of us **within 3 of that pet: 157 of 168
  there, 0 of 181 at 4+** (Logan 25 of 29 and 0 of 79). By distance from the target's tile: 0 122/127, 1 7/11, 2 8/8, 3
  20/22, 4-19 0/184. Not a ring round the zoa: the scenario's giant had radius 12 ("Logan at 8-12" was Logan within 3
  of the pets). Its sight of us doesn't matter (12:36:47 and 55).
- Every 6.4-10.1 s (median 7.4, n=40 gaps; the giant 5.6, n=44), the first 1.1-8.5 s after it went to war.
- Pets 40-70 (median 53), rising fight-long ("more susceptible": 46, 41, 50, 55, 63, 68, 70 on one pet; not
  modelled); Logan 30-48 (median 36, n=14; the giant's 32-50). A combustive zoa scorched Logan 14 times since 2026-10-24,
  twice in the runner's own run 2026-10-28 18:34 (-47, -48).

**Why he stood there:** the zoa's reach was its shot (a caster, `tamer.CASTER_R` 12, its sight of our tile), and the
runner's sight said it had none of (6893, 2065); no rule knew a hit centred on its target, so 3 from the pets was "out
of it". The astral zoas' spell has the same shape (docs/SIM.md "Backlash") and had no runner rule either. At 56 hits he
was above the leave line (0.5) and the retreat's (an attacker beside us); the potion was refused and counted; 23 hits
lasted 0.7 s, and the last 63 came in one instant at 62.

**The log spam:** "patrol to (6861, 2054): stopped (would wake 0.4 more onto 3.0 (budget 3))" 100 times at
12:36:42-43 and "patrol to (6904, 2065): stopped (would wake 2.7 more onto 2.5 (budget 3))" 329 times at 12:35:39-42:
with a fight on, the patrol's stop for an overpull has no other rule take it up, the waypoint was kept and the next
tick walked again at once.

- **Built 2026-10-29:** the scorch in the simulator (the combustive zoa's `scorch` burst, centre "target", reach 12,
  radius 3; the giant's ring of 12 corrected to the same shape); the runner's `tamer.TARGET_AREA` and
  `Team.target_zone` (each live pet in reach of an awake zoa a zone of 4: the stand-off, routes round it, and the
  cover, reposition and bard step clear of every area zone, not only its centres); the potion's 500119 clicked again
  (`loop_hunt.DRINK_TRIES`) and never counted as a drink; the crawl's hold on a patrol refused again
  (`Crawl.stop_last`). docs/HUNT_LOOP.md (the rules), docs/SIM.md "Tidal Tomb: the combustive zoa's scorch" (the
  numbers), docs/PLAN.md (the decision).

## Tidal Tomb: the zoas' lightning, the scorch's susceptibility, the shooters' hits (2026-10-29)

The three gaps 1ffae37 left ("not modelled"), and the level 2 shooters' real numbers, measured over the nine Tidal Tomb
captures (20261024_102346, 20261024_225113, 20261025_210102, 20261026_183552, 20261026_213634, 20261027_220057,
20261028_165135, 20261028_182147, 20261029_100942) with `replay.timed_packets` and `world.runtime.WorldRuntime`: every
effect, sound, animation, overhead number, buff (0xFF sub 8) and war flag while Logan stood in the dungeon, with the
mobiles within 20 of him at each. "Ours" is Logan and his named pets (other players' pets left out).

- **The zoas' lightning is every zoa type's spell, cast like their others.** Every zoa (astral, toxic, combustive and
  their giants) casts every 3.37 s (median of 498 gaps): animation 4, again 1.5 s later, the spell 1.2 s after the
  second (1.18-1.27 s). The lightning is one of its spells: bolts (0xC0 type 1, graphic 0, sound 0x29) on several of us
  at once and a number on each 0.24 s later, no buff (not the astral zoas' Backlash spell). 45 casts with one of ours
  struck. Before this the caster was [INFERENCE] by elimination; now the double animation-4 of one zoa 2.75 and 1.22 s
  before each bolt names it (live 12:36:51.56 and 54.90: the combustive zoa 0x70BB4).
- **Its shape: on its target and everyone of us within 4 of it.** For each cast the victims fit within 4 of one
  victim (r_in 0-4 in 45 of 45), none spared within 4 (the nearest spared 5-14 off, 31 casts with one spared); Logan
  struck 22 of 22 times within 4 of its target, 0 of 23 at 5+; four of four of us once (2026-10-24 11:33:17). Its target
  1-11 from the zoa; its sight of the others not needed (12:36 Logan 8 from the zoa and out of its sight, 3 from the
  pets). The giant astral zoa's "volley" (modelled as 3 of us at random within 12) was this: Logan "skipped at 5-10" stood
  5+ from its target.
- **Its numbers and rate.** Plain zoas: pets 21-33 (p10-p90, median 25, n=40; 74 on a scorched one), Logan 21-34
  (median 31, n=15; 18 at 12:36:55). Giants: pets 32-51 (median 43, n=39), Logan 26-33 (median 30, n=7). Rate against
  each zoa's own clock: the combustive zoa 5 per its 59 scorches (7.4 s apart: one every ~87 s), the giant 7 per 50
  (~40 s), the astral zoa 12 per its 36 astral spells (Backlash buffs on ours, 7.35 s apart: ~22 s), the giant astral
  14 per 72 (~29 s); the toxic zoas 5 and 2 seen, no clock of their own (the astral pair's rates [INFERENCE]). It comes
  in runs, every second cast (6.7 s apart) or the next (3.35 s: 12:36:51.56 and 54.90).
- **The scorch's susceptibility: +8 % a stack on what the combustive zoas do to its victim.** No buff icon comes with
  "The creature scorches you and you become more susceptible to damage from them!". A pet's scorches in a row (31
  chains, plain and giant alike) were x1.10, 1.17, 1.24-1.31, 1.37-1.45, 1.41-1.48, 1.52 its first after 1-6 before
  (medians): ~0.08 a stack. Gaps: still rising after 10.2-12.7 s (7 of 7), mixed at 14-20 s (4 kept, 4 back to the
  first), back to the first at 27-35 s (5 of 5): every stack refreshed by the next, gone ~17 s after the last
  [INFERENCE: 17 between 12.7 and 27]. A pet joining late starts at the first (GraveRobber 71 at the giant's 5th
  scorch, 2026-10-25 21:23:45): per victim, not a ramp of the zoa's. "From them" is the combustive zoas: the mariners'
  hits on scorched pets 22.4-25.0 (n=19) against 24.4 unscorched (n=127), the soulcatchers' 27-28 against 27.0; their
  other spells [INFERENCE: the lightning's few numbers on scorched pets, 27 then 74; the giant's single hits 40 -> 74].
  Logan's own rise is unmeasured (44 then 45 live, the giant's three runs of 3 noisy) [INFERENCE: as the pets'].
- **The drowned mariner: a shot every 4.0 s on its target and everyone within 1 of it, half of them missing.** 202
  shots (animation 12, 14 or 19), 4.00-4.07 s apart (p10-p90, n=169 gaps); 112 hit (sound 0x85B, the numbers at once),
  90 missed (sound 0x7D1, no number on anyone); both kinds send its 0x79C5 effect to the target 0.24 s after. On a hit,
  159 of 161 of us within 1 of its target took a number, 0 of 132 at 2+ (Logan 13 of 14 at 0-1, 0 of 79 at 2+); its
  target 1-11 from it. Pets 12-38 (median 23, n=146), Logan 15-39 (median 22, n=13). Logan was its target 3 times in the
  96 hits with him within 10 of it (2026-10-27 22:14:52, 2026-10-29 10:40:07 and 11). Its swing beside a pet (action
  20, every 1.77 s) left no number of its own. The simulator had it shooting one target every 1.57 s (its swing
  period) for 24-26, 1.6x that on Logan, and a fifth of its shots at a pet sent at him (`SHOT_AT_PLAYER_P`, fitted on
  the Mausoleum's casters).
- **The soulcatcher: a spell every 5.7 s on its target and everyone within 1 of it.** Animation 11, then 1.52-2.71 s
  later (median 2.16, n=98) sound 0x9FE on its target's tile and 0x8E4 with a number on each victim, no projectile;
  every 5.68 s (4.56-7.15, n=75 gaps). 98 of 106 of us within 1 of its target hit, 0 of 242 at 2+ (Logan 8 of 8, 0 of
  95); with one of us on the target's tile 75 of 82 took a number. Pets 23-38 (median 26, n=90), Logan 28-37 (median
  30, n=8); Logan its target 2 times in its 80 spells with him within 11 of it. Simulated before: one target every
  2.73 s, 37-48 on Logan.
- **Fins on Logan live: rare.** In all nine captures Logan took 6 numbers with a fin beside him (voidfin 27-40, one
  razorfin 33), while the simulator's level 2 deaths are now mostly fins on him (docs/SIM.md "Tidal Tomb: the zoas'
  lightning, ..."): how often they pick Logan live under the runner is unmeasured.
- **Built 2026-10-29:** all four in the simulator (harness/data/sim/tidal_tomb.json: each zoa's `lightning` burst,
  the scorch's `susceptible` debuff, the shooters' `ranged_observed` with `area_r`, `hit_chance`, `damage_player`,
  `at_player_p`; `World.hurt`, `_burst`, `_release`, `_impact`, `_shot_damage`), Logan's death readout (`World.die`:
  each hit in his last 15 s with its source, kind, his hits after and his distance to the hitter, the monsters on our
  side, the pets, the runner's last intents), and the runner rules in docs/HUNT_LOOP.md (the shooters' target areas,
  no zone for the lightning, a monster beside us the pets aren't fighting).

## Tidal Tomb: the oxygen leg's back-off loop (live 2026-10-29 18:13:00-18:14:29, hunt-20261029-180419-d505)

Level 2 shift, Logan with Bussin 0x000EA04F and AlaFlambe 0x0026EF56, `--vet-range 5 --crawl-learn-s 180`; the user
took over at 18:14:29 ("Logan's stuck in a loop"). Capture 20261029_100942, decoded with `replay.timed_packets` ->
`WorldRuntime` (scratch scripts, not kept).

- **18:13:00** "oxygen: to the teleporter up at 6943,2004: 470 s under water; 96 route steps", "fought through" (the
  oxygen rule's leg, docs/HUNT_LOOP.md "Oxygen"), 95 steps from (6918, 2039).
- **18:13:23-18:14:28** at (6948, 2034) the leg's walk stopped ("holding here: a soulcatcher would see us": `Team.
  pull_check`), and the next round's `Team.back_off` stepped 3 back to (6951, 2037), out of the sight of the idle
  soulcatcher 0x000B2A2F at (6942, 2023, -110), 11 off; the round after, the leg's route (32-33 steps) went the same 3
  steps back into its sight. 18 back-offs in 63 s (17 to (6951, 2037)), about 2.3 s a round: the back-off's intent to
  its first step 0.35-0.42 s, the leg's 0.70-0.74 s, three steps each way. The pets stood beside Logan the whole time
  (1 tile behind: (6948, 2035) at 18:13:23, (6950, 2037) at 25, (6949, 2036) at 18:14:24), so from (6948, 2034) he was
  nearer it than they were and "first" again. It stayed idle (no war flag) to the end; Logan 100/100; ~560 s under
  water at the stop. The stairs are 19 north of it: the leg has no way round its sight.
- **'All Patrol' at it refused twice** (18:13:25.48, 18:14:27.52): the target (6942, 2026, z -117, graphic 0), answered
  500237 "Target cannot be seen." in 0.06-0.08 s; `patrol_failed` then held it off for 60 s, and the back-off went on
  without a spot ("a patrol at it refused or timed out lately").
- **Why refused: graphic 0 names the land, and the land is under the stairs.** (6942, 2026)'s land is 0x244 at -128
  under the stone blocks 0x71E (-124) and 0x71F (-119, top -117). The runner's `patrol_spot` checks our eye to the
  top; the server takes a graphic 0 answer as the land at its own height [INFERENCE: RunUO's LandTarget, z from the
  map], as the songs on level 1 showed (`Team.song_spot`, 2026-10-26). By `los.Sight` the land is out of our eye's
  sight (blocked at (6942, 2027, -127)), the top in it. Over the level 2 captures (20261028_182147, 20261029_100942)
  20 ground clicks at graphic 0: 14 patrols "cannot be seen" (2026-10-28 22:10-22:21 four, 2026-10-29 12:30-12:36
  three, 18:08-18:14 seven), every one on a floor over the land 0x1DB/0x244 (-128, once -100) with that land out of
  sight; of the 6 answered, 5 had it in sight (the sixth, the 2026-10-28 22:01:52 patrol to (6944, 2025), grazes the
  neighbour's land at -127). The tops were in sight for all 20. Of 16 Tidal Tomb patrols, 2 went out. Within 3 of
  this soulcatcher on its stairs only five tiles (6942-6944, 2023-2024) have a flat floor static a click could name
  (0x522, 0x2198, 0x51B).
- **The crawl does the same** (not built, below): 18:09:06-43 13 back-offs from a drowned mariner 0x000B2103 while
  the crawl patrolled toward a frenzyfin past it, its patrol refused too; 2026-10-28 22:09:44-22:12:14 32 from a
  soulcatcher 0x000EBB06 at the same stairs, every way from the arrival's nook north of it running through its sight.
- **The simulator missed it**: its 'All Patrol' answer checked our eye to the stand point, so all 14 would have gone
  out; and its rounds came under `BACK_OFF_S` (2 s): the runner's planning takes real time live, none on the
  simulator's clock, so a round now and then found the back-off not due and the leg crept through.
- **Built 2026-10-29:** the simulator's 'All Patrol' answer by the ground point (`World._ground_point`, as the
  songs'); the runner's breaker: in a fought leg (`walk_in_fought`: the walk in with `--fight-via`, the walk to the
  moongate after `--leave-via`, the oxygen legs) the 4th back-off from one idle shy monster with no kill since the
  first goes on past it instead, the pets on guard ("…: backed off from the soulcatcher 0x… 3 times with no kill;
  going on past it with the pets on guard"; `tamer.BACK_OFF_REPEAT`, `Team.faced`). docs/HUNT_LOOP.md (the rule),
  docs/SIM.md "Tidal Tomb: the oxygen leg's back-off loop" (the test and the runs), docs/PLAN.md (the decision).
  Not built: the patrol's spot by the point the server checks (tried: no refused patrol, more of them out, the runs
  mixed; docs/PLAN.md), and a breaker on the hunt level (the simulator: more deaths with it there).

## Tidal Tomb: level 2 death 2026-10-30 18:29

Run hunt-20261030-180017-3421 (Logan with Bussin 0x000EA04F and AlaFlambe 0x0026EF56, level 2, `--vet-range 5
--crawl-learn-s 180 --heal-in-place`, `--leave-gate 6913 2071`, no `--fight-via`). Logan died at (6910, 2058) at
18:29:34.90 (t 1793402974.90) on the planned leave ("taking the loot home: 36150 gp carried ... >= V* 29764"); the pets
lived. Capture 20261030_175703, decoded with `replay.timed_packets` -> `WorldRuntime` plus the raw 0x2F / 0x54 / 0x6E /
0xC0 and the buffs (scratch scripts, not kept). No 0x0B: the numbers are the overhead "-N". Positions and war flags
are the world model's (Outlands' 0x77 and 0x78 aren't the stock layouts; its 0x20s carry the monsters' flags).

| time | what happened |
|---|---|
| 18:28:43.4-46.0 | "toward a target: route 138 steps": Logan ran 10 moves north to (6864, 2039) after the astral zoa 0x00185B47 the pets fought at (6855, 2064); they stayed on it, 12-15 off. 46: "left the client's world", "LEAVING by the moongate". |
| 18:28:46.8-54.6 | "All Follow Me" ("not waiting here": `Team.follow(wait=False)`, a planned leave's), then the walk's lag stop: "waiting for the pets (Bussin at 15)", Bussin came; "to AlaFlambe" (still fighting the zoa), "All Follow Me" again at 8 off. From 18:28:57 both pets 1-2 behind him. |
| 18:28:55 | "to the moongate: route 105 steps from (6863, 2049)". 18:29:03-22 into view, all idle: a biolumen shellpod 0x00185A11 (6896, 2039), a soulcatcher 0x00185447 (6911, 2043; 49 %), the giant pincer crab 0x00185B44 (6913, 2060) at 14.0, moving to (6911, 2060) by 16.9, a voidfin 0x00185F2F (6904, 2061), a drowned mariner 0x00185F18 (6902, 2068) at 22.4. |
| 18:29:15.7-25.9 | The shellpod to war 8 off: "coming on the way out; the pets guard" (`walk_in_fight`). Its barrage on the pets (Bussin -22, AlaFlambe -22, -21); the vet supplies twice, "keeping off the tank" to (6901, 2050). At 25.89 the stand ended (nothing on Logan, no hit on him for 4 s, it no longer coming): "All Follow Me" with the shellpod at 42 % in war mode beside the pets. |
| 18:29:25.9-29.0 | "to the moongate: route 23 steps from (6901, 2050)": (6904, 2050), (6907, 2053), (6908, 2056), (6910, 2058) at 28.96, the pets 1-3 behind on follow. 28.69 the soulcatcher to war 10 off ("keeping out of the reach of the awake soulcatcher"). |
| 18:29:29.14 | The crab, idle at (6911, 2060) **2 tiles** off, swings at Logan (0x2F, its action 4): **-16** (100 -> 84). |
| 18:29:29.15 | **Its pinch**: effect 0x5683 on Logan, sounds 0x51C and 0xA11, the system line "Claws pinch you in place, piercing your armor and applying pressure buildup!" (a unicode message, no cliloc), **-20** (-> 64). Buffs on him: icon 20 'Unable to Move' (cliloc 1110024) **6.0 s**, icon 11 'Armor Rating Reduction' 25 s, icon 286 'Pressure' (10 a stack at 30 s, never reached). Its war flag at 29.156; the step at 29.13 denied (0x21, 29.18). |
| 18:29:29.65 | The voidfin wakes (sound 0xB1E on it, war at (6905, 2060) by 30.05) and comes on. |
| 18:29:29.87-29.92 | The heal potion: **+36** (-> 100). The runner: "to the moongate: held in place; waiting it out", "attacked on the way out; the pets guard". |
| 18:29:30.30 | "All Guard Me"; "stand-off to (6912, 2056): 2 step(s)" (the mariner's area hit round the pets), "out of charge reach: held in place; waiting it out": the stand's walk waits the hold out (`Mover.wait_held`), nothing else of the stand's order runs after this. |
| 18:29:30.91-30.92 | The crab steps beside him (6910, 2059). **The mariner's first shot** (10 off; its action 14, sound 0x85B, its war flag): **Logan -33** (-> 67), Bussin -21, AlaFlambe -21 (its hit on its target and each of us within 1); 0x79C5 mariner -> Logan at 31.14. |
| 18:29:31.25-31.52 | The voidfin at (6908, 2058): AlaFlambe -30; the shellpod at (6908, 2055): AlaFlambe -25. |
| 18:29:31.64-31.95 | **The voidfin's spin** (sound 0x090A on its tile, then 0x7D8 on each victim): **Logan -43** (-> 24), AlaFlambe -45, both 2 off it (Bussin on her tile no number). |
| 18:29:32.47-33.77 | The crab on AlaFlambe: -32 and its pinch -22 (0x5683). The shellpod's barrage: AlaFlambe -24, -24, Festering Wound. The soulcatcher (4 off) casts (action 11, 33.44). |
| 18:29:34.90 | **The mariner's second shot** (action 19, sound 0x85B, 3.99 s after the first): **Logan -25** -> 0, dead (0x2C), 5.75 s into the 6 s hold. |
| 18:29:35 | "out of charge reach: free to move again after 4.7 s" (logged after the death: the death cleared the buff). |

- **What held him:** the giant pincer crab's pinch, Entangle (buff 20) for 6.0 s from 18:29:29.15, with its own -20
  after its swing's -16. He died in it.
- **What took him from 100 to 0 in 4 s** (from the potion's 100 at 29.92): the drowned mariner's shot -33 (30.92), the
  voidfin's spin -43 (31.95), the mariner's next shot -25 (34.90): 101, three monsters woken by his walk, him unable to
  step. The next potion was due 10 s after 29.87 (`healing.POTION_COOLDOWN_S`); a vet channel takes ~5 s.
- **"Pets 15-18 behind ... not waiting here":** at the leave's start only. The runner's approach had walked Logan 10
  moves away from the pets' fight; the planned leave's 'All Follow Me' doesn't wait ("not waiting here"), the walk's
  own lag stop does (`pets_lagging`, `wait_pets`), and it fetched both. On the walk that killed him the pets were on
  follow 1-3 tiles behind him, at the pinch 1 off (both at (6910, 2057)): not lagging, but behind, so Logan was the
  first of us in reach of each monster the walk woke.
- **Cause: the planned walk out across level 2 is a plain walk.** Its stops are an attack, a monster in war mode coming
  on (`HuntLoop.coming`) and a lagging pet; nothing about the idle monsters by its way. Its 23-step route from (6901,
  2050) passed 2 tiles from the idle crab, 6 from an idle voidfin and 10 from an idle mariner, Logan in front; the stand
  for the shellpod had ended with it alive and pulled the pets off it. Everywhere else on level 2 the runner moves as the
  hunt fights (the crawl's pull budget and holds, the step-in or back-off from a shy monster's first sight, the pets
  sent first): the oxygen legs and `--fight-via`'s walk are fought legs (`walk_in_fought`); the walk out was not.
- **Why a stand-off while held:** `walk_in_fight`'s order puts the stand-off first and doesn't look at the hold; its
  walk sat in `Mover.wait_held` (only the walk's guard runs there, and `drink_on_the_run` stays out of a stand) until
  the buff's end. Nothing else could have helped then [INFERENCE: an area peace on the crab and the voidfin, two on him
  within 3 from 31.25, might have stopped the spin; not the mariner's shots from 10 off]; not changed.
- **The crab's hold was known, not modelled or used.** This file's Pressure entry (2026-10-27) names the pinch "with an
  entangle"; the scenario's note said "an entangle to 22:50:47.56, not modelled". The simulator had the Pressure stack
  only, and calibrate's swing (21-78 every 2.05 s, reach 1). The runner sees any hold (`Mover.held`: HOLD_ICONS 19, 20,
  135) but knows nothing of the crab's.
- **The pinch measured** (8 captures 2026-10-24..30, effect 0x5683 on the victim; harness/data/sim/tidal_tomb.json
  `specials` and `melee_observed`): 52 from giant pincer crabs, the victim 2 tiles off 29 times and beside it 23; buff
  20 6.0 s (41; 9.3-9.4 s 10 times, a second pinch 3.3-3.4 s into one); the pinch's own number on the pets 15-24
  (median 21, n=45), on Logan 16, 17, 20, at the same instant as the swing's. Logan's steps stay refused through damage
  (2026-10-27 22:50:38.1 and 41.5 two pinches, -29 at 44.8: every step denied, the next taken at 47.56). Its plain
  numbers on the pets 19-46 (median 33, n=70), one crab's 3.36 s apart; 76 of its 168 numbers landed 2 off.
  calibrate's 2.05 s and 78 counted a pinch's pair as a swing. Not modelled: the small pincer crab's (level 1; 103, 6
  s, 2-9 a pinch, Logan twice) and the colossal's (level 3; 12, 6 s, 29-43, 2 off 5 times).
- **Built 2026-10-30 (simulator only):** the crab's swing from 2 (`melee_observed.reach`, `melee_reach`), its numbers
  and period, the pinch with its hold (`World._pinch`: buff 20 with its end after a TimeSync, steps refused, the pets
  rooted); test_sim `test_tidal_pinch`. On it the live layout (18:29:25.9, the four monsters idle where they stood,
  seeds 1-24) reproduces the cause: the walked leave wakes the crab with Logan 1-3 off, pinched in 7 of 24.
- **Tried, not kept (no runner change):** (1) a planned leave from under water as a fought leg (`walk_in_fought`, as
  after `--leave-via`): on the layout the crab woke on the pets 6 off in 24 of 24 (Logan pinched once, 129 on him
  against 1,241), but level 2's 144 runs went 21 -> 48 with a death (planned-leave deaths 1 -> 31, oxygen on Logan an
  hour 383 -> 602, banked an hour 125k -> 113k): the fought leave on the hunt level runs long under water and fights its
  way. (2) the walk out routed 3 tiles wide of every idle monster in view (Mover.avoid): the layout 3 off in 23 of 24,
  pinched once, 541 on Logan; 144 runs 21 -> 33 with a death (planned-leave deaths 1 -> 8, banked 125k -> 102k). A
  144-run batch's spread is ~5.
- **Built 2026-10-30 evening (runner): the pets first by an idle melee monster** (the user's "narrow rule";
  `HuntLoop.idle_ahead`, `lead_pets`; docs/HUNT_LOOP.md "In and out of a dungeon"). The planned walk to the leave gate
  stops when one of its next 6 route tiles is within an idle melee monster's reach (the crab's 2, else 1) plus 1 in its
  sight, with no pet as near it there and its wake under way (one of us in its range and sight), steps back up to 4
  steps so a pet is the nearest of us (the step-in's `Team.step_plan`; the pets let close up 2 s first when they
  trail), holds up to 6 s for its wake, then walks on with the walk's stops. On this layout (test_sim
  `test_tidal_leave_crab`, seeds 1-24): "stopping at (6905, 2051) for the pets to lead: back to (6906, 2051) in 1
  steps, out of its sight, Bussin in it", the crab awake on AlaFlambe 24 of 24 with Logan 8 off, no pinch (before: on
  Logan 22 of 24, 1-3 off, pinched 7 times, 1,241 on him against 591). Level 2, 288 runs: runs with a death 51 -> 60
  (+5, +4 in the two blocks: within the spread; a variant that acts only 17 times a block moved them +2 and +5), pet
  deaths 471 -> 470; level 1 1 -> 3 of 96, Maus 3 1 -> 0 (docs/SIM.md "Tidal Tomb: the pets first by an idle melee
  monster"). Not on the walk in (`LEAD_IN` False: no stand there for what wakes). Whom a melee monster takes at its
  wake is the simulator's rule (the nearest it sees) [INFERENCE: measured for the shy ones only]; live, the crab
  struck from 2 with its war flag after the hit, so its wake may come later and nearer than the simulator's 10-tile
  range. **Open:** the crab when the pets trail outside its range at the stop (no tile: today's walk; 12 of its 17
  stops in block 2), the small and colossal pincer crabs' pinches.

## Tidal Tomb: Marinerbane's room (2026-10-30 21:03-21:31 with the guild, capture 20261030_175703)

The user played Logan Wolf (Bussin 0x000EA04F, AlaFlambe 0x0026EF56) with his guild and allies (24 other players in
the room, most green). Decoded with `replay.timed_packets` -> `WorldRuntime`; the scratch pass is not in the repo. The
same capture's 18:00-18:58 is the runner's level 2 run and its death ("Tidal Tomb: level 2 death 2026-10-30 18:29").

- **The route:** 21:03:01 recall in to (6653, 2097); 21:03:41 down level 1's west strip (6610, 2060); 21:05:51 N from
  (6821, 1910) to level 3's west entrance (7092, 2193); along level 3's west side to (7059, 2008) by 21:09:24. Four
  visits to the room: 21:09:25-21:11:46, 21:13:12-21:20:00, 21:21:15-21:26:08, 21:27:15-21:30:57 (1,066 s). Out at
  21:31:25 from (7106, 2055), beside level 3's crimson gate (7107, 2055), to (3848, 2867, -19).
- **The way in:** level 3's west wall. A step W from (7059, y) onto (7058, y) lands on (7425, y - 51, 0) for y 2007,
  2008 and 2009. A step E from (7424, y) onto (7425, y) lands on (7058, y + 51, -50) for y 1958, 1959 and 1960. Each
  landing is the other way's teleporter row, as at the level strips.
- **The room:** a map flood fill from the arrival gives x 7352-7425, y 1938-1979, 1,682 tiles, z -2..3. It joins no
  other ground; the boss approach's chamber (x 7329-7400, y 2060-2108) is another room.
  - 'recovery dolphin' 0x00004970 stands at (7418, 1957, 0). 'Your oxygen is refilled.' came 2.2 s after each arrival
    and 59 times in all: the room is under water.
  - No monster lives there but the boss and its summons: none before its first spawn, none while it was dead.
  - 73 unnamed notoriety-3 mobiles of body 22 showed at (7370-7373, 1940-1944) 5-10 s apart, boss or none. Each was
    removed 0.04-0.09 s after it came [INFERENCE: a server-side helper, not a creature].
- **Marinerbane** ('[Mini Boss]', notoriety 6, body 673). The wiki gives 100,000 hits, melee 40-50, armor 50 and gold
  50,000 (#10815, #11497).
  - Its spawn is (7373, 1957, 3). It came back 300.2, 300.4 and 300.1 s after each death; the first spawn showed 8 s
    after Logan's first arrival.
  - Three kills took 99.0, 143.0 and 116.9 s. It fought within x 7373-7390, y 1953-1975.
  - '*Increases in Size*' came 7, 11 and 10 times, 27 of the 28 in each fight's first 28 s. That is the boss scaling,
    40,000 hits a damaging player for a mini boss (patch notes 2025-01-25, #5121; up to 30 players, #6336).
- **Its moves:** 24 emotes on one timer. The first came 5.4, 5.8 and 11.5 s in, then 10.3-22.7 s apart (median 15.1,
  n=21). By kind:
  - '*stirs deep dwellers*' 13. 1.25-1.31 s later, 4-8 Tidal Tomb monsters appear 1-12 tiles from it (12 of 13 waves;
    one had 9). The 81: lowtide scavenger 17, aquamarine smallshell 11, ruby smallshell 9, singing noughtilus 8, giant
    pincer crab 6, drowned mariner 5, oozing noughtilus 5, soulcatcher 4, biolumen shellpod 4, drowned buccaneer 4,
    colossal pincer crab 3, lighthouse shellpod 3, tidal krait 1, drowned swashbuckler 1. All of them are known types.
  - '*raises a mighty cannon crew*' 4, none before 87 s in. Drowned gunners (bodies 400/401, '*rises*', not on the
    wiki) appeared 0.7 s after it, then one every ~1.1 s, 1-4 of them. They never moved. Every ~8 s each played
    animation 32 then 10, with sounds 0x5AE and 0x5AC. No number from them landed on us; their target is unknown.
  - Glowing barrels 3: '*throws glowing barrels*' (3 barrels) and '*throws a barrage of glowing barrels*' (5).
    - The barrels went 0.80-1.29 s apart (moving effects 0xF528/0xF52C) at tiles 5-12 off where one of us or a
      guild player stood.
    - Each burst 0.36-0.61 s after its throw (sound 0x8B8 on its tile, 0x905 round it).
    - Bussin and AlaFlambe took 75-91 at 0, 3 and 4 tiles (n=6). Logan took 19 and 22 at 2 tiles. Nobody at 5-10
      tiles was hit (n=11).
  - '*sends out a horde of feelers*' 2 and '*lashes out*' 2 (once '*furiously lashes out*'): emote only, as seen from
    5+ tiles off. The bursts of 25 moving effects 0x6890-0x6895 every ~8 s round a tile are a player's: they came on
    level 2 before the room too.
  - Its swing never landed on Logan or a pet.
- **Its summons die with it:** 43 of the 51 seen alive in the room died in its death tick. Nothing came for the other
  8 [INFERENCE: out of our view].
- **On Logan and the pets in the room:** 2,141 in 1,066 s (Logan 480, Bussin 822, AlaFlambe 839).
  - The barrels above.
  - A summoned drowned mariner's shots on all three every 4.0 s (21:24:42-21:25:28): Logan 30-39, down to 27 of 111.
  - A soulcatcher's spell on all three (21:17:11.88: 75, 75, 30).
  - An oozing noughtilus's ooze on Logan.
  - Logan's max hits switched between 100 and 111 four times [INFERENCE: a guild member's buff].
- **Level 3's west wall:** three spawns are new by the idle rule: colossal pincer crabs at (7093, 2147) and
  (7073, 2016), a giant kelpdweller at (7078, 2002).
- **Not in calibrate.py's observations:** the capture stays out of `calibration.tags`, for three reasons.
  - calibrate takes every notoriety-2 bonded pet in view for ours. The guild's Trashe, Trashi, DragonSmurf, 'e' and a
    phoenix would count.
  - Its other-player context counts only blue players, and the guild was green.
  - Its 'swing' sums one attacker's numbers within 0.25 s over all its victims. With this capture the drowned
    mariner's p90 swing goes 43 -> 139: one shot on Logan, Bussin and AlaFlambe summed.
- **Found on the way:** the committed `tidal_tomb_observed.json` was generated while 20261027_220057 was still being
  written (its `in_region` ends 23:39:44; the capture now runs to 01:36:38). Regenerating from today's files, the same
  four tags, changes six types' melee or swing in the simulator. Examples: dungeon-nest crab 7-41 -> 7-68, mermadon
  swing 2.52 -> 3.35 s, singing noughtilus 1.70 -> 2.25 s, flying manta 3.36 -> 2.67 s. It was not regenerated here.
- **Built 2026-10-31 (simulator):** the room, its teleporters and dolphin, the three spawns, Marinerbane with its
  size, rotation, waves and barrels (docs/SIM.md "Tidal Tomb: Marinerbane's room"). The teleporters also went into
  `harness/data/teleporters.json`, so no Mover walks onto them by accident. Before, it held neither level 3's west
  entrance nor its boss approach.

## The Ossuary: three visits by hand (2026-10-28, captures 20261028_165135 and 20261028_182147)

The user played them: 17:13:43-17:26:28 (levels 1-3) and 17:32:01-17:37:37 (levels 1-2) with GraveRobber and
AlaFlambe, 20:34:27-21:03:36 (levels 1-4) with AlaFlambe and Bussin; AR 120. The evidence behind the simulator's
`ossuary` scenario (docs/SIM.md "The Ossuary").

**Getting in and out:**

- **The rune.** Logan's "A Rune Tome" (0x444AB1F3) has an "Ossuary" row (its gump at 17:13:29). It lands **outside**
  at (1213, 2816, 31), the rune libraries' "Ossuary Outside". Outland Dan's runebook "Ossuary" and the libraries'
  "Ossuary" (5870, 1307-1308) land inside, beside a golden gate.
- **The entrance:** a step W from (1206, 2816-2817) onto (1205, 2816-2817) puts one on level 1 at (5927, 1316-1317,
  37) (17:13:43, 17:32:01, 20:34:27).
- **Level 1 -> 2:** a step W onto (5786, 1326-1329) lands at (5689, 1248-1250, 0) (3 of 3 visits).
- **Level 2's stairs** (5634, 1309) hop to (5638, 1300, -40) on the same level; the walk round them joins the two
  ends as well (20:35-20:39).
- **Level 2 -> 3:** a step S onto (5683-5684, 1363) lands at (5667-5668, 1123, 37).
- **Level 3 <-> 4:** level 3's north strip (5588-5590, 985), a step N from y 986, lands on level 4 at (4851-4853,
  2073, -3), far from the rest (x 4825-5000, y 1744-2073). Level 4's arrival row (4850-4854, 2073) leads back to
  (5587-5590, 985, -8); stepping W/E along either row bounced Logan between them five times in 8 s (20:58:15-23).
- **Moongates:** golden (hue 2822) 0x40001A02 (5926, 1326, 0) beside level 1's arrival and 0x40001A04 (5870, 1310, 5);
  crimson (hue 2820) 0x4004A43E (5672, 1277, 0) on level 2 and 0x493E7470 (5577, 1095, 0) on level 3. The menu
  (0xE0E675B8) is Tidal Tomb's; Totem landed Logan at (3884, 2901, -40) all three times (17:26:28 and 21:03:36 by
  level 3's gate, 17:37:37 by level 2's). No recall was tried inside.
- All 14 teleporter tiles are in `harness/data/teleporters.json`.

**The levels** (map flood fill from each arrival; all of Logan's steps walk): level 1 x 5785-5935, y 1270-1381
(9,259 tiles, 141 s there), level 2 x 5581-5689, y 1225-1363 (6,668; 831 s), level 3 x 5527-5739, y 950-1156 (11,364;
1,355 s), level 4 x 4825-5000, y 1744-2073 (21,141; 322 s). Level 1's way west has a one-wide ramp at (5889,
1321-1329) beside a four-wide stair at (5882-5886, 1330-1333).

**Our pets share tiles.** In the Mausoleum trip (20261012_210013, 1,300 s with both pets known) GraveRobber and
AlaFlambe stood on one tile 54 % of the time and a pet on Logan's tile 25 %; in 20261028_165135 3,775 of 8,483 pet
moves landed on a tile another of ours held. They walk through each other and us.

**Monster specials** (each emote's period per creature; the numbers on our side after it with no other monster beside
the victim):

| type | emote | every | then |
|---|---|---|---|
| phantasm | `*laments*` (28) | 15.17 s | 0.8-2.2 s later (median 1.48) one hit on each of us out to 12 tiles (38 of 53 at 6-12, 0 of 19 at 13+): pets 26-31, Logan 26-33 |
| spectral terror | `*terrifies*` (8) | 7.86 s | 1.2-1.9 s later one on each of us out to 11 (12 of 14 at 7-11): pets 55-56, Logan 37-46 |
| hierarch | `*channels ancient fire*` (22) | 7.96 s | from 0.6-1.3 s a hit every 0.50 s on its target, 5-7 of them, 12-20 |
| ossuarian sightless seer | `*plumbs the darkness*` (28) | 8.24 s | 1.01-1.05 s later one on 1-2 of us out to 12 (patch 2020-05-12, #8441: at most 2 targets): pets 31-56, Logan 24-26 |
| jackal spirit | `*ritual strike*` (17) | 7.78 s | 0.73-0.80 s later on the pet beside it, 29-53 (12 of 17) |
| ossuarian executioner | `*prepares execution*` (8) | 8.06 s | 2.01-2.04 s later on the pet beside it, 29-48 (5 of 8) |
| ossuarian deathbringer | `*begins rain of death*` (2) | 8.44 s | 2.25 s later on each of us within 4: Logan 35 and "A rain of deadly strikes hit you, causing you to bleed!", then 12 every 3.0 s five times (the pets 8) |
| khamsin | `*charges*` (6) | 7.58 s | 0.53 s later 32-35 beside its landing |
| rag witch | `*dematerializes*` (5) | 8.4-18 s | `*evasive*` 1.5 s later, `*materializes*` 6.4-6.8 s after it |

- **Summons:** 5 spirit asps at each apophite spirit's death (3 seen), gone 2.4-10.3 s later, as the 2020-11-21 patch
  says (#7925: "die 5-10 seconds later and not generate loot"). Level 4: a plaguecaller's plague frogs every 3.1-6.9 s
  (10 in 38 s), the brood beetles' pocket beetles 2.3-36 s apart (4,000 hits: 4 of 5 killed in view took 4,001).
- **Seen, not measured enough:** the anubite's `*exhales*` ("You have been struck by an ancient blight!"), the
  huntress's and hunter's `*readies net*` (`*entangled*` over the pets, "You have been entangled in a net!"), the lion
  warrior's `*parries spell*`, the firebrand's `*ignites*`, the embalmer's `*prepares mixture*`, the discguard's "A
  razor-sharp disc cuts and causes you to bleed!" (level 4).
- **The wiki's list lacks** the ossuarian huntress (her kills took the hunter's 6,000 + 1), the fire priest (the
  priestess's), the summons and the "mummified remains" (level 4).
- **Ostards:** 6, each first seen 3-8 tiles from Logan beside hunters and huntresses, dead soon after, no gold:
  dismounted riders' mounts [INFERENCE].

**Logan on level 3** (22.6 min): 70 kills, 1,757 damage on him (4,669 an hour; the phantasm's laments 477 of it, the
seer's 160, the spectral terror's 122), 31 heal potions, 80 vet uses, 43,867 gold lifted. Discordance 42 of 51 at E
123 (every song 18.4 %), Provocation 6 of 9, Peacemaking 3 of 4.

**Other players:** 25 and 12 seen; four reds (Dfence, Monex, I Make Bodies, Saint Natalie) at 18 tiles on level 2's
stairs at 17:17:52-57 (Logan took the stairs at 17:18:01 and went on to level 3).

## Other pets than GraveRobber and AlaFlambe: no roster (2026-10-24 21:11)

- The user brought GirthyEarthy 0x000E820F (body 12, the tank), fireminion 0x001D17E6 (body 776) and a searing imp
  0x006C15F9 (body 74): all on Logan's tile at the house landing, notoriety 2, followers 5/5, claimed 216 s before.
- None had a pet tag in the proxy's state (`Mobile.pet` unset; the viz state shows no `pet` field). The "(tame)" /
  "(bonded)" line comes only when the client single-clicks the mobile; the client hadn't clicked these.
- So the runner's roster (`tamer.resolve`) had nothing: the remembered serials (GraveRobber, AlaFlambe) weren't in
  view and `mount.pets_near` needs the tag. Reproduced in `test_loop_hunt.py` `roster_run` on the old code: "--pets: no
  pet of ours in view". The Seer ran its first run with `--pet` for the three meanwhile.
- Also, before: when any remembered pet was in view, only the remembered ones were taken, so a new pet brought
  beside an old one was left out.
- Fixed (`Team.setup`, `tamer.untagged_near`): an untagged non-human, non-attackable mobile within 3 tiles is
  single-clicked first (`agent_link.look_at`, the stock client's 0x09 + 0x34), and the roster is the tagged pets
  within 3 tiles plus the remembered ones in view. Nothing else in the runner is tied to the pets' kind: the tank is
  whichever pet stands next to a monster in war mode (`Team.tank_t`). The simulator modelled only the gravebug
  and the phoenix until 2026-10-25; since then all eight of Logan's pets ("The Healer's Codex, the Taming Bestiary
  and the Guide" below, docs/SIM.md "Pets, teams and perks").

## Pets locked on a fight they can't reach: walk back / patrol ping-pong (live 2026-10-24 21:40:47-21:41:31, hunt-20261024-211218-bab0)

Task log lines 1116-1186, capture session_20261024_102346 (replayed through `WorldRuntime`):
- 21:40:47 target a reanimated daemon 0x0060B7F6 at 8 tiles, fighting another player's giant swamp beetle out of our
  sight; 21:40:48 "All Kill" at it. 21:40:53 passed over (still out of sight), its reach kept out of, the 'All Kill'
  target dropped, 21:40:54 "All Guard Me".
- From 21:40:56 to 21:41:30 GirthyEarthy, fireminion and a searing imp stood together at (5243, 692) in war mode, 9-10
  tiles from the daemon at (5237, 701), with no attack animation (0x6E) of their own; the daemon's hits fell 80 -> 2
  from the beetle. They were locked on it and couldn't reach it [INFERENCE: war mode, no step, no swing of their own].
  "All Guard Me" didn't call them off (seen before in the "All Patrol" demo of 2026-10-18: "'All Guard Me' mid-fight
  doesn't call them off"). They came at once when it died.
- `pets_busy` didn't count it: a monster on another player's side that no pet of ours swings at (6b4f926). So the crawl
  patrolled, and seven times in 45 s: "patrol to (5231, 722): stopped (GirthyEarthy at 9)", "reposition: reanimated
  daemon 0x0060B7F6 7 tiles off can cast at us here; to (5244, 703)", "crawl: not patrolling yet: GirthyEarthy at 11",
  "back to GirthyEarthy at (5243, 692) (11 tiles)", "reposition ... to (5242, 695)", the patrol again. Two stamina
  potions went on shoves.
- Fixed (`Team.rejoin`): the pet walked back to, in view within `tamer.COMMAND_R` 12 (moved there from loop_hunt), on
  the tile it stood on at the last walk back within 30 s, with no fight of ours (`pets_busy` None), gets "All Follow Me"
  instead (the test's log: "AlaFlambe at (5243, 692) (11 tiles) stands where it stood at our walk back 8 s ago, no fight
  of ours there: 'All Follow Me' instead of another walk back"). No walk back for 10 s while they come; "All Guard Me" once
  they're with us, after the 10 s, or at once with something on us ("after 'All Follow Me': the pets with us; 'All
  Guard Me' due"). The guard waits because [INFERENCE] guarding pets may go back to the fight they stood locked on.
- test_sim `test_pets_stuck_off_fight` replays it: the daemon at (5237, 701) on another player, both pets held in war
  mode where they stand (no step, no attack) until a command puts them on follow. Before: 29 walks back, no "All Follow
  Me", the pets 9 off. After: 1 walk back, then "All Follow Me", the pets come, the patrol reaches (5232, 723), then
  "All Guard Me".
- **Simulator, before and after** (120 runs of 60 min each, seed 101, `--others --eerie --prior harness/data/harness.db`;
  before = 4fe805e): deaths 28 in 99.0 h and 28 in 99.7 h, pet deaths 52 and 56 (none of the 56 within 60 s after a
  stuck "All Follow Me"), potions 42.8 and 43.4 an hour, 112.6k and 112.8k gp banked an hour. Walks back 1,267 and 823.
  Repeat walks back (same pet, same tile, within 30 s) 618 and 175. Chains of three or more 87 and 1. Stuck "All Follow
  Me" 220; the pets were with us within 10 s after 185 of them. The simulator's pets stand still at a walk back too,
  not only in this scene.
- **Rejected: no reposition out of a caster's reach when it fights someone else** (`Team.elsewhere`, not on us or our
  pets, as `Team.shy_threats` does for purgers and blightmares). In the test it saves one reposition and ~6 s. On the
  same 120 runs with the fix: deaths 30 in 102.2 h, pet deaths 58, potions 43.7 an hour, 108.9k gp banked an hour,
  reposition steps 3,587 against 3,569. No gain, so it isn't in.

## Dead vetting a pet in two casters' reach (2026-10-25 00:38)

Live 2026-10-25 00:38:45, Maus 3, hunt-20261024-230728-d7cf (task log lines ~5160-5185), visit 4: 22 kills, 16,274 gp
on him (lost). Pets GirthyEarthy 0x000E820F (the tank), fireminion 0x001D17E6, a searing imp 0x006C15F9, all still
levelling. Capture session_20261024_225113 replayed through `WorldRuntime` (0xC0 by `world/layouts.py`: graphic and
tiles as u32; 0x6E actions; 0x20 war flags; the overhead numbers). No player in view; no swing on him, so the death
juncture listed no attacker.

- **00:38:15-21** the patrol went south past a master reanimator 0x006213A3 at (5239, 721) toward a void slime; the
  reanimator woke on the pets beside it (war mode, action 245 at 18.4; the pets' fireballs at it from 19.0).
- **21.16** a flamestrike column (0xC0 type 3, 0x3709) on Logan at (5244, 733), 12 from it: -34 (100 -> 66) at 21.67;
  potion 22.30 -> 100. [INFERENCE: the reanimator's, the only caster awake in view; the same column on GirthyEarthy at
  24.51, 31.20, 34.57 and 41.05 while it fought him.]
- **27** "reposition: master reanimator 0x006213A3 12 tiles off can cast at us here; to (5243, 733)": out of its sight
  by our map. **30** "to a hurt pet: (5243, 722), 4 from GirthyEarthy (--vet-range 5)" (below --pet-heal-at 95 %;
  92 % at the vet at 33), 11 steps north, arrived 33.4: 4 tiles from the reanimator (78 % -> 43 % hits), behind a wall
  by our map (`los.Sight`: it sees no tile at x 5241-5245, y 716-724), 13 from an idle shadowguard 0x006217CC at
  (5230, 723). Vet channel on GirthyEarthy 33.8.
- **34.96** the shadowguard's first action in war mode [INFERENCE: woken by the pets 8-11 off; Logan was 13 off].
- **37.69** its fireball (0xC0 type 0, 0x36D4) from (5230, 723) to Logan (13 tiles) and to each pet at once; "You have
  been ensnared and diseased!"; -28 at 38.19 (100 -> 72), the pets -17, -19, -17.
- **38.89** the vet finish: +28 on Logan (100) and +117 on GirthyEarthy. **38.90** the runner's potion click, chosen
  at 72: cliloc 1049547 "You are already at full health." The runner logged "heal potion: 72 -> 100 hits" and started
  its 10 s potion clock (to 48.9): no potion could be tried before then.
- **From 38.55** the reanimator's action 209, new since Logan came within 4 (before: 245, 20, 230), at 38.55, 40.12,
  41.92, 43.48.
- **41.28** a lightning (0xC0 type 1) on Logan and all three pets at once, and a burst of source-less moving effects
  (0x688F-0x6895) from all around onto GirthyEarthy's tile: -35 at 41.54 (100 -> 65), the pets -21, -25, -22.
  [INFERENCE: the reanimator's, its action 209 1.16 s before (cast_scan's reanimator cast_s 1.16-1.27); the
  shadowguard's action 6 was 1.48 s before.] At 65 (35 missing) a potion was due; the clock said 7.4 s more.
- **42.80** location effects 0x376A on Logan's tile and the imp's: -25 (70 -> 45; +5 on all four at 41.96), the imp
  -20 [INFERENCE: the reanimator (209 at 41.92) or the shadowguard (6 at 41.65)]. **43.31** "vet supplies: a searing
  imp at 93% (poisoned)": a 5 s channel begun at 45 hits, no potion possible.
- **44.40** the shadowguard's fireball to Logan (11 tiles) and each pet; **44.67** a lightning on all four (the
  reanimator's 209 at 43.48, 1.19 s before). **44.91** -21 (45 -> 24), **44.93** -25 (24 -> 0). Dead; the reanimator
  at 4 %.
- **By source**, the last 25 s, 168 hits: the reanimator 34 + 35 + 21 (+25 at 42.80 if its), the shadowguard 28 + 25.
  Every hit after 33.4 landed on the tile our map says neither caster sees: their spells on all of us needed no sight.
- **Cause** [INFERENCE: the order of weight]: the walk to a hurt pet took him from 12 off an awake caster, out of its
  sight, to 4 of it for a tank at 92 %, into spells that hit every one of us; then the full-health refusal counted as a
  drink cost the potions at 65 and 45. The leave (--leave-at 50 %) didn't fire at 45: --heal-in-place with vet
  supplies in the pack is "a heal coming". The retreat needs an attacker beside him.
- **Old gap, new scene**: walks to a hurt pet ran 14.5 an hour this run against 7-43 in the runs of 10-21..24 (not
  more with the levelling pets); of the 17 deaths in the task logs since 10-15 this is the first with one in its last
  30 s. The captures since 10-17 hold 27 "already at full health" refusals.
- **Replayed** (`sim/fight0038.py`, from 00:38:25: Logan at (5245, 733), the pets on the reanimator at 78 %, the
  shadowguard waking on the tank 10 s in, the corpse purger idle at (5229, 727); the two casters at the capture's
  cadence, their spells on every one of us within 4 (reanimator) or 6 (shadowguard) of the victim, sight or not:
  `ranged.area_r`, the simulator's pets the gravebug and the phoenix). Seeds 1-200:

  | | deaths | lowest hits median | damage on him, mean | potions | vet channels | walks to a hurt pet |
  |---|---|---|---|---|---|---|
  | 63aa4ab | 5 | 56 | 159 | 618 | 1045 | 348 |
  | after | 0 | 67 | 113 | 460 | 1025 | 276 (219 held) |

  Each of the five deaths was one walk to a hurt pet, then dead 21-25 s in under the reanimator and the shadowguard.
- **Fixed.** `Team.to_pet`: with an awake caster or ranged monster (not a shy one) that doesn't see us here, a live
  pet at `tamer.TO_PET_CASTER_AT` (50 %) or more isn't walked to when the vet tile (or its tile) is within that
  monster's reach by distance (`CASTER_R` 12, `RANGED_R` 13) and nearer it than here: "to a hurt pet: GirthyEarthy
  (92%) is only in reach 4 from the awake master reanimator, out of its sight 12 off here (50% or more); staying out
  of it". `HuntLoop.drink`: "already at full health" leaves the potion clock alone ("heal potion not drunk: already at
  full health (72 -> 100 hits before the click)"). Test: test_sim `test_to_pet_not_into_caster_reach` (the live
  tiles: no walk at 92 %, the walk at 15 %, the clock kept; on 63aa4ab the walk to 4 of it and the clock started).
  `test_vet_tile_off_tank` puts its gravebug at 40 % (the daemon there is a caster out of our sight).
- **Simulator, before and after** (3 x 120 runs of 60 min, seeds 101, 201, 301, `--others --eerie --prior` a copy of
  harness/data/harness.db taken 2026-10-25 00:57; before = 63aa4ab): deaths 78 in 318.5 h and 66 in 325.4 h (0.245 and
  0.203 an hour), pet deaths 140 and 150 (0.44 and 0.46), damage on Logan 2,078 and 1,980 an hour, potions 41.2 and
  40.6, vet channels 133.4 and 127.5, gp banked 107.7k and 108.6k an hour (per-run gold an hour 98,974 and 101,485,
  Welch t 0.95). The potion fix alone (gate off, 120 runs of 30 min, seed 101): deaths 11 against 10, pet deaths 39
  against 34.
- **Rejected: the rule for every awake caster, shy ones and those that see us here too** (the first version, 50 %):
  deaths 42 in 218.6 h (0.192 an hour), but pet deaths 135 (0.62 an hour against 0.43). In 40 logged runs 862 of its
  1,931 holds were at a corpse purger or a blightmare, whose shot rule and cover already keep us out by sight. At 70 %:
  deaths 46 in 216.2 h, pet deaths 111.

## Less time not fighting in the simulator (2026-10-24/25, goal run on the efficient-crawl branch)

Goal (user 2026-10-24): gold banked an hour in the simulator +10 % with deaths per 100k gold banked at most 1.1x the
current runner's and pet-death / pet-left-behind runs at most +2, on both seed blocks; runner code only. The user's
three observations: stepping off the tank repeatedly with no monsters around, patrolling to the ends of hallways
already cleared, waiting to heal before moving on when a pet misses a few hits.

- **The batch**: as "Half the deaths in the simulator" (200 runs, 20 careers, `--others --eerie --prior <frozen>`,
  `-- --vet-range 5 --crawl-learn-s 180`); frozen prior: the main store's 90 episodes and 4,446 hunt events older
  than `EPOCH` (the same counts as that goal's copy). Seeds 1001-1200 tuning, 2001-2200 holdout. 13-17 min a batch.
- **Where the game time went** (a throwaway profiler: `SimLink.intent` time by kind, and the sim world sampled every
  second on the level; 80-128 runs as careers): base runner fight 31 %, patrol 20 %, bard 10 %, loot 9.5 %, hold
  5.8 %, step off the tank 3.8 %, vet 4.3 % (1.2 % "Healing up before moving on"). On the level the pets fought 64 %
  of the time (both attacking 57 %); 25 % had an idle monster within 18 tiles not fighting anyone (most beyond the
  pull range or passed over as unseen: tormented and bound souls in their cells first), 9.5 % none in view.
- **Fixed** (all three user observations were real):
  - `Team.spacing` ran for `FIGHT_LINGER_S` 8 s after the last monster: after every kill Logan stepped off the
    guarding pets as they came back, every 1.5 s, before looting. Now only while `aggro.View.engaged`. Spacing
    steps 16,479 -> 8,983 in 97 game hours. test_sim `test_no_spacing_after_fight` (fails before: 3 steps after the kill).
  - Out of a fight a pet below `--pet-heal-at` 0.95 got a channel and `busy_healing` held the patrol through it; now
    only below `CALM_HEAL_AT` 0.80 (or poisoned, a ghost, us below `--heal-at`). test_sim `test_calm_heal`.
  - Only a waypoint stood on counted as visited: `Crawl.glimpse` marks every waypoint in sight within `SEEN_R` 7, and a
    patrol leg stops once its waypoint is in sight within 7 ("looked at it from N tiles").
  - `HOLD_S` 10 -> 5 (wakes take 1.4-2.6 s); the blink wait ends when the monster is back in view (it was 2.3 % of
    the time once the spacing no longer filled it); the eerie corpse walk is bounded like a loot walk (one corpse
    18 tiles off by a 143-step route was walked to every 3 s for 2 minutes).
  - Second round (2026-10-25): `Crawl.seek` no longer walks toward a monster the fight passed over (`HuntLoop.skip`;
    it went back to the souls in their cells over and over); a monster on another player already below
    `THEIRS_BELOW` 0.8 of its hits is passed over, not "All Kill"ed: 63 of 63 such kills had left a blue corpse
    (test_sim `test_theirs_passed_over`; blue corpses 471 -> 160 in 128 runs).
- **Result** (batch command unchanged; "first" = the first round, commit 4bc17be; "second" = both rounds):

  | 200 runs | gold/h | x | deaths | d/100k gold | x | pet-death runs | pet left behind | kills/h |
  |---|---|---|---|---|---|---|---|---|
  | 1001-1200 base (4fe805e) | 106,143 | | 17 | 0.165 | | 4 | 9 | 97.5 |
  | 1001-1200 first | 110,045 | 1.037 | 17 | 0.159 | 0.96 | 2 | 6 | 101.0 |
  | 1001-1200 second | 111,948 | 1.055 | 16 | 0.145 | 0.88 | 5 | 7 | 103.7 |
  | 2001-2200 base (4fe805e) | 105,949 | | 18 | 0.176 | | 4 | 7 | 97.6 |
  | 2001-2200 first | 110,594 | 1.044 | 23 | 0.218 | 1.24 | 7 | 3 | 101.4 |
  | 2001-2200 second | 112,179 | 1.059 | 13 | 0.119 | 0.68 | 1 | 6 | 103.0 |

  The goal's +10 % is not met; the second round passes every other criterion on both blocks. The first round's
  holdout deaths (23 against 18) were noise [INFERENCE: about ±5 deaths between equivalent arms].
- **Why no more**: the level is supply-bound with the other players on. They kill ~230 monsters a game hour on
  the level to our ~100; without them (`--others` off) the base runner makes 120k an hour and the first round 123k
  (+2.8 %), with them 105k and 111k (+5.9 %): the time saved turns into kills only as far as there are monsters to
  reach first. A 128-run screen's noise is about ±2 % (one arm 1.4 % SE), and no knob tried moved it beyond: the pull
  budget 4, `--crawl-dwell` 0.5 / `--crawl-depleted-s` 3, `KILL_AFTER_S` 0 or 2, `PIN_S` 8, `IDLE_PET_S` 2,
  `SPACING_REPEAT_S` 3 or no in-fight spacing at all, `SEEN_R` 10, `RESPAWN_S` / `STALE_CAP_S` 300, `--crawl-deep-x`
  3, `CALM_HEAL_AT` 0.7, `FIGHT_LINGER_S` 3, `HOLD_S` 3, `HOLD_R` 8, `--no-hold-first-sight` (-2.8 %),
  `--pull-range` 10, `--no-reposition`, `--no-peace`, `--no-provoke`, `CROWD_FACTOR` 0.1 or 1, `SKIP_S` 20, `D0` 10
  or 40, `THEIRS_BELOW` 1.01 (never "All Kill" one on someone else), even `--return-at off` (+1.6 %). The small
  positives together (pull range 10, no peace, `HOLD_S` 3, `IDLE_PET_S` 2, `SPACING_REPEAT_S` 3, `D0` 40) at 256 runs:
  -1.5 %. Gold an hour by zone is flat (1,870-2,170 a minute).
- **Tried, not kept**: routing the patrol round the sight of idle purgers, blightmares and --never-aggro monsters
  (it ended the stop / back off / next waypoint rounds at one blightmare, 100 in 16 runs) gave kills +4 % but gold
  -2 %: the gold a kill fell 1,092 -> 1,028, the rich rooms are behind them. Opening with "All Kill" at an idle target
  in reach (within 8 or 5; "guard, never open" loosened): no gain. Passing over the cheap types (raw flesh, reanimated
  knight, calamity, vampire bat, shadow minion): no gain. No hold for a passed-over monster: -3.6 %. A kill resetting
  its area's staleness, looting after a target in reach, the nearest corpse first: no change.
- **After the merge into main** (692c875 against 6c86097, main just before it, which had the stuck-pets "All Follow
  Me" and the caster-reach rule for walks to a hurt pet; the same batch, frozen prior):

  | 200 runs | gold/h before | after | x | deaths before / after | d/100k gold x | pet-death runs | pet left behind |
  |---|---|---|---|---|---|---|---|
  | 1001-1200 | 106,087 | 110,437 | 1.041 | 22 / 16 | 0.68 | 5 -> 10 | 8 -> 1 |
  | 2001-2200 | 105,724 | 110,624 | 1.046 | 17 / 18 | 1.02 | 5 -> 6 | 7 -> 7 |
  | pooled | 105,904 | 110,529 | 1.044 | 39 / 34 | 0.83 | 10 -> 16 | 15 -> 8 |

  +4.4 % gold an hour on the merged code, a little under the branch's +5.7 % against 4fe805e [INFERENCE: noise of
  about ±3 % a batch, or some overlap with main's to_pet rule]. Pet-death runs 5 -> 10 on seeds 1001-1200 only (6 vs 5
  on the holdout): watch for it live.

## Hunt job pane lost its tally mid-run (live 2026-10-25; fixed in the runner)

- **Symptom (user):** the Live page's Hunt job pane said "kills/hr — no run tally (the proxy or runner predates
  it)" during a Mausoleum pet run whose proxy and runner both send `tally`.
- **Cause:** `/api/state` `.intents`: every step up to 1792952242.8 carried `tally`, the last with exactly 32
  keys; every step from 1792952243.8 ("Back to NightmareA", 15 min into the run) had none. The runner sent its
  whole `totals`, which `tamer.Team.count` grows with open-ended names (`bard_*`, `<effect>_ok/_fail`, `song`,
  `vet`, `spacing`, `los_moves`, ...); the proxy's `set_intent` keeps a tally of at most `INTENT_WOODS_MAX` (32)
  entries, all or nothing, and drops a bigger one without a log line.
- **Fix:** `HuntLoop.doing` sends only `loop_hunt.INTENT_TALLY` (the pane's ten counters); no proxy change, so no
  proxy restart. A runner started before the fix still loses its tally at the 33rd counter. The pane's message
  now names a rejected tally as a cause too.

## Backed off from a purger, never patrolled at (live 2026-10-25 15:52-15:57, hunt-20261025-152542-5ab5)

- **Symptom (user):** "It says something like 'pets first in the corpse purger room' but then doesn't actually patrol
  the pets in, it just walks away." (the doing line of `Team.back_off`: "Stepping back to …: the pets first in the
  corpse purger's sight").
- **Cause 1, the back-off's tile:** 15:57:40 "corpse purger 0x0069D3DA would see us before the pets at 11 tiles …;
  back to (5251, 654) out of its sight", the purger at (5265, 653), 14 off (capture 20261024_225113 replayed to
  15:57:40.5: that purger idle, a bound soul at (5266, 647), the three pets beside Logan). `back_off` took the
  farthest tile out of its sight; `patrol_spot` wanted a tile within `PATROL_SPOT_R` 2 of the purger and
  `PATROL_MAX_R` 11 of us, so 13 at most: none. No line said so, and the crawl, keeping off its sight for 60 s
  (`KEEP_OFF_S`), patrolled elsewhere. Task logs 10-22..10-25, first back-offs: 30 of 38 that ended 14 or more from
  it had no "All Patrol" after (the other 8 came after something else moved Logan within 13, e.g. 13:07:39 an
  off-the-tank step); 13 of 21 that ended within 13 had none either (why not logged then).
- **Cause 2, a pass-over:** 15:52:21 "passing over a corpse purger 0x0069C406: it can't see us from 8 tiles; skipping
  it for 60 s", 15:52:29 backed off from at 4 tiles, no patrol: `patrol_pull` skipped anything in `HuntLoop.skip`.
  11 of 11 back-offs from a monster passed over in the 200 lines before had no patrol.
- **Reproduced in the simulator** (that layout: purger at (5265, 653), Logan at (5254, 653), HEAD dce6461): back to
  (5251, 654), 0 patrols, Logan off to (5244, 658); from 13 tiles the patrol goes at once.
- **Fix (tamer):** `PATROL_SPOT_R` 2 -> 3 (the 2026-10-18 demo's spot was 3 from the purger with Logan 14 from it; the
  pets took monsters 1-3 off the spot in 5 of 5); `back_off` prefers, out of sight, a tile with a patrol spot in reach
  ("…, in reach to send the pets at it"); `patrol_pull` no longer checks `HuntLoop.skip` (its own `patrol_failed`
  keeps a refused or timed-out patrol off); it logs why none goes out at an idle purger or blightmare within 14 ("no
  'All Patrol' at the corpse purger 0x… N tiles off: <why>"). Tests: test_sim `test_patrol_after_back_off`,
  `test_patrol_after_pass_over` (both fail on dce6461).
- **Simulator, 144 runs a side** (seeds 17-160, `--others --eerie --prior <main store>`, `-- --vet-range 5
  --crawl-learn-s 180`, on dce6461's simulator; "radius 3" = `--set tamer.PATROL_SPOT_R=3`):

  | Variant | Runs with a death | Deaths/h | Pet deaths | Gold banked/h | Kills/h | Purgers killed | Patrols | Back-offs | Shy dmg on Logan per shy kill |
  |---|---|---|---|---|---|---|---|---|---|
  | HEAD | 14 | 0.22 | 4 | 119.8k | 110.4 | 1,152 | 247 | 1,164 | 4.35 |
  | HEAD, radius 3 only | 10 | 0.15 | 6 | 121.6k | 111.5 | 1,269 | 416 | 699 | 5.37 |
  | back-off + pass-over fix, radius 2 | 6 | 0.09 | 9 | 124.3k | 113.1 | 1,284 | 401 | 570 | 5.10 |
  | **back-off + pass-over fix, radius 3 (shipped)** | 7 | 0.11 | 7 | 125.4k | 113.0 | 1,334 | 497 | 517 | 4.27 |

  With radius 2 the back-off drew Logan to 12-13 from the shy monster, inside an awake blightmare's 12 and a purger's
  13 (`CASTER_R`, `RANGED_R`): more of their damage per kill (seed 25: 0 -> 291 from the blightmares and disease).
  Radius 3 keeps him at 14 and the damage per kill at HEAD's. Pet deaths 4 -> 7 in 66 h [INFERENCE: noise at these
  counts; watch live].

## "All Kill" then "passing over" a few seconds later (live 2026-10-25 13:02-16:36, two runs)

- **Symptom (the Seer):** a monster on another player got "<it> is fighting <who>: 'All Kill' at it", then 1-5 s later
  "passing over <it>: <who> has it at N %, its loot is theirs" and "'All Kill' target … dropped: the pets off guard
  since; 'All Guard Me' due". E.g. 16:33:38-43 a nightstalker 0x006A240B (A, 52 %), 16:34:32-38 a tormented soul
  0x006A1F78 (A, 66 %), hunt-20261025-152542-5ab5.
- **Not a stale hits read** (the Seer's guess). Capture session_20261024_225113, 0xA1 for 0x006A240B: 86 -> 81 % at
  the "All Kill" (its intent at 1792964018.98), 52 % at 1792964022.34, dead at 1792964029.97 (about 5 % a second);
  0x006A1F78 had no 0xA1 before the "All Kill" (full hits) and was at 99 -> 66 % over the next 3 s. Both were above
  `THEIRS_BELOW` 0.8 when the pets were sent; the share is read again on every fight tick while they run there
  (the branch only runs while no pet of ours is beside it), and the hits lost meanwhile are partly the pets' own.
- **Count** (both runs' task logs): 34 such "All Kill"s, 25 dropped by a pass-over 1-5 s later at 52-79 %; of those
  killed in view, 11 corpses were looted by us all the same and 10 were blue, 4 died out of view.
- **Fix:** no pass-over while our "All Kill" at it is under `KILL_REPEAT_S` 12 s old (`Team.fight`); after that, with
  no pet beside it, the share counts again. test_sim `test_theirs_after_kill_sent` (fails before: passed over at 50 %
  3 s after the "All Kill").
- **Simulator, 200 runs a side** (`--runs 200 --careers 20 --others --eerie --team nightmare_fire --prior <main store>
  -- --vet-range 5 --crawl-learn-s 180`; before = 392fafd):

  | 200 runs | gold banked/h before / after | deaths | pet-death runs | kills/h | "All Kill" at one on others | lost kills |
  |---|---|---|---|---|---|---|
  | seeds 1001-1200 | 116,336 / 117,472 | 9 / 6 | 8 / 6 | 112.0 / 112.5 | 230 / 216 | 238 / 224 |
  | seeds 2001-2200 | 115,385 / 115,223 | 11 / 11 | 7 / 5 | 111.7 / 111.5 | 260 / 310 | 231 / 271 |
  | pooled | 115,867 / 116,361 | 20 / 17 | 15 / 11 | 111.9 / 112.0 | 490 / 526 | 469 / 495 |

  Neutral in the simulator (gold +0.4 %, per-run SE about 2 %); its other players out-damage the pets more than live
  [INFERENCE: 63 of 63 sim kills after such an "All Kill" were blue, live 10 of 21], so the live gain is the corpses
  that were ours yet left by the call-off and the "All Guard Me" round trip.

## The instrument wore out mid-run; the replacement unknown by graphic (live 2026-10-25 17:24-17:29)

- **Break** (hunt-20261025-172209-8509, capture session_20261024_225113): at 17:24:23.88 a Discordance answered with
  cliloc 502079 "You broke your instrument trying to play it." and "You fail", then the 0x1D of 0x672A0FE1, Logan's
  only instrument, a "durable exceedingly melodious psaltery" (graphic 0x4E2A, hue 0x0973 [from its 0x25]; durable
  ones wear out too). The runner logged nothing until the next skill's prompt, 24 s later: "no instrument in the
  pack: the bard skills and songs are off" and a `low_supplies`. The Seer stopped the run --after-trip.
- **0x4E2A wasn't in `tamer.INSTRUMENT_GRAPHICS`**: a spare psaltery would have been reported as none just the same.
- **The first home check refused a valid instrument**: at 17:28:28 (hunt-20261025-172827-76de) "no instrument in the
  pack: not going in" with the user's new "supremely melodious bamboo flute" 0x68D40079, graphic 0x2805, in the
  main pack. Fixed the same minute: `Team.instrument` takes graphics (0x2805 added) or a name matching
  `INSTRUMENT_NAME` (lute, harp, drum, tambourine, psaltery, flute, bagpipes); checked against Logan's live state
  (it finds 0x68D40079).
- **Runner now**: "the instrument broke (…)" logged after the skill that broke it (`Team.broke`, counter
  `instrument_broke`); no recall in from home without an instrument while any bard skill or song is on
  (`HuntLoop.check_instrument`, `low_supplies` item `instrument`). test_sim `test_instrument` (a lap harp, a psaltery,
  none; fails before on the psaltery).

## The rune tome ran out of charges at home (live 2026-10-25 17:53, hunt-20261025-172927-2bc3)

- 17:53:38 "recall try 1 (cast 1): arrived (runetome, charge, rune Logan's House, 2.51 s)", the drop-off (32,106 gp ->
  0), then 17:53:49 "recall try 1 (cast 1): reagents (runetome, spell, rune Maus Near 2, …)" and "ABORTED: recall to
  Maus Near 2 failed: reagents". The Seer's `act recall 0x444AB1F3 --check`: 0 charges (26 entries); Logan carries no
  reagents. The drop-off's shelf reply listed no recharge [INFERENCE, the Seer: no recall scrolls on the shelf].
- The home recall happened to take the last charge. The runner had no look at the charges before going in: at 1
  charge it would have recalled in and stood at Totem after the crimson gate with no way home and the loot.
- **Runner now** (`HuntLoop.check_charges`, before every recall in): the in and home books glanced at
  (`escape.check_ready`), a charge per recall of the visit (2 for §5's one tome) unless Recall can be cast; else a
  `low_supplies` juncture (item `recall charges`) and the run ends at home. Log "runetome 0x444AB1F3: N recall
  charge(s)" each time. test_sim `test_recall_charges` (36, 2, 1 charges).

## No bard skill at a caster or shy monster the pets fought (live 2026-10-25 21:45-21:49, hunt-20261025-214237-36a7)

User: "Logan patrolled the pets correctly to a blightmare but did not try to discord or peace it", then "the same issue
on the reanimated demon … we never discorded or peaced it which is a huge issue".

- **21:45:50-46:24** "'All Patrol' to (5242, 691), 3 from the blightmare 0x006CC55D", "cover: the blightmare shoots
  us here; to (5240, 698)", "target a blightmare … at 10 tiles", the pets killed it at 21:46:24. Capture
  session_20261025_210102 (`WorldRuntime` replay): the blightmare stood at (5244, 688) on GraveRobber at (5244, 689)
  all fight. **21:48:37-49:39** "reposition: reanimated daemon 0x006CC919 8 tiles off can cast at us here; to (5240,
  705)", the daemon at (5242, 695) on GraveRobber at (5242, 696) till it died. No bard skill at either; Discordance
  and Peacemaking went at a shadow minion, a shadowguard and a vampiric consort in the same run.
- **Cause**: `Team.bard` needs the target in our sight (`bard_sees`); the cover (shy ones) and the reposition
  (casters) had taken Logan out of its sight, and `Team.bard_step` scored a tile that sees it with `stand_score`,
  which counts every caster or ranged monster in war mode that sees the tile: the target too. Seeing the target is
  its sight of us, so no tile was ever as good as here and the step never went (a step needed fewer eyes than here
  after its 0.3 a step). Task logs: "Discordance|Peacemaking|Provocation on a blightmare|corpse purger" 89 at 54
  kills 2026-10-17..21, **0 at 96 kills 2026-10-22..25** (the cover shipped 2026-10-22, a400be6).
- **Runner now**: a due skill at a target not on us gets the step with its own sight of us not counted and no more
  eyes than here (`bard_step(peek=True)`), and the skill goes from the new tile in the same call (the care's cover or
  reposition walks us back out next tick and would undo the step first). Log: "blightmare 0x… out of our sight:
  stepping to (x, y)", then "Discordance on a blightmare: …". A target on us keeps the old rule. test_sim
  `test_bard_peek` (both live scenes: no skill before, the step and the skill after).
- **Simulator** (98 runs a side, seed 17, careers 14, `--others --eerie --prior` the main store, 9dfdbf7's pets):

  | | deaths | pet-death runs | kills/h | banked/h | damage on us/h | pet damage/h | discord/h | peace/h | bard steps |
  |---|---|---|---|---|---|---|---|---|---|
  | before (9dfdbf7) | 11 | 1 | 117 | 125.0k | 1,837 | 14,019 | 122.9 | 55.8 | 3 |
  | after | 6 | 0 | 118 | 128.4k | 1,812 | 13,641 | 133.7 | 66.0 | 1,012 |
- **The vet in the same fight** (user: "We also didn't use vet supplies on GR for quite a while even though he was ~70%
  health"): 21:48:50-49:06 "to a hurt pet: GraveRobber (82%|56%|64%|62%) is only in reach 6 from the awake reanimated
  daemon, out of its sight 10 off here (50% or more); staying out of it", the walk and the vet only at 51 % (21:49:13);
  21:49:30-35 the same at 72 % and 63 %. `tamer.TO_PET_CASTER_AT` (the 00:38 rule, "Dead vetting a pet in two casters'
  reach") 0.5 -> **0.8**: the 92 % hold of that death stays. Simulator: the same batch with the bard fix, 0.8 against
  0.5: deaths 7 vs 6, banked/h 127.6k vs 128.4k, vets/h 132.2 vs 131.0 (noise); `sim/fight0038.py` seeds 1-200 (its
  `hurt` wrapper now passes the world's `ref`): 0 deaths at both, damage on Logan mean 98 at both. test_sim
  `test_to_pet_not_into_caster_reach` walks at 70 % now (held at 0.5).

## The patrol walked away from a fresh reanimated daemon (live 2026-10-25 22:02, hunt-20261025-214237-36a7)

User: "the runner is still going off and patrolling away even when perfectly good high value mobs are right next to it.
About 22:02 it walked away from a freshly spawned reanimated demon to patrol elsewhere which makes no sense."

- **Capture** (session_20261025_210102, `WorldRuntime` replay): a reanimated daemon 0x006CE63B appeared at 22:01:42 at
  (5237, 701), idle (war flag off) until out of view at 22:02:15. 22:02:08, after the blightmare's loot at (5238, 688):
  "crawl: area (5237, 687) depleted …", "crawl: tormented soul 0x006CE523 in view at 12 tiles: patrolling toward it
  ((5243, 673))"; the daemon was 13 off. 22:02:14 "passing over tormented soul …: it can't see us from 6 tiles"; 22:02:19
  the next leg went north to (5249, 626), the daemon 27 off and gone from view; never fought.
- **Cause** (`Crawl.seek`, the scene replayed in the simulator with the store's model): the seek scored by gold a
  fighting minute over the route. The store then: tormented soul 857 gp a kill in 13.1 s (3,940/min), reanimated
  daemon 1,491 gp in 34.7 s (2,581/min, the lowest of the Maus 3 types). Scores 1,922 (soul, 21 route steps) against
  1,475 (daemon, 15).
- **Fix**: score = gold a kill / (fight_s + `SEEK_KILL_S` 20 + 0.3 s x route steps): the time a kill costs. Live
  10-22..25: 37.9 s on Maus 3 per kill against a 15.7 s mean fight (hunt episodes, `fight` events); ~0.28 s a patrol
  step. The scene now seeks the daemon (25.2 against 21.8 gp/s). test_sim `test_seek_by_kill_value` (fails before).
- **Simulator** (98 runs a side, seed 17, careers 14, `--others --eerie --prior` the main store; fafcbc6 against the
  fix): kills/h 118.1 -> 119.2, banked/h 127.6k -> 129.4k (+1.4 %), deaths 7 -> 4, runs with a pet death 1 -> 2,
  damage on Logan/h 1,833 -> 1,937. Within the noise but none worse in gold.
- **What else walks away** (a throwaway audit of the run, 84 patrol legs: the idle non-pet mobiles within 18 in the
  capture at each leg's start): 14 legs headed away from every idle one in view. Most were a monster the fight had just
  passed over for "it can't see us" (`Team.unseen`, 50 of them against 97 kills that run: souls 6 tiles off behind a
  wall), and the elder vampires (never aggro, right). **Tried and not shipped**: "All Patrol" at such a one before the
  pass-over (`patrol_pull` for any kind): patrols 337 -> 1,529 in the batch, kills/h 119.2 -> 114.7, banked/h 129.4k
  -> 125.6k, deaths 4 -> 6. Walking into its sight cost deaths in 2026-10-17's batch ("Targets behind walls").

## The armor aspect ran out mid-run; no barding round a corner (live 2026-10-25 21:16 and 23:31)

- **The aspect** (user 23:16: "Logan's armor is not aspected"; then "make the runner check aspect before a trip and
  refuse to leave home without it"). Store speech: 21:16:06 "Your aspect items revert to normal (no arcane essence
  remaining)." The capture: Logan's five verite platemail pieces (0x67291869-0x6729186D) went from hue 1782 to 2207
  at that second; his ramshead mask (helmet layer) stayed 1700 throughout. The "(Command Aspect N/250000 xp)" lines
  went on after the revert: no sign of it. The Aspect Mastery gump at 23:14:45: Charges 0, every section Command Tier
  14, icon hue 1782; 23:17:57 Charges 20,000 (the user's essence), 23:17:59 the plate back to 1782 (activation, -5),
  23:18:01 19,990. Fixtures `testdata/aspect_gumps.json` `logan_out`/`logan_on`.
- **Runner now** (`HuntLoop.check_aspect`, `aspects.armor_check`, `--armor-aspect` default on): before each recall
  in, the menu read and the worn armor checked: essence left and a worn armor piece in the armor section's icon hue
  (one piece is enough: the revert and an activation re-send every piece; the mask never takes it). Off: `low_supplies`
  item `armor aspect`, "armor aspect off: …; not going in", the run ends at home. test_aspects `test_armor_check` (the
  live gumps and suit); the runner path smoke-tested against the fixtures (off for the essence, off for the hue, on).
  Not watched mid-run: the revert line is the evidence for that if wanted.
- **Barding round a corner** (user 23:32: "lots of mobs are fighting the pets and we are not barding any of them ...
  Should be firing off discos and provos"). Capture replay 23:31:26-23:32:02 (hunt-20261025-231940-5e67): Logan at
  (5262, 647) where cover put him, out of two awake corpse purgers' shot; on the pets 4-7 tiles off two purgers and two
  bound souls; one Discordance (23:31:10) in 50 s. The scene in the simulator: none of the four in sight from there;
  within 2 steps no tile saw any; within 4 only (5260, 651), beyond --vet-range of AlaFlambe and in the second purger's
  sight, which the peek counted. **Now** the peek (`bard_step(peek=True)`) goes up to `PEEK_STEPS` 4, counts only the
  eyes that would wake to us (idle first sight, --never-aggro, backspawn), not monsters already fighting, keeps the pets
  in vet range only while a vet channel runs (and may start during one). test_sim `test_bard_peek_corner` (fails before).
  Simulator (98 runs a side, seed 17, careers 14, `--others --eerie --prior`, a361f14 against the change): peeks 1,133
  -> 1,983, discord/h 140.6 -> 151.1, peace/h 52.7 -> 57.4, kills/h 131.8 -> 133.5, banked/h 147.1k -> 146.8k, deaths 3
  -> 4, damage on Logan/h 1,647 -> 1,865 (+13 %): more skills for more of the purgers' stray shots, gold even.
  **Provocation never goes out in the simulator** (0/h both sides; live 56 today): open.

## Dead in a daemon's and a blightmare's reach (live 2026-10-26 00:32:45, hunt-20261025-234615-4525)

Visit 3 on 0f3c2ee, ~18.8k gp lost with the corpse; the pets lived (GraveRobber 59 %, AlaFlambe 67 %). Death juncture
1871: no attacker, no swing. Capture session replay (`WorldRuntime`), task log lines 2836-2897:

- **00:31:54-59** "holding here: reanimated daemon in sight at 10 tiles", then "toward a target: route 2 steps" to 7 of
  it; GraveRobber on the daemon at (5241, 692-693); two of its spells reached Logan at (5244, 686), 6 from the pet:
  100 -> 67 -> 33 in 0.3 s. Potion (74), stand-off to (5247, 687).
- **00:32:10-19** "to a hurt pet: (5246, 687) … 1 into an area hit's reach" (GraveRobber 74 %), Discordance on the
  daemon (success), cover from a blightmare that woke at (5242, 685), then "to a hurt pet: (5239, 687) … 1 into an area
  hit's reach", 8 steps west for GraveRobber at 65 %: held below 0.5 before fafcbc6, walked since (`TO_PET_CASTER_AT`
  0.8). A tormented soul woke on him there (1 tile).
- **00:32:29-30** "to heal the tank: (5235, 686), 4 from GraveRobber, 2 into an area hit's reach" (`stand_off`'s step in
  for a tank below its heal line). He stood there to the end: the daemon at (5240, 687) 5 off, the blightmare at (5241,
  686) 6 off. Area peace 00:32:32 calmed both for ~2 s.
- **00:32:37-42** "stand-off cut short: out of charge reach: no route within 6 steps" three times (no tile clear of the
  daemon's reach within `STANDOFF_MOVES` 6; each try returns True and takes the tick). No "cover:" line after 00:32:14.
- **Damage, 100 -> 0 in 7.6 s** after a potion at 00:32:37.17: 39.47 -27 (on Logan and both pets at once: the daemon's
  area spell), 39.58 -17 (disease, graphic 0x376A, the blightmare's), 40.28 vet channel on us begun at 56 (the potion
  clock had 7 s to run), 42.81 -30 (again on all three at once), 44.74 -28 (graphic 0x091D). Dead at 44.84.
- **Not the peek**: no "out of our sight: stepping" line in the last minute; the bard skills went from where he stood.
- **Cause** [INFERENCE: the order of weight]: the walks to vet GraveRobber (65 % and 75 %) took Logan into the reach of an
  awake daemon's area spells beside an awake blightmare, where no tile clear of it lay within 6 steps; there the two
  casters' damage outran a potion clock. The 0.8 threshold (the user's ~70 % vet, 21:49) allowed the first walk.
- **Simulator** (`sim/fight0032.py`, the scene from 00:32:08; seeds 1-200): no death at either threshold (0.8: damage
  on Logan mean 38, walks to a hurt pet 60, held 266; 0.5: 30, 38, 323). The simulator's daemon stayed by its spawn and
  its spells didn't reach Logan as live: the death isn't reproduced, so no fix is proven by it.
- **After the death** (2026-10-26): his played flute 0x68D40079 went with the corpse. A second, looted "durable supremely
  melodious bamboo flute" 0x6A2E1957 (from a corpse at 00:12:02) and "supremely melodious drums" 0x6807160E sit in the
  magic item recycler 0x6264BB54: the 00:19 drop-off's recycler step took the flute. The user re-geared him (platemail,
  an agapite gorget, Command aspect on: "armor aspect: Command on arms, chest, gloves, gorget, legs; 19668 Arcane
  Essence"). Run hunt-20261026-184101-3117 then refused at home: "no instrument in the pack: not going in"; his only
  one was a "supremely melodious shawm" 0x67F5BCA3, graphic 0x4E2E, known to neither `INSTRUMENT_GRAPHICS` nor
  `INSTRUMENT_NAME` (user 18:43: "the shawm is an instrument"). Both now have it; the name pattern takes "drums" and
  "sackbut" too. test_sim `test_instrument` with a shawm (fails before).

## Dead in the master reanimator's room again (live 2026-10-26 19:30:23, hunt-20261026-184350-3c20)

Visit 3 on 4520b6a (the shawm), 9,571 gp lost with the corpse; the pets lived (AlaFlambe 100 %, GraveRobber 90 %).
The room of the 2026-10-25 00:38 death ("Dead vetting a pet in two casters' reach"): Logan died 2 tiles from that spot.
Capture session_20261026_183552 replayed (`WorldRuntime`):

- **19:30:06-11** a shadowguard 0x0006221E at (5235, 723) passed over ("it can't see us from 6 tiles"), then "holding
  here: shadowguard in sight at 6 tiles: letting it come"; it woke on the pets (19:30:11); an idle master reanimator
  0x00062483 at (5230, 722), 11 off.
- **19:30:13** "You have been ensnared and diseased!" (effect 0x5683 on Logan), -23 (100 -> 77); potion at 14.17
  (100). 15.27 Discordance on the shadowguard (success); the reanimator in war mode from 15.7.
- **19:30:16** "reposition: shadowguard 0x0006221E 5 tiles off, master reanimator 0x00062483 10 tiles off can cast at
  us here; to (5241, 726) (2 -> 0 in reach)". By our map (`los.Sight`, server rule and Wall-strict alike) neither sees
  (5241, 726): the line crosses a NoShoot wall piece 0x00C5 at (5240, 726) (flags 0x6050), from the reanimator's tile
  and from every tile beside it.
- **Damage there, 100 -> 0 in 5 s**: 18.25 -20 (disease tick, 0x376A), 19.21 -31 (effect 0x36BD, an explosion, on Logan
  alone), 19.54 vet channel on us begun at 49 (the potion clock had 5 s to run), 20.88 -33 (0x3709, a flamestrike
  column, on Logan; GraveRobber -24 0.1 s before), 23.25 -18 (disease). Dead at 23.30.
- **Same signature as 00:38 on 10-25** (Logan at (5243, 722), the reanimator then at (5239, 721)): spells landed on a tile
  our map says the casters don't see; both times a wall along x 5240 (0x00C5 / 0x6685, NoShoot) is the block.
  [Settled in the next section: the spells were begun while it saw him, 1.3-4.5 s before they landed.] `tamer.AREA_R`
  has no master reanimator, so no stand-off keeps Logan off its victims (its chain lightning ignores walls).
- Both of 2026-10-26's deaths ended the same way: under two awake casters, the potion clock running, a vet channel on
  Logan below 50 % counted as "a heal coming", so no leave.

## How the user plays the casters and purgers, and what the captures show (2026-10-26 19:45-20:30)

The user took Logan over at 19:45:21 (run hunt-20261026-193645-612f paused) and fought the master reanimator's room:
"I managed to fight that room from the same area just now without taking any damage"; "I keep the pets between me and
mobs so mobs aggro on the pets, I don't walk over to other mobs while our pets are fighting (i.e. I avoid being sighted
by hostiles)"; "I move to spots where if a mob respawns (like the dreaded blightmare) I won't be standing in its
sight". Throwaway replays of session_20261026_183552 and a scan of the Mausoleum captures since 2026-10-15:

- **The user's fight (19:45:21-19:46:20)**: from (5241, 717), north of the room and out of sight of the reanimator at
  (5234, 725) and a shadowguard at (5231, 723), "All Patrol" into the room (19:45:27); both woke on the pets. Then he
  stood at (5241, 726) (the 19:30 death's tile) and in the doorway (5241, 723) in plain sight of both, Discordance on
  each, Peacemaking on the shadowguard: no damage. Their aggro was on the pets.
- **Metrics** (sampled every 0.5 s with an awake monster within 14; damage per such minute): the user 9.3 min (4.2 in
  a fight): 26/min on Logan, a pet nearer each awake monster than he 92 % of the time, "All Patrol" 8 times, no disease
  line; the runner 19:36:45-19:45:20 (4.3 in a fight): 90/min, 71 %, "All Patrol" once, 3 disease lines; the runner
  18:44-19:30 (21.3): 59/min, 84 %, "All Patrol" 5 times in 46 min. Distances to the nearest awake monster and the
  share of time seen by none were about the same (median 4; 19-33 %): the difference is who has the aggro, not how far.
- **The reanimator's walls hold** (user: "it can't see through the walls ... I just tested several tiles across the
  wall from it and it didn't aggro on me"; chain lightning, the bolts on Logan and the pets at once, ignores walls).
  Of the 39 explosions and flamestrikes on Logan alone 10-15..10-26, 20 landed with it in sight by our map; of the 19
  that didn't, 18 came 1.3-4.5 s after it last had sight of him (explosion median ~3 s, flamestrike 1.3-2.9 s): the
  spell was begun while it saw him and landed after he stepped out of its sight (user: "Maybe it actually cast the
  spell when it briefly saw me and there's a delay before it hits... that's certainly the case for explosion").
  At 19:30:16 the reposition walked Logan from the doorway to (5241, 726): seen on the way, hit there. So a step out
  of a caster's sight saves nothing for ~4.5 s, and a walk that crosses its sight is the hit.
- **The corpse purger's barrage** (user: "It looks like it targets the ground where the player/pet it aggro'd on stood
  and shoots an AOE disease effect"): a volley of 4-14 moving body-part effects (0x1DA4, 0x1E19, 0x09B7, 0x09B9, 0x160A,
  0x1DB1, ...) from the purger to ground points around its target's tile at the throw (spread median 2), then the
  burst ~0.8 s later (0x377A at the centre, 0x372A "You have contracted a foul disease!" on who stands in it), and
  disease ticks -11..-17 every ~0.3-1 s that stack (the user's 19:54:46-57 death: two volleys, the second centred on
  his own tile; 100 -> 0 in 11 s while walking away). 933 volleys: hits on whoever stood 0-1 tiles from the centre
  38-45 %, at 2 or more 5-16 % (noise of other damage included).
- **Damage on Logan by the runner's activity** (the latest task-log line before each hit, 10-22..26): spread over
  vets, fights, patrols, spacing, stand-offs and the rest; no single action stands out that way.
- **Vet channels and steps** (user: "you can walk up to the injured pet right as a vet supply tick is about to land, you
  don't have to hang out next to a reanimated demon very long"): the runner's channels 18:44-19:30 with 2-7 steps in
  them healed pets 3-4 tiles off at the finish (+140 d3 after 2 steps, +77 d3 after 6, +124 d4 after 2): a step doesn't
  cut the reach to 1, as the simulator has held since 2026-10-20 (126 of 128). The runner's "never mid-channel (a step
  shortens its reach to 1)" gates are stale. The begin's reach for a pet farther than --vet-range is not established
  live (the simulator lets a channel begin with a hurt pet within range + 3).
- **Built 2026-10-26** (the user's choice of five): the casters that stand and cast made shy (`aggro.SHY`: master
  reanimator, shadowguard, banshee, shadow minion, insane reanimator: back off from their first sight, "All Patrol"
  the pets at them, cover from their shots, the to_pet caster rule kept); `tamer.AREA_R` corpse purger 3 and master
  reanimator 3; a purger's and a blightmare's spawn spots in the backspawn rule within 12; no loot walk into an idle shy
  monster's first sight. The walks keep off awake shy casters' reach through the crawl's soft danger zones. Simulator
  (196 runs a side: seeds 17 and 1001, careers 14, `--others --eerie --prior`; aa4a846 against the change): deaths 9 ->
  5, damage on Logan/h ~1,920 -> ~1,690 (-12 %), gold banked/h 145.4k -> 138.2k (-5 %), kills/h -4 %, pet-death runs
  1 -> 0. Corpsebride damage on him rose (seed 17: 244 -> 342/h). Each alone (seed 17): the shy casters dmg -18 %, gold
  -5 %, 0 deaths; the area zones gold +1 %, dmg -1 %; the purger/blightmare spawn spots dmg -5 %, gold 0; the other shy
  casters' spawn spots too: gold -8 %, deaths 2 -> 5 (left out). The simulator doesn't reproduce today's live deaths
  (base: 2-7 deaths in ~45 h; live 3 in ~4 h): the live runs are the test.
- **Why the user patrols, and the other way to take the aggro** (user 2026-10-29). He sends the pets on "All
  Patrol" for two reasons.
  - To keep the pets away from him when a monster like the corpse purger throws an area hit on them.
  - To isolate one monster and pull it away from a dangerous pack: patrol the pets out, let the monster he wants
    aggro on them, then "All Follow Me", so the pets come back to him with only that monster following (how he avoids
    fighting a whole pack in a crowded room, e.g. the Darkscale area on Maus 4; an example, not a request for the
    runner now).
  - For a single shooter or caster, his other way doesn't need a patrol. Pets on "All Guard Me", walk up, then
    step back a little so the pets end up between Logan and the monster. In his words, a monster takes "something
    like 1 or 2 seconds" from seeing us to aggroing on "whatever is closer to them, Logan or the pets".
  - Measured 2026-10-29 (next section): it holds. The war flag comes 1.6-3.2 s after the first sight (median 2.1),
    and the first target is the nearest of us in its sight then, not the first seen.

## Shooters' and casters' first target: the delay and the nearest (2026-10-29)

The user's claim (previous section, last bullet) against the runner's shy rule (`aggro.View.first_on_us`, "first
sight takes whoever they see first"). Scanner kept: `python harness/sim/wake_scan.py --out <rows>` then `--summary
<rows>` (module docstring). It covers the 84 captures since 2026-10-15: 33 with a wake used, 8 of them Tidal Tomb
(20261024_102346, 20261024_225113, 20261025_210102, 20261026_183552, 20261027_220057, 20261028_165135,
20261028_182147, 20261029_100942). 20261015_165115 doesn't replay.

- **Method.** `replay.timed_packets` goes through `WorldRuntime` while Logan stands in the Mausoleum or Tidal Tomb box.
  Every mobile's position is kept on each change (Logan's on the 0x22 confirm). Each monster's war flag comes from
  0x20/0x78 flags 0x40: Outlands' 0x77 has no flags. Also kept: the 0x6E animations, the 0xC0/0x70 effects, the
  soulcatcher's 0x9FE sound, the '-N' numbers, and our C2S walks, 'All …' speech and target answers, each with its
  sender (`client` = the user by hand, `agent` = the runner).
  - A **wake** is a shy monster's (`aggro.shy`) war flag going on after it was seen idle: either from its first
    sighting or after 10 s idle. In a fight the flag flaps on and off between casts.
  - **Sight** is `los.Sight`, the server's Map.LineOfSight: Window|NoShoot statics block, both eyes at z + 14 on the
    map surface (`los.surface_z`), ground items left out. It is checked at every change point of its, Logan's and the
    pets' positions.
  - **The onset** is the first change point with one of us within 10 and in its sight. At that point a step goes into
    range (370 onsets) or into line of sight (224). "Held" means the sight stayed unbroken from the onset to the flag
    (502).
  - The half round trip cancels: the onset step's confirm and the war flag both arrive half an RTT after the
    server's event [INFERENCE].
  - **Acts** count as a target only when they name one: a moving effect from it at a mobile serial (`shot`, the
    mariner's 0x79C5); a volley at ground points (the calamity's, the purger's), taking the mobile nearest the
    volley's centre and unique; or an explosion, flamestrike or lightning on one of us, or a '-N' on one of us
    (`fixed`/`number`), within 3 s of its 0x6E, with no other monster animating within 14 and none beside the victim.
    A first act that lands on several of us at once (chain lightning, the banshee, the zoas' lightning, the
    mariner's within 1) names no target. Spell sounds on our tiles were left out: 68 of 106 "victims" were AlaFlambe
    and the imps hearing their own casts.
  - **Nearest** is Euclidean (RunUO's GetDistanceToSqrt), among those of us in its sight within 14. "Nearer" means by
    1 tile or more.
- **Wakes: 1,870 flips after idle, 602 used** (540 Mausoleum, 62 Tidal Tomb; 478 with the runner driving, 111 by
  hand; the pets' mode was 'All Guard Me' 397, 'All Follow Me' 136, 'All Patrol' 57, 'All Kill' 9). Left out:

  | reason | wakes |
  |---|---|
  | a bystander (another player or a pet) in its sight first or nearer | 414 |
  | unnamed (no name packet: the type is unknown) | 212 |
  | one of us in its sight already when it was first seen | 174 |
  | already awake when first seen | 174 |
  | none of us in its range and sight by our map before the flip | 171 |
  | no pet within 18 | 64 |
  | packets missing (an S2C gap over 2 s) | 46 |
  | hit, shot or targeted by us before | 11 |
  | Logan beyond 18 | 2 |

- **The delay (a).** From the onset to the war flag, in seconds:

  | set | n | p5 | p10 | p25 | p50 | p75 | p90 |
  |---|---|---|---|---|---|---|---|
  | all | 602 | 1.67 | 1.74 | 1.93 | 2.19 | 2.64 | 4.18 |
  | sight held to the flip | 502 | 1.66 | 1.73 | 1.90 | 2.12 | 2.47 | 3.19 |
  | Tidal Tomb, held | 48 | 1.97 | 1.99 | 2.12 | 2.34 | 2.59 | 3.35 |
  | from Logan's own onset | 492 | 1.64 | 1.72 | 1.92 | 2.16 | 2.59 | 4.11 |
  | onset to its first 0x6E | 600 | 1.81 | 1.93 | 2.16 | 2.45 | 3.01 | 4.37 |
  | onset to its first act landing | 434 | 2.25 | 2.41 | 3.37 | 5.07 | 8.68 | 19.2 |

  - **A floor at 1.6 s.** Of the 502 held, 12 fell under 1.6 s and 4 under 1.0. The held delays spread about evenly
    over 1.6-2.7 s, with a tail to ~4.
  - **The low ones are sight edges.** 6 of the 11 under 1.5 are banshees, 5 of them on a step into range at 10
    [INFERENCE: its range a little over 10]. The rest step into line of sight, where our map may be wrong.
  - **Its swing or cast begins with the flag:** the first 0x6E comes at a median +0.04 s, 90 % within 1 s.
  - **Alike for the runner and by hand** (held p5/p50/p90: 1.66/2.12/3.22 and 1.68/2.13/2.80), for steps into range
    and into line of sight (p50 2.14 and 2.26), and for the 121 onsets that cross a Wall-only static (p50 2.23).

  Per type, held sight to the flag (p10/p50/p90): shadowguard (130 wakes) 1.89/2.36/3.55, calamity (103)
  1.67/1.97/2.26, corpse purger (66) 1.66/1.88/3.13, banshee (65) 1.48/2.31/3.99, shadow minion (58) 1.69/1.94/2.88,
  master reanimator (56) 1.86/2.23/2.58, blightmare (41) 1.78/2.22/3.46, insane reanimator (21) 1.81/2.22/2.59,
  drowned mariner (20) 2.02/2.21/2.98, soulcatcher (14) 1.98/2.41/3.67, combustive zoa (9) 1.98/2.37/2.99, astral zoa
  (9) 1.98/2.24/3.09, the other zoas (10) 2.07/2.40/2.85.
- **The shadow minion's and the calamity's range is 10, not 13.** `aggro.aggro_range` gives them 13. In their 161
  wakes, one of us was within 10 before every flip, and the delay was tightest counted from 10 (IQR 1.75-2.13) against
  2.30-3.93 from 13 [INFERENCE: 13 is where they cast from, not where they wake].
- **The first target (b).** 434 of the 602 had an act. Its first act named one target in 364 (volley 87, fixed 101,
  number 150, shot 25, the soulcatcher's sound 1), 271 of them within 3.5 s of the flip. How Logan stood against the
  nearest pet in its sight at the flip:

  | at the flip | Logan taken | a pet taken |
  |---|---|---|
  | a pet nearer by 2 or more | 2 | 38 |
  | a pet nearer by 1-2 | 0 | 9 |
  | a pet nearer by under 1 | 17 | 18 |
  | equal | 4 | 4 |
  | Logan nearer by under 1 | 71 | 35 |
  | Logan nearer by 1-2 | 35 | 10 |
  | Logan nearer by 2 or more | 4 | 1 |

  - **With a clear nearest, the nearest is taken 72 times in 91** (by chance 34; 50 of 62 when the first act came
    within 3.5 s).
  - **A pet a tile or more nearer and in its sight takes it: 47 of 49.** Logan was taken while a pet was 1+ nearer in
    2 of 49. A pet was taken while Logan was 1+ nearer in 11 of 50: GraveRobber, the tank, came within a tile of it
    in the 0.5 s after the flip and took the spell.
  - **Under a tile the choice is a coin, tipped to Logan:** 71/35 when he is the slightly nearer, 17/18 when a pet
    is.
  - **The flip is the moment that predicts it best.** Nearest at the onset: 109 of 152; 1 s before the flip 79 %; at
    it and +0.5 s 79 %; +1 s 69 %; +2 s 57 %; at the landing 67 %, since by then the pets have closed in.
  - **"First seen takes it" doesn't hold.** When Logan alone was seen first but a pet was the nearest at the flip, a
    pet was taken 7 of 7 (the nearest 5, the other pet 2).
  - The first victims were Logan 175 and the pets 189 (GraveRobber 146). That is the runner walking in ahead of
    pets that trail a tile behind: 185 of the 364 were under-a-tile ties at the flip. Tidal Tomb alone: 36 named
    first targets, pets 31, Logan 5. Of its 9 with a clear nearest, 7 took the nearest.
- **Retargets (c).** Of the 322 with two or more named acts in the next 30 s:
  - 163 kept their first target.
  - 159 switched, 3.9 s after the first act at the median (p10 1.8, p90 10.2). 112 of the switches went to the new
    nearest (Logan to a pet 101, pet to pet 9, pet to Logan 2), 16 away from the nearest and 31 with no clear
    nearest.
  - So the pet closing in takes it off Logan within ~4 s. The simulator's `RETARGET_P` only turns a monster from a pet
    to Logan.
- **The user's tactic (d): Logan alone seen first, then a pet the nearest in its sight at the flip** (a pet 1+
  nearer, or Logan back out of its sight). 11 cases, a pet taken in all 11. 10 had the runner driving: in 7 Logan had
  stepped back out of its sight by the flip, in 3 a pet had come nearer. 1 was by hand.
  - Held and prompt: 10-19 11:22:26 calamity (Logan seen at 7, at the flip Logan 9.5 and AlaFlambe 8.0; 1.66 s; the
    volley on AlaFlambe +0.53 s).
  - By hand: 10-26 22:36:50 drowned mariner (Logan seen at 10, stepped back to 11.4 with GraveRobber at 9.2; 2.00 s;
    the shot on GraveRobber +0.26 s).
  - Logan seen first and still the nearest at the flip: Logan taken 80, a pet 10.
- **The user's own 'All Patrol' pulls (e).** 53 'All Patrol' by hand in seven captures. 21 wakes followed within
  30 s of one, 15 of them usable.
  - Every named first target was a pet. At the flip Logan stood 10.0-16.1 from it and the nearest pet 2.0-10.3; once
    Logan was the nearest (19:48:53, a corpse purger at 10.05) and the first act still fell on GraveRobber. The
    2026-10-26 19:45:27 patrol woke the master reanimator (19:45:32, 2.24 s after its first sight of us) and the
    shadowguard (19:45:35, 2.14 s, its first act on GraveRobber).
  - The two first acts on Logan (19:53:25 the master reanimator's chain lightning, 22:43:03 a soulcatcher) came
    14-16 s later and hit several of us at once.
  - An 'All Follow Me' came within 30 s of the patrol 7 times of 53 (4.5-29.9 s). The scan saw no isolate-and-lead-back
    pull to count.
- **Conclusion: the claim holds for every shy type with enough wakes** (the Mausoleum's eight; on Tidal Tomb the
  mariners, the soulcatchers and the zoas, n 9-20 each and alike).
  - The delay is ~2 s (1.6-3.2), with a floor of 1.6 s in the Mausoleum and 2.0 s on Tidal Tomb. Confidence high:
    602 wakes, one floor across types, drivers and edges.
  - The target is the nearest of us in its sight at the flip when one is a tile or more nearer. Confidence moderate:
    by its named acts; 49 + 50 cases with a clear nearest, and Tidal Tomb's 9 are few.
  - A runner rule can count on **1.5 s** (11 of 502 held came earlier) from the step that first puts Logan in an idle
    shy monster's range and sight.
- **The rule it implies** (sketch, not built):
  - Replace the shy rule's "never be first in its sight" (`first_on_us`, `Team.back_off` from its first sight) with a
    planned step-in and step-back. Pets on 'All Guard Me'.
  - Take the step that puts Logan first in its sight only with a step-back tile planned before it. From that tile,
    within 1.5 s less the runner's round (0.35-0.74 s live), Logan is either out of its sight with a pet in it, or a
    tile or more (Euclidean) farther than a pet in its sight. Then hold until its first act lands on a pet.
  - A near tie (under a tile) isn't good enough: Logan is taken 2 times in 3.
  - If Logan is taken anyway, the tank closing to within a tile takes it off in ~4 s (median; p90 10).
- **The simulator model it implies** (sketch, not built; docs/SIM.md "Tank draw" and `world.RETARGET_P`):
  - `defaults.wake_s` 1.5-3 → about 1.6 + U(0, 1.1) s, with a tenth beyond 3.2 s (Tidal Tomb about 2.0-2.7 s,
    a tenth beyond 3.3).
  - The target is chosen at the end of the delay: the nearest of us in its sight by Euclidean distance, not the
    first seen. A tie under a tile goes to Logan 2:1 when he is the slightly nearer and 1:1 when a pet is; the
    simulator gives a tie to the pet.
  - A retarget to the new nearest of us in its sight goes either way, mostly a closing pet taking it off Logan
    (101 of 159 switches, median 3.9 s). `RETARGET_P` covers only pet to Logan.
  - The shadow minion's and the calamity's wake range is 10 [INFERENCE].

- **Built 2026-10-29** (docs/SIM.md "The shy monsters' wake, and the step-in"; docs/HUNT_LOOP.md "Aggro").
  - The simulator's model as sketched above (8cfcb41). Its tables against these (`wake_scan.py --sim`): delay p50
    2.2 s Mausoleum, 2.3 Tidal Tomb, none under 1.6; a pet 1+ nearer at the flip taken 484 of 492 (Tidal Tomb); near
    ties 65-71 % to Logan when he is the slightly nearer. Off: Logan is the first target less often than live
    (Mausoleum 38 % against 52 %), and the switch comes later (median 6.1 s against 3.9).
  - HEAD's runner on the new model, level 2: runs with a death 18 -> 21, pet deaths 255 -> 326.
  - The step-in as the runner rule (a698a73), planned as above with a 0.75 s margin; the runner's own planning is
    17-30 ms, so the steps back go out pre-planned. Level 2 (144 runs): runs with a death 21 -> 19, pet deaths 326 ->
    264, damage on Logan an hour 1,191 -> 1,100, banked +1 %; Maus 3 (96): damage on Logan 1,408 -> 1,266, banked
    +1 %; level 1 unchanged (no shy monster). Of 416 step-ins on level 2, 191 ended with a pet hit first, 121 with a
    hit on Logan (area hits on the pet beside him included), 85 with no act in 10 s. Without the back-off as its
    fallback: 31 runs with a death.
  - The `first_on_us` rule elsewhere (walk stops, stand scores) still counts a pet a Chebyshev tile nearer as enough.
  - **The pass** (built 2026-10-29 evening, `tamer.Team.pass_by`; docs/SIM.md "The shy monsters' wake, and the
    step-in"). `test_walk_in_fought` failed on the new model, traced: after 3 back-offs the fought leg went on past the
    drowned mariner at (6852, 2072) (`Team.faced`: no stop, no step-back for it), the pets 2 tiles behind Logan up a
    corridor with no way round its sight; its clock started with Logan at 10 tiles and it woke 2.0 s later with him
    1.0 off it and both pets 1.41, a near tie. Now the leg plans one walk of up to 12 steps with the pets followed
    (the onset may be a pet's; Logan's steps in its sight with no pet a tile nearer within the step-in's budget after
    each onset), walks to just before the onset, lets the pets close up and plans again; with none, on past it as
    before. On level 2 (three 144-run blocks) the mariners' and soulcatchers' wakes on Logan within 2.5 tiles 152 ->
    118 and his deprivation -10 %, but runs with a death 56 -> 71; with no memory carried between runs 13 -> 13 and
    the differing runs 3 -> 3 deaths.

## The Healer's Codex, the Taming Bestiary and the Guide: Logan's gumps (read 2026-10-25, sim/tamer_scan.py)

The memory store keeps every gump the client opened (`events`, ev `gump_open`, data {gump_id, layout, lines, serial,
x, y}; the answers as `gump_response` {gump_id, button_id, switches, texts}). `lines` holds each distinct string once,
so a value is read through its element's line index, never by its position in `lines`.

- **Healer's Codex**, gump 1244441914, 2 pages. A perk row: 0-3 gems (`tilepic X Y 3617`), "N Pts" (a text at x 70),
  then its name (the next text at x 130); the tier is the gem count, the points 0/1/3/5 for tiers 0-3. Page "k/2" at
  (213, 528), points spent "a/20" at (77, 605); button 9 is Reset Spent Points. Logan's since 2026-10-17 22:06:15
  (session 150, seq 4485 page 1 and 4474 page 2): Overheal, Triage, Specialist and Without Borders at tier 3 (20/20),
  Combat Medic, Immunize, Pressure Points and Self Treatment 0.
- **Taming Bestiary**, gump 1353229584: per loadout three tabs (Attack button 10, Utility 11, Tank 12; the tab shown
  is the button drawn with gump 2154) of 11 pages of 4 perk rows in the codex's grammar (gems `tilepic 55046`; a
  "[Cross] " name is another tab's perk, at most 5 cross points); page "k/11" at (215, 356), points "a/20" at
  (77, 605), cross-class "c/5" at (310, 547). The loadout shown is the select's default item (`select 96 704 252
  <id> 1`, `selectitem <id> @Name@`), the active one the item drawn with ` 63 -1 -1`. A Reset (button 9, twice) or a
  new loadout shows 0/20: the scan rebuilds a tab from its views after the last 0/20 one. Active: Phoenix/Gravebug
  from 2026-10-12 21:01, Random from 2026-10-24 20:38, Nightmare/Fire from 2026-10-25 11:01, Phoenix/Gravebug again
  from 2026-10-25 20:49, Phoenix/Incubus from 2026-10-28 18:25:02 (Attack Immolate 3, Lethality 3, Primal 3, [Cross]
  Behemoth 3; Utility Breaker 3, Opportunity 3, Scour 3, [Cross] Primal 3; its Tank tab never viewed: the team has no
  tank-class pet). Never viewed: the Utility tab of Phoenix/Gravebug (empty, the user 2026-10-25: no
  utility-class pet in that team), Fortress, Manticore/BloodSerp, Water Dragon/Drake, 5 Casters, Flamehounds,
  Drowned/WaterDrake, DebuffBirb; Manticore/Zoon one Attack page only. The perks' texts (each with its numbers per
  rank) are in `harness/data/sim/tamer/logan_wolf.json` `perks`; e.g. Swarm's healing is (1 % / 3 % / 5 %) x the
  followers controlled, Menagerie's (1 % / 3 % / 5 %) x their distinct types.
- **The loadouts changed under the live data** (`tamer_scan.py --as-of`): Phoenix/Gravebug was built 2026-10-12
  21:10, rebuilt 10-15 12:04-12:07 (both tabs reset and refilled) and unchanged until the Attack tab's reset on 10-25
  at 21:01:26, refilled by 21:11:26 to Heavy Breathing 1, Hunting Party 1, Immolate 2, Lethality 1, Mutation 2, Pelt 2,
  Primal 3, [Cross] Behemoth 1, Breaker 1, Swoop 1 (20/20, 3/5); its Utility tab viewed at 21:05:39, empty. Nightmare/Fire's Attack tab was
  Ambush 1, Cornered 1, Hunting Party 1, Immolate 2, Lethality 2, Menagerie 2, Mutation 1, Pelt 1, Primal 2, Swarm 2
  (20/20, no cross) from 10-25 11:01 until its reset at 11:44:02, so the Tidal Tomb level 3 fights of 11:31-11:36 ran
  with that one, not the rebuild with four cross perks. Random unchanged since 10-24 20:38. The simulator's anchors
  keep each as it was (data/sim/tamer/logan_wolf.json `anchors.loadouts`).
- **Levels** (the Guide views): GraveRobber and AlaFlambe 11 on 10-14, 15 from 10-17 23:10 (AlaFlambe) and 10-18
  16:48 (GraveRobber); NightmareA/B 13 on 10-23 21:50, 14 from 10-25 11:20, 15 at 20:48; fireminion 13 on 10-24
  20:34, 15 from 10-25 10:56; a searing imp 10 on 10-24 20:34, 13 at 23:41, 14 at 11:43, 15 at 20:48; GirthyEarthy 10
  until 10-24 20:27 (11), 14 on 10-25 10:56 (not viewed since); the mantis 11 on 10-24 20:32; Bussin (an incubus, the
  user 2026-10-28; 0x000EA04F, body 141) 11 on 10-28 18:36. Level 15 is the top
  (knowledge #1204); its Guide shows no xp bar and puts "Level 15" at (209, 90).
- **Live Mausoleum visits by team** (episodes, loop hunt, the visit's `pets` serials and the loadout active then):
  GraveRobber + AlaFlambe with Phoenix/Gravebug 84 visits, 15.3 h, 10-17 22:10 to 10-24 18:55 (92.6 kills an hour of
  visit); GirthyEarthy + fireminion + a searing imp with Random 6 visits, 2.0 h, 10-24 21:13 to 10-25 00:24 (78.0);
  NightmareA/B + a searing imp with Nightmare/Fire 13 visits, 4.2 h, 10-25 13:03 to 17:30 (82.0). The simulator's
  recalibration windows (docs/SIM.md "Recalibrating").
- **Guide**, gump 3280914284, opened on a pet or a wild creature: the title at y 40 ("Name the Species", or "A
  Species"), "Level N" at (205, 67) with an xp bar, at (209, 90) without one at the top level 15; the Values page's
  rows at label x 75 / value x 170 (y 150 min taming, 170 slots, 190 hits "cur/max", 210 attack speed "2.86s", 230
  "Melee Damage" or "Spell Damage" "lo - hi", 250 wrestling, 270 armor, 290 magic resist, 310 poison and 330 special
  resist). They are the *base* values, not levelled (the wiki's level table makes the live hit bars: GirthyEarthy
  448 -> ~1000, NightmareB 228 -> ~480). A wild creature's adds Barding and Taming Chance; a toggle at (212, 127)
  reads "Base" or "Tamed".
- **The Guide's Abilities page** (its button 3 from the Values page; no title, so the pet is the last Values page's):
  "Aggro" at (69, 28), "Combat Distance" at (140, 28), "AoE" at (292, 28), each with arrows (buttons 3/4, 5/6, 7/8).
  Aggro is `tilepichue 64 45 39892 <hue>`: hue 2117 (red) on, 2104 (grey) off; Combat Distance `tilepic 165 ...`:
  45320 melee, 3643 ranged; AoE 14030 in every view but one (8305 hue 2669 for one click at 18:29:39). Every view
  2026-10-11..28: aggro on for GraveRobber, GirthyEarthy and NightmareA/B, off for AlaFlambe (until 10-28), the imp,
  fireminion, the mantis and Bussin; melee distance on the melee species, ranged on the casters. 2026-10-28 18:25:24 the
  user turned AlaFlambe's aggro on (user: "I switched the aggro setting on AlaFlambe to on, so it is the tank now"),
  and 18:29:43-44 its Combat Distance to melee. Players' word (docs/research/DISCORD_KB.md): red aggro makes a pet
  draw the mobs, grey avoids it; "any pet can serve as a tank as long as its aggro setting is configured properly".
  `sim/tamer_scan.py` reads both settings into the Guide values; the simulator draws aggro by the setting.
- The supplies at a finish (live): the one resurrection seen (2026-10-14 22:35:55.11, capture 20261014_221341) was
  GraveRobber's, at 31 % with Critical Care rank 2 in its tab; a '+4' over it 1.06 s after that finish was an
  Overheal tick (the codex's tier then is in the scan's history).
- The wiki's bandage heal on a creature is (Veterinary / 100) x U(80, 120) x (1 + 0.2 x Lore / 100) (knowledge
  #11327); supplies heal 50 % of it on each patient, 75 % when there is one (#11331).
- **The pets' damage live, measured for the recalibration** (2026-10-25 evening; docs/SIM.md "Recalibrating"):
  - Barding on the gravebug's hits: all 198 of its attributed Tidal Tomb hits (20261024_102346, 10:59-11:14) were under
    the Song of Provocation ("+16.9% damage against creatures for 15 minutes", played five times), 91 on a target
    discorded then. Any number measured on our pets' hits carries these; the simulator applies them itself.
  - Epic Barrage: AlaFlambe's animation action 12 every 13.6-13.9 s in a fight (n 3,487, never under 13.3); three of
    our-hue numbers on one monster 0.04-0.16 s apart, 2,038 of them in 775 war-minutes of the 83 GraveRobber +
    AlaFlambe visits, hits median 213 (192-326). Its casts are action 4, 1.6 s apart.
  - GirthyEarthy: Earth Breath is its action 12, 10.8-11.5 s apart (never under 10.7); Earth Shield action 11, 61-67 s
    apart; its swings send actions 4, 5 and 6 together.
  - AlaFlambe's attributable numbers (no gravebug beside the monster): 24 % of its attacks with any bring two numbers
    at once (spell and Spellburn), 13 % three or more; the numbers' 25-65 % quantiles 196-245, 95 % 768.
  - Mausoleum parity per team: the GraveRobber + AlaFlambe visits killed 93.1 an hour on the level, a type's median
    kill time from 3.3 s (banshee) to 38.1 s (reanimated daemon); Nightmare/Fire 82.0, Random 78.0.
- **Phoenix/Gravebug rebuilt again, 2026-10-25 evening** (the scan's views, `tamer_scan.py --as-of`): Tank tab to
  Mending 3, Stalwart 3, Survival 3, [Cross] Carrion 3 at ~21:38; Attack tab to Immolate 3, Lethality 3, Primal 3,
  [Cross] Behemoth 3 by 21:39:35; Behemoth removed (Attack 15/20, 0/5 cross) between 23:06:00 and the view at 23:06:43,
  put back at ~23:17 by the user. The 4 visits with Behemoth 3 (21:43-22:45, 0.94 h) ran **without Logan's armor
  aspect** (next item): kills 94.7 an hour (the 10-15 loadout's 83 aspected visits 93.1), each monster's kill time
  1.19x (0.85-1.42), damage on GraveRobber 19,926 an hour (11,040), vet uses 157 (130), fights ending in a target
  switch 13.3 % (5.3 %), AlaFlambe's attributable numbers' median 183 (212, n 70; undiscorded under the song 183, n 50,
  against 214, n 1,155). Discords and songs an hour as before. docs/SIM.md "The 21:39 Phoenix/Gravebug loadout".
- **Logan's armor aspect, read from his AR** (0x11 `physical_resist`, Logan's full status packets in the captures):
  AR 133 (148 under the +15 Armor Rating Increase buff) to 2026-10-24 22:07; a suit change then: AR 122 (137 buffed)
  aspected, 80 without; the aspect off from 2026-10-25 21:16:07 to 23:17:59 (AR 80; the user: Logan ran out of arcane
  essence), on again after. No visit from 10-17 23:08 to 10-25 21:16 spent over 5 % of its time below AR 110. The aspect
  only works with arcane essence above zero (knowledge #332, the Harvest aspect's wording). The captures' jsonl keeps a
  64-byte hex prefix of each packet: `world.parsers._p_character_status` fails on a type-5 0x11 there (weight at byte
  64), but AR (u16 at 62) and max hits (39) are inside.
- **The pets' damage per second of fighting, live** (our-hue overhead numbers on monsters, no other player within 18,
  over the runner's fight records' `fight_s`): the 10-15 Phoenix/Gravebug loadout's 83 visits 325 (26,118 numbers, 287
  each); the 21:39 loadout unaspected (21:40-23:00) 272 (264 each), aspected (2026-10-25 23:18 to 10-26 00:33, 6
  visits, 1.05 h) 449 (2,476 numbers, 344 each).
- **The 21:39 loadout aspected, live** (23:18-00:32): 123.4 kills an hour; each monster's kill time 0.91 (0.78-1.02)
  the 10-15 loadout's; GraveRobber 14,548 an hour, AlaFlambe 4,097, Logan 2,564, vet uses 145. GraveRobber's numbers
  per monster type (one war monster in reach): 0.940 (0.900-0.981) of the 10-15 Tank tab's on the means (tormented
  soul 17 -> 16, blightmare 18 -> 18, reanimated daemon 30 -> 32, shadowguard 20 -> 18). The runner stopped at Logan's
  death, 2026-10-26 00:32:45 ("Dead in a daemon's and a blightmare's reach" above); his AR 0 since.
- Behemoth's text: "Follower Damage increased by (8% / 24% / 40%) and Damage Resistance by (1.5% / 4.5% / 7.5%) if
  follower takes 3 or more Control Slots" (knowledge #4243, #10236); an older one, "Melee and Spell Damage increased by
  (10% / 20% / 30%)" (#5808).
- **Dreamlull live** (2026-10-28, Bussin 0x000EA04F, capture 20261028_182147 read 18:26:13-19:24:12 while the shift
  ran; `replay.timed_packets`, 0x6E and 0xC0 parsed; docs/SIM.md "Dreamlull"). Bussin cast 832 times (action 4, median
  1.74 s apart over 701 gaps under 3 s); 542 casts show an effect in his hue 2605 (projectiles 0x36E4 187 and 0x36D4
  186, 0x374A on the target 169), the other 290 none of his hue (hue-0 lightning can't be told apart [INFERENCE]).
  - **No packet marks a proc.** No overhead or system line for it (searched: hex, hinder, lull, sleep, dream, stun,
    root; the "[Hex]" lines are the player Valentino Rossi's title, the 3 "You are no longer hindered." Logan's own);
    the 83 0x17 health-bar rows are all type 1 (poison: 70 on Logan and the pets, 5 on other players, 8 on monsters);
    no 0xDF buff packet at all; against the Phoenix/Gravebug capture 20261027_220057 the new 0xC0 graphics are
    Bussin's spells, sourceless ground effects (0x0EB3 from each song on, 0x0E9E, 0x5418, a 0x377A) and one 0x377A on
    a pincer crab (1 in 832 casts: not a 30 % proc). The 0x376A sparkle on monsters (543 by 19:13) isn't his: 2,376 in
    20261027_220057 and 654 in 20261028_165135, before he was claimed (18:25:05). Outlands' 0xC0 carries the graphic as
    a u32 at bytes 10-13 (the jsonl hex [24:28]). So the proc rate isn't countable; the model's 15 % x Primal 3 = 30 %
    would be ~250 of the 832 [INFERENCE].
  - **The 1 s hinder is too short to see in an hour.** A monster walking (2+ steps in the second before) took its next
    step 0.9 s or more after one of Bussin's landings 11 times in 37 (30 %), after one of AlaFlambe's (no hinder) 4 in
    21 (19 %): Fisher p 0.28. Swings: the regular 3.36 s swingers (pincer crabs, nest crabs, giant pincer crab, singing
    noughtilus) had only 5 gaps with a Bussin landing in the second before the swing was due, none late.
  - **What hex and hinder are** (no live number): hex lowers the target's magic resist (wiki Template:StatusEffects
    oldid 30885 "Reduces Magic Resist on the target"; Arcane Staff oldid 31888 "a Hex effect reducing the creature's
    Magic Resist by 50"; Buff Bar #13536 "Hex: Reduce Magic Resist Skill; Stackable: No"); hinder "Roots the target in
    place and stuns them" (StatusEffects). Debuffs on creatures of difficulty 100+ are cut to 1 - 70 % x (difficulty -
    100) / 900, at least 30 % (#5947); magic resist goes down at most 150 in all (#4223), not below a creature's Magic
    Resist Minimum (#5435, values unknown). The players' archery Hinder "pauses a mob for about 8 seconds" (#3072).
  - The Bestiary's texts (gump 1353229584, session 105 seq 1621, 20261012_210013 21:01:50.62): Opportunity "Follower
    Damage increased by (20%, 60%, 100%) if follower has applied an Entangle or Hinder effect to the target within last
    10 seconds. Follower Damage increased by (5% / 15% / 25%) if follower cannot inflict Entangle or Hinder effects with
    abilities."; Punishment "When follower's Pierce or Hex effects expire on a target, will inflict (DamageMax * (0.5 /
    1.5 / 2.5)) Damage onto them. ... (5% / 15% / 25%) if follower cannot inflict Pierce or Hex effects."; Scour "When
    applying Weaken, Cripple, Pierce, or Hex effects the amount of the effect is increased by (20% / 60% / 100%) of
    normal. ... (5% / 15% / 25%) if follower cannot inflict any of these effects."

## Network observations

- Session profile: one HTTPS auth connection per login (~75 s), then exactly one persistent game TCP. 22 min idle: zero extra connections. Launcher idle: zero connections.
- **VPN exit nodes are SYN-dropped** on the game port (network-layer, no RST) while Cloudflare HTTPS accepts them. Direct IP required to play. Repeated failed attempts are logged server-side — don't hammer.
- Game server observed at `74.91.115.123` (NFOservers). Auth at `104.26.0.46` (Cloudflare).
- 2026-09-30: the client connected straight to `35.71.142.123:2593` at 22:14 and to `52.223.17.219:2593` at 22:26, bypassing a NAT that matched one IP. The server IP changes between logins; the two look like an AWS Global Accelerator anycast pair [INFERENCE]. The NAT now diverts any IP on port 2593, and the proxy dials the original IP via the NAT lookup port 25943 (docs/INTERCEPTION.md). To check whether a session goes through the proxy, run `Get-NetTCPConnection -OwningProcess <ClassicUO pid>`: it shows the real server either way, because the NAT is invisible to the socket. Proof is the proxy pid holding a connection from a local port in 25940–25960 to `<server>:2593`, plus a new `logs/session_*.jsonl`.

## Links

- Captcha: https://wiki.uooutlands.com/Captcha · Razor Scripting: https://wiki.uooutlands.com/Razor_Scripting · Commands (incl. Test Shard): https://wiki.uooutlands.com/Commands · OutlandsID/2FA news: https://uooutlands.com/news/outlandsid-and-infrastructure-updates/ · Upstream: github.com/ClassicUO/ClassicUO, github.com/markdwags/Razor

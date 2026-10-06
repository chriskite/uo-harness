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

## Login & identity

- Flow: launcher → OutlandsID auth over `https://login.uooutlands.com` (Cloudflare) → JWT (claims: `outlandsid`, `purpose`, `mahid` = multi-account household, `mahleader`) → game server `play.uooutlands.com:2593`. New devices require email 2FA (Feb 2026 change); this machine is a recognized device.
- API model names found in binary (System.Text.Json source-gen contexts): `WebLoginRequestModel`, `PanelLoginRequestModel`, `PortalLoginResponseModel`, `PatchLoginResponseModel`, `GameLoginRequestModel/GameLoginResponse`, `ClientLoginRequestModel`, `GetClientInfoResponse`, `LinkRequestModel`, `CreateOutlandsIDModel`, `CreateGameAccountModel`, `GetOutlandsIDFromGameAccountRequest/Response`, `GetTotalPrevCoinsRequest/Response`, `VerifyOutlandsIDModel`, `VerifyDeviceModel`, `VerificationStatusResponse`, `AcceptPrivacyPolicyRequestModel`, `PrivacyPolicyCheckResponseModel`, `TPMModel`, `AccountInfo`, `ServerInfo`. Full list: `api_surface.txt`.
- `ServerClientRestriction`, `VersionRestrictions`, `IsMostRecentVersion`, `IsMostRecentGameFilesVersion`: client/game-file version gating at login — keep files stock and the launcher will handle patching.
- Launcher carries AWS SigV4 signing strings → patch downloads from AWS infra.

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
  2026-09-29). The threshold and the stamina cost aren't measured yet; RunUO requires full stamina
  and costs 10 `[INFERENCE]`. A denied shove comes back as a normal walk reject (0x21), and
  `Mover` logs the stamina with each one. Live attempt 2 failed because the planner treated NPCs
  in the inn's one-tile upstairs hallway as walls (LUMBER_LOOP.md §13).
- **S2C 0x6C with cursor type 3 is a cancel, not a cursor** (ClassicUO TargetManager:
  IsTargeting = cursorType < 3). The server sent one after a moongate Travel reply. The world
  model showed it as an active cursor until 2026-09-30.
- **Buffs on Outlands arrive as 0xFF sub 8/9** (OutlandsBuffUpdate/RemoveBuff), not 0xDF (none
  in the live session). Examples: "Stationary Penalty", with the description "All damage is
  reduced to 1. Move {value} more steps to remove this effect", timer value 5, f2 = 1; and a buff
  titled by cliloc 1075655. `{value}` is `timers[0].value` (decoded 2026-10-03, see "Stationary
  Penalty decoded" below; the parser called it `seconds` until the evening of 2026-10-03, but it is
  never a duration: the timer's `end` is, docs/WORLDMODEL.md sub 8); f2 is always 1.
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
- Digits are fixed shapes with dots displaced — template-matching territory. **Solver built 2026-09-30: `harness/captcha.py` reads the digits from the gump layout's tilepic dot clusters against `harness/data/captcha_font.json` (mined from the 26 captured captchas, 79 references since 2026-10-03; digit 0 is synthetic, unverified; every other digit has ≥ 3 real samples). Margin-gated; falls back to pause + beep. Tests: `harness/test_captcha.py`, including a leave-one-session-out check (ANTICHEAT.md §8.8 "Live").** **Since 2026-10-01 (user decision) it only runs in captcha mode `auto`:** the mode is `meta.captcha_mode` in the memory store (missing = `human`), switched from the viz header; in `human` the runner pauses and beeps until the client shows "Captcha successful." (ANTICHEAT.md §8.8).
  - **Growing the dataset (since 2026-10-03): `python harness/captcha_mine.py [--dry-run] [TAG ...]`.** It replays every `logs/session_*` capture (`replay.timed_packets`), takes each real captcha (S2C `0xB0`/`0xDD`, gump id 1 with a `textentry`), pairs it with our `0xB1` answer to that gump serial and keeps it only if the server's "Captcha successful." follows within 10 s (pairing stops at the next captcha gump). New layouts are appended to `captcha_samples.json` and their normalized digit clusters to `captcha_font.json`; known layouts are skipped, so a rerun is a no-op. For each new captcha it prints whether the pre-run font solves it and its weakest digit margin (out-of-sample evidence). After it adds anything, bump the hard-coded sample count and measured figures in `harness/test_captcha.py` and rerun it (~5 min). It re-finds all 24 hand-mined samples byte-identically. First run (2026-10-03, dry run): 26 accepted, 2 new, both in 20261003_150103, which kept recording after the hand mining: 437 (button 606) and 792 (button 933). The current font solves both right, min margins 0.58 and 0.43. With 150103's own sample and references removed, the 357 captcha solves right at margin 0.50. 15 captures from 2026-09-28/29 morning don't replay (`timed_packets` row/raw mismatch at the prelude); their S2C streams hold no captcha gump.
- **The harvest attempt that raised the captcha resumes after the solve** (all 24 captures, and the Smart Harvest self-target at 23:20 in `20261001_214649`: captcha, solve, then "nothing nearby"): the server answers that attempt right after "Captcha successful.", at once for an instant check (500493 not enough wood, 500489 not a tree, "You do not see any harvestable resources nearby.") or one chop time later (~4.1 s: logs or 500495 fail). So the runner's next chop result after a captcha belongs to the attempt it sent before the captcha.
- Loop-relevant mechanics (wiki, read 2026-09-29; details and links in docs/LUMBER_LOOP.md §2):
  - Lumberjacking uses Smart Harvest: double-click the hatchet and target yourself; the server chops a nearby tree with wood left. **Confirmed (user, and capture `20261001_214649` at 23:20 and 23:47):** the hatchet's cursor answered with our own serial gives the ordinary chop results (e.g. cliloc 500495), and with no tree in reach "You do not see any harvestable resources nearby." plus "You cannot produce any wood from that." over our head. All of Jaseowns' public lumber scripts do the same (ANTICHEAT.md §3). **The runner uses it since 2026-10-04** (built offline, attended live trip pending; docs/PLAN.md "Smart Harvest for lumber"). Range and which tree it picks are unmeasured; the runner's `stand` job events collect the data.
    - **The client's self-target, byte for byte** (replayed through the proxy's SessionTap, `test_loop_lumber.py unit_capture_smart_harvest`): `6c 00 <cursor id> 00 <our serial> <x u32> <y u32> <z i32> 00000190`, the server's cursor being type 1 (location allowed). `combat.target_self` with movement's `pos` and `world.self.body` builds exactly that. **Its z is movement's (0 at both tiles), not the world model's `world.self.z`, which read 10 there.**
    - **"You cannot produce any wood from that." alone means the targeted thing isn't harvestable:** at 23:40.8 the user answered the hatchet's cursor with his worn leather chest and the server put that line as a label (type 6) over the chest (serial `0x4B6BB8A5`), with no "nothing nearby" line. With a self-target it comes over us, right after the system's "You do not see any harvestable resources nearby.".
    - **The server turns us toward the tree it chops** (RunUO's harvest does `from.Direction = GetDirectionTo(target)` [INFERENCE for Outlands]): at 23:47 (1905,2616) the self-target was followed by `0x6E` (chop animation) and a `0x77` for us with dir 3 (SE); the only tree within 6 tiles (map0 `find_trees`) stood at (1906,2617), SE at distance 1. We already faced SE from the last step, so this sample can't prove the turn. The chop sound `0x54` 0x013E plays at our own tile, not the tree's. At 23:20 (1918,2612) 'nothing nearby': the only tree static within 6 was 0xACA9 at distance 2 (a graphic with 1 not_tree in harvest memory), so that sample bounds nothing.
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
  7/50 at 30–45, 12/25 at 45–60, 41/45 after 60 (a fail or a success counts as regrown; a
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
- Memory: #4082 (procedure, `char:outland_dan`, standing in his brief; #4081 superseded).

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
    4–6 stay players. Their corpses ("an orc hunter corpse") are monster corpses.
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
- **A tracked red within 80 tiles** (`--track-react-range`, user decision 2026-10-03; 40 until the
  third Bastet death below) at a pvp spot is a red escape (recall home, "tracking: <name> N
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

## Backups (NAS, since 2026-10-01)

- **What:** `harness/backup.py` copies the gitignored data that can't be recreated easily to `\\STARGAZER\files\uo-harness` (mapped as `F:` interactively): gzipped snapshots of `harness/data/harness.db` (`db/`) and, since 2026-10-03, of the Discord capture `harness/data/discord.db` (`discord/`, same snapshot method and retention, skipped while it doesn't exist), the Discord images `harness/data/discord_media/` (`discord_media/`, additive, skipped while absent), `logs/` (session captures, screens, overseer task logs), repo-root `*.pcapng`/`*.etl`/`*.png`/`divert.log` (`artifacts/`) and the 1.4 GB Ghidra project (`ghidra/`). The module docstring lists what is deliberately left out (git-tracked files, `logs_test*/`, downloads, regenerable dumps, credentials, the Discord browser profile).
- **Schedule:** Task Scheduler task `uo-harness backup`, hourly, registered by `register_backup_task.ps1` (re-run it to change anything). It runs `pythonw.exe` (no console window over the game) as the current user, non-elevated, only while logged on, so no password is stored.
- **A computer without `ghidra/` (the desktop):** the tree used to be required, so robocopy's "source not found" made every run `FAIL` (desktop, 2026-10-04). It's now skipped while absent like `discord_media/`; that also keeps `/MIR` from ever mirroring an empty local dir over the share's project.
- **Mapped drives are per logon session**, so a scheduled task doesn't reliably see `F:`. The script uses the UNC path, which works through the logon session's SMB connection (verified: `schtasks /run` → Last Result 0, snapshot written).
- **DB snapshot:** SQLite online backup API from a `mode=ro` connection (never checkpoints or writes the live store; rows still in the WAL are included), switched to rollback-journal mode so the file stands alone, `PRAGMA quick_check` before shipping, then written as `.part` and renamed. Backups of an unchanged store are byte-identical, so a sha256 in `db/latest.json` skips duplicates. Retention: every snapshot from the last 48 h, then the newest per day, kept indefinitely (~6.5 MB each; 38 MB raw). Restore: stop the proxy, gunzip to `harness/data/harness.db`, delete stale `-wal`/`-shm`.
- **Restore drill (2026-10-01, from the share, into a scratch dir):** all 3 snapshots gunzipped; the latest matched the sha256 recorded at backup time; the documented procedure (gunzip over a stale DB, delete stale `-wal`/`-shm`) gave `PRAGMA integrity_check` ok, schema v4. `memory.py --db <restored> stats`, `Memory.walk_memory(0)` (4774 tiles) and `Knowledge.search` (FTS) all worked on it, and all 11 tables matched the live store row-for-row (247,502 events). Note: the harness's `memory.connect` switches a restored file back to WAL on first open; that's expected.
- **Copies** are robocopy (size+time incremental, `/FFT` for NAS timestamp granularity). `logs/` and `artifacts/` are additive (local deletions stay on the share). `ghidra/` is `/MIR` so the copy stays one consistent project. First full run took 35 s on the LAN, then ~5–10 s.
- **Status:** `F:/uo-harness/last_backup.json` (last run, per-part result) and `logs/backup.log` (one line per run, `OK`/`FAIL`). Exit code 1 if any part failed; the other parts still run.
- Tests: `python harness/test_backup.py` (retention plan, WAL rows in the snapshot, restore, dedupe).
- **Two computers:** the `harness.db` snapshot runs only on the computer that holds the store (`dbhandoff.not_held_here`; `last_backup.json` shows `"skipped": "held by …"` otherwise). Once the handoff is in use, a computer that pushed and hasn't pulled back doesn't back the store up. Without that rule, a laptop waking from sleep would immediately run its missed backup (the task has `-StartWhenAvailable`), and its stale store would land as the newest `db/` snapshot. Before the first push, nothing changes. The Discord DBs aren't gated: run the Discord tooling on one computer only.
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
- **Adoption:** a service whose ports already listen (the bridge: its command line, via `Get-CimInstance`) is watched as `external` and never stopped by `down`; when it goes away the supervisor starts its own. Live check 2026-10-04 with the user's stack up (proxy 28952, viz 6100, laya 38488, NAT): `up --no-bridge` showed all three `external` and the NAT `up`, and after `down` all four ports were still served by the same pids.
- **Stopping a child cleanly on Windows:** the only signal a parent can send a child is CTRL_BREAK (`os.kill(pid, CTRL_BREAK_EVENT)`, child created with `CREATE_NEW_PROCESS_GROUP`, same console). Python's default for SIGBREAK is to die with no cleanup, so each child starts through a `-c` bootstrap that sets `signal.signal(SIGBREAK, default_int_handler)` and `runpy.run_path`s the script (its folder put first on `sys.path`, as `python script.py` does). The services' existing `except KeyboardInterrupt` paths then run: the proxy's `amain` `finally` saves the agent gate and flushes the memory store. Verified 2026-10-04: a real proxy and a replay viz on spare ports were up in 1.6 s and stopped within 1 s with no traceback, and the proxy's gate file was written. CTRL_BREAK needs a console: run `up` from one (pythonw or a detached start would fall back to `taskkill` and could lose the proxy's last ≤ 0.25 s of events).
- **laya-serve.exe outlives its Python wrapper** (NOTES "Laya speech triage"): the supervisor records each port's owner pid while the service is up and kills those too when the service exits or stops.
- **Restarts:** backoff 2, 5, 10, 30, 60 s, reset after 5 min up; a service that isn't ready in time (proxy 90 s, viz 60 s, laya 240 s) counts as an exit. One `service_down` juncture (attention, with the last log lines) per outage, `service_up` (info) when back, `service_flapping` (attention) after 5 exits in 10 min. A juncture that can't be written (no store, e.g. after a handoff push) is only logged.
- **NAT:** watched on its lookup port 25943. Down (or a start that fails) → urgent `nat_down` and one run of `start_proxy_nat.ps1` per outage, which asks UAC; started only once the proxy is up, because the script would otherwise start a proxy of its own. `python harness/stack.py restart nat` tries again. `down` leaves the NAT running.
- **Handoff:** `down` stops everything that has the store open that the supervisor started (proxy, viz, bridge), which `dbhandoff.py push` needs; an `external` one stays up and has to be stopped by hand.
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

## Network observations

- Session profile: one HTTPS auth connection per login (~75 s), then exactly one persistent game TCP. 22 min idle: zero extra connections. Launcher idle: zero connections.
- **VPN exit nodes are SYN-dropped** on the game port (network-layer, no RST) while Cloudflare HTTPS accepts them. Direct IP required to play. Repeated failed attempts are logged server-side — don't hammer.
- Game server observed at `74.91.115.123` (NFOservers). Auth at `104.26.0.46` (Cloudflare).
- 2026-09-30: the client connected straight to `35.71.142.123:2593` at 22:14 and to `52.223.17.219:2593` at 22:26, bypassing a NAT that matched one IP. The server IP changes between logins; the two look like an AWS Global Accelerator anycast pair [INFERENCE]. The NAT now diverts any IP on port 2593, and the proxy dials the original IP via the NAT lookup port 25943 (docs/INTERCEPTION.md). To check whether a session goes through the proxy, run `Get-NetTCPConnection -OwningProcess <ClassicUO pid>`: it shows the real server either way, because the NAT is invisible to the socket. Proof is the proxy pid holding a connection from a local port in 25940–25960 to `<server>:2593`, plus a new `logs/session_*.jsonl`.

## Links

- Captcha: https://wiki.uooutlands.com/Captcha · Razor Scripting: https://wiki.uooutlands.com/Razor_Scripting · Commands (incl. Test Shard): https://wiki.uooutlands.com/Commands · OutlandsID/2FA news: https://uooutlands.com/news/outlandsid-and-infrastructure-updates/ · Upstream: github.com/ClassicUO/ClassicUO, github.com/markdwags/Razor

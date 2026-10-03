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
  reduced to 1. Move {value} more steps to remove this effect", 5 s timer, f2 = 1; and a buff
  titled by cliloc 1075655. How `{value}` is filled isn't decoded; `ctl status` shows the raw
  numbers.
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
- Frontend toolchain: **Bun 1.4.2** installed 2026-09-29 (user-level, `irm bun.sh/install.ps1 | iex`) at `C:\Users\chris\.bun\bin\bun.exe`, added to the user PATH (new terminals only; in the agent's git-bash shell call it via PowerShell or the full path). There is no Node/npm on this machine. The visualizer frontend (docs/VISUALIZER.md) uses Bun for install/bundle/test: React + TSX.

## CAPTCHA facts (wiki)

- Triggers: lumberjacking, mining, fishing, forensic evaluation, sheep shearing, lockpicking chests — every 5–10 min of activity. (Land fishing currently exempt.)
- Mechanics: enter the dotted digits, click Okay twice; success suppresses next captcha for 10–15 min. Fail ×3 = 6 h harvest block (scales with priors). Same captcha persists across relog until solved; closing it cancels the harvest attempt.
- Digits are fixed shapes with dots displaced — template-matching territory. **Solver built 2026-09-30: `harness/captcha.py` reads the digits from the gump layout's tilepic dot clusters against `harness/data/captcha_font.json` (mined from the 12 captured captchas, 37 references since 2026-10-01; digit 0 is synthetic, unverified; digit 9 has two real samples). Margin-gated; falls back to pause + beep. Tests: `harness/test_captcha.py`, including a leave-one-session-out check (ANTICHEAT.md §8.8 "Live").** **Since 2026-10-01 (user decision) it only runs in captcha mode `auto`:** the mode is `meta.captcha_mode` in the memory store (missing = `human`), switched from the viz header; in `human` the runner pauses and beeps until the client shows "Captcha successful." (ANTICHEAT.md §8.8).
- Loop-relevant mechanics (wiki, read 2026-09-29; details and links in docs/LUMBER_LOOP.md §2):
  - Lumberjacking uses Smart Harvest: double-click the hatchet to auto-harvest nearby trees. The Smart Harvest page instead says to self-target non-pickaxe tools; verify on the wire.
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
  "Max Charges: ", "10"`. Rune names are the region ("Prevalia"); coordinates are sextant lines.
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
- The `-import` path must contain **no spaces/parens** — copy the binary to a clean path first (`C:\Users\chris\uo-harness\ClassicUO.exe`).
- `getReferencesTo()` returns `ReferenceIterator`, not an array.
- Import+analysis of this 67 MB binary: ~30–40 min on this machine. Rerunning only scripts: `-process <file> -noanalysis`.
- Invocation: `analyzeHeadless.bat <projdir> <projname> -import <clean-path.exe> -scriptPath <dir> -postScript <Script.java> -analysisTimeoutPerFile 7200 -max-cpu 8`

## Windows shell / tooling gotchas (this machine)

- The agent shell is git-bash-like: **backslashes in unquoted paths get eaten** (use forward slashes or quote), `$_` gets expanded (breaks inline PowerShell — use `.ps1` files), `timeout` is GNU (no `/t`), `copy` doesn't exist (use `cp`).
- The agent shell starts programs through Windows directly: **a shebang script fails** (`./x`: "%1 is not a valid Win32 application"), and an extensionless name doesn't resolve to `x.cmd`. `.cmd` files run (`./ctl.cmd status`; quotes, `&` and exit codes pass through), and so does `bash x`.
- **Short commands:** plain `python` on this shell's PATH is the Python 3.13 install (`sys.executable` = `C:\Users\chris\AppData\Local\Programs\Python\Python313\python.exe`), so the full path is unnecessary. `jq` is present (it's `jaq` 2.3, a jq clone): `./ctl.cmd status | jq -c '{pos, hits}'`.
- `Outlands.exe` **elevates (UAC)** — background launches hang on hidden UAC prompts; use `launch_game.ps1` and have the user accept the prompt.
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

## Backups (NAS, since 2026-10-01)

- **What:** `harness/backup.py` copies the gitignored data that can't be recreated easily to `\\STARGAZER\files\uo-harness` (mapped as `F:` interactively): gzipped snapshots of `harness/data/harness.db` (`db/`) and, since 2026-10-03, of the Discord capture `harness/data/discord.db` (`discord/`, same snapshot method and retention, skipped while it doesn't exist), `logs/` (session captures, screens, overseer task logs), repo-root `*.pcapng`/`*.etl`/`*.png`/`divert.log` (`artifacts/`) and the 1.4 GB Ghidra project (`ghidra/`). The module docstring lists what is deliberately left out (git-tracked files, `logs_test*/`, downloads, regenerable dumps, credentials, the Discord browser profile).
- **Schedule:** Task Scheduler task `uo-harness backup`, hourly, registered by `register_backup_task.ps1` (re-run it to change anything). It runs `pythonw.exe` (no console window over the game) as the current user, non-elevated, only while logged on, so no password is stored.
- **Mapped drives are per logon session**, so a scheduled task doesn't reliably see `F:`. The script uses the UNC path, which works through the logon session's SMB connection (verified: `schtasks /run` → Last Result 0, snapshot written).
- **DB snapshot:** SQLite online backup API from a `mode=ro` connection (never checkpoints or writes the live store; rows still in the WAL are included), switched to rollback-journal mode so the file stands alone, `PRAGMA quick_check` before shipping, then written as `.part` and renamed. Backups of an unchanged store are byte-identical, so a sha256 in `db/latest.json` skips duplicates. Retention: every snapshot from the last 48 h, then the newest per day, kept indefinitely (~6.5 MB each; 38 MB raw). Restore: stop the proxy, gunzip to `harness/data/harness.db`, delete stale `-wal`/`-shm`.
- **Restore drill (2026-10-01, from the share, into a scratch dir):** all 3 snapshots gunzipped; the latest matched the sha256 recorded at backup time; the documented procedure (gunzip over a stale DB, delete stale `-wal`/`-shm`) gave `PRAGMA integrity_check` ok, schema v4. `memory.py --db <restored> stats`, `Memory.walk_memory(0)` (4774 tiles) and `Knowledge.search` (FTS) all worked on it, and all 11 tables matched the live store row-for-row (247,502 events). Note: the harness's `memory.connect` switches a restored file back to WAL on first open; that's expected.
- **Copies** are robocopy (size+time incremental, `/FFT` for NAS timestamp granularity). `logs/` and `artifacts/` are additive (local deletions stay on the share). `ghidra/` is `/MIR` so the copy stays one consistent project. First full run took 35 s on the LAN, then ~5–10 s.
- **Status:** `F:/uo-harness/last_backup.json` (last run, per-part result) and `logs/backup.log` (one line per run, `OK`/`FAIL`). Exit code 1 if any part failed; the other parts still run.
- Tests: `python harness/test_backup.py` (retention plan, WAL rows in the snapshot, restore, dedupe).

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
- **Stopping:** Ctrl+C in the `serve` terminal stops both processes. Killing only the Python wrapper (TerminateProcess, e.g. `Popen.terminate()`) leaves `laya-serve.exe` listening on 25970; stop it with `powershell Stop-Process -Name laya-serve`.
- **When it's down:** the runner logs "speech triage unavailable (…)" once per outage, each verdict carries `error`, and calls back off for 60 s (a refused localhost connect can take ~2 s on Windows [INFERENCE: Windows TCP SYN retries; not timed here]). The speech hold itself is unchanged.
- **Re-measure** after changing questions, the prompt or the checkpoint (bump `triage.VERSION`): `python harness/eval_triage.py` against the running service. Zero-shot findings: a bare one-line prompt does worse than the prompt with nearby players and recent speech; `score` and `choice` phrasings were no better than noul; the multilingual checkpoint separated worse than English on this set, even on the Turkish/French lines.
- **Tamer pet commands are not speech to us (2026-10-02):** at the New Player Dungeon, tamers' "all guard", "All Stop", "All Guard Me", "All Kill", "ALL KILL" each held the hunt (junctures 52/56/57/58/60), and Laya scored "All Guard Me" `check` 0.57, just under the 0.6 staff-hint line. `speech_guard.pet_command` now drops a whole-line `all <command>` or `<pet name> <command>` (the name slot must be the first word of a non-human mobile's name on screen and not a word of our own name; commands: speech.mul's English pet keywords 0x155-0x170 plus `unfriend`). Extra words, a bare command, or a speaker with staff hints still hold; dropped lines stay in later lines' `context`.

## Telegram bridge (since 2026-10-02)

- **What:** `harness/telegram_bridge.py` puts the overseer chat and the junctures that wake it on a Telegram bot chat, and turns the user's replies into `user` chat rows. Setup, what's forwarded and the delivery rules: docs/OVERSEER.md §8; the decision: docs/PLAN.md "Overseer chat on Telegram".
- **Secret:** the bot token lives in `harness/data/telegram.json` (gitignored, with `.part` for the atomic write; `backup.py` doesn't copy it either, as with other credentials). A new token comes from BotFather (`/revoke` invalidates a leaked one). The bridge never logs request URLs, which carry the token (`Bot.call` raises `ApiError` with only the code and description).
- **Reachability (2026-10-02):** `api.telegram.org` answers from this PC without a proxy: `telegram_bridge.py send` with a bogus token got `401: Unauthorized` (exit 1). Not tested end to end with a real bot yet (no token in the repo or environment); the offline proof is `harness/test_telegram_bridge.py` plus a smoke run of the real `run` process against the fake API, where a phone message woke `ctl wait` (`data.via: telegram`) and `ctl say` and an urgent `speech_nearby` juncture reached the fake chat, both loud.
- **Game-side footprint:** none. The bridge's HTTPS goes to Telegram, never to the Outlands servers, and it doesn't touch the client, the proxy or the capture.
- **Bot API behaviour it relies on:** `getUpdates` keeps unconfirmed updates for 24 h and confirms everything below the `offset` you pass; a second poller on the same token gets 409 Conflict; 429 bodies carry `parameters.retry_after`; `sendMessage` text is limited to 4096 characters; `disable_notification` delivers without sound. Source: https://core.telegram.org/bots/api ([INFERENCE] from the docs; only the 401 case was seen live).

## Discord capture (since 2026-10-03)

- **What:** `harness/discord_capture.py` records Outlands Discord history into `harness/data/discord.db` (gitignored, backed up to the NAS `discord/` folder by `backup.py`). The decision and the rejected alternatives: docs/PLAN.md "Discord history capture".
- **Install (done 2026-10-03):** `py -3.13 -m venv .venv-discord && .venv-discord/Scripts/python.exe -m pip install patchright zstandard` (patchright 1.63.0, zstandard 0.25.0). No browser download: it drives the installed Microsoft Edge (`--channel msedge`; Edge 154 here; Chrome isn't installed). Patchright's README setup is persistent context + `channel` + `headless=False` + `no_viewport=True` with no UA/header overrides; checked: `navigator.webdriver` is `false` and the UA is stock Edge.
- **Run:** `.venv-discord/Scripts/python.exe harness/discord_capture.py serve` opens the window (profile `harness/data/discord_profile/`, which holds the login: gitignored, never backed up) and listens on `127.0.0.1:25980`. Every other subcommand talks to that port and runs under the plain harness Python: `status`, `shot` (PNG in `logs/discord/`), `goto URL`, `click SEL`, `fill SEL TEXT`, `press KEY`, `eval JS`, `guilds`, `channels GUILD` (category tree with captured counts), `crawl GUILD --channels A,B --until YYYY-MM-DD`, `crawl-stop`, `quit`. `stats` and `search QUERY [--channel NAME]` read the DB directly. Log: `logs/discord/capture.log`.
- **What it records:** only responses the web app requested: `GET /api/v*/channels/<id>/messages` (scrolling and jumps), guild message search, thread/forum lists; plus the gateway WebSocket, decoded with a decompressor that lasts the whole connection (zstd-stream or zlib-stream, read from the URL's `compress=` parameter). From the gateway it takes READY/GUILD_CREATE channel lists and live MESSAGE_CREATE/UPDATE. Each message is upserted by id, and an edit replaces the stored text, with the FTS index following through triggers. `captures` logs every recorded response (status, count), so 429s and 403s show up there.
- **Crawl:** for each channel, jump to the oldest stored message (`/channels/G/C/<id>`, an `around=` load) and send mouse-wheel bursts over `[data-list-id='chat-messages']` until a `before=` page arrives. After each page it waits 2–6 s, plus 20–90 s on 6 % of pages, and takes a 5–15 min break every 60–90 min. A `before=` page shorter than its `limit` means the channel's start (`crawl.reached_start`); `--until` stops earlier. After 3 jumps that load nothing it gives up on the channel (`crawl.note`). Every 150 pages it re-jumps, to drop the app's in-memory list. On a captcha it beeps and waits for a human to solve it in the window. On a logout it beeps and stops. After a 429 it pauses 10–15 min.
- **Game-side footprint:** none. It only talks to Discord, never to the client, the proxy or the Outlands servers.

## Network observations

- Session profile: one HTTPS auth connection per login (~75 s), then exactly one persistent game TCP. 22 min idle: zero extra connections. Launcher idle: zero connections.
- **VPN exit nodes are SYN-dropped** on the game port (network-layer, no RST) while Cloudflare HTTPS accepts them. Direct IP required to play. Repeated failed attempts are logged server-side — don't hammer.
- Game server observed at `74.91.115.123` (NFOservers). Auth at `104.26.0.46` (Cloudflare).
- 2026-09-30: the client connected straight to `35.71.142.123:2593` at 22:14 and to `52.223.17.219:2593` at 22:26, bypassing a NAT that matched one IP. The server IP changes between logins; the two look like an AWS Global Accelerator anycast pair [INFERENCE]. The NAT now diverts any IP on port 2593, and the proxy dials the original IP via the NAT lookup port 25943 (docs/INTERCEPTION.md). To check whether a session goes through the proxy, run `Get-NetTCPConnection -OwningProcess <ClassicUO pid>`: it shows the real server either way, because the NAT is invisible to the socket. Proof is the proxy pid holding a connection from a local port in 25940–25960 to `<server>:2593`, plus a new `logs/session_*.jsonl`.

## Links

- Captcha: https://wiki.uooutlands.com/Captcha · Razor Scripting: https://wiki.uooutlands.com/Razor_Scripting · Commands (incl. Test Shard): https://wiki.uooutlands.com/Commands · OutlandsID/2FA news: https://uooutlands.com/news/outlandsid-and-infrastructure-updates/ · Upstream: github.com/ClassicUO/ClassicUO, github.com/markdwags/Razor

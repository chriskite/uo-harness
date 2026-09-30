# Operational knowledge base

Facts learned during the 2026-09-27 research session that don't belong in the report but must not be lost.

## Environment & client

- Install root: `C:\Program Files (x86)\Ultima Online Outlands`. `Outlands.exe` = launcher/patcher (args `-installed`, config `Outlands.exe.json` → PatchSettings). Real client: `ClassicUO\ClassicUO.exe`.
- Client version string: `ClassicUO [STANDARD_BUILD] - 1.0.2.544` (from `ClassicUO/Logs/*_network-disconnects.txt`, which also record disconnect stack traces).
- Both binaries are **NativeAOT**: no CLR header, no `coreclr`/`hostfxr`/`runtimeconfig` strings, `Rhp*` Redhawk symbols, one embedded `ReadyToRun` header, `BSJB` managed-metadata blob in `.rdata`. No embedded DLLs to carve — assemblies are compiled into the binary. ILSpy/dnSpy/dnfile are useless here; Ghidra (native) is the tool.
- `ClassicUO/settings.json` holds plain-text `ip`/`port` (proxy insertion point), `loginserver` (`https://login.uooutlands.com`), shard/character selection, and **live account credentials** (`email`, obfuscated `outlandsid_pw`). Treat the file as a secret; never commit or log it.
- Native DLLs shipped beside the exe: SDL3, FNA3D, FAudio, cimgui, libtheorafile, zlib, vcruntime, WPF `_cor3` DLLs (used by the Razor assistant UI).
- Profile data: `ClassicUO/Data/Profiles/<OutlandsID>/<Shard>/<Char>/` (gumps, macros, journal.xml…). Plaintext journal logs: `ClassicUO/Data/Client/JournalLogs/*.txt`. Assistant profile/scripts: `ClassicUO/Data/Plugins/Assistant/` (`settings.csv` ships with stale template values; `outlandscommands.def` lists every `[`-command).
- World-map marker XMLs in `Data/Client/` (POI, moongates, dungeons, banks) — free navigation data for the world model later.
- **Map data in the install dir (surveyed read-only 2026-09-29).**
  - Outlands ships its world data in **proprietary `.uoo` containers**, not the standard MUL/UOP files: `map0.uoo`–`map5.uoo` (map0 = 585 MB, updated 2026-09-27), `art.uoo`, `artdata.uoo`, `landdata.uoo`, `landtiles.uoo`, `texmaps.uoo`, `gumps.uoo`, `anim.uoo`, `fonts.uoo`. There are no `statics*.mul`, `staidx*.mul` or `tiledata.mul` files.
  - The magic numbers differ: `map0.uoo` starts `3f3632e5 01000000`; `art`/`artdata`/`landdata.uoo` start `6dab7f1e 01000000`. The art entries look compressed.
  - Decoding `.uoo` is unmapped work: the client's loader would have to be reverse-engineered with Ghidra.
  - Standard files present: `hues.mul`, `radarcol.mul`, `multi.idx/.mul`, `Multimap.rle`, `facet00.mul`–`facet05.mul`, `speech.mul`, `skills.mul`, `light.mul`, `sound.mul`, `cliloc.*`.
- **`facet00.mul` is a ready-made 1 px/tile top-down picture of Outlands map 0** (dated 2024-12-21, so it may predate recent map edits).
  - Format as in upstream `MultiMapLoader.LoadFacet`: `u16 width, u16 height`, then per row an `i32` byte count followed by `(u8 run, u16 ARGB1555 color)` runs.
  - The file is 10752×6144 and decodes exactly: all bytes consumed, every row sums to the width, 2.9 s in pure Python.
  - The 1 px/tile scale was proven with all 40 facet-0 markers in `Data/Client/Banks_and_Healers.xml`: each one lands on a non-water pixel at scale 1.0, versus ≤57% at scales 1.25–2.0.
  - Shelter Island (the bank run's area, 1963,2597) renders as the town plaza next to the bank.
  - Implication [INFERENCE]: Outlands facet 0 is 10752×6144 tiles, larger than the standard 7168×4096. To confirm once `map0.uoo` is decoded.

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
- Outlands map/art files aren't the stock names: `facet00.mul`–`facet05.mul`, `art.uoo`, `artdata.uoo`, `anim.uoo` in the install root. A map reader has to handle these formats. Not decoded yet.
- Frontend toolchain: **Bun 1.4.2** installed 2026-09-29 (user-level, `irm bun.sh/install.ps1 | iex`) at `C:\Users\chris\.bun\bin\bun.exe`, added to the user PATH (new terminals only; in the agent's git-bash shell call it via PowerShell or the full path). There is no Node/npm on this machine. The visualizer frontend (docs/VISUALIZER.md) uses Bun for install/bundle/test: React + TSX.

## CAPTCHA facts (wiki)

- Triggers: lumberjacking, mining, fishing, forensic evaluation, sheep shearing, lockpicking chests — every 5–10 min of activity. (Land fishing currently exempt.)
- Mechanics: enter the dotted digits, click Okay twice; success suppresses next captcha for 10–15 min. Fail ×3 = 6 h harvest block (scales with priors). Same captcha persists across relog until solved; closing it cancels the harvest attempt.
- Digits are fixed shapes with dots displaced — template-matching territory.
- Loop-relevant mechanics (wiki, read 2026-09-29; details and links in docs/LUMBER_LOOP.md §2):
  - Lumberjacking uses Smart Harvest: double-click the hatchet to auto-harvest nearby trees. The Smart Harvest page instead says to self-target non-pickaxe tools; verify on the wire.
  - Harvesting is blocked in town regions, except Shelter Island while Young.
  - Shelter Island ([wiki](https://wiki.uooutlands.com/Shelter_Island)): no hostile player actions; bank and vendors need Young; harvest chance 50 % of normal; skills cap at 80. Leaving the island by moongate, hike, recall or gate asks you to confirm renouncing Young, and that's permanent.
  - TestWorth is Young as of 2026-09-29: the Young-only "Welcome to Shelter Island" gump `0xC16E0192` opens at login (sessions 163420, 202723).
  - Test Shard ([wiki](https://wiki.uooutlands.com/Test_Shard)): every house and inn room is cleared every 24 h at midnight UTC. Test resource stockpiles are only in North Prevalia and Corpse Creek, which are off-island for a Young character. Documented test commands: `[TestRes`, `[TestIgnoreMaxDamageCap`, `[TestMaxMeleeDamageRolls`, `[TestMaxSpellDamageRolls`, `[TestBlessedGear`, `[Go`. None of them grants gold.
  - TestWorth had 0 gold (2026-09-29). Shelter NPC vendors answered "You have nothing I would be interested in" to sell attempts (sessions 141253, 164548). Update the same day: a mongbat kill in the New Player Dungeon gave 21 gp (the live state port shows `gold 21`), and the user confirms TestWorth holds a Rental Room Credit Deed, so renting needs no gold.
  - 60 s harvest lockout after recall, gate, hike, teleport or rope.
  - Log/board weight is 0.025 st.
  - Commodity deeds (5 gp at a banker) list boards (5 000 regular / 2 500 colored), not logs.
  - Rental rooms: say `rent`/`room`/`house` to an innkeeper. You exit to a random inn room. No recall in. Floor items decay after 1 h unless locked down or secured.
  - **Rental-room exits land upstairs** in the Shelter inn: a random room at z 20 (demo (1932, 2589, 20); live agent run (1938, 2584, 20)), with doors between the room and the stairs. 2D walk memory can't tell the floors apart, so pathing out needs z-aware map data (docs/LUMBER_LOOP.md §13).

## Test Shard

- CoC: experimentation explicitly allowed; don't interfere with other testers; no unsanctioned PvP.
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
- `Outlands.exe` **elevates (UAC)** — background launches hang on hidden UAC prompts; use `launch_game.ps1` and have the user accept the prompt.
- winget works but: source update may fail (harmless), some installers block on UAC, 900 s job timeout can kill slow installs mid-flight.
- `ssh-keygen -N '""'` in this shell sets a literal `""` passphrase — use `-N ''`.
- `ls -l` / directory listings show **stale sizes (even 0 B) for capture files the live proxy still has open**. NTFS updates the directory entry lazily. Use `wc -c` or open the file to measure it (seen on session_20260929_204225 while it was live).
- Git remote: SSH host alias `github.com-uoharness` in `~/.ssh/config` pins deploy key `~/.ssh/uo_harness_deploy` (repo: `chriskite/uo-harness`).

## Network observations

- Session profile: one HTTPS auth connection per login (~75 s), then exactly one persistent game TCP. 22 min idle: zero extra connections. Launcher idle: zero connections.
- **VPN exit nodes are SYN-dropped** on the game port (network-layer, no RST) while Cloudflare HTTPS accepts them. Direct IP required to play. Repeated failed attempts are logged server-side — don't hammer.
- Game server observed at `74.91.115.123` (NFOservers). Auth at `104.26.0.46` (Cloudflare).

## Rules quick reference (production shard — do not operate there)

- §3.1/3.2: only Outlands Launcher + its Razor; all other clients/tools/artificial-input programs banned. Staff-sanctioned exceptions: ExploreOutlands, Outlands Butler, LootGoblin.
- §3.3: no automation or programmatic data extraction (journal file/memory, OCR, packet sniffing, memory scanning, event-reacting scripts).
- §3.4: only intended visual/audio cues + official Razor, acted on manually.
- §2: no AFK gathering/XP; GM unresponsiveness (2 min) = jail then ban; unattended skill macroing (skills tab) IS allowed.
- §1.c/1.d: circumventing dungeon/murder/ship restrictions (incl. VPN) = permanent ban.
- §11: no modifying Outlands files. §14: no deciphering server messages; all access logged.

## Links

- Rules: https://uooutlands.com/rules/ · Captcha: https://wiki.uooutlands.com/Captcha · Razor Scripting: https://wiki.uooutlands.com/Razor_Scripting · Commands (incl. Test Shard): https://wiki.uooutlands.com/Commands · OutlandsID/2FA news: https://uooutlands.com/news/outlandsid-and-infrastructure-updates/ · Upstream: github.com/ClassicUO/ClassicUO, github.com/markdwags/Razor

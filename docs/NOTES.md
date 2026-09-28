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
- **Traffic obfuscation (SOLVED 2026-09-28, see docs/CIPHER.md):** C2S = standard UO protocol XORed with a **single per-session key byte** `S` (server assigns it in the 19-byte cleartext prelude `ff 00 0d | 7×00 | 0c <tick> S <6B>`, byte 12) after a 5-byte cleartext client preamble `ef 0000000c`. Login packet = `91 <len> <name\0> <JWT>` — the HTTPS-issued JWT is the entire game credential (no password on wire). S2C = unencrypted standard UO + static Huffman from prelude byte 19 (verified: 242/242 C2S packets frame cleanly on a live capture; S2C needs 3 custom length overrides `{0x6F: 59, 0xFF: 16, 0x49: 14}`). The standard UO encryption namespace (`EncryptionHelper`/`LoginCrypt`/`Blowfish`/`Twofish`) is trimmed from the binary — this bespoke layer replaces it. The `Encrypt`/`Decrypt`/`CalculateKey` cluster in the binary is `ClassicUO.Utility/Crypter.cs` (machine-name-keyed local obfuscation of the saved password), NOT network code. Razor's `ClientEncrypted, 1` in Assistant `settings.csv` is a stale classic-client template value.

## CAPTCHA facts (wiki)

- Triggers: lumberjacking, mining, fishing, forensic evaluation, sheep shearing, lockpicking chests — every 5–10 min of activity. (Land fishing currently exempt.)
- Mechanics: enter the dotted digits, click Okay twice; success suppresses next captcha for 10–15 min. Fail ×3 = 6 h harvest block (scales with priors). Same captcha persists across relog until solved; closing it cancels the harvest attempt.
- Digits are fixed shapes with dots displaced — template-matching territory.

## Test Shard

- CoC: experimentation explicitly allowed; don't interfere with other testers; no unsanctioned PvP.
- Test-only commands: `[TestRes` (res self+followers), `[TestIgnoreMaxDamageCap`, `[TestMaxMeleeDamageRolls`, `[TestMaxSpellDamageRolls`, `[TestBlessedGear`, `[Go` (warp self+followers). Use these for fast harness iteration.
- Character creation offers skill templates (sets skills to 60) — handy for capability unlocks.
- **Client requires elevation (2026-09-28).** Launching `ClassicUO.exe` from a medium-integrity shell crashes in `UOFileManager.Load` with `Access to the path '<install>\animdata.def' is denied` (Event Viewer 1026/BEX64, fault offset 0x806816). Root cause: the Outlands loader opens game files in a way that needs write capability (their world-data/UltimaLive infrastructure writes into the install dir; upstream already has `OpenOrCreate, ReadWrite` opens), but `Program Files (x86)` grants `BUILTIN\Users` only ReadAndExecute — so a non-elevated client dies on the first file. The launcher (`Outlands.exe`) elevates via UAC for exactly this reason; the client inherits the high integrity level from it. **Always launch the client elevated**: via the launcher, or `Start-Process ClassicUO.exe -Verb RunAs` with args. `ClassicUO.exe` accepts `-ip <host>` and `-port <n>` CLI overrides (upstream `Main.cs`) — no need to edit `settings.json` to route it through the proxy. Not a lock/ACL/mandatory-label issue (verified with handle.exe + icacls).

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
